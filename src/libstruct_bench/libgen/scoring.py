from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Iterable

from libstruct_bench.matching import best_one_to_one_matching, edit_similarity
from libstruct_bench.normalization import normalize_sequence, sequence_tokens


SCORABLE_SUPPORT = frozenset({"explicit", "derivable"})
NEUTRAL_SUPPORT = frozenset(
    {"externally_completed", "ambiguous", "unsupported"}
)

STATE_WEIGHTS = {
    "reference_strand": 0.40,
    "architecture": 0.25,
    "segments": 0.20,
    "pairing": 0.15,
}
TRANSITION_WEIGHTS = {
    "operation": 0.30,
    "topology": 0.35,
    "oligos": 0.15,
    "disposition": 0.10,
    "reagents": 0.10,
}


def grade_libgen(
    t2_prediction: dict[str, Any],
    t3_prediction: dict[str, Any],
    t2_groundtruth: dict[str, Any],
    t3_groundtruth: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    """Score linked T2/T3 predictions without relying on generated IDs."""

    t2_metrics, t2_details, oligo_map = score_t2(
        t2_prediction["oligos"], t2_groundtruth["oligos"]
    )
    t3_metrics, t3_details = score_t3(
        t3_prediction,
        t3_groundtruth,
        oligo_map=oligo_map,
    )
    reward = 0.30 * t2_metrics["sequence_f1"] + 0.70 * t3_metrics["t3_score"]
    metrics = {
        "reward": reward,
        "t2_score": t2_metrics["sequence_f1"],
        "t3_score": t3_metrics["t3_score"],
        **{f"t2_{key}": value for key, value in t2_metrics.items()},
        **{
            f"t3_{key}": value
            for key, value in t3_metrics.items()
            if key != "t3_score"
        },
    }
    return metrics, {"t2": t2_details, "t3": t3_details}


def score_t2(
    predictions: list[dict[str, Any]],
    groundtruth: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, Any], dict[str, str]]:
    """Sequence-only global one-to-one T2 scoring with recoverability masking."""

    all_scores = [
        [_oligo_similarity(prediction, truth, scorable_only=False) for truth in groundtruth]
        for prediction in predictions
    ]
    assignment_scores = [
        [
            score + 1e-9 * _name_similarity(prediction["name"], truth["name"])
            for score, truth in zip(row, groundtruth, strict=True)
        ]
        for prediction, row in zip(predictions, all_scores, strict=True)
    ]
    matches = best_one_to_one_matching(assignment_scores)
    actual_matches = [
        (prediction_index, truth_index, all_scores[prediction_index][truth_index])
        for prediction_index, truth_index, _ in matches
    ]

    scorable_truth = {
        index for index, truth in enumerate(groundtruth) if _oligo_is_scorable(truth)
    }
    neutral_truth = set(range(len(groundtruth))) - scorable_truth
    matched_predictions: set[int] = set()
    score_sum = 0.0
    oligo_map: dict[str, str] = {}
    match_details: list[dict[str, Any]] = []
    name_scores: list[float] = []
    role_scores: list[float] = []
    orientation_scores: list[float] = []
    alias_scores: list[float] = []
    modification_scores: list[float] = []
    kind_scores: list[float] = []

    for prediction_index, truth_index, all_score in actual_matches:
        prediction = predictions[prediction_index]
        truth = groundtruth[truth_index]
        matched_predictions.add(prediction_index)
        if all_score >= 0.80:
            oligo_map[prediction["oligo_id"]] = truth["oligo_id"]
        scored = truth_index in scorable_truth
        sequence_score = (
            _oligo_similarity(prediction, truth, scorable_only=True)
            if scored
            else None
        )
        if sequence_score is not None:
            score_sum += sequence_score
        name_score = _name_similarity(prediction["name"], truth["name"])
        name_scores.append(name_score)
        role_scores.append(_name_similarity(prediction["role"], truth["role"]))
        alias_scores.append(
            _text_collection_f1(prediction.get("aliases", []), truth.get("aliases", []))
        )
        modification_scores.append(
            _text_collection_f1(
                prediction.get("modifications", []), truth.get("modifications", [])
            )
        )
        kind_scores.append(float(prediction.get("kind") == truth.get("kind")))
        if truth.get("orientation") != "unknown":
            orientation_scores.append(
                float(prediction.get("orientation") == truth.get("orientation"))
            )
        match_details.append(
            {
                "prediction_index": prediction_index,
                "groundtruth_index": truth_index,
                "sequence_score": sequence_score,
                "alignment_sequence_score": all_score,
                "scored": scored,
            }
        )

    neutralized_predictions = {
        prediction_index
        for prediction_index, truth_index, score in actual_matches
        if truth_index in neutral_truth and score == 1.0
    }
    unmatched_predictions = set(range(len(predictions))) - matched_predictions
    for prediction_index in list(unmatched_predictions):
        if any(
            _oligo_similarity(predictions[prediction_index], groundtruth[index], scorable_only=False)
            == 1.0
            for index in neutral_truth
        ):
            neutralized_predictions.add(prediction_index)

    effective_prediction_count = len(predictions) - len(neutralized_predictions)
    precision, recall, f1 = _soft_prf(
        score_sum,
        effective_prediction_count,
        len(scorable_truth),
    )
    metrics = {
        "sequence_precision": precision,
        "sequence_recall": recall,
        "sequence_f1": f1,
        "name_similarity": _mean(name_scores),
        "role_similarity": _mean(role_scores),
        "alias_f1": _mean(alias_scores),
        "modification_f1": _mean(modification_scores),
        "kind_accuracy": _mean(kind_scores),
        "orientation_accuracy": _mean(orientation_scores, empty=1.0),
        "predicted_count": float(len(predictions)),
        "scorable_groundtruth_count": float(len(scorable_truth)),
        "neutral_groundtruth_count": float(len(neutral_truth)),
        "neutralized_prediction_count": float(len(neutralized_predictions)),
    }
    details = {
        "matches": match_details,
        "unmatched_prediction_indices": sorted(unmatched_predictions),
        "unmatched_groundtruth_indices": sorted(
            set(range(len(groundtruth))) - {truth_index for _, truth_index, _ in actual_matches}
        ),
        "neutralized_prediction_indices": sorted(neutralized_predictions),
        "matching_policy": "global one-to-one sequence similarity; names only break exact assignment ties",
        "recoverability_policy": "explicit and derivable claims scored; externally completed, ambiguous, and unsupported claims neutral",
    }
    return metrics, details, oligo_map


