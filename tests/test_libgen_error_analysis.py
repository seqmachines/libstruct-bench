from __future__ import annotations

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from libstruct_bench.cli.prepare_libgen_error_review import (
    _artifact_role,
    main as prepare_review_main,
)
from libstruct_bench.cli.validate_libgen_error_review import (
    main as validate_review_main,
)
from tests.libgen_fixtures import (
    t2_groundtruth,
    t2_prediction,
    t3_groundtruth,
    t3_prediction,
)
from libstruct_bench.libgen.error_analysis import (
    artifact_record,
    build_error_analysis,
    summarize_error_analysis,
    substantive_review_complete,
)
from libstruct_bench.libgen.scoring import grade_libgen


ROOT = Path(__file__).resolve().parents[1]
ERROR_SCHEMA = json.loads(
    (ROOT / "schemas/analysis/libgen_error_analysis.schema.json").read_text(
        encoding="utf-8"
    )
)


def _valid_details(
    *,
    t2: dict | None = None,
    t3: dict | None = None,
    truth_t2: dict | None = None,
    truth_t3: dict | None = None,
) -> dict:
    _, scoring = grade_libgen(
        t2 or t2_prediction(),
        t3 or t3_prediction(),
        truth_t2 or t2_groundtruth(),
        truth_t3 or t3_groundtruth(),
    )
    return {
        "protocol_id": "example_protocol",
        "prediction_valid": True,
        "scoring": scoring,
    }


def _analysis(
    details: dict,
    *,
    prediction_t2: dict | None = None,
    prediction_t3: dict | None = None,
    truth_t2: dict | None = None,
    truth_t3: dict | None = None,
    trajectory: dict | None = None,
) -> dict:
    document = build_error_analysis(
        trial_id="example_protocol__trial",
        protocol_id="example_protocol",
        result={},
        details=details,
        verifier_error=None,
        artifact_inventory=[],
        model="model-a",
        harness="harness-a",
        attempt=1,
        t2_prediction=prediction_t2 or t2_prediction(),
        t3_prediction=prediction_t3 or t3_prediction(),
        t2_groundtruth=truth_t2 or t2_groundtruth(),
        t3_groundtruth=truth_t3 or t3_groundtruth(),
        trajectory=trajectory,
        trajectory_path="agent/trajectory.json" if trajectory else None,
    )
    Draft202012Validator(ERROR_SCHEMA).validate(document)
    return document


def _prediction_without_t2_oligo() -> tuple[dict, dict]:
    t2 = t2_prediction()
    t2["oligos"] = []
    t3 = t3_prediction()
    transition = t3["workflows"][0]["transitions"][0]
    transition["oligo_ids"] = []
    segment = t3["workflows"][0]["states"][1]["strands"][0]["segments"][0]
    segment["oligo_derivations"] = []
    return t2, t3


def test_exact_prediction_needs_no_error_adjudication() -> None:
    document = _analysis(_valid_details())

    assert document["run_outcome"] == "valid_prediction"
    assert document["observations"] == []
    assert document["review_status"] == "not_required"
    assert document["process_review"]["review_status"] == "not_reviewed"
    assert document["process_review"]["categories"] == []
    assert document["summary"]["substantive_discrepancy_count"] == 0
    assert document["summary"]["counts_by_attribution"] == {}
    assert substantive_review_complete(document)


def test_recoverable_mismatch_is_observed_but_not_attributed_to_agent() -> None:
    t2, t3 = _prediction_without_t2_oligo()
    document = _analysis(
        _valid_details(t2=t2, t3=t3),
        prediction_t2=t2,
        prediction_t3=t3,
    )

    observation = next(
        item
        for item in document["observations"]
        if item["location"] == "T2/oligo_families/oligo_rt"
    )
    assert observation["category"] == "missing_recoverable_information"
    assert observation["claim_recoverability"] == "recoverable"
    assert observation["benchmark_validity"] == "unresolved"
    assert observation["attribution"] == "unresolved"
    assert observation["prediction_id"] is None
    assert observation["groundtruth_id"] == "oligo_rt"
    assert observation["matched_score"] == 0.0
    assert observation["affected_metrics"] == [
        "reward",
        "t2_required_family_f1",
        "t2_exact_required_family_recall",
    ]
    assert observation["process_cause"] == "unresolved"
    assert document["process_review"]["categories"] == []
    assert document["summary"]["unresolved_issue_count"] >= 1
    assert not substantive_review_complete(document)


