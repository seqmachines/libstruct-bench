from __future__ import annotations

import json
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    sha256_file,
    validate_document,
    write_json_atomic,
)

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    copy_capability_pack,
    freeze_tree,
    improvement_schema_root,
    load_and_validate,
    normalized_timestamp,
    safe_relative_path,
    thaw_tree,
    trees_byte_identical,
    validate_capability_pack,
    validate_digest,
    with_digest,
)
from .governance import tree_digest
from .mutation_lock import (
    experiment_mutation_lock,
    single_branch_migration_journal_path,
    split_freeze_journal_path,
)


MIGRATION_SCHEMA_VERSION = "libstruct.libgen_capability_single_branch_migration.v1"
ARCHIVED_TREE_ROLES = ("design", "checkpoints", "packs", "rounds")
IMMUTABLE_ACTIVE_GOVERNANCE_PATHS = (
    "design/experiment_manifest.json",
    "design/exemplar_memory_adoption.json",
    "design/exemplar_projection_policy.json",
    "design/frozen_split.json",
    "design/single_branch_migration.json",
    "design/test_isolation_audit.json",
    "design/transfer_access_policy.json",
    "design/transfer_panel_commitment.json",
    "design/validation_access_policy.json",
    "design/validation_isolation_audit.json",
)
ACTIVE_MIGRATION_PATH = Path("design/single_branch_migration.json")
MIGRATION_JOURNAL_SCHEMA_VERSION = (
    "libstruct.libgen_capability_single_branch_migration_journal.v1"
)


def build_single_branch_migration(
    *,
    experiment_root: Path,
    prior_experiment: Mapping[str, Any],
    prior_split_digest: str,
    new_split_digest: str,
    archive_relative: Path,
    source_s0_root: Path,
    staged_c0_pack_root: Path,
    validation_access_policy: Mapping[str, Any],
    validation_isolation_audit: Mapping[str, Any],
    validation_panel_commitment_sha256: str,
    final_test_panel_commitment_sha256: str,
    external_root_bindings: Mapping[str, Any],
    migrated_at: str,
) -> dict[str, Any]:
    """Build the hash-pinned bridge from the contaminated A lineage to C0."""

    root = experiment_root.expanduser().resolve()
    archive_relative = safe_relative_path(archive_relative.as_posix())
    source_s0 = source_s0_root.expanduser().resolve()
    staged_c0_pack = staged_c0_pack_root.expanduser().resolve()
    source_pack = validate_capability_pack(source_s0)
    c0_pack = validate_capability_pack(staged_c0_pack)
    if source_pack["pack_digest"] != c0_pack["pack_digest"] or not (
        trees_byte_identical(source_s0, staged_c0_pack)
    ):
        raise CapabilityImprovementError(
            "clean C0 must be byte-identical to the predecessor's original S0"
        )
    validate_digest(validation_access_policy, "policy_digest")
    validate_digest(validation_isolation_audit, "audit_digest")
    if validation_access_policy.get("validation_panel_commitment_sha256") != (
        validation_panel_commitment_sha256
    ):
        raise CapabilityImprovementError(
            "validation policy belongs to another fixed panel"
        )
    if (
        validation_isolation_audit.get("learning_isolation") != "pass"
        or validation_isolation_audit.get("access_policy_digest")
        != validation_access_policy["policy_digest"]
        or validation_isolation_audit.get("validation_panel_commitment_sha256")
        != validation_panel_commitment_sha256
    ):
        raise CapabilityImprovementError(
            "single-branch migration requires a passing validation-isolation audit"
        )
    from .validation import scan_validation_pack_leakage

    validation_leaks = scan_validation_pack_leakage(
        staged_c0_pack, validation_access_policy
    )
    if validation_leaks:
        raise CapabilityImprovementError(
            "clean C0 violates validation isolation: " + "; ".join(validation_leaks[:8])
        )
    checkpoint_ids = _active_checkpoint_ids(root / "checkpoints")
    if checkpoint_ids != ["A5", "A10"]:
        raise CapabilityImprovementError(
            "single-branch migration requires exactly superseded A5 and A10"
        )
    archived_trees = [
        _tree_record(
            root / role,
            role=role,
            archived_path=archive_relative / role,
        )
        for role in ARCHIVED_TREE_ROLES
    ]
    preserved_prior_history = _prior_history_record(
        root / "history",
        appended_archive=archive_relative,
    )
    payload: dict[str, Any] = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_id": (
            f"single-branch-25-5-10-{prior_experiment['experiment_digest'][:16]}"
        ),
        "migrated_at": normalized_timestamp(migrated_at),
        "reason": (
            "replace_superseded_30_10_two_branch_design_with_clean_"
            "single_branch_25_5_10_design"
        ),
        "prior_experiment_digest": prior_experiment["experiment_digest"],
        "prior_experiment_manifest_sha256": sha256_file(
            root / "design" / "experiment_manifest.json"
        ),
        "prior_supersession_digest": prior_experiment["supersession"]["digest"],
        "prior_split_digest": prior_split_digest,
        "new_split_digest": new_split_digest,
        "archived_root": archive_relative.as_posix(),
        "archived_trees": archived_trees,
        "preserved_prior_history": preserved_prior_history,
        "prior_active_checkpoint_ids": checkpoint_ids,
        "validation_contamination_disposition": {
            "contaminated_checkpoint_ids": ["A10"],
            "disposition": "superseded_history_only",
            "eligible_for_c_lineage": False,
            "eligible_as_validation_baseline": False,
        },
        "source_s0": _pack_record(
            source_s0,
            path=(archive_relative / "packs" / "S0").as_posix(),
        ),
        "new_active_start": {
            **_pack_record(staged_c0_pack, path="checkpoints/C0/pack"),
            "checkpoint_id": "C0",
            "byte_identical_to_source_s0": True,
        },
        "validation_panel_commitment_sha256": (validation_panel_commitment_sha256),
        "validation_access_policy_digest": validation_access_policy["policy_digest"],
        "validation_isolation_audit_digest": validation_isolation_audit["audit_digest"],
        "final_test_panel_commitment_sha256": (final_test_panel_commitment_sha256),
        "external_root_bindings": dict(external_root_bindings),
        "history_eligibility": "immutable_superseded_history_only",
        "harbor_run_started": False,
    }
    result = with_digest(payload, "migration_digest")
    validate_document(
        result,
        improvement_schema_root() / "single_branch_migration.schema.json",
        label="single-branch capability migration",
    )
    return result


