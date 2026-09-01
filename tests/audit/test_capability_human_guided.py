from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from libstruct_bench.audit.artifacts import sha256_file, write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    validate_digest,
    with_digest,
)
from libstruct_bench.improvement.human_guided import (
    _assert_verifier_refresh_window,
    _ordered_validation_checkpoint_labels,
    _repin_registry_verifier_artifacts,
    _validate_protocol_findings,
)
from libstruct_bench.improvement.learning_ledger import (
    _observation_exclusion_reason,
)
from libstruct_bench.improvement.local_learning import _prepare_or_validate_packet


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "schemas" / "improvement"
PACK_ROOT = REPO_ROOT / "improvement" / "capability_pack"


def test_validation_checkpoint_order_does_not_follow_json_key_order() -> None:
    aliases = {
        "C0": "H0",
        "C10": "H10",
        "C15": "H15",
        "C20": "H20",
        "C25": "H25",
        "C5": "H5",
    }

    assert _ordered_validation_checkpoint_labels(aliases) == (
        "C0",
        "C5",
        "C10",
        "C15",
        "C20",
        "C25",
    )


def test_verifier_refresh_allows_prior_batch_progress(tmp_path: Path) -> None:
    (tmp_path / "checkpoints" / "C0").mkdir(parents=True)
    (tmp_path / "checkpoints" / "C5").mkdir()
    prior_round = tmp_path / "rounds" / "B1" / "cumulative"
    prior_round.mkdir(parents=True)
    (prior_round / "proposal.json").write_text("{}", encoding="utf-8")

    _assert_verifier_refresh_window(tmp_path, "B2")

    (tmp_path / "checkpoints" / "C10").mkdir()
    with pytest.raises(CapabilityImprovementError, match="C10"):
        _assert_verifier_refresh_window(tmp_path, "B2")


def test_verifier_refresh_rejects_target_batch_synthesis(tmp_path: Path) -> None:
    (tmp_path / "checkpoints" / "C0").mkdir(parents=True)
    (tmp_path / "checkpoints" / "C5").mkdir()
    target_round = tmp_path / "rounds" / "B2" / "cumulative"
    target_round.mkdir(parents=True)
    (target_round / "packet.json").write_text("{}", encoding="utf-8")

    with pytest.raises(CapabilityImprovementError, match="B2"):
        _assert_verifier_refresh_window(tmp_path, "B2")


def test_human_guided_schemas_are_valid_and_self_contained() -> None:
    paths = sorted(SCHEMA_ROOT.glob("human_*.schema.json"))
    assert len(paths) == 9
    for path in paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert not any(
            isinstance(value, str) and value.startswith("human_")
            for value in _refs(schema)
        ), path.name


def test_verifier_sidecar_repin_preserves_original_registry_bytes(
    tmp_path: Path,
) -> None:
    original_paths = {}
    rescored_paths = {}
    for role, filename in (
        ("verifier_reward", "reward.json"),
        ("verifier_details", "details.json"),
        ("verifier_error_analysis", "error_analysis.json"),
    ):
        original = tmp_path / "original" / filename
        rescored = tmp_path / "rescore" / filename
        original.parent.mkdir(exist_ok=True)
        rescored.parent.mkdir(exist_ok=True)
        original.write_text(f"original {role}", encoding="utf-8")
        rescored.write_text(f"rescored {role}", encoding="utf-8")
        original_paths[role] = original
        rescored_paths[role] = rescored

    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text("{}", encoding="utf-8")

    def artifact(role: str, path: Path) -> dict:
        return {
            "role": role,
            "path": path.as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }

    entry = with_digest(
        {
            "protocol_id": "protocol_a",
            "prediction_valid": False,
            "artifacts": [
                artifact("trajectory", trajectory),
                *(artifact(role, path) for role, path in original_paths.items()),
            ],
        },
        "entry_digest",
    )
    registry = with_digest(
        {
            "entries": [entry],
            "valid_prediction_count": 0,
            "invalid_prediction_count": 1,
        },
        "registry_digest",
    )
    original_registry = copy.deepcopy(registry)

    new_entry, new_registry, replacements = _repin_registry_verifier_artifacts(
        registry=registry,
        entry=entry,
        rescored_paths=rescored_paths,
        prediction_valid=True,
    )

    assert registry == original_registry
    assert new_entry["prediction_valid"] is True
    assert new_registry["valid_prediction_count"] == 1
    assert new_registry["invalid_prediction_count"] == 0
    validate_digest(new_entry, "entry_digest")
    validate_digest(new_registry, "registry_digest")
    for role, replacement in replacements.items():
        assert replacement["path"] == rescored_paths[role].as_posix()
        assert replacement in new_entry["artifacts"]
        assert artifact(role, original_paths[role]) in entry["artifacts"]

    with pytest.raises(CapabilityImprovementError, match="requires reward"):
        _repin_registry_verifier_artifacts(
            registry=registry,
            entry=entry,
            rescored_paths={"verifier_reward": rescored_paths["verifier_reward"]},
            prediction_valid=True,
        )