def test_nonrecoverable_t2_claim_is_neutral_in_error_analysis() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["support_status"] = "externally_completed"
    t2, t3 = _prediction_without_t2_oligo()
    details = _valid_details(t2=t2, t3=t3, truth_t2=truth)
    # Even stale details must not turn a source-scope/neutral record into an error.
    details["scoring"]["t2"]["unmatched_required_family_ids"] = ["oligo_rt"]

    document = _analysis(
        details,
        prediction_t2=t2,
        prediction_t3=t3,
        truth_t2=truth,
    )

    assert not any(item["task"] == "T2" for item in document["observations"])
    assert document["summary"]["raw_agent_attributed_error_count"] == 0


def test_invalid_prediction_is_a_representation_observation_not_agent_cause() -> None:
    document = _analysis(
        {
            "protocol_id": "example_protocol",
            "prediction_valid": False,
        }
    )

    observation = document["observations"][0]
    assert observation["category"] == "representation_or_schema_error"
    assert observation["benchmark_validity"] == "unresolved"
    assert observation["attribution"] == "unresolved"


def test_t3_strand_orientation_disagreement_has_specific_output_category() -> None:
    prediction = t3_prediction()
    prediction["workflows"][0]["states"][1]["strands"][0]["orientation"] = "3_to_5"
    document = _analysis(
        _valid_details(t3=prediction),
        prediction_t3=prediction,
    )

    observation = next(
        item
        for item in document["observations"]
        if item["location"].endswith("/states/state_cdna")
    )
    assert observation["category"] == "strand_or_orientation_error"


def test_representation_equivalent_t2_sequence_is_not_an_error() -> None:
    prediction = t2_prediction()
    truth = t2_groundtruth()
    oligo = truth["oligos"][0]
    oligo["kind"] = "assembled"
    oligo.pop("sequence")
    oligo["components"] = [
        {"sequence": "ACGT", "support_status": "explicit"},
        {"placeholder": "[UMI:4]", "support_status": "explicit"},
    ]
    prediction["oligos"][0]["kind"] = "assembled"

    details = _valid_details(t2=prediction, truth_t2=truth)
    document = _analysis(
        details,
        prediction_t2=prediction,
        truth_t2=truth,
    )

    assert details["scoring"]["diagnostic_metrics"]["t2"]["required_family_f1"] == 1.0
    assert not any(item["task"] == "T2" for item in document["observations"])


def test_t2_structured_identity_residuals_are_isolated_by_dimension() -> None:
    prediction = t2_prediction()
    prediction["oligos"][0].update(
        {
            "kind": "assembled",
            "orientation": "3_to_5",
            "role": "unrelated role",
            "modifications": ["unknown modification"],
        }
    )
    truth = t2_groundtruth()
    truth["oligos"][0]["modifications"] = ["5' phosphate"]
    details = _valid_details(t2=prediction, truth_t2=truth)
    document = _analysis(
        details,
        prediction_t2=prediction,
        truth_t2=truth,
    )

    t2_observations = [
        item for item in document["observations"] if item["task"] == "T2"
    ]
    assert {item["location"].rsplit("/", 1)[-1] for item in t2_observations} == {
        "kind",
        "modifications",
        "orientation",
        "role",
    }
    assert all(
        "t2_required_family_f1" in item["affected_metrics"] for item in t2_observations
    )


def test_stale_noncanonical_score_is_an_evaluator_defect_candidate() -> None:
    details = _valid_details()
    match = details["scoring"]["t2"]["matches"][0]
    match["score"] = 0.5
    match["sequence_score"] = 0.5
    match["dimension_scores"]["sequence"] = 0.5

    document = _analysis(details)
    observation = next(
        item for item in document["observations"] if item["task"] == "T2"
    )

    assert observation["category"] == "representation_or_schema_error"
    assert observation["substantive"] is False
    assert observation["benchmark_validity"] == "unresolved"
    assert observation["benchmark_validity_candidate"] == "evaluator_defect"
    assert observation["attribution"] == "unresolved"
    assert document["summary"]["substantive_discrepancy_count"] == 0
    assert document["summary"]["candidate_benchmark_issue_count"] == 1


def test_stale_t3_match_score_is_an_evaluator_defect_candidate() -> None:
    details = _valid_details()
    workflow = next(iter(details["scoring"]["t3"]["workflows"].values()))
    match = workflow["transition_matches"][0]
    match["score"] = 0.25

    document = _analysis(details)
    observation = next(
        item
        for item in document["observations"]
        if item["entity_type"] == "transition_scoring"
    )

    assert observation["category"] == "representation_or_schema_error"
    assert observation["substantive"] is False
    assert observation["benchmark_validity"] == "unresolved"
    assert observation["benchmark_validity_candidate"] == "evaluator_defect"
    assert observation["attribution"] == "unresolved"
    assert observation["matched_score"] == 0.25
    assert document["summary"]["substantive_discrepancy_count"] == 0
    assert document["summary"]["counts_by_output_error_category"] == {}
    assert document["summary"]["candidate_benchmark_issue_count"] == 1