def validate_single_branch_migration(
    path: Path,
    *,
    experiment_root: Path,
) -> dict[str, Any]:
    """Validate the archived predecessor and clean active C0 as one lineage."""

    root = experiment_root.expanduser().resolve()
    document = load_and_validate(
        path,
        schema_filename="single_branch_migration.schema.json",
        digest_field="migration_digest",
        label="single-branch capability migration",
    )
    archived_root = _resolve_internal_path(
        root, document["archived_root"], label="superseded archive"
    )
    records = document["archived_trees"]
    if [item["role"] for item in records] != list(ARCHIVED_TREE_ROLES):
        raise CapabilityImprovementError(
            "single-branch migration must archive the four active trees in order"
        )
    for item in records:
        expected = archived_root / item["role"]
        if root / safe_relative_path(item["path"]) != expected:
            raise CapabilityImprovementError(
                f"archived tree path differs for {item['role']}"
            )
        digest, count = tree_digest(expected)
        if digest != item["tree_digest"] or count != item["file_count"]:
            raise CapabilityImprovementError(
                f"archived tree changed after migration: {item['role']}"
            )
    if sorted(path.name for path in archived_root.iterdir()) != list(
        sorted(ARCHIVED_TREE_ROLES)
    ):
        raise CapabilityImprovementError(
            "superseded archive must contain exactly the four predecessor trees"
        )
    preserved = document["preserved_prior_history"]
    excluded_history = [Path(document["archived_root"]).relative_to("history")]
    exemplar_adoption_path = root / "design" / "exemplar_memory_adoption.json"
    if exemplar_adoption_path.is_file() and not exemplar_adoption_path.is_symlink():
        exemplar_adoption = load_and_validate(
            exemplar_adoption_path,
            schema_filename="exemplar_memory_adoption.schema.json",
            digest_field="adoption_digest",
            label="exemplar-memory adoption record",
        )
        adoption_archive = Path(exemplar_adoption["archive_root"])
        try:
            adoption_history_relative = adoption_archive.relative_to("history")
        except ValueError as error:
            raise CapabilityImprovementError(
                "exemplar-memory adoption archive is outside immutable history"
            ) from error
        if exemplar_adoption["mode"] == "adopted_into_clean_c0":
            excluded_history.append(adoption_history_relative)
    preserved_digest, preserved_count = _tree_digest_excluding(
        root / "history",
        excluded=excluded_history,
    )
    if (
        preserved["path"] != "history"
        or preserved["tree_digest"] != preserved_digest
        or preserved["file_count"] != preserved_count
        or preserved["appended_archive"] != document["archived_root"]
    ):
        raise CapabilityImprovementError(
            "pre-existing immutable history changed during migration"
        )
    prior_manifest_path = archived_root / "design" / "experiment_manifest.json"
    if sha256_file(prior_manifest_path) != document["prior_experiment_manifest_sha256"]:
        raise CapabilityImprovementError("archived predecessor manifest changed")
    prior_manifest = _load_object(prior_manifest_path, "archived experiment manifest")
    if prior_manifest.get("experiment_digest") != document["prior_experiment_digest"]:
        raise CapabilityImprovementError(
            "archived predecessor experiment digest differs"
        )
    prior_split = _load_object(
        archived_root / "design" / "frozen_split.json",
        "archived frozen split",
    )
    if prior_split.get("split_digest") != document["prior_split_digest"]:
        raise CapabilityImprovementError("archived predecessor split digest differs")
    if _active_checkpoint_ids(archived_root / "checkpoints") != ["A5", "A10"]:
        raise CapabilityImprovementError("archived checkpoint set differs")

    source_s0 = _resolve_internal_path(
        root, document["source_s0"]["path"], label="archived S0"
    )
    active_c0 = _resolve_internal_path(
        root, document["new_active_start"]["path"], label="active C0"
    )
    _validate_pack_record(source_s0, document["source_s0"])
    _validate_pack_record(active_c0, document["new_active_start"])
    if not trees_byte_identical(source_s0, active_c0):
        raise CapabilityImprovementError(
            "active C0 is not byte-identical to archived original S0"
        )
    rebound = _build_external_root_bindings(
        prior_root=archived_root,
        prior_experiment=prior_manifest,
        history_reference_root=root,
        source_root=Path(document["external_root_bindings"]["source_root"]),
        groundtruth_root=Path(document["external_root_bindings"]["groundtruth_root"]),
    )
    if rebound != document["external_root_bindings"]:
        raise CapabilityImprovementError(
            "external source or ground-truth roots differ from predecessor bindings"
        )
    from .validation import (
        scan_validation_pack_leakage,
        validate_validation_access_policy,
        validate_validation_isolation_audit,
    )

    validation_policy = validate_validation_access_policy(
        root / "design" / "validation_access_policy.json"
    )
    validation_audit = validate_validation_isolation_audit(
        root / "design" / "validation_isolation_audit.json",
        validation_access_policy=validation_policy,
    )
    if (
        validation_policy["policy_digest"]
        != document["validation_access_policy_digest"]
        or validation_audit["audit_digest"]
        != document["validation_isolation_audit_digest"]
        or validation_audit["learning_isolation"] != "pass"
    ):
        raise CapabilityImprovementError(
            "active validation isolation governance differs from migration"
        )
    validation_leaks = scan_validation_pack_leakage(active_c0, validation_policy)
    if validation_leaks:
        raise CapabilityImprovementError(
            "active C0 violates validation isolation: "
            + "; ".join(validation_leaks[:8])
        )
    _validate_active_cumulative_lineage(root)
    return document


