from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .artifacts import (
    AuditArtifactError,
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from .connected_process_source_check import (
    ConnectedProcessSourceCheckError,
    validate_connected_process_source_check,
)
from .review import ReviewError, validate_review_decision
from libstruct_bench.libgen.validation import (
    LibgenValidationError,
    validate_groundtruth_bundle,
)


GROUNDTRUTH_FILES = {
    "T1": "groundtruth_final_lib_struct.json",
    "T2": "groundtruth_oligos.json",
    "T3": "groundtruth_library_generation_workflow.json",
}


class ConnectedProcessFinalApprovalError(ValueError):
    """Raised when a connected-process final approval is stale or incomplete."""


def record_connected_process_final_approval(
    *,
    preview_manifest_path: Path,
    source_check_path: Path,
    migration_plan_path: Path,
    approval_path: Path,
    audit_root: Path,
    schema_root: Path,
    reviewer: Mapping[str, Any],
    approved_at: str | None = None,
) -> dict[str, Any]:
    """Finalize reviewed decisions and record approval of one exact preview.

    Final protocol decisions are written as new immutable files; the working
    checkpoints used to compile the preview are never mutated. The batch
    approval is written last and explicitly leaves application and promotion
    unauthorized.
    """

    audit_root = _directory(audit_root, "audit root")
    schema_root = _directory(schema_root, "schema root")
    preview_manifest_path = _file(preview_manifest_path, "preview manifest")
    source_check_path = _file(source_check_path, "source check")
    migration_plan_path = _file(migration_plan_path, "migration plan")
    approval_path = approval_path.expanduser().resolve()
    if not approval_path.is_relative_to(audit_root):
        raise ConnectedProcessFinalApprovalError(
            "final approval must be written below the audit root"
        )
    if approval_path.exists():
        raise ConnectedProcessFinalApprovalError(
            f"final approval already exists: {approval_path}"
        )
    timestamp = normalize_timestamp(approved_at)
    preview = load_json_object(preview_manifest_path, label="preview manifest")
    plan = load_json_object(migration_plan_path, label="migration plan")
    _validate(
        preview,
        schema_root / "audit" / "connected_process_preview.schema.json",
        "preview manifest",
    )
    _validate(
        plan,
        schema_root / "audit" / "connected_process_migration_plan.schema.json",
        "migration plan",
    )
    try:
        source_check = validate_connected_process_source_check(
            report_path=source_check_path,
            preview_dir=preview_manifest_path.parent,
            audit_root=audit_root,
            schema_root=schema_root,
        )
    except ConnectedProcessSourceCheckError as error:
        raise ConnectedProcessFinalApprovalError(str(error)) from error
    if preview["plan_sha256"] != sha256_file(migration_plan_path):
        raise ConnectedProcessFinalApprovalError(
            "preview does not pin the supplied migration plan"
        )
    if source_check["preview_manifest"]["sha256"] != sha256_file(preview_manifest_path):
        raise ConnectedProcessFinalApprovalError(
            "source check does not pin the supplied preview"
        )
    if any(
        source_check["summary"][field]
        for field in ("conflict", "missing", "ambiguous", "unresolved")
    ):
        raise ConnectedProcessFinalApprovalError(
            "source check still contains unresolved candidate records or findings"
        )

    plan_protocols = _unique_by_id(
        plan["protocols"], "protocol_id", "migration-plan protocol"
    )
    preview_protocols = _unique_by_id(
        preview["protocols"], "protocol_id", "preview protocol"
    )
    if set(plan_protocols) != set(preview_protocols):
        raise ConnectedProcessFinalApprovalError(
            "preview and migration plan have different protocol sets"
        )

    created_decisions: list[Path] = []
    approval_protocols: list[dict[str, Any]] = []
    try:
        for protocol_id in [item["protocol_id"] for item in preview["protocols"]]:
            preview_protocol = preview_protocols[protocol_id]
            plan_protocol = plan_protocols[protocol_id]
            approval_protocol: dict[str, Any] = {
                "protocol_id": protocol_id,
                "artifacts": copy.deepcopy(preview_protocol["artifacts"]),
            }
            if preview_protocol["review_gate"] == "protocol":
                planned_review = plan_protocol.get("review")
                if planned_review is None:
                    raise ConnectedProcessFinalApprovalError(
                        f"{protocol_id} is missing its planned protocol review"
                    )
                proposal_path = _pinned_artifact(
                    audit_root,
                    planned_review["proposal"],
                    f"{protocol_id} proposal",
                )
                working_path = _pinned_artifact(
                    audit_root,
                    planned_review["decision"],
                    f"{protocol_id} working decision",
                )
                try:
                    _, working = validate_review_decision(
                        proposal_path=proposal_path,
                        decision_path=working_path,
                        proposal_schema_path=(
                            schema_root / "audit" / "protocol_audit.schema.json"
                        ),
                        decision_schema_path=(
                            schema_root / "audit" / "review_decision.schema.json"
                        ),
                        require_final=False,
                    )
                except ReviewError as error:
                    raise ConnectedProcessFinalApprovalError(str(error)) from error
                if working["review_state"] != "in_progress":
                    raise ConnectedProcessFinalApprovalError(
                        f"{protocol_id} preview decision is not in progress"
                    )
                if working["reviewer"] != reviewer:
                    raise ConnectedProcessFinalApprovalError(
                        f"{protocol_id} reviewer differs from final approval reviewer"
                    )
                final_path = working_path.with_name("decision-final.json")
                if final_path.exists():
                    raise ConnectedProcessFinalApprovalError(
                        f"{protocol_id} final decision already exists: {final_path}"
                    )
                final = copy.deepcopy(working)
                final["decision_id"] = f"{working['decision_id']}:final"
                final["review_state"] = "final"
                final["overall_disposition"] = "accepted"
                final["review_completed_at"] = timestamp
                note = final.get("review_notes") or ""
                if note and not note.endswith(" "):
                    note += " "
                final["review_notes"] = (
                    note
                    + "Final scientific approval applies to preview manifest SHA-256 "
                    + sha256_file(preview_manifest_path)
                    + " and source-check SHA-256 "
                    + sha256_file(source_check_path)
                    + ". Application and promotion remain unauthorized."
                )
                write_json_atomic(final_path, final)
                created_decisions.append(final_path)
                try:
                    validate_review_decision(
                        proposal_path=proposal_path,
                        decision_path=final_path,
                        proposal_schema_path=(
                            schema_root / "audit" / "protocol_audit.schema.json"
                        ),
                        decision_schema_path=(
                            schema_root / "audit" / "review_decision.schema.json"
                        ),
                        require_final=True,
                    )
                except ReviewError as error:
                    raise ConnectedProcessFinalApprovalError(str(error)) from error
                approval_protocol["review"] = {
                    "proposal": copy.deepcopy(planned_review["proposal"]),
                    "working_decision": copy.deepcopy(planned_review["decision"]),
                    "final_decision": {
                        "path": _relative_path(final_path, audit_root),
                        "sha256": sha256_file(final_path),
                    },
                }
            approval_protocols.append(approval_protocol)

        approval = {
            "approval_id": f"{preview['preview_id']}:final-approval",
            "migration_id": preview["migration_id"],
            "approved_at": timestamp,
            "status": "final",
            "scientific_disposition": "approved",
            "reviewer": dict(reviewer),
            "preview_manifest": {
                "path": _relative_path(preview_manifest_path, audit_root),
                "sha256": sha256_file(preview_manifest_path),
            },
            "source_check": {
                "path": _relative_path(source_check_path, audit_root),
                "sha256": sha256_file(source_check_path),
            },
            "migration_plan": {
                "path": _relative_path(migration_plan_path, audit_root),
                "sha256": sha256_file(migration_plan_path),
            },
            "policy_proposal": copy.deepcopy(plan["policy_proposal"]),
            "preview_policy_decision": copy.deepcopy(plan["policy_decision"]),
            "approved_structure": copy.deepcopy(preview["observed_structure"]),
            "protocols": approval_protocols,
            "application_authorized": False,
            "promotion_authorized": False,
            "canonical_ground_truth_modified": False,
            "scg_upload_modified": False,
            "notes": (
                "The human approved this exact hash-pinned preview after the "
                "record-level primary-source check. This finalizes scientific "
                "review only; deterministic application and promotion require "
                "a separate explicit authorization."
            ),
        }
        write_json_atomic(approval_path, approval)
        validate_connected_process_final_approval(
            approval_path=approval_path,
            audit_root=audit_root,
            schema_root=schema_root,
        )
    except BaseException:
        approval_path.unlink(missing_ok=True)
        for path in created_decisions:
            path.unlink(missing_ok=True)
        raise
    return approval


def validate_connected_process_final_approval(
    *,
    approval_path: Path,
    audit_root: Path,
    schema_root: Path,
) -> dict[str, Any]:
    """Validate a scientific approval without authorizing application.

    The approval pins an immutable preview, its record-level source check, the
    preview inputs, and finalized protocol decisions. Candidate T1--T3 bundles
    are revalidated, while the schema requires every write/application flag to
    remain false.
    """

    approval_path = _file(approval_path, "connected-process final approval")
    audit_root = _directory(audit_root, "audit root")
    schema_root = _directory(schema_root, "schema root")
    approval = load_json_object(approval_path, label="connected-process final approval")
    _validate(
        approval,
        schema_root / "audit" / "connected_process_final_approval.schema.json",
        "connected-process final approval",
    )

    preview_manifest_path = _pinned_artifact(
        audit_root, approval["preview_manifest"], "approved preview manifest"
    )
    source_check_path = _pinned_artifact(
        audit_root, approval["source_check"], "approved source check"
    )
    plan_path = _pinned_artifact(
        audit_root, approval["migration_plan"], "approved migration plan"
    )
    policy_proposal_path = _pinned_artifact(
        audit_root, approval["policy_proposal"], "approved policy proposal"
    )
    policy_decision_path = _pinned_artifact(
        audit_root,
        approval["preview_policy_decision"],
        "preview policy decision",
    )

    preview = load_json_object(preview_manifest_path, label="approved preview")
    plan = load_json_object(plan_path, label="approved migration plan")
    policy_proposal = load_json_object(
        policy_proposal_path, label="approved policy proposal"
    )
    policy_decision = load_json_object(
        policy_decision_path, label="preview policy decision"
    )
    _validate(
        preview,
        schema_root / "audit" / "connected_process_preview.schema.json",
        "approved preview",
    )
    _validate(
        plan,
        schema_root / "audit" / "connected_process_migration_plan.schema.json",
        "approved migration plan",
    )
    _validate(
        policy_proposal,
        schema_root / "audit" / "connected_process_policy_proposal.schema.json",
        "approved policy proposal",
    )
    _validate(
        policy_decision,
        schema_root / "audit" / "connected_process_policy_decision.schema.json",
        "preview policy decision",
    )

    preview_dir = preview_manifest_path.parent
    try:
        source_check = validate_connected_process_source_check(
            report_path=source_check_path,
            preview_dir=preview_dir,
            audit_root=audit_root,
            schema_root=schema_root,
        )
    except ConnectedProcessSourceCheckError as error:
        raise ConnectedProcessFinalApprovalError(str(error)) from error

    migration_id = approval["migration_id"]
    if {
        preview["migration_id"],
        plan["migration_id"],
        source_check["migration_id"],
    } != {migration_id}:
        raise ConnectedProcessFinalApprovalError(
            "final approval inputs use different migration IDs"
        )
    if preview["plan_sha256"] != sha256_file(plan_path):
        raise ConnectedProcessFinalApprovalError(
            "approved preview references a different migration plan"
        )
    if preview["policy_proposal_sha256"] != sha256_file(policy_proposal_path):
        raise ConnectedProcessFinalApprovalError(
            "approved preview references a different policy proposal"
        )
    if preview["policy_decision_sha256"] != sha256_file(policy_decision_path):
        raise ConnectedProcessFinalApprovalError(
            "approved preview references a different preview policy decision"
        )
    if plan["policy_proposal"] != approval["policy_proposal"]:
        raise ConnectedProcessFinalApprovalError(
            "approved plan and final approval pin different policy proposals"
        )
    if plan["policy_decision"] != approval["preview_policy_decision"]:
        raise ConnectedProcessFinalApprovalError(
            "approved plan and final approval pin different policy decisions"
        )
    if policy_decision["proposal_sha256"] != sha256_file(policy_proposal_path):
        raise ConnectedProcessFinalApprovalError(
            "preview policy decision references a stale proposal"
        )
    if policy_decision["final_scientific_approval"] != "pending":
        raise ConnectedProcessFinalApprovalError(
            "preview policy decision must remain the immutable preview-stage decision"
        )
    if policy_decision["application_authorized"]:
        raise ConnectedProcessFinalApprovalError(
            "preview policy decision unexpectedly authorizes application"
        )

    if approval["approved_structure"] != preview["observed_structure"]:
        raise ConnectedProcessFinalApprovalError(
            "approved structure differs from the validated preview"
        )
    if preview["expected_structure"] != preview["observed_structure"]:
        raise ConnectedProcessFinalApprovalError(
            "approved preview does not meet its expected structure"
        )
    if any(
        source_check["summary"][field]
        for field in ("conflict", "missing", "ambiguous", "unresolved")
    ):
        raise ConnectedProcessFinalApprovalError(
            "source check still contains unresolved candidate records or findings"
        )

    approval_protocols = _unique_by_id(
        approval["protocols"], "protocol_id", "final approval protocol"
    )
    preview_protocols = _unique_by_id(
        preview["protocols"], "protocol_id", "preview protocol"
    )
    plan_protocols = _unique_by_id(
        plan["protocols"], "protocol_id", "migration-plan protocol"
    )
    if set(approval_protocols) != set(preview_protocols) or set(
        approval_protocols
    ) != set(plan_protocols):
        raise ConnectedProcessFinalApprovalError(
            "final approval, preview, and migration plan have different protocol sets"
        )
    source_checked = {item["protocol_id"] for item in source_check["protocols"]}
    review_gated = {
        protocol_id
        for protocol_id, record in preview_protocols.items()
        if record["review_gate"] == "protocol"
    }
    if not review_gated.issubset(source_checked):
        raise ConnectedProcessFinalApprovalError(
            "source check does not cover every protocol-review gate"
        )

    for protocol_id, protocol in approval_protocols.items():
        preview_protocol = preview_protocols[protocol_id]
        plan_protocol = plan_protocols[protocol_id]
        if protocol["artifacts"] != preview_protocol["artifacts"]:
            raise ConnectedProcessFinalApprovalError(
                f"{protocol_id} approved artifacts differ from the preview manifest"
            )
        artifact_names = [item["filename"] for item in protocol["artifacts"]]
        if set(artifact_names) != set(GROUNDTRUTH_FILES.values()) or len(
            artifact_names
        ) != len(set(artifact_names)):
            raise ConnectedProcessFinalApprovalError(
                f"{protocol_id} must approve exactly one T1, T2, and T3 artifact"
            )
        plan_baselines = {
            item["filename"]: item["sha256"]
            for item in plan_protocol["baseline_artifacts"]
        }
        candidate_documents: dict[str, dict[str, Any]] = {}
        for task, filename in GROUNDTRUTH_FILES.items():
            artifact = next(
                item for item in protocol["artifacts"] if item["filename"] == filename
            )
            if artifact["baseline_sha256"] != plan_baselines[filename]:
                raise ConnectedProcessFinalApprovalError(
                    f"{protocol_id} {filename} baseline hash differs from the plan"
                )
            candidate_path = _file(
                preview_dir / "groundtruth" / protocol_id / filename,
                f"{protocol_id} approved {task} candidate",
            )
            if sha256_file(candidate_path) != artifact["candidate_sha256"]:
                raise ConnectedProcessFinalApprovalError(
                    f"{protocol_id} {filename} candidate hash is stale"
                )
            candidate_documents[task] = load_json_object(
                candidate_path, label=f"{protocol_id} approved {task} candidate"
            )
        try:
            validate_groundtruth_bundle(
                candidate_documents,
                protocol_id=protocol_id,
                schema_root=schema_root,
            )
        except LibgenValidationError as error:
            raise ConnectedProcessFinalApprovalError(
                f"{protocol_id} approved candidate failed linked validation: {error}"
            ) from error

        review = protocol.get("review")
        if protocol_id in review_gated:
            if review is None:
                raise ConnectedProcessFinalApprovalError(
                    f"{protocol_id} is missing its finalized protocol review"
                )
            _validate_protocol_review(
                protocol_id=protocol_id,
                review=review,
                preview_protocol=preview_protocol,
                plan_protocol=plan_protocol,
                approval=approval,
                audit_root=audit_root,
                schema_root=schema_root,
            )
        elif review is not None:
            raise ConnectedProcessFinalApprovalError(
                f"{protocol_id} unexpectedly includes a protocol review"
            )

    return approval


def _validate_protocol_review(
    *,
    protocol_id: str,
    review: Mapping[str, Any],
    preview_protocol: Mapping[str, Any],
    plan_protocol: Mapping[str, Any],
    approval: Mapping[str, Any],
    audit_root: Path,
    schema_root: Path,
) -> None:
    proposal_path = _pinned_artifact(
        audit_root, review["proposal"], f"{protocol_id} proposal"
    )
    working_path = _pinned_artifact(
        audit_root, review["working_decision"], f"{protocol_id} working decision"
    )
    final_path = _pinned_artifact(
        audit_root, review["final_decision"], f"{protocol_id} final decision"
    )
    planned_review = plan_protocol.get("review")
    if planned_review is None:
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} review is absent from the migration plan"
        )
    if review["proposal"] != planned_review["proposal"]:
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} final approval pins a different proposal"
        )
    if review["working_decision"] != planned_review["decision"]:
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} final approval pins a different working decision"
        )
    if sha256_file(proposal_path) != preview_protocol["proposal_sha256"]:
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} proposal differs from the approved preview"
        )
    if sha256_file(working_path) != preview_protocol["decision_sha256"]:
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} working decision differs from the approved preview"
        )
    try:
        _, working = validate_review_decision(
            proposal_path=proposal_path,
            decision_path=working_path,
            proposal_schema_path=schema_root / "audit" / "protocol_audit.schema.json",
            decision_schema_path=schema_root / "audit" / "review_decision.schema.json",
            require_final=False,
        )
        _, final = validate_review_decision(
            proposal_path=proposal_path,
            decision_path=final_path,
            proposal_schema_path=schema_root / "audit" / "protocol_audit.schema.json",
            decision_schema_path=schema_root / "audit" / "review_decision.schema.json",
            require_final=True,
        )
    except ReviewError as error:
        raise ConnectedProcessFinalApprovalError(str(error)) from error
    _validate_finalized_decision_derivation(
        protocol_id=protocol_id,
        working=working,
        final=final,
        reviewer=approval["reviewer"],
        preview_sha256=approval["preview_manifest"]["sha256"],
        source_check_sha256=approval["source_check"]["sha256"],
    )


