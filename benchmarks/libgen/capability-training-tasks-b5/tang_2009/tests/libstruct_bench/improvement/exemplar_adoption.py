from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from libstruct_bench.audit.artifacts import (
    sha256_file,
    validate_document,
    write_json_atomic,
)

from .artifacts import (
    CapabilityImprovementError,
    improvement_schema_root,
    safe_relative_path,
    validate_capability_pack,
    validate_digest,
    with_digest,
)
from .exemplar_governance import (
    PUBLIC_STATEMENT,
    build_exemplar_memory_adoption,
    build_exemplar_projection_policy,
)
from .exemplar_memory import (
    create_empty_exemplar_memory,
    ensure_exemplar_identity_map,
)
from .governance import tree_digest
from .lineage import CHECKPOINT_LABELS
from .mutation_lock import (
    exemplar_adoption_journal_path,
    experiment_mutation_lock,
    single_branch_migration_journal_path,
    split_freeze_journal_path,
)


ADOPTION_JOURNAL_SCHEMA_VERSION = "libstruct.libgen_exemplar_memory_adoption_journal.v1"


def adopt_exemplar_memory(
    *,
    experiment_root: Path,
    recorded_at: str,
    authorize_adoption: bool = False,
) -> dict[str, Any]:
    """Preflight or atomically add empty, queryable exemplar memory to clean C0.

    This is a narrow one-time bridge for an already-migrated cumulative C0. It
    never evaluates a protocol, reveals ground truth, or changes the procedural
    capability pack.
    """

    root = experiment_root.expanduser().resolve()
    with experiment_mutation_lock(root, operation="adopt exemplar memory at C0"):
        journal = exemplar_adoption_journal_path(root)
        if journal.exists() and not authorize_adoption:
            raise CapabilityImprovementError(
                "an interrupted exemplar-memory adoption requires an authorized "
                "rerun for recovery"
            )
        if authorize_adoption:
            recovered = _recover_adoption(root)
            if recovered is not None:
                return recovered
        elif any(path.exists() for path in _transaction_paths(root)):
            raise CapabilityImprovementError(
                "untracked exemplar-memory adoption staging or backup path exists"
            )
        existing = _existing_adoption(root)
        if existing is not None:
            return existing
        if split_freeze_journal_path(root).exists() or (
            single_branch_migration_journal_path(root).exists()
        ):
            raise CapabilityImprovementError(
                "another interrupted experiment transaction must be recovered first"
            )
        prior = _validate_adoptable_c0(root)
        archive_relative = Path(
            f"history/superseded/exemplar-memory-{prior['experiment_digest'][:16]}"
        )
        if not authorize_adoption:
            scratch_container = Path(
                tempfile.mkdtemp(
                    prefix=f".{root.name}.exemplar-adoption-preflight-",
                    dir=root.parent,
                )
            )
            scratch = scratch_container / "experiment"
            try:
                manifest = _stage_adoption(
                    prior_root=root,
                    stage=scratch,
                    prior=prior,
                    archive_relative=archive_relative,
                    recorded_at=recorded_at,
                )
                return _result(
                    root=scratch,
                    reported_root=root,
                    manifest=manifest,
                    prior=prior,
                    status="ready_to_adopt_exemplar_memory",
                )
            finally:
                _remove_tree(scratch_container, protected_root=root)

        stage, backup = _transaction_paths(root)
        _write_journal(
            journal,
            root=root,
            stage=stage,
            backup=backup,
            prior_digest=prior["experiment_digest"],
            new_digest=None,
            phase="staging",
        )
        try:
            manifest = _stage_adoption(
                prior_root=root,
                stage=stage,
                prior=prior,
                archive_relative=archive_relative,
                recorded_at=recorded_at,
            )
            _validate_prior_snapshot(root, prior)
            _write_journal(
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
                    "installed exemplar-memory experiment digest changed"
                )
            _remove_tree(backup, protected_root=root)
            journal.unlink()
            return _result(
                root=root,
                reported_root=root,
                manifest=active,
                prior=prior,
                status="exemplar_memory_adopted",
            )
        except BaseException as error:
            try:
                if backup.exists():
                    if root.exists():
                        _remove_tree(root, protected_root=root, allow_root=True)
                    backup.replace(root)
                if stage.exists():
                    _remove_tree(stage, protected_root=root)
                if journal.exists():
                    journal.unlink()
            except BaseException as recovery_error:
                raise CapabilityImprovementError(
                    "exemplar-memory adoption failed and rollback also failed: "
                    f"{recovery_error}"
                ) from error
            raise