def test_human_findings_require_complete_root_grouping_and_recoverability(
    tmp_path: Path,
) -> None:
    analysis_path = tmp_path / "error_analysis.json"
    write_json_atomic(
        analysis_path,
        {
            "process_review": {
                "events": [],
            }
        },
    )
    observations = [
        _observation("err_0001", "recoverable"),
        _observation("err_0002", "recoverable"),
    ]
    comparison = {
        "observations": observations,
        "artifacts": [
            {
                "role": "verifier_error_analysis",
                "path": analysis_path.as_posix(),
            }
        ],
    }
    finding = {
        "finding_id": "root_1",
        "observation_ids": ["err_0001", "err_0002"],
        "category": "workflow_or_topology_error",
        "benchmark_validity": "valid",
        "attribution": "agent",
        "process_cause": "unresolved",
        "process_evidence": [],
        "diagnosis": "Two metric symptoms arise from one missing edge.",
        "generalized_failure_pattern": "A product disposition was omitted.",
        "proposed_remedy": "Audit every typed product edge once.",
        "applicability": ["directed molecular workflows"],
        "exclusions": ["handling-only steps"],
        "suggested_capability_class": "instruction_only",
    }
    draft = {
        "successful_self_correction": "not_observed",
        "root_findings": [finding],
        "no_change_rationale": None,
    }
    human = {"posthoc_transfer": {"protocol_ids": ["blocked_protocol"]}}
    _validate_protocol_findings(
        draft=draft,
        comparison=comparison,
        human_manifest=human,
        parent_pack_root=PACK_ROOT,
    )

    incomplete = copy.deepcopy(draft)
    incomplete["root_findings"][0]["observation_ids"] = ["err_0001"]
    with pytest.raises(CapabilityImprovementError, match="exactly once"):
        _validate_protocol_findings(
            draft=incomplete,
            comparison=comparison,
            human_manifest=human,
            parent_pack_root=PACK_ROOT,
        )

    neutral = copy.deepcopy(comparison)
    neutral["observations"][0]["claim_recoverability"] = "neutral"
    with pytest.raises(CapabilityImprovementError, match="neutral or unresolved"):
        _validate_protocol_findings(
            draft=draft,
            comparison=neutral,
            human_manifest=human,
            parent_pack_root=PACK_ROOT,
        )


def test_human_adjudication_filter_is_strict_but_legacy_pending_is_preserved() -> None:
    complete_unresolved = {
        "adjudication_status": "complete",
        "benchmark_validity": "unresolved",
        "benchmark_validity_candidate": None,
        "attribution": "unresolved",
    }
    assert _observation_exclusion_reason(complete_unresolved) == "policy_defect"
    assert (
        _observation_exclusion_reason(
            {
                **complete_unresolved,
                "benchmark_validity": "source_conflict",
                "attribution": "benchmark",
            }
        )
        == "policy_defect"
    )
    assert (
        _observation_exclusion_reason(
            {
                **complete_unresolved,
                "benchmark_validity": "valid",
                "attribution": "benchmark",
            }
        )
        == "ground_truth_defect"
    )
    assert (
        _observation_exclusion_reason(
            {
                **complete_unresolved,
                "adjudication_status": "pending",
            }
        )
        is None
    )


def test_prebuilt_packet_does_not_require_a_synthetic_run_root(tmp_path: Path) -> None:
    protocol_ids = [f"protocol_{index}" for index in range(5)]
    experiment = {
        "experiment_digest": "1" * 64,
        "batches": [
            {
                "batch_id": "B1",
                "phase": "retrospective",
                "protocol_ids": protocol_ids,
            }
        ],
    }
    packet = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_batch_packet.v1",
            "packet_id": "B1:cumulative:revealed",
            "experiment_digest": experiment["experiment_digest"],
            "batch_id": "B1",
            "branch": "cumulative",
            "phase": "retrospective",
            "parent_pack_digest": "2" * 64,
            "protocol_ids": protocol_ids,
            "reveal_state": "revealed",
            "eligibility_status": "eligible_for_improvement",
            "transfer_access_policy_digest": "3" * 64,
            "artifacts": [],
            "trial_terminality": [],
            "learning_ledger": None,
        },
        "packet_digest",
    )
    packet_path = tmp_path / "packet.json"
    write_json_atomic(packet_path, packet)
    observed = _prepare_or_validate_packet(
        packet_path=packet_path,
        experiment=experiment,
        batch_id="B1",
        branch="cumulative",
        parent_pack_digest="2" * 64,
        run_root=None,
        c0_run_root=None,
        source_root=tmp_path / "unused-sources",
        groundtruth_root=tmp_path / "unused-groundtruth",
        access_policy={"policy_digest": "3" * 64},
    )
    assert observed == packet


def _observation(error_id: str, recoverability: str) -> dict:
    return {
        "error_id": error_id,
        "category": "workflow_or_topology_error",
        "claim_recoverability": recoverability,
        "substantive": True,
    }


def _refs(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref":
                yield child
            yield from _refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _refs(child)
