from __future__ import annotations

import re
from typing import Any

from .library_structure import normalize_library_sequence
from .matching import best_one_to_one_matching, levenshtein_distance


PREDICTION_SCHEMA_VERSION = "libstruct.library_structure.v1"
GROUNDTRUTH_SCHEMA_VERSION = "libstruct.final_library_groundtruth.v1"


class AuditedLibraryValidationError(ValueError):
    """Raised when an audited T1 prediction or ground truth is invalid."""


def grade_audited_library_prediction(
    prediction_document: Any,
    ground_truth_document: Any,
    *,
    expected_protocol_id: str | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Grade audited T1 with identifier-aware global one-to-one matching."""

    protocol_id, predicted = parse_prediction_document(
        prediction_document, expected_protocol_id=expected_protocol_id
    )
    groundtruth_protocol_id, truth = parse_ground_truth_document(
        ground_truth_document, expected_protocol_id=expected_protocol_id
    )
    matches, unmatched_predictions = _match_entries(predicted, truth)
    score_sum = sum(item["sequence_similarity"] for item in matches)
    predicted_count = len(predicted)
    truth_count = len(truth)
    precision = score_sum / predicted_count if predicted_count else float(not truth_count)
    recall = score_sum / truth_count if truth_count else float(not predicted_count)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    exact = float(
        predicted_count == truth_count
        and all(item["sequence_similarity"] == 1.0 for item in matches)
    )
    metrics = {
        "reward": f1,
        "sequence_precision": precision,
        "sequence_recall": recall,
        "sequence_f1": f1,
        "exact_match": exact,
        "prediction_parse_valid": 1.0,
        "predicted_library_count": float(predicted_count),
        "ground_truth_library_count": float(truth_count),
        "matched_library_count": float(
            sum(item["predicted_index"] is not None for item in matches)
        ),
        "edit_distance": float(sum(item["edit_distance"] for item in matches)),
    }
    audit = {
        "protocol_id": protocol_id,
        "ground_truth_protocol_id": groundtruth_protocol_id,
        "matching_policy": (
            "unique normalized library_id, then unique normalized modality, "
            "then global maximum sequence similarity"
        ),
        "library_matches": matches,
        "unmatched_predictions": unmatched_predictions,
    }
    return metrics, audit


def parse_prediction_document(
    document: Any, *, expected_protocol_id: str | None = None
) -> tuple[str, list[dict[str, str]]]:
    parsed = _document(document, "prediction")
    if parsed.get("schema_version") != PREDICTION_SCHEMA_VERSION:
        raise AuditedLibraryValidationError(
            f"prediction.schema_version must be {PREDICTION_SCHEMA_VERSION!r}"
        )
    protocol_id = _protocol_id(parsed, "prediction", expected_protocol_id)
    entries = _entries(parsed, "prediction", scoring_filter=False)
    return protocol_id, entries


def parse_ground_truth_document(
    document: Any, *, expected_protocol_id: str | None = None
) -> tuple[str, list[dict[str, str]]]:
    parsed = _document(document, "ground truth")
    if parsed.get("schema_version") != GROUNDTRUTH_SCHEMA_VERSION:
        raise AuditedLibraryValidationError(
            f"ground_truth.schema_version must be {GROUNDTRUTH_SCHEMA_VERSION!r}"
        )
    protocol_id = _protocol_id(parsed, "ground truth", expected_protocol_id)
    entries = _entries(parsed, "ground truth", scoring_filter=True)
    if not entries:
        raise AuditedLibraryValidationError(
            "ground truth has no benchmark-included final libraries"
        )
    return protocol_id, entries


def zero_metrics() -> dict[str, float]:
    return {
        "reward": 0.0,
        "sequence_precision": 0.0,
        "sequence_recall": 0.0,
        "sequence_f1": 0.0,
        "exact_match": 0.0,
        "prediction_parse_valid": 0.0,
        "predicted_library_count": 0.0,
        "ground_truth_library_count": 0.0,
        "matched_library_count": 0.0,
        "edit_distance": 0.0,
    }


def _document(document: Any, label: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise AuditedLibraryValidationError(f"{label} must be a JSON object")
    return document


def _protocol_id(
    document: dict[str, Any], label: str, expected: str | None
) -> str:
    value = document.get("protocol_id")
    if not isinstance(value, str) or not value.strip():
        raise AuditedLibraryValidationError(f"{label}.protocol_id must be non-empty")
    value = value.strip()
    if expected is not None and value != expected:
        raise AuditedLibraryValidationError(
            f"{label}.protocol_id={value!r} does not match expected {expected!r}"
        )
    return value


def _entries(
    document: dict[str, Any], label: str, *, scoring_filter: bool
) -> list[dict[str, str]]:
    raw = document.get("libraries")
    if not isinstance(raw, list) or not raw:
        raise AuditedLibraryValidationError(f"{label}.libraries must be non-empty")
    entries: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise AuditedLibraryValidationError(
                f"{label}.libraries[{index}] must be an object"
            )
        if scoring_filter and item.get("benchmark_status", "included") == "excluded":
            continue
        values: dict[str, str] = {}
        for key in ("library_id", "modality", "library_sequence"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                raise AuditedLibraryValidationError(
                    f"{label}.libraries[{index}].{key} must be non-empty"
                )
            values[key] = value.strip()
        values["normalized_id"] = _normalize_identifier(values["library_id"])
        values["normalized_modality"] = _normalize_identifier(values["modality"])
        values["normalized_sequence"] = normalize_library_sequence(
            values["library_sequence"]
        )
        values["original_index"] = str(index)
        entries.append(values)
    normalized_ids = [entry["normalized_id"] for entry in entries]
    if len(normalized_ids) != len(set(normalized_ids)):
        raise AuditedLibraryValidationError(
            f"{label} contains duplicate normalized library_id values"
        )
    return entries


def _match_entries(
    predicted: list[dict[str, str]], truth: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], list[int]]:
    assignments: dict[int, tuple[int, str]] = {}
    available_pred = set(range(len(predicted)))
    available_truth = set(range(len(truth)))

    pred_ids = {entry["normalized_id"]: index for index, entry in enumerate(predicted)}
    truth_ids = {entry["normalized_id"]: index for index, entry in enumerate(truth)}
    for key in sorted(set(pred_ids) & set(truth_ids)):
        pred_index = pred_ids[key]
        truth_index = truth_ids[key]
        assignments[truth_index] = (pred_index, "library_id")
        available_pred.remove(pred_index)
        available_truth.remove(truth_index)

    pred_modalities = _unique_values(predicted, available_pred, "normalized_modality")
    truth_modalities = _unique_values(truth, available_truth, "normalized_modality")
    for key in sorted(set(pred_modalities) & set(truth_modalities)):
        pred_index = pred_modalities[key]
        truth_index = truth_modalities[key]
        assignments[truth_index] = (pred_index, "modality")
        available_pred.remove(pred_index)
        available_truth.remove(truth_index)

    pred_remaining = sorted(available_pred)
    truth_remaining = sorted(available_truth)
    scores = [
        [
            _sequence_similarity(
                predicted[pred_index]["normalized_sequence"],
                truth[truth_index]["normalized_sequence"],
            )[0]
            for truth_index in truth_remaining
        ]
        for pred_index in pred_remaining
    ]
    for pred_local, truth_local, _ in best_one_to_one_matching(scores):
        pred_index = pred_remaining[pred_local]
        truth_index = truth_remaining[truth_local]
        assignments[truth_index] = (pred_index, "global_sequence")
        available_pred.remove(pred_index)

    matches: list[dict[str, Any]] = []
    for truth_index, truth_entry in enumerate(truth):
        assignment = assignments.get(truth_index)
        if assignment is None:
            predicted_index = None
            predicted_sequence = ""
            similarity = 0.0
            distance = len(truth_entry["normalized_sequence"])
            matched_by = "unmatched"
            predicted_id = None
        else:
            predicted_index, matched_by = assignment
            predicted_entry = predicted[predicted_index]
            predicted_sequence = predicted_entry["normalized_sequence"]
            similarity, distance = _sequence_similarity(
                predicted_sequence, truth_entry["normalized_sequence"]
            )
            predicted_id = predicted_entry["library_id"]
        matches.append(
            {
                "ground_truth_index": int(truth_entry["original_index"]),
                "predicted_index": predicted_index,
                "library_id": truth_entry["library_id"],
                "modality": truth_entry["modality"],
                "predicted_library_id": predicted_id,
                "matched_by": matched_by,
                "predicted_sequence": predicted_sequence,
                "ground_truth_sequence": truth_entry["normalized_sequence"],
                "edit_distance": distance,
                "sequence_similarity": similarity,
            }
        )
    return matches, sorted(available_pred)


def _unique_values(
    entries: list[dict[str, str]], indexes: set[int], key: str
) -> dict[str, int]:
    grouped: dict[str, list[int]] = {}
    for index in indexes:
        grouped.setdefault(entries[index][key], []).append(index)
    return {value: values[0] for value, values in grouped.items() if len(values) == 1}


def _sequence_similarity(left: str, right: str) -> tuple[float, int]:
    distance = levenshtein_distance(left, right)
    denominator = max(len(left), len(right))
    return (1.0 if denominator == 0 else max(0.0, 1.0 - distance / denominator), distance)


def _normalize_identifier(value: str) -> str:
    folded = value.casefold()
    return re.sub(r"_+", "_", "".join(
        character if character.isalnum() else "_" for character in folded
    )).strip("_")
