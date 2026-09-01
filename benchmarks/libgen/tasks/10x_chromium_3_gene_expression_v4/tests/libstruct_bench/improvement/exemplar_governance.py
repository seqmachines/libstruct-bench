from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import sha256_file, validate_document

from .artifacts import (
    CapabilityImprovementError,
    improvement_schema_root,
    load_and_validate,
    normalized_timestamp,
    safe_relative_path,
    validate_capability_pack,
    validate_digest,
    with_digest,
)
from .exemplar_memory import (
    IDENTITY_MAP_RELATIVE_PATH,
    validate_exemplar_identity_map,
    validate_exemplar_memory,
)
from .split_design import (
    FINAL_DEVELOPMENT_BATCHES,
    FINAL_TRANSFER_PANEL,
    FIXED_VALIDATION_PANEL,
)


PROJECTION_POLICY_SCHEMA_VERSION = "libstruct.libgen_exemplar_projection_policy.v1"
ADOPTION_SCHEMA_VERSION = "libstruct.libgen_exemplar_memory_adoption.v1"
PUBLIC_STATEMENT = (
    "Raw GT and audit records are not exposed. Approved training GT is "
    "projected into prediction-shaped exemplars and retained as cumulative "
    "memory."
)
CHECKPOINT_SCHEDULE = (
    ("C0", None, None, 0),
    ("C5", "C0", "B1", 5),
    ("C10", "C5", "B2", 10),
    ("C15", "C10", "B3", 15),
    ("C20", "C15", "B4", 20),
    ("C25", "C20", "B5", 25),
)


def build_exemplar_projection_policy(
    *,
    experiment_root: Path,
    frozen_split_path: Path,
    identity_map_path: Path,
    created_at: str,
) -> dict[str, Any]:
    """Build the frozen public policy for deterministic exemplar projection."""

    root = experiment_root.expanduser().resolve()
    split = _validate_active_split(frozen_split_path)
    identity_path = _canonical_private_map_path(root, identity_map_path)
    identity_map = validate_exemplar_identity_map(
        identity_path,
        split_digest=split["split_digest"],
    )
    payload: dict[str, Any] = {
        "schema_version": PROJECTION_POLICY_SCHEMA_VERSION,
        "policy_id": "cumulative-approved-training-exemplars-v1",
        "created_at": normalized_timestamp(created_at),
        "training_batches": [
            {
                "batch_id": batch["batch_id"],
                "protocol_ids": list(batch["protocol_ids"]),
            }
            for batch in FINAL_DEVELOPMENT_BATCHES
        ],
        "excluded_protocol_ids": {
            "validation": list(FIXED_VALIDATION_PANEL),
            "final_test": list(FINAL_TRANSFER_PANEL),
        },
        "projection": {
            "source_role": "approved_groundtruth",
            "source_binding": "exact_batch_packet_artifact_paths_roles_and_sha256",
            "outputs": [
                "mechanism_summary.json",
                "t2_example.json",
                "t3_example.json",
            ],
            "removed_categories": [
                "support_status",
                "audit_or_reviewer_metadata",
                "provenance",
                "private_paths",
                "groundtruth_only_schema_fields",
            ],
            "preserved_content": "scientific_prediction_shaped_t2_and_t3",
            "public_identity": "opaque_pseudonymous_exemplar_id_only",
            "internal_id_policy": (
                "deterministically_remap_identifiers_and_preserve_references"
            ),
            "raw_input_visibility": (
                "orchestrator_only_never_copied_to_checkpoint_memory"
            ),
        },
        "identity_map": {
            "canonical_path": IDENTITY_MAP_RELATIVE_PATH.as_posix(),
            "agent_visibility": "none_orchestrator_only",
            "mapping_count": identity_map["protocol_count"],
            "map_sha256": sha256_file(identity_path),
            "map_digest": identity_map["identity_map_digest"],
            "public_commitment_sha256": identity_map["identity_map_digest"],
        },
        "checkpoint_schedule": [
            {
                "checkpoint_id": checkpoint_id,
                "parent_checkpoint_id": parent_id,
                "batch_id": batch_id,
                "exemplar_count": count,
            }
            for checkpoint_id, parent_id, batch_id, count in CHECKPOINT_SCHEDULE
        ],
        "retrieval": {
            "maximum_exemplars": 3,
            "query_evidence": (
                "target_visible_source_derived_explicit_or_derivable_claims_only"
            ),
            "allowed_features": [
                "modality",
                "molecular_operations",
                "barcoding_and_partitioning_strategy",
                "strand_architecture",
                "selection_and_branching_operations",
                "reverse_transcription",
                "template_switching",
                "ligation",
                "tagmentation",
                "pcr",
                "restriction_chemistry",
                "conversion_chemistry",
            ],
            "forbidden_features": [
                "target_groundtruth",
                "target_errors",
                "verifier_diagnostics",
                "target_scores",
            ],
            "ranking": "group_weighted_jaccard_then_exemplar_id_v1",
            "zero_overlap_policy": "return_no_exemplars",
            "returned_scope": (
                "matching_transitions_adjacent_states_linked_t2_and_short_summary"
            ),
            "usage_record": "non_scored_exemplar_ids_only",
        },
        "target_evidence_guard": {
            "categories": [
                "sequence",
                "operation",
                "state",
                "modification",
                "branch",
            ],
            "allowed_support": (
                "target_source_or_mechanical_consequence_of_target_supported_operation"
            ),
            "failure_policy": "finding_and_nonzero_audit_exit",
        },
        "procedural_update_admission": {
            "maximum_proposed_per_batch": 2,
            "maximum_accepted_per_batch": 1,
            "admission_basis": ["recurring_root_error", "general_invariant"],
            "synthetic_regression_required": True,
            "exemplar_projection_budget": (
                "separate_deterministic_five_example_addition"
            ),
        },
        "validation_guidance": {
            "visibility": "five_protocol_macro_aggregate_only",
            "maximum_bounded_revisions": 1,
            "guidance_policy": (
                "aggregate_may_guide_bounded_revision_but_payload_may_not_be_copied"
            ),
            "prohibited_content": [
                "validation_groundtruth",
                "validation_solved_records",
                "validation_exact_sequences",
                "validation_protocol_specific_errors",
            ],
        },
        "public_statement": PUBLIC_STATEMENT,
    }
    policy = with_digest(payload, "policy_digest")
    validate_document(
        policy,
        improvement_schema_root() / "exemplar_projection_policy.schema.json",
        label="approved-training exemplar projection policy",
    )
    _validate_policy_semantics(
        policy,
        identity_map=identity_map,
        identity_map_path=identity_path,
    )
    return policy


