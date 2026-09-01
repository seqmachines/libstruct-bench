from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from libstruct_bench.modalities import modality_key
from libstruct_bench.matching import (
    best_one_to_one_matching,
    best_partial_one_to_one_matching,
    edit_similarity,
)
from libstruct_bench.normalization import normalize_sequence, sequence_tokens
from libstruct_bench.libgen.validation import derive_required_t2_ids
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION


SCORABLE_SUPPORT = frozenset({"explicit", "derivable"})
NEUTRAL_SUPPORT = frozenset({"externally_completed", "ambiguous", "unsupported"})
ORDERED_MOLECULE_KINDS = frozenset({"single", "assembled", "hairpin"})

T2_WEIGHTS = {
    "sequence": 0.65,
    "modifications": 0.15,
    "kind": 0.10,
    "orientation": 0.05,
    "role": 0.05,
}
_T2_ASSIGNMENT_TIE_BREAK = 1e-6
_T3_MIN_ASSIGNMENT_SCORE = 0.25
_STATE_BOUNDARY_TIE_BREAK = 0.10
_TRANSITION_IDENTITY_TIE_BREAK = 0.03

STATE_WEIGHTS = {
    "reference_strand": 0.50,
    "architecture": 0.15,
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

_STATE_COMPLEMENT = {
    "A": "T",
    "C": "G",
    "G": "C",
    "T": "A",
    "U": "A",
    "R": "Y",
    "Y": "R",
    "S": "S",
    "W": "W",
    "K": "M",
    "M": "K",
    "B": "V",
    "D": "H",
    "H": "D",
    "V": "B",
    "N": "N",
}
_ANCHOR_PLACEHOLDER_SPAN = ("<ANCHOR>", "<ANCHOR>")
_ANCHOR_IUPAC_SHORTHANDS = frozenset({("V", "N"), ("N", "B")})
_EQUIVALENT_OPERATION_GROUPS = (frozenset({"extension", "strand_synthesis"}),)
_CONTROLLED_PRIMER_ROLES = frozenset(
    {
        "primer",
        "reverse_transcription_primer",
        "second_strand_primer",
        "indexing_primer",
        "amplification_primer",
        "sequencing_primer",
        "index_sequencing_primer",
        "sgrna_primer",
        "linear_amplification_primer",
        "random_amplification_primer",
        "capture_oligo",
    }
)
_CONTROLLED_ADAPTER_ROLES = frozenset(
    {
        "adapter",
        "tagmentation_adapter",
        "ligation_adapter",
        "sequencing_adapter",
        "flowcell_adapter",
    }
)
LIBGEN_PUBLIC_METRIC_KEYS = (
    "reward",
    "t2_required_family_f1",
    "t2_exact_required_family_recall",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)

_FAMILY_PLACEHOLDER_RE = re.compile(r"\[([A-Z0-9_]+):([1-9][0-9]*)\]")
_FAMILY_TOKEN_RE = re.compile(r"^<([A-Z0-9_]+)>$")
_NUCLEOTIDE_TOKEN_RE = re.compile(r"^(?:[ACGTUNV]|[r+][ACGTUNV])$")
_VARIABLE_COMPONENT_EXCLUSIONS = frozenset(
    {
        "adapter",
        "arm",
        "capture arm",
        "handle",
        "hybridization",
        "hybridisation",
        "linker",
        "mosaic end",
        "primer binding",
    }
)


@dataclass(frozen=True)
class _PredictionFamily:
    representative_index: int
    member_indices: tuple[int, ...]
    signature: tuple[str, ...]


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
        0.30 * t2_metrics["required_family_f1"]
        + 0.70 * t3_metrics["molecular_transition_f1"]
    )
    metrics = {
        "reward": reward,
        "t2_required_family_f1": t2_metrics["required_family_f1"],
        "t2_exact_required_family_recall": t2_metrics["exact_required_family_recall"],
        "t3_molecular_transition_f1": t3_metrics["molecular_transition_f1"],
        "t3_state_f1": t3_metrics["state_f1"],
        "t3_typed_edge_f1": t3_metrics["typed_edge_f1"],
    }
    return metrics, {
        "benchmark_version": LIBGEN_BENCHMARK_VERSION,
        "t2": t2_details,
        "t3": t3_details,
        "diagnostic_metrics": {
            "t2": t2_metrics,
            "t3": t3_metrics,
        },
    }


def t2_sequence_similarity(
    prediction: Mapping[str, Any],
    groundtruth: Mapping[str, Any],
) -> float:
    """Recompute the canonical T2 sequence score for diagnostic consistency checks."""

    return _oligo_similarity(
        dict(prediction),
        dict(groundtruth),
        scorable_only=True,
    )