def migrate_to_cumulative_experiment(
    *,
    experiment_root: Path,
    source_root: Path,
    groundtruth_root: Path,
    recorded_at: str,
    agent_version: str,
    authorize_migration: bool = False,
) -> dict[str, Any]:
    """Preflight or atomically install the clean C0 cumulative experiment.

    The authorized transaction copies the complete predecessor history into a
    new root, freezes a byte-identical C0 from the predecessor's original S0,
    validates every active governance artifact, and only then swaps roots.
    """

    root = experiment_root.expanduser().resolve()
    source_root = source_root.expanduser().resolve()
    groundtruth_root = groundtruth_root.expanduser().resolve()
    with experiment_mutation_lock(
        root,
        operation="single cumulative 25/5/10 migration",
    ):
        journal = single_branch_migration_journal_path(root)
        if journal.exists() and not authorize_migration:
            raise CapabilityImprovementError(
                "an interrupted single-branch migration requires an authorized "
                "rerun for recovery"
            )
        if authorize_migration:
            recovered = _recover_migration_transaction(root)
            if recovered is not None:
                return recovered
        else:
            stage, backup = _migration_transaction_paths(root)
            if stage.exists() or backup.exists():
                raise CapabilityImprovementError(
                    "untracked single-branch migration staging or backup path exists"
                )
        existing = _existing_migration_result(root)
        if existing is not None:
            return existing
        if split_freeze_journal_path(root).exists():
            raise CapabilityImprovementError(
                "an interrupted split freeze must be recovered before migration"
            )
        prior = _validate_prior_experiment(root)
        archive_relative = Path(
            "history/superseded/"
            f"single-branch-25-5-10-{prior['experiment_digest'][:16]}"
        )
        stage, backup = _migration_transaction_paths(root)
        if stage.exists() or backup.exists():
            raise CapabilityImprovementError(
                "untracked single-branch migration staging or backup path exists"
            )
        if not authorize_migration:
            scratch_container = Path(
                tempfile.mkdtemp(
                    prefix=f".{root.name}.single-branch-preflight-",
                    dir=root.parent,
                )
            )
            scratch = scratch_container / "experiment"
            try:
                manifest = _stage_cumulative_experiment(
                    prior_root=root,
                    stage=scratch,
                    prior=prior,
                    archive_relative=archive_relative,
                    source_root=source_root,
                    groundtruth_root=groundtruth_root,
                    recorded_at=recorded_at,
                    agent_version=agent_version,
                )
                return _migration_result(
                    root=scratch,
                    manifest=manifest,
                    archive_relative=archive_relative,
                    status="ready_to_migrate",
                    reported_root=root,
                )
            finally:
                _remove_transaction_tree(scratch_container, root=root)

        journal = single_branch_migration_journal_path(root)
        _write_migration_journal(
            journal,
            root=root,
            stage=stage,
            backup=backup,
            prior_digest=prior["experiment_digest"],
            new_digest=None,
            phase="staging",
        )
        try:
            manifest = _stage_cumulative_experiment(
                prior_root=root,
                stage=stage,
                prior=prior,
                archive_relative=archive_relative,
                source_root=source_root,
                groundtruth_root=groundtruth_root,
                recorded_at=recorded_at,
                agent_version=agent_version,
            )
            _validate_prior_unchanged(root, prior)
            _write_migration_journal(
                journal,
                root=root,
                stage=stage,
                backup=backup,
                prior_digest=prior["experiment_digest"],
                new_digest=manifest["experiment_digest"],
                phase="prepared",
            )
            root.replace(backup)
            stage.replace(root)
            from .experiment import validate_experiment_manifest

            active = validate_experiment_manifest(
                root / "design" / "experiment_manifest.json",
                experiment_root=root,
            )
            if active["experiment_digest"] != manifest["experiment_digest"]:
                raise CapabilityImprovementError(
                    "installed cumulative experiment differs from staged manifest"
                )
            validate_single_branch_migration(
                root / ACTIVE_MIGRATION_PATH,
                experiment_root=root,
            )
            _remove_transaction_tree(backup, root=root)
            journal.unlink()
            return _migration_result(
                root=root,
                manifest=active,
                archive_relative=archive_relative,
                status="migrated_to_clean_c0",
            )
        except BaseException as error:
            try:
                recovered = _recover_migration_transaction(root)
            except BaseException as recovery_error:
                raise CapabilityImprovementError(
                    "single-branch migration failed and recovery also failed: "
                    f"{recovery_error}"
                ) from error
            if recovered is not None:
                return recovered
            raise


