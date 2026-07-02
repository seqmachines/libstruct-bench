"""Sequence-only grader for parser-control text5 tasks."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .matching import best_one_to_one_matching, edit_similarity
from .normalization import normalize_sequence, sequence_tokens
from .schema import (
    EXPECTED_INPUT_IDS,
    SchemaError,
    parse_ground_truth_document,
    parse_prediction_document,
)


SIMILARITY_FLOOR = 0.70


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _dedupe_normalized(sequences: Iterable[str]) -> list[dict[str, Any]]:
    seen: dict[str, str] = {}
    for sequence in sequences:
        normalized = normalize_sequence(sequence)
        if not normalized:
            continue
        seen.setdefault(normalized, sequence)
    return [
        {
            "sequence": original,
            "normalized_sequence": normalized,
            "tokens": sequence_tokens(normalized, already_normalized=True),
        }
        for normalized, original in seen.items()
    ]


def _score_sequence_set(predicted_sequences: list[str], truth_sequences: list[str]) -> tuple[dict[str, float], dict[str, Any]]:
    predicted = _dedupe_normalized(predicted_sequences)
    truth = _dedupe_normalized(truth_sequences)

    scores = [
        [edit_similarity(pred["tokens"], gt["tokens"]) for gt in truth]
        for pred in predicted
    ]
    matches = [
        (pred_idx, gt_idx, score)
        for pred_idx, gt_idx, score in best_one_to_one_matching(scores)
        if score >= SIMILARITY_FLOOR
    ]

    similarity_sum = sum(score for _, _, score in matches)
    predicted_count = len(predicted)
    truth_count = len(truth)
    precision = similarity_sum / predicted_count if predicted_count else 0.0
    recall = similarity_sum / truth_count if truth_count else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    metrics = {
        "sequence_precision": precision,
        "sequence_recall": recall,
        "sequence_f1": f1,
        "matched_sequence_count": float(len(matches)),
        "predicted_sequence_count": float(predicted_count),
        "ground_truth_sequence_count": float(truth_count),
    }

    audit = {
        "metrics": metrics,
        "matched_sequences": [
            {
                "prediction_index": pred_idx,
                "ground_truth_index": gt_idx,
                "sequence_score": score,
                "sequence_exact": predicted[pred_idx]["normalized_sequence"]
                == truth[gt_idx]["normalized_sequence"],
                "predicted_sequence": predicted[pred_idx]["sequence"],
                "ground_truth_sequence": truth[gt_idx]["sequence"],
                "predicted_normalized_sequence": predicted[pred_idx]["normalized_sequence"],
                "ground_truth_normalized_sequence": truth[gt_idx]["normalized_sequence"],
            }
            for pred_idx, gt_idx, score in matches
        ],
        "unmatched_predictions": sorted(
            set(range(predicted_count)) - {pred_idx for pred_idx, _, _ in matches}
        ),
        "unmatched_ground_truth": sorted(
            set(range(truth_count)) - {gt_idx for _, gt_idx, _ in matches}
        ),
        "predicted_sequences": predicted,
        "ground_truth_sequences": truth,
    }
    return metrics, audit


def zero_metrics() -> dict[str, float]:
    metrics: dict[str, float] = {
        "reward": 0.0,
        "schema_valid": 0.0,
        "mean_sequence_precision": 0.0,
        "mean_sequence_recall": 0.0,
        "mean_sequence_f1": 0.0,
        "min_sequence_recall": 0.0,
        "expected_input_count": float(len(EXPECTED_INPUT_IDS)),
    }
    for input_id in EXPECTED_INPUT_IDS:
        metrics[f"{input_id}_sequence_precision"] = 0.0
        metrics[f"{input_id}_sequence_recall"] = 0.0
        metrics[f"{input_id}_sequence_f1"] = 0.0
    return metrics


def grade_prediction(
    prediction_path: str | Path,
    groundtruth_path: str | Path,
    *,
    expected_protocol_id: str | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    try:
        prediction = parse_prediction_document(_load_json(prediction_path))
        ground_truth = parse_ground_truth_document(_load_json(groundtruth_path))
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        return zero_metrics(), {"schema_valid": False, "error": str(exc)}

    if expected_protocol_id and prediction.protocol_id != expected_protocol_id:
        return zero_metrics(), {
            "schema_valid": True,
            "error": f"protocol_id must be {expected_protocol_id!r}; got {prediction.protocol_id!r}",
        }
    if prediction.protocol_id != ground_truth.protocol_id:
        return zero_metrics(), {
            "schema_valid": True,
            "error": (
                f"prediction protocol_id {prediction.protocol_id!r} does not match "
                f"ground truth {ground_truth.protocol_id!r}"
            ),
        }

    metrics = zero_metrics()
    metrics["schema_valid"] = 1.0
    metrics["expected_input_count"] = float(len(ground_truth.expected_input_ids))

    per_input: dict[str, Any] = {}
    precision_values: list[float] = []
    recall_values: list[float] = []
    f1_values: list[float] = []

    for input_id in ground_truth.expected_input_ids:
        input_metrics, input_audit = _score_sequence_set(
            prediction.sequences_by_input.get(input_id, []),
            ground_truth.sequences,
        )
        per_input[input_id] = input_audit
        precision = input_metrics["sequence_precision"]
        recall = input_metrics["sequence_recall"]
        f1 = input_metrics["sequence_f1"]
        precision_values.append(precision)
        recall_values.append(recall)
        f1_values.append(f1)
        metrics[f"{input_id}_sequence_precision"] = precision
        metrics[f"{input_id}_sequence_recall"] = recall
        metrics[f"{input_id}_sequence_f1"] = f1

    metrics["mean_sequence_precision"] = sum(precision_values) / len(precision_values) if precision_values else 0.0
    metrics["mean_sequence_recall"] = sum(recall_values) / len(recall_values) if recall_values else 0.0
    metrics["mean_sequence_f1"] = sum(f1_values) / len(f1_values) if f1_values else 0.0
    metrics["min_sequence_recall"] = min(recall_values) if recall_values else 0.0
    metrics["reward"] = metrics["mean_sequence_f1"]

    audit = {
        "schema_valid": True,
        "protocol_id": prediction.protocol_id,
        "similarity_floor": SIMILARITY_FLOOR,
        "expected_input_ids": list(ground_truth.expected_input_ids),
        "per_input": per_input,
        "metrics": metrics,
    }
    return metrics, audit
