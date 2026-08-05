from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from libstruct_bench.audit.claude_runner import (
    ClaudeAuditError,
    _agent_output_schema,
    _claude_result_error,
    _progress_messages,
    run_claude_audit,
)
from libstruct_bench.audit.artifacts import sha256_file
from libstruct_bench.audit.packets import build_phase_packet
from tests.audit.test_packets import _fixture
from tests.audit.test_review_application import _proposal


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


def _frozen_evidence_document() -> dict:
    return {
        "evidence_id": "example:evidence:evidence-001",
        "protocol_id": "example",
        "packet_sha256": "1" * 64,
        "input_manifest_sha256": "2" * 64,
        "run": {
            "run_id": "evidence-001",
            "agent": "claude-code",
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "tool_version": "2.1.0",
            "harness_version": "audit-harness",
            "review_mode": "primary",
            "started_at": "2026-08-01T12:00:00Z",
            "completed_at": "2026-08-01T12:05:00Z",
            "prompt_sha256": "3" * 64,
            "skill_sha256": "4" * 64,
            "policy_sha256": "5" * 64,
            "schema_sha256": sha256_file(
                AUDIT_SCHEMAS / "protocol_evidence.schema.json"
            ),
            "skills": ["audit-protocol"],
            "tools": ["Read"],
            "permission_mode": "plan",
            "checkpoint_id": "checkpoint-0",
        },
        "source_coverage": [
            {
                "source_id": "primary:paper",
                "status": "reviewed",
                "tasks": ["T1", "T2", "T3"],
                "portions_reviewed": [{"page": 1}],
            }
        ],
        "t1": {"libraries": []},
        "t2": {"oligos": []},
        "t3": {"workflows": []},
        "summary": "Test frozen evidence.",
    }


def _duplex_evidence_output(bottom_sequence: str = "CGTT") -> dict:
    output = _evidence_output()
    field = {
        "field_id": "state-architecture",
        "field_path": "/t3/workflows/0/states/0/strand_architecture",
        "value": "double_stranded",
        "support_status": "explicit",
        "evidence": [
            {"source_id": "primary:paper", "locator": {"page": 1}}
        ],
        "transformations": [],
        "confidence": "high",
    }
    output["t3"]["workflows"] = [
        {
            "workflow_id": "workflow",
            "modality": "test",
            "states": [
                {
                    "state_id": "duplex",
                    "strand_architecture": "double_stranded",
                    "reference_strand_id": "top",
                    "strands": [
                        {
                            "strand_id": "top",
                            "name": "Top strand",
                            "molecule_type": "DNA",
                            "orientation": "5_to_3",
                            "segments": [
                                {
                                    "segment_id": "top-paired",
                                    "role": "duplex",
                                    "structural_role": "paired_region",
                                    "sequence": "AACG",
                                }
                            ],
                        },
                        {
                            "strand_id": "bottom",
                            "name": "Bottom strand",
                            "molecule_type": "DNA",
                            "orientation": "5_to_3",
                            "segments": [
                                {
                                    "segment_id": "bottom-paired",
                                    "role": "duplex",
                                    "structural_role": "paired_region",
                                    "sequence": bottom_sequence,
                                }
                            ],
                        },
                    ],
                    "paired_regions": [
                        {
                            "paired_region_id": "duplex-region",
                            "side_1": {
                                "strand_id": "top",
                                "segment_ids": ["top-paired"],
                            },
                            "side_2": {
                                "strand_id": "bottom",
                                "segment_ids": ["bottom-paired"],
                            },
                            "relationship": "reverse_complementary",
                        }
                    ],
                    "discontinuities": [],
                    "fields": [field],
                }
            ],
            "transitions": [],
            "initial_state_ids": ["duplex"],
            "final_state_ids": ["duplex"],
            "fields": [],
        }
    ]
    return output


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


def _replace_packet_file(
    packet_dir: Path, source_id: str, document: dict
) -> None:
    packet_path = packet_dir / "packet.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    item = next(value for value in packet["files"] if value["source_id"] == source_id)
    payload = json.dumps(document).encode()
    artifact_path = packet_dir / item["path"]
    artifact_path.chmod(artifact_path.stat().st_mode | stat.S_IWUSR)
    artifact_path.write_bytes(payload)
    artifact_path.chmod(artifact_path.stat().st_mode & ~stat.S_IWUSR)
    item["sha256"] = hashlib.sha256(payload).hexdigest()
    packet_path.chmod(packet_path.stat().st_mode | stat.S_IWUSR)
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    packet_path.chmod(packet_path.stat().st_mode & ~stat.S_IWUSR)