def _validate_finalized_decision_derivation(
    *,
    protocol_id: str,
    working: Mapping[str, Any],
    final: Mapping[str, Any],
    reviewer: Mapping[str, Any],
    preview_sha256: str,
    source_check_sha256: str,
) -> None:
    if working["review_state"] != "in_progress":
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} preview decision is not an in-progress checkpoint"
        )
    if final["review_state"] != "final" or final["overall_disposition"] != "accepted":
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} final decision is not scientifically accepted"
        )
    if final["decision_id"] != f"{working['decision_id']}:final":
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} final decision ID does not derive from the preview decision"
        )
    immutable_fields = (
        "protocol_id",
        "audit_id",
        "proposal_sha256",
        "baseline_artifacts",
        "reviewer",
        "iteration",
        "review_started_at",
        "issue_decisions",
    )
    changed = [field for field in immutable_fields if final[field] != working[field]]
    if changed:
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} finalized decision changed reviewed content: "
            + ", ".join(changed)
        )
    if final["reviewer"] != reviewer:
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} final reviewer differs from the batch approval reviewer"
        )
    try:
        working_completed = datetime.fromisoformat(
            working["review_completed_at"].replace("Z", "+00:00")
        )
        final_completed = datetime.fromisoformat(
            final["review_completed_at"].replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} has invalid review timestamps"
        ) from error
    if final_completed < working_completed:
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} finalization precedes its working decision"
        )
    notes = final.get("review_notes")
    if not isinstance(notes, str) or not all(
        value in notes for value in (preview_sha256, source_check_sha256)
    ):
        raise ConnectedProcessFinalApprovalError(
            f"{protocol_id} final decision does not identify the approved preview "
            "and source check"
        )


