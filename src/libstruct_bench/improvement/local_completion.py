from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from libstruct_bench.audit.artifacts import sha256_file, write_json_atomic

from .agent import compile_capability_proposal_draft, compile_independent_decision_draft
from .artifacts import (
    CapabilityImprovementError,
    load_and_validate,
    trees_byte_identical,
    validate_capability_pack,
)
from .experiment import (
    build_cumulative_leakage_policy,
    validate_cumulative_leakage_policy,
    validate_experiment_manifest,
)
from .isolation import (
    prepare_isolated_worker_workspace,
    validate_isolated_worker_workspace,
)
from .governance import assert_capability_modification_open
from .local_learning import (
    LocalCodexRunner,
    LocalCodexRunRequest,
    LocalCodexRunResult,
    run_native_codex,
)
from .lineage import (
    ACTIVE_BRANCH,
    checkpoint_id,
    parent_checkpoint,
    require_active_branch,
)
from .mutation_lock import guard_experiment_mutation
from .review_summary import (
    render_capability_review_summary,
    render_completion_summary,
    write_or_validate_capability_review_summary,
)
from .review_console import InputFunction, run_human_review_console
from .workflow import (
    apply_capability_decision,
    checkpoint_exemplar_max_results,
    freeze_checkpoint,
    run_pack_synthetic_suite_docker,
    validate_capability_decision,
    validate_capability_proposal,
    validate_checkpoint_runtime,
)


ProgressCallback = Callable[[str], None]
TimestampProvider = Callable[[], str]
SyntheticRunner = Callable[[Path], Sequence[str]]


@guard_experiment_mutation("capability completion")
def run_capability_completion(
    *,
    branch: str,
    review_mode: str = "independent",
    experiment_root: Path,
    batch_id: str,
    groundtruth_root: Path,
    authorize_apply: bool = False,
    exemplar_max_results: int = 3,
    reviewer_id: str | None = None,
    manual_revision: bool = False,
    manual_revision_ready: bool = False,
    input_function: InputFunction = input,
    output: TextIO = sys.stdout,
    **kwargs: Any,
) -> dict[str, Any]:
    """Complete the cumulative branch using an independent or human review.

    Reviewer mode is deliberately orthogonal to pack lineage: either review
    mode writes to the same cumulative branch and produces the same C-series
    checkpoint chain.
    """

    require_active_branch(branch)
    if review_mode == "independent":
        if reviewer_id is not None or manual_revision or manual_revision_ready:
            raise CapabilityImprovementError(
                "human review options require --review-mode human"
            )
        independent_kwargs = dict(kwargs)
        independent_kwargs.pop("review_workspace_root", None)
        independent_kwargs.pop("revised_review_workspace_root", None)
        return run_independent_completion(
            experiment_root=experiment_root,
            batch_id=batch_id,
            groundtruth_root=groundtruth_root,
            authorize_apply=authorize_apply,
            exemplar_max_results=exemplar_max_results,
            **independent_kwargs,
        )
    if review_mode != "human":
        raise CapabilityImprovementError("review mode must be independent or human")
    if reviewer_id is None or not reviewer_id.strip():
        raise CapabilityImprovementError(
            "human review requires a non-empty reviewer ID"
        )
    human_kwargs = dict(kwargs)
    human_kwargs.pop("critic_workspace_root", None)
    human_kwargs.pop("revised_critic_workspace_root", None)
    return run_human_completion(
        experiment_root=experiment_root,
        batch_id=batch_id,
        groundtruth_root=groundtruth_root,
        authorize_apply=authorize_apply,
        exemplar_max_results=exemplar_max_results,
        reviewer_id=reviewer_id.strip(),
        manual_revision=manual_revision or manual_revision_ready,
        manual_revision_ready=manual_revision_ready,
        input_function=input_function,
        output=output,
        **human_kwargs,
    )


