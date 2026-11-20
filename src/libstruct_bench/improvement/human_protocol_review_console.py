from __future__ import annotations

import json
import shutil
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from libstruct_bench.audit.artifacts import load_json_object, sha256_file
from libstruct_bench.libgen.scoring import score_t2
from libstruct_bench.libgen.validation import derive_required_t2_ids

from .artifacts import CapabilityImprovementError, load_and_validate

REVIEW_SECTIONS: tuple[str, ...] = (
    "t2",
    "t3-workflow-boundary",
    "t3-states",
    "t3-transitions",
    "t3-typed-edges",
    "proposal",
)

_SECTION_TITLES = {
    "t2": "T2 oligo families",
    "t3-workflow-boundary": "T3 workflow boundary",
    "t3-states": "T3 molecular states",
    "t3-transitions": "T3 molecular transitions",
    "t3-typed-edges": "T3 typed edges",
    "proposal": "Complete proposal decision",
}


@dataclass(frozen=True)
class HumanProtocolReviewBundle:
    experiment_root: Path
    proposal_path: Path
    proposal: dict[str, Any]
    comparison: dict[str, Any]
    groundtruth_t2: dict[str, Any]
    prediction_t2: dict[str, Any] | None
    groundtruth_t3: dict[str, Any]
    prediction_t3: dict[str, Any] | None


def resolve_human_protocol_proposal(
    *,
    experiment_root: Path,
    proposal_path: Path | None = None,
) -> Path:
    """Resolve the exact active human-protocol proposal without guessing bytes."""

    root = experiment_root.expanduser().resolve()
    if proposal_path is not None:
        resolved = proposal_path.expanduser().resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise CapabilityImprovementError(
                f"human protocol proposal is outside the experiment: {resolved}"
            ) from error
        return resolved

    registry = load_and_validate(
        root / "design" / "human_training_registry.json",
        schema_filename="human_training_registry.schema.json",
        digest_field="registry_digest",
        label="human training registry",
    )
    for entry in registry["entries"]:
        review_root = (
            root
            / "human-reviews"
            / str(entry["batch_id"])
            / f"{int(entry['batch_position']):02d}-{entry['protocol_id']}"
        )
        if (review_root / "review-record.json").is_file():
            continue
        revised = review_root / "proposal-r1.json"
        if revised.is_file():
            return revised
        initial = review_root / "proposal.json"
        if initial.is_file():
            decision = review_root / "decision.json"
            if decision.is_file():
                value = load_and_validate(
                    decision,
                    schema_filename="human_protocol_review_decision.schema.json",
                    digest_field="decision_digest",
                    label="human protocol review decision",
                )
                if value["disposition"] == "revision_requested":
                    raise CapabilityImprovementError(
                        "the active review requires proposal-r1.json before it can "
                        "be shown or decided"
                    )
            return initial
        raise CapabilityImprovementError(
            f"the next protocol review has no compiled proposal: {review_root}"
        )
    raise CapabilityImprovementError("all human protocol reviews are complete")


def load_human_protocol_review_bundle(
    *,
    experiment_root: Path,
    proposal_path: Path | None = None,
) -> HumanProtocolReviewBundle:
    root = experiment_root.expanduser().resolve()
    resolved_proposal = resolve_human_protocol_proposal(
        experiment_root=root,
        proposal_path=proposal_path,
    )
    proposal = load_and_validate(
        resolved_proposal,
        schema_filename="human_protocol_review_proposal.schema.json",
        digest_field="proposal_digest",
        label="human protocol review proposal",
    )
    comparison = load_and_validate(
        resolved_proposal.parent / "comparison.json",
        schema_filename="human_protocol_comparison.schema.json",
        digest_field="comparison_digest",
        label="human protocol comparison",
    )
    if proposal["comparison_digest"] != comparison["comparison_digest"]:
        raise CapabilityImprovementError(
            "human protocol proposal is stale relative to its comparison"
        )
    if proposal["protocol_id"] != comparison["protocol_id"]:
        raise CapabilityImprovementError(
            "human protocol proposal and comparison use different protocols"
        )

    prediction_t2 = _artifact_json(
        comparison,
        "t2_prediction",
        required=bool(comparison["prediction_valid"]),
    )
    prediction_t3 = _artifact_json(
        comparison,
        "t3_prediction",
        required=bool(comparison["prediction_valid"]),
    )
    return HumanProtocolReviewBundle(
        experiment_root=root,
        proposal_path=resolved_proposal,
        proposal=proposal,
        comparison=comparison,
        groundtruth_t2=_required_artifact_json(comparison, "groundtruth_t2"),
        prediction_t2=prediction_t2,
        groundtruth_t3=_required_artifact_json(comparison, "groundtruth_t3"),
        prediction_t3=prediction_t3,
    )


def render_human_protocol_review(
    bundle: HumanProtocolReviewBundle,
    *,
    width: int | None = None,
) -> str:
    """Render scorecards, exact entity comparisons, and the proposal as text."""

    terminal_width = width or shutil.get_terminal_size((150, 40)).columns
    terminal_width = max(100, min(terminal_width, 220))
    comparison = bundle.comparison
    lines = [
        "",
        "=" * terminal_width,
        f"HUMAN PROTOCOL REVIEW — {comparison['protocol_id']}",
        "=" * terminal_width,
        f"Batch {comparison['batch_id']} | protocol "
        f"{comparison['batch_position']}/5 | overall "
        f"{comparison['global_position']}/25 | parent "
        f"{comparison['display_parent_checkpoint']}",
        f"Frozen prediction: "
        f"{'valid' if comparison['prediction_valid'] else 'invalid'}",
        "",
        _render_metric_scorecard(comparison, terminal_width),
        "",
        _render_t2_comparison(bundle, terminal_width),
        "",
        _render_t3_comparison(bundle, terminal_width),
        "",
        _render_proposal(bundle.proposal, terminal_width),
        "=" * terminal_width,
        "Human approval is required. No capability bytes are changed by this review.",
        "",
    ]
    return "\n".join(lines)


