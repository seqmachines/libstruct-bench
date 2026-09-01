from __future__ import annotations

import copy
import hashlib
import json
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from libstruct_bench.audit.claude_runner import (
    ClaudeAuditError,
    _assert_repair_scope,
    _agent_output_schema,
    _claude_result_error,
    _progress_messages,
    _user_prompt,
    revalidate_rejected_comparison,
    run_claude_audit,
)
from libstruct_bench.audit.packets import build_phase_packet
from tests.audit.test_packets import _fixture
from tests.audit.test_review_application import _proposal


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCHEMAS = REPO_ROOT / "schemas" / "audit"
PROMPTS = REPO_ROOT / ".claude" / "prompts"
REPAIR_PROMPT = PROMPTS / "audit-comparison-repair.md"
SKILL = REPO_ROOT / ".claude" / "skills" / "audit-protocol" / "SKILL.md"
POLICIES = [
    REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
    REPO_ROOT / "docs" / "audit" / "benchmark-standardization-policy.md",
]


def _fake_claude(path: Path, artifact: dict) -> Path:
    payload = json.dumps({"structured_output": artifact})
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'Claude Code 2.1.0\'; exit 0; fi\n'
        f"printf '%s\\n' '{payload}'\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _fake_claude_sequence(path: Path, artifacts: list[dict]) -> Path:
    responses_path = path.with_suffix(".responses.json")
    counter_path = path.with_suffix(".counter")
    responses_path.write_text(json.dumps(artifacts), encoding="utf-8")
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"responses_path = Path({str(responses_path)!r})\n"
        f"counter_path = Path({str(counter_path)!r})\n"
        "if '--version' in sys.argv:\n"
        "    print('Claude Code 2.1.0')\n"
        "    raise SystemExit(0)\n"
        "responses = json.loads(responses_path.read_text(encoding='utf-8'))\n"
        "index = int(counter_path.read_text()) if counter_path.exists() else 0\n"
        "counter_path.write_text(str(index + 1))\n"
        "artifact = responses[min(index, len(responses) - 1)]\n"
        "print(json.dumps({'structured_output': artifact}))\n",
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


def _conversion_packet(tmp_path: Path) -> Path:
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    return build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=tmp_path / "legacy-conversion-packet",
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        phase="legacy_conversion",
    ).output_dir