def validate_exemplar_projection_policy(
    path: Path,
    *,
    experiment_root: Path,
    frozen_split_path: Path,
) -> dict[str, Any]:
    root = experiment_root.expanduser().resolve()
    split = _validate_active_split(frozen_split_path)
    policy = load_and_validate(
        path,
        schema_filename="exemplar_projection_policy.schema.json",
        digest_field="policy_digest",
        label="approved-training exemplar projection policy",
    )
    identity_path = root / IDENTITY_MAP_RELATIVE_PATH
    identity_map = validate_exemplar_identity_map(
        identity_path,
        split_digest=split["split_digest"],
    )
    _validate_policy_semantics(
        policy,
        identity_map=identity_map,
        identity_map_path=identity_path,
    )
    return policy


def build_exemplar_memory_adoption(
    *,
    experiment_root: Path,
    prior_experiment_manifest_path: Path,
    archive_root: Path,
    archived_artifact_paths: Sequence[Path],
    projection_policy: Mapping[str, Any],
    identity_map_path: Path,
    c0_memory_root: Path,
    prior_c0_pack_root: Path,
    new_c0_pack_root: Path,
    adopted_at: str,
    mode: str,
) -> dict[str, Any]:
    """Build the immutable bridge that installs empty exemplar memory at C0."""

    if mode not in {
        "initialized_during_single_branch_migration",
        "adopted_into_clean_c0",
    }:
        raise CapabilityImprovementError(f"unknown exemplar adoption mode: {mode}")
    root = experiment_root.expanduser().resolve()
    prior_path = prior_experiment_manifest_path.expanduser().resolve()
    prior = _load_digest_document(prior_path, "experiment_digest")
    archive_relative = safe_relative_path(archive_root.as_posix())
    resolved_archive = root / archive_relative
    if not resolved_archive.is_dir() or resolved_archive.is_symlink():
        raise CapabilityImprovementError(
            f"exemplar-memory archive is missing or unsafe: {resolved_archive}"
        )
    identity_path = _canonical_private_map_path(root, identity_map_path)
    identity_map = _load_digest_document(identity_path, "identity_map_digest")
    if projection_policy.get("policy_digest") is None:
        raise CapabilityImprovementError("exemplar projection policy lacks a digest")
    validate_digest(projection_policy, "policy_digest")
    _validate_policy_semantics(
        projection_policy,
        identity_map=identity_map,
        identity_map_path=identity_path,
    )
    memory = validate_exemplar_memory(
        c0_memory_root,
        expected_count=0,
        identity_map=identity_map,
    )
    prior_pack = validate_capability_pack(prior_c0_pack_root)
    new_pack = validate_capability_pack(new_c0_pack_root)
    if prior_pack["pack_digest"] != new_pack["pack_digest"]:
        raise CapabilityImprovementError(
            "exemplar-memory adoption may not change the procedural C0 pack"
        )
    artifacts = _artifact_records(root, archived_artifact_paths)
    if not any(item["sha256"] == sha256_file(prior_path) for item in artifacts):
        raise CapabilityImprovementError(
            "exemplar-memory adoption must preserve the prior experiment manifest"
        )
    payload: dict[str, Any] = {
        "schema_version": ADOPTION_SCHEMA_VERSION,
        "adoption_id": f"exemplar-memory-{prior['experiment_digest'][:16]}",
        "adopted_at": normalized_timestamp(adopted_at),
        "mode": mode,
        "reason": "add_separate_cumulative_prediction_shaped_exemplar_memory",
        "prior_experiment_digest": prior["experiment_digest"],
        "prior_experiment_manifest_sha256": sha256_file(prior_path),
        "archive_root": archive_relative.as_posix(),
        "archived_artifacts": artifacts,
        "policy_digest": projection_policy["policy_digest"],
        "identity_map_digest": identity_map["identity_map_digest"],
        "identity_map_sha256": sha256_file(identity_path),
        "identity_map_public_commitment_sha256": identity_map["identity_map_digest"],
        "c0_memory_digest": memory["memory_digest"],
        "c0_exemplar_count": 0,
        "prior_c0_pack_digest": prior_pack["pack_digest"],
        "new_c0_pack_digest": new_pack["pack_digest"],
        "procedural_pack_unchanged": True,
        "training_batch_processed": False,
        "harbor_run_started": False,
        "history_eligibility": "immutable_superseded_manifest_history_only",
    }
    adoption = with_digest(payload, "adoption_digest")
    validate_document(
        adoption,
        improvement_schema_root() / "exemplar_memory_adoption.schema.json",
        label="exemplar-memory adoption record",
    )
    return adoption


