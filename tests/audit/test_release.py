from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from libstruct_bench.audit.application import apply_review_decision
from libstruct_bench.audit.oligo_catalog import build_oligo_outputs
from libstruct_bench.audit.release import (
    ReleaseError,
    _independent_selection,
    _validate_checkpoint_cadence,
    build_release_manifest,
)
from libstruct_bench.audit.reporting import build_checkpoint_report
from libstruct_bench.audit.regressions import run_regressions
from tests.audit.test_review_application import (
    LATER,
    NOW,
    _decision,
    _proposal,
    _sha,
    _write_json,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCHEMAS = REPO_ROOT / "schemas" / "audit"
GROUNDTRUTH_SCHEMAS = REPO_ROOT / "schemas" / "groundtruth"
POLICY_PATHS = [
    REPO_ROOT / "docs" / "audit" / "evidence-policy.md",
    REPO_ROOT / "docs" / "audit" / "adjudication-policy.md",
    REPO_ROOT / "docs" / "audit" / "benchmark-standardization-policy.md",
]


def _review() -> dict:
    return {
        "reviewer_id": "curator-001",
        "reviewed_at": NOW,
        "reason": "Approved source for the pilot.",
    }


def _run(run_id: str, review_mode: str) -> dict:
    return {
        "run_id": run_id,
        "agent": "claude-code" if review_mode == "primary" else "codex",
        "provider": "anthropic" if review_mode == "primary" else "openai",
        "model": "claude-opus-4-1-20250805" if review_mode == "primary" else "gpt-5.6-codex-20260701",
        "tool_version": "2.1.0",
        "harness_version": "libstruct-bench-audit/0.2.0",
        "review_mode": review_mode,
        "started_at": NOW,
        "completed_at": LATER,
        "prompt_sha256": "a" * 64,
        "skill_sha256": "a" * 64,
        "policy_sha256": "a" * 64,
        "schema_sha256": "a" * 64,
        "skills": ["audit-protocol" if review_mode == "primary" else "libstruct-audit"],
        "tools": ["Read", "Glob", "Grep"],
        "permission_mode": "plan",
        "checkpoint_id": "pilot-1",
    }


def _copy_release_inputs(root: Path) -> list[dict[str, str]]:
    schema_dir = root / "schemas"
    policy_dir = root / "policies"
    schema_dir.mkdir(parents=True)
    policy_dir.mkdir()
    schema_sources = [
        AUDIT_SCHEMAS / "audit_input_manifest.v2.schema.json",
        AUDIT_SCHEMAS / "protocol_evidence.v1.schema.json",
        AUDIT_SCHEMAS / "protocol_audit.v2.schema.json",
        AUDIT_SCHEMAS / "review_decision.v2.schema.json",
        AUDIT_SCHEMAS / "application_log.v1.schema.json",
        AUDIT_SCHEMAS / "accepted_correction_regression.v1.schema.json",
        AUDIT_SCHEMAS / "regression_results.v1.schema.json",
        AUDIT_SCHEMAS / "checkpoint_report.v1.schema.json",
        AUDIT_SCHEMAS / "groundtruth_release_manifest.v2.schema.json",
        AUDIT_SCHEMAS / "oligo_output_build.v1.schema.json",
        GROUNDTRUTH_SCHEMAS / "final_library_groundtruth.v1.schema.json",
        GROUNDTRUTH_SCHEMAS / "oligo_groundtruth.v1.schema.json",
        GROUNDTRUTH_SCHEMAS / "library_generation_workflow.v1.schema.json",
        GROUNDTRUTH_SCHEMAS / "oligo_catalog.v1.schema.json",
    ]
    values: list[dict[str, str]] = []
    for source in schema_sources:
        destination = schema_dir / source.name
        shutil.copyfile(source, destination)
        schema = json.loads(destination.read_text(encoding="utf-8"))
        values.append(
            {
                "path": f"schemas/{source.name}",
                "schema_version": schema["properties"]["schema_version"]["const"],
            }
        )
    for source in POLICY_PATHS:
        shutil.copyfile(source, policy_dir / source.name)
    return values


def _release_fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "release-root"
    root.mkdir()
    schema_paths = _copy_release_inputs(root)
    protocol_dir = root / "protocols" / "example_protocol"
    protocol_dir.mkdir(parents=True)

    baseline = protocol_dir / "t1-baseline.json"
    _write_json(
        baseline,
        {
            "schema_version": "libstruct.final_library_groundtruth.v1",
            "protocol_id": "example_protocol",
            "protocol_name": "Example protocol",
            "libraries": [
                {
                    "library_id": "library-1",
                    "modality": "transcriptome",
                    "library_sequence": "AAA?",
                    "benchmark_status": "included",
                    "support_status": "explicit",
                    "evidence": [
                        {"source_id": "primary-paper", "locator": {"page": 4}}
                    ],
                    "segments": [],
                }
            ],
        },
    )
    baseline_sha = _sha(baseline)
    t2_path = protocol_dir / "t2-baseline.json"
    _write_json(
        t2_path,
        {
            "schema_version": "libstruct.oligo_groundtruth.v1",
            "protocol_id": "example_protocol",
            "protocol_name": "Example protocol",
            "oligos": [],
        },
    )
    t2_sha = _sha(t2_path)
    manifest = {
        "schema_version": "libstruct.audit_input_manifest.v2",
        "manifest_id": "example_protocol:inputs:v2",
        "protocol_id": "example_protocol",
        "created_at": NOW,
        "source_catalog_sha256": "a" * 64,
        "checkpoint": {
            "checkpoint_id": "pilot-1",
            "protocol_ordinal": 1,
            "reviewed_protocol_count": 1,
        },
        "sources": [
            {
                "source_id": "primary-paper",
                "role": "primary_evidence",
                "source_kind": "original_paper",
                "approval_status": "included",
                "task_relevance": ["T1", "T2", "T3"],
                "path": "example_protocol/paper.pdf",
                "sha256": "1" * 64,
                "size_bytes": 100,
                "media_type": "application/pdf",
                "dataset_reference": {
                    "provider": "huggingface",
                    "repository": "seqmachines/all-protocol-sources",
                    "revision": "d" * 40,
                    "path": "example_protocol/paper.pdf",
                },
                "review": _review(),
            },
            {
                "source_id": "legacy-html",
                "role": "legacy_curated_html",
                "source_kind": "legacy_html",
                "approval_status": "included",
                "task_relevance": ["T1", "T2", "T3"],
                "path": "scg_html/Example.html",
                "sha256": "2" * 64,
                "size_bytes": 100,
                "media_type": "text/html",
                "dataset_reference": {
                    "provider": "huggingface",
                    "repository": "seqmachines/all-protocol-sources",
                    "revision": "d" * 40,
                    "path": "scg_html/Example.html",
                },
                "review": _review(),
            },
            {
                "source_id": "current-t1",
                "role": "current_benchmark_record",
                "source_kind": "current_t1",
                "approval_status": "included",
                "task_relevance": ["T1"],
                "path": "example_protocol/t1-baseline.json",
                "sha256": baseline_sha,
                "size_bytes": baseline.stat().st_size,
                "media_type": "application/json",
                "dataset_reference": {
                    "provider": "huggingface",
                    "repository": "seqmachines/libstruct-groundtruth-audit",
                    "revision": "e" * 40,
                    "path": "example_protocol/t1-baseline.json",
                },
                "review": _review(),
            },
            {
                "source_id": "current-t2",
                "role": "current_benchmark_record",
                "source_kind": "current_t2",
                "approval_status": "included",
                "task_relevance": ["T2"],
                "path": "example_protocol/t2-baseline.json",
                "sha256": t2_sha,
                "size_bytes": t2_path.stat().st_size,
                "media_type": "application/json",
                "dataset_reference": {
                    "provider": "huggingface",
                    "repository": "seqmachines/libstruct-groundtruth-audit",
                    "revision": "e" * 40,
                    "path": "example_protocol/t2-baseline.json",
                },
                "review": _review(),
            },
        ],
    }
    manifest_path = protocol_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    manifest_sha = _sha(manifest_path)

    evidence = {
        "schema_version": "libstruct.protocol_evidence.v1",
        "evidence_id": "example_protocol:evidence:run-001",
        "protocol_id": "example_protocol",
        "packet_sha256": "a" * 64,
        "input_manifest_sha256": manifest_sha,
        "run": _run("run-001", "primary"),
        "source_coverage": [
            {
                "source_id": "primary-paper",
                "status": "reviewed",
                "tasks": ["T1", "T2", "T3"],
                "portions_reviewed": [{"page": 1}],
            }
        ],
        "t1": {"libraries": []},
        "t2": {"oligos": []},
        "t3": {"workflows": []},
        "summary": "Primary source reviewed before comparison.",
    }
    evidence_path = protocol_dir / "evidence.json"
    _write_json(evidence_path, evidence)
    evidence_sha = _sha(evidence_path)

    primary = _proposal(baseline_sha)
    primary["baseline_artifacts"].append(
        {"source_id": "current-t2", "sha256": t2_sha}
    )
    primary["input_manifest_sha256"] = manifest_sha
    primary["evidence_sha256"] = evidence_sha
    primary["run"]["checkpoint_id"] = "pilot-1"
    primary["issues"][0]["checkpoint_id"] = "pilot-1"
    t3_document = {
        "schema_version": "libstruct.library_generation_workflow.v1",
        "protocol_id": "example_protocol",
        "protocol_name": "Example protocol",
        "workflows": [
            {
                "workflow_id": "workflow-1",
                "modality": "transcriptome",
                "input_material": "RNA",
                "benchmark_status": "included",
                "steps": [
                    {
                        "step_id": "step-1",
                        "order": 1,
                        "operation": "reverse transcription",
                        "inputs": ["RNA"],
                        "reagent_refs": [],
                        "oligo_refs": [],
                        "outputs": ["cDNA"],
                        "support_status": "explicit",
                        "evidence": [
                            {
                                "source_id": "primary-paper",
                                "locator": {"page": 2},
                            }
                        ],
                    }
                ],
                "final_library_refs": ["library-1"],
            }
        ],
    }
    primary["audited_fields"].append(
        {
            "field_id": "T3:workflow-1:record",
            "task": "T3",
            "object_id": "workflow-1",
            "field_path": "/workflows/0",
            "comparison_status": "missing_current",
            "issue_ids": ["issue-t3-create"],
        }
    )
    primary["issues"].append(
        {
            "issue_id": "issue-t3-create",
            "task": "T3",
            "field_id": "T3:workflow-1:record",
            "category": "human_curation_error",
            "defect_type": "incomplete_workflow",
            "responsibility": "human_curation",
            "severity": "high",
            "title": "Current T3 artifact is absent",
            "target": {
                "kind": "new_groundtruth_record",
                "artifact_source_id": "new-t3",
                "artifact_filename": "groundtruth_library_generation_workflow.json",
                "json_pointer": "",
            },
            "current_value": None,
            "proposed_value": t3_document,
            "support_status": "explicit",
            "evidence": [
                {
                    "source_id": "primary-paper",
                    "locator": {"page": 2},
                    "supports": "proposed",
                }
            ],
            "transformations": [],
            "explanation": "The primary source explicitly supports T3.",
            "recommendation": "propose_change",
            "proposed_patch": [
                {"op": "add", "path": "", "value": t3_document}
            ],
            "confidence": "high",
            "run_id": "run-001",
            "checkpoint_id": "pilot-1",
        }
    )
    primary_path = protocol_dir / "primary-audit.json"
    _write_json(primary_path, primary)
    primary_decision = _decision(primary_path, baseline_sha)
    primary_decision["baseline_artifacts"] = primary["baseline_artifacts"]
    primary_decision["issue_decisions"].append(
        {
            "issue_id": "issue-t3-create",
            "disposition": "accept",
            "rationale": "The reviewed primary evidence supports creating T3.",
        }
    )
    primary_decision_path = protocol_dir / "primary-decision.json"
    _write_json(primary_decision_path, primary_decision)

    independent = copy.deepcopy(primary)
    independent["audit_id"] = "example_protocol:audit:independent-001"
    independent["run"] = _run("independent-001", "independent")
    independent["disposition"] = "no_issues"
    independent["summary"] = "Independent audit found no additional issues."
    for field in independent["audited_fields"]:
        field["comparison_status"] = "match"
        field["issue_ids"] = []
    independent["issues"] = []
    independent_path = protocol_dir / "independent-audit.json"
    _write_json(independent_path, independent)
    independent_decision = {
        "schema_version": "libstruct.review_decision.v2",
        "decision_id": "example_protocol:decision:independent-001",
        "protocol_id": "example_protocol",
        "audit_id": independent["audit_id"],
        "proposal_sha256": _sha(independent_path),
        "baseline_artifacts": independent["baseline_artifacts"],
        "reviewer": {"reviewer_id": "curator-002"},
        "review_started_at": NOW,
        "review_completed_at": LATER,
        "review_duration_seconds": 120,
        "overall_disposition": "confirmed",
        "issue_decisions": [],
    }
    independent_decision_path = protocol_dir / "independent-decision.json"
    _write_json(independent_decision_path, independent_decision)

    application = apply_review_decision(
        proposal_path=primary_path,
        decision_path=primary_decision_path,
        baseline_paths={"current-t1": baseline, "current-t2": t2_path},
        output_dir=protocol_dir / "application",
        proposal_schema_path=AUDIT_SCHEMAS / "protocol_audit.v2.schema.json",
        decision_schema_path=AUDIT_SCHEMAS / "review_decision.v2.schema.json",
        application_schema_path=AUDIT_SCHEMAS / "application_log.v1.schema.json",
        artifact_schema_paths={
            "current-t1": GROUNDTRUTH_SCHEMAS
            / "final_library_groundtruth.v1.schema.json",
            "current-t2": GROUNDTRUTH_SCHEMAS
            / "oligo_groundtruth.v1.schema.json",
            "new-t3": GROUNDTRUTH_SCHEMAS
            / "library_generation_workflow.v1.schema.json",
        },
    )
    application_log = json.loads(application.log_path.read_text(encoding="utf-8"))
    oligo_outputs = build_oligo_outputs(
        t2_paths=[application.candidate_paths["current-t2"]],
        decision_ids_by_protocol={
            "example_protocol": [
                primary_decision["decision_id"],
                independent_decision["decision_id"],
            ]
        },
        output_dir=root / "audited-oligos",
        t2_schema_path=GROUNDTRUTH_SCHEMAS
        / "oligo_groundtruth.v1.schema.json",
        catalog_schema_path=GROUNDTRUTH_SCHEMAS / "oligo_catalog.v1.schema.json",
        metadata_schema_path=AUDIT_SCHEMAS
        / "oligo_output_build.v1.schema.json",
        created_at=LATER,
    )

    application_fixtures = [
        application.output_dir / path
        for path in application_log["regression_fixtures"]
    ]
    regressions = root / "regression-results.json"
    run_regressions(
        fixture_paths=application_fixtures,
        baseline_paths={"current-t1": baseline},
        output_path=regressions,
        fixture_schema_path=AUDIT_SCHEMAS
        / "accepted_correction_regression.v1.schema.json",
        results_schema_path=AUDIT_SCHEMAS / "regression_results.v1.schema.json",
        created_at=LATER,
    )
    checkpoint_zero = root / "checkpoint-0.json"
    build_checkpoint_report(
        checkpoint_id="checkpoint-0",
        reviewed_protocol_count=0,
        proposal_paths=[],
        decision_paths=[],
        output_path=checkpoint_zero,
        proposal_schema_path=AUDIT_SCHEMAS / "protocol_audit.v2.schema.json",
        decision_schema_path=AUDIT_SCHEMAS / "review_decision.v2.schema.json",
        report_schema_path=AUDIT_SCHEMAS / "checkpoint_report.v1.schema.json",
        created_at=NOW,
    )
    checkpoint_one = root / "checkpoint-1.json"
    build_checkpoint_report(
        checkpoint_id="checkpoint-1",
        reviewed_protocol_count=1,
        proposal_paths=[primary_path, independent_path],
        decision_paths=[primary_decision_path, independent_decision_path],
        output_path=checkpoint_one,
        proposal_schema_path=AUDIT_SCHEMAS / "protocol_audit.v2.schema.json",
        decision_schema_path=AUDIT_SCHEMAS / "review_decision.v2.schema.json",
        report_schema_path=AUDIT_SCHEMAS / "checkpoint_report.v1.schema.json",
        application_log_paths=[application.log_path],
        regression_results_path=regressions,
        previous_checkpoint_path=checkpoint_zero,
        created_at=LATER,
    )

    def relative(path: Path) -> str:
        return path.relative_to(root).as_posix()

    spec = {
        "schema_version": "libstruct.release_spec.v1",
        "release_id": "libstruct-groundtruth-v1.0.0",
        "version": "v1.0.0",
        "release_status": "frozen",
        "created_at": LATER,
        "expected_protocol_count": 1,
        "reviewed_protocol_count": 1,
        "generated_by": {
            "tool_version": "libstruct-bench-0.2.0",
            "git_commit": "c" * 40,
        },
        "source_datasets": [
            {
                "provider": "huggingface",
                "repository": "seqmachines/all-protocol-sources",
                "revision": "d" * 40,
            },
            {
                "provider": "huggingface",
                "repository": "seqmachines/libstruct-groundtruth-audit",
                "revision": "e" * 40,
            },
        ],
        "policy_paths": [f"policies/{path.name}" for path in POLICY_PATHS],
        "schema_paths": schema_paths,
        "checkpoint_paths": ["checkpoint-0.json", "checkpoint-1.json"],
        "independent_audit": {
            "seed": "release-v1",
            "sample_fraction": 0.1,
            "selected_protocol_ids": ["example_protocol"],
        },
        "oligo_outputs": {
            "catalog_path": relative(oligo_outputs.catalog_path),
            "tsv_path": relative(oligo_outputs.tsv_path),
            "build_metadata_path": relative(oligo_outputs.metadata_path),
        },
        "protocols": [
            {
                "protocol_id": "example_protocol",
                "task_dispositions": {
                    "T1": "included",
                    "T2": "included",
                    "T3": "included",
                },
                "input_manifest_path": relative(manifest_path),
                "evidence": [
                    {
                        "id": evidence["evidence_id"],
                        "path": relative(evidence_path),
                    }
                ],
                "audits": [
                    {"id": primary["audit_id"], "path": relative(primary_path)},
                    {
                        "id": independent["audit_id"],
                        "path": relative(independent_path),
                    },
                ],
                "decisions": [
                    {
                        "id": primary_decision["decision_id"],
                        "path": relative(primary_decision_path),
                    },
                    {
                        "id": independent_decision["decision_id"],
                        "path": relative(independent_decision_path),
                    },
                ],
                "applications": [
                    {
                        "id": application_log["application_id"],
                        "path": relative(application.log_path),
                    }
                ],
                "artifacts": [
                    {
                        "task": "T1",
                        "path": relative(application.candidate_paths["current-t1"]),
                        "schema_version": "libstruct.final_library_groundtruth.v1",
                        "artifact_source_id": "current-t1",
                    },
                    {
                        "task": "T2",
                        "path": relative(application.candidate_paths["current-t2"]),
                        "schema_version": "libstruct.oligo_groundtruth.v1",
                        "artifact_source_id": "current-t2",
                    },
                    {
                        "task": "T3",
                        "path": relative(application.candidate_paths["new-t3"]),
                        "schema_version": "libstruct.library_generation_workflow.v1",
                        "artifact_source_id": "new-t3",
                    },
                ],
                "unresolved_issue_ids": [],
                "limitations": [],
            }
        ],
    }
    spec_path = root / "release-spec.json"
    _write_json(spec_path, spec)
    return root, spec_path


def test_frozen_release_verifies_full_provenance_chain(tmp_path: Path) -> None:
    root, spec_path = _release_fixture(tmp_path)
    manifest = build_release_manifest(
        spec_path=spec_path,
        artifact_root=root,
        output_path=tmp_path / "release-manifest.json",
        spec_schema_path=AUDIT_SCHEMAS / "release_spec.v1.schema.json",
        release_schema_path=AUDIT_SCHEMAS
        / "groundtruth_release_manifest.v2.schema.json",
    )
    assert manifest["release_status"] == "frozen"
    assert manifest["protocols"][0]["independent_audit_ids"] == [
        "example_protocol:audit:independent-001"
    ]
    assert manifest["metrics"]["new_regression_count"] == 0


def test_release_rejects_accepted_change_without_application_log(
    tmp_path: Path,
) -> None:
    root, spec_path = _release_fixture(tmp_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["protocols"][0]["applications"] = []
    _write_json(spec_path, spec)
    with pytest.raises(ReleaseError, match="lack deterministic application logs"):
        build_release_manifest(
            spec_path=spec_path,
            artifact_root=root,
            output_path=tmp_path / "release-manifest.json",
            spec_schema_path=AUDIT_SCHEMAS / "release_spec.v1.schema.json",
            release_schema_path=AUDIT_SCHEMAS
            / "groundtruth_release_manifest.v2.schema.json",
        )


def test_release_rejects_artifact_outside_the_application_chain(
    tmp_path: Path,
) -> None:
    root, spec_path = _release_fixture(tmp_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    t3 = spec["protocols"][0]["artifacts"][2]
    approved = root / t3["path"]
    rogue_document = json.loads(approved.read_text(encoding="utf-8"))
    rogue_document["workflows"][0]["notes"] = "Unreviewed post-application edit"
    rogue = root / "protocols" / "example_protocol" / "rogue-t3.json"
    _write_json(rogue, rogue_document)
    t3["path"] = rogue.relative_to(root).as_posix()
    _write_json(spec_path, spec)
    with pytest.raises(ReleaseError, match="latest deterministic candidate"):
        build_release_manifest(
            spec_path=spec_path,
            artifact_root=root,
            output_path=tmp_path / "release-manifest.json",
            spec_schema_path=AUDIT_SCHEMAS / "release_spec.v1.schema.json",
            release_schema_path=AUDIT_SCHEMAS
            / "groundtruth_release_manifest.v2.schema.json",
        )


def test_release_rebuilds_audited_oligo_outputs_before_freezing(
    tmp_path: Path,
) -> None:
    root, spec_path = _release_fixture(tmp_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    outputs = spec["oligo_outputs"]
    tsv_path = root / outputs["tsv_path"]
    tsv_path.write_text(
        tsv_path.read_text(encoding="utf-8") + "unreviewed\trow\n",
        encoding="utf-8",
    )
    metadata_path = root / outputs["build_metadata_path"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["tsv"]["sha256"] = hashlib.sha256(tsv_path.read_bytes()).hexdigest()
    _write_json(metadata_path, metadata)
    with pytest.raises(ReleaseError, match="deterministic projection"):
        build_release_manifest(
            spec_path=spec_path,
            artifact_root=root,
            output_path=tmp_path / "release-manifest.json",
            spec_schema_path=AUDIT_SCHEMAS / "release_spec.v1.schema.json",
            release_schema_path=AUDIT_SCHEMAS
            / "groundtruth_release_manifest.v2.schema.json",
        )


def test_independent_selection_includes_high_impact_and_is_deterministic() -> None:
    protocols = [f"protocol-{number}" for number in range(20)]
    first = _independent_selection(
        protocol_ids=protocols,
        high_impact={"protocol-19"},
        seed="release-v1",
        fraction=0.1,
    )
    second = _independent_selection(
        protocol_ids=list(reversed(protocols)),
        high_impact={"protocol-19"},
        seed="release-v1",
        fraction=0.1,
    )
    assert first == second
    assert "protocol-19" in first
    assert len(first) == 3


def test_checkpoint_cadence_is_zero_every_five_and_final() -> None:
    _validate_checkpoint_cadence([0, 5, 10, 12], 12)
    with pytest.raises(ReleaseError, match="checkpoint cadence"):
        _validate_checkpoint_cadence([0, 10, 12], 12)