def _canonical_t1() -> dict:
    return {
        "protocol_id": "example",
        "protocol_name": "Example",
        "libraries": [
            {
                "modality": "test",
                "library_sequence": "A",
                "annotated_library_sequence": "A",
                "strand": "single",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": "library-segment",
                        "kind": "constant",
                        "role": "adapter",
                        "sequence": "A",
                        "orientation": "5_to_3",
                        "support_status": "explicit",
                    }
                ],
                "support_status": "explicit",
            }
        ],
    }


def _root_conversion_proposal(candidate: dict) -> dict:
    value = _proposal("a" * 64)
    issue = value["issues"][0]
    issue.update(
        {
            "category": "formatting_or_schema_error",
            "defect_type": "other",
            "responsibility": "harness",
            "title": "Convert legacy T1 to the canonical shape",
            "target": {
                "kind": "groundtruth_record",
                "artifact_source_id": "current:t1",
                "json_pointer": "",
            },
            "current_value": {"protocol_id": "example", "libraries": []},
            "proposed_value": candidate,
            "proposed_patch": [
                {"op": "replace", "path": "", "value": candidate}
            ],
        }
    )
    return value


def test_agent_schema_omits_cli_unsupported_dialect_and_injected_fields() -> None:
    for phase, filename in (
        ("evidence", "protocol_evidence.schema.json"),
        ("comparison", "protocol_audit.schema.json"),
    ):
        schema = json.loads((AUDIT_SCHEMAS / filename).read_text())
        relaxed = _agent_output_schema(schema, phase)
        assert "$schema" not in relaxed
        assert "$id" not in relaxed
        assert not {"allOf", "anyOf", "oneOf"} & relaxed.keys()
        assert "run" not in relaxed["required"]
        assert "$schema" in schema


def test_canonical_schema_keeps_disposition_issue_invariant() -> None:
    schema = json.loads(
        (AUDIT_SCHEMAS / "protocol_audit.schema.json").read_text()
    )
    assert "allOf" in schema
    artifact = _proposal("a" * 64)
    validator = Draft202012Validator(schema)
    assert validator.is_valid(artifact)
    artifact["disposition"] = "no_issues"

    errors = list(validator.iter_errors(artifact))
    assert any(
        error.validator == "maxItems" and list(error.path) == ["issues"]
        for error in errors
    )


def test_t3_transition_granularity_policy_reaches_both_phase_workers() -> None:
    instruction_paths = [
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        SKILL,
        PROMPTS / "audit-evidence.md",
        PROMPTS / "audit-comparison.md",
    ]
    for path in instruction_paths:
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert "smallest scientifically sufficient" in text
        assert "nearest substantive transition" in text
        assert "cleanup" in text
        assert "size selection" in text
        assert "sequence architecture" in text


def test_terminal_t3_link_contract_reaches_comparison_worker() -> None:
    instruction_paths = [
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        SKILL,
        PROMPTS / "audit-comparison.md",
    ]
    for path in instruction_paths:
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert "sequence_architecture" in text
        assert "simpler" in text or "simplifications" in text
        assert "exact" in text


def test_minimal_groundtruth_contract_reaches_comparison_worker() -> None:
    prompt = (PROMPTS / "audit-comparison.md").read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8")

    assert "do not create a T1 `library_id`" in prompt
    assert "Do not emit `final_library_links`" in prompt
    assert "store T3 `modality` once at the document root" in prompt
    for field in (
        "baseline_lineage",
        "ground_truth_status",
        "workflow_branch",
    ):
        assert field in prompt
        assert field in skill


def test_strand_architecture_contract_reaches_both_phase_workers() -> None:
    instruction_paths = [
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        SKILL,
        PROMPTS / "audit-evidence.md",
        PROMPTS / "audit-comparison.md",
    ]
    for path in instruction_paths:
        text = path.read_text(encoding="utf-8")
        assert "strand_architecture" in text
        assert "reference_strand_id" in text
        assert "reverse_complement" in text
        assert "paired" in text
        assert "5′→3′" in text


def test_evidence_worker_receives_deterministic_state_validator_contract() -> None:
    instruction_paths = [SKILL, PROMPTS / "audit-evidence.md"]
    required_fragments = (
        "meaningful carried-forward products",
        "mRNA:cDNA hybrid",
        "template-switching transition's `oligo_ids`",
        "third TSO strand",
        "`validate_molecular_state_architecture`",
        "`single_stranded` has exactly one strand",
        "`double_stranded` has exactly two strands",
        "`partially_duplex` has at least two strands",
        "`rna_dna_hybrid` has exactly two logical strands",
        "one RNA and one DNA",
        "`y_shaped_duplex` has exactly two",
        "`mixed_population`",
        "every segment labeled `paired_region`",
        "contiguous",
        "reverse-complementary",
        "every discontinuity",
        "adjacent",
        "Do not change scientifically supported",
    )
    for path in instruction_paths:
        text = " ".join(path.read_text(encoding="utf-8").split())
        for fragment in required_fragments:
            assert fragment in text, f"{path} omits validator rule: {fragment}"