def validate_exemplar_memory_adoption(
    path: Path,
    *,
    experiment_root: Path,
    projection_policy: Mapping[str, Any],
    c0_memory_root: Path,
    c0_pack_root: Path,
) -> dict[str, Any]:
    root = experiment_root.expanduser().resolve()
    adoption = load_and_validate(
        path,
        schema_filename="exemplar_memory_adoption.schema.json",
        digest_field="adoption_digest",
        label="exemplar-memory adoption record",
    )
    if adoption["policy_digest"] != projection_policy["policy_digest"]:
        raise CapabilityImprovementError(
            "exemplar-memory adoption references another projection policy"
        )
    identity_path = root / IDENTITY_MAP_RELATIVE_PATH
    identity_map = _load_digest_document(identity_path, "identity_map_digest")
    if (
        adoption["identity_map_digest"] != identity_map["identity_map_digest"]
        or adoption["identity_map_sha256"] != sha256_file(identity_path)
        or adoption["identity_map_public_commitment_sha256"]
        != identity_map["identity_map_digest"]
    ):
        raise CapabilityImprovementError(
            "exemplar-memory adoption references another private identity map"
        )
    archive = root / safe_relative_path(adoption["archive_root"])
    if not archive.is_dir() or archive.is_symlink():
        raise CapabilityImprovementError(
            f"exemplar-memory adoption archive is missing or unsafe: {archive}"
        )
    for item in adoption["archived_artifacts"]:
        archived = root / safe_relative_path(item["path"])
        if (
            not archived.is_file()
            or archived.is_symlink()
            or not archived.is_relative_to(archive)
            or sha256_file(archived) != item["sha256"]
        ):
            raise CapabilityImprovementError(
                f"archived exemplar-adoption artifact changed: {item['path']}"
            )
    manifest_matches = [
        item
        for item in adoption["archived_artifacts"]
        if item["sha256"] == adoption["prior_experiment_manifest_sha256"]
    ]
    if len(manifest_matches) != 1:
        raise CapabilityImprovementError(
            "exemplar-memory adoption lacks one exact prior manifest"
        )
    prior = _load_digest_document(
        root / safe_relative_path(manifest_matches[0]["path"]),
        "experiment_digest",
    )
    if prior["experiment_digest"] != adoption["prior_experiment_digest"]:
        raise CapabilityImprovementError(
            "archived exemplar-adoption manifest has another digest"
        )
    memory = validate_exemplar_memory(
        c0_memory_root,
        expected_count=0,
        identity_map=identity_map,
    )
    pack = validate_capability_pack(c0_pack_root)
    if (
        adoption["c0_memory_digest"] != memory["memory_digest"]
        or adoption["c0_exemplar_count"] != 0
        or adoption["prior_c0_pack_digest"] != pack["pack_digest"]
        or adoption["new_c0_pack_digest"] != pack["pack_digest"]
        or not adoption["procedural_pack_unchanged"]
    ):
        raise CapabilityImprovementError(
            "active C0 differs from its exemplar-memory adoption record"
        )
    return adoption