def score_t3(
    prediction: dict[str, Any],
    groundtruth: dict[str, Any],
    *,
    oligo_map: dict[str, str],
) -> tuple[dict[str, float], dict[str, Any]]:
    predicted_states, predicted_transitions, predicted_initial, predicted_final = _flatten_t3(
        prediction
    )
    truth_states, truth_transitions, truth_initial, truth_final = _flatten_t3(groundtruth)

    all_state_scores = [
        [_state_similarity(item, truth, scorable_only=False) for truth in truth_states]
        for item in predicted_states
    ]
    state_matches = best_one_to_one_matching(all_state_scores)
    state_map = {
        predicted_states[prediction_index]["state_id"]: truth_states[truth_index]["state_id"]
        for prediction_index, truth_index, score in state_matches
        if score >= 0.25
    }
    state_prf, state_details = _score_matched_entities(
        predicted_states,
        truth_states,
        state_matches,
        all_state_scores,
        score=lambda item, truth: _state_similarity(item, truth, scorable_only=True),
        scorable=_state_is_scorable,
    )

    all_transition_scores = [
        [
            _transition_similarity(
                item,
                truth,
                state_map=state_map,
                oligo_map=oligo_map,
            )
            for truth in truth_transitions
        ]
        for item in predicted_transitions
    ]
    transition_matches = best_one_to_one_matching(all_transition_scores)
    transition_prf, transition_details = _score_matched_entities(
        predicted_transitions,
        truth_transitions,
        transition_matches,
        all_transition_scores,
        score=lambda item, truth: _transition_similarity(
            item,
            truth,
            state_map=state_map,
            oligo_map=oligo_map,
        ),
        scorable=_supported,
    )

    truth_state_by_id = {item["state_id"]: item for item in truth_states}
    initial_score = _mapped_masked_boundary_f1(
        predicted_initial, truth_initial, state_map, truth_state_by_id
    )
    final_score = _mapped_masked_boundary_f1(
        predicted_final, truth_final, state_map, truth_state_by_id
    )
    boundary_score = (initial_score + final_score) / 2
    t3_score = (
        0.45 * state_prf[2]
        + 0.45 * transition_prf[2]
        + 0.10 * boundary_score
    )
    metrics = {
        "t3_score": t3_score,
        "state_precision": state_prf[0],
        "state_recall": state_prf[1],
        "state_f1": state_prf[2],
        "transition_precision": transition_prf[0],
        "transition_recall": transition_prf[1],
        "transition_f1": transition_prf[2],
        "initial_boundary_f1": initial_score,
        "final_boundary_f1": final_score,
        "boundary_f1": boundary_score,
        "predicted_state_count": float(len(predicted_states)),
        "groundtruth_state_count": float(len(truth_states)),
        "predicted_transition_count": float(len(predicted_transitions)),
        "groundtruth_transition_count": float(len(truth_transitions)),
    }
    details = {
        "state_matches": state_details,
        "transition_matches": transition_details,
        "state_id_map": state_map,
        "oligo_id_map": oligo_map,
        "weights": {
            "t3": {"states": 0.45, "transitions": 0.45, "boundaries": 0.10},
            "state": STATE_WEIGHTS,
            "transition": TRANSITION_WEIGHTS,
        },
    }
    return metrics, details


