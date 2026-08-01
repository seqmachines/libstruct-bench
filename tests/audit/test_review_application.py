from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.application import (
    ApplicationError,
    apply_review_decision,
)
from libstruct_bench.audit.review import (
    ReviewError,
    render_review_packet,
    validate_review_decision,
)
from libstruct_bench.audit.reporting import ReportingError, build_checkpoint_report
from libstruct_bench.audit.regressions import run_regressions


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "audit"
PROPOSAL_SCHEMA = SCHEMA_DIR / "protocol_audit.v2.schema.json"
DECISION_SCHEMA = SCHEMA_DIR / "review_decision.v2.schema.json"
APPLICATION_SCHEMA = SCHEMA_DIR / "application_log.v1.schema.json"
CHECKPOINT_SCHEMA = SCHEMA_DIR / "checkpoint_report.v1.schema.json"
REGRESSION_FIXTURE_SCHEMA = (
    SCHEMA_DIR / "accepted_correction_regression.v1.schema.json"
)
REGRESSION_RESULTS_SCHEMA = SCHEMA_DIR / "regression_results.v1.schema.json"
NOW = "2026-08-01T12:00:00Z"
LATER = "2026-08-01T12:02:00Z"
SHA_A = "a" * 64


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _proposal(baseline_sha: str) -> dict:
    return {
        "schema_version": "libstruct.protocol_audit.v2",
        "audit_id": "example_protocol:audit:run-001",
        "protocol_id": "example_protocol",
        "packet_sha256": SHA_A,
        "input_manifest_sha256": SHA_A,
        "evidence_id": "example_protocol:evidence:run-001",
        "evidence_sha256": SHA_A,
        "baseline_artifacts": [
            {"source_id": "current-t1", "sha256": baseline_sha}
        ],
        "run": {
            "run_id": "run-001",
            "agent": "claude-code",
            "provider": "anthropic",
            "model": "claude-opus-4-1-20250805",
            "tool_version": "2.1.0",
            "harness_version": "libstruct-bench-audit/0.2.0",
            "review_mode": "primary",
            "started_at": NOW,
            "completed_at": LATER,
            "prompt_sha256": SHA_A,
            "skill_sha256": SHA_A,
            "policy_sha256": SHA_A,
            "schema_sha256": SHA_A,
            "skills": ["audit-protocol"],
            "tools": ["Read", "Glob", "Grep"],
            "permission_mode": "plan",
            "checkpoint_id": "pilot-0",
        },
        "disposition": "issues_proposed",
        "summary": "One explicit barcode-length discrepancy requires review.",
        "audited_fields": [
            {
                "field_id": "T1:library-1:sequence",
                "task": "T1",
                "object_id": "library-1",
                "field_path": "/libraries/0/library_sequence",
                "comparison_status": "disagreement",
                "issue_ids": ["issue-001"],
            }
        ],
        "issues": [
            {
                "issue_id": "issue-001",
                "task": "T1",
                "field_id": "T1:library-1:sequence",
                "category": "human_curation_error",
                "defect_type": "variable_region_length",
                "responsibility": "human_curation",
                "severity": "high",
                "title": "Barcode is one base short",
                "target": {
                    "kind": "groundtruth_record",
                    "artifact_source_id": "current-t1",
                    "json_pointer": "/libraries/0/library_sequence",
                },
                "current_value": "AAA?",
                "proposed_value": "AAA??",
                "support_status": "explicit",
                "evidence": [
                    {
                        "source_id": "primary-paper",
                        "locator": {"page": 4, "table": "Oligos"},
                        "supports": "proposed",
                        "observed_sequence": "AAA??",
                    }
                ],
                "transformations": [],
                "explanation": "The oligo table explicitly contains two random bases.",
                "recommendation": "propose_change",
                "proposed_patch": [
                    {
                        "op": "test",
                        "path": "/libraries/0/library_sequence",
                        "value": "AAA?",
                    },
                    {
                        "op": "replace",
                        "path": "/libraries/0/library_sequence",
                        "value": "AAA??",
                    },
                ],
                "confidence": "high",
                "run_id": "run-001",
                "checkpoint_id": "pilot-0",
            }
        ],
    }


