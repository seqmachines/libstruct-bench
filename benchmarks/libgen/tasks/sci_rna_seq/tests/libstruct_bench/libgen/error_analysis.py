from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable


ERROR_ANALYSIS_SCHEMA_VERSION = "libstruct.libgen_error_analysis.v1"
_EXACT_TOLERANCE = 1e-12


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
) -> dict[str, Any]:
    """Build deterministic output observations without inferring process causes."""

    observations: list[dict[str, Any]] = []
    exception = (result or {}).get("exception_info")
    error_kind = (verifier_error or {}).get("kind")
    prediction_valid = (details or {}).get("prediction_valid")

    if exception:
        run_outcome = "infrastructure_failure"
        _append_observation(
            observations,
            task="cross_task",
            category="infrastructure_failure",
            location="run",
            summary="Harbor recorded a trial exception before a valid scored output was produced.",
            signals=[_stringify(exception)],
            recoverability="not_applicable",
            attribution="infrastructure",
            substantive=False,
        )
    elif error_kind == "verifier_configuration_error":
        run_outcome = "verifier_failure"
        _append_observation(
            observations,
            task="cross_task",
            category="infrastructure_failure",
            location="verifier",
            summary="The verifier or private benchmark configuration failed.",
            signals=[
                str(
                    (verifier_error or {}).get(
                        "message", "unknown verifier error"
                    )
                )
            ],
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
            location="linked_prediction",
            summary="The linked T2/T3 prediction failed deterministic validation.",
            signals=[str((verifier_error or {}).get("message", "invalid prediction"))],
            recoverability="not_applicable",
            substantive=True,
        )
    elif prediction_valid is True:
        run_outcome = "valid_prediction"
        scoring = (details or {}).get("scoring", {})
        _t2_observations(scoring.get("t2", {}), observations)
        _t3_observations(scoring.get("t3", {}), observations)
    else:
        run_outcome = "unknown"

    substantive = any(item["substantive"] for item in observations)
    return {
        "schema_version": ERROR_ANALYSIS_SCHEMA_VERSION,
        "trial_id": trial_id,
        "protocol_id": protocol_id,
        "model": model,
        "harness": harness,
        "attempt": attempt,
        "run_outcome": run_outcome,
        "artifact_inventory": artifact_inventory,
        "observations": observations,
        "process_review": {
            "review_status": "not_reviewed",
            "categories": [],
            "successful_self_correction": "not_reviewed",
            "evidence": [],
            "notes": None,
            "reviewed_by": None,
            "reviewed_at": None,
        },
        "review_status": "pending" if substantive else "not_required",
        "notes": (
            "Deterministic observations describe output differences only. "
            "Benchmark validity, attribution, and process causes require adjudication."
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


def _t2_observations(
    details: dict[str, Any],
    observations: list[dict[str, Any]],
) -> None:
    for oligo_id in details.get("unmatched_required_oligo_ids", []):
        _append_observation(
            observations,
            task="T2",
            category="missing_recoverable_information",
            location=f"T2/oligos/{oligo_id}",
            summary=f"Recoverable T3-linked oligo {oligo_id!r} was not recovered.",
            signals=["unmatched O_score ground-truth oligo"],
            recoverability="recoverable",
            substantive=True,
        )
    for oligo_id in details.get("unmatched_prediction_oligo_ids", []):
        _append_observation(
            observations,
            task="T2",
            category="unsupported_completion",
            location=f"T2/predictions/{oligo_id}",
            summary=f"Predicted oligo {oligo_id!r} matched neither scored nor neutral ground truth.",
            signals=["unmatched non-neutral prediction"],
            recoverability="unresolved",
            substantive=True,
        )
    for match in details.get("matches", []):
        if not match.get("scored", True):
            continue
        oligo_id = match.get("groundtruth_oligo_id", "unknown")
        sequence_score = match.get("sequence_score")
        if _below_one(sequence_score):
            _append_observation(
                observations,
                task="T2",
                category="missing_recoverable_information",
                location=f"T2/oligos/{oligo_id}",
                summary=f"Recoverable oligo {oligo_id!r} was only partially matched.",
                signals=[f"sequence_similarity={sequence_score:.6f}"],
                recoverability="recoverable",
                substantive=True,
            )
        orientation_score = (match.get("dimension_scores") or {}).get("orientation")
        if orientation_score == 0.0:
            _append_observation(
                observations,
                task="T2",
                category="strand_or_orientation_error",
                location=f"T2/oligos/{oligo_id}/orientation",
                summary=f"Oligo {oligo_id!r} has the wrong stated orientation.",
                signals=["orientation_accuracy=0"],
                recoverability="recoverable",
                substantive=True,
            )


def _t3_observations(details: dict[str, Any], observations: list[dict[str, Any]]) -> None:
    for modality_key, modality in sorted(details.get("modalities", {}).items()):
        predicted_modality = modality.get("predicted_modality")
        groundtruth_modality = modality.get("groundtruth_modality")
        scorable = modality.get("groundtruth_scorable", True)
        if predicted_modality is None and groundtruth_modality is not None and scorable:
            _append_observation(
                observations,
                task="T3",
                category="wrong_target_or_modality",
                location=f"T3/modalities/{modality_key}",
                summary=f"Recoverable modality {groundtruth_modality!r} is missing.",
                signals=[
                    "missing predicted workflow",
                    f"recoverable_states={len(modality.get('unmatched_groundtruth_state_ids', []))}",
                    f"recoverable_transitions={len(modality.get('unmatched_groundtruth_transition_ids', []))}",
                ],
                recoverability="recoverable",
                substantive=True,
            )
            continue
        elif predicted_modality is not None and groundtruth_modality is None:
            _append_observation(
                observations,
                task="T3",
                category="wrong_target_or_modality",
                location=f"T3/modalities/{modality_key}",
                summary=f"Predicted modality {predicted_modality!r} is not in canonical ground truth.",
                signals=["extra predicted workflow"],
                recoverability="unresolved",
                substantive=True,
            )
            continue

        _missing_entity_observations(
            observations,
            modality=modality_key,
            entity="state",
            missing_truth=modality.get("unmatched_groundtruth_state_ids", []),
            extra_predictions=modality.get("unmatched_prediction_state_ids", []),
        )
        _missing_entity_observations(
            observations,
            modality=modality_key,
            entity="transition",
            missing_truth=modality.get("unmatched_groundtruth_transition_ids", []),
            extra_predictions=modality.get("unmatched_prediction_transition_ids", []),
        )

        for match in modality.get("state_matches", []):
            if not match.get("scored") or not _below_one(match.get("score")):
                continue
            state_id = match.get("groundtruth_state_id", "unknown")
            orientation_accuracy = match.get("strand_orientation_accuracy")
            category = (
                "strand_or_orientation_error"
                if _below_one(orientation_accuracy)
                else "molecular_assembly_or_topology_error"
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
                location=f"T3/modalities/{modality_key}/states/{state_id}",
                summary=f"Molecular state {state_id!r} disagrees on recoverable content.",
                signals=signals,
                recoverability="recoverable",
                substantive=True,
            )
        for match in modality.get("transition_matches", []):
            if not match.get("scored") or not _below_one(match.get("score")):
                continue
            transition_id = match.get("groundtruth_transition_id", "unknown")
            _append_observation(
                observations,
                task="T3",
                category="molecular_assembly_or_topology_error",
                location=f"T3/modalities/{modality_key}/transitions/{transition_id}",
                summary=f"Molecular transition {transition_id!r} disagrees on recoverable content.",
                signals=_dimension_signals(match.get("dimension_scores", {})),
                recoverability="recoverable",
                substantive=True,
            )

        edges = modality.get("typed_edges", {})
        if (
            edges.get("matched", 0) != edges.get("groundtruth", 0)
            or edges.get("matched", 0) != edges.get("predicted", 0)
        ):
            _append_observation(
                observations,
                task="T3",
                category="molecular_assembly_or_topology_error",
                location=f"T3/modalities/{modality_key}/typed_edges",
                summary="The aligned typed graph edges disagree.",
                signals=[
                    f"matched={edges.get('matched', 0)}",
                    f"predicted={edges.get('predicted', 0)}",
                    f"groundtruth={edges.get('groundtruth', 0)}",
                    f"neutralized_predictions={edges.get('neutralized_predictions', 0)}",
                ],
                recoverability="recoverable",
                substantive=True,
            )


def _missing_entity_observations(
    observations: list[dict[str, Any]],
    *,
    modality: str,
    entity: str,
    missing_truth: Iterable[str],
    extra_predictions: Iterable[str],
) -> None:
    for entity_id in missing_truth:
        _append_observation(
            observations,
            task="T3",
            category="missing_recoverable_information",
            location=f"T3/modalities/{modality}/{entity}s/{entity_id}",
            summary=f"Recoverable {entity} {entity_id!r} is missing.",
            signals=[f"unmatched ground-truth {entity}"],
            recoverability="recoverable",
            substantive=True,
        )
    for entity_id in extra_predictions:
        _append_observation(
            observations,
            task="T3",
            category="unsupported_completion",
            location=f"T3/modalities/{modality}/{entity}s/{entity_id}",
            summary=f"Predicted {entity} {entity_id!r} has no matched canonical entity.",
            signals=[f"unmatched predicted {entity}"],
            recoverability="unresolved",
            substantive=True,
        )


def _append_observation(
    observations: list[dict[str, Any]],
    *,
    task: str,
    category: str,
    location: str,
    summary: str,
    signals: list[str],
    recoverability: str,
    substantive: bool,
    attribution: str = "unresolved",
) -> None:
    observations.append(
        {
            "error_id": f"err_{len(observations) + 1:04d}",
            "task": task,
            "category": category,
            "location": location,
            "summary": summary,
            "signals": signals,
            "claim_recoverability": recoverability,
            "benchmark_validity": "unresolved",
            "attribution": attribution,
            "detected_by": "deterministic_comparison",
            "substantive": substantive,
            "adjudication_status": "pending",
            "adjudication_notes": None,
            "adjudicated_by": None,
            "adjudicated_at": None,
        }
    )


def _dimension_signals(dimensions: dict[str, Any]) -> list[str]:
    result = [
        f"{key}={value:.6f}"
        for key, value in sorted(dimensions.items())
        if isinstance(value, (int, float)) and _below_one(float(value))
    ]
    return result or ["aggregate_similarity_below_one"]


def _below_one(value: Any) -> bool:
    return isinstance(value, (int, float)) and float(value) < 1.0 - _EXACT_TOLERANCE


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value)