def _unique_by_id(
    values: list[dict[str, Any]], key: str, label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        identifier = value[key]
        if identifier in result:
            raise ConnectedProcessFinalApprovalError(
                f"duplicate {label} ID: {identifier}"
            )
        result[identifier] = value
    return result


def _pinned_artifact(root: Path, reference: Mapping[str, str], label: str) -> Path:
    relative = PurePosixPath(reference["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ConnectedProcessFinalApprovalError(
            f"{label} path must stay below the audit root"
        )
    path = _file(root.joinpath(*relative.parts), label)
    if sha256_file(path) != reference["sha256"]:
        raise ConnectedProcessFinalApprovalError(f"{label} hash is stale")
    return path


def _validate(value: dict[str, Any], schema_path: Path, label: str) -> None:
    try:
        validate_document(value, schema_path, label=label)
    except AuditArtifactError as error:
        raise ConnectedProcessFinalApprovalError(str(error)) from error


def _file(path: Path, label: str) -> Path:
    value = path.expanduser().resolve()
    if not value.is_file():
        raise ConnectedProcessFinalApprovalError(f"{label} does not exist: {value}")
    return value


def _directory(path: Path, label: str) -> Path:
    value = path.expanduser().resolve()
    if not value.is_dir():
        raise ConnectedProcessFinalApprovalError(f"{label} does not exist: {value}")
    return value


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ConnectedProcessFinalApprovalError(
            f"artifact path must stay below the audit root: {path}"
        ) from error
