from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.application import ApplicationError, apply_review_decision
from libstruct_bench.audit.artifacts import sha256_file
from libstruct_bench.audit.promotion import promote_reviewed_groundtruth
from libstruct_bench.audit.review import (
    ReviewError,
    issue_requires_individual_review,
    render_console_summary,
    render_review_packet,
    validate_review_decision,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT = REPO_ROOT / "schemas" / "audit"
GROUNDTRUTH = REPO_ROOT / "schemas" / "groundtruth"
NOW = "2026-08-01T12:00:00Z"
LATER = "2026-08-01T12:05:00Z"


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _scope() -> dict:
    return {"protocol_version": "paper", "applicable_variants": ["default"]}


def _evidence() -> list[dict]:
    return [{"source_id": "primary:paper", "locator": {"page": 1}}]


def _t1(sequence: str = "AAA") -> dict:
    return {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "libraries": [
            {
                "library_id": "library",
                "modality": "rna",
                "protocol_scope": _scope(),
                "final_molecule": "DNA",
                "library_sequence": sequence,
                "annotated_library_sequence": sequence,
                "strand": "single",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": "adapter",
                        "kind": "constant",
                        "role": "adapter",
                        "sequence": sequence,
                        "orientation": "5_to_3",
                        "oligo_ids": [],
                        "ground_truth_status": "included",
                        "support_status": "explicit",
                        "evidence": _evidence(),
                    }
                ],
                "ground_truth_status": "included",
                "support_status": "explicit",
                "evidence": _evidence(),
            }
        ],
    }


def _t3(sequence: str = "CCC") -> dict:
    state = lambda state_id, role, value: {
        "state_id": state_id,
        "name": state_id.title(),
        "molecule_type": "DNA",
        "strand_state": "single_stranded",
        "physical_state": "solution",
        "modality": "rna",
        "workflow_branch": None,
        "segments": [{"segment_id": f"{state_id}-segment", "role": role, "sequence": value}],
        "properties": [],
        "protocol_scope": _scope(),
        "support_status": "explicit",
        "evidence": _evidence(),
    }
    return {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "workflows": [
            {
                "workflow_id": "workflow",
                "modality": "rna",
                "workflow_branch": None,
                "protocol_scope": _scope(),
                "ground_truth_status": "included",
                "states": [state("input", "input", "GGG"), state("final", "adapter", sequence)],
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
                        "protocol_scope": _scope(),
                        "support_status": "explicit",
                        "evidence": _evidence(),
                    }
                ],
                "initial_state_ids": ["input"],
                "final_state_ids": ["final"],
                "final_library_links": [{"state_id": "final", "library_id": "library"}],
            }
        ],
    }


def _run() -> dict:
    return {
        "run_id": "comparison-001", "agent": "claude-code", "provider": "anthropic",
        "model": "claude-sonnet-4-20250514", "tool_version": "2.1.0",
        "harness_version": "audit-harness", "review_mode": "primary",
        "started_at": NOW, "completed_at": LATER, "prompt_sha256": "1" * 64,
        "skill_sha256": "2" * 64, "policy_sha256": "3" * 64,
        "schema_sha256": "4" * 64, "skills": ["audit-protocol"], "tools": ["Read"],
        "permission_mode": "plan", "checkpoint_id": "checkpoint-0",
    }