def _decision(proposal_path: Path, baseline_sha: str, disposition: str = "accept") -> dict:
    issue = {
        "issue_id": "issue-001",
        "disposition": disposition,
        "rationale": "The explicit source evidence supports this decision.",
    }
    if disposition == "modify":
        issue["replacement_value"] = "AAA???"
        issue["replacement_patch"] = [
            {
                "op": "test",
                "path": "/libraries/0/library_sequence",
                "value": "AAA?",
            },
            {
                "op": "replace",
                "path": "/libraries/0/library_sequence",
                "value": "AAA???",
            },
        ]
    return {
        "schema_version": "libstruct.review_decision.v2",
        "decision_id": "example_protocol:decision:001",
        "protocol_id": "example_protocol",
        "audit_id": "example_protocol:audit:run-001",
        "proposal_sha256": _sha(proposal_path),
        "baseline_artifacts": [
            {"source_id": "current-t1", "sha256": baseline_sha}
        ],
        "reviewer": {"reviewer_id": "curator-001"},
        "review_started_at": NOW,
        "review_completed_at": LATER,
        "review_duration_seconds": 120,
        "overall_disposition": "accepted",
        "issue_decisions": [issue],
    }


def _artifacts(tmp_path: Path, disposition: str = "accept") -> tuple[Path, Path, Path]:
    baseline = tmp_path / "groundtruth.json"
    _write_json(
        baseline,
        {
            "protocol_id": "example_protocol",
            "libraries": [{"library_id": "library-1", "library_sequence": "AAA?"}],
        },
    )
    proposal = tmp_path / "proposal.json"
    _write_json(proposal, _proposal(_sha(baseline)))
    decision = tmp_path / "decision.json"
    _write_json(decision, _decision(proposal, _sha(baseline), disposition))
    return baseline, proposal, decision


def test_review_report_and_decision_validation(tmp_path: Path) -> None:
    _, proposal, decision = _artifacts(tmp_path)
    report, template = render_review_packet(
        proposal_path=proposal,
        proposal_schema_path=PROPOSAL_SCHEMA,
        output_dir=tmp_path / "review",
    )
    assert "Barcode is one base short" in report.read_text(encoding="utf-8")
    assert json.loads(template.read_text(encoding="utf-8"))["issue_decisions"][0][
        "disposition"
    ] == "unresolved"
    validated_proposal, validated_decision = validate_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        proposal_schema_path=PROPOSAL_SCHEMA,
        decision_schema_path=DECISION_SCHEMA,
    )
    assert validated_proposal["audit_id"] == validated_decision["audit_id"]


def test_review_rejects_stale_proposal_hash(tmp_path: Path) -> None:
    _, proposal, decision = _artifacts(tmp_path)
    value = json.loads(proposal.read_text(encoding="utf-8"))
    value["summary"] = "Proposal changed after review."
    _write_json(proposal, value)
    with pytest.raises(ReviewError, match="stale proposal hash"):
        validate_review_decision(
            proposal_path=proposal,
            decision_path=decision,
            proposal_schema_path=PROPOSAL_SCHEMA,
            decision_schema_path=DECISION_SCHEMA,
        )


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [("accept", "AAA??"), ("modify", "AAA???")],
)
def test_application_uses_only_the_human_approved_patch(
    tmp_path: Path, disposition: str, expected: str
) -> None:
    baseline, proposal, decision = _artifacts(tmp_path, disposition)
    result = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths={"current-t1": baseline},
        output_dir=tmp_path / "application",
        proposal_schema_path=PROPOSAL_SCHEMA,
        decision_schema_path=DECISION_SCHEMA,
        application_schema_path=APPLICATION_SCHEMA,
    )
    candidate = json.loads(
        result.candidate_paths["current-t1"].read_text(encoding="utf-8")
    )
    assert candidate["libraries"][0]["library_sequence"] == expected
    assert result.applied_issue_ids == ("issue-001",)
    log = json.loads(result.log_path.read_text(encoding="utf-8"))
    assert len(log["regression_fixtures"]) == 1
    assert (result.output_dir / log["regression_fixtures"][0]).is_file()


def test_application_rejects_a_baseline_changed_after_review(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path)
    value = json.loads(baseline.read_text(encoding="utf-8"))
    value["libraries"][0]["library_sequence"] = "changed"
    _write_json(baseline, value)
    with pytest.raises(ApplicationError, match="stale baseline"):
        apply_review_decision(
            proposal_path=proposal,
            decision_path=decision,
            baseline_paths={"current-t1": baseline},
            output_dir=tmp_path / "application",
            proposal_schema_path=PROPOSAL_SCHEMA,
            decision_schema_path=DECISION_SCHEMA,
            application_schema_path=APPLICATION_SCHEMA,
        )


