from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from libstruct_bench.libgen.scoring import grade_libgen, t2_sequence_similarity
from libstruct_bench.modalities import modality_key


ERROR_ANALYSIS_SCHEMA_VERSION = "libstruct.libgen_error_analysis.v2"
_EXACT_TOLERANCE = 1e-12
_NEUTRAL_SUPPORT = frozenset({"externally_completed", "ambiguous", "unsupported"})

OUTPUT_ERROR_CATEGORIES = (
    "missing_recoverable_information",
    "unsupported_completion",
    "strand_or_orientation_error",
    "molecular_state_or_assembly_error",
    "operation_error",
    "workflow_or_topology_error",
    "representation_or_schema_error",
    "other",
    "unresolved",
)
BENCHMARK_VALIDITY_VALUES = (
    "valid",
    "source_scope_mismatch",
    "ground_truth_defect",
    "policy_ambiguity",
    "evaluator_defect",
    "unresolved",
)
ATTRIBUTION_VALUES = (
    "agent",
    "benchmark",
    "mixed",
    "infrastructure",
    "unresolved",
)
PROCESS_CAUSES = (
    "evidence_not_retrieved",
    "extraction_failure",
    "evidence_retrieved_but_misinterpreted",
    "molecular_or_strand_reasoning_error",
    "graph_abstraction_error",
    "output_bookkeeping_error",
    "unresolved",
)

_T2_AFFECTED_METRICS = [
    "reward",
    "t2_required_family_f1",
    "t2_exact_required_family_recall",
]
_T3_TRANSITION_AFFECTED_METRICS = [
    "reward",
    "t3_molecular_transition_f1",
]


def task_bundle_sha256(tasks_root: Path, task_ids: Iterable[str]) -> str:
    """Hash every file in the selected generated task directories."""

    digest = hashlib.sha256()
    for task_id in sorted(set(task_ids)):
        task_root = tasks_root / task_id
        if not task_root.is_dir():
            raise FileNotFoundError(f"generated task directory is missing: {task_root}")
        files = sorted(path for path in task_root.rglob("*") if path.is_file())
        if not files:
            raise ValueError(f"generated task directory is empty: {task_root}")
        for path in files:
            relative = path.relative_to(tasks_root).as_posix().encode("utf-8")
            data = path.read_bytes()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    return digest.hexdigest()


