from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Iterable, Mapping

from libstruct_bench.modalities import modality_key
from libstruct_bench.matching import best_one_to_one_matching, edit_similarity
from libstruct_bench.normalization import normalize_sequence, sequence_tokens
from libstruct_bench.libgen.validation import derive_required_t2_ids


SCORABLE_SUPPORT = frozenset({"explicit", "derivable"})
NEUTRAL_SUPPORT = frozenset(
    {"externally_completed", "ambiguous", "unsupported"}
)
ORDERED_MOLECULE_KINDS = frozenset({"single", "assembled", "hairpin"})

STATE_WEIGHTS = {
    "reference_strand": 0.40,
    "architecture": 0.25,
    "segments": 0.20,
    "pairing": 0.15,
}
TRANSITION_WEIGHTS = {
    "operation": 0.30,
    "substrates": 0.15,
    "products": 0.20,
    "disposition": 0.15,
    "oligos": 0.20,
}
LIBGEN_PUBLIC_METRIC_KEYS = (
    "reward",
    "t2_required_sequence_f1",
    "t2_all_required_exact",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)


def grade_libgen(
    t2_prediction: dict[str, Any],
    t3_prediction: dict[str, Any],
    t2_groundtruth: dict[str, Any],
    t3_groundtruth: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    """Return headline Harbor metrics plus detailed internal diagnostics."""

    required_oligo_ids = derive_required_t2_ids(t3_groundtruth)
    t2_metrics, t2_details = score_t2(
        t2_prediction["oligos"],
        t2_groundtruth["oligos"],
        required_oligo_ids=required_oligo_ids,
    )
    t3_metrics, t3_details = score_t3(
        t3_prediction,
        t3_groundtruth,
        t2_prediction=t2_prediction,
        t2_groundtruth=t2_groundtruth,
    )
    reward = (
        0.30 * t2_metrics["required_sequence_f1"]
        + 0.70 * t3_metrics["molecular_transition_f1"]
    )
    metrics = {
        "reward": reward,
        "t2_required_sequence_f1": t2_metrics["required_sequence_f1"],
        "t2_all_required_exact": t2_metrics["all_required_exact"],
        "t3_molecular_transition_f1": t3_metrics["molecular_transition_f1"],
        "t3_state_f1": t3_metrics["state_f1"],
        "t3_typed_edge_f1": t3_metrics["typed_edge_f1"],
    }
    return metrics, {
        "t2": t2_details,
        "t3": t3_details,
        "diagnostic_metrics": {
            "t2": t2_metrics,
            "t3": t3_metrics,
        },
    }


def score_t2(
    predictions: list[dict[str, Any]],
    groundtruth: list[dict[str, Any]],
    *,
    required_oligo_ids: set[str],
) -> tuple[dict[str, float], dict[str, Any]]:
    """Score source-recoverable T3-linked T2 claims by nucleotide sequence."""

    used_truth = {
        index
        for index, truth in enumerate(groundtruth)
        if truth["oligo_id"] in required_oligo_ids
    }
    scorable_truth = {
        index for index in used_truth if _oligo_is_scorable(groundtruth[index])
    }
    neutral_used_truth = used_truth - scorable_truth
    optional_truth = {
        index
        for index, truth in enumerate(groundtruth)
        if truth["oligo_id"] not in required_oligo_ids
    }
    neutral_truth = neutral_used_truth | optional_truth
    required_indices = sorted(scorable_truth)
    pre_neutralized_predictions = {
        prediction_index
        for prediction_index, prediction in enumerate(predictions)
        if any(
            _oligo_similarity(
                prediction,
                groundtruth[index],
                scorable_only=False,
            )
            == 1.0
            for index in neutral_truth
        )
        and not any(
            _oligo_similarity(
                prediction,
                groundtruth[index],
                scorable_only=True,
            )
            == 1.0
            for index in scorable_truth
        )
    }
    active_prediction_indices = [
        index
        for index in range(len(predictions))
        if index not in pre_neutralized_predictions
    ]
    required_scores = [
        [
            _oligo_similarity(
                predictions[prediction_index],
                groundtruth[index],
                scorable_only=True,
            )
            for index in required_indices
        ]
        for prediction_index in active_prediction_indices
    ]
    raw_matches = best_one_to_one_matching(required_scores)
    matches = [
        (
            active_prediction_indices[prediction_position],
            required_indices[required_position],
            score,
        )
        for prediction_position, required_position, score in raw_matches
    ]

    matched_predictions = {item[0] for item in matches}
    score_sum = 0.0
    match_details: list[dict[str, Any]] = []
    name_scores: list[float] = []
    role_scores: list[float] = []
    orientation_scores: list[float] = []
    alias_scores: list[float] = []
    modification_scores: list[float] = []
    kind_scores: list[float] = []

    for prediction_index, truth_index, sequence_score in matches:
        prediction = predictions[prediction_index]
        truth = groundtruth[truth_index]
        score_sum += sequence_score
        name_score = _name_similarity(prediction["name"], truth["name"])
        role_score = _name_similarity(prediction["role"], truth["role"])
        alias_score = _text_collection_f1(
            prediction.get("aliases", []), truth.get("aliases", [])
        )
        modification_score = _text_collection_f1(
            prediction.get("modifications", []), truth.get("modifications", [])
        )
        kind_score = float(prediction.get("kind") == truth.get("kind"))
        orientation_score = (
            None
            if truth.get("orientation") == "unknown"
            else float(prediction.get("orientation") == truth.get("orientation"))
        )
        name_scores.append(name_score)
        role_scores.append(role_score)
        alias_scores.append(alias_score)
        modification_scores.append(modification_score)
        kind_scores.append(kind_score)
        if orientation_score is not None:
            orientation_scores.append(orientation_score)
        match_details.append(
            {
                "prediction_index": prediction_index,
                "groundtruth_index": truth_index,
                "prediction_oligo_id": prediction.get("oligo_id"),
                "sequence_score": sequence_score,
                "groundtruth_oligo_id": truth["oligo_id"],
                "groundtruth_support_status": truth.get(
                    "support_status", "explicit"
                ),
                "scored": True,
                "dimension_scores": {
                    "sequence": sequence_score,
                    "name": name_score,
                    "role": role_score,
                    "aliases": alias_score,
                    "modifications": modification_score,
                    "kind": kind_score,
                    "orientation": orientation_score,
                },
            }
        )

    unmatched_predictions = set(range(len(predictions))) - matched_predictions
    neutralized_predictions: set[int] = set(pre_neutralized_predictions)
    for prediction_index in unmatched_predictions:
        if any(
            _oligo_similarity(
                predictions[prediction_index],
                groundtruth[index],
                scorable_only=False,
            )
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
    exact_required_matches = sum(score == 1.0 for _, _, score in matches)
    all_required_exact = float(
        exact_required_matches == len(scorable_truth)
        and effective_prediction_count == len(scorable_truth)
    )
    metrics = {
        "required_sequence_precision": precision,
        "required_sequence_recall": recall,
        "required_sequence_f1": f1,
        "all_required_exact": all_required_exact,
        "name_similarity": _mean(name_scores),
        "role_similarity": _mean(role_scores),
        "alias_f1": _mean(alias_scores),
        "modification_f1": _mean(modification_scores),
        "kind_accuracy": _mean(kind_scores),
        "orientation_accuracy": _mean(orientation_scores, empty=1.0),
        "predicted_count": float(len(predictions)),
        "used_groundtruth_count": float(len(used_truth)),
        "required_groundtruth_count": float(len(scorable_truth)),
        "scored_groundtruth_count": float(len(scorable_truth)),
        "neutral_used_groundtruth_count": float(len(neutral_used_truth)),
        "optional_groundtruth_count": float(len(optional_truth)),
        "neutralized_prediction_count": float(len(neutralized_predictions)),
    }
    details = {
        "matches": match_details,
        "unmatched_prediction_indices": sorted(
            unmatched_predictions - neutralized_predictions
        ),
        "unmatched_prediction_oligo_ids": sorted(
            predictions[index].get("oligo_id", f"prediction_{index}")
            for index in unmatched_predictions - neutralized_predictions
        ),
        "unmatched_required_oligo_ids": sorted(
            groundtruth[index]["oligo_id"]
            for index in scorable_truth
            - {truth_index for _, truth_index, _ in matches}
        ),
        "neutralized_prediction_indices": sorted(neutralized_predictions),
        "used_oligo_ids": sorted(
            groundtruth[index]["oligo_id"] for index in used_truth
        ),
        "required_oligo_ids": sorted(
            groundtruth[index]["oligo_id"] for index in scorable_truth
        ),
        "scored_oligo_ids": sorted(
            groundtruth[index]["oligo_id"] for index in scorable_truth
        ),
        "neutral_used_oligo_ids": sorted(
            groundtruth[index]["oligo_id"] for index in neutral_used_truth
        ),
        "optional_oligo_ids": sorted(
            groundtruth[index]["oligo_id"] for index in optional_truth
        ),
        "matching_policy": (
            "global maximum-weight one-to-one normalized Levenshtein "
            "molecule-sequence matching; ordered single, assembled, and hairpin "
            "components are concatenated while double-stranded components remain "
            "separate; prediction metadata never affects assignment or reward"
        ),
        "scope_policy": "O_used contains every T2 record referenced by T3; O_score is O_used restricted to explicit or derivable sequence claims",
        "recoverability_policy": "externally completed, ambiguous, and unsupported sequence claims remain canonical but are neutral in the source-only benchmark",
    }
    return metrics, details


def score_t3(
    prediction: dict[str, Any],
    groundtruth: dict[str, Any],
    *,
    t2_prediction: dict[str, Any],
    t2_groundtruth: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    predicted_by_modality = _workflows_by_modality(prediction)
    truth_by_modality = _workflows_by_modality(groundtruth)
    predicted_oligos = {
        item["oligo_id"]: item for item in t2_prediction.get("oligos", [])
    }
    truth_oligos = {
        item["oligo_id"]: item for item in t2_groundtruth.get("oligos", [])
    }

    state_score_sum = 0.0
    predicted_state_count = 0
    truth_state_count = 0
    transition_score_sum = 0.0
    predicted_transition_count = 0
    truth_transition_count = 0
    matched_typed_edges = 0
    predicted_typed_edges = 0
    truth_typed_edges = 0
    neutralized_typed_edges = 0
    reagent_scores: list[float] = []
    boundary_scores: list[float] = []
    modality_details: dict[str, Any] = {}

    for modality in sorted(set(predicted_by_modality) | set(truth_by_modality)):
        predicted_workflow = predicted_by_modality.get(modality)
        truth_workflow = truth_by_modality.get(modality)
        predicted_states = (
            list(predicted_workflow.get("states", [])) if predicted_workflow else []
        )
        truth_states = list(truth_workflow.get("states", [])) if truth_workflow else []
        state_scores = [
            [_state_similarity(item, truth, scorable_only=False) for truth in truth_states]
            for item in predicted_states
        ]
        state_matches = best_one_to_one_matching(state_scores)
        state_map = {
            predicted_states[prediction_index]["state_id"]: truth_states[truth_index]["state_id"]
            for prediction_index, truth_index, score in state_matches
            if score >= 0.25
        }
        state_counts, state_details, neutralized_state_predictions = _matched_entity_counts(
            predicted_states,
            truth_states,
            state_matches,
            state_scores,
            score=lambda item, truth: _state_similarity(
                item, truth, scorable_only=True
            ),
            scorable=_state_is_scorable,
        )
        for detail in state_details:
            prediction_index = detail["prediction_index"]
            truth_index = detail["groundtruth_index"]
            predicted_state = predicted_states[prediction_index]
            truth_state = truth_states[truth_index]
            detail.update(
                {
                    "prediction_state_id": predicted_state["state_id"],
                    "groundtruth_state_id": truth_state["state_id"],
                    "groundtruth_support_status": truth_state.get(
                        "support_status", "explicit"
                    ),
                    "dimension_scores": _enabled_dimension_scores(
                        _state_dimensions(
                            predicted_state,
                            truth_state,
                            scorable_only=True,
                        )
                    ),
                    "strand_orientation_accuracy": (
                        _matched_strand_orientation_accuracy(
                            predicted_state,
                            truth_state,
                        )
                    ),
                }
            )
        state_score_sum += state_counts[0]
        predicted_state_count += state_counts[1]
        truth_state_count += state_counts[2]

        predicted_transitions = (
            list(predicted_workflow.get("transitions", []))
            if predicted_workflow
            else []
        )
        truth_transitions = (
            list(truth_workflow.get("transitions", [])) if truth_workflow else []
        )
        transition_scores = [
            [
                _transition_similarity(
                    item,
                    truth,
                    state_map=state_map,
                    predicted_oligos=predicted_oligos,
                    truth_oligos=truth_oligos,
                )
                for truth in truth_transitions
            ]
            for item in predicted_transitions
        ]
        transition_matches = best_one_to_one_matching(transition_scores)
        transition_map = {
            predicted_transitions[prediction_index]["transition_id"]:
            truth_transitions[truth_index]["transition_id"]
            for prediction_index, truth_index, score in transition_matches
            if score >= 0.25
        }
        (
            transition_counts,
            transition_details,
            neutralized_transition_predictions,
        ) = _matched_entity_counts(
            predicted_transitions,
            truth_transitions,
            transition_matches,
            transition_scores,
            score=lambda item, truth: _transition_similarity(
                item,
                truth,
                state_map=state_map,
                predicted_oligos=predicted_oligos,
                truth_oligos=truth_oligos,
            ),
            scorable=_supported,
        )
        for detail in transition_details:
            prediction_index = detail["prediction_index"]
            truth_index = detail["groundtruth_index"]
            predicted_transition = predicted_transitions[prediction_index]
            truth_transition = truth_transitions[truth_index]
            detail.update(
                {
                    "prediction_transition_id": predicted_transition[
                        "transition_id"
                    ],
                    "groundtruth_transition_id": truth_transition[
                        "transition_id"
                    ],
                    "groundtruth_support_status": truth_transition.get(
                        "support_status", "explicit"
                    ),
                    "dimension_scores": (
                        _transition_dimensions(
                            predicted_transition,
                            truth_transition,
                            state_map=state_map,
                            predicted_oligos=predicted_oligos,
                            truth_oligos=truth_oligos,
                        )
                        if _supported(truth_transition)
                        else {}
                    ),
                }
            )
        transition_score_sum += transition_counts[0]
        predicted_transition_count += transition_counts[1]
        truth_transition_count += transition_counts[2]

        for prediction_index, truth_index, _ in transition_matches:
            if not _supported(truth_transitions[truth_index]):
                continue
            reagent_scores.append(
                _text_collection_f1(
                    [
                        item.get("name", "")
                        for item in predicted_transitions[prediction_index].get(
                            "major_reagents", []
                        )
                    ],
                    [
                        item.get("name", "")
                        for item in truth_transitions[truth_index].get(
                            "major_reagents", []
                        )
                    ],
                )
            )

        edge_counts = _typed_edge_counts(
            predicted_workflow,
            truth_workflow,
            state_map=state_map,
            transition_map=transition_map,
        )
        matched_typed_edges += edge_counts[0]
        predicted_typed_edges += edge_counts[1]
        truth_typed_edges += edge_counts[2]
        neutralized_typed_edges += edge_counts[3]

        if predicted_workflow and truth_workflow:
            truth_state_by_id = {
                item["state_id"]: item for item in truth_states
            }
            initial_score = _mapped_masked_boundary_f1(
                predicted_workflow["initial_state_ids"],
                truth_workflow["initial_state_ids"],
                state_map,
                truth_state_by_id,
            )
            final_score = _mapped_masked_boundary_f1(
                predicted_workflow["final_state_ids"],
                truth_workflow["final_state_ids"],
                state_map,
                truth_state_by_id,
            )
            boundary_scores.extend((initial_score, final_score))
        else:
            initial_score = final_score = 0.0

        modality_details[modality] = {
            "predicted_modality": (
                predicted_workflow.get("modality") if predicted_workflow else None
            ),
            "groundtruth_modality": (
                truth_workflow.get("modality") if truth_workflow else None
            ),
            "state_matches": state_details,
            "transition_matches": transition_details,
            "groundtruth_scorable": bool(
                any(_state_is_scorable(item) for item in truth_states)
                or any(_supported(item) for item in truth_transitions)
            ),
            "unmatched_prediction_state_ids": sorted(
                predicted_states[index]["state_id"]
                for index in set(range(len(predicted_states)))
                - {item[0] for item in state_matches}
                - neutralized_state_predictions
            ),
            "unmatched_groundtruth_state_ids": sorted(
                truth_states[index]["state_id"]
                for index in {
                    index
                    for index, item in enumerate(truth_states)
                    if _state_is_scorable(item)
                }
                - {item[1] for item in state_matches}
            ),
            "unmatched_prediction_transition_ids": sorted(
                predicted_transitions[index]["transition_id"]
                for index in set(range(len(predicted_transitions)))
                - {item[0] for item in transition_matches}
                - neutralized_transition_predictions
            ),
            "unmatched_groundtruth_transition_ids": sorted(
                truth_transitions[index]["transition_id"]
                for index in {
                    index
                    for index, item in enumerate(truth_transitions)
                    if _supported(item)
                }
                - {item[1] for item in transition_matches}
            ),
            "state_id_map": state_map,
            "transition_id_map": transition_map,
            "initial_boundary_f1": initial_score,
            "final_boundary_f1": final_score,
            "typed_edges": {
                "matched": edge_counts[0],
                "predicted": edge_counts[1],
                "groundtruth": edge_counts[2],
                "neutralized_predictions": edge_counts[3],
            },
        }

    state_prf = _soft_prf(
        state_score_sum, predicted_state_count, truth_state_count
    )
    transition_prf = _soft_prf(
        transition_score_sum,
        predicted_transition_count,
        truth_transition_count,
    )
    typed_edge_f1 = (
        1.0
        if predicted_typed_edges == truth_typed_edges == 0
        else (
            2.0 * matched_typed_edges
            / (predicted_typed_edges + truth_typed_edges)
            if predicted_typed_edges + truth_typed_edges
            else 0.0
        )
    )
    metrics = {
        "molecular_transition_precision": transition_prf[0],
        "molecular_transition_recall": transition_prf[1],
        "molecular_transition_f1": transition_prf[2],
        "typed_edge_f1": typed_edge_f1,
        "state_precision": state_prf[0],
        "state_recall": state_prf[1],
        "state_f1": state_prf[2],
        "boundary_f1": _mean(boundary_scores),
        "major_reagent_name_f1": _mean(reagent_scores),
        "predicted_workflow_count": float(len(predicted_by_modality)),
        "groundtruth_workflow_count": float(len(truth_by_modality)),
        "predicted_state_count": float(predicted_state_count),
        "groundtruth_state_count": float(truth_state_count),
        "predicted_transition_count": float(predicted_transition_count),
        "groundtruth_transition_count": float(truth_transition_count),
        "matched_typed_edge_count": float(matched_typed_edges),
        "predicted_typed_edge_count": float(predicted_typed_edges),
        "groundtruth_typed_edge_count": float(truth_typed_edges),
        "neutralized_typed_edge_count": float(neutralized_typed_edges),
    }
    details = {
        "modalities": modality_details,
        "workflow_matching_policy": "canonicalized modality aliases; one workflow per modality",
        "oligo_use_policy": "resolve transition-local T2 IDs to nucleotide sequence signatures and compare multisets directly",
        "weights": {
            "state": STATE_WEIGHTS,
            "transition": TRANSITION_WEIGHTS,
        },
    }
    return metrics, details


def _matched_entity_counts(
    predicted: list[dict[str, Any]],
    truth: list[dict[str, Any]],
    matches: list[tuple[int, int, float]],
    all_scores: list[list[float]],
    *,
    score: Callable[[dict[str, Any], dict[str, Any]], float],
    scorable: Callable[[dict[str, Any]], bool],
) -> tuple[tuple[float, int, int], list[dict[str, Any]], set[int]]:
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
                "neutralized": not is_scored and alignment_score == 1.0,
            }
        )
    for prediction_index in set(range(len(predicted))) - matched_predictions:
        if any(all_scores[prediction_index][index] == 1.0 for index in neutral_truth):
            neutralized_predictions.add(prediction_index)
    effective_prediction_count = len(predicted) - len(neutralized_predictions)
    return (
        (score_sum, effective_prediction_count, len(scorable_truth)),
        details,
        neutralized_predictions,
    )


def _oligo_is_scorable(item: dict[str, Any]) -> bool:
    return any(
        status in SCORABLE_SUPPORT
        for _, status in _truth_sequence_claims(item)
    )


def _truth_sequence_claims(item: dict[str, Any]) -> list[tuple[str, str]]:
    sequence = item.get("sequence")
    if isinstance(sequence, str) and sequence:
        return [(sequence, item.get("support_status", "explicit"))]
    claims: list[tuple[str, str]] = []
    for component in item.get("components", []):
        value = component.get("sequence") or component.get("placeholder")
        if isinstance(value, str) and value:
            claims.append(
                (
                    value,
                    component.get(
                        "support_status",
                        item.get("support_status", "explicit"),
                    ),
                )
            )
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
    ordered_similarity = _ordered_molecule_similarity(
        prediction,
        truth,
        scorable_only=scorable_only,
    )
    if ordered_similarity is not None:
        return ordered_similarity

    predicted_claims = _prediction_sequence_claims(prediction)
    truth_claims_with_status = _truth_sequence_claims(truth)
    truth_claims = [
        value
        for value, status in truth_claims_with_status
        if not scorable_only or status in SCORABLE_SUPPORT
    ]
    if scorable_only:
        neutral_claims = [
            value
            for value, status in truth_claims_with_status
            if status in NEUTRAL_SUPPORT
        ]
        predicted_claims = _remove_exact_neutral_sequence_claims(
            predicted_claims,
            neutral_claims,
        )
    return _sequence_collection_f1(predicted_claims, truth_claims)


def _ordered_molecule_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> float | None:
    """Compare equivalent flat and ordered-component molecule representations."""

    if truth.get("kind") not in ORDERED_MOLECULE_KINDS:
        return None
    if scorable_only and not all(
        status in SCORABLE_SUPPORT for _, status in _truth_sequence_claims(truth)
    ):
        # Component-level claims preserve the support mask for mixed-evidence
        # molecules. The fallback path below can remove exact neutral claims.
        return None
    predicted_sequence = _ordered_molecule_sequence(prediction)
    truth_sequence = _ordered_molecule_sequence(truth)
    if predicted_sequence is None or truth_sequence is None:
        return None
    return edit_similarity(
        _sequence_value(predicted_sequence),
        _sequence_value(truth_sequence),
    )


def _ordered_molecule_sequence(item: dict[str, Any]) -> str | None:
    sequence = item.get("sequence")
    if isinstance(sequence, str) and sequence:
        return sequence
    components = item.get("components", [])
    if not components:
        return None
    values: list[str] = []
    for component in components:
        value = component.get("sequence") or component.get("placeholder")
        if not isinstance(value, str) or not value:
            return None
        values.append(value)
    return "".join(values)


def _remove_exact_neutral_sequence_claims(
    predicted: list[str], neutral: list[str]
) -> list[str]:
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
    return _weighted_supported_score(
        _state_dimensions(
            prediction,
            truth,
            scorable_only=scorable_only,
        ),
        STATE_WEIGHTS,
    )


def _state_dimensions(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> dict[str, tuple[float, bool]]:
    truth_reference = _reference_strand(truth)
    return {
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


def _matched_strand_orientation_accuracy(
    prediction: dict[str, Any],
    truth: dict[str, Any],
) -> float | None:
    predicted_strands = list(prediction.get("strands", []))
    truth_strands = [
        strand for strand in truth.get("strands", []) if _supported(strand)
    ]
    if not truth_strands:
        return None
    scores = [
        [_strand_similarity(left, right) for right in truth_strands]
        for left in predicted_strands
    ]
    matches = best_one_to_one_matching(scores)
    if not matches:
        return None
    return _mean(
        [
            float(
                predicted_strands[prediction_index].get("orientation")
                == truth_strands[truth_index].get("orientation")
            )
            for prediction_index, truth_index, _ in matches
        ]
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
    predicted_oligos: Mapping[str, dict[str, Any]],
    truth_oligos: Mapping[str, dict[str, Any]],
) -> float:
    dimensions = _transition_dimensions(
        prediction,
        truth,
        state_map=state_map,
        predicted_oligos=predicted_oligos,
        truth_oligos=truth_oligos,
    )
    return sum(TRANSITION_WEIGHTS[key] * value for key, value in dimensions.items())


def _transition_dimensions(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    state_map: dict[str, str],
    predicted_oligos: Mapping[str, dict[str, Any]],
    truth_oligos: Mapping[str, dict[str, Any]],
) -> dict[str, float]:
    return {
        "operation": float(prediction.get("operation") == truth.get("operation")),
        "substrates": _mapped_set_f1(
            prediction.get("substrate_state_ids", []),
            set(truth.get("substrate_state_ids", [])),
            state_map,
        ),
        "products": _mapped_set_f1(
            prediction.get("product_state_ids", []),
            set(truth.get("product_state_ids", [])),
            state_map,
        ),
        "oligos": _transition_oligo_sequence_f1(
            prediction.get("oligo_ids", []),
            truth.get("oligo_ids", []),
            predicted_oligos=predicted_oligos,
            truth_oligos=truth_oligos,
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
    }


def _transition_oligo_sequence_f1(
    predicted_ids: Iterable[str],
    truth_ids: Iterable[str],
    *,
    predicted_oligos: Mapping[str, dict[str, Any]],
    truth_oligos: Mapping[str, dict[str, Any]],
) -> float:
    predicted = [predicted_oligos[item] for item in predicted_ids]
    truth = [truth_oligos[item] for item in truth_ids]
    scorable_truth = {
        index for index, item in enumerate(truth) if _oligo_is_scorable(item)
    }
    neutral_truth = set(range(len(truth))) - scorable_truth
    scorable_indices = sorted(scorable_truth)
    pre_neutralized_predictions = {
        prediction_index
        for prediction_index, prediction in enumerate(predicted)
        if any(
            _oligo_similarity(
                prediction,
                truth[index],
                scorable_only=False,
            )
            == 1.0
            for index in neutral_truth
        )
        and not any(
            _oligo_similarity(
                prediction,
                truth[index],
                scorable_only=True,
            )
            == 1.0
            for index in scorable_truth
        )
    }
    active_prediction_indices = [
        index
        for index in range(len(predicted))
        if index not in pre_neutralized_predictions
    ]
    scores = [
        [
            _oligo_similarity(
                predicted[prediction_index],
                truth[index],
                scorable_only=True,
            )
            for index in scorable_indices
        ]
        for prediction_index in active_prediction_indices
    ]
    raw_matches = best_one_to_one_matching(scores)
    matches = [
        (
            active_prediction_indices[prediction_position],
            scorable_indices[truth_position],
            score,
        )
        for prediction_position, truth_position, score in raw_matches
    ]
    matched_predictions = {item[0] for item in matches}
    neutralized_predictions: set[int] = set(pre_neutralized_predictions)
    for prediction_index in set(range(len(predicted))) - matched_predictions:
        if any(
            _oligo_similarity(
                predicted[prediction_index],
                truth[index],
                scorable_only=False,
            )
            == 1.0
            for index in neutral_truth
        ):
            neutralized_predictions.add(prediction_index)
    return _soft_prf(
        sum(item[2] for item in matches),
        len(predicted) - len(neutralized_predictions),
        len(scorable_truth),
    )[2]


def _workflows_by_modality(
    document: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        modality_key(workflow["modality"]): workflow
        for workflow in document.get("workflows", [])
    }


def _typed_edges(
    workflow: Mapping[str, Any],
    *,
    support: str | None = None,
) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for transition in workflow.get("transitions", []):
        if support == "scorable" and not _supported(transition):
            continue
        if support == "neutral" and _supported(transition):
            continue
        transition_id = transition["transition_id"]
        result.update(
            ("substrate", state_id, transition_id)
            for state_id in transition.get("substrate_state_ids", [])
        )
        result.update(
            ("carried_product", transition_id, state_id)
            for state_id in transition.get("carried_forward_product_ids", [])
        )
        result.update(
            ("discarded_product", transition_id, state_id)
            for state_id in transition.get("discarded_product_ids", [])
        )
    return result


def _typed_edge_counts(
    predicted_workflow: Mapping[str, Any] | None,
    truth_workflow: Mapping[str, Any] | None,
    *,
    state_map: Mapping[str, str],
    transition_map: Mapping[str, str],
) -> tuple[int, int, int, int]:
    predicted_edges = _typed_edges(predicted_workflow or {})
    if truth_workflow is None:
        return 0, len(predicted_edges), 0, 0
    scorable_truth_edges = _typed_edges(truth_workflow, support="scorable")
    neutral_truth_edges = _typed_edges(truth_workflow, support="neutral")
    if predicted_workflow is None:
        return 0, 0, len(scorable_truth_edges), 0

    mapped_predicted: set[tuple[str, str, str]] = set()
    for edge_type, left, right in predicted_edges:
        if edge_type == "substrate":
            mapped_left = state_map.get(left)
            mapped_right = transition_map.get(right)
        else:
            mapped_left = transition_map.get(left)
            mapped_right = state_map.get(right)
        if mapped_left is not None and mapped_right is not None:
            mapped_predicted.add((edge_type, mapped_left, mapped_right))
    matched = len(mapped_predicted & scorable_truth_edges)
    neutralized = len(mapped_predicted & neutral_truth_edges)
    effective_prediction_count = len(predicted_edges) - neutralized
    return matched, effective_prediction_count, len(scorable_truth_edges), neutralized


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


def _enabled_dimension_scores(
    dimensions: dict[str, tuple[float, bool]],
) -> dict[str, float | None]:
    return {
        key: value if enabled else None
        for key, (value, enabled) in dimensions.items()
    }


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