def _stage_cumulative_experiment(
    *,
    prior_root: Path,
    stage: Path,
    prior: Mapping[str, Any],
    archive_relative: Path,
    source_root: Path,
    groundtruth_root: Path,
    recorded_at: str,
    agent_version: str,
) -> dict[str, Any]:
    if stage.exists():
        raise CapabilityImprovementError(f"migration stage already exists: {stage}")
    stage.mkdir(parents=True)
    prior_history = prior_root / "history"
    _copy_preserved_history(
        prior_history,
        stage / "history",
        appended_archive=archive_relative,
    )
    archive = stage / archive_relative
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        raise CapabilityImprovementError(
            f"single-branch archive already exists: {archive_relative}"
        )
    archive.mkdir()
    for role in ARCHIVED_TREE_ROLES:
        source = prior_root / role
        destination = archive / role
        if not source.is_dir() or source.is_symlink():
            raise CapabilityImprovementError(
                f"predecessor active tree is missing or unsafe: {source}"
            )
        shutil.copytree(source, destination, copy_function=shutil.copy2)
        if not trees_byte_identical(source, destination):
            raise CapabilityImprovementError(
                f"staged predecessor archive differs for {role}"
            )

    source_s0 = prior_root / "packs" / "S0"
    temporary_pack = stage / ".clean-c0-source-pack"
    copy_capability_pack(source_s0, temporary_pack, freeze=True)
    design = stage / "design"
    design.mkdir()

    from .governance import build_transfer_access_policy
    from .split_design import (
        FINAL_DEVELOPMENT_BATCHES,
        FINAL_REBALANCE_POLICY,
        FINAL_TRANSFER_ANNOTATIONS,
        FINAL_TRANSFER_PANEL,
        FINAL_TRANSFER_PURPOSE,
        FINAL_TRANSFER_STRATA,
        FIXED_VALIDATION_PANEL,
    )
    from .split_freeze import (
        _final_panel_commitment_digest,
        _transfer_panel_commitment,
        build_frozen_split,
        build_test_isolation_audit,
    )
    from .validation import (
        build_validation_access_policy,
        build_validation_isolation_audit,
        validation_panel_commitment_digest,
    )

    final_commitment = _final_panel_commitment_digest()
    validation_commitment = validation_panel_commitment_digest()
    external_root_bindings = _build_external_root_bindings(
        prior_root=prior_root,
        prior_experiment=prior,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
    )
    transfer_policy = build_transfer_access_policy(
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        panel_commitment_sha256=final_commitment,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        baseline_run_roots=(),
    )
    validation_policy = build_validation_access_policy(
        validation_panel_commitment_sha256=validation_commitment,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        initial_pack_root=temporary_pack,
        created_at=recorded_at,
    )
    frozen_split = build_frozen_split(
        batches=FINAL_DEVELOPMENT_BATCHES,
        validation_protocol_ids=FIXED_VALIDATION_PANEL,
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        recorded_at=recorded_at,
        status="active",
        transfer_strata=FINAL_TRANSFER_STRATA,
        transfer_annotations=FINAL_TRANSFER_ANNOTATIONS,
        purposes=FINAL_TRANSFER_PURPOSE,
        rebalance_policy=FINAL_REBALANCE_POLICY,
        split_id="libgen-capability-25-5-10-split",
    )
    transfer_commitment = _transfer_panel_commitment(final_commitment)
    write_json_atomic(
        design / "transfer_access_policy.json", transfer_policy, mode=0o444
    )
    write_json_atomic(
        design / "validation_access_policy.json", validation_policy, mode=0o444
    )
    write_json_atomic(design / "frozen_split.json", frozen_split, mode=0o444)
    write_json_atomic(
        design / "transfer_panel_commitment.json", transfer_commitment, mode=0o444
    )

    from .exemplar_governance import (
        build_exemplar_memory_adoption,
        build_exemplar_projection_policy,
    )
    from .exemplar_memory import (
        create_empty_exemplar_memory,
        ensure_exemplar_identity_map,
    )

    identity_map = ensure_exemplar_identity_map(
        experiment_root=stage,
        split_digest=frozen_split["split_digest"],
    )
    exemplar_policy = build_exemplar_projection_policy(
        experiment_root=stage,
        frozen_split_path=design / "frozen_split.json",
        identity_map_path=stage / "private" / "exemplar_identity_map.json",
        created_at=recorded_at,
    )
    write_json_atomic(
        design / "exemplar_projection_policy.json",
        exemplar_policy,
        mode=0o444,
    )
    temporary_memory = stage / ".clean-c0-memory"
    create_empty_exemplar_memory(
        memory_root=temporary_memory,
        identity_map=identity_map,
    )

    test_audit = build_test_isolation_audit(
        experiment_root=stage,
        active_batches=FINAL_DEVELOPMENT_BATCHES,
        audited_at=recorded_at,
        transfer_access_policy=transfer_policy,
    )
    if (
        test_audit["learning_isolation"] != "pass"
        or test_audit["development_access_isolation"] != "pass"
    ):
        raise CapabilityImprovementError(
            "final-test isolation audit failed during cumulative migration"
        )
    validation_audit = build_validation_isolation_audit(
        experiment_root=stage,
        validation_access_policy=validation_policy,
        audited_at=recorded_at,
    )
    if validation_audit["learning_isolation"] != "pass":
        raise CapabilityImprovementError(
            "validation isolation audit failed during cumulative migration"
        )
    write_json_atomic(design / "test_isolation_audit.json", test_audit, mode=0o444)
    write_json_atomic(
        design / "validation_isolation_audit.json", validation_audit, mode=0o444
    )

    migration = build_single_branch_migration(
        experiment_root=prior_root,
        prior_experiment=prior,
        prior_split_digest=_prior_split_digest(prior_root),
        new_split_digest=frozen_split["split_digest"],
        archive_relative=archive_relative,
        source_s0_root=source_s0,
        staged_c0_pack_root=temporary_pack,
        validation_access_policy=validation_policy,
        validation_isolation_audit=validation_audit,
        validation_panel_commitment_sha256=validation_commitment,
        final_test_panel_commitment_sha256=final_commitment,
        external_root_bindings=external_root_bindings,
        migrated_at=recorded_at,
    )
    write_json_atomic(design / "single_branch_migration.json", migration, mode=0o444)

    exemplar_adoption = build_exemplar_memory_adoption(
        experiment_root=stage,
        prior_experiment_manifest_path=(
            archive / "design" / "experiment_manifest.json"
        ),
        archive_root=archive_relative,
        archived_artifact_paths=(
            archive / "design" / "experiment_manifest.json",
            archive / "checkpoints" / "A5" / "checkpoint.json",
            archive / "checkpoints" / "A10" / "checkpoint.json",
        ),
        projection_policy=exemplar_policy,
        identity_map_path=stage / "private" / "exemplar_identity_map.json",
        c0_memory_root=temporary_memory,
        prior_c0_pack_root=source_s0,
        new_c0_pack_root=temporary_pack,
        adopted_at=recorded_at,
        mode="initialized_during_single_branch_migration",
    )
    write_json_atomic(
        design / "exemplar_memory_adoption.json",
        exemplar_adoption,
        mode=0o444,
    )

    provenance_path = archive / "design" / "s0_provenance_audit.json"
    supersession_path = archive / "design" / "supersession_manifest.json"
    from .experiment import build_cumulative_experiment_manifest

    manifest = build_cumulative_experiment_manifest(
        experiment_root=stage,
        capability_pack_root=temporary_pack,
        private_groundtruth_root=groundtruth_root,
        c0_provenance_audit_path=provenance_path,
        transfer_access_policy_path=design / "transfer_access_policy.json",
        validation_access_policy_path=design / "validation_access_policy.json",
        validation_isolation_audit_path=(design / "validation_isolation_audit.json"),
        exemplar_projection_policy_path=(design / "exemplar_projection_policy.json"),
        exemplar_memory_adoption_path=(design / "exemplar_memory_adoption.json"),
        frozen_split_path=design / "frozen_split.json",
        test_isolation_audit_path=design / "test_isolation_audit.json",
        transfer_panel_commitment_path=(design / "transfer_panel_commitment.json"),
        supersession_manifest_path=supersession_path,
        single_branch_migration_path=design / "single_branch_migration.json",
        created_at=recorded_at,
        agent_version=agent_version,
    )
    write_json_atomic(design / "experiment_manifest.json", manifest, mode=0o444)
    from .workflow import freeze_baseline_checkpoint

    freeze_baseline_checkpoint(
        experiment_digest=manifest["experiment_digest"],
        source_pack_root=temporary_pack,
        source_memory_root=temporary_memory,
        output_dir=stage / "checkpoints" / "C0",
        created_at=recorded_at,
    )
    _remove_staged_child_tree(temporary_pack, stage=stage)
    _remove_staged_child_tree(temporary_memory, stage=stage)

    recomputed_validation = build_validation_isolation_audit(
        experiment_root=stage,
        validation_access_policy=validation_policy,
        audited_at=recorded_at,
    )
    recomputed_test = build_test_isolation_audit(
        experiment_root=stage,
        active_batches=FINAL_DEVELOPMENT_BATCHES,
        audited_at=recorded_at,
        transfer_access_policy=transfer_policy,
    )
    if recomputed_validation != validation_audit or recomputed_test != test_audit:
        raise CapabilityImprovementError(
            "C0 changed a pre-manifest isolation audit during migration"
        )
    freeze_tree(stage / "history")
    _assert_immutable_active_governance(stage)
    from .experiment import validate_experiment_manifest

    validated = validate_experiment_manifest(
        design / "experiment_manifest.json",
        experiment_root=stage,
    )
    validate_single_branch_migration(
        design / "single_branch_migration.json",
        experiment_root=stage,
    )
    return validated


