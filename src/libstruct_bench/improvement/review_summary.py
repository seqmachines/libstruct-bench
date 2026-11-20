from __future__ import annotations

import re
from pathlib import Path
from textwrap import wrap
from typing import Any, Mapping

from libstruct_bench.audit.artifacts import validate_document, write_json_atomic

from .artifacts import (
    CapabilityImprovementError,
    improvement_schema_root,
    load_and_validate,
    with_digest,
)
from .workflow import validate_capability_decision


SUMMARY_SCHEMA_VERSION = "libstruct.libgen_capability_review_summary.v1"
_EFFECTS = {
    "accept": "candidate_bytes_accepted",
    "reject": "parent_bytes_retained",
    "modify": "revision_required",
    "unresolved": "no_change_unresolved",
}


def build_capability_review_summary(
    *,
    proposal_path: Path,
    decision_path: Path,
) -> dict[str, Any]:
    """Render recorded proposal and reviewer claims without adding conclusions."""

    proposal, decision = validate_capability_decision(
        proposal_path=proposal_path,
        decision_path=decision_path,
        require_final=False,
    )
    if decision["review_state"] not in {"final", "revision_requested"}:
        raise CapabilityImprovementError(
            "a review summary requires a completed capability decision"
        )
    by_change = {item["change_id"]: item for item in decision["change_decisions"]}
    counts = {
        "proposed": len(proposal["change_units"]),
        "accept": 0,
        "reject": 0,
        "modify": 0,
        "unresolved": 0,
    }
    lessons: list[dict[str, Any]] = []
    for change in proposal["change_units"]:
        review = by_change[change["change_id"]]
        disposition = review["disposition"]
        counts[disposition] += 1
        mutation_paths = [item["path"] for item in change["mutations"]]
        lessons.append(
            {
                "change_id": change["change_id"],
                "capability_class": change["capability_class"],
                "mutation_paths": mutation_paths,
                "enforcement_paths": list(change["enforcement_paths"]),
                "finding_codes": list(change["finding_codes"]),
                "residual_judgment": change["residual_judgment"],
                "disposition": disposition,
                "effect": _EFFECTS[disposition],
                "generalized_failure_pattern": change["generalized_failure_pattern"],
                "expected_invariant": change["expected_invariant"],
                "reviewer_rationale": review["rationale"],
                "revision_instruction": review.get("revision_instruction"),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "summary_id": decision["decision_id"] + ":summary",
        "proposal_digest": proposal["proposal_digest"],
        "decision_digest": decision["decision_digest"],
        "branch": proposal["branch"],
        "batch_id": proposal["batch_id"],
        "reviewer_kind": decision["reviewer_kind"],
        "revision_round": proposal["revision_round"],
        "review_state": decision["review_state"],
        "counts": counts,
        "lessons": lessons,
    }
    summary = with_digest(payload, "summary_digest")
    validate_document(
        summary,
        improvement_schema_root() / "capability_review_summary.schema.json",
        label="capability review summary",
    )
    return summary


def write_or_validate_capability_review_summary(
    *,
    proposal_path: Path,
    decision_path: Path,
    output_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    path = (
        output_path.expanduser().resolve()
        if output_path is not None
        else default_review_summary_path(decision_path)
    )
    expected = build_capability_review_summary(
        proposal_path=proposal_path,
        decision_path=decision_path,
    )
    if path.is_file():
        observed = load_and_validate(
            path,
            schema_filename="capability_review_summary.schema.json",
            digest_field="summary_digest",
            label="capability review summary",
        )
        if observed != expected:
            raise CapabilityImprovementError(
                f"existing review summary has stale decision lineage: {path}"
            )
        return path, observed
    if path.exists():
        raise CapabilityImprovementError(
            f"review-summary path exists but is not a regular file: {path}"
        )
    write_json_atomic(path, expected, mode=0o444)
    return path, expected


def default_review_summary_path(decision_path: Path) -> Path:
    decision = decision_path.expanduser().resolve()
    name = (
        "review-summary.json"
        if decision.name == "decision.json"
        else decision.stem + ".review-summary.json"
    )
    return decision.with_name(name)


def render_capability_review_summary(
    summary: Mapping[str, Any],
    *,
    summary_path: Path | None = None,
) -> str:
    """Render only the operational result; the JSON retains full review detail."""

    counts = summary["counts"]
    outcome = (
        "REVISION REQUIRED"
        if summary["review_state"] == "revision_requested"
        else "FINAL"
    )
    lines = [
        f"Review: {summary['branch'].title()} {summary['batch_id']} - {outcome}",
        (
            f"Decision summary: {counts['accept']} accepted, "
            f"{counts['modify']} requiring revision, {counts['reject']} rejected, "
            f"{counts['unresolved']} unresolved ({counts['proposed']} total)."
        ),
    ]
    revisions = [item for item in summary["lessons"] if item["disposition"] == "modify"]
    accepted = [item for item in summary["lessons"] if item["disposition"] == "accept"]
    if accepted:
        lines.append("Accepted improvements:")
        for index, item in enumerate(accepted, start=1):
            lines.append(f"  {index}. {_display_name(item['change_id'])}")
            lines.append(
                f"     Control: {item['capability_class']}; files: "
                + ", ".join(item["mutation_paths"])
            )
            if item["finding_codes"]:
                lines.append("     Findings: " + ", ".join(item["finding_codes"]))
            if item["residual_judgment"] is not None:
                lines.extend(
                    _wrapped_line(
                        "     Human judgment remains: ",
                        item["residual_judgment"],
                    )
                )
            lines.extend(
                _wrapped_line(
                    "     Ensures: ",
                    item["expected_invariant"],
                )
            )
    if revisions:
        lines.append("Revision required:")
        for item in revisions:
            lines.append(f"  - {_display_name(item['change_id'])}")
            lines.append(
                f"    Control: {item['capability_class']}; files: "
                + ", ".join(item["mutation_paths"])
            )
            lines.extend(
                _wrapped_line(
                    "    Problem: ",
                    _compact_problem(item["reviewer_rationale"]),
                )
            )
            instruction = item["revision_instruction"]
            if instruction is not None:
                lines.append("    Required fixes:")
                for clause in _compact_instruction_clauses(instruction):
                    lines.extend(_wrapped_line("      - ", clause))
        lines.append("Next step: run the single bounded revision workflow.")
        lines.append("Application status: pending revision; nothing has been applied.")
    else:
        lines.append(
            "Next step: deterministic application may proceed when authorized."
        )
        attention = [
            item
            for item in summary["lessons"]
            if item["disposition"] in {"reject", "unresolved"}
        ]
        if attention:
            lines.append("Not accepted:")
            for item in attention:
                lines.append(
                    f"  - {item['disposition'].title()}: "
                    f"{_display_name(item['change_id'])} "
                    f"({', '.join(item['mutation_paths'])})"
                )
                lines.extend(
                    _wrapped_line(
                        "    Reason: ",
                        _shorten(item["reviewer_rationale"], limit=240),
                    )
                )
    if summary_path is not None:
        lines.append(f"Full review record: {summary_path.expanduser().resolve()}")
    return "\n".join(lines) + "\n"


def render_completion_summary(
    result: Mapping[str, Any],
    review_summary: Mapping[str, Any],
) -> str:
    """Render the durable lesson, exact-byte effect, residuals, and checkpoint."""

    accepted = [
        item for item in review_summary["lessons"] if item["disposition"] == "accept"
    ]
    residual = [
        item
        for item in review_summary["lessons"]
        if item["disposition"] in {"reject", "unresolved"}
    ]
    judgment_residual = [
        item for item in accepted if item.get("residual_judgment") is not None
    ]
    lines = [
        "Learned: "
        + (
            "; ".join(
                f"{_display_name(item['change_id'])} — "
                f"{_shorten(item['expected_invariant'], limit=150)}"
                for item in accepted
            )
            if accepted
            else "no candidate control was accepted"
        )
    ]
    if result["status"] == "checkpoint_ready":
        lines.append(
            f"Changed: {len(accepted)} accepted control(s) were applied as exact "
            f"candidate bytes; pack {result['pack_digest'][:12]}…."
        )
    elif result["status"] == "decision_ready":
        lines.append(
            f"Changed: none yet; {len(accepted)} accepted control(s) await explicit "
            "--authorize-apply."
        )
    else:
        lines.append("Changed: no checkpoint bytes were frozen in this invocation.")
    if residual or judgment_residual:
        values = [
            f"{item['disposition']} {_display_name(item['change_id'])}"
            for item in residual
        ]
        values.extend(
            f"{_display_name(item['change_id'])} still requires judgment: "
            f"{_shorten(item['residual_judgment'], limit=140)}"
            for item in judgment_residual
        )
        lines.append("Residual: " + "; ".join(values))
    else:
        lines.append("Residual: no rejected or unresolved proposal units.")
    if result.get("checkpoint_id"):
        lines.append(
            f"Checkpoint: {result['checkpoint_id']} ({result['checkpoint_digest'][:12]}…) "
            f"at {result['checkpoint_path']}."
        )
    else:
        lines.append(f"Checkpoint: not created; {result['next_action']}.")
    lines.append(f"Full review record: {result['review_summary_path']}")
    return "\n".join(lines) + "\n"


def _display_name(change_id: str) -> str:
    words = change_id.replace("-", "_").split("_")
    minor = {"and", "or", "to", "of", "the", "in"}
    return " ".join(
        word.lower() if index and word.lower() in minor else word.title()
        for index, word in enumerate(words)
    ).strip()


def _compact_instruction_clauses(value: str) -> list[str]:
    instruction = " ".join(value.split())
    if ":" in instruction:
        prefix, remainder = instruction.split(":", 1)
        if len(prefix) <= 180:
            instruction = remainder.strip()
    clauses: list[str] = []
    for raw_clause in instruction.split(";"):
        clause = raw_clause.strip().rstrip(".")
        if clause.lower().startswith(("do not ", "update ")):
            continue
        if clause.lower().startswith("and "):
            clause = clause[4:]
        for marker in (". Do not ", ". Update "):
            if marker in clause:
                clause = clause.split(marker, 1)[0].rstrip()
        if clause:
            clauses.append(clause)
        if len(clauses) == 3:
            break
    return [clause.rstrip(".") + "." for clause in clauses] or [instruction]


def _compact_problem(value: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(value.split()))
    selected = next(
        (
            sentence
            for sentence in sentences
            if sentence.lower().startswith(
                ("however,", "but ", "the concern", "the issue")
            )
        ),
        sentences[-1],
    )
    for prefix in ("However, ", "But "):
        if selected.startswith(prefix):
            selected = selected[len(prefix) :]
    return _shorten(selected, limit=320)


def _shorten(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    shortened = normalized[: limit - 1].rsplit(" ", 1)[0]
    return shortened.rstrip(".,;:") + "…"


def _wrapped_line(label: str, value: str) -> list[str]:
    return wrap(
        label + value,
        width=100,
        subsequent_indent=" " * len(label),
        break_long_words=False,
        break_on_hyphens=False,
    )
