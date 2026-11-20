from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    AuditArtifactError,
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION

from .artifacts import (
    CapabilityImprovementError,
    improvement_schema_root,
    reject_private_output_in_repository,
    validate_digest,
    with_digest,
)
from .kb_guided import validate_kb_experiment
from .kb_orchestrator import (
    METRIC_KEYS,
    _validate_codex_workspace_access,
    resolve_trial_artifacts,
    run_local_codex_json,
)


MANIFEST_SCHEMA = "kb_validator_review_manifest.schema.json"
COMPARISON_SCHEMA = "kb_validator_review_comparison.schema.json"
DRAFT_SCHEMA = "kb_validator_review_proposal_draft.schema.json"
PROPOSAL_SCHEMA = "kb_validator_review_proposal.schema.json"
DECISION_SCHEMA = "kb_validator_review_decision.schema.json"
MANIFEST_PATH = Path("manifest.json")
GROUNDTRUTH_FILES = {
    "T1": "groundtruth_final_lib_struct.json",
    "T2": "groundtruth_oligos.json",
    "T3": "groundtruth_library_generation_workflow.json",
}
PROHIBITED_ACTIONS = (
    "agent_rerun",
    "pact_update",
    "prompt_update",
    "knowledge_base_update",
    "ground_truth_update",
    "benchmark_promotion",
    "verifier_update",
    "capability_synthesis",
)
CLASSIFICATIONS = (
    "valid_agent_difference",
    "evaluator_defect",
    "ground_truth_defect",
    "source_scope_mismatch",
    "representation_policy_ambiguity",
    "source_conflict",
    "infrastructure",
    "unresolved",
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_ERROR_ID_RE = re.compile(r"^err_[0-9]{4,}$")


def initialize_validator_review(
    *,
    source_experiment_root: Path,
    output_root: Path,
    workspace_root: Path,
    validation_aggregate_path: Path,
    test_aggregate_path: Path,
    review_id: str,
    created_at: str,
) -> dict[str, Any]:
    """Create a hash-pinned, diagnostic-only C0/C30 review lineage.

    The lineage deliberately has no application or synthesis operation. C0 is
    retained as metric and persistent-behavior context; only substantive C30
    verifier observations enter the human classification queue.
    """

    source_root = source_experiment_root.expanduser().resolve()
    target = output_root.expanduser().resolve()
    workspaces = workspace_root.expanduser().resolve()
    reject_private_output_in_repository(target)
    if target.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite validator review: {target}"
        )
    if not _ID_RE.fullmatch(review_id):
        raise CapabilityImprovementError(f"invalid validator review ID: {review_id!r}")

    experiment = validate_kb_experiment(source_root)
    if experiment.get("final_lock") is not True:
        raise CapabilityImprovementError(
            "validator review requires a completed and locked source experiment"
        )
    design_path = source_root / str(experiment["design"]["path"])
    design = _load_digest_document(
        design_path,
        "kb_experiment_design.schema.json",
        "design_digest",
        "KB experiment design",
    )
    final_lock_path = source_root / "final_lock.json"
    final_lock = _load_digest_document(
        final_lock_path,
        "kb_final_lock.schema.json",
        "lock_digest",
        "KB final lock",
    )
    endpoint = final_lock.get("endpoint_selection") or {}
    if (
        final_lock.get("primary_checkpoint") != "C30"
        or endpoint.get("pact_checkpoint") != "C30"
        or endpoint.get("knowledge_checkpoint") != "C30"
    ):
        raise CapabilityImprovementError(
            "validator review is pinned to the selected coherent C30 endpoint"
        )

    aggregate_paths = {
        "validation": validation_aggregate_path.expanduser().resolve(),
        "test": test_aggregate_path.expanduser().resolve(),
    }
    aggregates = {
        panel: _load_digest_document(
            path,
            "kb_evaluation_aggregate.schema.json",
            "aggregate_digest",
            f"C30 {panel} aggregate",
        )
        for panel, path in aggregate_paths.items()
    }
    for panel, aggregate in aggregates.items():
        if (
            aggregate.get("panel") != panel
            or aggregate.get("checkpoint") != "C30"
            or aggregate.get("benchmark_version") != LIBGEN_BENCHMARK_VERSION
            or aggregate.get("design_digest") != design["design_digest"]
        ):
            raise CapabilityImprovementError(
                f"{panel} aggregate is not the selected C30 v{LIBGEN_BENCHMARK_VERSION} result"
            )
        if aggregate.get("valid_count") != aggregate.get("protocol_count"):
            raise CapabilityImprovementError(
                f"{panel} aggregate contains invalid or missing trials"
            )

    compatibility_path = source_root / "design/c0_compatibility.json"
    compatibility = _load_digest_document(
        compatibility_path,
        "kb_c0_compatibility.schema.json",
        "registry_digest",
        "C0 compatibility registry",
    )
    if compatibility.get("design_digest") != design["design_digest"]:
        raise CapabilityImprovementError("C0 compatibility registry is stale")

    expected_protocols = [
        (panel, protocol_id)
        for panel in ("validation", "test")
        for protocol_id in design["split"][panel]
    ]
    if len(expected_protocols) != 15:
        raise CapabilityImprovementError(
            "validator review requires five validation and ten test protocols"
        )

    groundtruth_root = Path(str(experiment["private_roots"]["groundtruth_root"]))
    entry_inputs: list[dict[str, Any]] = []
    for position, (panel, protocol_id) in enumerate(expected_protocols, start=1):
        baseline_entry = _unique_by_protocol(
            compatibility["entries"], protocol_id, "C0 compatibility entry"
        )
        if (
            baseline_entry.get("panel") != panel
            or baseline_entry.get("status") != "compatible"
            or not isinstance(baseline_entry.get("baseline_trial"), Mapping)
        ):
            raise CapabilityImprovementError(
                f"{protocol_id} lacks a compatible C0 trial"
            )
        aggregate_trial = _unique_by_protocol(
            aggregates[panel]["trials"], protocol_id, f"C30 {panel} trial"
        )
        if aggregate_trial.get("status") != "valid" or not aggregate_trial.get(
            "trial_root"
        ):
            raise CapabilityImprovementError(f"{protocol_id} lacks a valid C30 trial")

        c0 = _condition_from_c0(baseline_entry, protocol_id)
        c30 = _condition_from_trial(
            Path(str(aggregate_trial["trial_root"])),
            protocol_id=protocol_id,
            aggregate_metrics=aggregate_trial["metrics"],
        )
        promoted_groundtruth = {
            task: _artifact_ref(groundtruth_root / protocol_id / filename)
            for task, filename in GROUNDTRUTH_FILES.items()
        }
        groundtruth = _evaluated_groundtruth(c30, promoted_groundtruth, protocol_id)
        source_manifest_path = (
            source_root / f"design/source_manifests/{protocol_id}.json"
        )
        source_manifest = load_json_object(
            source_manifest_path, label=f"{protocol_id} source manifest"
        )
        if source_manifest.get("protocol_id") != protocol_id:
            raise CapabilityImprovementError(
                f"source manifest targets another protocol: {source_manifest_path}"
            )
        _validate_primary_sources(
            source_manifest,
            source_base=groundtruth_root.parent,
            protocol_id=protocol_id,
        )
        entry_inputs.append(
            {
                "position": position,
                "panel": panel,
                "protocol_id": protocol_id,
                "comparison_path": (
                    f"comparisons/{position:02d}-{panel}-{protocol_id}.json"
                ),
                "source_manifest": _artifact_ref(source_manifest_path),
                "groundtruth": groundtruth,
                "conditions": {"C0": c0, "C30": c30},
            }
        )

    timestamp = _timestamp(created_at)
    manifest_payload = {
        "schema_version": "libstruct.libgen_kb_validator_review_manifest.v1",
        "review_id": review_id,
        "purpose": "validator_diagnostic_only",
        "source_experiment": {
            "root": source_root.as_posix(),
            "experiment_digest": experiment["experiment_digest"],
            "experiment_sha256": sha256_file(source_root / "experiment.json"),
            "design_digest": design["design_digest"],
            "final_lock_digest": final_lock["lock_digest"],
            "final_lock_sha256": sha256_file(final_lock_path),
            "verifier_source_digest": experiment["repository"][
                "verifier_source_digest"
            ],
        },
        "benchmark_version": LIBGEN_BENCHMARK_VERSION,
        "selected_checkpoint": "C30",
        "workspace_root": workspaces.as_posix(),
        "aggregate_inputs": {
            panel: _artifact_ref(path) for panel, path in aggregate_paths.items()
        },
        "review_scope": {
            "conditions_compared": ["C0", "C30"],
            "adjudicated_observations": "C30_substantive_only",
            "c0_role": "metric_and_persistent_behavior_context_only",
            "human_gate": "protocol_level_classification_approval",
        },
        "prohibited_actions": list(PROHIBITED_ACTIONS),
        "entries": [
            {
                key: copy.deepcopy(value)
                for key, value in item.items()
                if key != "conditions"
            }
            for item in entry_inputs
        ],
        "created_at": timestamp,
    }
    manifest = with_digest(manifest_payload, "manifest_digest")
    _validate_schema(manifest, MANIFEST_SCHEMA, "validator review manifest")

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))
    try:
        (stage / "comparisons").mkdir()
        (stage / "reviews").mkdir()
        write_json_atomic(stage / MANIFEST_PATH, manifest, mode=0o444)
        for item in entry_inputs:
            conditions = item["conditions"]
            comparison = with_digest(
                {
                    "schema_version": "libstruct.libgen_kb_validator_review_comparison.v1",
                    "review_manifest_digest": manifest["manifest_digest"],
                    "position": item["position"],
                    "panel": item["panel"],
                    "protocol_id": item["protocol_id"],
                    "benchmark_version": LIBGEN_BENCHMARK_VERSION,
                    "conditions": copy.deepcopy(conditions),
                    "metric_delta_c30_minus_c0": {
                        metric: conditions["C30"]["metrics"][metric]
                        - conditions["C0"]["metrics"][metric]
                        for metric in METRIC_KEYS
                    },
                    "adjudicated_observation_scope": "C30_substantive_only",
                    "source_manifest": copy.deepcopy(item["source_manifest"]),
                    "groundtruth": copy.deepcopy(item["groundtruth"]),
                    "created_at": timestamp,
                },
                "comparison_digest",
            )
            _validate_schema(
                comparison,
                COMPARISON_SCHEMA,
                f"{item['protocol_id']} validator comparison",
            )
            write_json_atomic(stage / item["comparison_path"], comparison, mode=0o444)
        (stage / "README.md").write_text(_review_readme(manifest), encoding="utf-8")
        os.replace(stage, target)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return validator_review_status(target)