def _stage_adoption(
    *,
    prior_root: Path,
    stage: Path,
    prior: Mapping[str, Any],
    archive_relative: Path,
    recorded_at: str,
) -> dict[str, Any]:
    if stage.exists():
        raise CapabilityImprovementError(f"adoption stage already exists: {stage}")
    _hardlink_clone(prior_root, stage)
    for relative in (
        Path("design"),
        Path("checkpoints"),
        Path("history"),
        Path("history/superseded"),
    ):
        os.chmod(stage / relative, 0o755)

    archive = stage / archive_relative
    if archive.exists():
        raise CapabilityImprovementError(
            f"exemplar-memory adoption archive already exists: {archive_relative}"
        )
    (archive / "design").mkdir(parents=True)
    (archive / "checkpoints" / "C0").mkdir(parents=True)
    archived_paths = (
        archive / "design" / "experiment_manifest.json",
        archive / "checkpoints" / "C0" / "checkpoint.json",
        archive / "checkpoints" / "C0" / "runtime.json",
    )
    source_paths = (
        stage / "design" / "experiment_manifest.json",
        stage / "checkpoints" / "C0" / "checkpoint.json",
        stage / "checkpoints" / "C0" / "runtime.json",
    )
    for source, destination in zip(source_paths, archived_paths, strict=True):
        shutil.copy2(source, destination)
        os.chmod(destination, 0o444)

    prior_c0 = stage / ".prior-c0-source"
    (stage / "checkpoints" / "C0").replace(prior_c0)
    identity_map = ensure_exemplar_identity_map(
        experiment_root=stage,
        split_digest=prior["split_digest"],
    )
    temporary_memory = stage / ".clean-c0-memory"
    create_empty_exemplar_memory(
        memory_root=temporary_memory,
        identity_map=identity_map,
    )
    policy = build_exemplar_projection_policy(
        experiment_root=stage,
        frozen_split_path=stage / "design" / "frozen_split.json",
        identity_map_path=stage / "private" / "exemplar_identity_map.json",
        created_at=recorded_at,
    )
    write_json_atomic(
        stage / "design" / "exemplar_projection_policy.json",
        policy,
        mode=0o444,
    )
    adoption = build_exemplar_memory_adoption(
        experiment_root=stage,
        prior_experiment_manifest_path=archived_paths[0],
        archive_root=archive_relative,
        archived_artifact_paths=archived_paths,
        projection_policy=policy,
        identity_map_path=stage / "private" / "exemplar_identity_map.json",
        c0_memory_root=temporary_memory,
        prior_c0_pack_root=prior_c0 / "pack",
        new_c0_pack_root=prior_c0 / "pack",
        adopted_at=recorded_at,
        mode="adopted_into_clean_c0",
    )
    write_json_atomic(
        stage / "design" / "exemplar_memory_adoption.json",
        adoption,
        mode=0o444,
    )

    payload = {
        key: value
        for key, value in prior["manifest"].items()
        if key != "experiment_digest"
    }
    payload["memory_model"] = _memory_model(
        stage=stage,
        policy=policy,
        adoption=adoption,
    )
    manifest = with_digest(payload, "experiment_digest")
    validate_document(
        manifest,
        improvement_schema_root() / "experiment_manifest.schema.json",
        label="memory-aware cumulative capability experiment",
    )
    write_json_atomic(
        stage / "design" / "experiment_manifest.json",
        manifest,
        mode=0o444,
    )

    from .workflow import freeze_baseline_checkpoint

    freeze_baseline_checkpoint(
        experiment_digest=manifest["experiment_digest"],
        source_pack_root=prior_c0 / "pack",
        source_memory_root=temporary_memory,
        output_dir=stage / "checkpoints" / "C0",
        created_at=recorded_at,
    )
    _remove_tree(prior_c0, protected_root=stage)
    _remove_tree(temporary_memory, protected_root=stage)
    _freeze_new_archive(archive)

    from .experiment import validate_experiment_manifest

    validated = validate_experiment_manifest(
        stage / "design" / "experiment_manifest.json",
        experiment_root=stage,
    )
    if validated["initial_pack"]["pack_digest"] != prior["pack_digest"]:
        raise CapabilityImprovementError(
            "exemplar-memory adoption changed the procedural pack digest"
        )
    return validated