def _tree_record(root: Path, *, role: str, archived_path: Path) -> dict[str, Any]:
    digest, count = tree_digest(root)
    if count == 0:
        raise CapabilityImprovementError(f"cannot archive empty active tree: {role}")
    return {
        "role": role,
        "path": archived_path.as_posix(),
        "tree_digest": digest,
        "file_count": count,
    }


def _prior_history_record(
    history_root: Path,
    *,
    appended_archive: Path,
) -> dict[str, Any]:
    history = history_root.expanduser().resolve()
    if not history.is_dir() or history_root.is_symlink():
        raise CapabilityImprovementError(
            "single-branch migration requires a regular predecessor history tree"
        )
    archive = safe_relative_path(appended_archive.as_posix())
    try:
        archive_within_history = archive.relative_to("history")
    except ValueError as error:
        raise CapabilityImprovementError(
            "single-branch archive must be appended beneath history"
        ) from error
    if not archive_within_history.parts:
        raise CapabilityImprovementError(
            "single-branch archive cannot replace the history root"
        )
    digest, count = tree_digest(history)
    return {
        "path": "history",
        "tree_digest": digest,
        "file_count": count,
        "appended_archive": archive.as_posix(),
    }


def _copy_preserved_history(
    source: Path,
    destination: Path,
    *,
    appended_archive: Path,
) -> dict[str, Any]:
    """Copy immutable history exactly, then thaw only the private staged copy."""

    record = _prior_history_record(source, appended_archive=appended_archive)
    if destination.exists():
        raise CapabilityImprovementError(
            f"staged predecessor history already exists: {destination}"
        )
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    if not trees_byte_identical(source, destination):
        raise CapabilityImprovementError(
            "staged copy of the predecessor history differs before archival"
        )
    thaw_tree(destination)
    return record


def _assert_immutable_active_governance(root: Path) -> None:
    resolved = root.expanduser().resolve()
    for relative in IMMUTABLE_ACTIVE_GOVERNANCE_PATHS:
        path = _resolve_internal_path(
            resolved, relative, label="active immutable governance"
        )
        if not path.is_file() or path.is_symlink():
            raise CapabilityImprovementError(
                f"active immutable governance file is missing or unsafe: {relative}"
            )
        if stat.S_IMODE(path.stat().st_mode) != 0o444:
            raise CapabilityImprovementError(
                f"active immutable governance file is not mode 0444: {relative}"
            )