def validate_validator_review(root: Path) -> dict[str, Any]:
    review_root = root.expanduser().resolve()
    manifest = _load_digest_document(
        review_root / MANIFEST_PATH,
        MANIFEST_SCHEMA,
        "manifest_digest",
        "validator review manifest",
    )
    source_root = Path(manifest["source_experiment"]["root"]).resolve()
    source_checks = {
        source_root / "experiment.json": manifest["source_experiment"][
            "experiment_sha256"
        ],
        source_root / "final_lock.json": manifest["source_experiment"][
            "final_lock_sha256"
        ],
    }
    for path, expected in source_checks.items():
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
            raise CapabilityImprovementError(
                f"validator review source provenance changed: {path}"
            )
    for ref in manifest["aggregate_inputs"].values():
        _validate_artifact_ref(ref)
    seen: set[str] = set()
    for entry in manifest["entries"]:
        protocol_id = entry["protocol_id"]
        if protocol_id in seen:
            raise CapabilityImprovementError(
                f"duplicate validator review protocol: {protocol_id}"
            )
        seen.add(protocol_id)
        _validate_artifact_ref(entry["source_manifest"])
        for ref in entry["groundtruth"].values():
            _validate_artifact_ref(ref)
        comparison = _load_digest_document(
            review_root / entry["comparison_path"],
            COMPARISON_SCHEMA,
            "comparison_digest",
            f"{protocol_id} validator comparison",
        )
        if (
            comparison["review_manifest_digest"] != manifest["manifest_digest"]
            or comparison["protocol_id"] != protocol_id
            or comparison["panel"] != entry["panel"]
            or comparison["position"] != entry["position"]
        ):
            raise CapabilityImprovementError(
                f"validator comparison is stale: {protocol_id}"
            )
        for condition in comparison["conditions"].values():
            for ref in condition["artifacts"].values():
                _validate_artifact_ref(ref)
    return manifest


