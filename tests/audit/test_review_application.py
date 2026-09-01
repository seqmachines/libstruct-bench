from __future__ import annotations

import json
from pathlib import Path

import pytest

from libstruct_bench.audit.application import ApplicationError, apply_review_decision
from libstruct_bench.audit.artifacts import sha256_file
from libstruct_bench.audit.promotion import promote_reviewed_groundtruth
from libstruct_bench.audit.regressions import run_regressions
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


def _t1(sequence: str = "AAA") -> dict:
    return {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "libraries": [
            {
                "modality": "gene expression",
                "protocol_scope": _scope(),
                "final_molecule": "DNA",
                "library_sequence": sequence,
                "strand": "single",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": "adapter",
                        "kind": "constant",
                        "role": "adapter",
                        "sequence": sequence,
                        "orientation": "5_to_3",
                        "oligo_derivations": [],
                        "support_status": "explicit",
                    }
                ],
                "support_status": "explicit",
            }
        ],
    }


def _t3(sequence: str = "CCC") -> dict:
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
            "protocol_scope": _scope(),
            "support_status": "explicit",
        }

    return {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "workflows": [
            {
                "workflow_id": "workflow",
                "protocol_scope": _scope(),
                "states": [
                    state("input", "input", "GGG"),
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
                        "protocol_scope": _scope(),
                        "support_status": "explicit",
                    }
                ],
                "initial_state_ids": ["input"],
                "final_outputs": [{"state_id": "final", "modality": "gene expression"}],
            }
        ],
    }


def _t2() -> dict:
    return {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "oligos": [],
    }


def _run() -> dict:
    return {
        "run_id": "comparison-001",
        "agent": "claude-code",
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "tool_version": "2.1.0",
        "harness_version": "audit-harness",
        "review_mode": "primary",
        "started_at": NOW,
        "completed_at": LATER,
        "prompt_sha256": "1" * 64,
        "skill_sha256": "2" * 64,
        "policy_sha256": "3" * 64,
        "schema_sha256": "4" * 64,
        "skills": ["audit-protocol"],
        "tools": ["Read"],
        "permission_mode": "plan",
        "checkpoint_id": "checkpoint-0",
    }


def _proposal(baseline_sha: str, *, new_t3: dict | None = None) -> dict:
    target = (
        {
            "kind": "new_groundtruth_record",
            "artifact_source_id": "new:t3",
            "artifact_filename": "groundtruth_library_generation_workflow.json",
            "json_pointer": "",
        }
        if new_t3 is not None
        else {
            "kind": "groundtruth_record",
            "artifact_source_id": "current:t1",
            "json_pointer": "/libraries/0/library_sequence",
        }
    )
    patch = (
        [{"op": "add", "path": "", "value": new_t3}]
        if new_t3 is not None
        else [
            {"op": "replace", "path": "/libraries/0/library_sequence", "value": "CCC"},
            {
                "op": "replace",
                "path": "/libraries/0/segments/0/sequence",
                "value": "CCC",
            },
        ]
    )
    task = "T3" if new_t3 is not None else "T1"
    return {
        "audit_id": "example_protocol:audit:comparison-001",
        "protocol_id": "example_protocol",
        "packet_sha256": "5" * 64,
        "input_manifest_sha256": "6" * 64,
        "baseline_artifacts": [{"source_id": "current:t1", "sha256": baseline_sha}],
        "run": _run(),
        "source_coverage": [
            {
                "source_id": "primary:paper",
                "status": "reviewed",
                "tasks": ["T1", "T2", "T3"],
                "portions_reviewed": [{"page": 1}],
            }
        ],
        "disposition": "issues_proposed",
        "summary": "One field needs human review.",
        "audited_fields": [
            {
                "field_id": "field-1",
                "task": task,
                "object_id": "library",
                "field_path": "/workflows"
                if new_t3 is not None
                else "/libraries/0/library_sequence",
                "comparison_status": "proposed_correction",
                "issue_ids": ["issue-1"],
            }
        ],
        "issues": [
            {
                "issue_id": "issue-1",
                "task": task,
                "field_id": "field-1",
                "category": "human_curation_error",
                "defect_type": "incomplete_workflow"
                if new_t3 is not None
                else "incorrect_sequence",
                "responsibility": "human_curation",
                "severity": "high",
                "title": "Correction",
                "target": target,
                "current_value": None if new_t3 is not None else "AAA",
                "proposed_value": new_t3 if new_t3 is not None else "CCC",
                "support_status": "explicit",
                "evidence": [
                    {
                        "source_id": "primary:paper",
                        "locator": {"page": 1},
                        "supports": "proposed",
                    }
                ],
                "transformations": [],
                "explanation": "The source explicitly supports the proposed value.",
                "recommendation": "propose_change",
                "proposed_patch": patch,
                "confidence": "high",
                "run_id": "comparison-001",
                "checkpoint_id": "checkpoint-0",
            }
        ],
    }