def _score_matched_entities(
    predicted: list[dict[str, Any]],
    truth: list[dict[str, Any]],
    matches: list[tuple[int, int, float]],
    all_scores: list[list[float]],
    *,
    score: Callable[[dict[str, Any], dict[str, Any]], float],
    scorable: Callable[[dict[str, Any]], bool],
) -> tuple[tuple[float, float, float], list[dict[str, Any]]]:
    scorable_truth = {index for index, item in enumerate(truth) if scorable(item)}
    neutral_truth = set(range(len(truth))) - scorable_truth
    score_sum = 0.0
    neutralized_predictions: set[int] = set()
    details: list[dict[str, Any]] = []
    matched_predictions = {prediction_index for prediction_index, _, _ in matches}
    for prediction_index, truth_index, alignment_score in matches:
        is_scored = truth_index in scorable_truth
        entity_score = score(predicted[prediction_index], truth[truth_index]) if is_scored else None
        if entity_score is not None:
            score_sum += entity_score
        if not is_scored and alignment_score == 1.0:
            neutralized_predictions.add(prediction_index)
        details.append(
            {
                "prediction_index": prediction_index,
                "groundtruth_index": truth_index,
                "score": entity_score,
                "alignment_score": alignment_score,
                "scored": is_scored,
            }
        )
    for prediction_index in set(range(len(predicted))) - matched_predictions:
        if any(all_scores[prediction_index][index] == 1.0 for index in neutral_truth):
            neutralized_predictions.add(prediction_index)
    effective_prediction_count = len(predicted) - len(neutralized_predictions)
    return (
        _soft_prf(score_sum, effective_prediction_count, len(scorable_truth)),
        details,
    )


def _oligo_is_scorable(item: dict[str, Any]) -> bool:
    return any(status in SCORABLE_SUPPORT for _, status in _truth_sequence_claims(item))


