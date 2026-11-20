from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import libstruct_bench.improvement.human_protocol_review_console as console


def test_protocol_review_renders_metrics_entities_and_full_proposal(
    tmp_path: Path,
) -> None:
    rendered = console.render_human_protocol_review(_bundle(tmp_path), width=132)

    assert "T2/T3 METRIC SCORECARD" in rendered
    assert "Exact required-family recall" in rendered
    assert "Molecular-transition F1" in rendered
    assert "Ground truth" in rendered
    assert "Agent" in rendered
    assert "AACCGGTT" in rendered
    assert "AACCGGTA" in rendered
    assert "gt_state_input" in rendered
    assert "agent_state_input" in rendered
    assert "ligation" in rendered
    assert "extension" in rendered
    assert "Missing from agent" in rendered
    assert "HUMAN-REVIEW PROPOSAL" in rendered
    assert "finding_synthetic_transition" in rendered
    assert "The agent selected the wrong operation." in rendered
    assert "Require one source-located event row." in rendered
    assert "Human approval is required" in rendered


def test_section_review_is_split_and_enumerates_every_entity(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    rendered = {
        section: console.render_human_protocol_review_section(
            bundle,
            section=section,
            width=132,
        )
        for section in console.REVIEW_SECTIONS
    }

    assert "Section 1/6 — T2 oligo families" in rendered["t2"]
    assert "Exact required-family recall" in rendered["t2"]
    assert "gt_oligo" in rendered["t2"]
    assert "agent_oligo" in rendered["t2"]
    assert "gt_optional_oligo" in rendered["t2"]
    assert "T2 — OLIGO FAMILY COMPARISON" in rendered["t2"]
    assert "T2 family 1/1 — scientific score 0.8750" in rendered["t2"]
    assert "T2 ground-truth family — optional" in rendered["t2"]
    assert rendered["t2"].count("gt_optional_oligo") == 1
    assert (
        "Inventory coverage: ground truth 2/2 shown; agent 1/1 shown" in rendered["t2"]
    )
    assert "T3 workflow and terminal outputs" not in rendered["t2"]

    assert "gt_workflow" in rendered["t3-workflow-boundary"]
    assert "agent_workflow" in rendered["t3-workflow-boundary"]
    assert "gt_state_input" in rendered["t3-states"]
    assert "agent_state_input" in rendered["t3-states"]
    assert (
        "State inventory coverage: ground truth 2/2 shown; agent 2/2 shown"
        in (rendered["t3-states"])
    )
    assert "gt_transition" in rendered["t3-transitions"]
    assert "agent_transition" in rendered["t3-transitions"]
    assert (
        "Transition inventory coverage: ground truth 1/1 shown; agent 1/1 shown"
        in rendered["t3-transitions"]
    )
    assert "Every edge" in rendered["t3-typed-edges"]
    assert "finding_synthetic_transition" in rendered["proposal"]
    assert "AACCGGTT" not in rendered["proposal"]


def test_invalid_prediction_renders_display_only_t2_family_assignment(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    comparison = copy.deepcopy(bundle.comparison)
    comparison["prediction_valid"] = False
    comparison["metrics"] = None
    comparison["scoring"] = None

    rendered = console.render_human_protocol_review_section(
        replace(bundle, comparison=comparison),
        section="t2",
        width=132,
    )

    assert "not scored" in rendered
    assert "DISPLAY-ONLY DIAGNOSTIC ASSIGNMENT" in rendered
    assert "T2 family 1/1 — diagnostic scientific similarity" in rendered
    assert "gt_oligo" in rendered
    assert "agent_oligo" in rendered
    assert "T2 ground-truth family — optional" in rendered
    assert "do not change the frozen comparison" in rendered


def _bundle(tmp_path: Path) -> console.HumanProtocolReviewBundle:
    groundtruth_t2 = {
        "oligos": [
            {
                "oligo_id": "gt_oligo",
                "name": "Ground-truth primer",
                "kind": "single",
                "sequence": "AACCGGTT",
                "orientation": "5_to_3",
                "role": "primer",
                "modifications": [],
                "components": [],
            },
            {
                "oligo_id": "gt_optional_oligo",
                "name": "Optional ground-truth primer",
                "kind": "single",
                "sequence": "TTTTAAAA",
                "orientation": "5_to_3",
                "role": "sequencing_primer",
                "modifications": [],
                "components": [],
            },
        ]
    }
    prediction_t2 = {
        "oligos": [
            {
                "oligo_id": "agent_oligo",
                "name": "Agent primer",
                "kind": "single",
                "sequence": "AACCGGTA",
                "orientation": "5_to_3",
                "role": "primer",
                "modifications": [],
                "components": [],
            }
        ]
    }
    groundtruth_t3 = {
        "workflows": [
            {
                "workflow_id": "gt_workflow",
                "initial_state_ids": ["gt_state_input"],
                "final_outputs": [
                    {"state_id": "gt_state_output", "modality": "synthetic"}
                ],
                "states": [
                    _state("gt_state_input", "GT input", "[GT_INPUT]"),
                    _state("gt_state_output", "GT output", "[GT_OUTPUT]"),
                ],
                "transitions": [
                    _transition(
                        "gt_transition",
                        "ligation",
                        "gt_state_input",
                        "gt_state_output",
                        "gt_oligo",
                    )
                ],
            }
        ]
    }
    prediction_t3 = {
        "workflows": [
            {
                "workflow_id": "agent_workflow",
                "initial_state_ids": ["agent_state_input"],
                "final_outputs": [
                    {"state_id": "agent_state_output", "modality": "synthetic"}
                ],
                "states": [
                    _state("agent_state_input", "Agent input", "[AGENT_INPUT]"),
                    _state("agent_state_output", "Agent output", "[AGENT_OUTPUT]"),
                ],
                "transitions": [
                    _transition(
                        "agent_transition",
                        "extension",
                        "agent_state_input",
                        "agent_state_output",
                        "agent_oligo",
                    )
                ],
            }
        ]
    }
    comparison = {
        "protocol_id": "synthetic_protocol",
        "batch_id": "B1",
        "batch_position": 1,
        "global_position": 1,
        "display_parent_checkpoint": "H0",
        "prediction_valid": True,
        "metrics": {
            "reward": 0.5,
            "t2_exact_required_family_recall": 0.6,
            "t2_required_family_f1": 0.7,
            "t3_molecular_transition_f1": 0.4,
            "t3_state_f1": 0.8,
            "t3_typed_edge_f1": 0.5,
        },
        "scoring": {
            "t2": {
                "matches": [
                    {
                        "groundtruth_oligo_id": "gt_oligo",
                        "prediction_oligo_id": "agent_oligo",
                        "sequence_score": 0.875,
                        "dimension_scores": {"sequence": 0.875, "kind": 1.0},
                    }
                ],
                "unmatched_required_family_ids": [],
                "unmatched_prediction_family_ids": [],
                "optional_oligo_ids": ["gt_optional_oligo"],
                "neutral_used_oligo_ids": [],
            },
            "t3": {
                "workflows": {
                    "gt_workflow": {
                        "groundtruth_workflow_id": "gt_workflow",
                        "predicted_workflow_id": "agent_workflow",
                        "workflow_match_score": 0.5,
                        "terminal_output_f1": 1.0,
                        "initial_boundary_f1": 1.0,
                        "state_matches": [
                            {
                                "groundtruth_state_id": "gt_state_input",
                                "prediction_state_id": "agent_state_input",
                                "score": 0.75,
                                "dimension_scores": {"architecture": 1.0},
                            }
                        ],
                        "unmatched_groundtruth_state_ids": ["gt_state_output"],
                        "unmatched_prediction_state_ids": ["agent_state_output"],
                        "transition_matches": [
                            {
                                "groundtruth_transition_id": "gt_transition",
                                "prediction_transition_id": "agent_transition",
                                "score": 0.4,
                                "dimension_scores": {"operation": 0.0},
                            }
                        ],
                        "unmatched_groundtruth_transition_ids": [],
                        "unmatched_prediction_transition_ids": [],
                        "typed_edges": {
                            "groundtruth": 3,
                            "predicted": 2,
                            "matched": 2,
                            "matched_edges": [
                                {
                                    "edge_type": "substrate",
                                    "left_id": "gt_state_input",
                                    "right_id": "gt_transition",
                                }
                            ],
                            "missing_groundtruth_edges": [
                                {
                                    "edge_type": "oligo",
                                    "left_id": "gt_oligo",
                                    "right_id": "gt_transition",
                                }
                            ],
                            "extra_prediction_edges": [],
                        },
                    }
                }
            },
        },
    }
    proposal = {
        "proposal_id": "B1:01:synthetic_protocol:r0",
        "proposal_digest": "a" * 64,
        "protocol_id": "synthetic_protocol",
        "summary": "One synthetic operation mismatch needs human adjudication.",
        "successful_self_correction": "not_observed",
        "root_findings": [
            {
                "finding_id": "finding_synthetic_transition",
                "observation_ids": ["err_0001"],
                "category": "operation_error",
                "benchmark_validity": "valid",
                "attribution": "agent",
                "process_cause": "evidence_retrieved_but_misinterpreted",
                "suggested_capability_class": "hybrid",
                "diagnosis": "The agent selected the wrong operation.",
                "generalized_failure_pattern": (
                    "A source-visible event is assigned a neighboring operation label."
                ),
                "proposed_remedy": "Require one source-located event row.",
                "applicability": ["synthetic molecular workflows"],
                "exclusions": ["handling-only steps"],
                "process_evidence": [
                    {
                        "artifact_path": "/logs/agent/trajectory.json",
                        "locator": "steps[1]",
                        "summary": "The operation evidence was retrieved.",
                    }
                ],
            }
        ],
        "no_change_rationale": None,
    }
    return console.HumanProtocolReviewBundle(
        experiment_root=tmp_path,
        proposal_path=tmp_path / "proposal.json",
        proposal=proposal,
        comparison=comparison,
        groundtruth_t2=groundtruth_t2,
        prediction_t2=prediction_t2,
        groundtruth_t3=groundtruth_t3,
        prediction_t3=prediction_t3,
    )


def _state(state_id: str, name: str, sequence: str) -> dict:
    strand_id = f"{state_id}_strand"
    segment_id = f"{state_id}_segment"
    return {
        "state_id": state_id,
        "name": name,
        "strand_architecture": "single_stranded",
        "reference_strand_id": strand_id,
        "strands": [
            {
                "strand_id": strand_id,
                "molecule_type": "DNA",
                "orientation": "5_to_3",
                "sequence_architecture": sequence,
                "segments": [
                    {
                        "segment_id": segment_id,
                        "role": "synthetic segment",
                        "placeholder": sequence,
                    }
                ],
            }
        ],
        "paired_regions": [],
        "discontinuities": [],
        "properties": [],
    }


def _transition(
    transition_id: str,
    operation: str,
    substrate_id: str,
    product_id: str,
    oligo_id: str,
) -> dict:
    return {
        "transition_id": transition_id,
        "operation": operation,
        "substrate_state_ids": [substrate_id],
        "oligo_ids": [oligo_id],
        "product_state_ids": [product_id],
        "carried_forward_product_ids": [product_id],
        "discarded_product_ids": [],
        "major_reagents": [],
        "operation_detail": "Synthetic operation detail.",
    }
