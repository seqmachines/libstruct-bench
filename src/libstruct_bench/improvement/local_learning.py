from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from libstruct_bench.audit.artifacts import (
    AuditArtifactError,
    load_json_object,
    sha256_file,
    validate_document,
    write_json_atomic,
)

from .agent import compile_capability_proposal_draft
from .artifacts import (
    CapabilityImprovementError,
    improvement_schema_root,
    load_and_validate,
    validate_capability_pack,
)
from .experiment import validate_experiment_manifest
from .governance import (
    assert_capability_modification_open,
    validate_transfer_access_policy,
)
from .isolation import (
    prepare_isolated_worker_workspace,
    validate_isolated_worker_workspace,
)
from .mutation_lock import guard_experiment_mutation
from .lineage import checkpoint_id, require_active_branch
from .packets import build_batch_packet_from_frozen_runs
from .workflow import validate_capability_proposal, validate_checkpoint_runtime
from .worker_runtime import (
    prepare_isolated_codex_launch,
    run_compose_cleanup,
    validate_isolated_codex_outputs,
)


@dataclass(frozen=True)
class LocalCodexRunRequest:
    workspace: Path
    prompt_path: Path
    output_schema_path: Path
    draft_output_path: Path
    event_log_path: Path
    stderr_log_path: Path
    model: str
    version: str
    reasoning_effort: str
    codex_executable: str
    idle_timeout_seconds: float
    hard_timeout_seconds: float


@dataclass(frozen=True)
class LocalCodexRunResult:
    returncode: int
    completion_reason: str
    elapsed_seconds: float


class LocalCodexRunner(Protocol):
    def __call__(self, request: LocalCodexRunRequest) -> LocalCodexRunResult: ...


ProgressCallback = Callable[[str], None]