def test_prompt_contract_rejections_are_not_attributed_to_human_curation() -> None:
    skill = " ".join(SKILL.read_text(encoding="utf-8").split())

    assert "worker guidance omitted a deterministic validator invariant" in skill
    assert "agent_harness_or_context_error" in skill
    assert "not to human curation" in skill


def test_placeholder_orientation_contract_reaches_both_phase_workers() -> None:
    instruction_paths = [
        REPO_ROOT / "docs" / "audit" / "benchmark-standardization-policy.md",
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        SKILL,
        PROMPTS / "audit-evidence.md",
        PROMPTS / "audit-comparison.md",
    ]
    for path in instruction_paths:
        text = path.read_text(encoding="utf-8")
        assert "[TN5_INDEX:8]" in text
        assert "orientation_to_source" in text
        assert "reverse_complement" in text
        assert "_RC" in text


def test_interactive_controller_requires_claude_question_tool() -> None:
    instruction_paths = [
        REPO_ROOT / "CLAUDE.md",
        SKILL,
        REPO_ROOT / "docs" / "audit" / "adjudication-policy.md",
        REPO_ROOT / ".claude" / "rules" / "groundtruth-audit.md",
    ]
    for path in instruction_paths:
        text = path.read_text(encoding="utf-8")
        assert "AskUserQuestion" in text
    skill_text = " ".join(SKILL.read_text(encoding="utf-8").split())
    assert "do not merely print the disposition options" in skill_text
    assert "Print the complete review card" in skill_text
    assert "Immediately call `AskUserQuestion`" in skill_text


def test_controller_defaults_to_one_review_pass_per_protocol() -> None:
    instruction_paths = [
        REPO_ROOT / "CLAUDE.md",
        SKILL,
        REPO_ROOT / "docs" / "audit" / "README.md",
        REPO_ROOT / "docs" / "audit" / "adjudication-policy.md",
        REPO_ROOT / ".claude" / "rules" / "groundtruth-audit.md",
    ]
    for path in instruction_paths:
        text = " ".join(path.read_text(encoding="utf-8").lower().split())
        assert "one interactive review pass per protocol" in text


def test_source_availability_is_not_a_human_gate() -> None:
    skill = " ".join(SKILL.read_text(encoding="utf-8").split())
    guidance = " ".join(
        (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8").split()
    )
    policy = " ".join(
        (
            REPO_ROOT / "docs" / "audit" / "evidence-policy.md"
        ).read_text(encoding="utf-8").split()
    )

    assert "Include every discovered file that is present and hashable" in skill
    assert "archived catalog contains `pending`" in skill
    assert "Source availability is deterministic and is not a human gate" in guidance
    assert "automatically included" in policy


def test_claude_api_error_is_extracted_from_stdout_result() -> None:
    event = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "result": "API Error: 400 tools.3.custom.input_schema",
        "api_error_status": 400,
    }
    assert _claude_result_error((json.dumps(event) + "\n").encode()) == (
        "API Error: 400 tools.3.custom.input_schema; api_error_status=400"
    )


def test_stream_progress_reports_tools_without_assistant_text(tmp_path: Path) -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "private reasoning is not progress"},
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "Read",
                    "input": {"file_path": str(tmp_path / "primary_evidence" / "paper.pdf")},
                },
            ]
        },
    }
    seen: set[str] = set()
    line = json.dumps(event).encode()
    assert _progress_messages(line, tmp_path, seen) == [
        "reading primary_evidence/paper.pdf"
    ]
    assert _progress_messages(line, tmp_path, seen) == []


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