def render_human_protocol_review_section(
    bundle: HumanProtocolReviewBundle,
    *,
    section: str,
    width: int | None = None,
) -> str:
    """Render one complete scientific review section for a native UI gate."""

    if section not in REVIEW_SECTIONS:
        raise CapabilityImprovementError(
            "human review section must be one of: " + ", ".join(REVIEW_SECTIONS)
        )
    terminal_width = width or shutil.get_terminal_size((150, 40)).columns
    terminal_width = max(100, min(terminal_width, 220))
    comparison = bundle.comparison
    ordinal = REVIEW_SECTIONS.index(section) + 1
    lines = [
        "",
        "=" * terminal_width,
        f"HUMAN PROTOCOL REVIEW — {comparison['protocol_id']}",
        f"Section {ordinal}/{len(REVIEW_SECTIONS)} — {_SECTION_TITLES[section]}",
        "=" * terminal_width,
        f"Batch {comparison['batch_id']} | protocol "
        f"{comparison['batch_position']}/5 | overall "
        f"{comparison['global_position']}/25 | parent "
        f"{comparison['display_parent_checkpoint']}",
        f"Proposal: {bundle.proposal['proposal_id']} "
        f"({bundle.proposal['proposal_digest'][:12]}…)",
        "",
    ]
    if section == "t2":
        lines.extend(
            [
                _render_selected_metrics(
                    comparison,
                    (
                        (
                            "T2",
                            "Exact required-family recall",
                            "t2_exact_required_family_recall",
                        ),
                        ("T2", "Required-family F1", "t2_required_family_f1"),
                    ),
                    terminal_width,
                ),
                "",
                _render_t2_comparison(bundle, terminal_width),
                "",
                _render_section_proposal(bundle, section, terminal_width),
            ]
        )
    elif section == "t3-workflow-boundary":
        lines.extend(
            [
                _render_workflow_boundaries(bundle, terminal_width),
                "",
                _render_section_proposal(bundle, section, terminal_width),
            ]
        )
    elif section == "t3-states":
        lines.extend(
            [
                _render_selected_metrics(
                    comparison,
                    (("T3", "State F1", "t3_state_f1"),),
                    terminal_width,
                ),
                "",
                _render_all_state_comparisons(bundle, terminal_width),
                "",
                _render_section_proposal(bundle, section, terminal_width),
            ]
        )
    elif section == "t3-transitions":
        lines.extend(
            [
                _render_selected_metrics(
                    comparison,
                    (
                        (
                            "T3",
                            "Molecular-transition F1",
                            "t3_molecular_transition_f1",
                        ),
                    ),
                    terminal_width,
                ),
                "",
                _render_all_transition_comparisons(bundle, terminal_width),
                "",
                _render_section_proposal(bundle, section, terminal_width),
            ]
        )
    elif section == "t3-typed-edges":
        lines.extend(
            [
                _render_selected_metrics(
                    comparison,
                    (("T3", "Typed-edge F1", "t3_typed_edge_f1"),),
                    terminal_width,
                ),
                "",
                _render_all_typed_edges(bundle, terminal_width),
                "",
                _render_section_proposal(bundle, section, terminal_width),
            ]
        )
    else:
        lines.extend(
            [
                _render_metric_scorecard(comparison, terminal_width),
                "",
                _render_proposal(bundle.proposal, terminal_width),
            ]
        )
    lines.extend(
        [
            "",
            "=" * terminal_width,
            "This section is evidence for human review; it records no decision.",
            "",
        ]
    )
    return "\n".join(lines)


def show_human_protocol_review_section(
    *,
    experiment_root: Path,
    section: str,
    proposal_path: Path | None = None,
    output: TextIO = sys.stdout,
    width: int | None = None,
) -> HumanProtocolReviewBundle:
    bundle = load_human_protocol_review_bundle(
        experiment_root=experiment_root,
        proposal_path=proposal_path,
    )
    output.write(
        render_human_protocol_review_section(
            bundle,
            section=section,
            width=width,
        )
    )
    output.flush()
    return bundle


def show_human_protocol_review(
    *,
    experiment_root: Path,
    proposal_path: Path | None = None,
    output: TextIO = sys.stdout,
    width: int | None = None,
) -> HumanProtocolReviewBundle:
    bundle = load_human_protocol_review_bundle(
        experiment_root=experiment_root,
        proposal_path=proposal_path,
    )
    output.write(render_human_protocol_review(bundle, width=width))
    output.flush()
    return bundle


def _render_metric_scorecard(
    comparison: Mapping[str, Any],
    width: int,
) -> str:
    metrics = comparison.get("metrics")
    rows: list[tuple[str, str, str, str, str]] = []
    definitions = (
        ("T2", "Exact required-family recall", "t2_exact_required_family_recall"),
        ("T2", "Required-family F1", "t2_required_family_f1"),
        ("T3", "Molecular-transition F1", "t3_molecular_transition_f1"),
        ("T3", "State F1", "t3_state_f1"),
        ("T3", "Typed-edge F1", "t3_typed_edge_f1"),
    )
    for task, label, key in definitions:
        if metrics is None:
            agent = "not scored"
            delta = "n/a"
        else:
            value = float(metrics[key])
            agent = f"{value:.4f}"
            delta = f"{value - 1.0:+.4f}"
        rows.append((task, label, "1.0000", agent, delta))
    result = [
        "T2/T3 METRIC SCORECARD",
        _table(
            ("Task", "Metric", "Ground truth", "Agent", "Agent − GT"),
            rows,
            width=width,
            fixed_widths=(6, None, 14, 14, 14),
        ),
    ]
    if metrics is not None:
        result.append(f"Composite reward (agent): {float(metrics['reward']):.4f}")
    else:
        result.append("Composite reward: not scored because the prediction is invalid")
    return "\n".join(result)


def _render_selected_metrics(
    comparison: Mapping[str, Any],
    definitions: Sequence[tuple[str, str, str]],
    width: int,
) -> str:
    metrics = comparison.get("metrics")
    rows: list[tuple[str, str, str, str, str]] = []
    for task, label, key in definitions:
        if metrics is None:
            agent = "not scored"
            delta = "n/a"
        else:
            value = float(metrics[key])
            agent = f"{value:.4f}"
            delta = f"{value - 1.0:+.4f}"
        rows.append((task, label, "1.0000", agent, delta))
    return _table(
        ("Task", "Metric", "Ground truth", "Agent", "Agent − GT"),
        rows,
        width=width,
        fixed_widths=(6, None, 14, 14, 14),
    )