def artifact_record(
    path: Path,
    *,
    role: str,
    relative_to: Path | None = None,
) -> dict[str, Any]:
    data = path.read_bytes()
    rendered_path = (
        path.relative_to(relative_to).as_posix()
        if relative_to is not None and path.is_relative_to(relative_to)
        else str(path)
    )
    return {
        "role": role,
        "path": rendered_path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def build_error_analysis(
    *,
    trial_id: str,
    protocol_id: str,
    result: dict[str, Any] | None,
    details: dict[str, Any] | None,
    verifier_error: dict[str, Any] | None,
    artifact_inventory: list[dict[str, Any]],
    model: str | None = None,
    harness: str | None = None,
    attempt: int | None = None,
    t2_prediction: dict[str, Any] | None = None,
    t3_prediction: dict[str, Any] | None = None,
    t2_groundtruth: dict[str, Any] | None = None,
    t3_groundtruth: dict[str, Any] | None = None,
    trajectory: dict[str, Any] | None = None,
    trajectory_path: str | None = None,
    canonical_scoring: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build observable discrepancies without inferring unsupported causes."""

    observations: list[dict[str, Any]] = []
    exception = (result or {}).get("exception_info")
    error_kind = (verifier_error or {}).get("kind")
    prediction_valid = (details or {}).get("prediction_valid")

    if exception:
        run_outcome = "infrastructure_failure"
        _append_observation(
            observations,
            task="cross_task",
            category="other",
            entity_type="run",
            prediction_id=None,
            groundtruth_id=None,
            matched_score=None,
            location="run",
            summary=(
                "Harbor recorded a trial exception before a valid scored output "
                "was produced."
            ),
            signals=[_stringify(exception)],
            affected_metrics=["reward"],
            recoverability="not_applicable",
            attribution="infrastructure",
            substantive=False,
        )
    elif error_kind == "verifier_configuration_error":
        run_outcome = "verifier_failure"
        _append_observation(
            observations,
            task="cross_task",
            category="other",
            entity_type="verifier",
            prediction_id=None,
            groundtruth_id=None,
            matched_score=None,
            location="verifier",
            summary="The verifier or private benchmark configuration failed.",
            signals=[
                str((verifier_error or {}).get("message", "unknown verifier error"))
            ],
            affected_metrics=["reward"],
            recoverability="not_applicable",
            attribution="infrastructure",
            substantive=False,
        )
    elif error_kind == "invalid_prediction" or prediction_valid is False:
        run_outcome = "invalid_prediction"
        _append_observation(
            observations,
            task="cross_task",
            category="representation_or_schema_error",
            entity_type="linked_prediction",
            prediction_id=protocol_id,
            groundtruth_id=None,
            matched_score=0.0,
            location="linked_prediction",
            summary="The linked T2/T3 prediction failed deterministic validation.",
            signals=[str((verifier_error or {}).get("message", "invalid prediction"))],
            affected_metrics=list(
                dict.fromkeys(
                    _T2_AFFECTED_METRICS
                    + _T3_TRANSITION_AFFECTED_METRICS
                    + ["t3_state_f1", "t3_typed_edge_f1"]
                )
            ),
            recoverability="not_applicable",
            substantive=True,
        )
    elif prediction_valid is True:
        run_outcome = "valid_prediction"
        scoring = (details or {}).get("scoring", {})
        reference_scoring = canonical_scoring or _rescore_details(
            t2_prediction=t2_prediction,
            t3_prediction=t3_prediction,
            t2_groundtruth=t2_groundtruth,
            t3_groundtruth=t3_groundtruth,
        )
        if reference_scoring is not None:
            _scoring_consistency_observations(
                stored=scoring,
                current=reference_scoring,
                observations=observations,
            )
            scoring = reference_scoring
        _t2_observations(
            scoring.get("t2", {}),
            observations,
            prediction=t2_prediction,
            groundtruth=t2_groundtruth,
        )
        _t3_observations(
            scoring.get("t3", {}),
            observations,
            groundtruth=t3_groundtruth,
        )
    else:
        run_outcome = "unknown"

    process_review = _trajectory_process_review(
        trajectory,
        trajectory_path=trajectory_path,
        prediction_valid=prediction_valid,
    )
    if prediction_valid is False and process_review["events"]:
        representation_observation = next(
            (
                item
                for item in observations
                if item["category"] == "representation_or_schema_error"
            ),
            None,
        )
        if representation_observation is not None:
            event = process_review["events"][-1]
            representation_observation["process_cause"] = event["process_cause"]
            representation_observation["process_evidence"] = event["evidence"]

    substantive = any(item["substantive"] for item in observations)
    document = {
        "schema_version": ERROR_ANALYSIS_SCHEMA_VERSION,
        "trial_id": trial_id,
        "protocol_id": protocol_id,
        "model": model,
        "harness": harness,
        "attempt": attempt,
        "run_outcome": run_outcome,
        "artifact_inventory": artifact_inventory,
        "observations": observations,
        "process_review": process_review,
        "review_status": "pending" if substantive else "not_required",
        "notes": (
            "Deterministic observations describe output differences only. "
            "Benchmark validity, attribution, and mismatch-specific process "
            "causes remain unresolved without supporting evidence or adjudication."
        ),
    }
    document["summary"] = summarize_error_analysis(document)
    return document


def build_error_analysis_failure(
    *,
    trial_id: str,
    protocol_id: str,
    message: str,
    artifact_inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a schema-shaped infrastructure record if analysis generation fails."""

    observations: list[dict[str, Any]] = []
    _append_observation(
        observations,
        task="cross_task",
        category="other",
        entity_type="error_analysis",
        prediction_id=None,
        groundtruth_id=None,
        matched_score=None,
        location="verifier/error_analysis.json",
        summary="Standalone error-analysis generation failed after scoring.",
        signals=[message],
        affected_metrics=[],
        recoverability="not_applicable",
        attribution="infrastructure",
        substantive=False,
    )
    document = {
        "schema_version": ERROR_ANALYSIS_SCHEMA_VERSION,
        "trial_id": trial_id,
        "protocol_id": protocol_id,
        "model": None,
        "harness": None,
        "attempt": None,
        "run_outcome": "verifier_failure",
        "artifact_inventory": artifact_inventory,
        "observations": observations,
        "process_review": _empty_process_review(trajectory_available=False),
        "review_status": "not_required",
        "notes": "Scoring outputs were preserved; only error-analysis generation failed.",
    }
    document["summary"] = summarize_error_analysis(document)
    return document


def summarize_error_analysis(document: Mapping[str, Any]) -> dict[str, Any]:
    observations = list(document.get("observations", []))
    substantive = [item for item in observations if item.get("substantive")]
    process_review = document.get("process_review", {})
    output_categories = Counter(
        item.get("category", "unresolved") for item in substantive
    )
    benchmark_validity = Counter(
        item.get("benchmark_validity", "unresolved") for item in substantive
    )
    attribution = Counter(item.get("attribution", "unresolved") for item in substantive)
    process_causes = Counter(
        item.get("process_cause", "unresolved") for item in substantive
    )
    process_causes.update(
        event.get("process_cause", "unresolved")
        for event in process_review.get("events", [])
    )
    benchmark_issue_values = {
        "source_scope_mismatch",
        "ground_truth_defect",
        "policy_ambiguity",
        "evaluator_defect",
    }
    return {
        "substantive_discrepancy_count": len(substantive),
        "raw_agent_attributed_error_count": sum(
            item.get("attribution") == "agent" for item in substantive
        ),
        "benchmark_or_evaluator_issue_count": sum(
            item.get("attribution") in {"benchmark", "mixed"}
            or item.get("benchmark_validity") in benchmark_issue_values
            for item in observations
        ),
        "candidate_benchmark_issue_count": sum(
            item.get("benchmark_validity_candidate") in benchmark_issue_values
            for item in observations
        ),
        "infrastructure_issue_count": sum(
            item.get("attribution") == "infrastructure" for item in observations
        ),
        "unresolved_issue_count": sum(
            item.get("attribution") == "unresolved"
            or item.get("benchmark_validity") == "unresolved"
            for item in substantive
        ),
        "counts_by_output_error_category": dict(sorted(output_categories.items())),
        "counts_by_benchmark_validity": dict(sorted(benchmark_validity.items())),
        "counts_by_attribution": dict(sorted(attribution.items())),
        "counts_by_process_cause": dict(sorted(process_causes.items())),
        "trajectory_available": bool(process_review.get("trajectory_available")),
        "observed_self_correction_count": sum(
            bool(event.get("self_correction_observed"))
            for event in process_review.get("events", [])
        ),
    }


def substantive_review_complete(document: dict[str, Any]) -> bool:
    for observation in document.get("observations", []):
        if not observation.get("substantive"):
            continue
        if observation.get("adjudication_status") != "complete":
            return False
        if observation.get("benchmark_validity") == "unresolved":
            return False
        if observation.get("attribution") == "unresolved":
            return False
    return True


def _rescore_details(
    *,
    t2_prediction: dict[str, Any] | None,
    t3_prediction: dict[str, Any] | None,
    t2_groundtruth: dict[str, Any] | None,
    t3_groundtruth: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not all(
        document is not None
        for document in (
            t2_prediction,
            t3_prediction,
            t2_groundtruth,
            t3_groundtruth,
        )
    ):
        return None
    _, scoring = grade_libgen(
        t2_prediction,
        t3_prediction,
        t2_groundtruth,
        t3_groundtruth,
    )
    return scoring


def _scoring_consistency_observations(
    *,
    stored: Mapping[str, Any],
    current: Mapping[str, Any],
    observations: list[dict[str, Any]],
) -> None:
    _match_score_consistency_observations(
        observations,
        stored_matches=(stored.get("t2") or {}).get("matches", []),
        current_matches=(current.get("t2") or {}).get("matches", []),
        task="T2",
        entity_type="oligo_family_scoring",
        prediction_key="prediction_oligo_id",
        groundtruth_key="groundtruth_oligo_id",
        score_key="sequence_score",
        location_prefix="T2/oligo_families",
        affected_metrics=_T2_AFFECTED_METRICS,
    )

    stored_workflows = (stored.get("t3") or {}).get("workflows", {})
    current_workflows = (current.get("t3") or {}).get("workflows", {})
    for workflow_id in sorted(set(stored_workflows) | set(current_workflows)):
        stored_workflow = stored_workflows.get(workflow_id, {})
        current_workflow = current_workflows.get(workflow_id, {})
        _match_score_consistency_observations(
            observations,
            stored_matches=stored_workflow.get("state_matches", []),
            current_matches=current_workflow.get("state_matches", []),
            task="T3",
            entity_type="state_scoring",
            prediction_key="prediction_state_id",
            groundtruth_key="groundtruth_state_id",
            score_key="score",
            location_prefix=f"T3/workflows/{workflow_id}/states",
            affected_metrics=["t3_state_f1"],
        )
        _match_score_consistency_observations(
            observations,
            stored_matches=stored_workflow.get("transition_matches", []),
            current_matches=current_workflow.get("transition_matches", []),
            task="T3",
            entity_type="transition_scoring",
            prediction_key="prediction_transition_id",
            groundtruth_key="groundtruth_transition_id",
            score_key="score",
            location_prefix=f"T3/workflows/{workflow_id}/transitions",
            affected_metrics=_T3_TRANSITION_AFFECTED_METRICS,
        )
        stored_edges = stored_workflow.get("typed_edges", {})
        current_edges = current_workflow.get("typed_edges", {})
        edge_counts = ("matched", "predicted", "groundtruth", "neutralized_predictions")
        if any(stored_edges.get(key) != current_edges.get(key) for key in edge_counts):
            _append_evaluator_candidate(
                observations,
                task="T3",
                entity_type="typed_edge_scoring",
                prediction_id=stored_workflow.get("predicted_workflow_id"),
                groundtruth_id=current_workflow.get("groundtruth_workflow_id"),
                stored_score=None,
                current_score=None,
                location=f"T3/workflows/{workflow_id}/typed_edges/scoring_consistency",
                affected_metrics=["t3_typed_edge_f1"],
                extra_signals=[
                    f"stored_{key}={stored_edges.get(key)!r};current_{key}={current_edges.get(key)!r}"
                    for key in edge_counts
                    if stored_edges.get(key) != current_edges.get(key)
                ],
            )


def _match_score_consistency_observations(
    observations: list[dict[str, Any]],
    *,
    stored_matches: Iterable[Mapping[str, Any]],
    current_matches: Iterable[Mapping[str, Any]],
    task: str,
    entity_type: str,
    prediction_key: str,
    groundtruth_key: str,
    score_key: str,
    location_prefix: str,
    affected_metrics: list[str],
) -> None:
    def _index(
        matches: Iterable[Mapping[str, Any]],
    ) -> dict[tuple[Any, Any], Mapping[str, Any]]:
        return {
            (match.get(prediction_key), match.get(groundtruth_key)): match
            for match in matches
            if match.get("scored", True)
        }

    stored_by_ids = _index(stored_matches)
    current_by_ids = _index(current_matches)
    for prediction_id, groundtruth_id in sorted(
        set(stored_by_ids) & set(current_by_ids),
        key=lambda ids: (str(ids[1]), str(ids[0])),
    ):
        stored_score = stored_by_ids[(prediction_id, groundtruth_id)].get(score_key)
        current_score = current_by_ids[(prediction_id, groundtruth_id)].get(score_key)
        if _scores_equal(stored_score, current_score):
            continue
        _append_evaluator_candidate(
            observations,
            task=task,
            entity_type=entity_type,
            prediction_id=prediction_id,
            groundtruth_id=groundtruth_id,
            stored_score=stored_score,
            current_score=current_score,
            location=f"{location_prefix}/{groundtruth_id}/scoring_consistency",
            affected_metrics=affected_metrics,
        )

    if set(stored_by_ids) != set(current_by_ids):
        _append_evaluator_candidate(
            observations,
            task=task,
            entity_type=f"{entity_type}_matching",
            prediction_id=None,
            groundtruth_id=None,
            stored_score=None,
            current_score=None,
            location=f"{location_prefix}/matching_consistency",
            affected_metrics=affected_metrics,
            extra_signals=[
                f"stored_match_ids={_json_text(sorted(stored_by_ids, key=str))}",
                f"current_match_ids={_json_text(sorted(current_by_ids, key=str))}",
            ],
        )


def _append_evaluator_candidate(
    observations: list[dict[str, Any]],
    *,
    task: str,
    entity_type: str,
    prediction_id: Any,
    groundtruth_id: Any,
    stored_score: Any,
    current_score: Any,
    location: str,
    affected_metrics: list[str],
    extra_signals: list[str] | None = None,
) -> None:
    signals = list(extra_signals or [])
    if stored_score is not None or current_score is not None:
        signals.extend(
            [
                f"stored_score={stored_score!r}",
                f"current_score={current_score!r}",
            ]
        )
    _append_observation(
        observations,
        task=task,
        category="representation_or_schema_error",
        entity_type=entity_type,
        prediction_id=prediction_id if isinstance(prediction_id, str) else None,
        groundtruth_id=groundtruth_id if isinstance(groundtruth_id, str) else None,
        matched_score=(
            float(stored_score) if isinstance(stored_score, (int, float)) else None
        ),
        location=location,
        summary=(
            "Stored match details disagree with current deterministic rescoring; "
            "this is an evaluator-defect candidate, not an attributed agent error."
        ),
        signals=signals,
        affected_metrics=affected_metrics,
        recoverability="not_applicable",
        benchmark_validity_candidate="evaluator_defect",
        candidate_reason=(
            "The preserved scoring detail and current canonical scorer produce "
            "different matching output for the same prediction and ground truth."
        ),
        substantive=False,
    )


def _scores_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= _EXACT_TOLERANCE
    return left == right


def _t2_observations(
    details: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    prediction: dict[str, Any] | None,
    groundtruth: dict[str, Any] | None,
) -> None:
    predicted = list((prediction or {}).get("oligos", []))
    truth = list((groundtruth or {}).get("oligos", []))
    predicted_by_id = {item.get("oligo_id"): item for item in predicted}
    truth_by_id = {item.get("oligo_id"): item for item in truth}

    unmatched_required = details.get(
        "unmatched_required_family_ids",
        details.get("unmatched_required_oligo_ids", []),
    )
    for oligo_id in unmatched_required:
        truth_item = truth_by_id.get(oligo_id)
        support = _support_status(truth_item)
        if support in _NEUTRAL_SUPPORT:
            continue
        _append_observation(
            observations,
            task="T2",
            category="missing_recoverable_information",
            entity_type="oligo_family",
            prediction_id=None,
            groundtruth_id=oligo_id,
            matched_score=0.0,
            location=f"T2/oligo_families/{oligo_id}",
            summary=(
                f"Recoverable T3-linked oligo family {oligo_id!r} was not recovered."
            ),
            signals=["unmatched source-recoverable ground-truth oligo family"],
            affected_metrics=_T2_AFFECTED_METRICS,
            recoverability="recoverable",
            source_support_status=support,
            substantive=True,
        )
    unmatched_prediction_families = details.get("unmatched_prediction_families")
    if unmatched_prediction_families is None:
        unmatched_prediction_families = [
            {
                "representative_oligo_id": oligo_id,
                "member_oligo_ids": [oligo_id],
            }
            for oligo_id in details.get("unmatched_prediction_oligo_ids", [])
        ]
    for family in unmatched_prediction_families:
        oligo_id = family.get("representative_oligo_id")
        if not isinstance(oligo_id, str):
            continue
        member_ids = [
            item for item in family.get("member_oligo_ids", []) if isinstance(item, str)
        ]
        _append_observation(
            observations,
            task="T2",
            category="unsupported_completion",
            entity_type="oligo_family",
            prediction_id=oligo_id,
            groundtruth_id=None,
            matched_score=0.0,
            location=f"T2/predicted_families/{oligo_id}",
            summary=(
                f"Predicted oligo family represented by {oligo_id!r} matched "
                "neither scored nor neutral ground truth."
            ),
            signals=[
                "unmatched non-neutral predicted family",
                f"collapsed_member_count={len(member_ids) or 1}",
            ],
            affected_metrics=_T2_AFFECTED_METRICS,
            recoverability="unresolved",
            substantive=True,
        )
    for match in details.get("matches", []):
        if not match.get("scored", True):
            continue
        groundtruth_id = match.get("groundtruth_oligo_id", "unknown")
        prediction_id = match.get("prediction_oligo_id")
        truth_item = _entity_from_match(
            truth,
            truth_by_id,
            entity_id=groundtruth_id,
            index=match.get("groundtruth_index"),
        )
        predicted_item = _entity_from_match(
            predicted,
            predicted_by_id,
            entity_id=prediction_id,
            index=match.get("prediction_index"),
        )
        support = match.get("groundtruth_support_status") or _support_status(truth_item)
        sequence_score = match.get("sequence_score")
        if _below_one(sequence_score):
            if support in _NEUTRAL_SUPPORT:
                continue
            recomputed = (
                t2_sequence_similarity(predicted_item, truth_item)
                if predicted_item is not None and truth_item is not None
                else None
            )
            evaluator_candidate = recomputed is not None and not _below_one(recomputed)
            _append_observation(
                observations,
                task="T2",
                category=(
                    "representation_or_schema_error"
                    if evaluator_candidate
                    else "missing_recoverable_information"
                ),
                entity_type="oligo_family",
                prediction_id=prediction_id,
                groundtruth_id=groundtruth_id,
                matched_score=float(sequence_score),
                location=f"T2/oligo_families/{groundtruth_id}",
                summary=(
                    f"Stored sequence score for oligo {groundtruth_id!r} disagrees "
                    "with current representation-equivalent canonicalization."
                    if evaluator_candidate
                    else f"Recoverable oligo family {groundtruth_id!r} was only partially matched."
                ),
                signals=[
                    f"sequence_similarity={sequence_score:.6f}",
                    *(
                        [f"canonical_recomputed_similarity={recomputed:.6f}"]
                        if recomputed is not None
                        else []
                    ),
                ],
                affected_metrics=_T2_AFFECTED_METRICS,
                recoverability="recoverable",
                source_support_status=support,
                benchmark_validity_candidate=(
                    "evaluator_defect" if evaluator_candidate else None
                ),
                candidate_reason=(
                    "The stored score is below one while the current canonical "
                    "sequence comparison is exact."
                    if evaluator_candidate
                    else None
                ),
                substantive=not evaluator_candidate,
            )
        orientation_score = (match.get("dimension_scores") or {}).get("orientation")
        if orientation_score == 0.0:
            _append_observation(
                observations,
                task="T2",
                category="strand_or_orientation_error",
                entity_type="oligo_family",
                prediction_id=prediction_id,
                groundtruth_id=groundtruth_id,
                matched_score=0.0,
                location=f"T2/oligo_families/{groundtruth_id}/orientation",
                summary=(
                    f"Oligo family {groundtruth_id!r} has the wrong stated orientation."
                ),
                signals=["orientation_accuracy=0"],
                affected_metrics=["diagnostic:t2_orientation_accuracy"],
                recoverability="recoverable",
                source_support_status=support,
                substantive=True,
            )


def _t3_observations(
    details: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    groundtruth: dict[str, Any] | None,
) -> None:
    truth_states, truth_transitions = _t3_entities(groundtruth)

    for workflow_key, workflow in sorted(details.get("workflows", {}).items()):
        predicted_workflow_id = workflow.get("predicted_workflow_id")
        groundtruth_workflow_id = workflow.get("groundtruth_workflow_id")
        scorable = workflow.get("groundtruth_scorable", True)
        if (
            predicted_workflow_id is None
            and groundtruth_workflow_id is not None
            and scorable
        ):
            _append_observation(
                observations,
                task="T3",
                category="missing_recoverable_information",
                entity_type="workflow",
                prediction_id=None,
                groundtruth_id=groundtruth_workflow_id,
                matched_score=0.0,
                location=f"T3/workflows/{workflow_key}",
                summary=f"Recoverable workflow {groundtruth_workflow_id!r} is missing.",
                signals=[
                    "missing predicted workflow",
                    f"recoverable_states={len(workflow.get('unmatched_groundtruth_state_ids', []))}",
                    f"recoverable_transitions={len(workflow.get('unmatched_groundtruth_transition_ids', []))}",
                ],
                affected_metrics=[
                    "reward",
                    "t3_molecular_transition_f1",
                    "t3_state_f1",
                    "t3_typed_edge_f1",
                ],
                recoverability="recoverable",
                substantive=True,
            )
            continue
        if predicted_workflow_id is not None and groundtruth_workflow_id is None:
            _append_observation(
                observations,
                task="T3",
                category="unsupported_completion",
                entity_type="workflow",
                prediction_id=predicted_workflow_id,
                groundtruth_id=None,
                matched_score=0.0,
                location=f"T3/workflows/{workflow_key}",
                summary=(
                    f"Predicted workflow {predicted_workflow_id!r} has no canonical "
                    "workflow match."
                ),
                signals=["extra predicted workflow"],
                affected_metrics=[
                    "reward",
                    "t3_molecular_transition_f1",
                    "t3_state_f1",
                    "t3_typed_edge_f1",
                ],
                recoverability="unresolved",
                substantive=True,
            )
            continue

        _missing_entity_observations(
            observations,
            workflow=workflow_key,
            entity="state",
            missing_truth=workflow.get("unmatched_groundtruth_state_ids", []),
            extra_predictions=workflow.get("unmatched_prediction_state_ids", []),
            truth_entities=truth_states,
            affected_metrics=["t3_state_f1"],
        )
        _missing_entity_observations(
            observations,
            workflow=workflow_key,
            entity="transition",
            missing_truth=workflow.get("unmatched_groundtruth_transition_ids", []),
            extra_predictions=workflow.get("unmatched_prediction_transition_ids", []),
            truth_entities=truth_transitions,
            affected_metrics=_T3_TRANSITION_AFFECTED_METRICS,
        )

        _terminal_output_observations(
            observations,
            workflow=workflow_key,
            details=workflow,
        )

        for match in workflow.get("state_matches", []):
            if not match.get("scored") or not _below_one(match.get("score")):
                continue
            groundtruth_id = match.get("groundtruth_state_id", "unknown")
            prediction_id = match.get("prediction_state_id")
            orientation_accuracy = match.get("strand_orientation_accuracy")
            category = (
                "strand_or_orientation_error"
                if _below_one(orientation_accuracy)
                else "molecular_state_or_assembly_error"
            )
            signals = _dimension_signals(match.get("dimension_scores", {}))
            if _below_one(orientation_accuracy):
                signals.append(
                    f"strand_orientation_accuracy={orientation_accuracy:.6f}"
                )
            _append_observation(
                observations,
                task="T3",
                category=category,
                entity_type="state",
                prediction_id=prediction_id,
                groundtruth_id=groundtruth_id,
                matched_score=float(match["score"]),
                location=f"T3/workflows/{workflow_key}/states/{groundtruth_id}",
                summary=f"Molecular state {groundtruth_id!r} disagrees on recoverable content.",
                signals=signals,
                affected_metrics=["t3_state_f1"],
                recoverability="recoverable",
                source_support_status=match.get("groundtruth_support_status"),
                substantive=True,
            )
        for match in workflow.get("transition_matches", []):
            if not match.get("scored") or not _below_one(match.get("score")):
                continue
            groundtruth_id = match.get("groundtruth_transition_id", "unknown")
            prediction_id = match.get("prediction_transition_id")
            dimensions = match.get("dimension_scores", {})
            operation_score = dimensions.get("operation")
            if _below_one(operation_score):
                _append_observation(
                    observations,
                    task="T3",
                    category="operation_error",
                    entity_type="transition",
                    prediction_id=prediction_id,
                    groundtruth_id=groundtruth_id,
                    matched_score=float(match["score"]),
                    location=(
                        f"T3/workflows/{workflow_key}/transitions/"
                        f"{groundtruth_id}/operation"
                    ),
                    summary=f"Transition {groundtruth_id!r} uses a different operation.",
                    signals=[f"operation={operation_score:.6f}"],
                    affected_metrics=_T3_TRANSITION_AFFECTED_METRICS,
                    recoverability="recoverable",
                    source_support_status=match.get("groundtruth_support_status"),
                    substantive=True,
                )
            topology_dimensions = {
                key: value
                for key, value in dimensions.items()
                if key != "operation" and _below_one(value)
            }
            if topology_dimensions or (
                not _below_one(operation_score) and not dimensions
            ):
                mismatches = (
                    ", ".join(sorted(topology_dimensions)) or "aggregate content"
                )
                _append_observation(
                    observations,
                    task="T3",
                    category="workflow_or_topology_error",
                    entity_type="transition",
                    prediction_id=prediction_id,
                    groundtruth_id=groundtruth_id,
                    matched_score=float(match["score"]),
                    location=f"T3/workflows/{workflow_key}/transitions/{groundtruth_id}",
                    summary=(
                        f"Transition {groundtruth_id!r} disagrees in {mismatches}."
                    ),
                    signals=_dimension_signals(topology_dimensions),
                    affected_metrics=_T3_TRANSITION_AFFECTED_METRICS,
                    recoverability="recoverable",
                    source_support_status=match.get("groundtruth_support_status"),
                    substantive=True,
                )

        _typed_edge_observations(
            observations,
            workflow=workflow_key,
            edges=workflow.get("typed_edges", {}),
        )


def _terminal_output_observations(
    observations: list[dict[str, Any]],
    *,
    workflow: str,
    details: Mapping[str, Any],
) -> None:
    state_map = details.get("state_id_map", {})
    predicted = [
        {
            "state_id": str(item.get("state_id", "")),
            "mapped_state_id": state_map.get(str(item.get("state_id", ""))),
            "modality": modality_key(str(item.get("modality", ""))),
            "reported_modality": str(item.get("modality", "")),
        }
        for item in details.get("predicted_final_outputs", [])
    ]
    truth = [
        {
            "state_id": str(item.get("state_id", "")),
            "modality": modality_key(str(item.get("modality", ""))),
            "reported_modality": str(item.get("modality", "")),
        }
        for item in details.get("groundtruth_final_outputs", [])
    ]
    used_predictions: set[int] = set()
    for expected in truth:
        exact_index = next(
            (
                index
                for index, candidate in enumerate(predicted)
                if index not in used_predictions
                and candidate["mapped_state_id"] == expected["state_id"]
                and candidate["modality"] == expected["modality"]
            ),
            None,
        )
        if exact_index is not None:
            used_predictions.add(exact_index)
            continue
        wrong_modality_index = next(
            (
                index
                for index, candidate in enumerate(predicted)
                if index not in used_predictions
                and candidate["mapped_state_id"] == expected["state_id"]
            ),
            None,
        )
        groundtruth_id = f"{expected['state_id']}:{expected['reported_modality']}"
        if wrong_modality_index is not None:
            candidate = predicted[wrong_modality_index]
            used_predictions.add(wrong_modality_index)
            prediction_id = f"{candidate['state_id']}:{candidate['reported_modality']}"
            _append_observation(
                observations,
                task="T3",
                category="workflow_or_topology_error",
                entity_type="terminal_output",
                prediction_id=prediction_id,
                groundtruth_id=groundtruth_id,
                matched_score=0.0,
                location=f"T3/workflows/{workflow}/final_outputs/{expected['state_id']}",
                summary=(
                    f"Terminal state {expected['state_id']!r} has modality "
                    f"{candidate['reported_modality']!r}; expected "
                    f"{expected['reported_modality']!r}."
                ),
                signals=["wrong terminal-output modality"],
                affected_metrics=["diagnostic:t3_terminal_output_f1"],
                recoverability="recoverable",
                substantive=True,
            )
            continue
        _append_observation(
            observations,
            task="T3",
            category="missing_recoverable_information",
            entity_type="terminal_output",
            prediction_id=None,
            groundtruth_id=groundtruth_id,
            matched_score=0.0,
            location=f"T3/workflows/{workflow}/final_outputs/{expected['state_id']}",
            summary=(
                f"Terminal output {groundtruth_id!r} is missing from the predicted "
                "connected workflow."
            ),
            signals=["missing terminal output"],
            affected_metrics=["diagnostic:t3_terminal_output_f1"],
            recoverability="recoverable",
            substantive=True,
        )

    for index, candidate in enumerate(predicted):
        if index in used_predictions:
            continue
        prediction_id = f"{candidate['state_id']}:{candidate['reported_modality']}"
        _append_observation(
            observations,
            task="T3",
            category="unsupported_completion",
            entity_type="terminal_output",
            prediction_id=prediction_id,
            groundtruth_id=None,
            matched_score=0.0,
            location=f"T3/workflows/{workflow}/final_outputs/{candidate['state_id']}",
            summary=f"Predicted terminal output {prediction_id!r} has no canonical match.",
            signals=["extra terminal output"],
            affected_metrics=["diagnostic:t3_terminal_output_f1"],
            recoverability="unresolved",
            substantive=True,
        )


def _missing_entity_observations(
    observations: list[dict[str, Any]],
    *,
    workflow: str,
    entity: str,
    missing_truth: Iterable[str],
    extra_predictions: Iterable[str],
    truth_entities: Mapping[str, dict[str, Any]],
    affected_metrics: list[str],
) -> None:
    for entity_id in missing_truth:
        support = _support_status(truth_entities.get(entity_id))
        if support in _NEUTRAL_SUPPORT:
            continue
        _append_observation(
            observations,
            task="T3",
            category="missing_recoverable_information",
            entity_type=entity,
            prediction_id=None,
            groundtruth_id=entity_id,
            matched_score=0.0,
            location=f"T3/workflows/{workflow}/{entity}s/{entity_id}",
            summary=f"Recoverable {entity} {entity_id!r} is missing.",
            signals=[f"unmatched ground-truth {entity}"],
            affected_metrics=affected_metrics,
            recoverability="recoverable",
            source_support_status=support,
            substantive=True,
        )
    for entity_id in extra_predictions:
        _append_observation(
            observations,
            task="T3",
            category="unsupported_completion",
            entity_type=entity,
            prediction_id=entity_id,
            groundtruth_id=None,
            matched_score=0.0,
            location=f"T3/workflows/{workflow}/{entity}s/{entity_id}",
            summary=f"Predicted {entity} {entity_id!r} has no matched canonical entity.",
            signals=[f"unmatched predicted {entity}"],
            affected_metrics=affected_metrics,
            recoverability="unresolved",
            substantive=True,
        )


def _typed_edge_observations(
    observations: list[dict[str, Any]],
    *,
    workflow: str,
    edges: dict[str, Any],
) -> None:
    missing = list(edges.get("missing_groundtruth_edges", []))
    extra = list(edges.get("extra_prediction_edges", []))
    for edge in missing:
        edge_id = _typed_edge_id(edge)
        _append_observation(
            observations,
            task="T3",
            category="workflow_or_topology_error",
            entity_type="typed_edge",
            prediction_id=None,
            groundtruth_id=edge_id,
            matched_score=0.0,
            location=f"T3/workflows/{workflow}/typed_edges/{edge_id}",
            summary=f"Canonical typed edge {edge_id!r} is missing.",
            signals=["missing typed edge"],
            affected_metrics=["t3_typed_edge_f1"],
            recoverability="recoverable",
            substantive=True,
        )
    for edge in extra:
        edge_id = _typed_edge_id(edge)
        _append_observation(
            observations,
            task="T3",
            category="workflow_or_topology_error",
            entity_type="typed_edge",
            prediction_id=edge_id,
            groundtruth_id=None,
            matched_score=0.0,
            location=f"T3/workflows/{workflow}/typed_edges/{edge_id}",
            summary=f"Predicted typed edge {edge_id!r} has no canonical match.",
            signals=["extra typed edge"],
            affected_metrics=["t3_typed_edge_f1"],
            recoverability="unresolved",
            substantive=True,
        )
    if (
        not missing
        and not extra
        and (
            edges.get("matched", 0) != edges.get("groundtruth", 0)
            or edges.get("matched", 0) != edges.get("predicted", 0)
        )
    ):
        _append_observation(
            observations,
            task="T3",
            category="workflow_or_topology_error",
            entity_type="typed_edge_set",
            prediction_id=workflow,
            groundtruth_id=workflow,
            matched_score=None,
            location=f"T3/workflows/{workflow}/typed_edges",
            summary="The aligned typed graph edges disagree.",
            signals=[
                f"matched={edges.get('matched', 0)}",
                f"predicted={edges.get('predicted', 0)}",
                f"groundtruth={edges.get('groundtruth', 0)}",
                f"neutralized_predictions={edges.get('neutralized_predictions', 0)}",
            ],
            affected_metrics=["t3_typed_edge_f1"],
            recoverability="recoverable",
            substantive=True,
        )


def _append_observation(
    observations: list[dict[str, Any]],
    *,
    task: str,
    category: str,
    entity_type: str,
    prediction_id: str | None,
    groundtruth_id: str | None,
    matched_score: float | None,
    location: str,
    summary: str,
    signals: list[str],
    affected_metrics: list[str],
    recoverability: str,
    substantive: bool,
    source_support_status: str | None = None,
    benchmark_validity: str = "unresolved",
    benchmark_validity_candidate: str | None = None,
    candidate_reason: str | None = None,
    attribution: str = "unresolved",
    process_cause: str = "unresolved",
    process_evidence: list[dict[str, str]] | None = None,
) -> None:
    if category not in OUTPUT_ERROR_CATEGORIES:
        raise ValueError(f"unsupported output-error category: {category}")
    observations.append(
        {
            "error_id": f"err_{len(observations) + 1:04d}",
            "task": task,
            "category": category,
            "entity_type": entity_type,
            "prediction_id": prediction_id,
            "groundtruth_id": groundtruth_id,
            "matched_score": matched_score,
            "location": location,
            "summary": summary,
            "signals": signals,
            "affected_metrics": list(dict.fromkeys(affected_metrics)),
            "claim_recoverability": recoverability,
            "source_support_status": source_support_status,
            "benchmark_validity": benchmark_validity,
            "benchmark_validity_candidate": benchmark_validity_candidate,
            "candidate_reason": candidate_reason,
            "attribution": attribution,
            "process_cause": process_cause,
            "process_evidence": process_evidence or [],
            "detected_by": "deterministic_comparison",
            "substantive": substantive,
            "adjudication_status": "pending",
            "adjudication_notes": None,
            "adjudicated_by": None,
            "adjudicated_at": None,
        }
    )


def _trajectory_process_review(
    trajectory: dict[str, Any] | None,
    *,
    trajectory_path: str | None,
    prediction_valid: Any,
) -> dict[str, Any]:
    steps = (trajectory or {}).get("steps")
    if not isinstance(steps, list):
        return _empty_process_review(trajectory_available=False)

    artifact_path = trajectory_path or "agent/trajectory.json"
    attempts: list[dict[str, Any]] = []
    for position, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        tool_text = _json_text(step.get("tool_calls", []))
        if "validate_libgen_predictions" not in tool_text:
            continue
        observation_text = _json_text(step.get("observation", ""))
        lowered = observation_text.lower()
        failed = bool(
            re.search(r"(?<![a-z])invalid\s*:", lowered)
            or "validation failed" in lowered
            or "schema validation error" in lowered
        )
        succeeded = bool(re.search(r"(?<![a-z])valid(?![a-z])", lowered)) and not failed
        if failed or succeeded:
            attempts.append(
                {
                    "position": position,
                    "step_id": step.get("step_id", position),
                    "failed": failed,
                    "succeeded": succeeded,
                }
            )

    events: list[dict[str, Any]] = []
    consumed_successes: set[int] = set()
    for attempt_index, attempt in enumerate(attempts):
        if not attempt["failed"]:
            continue
        later_success = next(
            (
                candidate
                for candidate_index, candidate in enumerate(attempts)
                if candidate_index > attempt_index
                and candidate_index not in consumed_successes
                and candidate["succeeded"]
            ),
            None,
        )
        if later_success is not None:
            success_index = attempts.index(later_success)
            consumed_successes.add(success_index)
            evidence = [
                _trajectory_evidence(
                    artifact_path,
                    attempt["step_id"],
                    "Local prediction validation reported an explicit failure.",
                ),
                _trajectory_evidence(
                    artifact_path,
                    later_success["step_id"],
                    "A later local prediction validation reported success.",
                ),
            ]
            events.append(
                {
                    "event_id": f"proc_{len(events) + 1:04d}",
                    "process_cause": "output_bookkeeping_error",
                    "summary": (
                        "An observable local-validation failure was followed by "
                        "successful validation before the final output."
                    ),
                    "self_correction_observed": True,
                    "evidence": evidence,
                }
            )
        elif prediction_valid is False:
            evidence = [
                _trajectory_evidence(
                    artifact_path,
                    attempt["step_id"],
                    "Local prediction validation reported an explicit failure.",
                )
            ]
            events.append(
                {
                    "event_id": f"proc_{len(events) + 1:04d}",
                    "process_cause": "output_bookkeeping_error",
                    "summary": "The saved trajectory contains an unresolved validation failure.",
                    "self_correction_observed": False,
                    "evidence": evidence,
                }
            )

    categories = sorted({item["process_cause"] for item in events})
    evidence = [item for event in events for item in event["evidence"]]
    return {
        "review_status": "evidence_detected" if events else "not_reviewed",
        "trajectory_available": True,
        "categories": categories,
        "successful_self_correction": "observed"
        if any(item["self_correction_observed"] for item in events)
        else "unclear",
        "events": events,
        "evidence": evidence,
        "notes": (
            "Only explicit tool outcomes were classified automatically; no hidden "
            "reasoning or mismatch-specific root cause was inferred."
        ),
        "reviewed_by": None,
        "reviewed_at": None,
    }


def _empty_process_review(*, trajectory_available: bool) -> dict[str, Any]:
    return {
        "review_status": "not_reviewed",
        "trajectory_available": trajectory_available,
        "categories": [],
        "successful_self_correction": (
            "unclear" if trajectory_available else "not_reviewed"
        ),
        "events": [],
        "evidence": [],
        "notes": None,
        "reviewed_by": None,
        "reviewed_at": None,
    }


def _trajectory_evidence(
    artifact_path: str,
    step_id: Any,
    summary: str,
) -> dict[str, str]:
    return {
        "artifact_path": artifact_path,
        "locator": f"steps[step_id={step_id}]",
        "summary": summary,
    }


def _t3_entities(
    document: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    states: dict[str, dict[str, Any]] = {}
    transitions: dict[str, dict[str, Any]] = {}
    for workflow in (document or {}).get("workflows", []):
        states.update(
            {
                item["state_id"]: item
                for item in workflow.get("states", [])
                if isinstance(item.get("state_id"), str)
            }
        )
        transitions.update(
            {
                item["transition_id"]: item
                for item in workflow.get("transitions", [])
                if isinstance(item.get("transition_id"), str)
            }
        )
    return states, transitions


def _entity_from_match(
    entities: list[dict[str, Any]],
    by_id: Mapping[Any, dict[str, Any]],
    *,
    entity_id: Any,
    index: Any,
) -> dict[str, Any] | None:
    if entity_id in by_id:
        return by_id[entity_id]
    if isinstance(index, int) and 0 <= index < len(entities):
        return entities[index]
    return None


def _support_status(item: Mapping[str, Any] | None) -> str | None:
    value = (item or {}).get("support_status")
    return value if isinstance(value, str) else None


def _typed_edge_id(edge: Mapping[str, Any]) -> str:
    return f"{edge.get('edge_type', 'edge')}:{edge.get('left_id', '?')}->{edge.get('right_id', '?')}"


def _dimension_signals(dimensions: dict[str, Any]) -> list[str]:
    result = [
        f"{key}={value:.6f}"
        for key, value in sorted(dimensions.items())
        if isinstance(value, (int, float)) and _below_one(float(value))
    ]
    return result or ["aggregate_similarity_below_one"]


def _below_one(value: Any) -> bool:
    return isinstance(value, (int, float)) and float(value) < 1.0 - _EXACT_TOLERANCE


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value)