def test_missing_t3_state_and_typed_edge_are_concrete_observations() -> None:
    prediction = t3_prediction()
    workflow = prediction["workflows"][0]
    workflow["states"] = [
        item for item in workflow["states"] if item["state_id"] != "state_input"
    ]
    workflow["initial_state_ids"] = []
    workflow["transitions"][0]["substrate_state_ids"] = []

    document = _analysis(
        _valid_details(t3=prediction),
        prediction_t3=prediction,
    )

    state = next(
        item
        for item in document["observations"]
        if item["entity_type"] == "state" and item["groundtruth_id"] == "state_input"
    )
    edge = next(
        item for item in document["observations"] if item["entity_type"] == "typed_edge"
    )
    assert state["category"] == "missing_recoverable_information"
    assert state["affected_metrics"] == ["t3_state_f1"]
    assert edge["category"] == "workflow_or_topology_error"
    assert edge["groundtruth_id"] == "substrate:state_input->transition_rt"
    assert edge["affected_metrics"] == ["t3_typed_edge_f1"]


def test_trajectory_supported_validation_correction_is_recorded() -> None:
    trajectory = {
        "steps": [
            {
                "step_id": 10,
                "tool_calls": [
                    {
                        "function_name": "exec",
                        "arguments": {
                            "input": "python -m libstruct_bench.cli.validate_libgen_predictions"
                        },
                    }
                ],
                "observation": {"output": "invalid: protocol_id mismatch\n"},
            },
            {
                "step_id": 12,
                "tool_calls": [
                    {
                        "function_name": "exec",
                        "arguments": {
                            "input": "python -m libstruct_bench.cli.validate_libgen_predictions"
                        },
                    }
                ],
                "observation": {"output": "valid\n"},
            },
        ]
    }

    document = _analysis(_valid_details(), trajectory=trajectory)

    assert document["process_review"]["review_status"] == "evidence_detected"
    assert document["process_review"]["categories"] == ["output_bookkeeping_error"]
    assert document["process_review"]["successful_self_correction"] == "observed"
    assert document["process_review"]["events"][0]["self_correction_observed"] is True
    assert len(document["process_review"]["events"][0]["evidence"]) == 2
    assert document["summary"]["observed_self_correction_count"] == 1
    assert document["summary"]["counts_by_process_cause"] == {
        "output_bookkeeping_error": 1
    }


def test_no_trajectory_evidence_leaves_process_cause_unresolved() -> None:
    t2, t3 = _prediction_without_t2_oligo()
    document = _analysis(
        _valid_details(t2=t2, t3=t3),
        prediction_t2=t2,
        prediction_t3=t3,
    )

    substantive = [item for item in document["observations"] if item["substantive"]]
    assert substantive
    assert {item["process_cause"] for item in substantive} == {"unresolved"}
    assert document["process_review"]["trajectory_available"] is False
    assert document["process_review"]["events"] == []
    assert document["summary"]["counts_by_process_cause"]["unresolved"] == len(
        substantive
    )


def test_error_analysis_does_not_mutate_scoring_outputs() -> None:
    metrics, scoring = grade_libgen(
        t2_prediction(),
        t3_prediction(),
        t2_groundtruth(),
        t3_groundtruth(),
    )
    metrics_before = copy.deepcopy(metrics)
    scoring_before = copy.deepcopy(scoring)

    _analysis(
        {
            "protocol_id": "example_protocol",
            "prediction_valid": True,
            "scoring": scoring,
        }
    )

    assert metrics == metrics_before
    assert scoring == scoring_before


def test_completed_adjudication_is_required_for_substantive_mismatches() -> None:
    t2, t3 = _prediction_without_t2_oligo()
    document = _analysis(
        _valid_details(t2=t2, t3=t3),
        prediction_t2=t2,
        prediction_t3=t3,
    )

    for observation in document["observations"]:
        if observation["substantive"]:
            observation["benchmark_validity"] = "valid"
            observation["attribution"] = "agent"
            observation["adjudication_status"] = "complete"
            observation["adjudication_notes"] = "Confirmed against the source bundle."
    document["summary"] = summarize_error_analysis(document)
    assert substantive_review_complete(document)
    assert document["summary"]["raw_agent_attributed_error_count"] >= 1


