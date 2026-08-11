from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from libstruct_bench.cli.prepare_libgen_error_review import main as prepare_review_main
from libstruct_bench.cli.validate_libgen_error_review import main as validate_review_main
from tests.libgen_fixtures import (
    t2_groundtruth,
    t2_prediction,
    t3_groundtruth,
    t3_prediction,
)
from libstruct_bench.libgen.error_analysis import (
    artifact_record,
    build_error_analysis,
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


def _analysis(details: dict) -> dict:
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
    assert substantive_review_complete(document)


def test_recoverable_mismatch_is_observed_but_not_attributed_to_agent() -> None:
    t2, t3 = _prediction_without_t2_oligo()
    document = _analysis(_valid_details(t2=t2, t3=t3))

    observation = next(
        item
        for item in document["observations"]
        if item["location"] == "T2/oligos/oligo_rt"
    )
    assert observation["category"] == "missing_recoverable_information"
    assert observation["claim_recoverability"] == "recoverable"
    assert observation["benchmark_validity"] == "unresolved"
    assert observation["attribution"] == "unresolved"
    assert document["process_review"]["categories"] == []
    assert not substantive_review_complete(document)


def test_nonrecoverable_t2_claim_is_neutral_in_error_analysis() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["support_status"] = "externally_completed"
    t2, t3 = _prediction_without_t2_oligo()

    document = _analysis(_valid_details(t2=t2, t3=t3, truth_t2=truth))

    assert not any(item["task"] == "T2" for item in document["observations"])


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
    prediction["workflows"][0]["states"][1]["strands"][0][
        "orientation"
    ] = "3_to_5"
    document = _analysis(_valid_details(t3=prediction))

    observation = next(
        item
        for item in document["observations"]
        if item["location"].endswith("/states/state_cdna")
    )
    assert observation["category"] == "strand_or_orientation_error"


def test_completed_adjudication_is_required_for_substantive_mismatches() -> None:
    t2, t3 = _prediction_without_t2_oligo()
    document = _analysis(_valid_details(t2=t2, t3=t3))

    for observation in document["observations"]:
        if observation["substantive"]:
            observation["benchmark_validity"] = "valid"
            observation["attribution"] = "agent"
            observation["adjudication_status"] = "complete"
            observation["adjudication_notes"] = "Confirmed against the source bundle."
    assert substantive_review_complete(document)


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
    assert document["observations"][0]["category"] == "infrastructure_failure"
    assert document["observations"][0]["attribution"] == "infrastructure"


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
        job = runs / (
            f"libgen-pilot-{cell['model_key']}-{cell['harness_key']}"
        )
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
        (task / "task.toml").write_text(
            f'name = "task-{index}"\n', encoding="utf-8"
        )
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