def _proposal(baseline_sha: str, *, new_t3: dict | None = None) -> dict:
    target = (
        {"kind": "new_groundtruth_record", "artifact_source_id": "new:t3", "artifact_filename": "groundtruth_library_generation_workflow.json", "json_pointer": ""}
        if new_t3 is not None
        else {"kind": "groundtruth_record", "artifact_source_id": "current:t1", "json_pointer": "/libraries/0/library_sequence"}
    )
    patch = (
        [{"op": "add", "path": "", "value": new_t3}]
        if new_t3 is not None
        else [
            {"op": "replace", "path": "/libraries/0/library_sequence", "value": "CCC"},
            {"op": "replace", "path": "/libraries/0/annotated_library_sequence", "value": "CCC"},
            {"op": "replace", "path": "/libraries/0/segments/0/sequence", "value": "CCC"},
        ]
    )
    task = "T3" if new_t3 is not None else "T1"
    return {
        "audit_id": "example_protocol:audit:comparison-001",
        "protocol_id": "example_protocol",
        "packet_sha256": "5" * 64,
        "input_manifest_sha256": "6" * 64,
        "evidence_id": "example_protocol:evidence:evidence-001",
        "evidence_sha256": "7" * 64,
        "baseline_artifacts": [{"source_id": "current:t1", "sha256": baseline_sha}],
        "run": _run(),
        "disposition": "issues_proposed",
        "summary": "One field needs human review.",
        "audited_fields": [
            {"field_id": "field-1", "task": task, "object_id": "library", "field_path": "/workflows" if new_t3 is not None else "/libraries/0/library_sequence", "comparison_status": "proposed_correction", "issue_ids": ["issue-1"]}
        ],
        "issues": [
            {
                "issue_id": "issue-1", "task": task, "field_id": "field-1",
                "category": "human_curation_error", "defect_type": "incomplete_workflow" if new_t3 is not None else "incorrect_sequence",
                "responsibility": "human_curation", "severity": "high", "title": "Correction",
                "target": target, "current_value": None if new_t3 is not None else "AAA",
                "proposed_value": new_t3 if new_t3 is not None else "CCC",
                "support_status": "explicit", "evidence": [{"source_id": "primary:paper", "locator": {"page": 1}, "supports": "proposed"}],
                "transformations": [], "explanation": "The source explicitly supports the proposed value.",
                "recommendation": "propose_change", "proposed_patch": patch, "confidence": "high",
                "run_id": "comparison-001", "checkpoint_id": "checkpoint-0",
            }
        ],
    }


def _decision(proposal_path: Path) -> dict:
    proposal = json.loads(proposal_path.read_text())
    return {
        "decision_id": "example_protocol:decision:001", "protocol_id": "example_protocol",
        "audit_id": proposal["audit_id"], "proposal_sha256": sha256_file(proposal_path),
        "baseline_artifacts": proposal["baseline_artifacts"], "reviewer": {"reviewer_id": "reviewer"},
        "iteration": 1, "review_state": "final", "review_started_at": NOW,
        "review_completed_at": LATER, "review_duration_seconds": 300,
        "overall_disposition": "accepted",
        "issue_decisions": [{"issue_id": "issue-1", "disposition": "accept", "rationale": "Checked against the source.", "category": "human_curation_error", "responsibility": "human_curation", "severity": "high", "confirmed_cause": "original_human_curation_error"}],
    }


def _artifacts(tmp_path: Path, *, new_t3: bool = False) -> tuple[Path, Path, Path]:
    baseline = _write(tmp_path / "baselines/groundtruth_final_lib_struct.json", _t1())
    proposal = _write(tmp_path / "audit.json", _proposal(sha256_file(baseline), new_t3=_t3("AAA") if new_t3 else None))
    decision = _write(tmp_path / "decision.json", _decision(proposal))
    return baseline, proposal, decision


def test_review_console_is_conflict_only_and_template_is_unversioned(tmp_path: Path) -> None:
    _, proposal, _ = _artifacts(tmp_path)
    value = json.loads(proposal.read_text())
    console = render_console_summary(value)
    assert "Current:" in console and "Proposed:" in console
    report, template = render_review_packet(
        proposal_path=proposal,
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        output_dir=tmp_path / "review",
    )
    assert report.name == "review.txt"
    assert not (tmp_path / "review/review.html").exists()
    assert "schema_version" not in json.loads(template.read_text())


def test_low_informational_finding_uses_grouped_human_decision(tmp_path: Path) -> None:
    baseline = _write(tmp_path / "baselines/groundtruth_final_lib_struct.json", _t1())
    value = _proposal(sha256_file(baseline))
    informational = dict(value["issues"][0])
    informational.update(
        {
            "issue_id": "issue-info",
            "field_id": "field-info",
            "title": "Optional alias metadata is absent",
            "category": "naming_or_normalization_inconsistency",
            "severity": "low",
            "recommendation": "needs_human_review",
            "proposed_patch": [],
        }
    )
    value["issues"].append(informational)
    value["audited_fields"].append(
        {
            "field_id": "field-info",
            "task": "T2",
            "object_id": "oligo",
            "field_path": "/oligos/0/aliases",
            "comparison_status": "ambiguous",
            "issue_ids": ["issue-info"],
        }
    )
    proposal = _write(tmp_path / "audit.json", value)
    decision = _write(tmp_path / "decision.json", _decision(proposal))

    assert not issue_requires_individual_review(informational)
    low_source_conflict = dict(informational, category="source_conflict")
    assert not issue_requires_individual_review(low_source_conflict)
    unresolved = dict(
        informational, category="unresolved_scientific_ambiguity"
    )
    assert issue_requires_individual_review(unresolved)
    console = render_console_summary(value)
    assert "1 issue(s) need individual review; 1 finding(s) need one grouped decision" in console
    assert "Optional alias metadata is absent" not in console
    assert "nothing is accepted or updated automatically" in console
    with pytest.raises(ReviewError, match="every proposal issue exactly once"):
        validate_review_decision(
            proposal_path=proposal,
            decision_path=decision,
            proposal_schema_path=AUDIT / "protocol_audit.schema.json",
            decision_schema_path=AUDIT / "review_decision.schema.json",
        )

    grouped_decision = json.loads(decision.read_text())
    grouped_decision["issue_decisions"].append(
        {
            "issue_id": "issue-info",
            "disposition": "accept",
            "rationale": "Accepted in the grouped informational review; no ground-truth edit.",
            "category": "naming_or_normalization_inconsistency",
            "responsibility": "human_curation",
            "severity": "low",
            "confirmed_cause": "naming_or_normalization_inconsistency",
        }
    )
    _write(decision, grouped_decision)
    validate_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
    )