def test_infrastructure_failure_is_separate_from_model_error() -> None:
    document = build_error_analysis(
        trial_id="example_protocol__trial",
        protocol_id="example_protocol",
        result={"exception_info": "provider timeout"},
        details=None,
        verifier_error=None,
        artifact_inventory=[],
    )
    Draft202012Validator(ERROR_SCHEMA).validate(document)

    assert document["run_outcome"] == "infrastructure_failure"
    assert document["observations"][0]["category"] == "other"
    assert document["observations"][0]["attribution"] == "infrastructure"
    assert document["summary"]["infrastructure_issue_count"] == 1


def test_artifact_record_hashes_preserved_trial_file(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    trial.mkdir()
    artifact = trial / "trajectory.json"
    artifact.write_text('{"steps": []}\n', encoding="utf-8")

    record = artifact_record(
        artifact,
        role="agent_trajectory",
        relative_to=trial,
    )

    assert record["path"] == "trajectory.json"
    assert record["size_bytes"] == artifact.stat().st_size
    assert len(record["sha256"]) == 64


def test_collected_trajectory_artifact_keeps_trajectory_role() -> None:
    assert _artifact_role(Path("artifacts/agent_trajectory.json")) == "agent_trajectory"


def test_sixty_trial_review_pack_preserves_traces_and_gates_full_run(
    tmp_path: Path,
) -> None:
    protocol_ids = [f"protocol_{index}" for index in range(4)]
    cells = [
        {
            "model_key": f"model_{index}",
            "harness_key": f"harness_{index}",
        }
        for index in range(15)
    ]
    lock = {
        "mode": "pilot",
        "expected_trial_count": 60,
        "protocol_ids": protocol_ids,
        "task_bundle_sha256": "0" * 64,
        "cells": cells,
    }
    lock_path = tmp_path / "experiment_lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    details = _valid_details()
    runs = tmp_path / "runs"
    for cell in cells:
        job = runs / (f"libgen-pilot-{cell['model_key']}-{cell['harness_key']}")
        for protocol_id in protocol_ids:
            trial = job / f"{protocol_id}__trial"
            (trial / "agent").mkdir(parents=True)
            (trial / "verifier").mkdir()
            predictions = trial / "artifacts/logs/artifacts"
            predictions.mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "trial_name": trial.name,
                        "config": {"task": {"path": f"tasks/{protocol_id}"}},
                        "exception_info": None,
                        "started_at": "2026-08-01T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (trial / "agent/trajectory.json").write_text(
                '{"steps": []}\n', encoding="utf-8"
            )
            (trial / "verifier/details.json").write_text(
                json.dumps(details), encoding="utf-8"
            )
            (trial / "verifier/reward.json").write_text(
                '{"reward": 1.0}\n', encoding="utf-8"
            )
            (predictions / "t2_prediction.json").write_text(
                json.dumps(t2_prediction()), encoding="utf-8"
            )
            (predictions / "t3_prediction.json").write_text(
                json.dumps(t3_prediction()), encoding="utf-8"
            )

    review = tmp_path / "review"
    assert (
        prepare_review_main(
            [
                "--runs-root",
                str(runs),
                "--experiment-lock",
                str(lock_path),
                "--schema",
                str(ROOT / "schemas/analysis/libgen_error_analysis.schema.json"),
                "--out",
                str(review),
            ]
        )
        == 0
    )
    manifest = json.loads((review / "review_manifest.json").read_text())
    assert manifest["observed_trial_count"] == 60
    assert manifest["preservation_complete"] is True
    first_analysis = review / manifest["trials"][0]["analysis_path"]
    assert json.loads(first_analysis.read_text())["observations"] == []
    assert list(review.glob("trials/*/*/raw/agent/trajectory.json"))

    tasks = tmp_path / "tasks"
    for index in range(20):
        task = tasks / f"task_{index}"
        task.mkdir(parents=True)
        (task / "task.toml").write_text(f'name = "task-{index}"\n', encoding="utf-8")
    status_path = tmp_path / "pilot_review_status.json"
    assert (
        validate_review_main(
            [
                "--review-root",
                str(review),
                "--tasks",
                str(tasks),
                "--schema",
                str(ROOT / "schemas/analysis/libgen_error_analysis.schema.json"),
                "--record-refreeze",
                "--recorded-by",
                "test-curator",
                "--out",
                str(status_path),
            ]
        )
        == 0
    )
    status = json.loads(status_path.read_text())
    assert status["full_run_ready"] is True
    assert status["expected_trial_count"] == 60