def _render_workflow_boundaries(
    bundle: HumanProtocolReviewBundle,
    width: int,
) -> str:
    lines = ["T3 — ALL WORKFLOW BOUNDARIES"]
    scoring = (bundle.comparison.get("scoring") or {}).get("t3", {})
    workflow_scores = scoring.get("workflows", {})
    truth_workflows = {
        item["workflow_id"]: item for item in bundle.groundtruth_t3.get("workflows", [])
    }
    prediction_workflows = {
        item["workflow_id"]: item
        for item in (bundle.prediction_t3 or {}).get("workflows", [])
    }
    shown_truth: set[str] = set()
    shown_prediction: set[str] = set()
    for ordinal, workflow_score in enumerate(workflow_scores.values(), start=1):
        truth_id = str(workflow_score.get("groundtruth_workflow_id"))
        prediction_id = str(workflow_score.get("predicted_workflow_id"))
        shown_truth.add(truth_id)
        shown_prediction.add(prediction_id)
        lines.append(
            _workflow_boundary_card(
                title=f"Matched workflow {ordinal}/{len(workflow_scores)}",
                truth_workflow=truth_workflows.get(truth_id, {}),
                predicted_workflow=prediction_workflows.get(prediction_id, {}),
                workflow_score=workflow_score,
                width=width,
            )
        )
    for workflow_id, workflow in truth_workflows.items():
        if workflow_id not in shown_truth:
            lines.append(
                _workflow_boundary_card(
                    title="Ground-truth workflow missing from agent result",
                    truth_workflow=workflow,
                    predicted_workflow={},
                    workflow_score=None,
                    width=width,
                )
            )
    for workflow_id, workflow in prediction_workflows.items():
        if workflow_id not in shown_prediction:
            lines.append(
                _workflow_boundary_card(
                    title="Extra agent workflow",
                    truth_workflow={},
                    predicted_workflow=workflow,
                    workflow_score=None,
                    width=width,
                )
            )
    if not truth_workflows and not prediction_workflows:
        lines.append("Neither side contains a workflow.")
    return "\n".join(lines)


def _workflow_boundary_card(
    *,
    title: str,
    truth_workflow: Mapping[str, Any],
    predicted_workflow: Mapping[str, Any],
    workflow_score: Mapping[str, Any] | None,
    width: int,
) -> str:
    if workflow_score is None:
        score_text = "unmatched"
    else:
        score_text = (
            f"workflow={float(workflow_score.get('workflow_match_score', 0.0)):.4f}; "
            f"initial_boundary={float(workflow_score.get('initial_boundary_f1', 0.0)):.4f}; "
            f"terminal={float(workflow_score.get('terminal_output_f1', 0.0)):.4f}"
        )
    return _side_by_side_card(
        title=title,
        score_text=score_text,
        rows=(
            (
                "Workflow ID",
                truth_workflow.get("workflow_id"),
                predicted_workflow.get("workflow_id"),
            ),
            (
                "Initial states",
                _workflow_state_boundaries(truth_workflow, "initial_state_ids"),
                _workflow_state_boundaries(
                    predicted_workflow,
                    "initial_state_ids",
                ),
            ),
            (
                "Final outputs",
                _workflow_final_outputs(truth_workflow),
                _workflow_final_outputs(predicted_workflow),
            ),
            (
                "State count",
                len(truth_workflow.get("states", [])),
                len(predicted_workflow.get("states", [])),
            ),
            (
                "Transition count",
                len(truth_workflow.get("transitions", [])),
                len(predicted_workflow.get("transitions", [])),
            ),
        ),
        width=width,
    )


def _workflow_state_boundaries(
    workflow: Mapping[str, Any],
    key: str,
) -> str:
    states = {item["state_id"]: item for item in workflow.get("states", [])}
    return _named_ids(workflow.get(key, []), states, "name")


def _workflow_final_outputs(workflow: Mapping[str, Any]) -> str:
    states = {item["state_id"]: item for item in workflow.get("states", [])}
    values = []
    for output in workflow.get("final_outputs", []):
        state_id = str(output.get("state_id"))
        name = states.get(state_id, {}).get("name", "name unavailable")
        values.append(f"{state_id} ({name}) — {output.get('modality')}")
    return "\n".join(values) or "(none)"


def _render_all_state_comparisons(
    bundle: HumanProtocolReviewBundle,
    width: int,
) -> str:
    lines = ["T3 — ALL MOLECULAR STATES"]
    scoring = (bundle.comparison.get("scoring") or {}).get("t3", {})
    workflow_scores = scoring.get("workflows", {})
    truth_workflows = {
        item["workflow_id"]: item for item in bundle.groundtruth_t3.get("workflows", [])
    }
    prediction_workflows = {
        item["workflow_id"]: item
        for item in (bundle.prediction_t3 or {}).get("workflows", [])
    }
    for workflow_score in workflow_scores.values():
        truth_workflow = truth_workflows.get(
            str(workflow_score.get("groundtruth_workflow_id")), {}
        )
        predicted_workflow = prediction_workflows.get(
            str(workflow_score.get("predicted_workflow_id")), {}
        )
        lines.extend(
            _render_state_matches(
                truth_workflow,
                predicted_workflow,
                workflow_score,
                width,
            )
        )
    if not workflow_scores:
        lines.extend(
            _render_unpaired_state_inventories(
                truth_workflows.values(),
                prediction_workflows.values(),
                width,
            )
        )
    return "\n".join(lines)


def _render_unpaired_state_inventories(
    truth_workflows: Sequence[Mapping[str, Any]] | Any,
    prediction_workflows: Sequence[Mapping[str, Any]] | Any,
    width: int,
) -> list[str]:
    lines: list[str] = []
    for workflow in truth_workflows:
        for state in workflow.get("states", []):
            lines.append(
                _side_by_side_card(
                    title="Ground-truth state — no scored workflow match",
                    score_text="unmatched",
                    rows=_state_rows(state, {}),
                    width=width,
                )
            )
    for workflow in prediction_workflows:
        for state in workflow.get("states", []):
            lines.append(
                _side_by_side_card(
                    title="Agent state — no scored workflow match",
                    score_text="unmatched",
                    rows=_state_rows({}, state),
                    width=width,
                )
            )
    return lines


def _render_all_transition_comparisons(
    bundle: HumanProtocolReviewBundle,
    width: int,
) -> str:
    lines = ["T3 — ALL MOLECULAR TRANSITIONS"]
    scoring = (bundle.comparison.get("scoring") or {}).get("t3", {})
    workflow_scores = scoring.get("workflows", {})
    truth_workflows = {
        item["workflow_id"]: item for item in bundle.groundtruth_t3.get("workflows", [])
    }
    prediction_workflows = {
        item["workflow_id"]: item
        for item in (bundle.prediction_t3 or {}).get("workflows", [])
    }
    for workflow_score in workflow_scores.values():
        truth_workflow = truth_workflows.get(
            str(workflow_score.get("groundtruth_workflow_id")), {}
        )
        predicted_workflow = prediction_workflows.get(
            str(workflow_score.get("predicted_workflow_id")), {}
        )
        lines.extend(
            _render_transition_matches(
                truth_workflow,
                predicted_workflow,
                bundle.groundtruth_t2,
                bundle.prediction_t2 or {},
                workflow_score,
                width,
            )
        )
    if not workflow_scores:
        lines.extend(
            _render_unpaired_transition_inventories(
                truth_workflows.values(),
                prediction_workflows.values(),
                bundle.groundtruth_t2,
                bundle.prediction_t2 or {},
                width,
            )
        )
    return "\n".join(lines)