def run_next_validator_review(
    *,
    review_root: Path,
    created_at: str,
    protocol_id: str | None = None,
    codex_executable: str = "codex",
    timeout_seconds: int = 7200,
    reasoning_effort: str = "max",
) -> dict[str, Any]:
    """Run one read-only Codex classification and compile its proposal."""

    root = review_root.expanduser().resolve()
    manifest = validate_validator_review(root)
    entry = _select_generation_entry(root, manifest, protocol_id)
    if entry is None:
        return {
            "status": "all_proposals_generated",
            "next_action": "review the remaining proposals",
            **validator_review_status(root),
        }
    review_dir = _review_dir(root, entry)
    review_dir.mkdir(parents=True, exist_ok=True)
    revision_round = _required_revision_round(review_dir)
    workspace = (
        Path(manifest["workspace_root"])
        / f"{entry['position']:02d}-{entry['panel']}-{entry['protocol_id']}"
        / f"r{revision_round}"
    )
    comparison_path = root / entry["comparison_path"]
    comparison = _load_digest_document(
        comparison_path,
        COMPARISON_SCHEMA,
        "comparison_digest",
        "validator comparison",
    )
    _stage_validator_workspace(
        review_root=root,
        manifest=manifest,
        entry=entry,
        comparison=comparison,
        workspace=workspace,
        revision_round=revision_round,
    )
    outputs = workspace / "outputs"
    suffix = "" if revision_round == 0 else "-r1"
    draft_path = outputs / f"proposal-draft{suffix}.json"
    event_path = outputs / f"codex{suffix}.events.jsonl"
    stderr_path = outputs / f"codex{suffix}.stderr.log"
    prompt = _review_prompt(
        comparison,
        revision_round=revision_round,
        revision_instruction=_revision_instruction(review_dir),
    )
    run = run_local_codex_json(
        workspace=workspace,
        prompt=prompt,
        schema_path=improvement_schema_root() / DRAFT_SCHEMA,
        output_path=draft_path,
        event_path=event_path,
        stderr_path=stderr_path,
        codex_executable=codex_executable,
        timeout_seconds=timeout_seconds,
        reasoning_effort=reasoning_effort,
    )
    source_experiment = Path(manifest["source_experiment"]["root"])
    source_manifest = validate_kb_experiment(source_experiment)
    _validate_codex_workspace_access(
        event_path=event_path,
        workspace=workspace,
        experiment_root=source_experiment,
        manifest=source_manifest,
    )
    proposal = compile_validator_review_proposal(
        review_root=root,
        draft_path=draft_path,
        created_at=created_at,
        proposer_version=run["version"],
        reasoning_effort=reasoning_effort,
        event_path=event_path,
        stderr_path=stderr_path,
    )
    return {
        "status": "awaiting_human_review",
        "protocol_id": entry["protocol_id"],
        "panel": entry["panel"],
        "proposal_path": _proposal_path(review_dir, revision_round).as_posix(),
        "proposal_digest": proposal["proposal_digest"],
        "validator_issue_candidate_count": sum(
            item["validator_issue_candidate"] for item in proposal["findings"]
        ),
        "workspace": workspace.as_posix(),
        "elapsed_seconds": run["elapsed_seconds"],
        "next_action": "show and human-review this proposal",
    }