def _decision(proposal_path: Path) -> dict:
    proposal = json.loads(proposal_path.read_text())
    return {
        "decision_id": "example_protocol:decision:001",
        "protocol_id": "example_protocol",
        "audit_id": proposal["audit_id"],
        "proposal_sha256": sha256_file(proposal_path),
        "baseline_artifacts": proposal["baseline_artifacts"],
        "reviewer": {"reviewer_id": "reviewer"},
        "iteration": 1,
        "review_state": "final",
        "review_started_at": NOW,
        "review_completed_at": LATER,
        "review_duration_seconds": 300,
        "overall_disposition": "accepted",
        "issue_decisions": [
            {
                "issue_id": "issue-1",
                "disposition": "accept",
                "rationale": "Checked against the source.",
                "category": "human_curation_error",
                "responsibility": "human_curation",
                "severity": "high",
                "confirmed_cause": "original_human_curation_error",
            }
        ],
    }


def _artifacts(tmp_path: Path, *, new_t3: bool = False) -> tuple[Path, Path, Path]:
    baseline = _write(tmp_path / "baselines/groundtruth_final_lib_struct.json", _t1())
    proposal = _write(
        tmp_path / "audit.json",
        _proposal(sha256_file(baseline), new_t3=_t3("AAA") if new_t3 else None),
    )
    decision = _write(tmp_path / "decision.json", _decision(proposal))
    return baseline, proposal, decision