def _render_unpaired_transition_inventories(
    truth_workflows: Sequence[Mapping[str, Any]] | Any,
    prediction_workflows: Sequence[Mapping[str, Any]] | Any,
    truth_t2: Mapping[str, Any],
    predicted_t2: Mapping[str, Any],
    width: int,
) -> list[str]:
    lines: list[str] = []
    truth_oligos = {item["oligo_id"]: item for item in truth_t2.get("oligos", [])}
    predicted_oligos = {
        item["oligo_id"]: item for item in predicted_t2.get("oligos", [])
    }
    for workflow in truth_workflows:
        states = {item["state_id"]: item for item in workflow.get("states", [])}
        for transition in workflow.get("transitions", []):
            lines.append(
                _side_by_side_card(
                    title="Ground-truth transition — no scored workflow match",
                    score_text="unmatched",
                    rows=_transition_rows(
                        transition,
                        {},
                        states,
                        {},
                        truth_oligos,
                        predicted_oligos,
                    ),
                    width=width,
                )
            )
    for workflow in prediction_workflows:
        states = {item["state_id"]: item for item in workflow.get("states", [])}
        for transition in workflow.get("transitions", []):
            lines.append(
                _side_by_side_card(
                    title="Agent transition — no scored workflow match",
                    score_text="unmatched",
                    rows=_transition_rows(
                        {},
                        transition,
                        {},
                        states,
                        truth_oligos,
                        predicted_oligos,
                    ),
                    width=width,
                )
            )
    return lines


def _render_all_typed_edges(
    bundle: HumanProtocolReviewBundle,
    width: int,
) -> str:
    lines = ["T3 — ALL RAW AND SCORED TYPED EDGES"]
    truth_edges = _raw_typed_edges(bundle.groundtruth_t3)
    prediction_edges = _raw_typed_edges(bundle.prediction_t3 or {})
    lines.append(
        _side_by_side_card(
            title="Complete raw typed-edge inventories",
            score_text=(
                f"ground_truth={len(truth_edges)}; agent={len(prediction_edges)}"
            ),
            rows=(("Every edge", truth_edges, prediction_edges),),
            width=width,
        )
    )
    scoring = (bundle.comparison.get("scoring") or {}).get("t3", {})
    for workflow_score in scoring.get("workflows", {}).values():
        lines.append(_render_typed_edges(workflow_score, width))
    return "\n".join(lines)


def _raw_typed_edges(document: Mapping[str, Any]) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for workflow in document.get("workflows", []):
        for transition in workflow.get("transitions", []):
            transition_id = str(transition.get("transition_id"))
            values.extend(
                {
                    "edge_type": "substrate",
                    "left_id": str(state_id),
                    "right_id": transition_id,
                }
                for state_id in transition.get("substrate_state_ids", [])
            )
            values.extend(
                {
                    "edge_type": "carried_product",
                    "left_id": transition_id,
                    "right_id": str(state_id),
                }
                for state_id in transition.get("carried_forward_product_ids", [])
            )
            values.extend(
                {
                    "edge_type": "discarded_product",
                    "left_id": transition_id,
                    "right_id": str(state_id),
                }
                for state_id in transition.get("discarded_product_ids", [])
            )
    return sorted(values, key=lambda item: tuple(item.values()))


def _render_section_proposal(
    bundle: HumanProtocolReviewBundle,
    section: str,
    width: int,
) -> str:
    observations = {
        item["error_id"]: item for item in bundle.comparison.get("observations", [])
    }
    entity_types = {
        "t2": {"oligo_family"},
        "t3-workflow-boundary": {"workflow"},
        "t3-states": {"state"},
        "t3-transitions": {"transition"},
        "t3-typed-edges": {"typed_edge"},
    }.get(section, set())
    findings = []
    for finding in bundle.proposal.get("root_findings", []):
        if any(
            observations.get(error_id, {}).get("entity_type") in entity_types
            for error_id in finding["observation_ids"]
        ):
            findings.append(finding)
    lines = ["PROPOSAL FINDINGS RELEVANT TO THIS SECTION"]
    if not findings:
        lines.append(
            "No proposal finding is assigned directly to this section. "
            "Review the boundary itself before continuing."
        )
        return "\n".join(lines)
    for ordinal, finding in enumerate(findings, start=1):
        eligible = (
            finding["benchmark_validity"] == "valid"
            and finding["attribution"] == "agent"
        )
        lines.extend(
            [
                "",
                f"Finding {ordinal}/{len(findings)} — {finding['finding_id']}",
                f"Observations: {', '.join(finding['observation_ids'])}",
                f"Classification: {finding['benchmark_validity']} / "
                f"{finding['attribution']}",
                f"Eligible for learning: {'yes' if eligible else 'no'}",
                f"Diagnosis: {_wrap_paragraph(finding['diagnosis'], width)}",
                "Proposed remedy: "
                + _wrap_paragraph(finding["proposed_remedy"], width),
            ]
        )
    return "\n".join(lines)