def _validate_adoptable_c0(root: Path) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise CapabilityImprovementError(
            f"experiment root is missing or unsafe: {root}"
        )
    manifest_path = root / "design" / "experiment_manifest.json"
    manifest = _load_digest_object(manifest_path, "experiment_digest")
    if (
        "memory_model" in manifest
        or (root / "design" / "exemplar_memory_adoption.json").exists()
    ):
        raise CapabilityImprovementError(
            "experiment already has exemplar-memory governance but failed validation"
        )
    checkpoint_ids = sorted(
        path.name for path in (root / "checkpoints").iterdir() if path.is_dir()
    )
    if checkpoint_ids != ["C0"]:
        raise CapabilityImprovementError(
            "exemplar memory may be adopted only before the first training batch"
        )
    for forbidden in (
        root / "rounds",
        root / "final",
        root / "design" / "final_lock.json",
        root / "design" / "transfer_panel_authorization.json",
    ):
        if forbidden.exists():
            raise CapabilityImprovementError(
                f"exemplar memory must be adopted before learning or replay: {forbidden}"
            )
    validation_root = root / "validation"
    if validation_root.exists() and any(validation_root.rglob("*")):
        raise CapabilityImprovementError(
            "exemplar memory must be adopted before validation runs"
        )
    if (root / "private" / "exemplar_identity_map.json").exists() or (
        root / "checkpoints" / "C0" / "memory"
    ).exists():
        raise CapabilityImprovementError(
            "partial exemplar-memory state exists without an adoption record"
        )
    c0_root = root / "checkpoints" / "C0"
    checkpoint = _load_digest_object(c0_root / "checkpoint.json", "checkpoint_digest")
    runtime = _load_digest_object(c0_root / "runtime.json", "runtime_digest")
    pack = validate_capability_pack(c0_root / "pack")
    if (
        checkpoint.get("checkpoint_id") != "C0"
        or checkpoint.get("protocol_count") != 0
        or checkpoint.get("experiment_digest") != manifest["experiment_digest"]
        or checkpoint.get("pack_digest") != pack["pack_digest"]
        or checkpoint.get("pack_manifest_sha256")
        != sha256_file(c0_root / "pack" / "manifest.json")
        or checkpoint.get("runtime_manifest_sha256")
        != sha256_file(c0_root / "runtime.json")
        or runtime.get("checkpoint_id") != "C0"
        or runtime.get("pack_digest") != pack["pack_digest"]
    ):
        raise CapabilityImprovementError(
            "pre-adoption C0 checkpoint/runtime/pack binding is invalid"
        )
    split = _load_digest_object(
        root / "design" / "frozen_split.json", "split_manifest_digest"
    )
    if split.get("status") != "active":
        raise CapabilityImprovementError(
            "exemplar memory requires an active frozen split"
        )
    from .single_branch_migration import validate_single_branch_migration

    validate_single_branch_migration(
        root / "design" / "single_branch_migration.json",
        experiment_root=root,
    )
    pack_tree_digest, pack_file_count = tree_digest(c0_root / "pack")
    return {
        "manifest": manifest,
        "experiment_digest": manifest["experiment_digest"],
        "manifest_sha256": sha256_file(manifest_path),
        "checkpoint_sha256": sha256_file(c0_root / "checkpoint.json"),
        "runtime_sha256": sha256_file(c0_root / "runtime.json"),
        "pack_digest": pack["pack_digest"],
        "pack_tree_digest": pack_tree_digest,
        "pack_file_count": pack_file_count,
        "split_digest": split["split_digest"],
    }


def _validate_prior_snapshot(root: Path, prior: Mapping[str, Any]) -> None:
    current = _validate_adoptable_c0(root)
    for key in (
        "experiment_digest",
        "manifest_sha256",
        "checkpoint_sha256",
        "runtime_sha256",
        "pack_digest",
        "pack_tree_digest",
        "pack_file_count",
        "split_digest",
    ):
        if current[key] != prior[key]:
            raise CapabilityImprovementError(
                f"clean C0 changed while exemplar-memory adoption was staged: {key}"
            )