def _staged_conversion_artifact() -> dict:
    candidates = copy.deepcopy(_conversion_output()["candidates"])
    return {
        "conversion_id": "example:legacy-conversion:conversion-001",
        "protocol_id": "example",
        "packet_sha256": "1" * 64,
        "input_manifest_sha256": "2" * 64,
        "status": "unapproved_legacy_conversion",
        "run": {
            "run_id": "conversion-001",
            "agent": "claude-code",
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "tool_version": "2.1.0",
            "harness_version": "test",
            "review_mode": "primary",
            "started_at": "2026-08-01T12:00:00Z",
            "completed_at": "2026-08-01T12:01:00Z",
            "prompt_sha256": "3" * 64,
            "skill_sha256": "4" * 64,
            "policy_sha256": "5" * 64,
            "schema_sha256": "6" * 64,
            "skills": ["audit-protocol"],
            "tools": ["Read"],
            "permission_mode": "plan",
            "checkpoint_id": "checkpoint-0",
        },
        "candidates": candidates,
        "candidate_sha256s": {
            task: hashlib.sha256(
                json.dumps(
                    candidate,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            for task, candidate in candidates.items()
        },
        "lineage": copy.deepcopy(_conversion_output()["lineage"]),
        "notes": [],
    }


def _staged_packet(
    tmp_path: Path, *, legacy_tasks: tuple[str, ...] = ()
) -> Path:
    manifest, source_root, groundtruth_root, run_root = _fixture(tmp_path)
    conversion_path = tmp_path / "conversion.json"
    conversion_path.write_text(
        json.dumps(_staged_conversion_artifact()), encoding="utf-8"
    )
    packet = build_phase_packet(
        manifest_path=manifest,
        source_dataset_dir=source_root,
        groundtruth_dataset_dir=groundtruth_root,
        run_artifact_dir=run_root,
        output_dir=tmp_path / "comparison-packet",
        manifest_schema_path=AUDIT_SCHEMAS / "audit_input_manifest.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        phase="comparison",
        legacy_conversion_path=conversion_path,
        legacy_conversion_schema_path=AUDIT_SCHEMAS
        / "legacy_conversion.schema.json",
    ).output_dir
    if "T1" in legacy_tasks:
        _replace_packet_file(
            packet,
            "current:t1",
            {"protocol_id": "example", "libraries": []},
        )
    if "T2" in legacy_tasks:
        _replace_packet_file(
            packet,
            "current:t2",
            {"protocol_id": "example", "oligos": []},
        )
    return packet


def _comparison_without_scientific_issues() -> dict:
    return {
        "source_coverage": [
            {
                "source_id": "primary:paper",
                "status": "reviewed",
                "tasks": ["T1", "T2", "T3"],
                "portions_reviewed": [{"section": "complete file"}],
            }
        ],
        "disposition": "no_issues",
        "summary": "No primary-evidence differences were found.",
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


def _comparison_with_three_prose_root_mirrors() -> dict:
    output = _comparison_without_scientific_issues()
    output["disposition"] = "issues_proposed"
    output["audited_fields"] = []
    candidates = _staged_conversion_artifact()["candidates"]
    conversion_id = "example:legacy-conversion:conversion-001"
    for task in ("T1", "T2", "T3"):
        issue_id = f"issue-{task.lower()}-root"
        field_id = f"field-{task.lower()}-root"
        output["audited_fields"].append(
            {
                "field_id": field_id,
                "task": task,
                "object_id": f"{task.lower()}-document",
                "field_path": "/",
                "comparison_status": "proposed_correction",
                "issue_ids": [issue_id],
            }
        )
        is_new = task == "T3"
        target_source_id = conversion_id if is_new else f"current:{task.lower()}"
        output["issues"].append(
            {
                "issue_id": issue_id,
                "task": task,
                "field_id": field_id,
                "category": "formatting_or_schema_error",
                "defect_type": "other",
                "responsibility": "policy",
                "severity": "medium",
                "title": f"Canonical {task} migration",
                "target": {
                    "kind": (
                        "new_groundtruth_record"
                        if is_new
                        else "groundtruth_record"
                    ),
                    "artifact_source_id": target_source_id,
                    "artifact_filename": {
                        "T1": "groundtruth_final_lib_struct.json",
                        "T2": "groundtruth_oligos.json",
                        "T3": "groundtruth_library_generation_workflow.json",
                    }[task],
                    "json_pointer": "",
                },
                "current_value": None,
                "proposed_value": f"The complete frozen {task} document.",
                "support_status": "derivable",
                "evidence": [
                    {
                        "source_id": "legacy:html",
                        "locator": {"html_selector": "body"},
                        "supports": "current",
                    }
                ],
                "transformations": [],
                "explanation": "Schema migration only.",
                "recommendation": "propose_change",
                "proposed_patch": [
                    {
                        "op": "add" if is_new else "replace",
                        "path": "",
                        "value": copy.deepcopy(candidates[task]),
                    }
                ],
                "confidence": "high",
                "run_id": "comparison-001",
                "checkpoint_id": "checkpoint-0",
            }
        )
    return output


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
        claude_executable=str(
            _fake_claude(tmp_path / "fake-comparison-claude", output)
        ),
    )


def _run_sequence(
    tmp_path: Path,
    *,
    packet: Path,
    outputs: list[dict],
    max_repair_attempts: int = 2,
):
    return run_claude_audit(
        packet_dir=packet,
        output_dir=tmp_path / "comparison-run",
        output_schema_path=AUDIT_SCHEMAS / "protocol_audit.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        prompt_path=PROMPTS / "audit-comparison.md",
        repair_prompt_path=REPAIR_PROMPT,
        skill_path=SKILL,
        policy_paths=POLICIES,
        model="claude-sonnet-4-20250514",
        run_id="comparison-001",
        max_repair_attempts=max_repair_attempts,
        claude_executable=str(
            _fake_claude_sequence(tmp_path / "fake-comparison-claude", outputs)
        ),
    )


def _replace_packet_file(packet_dir: Path, source_id: str, document: dict) -> None:
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
                "modality": "gene expression",
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


def _canonical_t3(sequence: str = "A") -> dict:
    def state(state_id: str, role: str, value: str) -> dict:
        strand_id = f"{state_id}-strand"
        return {
            "state_id": state_id,
            "name": state_id.title(),
            "molecule_type": "DNA",
            "strand_architecture": "single_stranded",
            "reference_strand_id": strand_id,
            "physical_state": "solution",
            "strands": [
                {
                    "strand_id": strand_id,
                    "name": f"{state_id.title()} strand",
                    "molecule_type": "DNA",
                    "orientation": "5_to_3",
                    "sequence_architecture": value,
                    "segments": [
                        {
                            "segment_id": f"{state_id}-segment",
                            "role": role,
                            "structural_role": "unpaired",
                            "sequence": value,
                        }
                    ],
                    "support_status": "explicit",
                }
            ],
            "paired_regions": [],
            "discontinuities": [],
            "properties": [],
            "support_status": "explicit",
        }

    return {
        "protocol_id": "example",
        "protocol_name": "Example",
        "workflows": [
            {
                "workflow_id": "workflow",
                "states": [
                    state("input", "input", "G"),
                    state("final", "adapter", sequence),
                ],
                "transitions": [
                    {
                        "transition_id": "extension",
                        "substrate_state_ids": ["input"],
                        "operation": "extension",
                        "operation_detail": None,
                        "oligo_ids": [],
                        "major_reagents": [],
                        "product_state_ids": ["final"],
                        "carried_forward_product_ids": ["final"],
                        "discarded_product_ids": [],
                        "support_status": "explicit",
                    }
                ],
                "initial_state_ids": ["input"],
                "final_outputs": [{"state_id": "final", "modality": "gene expression"}],
            }
        ],
    }


def _conversion_output() -> dict:
    return {
        "candidates": {
            "T1": _canonical_t1(),
            "T2": {
                "protocol_id": "example",
                "protocol_name": "Example",
                "oligos": [],
            },
            "T3": _canonical_t3(),
        },
        "lineage": [
            {
                "task": "T1",
                "source_ids": ["current:t1"],
                "summary": "Canonical representation of current T1.",
            },
            {
                "task": "T2",
                "source_ids": ["current:t2", "current:tsv"],
                "summary": "Canonical representation of current T2 and TSV.",
            },
            {
                "task": "T3",
                "source_ids": ["legacy:html"],
                "summary": "Workflow translated from ordered legacy steps.",
            },
        ],
        "notes": [],
    }


def test_legacy_conversion_run_is_linked_hash_pinned_and_unapproved(
    tmp_path: Path,
) -> None:
    packet = _conversion_packet(tmp_path)
    result = run_claude_audit(
        packet_dir=packet,
        output_dir=tmp_path / "legacy-conversion-run",
        output_schema_path=AUDIT_SCHEMAS / "legacy_conversion.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        prompt_path=PROMPTS / "audit-legacy-conversion.md",
        skill_path=SKILL,
        policy_paths=POLICIES,
        model="claude-sonnet-4-20250514",
        run_id="conversion-001",
        max_repair_attempts=0,
        claude_executable=str(
            _fake_claude(tmp_path / "fake-conversion-claude", _conversion_output())
        ),
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert result.artifact_path.name == "conversion.json"
    assert artifact["status"] == "unapproved_legacy_conversion"
    assert artifact["conversion_id"] == (
        "example:legacy-conversion:conversion-001"
    )
    assert set(artifact["candidate_sha256s"]) == {"T1", "T2", "T3"}
    packet_document = json.loads((packet / "packet.json").read_text(encoding="utf-8"))
    assert not {
        "primary_evidence",
        "benchmark_run_artifact",
    } & {item["role"] for item in packet_document["files"]}


def test_legacy_conversion_gets_bounded_representation_repair(
    tmp_path: Path,
) -> None:
    packet = _conversion_packet(tmp_path)
    invalid = _conversion_output()
    invalid["candidates"]["T3"] = _canonical_t3("C")
    result = run_claude_audit(
        packet_dir=packet,
        output_dir=tmp_path / "legacy-conversion-run",
        output_schema_path=AUDIT_SCHEMAS / "legacy_conversion.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
        prompt_path=PROMPTS / "audit-legacy-conversion.md",
        repair_prompt_path=PROMPTS / "audit-legacy-conversion-repair.md",
        skill_path=SKILL,
        policy_paths=POLICIES,
        model="claude-sonnet-4-20250514",
        run_id="conversion-001",
        max_repair_attempts=1,
        claude_executable=str(
            _fake_claude_sequence(
                tmp_path / "fake-conversion-claude",
                [invalid, _conversion_output()],
            )
        ),
    )

    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["validation_repair"]["status"] == "succeeded"
    assert metadata["validation_repair"]["attempt_count"] == 1
    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["candidates"]["T3"] == _canonical_t3()


def _paired_terminal_t3(*, include_terminal_pair: bool) -> dict:
    document = _canonical_t3("A")
    final_state = {
        "state_id": "final",
        "name": "Final duplex",
        "molecule_type": "DNA",
        "strand_architecture": "double_stranded",
        "reference_strand_id": "top",
        "physical_state": "solution",
        "strands": [
            {
                "strand_id": "top",
                "name": "Top strand",
                "molecule_type": "DNA",
                "orientation": "5_to_3",
                "sequence_architecture": "AC",
                "segments": [
                    {
                        "segment_id": "top-left",
                        "role": "adapter",
                        "structural_role": "paired_region",
                        "sequence": "A",
                    },
                    {
                        "segment_id": "top-terminal",
                        "role": "adapter",
                        "structural_role": "paired_region",
                        "sequence": "C",
                    },
                ],
                "support_status": "explicit",
            },
            {
                "strand_id": "bottom",
                "name": "Bottom strand",
                "molecule_type": "DNA",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": "bottom-terminal",
                        "role": "adapter",
                        "structural_role": "paired_region",
                        "sequence": "G",
                    },
                    {
                        "segment_id": "bottom-right",
                        "role": "adapter",
                        "structural_role": "paired_region",
                        "sequence": "T",
                    },
                ],
                "support_status": "explicit",
            },
        ],
        "paired_regions": [
            {
                "paired_region_id": "left-pair",
                "side_1": {"strand_id": "top", "segment_ids": ["top-left"]},
                "side_2": {
                    "strand_id": "bottom",
                    "segment_ids": ["bottom-right"],
                },
                "relationship": "reverse_complementary",
                "support_status": "explicit",
            }
        ],
        "discontinuities": [],
        "properties": [],
        "support_status": "explicit",
    }
    if include_terminal_pair:
        final_state["paired_regions"].append(
            {
                "paired_region_id": "terminal-pair",
                "side_1": {
                    "strand_id": "top",
                    "segment_ids": ["top-terminal"],
                },
                "side_2": {
                    "strand_id": "bottom",
                    "segment_ids": ["bottom-terminal"],
                },
                "relationship": "reverse_complementary",
                "support_status": "explicit",
            }
        )
    document["workflows"][0]["states"][1] = final_state
    return document


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
            "proposed_patch": [{"op": "replace", "path": "", "value": candidate}],
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
    assert "must never appear in `issues[].evidence[].source_id`" in prompt
    assert "not issue evidence" in skill
    assert "not an independent evidence source" in policy
    assert "do not serialize the complete frozen T1, T2, or T3" in prompt
    assert "deterministic harness attaches" in prompt
    assert "do not echo the complete documents" in skill
    assert "deterministic harness attaches" in inline


def test_canonical_schema_keeps_disposition_issue_invariant() -> None:
    schema = json.loads((AUDIT_SCHEMAS / "protocol_audit.schema.json").read_text())
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


def test_ordered_segment_projection_contract_reaches_both_workers() -> None:
    instruction_paths = [
        PROMPTS / "audit-legacy-conversion.md",
        PROMPTS / "audit-comparison.md",
        SKILL,
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        REPO_ROOT / "docs" / "audit" / "benchmark-standardization-policy.md",
    ]
    for path in instruction_paths:
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert "ordered" in text
        assert "segment" in text
        assert "projection" in text
        assert "consume" in text
        assert "UMI" in text


def test_minimal_groundtruth_contract_reaches_comparison_worker() -> None:
    prompt = (PROMPTS / "audit-comparison.md").read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8")

    assert "do not create a T1 `library_id`" in prompt
    assert "Do not emit `final_library_links`" in prompt
    assert "one workflow" in prompt
    assert "connected T3 molecular process" in prompt
    assert "final_outputs" in prompt
    assert "shared ancestors" in prompt
    for field in (
        "baseline_lineage",
        "ground_truth_status",
        "workflow_branch",
    ):
        assert field in prompt
        assert field in skill


def test_in_progress_t3_connected_process_migration_reaches_controller() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    policy = (REPO_ROOT / "docs" / "audit" / "adjudication-policy.md").read_text(
        encoding="utf-8"
    )

    for raw_text in (skill, policy):
        text = " ".join(raw_text.split())
        assert "unfinalized proposal" in text
        assert "proposal immutable" in text
        assert "one workflow per connected molecular process" in text
        assert "final_outputs" in text
        assert "Freeze" in text or "freeze" in text
        assert "ambiguous" in text


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


def test_canonical_modality_vocabulary_reaches_audit_worker() -> None:
    instruction_paths = [
        REPO_ROOT / "docs" / "audit" / "benchmark-standardization-policy.md",
        REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
        SKILL,
        PROMPTS / "audit-comparison.md",
    ]
    for path in instruction_paths:
        text = path.read_text(encoding="utf-8")
        for modality in (
            "gene expression",
            "genomic DNA",
            "feature barcode",
            "sgRNA",
            "chromatin accessibility",
        ):
            assert modality in text


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
        "`partially_duplex` has at least one strand",
        "`rna_dna_hybrid` has exactly two logical strands",
        "one RNA and one DNA",
        "`y_shaped_duplex` has exactly two",
        "every segment labeled `paired_region`",
        "contiguous",
        "intramolecular",
        "non-overlapping",
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


def test_repair_worker_is_evidence_isolated_and_scope_bounded() -> None:
    prompt = " ".join(REPAIR_PROMPT.read_text(encoding="utf-8").split())

    assert "Read only `repair-input.json`" in prompt
    assert "Do not open the comparison packet" in prompt
    assert "do not add or remove a finding" in prompt
    assert "do not change scientifically supported sequence content" in prompt
    assert "smallest change necessary" in prompt
    assert "re-run the full audit schema" in prompt
    assert "legacy_conversion_candidate" in prompt
    assert "at least one other admissible evidence entry remains" in prompt


def test_repair_scope_allows_only_deleting_frozen_conversion_evidence() -> None:
    initial = _proposal("a" * 64)
    conversion_source_id = "example:legacy-conversion:conversion-001"
    initial["issues"][0]["evidence"].append(
        {
            "source_id": conversion_source_id,
            "locator": {"section": "candidates.T1"},
            "supports": "current",
            "excerpt": "Frozen claim",
        }
    )
    repaired = copy.deepcopy(initial)
    repaired["issues"][0]["evidence"] = [
        item
        for item in repaired["issues"][0]["evidence"]
        if item["source_id"] != conversion_source_id
    ]

    _assert_repair_scope(
        initial,
        repaired,
        removable_evidence_source_ids={conversion_source_id},
    )

    changed_locator = copy.deepcopy(repaired)
    changed_locator["issues"][0]["evidence"][0]["locator"] = {"page": 99}
    with pytest.raises(ClaudeAuditError, match="changed evidence beyond deleting"):
        _assert_repair_scope(
            initial,
            changed_locator,
            removable_evidence_source_ids={conversion_source_id},
        )

    removed_admissible = copy.deepcopy(repaired)
    removed_admissible["issues"][0]["evidence"] = []
    with pytest.raises(ClaudeAuditError, match="changed evidence beyond deleting"):
        _assert_repair_scope(
            initial,
            removed_admissible,
            removable_evidence_source_ids={conversion_source_id},
        )


def test_citation_repair_can_preserve_an_unrelated_root_mirror_error() -> None:
    initial = _proposal("a" * 64, new_t3=_canonical_t3())
    conversion_source_id = "example:legacy-conversion:conversion-001"
    initial["issues"][0]["evidence"].append(
        {
            "source_id": conversion_source_id,
            "locator": {"section": "candidates.T3"},
            "supports": "current",
            "excerpt": "Frozen claim",
        }
    )
    initial["issues"][0]["proposed_value"] = copy.deepcopy(_canonical_t3())
    initial["issues"][0]["proposed_value"]["protocol_name"] = "Stale mirror"
    repaired = copy.deepcopy(initial)
    repaired["issues"][0]["evidence"] = repaired["issues"][0]["evidence"][:-1]

    _assert_repair_scope(
        initial,
        repaired,
        removable_evidence_source_ids={conversion_source_id},
    )


def test_comparison_repairs_root_candidate_mirror_mismatch(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    repaired = _proposal("a" * 64, new_t3=_canonical_t3())
    initial = copy.deepcopy(repaired)
    initial["issues"][0]["proposed_value"] = copy.deepcopy(_canonical_t3())
    initial["issues"][0]["proposed_value"]["protocol_name"] = "Stale mirror"

    result = _run_sequence(
        tmp_path,
        packet=packet,
        outputs=[initial, repaired],
    )

    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    attempt = metadata["validation_repair"]["attempts"][0]
    errors = json.loads(
        (result.output_dir / attempt["validator_errors_path"]).read_text(
            encoding="utf-8"
        )
    )["errors"]
    assert metadata["validation_repair"]["status"] == "succeeded"
    assert "must serialize the same complete document" in errors[0]


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
    assert skill.index(
        "directly open the immutable packet's primary PDFs"
    ) < skill.index("Only then may the controller open the final approval selector")


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
    guidance = " ".join((REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8").split())
    policy = " ".join(
        (REPO_ROOT / "docs" / "audit" / "evidence-policy.md")
        .read_text(encoding="utf-8")
        .split()
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
                    "input": {
                        "file_path": str(tmp_path / "primary_evidence" / "paper.pdf")
                    },
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
        "current:t1",
        "current:t2",
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


def test_comparison_repairs_complete_schema_invalid_artifact_once(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path)
    invalid_t3 = {"protocol_id": "example", "legacy_steps": []}
    repaired_t3 = _canonical_t3()
    initial = _proposal("a" * 64, new_t3=invalid_t3)
    repaired = _proposal("a" * 64, new_t3=repaired_t3)

    result = _run_sequence(
        tmp_path,
        packet=packet,
        outputs=[initial, repaired],
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    repair = metadata["validation_repair"]
    assert artifact["issues"][0]["proposed_value"] == repaired_t3
    assert repair["status"] == "succeeded"
    assert repair["attempt_count"] == 1
    attempt = repair["attempts"][0]
    assert attempt["status"] == "validated"
    assert attempt["tools"] == ["Read"]
    assert (result.output_dir / attempt["input_artifact_path"]).is_file()
    assert (result.output_dir / attempt["candidate_artifact_path"]).is_file()
    preserved_input = json.loads(
        (result.output_dir / attempt["input_artifact_path"]).read_text(encoding="utf-8")
    )
    assert preserved_input["issues"][0]["proposed_value"] == invalid_t3
    validator_errors = json.loads(
        (result.output_dir / attempt["validator_errors_path"]).read_text(
            encoding="utf-8"
        )
    )
    assert (
        "new ground-truth candidate issue-1 schema error"
        in validator_errors["errors"][0]
    )


def test_comparison_repairs_linked_t1_t3_validation_failure(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    linked_t1 = _canonical_t1()
    linked_t1["libraries"][0]["library_sequence"] = "AC"
    linked_t1["libraries"][0]["segments"][0]["sequence"] = "AC"
    _replace_packet_file(packet, "current:t1", linked_t1)
    initial = _proposal(
        "a" * 64,
        new_t3=_paired_terminal_t3(include_terminal_pair=False),
    )
    repaired_t3 = _paired_terminal_t3(include_terminal_pair=True)
    repaired = _proposal("a" * 64, new_t3=repaired_t3)

    result = _run_sequence(
        tmp_path,
        packet=packet,
        outputs=[initial, repaired],
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    attempt = metadata["validation_repair"]["attempts"][0]
    errors = json.loads(
        (result.output_dir / attempt["validator_errors_path"]).read_text(
            encoding="utf-8"
        )
    )["errors"]
    assert artifact["issues"][0]["proposed_value"] == repaired_t3
    assert metadata["validation_repair"]["status"] == "succeeded"
    assert "linked root conversion candidates are inconsistent" in errors[0]
    assert "top-terminal" in errors[0]


def test_comparison_repair_rejects_changed_issue_conclusion(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    initial = _proposal(
        "a" * 64,
        new_t3={"protocol_id": "example", "legacy_steps": []},
    )
    out_of_scope = _proposal("a" * 64, new_t3=_canonical_t3())
    out_of_scope["summary"] = "The repair silently changed the conclusion."

    with pytest.raises(
        ClaudeAuditError,
        match="repair changed protected comparison field 'summary'",
    ):
        _run_sequence(
            tmp_path,
            packet=packet,
            outputs=[initial, out_of_scope],
        )

    failure = json.loads(
        (tmp_path / "comparison-run.rejected/failure.json").read_text(encoding="utf-8")
    )
    assert failure["validation_repair"]["status"] == "failed"
    assert failure["validation_repair"]["attempt_count"] == 1
    assert failure["validation_repair"]["attempts"][0]["status"] == "rejected"


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
    failure = json.loads((rejected_dir / "failure.json").read_text(encoding="utf-8"))
    assert rejected_artifact["issues"][0]["issue_id"] == "issue-1"
    assert rejected_artifact["issues"][0]["proposed_value"] == invalid_t3
    assert failure["status"] == "rejected"
    assert failure["artifact_path"] == "rejected-artifact.json"
    assert failure["artifact_sha256"]
    assert "new ground-truth candidate issue-1" in failure["reason"]
    assert failure["validation_repair"]["status"] == "exhausted"
    assert failure["validation_repair"]["attempt_count"] == 2
    assert all(
        item["status"] == "validation_failed"
        for item in failure["validation_repair"]["attempts"]
    )


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


def test_staged_comparison_attaches_all_required_roots_without_model_repair(
    tmp_path: Path,
) -> None:
    packet = _staged_packet(tmp_path, legacy_tasks=("T1", "T2"))

    result = _run(
        tmp_path,
        packet=packet,
        output=_comparison_without_scientific_issues(),
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    roots = {issue["task"]: issue for issue in artifact["issues"]}
    assert set(roots) == {"T1", "T2", "T3"}
    assert roots["T1"]["target"]["kind"] == "groundtruth_record"
    assert roots["T2"]["target"]["kind"] == "groundtruth_record"
    assert roots["T3"]["target"] == {
        "kind": "new_groundtruth_record",
        "artifact_source_id": "example:legacy-conversion:conversion-001",
        "artifact_filename": "groundtruth_library_generation_workflow.json",
        "json_pointer": "",
    }
    for issue in roots.values():
        assert issue["proposed_value"] == issue["proposed_patch"][0]["value"]
    assert metadata["validation_repair"]["status"] == "not_needed"
    normalization = metadata["deterministic_normalization"]
    assert normalization["status"] == "applied"
    assert normalization["attached_tasks"] == ["T1", "T2", "T3"]
    generated = json.loads(
        (result.output_dir / normalization["generated_artifact_path"]).read_text(
            encoding="utf-8"
        )
    )
    assert generated["issues"] == []
    assert artifact["disposition"] == "issues_proposed"


def test_staged_comparison_normalizes_three_prose_root_mirrors_in_one_pass(
    tmp_path: Path,
) -> None:
    packet = _staged_packet(tmp_path, legacy_tasks=("T1", "T2"))

    result = _run(
        tmp_path,
        packet=packet,
        output=_comparison_with_three_prose_root_mirrors(),
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["validation_repair"]["attempt_count"] == 0
    assert metadata["deterministic_normalization"]["status"] == "applied"
    assert len(artifact["issues"]) == 3
    for issue in artifact["issues"]:
        assert isinstance(issue["proposed_value"], dict)
        assert issue["proposed_value"] == issue["proposed_patch"][0]["value"]


def test_staged_comparison_rejects_a_scientifically_changed_frozen_root(
    tmp_path: Path,
) -> None:
    packet = _staged_packet(tmp_path, legacy_tasks=("T1", "T2"))
    output = _comparison_with_three_prose_root_mirrors()
    output["issues"][0]["proposed_patch"][0]["value"][
        "protocol_name"
    ] = "Changed by comparison worker"

    with pytest.raises(
        ClaudeAuditError,
        match="changed the frozen legacy conversion candidate for T1",
    ):
        _run(tmp_path, packet=packet, output=output)


def test_staged_root_attachment_does_not_invent_missing_scientific_issues(
    tmp_path: Path,
) -> None:
    packet = _staged_packet(tmp_path, legacy_tasks=("T1", "T2"))
    output = _comparison_without_scientific_issues()
    output["audited_fields"].append(
        {
            "field_id": "scientific-field",
            "task": "T3",
            "object_id": "transition",
            "field_path": "/workflows/0/transitions/0",
            "comparison_status": "proposed_correction",
            "issue_ids": ["missing-scientific-issue"],
        }
    )

    with pytest.raises(
        ClaudeAuditError,
        match="unknown_issue_ids_in_ledger=.*missing-scientific-issue",
    ):
        _run_sequence(
            tmp_path,
            packet=packet,
            outputs=[output],
            max_repair_attempts=0,
        )

    rejected = json.loads(
        (tmp_path / "comparison-run.rejected/rejected-artifact.json").read_text(
            encoding="utf-8"
        )
    )
    assert "missing-scientific-issue" not in {
        issue["issue_id"] for issue in rejected["issues"]
    }
    assert {
        issue["task"] for issue in rejected["issues"]
    } == {"T1", "T2", "T3"}


def test_complete_rejected_comparison_can_be_revalidated_without_model_call(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    packet = _staged_packet(source_root, legacy_tasks=("T1", "T2"))
    initial = _run(
        source_root,
        packet=packet,
        output=_comparison_with_three_prose_root_mirrors(),
    )
    generated = initial.output_dir / "generated-artifact.json"
    rejected_dir = tmp_path / "comparison-old.rejected"
    rejected_dir.mkdir()
    artifact_path = rejected_dir / "rejected-artifact.json"
    artifact_path.write_bytes(generated.read_bytes())
    failure = {
        "status": "rejected",
        "phase": "comparison",
        "packet_sha256": hashlib.sha256(
            (packet / "packet.json").read_bytes()
        ).hexdigest(),
        "artifact_path": artifact_path.name,
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
    }
    (rejected_dir / "failure.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )

    result = revalidate_rejected_comparison(
        packet_dir=packet,
        rejected_dir=rejected_dir,
        output_dir=tmp_path / "comparison-old.revalidated",
        output_schema_path=AUDIT_SCHEMAS / "protocol_audit.schema.json",
        packet_schema_path=AUDIT_SCHEMAS / "audit_packet.schema.json",
    )

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert {issue["task"] for issue in artifact["issues"]} == {"T1", "T2", "T3"}
    assert metadata["status"] == "validated"
    assert metadata["source_rejected_run"] == "comparison-old.rejected"
    assert metadata["deterministic_normalization"]["status"] == "applied"
    assert metadata["artifact_sha256"]


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