def _compiled_artifacts(
    tmp_path: Path,
) -> tuple[dict[str, Path], Path, Path, dict[str, Path]]:
    baseline_paths = {
        "current:t1": _write(tmp_path / "baselines/t1.json", _t1()),
        "current:t2": _write(tmp_path / "baselines/t2.json", _t2()),
    }
    candidates = {
        "T1": _write(tmp_path / "review/candidates/T1.json", _t1("CCC")),
        "T2": _write(tmp_path / "review/candidates/T2.json", _t2()),
        "T3": _write(tmp_path / "review/candidates/T3.json", _t3("CCC")),
    }
    documents = {
        task: json.loads(path.read_text(encoding="utf-8"))
        for task, path in candidates.items()
    }
    proposal = _proposal(sha256_file(baseline_paths["current:t1"]))
    proposal["baseline_artifacts"] = [
        {"source_id": source_id, "sha256": sha256_file(path)}
        for source_id, path in baseline_paths.items()
    ]
    base_issue = proposal["issues"][0]
    roots = []
    root_specs = [
        ("root-t1", "T1", "current:t1", "groundtruth_record"),
        ("root-t2", "T2", "current:t2", "groundtruth_record"),
        ("root-t3", "T3", "new:t3", "new_groundtruth_record"),
    ]
    for issue_id, task, source_id, target_kind in root_specs:
        operation = "replace" if target_kind == "groundtruth_record" else "add"
        roots.append(
            {
                **base_issue,
                "issue_id": issue_id,
                "task": task,
                "field_id": f"field-{task.lower()}",
                "category": "formatting_or_schema_error",
                "defect_type": "other",
                "responsibility": "harness",
                "title": f"Compile {task} root",
                "target": {
                    "kind": target_kind,
                    "artifact_source_id": source_id,
                    "artifact_filename": TASK_FILENAMES[task],
                    "json_pointer": "",
                },
                "current_value": (
                    None
                    if target_kind == "new_groundtruth_record"
                    else json.loads(baseline_paths[source_id].read_text())
                ),
                "proposed_value": documents[task],
                "proposed_patch": [
                    {"op": operation, "path": "", "value": documents[task]}
                ],
            }
        )
    delta = {
        **base_issue,
        "issue_id": "delta-evidence",
        "task": "T2",
        "field_id": "field-delta",
        "title": "Resolve evidence status in the compiled T2 root",
        "target": {"kind": "source_bundle"},
        "current_value": "ambiguous",
        "proposed_value": "review required",
        "recommendation": "needs_human_review",
        "proposed_patch": [],
    }
    proposal["issues"] = [*roots, delta]
    root_paths = {
        "T1": "/libraries",
        "T2": "/oligos",
        "T3": "/workflows",
    }
    proposal["audited_fields"] = [
        {
            "field_id": issue["field_id"],
            "task": issue["task"],
            "object_id": issue["issue_id"],
            "field_path": root_paths[issue["task"]],
            "comparison_status": "proposed_correction",
            "issue_ids": [issue["issue_id"]],
        }
        for issue in proposal["issues"]
    ]
    proposal_path = _write(tmp_path / "audit.json", proposal)
    decision = {
        "decision_id": "example_protocol:compiled-decision:001",
        "protocol_id": "example_protocol",
        "audit_id": proposal["audit_id"],
        "iteration": 1,
        "review_state": "final",
        "review_batch": "R1",
        "issue_order": ["delta-evidence"],
        "deferred_root_issues": ["root-t1", "root-t2", "root-t3"],
        "decisions": [
            {
                "issue_id": "delta-evidence",
                "task": "T2",
                "target_kind": "groundtruth_record",
                "disposition": "accept",
                "confirmed_cause": "source_or_protocol_ambiguity",
                "rationale": "Human-reviewed evidence tiering is compiled into T2.",
                "decided_at": "2026-08-01",
            },
            {
                "issue_id": "FACTCHECK-T3-CORRECTION",
                "task": "T3",
                "target_kind": "groundtruth_record",
                "disposition": "modify",
                "confirmed_cause": "original_human_curation_error",
                "rationale": "The direct source check corrected the final T3 root.",
                "decided_at": "2026-08-01",
            },
        ],
        "overall_disposition": "accepted",
        "review_completed_at": LATER,
        "reviewer": "human reviewer",
        "candidate_sha256": {
            task: sha256_file(path) for task, path in candidates.items()
        },
        "scientific_approval": {
            "granted": True,
            "granted_at": LATER,
            "scope": "Complete T1-T3 compiled candidates",
        },
    }
    decision_path = _write(tmp_path / "decision.json", decision)
    return baseline_paths, proposal_path, decision_path, candidates


TASK_FILENAMES = {
    "T1": "groundtruth_final_lib_struct.json",
    "T2": "groundtruth_oligos.json",
    "T3": "groundtruth_library_generation_workflow.json",
}


def test_review_console_is_conflict_only_and_template_is_unversioned(
    tmp_path: Path,
) -> None:
    _, proposal, _ = _artifacts(tmp_path)
    value = json.loads(proposal.read_text())
    console = render_console_summary(value)
    assert "Current:" in console and "Proposed:" in console
    assert "Category / defect:" in console
    assert "Evidence locators: supports proposed:" in console
    assert "Reason / impact:" in console
    report, template = render_review_packet(
        proposal_path=proposal,
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        output_dir=tmp_path / "review",
    )
    assert report.name == "review.txt"
    assert not (tmp_path / "review/review.html").exists()
    assert "schema_version" not in json.loads(template.read_text())