def _render_t2_comparison(
    bundle: HumanProtocolReviewBundle,
    width: int,
) -> str:
    lines = ["T2 — OLIGO FAMILY COMPARISON"]
    truth = {item["oligo_id"]: item for item in bundle.groundtruth_t2.get("oligos", [])}
    prediction = {
        item["oligo_id"]: item
        for item in (bundle.prediction_t2 or {}).get("oligos", [])
    }
    scoring = (bundle.comparison.get("scoring") or {}).get("t2", {})
    diagnostic_assignment = False
    if (
        not scoring
        and bundle.comparison.get("prediction_valid") is False
        and bundle.prediction_t2 is not None
    ):
        scoring = _diagnostic_t2_assignment(bundle)
        diagnostic_assignment = bool(scoring)
    if diagnostic_assignment:
        lines.extend(
            [
                "",
                "DISPLAY-ONLY DIAGNOSTIC ASSIGNMENT",
                (
                    "The linked prediction is invalid, so T2 metrics remain unscored. "
                    "The family pairs and similarities below are computed only to make "
                    "human review readable; they do not change the frozen comparison, "
                    "reward, observations, or proposal."
                ),
            ]
        )
    matches = list(scoring.get("matches", []))
    optional_ids = set(scoring.get("optional_oligo_ids", []))
    neutral_ids = set(scoring.get("neutral_used_oligo_ids", []))
    shown_truth: set[str] = set()
    shown_prediction: set[str] = set()

    for ordinal, match in enumerate(matches, start=1):
        groundtruth_id = str(match.get("groundtruth_oligo_id"))
        prediction_id = str(match.get("prediction_oligo_id"))
        shown_truth.add(groundtruth_id)
        shown_prediction.add(prediction_id)
        scientific_score = match.get("score", match.get("sequence_score", 0.0))
        score_label = (
            "diagnostic scientific similarity"
            if diagnostic_assignment
            else "scientific score"
        )
        lines.append(
            _side_by_side_card(
                title=(
                    f"T2 family {ordinal}/{len(matches)} — {score_label} "
                    f"{float(scientific_score):.4f}"
                ),
                score_text=_score_list(match.get("dimension_scores", {})),
                rows=_oligo_rows(
                    truth.get(groundtruth_id, {}),
                    prediction.get(prediction_id, {}),
                ),
                width=width,
            )
        )

    for groundtruth_id in scoring.get("unmatched_required_family_ids", []):
        groundtruth_id = str(groundtruth_id)
        shown_truth.add(groundtruth_id)
        lines.append(
            _side_by_side_card(
                title="T2 required ground-truth family — missing from agent",
                score_text="unmatched required family",
                rows=_oligo_rows(truth.get(groundtruth_id, {}), {}),
                width=width,
            )
        )

    for groundtruth_id, groundtruth in truth.items():
        if groundtruth_id in shown_truth or groundtruth_id not in optional_ids:
            continue
        shown_truth.add(groundtruth_id)
        lines.append(
            _side_by_side_card(
                title="T2 ground-truth family — optional",
                score_text="not part of the scored family assignment",
                rows=_oligo_rows(groundtruth, {}),
                width=width,
            )
        )

    for groundtruth_id, groundtruth in truth.items():
        if groundtruth_id in shown_truth or groundtruth_id not in neutral_ids:
            continue
        shown_truth.add(groundtruth_id)
        lines.append(
            _side_by_side_card(
                title="T2 ground-truth family — neutral",
                score_text="excluded from source-only scoring",
                rows=_oligo_rows(groundtruth, {}),
                width=width,
            )
        )

    for groundtruth_id, groundtruth in truth.items():
        if groundtruth_id in shown_truth:
            continue
        shown_truth.add(groundtruth_id)
        lines.append(
            _side_by_side_card(
                title="T2 ground-truth family — unscored",
                score_text="not part of the scored family assignment",
                rows=_oligo_rows(groundtruth, {}),
                width=width,
            )
        )

    for prediction_id in scoring.get("unmatched_prediction_family_ids", []):
        prediction_id = str(prediction_id)
        shown_prediction.add(prediction_id)
        lines.append(
            _side_by_side_card(
                title="T2 agent family — unmatched",
                score_text="not assigned to a ground-truth family",
                rows=_oligo_rows({}, prediction.get(prediction_id, {})),
                width=width,
            )
        )

    for prediction_id, predicted in prediction.items():
        if prediction_id in shown_prediction:
            continue
        shown_prediction.add(prediction_id)
        lines.append(
            _side_by_side_card(
                title="T2 agent family — unscored or neutralized",
                score_text="not part of the scored family assignment",
                rows=_oligo_rows({}, predicted),
                width=width,
            )
        )

    lines.append(
        f"Inventory coverage: ground truth {len(shown_truth)}/{len(truth)} shown; "
        f"agent {len(shown_prediction)}/{len(prediction)} shown."
    )
    return "\n".join(lines)


def _diagnostic_t2_assignment(
    bundle: HumanProtocolReviewBundle,
) -> dict[str, Any]:
    """Build a presentation-only T2 assignment for an invalid linked prediction."""

    if bundle.prediction_t2 is None:
        return {}
    try:
        _, details = score_t2(
            list(bundle.prediction_t2.get("oligos", [])),
            list(bundle.groundtruth_t2.get("oligos", [])),
            required_oligo_ids=derive_required_t2_ids(bundle.groundtruth_t3),
        )
    except (IndexError, KeyError, TypeError, ValueError):
        return {}
    return details


def _render_t3_comparison(
    bundle: HumanProtocolReviewBundle,
    width: int,
) -> str:
    lines = ["T3 — MOLECULAR GRAPH COMPARISON"]
    scoring = (bundle.comparison.get("scoring") or {}).get("t3", {})
    workflow_scores = scoring.get("workflows", {})
    truth_workflows = {
        item["workflow_id"]: item for item in bundle.groundtruth_t3.get("workflows", [])
    }
    prediction_workflows = {
        item["workflow_id"]: item
        for item in (bundle.prediction_t3 or {}).get("workflows", [])
    }
    if not workflow_scores:
        lines.append("No valid scored T3 workflow comparison is available.")
        return "\n".join(lines)

    for workflow_score in workflow_scores.values():
        truth_workflow = truth_workflows.get(
            str(workflow_score.get("groundtruth_workflow_id")), {}
        )
        predicted_workflow = prediction_workflows.get(
            str(workflow_score.get("predicted_workflow_id")), {}
        )
        lines.append(
            _side_by_side_card(
                title="T3 workflow and terminal outputs",
                score_text=(
                    f"workflow={float(workflow_score.get('workflow_match_score', 0.0)):.4f}; "
                    f"terminal={float(workflow_score.get('terminal_output_f1', 0.0)):.4f}; "
                    f"initial_boundary={float(workflow_score.get('initial_boundary_f1', 0.0)):.4f}"
                ),
                rows=(
                    (
                        "Workflow ID",
                        truth_workflow.get("workflow_id"),
                        predicted_workflow.get("workflow_id"),
                    ),
                    (
                        "Initial states",
                        truth_workflow.get("initial_state_ids", []),
                        predicted_workflow.get("initial_state_ids", []),
                    ),
                    (
                        "Final outputs",
                        truth_workflow.get("final_outputs", []),
                        predicted_workflow.get("final_outputs", []),
                    ),
                ),
                width=width,
            )
        )
        lines.extend(
            _render_state_matches(
                truth_workflow, predicted_workflow, workflow_score, width
            )
        )
        lines.extend(
            _render_transition_matches(
                truth_workflow,
                predicted_workflow,
                bundle.groundtruth_t2,
                bundle.prediction_t2 or {},
                workflow_score,
                width,
            )
        )
        lines.append(_render_typed_edges(workflow_score, width))
    return "\n".join(lines)


