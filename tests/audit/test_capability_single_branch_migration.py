from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from libstruct_bench.improvement import validation as validation_module
from libstruct_bench.improvement import single_branch_migration as migration_module
from libstruct_bench.audit.artifacts import write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    copy_capability_pack,
    with_digest,
)
from libstruct_bench.improvement.single_branch_migration import (
    IMMUTABLE_ACTIVE_GOVERNANCE_PATHS,
    _assert_immutable_active_governance,
    _copy_preserved_history,
    build_single_branch_migration,
    validate_single_branch_migration,
)
from libstruct_bench.improvement.validation import validation_panel_commitment_digest


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPO_ROOT / "improvement" / "capability_pack"


def _predecessor(root: Path) -> tuple[dict[str, str], Path]:
    prior = {
        "experiment_digest": "a" * 64,
        "supersession": {"digest": "9" * 64},
    }
    write_json_atomic(root / "design" / "experiment_manifest.json", prior)
    write_json_atomic(root / "design" / "frozen_split.json", {"split_digest": "b" * 64})
    for checkpoint_id in ("A5", "A10"):
        write_json_atomic(
            root / "checkpoints" / checkpoint_id / "checkpoint.json",
            {"checkpoint_id": checkpoint_id},
        )
    copy_capability_pack(PACK_ROOT, root / "packs" / "S0", freeze=True)
    write_json_atomic(root / "rounds" / "B1" / "autonomous" / "proposal.json", {})
    write_json_atomic(root / "history" / "preserved.json", {"preserved": True})
    staged_c0 = root.parent / "staged-C0"
    copy_capability_pack(
        root / "packs" / "S0",
        staged_c0 / "pack",
        freeze=True,
    )
    write_json_atomic(staged_c0 / "checkpoint.json", {"checkpoint_id": "C0"})
    return prior, staged_c0


def _external_bindings(root: Path) -> dict:
    return {
        "source_root": (root.parent / "sources").resolve().as_posix(),
        "groundtruth_root": (root.parent / "groundtruth").resolve().as_posix(),
        "final_test_transfer_policy_digest": "1" * 64,
        "validation_packet_prior_relative_path": (
            "history/superseded/fixture/rounds/B2/autonomous/packet.json"
        ),
        "validation_packet_sha256": "2" * 64,
        "validation_packet_digest": "3" * 64,
        "validation_artifact_count": 20,
    }


def _validation_governance(
    *, forbidden_terms: list[str] | None = None
) -> tuple[dict, dict]:
    commitment = validation_panel_commitment_digest()
    policy = with_digest(
        {
            "validation_panel_commitment_sha256": commitment,
            "protected_files": [],
            "forbidden_terms": forbidden_terms or [],
            "forbidden_sequences": [],
            "forbidden_scaffolds": [],
        },
        "policy_digest",
    )
    audit = with_digest(
        {
            "validation_panel_commitment_sha256": commitment,
            "access_policy_digest": policy["policy_digest"],
            "learning_isolation": "pass",
        },
        "audit_digest",
    )
    return policy, audit


def test_migration_record_closes_archived_lineage_and_clean_c0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "v1"
    prior, staged_c0 = _predecessor(root)
    policy, audit = _validation_governance()
    monkeypatch.setattr(
        validation_module,
        "validate_validation_access_policy",
        lambda _path: policy,
    )
    monkeypatch.setattr(
        validation_module,
        "validate_validation_isolation_audit",
        lambda _path, *, validation_access_policy: audit,
    )
    monkeypatch.setattr(
        validation_module,
        "scan_validation_pack_leakage",
        lambda _path, _policy: [],
    )
    monkeypatch.setattr(
        migration_module,
        "_build_external_root_bindings",
        lambda **_kwargs: _external_bindings(root),
    )
    archive_relative = Path("history/superseded/single-branch-fixture")
    migration = build_single_branch_migration(
        experiment_root=root,
        prior_experiment=prior,
        prior_split_digest="b" * 64,
        new_split_digest="c" * 64,
        archive_relative=archive_relative,
        source_s0_root=root / "packs" / "S0",
        staged_c0_pack_root=staged_c0 / "pack",
        validation_access_policy=policy,
        validation_isolation_audit=audit,
        validation_panel_commitment_sha256=validation_panel_commitment_digest(),
        final_test_panel_commitment_sha256="e" * 64,
        external_root_bindings=_external_bindings(root),
        migrated_at="2026-08-22T12:00:00Z",
    )

    archive = root / archive_relative
    archive.mkdir(parents=True)
    for role in ("design", "checkpoints", "packs", "rounds"):
        (root / role).replace(archive / role)
    active_c0 = root / "checkpoints" / "C0"
    shutil.copytree(staged_c0, active_c0, copy_function=shutil.copy2)
    write_json_atomic(root / "design" / "single_branch_migration.json", migration)

    path = root / "design" / "single_branch_migration.json"
    assert validate_single_branch_migration(path, experiment_root=root) == migration

    write_json_atomic(root / "rounds" / "B1" / "cumulative" / "packet.json", {})
    write_json_atomic(
        root / "checkpoints" / "C5" / "checkpoint.json",
        {"checkpoint_id": "C5"},
    )
    assert validate_single_branch_migration(path, experiment_root=root) == migration

    write_json_atomic(root / "rounds" / "B1" / "autonomous" / "leftover.json", {})
    with pytest.raises(CapabilityImprovementError, match="non-cumulative branch"):
        validate_single_branch_migration(path, experiment_root=root)
    shutil.rmtree(root / "rounds" / "B1" / "autonomous")

    write_json_atomic(
        root / "checkpoints" / "A5" / "checkpoint.json",
        {"checkpoint_id": "A5"},
    )
    with pytest.raises(CapabilityImprovementError, match="contiguous C0-to-C25"):
        validate_single_branch_migration(path, experiment_root=root)
    shutil.rmtree(root / "checkpoints" / "A5")

    preserved = root / "history" / "preserved.json"
    original = preserved.read_bytes()
    preserved.chmod(0o600)
    preserved.write_text('{"preserved":false}\n', encoding="utf-8")
    with pytest.raises(
        CapabilityImprovementError, match="pre-existing immutable history changed"
    ):
        validate_single_branch_migration(path, experiment_root=root)
    preserved.write_bytes(original)

    archived_manifest = archive / "design" / "experiment_manifest.json"
    archived_manifest.chmod(0o600)
    archived_manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(
        CapabilityImprovementError, match="archived tree changed after migration"
    ):
        validate_single_branch_migration(path, experiment_root=root)


