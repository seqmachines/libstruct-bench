from __future__ import annotations

import copy
from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    copy_capability_pack,
    with_digest,
)
from libstruct_bench.improvement.exemplar_governance import (
    PUBLIC_STATEMENT,
    build_exemplar_memory_adoption,
    build_exemplar_projection_policy,
    validate_exemplar_memory_adoption,
    validate_exemplar_projection_policy,
)
from libstruct_bench.improvement.exemplar_memory import (
    create_empty_exemplar_memory,
    ensure_exemplar_identity_map,
)
from libstruct_bench.improvement.split_design import (
    FINAL_DEVELOPMENT_BATCHES,
    FINAL_REBALANCE_POLICY,
    FINAL_TRANSFER_ANNOTATIONS,
    FINAL_TRANSFER_PANEL,
    FINAL_TRANSFER_PURPOSE,
    FINAL_TRANSFER_STRATA,
    FIXED_VALIDATION_PANEL,
)
from libstruct_bench.improvement.split_freeze import build_frozen_split


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPO_ROOT / "improvement" / "capability_pack"
NOW = "2026-08-22T12:00:00Z"


def _governed_c0(root: Path) -> tuple[dict, dict, Path]:
    split = build_frozen_split(
        batches=FINAL_DEVELOPMENT_BATCHES,
        validation_protocol_ids=FIXED_VALIDATION_PANEL,
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        recorded_at=NOW,
        status="active",
        transfer_strata=FINAL_TRANSFER_STRATA,
        transfer_annotations=FINAL_TRANSFER_ANNOTATIONS,
        purposes=FINAL_TRANSFER_PURPOSE,
        rebalance_policy=FINAL_REBALANCE_POLICY,
        split_id="libgen-capability-25-5-10-split",
    )
    split_path = root / "design" / "frozen_split.json"
    write_json_atomic(split_path, split, mode=0o444)
    identity = ensure_exemplar_identity_map(
        experiment_root=root,
        split_digest=split["split_digest"],
    )
    memory_root = root / "checkpoints" / "C0" / "memory"
    create_empty_exemplar_memory(memory_root=memory_root, identity_map=identity)
    copy_capability_pack(PACK_ROOT, root / "checkpoints" / "C0" / "pack", freeze=True)
    policy = build_exemplar_projection_policy(
        experiment_root=root,
        frozen_split_path=split_path,
        identity_map_path=root / "private" / "exemplar_identity_map.json",
        created_at=NOW,
    )
    return split, policy, memory_root


def test_projection_policy_freezes_dual_memory_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "experiment"
    _, policy, _ = _governed_c0(root)
    policy_path = root / "design" / "exemplar_projection_policy.json"
    write_json_atomic(policy_path, policy, mode=0o444)

    validated = validate_exemplar_projection_policy(
        policy_path,
        experiment_root=root,
        frozen_split_path=root / "design" / "frozen_split.json",
    )

    assert validated["public_statement"] == PUBLIC_STATEMENT
    assert [item["exemplar_count"] for item in validated["checkpoint_schedule"]] == [
        0,
        5,
        10,
        15,
        20,
        25,
    ]
    assert validated["retrieval"]["maximum_exemplars"] == 3
    assert validated["procedural_update_admission"]["maximum_proposed_per_batch"] == 2
    assert validated["procedural_update_admission"]["maximum_accepted_per_batch"] == 1
    assert validated["excluded_protocol_ids"] == {
        "validation": list(FIXED_VALIDATION_PANEL),
        "final_test": list(FINAL_TRANSFER_PANEL),
    }


def test_adoption_binds_empty_memory_prior_manifest_and_unchanged_pack(
    tmp_path: Path,
) -> None:
    root = tmp_path / "experiment"
    _, policy, memory_root = _governed_c0(root)
    archive = root / "history" / "superseded" / "exemplar-memory-fixture"
    prior = with_digest(
        {"schema_version": "superseded-fixture", "created_at": NOW},
        "experiment_digest",
    )
    prior_path = archive / "design" / "experiment_manifest.json"
    write_json_atomic(prior_path, prior, mode=0o444)
    adoption = build_exemplar_memory_adoption(
        experiment_root=root,
        prior_experiment_manifest_path=prior_path,
        archive_root=archive.relative_to(root),
        archived_artifact_paths=(prior_path,),
        projection_policy=policy,
        identity_map_path=root / "private" / "exemplar_identity_map.json",
        c0_memory_root=memory_root,
        prior_c0_pack_root=root / "checkpoints" / "C0" / "pack",
        new_c0_pack_root=root / "checkpoints" / "C0" / "pack",
        adopted_at=NOW,
        mode="adopted_into_clean_c0",
    )
    adoption_path = root / "design" / "exemplar_memory_adoption.json"
    write_json_atomic(adoption_path, adoption, mode=0o444)

    validated = validate_exemplar_memory_adoption(
        adoption_path,
        experiment_root=root,
        projection_policy=policy,
        c0_memory_root=memory_root,
        c0_pack_root=root / "checkpoints" / "C0" / "pack",
    )
    assert validated["c0_exemplar_count"] == 0
    assert validated["prior_c0_pack_digest"] == validated["new_c0_pack_digest"]
    assert validated["training_batch_processed"] is False
    assert validated["harbor_run_started"] is False

    tampered = copy.deepcopy(adoption)
    tampered.pop("adoption_digest")
    tampered["new_c0_pack_digest"] = "f" * 64
    tampered = with_digest(tampered, "adoption_digest")
    write_json_atomic(adoption_path, tampered, mode=0o444)
    with pytest.raises(CapabilityImprovementError, match="active C0 differs"):
        validate_exemplar_memory_adoption(
            adoption_path,
            experiment_root=root,
            projection_policy=policy,
            c0_memory_root=memory_root,
            c0_pack_root=root / "checkpoints" / "C0" / "pack",
        )