def _render_state_matches(
    truth_workflow: Mapping[str, Any],
    predicted_workflow: Mapping[str, Any],
    workflow_score: Mapping[str, Any],
    width: int,
) -> list[str]:
    truth = {item["state_id"]: item for item in truth_workflow.get("states", [])}
    prediction = {
        item["state_id"]: item for item in predicted_workflow.get("states", [])
    }
    matches = list(workflow_score.get("state_matches", []))
    lines = ["T3 states"]
    shown_truth: set[str] = set()
    shown_prediction: set[str] = set()
    for ordinal, match in enumerate(matches, start=1):
        groundtruth_id = str(match.get("groundtruth_state_id"))
        prediction_id = str(match.get("prediction_state_id"))
        shown_truth.add(groundtruth_id)
        shown_prediction.add(prediction_id)
        groundtruth = truth.get(groundtruth_id, {})
        predicted = prediction.get(prediction_id, {})
        score = float(match.get("score", 0.0))
        lines.append(
            _side_by_side_card(
                title=f"State {ordinal}/{len(matches)} — score {score:.4f}",
                score_text=_score_list(match.get("dimension_scores", {})),
                rows=_state_rows(groundtruth, predicted),
                width=width,
            )
        )
    for state_id in workflow_score.get("unmatched_groundtruth_state_ids", []):
        shown_truth.add(str(state_id))
        lines.append(
            _side_by_side_card(
                title="Ground-truth state missing from agent graph",
                score_text="unmatched",
                rows=_state_rows(truth.get(str(state_id), {}), {}),
                width=width,
            )
        )
    for state_id in workflow_score.get("unmatched_prediction_state_ids", []):
        shown_prediction.add(str(state_id))
        lines.append(
            _side_by_side_card(
                title="Extra agent state",
                score_text="unmatched",
                rows=_state_rows({}, prediction.get(str(state_id), {})),
                width=width,
            )
        )
    for state_id, state in truth.items():
        if state_id not in shown_truth:
            lines.append(
                _side_by_side_card(
                    title="Ground-truth state — unscored",
                    score_text="not part of the scored state assignment",
                    rows=_state_rows(state, {}),
                    width=width,
                )
            )
    for state_id, state in prediction.items():
        if state_id not in shown_prediction:
            lines.append(
                _side_by_side_card(
                    title="Agent state — unscored or neutralized",
                    score_text="not part of the scored state assignment",
                    rows=_state_rows({}, state),
                    width=width,
                )
            )
    lines.append(
        f"State inventory coverage: ground truth {len(truth)}/{len(truth)} shown; "
        f"agent {len(prediction)}/{len(prediction)} shown."
    )
    return lines


def _render_transition_matches(
    truth_workflow: Mapping[str, Any],
    predicted_workflow: Mapping[str, Any],
    truth_t2: Mapping[str, Any],
    predicted_t2: Mapping[str, Any],
    workflow_score: Mapping[str, Any],
    width: int,
) -> list[str]:
    truth = {
        item["transition_id"]: item for item in truth_workflow.get("transitions", [])
    }
    prediction = {
        item["transition_id"]: item
        for item in predicted_workflow.get("transitions", [])
    }
    truth_states = {item["state_id"]: item for item in truth_workflow.get("states", [])}
    predicted_states = {
        item["state_id"]: item for item in predicted_workflow.get("states", [])
    }
    truth_oligos = {item["oligo_id"]: item for item in truth_t2.get("oligos", [])}
    predicted_oligos = {
        item["oligo_id"]: item for item in predicted_t2.get("oligos", [])
    }
    matches = list(workflow_score.get("transition_matches", []))
    lines = ["T3 transitions"]
    shown_truth: set[str] = set()
    shown_prediction: set[str] = set()
    for ordinal, match in enumerate(matches, start=1):
        groundtruth_id = str(match.get("groundtruth_transition_id"))
        prediction_id = str(match.get("prediction_transition_id"))
        shown_truth.add(groundtruth_id)
        shown_prediction.add(prediction_id)
        groundtruth = truth.get(groundtruth_id, {})
        predicted = prediction.get(prediction_id, {})
        score = float(match.get("score", 0.0))
        lines.append(
            _side_by_side_card(
                title=f"Transition {ordinal}/{len(matches)} — score {score:.4f}",
                score_text=_score_list(match.get("dimension_scores", {})),
                rows=_transition_rows(
                    groundtruth,
                    predicted,
                    truth_states,
                    predicted_states,
                    truth_oligos,
                    predicted_oligos,
                ),
                width=width,
            )
        )
    for transition_id in workflow_score.get("unmatched_groundtruth_transition_ids", []):
        shown_truth.add(str(transition_id))
        lines.append(
            _side_by_side_card(
                title="Ground-truth transition missing from agent graph",
                score_text="unmatched",
                rows=_transition_rows(
                    truth.get(str(transition_id), {}),
                    {},
                    truth_states,
                    predicted_states,
                    truth_oligos,
                    predicted_oligos,
                ),
                width=width,
            )
        )
    for transition_id in workflow_score.get("unmatched_prediction_transition_ids", []):
        shown_prediction.add(str(transition_id))
        lines.append(
            _side_by_side_card(
                title="Extra agent transition",
                score_text="unmatched",
                rows=_transition_rows(
                    {},
                    prediction.get(str(transition_id), {}),
                    truth_states,
                    predicted_states,
                    truth_oligos,
                    predicted_oligos,
                ),
                width=width,
            )
        )
    for transition_id, transition in truth.items():
        if transition_id not in shown_truth:
            lines.append(
                _side_by_side_card(
                    title="Ground-truth transition — unscored",
                    score_text="not part of the scored transition assignment",
                    rows=_transition_rows(
                        transition,
                        {},
                        truth_states,
                        predicted_states,
                        truth_oligos,
                        predicted_oligos,
                    ),
                    width=width,
                )
            )
    for transition_id, transition in prediction.items():
        if transition_id not in shown_prediction:
            lines.append(
                _side_by_side_card(
                    title="Agent transition — unscored or neutralized",
                    score_text="not part of the scored transition assignment",
                    rows=_transition_rows(
                        {},
                        transition,
                        truth_states,
                        predicted_states,
                        truth_oligos,
                        predicted_oligos,
                    ),
                    width=width,
                )
            )
    lines.append(
        "Transition inventory coverage: ground truth "
        f"{len(truth)}/{len(truth)} shown; agent "
        f"{len(prediction)}/{len(prediction)} shown."
    )
    return lines


def _render_typed_edges(
    workflow_score: Mapping[str, Any],
    width: int,
) -> str:
    values = workflow_score.get("typed_edges", {})
    rows = (
        (
            "Edge count",
            values.get("groundtruth", 0),
            values.get("predicted", 0),
        ),
        (
            "Matched edges",
            values.get("matched_edges", []),
            values.get("matched_edges", []),
        ),
        (
            "Missing from agent",
            values.get("missing_groundtruth_edges", []),
            "(missing)",
        ),
        (
            "Extra agent edges",
            "(absent)",
            values.get("extra_prediction_edges", []),
        ),
    )
    return _side_by_side_card(
        title="T3 typed edges",
        score_text=(
            f"matched={values.get('matched', 0)}; "
            f"ground_truth={values.get('groundtruth', 0)}; "
            f"agent={values.get('predicted', 0)}"
        ),
        rows=rows,
        width=width,
    )