def compile_validator_review_proposal(
    *,
    review_root: Path,
    draft_path: Path,
    created_at: str,
    proposer_version: str,
    reasoning_effort: str,
    event_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    root = review_root.expanduser().resolve()
    manifest = validate_validator_review(root)
    draft_resolved = draft_path.expanduser().resolve()
    draft = load_json_object(draft_resolved, label="validator proposal draft")
    entry = _entry_for_protocol(manifest, draft.get("protocol_id"))
    comparison_path = root / entry["comparison_path"]
    comparison = _load_digest_document(
        comparison_path,
        COMPARISON_SCHEMA,
        "comparison_digest",
        "validator comparison",
    )
    validate_validator_review_draft(draft, comparison)
    review_dir = _review_dir(root, entry)
    revision_round = int(draft["revision_round"])
    if revision_round != _required_revision_round(review_dir):
        raise CapabilityImprovementError(
            "validator proposal draft uses the wrong revision round"
        )
    prior_digest = None
    revision_decision_digest = None
    if revision_round == 1:
        prior = _load_digest_document(
            review_dir / "proposal.json",
            PROPOSAL_SCHEMA,
            "proposal_digest",
            "prior validator proposal",
        )
        decision = _load_digest_document(
            review_dir / "decision.json",
            DECISION_SCHEMA,
            "decision_digest",
            "validator revision decision",
        )
        if decision["disposition"] != "revision_requested":
            raise CapabilityImprovementError(
                "validator revision lacks a revision-request decision"
            )
        prior_digest = prior["proposal_digest"]
        revision_decision_digest = decision["decision_digest"]
    proposal_path = _proposal_path(review_dir, revision_round)
    payload = {
        "schema_version": "libstruct.libgen_kb_validator_review_proposal.v1",
        "proposal_id": f"{manifest['review_id']}:{entry['position']:02d}:{entry['protocol_id']}:r{revision_round}",
        "review_manifest_digest": manifest["manifest_digest"],
        "protocol_id": entry["protocol_id"],
        "panel": entry["panel"],
        "comparison_digest": comparison["comparison_digest"],
        "comparison_sha256": sha256_file(comparison_path),
        "draft_sha256": sha256_file(draft_resolved),
        "revision_round": revision_round,
        "revision_of_proposal_digest": prior_digest,
        "revision_request_decision_digest": revision_decision_digest,
        "summary": draft["summary"],
        "findings": copy.deepcopy(draft["findings"]),
        "no_validator_issue_rationale": draft["no_validator_issue_rationale"],
        "proposer": {
            "agent": "codex",
            "model": "gpt-5.6-sol",
            "version": proposer_version,
            "reasoning_effort": reasoning_effort,
            "event_sha256": sha256_file(event_path),
            "stderr_sha256": sha256_file(stderr_path),
        },
        "created_at": _timestamp(created_at),
    }
    proposal = with_digest(payload, "proposal_digest")
    _validate_schema(proposal, PROPOSAL_SCHEMA, "validator review proposal")
    if proposal_path.exists():
        existing = _load_digest_document(
            proposal_path,
            PROPOSAL_SCHEMA,
            "proposal_digest",
            "existing validator proposal",
        )
        if existing != proposal:
            raise CapabilityImprovementError(
                f"refusing to overwrite validator proposal: {proposal_path}"
            )
        return existing
    write_json_atomic(proposal_path, proposal, mode=0o444)
    return proposal


def validate_validator_review_draft(
    draft: Mapping[str, Any], comparison: Mapping[str, Any]
) -> None:
    document = copy.deepcopy(dict(draft))
    _validate_schema(document, DRAFT_SCHEMA, "validator proposal draft")
    if (
        document["protocol_id"] != comparison["protocol_id"]
        or document["comparison_digest"] != comparison["comparison_digest"]
    ):
        raise CapabilityImprovementError(
            "validator proposal draft targets another comparison"
        )
    error_ref = comparison["conditions"]["C30"]["artifacts"]["error_analysis"]
    _validate_artifact_ref(error_ref)
    analysis = load_json_object(
        Path(error_ref["path"]), label="C30 verifier error analysis"
    )
    expected = {
        item["error_id"]
        for item in analysis.get("observations", [])
        if item.get("substantive") is True
    }
    observed = [
        ref["error_id"]
        for finding in document["findings"]
        for ref in finding["observation_refs"]
    ]
    if len(observed) != len(set(observed)) or set(observed) != expected:
        missing = sorted(expected - set(observed))
        extra = sorted(set(observed) - expected)
        raise CapabilityImprovementError(
            "validator findings do not exactly partition C30 substantive observations; "
            f"missing={missing[:10]}, extra={extra[:10]}"
        )
    expected_finding_ids = [
        f"F{index:03d}" for index in range(1, len(document["findings"]) + 1)
    ]
    if [item["finding_id"] for item in document["findings"]] != expected_finding_ids:
        raise CapabilityImprovementError(
            "validator finding IDs must be consecutive in source order"
        )
    candidates = 0
    for finding in document["findings"]:
        candidate = finding["classification"] == "evaluator_defect"
        if finding["validator_issue_candidate"] is not candidate:
            raise CapabilityImprovementError(
                f"{finding['finding_id']} has an inconsistent validator-issue flag"
            )
        candidates += int(candidate)
        for evidence_group in (
            "source_evidence",
            "verifier_evidence",
            "trajectory_evidence",
        ):
            for evidence in finding[evidence_group]:
                _validate_workspace_evidence_path(evidence["artifact_path"])
        if (
            finding["classification"]
            in {
                "evaluator_defect",
                "ground_truth_defect",
                "source_scope_mismatch",
                "source_conflict",
            }
            and not finding["source_evidence"]
        ):
            raise CapabilityImprovementError(
                f"{finding['finding_id']} requires primary-source evidence"
            )
    rationale = document["no_validator_issue_rationale"]
    if candidates == 0 and not isinstance(rationale, str):
        raise CapabilityImprovementError(
            "a proposal with no validator issue needs an explicit rationale"
        )
    if candidates > 0 and rationale is not None:
        raise CapabilityImprovementError(
            "no-validator-issue rationale must be null when candidates exist"
        )


def record_validator_review_decision(
    *,
    review_root: Path,
    protocol_id: str,
    reviewer_id: str,
    disposition: str,
    rationale: str,
    started_at: str,
    completed_at: str,
    revision_instruction: str | None = None,
) -> dict[str, Any]:
    """Record a human classification decision without applying any change."""

    root = review_root.expanduser().resolve()
    manifest = validate_validator_review(root)
    entry = _entry_for_protocol(manifest, protocol_id)
    review_dir = _review_dir(root, entry)
    revision_round = 1 if (review_dir / "proposal-r1.json").is_file() else 0
    proposal_path = _proposal_path(review_dir, revision_round)
    proposal = _load_digest_document(
        proposal_path,
        PROPOSAL_SCHEMA,
        "proposal_digest",
        "validator proposal",
    )
    if disposition not in {"approve", "reject", "revision_requested", "unresolved"}:
        raise CapabilityImprovementError(
            f"invalid validator review disposition: {disposition}"
        )
    if not _ID_RE.fullmatch(reviewer_id) or not rationale.strip():
        raise CapabilityImprovementError(
            "validator decision requires a valid reviewer ID and rationale"
        )
    if disposition == "revision_requested":
        if revision_round != 0:
            raise CapabilityImprovementError(
                "only one validator-review revision is allowed"
            )
        if not revision_instruction or not revision_instruction.strip():
            raise CapabilityImprovementError(
                "revision_requested requires a revision instruction"
            )
    elif revision_instruction is not None:
        raise CapabilityImprovementError(
            "revision instruction is allowed only for revision_requested"
        )
    started = _timestamp(started_at)
    completed = _timestamp(completed_at)
    if _parse_time(completed) < _parse_time(started):
        raise CapabilityImprovementError("validator review completed before it started")
    decision_path = _decision_path(review_dir, revision_round)
    payload = {
        "schema_version": "libstruct.libgen_kb_validator_review_decision.v1",
        "decision_id": f"{proposal['proposal_id']}:decision",
        "review_manifest_digest": manifest["manifest_digest"],
        "protocol_id": entry["protocol_id"],
        "panel": entry["panel"],
        "proposal_digest": proposal["proposal_digest"],
        "proposal_sha256": sha256_file(proposal_path),
        "revision_round": revision_round,
        "reviewer_id": reviewer_id,
        "reviewer_kind": "human",
        "disposition": disposition,
        "rationale": rationale.strip(),
        "revision_instruction": (
            revision_instruction.strip() if revision_instruction is not None else None
        ),
        "effect": "classification_record_only_no_mutation",
        "started_at": started,
        "completed_at": completed,
    }
    decision = with_digest(payload, "decision_digest")
    _validate_schema(decision, DECISION_SCHEMA, "validator review decision")
    if decision_path.exists():
        existing = _load_digest_document(
            decision_path,
            DECISION_SCHEMA,
            "decision_digest",
            "existing validator decision",
        )
        if existing != decision:
            raise CapabilityImprovementError(
                f"refusing to overwrite validator decision: {decision_path}"
            )
        return existing
    write_json_atomic(decision_path, decision, mode=0o444)
    return {
        **decision,
        "status": (
            "revision_requested"
            if disposition == "revision_requested"
            else "review_complete"
        ),
        "next_action": (
            "run the bounded r1 reviewer"
            if disposition == "revision_requested"
            else validator_review_status(root).get("next_action")
        ),
    }


def render_validator_review(review_root: Path, protocol_id: str | None = None) -> str:
    root = review_root.expanduser().resolve()
    manifest = validate_validator_review(root)
    entry = (
        _entry_for_protocol(manifest, protocol_id)
        if protocol_id is not None
        else _select_human_review_entry(root, manifest)
    )
    if entry is None:
        return "All validator diagnostic proposals have final human decisions."
    review_dir = _review_dir(root, entry)
    revision_round = 1 if (review_dir / "proposal-r1.json").is_file() else 0
    proposal = _load_digest_document(
        _proposal_path(review_dir, revision_round),
        PROPOSAL_SCHEMA,
        "proposal_digest",
        "validator proposal",
    )
    comparison = _load_digest_document(
        root / entry["comparison_path"],
        COMPARISON_SCHEMA,
        "comparison_digest",
        "validator comparison",
    )
    metrics = [
        ("Reward", "reward"),
        ("T2 exact recall", "t2_exact_required_family_recall"),
        ("T2 family F1", "t2_required_family_f1"),
        ("T3 transition F1", "t3_molecular_transition_f1"),
        ("T3 state F1", "t3_state_f1"),
        ("T3 edge F1", "t3_typed_edge_f1"),
    ]
    lines = [
        f"VALIDATOR DIAGNOSTIC REVIEW — {entry['protocol_id']}",
        f"Panel {entry['panel']} | item {entry['position']}/15 | proposal r{revision_round}",
        "",
        "Metric                  C0       C30      Delta",
        "--------------------  -------  -------  --------",
    ]
    for label, key in metrics:
        c0 = comparison["conditions"]["C0"]["metrics"][key]
        c30 = comparison["conditions"]["C30"]["metrics"][key]
        lines.append(f"{label:<20}  {c0:7.3f}  {c30:7.3f}  {c30 - c0:+8.3f}")
    c30_count = comparison["conditions"]["C30"]["substantive_observation_count"]
    candidates = [
        item for item in proposal["findings"] if item["validator_issue_candidate"]
    ]
    context = [
        item for item in proposal["findings"] if not item["validator_issue_candidate"]
    ]
    lines.extend(
        [
            "",
            f"C30 substantive observations: {c30_count}; consolidated findings: {len(proposal['findings'])}",
            f"Proposed validator issues: {len(candidates)}",
            "",
            proposal["summary"],
            "",
        ]
    )
    if candidates:
        lines.append("PROPOSED VALIDATOR ISSUES")
        for finding in candidates:
            lines.extend(_render_finding(finding, detailed=True))
    else:
        lines.extend(
            [
                "NO VALIDATOR ISSUE PROPOSED",
                str(proposal["no_validator_issue_rationale"]),
                "",
            ]
        )
    if context:
        lines.append("CONTEXT / EXCLUDED FROM VALIDATOR-ISSUE SET")
        counts = Counter(item["classification"] for item in context)
        lines.append(
            ", ".join(f"{key}={counts[key]}" for key in CLASSIFICATIONS if counts[key])
        )
        for finding in context:
            lines.extend(_render_finding(finding, detailed=False))
    lines.extend(
        [
            "",
            "Human decision approves or rejects this classification package only.",
            "It cannot alter the agent, pact, prompt, knowledge base, verifier, or ground truth.",
        ]
    )
    return "\n".join(lines)


def validator_review_status(review_root: Path) -> dict[str, Any]:
    root = review_root.expanduser().resolve()
    manifest = _load_digest_document(
        root / MANIFEST_PATH,
        MANIFEST_SCHEMA,
        "manifest_digest",
        "validator review manifest",
    )
    rows: list[dict[str, Any]] = []
    for entry in manifest["entries"]:
        review_dir = _review_dir(root, entry)
        proposal_path = (
            review_dir / "proposal-r1.json"
            if (review_dir / "proposal-r1.json").is_file()
            else review_dir / "proposal.json"
        )
        decision_path = (
            review_dir / "decision-r1.json"
            if (review_dir / "decision-r1.json").is_file()
            else review_dir / "decision.json"
        )
        proposal = None
        decision = None
        if proposal_path.is_file():
            proposal = _load_digest_document(
                proposal_path,
                PROPOSAL_SCHEMA,
                "proposal_digest",
                "validator proposal",
            )
        if decision_path.is_file():
            decision = _load_digest_document(
                decision_path,
                DECISION_SCHEMA,
                "decision_digest",
                "validator decision",
            )
        final = decision is not None and decision["disposition"] != "revision_requested"
        rows.append(
            {
                "position": entry["position"],
                "panel": entry["panel"],
                "protocol_id": entry["protocol_id"],
                "proposal": proposal is not None,
                "validator_issue_candidate_count": (
                    None
                    if proposal is None
                    else sum(
                        item["validator_issue_candidate"]
                        for item in proposal["findings"]
                    )
                ),
                "decision": None if decision is None else decision["disposition"],
                "final": final,
            }
        )
    proposed = sum(item["proposal"] for item in rows)
    final = sum(item["final"] for item in rows)
    next_generation = next((item for item in rows if not item["proposal"]), None)
    next_human = next(
        (
            item
            for item in rows
            if item["proposal"]
            and not item["final"]
            and item["decision"] != "revision_requested"
        ),
        None,
    )
    if next_human is not None:
        next_action = f"review {next_human['protocol_id']}"
    elif next_generation is not None:
        next_action = f"generate proposal for {next_generation['protocol_id']}"
    elif final == len(rows):
        next_action = "review complete; report confirmed diagnostic classifications"
    else:
        next_action = "run the requested bounded revision"
    return {
        "status": "complete" if final == len(rows) else "open",
        "review_id": manifest["review_id"],
        "purpose": manifest["purpose"],
        "protocol_count": len(rows),
        "proposal_count": proposed,
        "final_decision_count": final,
        "approved_count": sum(item["decision"] == "approve" for item in rows),
        "validator_issue_candidate_count": sum(
            item["validator_issue_candidate_count"] or 0 for item in rows
        ),
        "next_action": next_action,
        "entries": rows,
    }


def _condition_from_c0(entry: Mapping[str, Any], protocol_id: str) -> dict[str, Any]:
    trial = entry["baseline_trial"]
    artifact_map = trial["artifacts"]
    artifacts = {
        key: copy.deepcopy(artifact_map[key])
        for key in (
            "result",
            "t2_prediction",
            "t3_prediction",
            "trajectory",
            "reward",
            "verifier_details",
            "error_analysis",
        )
    }
    for ref in artifacts.values():
        _validate_artifact_ref(ref)
    return _condition_document(
        trial_root=Path(trial["trial_root"]),
        trial_digest=trial["trial_digest"],
        artifacts=artifacts,
        protocol_id=protocol_id,
    )


def _condition_from_trial(
    trial_root: Path,
    *,
    protocol_id: str,
    aggregate_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    resolved = resolve_trial_artifacts(trial_root, protocol_id=protocol_id)
    artifacts = {
        key: _artifact_ref(Path(resolved[key]))
        for key in (
            "result",
            "t2_prediction",
            "t3_prediction",
            "trajectory",
            "reward",
            "verifier_details",
            "error_analysis",
        )
    }
    condition = _condition_document(
        trial_root=trial_root,
        trial_digest=with_digest(
            {"trial_root": trial_root.resolve().as_posix(), "artifacts": artifacts},
            "trial_digest",
        )["trial_digest"],
        artifacts=artifacts,
        protocol_id=protocol_id,
    )
    for metric in METRIC_KEYS:
        if abs(condition["metrics"][metric] - float(aggregate_metrics[metric])) > 1e-12:
            raise CapabilityImprovementError(
                f"C30 aggregate metric changed for {protocol_id}: {metric}"
            )
    return condition


def _condition_document(
    *,
    trial_root: Path,
    trial_digest: str,
    artifacts: Mapping[str, Mapping[str, str]],
    protocol_id: str,
) -> dict[str, Any]:
    reward = load_json_object(Path(artifacts["reward"]["path"]), label="reward")
    details = load_json_object(
        Path(artifacts["verifier_details"]["path"]), label="verifier details"
    )
    analysis = load_json_object(
        Path(artifacts["error_analysis"]["path"]), label="verifier error analysis"
    )
    if (
        details.get("benchmark_version") != LIBGEN_BENCHMARK_VERSION
        or details.get("protocol_id") != protocol_id
        or details.get("prediction_valid") is not True
        or analysis.get("protocol_id") != protocol_id
    ):
        raise CapabilityImprovementError(
            f"trial is not a valid {protocol_id} v{LIBGEN_BENCHMARK_VERSION} result"
        )
    substantive = [
        item
        for item in analysis.get("observations", [])
        if item.get("substantive") is True
    ]
    ids = [item.get("error_id") for item in substantive]
    if any(
        not isinstance(value, str) or not _ERROR_ID_RE.fullmatch(value) for value in ids
    ) or len(ids) != len(set(ids)):
        raise CapabilityImprovementError(
            f"verifier observations have malformed IDs for {protocol_id}"
        )
    return {
        "trial_root": trial_root.expanduser().resolve().as_posix(),
        "trial_digest": trial_digest,
        "status": "valid",
        "metrics": {metric: float(reward[metric]) for metric in METRIC_KEYS},
        "substantive_observation_count": len(substantive),
        "observation_category_counts": dict(
            sorted(
                Counter(
                    str(item.get("category", "unknown")) for item in substantive
                ).items()
            )
        ),
        "artifacts": copy.deepcopy(dict(artifacts)),
    }


def _evaluated_groundtruth(
    condition: Mapping[str, Any],
    promoted_groundtruth: Mapping[str, Mapping[str, str]],
    protocol_id: str,
) -> dict[str, dict[str, str]]:
    """Resolve the exact task-side target bytes consumed by the C30 verifier.

    Task generation can deterministically migrate a connected-process record
    into the current scoring representation. That may change bytes without
    changing the underlying workflow, so this review pins the projected task
    target instead of requiring byte identity with its promoted source.
    """

    config_path = Path(condition["trial_root"]) / "config.json"
    if not config_path.is_file():
        return copy.deepcopy(dict(promoted_groundtruth))
    config = load_json_object(config_path, label=f"{protocol_id} C30 config")
    task_path = (config.get("task") or {}).get("path")
    if not isinstance(task_path, str):
        return copy.deepcopy(dict(promoted_groundtruth))
    task_root = Path(task_path).expanduser()
    if not task_root.is_absolute():
        return copy.deepcopy(dict(promoted_groundtruth))
    evaluated_root = task_root / "tests/groundtruth"
    if not evaluated_root.is_dir():
        return copy.deepcopy(dict(promoted_groundtruth))
    evaluated_groundtruth = {
        task: _artifact_ref(evaluated_root / filename)
        for task, filename in GROUNDTRUTH_FILES.items()
    }
    for task, ref in evaluated_groundtruth.items():
        document = load_json_object(
            Path(ref["path"]), label=f"{protocol_id} evaluated {task} ground truth"
        )
        if document.get("protocol_id") != protocol_id:
            raise CapabilityImprovementError(
                f"evaluated ground truth targets another protocol: {protocol_id} {task}"
            )
    return evaluated_groundtruth


def _validate_primary_sources(
    source_manifest: Mapping[str, Any], *, source_base: Path, protocol_id: str
) -> None:
    included = 0
    for source in source_manifest.get("sources", []):
        if (
            source.get("role") != "primary_evidence"
            or source.get("approval_status") != "included"
        ):
            continue
        included += 1
        path = (source_base / str(source["path"])).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != source["sha256"]
        ):
            raise CapabilityImprovementError(
                f"approved primary source changed for {protocol_id}: {path}"
            )
    if included == 0:
        raise CapabilityImprovementError(
            f"{protocol_id} source manifest has no included primary evidence"
        )


def _stage_validator_workspace(
    *,
    review_root: Path,
    manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
    comparison: Mapping[str, Any],
    workspace: Path,
    revision_round: int,
) -> None:
    if workspace.exists():
        _validate_workspace(workspace, comparison)
        return
    workspace.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{workspace.name}.stage-", dir=workspace.parent)
    )
    try:
        inputs = stage / "inputs"
        (inputs / "C0").mkdir(parents=True)
        (inputs / "C30").mkdir()
        (inputs / "groundtruth").mkdir()
        (inputs / "sources").mkdir()
        (inputs / "policies").mkdir()
        (inputs / "verifier_contract").mkdir()
        (stage / "outputs").mkdir()
        shutil.copy2(review_root / entry["comparison_path"], inputs / "comparison.json")
        for condition_name in ("C0", "C30"):
            for key, ref in comparison["conditions"][condition_name][
                "artifacts"
            ].items():
                shutil.copy2(Path(ref["path"]), inputs / condition_name / f"{key}.json")
        for task, ref in comparison["groundtruth"].items():
            shutil.copy2(
                Path(ref["path"]), inputs / "groundtruth" / GROUNDTRUTH_FILES[task]
            )
        source_manifest = load_json_object(
            Path(comparison["source_manifest"]["path"]), label="source manifest"
        )
        source_experiment = validate_kb_experiment(
            Path(manifest["source_experiment"]["root"])
        )
        source_base = Path(
            source_experiment["private_roots"]["groundtruth_root"]
        ).parent
        source_index = []
        for index, source in enumerate(source_manifest.get("sources", []), start=1):
            if (
                source.get("role") != "primary_evidence"
                or source.get("approval_status") != "included"
            ):
                continue
            source_path = (source_base / source["path"]).resolve()
            safe_name = f"{index:03d}-{Path(source['path']).name}"
            shutil.copy2(source_path, inputs / "sources" / safe_name)
            source_index.append(
                {
                    "source_id": source["source_id"],
                    "file": safe_name,
                    "sha256": source["sha256"],
                }
            )
        write_json_atomic(inputs / "source_index.json", {"sources": source_index})
        repository = Path(source_experiment["repository"]["path"])
        for relative in (
            "docs/audit/README.md",
            "docs/audit/evidence-policy.md",
            "docs/audit/adjudication-policy.md",
            "docs/audit/benchmark-standardization-policy.md",
        ):
            shutil.copy2(
                repository / relative, inputs / "policies" / Path(relative).name
            )
        verifier_files = [
            repository / "paper/benchmarks/tasks2_3/rules.md",
            *sorted((repository / "src/libstruct_bench/libgen").glob("*.py")),
            *sorted((repository / "schemas/benchmark").glob("*.json")),
            *sorted((repository / "schemas/groundtruth").glob("*.json")),
        ]
        for index, source in enumerate(verifier_files, start=1):
            shutil.copy2(
                source, inputs / "verifier_contract" / f"{index:03d}-{source.name}"
            )
        if revision_round == 1:
            review_dir = _review_dir(review_root, entry)
            shutil.copy2(review_dir / "proposal.json", inputs / "prior_proposal.json")
            shutil.copy2(
                review_dir / "decision.json", inputs / "revision_decision.json"
            )
        index = _workspace_index(inputs)
        write_json_atomic(inputs / "workspace_index.json", index)
        _freeze_inputs(inputs)
        os.replace(stage, workspace)
        _validate_workspace(workspace, comparison)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _validate_workspace(workspace: Path, comparison: Mapping[str, Any]) -> None:
    inputs = workspace / "inputs"
    index_path = inputs / "workspace_index.json"
    if not index_path.is_file() or index_path.is_symlink():
        raise CapabilityImprovementError(
            f"validator workspace is incomplete: {workspace}"
        )
    index = load_json_object(index_path, label="validator workspace index")
    for item in index.get("files", []):
        path = inputs / item["path"]
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != item["sha256"]
        ):
            raise CapabilityImprovementError(
                f"validator workspace input changed: {item.get('path')}"
            )
    staged = load_json_object(inputs / "comparison.json", label="staged comparison")
    if staged.get("comparison_digest") != comparison["comparison_digest"]:
        raise CapabilityImprovementError("validator workspace pins another comparison")
    if not (workspace / "outputs").is_dir() or (workspace / "outputs").is_symlink():
        raise CapabilityImprovementError(
            "validator workspace lacks an output directory"
        )


