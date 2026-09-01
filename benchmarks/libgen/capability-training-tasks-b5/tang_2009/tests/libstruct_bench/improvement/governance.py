from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import sha256_file, validate_document

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    improvement_schema_root,
    load_and_validate,
    normalized_timestamp,
    validate_capability_pack,
    with_digest,
)


S0_AUDIT_SCHEMA_VERSION = "libstruct.libgen_s0_provenance_audit.v1"
TRANSFER_POLICY_SCHEMA_VERSION = "libstruct.libgen_transfer_access_policy.v1"
SUPERSESSION_SCHEMA_VERSION = "libstruct.libgen_experiment_supersession.v1"
BLOCKED_ARTIFACT_ROLES = (
    "target_source",
    "approved_groundtruth",
    "trial_result",
    "prediction",
    "trajectory",
    "verifier_reward",
    "verifier_details",
    "verifier_error_analysis",
    "verifier_error",
    "score",
)
CAPABILITY_CLOSURE_MARKERS = (
    (
        Path("design/final_lock.json"),
        "final_lock.schema.json",
        "lock_digest",
        "final development lock",
    ),
    (
        Path("design/final_development_lock.json"),
        "final_lock.schema.json",
        "lock_digest",
        "final development lock",
    ),
    (
        Path("design/transfer_panel_authorization.json"),
        "transfer_panel_authorization.schema.json",
        "authorization_digest",
        "transfer-panel authorization",
    ),
    (
        Path("final/fixed-panel-replay/manifest.json"),
        "fixed_panel_replay.schema.json",
        "replay_digest",
        "fixed-panel replay",
    ),
)


def assert_capability_modification_open(
    experiment_root: Path,
    *,
    experiment_digest: str | None = None,
) -> None:
    """Fail closed at the final development lock and every later stage."""

    root = experiment_root.expanduser().resolve()
    for relative, schema, digest_field, label in CAPABILITY_CLOSURE_MARKERS:
        marker_path = root / relative
        if not marker_path.exists():
            continue
        marker = load_and_validate(
            marker_path,
            schema_filename=schema,
            digest_field=digest_field,
            label=label,
        )
        if (
            experiment_digest is not None
            and marker["experiment_digest"] != experiment_digest
        ):
            raise CapabilityImprovementError(
                f"{label} marker belongs to another experiment"
            )
        raise CapabilityImprovementError(
            "capability modification is closed because the final development "
            f"state was frozen by {marker_path}"
        )


def build_s0_provenance_audit(
    *,
    pack_root: Path,
    original_protocol_ids: Sequence[str],
    audited_at: str,
) -> dict[str, Any]:
    """Record the known aggregate-result influence without inventing provenance."""

    pack = validate_capability_pack(pack_root)
    hits: list[dict[str, str]] = []
    audited_files = []
    for item in pack["files"]:
        path = pack_root / item["path"]
        audited_files.append({"path": item["path"], "sha256": item["sha256"]})
        if path.suffix.lower() not in {".md", ".py", ".json", ".txt", ".tsv"}:
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue
        for protocol_id in original_protocol_ids:
            if protocol_id.lower() in text:
                hits.append({"protocol_id": protocol_id, "path": item["path"]})
    payload: dict[str, Any] = {
        "schema_version": S0_AUDIT_SCHEMA_VERSION,
        "audit_id": "libgen-capability-s0-provenance-v1",
        "pack_digest": pack["pack_digest"],
        "audited_at": normalized_timestamp(audited_at),
        "baseline_inspection_preceded_s0_freeze": True,
        "aggregate_results_informed_s0": True,
        "influence_scope": "indirect_aggregate_problem_selection_and_generic_control_design",
        "direct_baseline_artifact_ingestion": False,
        "direct_groundtruth_ingestion": False,
        "protocol_specific_content_detected": bool(hits),
        "protocol_term_hits": sorted(
            hits, key=lambda item: (item["protocol_id"], item["path"])
        ),
        "audited_files": audited_files,
        "conclusion": (
            "S0 was frozen after aggregate baseline inspection. Aggregate failure themes "
            "informed the choice of generic evidence, conservation, topology, and final-audit "
            "controls. No baseline artifact or ground-truth file was directly ingested, and "
            "the deterministic text scan found no original-protocol identifier in S0."
            if not hits
            else "S0 was frozen after aggregate baseline inspection and contains protocol-term hits; "
            "those hits require resolution before the pack can be treated as neutral."
        ),
    }
    audit = with_digest(payload, "audit_digest")
    validate_document(
        audit,
        improvement_schema_root() / "s0_provenance_audit.schema.json",
        label="S0 provenance audit",
    )
    return audit