def _render_proposal(proposal: Mapping[str, Any], width: int) -> str:
    lines = [
        "HUMAN-REVIEW PROPOSAL",
        f"Proposal: {proposal['proposal_id']}",
        f"Digest: {proposal['proposal_digest']}",
        f"Summary: {_wrap_paragraph(str(proposal['summary']), width)}",
        f"Successful self-correction: {proposal['successful_self_correction']}",
    ]
    findings = list(proposal["root_findings"])
    if not findings:
        lines.append(f"No-change rationale: {proposal['no_change_rationale']}")
        return "\n".join(lines)
    for ordinal, finding in enumerate(findings, start=1):
        eligible = (
            finding["benchmark_validity"] == "valid"
            and finding["attribution"] == "agent"
        )
        lines.extend(
            [
                "",
                "-" * width,
                f"Finding {ordinal}/{len(findings)} — {finding['finding_id']}",
                f"Observations: {', '.join(finding['observation_ids'])}",
                f"Category: {finding['category']}",
                f"Benchmark validity: {finding['benchmark_validity']}",
                f"Attribution: {finding['attribution']}",
                f"Eligible for learning: {'yes' if eligible else 'no'}",
                f"Process cause: {finding['process_cause']}",
                f"Suggested capability class: {finding['suggested_capability_class']}",
                f"Diagnosis: {_wrap_paragraph(finding['diagnosis'], width)}",
                "Generalized failure pattern: "
                + _wrap_paragraph(finding["generalized_failure_pattern"], width),
                "Proposed remedy: "
                + _wrap_paragraph(finding["proposed_remedy"], width),
                "Applicability:",
                *[f"  - {item}" for item in finding["applicability"]],
                "Exclusions:",
                *([f"  - {item}" for item in finding["exclusions"]] or ["  - (none)"]),
                "Trajectory evidence:",
                *(
                    [
                        f"  - {item['artifact_path']} {item['locator']}: "
                        f"{item['summary']}"
                        for item in finding["process_evidence"]
                    ]
                    or ["  - (none; process cause remains unresolved)"]
                ),
            ]
        )
    return "\n".join(lines)


