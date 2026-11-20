from __future__ import annotations

import copy
import fcntl
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    sha256_file,
    validate_document,
    write_json_atomic,
)

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
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
from .baselines import build_baseline_registry
from .experiment import (
    INITIAL_RETROSPECTIVE_TRANSFER_PANEL,
    ORIGINAL_PROTOCOLS,
    initialize_superseded_experiment,
    validate_experiment_manifest,
)
from .governance import (
    build_s0_provenance_audit,
    build_supersession_manifest,
    build_transfer_access_policy,
)


HISTORY_PREFIX = Path("history/superseded/original-partition")
UPDATE_DIRECTORIES = ("proposals", "decisions", "applications", "checkpoints")
RESTART_HISTORY_ROOT = Path("history/superseded/pre-deterministic-rewrite/root")
RESTART_SCHEMA_VERSION = "libstruct.libgen_capability_lineage_restart.v1"
RESTART_JOURNAL_SCHEMA_VERSION = (
    "libstruct.libgen_capability_lineage_restart_transaction.v1"
)


def restart_experiment_v1_in_place(
    *,
    experiment_root: Path,
    capability_pack_root: Path,
    retrospective_run_root: Path,
    source_root: Path,
    groundtruth_root: Path,
    baseline_run_roots: Sequence[Path],
    tasks_root: Path,
    recorded_at: str,
    agent_version: str,
    expected_prior_digest: str,
) -> dict[str, Any]:
    """Restart the sole active v1 lineage while preserving the old root intact.

    This is deliberately separate from schema compatibility.  The previous
    experiment tree becomes immutable, ineligible pilot history and the same
    public experiment path is rebuilt from the current S0 pack.  No old
    proposal, decision, application, checkpoint, or pack remains in an active
    index.
    """

    from .governance import (
        assert_capability_modification_open,
        build_s0_provenance_audit,
        build_transfer_access_policy,
    )

    root = experiment_root.expanduser().resolve()
    with _lineage_restart_lock(root):
        recovered = _recover_lineage_restart(root, expected_prior_digest)
        if recovered is not None:
            return recovered
        if not root.is_dir():
            raise CapabilityImprovementError(f"experiment v1 root is missing: {root}")
        assert_capability_modification_open(root)
        current_manifest_path = root / "design" / "experiment_manifest.json"
        current_manifest = _validate_prior_experiment_for_restart(
            current_manifest_path,
            experiment_root=root,
        )
        if current_manifest.get("experiment_id") != "libgen-capability-improvement-v1":
            raise CapabilityImprovementError(
                "refusing to restart an unexpected experiment"
            )
        if current_manifest["experiment_digest"] != expected_prior_digest:
            raise CapabilityImprovementError(
                "expected prior experiment digest does not match the active v1 lineage"
            )
        if (
            current_manifest.get("lineage_restart") is not None
            or (root / "design" / "lineage_restart.json").exists()
            or (root / RESTART_HISTORY_ROOT).exists()
        ):
            raise CapabilityImprovementError(
                "the sole in-place v1 lineage restart has already been performed"
            )

        pack = validate_capability_pack(capability_pack_root)
        pack_manifest_sha256 = sha256_file(
            capability_pack_root.expanduser().resolve() / "manifest.json"
        )
        archived_artifacts = _restart_artifact_inventory(root)
        checkpoint_ids = _active_checkpoint_ids(
            root,
            experiment_digest=current_manifest["experiment_digest"],
        )
        supersession = load_and_validate(
            root / "design" / "supersession_manifest.json",
            schema_filename="supersession_manifest.schema.json",
            digest_field="supersession_digest",
            label="supersession manifest",
        )
        _validate_supersession_history(root, supersession)
        rebased_supersession = _rebase_supersession(supersession)
        restart_payload = {
            "schema_version": RESTART_SCHEMA_VERSION,
            "restart_id": (
                f"deterministic-rewrite-{current_manifest['experiment_digest'][:16]}"
            ),
            "restarted_at": normalized_timestamp(recorded_at),
            "reason": (
                "replace_the_current_orchestration_in_place_with_"
                "deterministic_learning_controls"
            ),
            "prior_experiment_digest": current_manifest["experiment_digest"],
            "prior_experiment_manifest_sha256": sha256_file(current_manifest_path),
            "prior_active_checkpoint_ids": checkpoint_ids,
            "archived_root": RESTART_HISTORY_ROOT.as_posix(),
            "archived_artifact_count": len(archived_artifacts),
            "archived_artifacts": archived_artifacts,
            "history_eligibility": "immutable_pilot_history_only",
            "new_initial_pack_digest": pack["pack_digest"],
            "new_initial_pack_manifest_sha256": pack_manifest_sha256,
            "new_active_start": {
                "S0": "packs/S0",
                "A0": "packs/A0",
                "H0": "packs/H0",
                "byte_identical": True,
            },
            "supersession_rebase_root": RESTART_HISTORY_ROOT.as_posix(),
        }
        restart = with_digest(restart_payload, "restart_digest")
        validate_document(
            restart,
            improvement_schema_root() / "lineage_restart.schema.json",
            label="capability lineage restart",
        )

        panel_commitment = canonical_digest(
            {
                "set_id": "frozen-retrospective-transfer-panel-v1",
                "protocol_ids": list(INITIAL_RETROSPECTIVE_TRANSFER_PANEL),
            }
        )
        s0_audit = build_s0_provenance_audit(
            pack_root=capability_pack_root,
            original_protocol_ids=ORIGINAL_PROTOCOLS,
            audited_at=recorded_at,
        )
        if s0_audit["protocol_specific_content_detected"]:
            raise CapabilityImprovementError(
                "new S0 contains development-protocol identifiers"
            )
        registry = build_baseline_registry(
            panel_protocol_ids=INITIAL_RETROSPECTIVE_TRANSFER_PANEL,
            panel_commitment_sha256=panel_commitment,
            run_roots=baseline_run_roots,
            tasks_root=tasks_root,
            created_at=recorded_at,
        )
        policy = build_transfer_access_policy(
            panel_protocol_ids=INITIAL_RETROSPECTIVE_TRANSFER_PANEL,
            panel_commitment_sha256=panel_commitment,
            source_root=source_root,
            groundtruth_root=groundtruth_root,
            baseline_run_roots=baseline_run_roots,
        )

        stage, backup, journal = _restart_transaction_paths(root)
        if stage.exists() or backup.exists():
            raise CapabilityImprovementError(
                "untracked lineage-restart staging or backup path exists"
            )
        _write_restart_journal(
            journal,
            root=root,
            stage=stage,
            backup=backup,
            expected_prior_digest=expected_prior_digest,
            phase="staging",
            new_experiment_digest=None,
        )
        try:
            initialize_superseded_experiment(
                output_root=stage,
                capability_pack_root=capability_pack_root,
                retrospective_run_root=retrospective_run_root,
                private_groundtruth_root=groundtruth_root,
                s0_provenance_audit=s0_audit,
                transfer_access_policy=policy,
                baseline_registry=registry,
                supersession_manifest=rebased_supersession,
                created_at=recorded_at,
                agent_version=agent_version,
                experiment_id="libgen-capability-improvement-v1",
            )
            archive = stage / RESTART_HISTORY_ROOT
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root, archive, copy_function=shutil.copy2)
            if _restart_artifact_inventory(archive) != archived_artifacts:
                raise CapabilityImprovementError(
                    "staged lineage archive differs from the prior active tree"
                )
            freeze_tree(archive.parent)
            write_json_atomic(
                stage / "design" / "lineage_restart.json",
                restart,
                mode=0o400,
            )
            manifest = _attach_lineage_restart(stage, restart)
            validate_experiment_manifest(
                stage / "design" / "experiment_manifest.json",
                experiment_root=stage,
            )
            if _restart_artifact_inventory(root) != archived_artifacts:
                raise CapabilityImprovementError(
                    "active experiment changed while the restart was staged"
                )
            _write_restart_journal(
                journal,
                root=root,
                stage=stage,
                backup=backup,
                expected_prior_digest=expected_prior_digest,
                phase="prepared",
                new_experiment_digest=manifest["experiment_digest"],
            )
            _replace_path(root, backup)
            _replace_path(stage, root)
            validate_experiment_manifest(
                root / "design" / "experiment_manifest.json",
                experiment_root=root,
            )
            _remove_restart_tree(backup)
            journal.unlink()
            return _restart_result(root, status="restarted_in_place")
        except BaseException as error:
            try:
                recovered = _recover_lineage_restart(root, expected_prior_digest)
            except BaseException as recovery_error:
                raise CapabilityImprovementError(
                    "lineage restart failed and deterministic recovery also failed: "
                    f"{recovery_error}"
                ) from error
            if recovered is not None:
                return recovered
            raise