def _memory_model(
    *,
    stage: Path,
    policy: Mapping[str, Any],
    adoption: Mapping[str, Any],
) -> dict[str, Any]:
    def reference(
        relative: str,
        document: Mapping[str, Any],
        digest_field: str,
    ) -> dict[str, str]:
        path = stage / safe_relative_path(relative)
        return {
            "path": relative,
            "sha256": sha256_file(path),
            "digest": str(document[digest_field]),
        }

    return {
        "procedural": {
            "checkpoint_path_template": "checkpoints/{checkpoint_id}/pack",
            "contents": [
                "instructions",
                "deterministic_scripts",
                "synthetic_tests",
            ],
            "cumulative": True,
            "maximum_proposed_per_batch": 2,
            "maximum_accepted_per_batch": 1,
            "synthetic_regression_required": True,
        },
        "exemplar": {
            "checkpoint_path_template": "checkpoints/{checkpoint_id}/memory",
            "classification": "prediction_shaped_approved_training_exemplars",
            "cumulative": True,
            "policy": reference(
                "design/exemplar_projection_policy.json",
                policy,
                "policy_digest",
            ),
            "adoption": reference(
                "design/exemplar_memory_adoption.json",
                adoption,
                "adoption_digest",
            ),
            "identity_map_public_commitment_sha256": policy["identity_map"][
                "public_commitment_sha256"
            ],
            "checkpoint_exemplar_counts": {
                "C0": 0,
                "C5": 5,
                "C10": 10,
                "C15": 15,
                "C20": 20,
                "C25": 25,
            },
            "agent_access": (
                "deterministic_query_returns_at_most_three_relevant_subgraphs"
            ),
        },
        "public_statement": PUBLIC_STATEMENT,
    }


def _existing_adoption(root: Path) -> dict[str, Any] | None:
    adoption_path = root / "design" / "exemplar_memory_adoption.json"
    if not adoption_path.exists():
        return None
    from .experiment import validate_experiment_manifest

    manifest = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    adoption = _load_digest_object(adoption_path, "adoption_digest")
    prior_candidates = [
        item
        for item in adoption["archived_artifacts"]
        if item["sha256"] == adoption["prior_experiment_manifest_sha256"]
    ]
    if len(prior_candidates) != 1:
        raise CapabilityImprovementError(
            "adoption record lacks one exact prior experiment manifest"
        )
    prior = {
        "experiment_digest": _load_digest_object(
            root / safe_relative_path(prior_candidates[0]["path"]),
            "experiment_digest",
        )["experiment_digest"]
    }
    return _result(
        root=root,
        reported_root=root,
        manifest=manifest,
        prior=prior,
        status="already_adopted_exemplar_memory",
    )


