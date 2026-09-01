from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import libstruct_bench.improvement.agent_guided as agent_guided
from libstruct_bench.improvement.agent_guided import (
    _harbor_command,
    _protocol_worker_contract,
    _record_agent_protocol_decision,
    _verifier_lock_changes,
    assert_verifier_lock,
    build_verifier_lock,
)
from libstruct_bench.improvement.artifacts import CapabilityImprovementError


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "schemas" / "improvement"


def test_agent_guided_schemas_are_valid() -> None:
    paths = sorted(SCHEMA_ROOT.glob("agent_*.schema.json"))
    assert {path.name for path in paths} >= {
        "agent_guided_experiment.schema.json",
        "agent_protocol_review_decision_draft.schema.json",
        "agent_checkpoint_sweep.schema.json",
        "agent_checkpoint_sweep_results.schema.json",
    }
    for path in paths:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_verifier_lock_detects_source_and_task_drift(tmp_path: Path) -> None:
    _seed_lock_repository(tmp_path)
    lock = build_verifier_lock(tmp_path)
    assert lock["benchmark_version"]
    assert len(lock["task_contracts"]) == 20
    assert_verifier_lock(lock)

    scoring = tmp_path / "src/libstruct_bench/libgen/scoring.py"
    scoring.write_text("changed\n", encoding="utf-8")
    with pytest.raises(CapabilityImprovementError, match="source_contract"):
        assert_verifier_lock(lock)

    scoring.write_text("fixture\n", encoding="utf-8")
    task_file = tmp_path / "benchmarks/libgen/tasks/protocol_07/task.toml"
    task_file.write_text("changed\n", encoding="utf-8")
    observed = build_verifier_lock(tmp_path)
    assert "task:protocol_07" in _verifier_lock_changes(lock, observed)


def test_single_protocol_workspace_mode_is_schema_valid() -> None:
    schema = json.loads(
        (SCHEMA_ROOT / "worker_workspace_manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    mode = schema["properties"]["mode"]["enum"]
    assert "agent_protocol_proposer" in mode
    assert "agent_protocol_compile_repair" in mode
    assert "agent_protocol_critic" in mode
    assert schema["properties"]["staged_protocol_ids"]["minItems"] == 1


def test_protocol_proposer_contract_exposes_compiler_partition_rule() -> None:
    prompt, schema, draft, events = _protocol_worker_contract(
        mode="agent_protocol_proposer", revision_round=0
    )
    assert "Compiler invariant: every observation grouped" in prompt
    assert "the same `category`" in prompt
    assert "avoid proposing or counting the same pact remedy twice" in prompt
    assert schema == "human_protocol_review_proposal_draft.schema.json"
    assert draft == "outputs/proposal_draft.json"
    assert events == "outputs/proposer.events.jsonl"

    repair_prompt, *_ = _protocol_worker_contract(
        mode="agent_protocol_compile_repair", revision_round=0
    )
    assert "narrow pre-review compiler repair" in repair_prompt
    assert "inputs/review/compiler_feedback.txt" in repair_prompt

    critic_prompt, *_ = _protocol_worker_contract(
        mode="agent_protocol_critic", revision_round=1
    )
    assert "canonical document digest" in critic_prompt
    assert "different from the file's SHA-256" in critic_prompt


def test_checkpoint_sweep_command_opts_into_host_codex_auth(tmp_path: Path) -> None:
    command = _harbor_command(tmp_path / "job.json")
    assert command[:3] == ["env", "CODEX_FORCE_AUTH_JSON=1", "harbor"]


def test_protocol_review_worker_can_read_primary_evidence_formats() -> None:
    dockerfile = (REPO_ROOT / "improvement/worker_runtime/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "file" in dockerfile
    assert "poppler-utils" in dockerfile
    assert "python-is-python3" in dockerfile
    assert "python3-openpyxl" in dockerfile
    assert "unzip" in dockerfile


def test_protocol_decision_recorder_accepts_serialized_event_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_path = tmp_path / "critic.events.jsonl"
    event_path.write_text('{"type":"turn.completed"}\n', encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_record(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "recorded"}

    monkeypatch.setattr(
        agent_guided, "record_human_protocol_review_decision", fake_record
    )
    result = _record_agent_protocol_decision(
        root=tmp_path,
        proposal_path=tmp_path / "proposal.json",
        decision={
            "disposition": "approve",
            "rationale": "independently checked",
            "revision_instruction": None,
        },
        critic_event_path=event_path.as_posix(),
        model="gpt-5.6-sol",
        version="0.150.1",
        reasoning_effort="max",
        created_at="2026-08-28T00:00:00Z",
    )
    assert result == {"status": "recorded"}
    assert len(str(captured["reviewer_transcript_sha256"])) == 64


def _seed_lock_repository(root: Path) -> None:
    files = {
        "src/libstruct_bench/libgen/scoring.py": "fixture\n",
        "src/libstruct_bench/libgen/validation.py": "fixture\n",
        "src/libstruct_bench/matching.py": "fixture\n",
        "src/libstruct_bench/normalization.py": "fixture\n",
        "schemas/analysis/libgen_error_analysis.schema.json": "{}\n",
        "schemas/benchmark/example.schema.json": "{}\n",
        "benchmarks/libgen/matrix.json": "{}\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for index in range(20):
        task = root / "benchmarks/libgen/tasks" / f"protocol_{index:02d}"
        task.mkdir(parents=True)
        (task / "task.toml").write_text("fixture\n", encoding="utf-8")