def _workspace_index(inputs: Path) -> dict[str, Any]:
    return {
        "schema_version": "libstruct.validator_review_workspace_index.v1",
        "files": [
            {
                "path": path.relative_to(inputs).as_posix(),
                "sha256": sha256_file(path),
            }
            for path in sorted(inputs.rglob("*"))
            if path.is_file() and path.name != "workspace_index.json"
        ],
    }


def _review_prompt(
    comparison: Mapping[str, Any],
    *,
    revision_round: int,
    revision_instruction: str | None,
) -> str:
    revision = ""
    if revision_round == 1:
        revision = f"""
This is the one permitted revision. Read `inputs/prior_proposal.json` and
`inputs/revision_decision.json`. Address exactly this human instruction:
{revision_instruction}
"""
    return f"""You are a scientific audit assistant reviewing whether LibGen verifier v{LIBGEN_BENCHMARK_VERSION} produced misleading discrepancies for one preserved C30 result.

Protocol: {comparison["protocol_id"]}
Panel: {comparison["panel"]}
{revision}
This is diagnostic-only. Do not propose or make changes to the agent, prompt, pact, knowledge base, verifier, ground truth, or any prediction. Do not rerun the reconstruction agent. Inspect only files below `inputs/`; do not browse the web or use remembered kit knowledge. Use only paths relative to the current workspace, never invoke `git`, and never inspect or name a parent directory or any absolute filesystem path.

Set `revision_round` to exactly {revision_round}. In every evidence reference,
use a workspace-relative path beginning with `inputs/sources/`, `inputs/C0/`,
`inputs/C30/`, or `inputs/verifier_contract/`. Never cite
`inputs/source_index.json`, `inputs/comparison.json`, or a path beginning only
with `sources/`.

Read the audit policies first. The files under `inputs/groundtruth/` are the exact benchmark target used for comparison, but ground truth is not independent scientific evidence. Only manifest-approved files under `inputs/sources/` are primary evidence. `inputs/verifier_contract/` is a review-time diagnostic snapshot; observed `C30/verifier_details.json` and `C30/error_analysis.json` remain the execution record.

Tasks:
1. Read `inputs/C30/error_analysis.json`. Every observation with `substantive: true` must appear exactly once across findings' `observation_refs`. Consolidate dependent symptoms into the smallest causal findings; do not copy observations one by one.
2. Use C0 metrics, predictions, verifier details, error analysis, and trajectory only as comparison context. C0 observations are not part of the required partition.
3. Classify each finding as one of: valid_agent_difference, evaluator_defect, ground_truth_defect, source_scope_mismatch, representation_policy_ambiguity, source_conflict, infrastructure, unresolved.
4. Use evaluator_defect only when the scorer or deterministic discrepancy generator falsely penalizes, falsely credits, or misdescribes a scientifically equivalent/non-equivalent output. Set validator_issue_candidate true exactly for evaluator_defect.
5. A disagreement caused by the target record is ground_truth_defect, not evaluator_defect. A missing policy for equivalent representations is representation_policy_ambiguity, not evaluator_defect. If the primary packet cannot settle the issue, use source_conflict or unresolved.
6. For evaluator_defect, ground_truth_defect, source_scope_mismatch, or source_conflict, cite at least one precise primary-source locator. Every finding must cite the exact verifier observation/detail locator that motivated it. Trajectory evidence is optional because this review does not diagnose or change agent behavior.
7. Use F001, F002, ... in source order. If no evaluator defect is found, give a concrete no_validator_issue_rationale; otherwise set it to null.

Return only the schema-conforming JSON object. Never include a remedy for agent behavior or a proposed code/ground-truth edit."""


