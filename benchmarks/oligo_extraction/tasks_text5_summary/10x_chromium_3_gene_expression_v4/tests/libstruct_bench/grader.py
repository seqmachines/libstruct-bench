from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .matching import best_one_to_one_matching, edit_similarity
from .normalization import normalize_sequence, sequence_tokens
from .schema import (
    EXPECTED_INPUT_IDS,
    Oligo,
    ParsedGroundTruth,
    PredictionValidationError,
    parse_ground_truth_document,
    parse_prediction_document,
)


SIMILARITY_FLOOR = 0.70


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def grade_prediction(
    prediction_path: str | Path,
    groundtruth_path: str | Path,
    *,
    expected_protocol_id: str | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    try:
        prediction = parse_prediction_document(
            _load_json(prediction_path),
            expected_protocol_id=expected_protocol_id,
        )
        ground_truth = parse_ground_truth_document(_load_json(groundtruth_path))
        if expected_protocol_id is not None and ground_truth.protocol_id not in (None, expected_protocol_id):
            raise ValueError(
                f"ground truth protocol_id={ground_truth.protocol_id!r} does not match expected {expected_protocol_id!r}"
            )
    except (OSError, json.JSONDecodeError, PredictionValidationError, ValueError) as exc:
        return zero_metrics(), {"schema_valid": False, "error": str(exc), "protocol_id": expected_protocol_id}

    metrics = zero_metrics()
    metrics["schema_valid"] = 1.0
    metrics["expected_input_count"] = float(len(EXPECTED_INPUT_IDS))

    ground_truth_prepared = [_prepared_oligo(oligo) for oligo in ground_truth.oligos]
    per_input: dict[str, Any] = {}
    precision_values: list[float] = []
    recall_values: list[float] = []
    f1_values: list[float] = []

    for input_id in EXPECTED_INPUT_IDS:
        input_metrics, input_audit = _score_oligo_set(
            prediction.oligos_by_input.get(input_id, []),
            ground_truth_prepared,
        )
        per_input[input_id] = input_audit
        precision = input_metrics["sequence_precision"]
        recall = input_metrics["sequence_recall"]
        f1 = input_metrics["sequence_f1"]
        precision_values.append(precision)
        recall_values.append(recall)
        f1_values.append(f1)
        for metric_name, metric_value in input_metrics.items():
            metrics[f"{input_id}_{metric_name}"] = metric_value

    metrics["mean_sequence_precision"] = _mean(precision_values)
    metrics["mean_sequence_recall"] = _mean(recall_values)
    metrics["mean_sequence_f1"] = _mean(f1_values)
    metrics["min_sequence_recall"] = min(recall_values) if recall_values else 0.0
    metrics["reward"] = metrics["mean_sequence_f1"]

    audit = {
        "schema_valid": True,
        "protocol_id": prediction.protocol_id,
        "similarity_floor": SIMILARITY_FLOOR,
        "expected_input_ids": list(EXPECTED_INPUT_IDS),
        "metrics": metrics,
        "per_input": per_input,
    }
    return metrics, audit


def _score_oligo_set(
    predicted_oligos: list[Oligo],
    ground_truth: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, Any]]:
    predicted = [_prepared_oligo(oligo) for oligo in predicted_oligos]
    scores = [
        [edit_similarity(pred["tokens"], truth["tokens"]) for truth in ground_truth]
        for pred in predicted
    ]
    raw_matches = best_one_to_one_matching(scores)
    matches = [(pred_idx, gt_idx, score) for pred_idx, gt_idx, score in raw_matches if score >= SIMILARITY_FLOOR]
    score_sum = sum(score for _, _, score in matches)

    predicted_count = len(predicted)
    ground_truth_count = len(ground_truth)
    precision = _safe_ratio(score_sum, predicted_count, ground_truth_count == 0)
    recall = _safe_ratio(score_sum, ground_truth_count, predicted_count == 0)
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    exact_match = _exact_match(predicted, ground_truth, matches)
    name_scores = [
        name_similarity(predicted[pred_idx]["name"], ground_truth[gt_idx]["name"])
        for pred_idx, gt_idx, _ in matches
    ]
    name_similarity_mean = _mean(name_scores)

    metrics = {
        "sequence_precision": precision,
        "sequence_recall": recall,
        "sequence_f1": f1,
        "collapsed_summary_recall": recall,
        "collapsed_summary_f1": f1,
        "exact_match": exact_match,
        "name_similarity_mean": name_similarity_mean,
        "predicted_count": float(predicted_count),
        "ground_truth_count": float(ground_truth_count),
        "matched_count": float(len(matches)),
    }
    audit = {
        "metrics": metrics,
        "matches": [
            {
                "prediction_index": pred_idx,
                "ground_truth_index": gt_idx,
                "sequence_score": score,
                "sequence_exact": predicted[pred_idx]["normalized_sequence"]
                == ground_truth[gt_idx]["normalized_sequence"],
                "name_similarity": name_scores[position],
                "predicted_name": predicted[pred_idx]["name"],
                "ground_truth_name": ground_truth[gt_idx]["name"],
                "predicted_sequence": predicted[pred_idx]["normalized_sequence"],
                "ground_truth_sequence": ground_truth[gt_idx]["normalized_sequence"],
            }
            for position, (pred_idx, gt_idx, score) in enumerate(matches)
        ],
        "unmatched_predictions": sorted(set(range(predicted_count)) - {pred_idx for pred_idx, _, _ in matches}),
        "unmatched_ground_truth": sorted(set(range(ground_truth_count)) - {gt_idx for _, gt_idx, _ in matches}),
        "predicted_oligos": predicted,
        "ground_truth_oligos": ground_truth,
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
        for metric_name in (
            "sequence_precision",
            "sequence_recall",
            "sequence_f1",
            "collapsed_summary_recall",
            "collapsed_summary_f1",
            "exact_match",
            "name_similarity_mean",
            "predicted_count",
            "ground_truth_count",
            "matched_count",
        ):
            metrics[f"{input_id}_{metric_name}"] = 0.0
    return metrics


def name_similarity(predicted_name: str, ground_truth_name: str) -> float:
    predicted = _normalize_name(predicted_name)
    ground_truth = _normalize_name(ground_truth_name)
    return edit_similarity(predicted, ground_truth)


def _prepared_oligo(oligo: Oligo) -> dict[str, Any]:
    normalized_sequence = normalize_sequence(oligo.sequence)
    return {
        "name": oligo.name,
        "sequence": oligo.sequence,
        "normalized_sequence": normalized_sequence,
        "tokens": sequence_tokens(normalized_sequence, already_normalized=True),
    }


def _safe_ratio(numerator: float, denominator: int, empty_success: bool) -> float:
    if denominator == 0:
        return 1.0 if empty_success else 0.0
    return numerator / denominator


def _exact_match(
    predicted: list[dict[str, Any]],
    ground_truth: list[dict[str, Any]],
    matches: list[tuple[int, int, float]],
) -> float:
    if len(predicted) != len(ground_truth) or len(matches) != len(ground_truth):
        return 0.0
    return float(
        all(
            predicted[pred_idx]["normalized_sequence"] == ground_truth[gt_idx]["normalized_sequence"]
            for pred_idx, gt_idx, _ in matches
        )
    )


def _normalize_name(name: str) -> str:
    text = name.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