def _truth_sequence_claims(item: dict[str, Any]) -> list[tuple[str, str]]:
    sequence = item.get("sequence")
    if isinstance(sequence, str) and sequence:
        return [(sequence, item.get("support_status", "explicit"))]
    claims: list[tuple[str, str]] = []
    for component in item.get("components", []):
        value = component.get("sequence") or component.get("placeholder")
        if isinstance(value, str) and value:
            claims.append((value, component.get("support_status", item.get("support_status", "explicit"))))
    return claims


def _prediction_sequence_claims(item: dict[str, Any]) -> list[str]:
    sequence = item.get("sequence")
    if isinstance(sequence, str) and sequence:
        return [sequence]
    result: list[str] = []
    for component in item.get("components", []):
        value = component.get("sequence") or component.get("placeholder")
        if isinstance(value, str) and value:
            result.append(value)
    return result


def _oligo_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> float:
    predicted_claims = _prediction_sequence_claims(prediction)
    truth_claims_with_status = _truth_sequence_claims(truth)
    truth_claims = [
        value
        for value, status in truth_claims_with_status
        if not scorable_only or status in SCORABLE_SUPPORT
    ]
    if scorable_only:
        neutral_claims = [
            value for value, status in truth_claims_with_status if status in NEUTRAL_SUPPORT
        ]
        predicted_claims = _remove_exact_neutral_claims(predicted_claims, neutral_claims)
    return _sequence_collection_f1(predicted_claims, truth_claims)


def _remove_exact_neutral_claims(predicted: list[str], neutral: list[str]) -> list[str]:
    remaining = list(predicted)
    for neutral_value in neutral:
        normalized = _sequence_value(neutral_value)
        for index, predicted_value in enumerate(remaining):
            if _sequence_value(predicted_value) == normalized:
                remaining.pop(index)
                break
    return remaining


def _sequence_collection_f1(predicted: list[str], truth: list[str]) -> float:
    scores = [
        [
            edit_similarity(_sequence_value(left), _sequence_value(right))
            for right in truth
        ]
        for left in predicted
    ]
    matches = best_one_to_one_matching(scores)
    return _soft_prf(sum(item[2] for item in matches), len(predicted), len(truth))[2]


def _sequence_value(value: str) -> list[str]:
    normalized = normalize_sequence(value)
    return sequence_tokens(normalized, already_normalized=True)


def _state_is_scorable(state: dict[str, Any]) -> bool:
    if _supported(state):
        return True
    for strand in state.get("strands", []):
        if _supported(strand):
            return True
    return any(_supported(item) for item in state.get("paired_regions", [])) or any(
        _supported(item) for item in state.get("discontinuities", [])
    )


def _state_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> float:
    truth_reference = _reference_strand(truth)
    dimensions: dict[str, tuple[float, bool]] = {
        "reference_strand": (
            _reference_similarity(prediction, truth),
            not scorable_only or _supported(truth_reference),
        ),
        "architecture": (
            _architecture_similarity(prediction, truth),
            not scorable_only or _supported(truth),
        ),
        "segments": (
            _strand_collection_similarity(prediction, truth, scorable_only=scorable_only),
            not scorable_only
            or any(_supported(strand) for strand in truth.get("strands", [])),
        ),
        "pairing": (
            _pairing_and_discontinuity_similarity(
                prediction, truth, scorable_only=scorable_only
            ),
            not scorable_only
            or _supported(truth)
            or any(_supported(item) for item in truth.get("paired_regions", []))
            or any(_supported(item) for item in truth.get("discontinuities", [])),
        ),
    }
    return _weighted_supported_score(dimensions, STATE_WEIGHTS)


def _reference_similarity(prediction: dict[str, Any], truth: dict[str, Any]) -> float:
    predicted = _reference_strand(prediction)
    expected = _reference_strand(truth)
    if not predicted or not expected:
        return 0.0
    predicted_value = _strand_architecture_value(predicted)
    expected_value = _strand_architecture_value(expected)
    return edit_similarity(_sequence_value(predicted_value), _sequence_value(expected_value))