def _result(
    *,
    root: Path,
    reported_root: Path,
    manifest: Mapping[str, Any],
    prior: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    checkpoint = _load_digest_object(
        root / "checkpoints" / "C0" / "checkpoint.json", "checkpoint_digest"
    )
    memory = checkpoint["exemplar_memory"]
    return {
        "status": status,
        "old_experiment_digest": prior["experiment_digest"],
        "new_experiment_digest": manifest["experiment_digest"],
        "experiment_manifest_path": (
            reported_root / "design" / "experiment_manifest.json"
        ).as_posix(),
        "projection_policy_path": (
            reported_root / "design" / "exemplar_projection_policy.json"
        ).as_posix(),
        "adoption_path": (
            reported_root / "design" / "exemplar_memory_adoption.json"
        ).as_posix(),
        "identity_map_path": (
            reported_root / "private" / "exemplar_identity_map.json"
        ).as_posix(),
        "checkpoint_id": "C0",
        "procedural_pack_digest": checkpoint["pack_digest"],
        "exemplar_count": memory["exemplar_count"],
        "memory_digest": memory["memory_digest"],
        "training_batch_processed": False,
        "harbor_run_started": False,
        "validation_trial_count": 30,
        "final_test_trial_count": 60,
        "checkpoint_labels": list(CHECKPOINT_LABELS),
    }


def _hardlink_clone(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve()
    if source.is_symlink() or not source.is_dir():
        raise CapabilityImprovementError(f"cannot clone unsafe experiment: {source}")
    destination.mkdir(parents=True)
    directory_modes: list[tuple[Path, int]] = [
        (destination, source.stat().st_mode & 0o777)
    ]
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"experiment contains a forbidden symlink: {path}"
            )
        if path.is_dir():
            target.mkdir(mode=0o755)
            directory_modes.append((target, path.stat().st_mode & 0o777))
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(path, target)
        else:
            raise CapabilityImprovementError(
                f"experiment contains an unsupported filesystem entry: {path}"
            )
    for directory, mode in reversed(directory_modes):
        os.chmod(directory, mode)


def _freeze_new_archive(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            os.chmod(path, 0o555)
        elif path.is_file():
            os.chmod(path, 0o444)
    os.chmod(root, 0o555)


def _remove_tree(
    path: Path,
    *,
    protected_root: Path,
    allow_root: bool = False,
) -> None:
    resolved = path.expanduser().resolve()
    protected = protected_root.expanduser().resolve()
    if resolved == protected and not allow_root:
        raise CapabilityImprovementError(f"refusing to remove active root: {resolved}")
    if resolved == resolved.parent or (
        resolved != protected
        and resolved.parent != protected.parent
        and not resolved.is_relative_to(protected)
    ):
        raise CapabilityImprovementError(f"refusing to remove unsafe tree: {resolved}")
    if not resolved.exists():
        return
    if resolved.is_symlink() or not resolved.is_dir():
        raise CapabilityImprovementError(f"refusing to remove unsafe tree: {resolved}")
    for directory in [resolved, *[p for p in resolved.rglob("*") if p.is_dir()]]:
        os.chmod(directory, 0o755)
    shutil.rmtree(resolved)


def _transaction_paths(root: Path) -> tuple[Path, Path]:
    return (
        root.parent / f".{root.name}.exemplar-adoption-staging",
        root.parent / f".{root.name}.exemplar-adoption-backup",
    )


def _write_journal(
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
        "schema_version": ADOPTION_JOURNAL_SCHEMA_VERSION,
        "experiment_root": root.as_posix(),
        "stage_path": stage.as_posix(),
        "backup_path": backup.as_posix(),
        "prior_experiment_digest": prior_digest,
        "new_experiment_digest": new_digest,
        "phase": phase,
    }
    write_json_atomic(path, with_digest(payload, "journal_digest"), mode=0o600)


def _recover_adoption(root: Path) -> dict[str, Any] | None:
    journal_path = exemplar_adoption_journal_path(root)
    stage, backup = _transaction_paths(root)
    if not journal_path.exists():
        if stage.exists() or backup.exists():
            raise CapabilityImprovementError(
                "untracked exemplar-memory adoption staging or backup exists"
            )
        return None
    journal = _load_digest_object(journal_path, "journal_digest")
    if (
        journal.get("schema_version") != ADOPTION_JOURNAL_SCHEMA_VERSION
        or journal.get("experiment_root") != root.as_posix()
        or journal.get("stage_path") != stage.as_posix()
        or journal.get("backup_path") != backup.as_posix()
        or journal.get("phase") not in {"staging", "prepared"}
    ):
        raise CapabilityImprovementError("exemplar-memory adoption journal differs")
    existing = None
    if root.is_dir():
        try:
            existing = _existing_adoption(root)
        except (CapabilityImprovementError, OSError, KeyError, ValueError):
            existing = None
    if existing is not None and existing["new_experiment_digest"] == journal.get(
        "new_experiment_digest"
    ):
        if stage.exists():
            _remove_tree(stage, protected_root=root)
        if backup.exists():
            _remove_tree(backup, protected_root=root)
        journal_path.unlink()
        existing["status"] = "recovered_exemplar_memory_adoption"
        return existing
    if backup.exists():
        if root.exists():
            _remove_tree(root, protected_root=root, allow_root=True)
        backup.replace(root)
    if stage.exists():
        _remove_tree(stage, protected_root=root)
    journal_path.unlink()
    return None


def _load_digest_object(path: Path, digest_field: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(f"cannot read {path}: {error}") from error
    if not isinstance(document, dict):
        raise CapabilityImprovementError(f"expected JSON object: {path}")
    validate_digest(document, digest_field)
    return document
