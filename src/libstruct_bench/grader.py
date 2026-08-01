from __future__ import annotations

import re
from typing import Any

from libstruct_bench.matching import best_one_to_one_matching, edit_similarity
from libstruct_bench.normalization import normalize_sequence, sequence_tokens
from libstruct_bench.schema import (
    Oligo,
    parse_ground_truth_document,
    parse_prediction_document,
)


def grade_prediction(
    prediction_document: Any,
    ground_truth_document: Any,
    *,
    expected_protocol_id: str | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Grade an oligo extraction prediction against ground truth."""

    protocol_id, predicted_oligos = parse_prediction_document(
        prediction_document,
        expected_protocol_id=expected_protocol_id,
    )
    ground_truth_protocol_id, ground_truth_oligos = parse_ground_truth_document(ground_truth_document)
    if expected_protocol_id is not None and ground_truth_protocol_id not in (None, expected_protocol_id):
        raise ValueError(
            f"ground truth protocol_id={ground_truth_protocol_id!r} does not match expected {expected_protocol_id!r}"
        )

    return grade_oligo_lists(
        protocol_id=protocol_id,
        predicted_oligos=predicted_oligos,
        ground_truth_oligos=ground_truth_oligos,
        ground_truth_protocol_id=ground_truth_protocol_id,
    )


def grade_oligo_lists(
    *,
    protocol_id: str,
    predicted_oligos: list[Oligo],
    ground_truth_oligos: list[Oligo],
    ground_truth_protocol_id: str | None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Score already validated oligo lists with global one-to-one matching."""

    predicted = [_prepared_oligo(oligo) for oligo in predicted_oligos]
    ground_truth = [_prepared_oligo(oligo) for oligo in ground_truth_oligos]

    scores = [
        [edit_similarity(pred["tokens"], truth["tokens"]) for truth in ground_truth]
        for pred in predicted
    ]
    matches = best_one_to_one_matching(scores)
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
    name_similarity_mean = sum(name_scores) / len(name_scores) if name_scores else 0.0
    direction_pairs = [
        (predicted_oligos[pred_idx].direction, ground_truth_oligos[gt_idx].direction)
        for pred_idx, gt_idx, _ in matches
        if ground_truth_oligos[gt_idx].direction != "unknown"
    ]
    direction_accuracy = (
        sum(predicted_direction == truth_direction for predicted_direction, truth_direction in direction_pairs)
        / len(direction_pairs)
        if direction_pairs
        else 1.0
    )

    metrics = {
        "reward": f1,
        "sequence_precision": precision,
        "sequence_recall": recall,
        "sequence_f1": f1,
        "exact_match": exact_match,
        "name_similarity_mean": name_similarity_mean,
        "direction_accuracy": direction_accuracy,
        "predicted_count": float(predicted_count),
        "ground_truth_count": float(ground_truth_count),
        "matched_count": float(len(matches)),
        "schema_valid": 1.0,
    }

    audit = {
        "protocol_id": protocol_id,
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
                "predicted_direction": predicted_oligos[pred_idx].direction,
                "ground_truth_direction": ground_truth_oligos[gt_idx].direction,
            }
            for position, (pred_idx, gt_idx, score) in enumerate(matches)
        ],
        "unmatched_predictions": sorted(set(range(predicted_count)) - {pred_idx for pred_idx, _, _ in matches}),
        "unmatched_ground_truth": sorted(set(range(ground_truth_count)) - {gt_idx for _, gt_idx, _ in matches}),
    }
    return metrics, audit


def zero_metrics() -> dict[str, float]:
    return {
        "reward": 0.0,
        "sequence_precision": 0.0,
        "sequence_recall": 0.0,
        "sequence_f1": 0.0,
        "exact_match": 0.0,
        "name_similarity_mean": 0.0,
        "direction_accuracy": 0.0,
        "predicted_count": 0.0,
        "ground_truth_count": 0.0,
        "matched_count": 0.0,
        "schema_valid": 0.0,
    }


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