def test_console_does_not_offer_modify_for_evidence_only_finding() -> None:
    value = _proposal("a" * 64)
    issue = value["issues"][0]
    issue.update(
        {
            "target": {"kind": "source_bundle"},
            "recommendation": "needs_human_review",
            "proposed_patch": [],
            "severity": "medium",
        }
    )

    console = render_console_summary(value)

    assert "Decision: accept / reject / unresolved / exclude" in console
    assert "Decision: accept / reject / modify" not in console
    assert "modify the linked ground-truth candidate" in console


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
    unresolved = dict(informational, category="unresolved_scientific_ambiguity")
    assert issue_requires_individual_review(unresolved)
    console = render_console_summary(value)
    assert (
        "1 issue(s) need individual review; 1 finding(s) need one grouped decision"
        in console
    )
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
        artifact_schema_paths={
            "current:t1": GROUNDTRUTH / "final_library_groundtruth.schema.json"
        },
    )
    candidate = json.loads(result.candidate_paths["current:t1"].read_text())
    assert candidate["libraries"][0]["library_sequence"] == "CCC"
    fixture = json.loads(
        next((result.output_dir / "regressions").glob("*.json")).read_text()
    )
    assert fixture["issue_id"] == "issue-1"
    assert "schema_version" not in fixture


def test_stale_baseline_is_rejected(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path)
    baseline.write_text("{}", encoding="utf-8")
    with pytest.raises(ApplicationError, match="stale baseline"):
        apply_review_decision(
            proposal_path=proposal,
            decision_path=decision,
            baseline_paths={"current:t1": baseline},
            output_dir=tmp_path / "application",
            proposal_schema_path=AUDIT / "protocol_audit.schema.json",
            decision_schema_path=AUDIT / "review_decision.schema.json",
            application_schema_path=AUDIT / "application_log.schema.json",
        )


def test_root_conversion_replaces_legacy_baseline_before_validation(
    tmp_path: Path,
) -> None:
    legacy = {
        "schema_version": "legacy",
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "source_html_file": "example.html",
        "libraries": [],
    }
    baseline = _write(tmp_path / "baselines/groundtruth_final_lib_struct.json", legacy)
    candidate = _t1()
    proposal_value = _proposal(sha256_file(baseline))
    proposal_value["issues"][0].update(
        {
            "category": "formatting_or_schema_error",
            "defect_type": "other",
            "responsibility": "harness",
            "title": "Convert legacy T1 to canonical ground truth",
            "target": {
                "kind": "groundtruth_record",
                "artifact_source_id": "current:t1",
                "json_pointer": "",
            },
            "current_value": legacy,
            "proposed_value": candidate,
            "proposed_patch": [{"op": "replace", "path": "", "value": candidate}],
        }
    )
    proposal = _write(tmp_path / "audit.json", proposal_value)
    decision = _write(tmp_path / "decision.json", _decision(proposal))

    result = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths={"current:t1": baseline},
        output_dir=tmp_path / "application",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
    )

    converted = json.loads(
        result.candidate_paths["current:t1"].read_text(encoding="utf-8")
    )
    assert converted == candidate
    assert "schema_version" not in converted


def test_new_t3_is_created_and_cross_task_validated(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path, new_t3=True)
    result = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths={"current:t1": baseline},
        output_dir=tmp_path / "application",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
        artifact_schema_paths={
            "current:t1": GROUNDTRUTH / "final_library_groundtruth.schema.json",
            "new:t3": GROUNDTRUTH / "library_generation_workflow.schema.json",
        },
    )
    assert (
        result.candidate_paths["new:t3"].name
        == "groundtruth_library_generation_workflow.json"
    )