def _reference_strand(state: dict[str, Any]) -> dict[str, Any]:
    reference_id = state.get("reference_strand_id")
    return next(
        (strand for strand in state.get("strands", []) if strand.get("strand_id") == reference_id),
        {},
    )


def _strand_architecture_value(strand: dict[str, Any]) -> str:
    architecture = strand.get("sequence_architecture")
    if isinstance(architecture, str) and architecture:
        return architecture
    return "".join(_segment_value(segment) for segment in strand.get("segments", []))


def _segment_value(segment: dict[str, Any]) -> str:
    if isinstance(segment.get("sequence"), str):
        return segment["sequence"]
    if isinstance(segment.get("placeholder"), str):
        return segment["placeholder"]
    if isinstance(segment.get("length"), int):
        return f"[VARIABLE:{segment['length']}]"
    return "[UNKNOWN]"


def _architecture_similarity(prediction: dict[str, Any], truth: dict[str, Any]) -> float:
    scores = [
        float(prediction.get("strand_architecture") == truth.get("strand_architecture")),
        _name_similarity(prediction.get("molecule_type", ""), truth.get("molecule_type", "")),
        _count_similarity(len(prediction.get("strands", [])), len(truth.get("strands", []))),
        _multiset_f1(
            [item.get("molecule_type", "") for item in prediction.get("strands", [])],
            [item.get("molecule_type", "") for item in truth.get("strands", [])],
        ),
        _name_similarity(prediction.get("physical_state", ""), truth.get("physical_state", "")),
        _multiset_f1(prediction.get("properties", []), truth.get("properties", [])),
    ]
    return sum(scores) / len(scores)


def _strand_collection_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> float:
    predicted = list(prediction.get("strands", []))
    expected = [
        strand
        for strand in truth.get("strands", [])
        if not scorable_only or _supported(strand)
    ]
    if scorable_only:
        neutral = [strand for strand in truth.get("strands", []) if not _supported(strand)]
        predicted = _remove_exact_neutral_entities(predicted, neutral, _strand_similarity)
    scores = [[_strand_similarity(left, right) for right in expected] for left in predicted]
    matches = best_one_to_one_matching(scores)
    return _soft_prf(sum(item[2] for item in matches), len(predicted), len(expected))[2]


def _strand_similarity(prediction: dict[str, Any], truth: dict[str, Any]) -> float:
    predicted_segments = prediction.get("segments", [])
    truth_segments = truth.get("segments", [])
    position_scores: list[float] = []
    for left, right in zip(predicted_segments, truth_segments):
        sequence_score = edit_similarity(
            _sequence_value(_segment_value(left)), _sequence_value(_segment_value(right))
        )
        role_score = _name_similarity(left.get("role", ""), right.get("role", ""))
        structural_score = float(
            left.get("structural_role") == right.get("structural_role")
        )
        position_scores.append(0.50 * sequence_score + 0.25 * role_score + 0.25 * structural_score)
    segment_score = _soft_prf(
        sum(position_scores), len(predicted_segments), len(truth_segments)
    )[2]
    return (
        0.75 * segment_score
        + 0.15 * float(prediction.get("molecule_type") == truth.get("molecule_type"))
        + 0.10 * float(prediction.get("orientation") == truth.get("orientation"))
    )