def validate_s0_provenance_audit(path: Path) -> dict[str, Any]:
    return load_and_validate(
        path,
        schema_filename="s0_provenance_audit.schema.json",
        digest_field="audit_digest",
        label="S0 provenance audit",
    )


def build_transfer_access_policy(
    *,
    panel_protocol_ids: Sequence[str],
    panel_commitment_sha256: str,
    source_root: Path,
    groundtruth_root: Path,
    baseline_run_roots: Sequence[Path],
) -> dict[str, Any]:
    panel = tuple(panel_protocol_ids)
    if len(panel) != 10 or len(set(panel)) != 10:
        raise CapabilityImprovementError(
            "transfer access policy requires ten unique protocols"
        )
    blocked_trees: list[dict[str, Any]] = []
    sources = source_root.expanduser().resolve()
    truth = groundtruth_root.expanduser().resolve()
    for protocol_id in panel:
        blocked_trees.append(
            _tree_record(protocol_id, "target_source", sources / protocol_id)
        )
        blocked_trees.append(
            _tree_record(protocol_id, "approved_groundtruth", truth / protocol_id)
        )
    blocked_files: list[dict[str, str]] = []
    seen_runs: set[Path] = set()
    for raw_root in baseline_run_roots:
        root = raw_root.expanduser().resolve()
        if root in seen_runs:
            continue
        seen_runs.add(root)
        if not root.is_dir():
            raise CapabilityImprovementError(f"baseline job root is missing: {root}")
        aggregate = root / "result.json"
        for path in sorted(root.iterdir()):
            if path.is_file() and path.name not in {"config.json", "lock.json"}:
                blocked_files.append(
                    {
                        "role": "aggregate_run_artifact",
                        "path": path.as_posix(),
                        "sha256": sha256_file(path),
                    }
                )
        for result_path in sorted(root.rglob("result.json")):
            if result_path == aggregate:
                continue
            protocol_id = _trial_protocol_id(result_path)
            if protocol_id not in panel:
                continue
            blocked_trees.append(
                _tree_record(protocol_id, "baseline_trial", result_path.parent)
            )
    payload: dict[str, Any] = {
        "schema_version": TRANSFER_POLICY_SCHEMA_VERSION,
        "panel_id": "frozen-retrospective-transfer-panel-v1",
        "panel_commitment_sha256": panel_commitment_sha256,
        "blocked_protocol_ids": list(panel),
        "blocked_artifact_roles": list(BLOCKED_ARTIFACT_ROLES),
        "blocked_trees": sorted(
            blocked_trees,
            key=lambda item: (item["role"], item["protocol_id"], item["path"]),
        ),
        "blocked_files": sorted(blocked_files, key=lambda item: item["path"]),
        "blocked_contexts": [
            "improvement_worker",
            "independent_critic",
            "human_review_console",
        ],
        "staging_policy": "copy_only_packet_allowlist_into_isolated_workspace",
        "endpoint_exception": "after_final_lock_target_sources_only_to_endpoint_agent_groundtruth_only_to_separate_verifier",
        "agent_visibility": "none_orchestrator_only",
    }
    policy = with_digest(payload, "policy_digest")
    validate_document(
        policy,
        improvement_schema_root() / "transfer_access_policy.schema.json",
        label="transfer-panel access policy",
    )
    return policy


def validate_transfer_access_policy(path: Path) -> dict[str, Any]:
    return load_and_validate(
        path,
        schema_filename="transfer_access_policy.schema.json",
        digest_field="policy_digest",
        label="transfer-panel access policy",
    )