def test_modified_new_t3_is_always_schema_validated(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path, new_t3=True)
    decision_value = json.loads(decision.read_text())
    invalid_t3 = {"protocol_id": "example_protocol", "legacy_steps": []}
    decision_value["issue_decisions"][0].update(
        {
            "disposition": "modify",
            "replacement_value": invalid_t3,
            "replacement_patch": [{"op": "add", "path": "", "value": invalid_t3}],
        }
    )
    _write(decision, decision_value)

    with pytest.raises(ApplicationError, match="candidate new:t3 schema error"):
        apply_review_decision(
            proposal_path=proposal,
            decision_path=decision,
            baseline_paths={"current:t1": baseline},
            output_dir=tmp_path / "application",
            proposal_schema_path=AUDIT / "protocol_audit.schema.json",
            decision_schema_path=AUDIT / "review_decision.schema.json",
            application_schema_path=AUDIT / "application_log.schema.json",
        )


def test_final_application_promotes_without_overwrite(tmp_path: Path) -> None:
    baseline, proposal, decision = _artifacts(tmp_path)
    application = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths={"current:t1": baseline},
        output_dir=tmp_path / "application",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
        artifact_schema_paths={
            "current:t1": GROUNDTRUTH / "final_library_groundtruth.schema.json"
        },
    )
    regression = _write(
        tmp_path / "regression-results.json",
        {
            "created_at": LATER,
            "fixture_count": 1,
            "passed_count": 1,
            "failed_count": 0,
            "new_regressions": [],
            "results": [
                {
                    "issue_id": "issue-1",
                    "artifact_source_id": "current:t1",
                    "status": "passed",
                    "expected_sha256": "8" * 64,
                    "actual_sha256": "8" * 64,
                }
            ],
        },
    )
    promoted = promote_reviewed_groundtruth(
        proposal_path=proposal,
        decision_path=decision,
        application_log_path=application.log_path,
        groundtruth_root=tmp_path / "approved",
        promotion_log_path=tmp_path / "promotion.json",
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
            proposal_path=proposal,
            decision_path=decision,
            application_log_path=application.log_path,
            groundtruth_root=tmp_path / "approved",
            promotion_log_path=tmp_path / "promotion-2.json",
            proposal_schema_path=AUDIT / "protocol_audit.schema.json",
            decision_schema_path=AUDIT / "review_decision.schema.json",
            application_schema_path=AUDIT / "application_log.schema.json",
            promotion_schema_path=AUDIT / "promotion_log.schema.json",
            regression_results_path=regression,
            regression_results_schema_path=AUDIT / "regression_results.schema.json",
        )


def test_compiled_review_preserves_gate_decisions_and_promotes_three_roots(
    tmp_path: Path,
) -> None:
    baselines, proposal, decision, candidates = _compiled_artifacts(tmp_path)
    validate_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
    )

    application = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths=baselines,
        compiled_candidate_paths=candidates,
        output_dir=tmp_path / "application",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
    )
    log = json.loads(application.log_path.read_text(encoding="utf-8"))
    assert log["application_mode"] == "compiled_roots"
    assert set(log["applied_issue_ids"]) == {"root-t1", "root-t2", "root-t3"}
    assert set(log["incorporated_decision_ids"]) == {
        "delta-evidence",
        "FACTCHECK-T3-CORRECTION",
    }
    assert len(log["regression_fixtures"]) == 3
    assert not any(
        "FACTCHECK" in relative for relative in log["regression_fixtures"]
    )
    for task, reviewed_path in candidates.items():
        source_id = {"T1": "current:t1", "T2": "current:t2", "T3": "new:t3"}[
            task
        ]
        assert application.candidate_paths[source_id].read_bytes() == reviewed_path.read_bytes()

    regression_path = tmp_path / "regression-results.json"
    fixtures = [
        application.output_dir / relative for relative in log["regression_fixtures"]
    ]
    run_regressions(
        fixture_paths=fixtures,
        baseline_paths=baselines,
        output_path=regression_path,
        fixture_schema_path=AUDIT / "accepted_correction_regression.schema.json",
        results_schema_path=AUDIT / "regression_results.schema.json",
        created_at=LATER,
    )
    promoted = promote_reviewed_groundtruth(
        proposal_path=proposal,
        decision_path=decision,
        application_log_path=application.log_path,
        groundtruth_root=tmp_path / "approved",
        promotion_log_path=tmp_path / "promotion.json",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
        promotion_schema_path=AUDIT / "promotion_log.schema.json",
        regression_results_path=regression_path,
        regression_results_schema_path=AUDIT / "regression_results.schema.json",
    )
    assert set(promoted.artifact_paths) == {"T1", "T2", "T3"}