def _validate_policy_semantics(
    policy: Mapping[str, Any],
    *,
    identity_map: Mapping[str, Any],
    identity_map_path: Path,
) -> None:
    validate_digest(policy, "policy_digest")
    expected_batches = [
        {
            "batch_id": batch["batch_id"],
            "protocol_ids": list(batch["protocol_ids"]),
        }
        for batch in FINAL_DEVELOPMENT_BATCHES
    ]
    expected_schedule = [
        {
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": parent_id,
            "batch_id": batch_id,
            "exemplar_count": count,
        }
        for checkpoint_id, parent_id, batch_id, count in CHECKPOINT_SCHEDULE
    ]
    identity = policy["identity_map"]
    if (
        policy["training_batches"] != expected_batches
        or policy["excluded_protocol_ids"]["validation"] != list(FIXED_VALIDATION_PANEL)
        or policy["excluded_protocol_ids"]["final_test"] != list(FINAL_TRANSFER_PANEL)
        or policy["checkpoint_schedule"] != expected_schedule
        or policy["public_statement"] != PUBLIC_STATEMENT
        or identity["canonical_path"] != IDENTITY_MAP_RELATIVE_PATH.as_posix()
        or identity["mapping_count"] != 25
        or identity["map_sha256"] != sha256_file(identity_map_path)
        or identity["map_digest"] != identity_map["identity_map_digest"]
        or identity["public_commitment_sha256"] != identity_map["identity_map_digest"]
    ):
        raise CapabilityImprovementError(
            "exemplar projection policy differs from the frozen memory design"
        )


def _validate_active_split(path: Path) -> dict[str, Any]:
    from .split_freeze import validate_frozen_split

    split = validate_frozen_split(path)
    if split["status"] != "active":
        raise CapabilityImprovementError(
            "exemplar memory requires the active frozen 25/5/10 split"
        )
    return split


def _canonical_private_map_path(root: Path, path: Path) -> Path:
    expected = root / IDENTITY_MAP_RELATIVE_PATH
    resolved = path.expanduser().resolve()
    if resolved != expected or path.is_symlink():
        raise CapabilityImprovementError(
            f"private exemplar identity map must use the canonical path: {expected}"
        )
    return resolved


def _artifact_records(root: Path, paths: Sequence[Path]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as error:
            raise CapabilityImprovementError(
                f"adoption archive artifact is outside experiment root: {resolved}"
            ) from error
        if not resolved.is_file() or path.is_symlink():
            raise CapabilityImprovementError(
                f"adoption archive artifact is missing or unsafe: {resolved}"
            )
        records.append({"path": relative, "sha256": sha256_file(resolved)})
    records.sort(key=lambda item: item["path"])
    if not records or len({item["path"] for item in records}) != len(records):
        raise CapabilityImprovementError(
            "adoption archive artifacts must be nonempty and unique"
        )
    return records


def _load_digest_document(path: Path, digest_field: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            f"cannot read immutable lineage artifact {path}: {error}"
        ) from error
    if not isinstance(document, dict):
        raise CapabilityImprovementError(
            f"immutable lineage artifact is not an object: {path}"
        )
    validate_digest(document, digest_field)
    return document