def _oligo_rows(
    groundtruth: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> tuple[tuple[str, Any, Any], ...]:
    return (
        ("ID", groundtruth.get("oligo_id"), prediction.get("oligo_id")),
        ("Name", groundtruth.get("name"), prediction.get("name")),
        ("Kind", groundtruth.get("kind"), prediction.get("kind")),
        ("Sequence", groundtruth.get("sequence"), prediction.get("sequence")),
        ("Orientation", groundtruth.get("orientation"), prediction.get("orientation")),
        ("Role", groundtruth.get("role"), prediction.get("role")),
        (
            "Modifications",
            groundtruth.get("modifications", []),
            prediction.get("modifications", []),
        ),
        (
            "Components",
            _oligo_components(groundtruth),
            _oligo_components(prediction),
        ),
    )


def _oligo_components(oligo: Mapping[str, Any]) -> str:
    components = list(oligo.get("components", []))
    if not components:
        return "(none)"
    values = []
    for index, component in enumerate(components, start=1):
        sequence = (
            component.get("sequence")
            or component.get("placeholder")
            or (
                f"[VARIABLE:{component['length']}]"
                if isinstance(component.get("length"), int)
                else "[UNKNOWN]"
            )
        )
        modifications = component.get("modifications", [])
        suffix = f"; modifications={modifications}" if modifications else ""
        values.append(
            f"{index}. {component.get('name', '(unnamed)')}: {sequence}{suffix}"
        )
    return "\n".join(values)


def _state_rows(
    groundtruth: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> tuple[tuple[str, Any, Any], ...]:
    return (
        ("ID", groundtruth.get("state_id"), prediction.get("state_id")),
        ("Name", groundtruth.get("name"), prediction.get("name")),
        (
            "Architecture",
            groundtruth.get("strand_architecture"),
            prediction.get("strand_architecture"),
        ),
        (
            "Reference strand",
            _reference_summary(groundtruth),
            _reference_summary(prediction),
        ),
        ("Physical strands", _state_strands(groundtruth), _state_strands(prediction)),
        (
            "Paired regions",
            _pairing_summary(groundtruth),
            _pairing_summary(prediction),
        ),
        (
            "Discontinuities",
            _discontinuity_summary(groundtruth),
            _discontinuity_summary(prediction),
        ),
        (
            "Properties",
            groundtruth.get("properties", []),
            prediction.get("properties", []),
        ),
    )


def _reference_summary(state: Mapping[str, Any]) -> str:
    reference_id = state.get("reference_strand_id")
    for strand in state.get("strands", []):
        if strand.get("strand_id") == reference_id:
            return f"{reference_id}: {_strand_value(strand)}"
    return str(reference_id or "(missing)")


def _state_strands(state: Mapping[str, Any]) -> str:
    strands = list(state.get("strands", []))
    if not strands:
        return "(none)"
    values = []
    for strand in strands:
        segments = "; ".join(
            f"{item.get('role', '(role missing)')}={_segment_value(item)}"
            for item in strand.get("segments", [])
        )
        values.append(
            f"{strand.get('strand_id')} "
            f"[{strand.get('molecule_type')}, {strand.get('orientation')}]: "
            f"{_strand_value(strand)} | segments: {segments or '(none)'}"
        )
    return "\n".join(values)


def _strand_value(strand: Mapping[str, Any]) -> str:
    value = strand.get("sequence_architecture")
    if isinstance(value, str) and value:
        return value
    return (
        "".join(_segment_value(item) for item in strand.get("segments", [])) or "(none)"
    )


def _segment_value(segment: Mapping[str, Any]) -> str:
    return str(
        segment.get("sequence")
        or segment.get("placeholder")
        or (
            f"[VARIABLE:{segment['length']}]"
            if isinstance(segment.get("length"), int)
            else "[UNKNOWN]"
        )
    )


def _pairing_summary(state: Mapping[str, Any]) -> str:
    values = []
    for item in state.get("paired_regions", []):
        left = item.get("side_1", {})
        right = item.get("side_2", {})
        values.append(
            f"{item.get('relationship')}: "
            f"{left.get('strand_id')}:{left.get('segment_ids', [])} <> "
            f"{right.get('strand_id')}:{right.get('segment_ids', [])}"
        )
    return "\n".join(values) or "(none)"


def _discontinuity_summary(state: Mapping[str, Any]) -> str:
    values = [
        f"{item.get('kind')} on {item.get('strand_id')}: "
        f"{item.get('after_segment_id')} -> {item.get('before_segment_id')}"
        for item in state.get("discontinuities", [])
    ]
    return "\n".join(values) or "(none)"


def _transition_rows(
    groundtruth: Mapping[str, Any],
    prediction: Mapping[str, Any],
    truth_states: Mapping[str, Mapping[str, Any]],
    predicted_states: Mapping[str, Mapping[str, Any]],
    truth_oligos: Mapping[str, Mapping[str, Any]],
    predicted_oligos: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[str, Any, Any], ...]:
    return (
        (
            "ID",
            groundtruth.get("transition_id"),
            prediction.get("transition_id"),
        ),
        ("Operation", groundtruth.get("operation"), prediction.get("operation")),
        (
            "Substrates",
            _named_ids(
                groundtruth.get("substrate_state_ids", []), truth_states, "name"
            ),
            _named_ids(
                prediction.get("substrate_state_ids", []), predicted_states, "name"
            ),
        ),
        (
            "Oligos",
            _named_ids(groundtruth.get("oligo_ids", []), truth_oligos, "name"),
            _named_ids(prediction.get("oligo_ids", []), predicted_oligos, "name"),
        ),
        (
            "Products",
            _named_ids(groundtruth.get("product_state_ids", []), truth_states, "name"),
            _named_ids(
                prediction.get("product_state_ids", []), predicted_states, "name"
            ),
        ),
        (
            "Carried products",
            _named_ids(
                groundtruth.get("carried_forward_product_ids", []),
                truth_states,
                "name",
            ),
            _named_ids(
                prediction.get("carried_forward_product_ids", []),
                predicted_states,
                "name",
            ),
        ),
        (
            "Discarded products",
            _named_ids(
                groundtruth.get("discarded_product_ids", []), truth_states, "name"
            ),
            _named_ids(
                prediction.get("discarded_product_ids", []),
                predicted_states,
                "name",
            ),
        ),
        (
            "Major reagents",
            _reagent_summary(groundtruth),
            _reagent_summary(prediction),
        ),
        (
            "Operation detail",
            groundtruth.get("operation_detail"),
            prediction.get("operation_detail"),
        ),
    )


def _named_ids(
    values: Sequence[str],
    records: Mapping[str, Mapping[str, Any]],
    name_field: str,
) -> str:
    if not values:
        return "(none)"
    return "\n".join(
        f"{value} ({records.get(value, {}).get(name_field, 'name unavailable')})"
        for value in values
    )


def _reagent_summary(transition: Mapping[str, Any]) -> str:
    values = [
        f"{item.get('name')} ({item.get('role') or 'role unspecified'})"
        for item in transition.get("major_reagents", [])
    ]
    return "\n".join(values) or "(none)"


def _side_by_side_card(
    *,
    title: str,
    score_text: str,
    rows: Sequence[tuple[str, Any, Any]],
    width: int,
) -> str:
    heading = f"{title}\nScores: {score_text}"
    rendered = _table(
        ("Field", "Ground truth", "Agent"),
        [(field, _display(left), _display(right)) for field, left, right in rows],
        width=width,
        fixed_widths=(20, None, None),
    )
    return f"\n{heading}\n{rendered}"


def _table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    width: int,
    fixed_widths: Sequence[int | None],
) -> str:
    separators = 3 * (len(headers) - 1)
    fixed = sum(item or 0 for item in fixed_widths)
    flexible_count = sum(item is None for item in fixed_widths)
    remaining = max(20 * flexible_count, width - separators - fixed)
    flexible_width = max(20, remaining // max(1, flexible_count))
    widths = [item or flexible_width for item in fixed_widths]
    used = sum(widths) + separators
    if used > width:
        overflow = used - width
        for index in reversed(range(len(widths))):
            reducible = max(0, widths[index] - 12)
            reduction = min(reducible, overflow)
            widths[index] -= reduction
            overflow -= reduction
            if overflow == 0:
                break

    def render_row(values: Sequence[Any]) -> list[str]:
        cells = [
            _wrap_cell(_display(value), widths[index])
            for index, value in enumerate(values)
        ]
        return [
            " | ".join(
                cells[index][line].ljust(widths[index])
                if line < len(cells[index])
                else " " * widths[index]
                for index in range(len(cells))
            ).rstrip()
            for line in range(max(len(cell) for cell in cells))
        ]

    lines = render_row(headers)
    lines.append("-+-".join("-" * item for item in widths))
    for row in rows:
        lines.extend(render_row(row))
    return "\n".join(lines)


def _wrap_cell(value: str, width: int) -> list[str]:
    result: list[str] = []
    for line in value.splitlines() or [""]:
        result.extend(
            textwrap.wrap(
                line,
                width=width,
                break_long_words=True,
                break_on_hyphens=False,
                replace_whitespace=False,
            )
            or [""]
        )
    return result


def _display(value: Any) -> str:
    if value is None:
        return "(missing)"
    if isinstance(value, str):
        return value or "(empty)"
    if isinstance(value, (list, dict)):
        if not value:
            return "(none)"
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _score_list(values: Mapping[str, Any]) -> str:
    rendered = [
        f"{key}=n/a" if value is None else f"{key}={float(value):.4f}"
        for key, value in sorted(values.items())
    ]
    return "; ".join(rendered) or "(no dimension scores)"


def _wrap_paragraph(value: str, width: int) -> str:
    return "\n".join(textwrap.wrap(value, width=max(60, width - 4)))


def _artifact_json(
    comparison: Mapping[str, Any],
    role: str,
    *,
    required: bool,
) -> dict[str, Any] | None:
    values = [item for item in comparison["artifacts"] if item["role"] == role]
    if len(values) != 1:
        if required:
            raise CapabilityImprovementError(
                f"human protocol comparison has {len(values)} artifacts for {role}"
            )
        return None
    artifact = values[0]
    path = Path(artifact["path"]).expanduser().resolve()
    if not path.is_file() or sha256_file(path) != artifact["sha256"]:
        if required:
            raise CapabilityImprovementError(
                f"human protocol review artifact changed: {path}"
            )
        return None
    try:
        return load_json_object(path, label=f"human protocol {role}")
    except Exception as error:
        if required:
            raise CapabilityImprovementError(
                f"cannot load human protocol review artifact: {path}"
            ) from error
        return None


def _required_artifact_json(
    comparison: Mapping[str, Any],
    role: str,
) -> dict[str, Any]:
    value = _artifact_json(comparison, role, required=True)
    if value is None:  # pragma: no cover - guarded by required=True
        raise CapabilityImprovementError(f"missing required artifact: {role}")
    return value
