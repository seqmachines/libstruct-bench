from __future__ import annotations

import json
from pathlib import Path

import pytest

from libstruct_bench.audit.claude_runner import ClaudeAuditError, run_claude_audit
from libstruct_bench.audit.packets import build_phase_packet
from tests.audit.test_packets import (
    MANIFEST_SCHEMA,
    PACKET_SCHEMA,
    _phase_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCHEMAS = REPO_ROOT / "schemas" / "audit"
SKILL = REPO_ROOT / ".claude" / "skills" / "audit-protocol" / "SKILL.md"
EVIDENCE_PROMPT = REPO_ROOT / ".claude" / "prompts" / "audit-evidence.md"
COMPARISON_PROMPT = REPO_ROOT / ".claude" / "prompts" / "audit-comparison.md"
POLICIES = [
    REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
    REPO_ROOT / "docs" / "audit" / "adjudication-policy.md",
    REPO_ROOT / "docs" / "audit" / "benchmark-standardization-policy.md",
]


def _fake_claude(path: Path, artifact: dict) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('2.1.0 (Claude Code)')\n"
        "else:\n"
        f"    print(json.dumps({json.dumps({'structured_output': artifact})}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _evidence_output(coverage_id: str = "primary-paper") -> dict:
    return {
        "schema_version": "libstruct.protocol_evidence.v1",
        "source_coverage": [
            {
                "source_id": coverage_id,
                "status": "reviewed",
                "tasks": ["T1", "T2", "T3"],
                "portions_reviewed": [{"page": 1}],
            }
        ],
        "t1": {"libraries": []},
        "t2": {"oligos": []},
        "t3": {"workflows": []},
        "summary": "All approved primary sources were reviewed.",
    }


def _evidence_packet(tmp_path: Path) -> Path:
    manifest, source_root, groundtruth_root = _phase_manifest(tmp_path)
    result = build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        output_dir=tmp_path / "evidence-packet",
        manifest_schema_path=MANIFEST_SCHEMA,
        packet_schema_path=PACKET_SCHEMA,
        phase="evidence",
    )
    return result.output_dir


def test_evidence_runner_records_exact_reproducibility_metadata(tmp_path: Path) -> None:
    packet = _evidence_packet(tmp_path)
    executable = _fake_claude(tmp_path / "fake-claude", _evidence_output())
    result = run_claude_audit(
        packet_dir=packet,
        output_dir=tmp_path / "evidence-run",
        output_schema_path=AUDIT_SCHEMAS / "protocol_evidence.v1.schema.json",
        packet_schema_path=PACKET_SCHEMA,
        prompt_path=EVIDENCE_PROMPT,
        skill_path=SKILL,
        policy_paths=POLICIES,
        model="claude-opus-4-1-20250805",
        run_id="evidence-001",
        max_budget_usd=1,
        timeout_seconds=30,
        claude_executable=str(executable),
    )
    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert artifact["protocol_id"] == "example_protocol"
    assert artifact["evidence_id"] == "example_protocol:evidence:evidence-001"
    assert artifact["run"]["model"] == "claude-opus-4-1-20250805"
    assert artifact["run"]["tools"] == ["Read", "Glob", "Grep"]
    assert metadata["artifact_sha256"]
    assert metadata["transcript_sha256"]
    assert "<system-prompt:" in " ".join(metadata["command"])


def test_runner_rejects_moving_model_alias(tmp_path: Path) -> None:
    packet = _evidence_packet(tmp_path)
    executable = _fake_claude(tmp_path / "fake-claude", _evidence_output())
    with pytest.raises(ClaudeAuditError, match="full versioned model ID"):
        run_claude_audit(
            packet_dir=packet,
            output_dir=tmp_path / "evidence-run",
            output_schema_path=AUDIT_SCHEMAS / "protocol_evidence.v1.schema.json",
            packet_schema_path=PACKET_SCHEMA,
            prompt_path=EVIDENCE_PROMPT,
            skill_path=SKILL,
            policy_paths=POLICIES,
            model="opus",
            run_id="evidence-001",
            claude_executable=str(executable),
        )


def test_runner_requires_coverage_of_exactly_the_packet_sources(tmp_path: Path) -> None:
    packet = _evidence_packet(tmp_path)
    executable = _fake_claude(
        tmp_path / "fake-claude", _evidence_output("unlisted-paper")
    )
    with pytest.raises(ClaudeAuditError, match="source coverage mismatch"):
        run_claude_audit(
            packet_dir=packet,
            output_dir=tmp_path / "evidence-run",
            output_schema_path=AUDIT_SCHEMAS / "protocol_evidence.v1.schema.json",
            packet_schema_path=PACKET_SCHEMA,
            prompt_path=EVIDENCE_PROMPT,
            skill_path=SKILL,
            policy_paths=POLICIES,
            model="claude-opus-4-1-20250805",
            run_id="evidence-001",
            max_budget_usd=1,
            timeout_seconds=30,
            claude_executable=str(executable),
        )


def test_comparison_runner_binds_frozen_evidence_and_current_baseline(
    tmp_path: Path,
) -> None:
    evidence_packet = _evidence_packet(tmp_path)
    evidence_executable = _fake_claude(
        tmp_path / "fake-evidence-claude", _evidence_output()
    )
    evidence_run = run_claude_audit(
        packet_dir=evidence_packet,
        output_dir=tmp_path / "evidence-run",
        output_schema_path=AUDIT_SCHEMAS / "protocol_evidence.v1.schema.json",
        packet_schema_path=PACKET_SCHEMA,
        prompt_path=EVIDENCE_PROMPT,
        skill_path=SKILL,
        policy_paths=POLICIES,
        model="claude-opus-4-1-20250805",
        run_id="evidence-001",
        max_budget_usd=1,
        timeout_seconds=30,
        claude_executable=str(evidence_executable),
    )
    manifest = tmp_path / "manifest-v2.json"
    comparison_packet = build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=tmp_path / "source-dataset",
        groundtruth_dataset_dir=tmp_path / "groundtruth-dataset",
        output_dir=tmp_path / "comparison-packet",
        manifest_schema_path=MANIFEST_SCHEMA,
        packet_schema_path=PACKET_SCHEMA,
        phase="comparison",
        evidence_artifact_path=evidence_run.artifact_path,
    )
    comparison_output = {
        "schema_version": "libstruct.protocol_audit.v2",
        "disposition": "no_issues",
        "summary": "Frozen evidence and current records agree.",
        "audited_fields": [
            {
                "field_id": "T1:library-1:sequence",
                "task": "T1",
                "object_id": "library-1",
                "field_path": "/libraries/0/library_sequence",
                "comparison_status": "match",
                "issue_ids": [],
            }
        ],
        "issues": [],
    }
    executable = _fake_claude(tmp_path / "fake-comparison-claude", comparison_output)
    result = run_claude_audit(
        packet_dir=comparison_packet.output_dir,
        output_dir=tmp_path / "comparison-run",
        output_schema_path=AUDIT_SCHEMAS / "protocol_audit.v2.schema.json",
        packet_schema_path=PACKET_SCHEMA,
        prompt_path=COMPARISON_PROMPT,
        skill_path=SKILL,
        policy_paths=POLICIES,
        model="claude-opus-4-1-20250805",
        run_id="comparison-001",
        max_budget_usd=1,
        timeout_seconds=30,
        claude_executable=str(executable),
    )
    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["evidence_id"] == "example_protocol:evidence:evidence-001"
    assert artifact["baseline_artifacts"] == [
        {
            "source_id": "current-t1",
            "sha256": artifact["baseline_artifacts"][0]["sha256"],
        }
    ]