def _pairing_and_discontinuity_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> float:
    predicted_pairings = _pairing_descriptors(prediction, support=None)
    truth_pairings = _pairing_descriptors(
        truth, support="scorable" if scorable_only else None
    )
    predicted_discontinuities = _discontinuity_descriptors(prediction, support=None)
    truth_discontinuities = _discontinuity_descriptors(
        truth, support="scorable" if scorable_only else None
    )
    if scorable_only:
        predicted_pairings = _remove_exact_text_values(
            predicted_pairings, _pairing_descriptors(truth, support="neutral")
        )
        predicted_discontinuities = _remove_exact_text_values(
            predicted_discontinuities,
            _discontinuity_descriptors(truth, support="neutral"),
        )
    return (
        _text_collection_f1(predicted_pairings, truth_pairings)
        + _text_collection_f1(predicted_discontinuities, truth_discontinuities)
    ) / 2


def _pairing_descriptors(
    state: dict[str, Any], *, support: str | None
) -> list[str]:
    strands = {item["strand_id"]: item for item in state.get("strands", [])}
    segment_values = {
        segment["segment_id"]: f"{segment.get('role', '')}:{_segment_value(segment)}"
        for strand in state.get("strands", [])
        for segment in strand.get("segments", [])
    }
    result: list[str] = []
    for region in state.get("paired_regions", []):
        if support == "scorable" and not _supported(region):
            continue
        if support == "neutral" and _supported(region):
            continue
        sides: list[str] = []
        for key in ("side_1", "side_2"):
            side = region[key]
            molecule_type = strands.get(side["strand_id"], {}).get("molecule_type", "")
            values = [segment_values.get(segment_id, "") for segment_id in side["segment_ids"]]
            sides.append(f"{molecule_type}:{'|'.join(values)}")
        result.append(region.get("relationship", "") + ":" + "<>".join(sorted(sides)))
    return result


def _discontinuity_descriptors(
    state: dict[str, Any], *, support: str | None
) -> list[str]:
    segment_values = {
        segment["segment_id"]: f"{segment.get('role', '')}:{_segment_value(segment)}"
        for strand in state.get("strands", [])
        for segment in strand.get("segments", [])
    }
    result: list[str] = []
    for item in state.get("discontinuities", []):
        if support == "scorable" and not _supported(item):
            continue
        if support == "neutral" and _supported(item):
            continue
        result.append(
            f"{item.get('kind', '')}:{segment_values.get(item.get('after_segment_id'), '')}>"
            f"{segment_values.get(item.get('before_segment_id'), '')}"
        )
    return result


def _transition_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    state_map: dict[str, str],
    oligo_map: dict[str, str],
) -> float:
    dimensions = {
        "operation": float(prediction.get("operation") == truth.get("operation")),
        "topology": (
            _mapped_set_f1(
                prediction.get("substrate_state_ids", []),
                set(truth.get("substrate_state_ids", [])),
                state_map,
            )
            + _mapped_set_f1(
                prediction.get("product_state_ids", []),
                set(truth.get("product_state_ids", [])),
                state_map,
            )
        )
        / 2,
        "oligos": _mapped_set_f1(
            prediction.get("oligo_ids", []), set(truth.get("oligo_ids", [])), oligo_map
        ),
        "disposition": (
            _mapped_set_f1(
                prediction.get("carried_forward_product_ids", []),
                set(truth.get("carried_forward_product_ids", [])),
                state_map,
            )
            + _mapped_set_f1(
                prediction.get("discarded_product_ids", []),
                set(truth.get("discarded_product_ids", [])),
                state_map,
            )
        )
        / 2,
        "reagents": _text_collection_f1(
            [item.get("name", "") for item in prediction.get("major_reagents", [])],
            [item.get("name", "") for item in truth.get("major_reagents", [])],
        ),
    }
    return sum(TRANSITION_WEIGHTS[key] * value for key, value in dimensions.items())


def _flatten_t3(
    document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str], set[str]]:
    states: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    initial: set[str] = set()
    final: set[str] = set()
    for workflow in document.get("workflows", []):
        states.extend(workflow.get("states", []))
        transitions.extend(workflow.get("transitions", []))
        initial.update(workflow.get("initial_state_ids", []))
        final.update(workflow.get("final_state_ids", []))
    return states, transitions, initial, final


