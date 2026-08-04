from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import (
    AuditArtifactError,
    load_json_object,
    sha256_file,
    validate_document,
    write_json_atomic,
)


class ReviewError(ValueError):
    """Raised when a human review packet or decision is inconsistent."""


INDIVIDUAL_REVIEW_CATEGORIES = {
    "protocol_version_confusion",
    "evaluator_or_matching_error",
    "unresolved_scientific_ambiguity",
}
INDIVIDUAL_REVIEW_RECOMMENDATIONS = {
    "propose_change",
    "fix_evaluator",
    "fix_harness",
    "exclude_from_scoring",
}


def issue_requires_individual_review(issue: dict[str, Any]) -> bool:
    """Return whether an issue should be presented individually."""

    return (
        bool(issue.get("proposed_patch"))
        or issue.get("severity") in {"blocker", "high", "medium"}
        or issue.get("category") in INDIVIDUAL_REVIEW_CATEGORIES
        or issue.get("recommendation") in INDIVIDUAL_REVIEW_RECOMMENDATIONS
    )


def render_review_packet(
    *,
    proposal_path: Path,
    proposal_schema_path: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Render a console review queue and an explicit decision template."""

    proposal_path = _file(proposal_path, "audit proposal")
    proposal_schema_path = _file(proposal_schema_path, "audit proposal schema")
    proposal = load_json_object(proposal_path, label="audit proposal")
    _validate(proposal, proposal_schema_path, "audit proposal")
    output_dir = output_dir.expanduser().resolve()
    _reject_output(output_dir)
    if output_dir.exists():
        raise ReviewError(f"review output already exists: {output_dir}")
    output_dir.mkdir(parents=True)

    report_path = output_dir / "review.txt"
    report_path.write_text(render_console_summary(proposal), encoding="utf-8")
    template = {
        "decision_id": f"{proposal['protocol_id']}:decision:REPLACE",
        "protocol_id": proposal["protocol_id"],
        "audit_id": proposal["audit_id"],
        "proposal_sha256": sha256_file(proposal_path),
        "baseline_artifacts": proposal["baseline_artifacts"],
        "reviewer": {"reviewer_id": "REPLACE"},
        "iteration": 1,
        "review_state": "in_progress",
        "review_started_at": "REPLACE",
        "review_completed_at": "REPLACE",
        "overall_disposition": "in_progress",
        "issue_decisions": [],
        "review_notes": None,
    }
    template_path = output_dir / "decision-template.json"
    write_json_atomic(template_path, template)
    return report_path, template_path


def render_console_summary(proposal: dict[str, Any]) -> str:
    """Return a concise conflict-only summary for an interactive review."""

    verified = sum(
        field["comparison_status"] == "verified_no_change"
        for field in proposal["audited_fields"]
    )
    review_issues = [
        issue for issue in proposal["issues"] if issue_requires_individual_review(issue)
    ]
    informational = [
        issue for issue in proposal["issues"] if not issue_requires_individual_review(issue)
    ]
    lines = [
        f"{proposal['protocol_id']}: {len(review_issues)} issue(s) need individual review; "
        f"{len(informational)} finding(s) need one grouped decision; "
        f"{verified} field(s) verified without change"
    ]
    severity_order = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
    for number, issue in enumerate(
        sorted(
            review_issues,
            key=lambda value: (
                severity_order[value["severity"]],
                value["task"],
                value["issue_id"],
            ),
        ),
        start=1,
    ):
        evidence = "; ".join(
            f"{item['source_id']} {json.dumps(item['locator'], sort_keys=True)}"
            for item in issue["evidence"][:3]
        )
        lines.extend(
            [
                "",
                f"[{number}] {issue['severity'].upper()} {issue['task']} — {issue['title']}",
                f"Issue: {issue['issue_id']}",
                f"Current: {_short_json(issue['current_value'])}",
                f"Proposed: {_short_json(issue['proposed_value'])}",
                f"Why: {issue['explanation']}",
                f"Evidence: {evidence}",
                "Decision: accept / reject / modify / unresolved / exclude",
            ]
        )
    if informational:
        counts: dict[str, int] = {}
        for issue in informational:
            counts[issue["task"]] = counts.get(issue["task"], 0) + 1
        summary = ", ".join(
            f"{task} {count}" for task, count in sorted(counts.items())
        )
        lines.extend(
            [
                "",
                f"Grouped informational review: {summary}.",
                "One explicit human decision is required for this group: "
                "accept as observations (no ground-truth edit) / reject and keep current / "
                "unresolved / review individually.",
                "The group answer must be recorded as a separate decision for every issue ID; "
                "nothing is accepted or updated automatically.",
            ]
        )
    if not review_issues:
        lines.append("Human confirmation is still required before promotion.")
    return "\n".join(lines) + "\n"


def _short_json(value: Any, *, limit: int = 800) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 1] + "…"


def validate_review_decision(
    *,
    proposal_path: Path,
    decision_path: Path,
    proposal_schema_path: Path,
    decision_schema_path: Path,
    require_final: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate identity and either partial or complete human issue decisions."""

    proposal_path = _file(proposal_path, "audit proposal")
    decision_path = _file(decision_path, "review decision")
    proposal = load_json_object(proposal_path, label="audit proposal")
    decision = load_json_object(decision_path, label="review decision")
    _validate(proposal, _file(proposal_schema_path, "proposal schema"), "audit proposal")
    _validate(decision, _file(decision_schema_path, "decision schema"), "review decision")
    if decision["proposal_sha256"] != sha256_file(proposal_path):
        raise ReviewError("review decision references a stale proposal hash")
    for key in ("protocol_id", "audit_id"):
        if decision[key] != proposal[key]:
            raise ReviewError(f"review decision {key} does not match proposal")
    if _artifact_map(decision["baseline_artifacts"]) != _artifact_map(
        proposal["baseline_artifacts"]
    ):
        raise ReviewError("review decision baseline hashes do not match proposal")

    issue_ids = [issue["issue_id"] for issue in proposal["issues"]]
    decision_ids = [item["issue_id"] for item in decision["issue_decisions"]]
    if len(decision_ids) != len(set(decision_ids)):
        raise ReviewError("review decision contains duplicate issue IDs")
    unknown = sorted(set(decision_ids) - set(issue_ids))
    if unknown:
        raise ReviewError(
            "review decision contains unknown issue IDs: " + ", ".join(unknown)
        )
    is_final = decision["review_state"] == "final"
    if require_final and not is_final:
        raise ReviewError("review decision is still in progress")
    if is_final and set(issue_ids) != set(decision_ids):
        raise ReviewError(
            "final review decision must decide every proposal issue exactly once; "
            f"missing={sorted(set(issue_ids) - set(decision_ids))}, "
            f"extra={sorted(set(decision_ids) - set(issue_ids))}"
        )
    if not is_final:
        if decision["overall_disposition"] != "in_progress":
            raise ReviewError("an in-progress review must use overall_disposition=in_progress")
    elif not decision_ids and decision["overall_disposition"] != "confirmed":
        raise ReviewError("a final proposal with no reviewed issues must be confirmed")
    elif decision_ids and decision["overall_disposition"] == "confirmed":
        raise ReviewError("a final proposal with reviewed issues cannot be confirmed")
    if is_final and decision_ids:
        dispositions = {item["disposition"] for item in decision["issue_decisions"]}
        if dispositions <= {"accept", "modify"}:
            expected_overall = "accepted"
        elif dispositions == {"reject"}:
            expected_overall = "rejected"
        elif dispositions == {"exclude"}:
            expected_overall = "excluded"
        elif "unresolved" in dispositions and not dispositions.intersection(
            {"accept", "modify", "exclude"}
        ):
            expected_overall = "unresolved"
        else:
            expected_overall = "partially_accepted"
        if decision["overall_disposition"] != expected_overall:
            raise ReviewError(
                "overall_disposition is inconsistent with issue decisions; "
                f"expected {expected_overall!r}"
            )
    _validate_review_time(decision)
    issues = {issue["issue_id"]: issue for issue in proposal["issues"]}
    for item in decision["issue_decisions"]:
        issue = issues[item["issue_id"]]
        if is_final and item["disposition"] != "unresolved" and not item.get(
            "confirmed_cause"
        ):
            raise ReviewError(
                f"final decision for {issue['issue_id']} requires confirmed_cause"
            )
        if item["disposition"] == "modify":
            if issue["target"]["kind"] not in {
                "groundtruth_record",
                "new_groundtruth_record",
            }:
                raise ReviewError(
                    f"issue {issue['issue_id']} cannot modify a non-ground-truth target"
                )
            if issue["target"]["kind"] == "new_groundtruth_record":
                _validate_new_artifact_patch(
                    issue["issue_id"], item["replacement_patch"]
                )
        if item["disposition"] == "accept" and issue["recommendation"] == "propose_change":
            if not issue["proposed_patch"]:
                raise ReviewError(f"accepted change {issue['issue_id']} has no patch")
            if issue["target"]["kind"] == "new_groundtruth_record":
                _validate_new_artifact_patch(
                    issue["issue_id"], issue["proposed_patch"]
                )
    return proposal, decision


def _validate_new_artifact_patch(
    issue_id: str, operations: list[dict[str, Any]]
) -> None:
    mutations = [item for item in operations if item["op"] != "test"]
    if (
        len(mutations) != 1
        or mutations[0]["op"] not in {"add", "replace"}
        or mutations[0]["path"] != ""
    ):
        raise ReviewError(
            f"new artifact issue {issue_id} must contain one root add/replace patch"
        )


def _validate_review_time(decision: dict[str, Any]) -> None:
    try:
        started = datetime.fromisoformat(decision["review_started_at"].replace("Z", "+00:00"))
        completed = datetime.fromisoformat(decision["review_completed_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ReviewError("review timestamps must be ISO 8601") from error
    duration = (completed - started).total_seconds()
    if duration < 0:
        raise ReviewError("review completion precedes review start")
    recorded = decision.get("review_duration_seconds")
    if recorded is not None and abs(float(recorded) - duration) > 1:
        raise ReviewError("review_duration_seconds does not match timestamps")


def _artifact_map(values: list[dict[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values:
        if item["source_id"] in result:
            raise ReviewError(f"duplicate baseline source ID: {item['source_id']}")
        result[item["source_id"]] = item["sha256"]
    return result


def _validate(document: dict[str, Any], schema: Path, label: str) -> None:
    try:
        validate_document(document, schema, label=label)
    except AuditArtifactError as error:
        raise ReviewError(str(error)) from error


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ReviewError(f"{label} does not exist: {path}")
    return resolved


def _reject_output(path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    if (repo / ".git").exists() and (path == repo or path.is_relative_to(repo)):
        raise ReviewError("private review output must not be written inside libstruct-bench")
