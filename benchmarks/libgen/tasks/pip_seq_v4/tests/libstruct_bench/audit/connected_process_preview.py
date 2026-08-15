from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .artifacts import (
    AuditArtifactError,
    canonical_json_bytes,
    load_json_object,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from .connected_process import (
    SPECIAL_MIGRATIONS,
    ConnectedProcessMigrationError,
    connected_process_counts,
    migrate_connected_process_bundle,
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
REVIEW_GATED_PROTOCOLS = SPECIAL_MIGRATIONS | {"smart_seq"}


class ConnectedProcessPreviewError(ValueError):
    """Raised when a pinned connected-process preview cannot be compiled."""


@dataclass(frozen=True)
class ConnectedProcessPreviewResult:
    output_dir: Path
    manifest_path: Path
    observed_structure: dict[str, int]


def compile_connected_process_preview(
    *,
    plan_path: Path,
    audit_root: Path,
    baseline_root: Path,
    output_dir: Path,
    schema_root: Path,
) -> ConnectedProcessPreviewResult:
    """Compile a non-promotable, hash-pinned preview for all planned protocols."""

    schema_root = _directory(schema_root, "schema root")
    audit_root = _directory(audit_root, "audit root")
    baseline_root = _directory(baseline_root, "baseline ground-truth root")
    plan_path = _file(plan_path, "connected-process migration plan")
    output_dir = output_dir.expanduser().resolve()
    _reject_overlapping_output(output_dir, baseline_root, audit_root)
    if output_dir.exists():
        raise ConnectedProcessPreviewError(
            f"connected-process preview output already exists: {output_dir}"
        )

    plan = load_json_object(plan_path, label="connected-process migration plan")
    _validate(
        plan,
        schema_root / "audit" / "connected_process_migration_plan.schema.json",
        "connected-process migration plan",
    )
    plan_sha = sha256_file(plan_path)
    policy_proposal_path = _pinned_artifact(
        audit_root, plan["policy_proposal"], "connected-process policy proposal"
    )
    policy_decision_path = _pinned_artifact(
        audit_root, plan["policy_decision"], "connected-process policy decision"
    )
    policy_proposal = load_json_object(
        policy_proposal_path, label="connected-process policy proposal"
    )
    policy_decision = load_json_object(
        policy_decision_path, label="connected-process policy decision"
    )
    _validate(
        policy_proposal,
        schema_root / "audit" / "connected_process_policy_proposal.schema.json",
        "connected-process policy proposal",
    )
    _validate(
        policy_decision,
        schema_root / "audit" / "connected_process_policy_decision.schema.json",
        "connected-process policy decision",
    )
    if policy_decision["proposal_id"] != policy_proposal["proposal_id"]:
        raise ConnectedProcessPreviewError(
            "connected-process policy decision targets a different proposal"
        )
    if policy_decision["proposal_sha256"] != sha256_file(policy_proposal_path):
        raise ConnectedProcessPreviewError(
            "connected-process policy decision has a stale proposal hash"
        )

    protocol_entries = plan["protocols"]
    protocol_ids = [entry["protocol_id"] for entry in protocol_entries]
    if len(protocol_ids) != len(set(protocol_ids)):
        raise ConnectedProcessPreviewError(
            "connected-process migration plan contains duplicate protocol IDs"
        )
    if plan["expected_structure"]["protocols"] != len(protocol_ids):
        raise ConnectedProcessPreviewError(
            "planned protocol count does not equal expected_structure.protocols"
        )
    reviewed_protocols = {
        entry["protocol_id"] for entry in protocol_entries if "review" in entry
    }
    missing_review = sorted(
        (REVIEW_GATED_PROTOCOLS & set(protocol_ids)) - reviewed_protocols
    )
    if missing_review:
        raise ConnectedProcessPreviewError(
            "protocol-specific review is required for: " + ", ".join(missing_review)
        )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    migrated_documents: list[dict[str, Any]] = []
    protocol_records: list[dict[str, Any]] = []
    try:
        candidate_root = temporary / "groundtruth"
        for entry in protocol_entries:
            record, migrated = _compile_protocol(
                entry=entry,
                audit_root=audit_root,
                baseline_root=baseline_root,
                candidate_root=candidate_root,
                schema_root=schema_root,
            )
            protocol_records.append(record)
            migrated_documents.append(migrated)

        observed = connected_process_counts(migrated_documents)
        if observed != plan["expected_structure"]:
            raise ConnectedProcessPreviewError(
                "connected-process preview structure differs from the approved plan: "
                f"expected {plan['expected_structure']}, found {observed}"
            )
        identity_payload = {
            "plan_sha256": plan_sha,
            "policy_proposal_sha256": sha256_file(policy_proposal_path),
            "policy_decision_sha256": sha256_file(policy_decision_path),
            "protocols": protocol_records,
        }
        identity = hashlib.sha256(canonical_json_bytes(identity_payload)).hexdigest()
        manifest = {
            "preview_id": f"{plan['migration_id']}:preview:{identity[:16]}",
            "migration_id": plan["migration_id"],
            "created_at": plan["created_at"],
            "status": "preview_only",
            "benchmark_version_from": plan["benchmark_version_from"],
            "benchmark_version_to": plan["benchmark_version_to"],
            "plan_sha256": plan_sha,
            "policy_proposal_sha256": sha256_file(policy_proposal_path),
            "policy_decision_sha256": sha256_file(policy_decision_path),
            "expected_structure": plan["expected_structure"],
            "observed_structure": observed,
            "protocols": protocol_records,
            "canonical_ground_truth_modified": False,
        }
        _validate(
            manifest,
            schema_root / "audit" / "connected_process_preview.schema.json",
            "connected-process preview manifest",
        )
        write_json_atomic(temporary / "preview-manifest.json", manifest)
        temporary.replace(output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    return ConnectedProcessPreviewResult(
        output_dir=output_dir,
        manifest_path=output_dir / "preview-manifest.json",
        observed_structure=observed,
    )


def _compile_protocol(
    *,
    entry: Mapping[str, Any],
    audit_root: Path,
    baseline_root: Path,
    candidate_root: Path,
    schema_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol_id = entry["protocol_id"]
    expected_hashes = {
        artifact["filename"]: artifact["sha256"]
        for artifact in entry["baseline_artifacts"]
    }
    if set(expected_hashes) != set(GROUNDTRUTH_FILES.values()):
        raise ConnectedProcessPreviewError(
            f"{protocol_id} must pin exactly the three T1-T3 baseline files"
        )
    baseline_dir = baseline_root / protocol_id
    paths = {
        task: _file(baseline_dir / filename, f"{protocol_id} {task} baseline")
        for task, filename in GROUNDTRUTH_FILES.items()
    }
    for path in paths.values():
        expected = expected_hashes[path.name]
        actual = sha256_file(path)
        if actual != expected:
            raise ConnectedProcessPreviewError(
                f"stale {protocol_id} baseline {path.name}: "
                f"expected {expected}, found {actual}"
            )

    proposal_sha: str | None = None
    decision_sha: str | None = None
    review = entry.get("review")
    if review is not None:
        proposal_path = _pinned_artifact(
            audit_root, review["proposal"], f"{protocol_id} migration proposal"
        )
        decision_path = _pinned_artifact(
            audit_root, review["decision"], f"{protocol_id} migration decision"
        )
        try:
            proposal, decision = validate_review_decision(
                proposal_path=proposal_path,
                decision_path=decision_path,
                proposal_schema_path=schema_root
                / "audit"
                / "protocol_audit.schema.json",
                decision_schema_path=schema_root
                / "audit"
                / "review_decision.schema.json",
                require_final=False,
            )
        except ReviewError as error:
            raise ConnectedProcessPreviewError(str(error)) from error
        if proposal["protocol_id"] != protocol_id:
            raise ConnectedProcessPreviewError(
                f"migration review protocol mismatch for {protocol_id}"
            )
        issue = next(
            (
                item
                for item in decision["issue_decisions"]
                if item["issue_id"] == review["required_issue_id"]
            ),
            None,
        )
        if issue is None or issue["disposition"] != "accept":
            raise ConnectedProcessPreviewError(
                f"{protocol_id} migration issue {review['required_issue_id']} "
                "does not have an accepted human decision"
            )
        proposal_baseline_hashes = {
            artifact["sha256"] for artifact in proposal["baseline_artifacts"]
        }
        if proposal_baseline_hashes != set(expected_hashes.values()):
            raise ConnectedProcessPreviewError(
                f"{protocol_id} migration proposal does not pin the planned baselines"
            )
        proposal_sha = sha256_file(proposal_path)
        decision_sha = sha256_file(decision_path)
    elif protocol_id in REVIEW_GATED_PROTOCOLS:
        raise ConnectedProcessPreviewError(
            f"{protocol_id} is missing its protocol-specific migration review"
        )

    documents = {
        task: load_json_object(path, label=f"{protocol_id} {task} baseline")
        for task, path in paths.items()
    }
    try:
        migrated_bundle = migrate_connected_process_bundle(documents)
        migrated = migrated_bundle["T3"]
        validate_groundtruth_bundle(
            migrated_bundle,
            protocol_id=protocol_id,
            schema_root=schema_root,
        )
    except (ConnectedProcessMigrationError, LibgenValidationError) as error:
        raise ConnectedProcessPreviewError(f"{protocol_id}: {error}") from error

    candidate_dir = candidate_root / protocol_id
    candidate_dir.mkdir(parents=True)
    artifacts: list[dict[str, str]] = []
    for task, filename in GROUNDTRUTH_FILES.items():
        candidate_path = candidate_dir / filename
        if migrated_bundle[task] == documents[task]:
            shutil.copyfile(paths[task], candidate_path)
        else:
            write_json_atomic(candidate_path, migrated_bundle[task])
        artifacts.append(
            {
                "filename": filename,
                "baseline_sha256": expected_hashes[filename],
                "candidate_sha256": sha256_file(candidate_path),
            }
        )
    record: dict[str, Any] = {
        "protocol_id": protocol_id,
        "artifacts": artifacts,
        "workflow_count": len(migrated["workflows"]),
        "terminal_output_count": sum(
            len(workflow["final_outputs"]) for workflow in migrated["workflows"]
        ),
        "linked_validation": "passed",
        "review_gate": "protocol" if review is not None else "policy",
    }
    if proposal_sha is not None and decision_sha is not None:
        record["proposal_sha256"] = proposal_sha
        record["decision_sha256"] = decision_sha
    return record, migrated


def _pinned_artifact(
    audit_root: Path, reference: Mapping[str, str], label: str
) -> Path:
    relative = PurePosixPath(reference["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ConnectedProcessPreviewError(
            f"{label} path must be relative to the audit root without '..'"
        )
    path = _file(audit_root / Path(*relative.parts), label)
    actual = sha256_file(path)
    if actual != reference["sha256"]:
        raise ConnectedProcessPreviewError(
            f"stale {label}: expected {reference['sha256']}, found {actual}"
        )
    return path


def _validate(value: dict[str, Any], schema_path: Path, label: str) -> None:
    try:
        validate_document(value, schema_path, label=label)
    except AuditArtifactError as error:
        raise ConnectedProcessPreviewError(str(error)) from error


def _file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ConnectedProcessPreviewError(f"{label} does not exist: {path}")
    return resolved


def _directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ConnectedProcessPreviewError(f"{label} does not exist: {path}")
    return resolved


def _reject_overlapping_output(
    output_dir: Path, baseline_root: Path, audit_root: Path
) -> None:
    if (
        output_dir == baseline_root
        or output_dir.is_relative_to(baseline_root)
        or baseline_root.is_relative_to(output_dir)
    ):
        raise ConnectedProcessPreviewError(
            "preview output cannot contain or replace the canonical baseline root"
        )
    if output_dir == audit_root or audit_root.is_relative_to(output_dir):
        raise ConnectedProcessPreviewError(
            "preview output cannot contain or replace the audit root"
        )