@guard_experiment_mutation("local capability learning")
def run_local_learning(
    *,
    experiment_root: Path,
    batch_id: str,
    branch: str,
    source_root: Path,
    groundtruth_root: Path,
    run_root: Path | None = None,
    c0_run_root: Path | None = None,
    parent_pack_root: Path | None = None,
    round_root: Path | None = None,
    workspace_root: Path | None = None,
    codex_executable: str = "codex",
    idle_timeout_seconds: float = 300.0,
    hard_timeout_seconds: float = 7200.0,
    agent_runner: LocalCodexRunner | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Build one five-protocol proposal with a resumable isolated Codex run.

    This is an operator convenience layer over the immutable packet, workspace,
    and proposal primitives. It does not approve, apply, or freeze a capability
    change.
    """

    require_active_branch(branch)
    if idle_timeout_seconds <= 0 or hard_timeout_seconds <= 0:
        raise CapabilityImprovementError("Codex timeouts must be positive")
    if hard_timeout_seconds <= idle_timeout_seconds:
        raise CapabilityImprovementError(
            "hard timeout must be greater than the idle-after-draft timeout"
        )
    announce = progress or (lambda _message: None)
    root = experiment_root.expanduser().resolve()
    assert_capability_modification_open(root)
    experiment_path = root / "design" / "experiment_manifest.json"
    try:
        experiment = validate_experiment_manifest(
            experiment_path,
            experiment_root=root,
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    batch = _batch(experiment, batch_id)
    if len(batch["protocol_ids"]) != 5:
        raise CapabilityImprovementError(
            f"{batch_id} must contain exactly five protocols"
        )

    access_policy_path = (
        root
        / experiment["frozen_retrospective_transfer_panel"]["access_policy"]["path"]
    )
    access_policy = validate_transfer_access_policy(access_policy_path)
    expected_parent_root = _default_parent_pack(root, experiment, batch_id, branch)
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
    parent = validate_capability_pack(parent_root)
    from .validation import (
        validate_required_validation_aggregate,
        validate_referenced_validation_access_policy,
    )

    validation_policy = validate_referenced_validation_access_policy(
        experiment_root=root,
        experiment_manifest=experiment,
    )
    validation_feedback = validate_required_validation_aggregate(
        experiment_root=root,
        experiment_digest=experiment["experiment_digest"],
        validation_access_policy=validation_policy,
        batch_id=batch_id,
        expected_pack_digest=parent["pack_digest"],
    )
    validation_feedback_path = (
        root
        / "validation"
        / "aggregates"
        / f"{validation_feedback['checkpoint_label']}.json"
    )
    work_root = (
        round_root.expanduser().resolve()
        if round_root is not None
        else root / "rounds" / batch_id / branch
    )
    work_root.mkdir(parents=True, exist_ok=True)
    packet_path = work_root / "packet.json"
    workspace = (
        workspace_root.expanduser().resolve()
        if workspace_root is not None
        else work_root / "proposer-workspace"
    )
    proposal_path = work_root / "proposal.json"

    packet_already_exists = packet_path.is_file()
    if run_root is None and not packet_already_exists:
        raise CapabilityImprovementError(
            "a new training packet requires an explicit frozen --run-root"
        )
    resolved_run_root = (
        run_root.expanduser().resolve() if run_root is not None else None
    )
    if (
        batch["phase"] == "prospective"
        and c0_run_root is None
        and not packet_already_exists
    ):
        raise CapabilityImprovementError(
            "a new prospective packet also requires --c0-run-root"
        )

    announce(f"packet: {batch_id} / {branch} / {len(batch['protocol_ids'])} protocols")
    packet = _prepare_or_validate_packet(
        packet_path=packet_path,
        experiment=experiment,
        batch_id=batch_id,
        branch=branch,
        parent_pack_digest=parent["pack_digest"],
        run_root=resolved_run_root,
        c0_run_root=c0_run_root,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        access_policy=access_policy,
    )

    announce("workspace: validating or staging allowlisted inputs")
    workspace_manifest = _prepare_or_validate_workspace(
        workspace=workspace,
        experiment=experiment,
        packet_path=packet_path,
        packet=packet,
        parent_pack_root=parent_root,
        access_policy_path=access_policy_path,
        validation_feedback_path=validation_feedback_path,
    )
    contract = workspace_manifest["agent_contract"]
    prompt_path = workspace / str(contract["prompt_path"])
    output_schema_path = workspace / str(contract["output_schema_path"])
    draft_path = workspace / str(contract["draft_output_path"])
    event_log_path = workspace / str(contract["event_log_path"])
    stderr_log_path = workspace / "outputs" / "proposal.stderr.log"
    candidate_root = workspace / "candidates"

    if proposal_path.is_file():
        proposal = validate_capability_proposal(
            experiment_manifest=experiment,
            access_policy_path=access_policy_path,
            proposal_path=proposal_path,
            candidate_root=candidate_root,
            parent_pack_root=parent_root,
            packet_path=packet_path,
        )
        announce("proposal: already complete; reused without rerunning Codex")
        return _learning_result(
            proposal=proposal,
            proposal_path=proposal_path,
            packet_path=packet_path,
            workspace=workspace,
            candidate_root=candidate_root,
            protocols=batch["protocol_ids"],
            branch=branch,
            batch_id=batch_id,
            run_result=None,
            bookkeeping_repairs=[],
        )

    run_result: LocalCodexRunResult | None = None
    if not draft_path.is_file():
        announce(
            "codex: starting one read-isolated container proposal run "
            "with local Codex auth"
        )
        request = LocalCodexRunRequest(
            workspace=workspace,
            prompt_path=prompt_path,
            output_schema_path=output_schema_path,
            draft_output_path=draft_path,
            event_log_path=event_log_path,
            stderr_log_path=stderr_log_path,
            model=experiment["anchor"]["model"],
            version=experiment["anchor"]["agent_version"],
            reasoning_effort=experiment["anchor"]["reasoning_effort"],
            codex_executable=codex_executable,
            idle_timeout_seconds=idle_timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
        )
        runner = agent_runner or run_native_codex
        run_result = runner(request)
        if run_result.completion_reason == "hard_timeout":
            raise CapabilityImprovementError(
                "isolated Codex worker reached the hard timeout; inspect "
                f"{event_log_path} and {stderr_log_path}"
            )
        if (
            run_result.returncode != 0
            and run_result.completion_reason != "idle_after_draft"
        ):
            raise CapabilityImprovementError(
                f"isolated Codex worker exited with status {run_result.returncode}; inspect "
                f"{event_log_path} and {stderr_log_path}"
            )
        if not draft_path.is_file():
            raise CapabilityImprovementError(
                "isolated Codex worker did not produce a proposal draft; inspect "
                f"{event_log_path} and {stderr_log_path}"
            )
    else:
        announce("codex: existing draft found; resuming at validation")

    if not event_log_path.is_file():
        raise CapabilityImprovementError(
            f"proposal event log is missing: {event_log_path}"
        )
    announce("proposal: validating the structured draft and candidate files")
    draft_for_compile, bookkeeping_repairs, transcript_path = _prepare_compilable_draft(
        draft_path=draft_path,
        event_log_path=event_log_path,
        parent_pack_root=parent_root,
        candidate_root=candidate_root,
    )
    try:
        proposal = compile_capability_proposal_draft(
            experiment_manifest=experiment,
            access_policy_path=access_policy_path,
            packet_path=packet_path,
            parent_pack_root=parent_root,
            candidate_root=candidate_root,
            draft_path=draft_for_compile,
            transcript_path=transcript_path,
            output_path=proposal_path,
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    announce(f"proposal: ready with {len(proposal['change_units'])} change unit(s)")
    return _learning_result(
        proposal=proposal,
        proposal_path=proposal_path,
        packet_path=packet_path,
        workspace=workspace,
        candidate_root=candidate_root,
        protocols=batch["protocol_ids"],
        branch=branch,
        batch_id=batch_id,
        run_result=run_result,
        bookkeeping_repairs=bookkeeping_repairs,
    )


def run_native_codex(request: LocalCodexRunRequest) -> LocalCodexRunResult:
    """Run Codex behind the external worker boundary with bounded timeouts.

    The historical public name is retained for API compatibility.  This no
    longer starts a host Codex subprocess: Docker Compose is validated before
    launch and there is deliberately no workspace-write fallback.
    """

    launch = prepare_isolated_codex_launch(request)
    request.event_log_path.parent.mkdir(parents=True, exist_ok=True)
    request.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    completion_reason = "exit"
    run_compose_cleanup(launch)
    with (
        request.event_log_path.open("ab") as event_handle,
        request.stderr_log_path.open("ab") as stderr_handle,
    ):
        try:
            process = subprocess.Popen(
                list(launch.command),
                stdin=subprocess.DEVNULL,
                stdout=event_handle,
                stderr=stderr_handle,
                env=dict(launch.environment),
                start_new_session=True,
            )
        except OSError as error:
            raise CapabilityImprovementError(
                f"cannot start the read-isolated Codex worker: {error}"
            ) from error
        try:
            last_signature = _output_signature(request)
            last_activity = time.monotonic()
            while process.poll() is None:
                now = time.monotonic()
                signature = _output_signature(request)
                if signature != last_signature:
                    last_signature = signature
                    last_activity = now
                if now - started >= request.hard_timeout_seconds:
                    completion_reason = "hard_timeout"
                    _terminate_process_group(process)
                    break
                if (
                    request.draft_output_path.is_file()
                    and now - last_activity >= request.idle_timeout_seconds
                ):
                    completion_reason = "idle_after_draft"
                    _terminate_process_group(process)
                    break
                time.sleep(0.5)
            returncode = process.wait()
        finally:
            run_compose_cleanup(launch)
    validate_isolated_codex_outputs(request)
    return LocalCodexRunResult(
        returncode=returncode,
        completion_reason=completion_reason,
        elapsed_seconds=round(time.monotonic() - started, 3),
    )


def _prepare_or_validate_packet(
    *,
    packet_path: Path,
    experiment: Mapping[str, Any],
    batch_id: str,
    branch: str,
    parent_pack_digest: str,
    run_root: Path | None,
    c0_run_root: Path | None,
    source_root: Path,
    groundtruth_root: Path,
    access_policy: Mapping[str, Any],
) -> dict[str, Any]:
    if packet_path.is_file():
        packet = load_and_validate(
            packet_path,
            schema_filename="batch_packet.schema.json",
            digest_field="packet_digest",
            label="capability batch packet",
        )
        expected = {
            "experiment_digest": experiment["experiment_digest"],
            "batch_id": batch_id,
            "branch": branch,
            "parent_pack_digest": parent_pack_digest,
            "transfer_access_policy_digest": access_policy["policy_digest"],
            "protocol_ids": list(_batch(experiment, batch_id)["protocol_ids"]),
            "reveal_state": "revealed",
            "eligibility_status": "eligible_for_improvement",
        }
        for key, value in expected.items():
            if packet[key] != value:
                raise CapabilityImprovementError(
                    f"existing packet has stale {key}: {packet_path}"
                )
        return packet
    if packet_path.exists():
        raise CapabilityImprovementError(
            f"packet path exists but is not a regular file: {packet_path}"
        )
    if run_root is None:
        raise CapabilityImprovementError(
            "cannot build a training packet without a frozen run root"
        )
    packet = build_batch_packet_from_frozen_runs(
        experiment_manifest=experiment,
        branch=branch,
        batch_id=batch_id,
        parent_pack_digest=parent_pack_digest,
        run_root=run_root,
        c0_run_root=(
            c0_run_root.expanduser().resolve() if c0_run_root is not None else None
        ),
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        transfer_access_policy=access_policy,
    )
    write_json_atomic(packet_path, packet, mode=0o444)
    return packet


def _prepare_or_validate_workspace(
    *,
    workspace: Path,
    experiment: Mapping[str, Any],
    packet_path: Path,
    packet: Mapping[str, Any],
    parent_pack_root: Path,
    access_policy_path: Path,
    validation_feedback_path: Path,
) -> dict[str, Any]:
    from .validation import (
        validate_validation_aggregate,
        validate_validation_feedback_projection,
    )

    feedback = validate_validation_aggregate(
        validation_feedback_path,
        experiment_digest=experiment["experiment_digest"],
    )
    expected_feedback = {
        "checkpoint_label": feedback["checkpoint_label"],
        "aggregate_digest": feedback["aggregate_digest"],
        "aggregate_sha256": sha256_file(validation_feedback_path),
    }
    if workspace.exists():
        manifest = validate_isolated_worker_workspace(workspace)
        projection_path = workspace / "inputs" / "validation_feedback.json"
        validate_validation_feedback_projection(
            projection_path,
            source_aggregate=feedback,
        )
        expected_feedback["projection_sha256"] = sha256_file(projection_path)
        expected = {
            "mode": "improvement_worker",
            "experiment_digest": experiment["experiment_digest"],
            "packet_digest": packet["packet_digest"],
            "access_policy_digest": packet["transfer_access_policy_digest"],
            "staged_protocol_ids": list(packet["protocol_ids"]),
            "validation_feedback": expected_feedback,
        }
        for key, value in expected.items():
            if manifest[key] != value:
                raise CapabilityImprovementError(
                    f"existing proposer workspace has stale {key}: {workspace}"
                )
        return manifest
    return prepare_isolated_worker_workspace(
        experiment_manifest=experiment,
        packet_path=packet_path,
        parent_pack_root=parent_pack_root,
        access_policy_path=access_policy_path,
        output_root=workspace,
        mode="improvement_worker",
        validation_feedback_path=validation_feedback_path,
    )


def _prepare_compilable_draft(
    *,
    draft_path: Path,
    event_log_path: Path,
    parent_pack_root: Path,
    candidate_root: Path,
) -> tuple[Path, list[dict[str, Any]], Path]:
    del parent_pack_root, candidate_root
    try:
        draft = load_json_object(draft_path, label="capability proposal draft")
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    schema_path = improvement_schema_root() / "capability_proposal_draft.schema.json"
    try:
        validate_document(
            draft,
            schema_path,
            label="capability proposal draft",
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    repairs: list[dict[str, Any]] = []
    for unit in draft["change_units"]:
        if (
            unit["admission_basis"] == "recurring_root_error"
            and unit["synthetic_regression_case_ids"]
        ):
            # A recurrence-backed unit can (and generally should) ship synthetic
            # fixtures, but those fixtures are not its admission basis.  Codex
            # occasionally lists a negative fixture here because the prompt asks
            # it to run and cite every changed fixture.  The trusted cluster is
            # already the authoritative admission evidence, so clear only this
            # redundant bookkeeping field and preserve the authored unit,
            # mutations, and complete fixture set byte-for-byte.
            unit["synthetic_regression_case_ids"] = []
            repairs.append(
                {
                    "change_id": unit["change_id"],
                    "field": "synthetic_regression_case_ids",
                    "repair": "cleared_for_recurring_root_error_basis",
                }
            )
        if (
            unit["capability_class"] == "instruction_only"
            and unit["residual_judgment"] is None
            and isinstance(unit["instruction_only_rationale"], str)
            and unit["instruction_only_rationale"].strip()
        ):
            # The rationale already states why—and therefore what—must remain
            # target-specific model judgment. Preserve the authored draft and
            # deterministically project that statement into the stricter final
            # proposal field instead of paying for another model run.
            unit["residual_judgment"] = unit["instruction_only_rationale"]
            repairs.append(
                {
                    "change_id": unit["change_id"],
                    "field": "residual_judgment",
                    "source_field": "instruction_only_rationale",
                    "repair": "copied_existing_instruction_only_rationale",
                }
            )
    if not repairs:
        return draft_path, [], event_log_path
    compilable_path = draft_path.with_name("proposal_draft.compilable.json")
    write_json_atomic(compilable_path, draft)
    try:
        validate_document(
            draft,
            schema_path,
            label="deterministically repaired capability proposal draft",
        )
    except AuditArtifactError as error:
        raise CapabilityImprovementError(str(error)) from error
    return compilable_path, repairs, event_log_path


def _default_parent_pack(
    experiment_root: Path,
    experiment: Mapping[str, Any],
    batch_id: str,
    branch: str,
) -> Path:
    batches = list(experiment["batches"])
    matching = [
        index for index, item in enumerate(batches) if item["batch_id"] == batch_id
    ]
    if len(matching) != 1:
        raise CapabilityImprovementError(f"unknown batch: {batch_id}")
    prior_count = matching[0] * 5
    require_active_branch(branch)
    label = checkpoint_id(prior_count)
    if prior_count == 0:
        relative = experiment["initial_pack"]["references"][label]
        return experiment_root / relative
    return experiment_root / "checkpoints" / label / "pack"


def _batch(experiment: Mapping[str, Any], batch_id: str) -> Mapping[str, Any]:
    values = [item for item in experiment["batches"] if item["batch_id"] == batch_id]
    if len(values) != 1:
        raise CapabilityImprovementError(f"unknown batch: {batch_id}")
    return values[0]


def _learning_result(
    *,
    proposal: Mapping[str, Any],
    proposal_path: Path,
    packet_path: Path,
    workspace: Path,
    candidate_root: Path,
    protocols: Any,
    branch: str,
    batch_id: str,
    run_result: LocalCodexRunResult | None,
    bookkeeping_repairs: list[dict[str, Any]],
) -> dict[str, Any]:
    next_action = "run independent review or explicitly choose human review"
    return {
        "status": "proposal_ready",
        "batch_id": batch_id,
        "branch": branch,
        "protocol_count": len(protocols),
        "protocol_ids": list(protocols),
        "proposal_digest": proposal["proposal_digest"],
        "change_unit_count": len(proposal["change_units"]),
        "packet_path": packet_path.resolve().as_posix(),
        "workspace_path": workspace.resolve().as_posix(),
        "candidate_root": candidate_root.resolve().as_posix(),
        "proposal_path": proposal_path.resolve().as_posix(),
        "codex_run": (
            None
            if run_result is None
            else {
                "returncode": run_result.returncode,
                "completion_reason": run_result.completion_reason,
                "elapsed_seconds": run_result.elapsed_seconds,
            }
        ),
        "bookkeeping_repairs": bookkeeping_repairs,
        "next_action": next_action,
    }


def _output_signature(request: LocalCodexRunRequest) -> tuple[tuple[int, int], ...]:
    result = []
    for path in (
        request.event_log_path,
        request.stderr_log_path,
        request.draft_output_path,
    ):
        try:
            stat = path.stat()
        except FileNotFoundError:
            result.append((-1, -1))
        else:
            result.append((stat.st_size, stat.st_mtime_ns))
    return tuple(result)


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