def test_evidence_runner_accepts_reverse_complementary_duplex(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        phase="evidence",
        packet=_packet(tmp_path, "evidence"),
        output=_duplex_evidence_output(),
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    state = artifact["t3"]["workflows"][0]["states"][0]
    assert state["strand_architecture"] == "double_stranded"
    assert len(state["strands"]) == 2


def test_evidence_runner_rejects_noncomplementary_duplex(tmp_path: Path) -> None:
    with pytest.raises(
        ClaudeAuditError,
        match="invalid strand architecture.*reverse-complementary",
    ):
        _run(
            tmp_path,
            phase="evidence",
            packet=_packet(tmp_path, "evidence"),
            output=_duplex_evidence_output(bottom_sequence="AAAA"),
        )

    assert (tmp_path / "evidence-run.rejected" / "failure.json").is_file()


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
    evidence_document = _frozen_evidence_document()
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
    metadata = json.loads(result.metadata_path.read_text())
    assert artifact["evidence_id"] == evidence_document["evidence_id"]
    assert {item["source_id"] for item in artifact["baseline_artifacts"]} == {
        "current:t1", "current:t2"
    }
    assert "candidate_id" not in artifact
    assert "candidate_sha256" not in artifact
    assert {item["task"] for item in metadata["groundtruth_schemas"]} == {
        "T1",
        "T2",
        "T3",
    }
    assert all(item["sha256"] for item in metadata["groundtruth_schemas"])


def test_comparison_rejects_evidence_from_an_obsolete_schema(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence_document = _frozen_evidence_document()
    evidence_document["run"]["schema_sha256"] = "0" * 64
    evidence.write_text(json.dumps(evidence_document), encoding="utf-8")
    packet = _packet(tmp_path, "comparison", evidence)

    with pytest.raises(
        ClaudeAuditError,
        match="different evidence schema; rerun the evidence phase",
    ):
        _run(
            tmp_path,
            phase="comparison",
            packet=packet,
            output={
                "disposition": "no_issues",
                "summary": "Not reached.",
                "audited_fields": [],
                "issues": [],
            },
        )

    assert not (tmp_path / "comparison-run").exists()
    assert not (tmp_path / "comparison-run.rejected").exists()


def test_comparison_rejects_schema_invalid_new_groundtruth(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(_frozen_evidence_document()), encoding="utf-8")
    packet = _packet(tmp_path, "comparison", evidence)
    invalid_t3 = {"protocol_id": "example", "legacy_steps": []}

    with pytest.raises(
        ClaudeAuditError,
        match="new ground-truth candidate issue-1.*rejected run preserved",
    ):
        _run(
            tmp_path,
            phase="comparison",
            packet=packet,
            output=_proposal("a" * 64, new_t3=invalid_t3),
        )

    rejected_dir = tmp_path / "comparison-run.rejected"
    assert not (tmp_path / "comparison-run").exists()
    assert (rejected_dir / "transcript.jsonl").is_file()
    assert (rejected_dir / "stderr.txt").is_file()
    rejected_artifact = json.loads(
        (rejected_dir / "rejected-artifact.json").read_text(encoding="utf-8")
    )
    failure = json.loads(
        (rejected_dir / "failure.json").read_text(encoding="utf-8")
    )
    assert rejected_artifact["issues"][0]["issue_id"] == "issue-1"
    assert failure["status"] == "rejected"
    assert failure["artifact_path"] == "rejected-artifact.json"
    assert failure["artifact_sha256"]
    assert "new ground-truth candidate issue-1" in failure["reason"]


def test_comparison_requires_root_conversion_for_legacy_baseline(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(_frozen_evidence_document()), encoding="utf-8")
    packet = _packet(tmp_path, "comparison", evidence)
    _replace_packet_file(
        packet,
        "current:t1",
        {"protocol_id": "example", "libraries": []},
    )
    output = {
        "disposition": "no_issues",
        "summary": "No scientific differences.",
        "audited_fields": [
            {
                "field_id": "field-1",
                "task": "T1",
                "object_id": "library",
                "field_path": "/libraries",
                "comparison_status": "verified_no_change",
                "issue_ids": [],
            }
        ],
        "issues": [],
    }

    with pytest.raises(
        ClaudeAuditError,
        match="legacy-shaped baselines require schema-valid root conversion issues: current:t1",
    ):
        _run(tmp_path, phase="comparison", packet=packet, output=output)


def test_comparison_accepts_schema_valid_root_conversion(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(_frozen_evidence_document()), encoding="utf-8")
    packet = _packet(tmp_path, "comparison", evidence)
    _replace_packet_file(
        packet,
        "current:t1",
        {"protocol_id": "example", "libraries": []},
    )

    result = _run(
        tmp_path,
        phase="comparison",
        packet=packet,
        output=_root_conversion_proposal(_canonical_t1()),
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["issues"][0]["proposed_patch"][0]["path"] == ""


def test_root_conversion_cannot_embed_audit_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(_frozen_evidence_document()), encoding="utf-8")
    packet = _packet(tmp_path, "comparison", evidence)
    _replace_packet_file(
        packet,
        "current:t1",
        {"protocol_id": "example", "libraries": []},
    )
    candidate = _canonical_t1()
    primary = [{"source_id": "primary:paper", "locator": {"page": 1}}]
    candidate["libraries"][0]["evidence"] = primary
    candidate["libraries"][0]["segments"][0]["evidence"] = primary

    with pytest.raises(
        ClaudeAuditError,
        match="root conversion candidate issue-1 schema error.*evidence",
    ):
        _run(
            tmp_path,
            phase="comparison",
            packet=packet,
            output=_root_conversion_proposal(candidate),
        )