def _tree_digest_excluding(
    root: Path,
    *,
    excluded: Path | Sequence[Path],
) -> tuple[str, int]:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir() or root.is_symlink():
        raise CapabilityImprovementError(
            f"preserved history tree is missing or unsafe: {resolved}"
        )
    excluded_values = (excluded,) if isinstance(excluded, Path) else tuple(excluded)
    excluded_relatives = tuple(
        safe_relative_path(value.as_posix()) for value in excluded_values
    )
    entries: list[dict[str, Any]] = []
    for path in sorted(resolved.rglob("*")):
        relative = path.relative_to(resolved)
        if any(
            relative == excluded_relative or excluded_relative in relative.parents
            for excluded_relative in excluded_relatives
        ):
            continue
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"preserved history contains a symlink: {path}"
            )
        if path.is_file():
            entries.append(
                {
                    "path": relative.as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    if not entries:
        raise CapabilityImprovementError("preserved predecessor history is empty")
    return canonical_digest(entries), len(entries)


def _build_external_root_bindings(
    *,
    prior_root: Path,
    prior_experiment: Mapping[str, Any],
    source_root: Path,
    groundtruth_root: Path,
    history_reference_root: Path | None = None,
) -> dict[str, Any]:
    """Bind migration inputs to predecessor-pinned private evidence bytes."""

    from .split_design import FINAL_TRANSFER_PANEL, FIXED_VALIDATION_PANEL

    root = prior_root.expanduser().resolve()
    history_refs = (history_reference_root or prior_root).expanduser().resolve()
    sources = source_root.expanduser().resolve()
    truth = groundtruth_root.expanduser().resolve()
    if not sources.is_dir() or source_root.is_symlink():
        raise CapabilityImprovementError(f"source root is missing or unsafe: {sources}")
    if not truth.is_dir() or groundtruth_root.is_symlink():
        raise CapabilityImprovementError(
            f"ground-truth root is missing or unsafe: {truth}"
        )

    panel = prior_experiment.get("frozen_retrospective_transfer_panel")
    if not isinstance(panel, Mapping) or not isinstance(
        panel.get("access_policy"), Mapping
    ):
        raise CapabilityImprovementError(
            "predecessor lacks a frozen final-test access-policy reference"
        )
    policy_reference = panel["access_policy"]
    policy_path = _resolve_internal_path(
        root,
        policy_reference.get("path"),
        label="predecessor transfer access policy",
    )
    if sha256_file(policy_path) != policy_reference.get("sha256"):
        raise CapabilityImprovementError(
            "predecessor transfer access-policy hash differs"
        )
    policy = _load_object(policy_path, "predecessor transfer access policy")
    validate_digest(policy, "policy_digest")
    if policy["policy_digest"] != policy_reference.get("digest"):
        raise CapabilityImprovementError(
            "predecessor transfer access-policy digest differs"
        )
    if tuple(policy.get("blocked_protocol_ids", ())) != tuple(FINAL_TRANSFER_PANEL):
        raise CapabilityImprovementError(
            "predecessor transfer access policy has another final-test panel"
        )
    for protocol_id in FINAL_TRANSFER_PANEL:
        for role, external_root in (
            ("target_source", sources),
            ("approved_groundtruth", truth),
        ):
            matching = [
                item
                for item in policy.get("blocked_trees", ())
                if item.get("protocol_id") == protocol_id and item.get("role") == role
            ]
            if len(matching) != 1:
                raise CapabilityImprovementError(
                    "predecessor final-test policy lacks an exact unique tree for "
                    f"{protocol_id} ({role})"
                )
            [record] = matching
            expected = (external_root / protocol_id).resolve()
            if Path(record["path"]).expanduser().resolve() != expected:
                raise CapabilityImprovementError(
                    "migration source/ground-truth roots do not match the "
                    f"predecessor final-test policy for {protocol_id} ({role})"
                )
            digest, count = tree_digest(expected)
            if record.get("tree_digest") != digest or record.get("file_count") != count:
                raise CapabilityImprovementError(
                    "migration source/ground-truth bytes differ from the "
                    f"predecessor final-test policy for {protocol_id} ({role})"
                )

    split_reference = prior_experiment.get("split_supersession")
    if not isinstance(split_reference, Mapping):
        raise CapabilityImprovementError(
            "predecessor lacks the archived B2 validation packet reference"
        )
    split_path = _resolve_internal_path(
        history_refs,
        split_reference.get("path"),
        label="predecessor split supersession",
    )
    if sha256_file(split_path) != split_reference.get("sha256"):
        raise CapabilityImprovementError("predecessor split-supersession hash differs")
    split = _load_object(split_path, "predecessor split supersession")
    if split.get("supersession_digest") != split_reference.get("digest"):
        raise CapabilityImprovementError(
            "predecessor split-supersession digest differs"
        )
    packet_relative = (
        safe_relative_path(split["archive_root"])
        / "rounds"
        / "B2"
        / "autonomous"
        / "packet.json"
    )
    packet_path = _resolve_internal_path(
        history_refs,
        packet_relative.as_posix(),
        label="archived B2 validation packet",
    )
    packet = _load_object(packet_path, "archived B2 validation packet")
    validate_digest(packet, "packet_digest")
    if tuple(packet.get("protocol_ids", ())) != tuple(FIXED_VALIDATION_PANEL):
        raise CapabilityImprovementError(
            "archived B2 packet does not contain the fixed validation panel"
        )
    validation_artifact_count = 0
    for protocol_id in FIXED_VALIDATION_PANEL:
        for role, external_root in (
            ("target_source", sources),
            ("approved_groundtruth", truth),
        ):
            records = [
                item
                for item in packet.get("artifacts", ())
                if item.get("protocol_id") == protocol_id and item.get("role") == role
            ]
            if not records:
                raise CapabilityImprovementError(
                    f"archived B2 packet lacks {role} evidence for {protocol_id}"
                )
            actual_root = (external_root / protocol_id).resolve()
            actual = _regular_file_inventory(actual_root)
            expected: dict[str, str] = {}
            for record in records:
                recorded_path = Path(record["path"]).expanduser().resolve()
                if not recorded_path.is_relative_to(actual_root):
                    raise CapabilityImprovementError(
                        "migration source/ground-truth root path differs from the "
                        f"archived B2 packet for {protocol_id} ({role})"
                    )
                relative = recorded_path.relative_to(actual_root).as_posix()
                expected[relative] = record["sha256"]
            if actual != expected:
                raise CapabilityImprovementError(
                    "migration source/ground-truth bytes differ from the archived "
                    f"B2 packet for {protocol_id} ({role})"
                )
            validation_artifact_count += len(records)

    return {
        "source_root": sources.as_posix(),
        "groundtruth_root": truth.as_posix(),
        "final_test_transfer_policy_digest": policy["policy_digest"],
        "validation_packet_prior_relative_path": packet_relative.as_posix(),
        "validation_packet_sha256": sha256_file(packet_path),
        "validation_packet_digest": packet["packet_digest"],
        "validation_artifact_count": validation_artifact_count,
    }


def _regular_file_inventory(root: Path) -> dict[str, str]:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir() or root.is_symlink():
        raise CapabilityImprovementError(
            f"evidence tree is missing or unsafe: {resolved}"
        )
    result: dict[str, str] = {}
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"evidence tree contains a symlink: {path}"
            )
        if path.is_file():
            result[path.relative_to(resolved).as_posix()] = sha256_file(path)
    if not result:
        raise CapabilityImprovementError(f"evidence tree is empty: {resolved}")
    return result


def _resolve_internal_path(root: Path, value: Any, *, label: str) -> Path:
    if not isinstance(value, str):
        raise CapabilityImprovementError(f"{label} path must be a string")
    resolved_root = root.expanduser().resolve()
    relative = safe_relative_path(value)
    lexical = resolved_root / relative
    resolved = lexical.resolve()
    if resolved != lexical or (
        resolved != resolved_root and not resolved.is_relative_to(resolved_root)
    ):
        raise CapabilityImprovementError(
            f"{label} path escapes through a symlink: {value}"
        )
    return resolved


def _validate_prior_experiment(root: Path) -> dict[str, Any]:
    from .experiment import validate_superseded_experiment_manifest

    return validate_superseded_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )


def _validate_prior_unchanged(root: Path, prior: Mapping[str, Any]) -> None:
    current = _validate_prior_experiment(root)
    if current["experiment_digest"] != prior["experiment_digest"]:
        raise CapabilityImprovementError(
            "predecessor experiment changed while migration was staged"
        )


def _prior_split_digest(root: Path) -> str:
    split = _load_object(root / "design" / "frozen_split.json", "prior frozen split")
    value = split.get("split_digest")
    if not isinstance(value, str) or len(value) != 64:
        raise CapabilityImprovementError("prior frozen split lacks its semantic digest")
    return value


def _migration_transaction_paths(root: Path) -> tuple[Path, Path]:
    return (
        root.parent / f".{root.name}.single-branch-migration-staging",
        root.parent / f".{root.name}.single-branch-migration-backup",
    )


def _write_migration_journal(
    path: Path,
    *,
    root: Path,
    stage: Path,
    backup: Path,
    prior_digest: str,
    new_digest: str | None,
    phase: str,
) -> None:
    payload = {
        "schema_version": MIGRATION_JOURNAL_SCHEMA_VERSION,
        "experiment_root": root.as_posix(),
        "stage_path": stage.as_posix(),
        "backup_path": backup.as_posix(),
        "prior_experiment_digest": prior_digest,
        "new_experiment_digest": new_digest,
        "phase": phase,
    }
    write_json_atomic(path, with_digest(payload, "journal_digest"), mode=0o600)


def _load_migration_journal(path: Path, *, root: Path) -> dict[str, Any]:
    value = _load_object(path, "single-branch migration journal")
    validate_digest(value, "journal_digest")
    stage, backup = _migration_transaction_paths(root)
    required = {
        "schema_version",
        "experiment_root",
        "stage_path",
        "backup_path",
        "prior_experiment_digest",
        "new_experiment_digest",
        "phase",
        "journal_digest",
    }
    if set(value) != required:
        raise CapabilityImprovementError(
            "single-branch migration journal fields differ"
        )
    if (
        value["schema_version"] != MIGRATION_JOURNAL_SCHEMA_VERSION
        or value["experiment_root"] != root.as_posix()
        or value["stage_path"] != stage.as_posix()
        or value["backup_path"] != backup.as_posix()
        or value["phase"] not in {"staging", "prepared"}
    ):
        raise CapabilityImprovementError(
            "single-branch migration journal identity differs"
        )
    return value


def _recover_migration_transaction(root: Path) -> dict[str, Any] | None:
    journal = single_branch_migration_journal_path(root)
    stage, backup = _migration_transaction_paths(root)
    if not journal.exists():
        if stage.exists() or backup.exists():
            raise CapabilityImprovementError(
                "untracked single-branch migration staging or backup path exists"
            )
        return None
    transaction = _load_migration_journal(journal, root=root)
    new_digest = transaction["new_experiment_digest"]
    if root.is_dir() and isinstance(new_digest, str):
        try:
            from .experiment import validate_experiment_manifest

            active = validate_experiment_manifest(
                root / "design" / "experiment_manifest.json",
                experiment_root=root,
            )
        except (CapabilityImprovementError, OSError):
            active = None
        if active is not None and active["experiment_digest"] == new_digest:
            if stage.exists():
                _remove_transaction_tree(stage, root=root)
            if backup.exists():
                _remove_transaction_tree(backup, root=root)
            journal.unlink()
            migration = validate_single_branch_migration(
                root / ACTIVE_MIGRATION_PATH,
                experiment_root=root,
            )
            return _migration_result(
                root=root,
                manifest=active,
                archive_relative=Path(migration["archived_root"]),
                status="recovered_migrated_to_clean_c0",
            )

    if backup.exists():
        if root.exists():
            _remove_transaction_tree(root, root=root, allow_active_root=True)
        backup.replace(root)
    elif not root.is_dir():
        raise CapabilityImprovementError(
            "cannot recover migration: active root and backup are both missing"
        )
    prior = _validate_prior_experiment(root)
    if prior["experiment_digest"] != transaction["prior_experiment_digest"]:
        raise CapabilityImprovementError(
            "recovered predecessor differs from the migration journal"
        )
    if stage.exists():
        _remove_transaction_tree(stage, root=root)
    journal.unlink()
    return None