def score_t2(
    predictions: list[dict[str, Any]],
    groundtruth: list[dict[str, Any]],
    *,
    required_oligo_ids: set[str],
) -> tuple[dict[str, float], dict[str, Any]]:
    """Score source-recoverable T3-linked T2 oligo families."""

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
    prediction_families = _collapse_prediction_families(
        predictions,
        groundtruth,
    )
    pre_neutralized_families = {
        family_index
        for family_index, family in enumerate(prediction_families)
        if any(
            _prediction_family_similarity(
                family,
                predictions,
                groundtruth[index],
                scorable_only=False,
            )
            == 1.0
            for index in neutral_truth
        )
        and not any(
            _prediction_family_similarity(
                family,
                predictions,
                groundtruth[index],
                scorable_only=True,
            )
            == 1.0
            for index in scorable_truth
        )
    }
    active_family_indices = [
        index
        for index in range(len(prediction_families))
        if index not in pre_neutralized_families
    ]
    required_assignment_scores = [
        [
            _prediction_family_assignment_similarity(
                prediction_families[family_index],
                predictions,
                groundtruth[index],
                scorable_only=True,
            )
            for index in required_indices
        ]
        for family_index in active_family_indices
    ]
    raw_matches = best_one_to_one_matching(required_assignment_scores)
    matches = [
        (
            active_family_indices[prediction_position],
            required_indices[required_position],
            _prediction_family_similarity(
                prediction_families[active_family_indices[prediction_position]],
                predictions,
                groundtruth[required_indices[required_position]],
                scorable_only=True,
            ),
            score,
        )
        for prediction_position, required_position, score in raw_matches
    ]

    matched_families = {item[0] for item in matches}
    score_sum = 0.0
    match_details: list[dict[str, Any]] = []
    name_scores: list[float] = []
    role_scores: list[float] = []
    orientation_scores: list[float] = []
    alias_scores: list[float] = []
    modification_scores: list[float] = []
    kind_scores: list[float] = []

    sequence_scores: list[float] = []
    controlled_role_scores: list[float] = []

    for family_index, truth_index, scientific_score, assignment_score in matches:
        family = prediction_families[family_index]
        prediction_index = _best_prediction_family_member(
            family,
            predictions,
            groundtruth[truth_index],
            scorable_only=True,
        )
        prediction = predictions[prediction_index]
        truth = groundtruth[truth_index]
        dimensions = _t2_dimensions(
            prediction,
            truth,
            scorable_only=True,
        )
        dimension_scores = _enabled_dimension_scores(dimensions)
        sequence_score = float(dimension_scores["sequence"] or 0.0)
        modification_score = dimension_scores["modifications"]
        kind_score = dimension_scores["kind"]
        orientation_score = dimension_scores["orientation"]
        controlled_role_score = dimension_scores["role"]
        score_sum += scientific_score
        name_score = _name_similarity(prediction["name"], truth["name"])
        role_text_score = _name_similarity(prediction["role"], truth["role"])
        alias_score = _text_collection_f1(
            prediction.get("aliases", []), truth.get("aliases", [])
        )
        modification_text_score = _text_collection_f1(
            prediction.get("modifications", []), truth.get("modifications", [])
        )
        name_scores.append(name_score)
        role_scores.append(role_text_score)
        alias_scores.append(alias_score)
        sequence_scores.append(sequence_score)
        if modification_score is not None:
            modification_scores.append(float(modification_score))
        if kind_score is not None:
            kind_scores.append(float(kind_score))
        if orientation_score is not None:
            orientation_scores.append(float(orientation_score))
        if controlled_role_score is not None:
            controlled_role_scores.append(float(controlled_role_score))
        match_details.append(
            {
                "prediction_index": prediction_index,
                "prediction_family_index": family_index,
                "prediction_oligo_ids": [
                    predictions[index].get("oligo_id", f"prediction_{index}")
                    for index in family.member_indices
                ],
                "prediction_family_signature": list(family.signature),
                "groundtruth_index": truth_index,
                "prediction_oligo_id": prediction.get("oligo_id"),
                "score": scientific_score,
                "assignment_score": assignment_score,
                "sequence_score": sequence_score,
                "groundtruth_oligo_id": truth["oligo_id"],
                "groundtruth_family_id": truth["oligo_id"],
                "groundtruth_scoring_level": (
                    "family"
                    if _truth_is_family_template(truth, scorable_only=True)
                    else "member"
                ),
                "groundtruth_support_status": truth.get("support_status", "explicit"),
                "scored": True,
                "dimension_scores": dimension_scores,
                "metadata_diagnostics": {
                    "name": name_score,
                    "role_text": role_text_score,
                    "aliases": alias_score,
                    "modification_text": modification_text_score,
                },
            }
        )

    unmatched_families = set(range(len(prediction_families))) - matched_families
    neutralized_families: set[int] = set(pre_neutralized_families)
    for family_index in unmatched_families:
        family = prediction_families[family_index]
        if any(
            _prediction_family_similarity(
                family,
                predictions,
                groundtruth[index],
                scorable_only=False,
            )
            == 1.0
            for index in neutral_truth
        ):
            neutralized_families.add(family_index)

    effective_prediction_count = len(prediction_families) - len(neutralized_families)
    precision, recall, f1 = _soft_prf(
        score_sum,
        effective_prediction_count,
        len(scorable_truth),
    )
    exact_required_matches = sum(score == 1.0 for _, _, score, _ in matches)
    exact_required_family_recall = (
        exact_required_matches / len(scorable_truth) if scorable_truth else 1.0
    )
    metrics = {
        "required_family_precision": precision,
        "required_family_recall": recall,
        "required_family_f1": f1,
        "exact_required_family_recall": exact_required_family_recall,
        "sequence_similarity": _mean(sequence_scores),
        "name_similarity": _mean(name_scores),
        "role_similarity": _mean(role_scores),
        "controlled_role_f1": _mean(controlled_role_scores, empty=1.0),
        "alias_f1": _mean(alias_scores),
        "modification_f1": _mean(modification_scores, empty=1.0),
        "kind_accuracy": _mean(kind_scores),
        "orientation_accuracy": _mean(orientation_scores, empty=1.0),
        "predicted_member_count": float(len(predictions)),
        "predicted_family_count": float(len(prediction_families)),
        "effective_prediction_family_count": float(effective_prediction_count),
        "used_groundtruth_count": float(len(used_truth)),
        "required_family_count": float(len(scorable_truth)),
        "scored_family_count": float(len(scorable_truth)),
        "neutral_used_groundtruth_count": float(len(neutral_used_truth)),
        "optional_groundtruth_count": float(len(optional_truth)),
        "neutralized_prediction_family_count": float(len(neutralized_families)),
    }
    unmatched_scored_families = sorted(unmatched_families - neutralized_families)
    neutralized_prediction_indices = sorted(
        index
        for family_index in neutralized_families
        for index in prediction_families[family_index].member_indices
    )
    details = {
        "matches": match_details,
        "prediction_families": [
            {
                "family_index": family_index,
                "representative_index": family.representative_index,
                "representative_oligo_id": predictions[family.representative_index].get(
                    "oligo_id", f"prediction_{family.representative_index}"
                ),
                "member_indices": list(family.member_indices),
                "member_oligo_ids": [
                    predictions[index].get("oligo_id", f"prediction_{index}")
                    for index in family.member_indices
                ],
                "signature": list(family.signature),
            }
            for family_index, family in enumerate(prediction_families)
        ],
        "unmatched_prediction_family_indices": unmatched_scored_families,
        "unmatched_prediction_family_ids": sorted(
            predictions[prediction_families[index].representative_index].get(
                "oligo_id",
                f"prediction_{prediction_families[index].representative_index}",
            )
            for index in unmatched_scored_families
        ),
        "unmatched_prediction_families": [
            {
                "representative_oligo_id": predictions[
                    prediction_families[index].representative_index
                ].get(
                    "oligo_id",
                    f"prediction_{prediction_families[index].representative_index}",
                ),
                "member_oligo_ids": [
                    predictions[member].get("oligo_id", f"prediction_{member}")
                    for member in prediction_families[index].member_indices
                ],
            }
            for index in unmatched_scored_families
        ],
        "unmatched_required_family_ids": sorted(
            groundtruth[index]["oligo_id"]
            for index in scorable_truth
            - {truth_index for _, truth_index, _, _ in matches}
        ),
        "neutralized_prediction_family_indices": sorted(neutralized_families),
        "neutralized_prediction_indices": neutralized_prediction_indices,
        "used_oligo_ids": sorted(
            groundtruth[index]["oligo_id"] for index in used_truth
        ),
        "required_family_ids": sorted(
            groundtruth[index]["oligo_id"] for index in scorable_truth
        ),
        "scored_family_ids": sorted(
            groundtruth[index]["oligo_id"] for index in scorable_truth
        ),
        "neutral_used_oligo_ids": sorted(
            groundtruth[index]["oligo_id"] for index in neutral_used_truth
        ),
        "optional_oligo_ids": sorted(
            groundtruth[index]["oligo_id"] for index in optional_truth
        ),
        "matching_policy": (
            "collapse concrete panel members into kind/controlled-role/orientation/"
            "canonical-modification-bounded oligo families, then apply global "
            "maximum-weight one-to-one assignment with nucleotide sequence as the "
            "primary key and structured scientific similarity as a deterministic "
            "tie-break; reward weights canonical nucleotide sequence 0.65, positional "
            "chemistry 0.15, kind 0.10, orientation 0.05, and controlled role 0.05"
        ),
        "weights": T2_WEIGHTS,
        "family_policy": (
            "ground-truth records with fixed-length placeholders are family-level; "
            "concrete ground-truth records are member-level requirements; exact "
            "concrete members of one family count once and unrelated families remain "
            "precision errors"
        ),
        "scope_policy": "O_used contains every T2 family referenced by T3; O_score is O_used restricted to explicit or derivable sequence claims",
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
    predicted_workflows = list(prediction.get("workflows", []))
    truth_workflows = list(groundtruth.get("workflows", []))
    predicted_oligos = {
        item["oligo_id"]: item for item in t2_prediction.get("oligos", [])
    }
    truth_oligos = {item["oligo_id"]: item for item in t2_groundtruth.get("oligos", [])}
    workflow_scores = [
        [
            _workflow_assignment_similarity(
                predicted_workflow,
                truth_workflow,
                predicted_oligos=predicted_oligos,
                truth_oligos=truth_oligos,
            )
            for truth_workflow in truth_workflows
        ]
        for predicted_workflow in predicted_workflows
    ]
    workflow_matches = best_one_to_one_matching(workflow_scores)
    matched_prediction_indices = {item[0] for item in workflow_matches}
    matched_truth_indices = {item[1] for item in workflow_matches}
    workflow_pairs: list[tuple[int | None, int | None, float]] = [
        (prediction_index, truth_index, score)
        for prediction_index, truth_index, score in workflow_matches
    ]
    workflow_pairs.extend(
        (index, None, 0.0)
        for index in range(len(predicted_workflows))
        if index not in matched_prediction_indices
    )
    workflow_pairs.extend(
        (None, index, 0.0)
        for index in range(len(truth_workflows))
        if index not in matched_truth_indices
    )
    workflow_pairs.sort(
        key=lambda item: (
            item[1] is None,
            item[1] if item[1] is not None else len(truth_workflows),
            item[0] if item[0] is not None else len(predicted_workflows),
        )
    )

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
    workflow_details: dict[str, Any] = {}

    for (
        prediction_workflow_index,
        truth_workflow_index,
        workflow_match_score,
    ) in workflow_pairs:
        predicted_workflow = (
            predicted_workflows[prediction_workflow_index]
            if prediction_workflow_index is not None
            else None
        )
        truth_workflow = (
            truth_workflows[truth_workflow_index]
            if truth_workflow_index is not None
            else None
        )
        predicted_states = (
            list(predicted_workflow.get("states", [])) if predicted_workflow else []
        )
        truth_states = list(truth_workflow.get("states", [])) if truth_workflow else []
        predicted_terminal_modalities = _terminal_modalities_by_state(
            predicted_workflow
        )
        truth_terminal_modalities = _terminal_modalities_by_state(truth_workflow)
        predicted_state_positions = _state_boundary_classes(predicted_workflow)
        truth_state_positions = _state_boundary_classes(truth_workflow)
        state_scientific_scores = [
            [
                _state_similarity(
                    item,
                    truth,
                    scorable_only=False,
                    allow_reverse_complement=_terminal_pair_allows_reverse_complement(
                        item,
                        truth,
                        predicted_terminal_modalities,
                        truth_terminal_modalities,
                    ),
                )
                for truth in truth_states
            ]
            for item in predicted_states
        ]
        state_assignment_scores = [
            [
                _state_assignment_similarity(
                    scientific_score=state_scientific_scores[prediction_index][
                        truth_index
                    ],
                    prediction=prediction_state,
                    truth=truth_state,
                    predicted_positions=predicted_state_positions,
                    truth_positions=truth_state_positions,
                )
                for truth_index, truth_state in enumerate(truth_states)
            ]
            for prediction_index, prediction_state in enumerate(predicted_states)
        ]
        state_matches = best_partial_one_to_one_matching(
            state_assignment_scores,
            minimum_score=_T3_MIN_ASSIGNMENT_SCORE,
        )
        state_map = {
            predicted_states[prediction_index]["state_id"]: truth_states[truth_index][
                "state_id"
            ]
            for prediction_index, truth_index, _ in state_matches
        }
        state_counts, state_details, neutralized_state_predictions = (
            _matched_entity_counts(
                predicted_states,
                truth_states,
                state_matches,
                state_scientific_scores,
                score=lambda item, truth: _state_similarity(
                    item,
                    truth,
                    scorable_only=True,
                    allow_reverse_complement=_terminal_pair_allows_reverse_complement(
                        item,
                        truth,
                        predicted_terminal_modalities,
                        truth_terminal_modalities,
                    ),
                ),
                scorable=_state_is_scorable,
            )
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
                            allow_reverse_complement=(
                                _terminal_pair_allows_reverse_complement(
                                    predicted_state,
                                    truth_state,
                                    predicted_terminal_modalities,
                                    truth_terminal_modalities,
                                )
                            ),
                        )
                    ),
                    "metadata_diagnostics": _state_metadata_diagnostics(
                        predicted_state,
                        truth_state,
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
        transition_assignment_scores = [
            [
                _transition_assignment_similarity(
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
        transition_scientific_scores = [
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
        transition_matches = best_partial_one_to_one_matching(
            transition_assignment_scores,
            minimum_score=_T3_MIN_ASSIGNMENT_SCORE,
        )
        transition_map = {
            predicted_transitions[prediction_index]["transition_id"]: truth_transitions[
                truth_index
            ]["transition_id"]
            for prediction_index, truth_index, _ in transition_matches
        }
        (
            transition_counts,
            transition_details,
            neutralized_transition_predictions,
        ) = _matched_entity_counts(
            predicted_transitions,
            truth_transitions,
            transition_matches,
            transition_scientific_scores,
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
                    "prediction_transition_id": predicted_transition["transition_id"],
                    "groundtruth_transition_id": truth_transition["transition_id"],
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

        edge_details = _typed_edge_analysis(
            predicted_workflow,
            truth_workflow,
            state_map=state_map,
            transition_map=transition_map,
        )
        edge_counts = (
            edge_details["matched"],
            edge_details["predicted"],
            edge_details["groundtruth"],
            edge_details["neutralized_predictions"],
        )
        matched_typed_edges += edge_counts[0]
        predicted_typed_edges += edge_counts[1]
        truth_typed_edges += edge_counts[2]
        neutralized_typed_edges += edge_counts[3]

        if predicted_workflow and truth_workflow:
            truth_state_by_id = {item["state_id"]: item for item in truth_states}
            initial_score = _mapped_masked_boundary_f1(
                predicted_workflow["initial_state_ids"],
                truth_workflow["initial_state_ids"],
                state_map,
                truth_state_by_id,
            )
            final_score = _mapped_terminal_output_f1(
                predicted_workflow["final_outputs"],
                truth_workflow["final_outputs"],
                state_map,
            )
            boundary_scores.extend((initial_score, final_score))
        else:
            initial_score = final_score = 0.0

        detail_key = (
            truth_workflow["workflow_id"]
            if truth_workflow is not None
            else f"extra:{predicted_workflow['workflow_id']}"
        )
        workflow_details[detail_key] = {
            "predicted_workflow_id": (
                predicted_workflow.get("workflow_id") if predicted_workflow else None
            ),
            "groundtruth_workflow_id": (
                truth_workflow.get("workflow_id") if truth_workflow else None
            ),
            "workflow_match_score": workflow_match_score,
            "predicted_final_outputs": (
                predicted_workflow.get("final_outputs", [])
                if predicted_workflow
                else []
            ),
            "groundtruth_final_outputs": (
                truth_workflow.get("final_outputs", []) if truth_workflow else []
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
            "terminal_output_f1": final_score,
            "typed_edges": edge_details,
        }

    state_prf = _soft_prf(state_score_sum, predicted_state_count, truth_state_count)
    transition_prf = _soft_prf(
        transition_score_sum,
        predicted_transition_count,
        truth_transition_count,
    )
    typed_edge_f1 = (
        1.0
        if predicted_typed_edges == truth_typed_edges == 0
        else (
            2.0 * matched_typed_edges / (predicted_typed_edges + truth_typed_edges)
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
        "predicted_workflow_count": float(len(predicted_workflows)),
        "groundtruth_workflow_count": float(len(truth_workflows)),
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
        "workflows": workflow_details,
        "terminal_modalities": _terminal_modality_summary(
            predicted_workflows,
            truth_workflows,
        ),
        "workflow_matching_policy": (
            "global maximum-weight one-to-one connected-workflow assignment "
            "using terminal-modality, molecular-state, and transition similarity; "
            "each connected DAG is scored once without modality projections"
        ),
        "state_sequence_policy": (
            "canonicalize protocol-neutral biological payload aliases; compare "
            "both complete strand architecture and ordered segment projections; "
            "accept the two-base IUPAC anchor shorthands VN and NB for a truth "
            "ANCHOR:2 span; "
            "allow token-aware reverse-complement equivalence only for terminal "
            "states with a shared modality"
        ),
        "state_metadata_policy": (
            "physical_state and properties are diagnostic prose and do not affect "
            "state reward"
        ),
        "entity_assignment_policy": (
            "global maximum-weight partial state and transition assignment; "
            f"pairs below {_T3_MIN_ASSIGNMENT_SCORE:.2f} remain unmatched; "
            "state workflow position and transition event identity are bounded "
            "assignment-only tie-breaks and never add reward"
        ),
        "assignment_parameters": {
            "minimum_score": _T3_MIN_ASSIGNMENT_SCORE,
            "state_boundary_tie_break": _STATE_BOUNDARY_TIE_BREAK,
            "transition_identity_tie_break": _TRANSITION_IDENTITY_TIE_BREAK,
        },
        "oligo_use_policy": "resolve transition-local T2 IDs to nucleotide sequence signatures and compare multisets directly",
        "weights": {
            "state": STATE_WEIGHTS,
            "transition": TRANSITION_WEIGHTS,
        },
    }
    return metrics, details


def _workflow_assignment_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    predicted_oligos: Mapping[str, dict[str, Any]],
    truth_oligos: Mapping[str, dict[str, Any]],
) -> float:
    predicted_states = list(prediction.get("states", []))
    truth_states = list(truth.get("states", []))
    predicted_terminal_modalities = _terminal_modalities_by_state(prediction)
    truth_terminal_modalities = _terminal_modalities_by_state(truth)
    predicted_state_positions = _state_boundary_classes(prediction)
    truth_state_positions = _state_boundary_classes(truth)
    state_scientific_scores = [
        [
            _state_similarity(
                item,
                expected,
                scorable_only=False,
                allow_reverse_complement=_terminal_pair_allows_reverse_complement(
                    item,
                    expected,
                    predicted_terminal_modalities,
                    truth_terminal_modalities,
                ),
            )
            for expected in truth_states
        ]
        for item in predicted_states
    ]
    state_assignment_scores = [
        [
            _state_assignment_similarity(
                scientific_score=state_scientific_scores[prediction_index][truth_index],
                prediction=prediction_state,
                truth=truth_state,
                predicted_positions=predicted_state_positions,
                truth_positions=truth_state_positions,
            )
            for truth_index, truth_state in enumerate(truth_states)
        ]
        for prediction_index, prediction_state in enumerate(predicted_states)
    ]
    state_matches = best_partial_one_to_one_matching(
        state_assignment_scores,
        minimum_score=_T3_MIN_ASSIGNMENT_SCORE,
    )
    state_map = {
        predicted_states[prediction_index]["state_id"]: truth_states[truth_index][
            "state_id"
        ]
        for prediction_index, truth_index, _ in state_matches
    }
    state_f1 = _soft_prf(
        sum(
            state_scientific_scores[prediction_index][truth_index]
            for prediction_index, truth_index, _ in state_matches
        ),
        len(predicted_states),
        len(truth_states),
    )[2]

    predicted_transitions = list(prediction.get("transitions", []))
    truth_transitions = list(truth.get("transitions", []))
    transition_assignment_scores = [
        [
            _transition_assignment_similarity(
                item,
                expected,
                state_map=state_map,
                predicted_oligos=predicted_oligos,
                truth_oligos=truth_oligos,
            )
            for expected in truth_transitions
        ]
        for item in predicted_transitions
    ]
    transition_scientific_scores = [
        [
            _transition_similarity(
                item,
                expected,
                state_map=state_map,
                predicted_oligos=predicted_oligos,
                truth_oligos=truth_oligos,
            )
            for expected in truth_transitions
        ]
        for item in predicted_transitions
    ]
    transition_matches = best_partial_one_to_one_matching(
        transition_assignment_scores,
        minimum_score=_T3_MIN_ASSIGNMENT_SCORE,
    )
    transition_f1 = _soft_prf(
        sum(
            transition_scientific_scores[prediction_index][truth_index]
            for prediction_index, truth_index, _ in transition_matches
        ),
        len(predicted_transitions),
        len(truth_transitions),
    )[2]
    terminal_modality_f1 = _terminal_modality_f1(
        prediction.get("final_outputs", []), truth.get("final_outputs", [])
    )
    return 0.45 * terminal_modality_f1 + 0.35 * transition_f1 + 0.20 * state_f1


def _terminal_modality_f1(
    predicted_outputs: Iterable[Mapping[str, Any]],
    truth_outputs: Iterable[Mapping[str, Any]],
) -> float:
    return _multiset_f1(
        [modality_key(str(item.get("modality", ""))) for item in predicted_outputs],
        [modality_key(str(item.get("modality", ""))) for item in truth_outputs],
    )


def _terminal_modalities_by_state(
    workflow: Mapping[str, Any] | None,
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    if workflow is None:
        return result
    for output in workflow.get("final_outputs", []):
        state_id = str(output.get("state_id", ""))
        if state_id:
            result[state_id].add(modality_key(str(output.get("modality", ""))))
    return result


def _state_boundary_classes(
    workflow: Mapping[str, Any] | None,
) -> dict[str, str]:
    if workflow is None:
        return {}
    initial_ids = set(workflow.get("initial_state_ids", []))
    terminal_ids = {
        str(item.get("state_id", "")) for item in workflow.get("final_outputs", [])
    }
    result: dict[str, str] = {}
    for state in workflow.get("states", []):
        state_id = str(state.get("state_id", ""))
        if state_id in initial_ids and state_id in terminal_ids:
            result[state_id] = "initial_terminal"
        elif state_id in initial_ids:
            result[state_id] = "initial"
        elif state_id in terminal_ids:
            result[state_id] = "terminal"
        else:
            result[state_id] = "intermediate"
    return result


def _state_assignment_similarity(
    *,
    scientific_score: float,
    prediction: Mapping[str, Any],
    truth: Mapping[str, Any],
    predicted_positions: Mapping[str, str],
    truth_positions: Mapping[str, str],
) -> float:
    predicted_position = predicted_positions.get(
        str(prediction.get("state_id", "")), "intermediate"
    )
    truth_position = truth_positions.get(str(truth.get("state_id", "")), "intermediate")
    boundary_identity = float(predicted_position == truth_position)
    return (scientific_score + _STATE_BOUNDARY_TIE_BREAK * boundary_identity) / (
        1.0 + _STATE_BOUNDARY_TIE_BREAK
    )


def _terminal_pair_allows_reverse_complement(
    prediction: Mapping[str, Any],
    truth: Mapping[str, Any],
    predicted_modalities: Mapping[str, set[str]],
    truth_modalities: Mapping[str, set[str]],
) -> bool:
    predicted = predicted_modalities.get(str(prediction.get("state_id", "")), set())
    expected = truth_modalities.get(str(truth.get("state_id", "")), set())
    return bool(predicted & expected)


def _mapped_terminal_output_f1(
    predicted_outputs: Iterable[Mapping[str, Any]],
    truth_outputs: Iterable[Mapping[str, Any]],
    state_map: Mapping[str, str],
) -> float:
    predicted = [
        (
            state_map.get(str(item.get("state_id"))),
            modality_key(str(item.get("modality", ""))),
        )
        for item in predicted_outputs
    ]
    truth = [
        (str(item.get("state_id")), modality_key(str(item.get("modality", ""))))
        for item in truth_outputs
    ]
    predicted_counter = Counter(item for item in predicted if item[0] is not None)
    truth_counter = Counter(truth)
    overlap = sum((predicted_counter & truth_counter).values())
    return _soft_prf(float(overlap), len(predicted), len(truth))[2]


def _terminal_modality_summary(
    predicted_workflows: Iterable[Mapping[str, Any]],
    truth_workflows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    predicted: dict[str, list[dict[str, str]]] = defaultdict(list)
    truth: dict[str, list[dict[str, str]]] = defaultdict(list)
    for workflow in predicted_workflows:
        for output in workflow.get("final_outputs", []):
            key = modality_key(str(output.get("modality", "")))
            predicted[key].append(
                {
                    "workflow_id": str(workflow.get("workflow_id", "")),
                    "state_id": str(output.get("state_id", "")),
                    "reported_modality": str(output.get("modality", "")),
                }
            )
    for workflow in truth_workflows:
        for output in workflow.get("final_outputs", []):
            key = modality_key(str(output.get("modality", "")))
            truth[key].append(
                {
                    "workflow_id": str(workflow.get("workflow_id", "")),
                    "state_id": str(output.get("state_id", "")),
                    "reported_modality": str(output.get("modality", "")),
                }
            )
    return {
        key: {
            "predicted_outputs": predicted.get(key, []),
            "groundtruth_outputs": truth.get(key, []),
        }
        for key in sorted(set(predicted) | set(truth))
    }


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
        scientific_alignment_score = all_scores[prediction_index][truth_index]
        entity_score = (
            score(predicted[prediction_index], truth[truth_index])
            if is_scored
            else None
        )
        if entity_score is not None:
            score_sum += entity_score
        if not is_scored and scientific_alignment_score == 1.0:
            neutralized_predictions.add(prediction_index)
        details.append(
            {
                "prediction_index": prediction_index,
                "groundtruth_index": truth_index,
                "score": entity_score,
                "alignment_score": alignment_score,
                "scientific_alignment_score": scientific_alignment_score,
                "scored": is_scored,
                "neutralized": not is_scored and scientific_alignment_score == 1.0,
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
    return any(status in SCORABLE_SUPPORT for _, status in _truth_sequence_claims(item))


def _collapse_prediction_families(
    predictions: list[dict[str, Any]],
    groundtruth: list[dict[str, Any]],
) -> list[_PredictionFamily]:
    signatures = [
        _prediction_family_signature(prediction, groundtruth)
        for prediction in predictions
    ]
    inferred_panels = _infer_flat_panel_signatures(predictions, signatures)
    grouped: dict[
        tuple[tuple[str, ...], tuple[Any, ...]],
        list[int],
    ] = {}
    for index, prediction in enumerate(predictions):
        signature = inferred_panels.get(index, signatures[index])
        key = (signature, _family_metadata_key(prediction))
        grouped.setdefault(key, []).append(index)

    result: list[_PredictionFamily] = []
    for (signature, _), indices in sorted(
        grouped.items(), key=lambda item: min(item[1])
    ):
        representative = min(
            indices,
            key=lambda index: (
                not _item_has_family_template(predictions[index]),
                index,
            ),
        )
        result.append(
            _PredictionFamily(
                representative_index=representative,
                member_indices=tuple(sorted(indices)),
                signature=signature,
            )
        )
    return result


def _prediction_family_signature(
    prediction: dict[str, Any],
    groundtruth: list[dict[str, Any]],
) -> tuple[str, ...]:
    raw_signature = _canonical_oligo_signature(
        prediction,
        family_template=False,
    )
    concrete_matches = [
        truth
        for truth in groundtruth
        if not _truth_is_family_template(truth, scorable_only=False)
        and _oligo_similarity(prediction, truth, scorable_only=False) == 1.0
    ]
    if concrete_matches:
        # Concrete ground-truth records explicitly request member-level coverage.
        return ("groundtruth-member", *raw_signature)

    family_matches = [
        truth
        for truth in groundtruth
        if _truth_is_family_template(truth, scorable_only=False)
        and _oligo_similarity(prediction, truth, scorable_only=False) == 1.0
    ]
    if family_matches:
        best_truth = max(
            family_matches,
            key=lambda truth: (
                _family_metadata_similarity(prediction, truth),
                truth.get("oligo_id", ""),
            ),
        )
        return (
            "groundtruth-family",
            *_canonical_oligo_signature(best_truth, family_template=True),
        )

    family_signature = _canonical_oligo_signature(
        prediction,
        family_template=True,
    )
    if family_signature != raw_signature or _item_has_family_template(prediction):
        return ("predicted-family", *family_signature)
    return ("predicted-member", *raw_signature)


def _canonical_oligo_signature(
    item: Mapping[str, Any],
    *,
    family_template: bool,
) -> tuple[str, ...]:
    if item.get("kind") != "double_stranded":
        molecule = _ordered_molecule_sequence(
            dict(item),
            family_template=family_template,
        )
        if molecule:
            return (_t2_nucleotide_projection(molecule),)
    values = _prediction_sequence_claims(
        dict(item),
        family_template=family_template,
    )
    return tuple(_t2_nucleotide_projection(value) for value in values)


def _prediction_family_similarity(
    family: _PredictionFamily,
    predictions: list[dict[str, Any]],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> float:
    return max(
        (
            _t2_scientific_similarity(
                predictions[index],
                truth,
                scorable_only=scorable_only,
            )
            for index in family.member_indices
        ),
        default=0.0,
    )


def _prediction_family_sequence_similarity(
    family: _PredictionFamily,
    predictions: list[dict[str, Any]],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> float:
    return max(
        (
            _oligo_similarity(
                predictions[index],
                truth,
                scorable_only=scorable_only,
            )
            for index in family.member_indices
        ),
        default=0.0,
    )


def _prediction_family_assignment_similarity(
    family: _PredictionFamily,
    predictions: list[dict[str, Any]],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> float:
    """Keep sequence primary while using structured claims to break ties."""

    return max(
        (
            _oligo_similarity(
                predictions[index],
                truth,
                scorable_only=scorable_only,
            )
            + _T2_ASSIGNMENT_TIE_BREAK
            * _t2_scientific_similarity(
                predictions[index],
                truth,
                scorable_only=scorable_only,
            )
            for index in family.member_indices
        ),
        default=0.0,
    )


def _best_prediction_family_member(
    family: _PredictionFamily,
    predictions: list[dict[str, Any]],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> int:
    return max(
        family.member_indices,
        key=lambda index: (
            _t2_scientific_similarity(
                predictions[index],
                truth,
                scorable_only=scorable_only,
            ),
            _oligo_similarity(
                predictions[index],
                truth,
                scorable_only=scorable_only,
            ),
            -index,
        ),
    )


def _truth_is_family_template(
    item: Mapping[str, Any],
    *,
    scorable_only: bool,
) -> bool:
    return any(
        _FAMILY_PLACEHOLDER_RE.search(normalize_sequence(value))
        for value, status in _truth_sequence_claims(dict(item))
        if not scorable_only or status in SCORABLE_SUPPORT
    )


def _item_has_family_template(item: Mapping[str, Any]) -> bool:
    values = []
    sequence = item.get("sequence")
    if isinstance(sequence, str):
        values.append(sequence)
    values.extend(
        value
        for component in item.get("components", [])
        for value in (component.get("placeholder"),)
        if isinstance(value, str)
    )
    return any(
        _FAMILY_PLACEHOLDER_RE.search(normalize_sequence(value)) for value in values
    )


def _t2_scientific_similarity(
    prediction: Mapping[str, Any],
    truth: Mapping[str, Any],
    *,
    scorable_only: bool,
) -> float:
    return _weighted_supported_score(
        _t2_dimensions(prediction, truth, scorable_only=scorable_only),
        T2_WEIGHTS,
    )


def _t2_dimensions(
    prediction: Mapping[str, Any],
    truth: Mapping[str, Any],
    *,
    scorable_only: bool,
) -> dict[str, tuple[float, bool]]:
    truth_supported = (
        not scorable_only or truth.get("support_status", "explicit") in SCORABLE_SUPPORT
    )
    truth_modifications = _oligo_modification_claims(
        truth,
        allowed_support=SCORABLE_SUPPORT if scorable_only else None,
    )
    predicted_modifications = _oligo_modification_claims(prediction)
    if scorable_only:
        neutral_modifications = _oligo_modification_claims(
            truth,
            allowed_support=NEUTRAL_SUPPORT,
        )
        predicted_modifications -= neutral_modifications

    truth_roles = _controlled_oligo_roles(str(truth.get("role", "")))
    truth_kind = str(truth.get("kind", "unknown"))
    truth_orientation = str(truth.get("orientation", "unknown"))
    modification_score = (
        _canonical_modification_f1(
            predicted_modifications,
            truth_modifications,
        )
        if truth_modifications
        else 1.0
    )
    return {
        "sequence": (
            _oligo_similarity(
                dict(prediction),
                dict(truth),
                scorable_only=scorable_only,
            ),
            True,
        ),
        "modifications": (
            modification_score,
            True,
        ),
        "kind": (
            float(prediction.get("kind") == truth_kind),
            truth_supported and truth_kind != "unknown",
        ),
        "orientation": (
            float(prediction.get("orientation") == truth_orientation),
            truth_supported and truth_orientation != "unknown",
        ),
        "role": (
            _controlled_role_similarity(prediction, truth),
            truth_supported and bool(truth_roles),
        ),
    }


def _controlled_role_similarity(
    prediction: Mapping[str, Any], truth: Mapping[str, Any]
) -> float:
    truth_roles = _controlled_oligo_roles(str(truth.get("role", "")))
    if not truth_roles:
        return 1.0
    prediction_roles = _controlled_oligo_roles(str(prediction.get("role", "")))

    # Canonical records sometimes make only the broad claim "primer". A
    # prediction with a more specific primer function satisfies that claim,
    # while the reverse remains false so generic prose cannot satisfy a
    # source-supported specific role.
    if truth_roles == {"primer"}:
        return float(bool(prediction_roles & _CONTROLLED_PRIMER_ROLES))

    # An assembled indexing oligo may be described by its primary PCR-primer
    # function while declaring a physical P5/P7 adapter component. Let that
    # explicit component satisfy a broad canonical "adapter" role; do not
    # globally alias primers and adapters.
    if truth_roles == {"adapter"}:
        component_roles = {
            role
            for component in prediction.get("components", [])
            for role in _controlled_oligo_roles(str(component.get("role", "")))
        }
        return float(
            bool(
                (set(prediction_roles) | component_roles)
                & _CONTROLLED_ADAPTER_ROLES
            )
        )

    return _set_f1(prediction_roles, truth_roles)


def _controlled_oligo_roles(value: str) -> frozenset[str]:
    """Project free-text roles into protocol-neutral functional categories."""

    text = _normalize_text(value)
    if not text:
        return frozenset()
    roles: set[str] = set()

    # Resolve primary roles before looking at words that may only describe a
    # referenced handle. For example, a ligation bridge can mention an RT
    # primer handle without itself becoming an RT primer.
    if re.search(r"\b(?:blocking|blocker|blocks|terminator|quench|quenching)\b", text):
        return frozenset({"blocking_oligo"})
    if ("linker" in text or "bridge" in text or "splint" in text) and (
        "ligat" in text or "anneal" in text
    ):
        return frozenset({"ligation_linker"})
    if (
        "ligation barcode" in text
        or "barcode ligation" in text
        or bool(
            re.search(
                r"\b(?:second|third|round [234]) round\b.*\bbarcode strand\b",
                text,
            )
        )
        or bool(re.search(r"\bbarcode strand ligated\b", text))
    ):
        return frozenset({"ligation_barcode"})
    if "template switch" in text and ("oligo" in text or "oligonucleotide" in text):
        return frozenset({"template_switching_oligo"})

    is_sequencing = "sequencing primer" in text or bool(
        re.search(r"\bread [12]\b.*\bprimer\b", text)
    )
    is_index_read = is_sequencing and bool(
        re.search(r"\b(?:index|i5|i7|sample index)\b", text)
    )
    if is_index_read:
        roles.add("index_sequencing_primer")
    elif is_sequencing:
        roles.add("sequencing_primer")

    is_amplification = bool(
        re.search(r"\b(?:pcr|amplification|amplifies|enrichment)\b", text)
    ) and ("primer" in text or "oligo" in text)
    referenced_rt_target = bool(
        is_amplification
        and re.search(
            r"\b(?:at|on|against|anneals?(?:\s+to)?|complementary\s+to)\b"
            r"[^.;]*\brt[- ]*(?:handle|adapter|adaptor|end)\b",
            text,
        )
    )
    if (
        not referenced_rt_target
        and re.search(r"\b(?:reverse transcription|reverse transcriptase|rt)\b", text)
        and ("primer" in text or "oligo" in text)
    ):
        roles.add("reverse_transcription_primer")
    if "second strand" in text and ("primer" in text or "oligo" in text):
        roles.add("second_strand_primer")

    is_indexing = is_amplification and bool(
        re.search(r"\b(?:indexing|indexed|sample index|i5 index|i7 index)\b", text)
    )
    if is_indexing:
        roles.add("indexing_primer")
    elif is_amplification:
        roles.add("amplification_primer")

    if "capture" in text and ("primer" in text or "oligo" in text):
        roles.add("capture_oligo")
    if "feature barcode" in text:
        roles.add("feature_barcode_oligo")
    if "sgrna" in text and ("primer" in text or "oligo" in text):
        roles.add("sgrna_primer")

    has_ligation = "ligat" in text
    referenced_tagmentation_target = bool(
        (is_amplification or is_sequencing)
        and re.search(
            r"\b(?:at|on|against|anneals?(?:\s+to)?|complementary\s+to)\b"
            r"[^.;]*\b(?:tn5|tagment\w*)[- ]*(?:adapter|adaptor)\b",
            text,
        )
    )

    if "primer entry point" in text or "primer_entry_point" in value.lower():
        roles.add("primer_entry_point")
    if (
        not referenced_tagmentation_target
        and re.search(r"\b(?:tn5|tagment|transposome|transposon)\b", text)
        and re.search(
            r"\b(?:adapter|adaptor|oligo|strand|binding site|transposon)\b", text
        )
    ):
        roles.add("tagmentation_adapter")
        if "non transferred" in text:
            roles.add("nontransferred_strand")
        elif "transferred" in text:
            roles.add("transferred_strand")
    if has_ligation and re.search(r"\badapt(?:er|or)\b", text):
        roles.add("ligation_adapter")
    if ("y shaped" in text or "sequencing adapter" in text) and not is_sequencing:
        roles.add("sequencing_adapter")
    if "flow cell" in text and re.search(r"\badapt(?:er|or)\b", text):
        roles.add("flowcell_adapter")

    if "promoter" in text:
        roles.add("promoter")
    if "probe" in text and ("hybrid" in text or "qc" in text):
        roles.add("hybridization_probe")
    if "bead synthesis" in text or "poly dt synthesis on beads" in text:
        roles.add("bead_synthesis_oligo")
    if "splint capture" in text:
        roles.add("splint_capture_oligo")
    if "linear amplification" in text and ("primer" in text or "oligo" in text):
        roles.add("linear_amplification_primer")
    if "random" in text and is_amplification:
        roles.add("random_amplification_primer")

    # Keep generic source labels scorable. They are added only when no more
    # specific primary function was recovered, then interpreted directionally
    # by _controlled_role_similarity.
    if not roles and re.search(r"\bprimer\b", text):
        roles.add("primer")
    if not roles and re.search(r"\badapt(?:er|or)\b", text):
        roles.add("adapter")

    return frozenset(roles)


def _oligo_modification_claims(
    item: Mapping[str, Any],
    *,
    allowed_support: frozenset[str] | None = None,
) -> set[str]:
    """Canonicalize inline and structured chemistry into one claim set."""

    claims: set[str] = set()
    item_status = str(item.get("support_status", "explicit"))
    item_enabled = allowed_support is None or item_status in allowed_support
    if item_enabled:
        sequence = item.get("sequence")
        if isinstance(sequence, str):
            claims.update(_inline_modification_claims(sequence))
        for value in item.get("modifications", []):
            claims.update(_canonical_modification_text(str(value)))

    components = list(item.get("components", []))
    for index, component in enumerate(components):
        status = str(component.get("support_status", item_status))
        if allowed_support is not None and status not in allowed_support:
            continue
        position_hint = (
            "five_prime"
            if index == 0
            else "three_prime"
            if index == len(components) - 1
            else "internal"
        )
        for field in ("sequence", "placeholder"):
            value = component.get(field)
            if isinstance(value, str):
                claims.update(_inline_modification_claims(value))
        for value in component.get("modifications", []):
            claims.update(
                _canonical_modification_text(
                    str(value),
                    position_hint=position_hint,
                )
            )
    return _reduce_modification_claims(claims)


def _reduce_modification_claims(claims: Iterable[str]) -> set[str]:
    """Remove generic chemistry claims already entailed by a specific claim."""

    reduced = set(claims)
    if any(re.fullmatch(r"riboguanosine:[1-9][0-9]*", claim) for claim in reduced):
        reduced.discard("riboguanosine")
        reduced.discard("ribonucleotide")
    return reduced


def _inline_modification_claims(value: str) -> set[str]:
    tokens = sequence_tokens(value)
    claims: set[str] = set()
    riboguanosines = sum(token == "rG" for token in tokens)
    lna_guanosines = sum(token == "+G" for token in tokens)
    deoxyuridines = sum(token == "(dU)" for token in tokens)
    if riboguanosines:
        claims.add(f"riboguanosine:{riboguanosines}")
    if lna_guanosines:
        claims.add(f"lna_guanosine:{lna_guanosines}")
    if deoxyuridines:
        claims.add(f"deoxyuridine:{deoxyuridines}")

    known_tags = {
        "/5phos/": "five_prime:phosphate",
        "/5bio/": "five_prime:biotin",
        "/5biosg/": "five_prime:biotin",
        "/5acryd/": "five_prime:acrydite",
        "/6-fam/": "five_prime:fluorescein",
        "/56-fam/": "five_prime:fluorescein",
        "/nh2/": "amino",
        "/3spc3/": "three_prime:c3_spacer",
        "/3ddc/": "three_prime:dideoxycytidine",
        "/ddc/": "dideoxycytidine",
        "/ddu/": "dideoxyuridine",
        "/3invdt/": "three_prime:inverted_dt",
        "/ideoxyu/": "internal:deoxyuridine",
        "/ibiodt/": "internal:biotin_dt",
        "/isppc/": "internal:photocleavable_spacer",
        "/ithiomc6-d/": "internal:thiol",
        "/5rapp/": "five_prime:preadenylated_phosphate",
    }
    for token in tokens:
        if not (token.startswith("/") and token.endswith("/")):
            continue
        canonical = known_tags.get(token.lower())
        claims.add(canonical or f"inline:{token[1:-1].lower()}")
    return claims


def _canonical_modification_text(
    value: str,
    *,
    position_hint: str | None = None,
) -> set[str]:
    text = _normalize_text(value)
    if not text:
        return set()
    location = _modification_location(text, position_hint=position_hint)
    claims: set[str] = set()

    if "phosphorothioate" in text:
        claims.add(f"{location}:phosphorothioate")
    elif "phosphate" in text or "phosphorylation" in text or "5phos" in text:
        claims.add(f"{location}:phosphate")
    if "biotin dt" in text or "ibiodt" in text:
        claims.add(f"{location}:biotin_dt")
    elif "biotin" in text or "5biosg" in text:
        claims.add(f"{location}:biotin")
    if "acrydite" in text or "5acryd" in text:
        claims.add(f"{location}:acrydite")
    if "fluorescein" in text or "6 fam" in text:
        claims.add(f"{location}:fluorescein")
    if re.search(r"\b(?:amine|amino|nh2)\b", text):
        claims.add(f"{location}:amino")
    if "c3 spacer" in text or "3spc3" in text:
        claims.add(f"{location}:c3_spacer")
    if "photocleavable spacer" in text or "isppc" in text:
        claims.add(f"{location}:photocleavable_spacer")
    if "inverted dt" in text or "3invdt" in text:
        claims.add(f"{location}:inverted_dt")
    if "dideoxy" in text or "ddc" in text:
        claims.add(f"{location}:dideoxycytidine")
    if "deoxyuridine" in text or re.search(r"\bdu\b", text):
        claims.add("internal:deoxyuridine")
    if "methylcytosine" in text:
        claims.add("methylcytosine")
    if "locked nucleic acid" in text or re.search(r"\blna\b", text):
        count = _modification_count(text)
        claims.add(f"lna_guanosine:{count}" if count else "lna_guanosine")
    if "riboguan" in text or "rgrg" in text or re.search(r"\brg\b", text):
        count = _modification_count(text)
        claims.add(f"riboguanosine:{count}" if count else "riboguanosine")
    if re.search(r"\b(?:rna )?ribonucleotides?\b", text):
        claims.add("ribonucleotide")
    if "hplc" in text:
        claims.add("purification:hplc")
    if "all rna" in text or "rna oligonucleotide" in text:
        claims.add("backbone:rna")
    if "t overhang" in text:
        claims.add(f"{location}:t_overhang")
    if "degenerate" in text and ("vn" in text or "anchor" in text):
        claims.add("anchor:degenerate_vn")
    if re.search(r"\b(?:bead|gel bead|magnetic bead)\b", text) and re.search(
        r"\b(?:attach(?:ed|ment)?|conjugat(?:ed|ion)|"
        r"immobili[sz](?:ed|ation)|tether(?:ed|ing)?)\b",
        text,
    ):
        claims.add(f"{location}:bead_tether")
    if "peg" in text and "linker" in text:
        claims.add(f"{location}:peg_linker")
    if "iso dc" in text or "iso dg" in text:
        claims.add(f"{location}:iso_base")

    if not claims:
        claims.add(f"text:{text}")
    return claims


def _modification_location(text: str, *, position_hint: str | None) -> str:
    if re.search(r"\b5 (?:prime )?(?:end )?", text) or "5prime" in text:
        return "five_prime"
    if re.search(r"\b3 (?:prime )?(?:end |terminal )?", text) or "3prime" in text:
        return "three_prime"
    if "internal" in text:
        return "internal"
    return position_hint or "unspecified"


def _modification_count(text: str) -> int | None:
    if "three" in text or "x3" in text or "rgrgrg" in text:
        return 3
    if "two" in text or "x2" in text or "rgrg" in text:
        return 2
    return None


def _canonical_modification_f1(
    prediction: Iterable[str], truth: Iterable[str]
) -> float:
    return _set_f1(set(prediction), set(truth))


def _set_f1(
    prediction: set[str] | frozenset[str], truth: set[str] | frozenset[str]
) -> float:
    overlap = len(set(prediction) & set(truth))
    return _soft_prf(float(overlap), len(prediction), len(truth))[2]


def _family_metadata_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    component_orientations: set[str] = set()
    for component in item.get("components", []):
        orientation = component.get("orientation")
        if isinstance(orientation, str) and orientation != item.get("orientation"):
            component_orientations.add(orientation)
    controlled_roles = _controlled_oligo_roles(str(item.get("role", "")))
    role_key: tuple[str, ...] = tuple(sorted(controlled_roles))
    if not role_key:
        role_key = (f"unclassified:{_normalize_text(str(item.get('role', '')))}",)
    return (
        item.get("kind", "unknown"),
        role_key,
        item.get("orientation", "unknown"),
        tuple(sorted(_oligo_modification_claims(item))),
        tuple(sorted(component_orientations)),
    )


def _family_metadata_similarity(
    prediction: Mapping[str, Any],
    truth: Mapping[str, Any],
) -> float:
    role = _controlled_role_similarity(prediction, truth)
    orientation = float(
        truth.get("orientation") == "unknown"
        or prediction.get("orientation") == truth.get("orientation")
    )
    truth_modifications = _oligo_modification_claims(truth)
    modifications = (
        _canonical_modification_f1(
            _oligo_modification_claims(prediction),
            truth_modifications,
        )
        if truth_modifications
        else 1.0
    )
    kind = float(
        truth.get("kind") == "unknown" or prediction.get("kind") == truth.get("kind")
    )
    return 0.40 * role + 0.20 * orientation + 0.25 * modifications + 0.15 * kind


def _infer_flat_panel_signatures(
    predictions: list[dict[str, Any]],
    signatures: list[tuple[str, ...]],
) -> dict[int, tuple[str, ...]]:
    """Conservatively cluster untemplated flat concrete panel members."""

    buckets: dict[tuple[tuple[Any, ...], int], list[int]] = {}
    tokens_by_index: dict[int, list[str]] = {}
    for index, (prediction, signature) in enumerate(zip(predictions, signatures)):
        if not signature or signature[0] != "predicted-member" or len(signature) != 2:
            continue
        tokens = _sequence_value(signature[1])
        if not tokens or not all(
            _NUCLEOTIDE_TOKEN_RE.fullmatch(token) or token.startswith("/")
            for token in tokens
        ):
            continue
        tokens_by_index[index] = tokens
        buckets.setdefault((_family_metadata_key(prediction), len(tokens)), []).append(
            index
        )

    result: dict[int, tuple[str, ...]] = {}
    for (_, token_count), indices in buckets.items():
        if len(indices) < 2:
            continue
        adjacency = {index: set() for index in indices}
        for left_position, left_index in enumerate(indices):
            for right_index in indices[left_position + 1 :]:
                window = _variable_window(
                    tokens_by_index[left_index],
                    tokens_by_index[right_index],
                )
                if window is None:
                    continue
                start, end = window
                if _valid_panel_window(
                    [tokens_by_index[left_index], tokens_by_index[right_index]],
                    start,
                    end,
                ):
                    adjacency[left_index].add(right_index)
                    adjacency[right_index].add(left_index)

        remaining = set(indices)
        while remaining:
            seed = min(remaining)
            stack = [seed]
            component: set[int] = set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(adjacency[current] - component)
            remaining -= component
            if len(component) < 2:
                continue
            ordered = sorted(component)
            sequences = [tokens_by_index[index] for index in ordered]
            differing = [
                position
                for position in range(token_count)
                if len({tokens[position] for tokens in sequences}) > 1
            ]
            if not differing:
                continue
            start, end = min(differing), max(differing) + 1
            if not _valid_panel_window(sequences, start, end):
                continue
            signature = (
                "inferred-panel-family",
                *sequences[0][:start],
                f"[VARIABLE:{end - start}]",
                *sequences[0][end:],
            )
            for index in ordered:
                result[index] = signature
    return result


def _variable_window(
    left: list[str],
    right: list[str],
) -> tuple[int, int] | None:
    differing = [
        index
        for index, (left_token, right_token) in enumerate(zip(left, right))
        if left_token != right_token
    ]
    if not differing:
        return None
    return min(differing), max(differing) + 1


def _valid_panel_window(
    sequences: list[list[str]],
    start: int,
    end: int,
) -> bool:
    variable_length = end - start
    total_length = len(sequences[0])
    fixed_length = total_length - variable_length
    if not 1 <= variable_length <= 32:
        return False
    if fixed_length < max(8, variable_length) or fixed_length / total_length < 0.5:
        return False
    return all(
        _NUCLEOTIDE_TOKEN_RE.fullmatch(token)
        for sequence in sequences
        for token in sequence[start:end]
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


def _prediction_sequence_claims(
    item: dict[str, Any],
    *,
    family_template: bool = False,
) -> list[str]:
    sequence = item.get("sequence")
    if isinstance(sequence, str) and sequence:
        return [_family_template_sequence(item) if family_template else sequence]
    result: list[str] = []
    for component in item.get("components", []):
        value = (
            _component_family_placeholder(component)
            or component.get("sequence")
            or component.get("placeholder")
            if family_template
            else component.get("sequence") or component.get("placeholder")
        )
        if isinstance(value, str) and value:
            result.append(value)
    return result


def _oligo_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
) -> float:
    truth_is_family = _truth_is_family_template(
        truth,
        scorable_only=scorable_only,
    )
    ordered_similarity = _ordered_molecule_similarity(
        prediction,
        truth,
        scorable_only=scorable_only,
        truth_is_family=truth_is_family,
    )
    if ordered_similarity is not None:
        return ordered_similarity

    predicted_claims = _prediction_sequence_claims(
        prediction,
        family_template=truth_is_family,
    )
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
    return _sequence_collection_f1(
        predicted_claims,
        truth_claims,
    )


def _ordered_molecule_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
    truth_is_family: bool,
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
    raw_predicted_sequence = _ordered_molecule_sequence(
        prediction,
        family_template=False,
    )
    raw_truth_sequence = _ordered_molecule_sequence(
        truth,
        family_template=False,
    )
    if (
        raw_predicted_sequence is not None
        and raw_truth_sequence is not None
        and normalize_sequence(raw_predicted_sequence)
        == normalize_sequence(raw_truth_sequence)
    ):
        # Exact normalized molecule equality is decisive. Component role prose
        # may mention a downstream barcode without making the component itself
        # variable; family templating must never degrade an exact sequence.
        return 1.0
    predicted_sequence = _ordered_molecule_sequence(
        prediction,
        family_template=truth_is_family,
    )
    truth_sequence = _ordered_molecule_sequence(
        truth,
        family_template=truth_is_family,
    )
    if predicted_sequence is None or truth_sequence is None:
        return None
    return _sequence_similarity(
        predicted_sequence,
        truth_sequence,
        family_truth=truth_is_family,
    )


def _ordered_molecule_sequence(
    item: dict[str, Any],
    *,
    family_template: bool = False,
) -> str | None:
    sequence = item.get("sequence")
    if isinstance(sequence, str) and sequence:
        return _family_template_sequence(item) if family_template else sequence
    components = item.get("components", [])
    if not components:
        return None
    values: list[str] = []
    for component in components:
        value = (
            _component_family_placeholder(component)
            or component.get("sequence")
            or component.get("placeholder")
            if family_template
            else component.get("sequence") or component.get("placeholder")
        )
        if not isinstance(value, str) or not value:
            return None
        values.append(value)
    return "".join(values)


def _family_template_sequence(item: Mapping[str, Any]) -> str:
    sequence = item.get("sequence")
    if not isinstance(sequence, str) or not sequence:
        return ""
    result = normalize_sequence(sequence)
    components = list(item.get("components", []))
    concrete_values = [
        normalize_sequence(component["sequence"])
        for component in components
        if isinstance(component.get("sequence"), str) and component.get("sequence")
    ]
    if len(concrete_values) == len(components) and components:
        concrete_molecule = "".join(concrete_values)
        family_molecule = "".join(
            _component_family_placeholder(component)
            or normalize_sequence(component["sequence"])
            for component in components
        )
        position = _find_unprotected_subsequence(result, concrete_molecule)
        if position >= 0:
            return (
                result[:position]
                + family_molecule
                + result[position + len(concrete_molecule) :]
            )

    cursor = 0
    for component in components:
        replacement = _component_family_placeholder(component)
        concrete = component.get("sequence")
        if not isinstance(concrete, str) or not concrete:
            continue
        normalized_concrete = normalize_sequence(concrete)
        position = _find_unprotected_subsequence(
            result,
            normalized_concrete,
            start=cursor,
        )
        if position < 0:
            continue
        if replacement:
            result = (
                result[:position]
                + replacement
                + result[position + len(normalized_concrete) :]
            )
            cursor = position + len(replacement)
        else:
            cursor = position + len(normalized_concrete)
    return result


def _find_unprotected_subsequence(
    sequence: str,
    subsequence: str,
    *,
    start: int = 0,
) -> int:
    """Find literal bases without matching inside placeholders/modification tags."""

    protected_spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(sequence):
        opener = sequence[cursor]
        closer = "]" if opener == "[" else "/" if opener == "/" else None
        if closer is None:
            cursor += 1
            continue
        end = sequence.find(closer, cursor + 1)
        if end < 0:
            cursor += 1
            continue
        protected_spans.append((cursor, end + 1))
        cursor = end + 1

    position = sequence.find(subsequence, start)
    while position >= 0:
        end = position + len(subsequence)
        if all(
            end <= protected_start or position >= protected_end
            for protected_start, protected_end in protected_spans
        ):
            return position
        position = sequence.find(subsequence, position + 1)
    return -1


def _component_family_placeholder(
    component: Mapping[str, Any],
) -> str | None:
    placeholder = component.get("placeholder")
    if isinstance(placeholder, str) and placeholder:
        return normalize_sequence(placeholder)
    role = _variable_component_role(component)
    if role is None:
        return None
    length = component.get("length")
    if not isinstance(length, int) or length <= 0:
        sequence = component.get("sequence")
        if not isinstance(sequence, str) or not sequence:
            return None
        length = len(_sequence_value(sequence))
    return f"[{role}:{length}]" if length > 0 else None


def _variable_component_role(component: Mapping[str, Any]) -> str | None:
    text = _normalize_text(
        " ".join(str(component.get(field, "")) for field in ("name", "role"))
    )
    if not text:
        return None
    if "unique molecular identifier" in text or re.search(r"\bumi\b", text):
        return "UMI"
    if "random" in text or "hexamer" in text or "randomer" in text:
        return "RANDOM"
    if "i5" in text:
        return "I5_INDEX"
    if "i7" in text:
        return "I7_INDEX"
    if "tn5" in text and ("index" in text or "barcode" in text):
        return "TN5_INDEX"
    if ("feature" in text or "antibody" in text or "capture" in text) and (
        "barcode" in text
    ):
        return "FEATURE_BARCODE"
    if "sample index" in text or "library index" in text:
        return "SAMPLE_INDEX"
    if "variable" in text or "degenerate" in text:
        return "VARIABLE"
    if "barcode" in text:
        if any(exclusion in text for exclusion in _VARIABLE_COMPONENT_EXCLUSIONS):
            return None
        return "CELL_BARCODE"
    if re.search(r"\bindex\b", text):
        return "SAMPLE_INDEX"
    return None


def _remove_exact_neutral_sequence_claims(
    predicted: list[str], neutral: list[str]
) -> list[str]:
    remaining = list(predicted)
    for neutral_value in neutral:
        for index, predicted_value in enumerate(remaining):
            if (
                _sequence_similarity(
                    predicted_value,
                    neutral_value,
                    family_truth=bool(
                        _FAMILY_PLACEHOLDER_RE.search(normalize_sequence(neutral_value))
                    ),
                )
                == 1.0
            ):
                remaining.pop(index)
                break
    return remaining


def _sequence_collection_f1(
    predicted: list[str],
    truth: list[str],
) -> float:
    scores = [
        [
            _sequence_similarity(
                left,
                right,
                family_truth=bool(
                    _FAMILY_PLACEHOLDER_RE.search(normalize_sequence(right))
                ),
            )
            for right in truth
        ]
        for left in predicted
    ]
    matches = best_one_to_one_matching(scores)
    return _soft_prf(sum(item[2] for item in matches), len(predicted), len(truth))[2]


def _sequence_similarity(
    prediction: str,
    truth: str,
    *,
    family_truth: bool,
) -> float:
    predicted_tokens = _sequence_value(prediction)
    truth_tokens = _sequence_value(truth)
    if not family_truth:
        return edit_similarity(predicted_tokens, truth_tokens)
    return _family_edit_similarity(predicted_tokens, truth_tokens)


def _family_edit_similarity(left: list[str], right: list[str]) -> float:
    denominator = max(len(left), len(right))
    if denominator == 0:
        return 1.0
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_token in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_token in enumerate(right, start=1):
            substitution = previous[right_index - 1] + (
                not _family_tokens_match(left_token, right_token)
            )
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return max(0.0, 1.0 - previous[-1] / denominator)


def _family_tokens_match(left: str, right: str) -> bool:
    if left == right:
        return True
    left_family = _FAMILY_TOKEN_RE.fullmatch(left)
    right_family = _FAMILY_TOKEN_RE.fullmatch(right)
    if left_family and right_family:
        return left_family.group(1) == right_family.group(1)
    if left_family:
        return bool(_NUCLEOTIDE_TOKEN_RE.fullmatch(right))
    if right_family:
        return bool(_NUCLEOTIDE_TOKEN_RE.fullmatch(left))
    return False


def _sequence_value(value: str) -> list[str]:
    normalized = _t2_nucleotide_projection(value)
    return sequence_tokens(normalized, already_normalized=True)


def _t2_nucleotide_projection(value: str) -> str:
    """Remove chemistry serialization while preserving the nucleotide molecule."""

    normalized = normalize_sequence(value)
    result: list[str] = []
    cursor = 0
    while cursor < len(normalized):
        if normalized[cursor] == "[":
            end = normalized.find("]", cursor + 1)
            if end >= 0:
                result.append(normalized[cursor : end + 1])
                cursor = end + 1
                continue
        if normalized[cursor] == "/":
            end = normalized.find("/", cursor + 1)
            if end >= 0:
                tag = normalized[cursor : end + 1]
                base = _modified_base_from_tag(tag)
                if base is not None:
                    result.append(base)
                cursor = end + 1
                continue
        if normalized[cursor : cursor + 4].lower() == "(du)":
            result.append("U")
            cursor += 4
            continue
        if (
            normalized[cursor] in {"r", "+"}
            and cursor + 1 < len(normalized)
            and normalized[cursor + 1] in _STATE_COMPLEMENT
        ):
            result.append(normalized[cursor + 1])
            cursor += 2
            continue
        result.append(normalized[cursor])
        cursor += 1
    return "".join(result)


def _modified_base_from_tag(tag: str) -> str | None:
    return {
        "/ideoxyu/": "U",
        "/ibiodt/": "T",
        "/ddc/": "C",
        "/3ddc/": "C",
        "/ddu/": "U",
        "/3invdt/": "T",
    }.get(tag.lower())


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
    allow_reverse_complement: bool = False,
) -> float:
    return _weighted_supported_score(
        _state_dimensions(
            prediction,
            truth,
            scorable_only=scorable_only,
            allow_reverse_complement=allow_reverse_complement,
        ),
        STATE_WEIGHTS,
    )


def _state_dimensions(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
    allow_reverse_complement: bool = False,
) -> dict[str, tuple[float, bool]]:
    truth_reference = _reference_strand(truth)
    return {
        "reference_strand": (
            _reference_similarity(
                prediction,
                truth,
                allow_reverse_complement=allow_reverse_complement,
            ),
            not scorable_only or _supported(truth_reference),
        ),
        "architecture": (
            _architecture_similarity(prediction, truth),
            not scorable_only or _supported(truth),
        ),
        "segments": (
            _strand_collection_similarity(
                prediction,
                truth,
                scorable_only=scorable_only,
                allow_reverse_complement=allow_reverse_complement,
            ),
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


def _reference_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    allow_reverse_complement: bool,
) -> float:
    predicted = _reference_strand(prediction)
    expected = _reference_strand(truth)
    if not predicted or not expected:
        return 0.0
    scores: list[float] = []
    for predicted_value in _strand_architecture_values(predicted):
        predicted_tokens = _sequence_value(predicted_value)
        for expected_value in _strand_architecture_values(expected):
            expected_tokens = _sequence_value(expected_value)
            scores.append(_state_sequence_similarity(predicted_tokens, expected_tokens))
            if allow_reverse_complement:
                reverse_tokens = _reverse_complement_state_tokens(expected_tokens)
                if reverse_tokens is not None:
                    scores.append(
                        _state_sequence_similarity(predicted_tokens, reverse_tokens)
                    )
    return max(scores, default=0.0)


def _reference_strand(state: dict[str, Any]) -> dict[str, Any]:
    reference_id = state.get("reference_strand_id")
    return next(
        (
            strand
            for strand in state.get("strands", [])
            if strand.get("strand_id") == reference_id
        ),
        {},
    )


def _strand_architecture_values(strand: dict[str, Any]) -> list[str]:
    values: list[str] = []
    architecture = strand.get("sequence_architecture")
    if isinstance(architecture, str) and architecture:
        values.append(architecture)
    segments = strand.get("segments", [])
    if segments:
        values.append("".join(_segment_value(segment) for segment in segments))

    result: list[str] = []
    observed: set[tuple[str, ...]] = set()
    for value in values:
        key = tuple(_sequence_value(value))
        if key not in observed:
            observed.add(key)
            result.append(value)
    return result


def _reverse_complement_state_tokens(tokens: list[str]) -> list[str] | None:
    result: list[str] = []
    for token in reversed(tokens):
        if token in _STATE_COMPLEMENT:
            result.append(_STATE_COMPLEMENT[token])
        elif (token.startswith("<") and token.endswith(">")) or (
            token.startswith("[") and token.endswith("]")
        ):
            # Placeholder roles are biological and orientation-free.
            result.append(token)
        elif len(token) == 2 and token[0] in {"r", "+"}:
            complement = _STATE_COMPLEMENT.get(token[1])
            if complement is None:
                return None
            result.append(token[0] + complement)
        else:
            # Modification tags cannot be reverse-complemented mechanically.
            return None
    return result


def _state_sequence_similarity(
    predicted_tokens: list[str], expected_tokens: list[str]
) -> float:
    """Directional edit similarity for prediction-shaped state sequences.

    Ground-truth VARIABLE placeholders are wildcards. A predicted two-base
    anchored-primer shorthand (``VN`` or its opposite-strand form ``NB``) also
    satisfies a canonical ground-truth ``[ANCHOR:2]`` span. The latter is a
    phrase-level rule so neither base alone becomes an ANCHOR wildcard.
    """

    denominator = max(len(predicted_tokens), len(expected_tokens))
    if denominator == 0:
        return 1.0
    # Keep the expected/ground-truth side fixed because VARIABLE compatibility
    # is intentionally directional: a specific prediction may satisfy an
    # unspecified truth region, but a generic prediction does not satisfy a
    # specifically typed truth region.
    two_rows_back: list[int] | None = None
    previous = list(range(len(expected_tokens) + 1))
    for predicted_index, predicted in enumerate(predicted_tokens, start=1):
        current = [predicted_index]
        for expected_index, expected in enumerate(expected_tokens, start=1):
            substitution = previous[expected_index - 1] + (
                not _state_tokens_compatible(predicted, expected)
            )
            insertion = current[expected_index - 1] + 1
            deletion = previous[expected_index] + 1
            distance = min(substitution, insertion, deletion)
            if (
                predicted_index >= 2
                and expected_index >= 2
                and two_rows_back is not None
                and tuple(predicted_tokens[predicted_index - 2 : predicted_index])
                in _ANCHOR_IUPAC_SHORTHANDS
                and tuple(expected_tokens[expected_index - 2 : expected_index])
                == _ANCHOR_PLACEHOLDER_SPAN
            ):
                distance = min(
                    distance,
                    two_rows_back[expected_index - 2],
                )
            current.append(distance)
        two_rows_back, previous = previous, current
    return max(0.0, 1.0 - previous[-1] / denominator)


def _state_tokens_compatible(predicted: str, expected: str) -> bool:
    if predicted == expected:
        return True
    if expected == "<VARIABLE>":
        return predicted.startswith("<") and predicted.endswith(">")
    if expected == "[VARIABLE]":
        return predicted.startswith("[") and predicted.endswith("]")
    return False


def _segment_value(segment: dict[str, Any]) -> str:
    if isinstance(segment.get("sequence"), str):
        return segment["sequence"]
    if isinstance(segment.get("placeholder"), str):
        return segment["placeholder"]
    if isinstance(segment.get("length"), int):
        return f"[VARIABLE:{segment['length']}]"
    return "[UNKNOWN]"


def _architecture_similarity(
    prediction: dict[str, Any], truth: dict[str, Any]
) -> float:
    # Only controlled structural fields affect reward. Free-text physical-state
    # and property descriptions remain available as diagnostics below.
    scores = [
        float(
            prediction.get("strand_architecture") == truth.get("strand_architecture")
        ),
        _name_similarity(
            prediction.get("molecule_type", ""), truth.get("molecule_type", "")
        ),
        _count_similarity(
            len(prediction.get("strands", [])), len(truth.get("strands", []))
        ),
        _multiset_f1(
            [item.get("molecule_type", "") for item in prediction.get("strands", [])],
            [item.get("molecule_type", "") for item in truth.get("strands", [])],
        ),
    ]
    return sum(scores) / len(scores)


def _state_metadata_diagnostics(
    prediction: dict[str, Any], truth: dict[str, Any]
) -> dict[str, float]:
    return {
        "physical_state_similarity": _name_similarity(
            prediction.get("physical_state", ""), truth.get("physical_state", "")
        ),
        "properties_f1": _multiset_f1(
            prediction.get("properties", []), truth.get("properties", [])
        ),
    }


def _strand_collection_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    scorable_only: bool,
    allow_reverse_complement: bool = False,
) -> float:
    predicted = list(prediction.get("strands", []))
    expected = [
        strand
        for strand in truth.get("strands", [])
        if not scorable_only or _supported(strand)
    ]
    if scorable_only:
        neutral = [
            strand for strand in truth.get("strands", []) if not _supported(strand)
        ]
        predicted = _remove_exact_neutral_entities(
            predicted, neutral, _strand_similarity
        )
    scores = [
        [
            _strand_similarity(
                left,
                right,
                allow_reverse_complement=allow_reverse_complement,
            )
            for right in expected
        ]
        for left in predicted
    ]
    matches = best_one_to_one_matching(scores)
    return _soft_prf(sum(item[2] for item in matches), len(predicted), len(expected))[2]


def _strand_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    allow_reverse_complement: bool = False,
) -> float:
    direct = _strand_similarity_in_orientation(
        prediction,
        truth,
        reverse_complement=False,
    )
    if not allow_reverse_complement:
        return direct
    return max(
        direct,
        _strand_similarity_in_orientation(
            prediction,
            truth,
            reverse_complement=True,
        ),
    )


def _strand_similarity_in_orientation(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    reverse_complement: bool,
) -> float:
    predicted_segments = prediction.get("segments", [])
    truth_segments = list(truth.get("segments", []))
    if reverse_complement:
        truth_segments.reverse()
    position_scores: list[float] = []
    for left, right in zip(predicted_segments, truth_segments):
        predicted_tokens = _sequence_value(_segment_value(left))
        truth_tokens = _sequence_value(_segment_value(right))
        if reverse_complement:
            reverse_tokens = _reverse_complement_state_tokens(truth_tokens)
            sequence_score = (
                _state_sequence_similarity(predicted_tokens, reverse_tokens)
                if reverse_tokens is not None
                else 0.0
            )
        else:
            sequence_score = _state_sequence_similarity(
                predicted_tokens,
                truth_tokens,
            )
        expected_structural_role = str(right.get("structural_role", ""))
        if reverse_complement:
            expected_structural_role = {
                "five_prime_overhang": "three_prime_overhang",
                "three_prime_overhang": "five_prime_overhang",
            }.get(expected_structural_role, expected_structural_role)
        structural_score = float(
            left.get("structural_role") == expected_structural_role
        )
        position_scores.append(0.75 * sequence_score + 0.25 * structural_score)
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


def _pairing_descriptors(state: dict[str, Any], *, support: str | None) -> list[str]:
    strands = {item["strand_id"]: item for item in state.get("strands", [])}
    segment_values = {
        segment["segment_id"]: (
            f"{segment.get('structural_role', '')}:"
            + normalize_sequence(_segment_value(segment))
        )
        for strand in state.get("strands", [])
        for segment in strand.get("segments", [])
    }
    selected_regions: list[dict[str, Any]] = []
    for region in state.get("paired_regions", []):
        if support == "scorable" and not _supported(region):
            continue
        if support == "neutral" and _supported(region):
            continue
        selected_regions.append(region)

    # Region records are a serialization choice.  When several records cover
    # one continuous duplex, compare the relationship and complete paired
    # coverage once instead of penalizing one-side partition boundaries.  A
    # missing paired segment still prevents this collapse and remains visible
    # through the ordinary region descriptors below.
    grouped: dict[tuple[str, tuple[str, str]], list[dict[str, Any]]] = defaultdict(list)
    for region in selected_regions:
        strand_pair = tuple(
            sorted((region["side_1"]["strand_id"], region["side_2"]["strand_id"]))
        )
        grouped[(str(region.get("relationship", "")), strand_pair)].append(region)

    complete_groups: set[tuple[str, tuple[str, str]]] = set()
    result: list[str] = []
    for group_key, regions in grouped.items():
        relationship, strand_pair = group_key
        covered: dict[str, set[str]] = {strand_id: set() for strand_id in strand_pair}
        for region in regions:
            for side_key in ("side_1", "side_2"):
                side = region[side_key]
                covered.setdefault(side["strand_id"], set()).update(side["segment_ids"])
        expected = {
            strand_id: {
                segment["segment_id"]
                for segment in strands.get(strand_id, {}).get("segments", [])
                if segment.get("structural_role") == "paired_region"
            }
            for strand_id in strand_pair
        }
        if all(
            expected[strand_id] and covered[strand_id] == expected[strand_id]
            for strand_id in strand_pair
        ):
            complete_groups.add(group_key)
            molecule_types = sorted(
                str(strands.get(strand_id, {}).get("molecule_type", ""))
                for strand_id in strand_pair
            )
            result.append(
                f"complete_paired_coverage:{relationship}:" + "<>".join(molecule_types)
            )

    for region in selected_regions:
        strand_pair = tuple(
            sorted((region["side_1"]["strand_id"], region["side_2"]["strand_id"]))
        )
        if (str(region.get("relationship", "")), strand_pair) in complete_groups:
            continue
        sides: list[str] = []
        for key in ("side_1", "side_2"):
            side = region[key]
            molecule_type = strands.get(side["strand_id"], {}).get("molecule_type", "")
            values = [
                segment_values.get(segment_id, "") for segment_id in side["segment_ids"]
            ]
            sides.append(f"{molecule_type}:{'|'.join(values)}")
        result.append(region.get("relationship", "") + ":" + "<>".join(sorted(sides)))
    return result


def _discontinuity_descriptors(
    state: dict[str, Any], *, support: str | None
) -> list[str]:
    segment_values = {
        segment["segment_id"]: (
            f"{segment.get('structural_role', '')}:"
            + normalize_sequence(_segment_value(segment))
        )
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


def _transition_assignment_similarity(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    state_map: dict[str, str],
    predicted_oligos: Mapping[str, dict[str, Any]],
    truth_oligos: Mapping[str, dict[str, Any]],
) -> float:
    """Align events by molecular identity before adjacent-state boundaries.

    A handling transition can share a canonical event's product after one
    graph folds cleanup into the event.  Operation identity, or a scorable
    physical-oligo identity when the event has one, therefore receives a
    bounded tie-break.  The ordinary scientific score remains the reward after
    assignment, so this changes close alignments without overriding a
    materially better topology match.
    """

    dimensions = _transition_dimensions(
        prediction,
        truth,
        state_map=state_map,
        predicted_oligos=predicted_oligos,
        truth_oligos=truth_oligos,
    )
    scientific_score = sum(
        TRANSITION_WEIGHTS[key] * value for key, value in dimensions.items()
    )
    identity_scores = [dimensions["operation"]]
    if any(
        oligo_id in truth_oligos and _oligo_is_scorable(truth_oligos[oligo_id])
        for oligo_id in truth.get("oligo_ids", [])
    ):
        identity_scores.append(dimensions["oligos"])
    event_identity = max(identity_scores, default=0.0)
    return (scientific_score + _TRANSITION_IDENTITY_TIE_BREAK * event_identity) / (
        1.0 + _TRANSITION_IDENTITY_TIE_BREAK
    )


def _transition_dimensions(
    prediction: dict[str, Any],
    truth: dict[str, Any],
    *,
    state_map: dict[str, str],
    predicted_oligos: Mapping[str, dict[str, Any]],
    truth_oligos: Mapping[str, dict[str, Any]],
) -> dict[str, float]:
    return {
        "operation": _operation_similarity(
            str(prediction.get("operation", "")),
            str(truth.get("operation", "")),
        ),
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


def _operation_similarity(prediction: str, truth: str) -> float:
    """Compare controlled molecular operations using published equivalences."""

    if prediction == truth:
        return 1.0
    if any(
        prediction in group and truth in group for group in _EQUIVALENT_OPERATION_GROUPS
    ):
        return 1.0
    return 0.0


def _transition_oligo_sequence_f1(
    predicted_ids: Iterable[str],
    truth_ids: Iterable[str],
    *,
    predicted_oligos: Mapping[str, dict[str, Any]],
    truth_oligos: Mapping[str, dict[str, Any]],
) -> float:
    predicted = [predicted_oligos[item] for item in predicted_ids]
    truth = [truth_oligos[item] for item in truth_ids]
    prediction_families = _collapse_prediction_families(predicted, truth)
    scorable_truth = {
        index for index, item in enumerate(truth) if _oligo_is_scorable(item)
    }
    neutral_truth = set(range(len(truth))) - scorable_truth
    scorable_indices = sorted(scorable_truth)
    pre_neutralized_families = {
        family_index
        for family_index, family in enumerate(prediction_families)
        if any(
            _prediction_family_sequence_similarity(
                family,
                predicted,
                truth[index],
                scorable_only=False,
            )
            == 1.0
            for index in neutral_truth
        )
        and not any(
            _prediction_family_sequence_similarity(
                family,
                predicted,
                truth[index],
                scorable_only=True,
            )
            == 1.0
            for index in scorable_truth
        )
    }
    active_family_indices = [
        index
        for index in range(len(prediction_families))
        if index not in pre_neutralized_families
    ]
    scores = [
        [
            _prediction_family_sequence_similarity(
                prediction_families[family_index],
                predicted,
                truth[index],
                scorable_only=True,
            )
            for index in scorable_indices
        ]
        for family_index in active_family_indices
    ]
    raw_matches = best_one_to_one_matching(scores)
    matches = [
        (
            active_family_indices[prediction_position],
            scorable_indices[truth_position],
            score,
        )
        for prediction_position, truth_position, score in raw_matches
    ]
    matched_families = {item[0] for item in matches}
    neutralized_families: set[int] = set(pre_neutralized_families)
    for family_index in set(range(len(prediction_families))) - matched_families:
        if any(
            _prediction_family_sequence_similarity(
                prediction_families[family_index],
                predicted,
                truth[index],
                scorable_only=False,
            )
            == 1.0
            for index in neutral_truth
        ):
            neutralized_families.add(family_index)
    return _soft_prf(
        sum(item[2] for item in matches),
        len(prediction_families) - len(neutralized_families),
        len(scorable_truth),
    )[2]


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
    details = _typed_edge_analysis(
        predicted_workflow,
        truth_workflow,
        state_map=state_map,
        transition_map=transition_map,
    )
    return (
        details["matched"],
        details["predicted"],
        details["groundtruth"],
        details["neutralized_predictions"],
    )


def _typed_edge_analysis(
    predicted_workflow: Mapping[str, Any] | None,
    truth_workflow: Mapping[str, Any] | None,
    *,
    state_map: Mapping[str, str],
    transition_map: Mapping[str, str],
) -> dict[str, Any]:
    predicted_edges = _typed_edges(predicted_workflow or {})
    if truth_workflow is None:
        return {
            "matched": 0,
            "predicted": len(predicted_edges),
            "groundtruth": 0,
            "neutralized_predictions": 0,
            "matched_edges": [],
            "missing_groundtruth_edges": [],
            "extra_prediction_edges": [
                _typed_edge_document(edge) for edge in sorted(predicted_edges)
            ],
            "neutralized_prediction_edges": [],
        }
    scorable_truth_edges = _typed_edges(truth_workflow, support="scorable")
    neutral_truth_edges = _typed_edges(truth_workflow, support="neutral")
    if predicted_workflow is None:
        return {
            "matched": 0,
            "predicted": 0,
            "groundtruth": len(scorable_truth_edges),
            "neutralized_predictions": 0,
            "matched_edges": [],
            "missing_groundtruth_edges": [
                _typed_edge_document(edge) for edge in sorted(scorable_truth_edges)
            ],
            "extra_prediction_edges": [],
            "neutralized_prediction_edges": [],
        }

    mapped_by_prediction: dict[tuple[str, str, str], tuple[str, str, str] | None] = {}
    for edge_type, left, right in predicted_edges:
        if edge_type == "substrate":
            mapped_left = state_map.get(left)
            mapped_right = transition_map.get(right)
        else:
            mapped_left = transition_map.get(left)
            mapped_right = state_map.get(right)
        if mapped_left is not None and mapped_right is not None:
            mapped_by_prediction[(edge_type, left, right)] = (
                edge_type,
                mapped_left,
                mapped_right,
            )
        else:
            mapped_by_prediction[(edge_type, left, right)] = None
    mapped_predicted = {
        edge for edge in mapped_by_prediction.values() if edge is not None
    }
    matched_edges = mapped_predicted & scorable_truth_edges
    extra_prediction_edges = [
        (edge, mapped)
        for edge, mapped in sorted(mapped_by_prediction.items())
        if mapped not in scorable_truth_edges and mapped not in neutral_truth_edges
    ]
    matched = len(mapped_predicted & scorable_truth_edges)
    neutralized = len(mapped_predicted & neutral_truth_edges)
    effective_prediction_count = len(predicted_edges) - neutralized
    return {
        "matched": matched,
        "predicted": effective_prediction_count,
        "groundtruth": len(scorable_truth_edges),
        "neutralized_predictions": neutralized,
        "matched_edges": [_typed_edge_document(edge) for edge in sorted(matched_edges)],
        "missing_groundtruth_edges": [
            _typed_edge_document(edge)
            for edge in sorted(scorable_truth_edges - matched_edges)
        ],
        "extra_prediction_edges": [
            _typed_edge_document(edge, mapped_edge=mapped)
            for edge, mapped in extra_prediction_edges
        ],
        "neutralized_prediction_edges": [
            _typed_edge_document(edge, mapped_edge=mapped)
            for edge, mapped in sorted(mapped_by_prediction.items())
            if mapped in neutral_truth_edges
        ],
    }


def _typed_edge_document(
    edge: tuple[str, str, str],
    *,
    mapped_edge: tuple[str, str, str] | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "edge_type": edge[0],
        "left_id": edge[1],
        "right_id": edge[2],
    }
    if mapped_edge is not None:
        document["mapped_left_id"] = mapped_edge[1]
        document["mapped_right_id"] = mapped_edge[2]
    return document


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
    scorable_truth = {item for item in truth if _state_is_scorable(truth_states[item])}
    neutral_truth = truth - scorable_truth
    mapped = [mapping.get(item) for item in predicted]
    effective_predictions = [item for item in mapped if item not in neutral_truth]
    overlap = len(set(effective_predictions) & scorable_truth)
    return _soft_prf(float(overlap), len(effective_predictions), len(scorable_truth))[2]


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
    return _soft_prf(
        float(overlap), sum(predicted_counter.values()), sum(truth_counter.values())
    )[2]


def _supported(item: dict[str, Any]) -> bool:
    return item.get("support_status", "explicit") in SCORABLE_SUPPORT


def _weighted_supported_score(
    dimensions: dict[str, tuple[float, bool]], weights: dict[str, float]
) -> float:
    denominator = sum(
        weights[key] for key, (_, enabled) in dimensions.items() if enabled
    )
    if denominator == 0:
        return 0.0
    return (
        sum(
            weights[key] * value
            for key, (value, enabled) in dimensions.items()
            if enabled
        )
        / denominator
    )


def _enabled_dimension_scores(
    dimensions: dict[str, tuple[float, bool]],
) -> dict[str, float | None]:
    return {
        key: value if enabled else None for key, (value, enabled) in dimensions.items()
    }


def _soft_prf(
    score_sum: float, predicted_count: int, truth_count: int
) -> tuple[float, float, float]:
    precision = (
        1.0
        if predicted_count == 0 and truth_count == 0
        else (score_sum / predicted_count if predicted_count else 0.0)
    )
    recall = (
        1.0
        if truth_count == 0 and predicted_count == 0
        else (score_sum / truth_count if truth_count else 0.0)
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
