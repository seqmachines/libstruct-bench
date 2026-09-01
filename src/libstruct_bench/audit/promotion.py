from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import (
    AuditArtifactError,
    load_json_object,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from .groundtruth import (
    TASK_ARTIFACTS,
    GroundtruthValidationError,
    validate_cross_task_links,
    validate_task_document,
)
from .review import (
    ReviewError,
    all_review_decision_items,
    compiled_root_operation_ids,
    is_compiled_review_decision,
    validate_review_decision,
)


class PromotionError(ValueError):
    """Raised when reviewed candidates are not eligible for promotion."""


@dataclass(frozen=True)
class PromotionResult:
    protocol_dir: Path
    log_path: Path
    artifact_paths: dict[str, Path]


def promote_reviewed_groundtruth(
    *,
    proposal_path: Path,
    decision_path: Path,
    application_log_path: Path,
    groundtruth_root: Path,
    promotion_log_path: Path,
    proposal_schema_path: Path,
    decision_schema_path: Path,
    application_schema_path: Path,
    promotion_schema_path: Path,
    regression_results_path: Path | None = None,
    regression_results_schema_path: Path | None = None,
    groundtruth_schema_dir: Path | None = None,
) -> PromotionResult:
    """Promote one final human-approved application without overwriting data."""

    try:
        proposal, decision = validate_review_decision(
            proposal_path=proposal_path,
            decision_path=decision_path,
            proposal_schema_path=proposal_schema_path,
            decision_schema_path=decision_schema_path,
            require_final=True,
        )
    except ReviewError as error:
        raise PromotionError(str(error)) from error
    review_items = all_review_decision_items(proposal, decision)
    unresolved = [
        item["issue_id"]
        for item in review_items
        if item["disposition"] == "unresolved"
    ]
    if unresolved:
        raise PromotionError(
            "unresolved issues block promotion: " + ", ".join(unresolved)
        )

    issues = {item["issue_id"]: item for item in proposal["issues"]}
    excluded_tasks: set[str] = set()
    for item in review_items:
        if item["disposition"] != "exclude":
            continue
        scope = item["exclusion_scope"]
        if scope == "protocol":
            raise PromotionError("the human decision excludes this protocol")
        if scope == "field":
            raise PromotionError(
                "field exclusion must be represented by a human-modified ground-truth patch"
            )
        issue = issues.get(item["issue_id"])
        task = issue["task"] if issue is not None else item["task"]
        if task == "cross_task":
            raise PromotionError("a cross-task issue cannot exclude one task implicitly")
        excluded_tasks.add(task)

    application_log_path = _file(application_log_path, "application log")
    application = load_json_object(application_log_path, label="application log")
    _validate(application, application_schema_path, "application log")
    if application["review_state"] != "final":
        raise PromotionError("working applications cannot be promoted")
    if application["audit_id"] != proposal["audit_id"]:
        raise PromotionError("application audit ID does not match proposal")
    if application["decision_id"] != decision["decision_id"]:
        raise PromotionError("application decision ID does not match review")
    if application["proposal_sha256"] != sha256_file(_file(proposal_path, "proposal")):
        raise PromotionError("application references a stale proposal")
    if application["decision_sha256"] != sha256_file(_file(decision_path, "decision")):
        raise PromotionError("application references a stale decision")

    if is_compiled_review_decision(decision):
        if application.get("application_mode") != "compiled_roots":
            raise PromotionError(
                "compiled-root review requires a compiled-root application"
            )
        try:
            expected_roots = set(
                compiled_root_operation_ids(proposal, decision).values()
            )
        except ReviewError as error:
            raise PromotionError(str(error)) from error
        if set(application["applied_issue_ids"]) != expected_roots:
            raise PromotionError(
                "compiled-root application does not cover every approved task root"
            )
        expected_decisions = {item["issue_id"] for item in review_items}
        if set(application.get("incorporated_decision_ids", [])) != expected_decisions:
            raise PromotionError(
                "compiled-root application omits reviewed gate or proposal decisions"
            )
    elif application.get("application_mode") == "compiled_roots":
        raise PromotionError(
            "patch review cannot be promoted from a compiled-root application"
        )

    applied = set(application["applied_issue_ids"])
    _verify_regressions(
        applied,
        regression_results_path,
        regression_results_schema_path,
    )
    schema_dir = groundtruth_schema_dir or (
        Path(__file__).resolve().parents[3] / "schemas" / "groundtruth"
    )
    task_by_filename = {
        details["filename"]: task for task, details in TASK_ARTIFACTS.items()
    }
    if is_compiled_review_decision(decision):
        application_tasks: dict[str, dict[str, Any]] = {}
        for artifact in application["artifacts"]:
            task = task_by_filename.get(Path(artifact["candidate_path"]).name)
            if task is None:
                raise PromotionError(
                    "compiled-root application contains a non-canonical artifact filename"
                )
            if task in application_tasks:
                raise PromotionError(
                    f"compiled-root application contains multiple {task} artifacts"
                )
            application_tasks[task] = artifact
        if set(application_tasks) != set(decision["candidate_sha256"]):
            raise PromotionError(
                "compiled-root application artifacts do not match approved candidate tasks"
            )
        for task, artifact in application_tasks.items():
            if artifact["candidate_sha256"] != decision["candidate_sha256"][task]:
                raise PromotionError(
                    f"compiled-root application does not contain the approved {task} bytes"
                )
    documents: dict[str, dict[str, Any]] = {}
    source_paths: dict[str, Path] = {}
    source_ids: dict[str, str] = {}
    for artifact in application["artifacts"]:
        task = task_by_filename.get(Path(artifact["candidate_path"]).name)
        if task is None or task in excluded_tasks:
            continue
        candidate_path = _resolve_application_path(
            application_log_path.parent, artifact["candidate_path"]
        )
        if sha256_file(candidate_path) != artifact["candidate_sha256"]:
            raise PromotionError(f"stale application candidate for {task}")
        document = load_json_object(candidate_path, label=f"{task} candidate")
        try:
            validate_task_document(
                task,
                document,
                protocol_id=proposal["protocol_id"],
                schema_dir=schema_dir,
            )
        except GroundtruthValidationError as error:
            raise PromotionError(str(error)) from error
        documents[task] = document
        source_paths[task] = candidate_path
        source_ids[task] = artifact["source_id"]
    try:
        validate_cross_task_links(documents)
    except GroundtruthValidationError as error:
        raise PromotionError(str(error)) from error
    if not documents and not excluded_tasks:
        raise PromotionError("application contains no promotable T1-T3 artifacts")

    groundtruth_root = groundtruth_root.expanduser().resolve()
    protocol_dir = groundtruth_root / proposal["protocol_id"]
    if protocol_dir.exists():
        raise PromotionError(f"approved ground truth already exists: {protocol_dir}")
    promotion_log_path = promotion_log_path.expanduser().resolve()
    if promotion_log_path.exists():
        raise PromotionError(f"promotion log already exists: {promotion_log_path}")
    if promotion_log_path == protocol_dir or promotion_log_path.is_relative_to(
        protocol_dir
    ):
        raise PromotionError("promotion log must be outside the approved protocol directory")
    groundtruth_root.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{proposal['protocol_id']}.promoting-", dir=groundtruth_root)
    )
    artifact_records: list[dict[str, str]] = []
    try:
        for task in sorted(documents):
            details = TASK_ARTIFACTS[task]
            destination = temporary_dir / details["filename"]
            shutil.copyfile(source_paths[task], destination)
            digest = sha256_file(destination)
            artifact_records.append(
                {
                    "task": task,
                    "source_id": source_ids[task],
                    "filename": details["filename"],
                    "sha256": digest,
                }
            )
        identity = hashlib.sha256(
            f"{sha256_file(application_log_path)}:{decision['decision_id']}".encode()
        ).hexdigest()
        log = {
            "promotion_id": f"{proposal['protocol_id']}:promotion:{identity[:16]}",
            "protocol_id": proposal["protocol_id"],
            "audit_id": proposal["audit_id"],
            "decision_id": decision["decision_id"],
            "application_id": application["application_id"],
            "proposal_sha256": sha256_file(_file(proposal_path, "proposal")),
            "decision_sha256": sha256_file(_file(decision_path, "decision")),
            "application_log_sha256": sha256_file(application_log_path),
            "regression_results_sha256": (
                sha256_file(regression_results_path)
                if regression_results_path is not None
                else None
            ),
            "created_at": decision["review_completed_at"],
            "target_directory": protocol_dir.as_posix(),
            "artifacts": artifact_records,
            "excluded_tasks": sorted(excluded_tasks),
        }
        _validate(log, promotion_schema_path, "promotion log")
        temporary_dir.rename(protocol_dir)
        try:
            write_json_atomic(promotion_log_path, log)
        except BaseException:
            shutil.rmtree(protocol_dir, ignore_errors=True)
            raise
    except BaseException:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise

    artifact_paths = {
        task: protocol_dir / TASK_ARTIFACTS[task]["filename"] for task in documents
    }
    return PromotionResult(protocol_dir, promotion_log_path, artifact_paths)


def _verify_regressions(
    applied: set[str], path: Path | None, schema_path: Path | None
) -> None:
    if not applied:
        return
    if path is None or schema_path is None:
        raise PromotionError("applied corrections require regression results")
    path = _file(path, "regression results")
    results = load_json_object(path, label="regression results")
    _validate(results, schema_path, "regression results")
    result_ids = {item["issue_id"] for item in results["results"]}
    if result_ids != applied:
        raise PromotionError("regression results do not cover applied corrections")
    if results["failed_count"] or results["new_regressions"]:
        raise PromotionError("regression failures block promotion")


def _resolve_application_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if path != root and not path.is_relative_to(root):
        raise PromotionError("application candidate path escapes its directory")
    return _file(path, "application candidate")


def _validate(document: dict[str, Any], schema_path: Path, label: str) -> None:
    try:
        validate_document(document, _file(schema_path, f"{label} schema"), label=label)
    except AuditArtifactError as error:
        raise PromotionError(str(error)) from error


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise PromotionError(f"{label} does not exist: {path}")
    return resolved