@guard_experiment_mutation("independent cumulative capability completion")
def run_independent_completion(
    *,
    experiment_root: Path,
    batch_id: str,
    groundtruth_root: Path,
    authorize_apply: bool = False,
    exemplar_max_results: int = 3,
    parent_pack_root: Path | None = None,
    round_root: Path | None = None,
    critic_workspace_root: Path | None = None,
    revision_workspace_root: Path | None = None,
    revised_critic_workspace_root: Path | None = None,
    codex_executable: str = "codex",
    idle_timeout_seconds: float = 300.0,
    hard_timeout_seconds: float = 7200.0,
    docker_image: str = "python:3.13-slim",
    critic_runner: LocalCodexRunner | None = None,
    revision_runner: LocalCodexRunner | None = None,
    synthetic_runner: SyntheticRunner | None = None,
    timestamp: TimestampProvider | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Review, once-revise if needed, and optionally apply one proposal.

    The operator sees one resumable command, while the proposal, independent
    critic decision, deterministic application, and frozen checkpoint remain
    separate hash-pinned artifacts. A revision request triggers exactly one
    bounded revision and a fresh independent review. Application requires the
    explicit ``authorize_apply`` gate and only occurs after a final decision.
    """

    if idle_timeout_seconds <= 0 or hard_timeout_seconds <= 0:
        raise CapabilityImprovementError("Codex timeouts must be positive")
    if hard_timeout_seconds <= idle_timeout_seconds:
        raise CapabilityImprovementError(
            "hard timeout must be greater than the idle-after-draft timeout"
        )
    announce = progress or (lambda _message: None)
    clock = timestamp or _utc_now
    root = experiment_root.expanduser().resolve()
    assert_capability_modification_open(root)
    experiment = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    batch_index, batch = _batch(experiment, batch_id)
    protocol_count = (batch_index + 1) * 5
    if len(batch["protocol_ids"]) != 5:
        raise CapabilityImprovementError(
            f"{batch_id} must contain exactly five protocols"
        )
    work_root = (
        round_root.expanduser().resolve()
        if round_root is not None
        else root / "rounds" / batch_id / ACTIVE_BRANCH
    )
    expected_parent_root = _default_parent_pack(root, experiment, batch_index)
    parent_root = (
        parent_pack_root.expanduser().resolve()
        if parent_pack_root is not None
        else expected_parent_root
    )
    if parent_root != expected_parent_root.expanduser().resolve():
        raise CapabilityImprovementError(
            f"parent pack must be the canonical cumulative checkpoint: {expected_parent_root}"
        )
    parent_checkpoint, _, _ = validate_checkpoint_runtime(parent_root.parent)
    if parent_checkpoint["experiment_digest"] != experiment["experiment_digest"]:
        raise CapabilityImprovementError(
            "parent checkpoint belongs to another experiment"
        )
    access_policy_path = (
        root
        / experiment["frozen_retrospective_transfer_panel"]["access_policy"]["path"]
    )
    packet_path = work_root / "packet.json"
    proposal_path = work_root / "proposal.json"
    candidate_root = work_root / "proposer-workspace" / "candidates"
    decision_path = work_root / "decision.json"
    critic_workspace = (
        critic_workspace_root.expanduser().resolve()
        if critic_workspace_root is not None
        else work_root / "critic-workspace"
    )
    revision_workspace = (
        revision_workspace_root.expanduser().resolve()
        if revision_workspace_root is not None
        else work_root / "revision-workspace"
    )
    revised_proposal_path = work_root / "proposal-r1.json"
    revised_candidate_root = revision_workspace / "candidates"
    revised_decision_path = work_root / "decision-r1.json"
    revised_critic_workspace = (
        revised_critic_workspace_root.expanduser().resolve()
        if revised_critic_workspace_root is not None
        else work_root / "critic-workspace-r1"
    )

    if not proposal_path.is_file():
        raise CapabilityImprovementError(
            f"compiled cumulative proposal is missing: {proposal_path}; "
            "run libstruct-learn-capability first"
        )
    proposal = validate_capability_proposal(
        experiment_manifest=experiment,
        access_policy_path=access_policy_path,
        proposal_path=proposal_path,
        candidate_root=candidate_root,
        parent_pack_root=parent_root,
        packet_path=packet_path,
    )
    if proposal["branch"] != ACTIVE_BRANCH:
        raise CapabilityImprovementError(
            "the completion command accepts only cumulative proposals"
        )

    announce("critic: validating or staging an independent review workspace")
    workspace_manifest = _prepare_or_validate_critic_workspace(
        workspace=critic_workspace,
        experiment=experiment,
        packet_path=packet_path,
        proposal_path=proposal_path,
        candidate_root=candidate_root,
        parent_pack_root=parent_root,
        access_policy_path=access_policy_path,
    )
    decision, critic_run = _prepare_or_validate_decision(
        proposal_path=proposal_path,
        decision_path=decision_path,
        workspace=critic_workspace,
        workspace_manifest=workspace_manifest,
        experiment=experiment,
        codex_executable=codex_executable,
        idle_timeout_seconds=idle_timeout_seconds,
        hard_timeout_seconds=hard_timeout_seconds,
        critic_runner=critic_runner,
        timestamp=clock,
        announce=announce,
    )
    summary_path, review_summary = write_or_validate_capability_review_summary(
        proposal_path=proposal_path,
        decision_path=decision_path,
    )
    for line in render_capability_review_summary(
        review_summary,
        summary_path=summary_path,
    ).splitlines():
        announce(f"review: {line}")

    active_proposal_path = proposal_path
    active_proposal = proposal
    active_candidate_root = candidate_root
    active_decision_path = decision_path
    active_decision = decision
    active_critic_workspace = critic_workspace
    active_critic_run = critic_run
    active_summary_path = summary_path
    active_review_summary = review_summary
    prior_proposal_path: Path | None = None
    revision_request_decision_path: Path | None = None
    revision_result: dict[str, Any] = {}

    if decision["review_state"] == "revision_requested":
        announce("revision: running the single bounded cumulative revision")
        revision_manifest = _prepare_or_validate_revision_workspace(
            workspace=revision_workspace,
            experiment=experiment,
            packet_path=packet_path,
            proposal_path=proposal_path,
            candidate_root=candidate_root,
            decision_path=decision_path,
            parent_pack_root=parent_root,
            access_policy_path=access_policy_path,
        )
        revised_proposal, revision_run = _prepare_or_validate_revised_proposal(
            proposal_path=revised_proposal_path,
            workspace=revision_workspace,
            workspace_manifest=revision_manifest,
            experiment=experiment,
            access_policy_path=access_policy_path,
            packet_path=packet_path,
            parent_pack_root=parent_root,
            candidate_root=revised_candidate_root,
            prior_proposal_path=proposal_path,
            revision_decision_path=decision_path,
            codex_executable=codex_executable,
            idle_timeout_seconds=idle_timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
            revision_runner=revision_runner,
            announce=announce,
        )
        announce("critic: staging a fresh review of the revised proposal")
        revised_workspace_manifest = _prepare_or_validate_critic_workspace(
            workspace=revised_critic_workspace,
            experiment=experiment,
            packet_path=packet_path,
            proposal_path=revised_proposal_path,
            candidate_root=revised_candidate_root,
            parent_pack_root=parent_root,
            access_policy_path=access_policy_path,
            prior_proposal_path=proposal_path,
            revision_decision_path=decision_path,
        )
        revised_decision, revised_critic_run = _prepare_or_validate_decision(
            proposal_path=revised_proposal_path,
            decision_path=revised_decision_path,
            workspace=revised_critic_workspace,
            workspace_manifest=revised_workspace_manifest,
            experiment=experiment,
            codex_executable=codex_executable,
            idle_timeout_seconds=idle_timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
            critic_runner=critic_runner,
            timestamp=clock,
            announce=announce,
        )
        revised_summary_path, revised_review_summary = (
            write_or_validate_capability_review_summary(
                proposal_path=revised_proposal_path,
                decision_path=revised_decision_path,
            )
        )
        for line in render_capability_review_summary(
            revised_review_summary,
            summary_path=revised_summary_path,
        ).splitlines():
            announce(f"review: {line}")

        prior_proposal_path = proposal_path
        revision_request_decision_path = decision_path
        active_proposal_path = revised_proposal_path
        active_proposal = revised_proposal
        active_candidate_root = revised_candidate_root
        active_decision_path = revised_decision_path
        active_decision = revised_decision
        active_critic_workspace = revised_critic_workspace
        active_critic_run = revised_critic_run
        active_summary_path = revised_summary_path
        active_review_summary = revised_review_summary
        revision_result = {
            "prior_proposal_path": proposal_path.as_posix(),
            "prior_proposal_digest": proposal["proposal_digest"],
            "revision_request_decision_path": decision_path.as_posix(),
            "revision_request_decision_digest": decision["decision_digest"],
            "initial_critic_workspace_path": critic_workspace.as_posix(),
            "initial_critic_run": _run_result(critic_run),
            "initial_review_summary_path": summary_path.as_posix(),
            "initial_review_summary_digest": review_summary["summary_digest"],
            "initial_review_counts": review_summary["counts"],
            "revision_workspace_path": revision_workspace.as_posix(),
            "revision_run": _run_result(revision_run),
        }

    if active_decision["review_state"] != "final":
        raise CapabilityImprovementError(
            "independent decision is not complete after the permitted revision: "
            f"{active_decision['review_state']}"
        )
    base_result = {
        "batch_id": batch_id,
        "branch": ACTIVE_BRANCH,
        "protocol_count": protocol_count,
        "protocol_ids": list(batch["protocol_ids"]),
        "revision_round": active_proposal["revision_round"],
        "proposal_path": active_proposal_path.as_posix(),
        "proposal_digest": active_proposal["proposal_digest"],
        "critic_workspace_path": active_critic_workspace.as_posix(),
        "decision_path": active_decision_path.as_posix(),
        "decision_digest": active_decision["decision_digest"],
        "review_state": active_decision["review_state"],
        "review_summary_path": active_summary_path.as_posix(),
        "review_summary_digest": active_review_summary["summary_digest"],
        "review_counts": active_review_summary["counts"],
        "critic_run": _run_result(active_critic_run),
        **revision_result,
    }
    if not authorize_apply:
        announce("decision: ready; deterministic application was not authorized")
        result = {
            **base_result,
            "status": "decision_ready",
            "application_path": None,
            "checkpoint_path": None,
            "runtime_path": None,
            "next_action": (
                "rerun this command with --authorize-apply to apply and freeze"
            ),
        }
        for line in render_completion_summary(
            result,
            active_review_summary,
        ).splitlines():
            announce(f"outcome: {line}")
        return result

    leakage_path = work_root / "leakage-policy.json"
    leakage = _prepare_or_validate_leakage_policy(
        path=leakage_path,
        experiment=experiment,
        groundtruth_root=groundtruth_root,
        batch_id=batch_id,
    )
    application_dir = work_root / "application"
    announce("application: applying accepted bytes and running sandboxed checks")
    application = _prepare_or_validate_application(
        experiment_root=root,
        path=application_dir,
        experiment=experiment,
        access_policy_path=access_policy_path,
        packet_path=packet_path,
        proposal_path=active_proposal_path,
        decision_path=active_decision_path,
        candidate_root=active_candidate_root,
        parent_pack_root=parent_root,
        prior_proposal_path=prior_proposal_path,
        revision_decision_path=revision_request_decision_path,
        leakage_policy=leakage,
        created_at=clock(),
        synthetic_runner=(
            synthetic_runner
            if synthetic_runner is not None
            else lambda pack: run_pack_synthetic_suite_docker(pack, image=docker_image)
        ),
    )
    application_path = application_dir / "application.json"
    if application["status"] == "no_op_validation_failed":
        announce("application: validation failed; checkpoint was not frozen")
        result = {
            **base_result,
            "status": "application_validation_failed",
            "application_path": application_path.as_posix(),
            "application_digest": application["application_digest"],
            "checkpoint_path": None,
            "runtime_path": None,
            "validation": application["validation"],
            "next_action": "inspect application validation issues",
        }
        for line in render_completion_summary(
            result,
            active_review_summary,
        ).splitlines():
            announce(f"outcome: {line}")
        return result

    checkpoint_label = checkpoint_id(protocol_count)
    checkpoint_dir = root / "checkpoints" / checkpoint_label
    announce(f"checkpoint: freezing {checkpoint_label}")
    checkpoint = _prepare_or_validate_checkpoint(
        experiment_root=root,
        path=checkpoint_dir,
        experiment=experiment,
        batch_id=batch_id,
        protocol_count=protocol_count,
        proposal_path=active_proposal_path,
        decision_path=active_decision_path,
        application_dir=application_dir,
        created_at=clock(),
        exemplar_max_results=exemplar_max_results,
    )
    announce(f"checkpoint: {checkpoint_label} is ready")
    result = {
        **base_result,
        "status": "checkpoint_ready",
        "application_path": application_path.as_posix(),
        "application_digest": application["application_digest"],
        "application_status": application["status"],
        "validation": application["validation"],
        "checkpoint_path": (checkpoint_dir / "checkpoint.json").as_posix(),
        "runtime_path": (checkpoint_dir / "runtime.json").as_posix(),
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "exemplar_max_results": exemplar_max_results,
        "pack_path": (checkpoint_dir / "pack").as_posix(),
        "pack_digest": checkpoint["pack_digest"],
        "next_action": f"use checkpoint {checkpoint_label} for the next batch",
    }
    for line in render_completion_summary(
        result,
        active_review_summary,
    ).splitlines():
        announce(f"outcome: {line}")
    return result


@guard_experiment_mutation("human capability completion")
def run_human_completion(
    *,
    experiment_root: Path,
    batch_id: str,
    groundtruth_root: Path,
    reviewer_id: str,
    authorize_apply: bool = False,
    exemplar_max_results: int = 3,
    manual_revision: bool = False,
    manual_revision_ready: bool = False,
    parent_pack_root: Path | None = None,
    round_root: Path | None = None,
    review_workspace_root: Path | None = None,
    revision_workspace_root: Path | None = None,
    revised_review_workspace_root: Path | None = None,
    codex_executable: str = "codex",
    idle_timeout_seconds: float = 300.0,
    hard_timeout_seconds: float = 7200.0,
    docker_image: str = "python:3.13-slim",
    revision_runner: LocalCodexRunner | None = None,
    synthetic_runner: SyntheticRunner | None = None,
    timestamp: TimestampProvider | None = None,
    progress: ProgressCallback | None = None,
    input_function: InputFunction = input,
    output: TextIO = sys.stdout,
) -> dict[str, Any]:
    """Run resumable human review on the one cumulative lineage.

    A modify decision either starts the same bounded Codex revision used by the
    independent path or stages a human-editable seeded workspace. Manual bytes
    are compiled only after an explicit ``manual_revision_ready`` resume gate,
    then receive a fresh exact-byte human review before application.
    """

    if idle_timeout_seconds <= 0 or hard_timeout_seconds <= 0:
        raise CapabilityImprovementError("Codex timeouts must be positive")
    if hard_timeout_seconds <= idle_timeout_seconds:
        raise CapabilityImprovementError(
            "hard timeout must be greater than the idle-after-draft timeout"
        )
    announce = progress or (lambda _message: None)
    clock = timestamp or _utc_now
    root = experiment_root.expanduser().resolve()
    assert_capability_modification_open(root)
    experiment = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    batch_index, batch = _batch(experiment, batch_id)
    protocol_count = (batch_index + 1) * 5
    work_root = (
        round_root.expanduser().resolve()
        if round_root is not None
        else root / "rounds" / batch_id / ACTIVE_BRANCH
    )
    expected_parent_root = _default_parent_pack(root, experiment, batch_index)
    parent_root = (
        parent_pack_root.expanduser().resolve()
        if parent_pack_root is not None
        else expected_parent_root
    )
    if parent_root != expected_parent_root.expanduser().resolve():
        raise CapabilityImprovementError(
            f"parent pack must be the canonical cumulative checkpoint: {expected_parent_root}"
        )
    parent_checkpoint, _, _ = validate_checkpoint_runtime(parent_root.parent)
    if parent_checkpoint["experiment_digest"] != experiment["experiment_digest"]:
        raise CapabilityImprovementError(
            "parent checkpoint belongs to another experiment"
        )
    access_policy_path = (
        root
        / experiment["frozen_retrospective_transfer_panel"]["access_policy"]["path"]
    )
    packet_path = work_root / "packet.json"
    proposal_path = work_root / "proposal.json"
    candidate_root = work_root / "proposer-workspace" / "candidates"
    decision_path = work_root / "decision.json"
    review_workspace = (
        review_workspace_root.expanduser().resolve()
        if review_workspace_root is not None
        else work_root / "review-workspace"
    )
    revision_workspace = (
        revision_workspace_root.expanduser().resolve()
        if revision_workspace_root is not None
        else work_root / "revision-workspace"
    )
    revised_proposal_path = work_root / "proposal-r1.json"
    revised_candidate_root = revision_workspace / "candidates"
    revised_decision_path = work_root / "decision-r1.json"
    manual_revision_marker = work_root / "manual-revision.json"
    revised_review_workspace = (
        revised_review_workspace_root.expanduser().resolve()
        if revised_review_workspace_root is not None
        else work_root / "review-workspace-r1"
    )

    if not proposal_path.is_file():
        raise CapabilityImprovementError(
            f"compiled cumulative proposal is missing: {proposal_path}; "
            "run libstruct-learn-capability first"
        )
    proposal = validate_capability_proposal(
        experiment_manifest=experiment,
        access_policy_path=access_policy_path,
        proposal_path=proposal_path,
        candidate_root=candidate_root,
        parent_pack_root=parent_root,
        packet_path=packet_path,
    )
    if proposal["branch"] != ACTIVE_BRANCH:
        raise CapabilityImprovementError("human review requires a cumulative proposal")

    announce("review: validating or staging the human review workspace")
    _prepare_or_validate_human_review_workspace(
        workspace=review_workspace,
        experiment=experiment,
        packet_path=packet_path,
        proposal_path=proposal_path,
        candidate_root=candidate_root,
        parent_pack_root=parent_root,
        access_policy_path=access_policy_path,
    )
    decision = run_human_review_console(
        proposal_path=review_workspace / "inputs" / "review" / "proposal.json",
        decision_path=decision_path,
        reviewer_id=reviewer_id,
        parent_pack_root=review_workspace / "inputs" / "capability_pack",
        candidate_root=review_workspace / "inputs" / "review" / "candidates",
        started_at=clock(),
        completed_at=clock(),
        input_function=input_function,
        output=output,
        experiment_root=root,
    )
    if decision["review_state"] == "in_progress":
        return _pending_human_result(
            batch_id=batch_id,
            protocol_count=protocol_count,
            batch=batch,
            proposal=proposal,
            proposal_path=proposal_path,
            decision=decision,
            decision_path=decision_path,
            status="review_in_progress",
            next_action="rerun this command to resume the human review",
        )

    summary_path, initial_summary = write_or_validate_capability_review_summary(
        proposal_path=proposal_path,
        decision_path=decision_path,
    )
    active_proposal = proposal
    active_proposal_path = proposal_path
    active_candidate_root = candidate_root
    active_decision = decision
    active_decision_path = decision_path
    active_summary = initial_summary
    active_summary_path = summary_path
    prior_proposal_path: Path | None = None
    revision_request_decision_path: Path | None = None
    revision_result: dict[str, Any] = {}

    if decision["review_state"] == "revision_requested":
        revision_manifest = _prepare_or_validate_revision_workspace(
            workspace=revision_workspace,
            experiment=experiment,
            packet_path=packet_path,
            proposal_path=proposal_path,
            candidate_root=candidate_root,
            decision_path=decision_path,
            parent_pack_root=parent_root,
            access_policy_path=access_policy_path,
        )
        _seed_revision_candidates(revision_workspace)
        manual_mode = manual_revision_marker.is_file()
        if manual_revision_ready and not manual_mode:
            raise CapabilityImprovementError(
                "--manual-revision-ready requires a prior --manual-revision "
                "invocation that seeded and pinned the revision workspace"
            )
        if manual_revision and not manual_revision_ready:
            _prepare_or_validate_manual_revision_marker(
                path=manual_revision_marker,
                proposal=proposal,
                decision=decision,
                reviewer_id=reviewer_id,
                workspace=revision_workspace,
            )
            manual_mode = True
        if (
            manual_mode
            and not manual_revision_ready
            and not revised_proposal_path.is_file()
        ):
            announce("revision: manual workspace seeded; waiting for ready gate")
            return {
                **_pending_human_result(
                    batch_id=batch_id,
                    protocol_count=protocol_count,
                    batch=batch,
                    proposal=proposal,
                    proposal_path=proposal_path,
                    decision=decision,
                    decision_path=decision_path,
                    status="manual_revision_pending",
                    next_action=(
                        "edit only the seeded revision workspace, then rerun with "
                        "--manual-revision-ready"
                    ),
                ),
                "revision_workspace_path": revision_workspace.as_posix(),
                "manual_revision_draft_path": (
                    revision_workspace / "outputs" / "proposal_draft.json"
                ).as_posix(),
            }
        if manual_mode:
            _prepare_or_validate_manual_revision_marker(
                path=manual_revision_marker,
                proposal=proposal,
                decision=decision,
                reviewer_id=reviewer_id,
                workspace=revision_workspace,
                create=False,
            )
            revised_proposal = _compile_manual_revision(
                proposal_path=revised_proposal_path,
                workspace=revision_workspace,
                experiment=experiment,
                access_policy_path=access_policy_path,
                packet_path=packet_path,
                parent_pack_root=parent_root,
                candidate_root=revised_candidate_root,
                prior_proposal_path=proposal_path,
                revision_decision_path=decision_path,
                reviewer_id=reviewer_id,
                ready_at=clock(),
            )
            revision_run = None
        else:
            revised_proposal, revision_run = _prepare_or_validate_revised_proposal(
                proposal_path=revised_proposal_path,
                workspace=revision_workspace,
                workspace_manifest=revision_manifest,
                experiment=experiment,
                access_policy_path=access_policy_path,
                packet_path=packet_path,
                parent_pack_root=parent_root,
                candidate_root=revised_candidate_root,
                prior_proposal_path=proposal_path,
                revision_decision_path=decision_path,
                codex_executable=codex_executable,
                idle_timeout_seconds=idle_timeout_seconds,
                hard_timeout_seconds=hard_timeout_seconds,
                revision_runner=revision_runner,
                announce=announce,
            )
        _prepare_or_validate_human_review_workspace(
            workspace=revised_review_workspace,
            experiment=experiment,
            packet_path=packet_path,
            proposal_path=revised_proposal_path,
            candidate_root=revised_candidate_root,
            parent_pack_root=parent_root,
            access_policy_path=access_policy_path,
            prior_proposal_path=proposal_path,
            revision_decision_path=decision_path,
        )
        revised_decision = run_human_review_console(
            proposal_path=(
                revised_review_workspace / "inputs" / "review" / "proposal.json"
            ),
            decision_path=revised_decision_path,
            reviewer_id=reviewer_id,
            parent_pack_root=(revised_review_workspace / "inputs" / "capability_pack"),
            candidate_root=(
                revised_review_workspace / "inputs" / "review" / "candidates"
            ),
            started_at=clock(),
            completed_at=clock(),
            input_function=input_function,
            output=output,
            experiment_root=root,
        )
        if revised_decision["review_state"] == "in_progress":
            return {
                **_pending_human_result(
                    batch_id=batch_id,
                    protocol_count=protocol_count,
                    batch=batch,
                    proposal=revised_proposal,
                    proposal_path=revised_proposal_path,
                    decision=revised_decision,
                    decision_path=revised_decision_path,
                    status="revised_review_in_progress",
                    next_action="rerun this command to resume final exact-byte review",
                ),
                "revision_workspace_path": revision_workspace.as_posix(),
            }
        revised_summary_path, revised_summary = (
            write_or_validate_capability_review_summary(
                proposal_path=revised_proposal_path,
                decision_path=revised_decision_path,
            )
        )
        prior_proposal_path = proposal_path
        revision_request_decision_path = decision_path
        active_proposal = revised_proposal
        active_proposal_path = revised_proposal_path
        active_candidate_root = revised_candidate_root
        active_decision = revised_decision
        active_decision_path = revised_decision_path
        active_summary = revised_summary
        active_summary_path = revised_summary_path
        revision_result = {
            "initial_review_summary_path": summary_path.as_posix(),
            "initial_review_summary_digest": initial_summary["summary_digest"],
            "initial_review_counts": initial_summary["counts"],
            "revision_workspace_path": revision_workspace.as_posix(),
            "revision_mode": "manual" if manual_mode else "agent_assisted",
            "revision_run": _run_result(revision_run),
        }

    if active_decision["review_state"] != "final":
        raise CapabilityImprovementError(
            "human decision is not final after the permitted revision"
        )
    base_result = {
        "batch_id": batch_id,
        "branch": ACTIVE_BRANCH,
        "protocol_count": protocol_count,
        "protocol_ids": list(batch["protocol_ids"]),
        "revision_round": active_proposal["revision_round"],
        "proposal_path": active_proposal_path.as_posix(),
        "proposal_digest": active_proposal["proposal_digest"],
        "decision_path": active_decision_path.as_posix(),
        "decision_digest": active_decision["decision_digest"],
        "review_state": "final",
        "review_summary_path": active_summary_path.as_posix(),
        "review_summary_digest": active_summary["summary_digest"],
        "review_counts": active_summary["counts"],
        **revision_result,
    }
    if not authorize_apply:
        result = {
            **base_result,
            "status": "decision_ready",
            "application_path": None,
            "checkpoint_path": None,
            "runtime_path": None,
            "next_action": (
                "rerun this command with --authorize-apply to apply and freeze"
            ),
        }
        for line in render_completion_summary(result, active_summary).splitlines():
            announce(f"outcome: {line}")
        return result

    leakage_path = work_root / "leakage-policy.json"
    leakage = _prepare_or_validate_leakage_policy(
        path=leakage_path,
        experiment=experiment,
        groundtruth_root=groundtruth_root,
        batch_id=batch_id,
    )
    application_dir = work_root / "application"
    application = _prepare_or_validate_application(
        experiment_root=root,
        path=application_dir,
        experiment=experiment,
        access_policy_path=access_policy_path,
        packet_path=packet_path,
        proposal_path=active_proposal_path,
        decision_path=active_decision_path,
        candidate_root=active_candidate_root,
        parent_pack_root=parent_root,
        prior_proposal_path=prior_proposal_path,
        revision_decision_path=revision_request_decision_path,
        leakage_policy=leakage,
        created_at=clock(),
        synthetic_runner=(
            synthetic_runner
            if synthetic_runner is not None
            else lambda pack: run_pack_synthetic_suite_docker(pack, image=docker_image)
        ),
    )
    application_path = application_dir / "application.json"
    if application["status"] == "no_op_validation_failed":
        result = {
            **base_result,
            "status": "application_validation_failed",
            "application_path": application_path.as_posix(),
            "validation": application["validation"],
            "checkpoint_path": None,
            "runtime_path": None,
            "next_action": "inspect application validation issues",
        }
        for line in render_completion_summary(result, active_summary).splitlines():
            announce(f"outcome: {line}")
        return result
    checkpoint_label = checkpoint_id(protocol_count)
    checkpoint_dir = root / "checkpoints" / checkpoint_label
    checkpoint = _prepare_or_validate_checkpoint(
        experiment_root=root,
        path=checkpoint_dir,
        experiment=experiment,
        batch_id=batch_id,
        protocol_count=protocol_count,
        proposal_path=active_proposal_path,
        decision_path=active_decision_path,
        application_dir=application_dir,
        created_at=clock(),
        exemplar_max_results=exemplar_max_results,
        branch=ACTIVE_BRANCH,
    )
    result = {
        **base_result,
        "status": "checkpoint_ready",
        "application_path": application_path.as_posix(),
        "application_digest": application["application_digest"],
        "application_status": application["status"],
        "validation": application["validation"],
        "checkpoint_path": (checkpoint_dir / "checkpoint.json").as_posix(),
        "runtime_path": (checkpoint_dir / "runtime.json").as_posix(),
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "exemplar_max_results": exemplar_max_results,
        "pack_path": (checkpoint_dir / "pack").as_posix(),
        "pack_digest": checkpoint["pack_digest"],
        "next_action": f"use checkpoint {checkpoint_label} for the next batch",
    }
    for line in render_completion_summary(result, active_summary).splitlines():
        announce(f"outcome: {line}")
    return result


def _prepare_or_validate_revision_workspace(
    *,
    workspace: Path,
    experiment: Mapping[str, Any],
    packet_path: Path,
    proposal_path: Path,
    candidate_root: Path,
    decision_path: Path,
    parent_pack_root: Path,
    access_policy_path: Path,
) -> dict[str, Any]:
    if not workspace.exists():
        return prepare_isolated_worker_workspace(
            experiment_manifest=experiment,
            packet_path=packet_path,
            parent_pack_root=parent_pack_root,
            access_policy_path=access_policy_path,
            output_root=workspace,
            mode="revision_worker",
            proposal_path=proposal_path,
            candidate_root=candidate_root,
            decision_path=decision_path,
        )
    manifest = validate_isolated_worker_workspace(workspace)
    expected = {
        "mode": "revision_worker",
        "experiment_digest": experiment["experiment_digest"],
        "review_materials_staged": True,
    }
    for key, value in expected.items():
        if manifest[key] != value:
            raise CapabilityImprovementError(
                f"existing revision workspace has stale {key}: {workspace}"
            )
    staged_review = workspace / "inputs" / "review"
    for staged_name, source, label in (
        ("proposal.json", proposal_path, "proposal"),
        ("decision.json", decision_path, "revision decision"),
    ):
        if sha256_file(staged_review / staged_name) != sha256_file(source):
            raise CapabilityImprovementError(
                f"existing revision workspace contains a stale {label}: {workspace}"
            )
    if not trees_byte_identical(staged_review / "candidates", candidate_root):
        raise CapabilityImprovementError(
            f"existing revision workspace contains stale candidate files: {workspace}"
        )
    return manifest


def _prepare_or_validate_revised_proposal(
    *,
    proposal_path: Path,
    workspace: Path,
    workspace_manifest: Mapping[str, Any],
    experiment: Mapping[str, Any],
    access_policy_path: Path,
    packet_path: Path,
    parent_pack_root: Path,
    candidate_root: Path,
    prior_proposal_path: Path,
    revision_decision_path: Path,
    codex_executable: str,
    idle_timeout_seconds: float,
    hard_timeout_seconds: float,
    revision_runner: LocalCodexRunner | None,
    announce: ProgressCallback,
) -> tuple[dict[str, Any], LocalCodexRunResult | None]:
    if proposal_path.is_file():
        proposal = validate_capability_proposal(
            experiment_manifest=experiment,
            access_policy_path=access_policy_path,
            proposal_path=proposal_path,
            candidate_root=candidate_root,
            parent_pack_root=parent_pack_root,
            packet_path=packet_path,
            prior_proposal_path=prior_proposal_path,
            revision_decision_path=revision_decision_path,
        )
        announce("revision: existing revised proposal reused")
        return proposal, None
    if proposal_path.exists():
        raise CapabilityImprovementError(
            f"revised proposal path is not a regular file: {proposal_path}"
        )

    _seed_revision_candidates(workspace)
    contract = workspace_manifest["agent_contract"]
    prompt_path = workspace / str(contract["prompt_path"])
    schema_path = workspace / str(contract["output_schema_path"])
    draft_path = workspace / str(contract["draft_output_path"])
    event_path = workspace / str(contract["event_log_path"])
    stderr_path = workspace / "outputs" / "revision.stderr.log"
    run_result: LocalCodexRunResult | None = None
    if not draft_path.is_file():
        announce(
            "revision: starting one read-isolated container Codex revision "
            "with local auth"
        )
        request = LocalCodexRunRequest(
            workspace=workspace,
            prompt_path=prompt_path,
            output_schema_path=schema_path,
            draft_output_path=draft_path,
            event_log_path=event_path,
            stderr_log_path=stderr_path,
            model=experiment["anchor"]["model"],
            version=experiment["anchor"]["agent_version"],
            reasoning_effort=experiment["anchor"]["reasoning_effort"],
            codex_executable=codex_executable,
            idle_timeout_seconds=idle_timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
        )
        runner = revision_runner or run_native_codex
        run_result = runner(request)
        if run_result.completion_reason == "hard_timeout":
            raise CapabilityImprovementError(
                "local revision reached the hard timeout; inspect "
                f"{event_path} and {stderr_path}"
            )
        if (
            run_result.returncode != 0
            and run_result.completion_reason != "idle_after_draft"
        ):
            raise CapabilityImprovementError(
                f"local revision exited with status {run_result.returncode}; "
                f"inspect {event_path} and {stderr_path}"
            )
    else:
        announce("revision: existing draft found; resuming at compilation")
    if not draft_path.is_file() or not event_path.is_file():
        raise CapabilityImprovementError(
            f"revision did not produce both a draft and transcript: {workspace}"
        )
    proposal = compile_capability_proposal_draft(
        experiment_manifest=experiment,
        access_policy_path=access_policy_path,
        packet_path=packet_path,
        parent_pack_root=parent_pack_root,
        candidate_root=candidate_root,
        draft_path=draft_path,
        transcript_path=event_path,
        output_path=proposal_path,
        prior_proposal_path=prior_proposal_path,
        revision_decision_path=revision_decision_path,
    )
    return proposal, run_result


def _seed_revision_candidates(workspace: Path) -> None:
    review_root = workspace / "inputs" / "review"
    proposal, decision = validate_capability_decision(
        proposal_path=review_root / "proposal.json",
        decision_path=review_root / "decision.json",
        require_final=False,
    )
    if decision["review_state"] != "revision_requested":
        raise CapabilityImprovementError(
            "revision candidate seeding requires a revision request"
        )
    dispositions = {
        item["change_id"]: item["disposition"] for item in decision["change_decisions"]
    }
    source_root = review_root / "candidates"
    destination_root = workspace / "candidates"
    for change in proposal["change_units"]:
        disposition = dispositions[change["change_id"]]
        if disposition not in {"accept", "modify"}:
            continue
        for mutation in change["mutations"]:
            if mutation["operation"] == "remove":
                continue
            relative = Path(mutation["path"])
            source = source_root / relative
            destination = destination_root / relative
            if (
                not source.is_file()
                or sha256_file(source) != mutation["candidate_sha256"]
            ):
                raise CapabilityImprovementError(
                    f"revision source candidate is stale: {mutation['path']}"
                )
            if destination.exists():
                if destination.is_symlink() or not destination.is_file():
                    raise CapabilityImprovementError(
                        f"revision candidate path is invalid: {destination}"
                    )
                if disposition == "accept" and sha256_file(destination) != sha256_file(
                    source
                ):
                    raise CapabilityImprovementError(
                        "revision changed an accepted candidate before compilation: "
                        f"{change['change_id']}"
                    )
                destination.chmod(0o644)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            destination.chmod(0o644)


def _prepare_or_validate_critic_workspace(
    *,
    workspace: Path,
    experiment: Mapping[str, Any],
    packet_path: Path,
    proposal_path: Path,
    candidate_root: Path,
    parent_pack_root: Path,
    access_policy_path: Path,
    prior_proposal_path: Path | None = None,
    revision_decision_path: Path | None = None,
) -> dict[str, Any]:
    if (prior_proposal_path is None) != (revision_decision_path is None):
        raise CapabilityImprovementError(
            "revised critic requires both prior proposal and revision decision"
        )
    if not workspace.exists():
        return prepare_isolated_worker_workspace(
            experiment_manifest=experiment,
            packet_path=packet_path,
            parent_pack_root=parent_pack_root,
            access_policy_path=access_policy_path,
            output_root=workspace,
            mode="independent_critic",
            proposal_path=proposal_path,
            candidate_root=candidate_root,
            prior_proposal_path=prior_proposal_path,
            revision_decision_path=revision_decision_path,
        )
    manifest = validate_isolated_worker_workspace(workspace)
    expected = {
        "mode": "independent_critic",
        "experiment_digest": experiment["experiment_digest"],
        "review_materials_staged": True,
    }
    for key, value in expected.items():
        if manifest[key] != value:
            raise CapabilityImprovementError(
                f"existing critic workspace has stale {key}: {workspace}"
            )
    staged_review = workspace / "inputs" / "review"
    if sha256_file(staged_review / "proposal.json") != sha256_file(proposal_path):
        raise CapabilityImprovementError(
            f"existing critic workspace contains a stale proposal: {workspace}"
        )
    if not trees_byte_identical(staged_review / "candidates", candidate_root):
        raise CapabilityImprovementError(
            f"existing critic workspace contains stale candidate files: {workspace}"
        )
    if prior_proposal_path is not None and revision_decision_path is not None:
        for staged_name, source, label in (
            ("prior-proposal.json", prior_proposal_path, "prior proposal"),
            (
                "revision-decision.json",
                revision_decision_path,
                "revision decision",
            ),
        ):
            if sha256_file(staged_review / staged_name) != sha256_file(source):
                raise CapabilityImprovementError(
                    f"existing critic workspace contains a stale {label}: {workspace}"
                )
    return manifest


def _prepare_or_validate_decision(
    *,
    proposal_path: Path,
    decision_path: Path,
    workspace: Path,
    workspace_manifest: Mapping[str, Any],
    experiment: Mapping[str, Any],
    codex_executable: str,
    idle_timeout_seconds: float,
    hard_timeout_seconds: float,
    critic_runner: LocalCodexRunner | None,
    timestamp: TimestampProvider,
    announce: ProgressCallback,
) -> tuple[dict[str, Any], LocalCodexRunResult | None]:
    if decision_path.is_file():
        _, decision = validate_capability_decision(
            proposal_path=proposal_path,
            decision_path=decision_path,
            require_final=False,
        )
        announce("critic: existing decision reused")
        return decision, None
    if decision_path.exists():
        raise CapabilityImprovementError(
            f"decision path exists but is not a regular file: {decision_path}"
        )
    contract = workspace_manifest["agent_contract"]
    prompt_path = workspace / str(contract["prompt_path"])
    schema_path = workspace / str(contract["output_schema_path"])
    draft_path = workspace / str(contract["draft_output_path"])
    event_path = workspace / str(contract["event_log_path"])
    stderr_path = workspace / "outputs" / "critic.stderr.log"
    run_result: LocalCodexRunResult | None = None
    started_at = timestamp()
    if not draft_path.is_file():
        announce(
            "critic: starting one fresh read-isolated container Codex review "
            "with local auth"
        )
        request = LocalCodexRunRequest(
            workspace=workspace,
            prompt_path=prompt_path,
            output_schema_path=schema_path,
            draft_output_path=draft_path,
            event_log_path=event_path,
            stderr_log_path=stderr_path,
            model=experiment["anchor"]["model"],
            version=experiment["anchor"]["agent_version"],
            reasoning_effort=experiment["anchor"]["reasoning_effort"],
            codex_executable=codex_executable,
            idle_timeout_seconds=idle_timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
        )
        runner = critic_runner or run_native_codex
        run_result = runner(request)
        if run_result.completion_reason == "hard_timeout":
            raise CapabilityImprovementError(
                "local critic reached the hard timeout; inspect "
                f"{event_path} and {stderr_path}"
            )
        if (
            run_result.returncode != 0
            and run_result.completion_reason != "idle_after_draft"
        ):
            raise CapabilityImprovementError(
                f"local critic exited with status {run_result.returncode}; "
                f"inspect {event_path} and {stderr_path}"
            )
    else:
        announce("critic: existing draft found; resuming at compilation")
    if not draft_path.is_file() or not event_path.is_file():
        raise CapabilityImprovementError(
            f"critic did not produce both a draft and transcript: {workspace}"
        )
    decision = compile_independent_decision_draft(
        proposal_path=proposal_path,
        draft_path=draft_path,
        transcript_path=event_path,
        output_path=decision_path,
        started_at=started_at,
        completed_at=timestamp(),
    )
    return decision, run_result


def _prepare_or_validate_leakage_policy(
    *,
    path: Path,
    experiment: Mapping[str, Any],
    groundtruth_root: Path,
    batch_id: str,
) -> dict[str, Any]:
    if path.is_file():
        policy = validate_cumulative_leakage_policy(
            path, experiment_manifest=experiment
        )
        if policy["through_batch"] != batch_id:
            raise CapabilityImprovementError(
                f"existing leakage policy is for {policy['through_batch']}, "
                f"not {batch_id}"
            )
        return policy
    if path.exists():
        raise CapabilityImprovementError(
            f"leakage-policy path exists but is not a regular file: {path}"
        )
    policy = build_cumulative_leakage_policy(
        experiment_manifest=experiment,
        private_groundtruth_root=groundtruth_root,
        through_batch=batch_id,
    )
    write_json_atomic(path, policy, mode=0o400)
    return policy


def _prepare_or_validate_application(
    *,
    experiment_root: Path,
    path: Path,
    experiment: Mapping[str, Any],
    access_policy_path: Path,
    packet_path: Path,
    proposal_path: Path,
    decision_path: Path,
    candidate_root: Path,
    parent_pack_root: Path,
    prior_proposal_path: Path | None,
    revision_decision_path: Path | None,
    leakage_policy: Mapping[str, Any],
    created_at: str,
    synthetic_runner: SyntheticRunner,
) -> dict[str, Any]:
    if not path.exists():
        return apply_capability_decision(
            experiment_root=experiment_root,
            experiment_manifest=experiment,
            access_policy_path=access_policy_path,
            proposal_path=proposal_path,
            decision_path=decision_path,
            candidate_root=candidate_root,
            parent_pack_root=parent_pack_root,
            packet_path=packet_path,
            prior_proposal_path=prior_proposal_path,
            revision_decision_path=revision_decision_path,
            output_dir=path,
            created_at=created_at,
            leakage_policy=leakage_policy,
            synthetic_runner=synthetic_runner,
        )
    application_path = path / "application.json"
    application = load_and_validate(
        application_path,
        schema_filename="capability_application.schema.json",
        digest_field="application_digest",
        label="capability application",
    )
    proposal = load_and_validate(
        proposal_path,
        schema_filename="capability_proposal.schema.json",
        digest_field="proposal_digest",
        label="capability proposal",
    )
    decision = load_and_validate(
        decision_path,
        schema_filename="capability_decision.schema.json",
        digest_field="decision_digest",
        label="capability decision",
    )
    parent = validate_capability_pack(parent_pack_root)
    expected = {
        "proposal_digest": proposal["proposal_digest"],
        "decision_digest": decision["decision_digest"],
        "parent_pack_digest": parent["pack_digest"],
    }
    for key, value in expected.items():
        if application[key] != value:
            raise CapabilityImprovementError(
                f"existing application has stale {key}: {application_path}"
            )
    output_pack = path / application["output_pack"]["path"]
    pack = validate_capability_pack(output_pack)
    if pack["pack_digest"] != application["output_pack"]["pack_digest"]:
        raise CapabilityImprovementError(
            f"existing application pack has a stale digest: {output_pack}"
        )
    return application


def _prepare_or_validate_checkpoint(
    *,
    experiment_root: Path,
    path: Path,
    experiment: Mapping[str, Any],
    batch_id: str,
    protocol_count: int,
    proposal_path: Path,
    decision_path: Path,
    application_dir: Path,
    created_at: str,
    exemplar_max_results: int = 3,
    branch: str = ACTIVE_BRANCH,
) -> dict[str, Any]:
    require_active_branch(branch)
    parent_checkpoint_id = parent_checkpoint(protocol_count)
    if not path.exists():
        return freeze_checkpoint(
            experiment_root=experiment_root,
            experiment_digest=experiment["experiment_digest"],
            branch=branch,
            batch_id=batch_id,
            protocol_count=protocol_count,
            parent_checkpoint_id=parent_checkpoint_id,
            proposal_path=proposal_path,
            decision_path=decision_path,
            application_dir=application_dir,
            output_dir=path,
            created_at=created_at,
            exemplar_max_results=exemplar_max_results,
        )
    checkpoint_path = path / "checkpoint.json"
    checkpoint, runtime, _ = validate_checkpoint_runtime(path)
    if checkpoint_exemplar_max_results(runtime) != exemplar_max_results:
        raise CapabilityImprovementError(
            "existing checkpoint has a different exemplar retrieval limit: "
            f"{checkpoint_path}"
        )
    expected = {
        "checkpoint_id": checkpoint_id(protocol_count),
        "experiment_digest": experiment["experiment_digest"],
        "branch": branch,
        "batch_id": batch_id,
        "protocol_count": protocol_count,
        "parent_checkpoint_id": parent_checkpoint_id,
        "proposal_sha256": sha256_file(proposal_path),
        "decision_sha256": sha256_file(decision_path),
        "application_sha256": sha256_file(application_dir / "application.json"),
    }
    for key, value in expected.items():
        if checkpoint[key] != value:
            raise CapabilityImprovementError(
                f"existing checkpoint has stale {key}: {checkpoint_path}"
            )
    return checkpoint


def _default_parent_pack(
    root: Path,
    experiment: Mapping[str, Any],
    batch_index: int,
    *,
    branch: str = ACTIVE_BRANCH,
) -> Path:
    require_active_branch(branch)
    prior_count = batch_index * 5
    label = checkpoint_id(prior_count)
    if prior_count == 0:
        return root / experiment["initial_pack"]["references"][label]
    return root / "checkpoints" / label / "pack"


def _prepare_or_validate_human_review_workspace(
    *,
    workspace: Path,
    experiment: Mapping[str, Any],
    packet_path: Path,
    proposal_path: Path,
    candidate_root: Path,
    parent_pack_root: Path,
    access_policy_path: Path,
    prior_proposal_path: Path | None = None,
    revision_decision_path: Path | None = None,
) -> dict[str, Any]:
    if not workspace.exists():
        return prepare_isolated_worker_workspace(
            experiment_manifest=experiment,
            packet_path=packet_path,
            parent_pack_root=parent_pack_root,
            access_policy_path=access_policy_path,
            output_root=workspace,
            mode="human_review_console",
            proposal_path=proposal_path,
            candidate_root=candidate_root,
            prior_proposal_path=prior_proposal_path,
            revision_decision_path=revision_decision_path,
        )
    manifest = validate_isolated_worker_workspace(workspace)
    for key, value in {
        "mode": "human_review_console",
        "experiment_digest": experiment["experiment_digest"],
        "review_materials_staged": True,
    }.items():
        if manifest[key] != value:
            raise CapabilityImprovementError(
                f"existing human review workspace has stale {key}: {workspace}"
            )
    review_root = workspace / "inputs" / "review"
    if sha256_file(review_root / "proposal.json") != sha256_file(proposal_path):
        raise CapabilityImprovementError(
            f"existing human review workspace contains a stale proposal: {workspace}"
        )
    if not trees_byte_identical(review_root / "candidates", candidate_root):
        raise CapabilityImprovementError(
            f"existing human review workspace contains stale candidates: {workspace}"
        )
    for staged_name, source, label in (
        ("prior-proposal.json", prior_proposal_path, "prior proposal"),
        (
            "revision-decision.json",
            revision_decision_path,
            "revision-request decision",
        ),
    ):
        if source is None:
            continue
        staged = review_root / staged_name
        if not staged.is_file() or sha256_file(staged) != sha256_file(source):
            raise CapabilityImprovementError(
                f"existing human review workspace contains a stale {label}: {workspace}"
            )
    return manifest


def _compile_manual_revision(
    *,
    proposal_path: Path,
    workspace: Path,
    experiment: Mapping[str, Any],
    access_policy_path: Path,
    packet_path: Path,
    parent_pack_root: Path,
    candidate_root: Path,
    prior_proposal_path: Path,
    revision_decision_path: Path,
    reviewer_id: str,
    ready_at: str,
) -> dict[str, Any]:
    if proposal_path.is_file():
        return validate_capability_proposal(
            experiment_manifest=experiment,
            access_policy_path=access_policy_path,
            proposal_path=proposal_path,
            candidate_root=candidate_root,
            parent_pack_root=parent_pack_root,
            packet_path=packet_path,
            prior_proposal_path=prior_proposal_path,
            revision_decision_path=revision_decision_path,
        )
    draft_path = workspace / "outputs" / "proposal_draft.json"
    if not draft_path.is_file():
        raise CapabilityImprovementError(
            "--manual-revision-ready requires outputs/proposal_draft.json in "
            f"the seeded workspace: {draft_path}"
        )
    transcript_path = workspace / "outputs" / "manual-revision-transcript.json"
    write_json_atomic(
        transcript_path,
        {
            "type": "libstruct.manual_revision_ready",
            "reviewer_id": reviewer_id,
            "ready_at": ready_at,
            "draft_sha256": sha256_file(draft_path),
        },
    )
    return compile_capability_proposal_draft(
        experiment_manifest=experiment,
        access_policy_path=access_policy_path,
        packet_path=packet_path,
        parent_pack_root=parent_pack_root,
        candidate_root=candidate_root,
        draft_path=draft_path,
        transcript_path=transcript_path,
        output_path=proposal_path,
        prior_proposal_path=prior_proposal_path,
        revision_decision_path=revision_decision_path,
    )


def _prepare_or_validate_manual_revision_marker(
    *,
    path: Path,
    proposal: Mapping[str, Any],
    decision: Mapping[str, Any],
    reviewer_id: str,
    workspace: Path,
    create: bool = True,
) -> dict[str, Any]:
    """Pin manual mode so a resume cannot silently switch revision authors."""

    expected = {
        "schema_version": "libstruct.libgen_manual_revision_mode.v1",
        "proposal_digest": proposal["proposal_digest"],
        "decision_digest": decision["decision_digest"],
        "reviewer_id": reviewer_id,
        "revision_workspace_path": workspace.resolve().as_posix(),
    }
    if path.is_file() and not path.is_symlink():
        try:
            observed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CapabilityImprovementError(
                f"cannot read the manual-revision marker: {error}"
            ) from error
        if observed != expected:
            raise CapabilityImprovementError(
                f"manual-revision marker has stale lineage: {path}"
            )
        return observed
    if path.exists():
        raise CapabilityImprovementError(
            f"manual-revision marker is not a regular file: {path}"
        )
    if not create:
        raise CapabilityImprovementError(f"manual-revision marker is missing: {path}")
    write_json_atomic(path, expected, mode=0o444)
    return expected


def _pending_human_result(
    *,
    batch_id: str,
    protocol_count: int,
    batch: Mapping[str, Any],
    proposal: Mapping[str, Any],
    proposal_path: Path,
    decision: Mapping[str, Any],
    decision_path: Path,
    status: str,
    next_action: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "batch_id": batch_id,
        "branch": ACTIVE_BRANCH,
        "protocol_count": protocol_count,
        "protocol_ids": list(batch["protocol_ids"]),
        "proposal_path": proposal_path.as_posix(),
        "proposal_digest": proposal["proposal_digest"],
        "decision_path": decision_path.as_posix(),
        "decision_digest": decision["decision_digest"],
        "review_state": decision["review_state"],
        "application_path": None,
        "checkpoint_path": None,
        "next_action": next_action,
    }


def _batch(
    experiment: Mapping[str, Any], batch_id: str
) -> tuple[int, Mapping[str, Any]]:
    matches = [
        (index, item)
        for index, item in enumerate(experiment["batches"])
        if item["batch_id"] == batch_id
    ]
    if len(matches) != 1:
        raise CapabilityImprovementError(f"unknown batch: {batch_id}")
    return matches[0]


def _run_result(value: LocalCodexRunResult | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "returncode": value.returncode,
        "completion_reason": value.completion_reason,
        "elapsed_seconds": value.elapsed_seconds,
    }


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