def test_generated_regression_fixture_replays_the_accepted_patch(
    tmp_path: Path,
) -> None:
    baseline, proposal, decision = _artifacts(tmp_path)
    application = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths={"current-t1": baseline},
        output_dir=tmp_path / "application",
        proposal_schema_path=PROPOSAL_SCHEMA,
        decision_schema_path=DECISION_SCHEMA,
        application_schema_path=APPLICATION_SCHEMA,
    )
    log = json.loads(application.log_path.read_text(encoding="utf-8"))
    fixture = application.output_dir / log["regression_fixtures"][0]
    results = run_regressions(
        fixture_paths=[fixture],
        baseline_paths={"current-t1": baseline},
        output_path=tmp_path / "regression-results.json",
        fixture_schema_path=REGRESSION_FIXTURE_SCHEMA,
        results_schema_path=REGRESSION_RESULTS_SCHEMA,
        created_at=LATER,
    )
    assert results["passed_count"] == 1
    assert results["new_regressions"] == []


def test_human_approved_new_t3_artifact_is_created_deterministically(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "groundtruth.json"
    _write_json(
        baseline,
        {
            "protocol_id": "example_protocol",
            "libraries": [
                {"library_id": "library-1", "library_sequence": "AAA?"}
            ],
        },
    )
    proposal_value = _proposal(_sha(baseline))
    proposal_value["audited_fields"].append(
        {
            "field_id": "T3:workflow-1:record",
            "task": "T3",
            "object_id": "workflow-1",
            "field_path": "/workflows/0",
            "comparison_status": "missing_current",
            "issue_ids": ["issue-t3-create"],
        }
    )
    workflow = {
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
    proposal_value["issues"].append(
        {
            "issue_id": "issue-t3-create",
            "task": "T3",
            "field_id": "T3:workflow-1:record",
            "category": "formatting_or_schema_error",
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
            "proposed_value": workflow,
            "support_status": "explicit",
            "evidence": [
                {
                    "source_id": "primary-paper",
                    "locator": {"page": 2},
                    "supports": "proposed",
                }
            ],
            "transformations": [],
            "explanation": "The reviewed source explicitly describes the workflow.",
            "recommendation": "propose_change",
            "proposed_patch": [{"op": "add", "path": "", "value": workflow}],
            "confidence": "high",
            "run_id": "run-001",
            "checkpoint_id": "pilot-0",
        }
    )
    proposal = tmp_path / "proposal.json"
    _write_json(proposal, proposal_value)
    decision_value = _decision(proposal, _sha(baseline))
    decision_value["issue_decisions"].append(
        {
            "issue_id": "issue-t3-create",
            "disposition": "accept",
            "rationale": "The primary evidence supports creating T3.",
        }
    )
    decision = tmp_path / "decision.json"
    _write_json(decision, decision_value)

    application = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths={"current-t1": baseline},
        output_dir=tmp_path / "application",
        proposal_schema_path=PROPOSAL_SCHEMA,
        decision_schema_path=DECISION_SCHEMA,
        application_schema_path=APPLICATION_SCHEMA,
        artifact_schema_paths={
            "new-t3": REPO_ROOT
            / "schemas"
            / "groundtruth"
            / "library_generation_workflow.v1.schema.json"
        },
    )
    created = json.loads(
        application.candidate_paths["new-t3"].read_text(encoding="utf-8")
    )
    assert created == workflow
    log = json.loads(application.log_path.read_text(encoding="utf-8"))
    new_artifact = next(
        item for item in log["artifacts"] if item["source_id"] == "new-t3"
    )
    assert new_artifact["baseline_state"] == "absent"
    assert "baseline_sha256" not in new_artifact
    fixture_path = application.output_dir / next(
        path
        for path in log["regression_fixtures"]
        if "issue-t3-create" in path
    )
    results = run_regressions(
        fixture_paths=[fixture_path],
        baseline_paths={},
        output_path=tmp_path / "t3-regression-results.json",
        fixture_schema_path=REGRESSION_FIXTURE_SCHEMA,
        results_schema_path=REGRESSION_RESULTS_SCHEMA,
        created_at=LATER,
    )
    assert results["passed_count"] == 1


def test_review_requires_a_decision_for_every_issue(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path)
    value = copy.deepcopy(json.loads(decision.read_text(encoding="utf-8")))
    value["issue_decisions"] = []
    _write_json(decision, value)
    with pytest.raises(ReviewError):
        validate_review_decision(
            proposal_path=proposal,
            decision_path=decision,
            proposal_schema_path=PROPOSAL_SCHEMA,
            decision_schema_path=DECISION_SCHEMA,
        )


def test_checkpoint_metrics_use_reviewed_fields_and_human_decisions(
    tmp_path: Path,
) -> None:
    _, proposal, decision = _artifacts(tmp_path)
    regressions = tmp_path / "regressions.json"
    _write_json(
        regressions,
        {
            "schema_version": "libstruct.regression_results.v1",
            "created_at": LATER,
            "fixture_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "new_regressions": [],
            "results": [],
        },
    )
    report = build_checkpoint_report(
        checkpoint_id="pilot-1",
        reviewed_protocol_count=1,
        proposal_paths=[proposal],
        decision_paths=[decision],
        output_path=tmp_path / "checkpoint.json",
        proposal_schema_path=PROPOSAL_SCHEMA,
        decision_schema_path=DECISION_SCHEMA,
        report_schema_path=CHECKPOINT_SCHEMA,
        regression_results_path=regressions,
        created_at=LATER,
    )
    metrics = report["metrics"]
    assert metrics["audited_field_count"] == 1
    assert metrics["confirmed_error_field_count"] == 1
    assert metrics["confirmed_error_rate"] == 1.0
    assert metrics["confirmed_issue_count"] == 1
    assert metrics["human_error_count"] == 1
    assert metrics["agent_error_count"] == 0
    assert metrics["human_review_seconds"] == 120
    assert metrics["new_regression_count"] == 0


def test_checkpoint_rejects_incorrect_reviewed_protocol_count(tmp_path: Path) -> None:
    _, proposal, decision = _artifacts(tmp_path)
    with pytest.raises(ReportingError, match="reviewed_protocol_count"):
        build_checkpoint_report(
            checkpoint_id="pilot-0",
            reviewed_protocol_count=0,
            proposal_paths=[proposal],
            decision_paths=[decision],
            output_path=tmp_path / "checkpoint.json",
            proposal_schema_path=PROPOSAL_SCHEMA,
            decision_schema_path=DECISION_SCHEMA,
            report_schema_path=CHECKPOINT_SCHEMA,
            created_at=LATER,
        )


def test_checkpoint_regressions_must_cover_applied_corrections(
    tmp_path: Path,
) -> None:
    baseline, proposal, decision = _artifacts(tmp_path)
    application = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths={"current-t1": baseline},
        output_dir=tmp_path / "application",
        proposal_schema_path=PROPOSAL_SCHEMA,
        decision_schema_path=DECISION_SCHEMA,
        application_schema_path=APPLICATION_SCHEMA,
    )
    regressions = tmp_path / "regressions.json"
    _write_json(
        regressions,
        {
            "schema_version": "libstruct.regression_results.v1",
            "created_at": LATER,
            "fixture_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "new_regressions": [],
            "results": [],
        },
    )
    with pytest.raises(ReportingError, match="exactly cover"):
        build_checkpoint_report(
            checkpoint_id="pilot-1",
            reviewed_protocol_count=1,
            proposal_paths=[proposal],
            decision_paths=[decision],
            application_log_paths=[application.log_path],
            regression_results_path=regressions,
            output_path=tmp_path / "checkpoint.json",
            proposal_schema_path=PROPOSAL_SCHEMA,
            decision_schema_path=DECISION_SCHEMA,
            report_schema_path=CHECKPOINT_SCHEMA,
            created_at=LATER,
        )