def test_compiled_application_rejects_candidate_changed_after_approval(
    tmp_path: Path,
) -> None:
    baselines, proposal, decision, candidates = _compiled_artifacts(tmp_path)
    candidates["T3"].write_text("{}", encoding="utf-8")

    with pytest.raises(ApplicationError, match="stale compiled T3 candidate"):
        apply_review_decision(
            proposal_path=proposal,
            decision_path=decision,
            baseline_paths=baselines,
            compiled_candidate_paths=candidates,
            output_dir=tmp_path / "application",
            proposal_schema_path=AUDIT / "protocol_audit.schema.json",
            decision_schema_path=AUDIT / "review_decision.schema.json",
            application_schema_path=AUDIT / "application_log.schema.json",
        )


def test_compiled_review_allows_empty_deferred_roots_and_grouped_findings(
    tmp_path: Path,
) -> None:
    baselines, proposal, decision, candidates = _compiled_artifacts(tmp_path)
    proposal_document = json.loads(proposal.read_text(encoding="utf-8"))
    grouped = {
        **next(
            issue
            for issue in proposal_document["issues"]
            if issue["issue_id"] == "delta-evidence"
        ),
        "issue_id": "grouped-low",
        "field_id": "field-grouped-low",
        "severity": "low",
        "title": "Grouped low finding compiled into T2",
    }
    proposal_document["issues"].insert(-3, grouped)
    proposal_document["audited_fields"].append(
        {
            "field_id": grouped["field_id"],
            "task": grouped["task"],
            "object_id": grouped["issue_id"],
            "field_path": "/oligos",
            "comparison_status": "proposed_correction",
            "issue_ids": [grouped["issue_id"]],
        }
    )
    _write(proposal, proposal_document)

    decision_document = json.loads(decision.read_text(encoding="utf-8"))
    decision_document["iteration"] = "iteration-001"
    decision_document["deferred_root_issues"] = []
    decision_document["decisions"].insert(
        1,
        {
            "issue_id": "grouped-low",
            "task": "cross_task",
            "target_kind": "groundtruth_record",
            "disposition": "accept",
            "confirmed_cause": "source_or_protocol_ambiguity",
            "rationale": "Reviewed in a grouped low-severity adjudication.",
            "decided_at": "2026-08-01",
        },
    )
    decision_document["decisions"].append(
        {
            "issue_id": "SWEEP-FINAL-CONSISTENCY",
            "task": "cross_task",
            "target_kind": "groundtruth_record",
            "disposition": "modify",
            "confirmed_cause": "original_human_curation_error",
            "rationale": "Final consistency sweep is embodied by approved roots.",
            "decided_at": "2026-08-01",
        }
    )
    _write(decision, decision_document)

    validate_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
    )
    application = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths=baselines,
        compiled_candidate_paths=candidates,
        output_dir=tmp_path / "application",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
    )
    log = json.loads(application.log_path.read_text(encoding="utf-8"))
    assert set(log["applied_issue_ids"]) == {"root-t1", "root-t2", "root-t3"}
    assert "grouped-low" in log["incorporated_decision_ids"]
    assert "SWEEP-FINAL-CONSISTENCY" in log["incorporated_decision_ids"]


