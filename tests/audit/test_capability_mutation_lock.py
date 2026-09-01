from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    with_digest,
)
from libstruct_bench.improvement.governance import (
    assert_capability_modification_open,
)
from libstruct_bench.improvement.mutation_lock import (
    experiment_mutation_lock,
    experiment_mutation_lock_path,
    guard_experiment_mutation,
    split_freeze_journal_path,
)
from libstruct_bench.improvement.review_console import run_human_review_console
from libstruct_bench.improvement.workflow import (
    apply_capability_decision,
    freeze_checkpoint,
)


def test_experiment_mutation_lock_is_same_thread_reentrant(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    root.mkdir()

    with experiment_mutation_lock(root, operation="outer") as outer:
        with experiment_mutation_lock(root, operation="inner") as inner:
            assert inner.reentrant is True
            assert inner.lock_path == outer.lock_path
            assert inner.lock_path == experiment_mutation_lock_path(root)


def test_experiment_mutation_lock_rejects_another_thread(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    root.mkdir()
    findings: list[str] = []

    def contender() -> None:
        try:
            with experiment_mutation_lock(root, operation="thread contender"):
                findings.append("unexpected acquisition")
        except CapabilityImprovementError as error:
            findings.append(str(error))

    with experiment_mutation_lock(root, operation="thread owner"):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert len(findings) == 1
    assert "another capability experiment mutation" in findings[0]


def test_experiment_mutation_lock_rejects_another_process(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    root.mkdir()
    repository_root = Path(__file__).resolve().parents[2]
    script = """
import sys
from pathlib import Path
from libstruct_bench.improvement.artifacts import CapabilityImprovementError
from libstruct_bench.improvement.mutation_lock import experiment_mutation_lock

try:
    with experiment_mutation_lock(Path(sys.argv[1]), operation="child"):
        pass
except CapabilityImprovementError as error:
    if "another capability experiment mutation" in str(error):
        raise SystemExit(0)
    raise
raise SystemExit(1)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = (repository_root / "src").as_posix()

    with experiment_mutation_lock(root, operation="parent"):
        result = subprocess.run(
            [sys.executable, "-c", script, root.as_posix()],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
        )

    assert result.returncode == 0, result.stderr


def test_guard_blocks_stale_journal_but_allows_split_restage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "v1"
    root.mkdir()
    split_freeze_journal_path(root).write_text("{}\n", encoding="utf-8")

    @guard_experiment_mutation("nested learning fixture")
    def guarded(*, experiment_root: Path) -> Path:
        return experiment_root

    with pytest.raises(
        CapabilityImprovementError,
        match="must be recovered before another experiment mutation",
    ):
        guarded(experiment_root=root)

    with experiment_mutation_lock(
        root,
        operation="authorized split freeze",
        authorize_split_journal_recovery=True,
    ):
        assert guarded(experiment_root=root) == root


def test_low_level_interactive_review_uses_resolved_experiment_lock(
    tmp_path: Path,
) -> None:
    root = tmp_path / "v1"
    manifest = root / "design" / "experiment_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    review_root = root / "rounds" / "B1" / "human"
    proposal = review_root / "proposal.json"
    decision = review_root / "decision.json"
    ready = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with experiment_mutation_lock(root, operation="concurrent learning"):
            ready.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert ready.wait(timeout=5)
    try:
        with pytest.raises(
            CapabilityImprovementError,
            match="another capability experiment mutation",
        ):
            run_human_review_console(
                proposal_path=proposal,
                decision_path=decision,
                reviewer_id="reviewer",
                parent_pack_root=root / "packs" / "H0",
                candidate_root=review_root / "candidates",
            )
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_low_level_review_fails_closed_on_nearby_split_journal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "v1"
    review_root = root / "rounds" / "B1" / "human"
    review_root.mkdir(parents=True)
    split_freeze_journal_path(root).write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        CapabilityImprovementError,
        match="split freeze blocks this low-level mutation",
    ):
        run_human_review_console(
            proposal_path=review_root / "proposal.json",
            decision_path=review_root / "decision.json",
            reviewer_id="reviewer",
            parent_pack_root=root / "packs" / "H0",
            candidate_root=review_root / "candidates",
        )


def test_low_level_apply_and_freeze_share_experiment_lock(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    root.mkdir()
    ready = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with experiment_mutation_lock(root, operation="concurrent final lock"):
            ready.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert ready.wait(timeout=5)
    try:
        with pytest.raises(
            CapabilityImprovementError,
            match="another capability experiment mutation",
        ):
            apply_capability_decision(
                experiment_root=root,
                experiment_manifest={},
                access_policy_path=root / "access.json",
                proposal_path=root / "proposal.json",
                decision_path=root / "decision.json",
                candidate_root=root / "candidates",
                parent_pack_root=root / "parent-pack",
                output_dir=root / "application",
                created_at="2026-08-22T00:00:00Z",
                synthetic_runner=None,
            )
        with pytest.raises(
            CapabilityImprovementError,
            match="another capability experiment mutation",
        ):
            freeze_checkpoint(
                experiment_root=root,
                experiment_digest="a" * 64,
                branch="cumulative",
                batch_id="B1",
                protocol_count=5,
                parent_checkpoint_id="C0",
                proposal_path=root / "proposal.json",
                decision_path=root / "decision.json",
                application_dir=root / "application",
                output_dir=root / "checkpoints" / "C5",
                created_at="2026-08-22T00:00:00Z",
            )
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_capability_modification_closes_at_final_lock(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    digest = "a" * 64
    labels = ("C0", "C5", "C10", "C15", "C20", "C25")
    lock = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_final_lock.v1",
            "lock_id": "synthetic-final-lock",
            "experiment_digest": digest,
            "checkpoint_records": [
                {
                    "checkpoint_id": label,
                    "checkpoint_sha256": "b" * 64,
                    "checkpoint_digest": "c" * 64,
                    "pack_digest": "d" * 64,
                }
                for label in labels
            ],
            "validation_curve": {
                "access_policy_digest": "e" * 64,
                "checkpoint_labels": list(labels),
                "aggregate_records": [
                    {
                        "checkpoint_label": label,
                        "aggregate_digest": "f" * 64,
                        "aggregate_sha256": "1" * 64,
                        "pack_digest": "d" * 64,
                        "checkpoint_digest": "3" * 64,
                        "runtime_digest": "4" * 64,
                        "integration_digest": "5" * 64,
                        "integration_manifest_sha256": "6" * 64,
                        "task_bundle_sha256": "7" * 64,
                        "harbor_config_sha256": "8" * 64,
                        "result_bundle_digest": "9" * 64,
                    }
                    for label in labels
                ],
                "expected_trial_count": 30,
            },
            "checkpoint_labels": list(labels),
            "endpoint_labels": ["C25"],
            "transfer_panel_commitment_sha256": "2" * 64,
            "baseline_mode": "post_lock_c0_replay",
            "primary_outcome": "t3_molecular_transition_f1",
            "checkpoint_modification_closed": True,
            "created_at": "2026-08-22T00:00:00Z",
        },
        "lock_digest",
    )
    write_json_atomic(root / "design" / "final_lock.json", lock)
    with pytest.raises(
        CapabilityImprovementError,
        match="capability modification is closed",
    ):
        assert_capability_modification_open(root, experiment_digest=digest)


def test_capability_modification_remains_closed_after_unseal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "v1"
    digest = "a" * 64
    authorization = with_digest(
        {
            "schema_version": "libstruct.libgen_transfer_panel_authorization.v1",
            "experiment_digest": digest,
            "lock_digest": "b" * 64,
            "transfer_panel_commitment_sha256": "c" * 64,
            "baseline_mode": "post_lock_c0_replay",
            "protocol_ids": [f"test_protocol_{index}" for index in range(10)],
            "replay_labels": ["C0", "C5", "C10", "C15", "C20", "C25"],
            "endpoint_labels": ["C25"],
            "authorized_by": "synthetic-reviewer",
            "authorized_at": "2026-08-22T00:00:00Z",
            "checkpoint_modification_closed": True,
        },
        "authorization_digest",
    )
    write_json_atomic(
        root / "design" / "transfer_panel_authorization.json",
        authorization,
    )
    with pytest.raises(
        CapabilityImprovementError,
        match="capability modification is closed",
    ):
        assert_capability_modification_open(root, experiment_digest=digest)