def _existing_migration_result(root: Path) -> dict[str, Any] | None:
    migration_path = root / ACTIVE_MIGRATION_PATH
    if not migration_path.is_file():
        return None
    from .experiment import validate_experiment_manifest

    manifest = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    migration = validate_single_branch_migration(
        migration_path,
        experiment_root=root,
    )
    return _migration_result(
        root=root,
        manifest=manifest,
        archive_relative=Path(migration["archived_root"]),
        status="already_migrated_to_clean_c0",
    )


def _migration_result(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    archive_relative: Path,
    status: str,
    reported_root: Path | None = None,
) -> dict[str, Any]:
    output_root = reported_root or root
    split = _load_object(root / "design" / "frozen_split.json", "active frozen split")
    return {
        "status": status,
        "experiment_digest": manifest["experiment_digest"],
        "old_split_digest": (
            _load_object(
                root / archive_relative / "design" / "frozen_split.json",
                "archived frozen split",
            )["split_digest"]
        ),
        "new_split_digest": split["split_digest"],
        "training_protocol_count": 25,
        "validation_protocol_count": 5,
        "final_test_protocol_count": 10,
        "validation_trial_count": 30,
        "final_test_trial_count": 60,
        "checkpoint_ids": ["C0", "C5", "C10", "C15", "C20", "C25"],
        "active_checkpoint_ids": _active_checkpoint_ids(root / "checkpoints"),
        "superseded_archive_path": (output_root / archive_relative).as_posix(),
        "harbor_run_started": False,
    }


def _remove_transaction_tree(
    path: Path, *, root: Path, allow_active_root: bool = False
) -> None:
    resolved = path.expanduser().resolve()
    parent = root.expanduser().resolve().parent
    active_root = root.expanduser().resolve()
    if resolved.parent != parent or (resolved == active_root and not allow_active_root):
        raise CapabilityImprovementError(
            f"refusing to remove unsafe migration transaction path: {resolved}"
        )
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise CapabilityImprovementError(
                f"invalid migration transaction tree: {resolved}"
            )
        thaw_tree(resolved)
        shutil.rmtree(resolved)


def _remove_staged_child_tree(path: Path, *, stage: Path) -> None:
    resolved = path.expanduser().resolve()
    stage_root = stage.expanduser().resolve()
    if resolved.parent != stage_root or resolved == stage_root:
        raise CapabilityImprovementError(
            f"refusing to remove unsafe staged child tree: {resolved}"
        )
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise CapabilityImprovementError(f"invalid staged child tree: {resolved}")
        thaw_tree(resolved)
        shutil.rmtree(resolved)


def _pack_record(root: Path, *, path: str) -> dict[str, Any]:
    pack = validate_capability_pack(root)
    digest, count = tree_digest(root)
    return {
        "path": path,
        "pack_digest": pack["pack_digest"],
        "manifest_sha256": sha256_file(root / "manifest.json"),
        "tree_digest": digest,
        "file_count": count,
    }


def _validate_pack_record(root: Path, record: Mapping[str, Any]) -> None:
    pack = validate_capability_pack(root)
    digest, count = tree_digest(root)
    if (
        pack["pack_digest"] != record["pack_digest"]
        or sha256_file(root / "manifest.json") != record["manifest_sha256"]
        or digest != record["tree_digest"]
        or count != record["file_count"]
    ):
        raise CapabilityImprovementError(
            f"migration pack record differs from the filesystem: {record['path']}"
        )


def _active_checkpoint_ids(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_dir():
            raise CapabilityImprovementError(f"invalid checkpoint entry: {path}")
        if not (path / "checkpoint.json").is_file():
            raise CapabilityImprovementError(f"checkpoint record is missing: {path}")
        result.append(path.name)
    return sorted(result, key=lambda value: (value[0], int(value[1:])))


def _validate_active_cumulative_lineage(root: Path) -> None:
    """Reject predecessor branches while allowing post-migration C learning.

    The immutable migration record proves how C0 was created.  It must remain
    valid after the experiment legitimately adds C5--C25 and B1--B5; requiring
    the whole active tree to stay frozen at the migration instant would make the
    first cumulative round impossible.
    """

    checkpoint_labels = ["C0", "C5", "C10", "C15", "C20", "C25"]
    checkpoint_ids = _active_checkpoint_ids(root / "checkpoints")
    if not checkpoint_ids or checkpoint_ids != checkpoint_labels[: len(checkpoint_ids)]:
        raise CapabilityImprovementError(
            "active checkpoints must be a contiguous C0-to-C25 prefix"
        )
    if (root / "packs").exists():
        raise CapabilityImprovementError(
            "superseded top-level capability packs remain active after migration"
        )
    rounds = root / "rounds"
    if not rounds.exists():
        return
    if rounds.is_symlink() or not rounds.is_dir():
        raise CapabilityImprovementError("active cumulative rounds root is invalid")
    batch_labels = ["B1", "B2", "B3", "B4", "B5"]
    observed_batches: list[str] = []
    for batch_path in sorted(
        rounds.iterdir(), key=lambda path: int(path.name[1:]) if path.name[1:].isdigit() else 999
    ):
        if (
            batch_path.is_symlink()
            or not batch_path.is_dir()
            or batch_path.name not in batch_labels
        ):
            raise CapabilityImprovementError(
                f"invalid active cumulative round: {batch_path}"
            )
        children = list(batch_path.iterdir())
        if (
            len(children) != 1
            or children[0].name != "cumulative"
            or children[0].is_symlink()
            or not children[0].is_dir()
        ):
            raise CapabilityImprovementError(
                f"active round contains a superseded or non-cumulative branch: {batch_path}"
            )
        observed_batches.append(batch_path.name)
    if observed_batches != batch_labels[: len(observed_batches)] or len(
        observed_batches
    ) > len(checkpoint_ids):
        raise CapabilityImprovementError(
            "active cumulative rounds are not contiguous with frozen checkpoints"
        )


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            f"cannot read {label} {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise CapabilityImprovementError(f"{label} must be a JSON object")
    return value
