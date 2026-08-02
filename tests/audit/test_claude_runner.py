from __future__ import annotations

import json
from pathlib import Path

import pytest

from libstruct_bench.audit.claude_runner import (
    ClaudeAuditError,
    _agent_output_schema,
    run_claude_audit,
)
from libstruct_bench.audit.packets import build_phase_packet
from tests.audit.test_packets import _fixture


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCHEMAS = REPO_ROOT / "schemas" / "audit"
PROMPTS = REPO_ROOT / ".claude" / "prompts"
SKILL = REPO_ROOT / ".claude" / "skills" / "audit-protocol" / "SKILL.md"
POLICIES = [
    REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
    REPO_ROOT / "docs" / "audit" / "benchmark-standardization-policy.md",
]


def _fake_claude(path: Path, artifact: dict) -> Path:
    payload = json.dumps({"structured_output": artifact})
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'Claude Code 2.1.0'; exit 0; fi\n"
        f"printf '%s\\n' '{payload}'\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _packet(tmp_path: Path, phase: str, evidence: Path | None = None) -> Path:
    evidence_bytes = evidence.read_bytes() if evidence is not None and evidence.exists() else None
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    if evidence is not None and evidence_bytes is not None:
        evidence.write_bytes(evidence_bytes)
    return build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=tmp_path / f"{phase}-packet",
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        phase=phase,
        evidence_artifact_path=evidence,
    ).output_dir


def _evidence_output() -> dict:
    return {
        "source_coverage": [
            {
                "source_id": "primary:paper",
                "status": "reviewed",
                "tasks": ["T1", "T2", "T3"],
                "portions_reviewed": [{"section": "complete file"}],
            }
        ],
        "t1": {"libraries": []},
        "t2": {"oligos": []},
        "t3": {"workflows": []},
        "summary": "No molecular structure was present in the test fixture.",
    }


def _run(tmp_path: Path, *, phase: str, packet: Path, output: dict):
    return run_claude_audit(
        packet_dir=packet,
        output_dir=tmp_path / f"{phase}-run",
        output_schema_path=AUDIT_SCHEMAS / (
            "protocol_evidence.schema.json" if phase == "evidence" else "protocol_audit.schema.json"
        ),
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        prompt_path=PROMPTS / ("audit-evidence.md" if phase == "evidence" else "audit-comparison.md"),
        skill_path=SKILL,
        policy_paths=POLICIES,
        model="claude-sonnet-4-20250514",
        run_id=f"{phase}-001",
        claude_executable=str(_fake_claude(tmp_path / f"fake-{phase}-claude", output)),
    )


def test_agent_schema_omits_cli_unsupported_dialect_and_injected_fields() -> None:
    for phase, filename in (
        ("evidence", "protocol_evidence.schema.json"),
        ("comparison", "protocol_audit.schema.json"),
    ):
        schema = json.loads((AUDIT_SCHEMAS / filename).read_text())
        relaxed = _agent_output_schema(schema, phase)
        assert "$schema" not in relaxed
        assert "$id" not in relaxed
        assert "run" not in relaxed["required"]
        assert "$schema" in schema


def test_evidence_runner_records_hash_pinned_metadata(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        phase="evidence",
        packet=_packet(tmp_path, "evidence"),
        output=_evidence_output(),
    )
    artifact = json.loads(result.artifact_path.read_text())
    metadata = json.loads(result.metadata_path.read_text())
    assert artifact["evidence_id"] == "example:evidence:evidence-001"
    assert artifact["run"]["model"] == "claude-sonnet-4-20250514"
    assert "schema_version" not in artifact
    assert "schema_version" not in metadata
    assert metadata["artifact_sha256"]


def test_runner_rejects_moving_model_alias(tmp_path: Path) -> None:
    packet = _packet(tmp_path, "evidence")
    with pytest.raises(ClaudeAuditError, match="moving alias"):
        run_claude_audit(
            packet_dir=packet,
            output_dir=tmp_path / "run",
            output_schema_path=AUDIT_SCHEMAS / "protocol_evidence.schema.json",
            packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
            prompt_path=PROMPTS / "audit-evidence.md",
            skill_path=SKILL,
            policy_paths=POLICIES,
            model="sonnet",
            run_id="run-001",
        )


def test_comparison_binds_frozen_evidence_and_current_records(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence_document = {
        "evidence_id": "example:evidence:evidence-001",
        "source_coverage": [{"source_id": "primary:paper"}],
    }
    evidence.write_text(json.dumps(evidence_document), encoding="utf-8")
    packet = _packet(tmp_path, "comparison", evidence)
    output = {
        "disposition": "no_issues",
        "summary": "Current test fields agree.",
        "audited_fields": [
            {
                "field_id": "field-1",
                "task": "T1",
                "object_id": "library",
                "field_path": "/libraries/0/library_sequence",
                "comparison_status": "verified_no_change",
                "issue_ids": [],
            }
        ],
        "issues": [],
    }
    result = _run(tmp_path, phase="comparison", packet=packet, output=output)
    artifact = json.loads(result.artifact_path.read_text())
    assert artifact["evidence_id"] == evidence_document["evidence_id"]
    assert {item["source_id"] for item in artifact["baseline_artifacts"]} == {
        "current:t1", "current:t2"
    }
    assert "candidate_id" not in artifact
    assert "candidate_sha256" not in artifact
