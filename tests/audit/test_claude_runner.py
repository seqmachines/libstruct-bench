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
    _user_prompt,
    run_claude_audit,
)
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


def _packet(tmp_path: Path) -> Path:
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    return build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=tmp_path / "comparison-packet",
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        phase="comparison",
    ).output_dir


def _run(tmp_path: Path, *, packet: Path, output: dict):
    return run_claude_audit(
        packet_dir=packet,
        output_dir=tmp_path / "comparison-run",
        output_schema_path=AUDIT_SCHEMAS / "protocol_audit.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        prompt_path=PROMPTS / "audit-comparison.md",
        skill_path=SKILL,
        policy_paths=POLICIES,
        model="claude-sonnet-4-20250514",
        run_id="comparison-001",
        claude_executable=str(_fake_claude(tmp_path / "fake-comparison-claude", output)),
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
    schema = json.loads((AUDIT_SCHEMAS / "protocol_audit.schema.json").read_text())
    relaxed = _agent_output_schema(
        schema,
        "comparison",
        primary_source_ids=["primary:z", "primary:a", "primary:a"],
    )
    assert "$schema" not in relaxed
    assert "$id" not in relaxed
    assert not {"allOf", "anyOf", "oneOf"} & relaxed.keys()
    assert "run" not in relaxed["required"]
    assert relaxed["$defs"]["source_coverage"]["properties"]["source_id"] == {
        "type": "string",
        "enum": ["primary:a", "primary:z"],
    }
    assert "$schema" in schema


def test_worker_contract_is_primary_only_and_exposes_link_invariants() -> None:
    inline = " ".join(_user_prompt(Path("/packet"), "comparison").split())
    prompt = " ".join(
        (PROMPTS / "audit-comparison.md").read_text(encoding="utf-8").split()
    )
    skill = " ".join(SKILL.read_text(encoding="utf-8").split())
    policy = " ".join(POLICIES[0].read_text(encoding="utf-8").split())

    assert "every included primary_evidence source exactly once" in inline
    assert "do not list legacy" in inline
    for text in (prompt, skill, policy):
        assert "primary-only" in text
        assert "protocol_scope" in text
        assert "applicable_variants" in text
        assert "issue_ids" in text
        assert "issue_id" in text


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


def test_t3_transition_granularity_policy_reaches_comparison_worker() -> None:
    instruction_paths = [
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        SKILL,
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


def test_strand_architecture_contract_reaches_comparison_worker() -> None:
    instruction_paths = [
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        SKILL,
        PROMPTS / "audit-comparison.md",
    ]
    for path in instruction_paths:
        text = path.read_text(encoding="utf-8")
        assert "strand_architecture" in text
        assert "reference_strand_id" in text
        assert "reverse_complement" in text
        assert "paired" in text
        assert "5′→3′" in text


def test_comparison_worker_receives_deterministic_state_validator_contract() -> None:
    instruction_paths = [SKILL, PROMPTS / "audit-comparison.md"]
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


def test_placeholder_orientation_contract_reaches_comparison_worker() -> None:
    instruction_paths = [
        REPO_ROOT / "docs" / "audit" / "benchmark-standardization-policy.md",
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        SKILL,
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


def test_finalized_review_requires_separate_application_gate() -> None:
    instruction_paths = [
        REPO_ROOT / "CLAUDE.md",
        SKILL,
        REPO_ROOT / "docs" / "audit" / "adjudication-policy.md",
        REPO_ROOT / ".claude" / "rules" / "groundtruth-audit.md",
    ]
    for path in instruction_paths:
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert "finalized" in text
        assert "unpromoted" in text
        assert "application" in text

    skill = SKILL.read_text(encoding="utf-8")
    assert "<!-- audit-application-question-required -->" in skill
    assert "/Users/seqmachines/playground/protocols-test/ground_truth/" in skill
    assert "leave the finalized review" in skill


def test_t3_final_approval_requires_direct_primary_source_fact_check() -> None:
    instruction_paths = [
        REPO_ROOT / "CLAUDE.md",
        SKILL,
        REPO_ROOT / "docs" / "audit" / "adjudication-policy.md",
        REPO_ROOT / ".claude" / "rules" / "groundtruth-audit.md",
    ]
    for path in instruction_paths:
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert "primary PDFs" in text
        assert "supplementary tables" in text
        assert "state and transition" in text
        assert "worker's summary" in text
        for status in ("verified", "conflict", "missing", "ambiguous"):
            assert status in text

    skill = " ".join(SKILL.read_text(encoding="utf-8").split())
    assert skill.index("directly open the immutable packet's primary PDFs") < skill.index(
        "Only then may the controller open the final approval selector"
    )


def test_active_audit_layout_has_no_pilot_namespace() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    assert "ground_truth_audit/<kind>/" in skill
    assert "Do not create or reuse a `pilot/`" in skill
    assert "ground_truth_audit/pilot/" not in skill


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


def test_runner_rejects_moving_model_alias(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    with pytest.raises(ClaudeAuditError, match="moving alias"):
        run_claude_audit(
            packet_dir=packet,
            output_dir=tmp_path / "run",
            output_schema_path=AUDIT_SCHEMAS / "protocol_audit.schema.json",
            packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
            prompt_path=PROMPTS / "audit-comparison.md",
            skill_path=SKILL,
            policy_paths=POLICIES,
            model="sonnet",
            run_id="run-001",
        )


def test_comparison_binds_primary_coverage_and_current_records(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    output = {
        "source_coverage": [
            {
                "source_id": "primary:paper",
                "status": "reviewed",
                "tasks": ["T1", "T2", "T3"],
                "portions_reviewed": [{"section": "complete file"}],
            }
        ],
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
    result = _run(tmp_path, packet=packet, output=output)
    artifact = json.loads(result.artifact_path.read_text())
    metadata = json.loads(result.metadata_path.read_text())
    assert artifact["source_coverage"][0]["source_id"] == "primary:paper"
    assert "evidence_id" not in artifact
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


def test_comparison_reports_issue_missing_from_field_ledger(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    output = _proposal("a" * 64)
    orphan = dict(output["issues"][0])
    orphan.update(
        {
            "issue_id": "issue-2",
            "target": {"kind": "source_bundle"},
            "recommendation": "needs_human_review",
            "proposed_patch": [],
        }
    )
    output["issues"].append(orphan)

    with pytest.raises(
        ClaudeAuditError,
        match=r"issues_missing_from_ledger=\['issue-2'\]",
    ):
        _run(tmp_path, packet=packet, output=output)


def test_comparison_rejects_schema_invalid_new_groundtruth(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    invalid_t3 = {"protocol_id": "example", "legacy_steps": []}

    with pytest.raises(
        ClaudeAuditError,
        match="new ground-truth candidate issue-1.*rejected run preserved",
    ):
        _run(
            tmp_path,
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
    packet = _packet(tmp_path)
    _replace_packet_file(
        packet,
        "current:t1",
        {"protocol_id": "example", "libraries": []},
    )
    output = {
        "source_coverage": [
            {
                "source_id": "primary:paper",
                "status": "reviewed",
                "tasks": ["T1", "T2", "T3"],
                "portions_reviewed": [{"section": "complete file"}],
            }
        ],
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
        _run(tmp_path, packet=packet, output=output)


def test_comparison_accepts_schema_valid_root_conversion(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path)
    _replace_packet_file(
        packet,
        "current:t1",
        {"protocol_id": "example", "libraries": []},
    )

    result = _run(
        tmp_path,
        packet=packet,
        output=_root_conversion_proposal(_canonical_t1()),
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["issues"][0]["proposed_patch"][0]["path"] == ""


def test_root_conversion_cannot_embed_audit_evidence(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
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
            packet=packet,
            output=_root_conversion_proposal(candidate),
        )