def _mapped_set_f1(
    predicted_ids: Iterable[str], truth_ids: Iterable[str], mapping: dict[str, str]
) -> float:
    mapped = {mapping[item] for item in predicted_ids if item in mapping}
    truth = set(truth_ids)
    overlap = len(mapped & truth)
    return _soft_prf(float(overlap), len(set(predicted_ids)), len(truth))[2]


def _mapped_masked_boundary_f1(
    predicted_ids: Iterable[str],
    truth_ids: Iterable[str],
    mapping: dict[str, str],
    truth_states: dict[str, dict[str, Any]],
) -> float:
    predicted = list(predicted_ids)
    truth = set(truth_ids)
    scorable_truth = {
        item for item in truth if _state_is_scorable(truth_states[item])
    }
    neutral_truth = truth - scorable_truth
    mapped = [mapping.get(item) for item in predicted]
    effective_predictions = [item for item in mapped if item not in neutral_truth]
    overlap = len(set(effective_predictions) & scorable_truth)
    return _soft_prf(
        float(overlap), len(effective_predictions), len(scorable_truth)
    )[2]


def _text_collection_f1(predicted: list[str], truth: list[str]) -> float:
    scores = [[_name_similarity(left, right) for right in truth] for left in predicted]
    matches = best_one_to_one_matching(scores)
    return _soft_prf(sum(item[2] for item in matches), len(predicted), len(truth))[2]


def _remove_exact_text_values(predicted: list[str], neutral: list[str]) -> list[str]:
    remaining = list(predicted)
    for value in neutral:
        normalized = _normalize_text(value)
        for index, candidate in enumerate(remaining):
            if _normalize_text(candidate) == normalized:
                remaining.pop(index)
                break
    return remaining


def _remove_exact_neutral_entities(
    predicted: list[dict[str, Any]],
    neutral: list[dict[str, Any]],
    similarity: Callable[[dict[str, Any], dict[str, Any]], float],
) -> list[dict[str, Any]]:
    remaining = list(predicted)
    for neutral_item in neutral:
        for index, candidate in enumerate(remaining):
            if similarity(candidate, neutral_item) == 1.0:
                remaining.pop(index)
                break
    return remaining


def _multiset_f1(predicted: Iterable[str], truth: Iterable[str]) -> float:
    predicted_counter = Counter(_normalize_text(item) for item in predicted)
    truth_counter = Counter(_normalize_text(item) for item in truth)
    overlap = sum((predicted_counter & truth_counter).values())
    return _soft_prf(float(overlap), sum(predicted_counter.values()), sum(truth_counter.values()))[2]


def _supported(item: dict[str, Any]) -> bool:
    return item.get("support_status", "explicit") in SCORABLE_SUPPORT


def _weighted_supported_score(
    dimensions: dict[str, tuple[float, bool]], weights: dict[str, float]
) -> float:
    denominator = sum(weights[key] for key, (_, enabled) in dimensions.items() if enabled)
    if denominator == 0:
        return 0.0
    return sum(
        weights[key] * value
        for key, (value, enabled) in dimensions.items()
        if enabled
    ) / denominator


def _soft_prf(score_sum: float, predicted_count: int, truth_count: int) -> tuple[float, float, float]:
    precision = 1.0 if predicted_count == 0 and truth_count == 0 else (
        score_sum / predicted_count if predicted_count else 0.0
    )
    recall = 1.0 if truth_count == 0 and predicted_count == 0 else (
        score_sum / truth_count if truth_count else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _count_similarity(left: int, right: int) -> float:
    if left == right == 0:
        return 1.0
    return min(left, right) / max(left, right) if max(left, right) else 0.0


def _name_similarity(left: str, right: str) -> float:
    return edit_similarity(_normalize_text(left), _normalize_text(right))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _mean(values: list[float], *, empty: float = 0.0) -> float:
    return sum(values) / len(values) if values else empty