def test_compiled_review_without_proposal_roots_uses_application_operation_ids(
    tmp_path: Path,
) -> None:
    baselines, proposal, decision, candidates = _compiled_artifacts(tmp_path)
    proposal_document = json.loads(proposal.read_text(encoding="utf-8"))
    root_ids = {"root-t1", "root-t2", "root-t3"}
    proposal_document["issues"] = [
        issue
        for issue in proposal_document["issues"]
        if issue["issue_id"] not in root_ids
    ]
    proposal_document["audited_fields"] = [
        field
        for field in proposal_document["audited_fields"]
        if not root_ids.intersection(field["issue_ids"])
    ]
    _write(proposal, proposal_document)
    decision_document = json.loads(decision.read_text(encoding="utf-8"))
    decision_document["deferred_root_issues"] = []
    _write(decision, decision_document)

    application = apply_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        baseline_paths=baselines,
        compiled_candidate_paths=candidates,
        output_dir=tmp_path / "application",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
    )
    log = json.loads(application.log_path.read_text(encoding="utf-8"))
    assert set(log["applied_issue_ids"]) == {
        "compiled.t1.root-application",
        "compiled.t2.root-application",
        "compiled.t3.root-application",
    }
    assert set(application.candidate_paths) == {
        "current:t1",
        "current:t2",
        "compiled:t3:new",
    }

    regression_path = tmp_path / "regression-results.json"
    run_regressions(
        fixture_paths=[
            application.output_dir / relative
            for relative in log["regression_fixtures"]
        ],
        baseline_paths=baselines,
        output_path=regression_path,
        fixture_schema_path=AUDIT / "accepted_correction_regression.schema.json",
        results_schema_path=AUDIT / "regression_results.schema.json",
        created_at=LATER,
    )
    promoted = promote_reviewed_groundtruth(
        proposal_path=proposal,
        decision_path=decision,
        application_log_path=application.log_path,
        groundtruth_root=tmp_path / "approved",
        promotion_log_path=tmp_path / "promotion.json",
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
        application_schema_path=AUDIT / "application_log.schema.json",
        promotion_schema_path=AUDIT / "promotion_log.schema.json",
        regression_results_path=regression_path,
        regression_results_schema_path=AUDIT / "regression_results.schema.json",
    )
    assert set(promoted.artifact_paths) == {"T1", "T2", "T3"}


def test_compiled_review_accepts_only_supported_historical_root_ids(
    tmp_path: Path,
) -> None:
    _, proposal, decision, _ = _compiled_artifacts(tmp_path)
    proposal_document = json.loads(proposal.read_text(encoding="utf-8"))
    root_ids = {"root-t1", "root-t2", "root-t3"}
    proposal_document["issues"] = [
        issue
        for issue in proposal_document["issues"]
        if issue["issue_id"] not in root_ids
    ]
    proposal_document["audited_fields"] = [
        field
        for field in proposal_document["audited_fields"]
        if not root_ids.intersection(field["issue_ids"])
    ]
    _write(proposal, proposal_document)
    decision_document = json.loads(decision.read_text(encoding="utf-8"))
    decision_document["deferred_root_issues"] = [
        "harness.t1.root-migration",
        "harness.t2.root-migration",
        "harness.t3.root-migration",
    ]
    _write(decision, decision_document)
    validate_review_decision(
        proposal_path=proposal,
        decision_path=decision,
        proposal_schema_path=AUDIT / "protocol_audit.schema.json",
        decision_schema_path=AUDIT / "review_decision.schema.json",
    )

    decision_document["deferred_root_issues"] = ["invented.root"]
    _write(decision, decision_document)
    with pytest.raises(ReviewError, match="neither a proposal issue"):
        validate_review_decision(
            proposal_path=proposal,
            decision_path=decision,
            proposal_schema_path=AUDIT / "protocol_audit.schema.json",
            decision_schema_path=AUDIT / "review_decision.schema.json",
        )