def _select_generation_entry(
    root: Path, manifest: Mapping[str, Any], protocol_id: str | None
) -> Mapping[str, Any] | None:
    entries = (
        [_entry_for_protocol(manifest, protocol_id)]
        if protocol_id is not None
        else manifest["entries"]
    )
    for entry in entries:
        review_dir = _review_dir(root, entry)
        if not (review_dir / "proposal.json").is_file():
            return entry
        decision_path = review_dir / "decision.json"
        if decision_path.is_file():
            decision = _load_digest_document(
                decision_path,
                DECISION_SCHEMA,
                "decision_digest",
                "validator decision",
            )
            if (
                decision["disposition"] == "revision_requested"
                and not (review_dir / "proposal-r1.json").is_file()
            ):
                return entry
    return None


def _select_human_review_entry(
    root: Path, manifest: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    for entry in manifest["entries"]:
        review_dir = _review_dir(root, entry)
        proposal_path = (
            review_dir / "proposal-r1.json"
            if (review_dir / "proposal-r1.json").is_file()
            else review_dir / "proposal.json"
        )
        if not proposal_path.is_file():
            continue
        decision_path = (
            review_dir / "decision-r1.json"
            if (review_dir / "proposal-r1.json").is_file()
            else review_dir / "decision.json"
        )
        if not decision_path.is_file():
            return entry
    return None


def _required_revision_round(review_dir: Path) -> int:
    if not (review_dir / "proposal.json").is_file():
        return 0
    decision_path = review_dir / "decision.json"
    if not decision_path.is_file():
        raise CapabilityImprovementError(
            f"initial proposal awaits human review: {review_dir}"
        )
    decision = _load_digest_document(
        decision_path,
        DECISION_SCHEMA,
        "decision_digest",
        "validator decision",
    )
    if decision["disposition"] != "revision_requested":
        raise CapabilityImprovementError(
            f"validator review already has a final decision: {review_dir}"
        )
    if (review_dir / "proposal-r1.json").exists():
        raise CapabilityImprovementError(
            f"validator revision proposal already exists: {review_dir}"
        )
    return 1


def _revision_instruction(review_dir: Path) -> str | None:
    path = review_dir / "decision.json"
    if not path.is_file():
        return None
    decision = _load_digest_document(
        path, DECISION_SCHEMA, "decision_digest", "validator decision"
    )
    return decision.get("revision_instruction")


def _entry_for_protocol(
    manifest: Mapping[str, Any], protocol_id: Any
) -> Mapping[str, Any]:
    if not isinstance(protocol_id, str):
        raise CapabilityImprovementError("validator review protocol ID is missing")
    matches = [
        item for item in manifest["entries"] if item["protocol_id"] == protocol_id
    ]
    if len(matches) != 1:
        raise CapabilityImprovementError(
            f"expected one validator review entry for {protocol_id}, found {len(matches)}"
        )
    return matches[0]


def _unique_by_protocol(
    entries: Sequence[Mapping[str, Any]], protocol_id: str, label: str
) -> Mapping[str, Any]:
    matches = [item for item in entries if item.get("protocol_id") == protocol_id]
    if len(matches) != 1:
        raise CapabilityImprovementError(
            f"expected one {label} for {protocol_id}, found {len(matches)}"
        )
    return matches[0]


def _review_dir(root: Path, entry: Mapping[str, Any]) -> Path:
    return (
        root
        / "reviews"
        / f"{entry['position']:02d}-{entry['panel']}-{entry['protocol_id']}"
    )


def _proposal_path(review_dir: Path, revision_round: int) -> Path:
    return review_dir / ("proposal.json" if revision_round == 0 else "proposal-r1.json")


def _decision_path(review_dir: Path, revision_round: int) -> Path:
    return review_dir / ("decision.json" if revision_round == 0 else "decision-r1.json")


def _artifact_ref(path: Path) -> dict[str, str]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise CapabilityImprovementError(f"artifact is missing or unsafe: {resolved}")
    return {"path": resolved.as_posix(), "sha256": sha256_file(resolved)}


def _validate_artifact_ref(ref: Mapping[str, Any]) -> None:
    path = Path(str(ref.get("path", ""))).expanduser().resolve()
    if (
        not path.is_file()
        or path.is_symlink()
        or sha256_file(path) != ref.get("sha256")
    ):
        raise CapabilityImprovementError(f"artifact reference changed: {path}")


def _validate_workspace_evidence_path(value: str) -> None:
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise CapabilityImprovementError(f"unsafe evidence path: {value!r}")
    allowed = (
        "inputs/sources/",
        "inputs/groundtruth/",
        "inputs/C0/",
        "inputs/C30/",
        "inputs/policies/",
        "inputs/verifier_contract/",
    )
    if value != "inputs/comparison.json" and not value.startswith(allowed):
        raise CapabilityImprovementError(
            f"evidence path is outside the staged review boundary: {value}"
        )


def _load_digest_document(
    path: Path, schema: str, digest_field: str, label: str
) -> dict[str, Any]:
    try:
        document = load_json_object(path, label=label)
        validate_document(document, improvement_schema_root() / schema, label=label)
        validate_digest(document, digest_field)
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    return document


def _validate_schema(document: Mapping[str, Any], schema: str, label: str) -> None:
    try:
        validate_document(
            copy.deepcopy(dict(document)),
            improvement_schema_root() / schema,
            label=label,
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _freeze_inputs(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise CapabilityImprovementError(f"symlink is forbidden: {path}")
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    root.chmod(0o555)


def _timestamp(value: str) -> str:
    try:
        return normalize_timestamp(value)
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _render_finding(finding: Mapping[str, Any], *, detailed: bool) -> list[str]:
    observations = ", ".join(ref["error_id"] for ref in finding["observation_refs"])
    lines = [
        "",
        f"{finding['finding_id']} [{finding['classification']}; {finding['confidence']}] {observations}",
        f"  {finding['diagnosis']}",
        f"  Score effect: {finding['score_effect']}; conditions: {', '.join(finding['conditions_affected'])}",
    ]
    if detailed:
        for title, key in (
            ("Source", "source_evidence"),
            ("Verifier", "verifier_evidence"),
        ):
            for evidence in finding[key]:
                lines.append(
                    f"  {title}: {evidence['artifact_path']} @ {evidence['locator']} — {evidence['summary']}"
                )
    return lines


def _review_readme(manifest: Mapping[str, Any]) -> str:
    return f"""# Validator diagnostic review

This branch reviews preserved C0 and selected C30 validation/test outputs from
`{manifest["source_experiment"]["root"]}`. It adjudicates substantive C30
verifier observations while retaining C0 only as comparison context.

The branch is classification-only. It has no synthesis, application, promotion,
agent-rerun, pact-update, prompt-update, knowledge-base-update, ground-truth-update,
or verifier-update operation. Human approval means only that the proposed
diagnostic classifications may be reported.
"""