def test_migration_rejects_nonidentical_c0(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    prior, staged_c0 = _predecessor(root)
    policy, audit = _validation_governance()
    staged_c0.chmod(0o700)
    staged_pack = staged_c0 / "pack"
    staged_pack.chmod(0o700)
    playbook = staged_pack / "PLAYBOOK.md"
    playbook.chmod(0o600)
    playbook.write_text(playbook.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(CapabilityImprovementError):
        build_single_branch_migration(
            experiment_root=root,
            prior_experiment=prior,
            prior_split_digest="b" * 64,
            new_split_digest="c" * 64,
            archive_relative=Path("history/superseded/single-branch-fixture"),
            source_s0_root=root / "packs" / "S0",
            staged_c0_pack_root=staged_c0 / "pack",
            validation_access_policy=policy,
            validation_isolation_audit=audit,
            validation_panel_commitment_sha256=validation_panel_commitment_digest(),
            final_test_panel_commitment_sha256="e" * 64,
            external_root_bindings=_external_bindings(root),
            migrated_at="2026-08-22T12:00:00Z",
        )


def test_migration_scans_clean_c0_for_validation_leakage(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    prior, staged_c0 = _predecessor(root)
    policy, audit = _validation_governance(forbidden_terms=["playbook"])

    with pytest.raises(CapabilityImprovementError, match="validation isolation"):
        build_single_branch_migration(
            experiment_root=root,
            prior_experiment=prior,
            prior_split_digest="b" * 64,
            new_split_digest="c" * 64,
            archive_relative=Path("history/superseded/single-branch-fixture"),
            source_s0_root=root / "packs" / "S0",
            staged_c0_pack_root=staged_c0 / "pack",
            validation_access_policy=policy,
            validation_isolation_audit=audit,
            validation_panel_commitment_sha256=validation_panel_commitment_digest(),
            final_test_panel_commitment_sha256="e" * 64,
            external_root_bindings=_external_bindings(root),
            migrated_at="2026-08-22T12:00:00Z",
        )


def test_copy_preserved_history_thaws_only_staged_copy_from_mode_0555(
    tmp_path: Path,
) -> None:
    source = tmp_path / "prior" / "history"
    write_json_atomic(source / "kept.json", {"kept": True}, mode=0o444)
    source.chmod(0o555)
    destination = tmp_path / "stage" / "history"

    record = _copy_preserved_history(
        source,
        destination,
        appended_archive=Path("history/superseded/new-lineage"),
    )

    assert record["file_count"] == 1
    assert source.stat().st_mode & 0o777 == 0o555
    assert destination.stat().st_mode & 0o777 == 0o755
    write_json_atomic(destination / "superseded" / "new-lineage" / "marker.json", {})


def test_active_migration_governance_requires_mode_0444(tmp_path: Path) -> None:
    root = tmp_path / "active"
    for relative in IMMUTABLE_ACTIVE_GOVERNANCE_PATHS:
        write_json_atomic(root / relative, {}, mode=0o444)
    _assert_immutable_active_governance(root)

    mutable = root / IMMUTABLE_ACTIVE_GOVERNANCE_PATHS[0]
    mutable.chmod(0o644)
    with pytest.raises(CapabilityImprovementError, match="not mode 0444"):
        _assert_immutable_active_governance(root)
