from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .artifacts import (
    AuditArtifactError,
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from .review import ReviewError, validate_review_decision


class ReportingError(ValueError):
    """Raised when checkpoint metrics cannot be reproduced from reviewed artifacts."""


def build_checkpoint_report(
    *,
    checkpoint_id: str,
    reviewed_protocol_count: int,
    proposal_paths: Iterable[Path],
    decision_paths: Iterable[Path],
    output_path: Path,
    proposal_schema_path: Path,
    decision_schema_path: Path,
    report_schema_path: Path,
    application_schema_path: Path | None = None,
    regression_results_schema_path: Path | None = None,
    application_log_paths: Iterable[Path] = (),
    regression_results_path: Path | None = None,
    previous_checkpoint_path: Path | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    proposals = [_file(path, "audit proposal") for path in proposal_paths]
    decisions = [_file(path, "review decision") for path in decision_paths]
    applications = [_file(path, "application log") for path in application_log_paths]
    schema_root = Path(__file__).resolve().parents[3] / "schemas" / "audit"
    application_schema_path = _file(
        application_schema_path
        or schema_root / "application_log.schema.json",
        "application log schema",
    )
    regression_results_schema_path = _file(
        regression_results_schema_path
        or schema_root / "regression_results.schema.json",
        "regression results schema",
    )
    if reviewed_protocol_count < 0:
        raise ReportingError("reviewed protocol count must be non-negative")
    if len(proposals) != len(decisions):
        raise ReportingError("checkpoint requires one decision for every proposal")

    proposal_by_audit: dict[str, Path] = {}
    for path in proposals:
        value = load_json_object(path, label="audit proposal")
        audit_id = value.get("audit_id")
        if not isinstance(audit_id, str) or audit_id in proposal_by_audit:
            raise ReportingError(f"duplicate or invalid audit ID in {path}")
        proposal_by_audit[audit_id] = path
    decision_by_audit: dict[str, Path] = {}
    for path in decisions:
        value = load_json_object(path, label="review decision")
        audit_id = value.get("audit_id")
        if not isinstance(audit_id, str) or audit_id in decision_by_audit:
            raise ReportingError(f"duplicate or invalid decision audit ID in {path}")
        decision_by_audit[audit_id] = path
    if set(proposal_by_audit) != set(decision_by_audit):
        raise ReportingError("proposal and decision audit IDs do not match")

    audited_fields: set[tuple[str, str]] = set()
    confirmed_fields: set[tuple[str, str]] = set()
    categories: dict[str, int] = {}
    responsibilities: dict[str, int] = {}
    severities: dict[str, int] = {}
    tasks: dict[str, int] = {}
    confirmed_causes: dict[str, int] = {}
    proposed_issue_count = 0
    confirmed_issue_count = 0
    review_seconds = 0.0
    protocol_rows: list[dict[str, Any]] = []

    for audit_id in sorted(proposal_by_audit):
        try:
            proposal, decision = validate_review_decision(
                proposal_path=proposal_by_audit[audit_id],
                decision_path=decision_by_audit[audit_id],
                proposal_schema_path=proposal_schema_path,
                decision_schema_path=decision_schema_path,
            )
        except ReviewError as error:
            raise ReportingError(str(error)) from error
        protocol_id = proposal["protocol_id"]
        protocol_fields = {
            (protocol_id, field["field_id"]) for field in proposal["audited_fields"]
        }
        audited_fields.update(protocol_fields)
        proposed_issue_count += len(proposal["issues"])
        issues = {item["issue_id"]: item for item in proposal["issues"]}
        confirmed_for_protocol: set[tuple[str, str]] = set()
        confirmed_count = 0
        for item in decision["issue_decisions"]:
            cause = item.get("confirmed_cause")
            if cause is not None:
                _increment(confirmed_causes, cause)
            if item["disposition"] not in {"accept", "modify"}:
                continue
            issue = issues[item["issue_id"]]
            confirmed_count += 1
            confirmed_issue_count += 1
            field_key = (protocol_id, issue["field_id"])
            confirmed_fields.add(field_key)
            confirmed_for_protocol.add(field_key)
            category = item.get("category", issue["category"])
            responsibility = item.get("responsibility", issue["responsibility"])
            severity = item.get("severity", issue["severity"])
            _increment(categories, category)
            _increment(responsibilities, responsibility)
            _increment(severities, severity)
            _increment(tasks, issue["task"])
        seconds = _review_seconds(decision)
        review_seconds += seconds
        protocol_rows.append(
            {
                "protocol_id": protocol_id,
                "audit_id": audit_id,
                "decision_id": decision["decision_id"],
                "audited_field_count": len(protocol_fields),
                "confirmed_error_field_count": len(confirmed_for_protocol),
                "confirmed_issue_count": confirmed_count,
                "review_seconds": seconds,
            }
        )

    unique_protocols = {row["protocol_id"] for row in protocol_rows}
    if len(unique_protocols) != reviewed_protocol_count:
        raise ReportingError(
            f"reviewed_protocol_count={reviewed_protocol_count} but artifacts contain "
            f"{len(unique_protocols)} unique protocols"
        )
    human_count = confirmed_causes.get("original_human_curation_error", 0)
    agent_count = sum(
        confirmed_causes.get(key, 0)
        for key in (
            "audit_agent_reasoning_error",
            "agent_harness_or_context_error",
            "pdf_table_or_figure_extraction_error",
        )
    )
    confirmed_cause_count = sum(confirmed_causes.values())
    other_count = confirmed_cause_count - human_count - agent_count
    human_agent_total = human_count + agent_count
    applied_issue_ids: set[str] = set()
    for path in applications:
        application = load_json_object(path, label="application log")
        try:
            validate_document(
                application,
                application_schema_path,
                label="application log",
            )
        except AuditArtifactError as error:
            raise ReportingError(str(error)) from error
        overlap = applied_issue_ids.intersection(application["applied_issue_ids"])
        if overlap:
            raise ReportingError(
                "checkpoint application logs duplicate applied issue IDs: "
                + ", ".join(sorted(overlap))
            )
        applied_issue_ids.update(application["applied_issue_ids"])
    regressions, regression_artifact = _regressions(
        regression_results_path,
        regression_results_schema_path,
        applied_issue_ids,
    )
    previous_checkpoint: dict[str, Any] | None = None
    previous_path: Path | None = None
    if previous_checkpoint_path is not None:
        previous_path = _file(previous_checkpoint_path, "previous checkpoint")
        previous_checkpoint = load_json_object(
            previous_path, label="previous checkpoint"
        )
        try:
            validate_document(
                previous_checkpoint,
                _file(report_schema_path, "checkpoint schema"),
                label="previous checkpoint",
            )
        except AuditArtifactError as error:
            raise ReportingError(str(error)) from error
        if previous_checkpoint["reviewed_protocol_count"] >= reviewed_protocol_count:
            raise ReportingError(
                "previous checkpoint must have a smaller reviewed protocol count"
            )
    report = {
        "checkpoint_id": checkpoint_id,
        "created_at": _timestamp(created_at),
        "reviewed_protocol_count": reviewed_protocol_count,
        "proposal_artifacts": [_artifact(path) for path in sorted(proposals)],
        "decision_artifacts": [_artifact(path) for path in sorted(decisions)],
        "application_artifacts": [_artifact(path) for path in sorted(applications)],
        "regression_results_artifact": regression_artifact,
        "metrics": {
            "audited_field_count": len(audited_fields),
            "confirmed_error_field_count": len(confirmed_fields),
            "confirmed_error_rate": (
                len(confirmed_fields) / len(audited_fields) if audited_fields else 0.0
            ),
            "proposed_issue_count": proposed_issue_count,
            "confirmed_issue_count": confirmed_issue_count,
            "category_distribution": dict(sorted(categories.items())),
            "confirmed_cause_distribution": dict(sorted(confirmed_causes.items())),
            "responsibility_distribution": dict(sorted(responsibilities.items())),
            "severity_distribution": dict(sorted(severities.items())),
            "task_distribution": dict(sorted(tasks.items())),
            "human_error_count": human_count,
            "agent_error_count": agent_count,
            "other_error_count": other_count,
            "human_error_proportion": human_count / human_agent_total if human_agent_total else 0.0,
            "agent_error_proportion": agent_count / human_agent_total if human_agent_total else 0.0,
            "human_review_seconds": review_seconds,
            "new_regression_count": len(regressions) if regressions is not None else None,
        },
        "protocols": sorted(protocol_rows, key=lambda row: row["protocol_id"]),
        "new_regressions": regressions,
        "previous_checkpoint": _artifact(previous_path) if previous_path else None,
        "deltas": None,
    }
    if previous_checkpoint is not None:
        previous_metrics = previous_checkpoint["metrics"]
        report["deltas"] = {
            "reviewed_protocol_count": reviewed_protocol_count
            - previous_checkpoint["reviewed_protocol_count"],
            "audited_field_count": len(audited_fields)
            - previous_metrics["audited_field_count"],
            "confirmed_error_field_count": len(confirmed_fields)
            - previous_metrics["confirmed_error_field_count"],
            "confirmed_issue_count": confirmed_issue_count
            - previous_metrics["confirmed_issue_count"],
            "human_error_count": human_count - previous_metrics["human_error_count"],
            "agent_error_count": agent_count - previous_metrics["agent_error_count"],
            "human_review_seconds": review_seconds
            - previous_metrics["human_review_seconds"],
        }
    try:
        validate_document(report, _file(report_schema_path, "checkpoint schema"), label="checkpoint report")
    except AuditArtifactError as error:
        raise ReportingError(str(error)) from error
    output_path = output_path.expanduser().resolve()
    _reject_output(output_path)
    if output_path.exists():
        raise ReportingError(f"checkpoint output already exists: {output_path}")
    write_json_atomic(output_path, report)
    return report


def _review_seconds(decision: dict[str, Any]) -> float:
    if "review_duration_seconds" in decision:
        return float(decision["review_duration_seconds"])
    started = datetime.fromisoformat(decision["review_started_at"].replace("Z", "+00:00"))
    completed = datetime.fromisoformat(decision["review_completed_at"].replace("Z", "+00:00"))
    return (completed - started).total_seconds()


def _regressions(
    path: Path | None,
    schema_path: Path,
    expected_issue_ids: set[str],
) -> tuple[list[str] | None, dict[str, str] | None]:
    if path is None:
        if expected_issue_ids:
            raise ReportingError(
                "application logs contain corrections but regression results are missing"
            )
        return None, None
    resolved = _file(path, "regression results")
    document = load_json_object(resolved, label="regression results")
    try:
        validate_document(document, schema_path, label="regression results")
    except AuditArtifactError as error:
        raise ReportingError(str(error)) from error
    result_ids = [item["issue_id"] for item in document["results"]]
    if len(result_ids) != len(set(result_ids)):
        raise ReportingError("regression results contain duplicate issue IDs")
    if set(result_ids) != expected_issue_ids:
        raise ReportingError(
            "regression results do not exactly cover applied corrections; "
            f"expected={sorted(expected_issue_ids)}, got={sorted(result_ids)}"
        )
    failed = sorted(
        item["issue_id"]
        for item in document["results"]
        if item["status"] == "failed"
    )
    if (
        document["fixture_count"] != len(document["results"])
        or document["passed_count"] != len(document["results"]) - len(failed)
        or document["failed_count"] != len(failed)
        or sorted(document["new_regressions"]) != failed
    ):
        raise ReportingError("regression result summary is internally inconsistent")
    return failed, _artifact(resolved)


def _artifact(path: Path) -> dict[str, str]:
    return {"path": path.as_posix(), "sha256": sha256_file(path)}


def _increment(values: dict[str, int], key: str) -> None:
    values[key] = values.get(key, 0) + 1


def _timestamp(value: str | None) -> str:
    try:
        return normalize_timestamp(value)
    except AuditArtifactError as error:
        raise ReportingError(str(error)) from error


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ReportingError(f"{label} does not exist: {path}")
    return resolved


def _reject_output(path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    if (repo / ".git").exists() and (path == repo or path.is_relative_to(repo)):
        raise ReportingError("private checkpoint output must not be written inside libstruct-bench")
