from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import (
    AuditArtifactError,
    sha256_file,
    validate_document,
)
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    improvement_schema_root,
)
from libstruct_bench.improvement.learning_ledger import (
    _observation_signature,
    build_learning_ledger,
    validate_learning_ledger,
)
from libstruct_bench.improvement.local_learning import _prepare_compilable_draft
from libstruct_bench.improvement.workflow import _validate_unit_semantics
from libstruct_bench.libgen.error_analysis import build_error_analysis


PROTOCOLS = tuple(f"synthetic_protocol_{index}" for index in range(5))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _summary_artifacts(
    root: Path,
    *,
    role_prefix: str = "",
    reward: float = 0.5,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for protocol_id in PROTOCOLS:
        error = build_error_analysis(
            trial_id=f"{protocol_id}:trial",
            protocol_id=protocol_id,
            result=None,
            details={"prediction_valid": False},
            verifier_error={
                "kind": "invalid_prediction",
                "message": "synthetic invalid prediction",
            },
            artifact_inventory=[],
        )
        error_path = root / f"{role_prefix}{protocol_id}-error.json"
        reward_path = root / f"{role_prefix}{protocol_id}-reward.json"
        _write_json(error_path, error)
        _write_json(
            reward_path,
            {
                "reward": reward,
                "t2_exact_required_family_recall": reward,
                "t2_required_family_f1": reward,
                "t3_molecular_transition_f1": reward,
                "t3_state_f1": reward,
                "t3_typed_edge_f1": reward,
            },
        )
        for role, path in (
            (f"{role_prefix}verifier_error_analysis", error_path),
            (f"{role_prefix}verifier_reward", reward_path),
        ):
            result.append(
                {
                    "protocol_id": protocol_id,
                    "role": role,
                    "path": path.as_posix(),
                    "sha256": sha256_file(path),
                    "visibility": "agent_after_reveal",
                }
            )
    return result


def test_learning_ledger_clusters_stably_and_records_score_context(
    tmp_path: Path,
) -> None:
    artifacts = _summary_artifacts(tmp_path / "active", reward=0.4)
    first = build_learning_ledger(
        batch_id="B1", protocol_ids=PROTOCOLS, artifacts=artifacts
    )
    assert first is not None
    assert len(first["source_artifacts"]) == 10
    assert len(first["clusters"]) == 1
    cluster = first["clusters"][0]
    assert cluster["observation_count"] == 5
    assert cluster["protocol_count"] == 5
    assert cluster["root_error_status"] == "recurring_across_protocols"
    assert first["filter_summary"] == {
        "substantive_observation_count": 5,
        "admitted_observation_count": 5,
        "excluded_observation_count": 0,
        "admitted_root_event_count": 1,
        "collapsed_observation_count": 4,
        "excluded_by_reason": {
            "infrastructure": 0,
            "evaluator_defect": 0,
            "policy_defect": 0,
            "ground_truth_defect": 0,
        },
    }
    reward_effect = next(
        item for item in cluster["metric_effects"] if item["metric"] == "reward"
    )
    assert reward_effect == {
        "metric": "reward",
        "active_score": pytest.approx(0.4),
        "c0_score": None,
        "paired_delta": None,
        "status": "active_only_delta_unavailable",
    }

    changed = _summary_artifacts(tmp_path / "changed", reward=0.9)
    second = build_learning_ledger(
        batch_id="B1", protocol_ids=PROTOCOLS, artifacts=changed
    )
    assert second is not None
    assert second["clusters"][0]["cluster_id"] == cluster["cluster_id"]
    assert second["ledger_digest"] != first["ledger_digest"]


def test_learning_ledger_filters_nonagent_defects_and_collapses_metric_mismatches(
    tmp_path: Path,
) -> None:
    artifacts = _summary_artifacts(tmp_path, reward=0.5)
    for artifact in artifacts:
        if artifact["role"] != "verifier_error_analysis":
            continue
        path = Path(str(artifact["path"]))
        document = json.loads(path.read_text(encoding="utf-8"))
        base = document["observations"][0]
        first = {
            **base,
            "error_id": "err_0001",
            "signals": ["architecture=0.25"],
            "affected_metrics": ["t3_state_f1"],
            "attribution": "agent",
            "benchmark_validity": "valid",
            "benchmark_validity_candidate": None,
            "process_cause": "graph_abstraction_error",
        }
        second = {
            **first,
            "error_id": "err_0002",
            "signals": ["pairing=0.50"],
            "affected_metrics": ["t3_typed_edge_f1"],
        }
        infrastructure = {
            **first,
            "error_id": "err_0003",
            "attribution": "infrastructure",
        }
        evaluator = {
            **first,
            "error_id": "err_0004",
            "benchmark_validity": "unresolved",
            "benchmark_validity_candidate": "evaluator_defect",
        }
        policy = {
            **first,
            "error_id": "err_0005",
            "benchmark_validity": "source_scope_mismatch",
        }
        ground_truth = {
            **first,
            "error_id": "err_0006",
            "attribution": "benchmark",
        }
        mixed = {
            **first,
            "error_id": "err_0007",
            "attribution": "mixed",
        }
        document["observations"] = [
            first,
            second,
            infrastructure,
            evaluator,
            policy,
            ground_truth,
            mixed,
        ]
        _write_json(path, document)
        artifact["sha256"] = sha256_file(path)

    ledger = build_learning_ledger(
        batch_id="B1", protocol_ids=PROTOCOLS, artifacts=artifacts
    )
    assert ledger is not None
    assert len(ledger["clusters"]) == 1
    cluster = ledger["clusters"][0]
    assert cluster["observation_count"] == 10
    assert cluster["protocol_count"] == 5
    assert cluster["affected_metrics"] == ["t3_state_f1", "t3_typed_edge_f1"]
    assert {"architecture", "pairing"} <= set(cluster["finding_codes"])
    assert ledger["filter_summary"] == {
        "substantive_observation_count": 35,
        "admitted_observation_count": 10,
        "excluded_observation_count": 25,
        "admitted_root_event_count": 1,
        "collapsed_observation_count": 9,
        "excluded_by_reason": {
            "infrastructure": 5,
            "evaluator_defect": 5,
            "policy_defect": 5,
            "ground_truth_defect": 10,
        },
    }


def test_prospective_ledger_pairs_active_with_c0_without_peer_details(
    tmp_path: Path,
) -> None:
    artifacts = _summary_artifacts(tmp_path / "active", reward=0.6)
    artifacts += _summary_artifacts(tmp_path / "c0", role_prefix="c0_", reward=0.5)
    ledger = build_learning_ledger(
        batch_id="B3", protocol_ids=PROTOCOLS, artifacts=artifacts
    )
    assert ledger is not None
    cluster = ledger["clusters"][0]
    reward_effect = next(
        item for item in cluster["metric_effects"] if item["metric"] == "reward"
    )
    assert reward_effect["status"] == "paired"
    assert reward_effect["paired_delta"] == pytest.approx(0.1)
    assert cluster["c0_observation_count"] == 5
    assert {item["role"] for item in ledger["source_artifacts"]} == {
        "verifier_error_analysis",
        "verifier_reward",
        "c0_verifier_error_analysis",
        "c0_verifier_reward",
    }


def test_learning_ledger_recomputation_rejects_tampering(tmp_path: Path) -> None:
    artifacts = _summary_artifacts(tmp_path, reward=0.5)
    ledger = build_learning_ledger(
        batch_id="B1", protocol_ids=PROTOCOLS, artifacts=artifacts
    )
    assert ledger is not None
    tampered = copy.deepcopy(ledger)
    tampered["clusters"][0]["observation_count"] = 4
    with pytest.raises(CapabilityImprovementError, match="differs from deterministic"):
        validate_learning_ledger(
            tampered,
            batch_id="B1",
            protocol_ids=PROTOCOLS,
            artifacts=artifacts,
            require_revealed=True,
        )


def test_learning_cluster_identity_excludes_signal_scores() -> None:
    common = {
        "task": "T3",
        "category": "molecular_state_or_assembly_error",
        "entity_type": "state",
        "process_cause": "unresolved",
        "affected_metrics": ["t3_state_f1"],
    }
    first = _observation_signature(
        {
            **common,
            "signals": [
                "architecture=0.715556",
                "pairing=0.818182;segments=0.562500",
            ],
        }
    )
    second = _observation_signature(
        {
            **common,
            "signals": [
                "architecture=0.250992",
                "pairing=0.500000;segments=0.201296",
            ],
        }
    )
    assert first == second
    assert first["finding_codes"] == [
        "architecture",
        "molecular_state_or_assembly_error",
        "pairing",
        "segments",
    ]


def _deterministic_unit() -> dict[str, object]:
    return {
        "change_id": "synthetic_atomic_unit",
        "cluster_ids": ["cluster_" + "a" * 24],
        "generalized_failure_pattern": "a general synthetic failure",
        "update_type": "procedural_or_tool",
        "admission_basis": "general_invariant_with_synthetic_regression",
        "synthetic_regression_case_ids": ["edge_negative"],
        "capability_class": "deterministic",
        "applicability": ["linked molecular graphs"],
        "exclusions": ["unlinked prose"],
        "finding_codes": ["missing_typed_edge"],
        "residual_judgment": None,
        "counterexample": "A source-supported alternate branch must remain valid.",
        "expected_invariant": "All supported edges are represented once.",
        "enforcement_paths": ["tools/check_typed_edges.py"],
        "fixtures": [
            {
                "case_id": "edge_positive",
                "polarity": "positive",
                "path": "synthetic_tests/valid/t3.json",
            },
            {
                "case_id": "edge_negative",
                "polarity": "negative",
                "path": "synthetic_tests/invalid/new_edge.json",
            },
            {
                "case_id": "edge_boundary",
                "polarity": "boundary",
                "path": "synthetic_tests/valid/t2.json",
            },
        ],
        "instruction_only_rationale": None,
        "mutations": [
            {
                "operation": "replace",
                "path": "tools/check_typed_edges.py",
                "baseline_sha256": "1" * 64,
                "candidate_sha256": "2" * 64,
            },
            {
                "operation": "add",
                "path": "synthetic_tests/invalid/new_edge.json",
                "baseline_sha256": None,
                "candidate_sha256": "3" * 64,
            },
        ],
        "leakage_attestation": True,
    }


def test_atomic_unit_schema_and_classification_require_fixture_polarities() -> None:
    unit = _deterministic_unit()
    validate_document(
        {
            "schema_version": "libstruct.libgen_capability_proposal_draft.v1",
            "change_units": [unit],
        },
        improvement_schema_root() / "capability_proposal_draft.schema.json",
        label="synthetic proposal draft",
    )
    _validate_unit_semantics(unit, cluster_index=None)

    missing_boundary = copy.deepcopy(unit)
    missing_boundary["fixtures"] = missing_boundary["fixtures"][:2]
    with pytest.raises(CapabilityImprovementError, match="boundary fixtures"):
        _validate_unit_semantics(missing_boundary, cluster_index=None)

    instruction_only = copy.deepcopy(unit)
    instruction_only["capability_class"] = "instruction_only"
    instruction_only["enforcement_paths"] = []
    instruction_only["fixtures"] = []
    instruction_only["admission_basis"] = "recurring_root_error"
    instruction_only["synthetic_regression_case_ids"] = []
    instruction_only["residual_judgment"] = "The model must interpret source scope."
    with pytest.raises(CapabilityImprovementError, match="requires a rationale"):
        _validate_unit_semantics(instruction_only, cluster_index=None)


def test_proposal_budget_and_admission_bases_are_deterministic() -> None:
    unit = _deterministic_unit()
    too_many = []
    for index in range(3):
        item = copy.deepcopy(unit)
        item["change_id"] = f"synthetic-unit-{index}"
        too_many.append(item)
    with pytest.raises(AuditArtifactError, match="too long"):
        validate_document(
            {
                "schema_version": "libstruct.libgen_capability_proposal_draft.v1",
                "change_units": too_many,
            },
            improvement_schema_root() / "capability_proposal_draft.schema.json",
            label="over-budget proposal draft",
        )

    recurring = copy.deepcopy(unit)
    recurring["admission_basis"] = "recurring_root_error"
    recurring["synthetic_regression_case_ids"] = []
    cluster_id = str(recurring["cluster_ids"][0])
    evidence = {
        "protocol_id": "synthetic-protocol",
        "artifact_sha256": "a" * 64,
        "json_pointer": "/observations/0",
    }
    recurring["evidence_refs"] = [evidence]
    cluster = {
        "finding_codes": ["missing_typed_edge"],
        "evidence_refs": [evidence],
        "root_error_status": "singleton",
    }
    with pytest.raises(CapabilityImprovementError, match="recurring root error"):
        _validate_unit_semantics(
            recurring,
            cluster_index={cluster_id: cluster},
        )
    cluster["root_error_status"] = "recurring_across_protocols"
    _validate_unit_semantics(
        recurring,
        cluster_index={cluster_id: cluster},
    )

    missing_regression = copy.deepcopy(unit)
    missing_regression["synthetic_regression_case_ids"] = []
    with pytest.raises(CapabilityImprovementError, match="negative synthetic"):
        _validate_unit_semantics(missing_regression, cluster_index=None)


def test_compilable_draft_clears_secondary_regression_basis_for_recurring_unit(
    tmp_path: Path,
) -> None:
    unit = _deterministic_unit()
    unit["admission_basis"] = "recurring_root_error"
    draft_path = tmp_path / "proposal_draft.json"
    event_path = tmp_path / "proposal.events.jsonl"
    _write_json(
        draft_path,
        {
            "schema_version": "libstruct.libgen_capability_proposal_draft.v1",
            "change_units": [unit],
        },
    )
    event_path.write_text("{}\n", encoding="utf-8")

    compilable_path, repairs, transcript_path = _prepare_compilable_draft(
        draft_path=draft_path,
        event_log_path=event_path,
        parent_pack_root=tmp_path / "parent",
        candidate_root=tmp_path / "candidate",
    )

    repaired = json.loads(compilable_path.read_text(encoding="utf-8"))
    assert repaired["change_units"][0]["synthetic_regression_case_ids"] == []
    assert repaired["change_units"][0]["fixtures"] == unit["fixtures"]
    assert repairs == [
        {
            "change_id": "synthetic_atomic_unit",
            "field": "synthetic_regression_case_ids",
            "repair": "cleared_for_recurring_root_error_basis",
        }
    ]
    assert transcript_path == event_path