def test_checkpoint_records_deltas_from_previous_checkpoint(tmp_path: Path) -> None:
    previous_path = tmp_path / "checkpoint-0.json"
    build_checkpoint_report(
        checkpoint_id="checkpoint-0",
        reviewed_protocol_count=0,
        proposal_paths=[],
        decision_paths=[],
        output_path=previous_path,
        proposal_schema_path=PROPOSAL_SCHEMA,
        decision_schema_path=DECISION_SCHEMA,
        report_schema_path=CHECKPOINT_SCHEMA,
        created_at=NOW,
    )
    _, proposal, decision = _artifacts(tmp_path)
    current = build_checkpoint_report(
        checkpoint_id="checkpoint-1",
        reviewed_protocol_count=1,
        proposal_paths=[proposal],
        decision_paths=[decision],
        output_path=tmp_path / "checkpoint-1.json",
        proposal_schema_path=PROPOSAL_SCHEMA,
        decision_schema_path=DECISION_SCHEMA,
        report_schema_path=CHECKPOINT_SCHEMA,
        previous_checkpoint_path=previous_path,
        created_at=LATER,
    )
    assert current["deltas"] == {
        "reviewed_protocol_count": 1,
        "audited_field_count": 1,
        "confirmed_error_field_count": 1,
        "confirmed_issue_count": 1,
        "human_error_count": 1,
        "agent_error_count": 0,
        "human_review_seconds": 120.0,
    }
