from __future__ import annotations

import json
import re
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
            f"supports {item['supports']}: {item['source_id']} "
            f"{json.dumps(item['locator'], sort_keys=True)}"
            for item in issue["evidence"][:3]
        )
        lines.extend(
            [
                "",
                f"[{number}] {issue['severity'].upper()} {issue['task']} — {issue['title']}",
                f"Issue: {issue['issue_id']}",
                f"Field: {issue['field_id']}",
                f"Category / defect: {issue['category']} / {issue['defect_type']}",
                f"Target: {issue['target']['kind']} "
                f"({'patch' if issue['proposed_patch'] else 'no patch'})",
                f"Support: {issue['support_status']}",
                f"Current: {_short_json(issue['current_value'])}",
                f"Proposed: {_short_json(issue['proposed_value'])}",
                f"Evidence locators: {evidence}",
                f"Reason / impact: {issue['explanation']}",
                (
                    "Decision: accept / reject / modify / unresolved / exclude"
                    if issue["target"]["kind"]
                    in {"groundtruth_record", "new_groundtruth_record"}
                    else "Decision: accept / reject / unresolved / exclude"
                ),
            ]
        )
        if issue.get("notes"):
            lines.append(f"Notes: {issue['notes']}")
        if issue["target"]["kind"] not in {
            "groundtruth_record",
            "new_groundtruth_record",
        }:
            lines.append(
                "To change ground truth, modify the linked ground-truth candidate; "
                "this finding itself has no patch."
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
    if is_compiled_review_decision(decision):
        _validate_compiled_review_decision(
            proposal_path=proposal_path,
            proposal=proposal,
            decision=decision,
            require_final=require_final,
        )
        return proposal, decision
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


def is_compiled_review_decision(decision: dict[str, Any]) -> bool:
    """Return whether a decision approves complete compiled task roots."""

    return "decisions" in decision


def proposal_decision_items(
    proposal: dict[str, Any], decision: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return decisions corresponding to frozen proposal issues."""

    if not is_compiled_review_decision(decision):
        return list(decision["issue_decisions"])
    proposal_ids = {item["issue_id"] for item in proposal["issues"]}
    return [
        item for item in decision["decisions"] if item["issue_id"] in proposal_ids
    ]


def gate_decision_items(
    proposal: dict[str, Any], decision: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return post-proposal source-check or final-consistency decisions."""

    if not is_compiled_review_decision(decision):
        return []
    proposal_ids = {item["issue_id"] for item in proposal["issues"]}
    return [
        item for item in decision["decisions"] if item["issue_id"] not in proposal_ids
    ]


def all_review_decision_items(
    proposal: dict[str, Any], decision: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return every explicit human decision, including gate-stage findings."""

    if is_compiled_review_decision(decision):
        return list(decision["decisions"])
    return list(decision["issue_decisions"])


def deferred_root_issue_ids(decision: dict[str, Any]) -> tuple[str, ...]:
    """Return proposal root issues embodied by compiled candidate documents."""

    if not is_compiled_review_decision(decision):
        return ()
    return tuple(decision["deferred_root_issues"])


_LEGACY_ROOT_ID = re.compile(
    r"^harness\.(t1|t2|t3)\.root-migration(?:\.[0-9]+)?$"
)
_LEGACY_GATE_PREFIXES = ("FACTCHECK-", "SWEEP-")


def compiled_root_operation_ids(
    proposal: dict[str, Any], decision: dict[str, Any]
) -> dict[str, str]:
    """Return one deterministic application operation ID per approved task root.

    A compiled review may start from a proposal that already has canonical T1--T3
    records.  Such a proposal has no scientific reason to invent a root-conversion
    issue, even though deterministic application still replaces each approved
    document with the exact hash-pinned candidate.  Prefer an explicit deferred
    proposal root when one exists, retain the historical ``harness.*`` IDs used by
    already-finalized reviews, and otherwise derive an application-only ID from the
    task.  Application-only IDs are regression identities, not proposal findings or
    human decisions.
    """

    if not is_compiled_review_decision(decision):
        return {}
    candidate_tasks = set(decision.get("candidate_sha256", {}))
    unknown_tasks = sorted(candidate_tasks - {"T1", "T2", "T3"})
    if unknown_tasks:
        raise ReviewError(
            "compiled review approves unknown task roots: "
            + ", ".join(unknown_tasks)
        )

    proposal_issues = {
        item["issue_id"]: item for item in proposal.get("issues", [])
    }
    proposal_roots = _proposal_roots_by_task(proposal)
    roots: dict[str, str] = {}
    for issue_id in decision.get("deferred_root_issues", []):
        issue = proposal_issues.get(issue_id)
        if issue is not None:
            if not _is_compiled_root_issue(issue):
                raise ReviewError(
                    f"deferred issue {issue_id} is not a task-root conversion"
                )
            task = issue["task"]
        else:
            match = _LEGACY_ROOT_ID.fullmatch(issue_id)
            if match is None:
                raise ReviewError(
                    "deferred root is neither a proposal issue nor a supported "
                    f"historical harness root ID: {issue_id}"
                )
            task = match.group(1).upper()
        if task in roots:
            raise ReviewError(f"compiled review has multiple roots for {task}")
        roots[task] = issue_id

    for task, issue in proposal_roots.items():
        if task in roots and roots[task] != issue["issue_id"]:
            raise ReviewError(
                f"compiled review declares a different {task} root from the proposal"
            )
        roots.setdefault(task, issue["issue_id"])

    extra_root_tasks = sorted(set(roots) - candidate_tasks)
    if extra_root_tasks:
        raise ReviewError(
            "compiled proposal roots lack approved candidate hashes: "
            + ", ".join(extra_root_tasks)
        )
    for task in sorted(candidate_tasks):
        roots.setdefault(task, f"compiled.{task.lower()}.root-application")
    return roots


def proposal_compiled_root_issue_ids(proposal: dict[str, Any]) -> set[str]:
    """Return proposal issue IDs whose only mutation is a complete task root."""

    return {
        issue["issue_id"]
        for issue in _proposal_roots_by_task(proposal).values()
    }


def _proposal_roots_by_task(
    proposal: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    roots: dict[str, dict[str, Any]] = {}
    for issue in proposal.get("issues", []):
        if not _is_compiled_root_issue(issue):
            continue
        task = issue["task"]
        if task in roots:
            raise ReviewError(f"compiled proposal has multiple roots for {task}")
        roots[task] = issue
    return roots


def _is_compiled_root_issue(issue: dict[str, Any]) -> bool:
    if (
        issue.get("task") not in {"T1", "T2", "T3"}
        or issue.get("recommendation") != "propose_change"
        or issue.get("target", {}).get("kind")
        not in {"groundtruth_record", "new_groundtruth_record"}
    ):
        return False
    mutations = [
        item
        for item in issue.get("proposed_patch", [])
        if item.get("op") != "test"
    ]
    if len(mutations) != 1 or mutations[0].get("path") != "":
        return False
    expected_op = (
        "replace"
        if issue["target"]["kind"] == "groundtruth_record"
        else "add"
    )
    return mutations[0].get("op") == expected_op


def _validate_compiled_review_decision(
    *,
    proposal_path: Path,
    proposal: dict[str, Any],
    decision: dict[str, Any],
    require_final: bool,
) -> None:
    """Validate a conversion-first review bound to complete T1-T3 candidates."""

    for key in ("protocol_id", "audit_id"):
        if decision[key] != proposal[key]:
            raise ReviewError(f"review decision {key} does not match proposal")
    if decision.get("proposal_sha256") not in {None, sha256_file(proposal_path)}:
        raise ReviewError("review decision references a stale proposal hash")
    if "baseline_artifacts" in decision and _artifact_map(
        decision["baseline_artifacts"]
    ) != _artifact_map(proposal["baseline_artifacts"]):
        raise ReviewError("review decision baseline hashes do not match proposal")

    proposal_ids = [item["issue_id"] for item in proposal["issues"]]
    if len(proposal_ids) != len(set(proposal_ids)):
        raise ReviewError("audit proposal contains duplicate issue IDs")
    proposal_id_set = set(proposal_ids)
    proposal_root_ids = proposal_compiled_root_issue_ids(proposal)
    proposal_finding_ids = proposal_id_set - proposal_root_ids
    issue_order = decision["issue_order"]
    deferred = decision["deferred_root_issues"]
    if set(issue_order).intersection(deferred):
        raise ReviewError("compiled review issue_order and deferred roots overlap")
    issue_order_extra = set(issue_order) - proposal_finding_ids
    if issue_order_extra:
        raise ReviewError(
            "compiled review issue_order contains unknown or task-root issues: "
            + ", ".join(sorted(issue_order_extra))
        )

    items = decision["decisions"]
    decision_ids = [item["issue_id"] for item in items]
    if len(decision_ids) != len(set(decision_ids)):
        raise ReviewError("review decision contains duplicate decision IDs")
    proposal_decision_ids = {
        item_id for item_id in decision_ids if item_id in proposal_id_set
    }
    decided_root_ids = proposal_decision_ids.intersection(proposal_root_ids)
    if decided_root_ids:
        raise ReviewError(
            "task-root application operations cannot also be scientific decisions: "
            + ", ".join(sorted(decided_root_ids))
        )
    is_final = decision["review_state"] == "final"
    if require_final and not is_final:
        raise ReviewError("review decision is still in progress")
    if is_final and proposal_decision_ids != proposal_finding_ids:
        raise ReviewError(
            "final compiled review must decide every scientific proposal issue exactly "
            f"once; missing={sorted(proposal_finding_ids - proposal_decision_ids)}, "
            f"extra={sorted(proposal_decision_ids - proposal_finding_ids)}"
        )
    if not is_final and not proposal_decision_ids.issubset(proposal_finding_ids):
        raise ReviewError("working compiled review decides a task-root proposal issue")

    issues = {item["issue_id"]: item for item in proposal["issues"]}
    for item in items:
        issue = issues.get(item["issue_id"])
        stage = item.get("decision_stage")
        if issue is None:
            if stage == "proposal":
                raise ReviewError(
                    f"gate decision {item['issue_id']} cannot use decision_stage=proposal"
                )
            if stage is None and not item["issue_id"].startswith(
                _LEGACY_GATE_PREFIXES
            ):
                raise ReviewError(
                    "legacy gate decisions without decision_stage must use the "
                    f"FACTCHECK- or SWEEP- prefix: {item['issue_id']}"
                )
        else:
            if stage not in {None, "proposal"}:
                raise ReviewError(
                    f"proposal issue {item['issue_id']} has a gate-stage decision"
                )
            if (
                item["task"] != issue["task"]
                and "cross_task" not in {item["task"], issue["task"]}
            ):
                raise ReviewError(
                    f"decision task does not match proposal issue {item['issue_id']}"
                )
            # A human may widen or narrow a finding between one task and
            # cross_task, or reclassify its affected target during adjudication.
            # In compiled-root reviews that judgment is embodied by the approved
            # documents rather than used to select an executable patch.
        if item["disposition"] == "modify" and item["target_kind"] not in {
            "groundtruth_record",
            "new_groundtruth_record",
        }:
            raise ReviewError(
                f"decision {item['issue_id']} cannot modify a non-ground-truth target"
            )

    if is_final:
        compiled_root_operation_ids(proposal, decision)
        approval = decision["scientific_approval"]
        if not approval["granted"]:
            raise ReviewError("final compiled review lacks scientific approval")
        if approval["granted_at"] != decision["review_completed_at"]:
            raise ReviewError(
                "scientific approval timestamp must match review completion"
            )
        dispositions = {item["disposition"] for item in items}
        dispositions.add("modify")  # complete root candidates approve deferred roots
        expected_overall = _overall_disposition(dispositions)
        if decision["overall_disposition"] != expected_overall:
            raise ReviewError(
                "overall_disposition is inconsistent with compiled review decisions; "
                f"expected {expected_overall!r}"
            )
    elif decision["overall_disposition"] != "in_progress":
        raise ReviewError(
            "an in-progress compiled review must use overall_disposition=in_progress"
        )
    if "review_started_at" in decision:
        _validate_review_time(decision)


def _overall_disposition(dispositions: set[str]) -> str:
    if dispositions <= {"accept", "modify"}:
        return "accepted"
    if dispositions == {"reject"}:
        return "rejected"
    if dispositions == {"exclude"}:
        return "excluded"
    if "unresolved" in dispositions and not dispositions.intersection(
        {"accept", "modify", "exclude"}
    ):
        return "unresolved"
    return "partially_accepted"


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