def test_accepted_patch_is_applied_and_regression_is_recorded(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path)
    result = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths={"current:t1": baseline},
        output_dir=tmp_path / "application",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
        artifact_schema_paths={"current:t1": GROUNDTRUTH / "final_library_groundtruth.schema.json"},
    )
    candidate = json.loads(result.candidate_paths["current:t1"].read_text())
    assert candidate["libraries"][0]["library_sequence"] == "CCC"
    fixture = json.loads(next((result.output_dir / "regressions").glob("*.json")).read_text())
    assert fixture["issue_id"] == "issue-1"
    assert "schema_version" not in fixture


def test_stale_baseline_is_rejected(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path)
    baseline.write_text("{}", encoding="utf-8")
    with pytest.raises(ApplicationError, match="stale baseline"):
        apply_review_decision(
            proposal_path=proposal, decision_path=decision,
            baseline_paths={"current:t1": baseline}, output_dir=tmp_path / "application",
            proposal_schema_path=AUDIT / "protocol_audit.schema.json",
            decision_schema_path=AUDIT / "review_decision.schema.json",
            application_schema_path=AUDIT / "application_log.schema.json",
        )


def test_new_t3_is_created_and_cross_task_validated(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path, new_t3=True)
    result = apply_review_decision(
        proposal_path=proposal, decision_path=decision,
        baseline_paths={"current:t1": baseline}, output_dir=tmp_path / "application",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
        artifact_schema_paths={
            "current:t1": GROUNDTRUTH / "final_library_groundtruth.schema.json",
            "new:t3": GROUNDTRUTH / "library_generation_workflow.schema.json",
        },
    )
    assert result.candidate_paths["new:t3"].name == "groundtruth_library_generation_workflow.json"


def test_final_application_promotes_without_overwrite(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path)
    application = apply_review_decision(
        proposal_path=proposal, decision_path=decision,
        baseline_paths={"current:t1": baseline}, output_dir=tmp_path / "application",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
        artifact_schema_paths={"current:t1": GROUNDTRUTH / "final_library_groundtruth.schema.json"},
    )
    regression = _write(
        tmp_path / "regression-results.json",
        {"created_at": LATER, "fixture_count": 1, "passed_count": 1, "failed_count": 0, "new_regressions": [], "results": [{"issue_id": "issue-1", "artifact_source_id": "current:t1", "status": "passed", "expected_sha256": "8" * 64, "actual_sha256": "8" * 64}]},
    )
    promoted = promote_reviewed_groundtruth(
        proposal_path=proposal, decision_path=decision,
        application_log_path=application.log_path,
        groundtruth_root=tmp_path / "approved", promotion_log_path=tmp_path / "promotion.json",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
        promotion_schema_path=AUDIT / "promotion_log.schema.json",
        regression_results_path=regression,
        regression_results_schema_path=AUDIT / "regression_results.schema.json",
    )
    assert promoted.artifact_paths["T1"].name == "groundtruth_final_lib_struct.json"
    with pytest.raises(Exception, match="already exists"):
        promote_reviewed_groundtruth(
            proposal_path=proposal, decision_path=decision,
            application_log_path=application.log_path,
            groundtruth_root=tmp_path / "approved", promotion_log_path=tmp_path / "promotion-2.json",
            proposal_schema_path=AUDIT / "protocol_audit.schema.json",
            decision_schema_path=AUDIT / "review_decision.schema.json",
            application_schema_path=AUDIT / "application_log.schema.json",
            promotion_schema_path=AUDIT / "promotion_log.schema.json",
            regression_results_path=regression,
            regression_results_schema_path=AUDIT / "regression_results.schema.json",
        )