def validate_transfer_policy_panel_binding(
    *,
    panel_protocol_ids: Sequence[str],
    policy: Mapping[str, Any],
) -> None:
    """Require the access boundary to cover exactly the declared panel.

    Historical policies may additionally pin baseline-trial trees, but every
    blocked tree must belong to the panel and every panel protocol must have
    exactly one source tree and one approved-ground-truth tree.
    """

    panel = tuple(panel_protocol_ids)
    if tuple(policy.get("blocked_protocol_ids", ())) != panel:
        raise CapabilityImprovementError(
            "transfer access policy blocked_protocol_ids differ from the panel"
        )
    trees = policy.get("blocked_trees")
    if not isinstance(trees, list):
        raise CapabilityImprovementError(
            "transfer access policy blocked_trees must be an array"
        )
    panel_set = set(panel)
    tree_protocols = {item.get("protocol_id") for item in trees}
    if tree_protocols != panel_set:
        raise CapabilityImprovementError(
            "transfer access policy blocked trees differ from the panel"
        )
    for protocol_id in panel:
        for role in ("target_source", "approved_groundtruth"):
            matching = [
                item
                for item in trees
                if item.get("protocol_id") == protocol_id and item.get("role") == role
            ]
            if len(matching) != 1:
                raise CapabilityImprovementError(
                    "transfer access policy requires exactly one "
                    f"{role} tree for {protocol_id}"
                )
            record = matching[0]
            path = record.get("path")
            if not isinstance(path, str) or not path:
                raise CapabilityImprovementError(
                    "transfer access policy blocked tree lacks a path for "
                    f"{role} {protocol_id}"
                )
            current_digest, current_count = tree_digest(Path(path))
            if (
                record.get("file_count") != current_count
                or record.get("tree_digest") != current_digest
            ):
                raise CapabilityImprovementError(
                    "transfer access policy blocked tree fingerprint changed for "
                    f"{role} {protocol_id}"
                )


def assert_transfer_panel_isolation(
    *,
    protocol_ids: Sequence[str],
    artifacts: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> None:
    blocked_protocols = set(policy["blocked_protocol_ids"])
    overlap = blocked_protocols & set(protocol_ids)
    if overlap:
        raise CapabilityImprovementError(
            "transfer-panel protocol is ineligible for improvement: "
            + ",".join(sorted(overlap))
        )
    blocked_roots = [
        Path(item["path"]).expanduser().resolve() for item in policy["blocked_trees"]
    ]
    blocked_files = {
        Path(item["path"]).expanduser().resolve() for item in policy["blocked_files"]
    }
    for artifact in artifacts:
        protocol_id = artifact.get("protocol_id")
        if protocol_id in blocked_protocols:
            raise CapabilityImprovementError(
                f"transfer-panel artifact is blocked from improvement: {protocol_id}"
            )
        path_value = artifact.get("path")
        if not isinstance(path_value, str):
            continue
        path = Path(path_value).expanduser().resolve()
        if path in blocked_files or any(
            path == root or path.is_relative_to(root) for root in blocked_roots
        ):
            raise CapabilityImprovementError(
                f"artifact path crosses the transfer-panel access boundary: {path}"
            )


def build_supersession_manifest(
    *,
    original_experiment_digest: str,
    original_artifacts: Sequence[Mapping[str, str]],
    superseded_packets: Sequence[Mapping[str, Any]],
    recorded_at: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SUPERSESSION_SCHEMA_VERSION,
        "supersession_id": "libgen-capability-v1-split-revision",
        "recorded_at": normalized_timestamp(recorded_at),
        "reason": (
            "The transfer panel was frozen before A5/H5 and before any capability proposal, "
            "decision, application, or checkpoint. The original split and B1 packets are "
            "retained byte-for-byte as ineligible historical artifacts."
        ),
        "original_experiment_digest": original_experiment_digest,
        "original_artifacts": [dict(item) for item in original_artifacts],
        "superseded_packets": [dict(item) for item in superseded_packets],
        "capability_updates_before_supersession": 0,
        "new_split_name": "frozen_retrospective_transfer_panel",
    }
    result = with_digest(payload, "supersession_digest")
    validate_document(
        result,
        improvement_schema_root() / "supersession_manifest.schema.json",
        label="capability experiment supersession",
    )
    return result


def validate_supersession_manifest(path: Path) -> dict[str, Any]:
    return load_and_validate(
        path,
        schema_filename="supersession_manifest.schema.json",
        digest_field="supersession_digest",
        label="capability experiment supersession",
    )


def tree_digest(root: Path) -> tuple[str, int]:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise CapabilityImprovementError(
            f"blocked artifact tree is missing: {resolved}"
        )
    entries = []
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"blocked artifact tree contains symlink: {path}"
            )
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(resolved).as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    if not entries:
        raise CapabilityImprovementError(f"blocked artifact tree is empty: {resolved}")
    return canonical_digest(entries), len(entries)


def _tree_record(protocol_id: str, role: str, root: Path) -> dict[str, Any]:
    digest, count = tree_digest(root)
    return {
        "protocol_id": protocol_id,
        "role": role,
        "path": root.expanduser().resolve().as_posix(),
        "file_count": count,
        "tree_digest": digest,
    }


def _trial_protocol_id(result_path: Path) -> str | None:
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    task_path = (value.get("task_id") or {}).get("path")
    return Path(task_path).name if isinstance(task_path, str) else None