def validate_lineage_restart_history(
    experiment_root: Path,
    *,
    restart: dict[str, Any] | None = None,
    active_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the closed prior-archive -> restart -> active-manifest chain."""

    root = experiment_root.expanduser().resolve()
    restart_path = root / "design" / "lineage_restart.json"
    on_disk = _load_object(
        restart_path,
        "capability lineage restart",
    )
    if restart is not None and restart != on_disk:
        raise CapabilityImprovementError(
            "in-memory lineage restart differs from the active restart record"
        )
    document = on_disk
    validate_document(
        document,
        improvement_schema_root() / "lineage_restart.schema.json",
        label="capability lineage restart",
    )
    validate_digest(document, "restart_digest")
    manifest = (
        dict(active_manifest)
        if active_manifest is not None
        else _load_object(
            root / "design" / "experiment_manifest.json",
            "active capability experiment manifest",
        )
    )
    validate_document(
        manifest,
        improvement_schema_root() / "experiment_manifest.schema.json",
        label="active capability experiment manifest",
    )
    validate_digest(manifest, "experiment_digest")
    reference = manifest.get("lineage_restart")
    if not isinstance(reference, Mapping):
        raise CapabilityImprovementError(
            "active experiment manifest does not reference its lineage restart"
        )
    if reference["path"] != "design/lineage_restart.json":
        raise CapabilityImprovementError("active lineage restart path differs")
    if sha256_file(restart_path) != reference["sha256"]:
        raise CapabilityImprovementError("active lineage restart file hash differs")
    if document["restart_digest"] != reference["digest"]:
        raise CapabilityImprovementError("active lineage restart digest differs")
    if manifest["initial_pack"]["pack_digest"] != document["new_initial_pack_digest"]:
        raise CapabilityImprovementError("restart references another active S0 pack")
    if (
        manifest["initial_pack"]["manifest_sha256"]
        != document["new_initial_pack_manifest_sha256"]
    ):
        raise CapabilityImprovementError("restart references another S0 manifest")
    if manifest["initial_pack"]["references"] != {
        key: document["new_active_start"][key] for key in ("S0", "A0", "H0")
    }:
        raise CapabilityImprovementError("restart active-start references differ")
    archive = root / document["archived_root"]
    if not archive.is_dir():
        raise CapabilityImprovementError(
            f"capability lineage archive is missing: {archive}"
        )
    recorded = document["archived_artifacts"]
    if document["archived_artifact_count"] != len(recorded):
        raise CapabilityImprovementError("lineage restart artifact count differs")
    recorded_paths = [item["path"] for item in recorded]
    if len(recorded_paths) != len(set(recorded_paths)):
        raise CapabilityImprovementError("lineage restart contains duplicate paths")
    actual = _restart_artifact_inventory(archive)
    if actual != recorded:
        raise CapabilityImprovementError(
            "archived lineage inventory is not an exact closed-world match"
        )
    prior_manifest_path = archive / "design" / "experiment_manifest.json"
    if sha256_file(prior_manifest_path) != document["prior_experiment_manifest_sha256"]:
        raise CapabilityImprovementError("archived prior manifest file hash differs")
    prior_manifest = _validate_prior_experiment_for_restart(
        prior_manifest_path,
        experiment_root=archive,
    )
    if prior_manifest["experiment_digest"] != document["prior_experiment_digest"]:
        raise CapabilityImprovementError("archived prior experiment digest differs")
    checkpoint_ids = _active_checkpoint_ids(
        archive,
        experiment_digest=prior_manifest["experiment_digest"],
    )
    if checkpoint_ids != document["prior_active_checkpoint_ids"]:
        raise CapabilityImprovementError("archived checkpoint ID set differs")

    prior_supersession = load_and_validate(
        archive / prior_manifest["supersession"]["path"],
        schema_filename="supersession_manifest.schema.json",
        digest_field="supersession_digest",
        label="archived supersession manifest",
    )
    _validate_supersession_history(archive, prior_supersession)
    active_supersession = load_and_validate(
        root / manifest["supersession"]["path"],
        schema_filename="supersession_manifest.schema.json",
        digest_field="supersession_digest",
        label="rebased supersession manifest",
    )
    if active_supersession != _rebase_supersession(prior_supersession):
        raise CapabilityImprovementError(
            "active supersession manifest is not the deterministic archive rebase"
        )
    _validate_supersession_history(
        root,
        active_supersession,
        required_prefix=RESTART_HISTORY_ROOT,
    )
    return document


def revise_experiment_v1_in_place(
    *,
    experiment_root: Path,
    capability_pack_root: Path,
    retrospective_run_root: Path,
    source_root: Path,
    groundtruth_root: Path,
    baseline_run_roots: Sequence[Path],
    tasks_root: Path,
    recorded_at: str,
    agent_version: str,
) -> dict[str, Any]:
    """Atomically supersede the pre-update v1 partition without running a batch."""

    root = experiment_root.expanduser().resolve()
    if not root.is_dir():
        raise CapabilityImprovementError(f"experiment v1 root is missing: {root}")
    _assert_no_capability_updates(root)
    old_manifest_path = root / "design" / "experiment_manifest.json"
    old_manifest = _load_object(old_manifest_path, "superseded experiment manifest")
    validate_digest(old_manifest, "experiment_digest")
    if old_manifest.get("experiment_id") != "libgen-capability-improvement-v1":
        raise CapabilityImprovementError("refusing to revise an unexpected experiment")
    if "frozen_retrospective_transfer_panel" in old_manifest:
        return _refresh_revised_v1(
            root=root,
            current_manifest=old_manifest,
            capability_pack_root=capability_pack_root,
            retrospective_run_root=retrospective_run_root,
            source_root=source_root,
            groundtruth_root=groundtruth_root,
            baseline_run_roots=baseline_run_roots,
            tasks_root=tasks_root,
            recorded_at=recorded_at,
            agent_version=agent_version,
        )

    historical_sources = _historical_sources(root)
    original_artifacts = [
        {
            "historical_path": (HISTORY_PREFIX / relative).as_posix(),
            "sha256": sha256_file(path),
        }
        for relative, path in historical_sources
    ]
    superseded_packets = _superseded_packets(historical_sources)
    if not superseded_packets:
        raise CapabilityImprovementError(
            "no current packet contains a frozen transfer-panel protocol"
        )
    supersession = build_supersession_manifest(
        original_experiment_digest=old_manifest["experiment_digest"],
        original_artifacts=original_artifacts,
        superseded_packets=superseded_packets,
        recorded_at=recorded_at,
    )

    panel_commitment = canonical_digest(
        {
            "set_id": "frozen-retrospective-transfer-panel-v1",
            "protocol_ids": list(INITIAL_RETROSPECTIVE_TRANSFER_PANEL),
        }
    )
    s0_audit = build_s0_provenance_audit(
        pack_root=capability_pack_root,
        original_protocol_ids=ORIGINAL_PROTOCOLS,
        audited_at=recorded_at,
    )
    if s0_audit["protocol_specific_content_detected"]:
        raise CapabilityImprovementError(
            "S0 contains original-protocol identifiers; resolve provenance before revision"
        )
    registry = build_baseline_registry(
        panel_protocol_ids=INITIAL_RETROSPECTIVE_TRANSFER_PANEL,
        panel_commitment_sha256=panel_commitment,
        run_roots=baseline_run_roots,
        tasks_root=tasks_root,
        created_at=recorded_at,
    )
    policy = build_transfer_access_policy(
        panel_protocol_ids=INITIAL_RETROSPECTIVE_TRANSFER_PANEL,
        panel_commitment_sha256=panel_commitment,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        baseline_run_roots=baseline_run_roots,
    )

    build_root = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.revision-", dir=root.parent)
    )
    # The historical initializer requires a nonexistent destination.
    build_root.rmdir()
    backup = root.parent / f".{root.name}.superseded-backup"
    if backup.exists():
        raise CapabilityImprovementError(f"stale revision backup exists: {backup}")
    try:
        manifest = initialize_superseded_experiment(
            output_root=build_root,
            capability_pack_root=capability_pack_root,
            retrospective_run_root=retrospective_run_root,
            private_groundtruth_root=groundtruth_root,
            s0_provenance_audit=s0_audit,
            transfer_access_policy=policy,
            baseline_registry=registry,
            supersession_manifest=supersession,
            created_at=recorded_at,
            agent_version=agent_version,
            experiment_id="libgen-capability-improvement-v1",
        )
        _copy_history(historical_sources, build_root)
        write_json_atomic(
            build_root / HISTORY_PREFIX / "eligibility.json",
            {
                "status": "superseded_ineligible",
                "supersession_digest": supersession["supersession_digest"],
                "preserved_packet_hashes": [
                    item["file_sha256"] for item in superseded_packets
                ],
            },
            mode=0o444,
        )
        freeze_tree(build_root / HISTORY_PREFIX)
        _validate_history(build_root, supersession)
        validate_experiment_manifest(
            build_root / "design" / "experiment_manifest.json",
            experiment_root=build_root,
        )

        root.replace(backup)
        try:
            build_root.replace(root)
        except BaseException:
            backup.replace(root)
            raise
        try:
            thaw_tree(backup)
            shutil.rmtree(backup)
        except OSError as error:
            raise CapabilityImprovementError(
                f"revision succeeded but superseded backup cleanup failed: {backup}: {error}"
            ) from error
        return {
            "status": "revised_in_place",
            "experiment_root": root.as_posix(),
            "experiment_digest": manifest["experiment_digest"],
            "superseded_experiment_digest": old_manifest["experiment_digest"],
            "supersession_digest": supersession["supersession_digest"],
            "s0_audit_digest": s0_audit["audit_digest"],
            "transfer_access_policy_digest": policy["policy_digest"],
            "baseline_registry_digest": registry["registry_digest"],
            "superseded_packet_count": len(superseded_packets),
            "active_packet_count": 0,
            "batch_processing_performed": False,
            "model_runs_performed": False,
        }
    except BaseException:
        if build_root.exists():
            try:
                thaw_tree(build_root)
            except OSError:
                pass
            shutil.rmtree(build_root, ignore_errors=True)
        raise


def _assert_no_capability_updates(root: Path) -> None:
    unexpected = []
    for name in UPDATE_DIRECTORIES:
        directory = root / name
        if directory.exists() and any(directory.rglob("*")):
            unexpected.append(name)
    packs = root / "packs"
    if packs.is_dir():
        unexpected_packs = sorted(
            path.name
            for path in packs.iterdir()
            if path.is_dir() and path.name not in {"S0", "A0", "H0"}
        )
        unexpected.extend(f"packs/{name}" for name in unexpected_packs)
    if unexpected:
        raise CapabilityImprovementError(
            "v1 already contains capability updates: " + ", ".join(unexpected)
        )


def _refresh_revised_v1(
    *,
    root: Path,
    current_manifest: dict[str, Any],
    capability_pack_root: Path,
    retrospective_run_root: Path,
    source_root: Path,
    groundtruth_root: Path,
    baseline_run_roots: Sequence[Path],
    tasks_root: Path,
    recorded_at: str,
    agent_version: str,
) -> dict[str, Any]:
    """Refresh pre-update governance after schema/policy hardening, preserving history."""

    _assert_no_capability_updates(root)
    supersession = _load_object(
        root / "design" / "supersession_manifest.json",
        "supersession manifest",
    )
    validate_digest(supersession, "supersession_digest")
    history_source = root / "history"
    if not history_source.is_dir():
        raise CapabilityImprovementError("revised v1 is missing superseded history")
    panel_commitment = canonical_digest(
        {
            "set_id": "frozen-retrospective-transfer-panel-v1",
            "protocol_ids": list(INITIAL_RETROSPECTIVE_TRANSFER_PANEL),
        }
    )
    audit = build_s0_provenance_audit(
        pack_root=capability_pack_root,
        original_protocol_ids=ORIGINAL_PROTOCOLS,
        audited_at=recorded_at,
    )
    registry = build_baseline_registry(
        panel_protocol_ids=INITIAL_RETROSPECTIVE_TRANSFER_PANEL,
        panel_commitment_sha256=panel_commitment,
        run_roots=baseline_run_roots,
        tasks_root=tasks_root,
        created_at=recorded_at,
    )
    policy = build_transfer_access_policy(
        panel_protocol_ids=INITIAL_RETROSPECTIVE_TRANSFER_PANEL,
        panel_commitment_sha256=panel_commitment,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        baseline_run_roots=baseline_run_roots,
    )
    build_root = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.refresh-", dir=root.parent)
    )
    build_root.rmdir()
    backup = root.parent / f".{root.name}.refresh-backup"
    if backup.exists():
        raise CapabilityImprovementError(f"stale refresh backup exists: {backup}")
    try:
        manifest = initialize_superseded_experiment(
            output_root=build_root,
            capability_pack_root=capability_pack_root,
            retrospective_run_root=retrospective_run_root,
            private_groundtruth_root=groundtruth_root,
            s0_provenance_audit=audit,
            transfer_access_policy=policy,
            baseline_registry=registry,
            supersession_manifest=supersession,
            created_at=recorded_at,
            agent_version=agent_version,
            experiment_id="libgen-capability-improvement-v1",
        )
        shutil.copytree(
            history_source, build_root / "history", copy_function=shutil.copy2
        )
        freeze_tree(build_root / "history")
        _validate_history(build_root, supersession)
        validate_experiment_manifest(
            build_root / "design" / "experiment_manifest.json",
            experiment_root=build_root,
        )
        root.replace(backup)
        try:
            build_root.replace(root)
        except BaseException:
            backup.replace(root)
            raise
        thaw_tree(backup)
        shutil.rmtree(backup)
        return {
            "status": "revised_in_place",
            "experiment_root": root.as_posix(),
            "experiment_digest": manifest["experiment_digest"],
            "superseded_experiment_digest": supersession["original_experiment_digest"],
            "supersession_digest": supersession["supersession_digest"],
            "s0_audit_digest": audit["audit_digest"],
            "transfer_access_policy_digest": policy["policy_digest"],
            "baseline_registry_digest": registry["registry_digest"],
            "superseded_packet_count": len(supersession["superseded_packets"]),
            "active_packet_count": 0,
            "batch_processing_performed": False,
            "model_runs_performed": False,
            "governance_refreshed": True,
            "previous_revised_experiment_digest": current_manifest["experiment_digest"],
        }
    except BaseException:
        if build_root.exists():
            try:
                thaw_tree(build_root)
            except OSError:
                pass
            shutil.rmtree(build_root, ignore_errors=True)
        raise


def _historical_sources(root: Path) -> list[tuple[Path, Path]]:
    roots = (root / "design", root / "packets", root / "policies")
    result: list[tuple[Path, Path]] = []
    for directory in roots:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise CapabilityImprovementError(
                    f"superseded experiment contains a symlink: {path}"
                )
            if path.is_file():
                result.append((path.relative_to(root), path))
    if not result:
        raise CapabilityImprovementError("superseded experiment has no split artifacts")
    return result


def _superseded_packets(
    historical_sources: Sequence[tuple[Path, Path]],
) -> list[dict[str, Any]]:
    panel = set(INITIAL_RETROSPECTIVE_TRANSFER_PANEL)
    result = []
    for relative, path in historical_sources:
        if not relative.is_relative_to("packets") or path.suffix != ".json":
            continue
        value = _load_object(path, "superseded batch packet")
        validate_digest(value, "packet_digest")
        if not panel.intersection(value.get("protocol_ids", [])):
            continue
        result.append(
            {
                "historical_path": (HISTORY_PREFIX / relative).as_posix(),
                "file_sha256": sha256_file(path),
                "packet_digest": value["packet_digest"],
                "contains_transfer_panel_protocols": True,
                "eligibility": "superseded_ineligible",
            }
        )
    return result


def _copy_history(
    historical_sources: Sequence[tuple[Path, Path]],
    build_root: Path,
) -> None:
    for relative, source in historical_sources:
        destination = build_root / HISTORY_PREFIX / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha256_file(destination) != sha256_file(source):
            raise CapabilityImprovementError(
                f"superseded artifact changed while copying: {relative}"
            )


def _validate_history(root: Path, supersession: dict[str, Any]) -> None:
    for item in supersession["original_artifacts"]:
        path = root / item["historical_path"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise CapabilityImprovementError(
                f"superseded historical hash mismatch: {item['historical_path']}"
            )


def _restart_artifact_inventory(root: Path) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"active experiment contains a symlink: {path}"
            )
        if path.is_file():
            artifacts.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                }
            )
    if not artifacts:
        raise CapabilityImprovementError(
            "active experiment has no artifacts to archive"
        )
    return artifacts


def _validate_prior_experiment_for_restart(
    manifest_path: Path,
    *,
    experiment_root: Path,
) -> dict[str, Any]:
    """Validate a sealed pre-rewrite v1 tree only as archival input.

    The ordinary experiment validator intentionally requires the current pack
    contract.  A pilot tree predating the deterministic schemas/compiler/audit
    cannot satisfy that contract, but its bytes still need strict validation
    before archival.  This narrow gate validates the unchanged experiment
    schema and digest, governance references, closed-world pack manifests, and
    byte-identical S0/A0/H0 roots.  It is used only by the one-time restart and
    can never bless an archival pack as an active capability pack.
    """

    root = experiment_root.expanduser().resolve()
    document = load_and_validate(
        manifest_path,
        schema_filename="experiment_manifest.schema.json",
        digest_field="experiment_digest",
        label="prior capability experiment manifest",
    )
    if document.get("lineage_restart") is not None:
        raise CapabilityImprovementError(
            "the sole in-place v1 lineage restart has already been performed"
        )
    panel = document["frozen_retrospective_transfer_panel"]
    _validate_archival_partition(document["batches"], panel["protocol_ids"])
    references = document["initial_pack"]["references"]
    pack_roots = [root / references[label] for label in ("S0", "A0", "H0")]
    manifests = [_validate_archival_pack(pack_root) for pack_root in pack_roots]
    expected_digest = document["initial_pack"]["pack_digest"]
    if {item["pack_digest"] for item in manifests} != {expected_digest}:
        raise CapabilityImprovementError(
            "prior S0, A0, and H0 reference another archival pack digest"
        )
    if (
        sha256_file(pack_roots[0] / "manifest.json")
        != document["initial_pack"]["manifest_sha256"]
    ):
        raise CapabilityImprovementError("prior S0 manifest hash differs")
    if not (
        trees_byte_identical(pack_roots[0], pack_roots[1])
        and trees_byte_identical(pack_roots[0], pack_roots[2])
    ):
        raise CapabilityImprovementError(
            "prior S0, A0, and H0 packs are not byte-identical"
        )
    for reference, digest_field, schema in (
        (
            document["initial_pack"]["provenance_audit"],
            "audit_digest",
            "s0_provenance_audit.schema.json",
        ),
        (
            panel["access_policy"],
            "policy_digest",
            "transfer_access_policy.schema.json",
        ),
        (
            panel["baseline_registry"],
            "registry_digest",
            "baseline_registry.schema.json",
        ),
        (
            document["supersession"],
            "supersession_digest",
            "supersession_manifest.schema.json",
        ),
    ):
        path = root / safe_relative_path(reference["path"])
        if path.is_symlink() or sha256_file(path) != reference["sha256"]:
            raise CapabilityImprovementError(
                f"prior governance artifact hash differs: {reference['path']}"
            )
        value = load_and_validate(
            path,
            schema_filename=schema,
            digest_field=digest_field,
            label="prior capability governance artifact",
        )
        if value[digest_field] != reference["digest"]:
            raise CapabilityImprovementError(
                f"prior governance artifact digest differs: {reference['path']}"
            )
    return document


def _validate_archival_partition(
    batches: Sequence[Mapping[str, Any]],
    transfer_panel: Sequence[str],
) -> None:
    """Validate a hash-pinned superseded split without making it active.

    Historical lineage can legitimately use a partition that no longer equals
    the current frozen design.  Its manifest digest pins the exact membership;
    this gate verifies only the invariant six-by-five development structure,
    chronological batch metadata, and closed 30/10 disjoint partition.
    """

    expected = [
        (f"B{index}", "retrospective" if index <= 2 else "prospective", index * 5)
        for index in range(1, 7)
    ]
    actual = [
        (
            batch.get("batch_id"),
            batch.get("phase"),
            batch.get("checkpoint_size"),
        )
        for batch in batches
    ]
    if actual != expected or any(
        len(batch.get("protocol_ids", ())) != 5 for batch in batches
    ):
        raise CapabilityImprovementError(
            "archived experiment does not contain six chronological five-protocol batches"
        )
    development = [
        protocol_id
        for batch in batches
        for protocol_id in batch.get("protocol_ids", ())
    ]
    all_protocols = development + list(transfer_panel)
    if (
        len(development) != 30
        or len(transfer_panel) != 10
        or len(set(all_protocols)) != 40
    ):
        raise CapabilityImprovementError(
            "archived development and transfer protocols are not a disjoint 30/10 partition"
        )


def _validate_archival_pack(pack_root: Path) -> dict[str, Any]:
    """Validate an old v1 pack by its own closed-world manifest, not new rules."""

    root = pack_root.expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise CapabilityImprovementError(f"prior archival pack is missing: {root}")
    manifest_path = root / "manifest.json"
    document = _load_object(manifest_path, "prior archival pack manifest")
    validate_digest(document, "pack_digest")
    required = {
        "schema_version",
        "pack_kind",
        "manifest_self_excluded",
        "editable_roots",
        "immutable_paths",
        "files",
        "pack_digest",
    }
    if set(document) != required:
        raise CapabilityImprovementError(
            "prior archival pack manifest fields differ from v1"
        )
    if (
        document["schema_version"] != "libstruct.libgen_capability_pack.v1"
        or document["pack_kind"] != "model_neutral"
        or document["manifest_self_excluded"] is not True
        or not isinstance(document["editable_roots"], list)
        or not isinstance(document["immutable_paths"], list)
        or not isinstance(document["files"], list)
    ):
        raise CapabilityImprovementError(
            "prior archival pack manifest contract is invalid"
        )
    recorded: dict[str, Mapping[str, Any]] = {}
    for item in document["files"]:
        if not isinstance(item, Mapping) or set(item) != {
            "path",
            "sha256",
            "size_bytes",
            "mode",
        }:
            raise CapabilityImprovementError(
                "prior archival pack contains an invalid file record"
            )
        relative = safe_relative_path(item["path"]).as_posix()
        if relative == "manifest.json" or relative in recorded:
            raise CapabilityImprovementError(
                f"prior archival pack repeats a path: {relative}"
            )
        if item["mode"] not in {"0444", "0555"}:
            raise CapabilityImprovementError(
                f"prior archival pack has an invalid mode: {relative}"
            )
        recorded[relative] = item
    observed: set[str] = set()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"prior archival pack contains a symlink: {path}"
            )
        if not path.is_file() or path == manifest_path:
            continue
        relative = path.relative_to(root).as_posix()
        observed.add(relative)
        item = recorded.get(relative)
        actual_mode = (
            "0555" if stat.S_IMODE(path.stat().st_mode) & stat.S_IXUSR else "0444"
        )
        if (
            item is None
            or sha256_file(path) != item["sha256"]
            or path.stat().st_size != item["size_bytes"]
            or actual_mode != item["mode"]
        ):
            raise CapabilityImprovementError(
                f"prior archival pack file differs from its manifest: {relative}"
            )
    if observed != set(recorded):
        raise CapabilityImprovementError(
            "prior archival pack inventory is not an exact closed-world match"
        )
    return document


def _active_checkpoint_ids(
    root: Path,
    *,
    experiment_digest: str,
) -> list[str]:
    values: list[str] = []
    checkpoint_root = root / "checkpoints"
    if checkpoint_root.is_dir():
        for checkpoint_dir in sorted(checkpoint_root.iterdir()):
            # A failed atomic freeze may leave a hidden build directory.  It is
            # not an active checkpoint, but the restart's closed-world archive
            # still preserves and hashes every byte beneath it.
            if checkpoint_dir.name.startswith("."):
                continue
            if checkpoint_dir.is_symlink() or not checkpoint_dir.is_dir():
                raise CapabilityImprovementError(
                    f"invalid active checkpoint entry: {checkpoint_dir}"
                )
            path = checkpoint_dir / "checkpoint.json"
            document = load_and_validate(
                path,
                schema_filename="checkpoint.schema.json",
                digest_field="checkpoint_digest",
                label="active capability checkpoint",
            )
            checkpoint_id = document["checkpoint_id"]
            if checkpoint_dir.name != checkpoint_id:
                raise CapabilityImprovementError(
                    "checkpoint directory and validated checkpoint ID differ"
                )
            if document["experiment_digest"] != experiment_digest:
                raise CapabilityImprovementError(
                    f"checkpoint {checkpoint_id} belongs to another experiment"
                )
            count = int(checkpoint_id[1:])
            prefix = checkpoint_id[0]
            expected_branch = "autonomous" if prefix == "A" else "human"
            if (
                document["branch"] != expected_branch
                or document["protocol_count"] != count
                or document["batch_id"] != f"B{count // 5}"
                or document["parent_checkpoint_id"] != f"{prefix}{count - 5}"
            ):
                raise CapabilityImprovementError(
                    f"checkpoint {checkpoint_id} has inconsistent lineage fields"
                )
            values.append(checkpoint_id)
    return sorted(values, key=lambda value: (value[0], int(value[1:])))


def _rebase_supersession(document: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(document))
    result.pop("supersession_digest", None)
    for collection, field in (
        (result["original_artifacts"], "historical_path"),
        (result["superseded_packets"], "historical_path"),
    ):
        for item in collection:
            relative = safe_relative_path(item[field])
            if relative.is_relative_to(RESTART_HISTORY_ROOT):
                raise CapabilityImprovementError(
                    "supersession history is already rebased into a prior restart"
                )
            item[field] = (RESTART_HISTORY_ROOT / relative).as_posix()
    rebased = with_digest(result, "supersession_digest")
    validate_document(
        rebased,
        improvement_schema_root() / "supersession_manifest.schema.json",
        label="rebased supersession manifest",
    )
    return rebased


def _validate_supersession_history(
    root: Path,
    document: Mapping[str, Any],
    *,
    required_prefix: Path | None = None,
) -> None:
    for collection, digest_field in (
        (document["original_artifacts"], "sha256"),
        (document["superseded_packets"], "file_sha256"),
    ):
        for item in collection:
            relative = safe_relative_path(item["historical_path"])
            if required_prefix is not None and not relative.is_relative_to(
                required_prefix
            ):
                raise CapabilityImprovementError(
                    "supersession history was not rebased into the restart archive"
                )
            path = root / relative
            if path.is_symlink() or not path.is_file():
                raise CapabilityImprovementError(
                    f"supersession historical artifact is missing: {relative}"
                )
            if sha256_file(path) != item[digest_field]:
                raise CapabilityImprovementError(
                    f"supersession historical hash differs: {relative}"
                )


def _attach_lineage_restart(
    experiment_root: Path,
    restart: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_path = experiment_root / "design" / "experiment_manifest.json"
    manifest = _load_object(manifest_path, "staged experiment manifest")
    manifest.pop("experiment_digest", None)
    restart_path = experiment_root / "design" / "lineage_restart.json"
    manifest["lineage_restart"] = {
        "path": "design/lineage_restart.json",
        "sha256": sha256_file(restart_path),
        "digest": restart["restart_digest"],
    }
    result = with_digest(manifest, "experiment_digest")
    validate_document(
        result,
        improvement_schema_root() / "experiment_manifest.schema.json",
        label="restarted capability experiment manifest",
    )
    write_json_atomic(manifest_path, result, mode=0o444)
    return result


def _restart_transaction_paths(root: Path) -> tuple[Path, Path, Path]:
    return (
        root.parent / f".{root.name}.lineage-restart-staging",
        root.parent / f".{root.name}.lineage-restart-backup",
        root.parent / f".{root.name}.lineage-restart-transaction.json",
    )


@contextmanager
def _lineage_restart_lock(root: Path) -> Iterator[None]:
    root.parent.mkdir(parents=True, exist_ok=True)
    lock_path = root.parent / f".{root.name}.lineage-restart.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise CapabilityImprovementError(
                f"another lineage restart holds the transaction lock: {lock_path}"
            ) from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _write_restart_journal(
    path: Path,
    *,
    root: Path,
    stage: Path,
    backup: Path,
    expected_prior_digest: str,
    phase: str,
    new_experiment_digest: str | None,
) -> None:
    payload = {
        "schema_version": RESTART_JOURNAL_SCHEMA_VERSION,
        "experiment_root": root.as_posix(),
        "stage_path": stage.as_posix(),
        "backup_path": backup.as_posix(),
        "expected_prior_digest": expected_prior_digest,
        "phase": phase,
        "new_experiment_digest": new_experiment_digest,
    }
    write_json_atomic(
        path,
        with_digest(payload, "journal_digest"),
        mode=0o600,
    )


def _load_restart_journal(
    path: Path,
    *,
    root: Path,
    stage: Path,
    backup: Path,
    expected_prior_digest: str,
) -> dict[str, Any]:
    document = _load_object(path, "lineage restart transaction journal")
    validate_digest(document, "journal_digest")
    required = {
        "schema_version",
        "experiment_root",
        "stage_path",
        "backup_path",
        "expected_prior_digest",
        "phase",
        "new_experiment_digest",
        "journal_digest",
    }
    if set(document) != required:
        raise CapabilityImprovementError("lineage restart journal fields differ")
    if document["schema_version"] != RESTART_JOURNAL_SCHEMA_VERSION:
        raise CapabilityImprovementError("lineage restart journal schema differs")
    if (
        document["experiment_root"] != root.as_posix()
        or document["stage_path"] != stage.as_posix()
        or document["backup_path"] != backup.as_posix()
        or document["expected_prior_digest"] != expected_prior_digest
    ):
        raise CapabilityImprovementError("lineage restart journal identity differs")
    if document["phase"] not in {"staging", "prepared"}:
        raise CapabilityImprovementError("lineage restart journal phase is invalid")
    new_digest = document["new_experiment_digest"]
    if new_digest is not None and (
        not isinstance(new_digest, str)
        or len(new_digest) != 64
        or any(character not in "0123456789abcdef" for character in new_digest)
    ):
        raise CapabilityImprovementError(
            "lineage restart journal has an invalid new experiment digest"
        )
    return document


def _recover_lineage_restart(
    root: Path,
    expected_prior_digest: str,
) -> dict[str, Any] | None:
    stage, backup, journal = _restart_transaction_paths(root)
    if not journal.exists():
        if stage.exists() or backup.exists():
            raise CapabilityImprovementError(
                "untracked lineage-restart staging or backup path exists"
            )
        return None
    transaction = _load_restart_journal(
        journal,
        root=root,
        stage=stage,
        backup=backup,
        expected_prior_digest=expected_prior_digest,
    )
    new_digest = transaction["new_experiment_digest"]
    if root.is_dir() and new_digest is not None:
        try:
            active = validate_experiment_manifest(
                root / "design" / "experiment_manifest.json",
                experiment_root=root,
            )
        except (CapabilityImprovementError, OSError):
            active = None
        if active is not None and active["experiment_digest"] == new_digest:
            if stage.exists():
                _remove_restart_tree(stage)
            if backup.exists():
                _remove_restart_tree(backup)
            journal.unlink()
            return _restart_result(root, status="recovered_restarted_in_place")

    if backup.exists():
        if root.exists():
            if _manifest_digest_at(root) != new_digest:
                raise CapabilityImprovementError(
                    "cannot recover restart: active root is neither prior nor staged lineage"
                )
            _remove_restart_tree(root)
        _replace_path(backup, root)
    elif not root.is_dir():
        raise CapabilityImprovementError(
            "cannot recover restart: both prior root and backup are missing"
        )
    if _manifest_digest_at(root) != expected_prior_digest:
        raise CapabilityImprovementError(
            "recovered prior experiment digest does not match the transaction"
        )
    if stage.exists():
        _remove_restart_tree(stage)
    journal.unlink()
    return None


def _manifest_digest_at(root: Path) -> str | None:
    path = root / "design" / "experiment_manifest.json"
    if not path.is_file():
        return None
    try:
        document = _load_object(path, "transaction experiment manifest")
        validate_digest(document, "experiment_digest")
    except CapabilityImprovementError:
        return None
    value = document.get("experiment_digest")
    return value if isinstance(value, str) else None


def _remove_restart_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise CapabilityImprovementError(
            f"refusing to remove unexpected restart transaction path: {path}"
        )
    thaw_tree(path)
    shutil.rmtree(path)


def _replace_path(source: Path, destination: Path) -> None:
    source.replace(destination)


def _restart_result(root: Path, *, status: str) -> dict[str, Any]:
    manifest = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    restart = validate_lineage_restart_history(root, active_manifest=manifest)
    return {
        "status": status,
        "experiment_root": root.as_posix(),
        "experiment_digest": manifest["experiment_digest"],
        "prior_experiment_digest": restart["prior_experiment_digest"],
        "restart_digest": restart["restart_digest"],
        "restart_path": (root / "design" / "lineage_restart.json").as_posix(),
        "archived_root": (root / restart["archived_root"]).as_posix(),
        "archived_artifact_count": restart["archived_artifact_count"],
        "archived_checkpoint_ids": restart["prior_active_checkpoint_ids"],
        "active_checkpoint_count": 0,
        "active_pack_labels": ["S0", "A0", "H0"],
        "batch_processing_performed": False,
        "model_runs_performed": False,
    }


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise CapabilityImprovementError(f"{label} must be an object")
    return value
