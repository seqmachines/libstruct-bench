from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    copy_capability_pack,
    freeze_tree,
    improvement_schema_root,
    load_and_validate,
    reject_private_output_in_repository,
    with_digest,
)
from .experiment import validate_experiment_manifest
from .harbor import (
    FIXED_ANCHOR,
    build_harbor_job_config,
    prepare_capability_harbor_integration,
)
from .human_guided import (
    HUMAN_COMPARISON_SCHEMA,
    HUMAN_MANIFEST_PATH,
    HUMAN_PROPOSAL_SCHEMA,
    HUMAN_REGISTRY_PATH,
    _review_root,
    build_human_guided_batch_packet,
    compile_human_protocol_review_proposal,
    initialize_human_guided_experiment,
    prepare_next_human_protocol_review,
    record_human_protocol_review_decision,
    refresh_human_protocol_verifier,
    run_human_guided_batch_synthesis,
    validate_human_guided_experiment,
)
from .isolation import (
    WORKSPACE_SCHEMA_VERSION,
    _resolve_parent_exemplar_memory,
    _workspace_exemplar_memory_record,
    compile_codex_output_schema,
    validate_isolated_worker_workspace,
)
from .lineage import BATCH_IDS, checkpoint_after_batch
from .local_learning import (
    LocalCodexRunRequest,
    LocalCodexRunner,
    run_native_codex,
)
from .local_completion import run_capability_completion
from .lineage import ACTIVE_BRANCH
from .validation import (
    VALIDATION_METRICS,
    VALIDATION_PROTOCOL_IDS,
    build_validation_aggregate,
    validate_referenced_validation_access_policy,
    validate_validation_aggregate,
)
from .workflow import validate_checkpoint_runtime


AGENT_MANIFEST_PATH = Path("design/agent_guided_experiment.json")
AGENT_MANIFEST_SCHEMA = "agent_guided_experiment.schema.json"
AGENT_SWEEP_SCHEMA = "agent_checkpoint_sweep.schema.json"
AGENT_SWEEP_RESULTS_SCHEMA = "agent_checkpoint_sweep_results.schema.json"
AGENT_PROTOCOL_DECISION_DRAFT_SCHEMA = (
    "agent_protocol_review_decision_draft.schema.json"
)

_VERIFIER_SOURCE_PATHS = (
    Path("src/libstruct_bench/libgen"),
    Path("src/libstruct_bench/matching.py"),
    Path("src/libstruct_bench/normalization.py"),
    Path("schemas/analysis/libgen_error_analysis.schema.json"),
    Path("schemas/benchmark"),
    Path("benchmarks/libgen/matrix.json"),
)
_IGNORED_TREE_PARTS = frozenset({"__pycache__", ".pytest_cache", ".DS_Store"})


def build_verifier_lock(repository_root: Path) -> dict[str, Any]:
    """Fingerprint canonical verifier sources and every generated task bundle."""

    repository = repository_root.expanduser().resolve()
    source_files: list[Path] = []
    for relative in _VERIFIER_SOURCE_PATHS:
        target = repository / relative
        if target.is_dir():
            source_files.extend(_regular_files(target))
        elif target.is_file() and not target.is_symlink():
            source_files.append(target)
        else:
            raise CapabilityImprovementError(
                f"verifier-lock input is missing or unsafe: {target}"
            )
    source_contract = _tree_contract(
        repository=repository,
        roots=_VERIFIER_SOURCE_PATHS,
        files=source_files,
    )

    tasks_root = repository / "benchmarks" / "libgen" / "tasks"
    task_contracts: list[dict[str, Any]] = []
    for task_root in sorted(path for path in tasks_root.iterdir() if path.is_dir()):
        files = _regular_files(task_root)
        if not files:
            raise CapabilityImprovementError(
                f"generated verifier task is empty: {task_root}"
            )
        record = _tree_contract(
            repository=repository,
            roots=(task_root.relative_to(repository),),
            files=files,
        )
        task_contracts.append(
            {
                "protocol_id": task_root.name,
                "path": task_root.relative_to(repository).as_posix(),
                "file_count": record["file_count"],
                "tree_digest": record["tree_digest"],
            }
        )
    payload = {
        "benchmark_version": LIBGEN_BENCHMARK_VERSION,
        "repository_root": repository.as_posix(),
        "source_contract": source_contract,
        "task_contracts": task_contracts,
    }
    return {**payload, "lock_digest": canonical_digest(payload)}


def assert_verifier_lock(lock: Mapping[str, Any]) -> None:
    repository = Path(str(lock["repository_root"])).expanduser().resolve()
    observed = build_verifier_lock(repository)
    if dict(lock) != observed:
        changed = _verifier_lock_changes(dict(lock), observed)
        raise CapabilityImprovementError(
            "agent-guided verifier lock changed; this condition permits pack "
            "changes only: " + ", ".join(changed)
        )


def initialize_agent_guided_experiment(
    *,
    source_experiment_root: Path,
    output_root: Path,
    experiment_id: str,
    repository_root: Path,
    c0_validation_result_root: Path,
    created_at: str,
) -> dict[str, Any]:
    """Create the agent condition with current sidecars and fresh C0 validation.

    The mature human protocol-review implementation is intentionally retained as
    a storage/validation substrate.  No human decision is claimed: the overlay
    identifies the compatibility format and requires an independent Codex critic.
    """

    target = output_root.expanduser().resolve()
    reject_private_output_in_repository(target)
    if target.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite agent-guided experiment: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.agent-stage-", dir=target.parent)
    )
    shutil.rmtree(stage)
    timestamp = normalize_timestamp(created_at)
    try:
        initialize_human_guided_experiment(
            source_experiment_root=source_experiment_root,
            output_root=stage,
            experiment_id=f"{experiment_id}-review-substrate",
            created_at=timestamp,
        )
        _replace_c0_validation_aggregate(
            experiment_root=stage,
            result_root=c0_validation_result_root,
            created_at=timestamp,
        )

        _, registry = validate_human_guided_experiment(stage)
        entries = list(registry["entries"])
        # Refresh the first protocol last: the compatibility layer auto-prepares
        # only the current next review, so its comparison binds the final registry.
        refresh_order = [*entries[1:], entries[0]]
        for entry in refresh_order:
            trial_root = Path(str(entry["trial_root"])).expanduser().resolve()
            sidecar = (
                trial_root
                / "verifier"
                / "rescore"
                / f"libgen-{LIBGEN_BENCHMARK_VERSION}"
            )
            summary_path = sidecar / "summary.json"
            if not summary_path.is_file():
                job_summary = (
                    trial_root.parent
                    / "rescore"
                    / f"libgen-{LIBGEN_BENCHMARK_VERSION}"
                    / "summary.json"
                )
                summary_path = job_summary
            refresh_human_protocol_verifier(
                experiment_root=stage,
                protocol_id=str(entry["protocol_id"]),
                rescore_dir=sidecar,
                rescore_summary_path=summary_path,
                created_at=timestamp,
            )

        # The last refresh prepares protocol 1 using the temporary stage path.
        # Remove only that undecided render and regenerate it after atomic rename.
        _, registry = validate_human_guided_experiment(stage)
        first_review = _review_root(stage, registry["entries"][0])
        if first_review.exists():
            shutil.rmtree(first_review)

        human, registry = validate_human_guided_experiment(stage)
        verifier_lock = build_verifier_lock(repository_root)
        payload = {
            "schema_version": "libstruct.libgen_agent_guided_experiment.v1",
            "experiment_id": experiment_id,
            "condition": "offline_agent_guided_protocol_review_frozen_verifier",
            "created_at": timestamp,
            "review_substrate": {
                "format": "human_protocol_review_v1_compatibility_no_human_claim",
                "manifest_path": HUMAN_MANIFEST_PATH.as_posix(),
                "manifest_sha256": sha256_file(stage / HUMAN_MANIFEST_PATH),
                "manifest_digest": human["manifest_digest"],
                "registry_path": HUMAN_REGISTRY_PATH.as_posix(),
                "registry_sha256": sha256_file(stage / HUMAN_REGISTRY_PATH),
                "registry_digest": registry["registry_digest"],
            },
            "verifier_lock": verifier_lock,
            "protocol_review": {
                "order": "frozen_B1_through_B5_protocol_order",
                "batch_size": 5,
                "proposer": "read_isolated_codex",
                "critic": "independent_read_isolated_codex",
                "revision_rounds": 1,
                "finding_status": "agent_proposal_not_canonical_groundtruth_approval",
            },
            "mutation_policy": {
                "agent_mutation_scope": "capability_pack_only",
                "verifier_mutation": False,
                "groundtruth_mutation": False,
                "orchestrator_exemplar_projection": "deterministic_not_agent_authored",
                "maximum_proposed_pack_changes_per_batch": 2,
                "maximum_accepted_pack_changes_per_batch": 1,
            },
            "validation_policy": {
                "feedback_scope": "macro_means_and_counts_only",
                "same_batch_direct_mutation": False,
                "next_batch_adjustment": True,
                "checkpoint_sweep": "after_each_batch_replay_all_available_C5_through_current_on_fixed_validation_panel",
                "sealed_final_test": "unavailable_until_C25_lock",
            },
        }
        agent = with_digest(payload, "manifest_digest")
        _validate_agent_document(agent, AGENT_MANIFEST_SCHEMA, "agent-guided manifest")
        write_json_atomic(stage / AGENT_MANIFEST_PATH, agent, mode=0o444)
        validate_agent_guided_experiment(stage)
        stage.replace(target)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    validate_agent_guided_experiment(target)
    prepared = prepare_next_human_protocol_review(
        experiment_root=target,
        created_at=timestamp,
    )
    return {
        "status": "initialized",
        "experiment_root": target.as_posix(),
        "agent_manifest_digest": load_and_validate(
            target / AGENT_MANIFEST_PATH,
            schema_filename=AGENT_MANIFEST_SCHEMA,
            digest_field="manifest_digest",
            label="agent-guided manifest",
        )["manifest_digest"],
        "benchmark_version": LIBGEN_BENCHMARK_VERSION,
        "protocol_count": 25,
        "first_review": prepared,
        "next_action": "run the independent agent review for the prepared protocol",
    }


def validate_agent_guided_experiment(
    experiment_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = experiment_root.expanduser().resolve()
    agent = load_and_validate(
        root / AGENT_MANIFEST_PATH,
        schema_filename=AGENT_MANIFEST_SCHEMA,
        digest_field="manifest_digest",
        label="agent-guided manifest",
    )
    human, registry = validate_human_guided_experiment(root)
    substrate = agent["review_substrate"]
    expected = {
        "manifest_sha256": sha256_file(root / HUMAN_MANIFEST_PATH),
        "manifest_digest": human["manifest_digest"],
        "registry_sha256": sha256_file(root / HUMAN_REGISTRY_PATH),
        "registry_digest": registry["registry_digest"],
    }
    for key, value in expected.items():
        if substrate[key] != value:
            raise CapabilityImprovementError(f"agent review substrate has stale {key}")
    assert_verifier_lock(agent["verifier_lock"])
    for entry in registry["entries"]:
        details_paths = [
            Path(str(item["path"])).expanduser().resolve()
            for item in entry["artifacts"]
            if item["role"] == "verifier_details"
        ]
        if len(details_paths) != 1:
            raise CapabilityImprovementError(
                f"agent baseline lacks one verifier-details artifact: {entry['protocol_id']}"
            )
        details = json.loads(details_paths[0].read_text(encoding="utf-8"))
        if (
            details.get("benchmark_version")
            != agent["verifier_lock"]["benchmark_version"]
        ):
            raise CapabilityImprovementError(
                f"agent baseline uses another verifier version: {entry['protocol_id']}"
            )
    experiment = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    return agent, registry, experiment


def run_next_agent_protocol_review(
    *,
    experiment_root: Path,
    agent_version: str,
    created_at: str,
    codex_executable: str = "codex",
    idle_timeout_seconds: float = 300.0,
    hard_timeout_seconds: float = 7200.0,
    agent_runner: LocalCodexRunner | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Propose, independently critique, revise once, and record one protocol.

    The proposer and critic receive separate read-isolated workspaces.  Their
    records affect only the capability-learning overlay; canonical GT and the
    frozen verifier remain untouched.
    """

    if not agent_version.strip():
        raise CapabilityImprovementError("agent reviewer version must be non-empty")
    if idle_timeout_seconds <= 0 or hard_timeout_seconds <= idle_timeout_seconds:
        raise CapabilityImprovementError(
            "protocol-review timeouts require hard > idle > 0"
        )
    announce = progress or (lambda _message: None)
    root = experiment_root.expanduser().resolve()
    agent, registry, experiment = validate_agent_guided_experiment(root)
    entry = next(
        (
            item
            for item in registry["entries"]
            if not (_review_root(root, item) / "review-record.json").is_file()
        ),
        None,
    )
    if entry is None:
        return {
            "status": "all_protocol_reviews_complete",
            "reviewed_protocol_count": 25,
            "next_action": "synthesize the final available five-protocol batch",
        }
    prepared = prepare_next_human_protocol_review(
        experiment_root=root,
        protocol_id=str(entry["protocol_id"]),
        created_at=created_at,
    )
    review_root = _review_root(root, entry)
    comparison_path = review_root / "comparison.json"
    comparison = load_and_validate(
        comparison_path,
        schema_filename=HUMAN_COMPARISON_SCHEMA,
        digest_field="comparison_digest",
        label="agent protocol comparison",
    )
    model = str(experiment["anchor"]["model"])
    effort = str(experiment["anchor"]["reasoning_effort"])
    if model != "gpt-5.6-sol" or effort != "max":
        raise CapabilityImprovementError(
            "agent protocol review must use the pinned GPT-5.6-sol/max anchor"
        )
    runner = agent_runner or run_native_codex
    announce(f"protocol proposer: {entry['protocol_id']}")
    proposal_path, proposer_run = _run_protocol_proposer(
        root=root,
        agent=agent,
        registry_entry=entry,
        comparison=comparison,
        comparison_path=comparison_path,
        review_root=review_root,
        model=model,
        version=agent_version,
        reasoning_effort=effort,
        created_at=created_at,
        revision_round=0,
        codex_executable=codex_executable,
        idle_timeout_seconds=idle_timeout_seconds,
        hard_timeout_seconds=hard_timeout_seconds,
        runner=runner,
    )
    announce("independent protocol critic: initial proposal")
    decision, critic_run = _run_protocol_critic(
        root=root,
        agent=agent,
        registry_entry=entry,
        comparison=comparison,
        proposal_path=proposal_path,
        review_root=review_root,
        model=model,
        version=agent_version,
        reasoning_effort=effort,
        revision_round=0,
        codex_executable=codex_executable,
        idle_timeout_seconds=idle_timeout_seconds,
        hard_timeout_seconds=hard_timeout_seconds,
        runner=runner,
    )
    _record_agent_protocol_decision(
        root=root,
        proposal_path=proposal_path,
        decision=decision,
        critic_event_path=critic_run["event_path"],
        model=model,
        version=agent_version,
        reasoning_effort=effort,
        created_at=created_at,
    )

    revision_used = decision["disposition"] == "revision_requested"
    if revision_used:
        announce("protocol proposer: bounded revision")
        proposal_path, revision_run = _run_protocol_proposer(
            root=root,
            agent=agent,
            registry_entry=entry,
            comparison=comparison,
            comparison_path=comparison_path,
            review_root=review_root,
            model=model,
            version=agent_version,
            reasoning_effort=effort,
            created_at=created_at,
            revision_round=1,
            codex_executable=codex_executable,
            idle_timeout_seconds=idle_timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
            runner=runner,
        )
        proposer_run = {"initial": proposer_run, "revision": revision_run}
        announce("independent protocol critic: revised proposal")
        decision, revised_critic_run = _run_protocol_critic(
            root=root,
            agent=agent,
            registry_entry=entry,
            comparison=comparison,
            proposal_path=proposal_path,
            review_root=review_root,
            model=model,
            version=agent_version,
            reasoning_effort=effort,
            revision_round=1,
            codex_executable=codex_executable,
            idle_timeout_seconds=idle_timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
            runner=runner,
        )
        if decision["disposition"] == "revision_requested":
            raise CapabilityImprovementError(
                "the independent critic requested a forbidden second protocol revision"
            )
        critic_run = {"initial": critic_run, "revision": revised_critic_run}
        _record_agent_protocol_decision(
            root=root,
            proposal_path=proposal_path,
            decision=decision,
            critic_event_path=revised_critic_run["event_path"],
            model=model,
            version=agent_version,
            reasoning_effort=effort,
            created_at=created_at,
        )

    assert_verifier_lock(agent["verifier_lock"])
    final_record = load_and_validate(
        review_root / "review-record.json",
        schema_filename="human_protocol_review_record.schema.json",
        digest_field="record_digest",
        label="agent-reviewed protocol record",
    )
    reviewed_in_batch = sum(
        (_review_root(root, item) / "review-record.json").is_file()
        for item in registry["entries"]
        if item["batch_id"] == entry["batch_id"]
    )
    return {
        "status": "protocol_review_complete",
        "batch_id": entry["batch_id"],
        "protocol_id": entry["protocol_id"],
        "reviewed_in_batch": reviewed_in_batch,
        "revision_used": revision_used,
        "final_disposition": decision["disposition"],
        "eligible_root_finding_count": final_record["eligible_root_finding_count"],
        "proposal_path": proposal_path.as_posix(),
        "record_path": (review_root / "review-record.json").as_posix(),
        "proposer_runs": proposer_run,
        "critic_runs": critic_run,
        "prepared": prepared,
        "next_action": (
            "synthesize and independently review this batch"
            if reviewed_in_batch == 5
            else "run the next protocol review"
        ),
    }


def run_agent_guided_batch_synthesis(
    *,
    experiment_root: Path,
    batch_id: str,
    workspace_root: Path | None = None,
    codex_executable: str = "codex",
    idle_timeout_seconds: float = 300.0,
    hard_timeout_seconds: float = 7200.0,
    agent_runner: Any = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Synthesize pact-only candidates after five independent protocol reviews."""

    root = experiment_root.expanduser().resolve()
    agent, registry, _ = validate_agent_guided_experiment(root)
    entries = [item for item in registry["entries"] if item["batch_id"] == batch_id]
    if len(entries) != 5:
        raise CapabilityImprovementError(f"unknown agent batch: {batch_id}")
    incomplete = [
        item["protocol_id"]
        for item in entries
        if not (_review_root(root, item) / "review-record.json").is_file()
    ]
    if incomplete:
        raise CapabilityImprovementError(
            "agent batch synthesis requires five finalized protocol reviews: "
            + ", ".join(incomplete)
        )
    for entry in entries:
        record = load_and_validate(
            _review_root(root, entry) / "review-record.json",
            schema_filename="human_protocol_review_record.schema.json",
            digest_field="record_digest",
            label="agent protocol review record",
        )
        if record.get("reviewer_kind") != "independent_codex":
            raise CapabilityImprovementError(
                f"agent batch contains a non-agent review: {entry['protocol_id']}"
            )
    build_human_guided_batch_packet(experiment_root=root, batch_id=batch_id)
    result = run_human_guided_batch_synthesis(
        experiment_root=root,
        batch_id=batch_id,
        workspace_root=workspace_root,
        codex_executable=codex_executable,
        idle_timeout_seconds=idle_timeout_seconds,
        hard_timeout_seconds=hard_timeout_seconds,
        agent_runner=agent_runner,
        progress=progress,
    )
    # The implementation intentionally reuses the mature human-guided storage
    # substrate, but this branch must never claim that a human authored or
    # adjudicated the synthesis.
    result.pop("human_condition", None)
    assert_verifier_lock(agent["verifier_lock"])
    return {
        **result,
        "agent_condition": "offline_agent_guided_protocol_review_frozen_verifier",
        "mutation_scope": "capability_pack_only",
        "next_action": "run independent exact-byte pact review and deterministic application",
    }


def complete_agent_guided_batch(
    *,
    experiment_root: Path,
    batch_id: str,
    groundtruth_root: Path,
    authorize_apply: bool,
    exemplar_max_results: int = 3,
    codex_executable: str = "codex",
    idle_timeout_seconds: float = 300.0,
    hard_timeout_seconds: float = 7200.0,
    docker_image: str = "python:3.13-slim",
    agent_runner: Any = None,
    synthetic_runner: Any = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Independently review/apply the pack proposal and freeze one checkpoint."""

    root = experiment_root.expanduser().resolve()
    agent, _, _ = validate_agent_guided_experiment(root)
    build_human_guided_batch_packet(experiment_root=root, batch_id=batch_id)
    result = run_capability_completion(
        branch=ACTIVE_BRANCH,
        review_mode="independent",
        experiment_root=root,
        batch_id=batch_id,
        groundtruth_root=groundtruth_root,
        authorize_apply=authorize_apply,
        exemplar_max_results=exemplar_max_results,
        round_root=root / "rounds" / batch_id / ACTIVE_BRANCH,
        codex_executable=codex_executable,
        idle_timeout_seconds=idle_timeout_seconds,
        hard_timeout_seconds=hard_timeout_seconds,
        docker_image=docker_image,
        critic_runner=agent_runner,
        revision_runner=agent_runner,
        synthetic_runner=synthetic_runner,
        progress=progress,
    )
    assert_verifier_lock(agent["verifier_lock"])
    checkpoint = checkpoint_after_batch(batch_id)
    return {
        **result,
        "agent_condition": "offline_agent_guided_protocol_review_frozen_verifier",
        "verifier_lock_digest": agent["verifier_lock"]["lock_digest"],
        "next_agent_action": (
            f"run and record canonical {checkpoint} validation, then replay all "
            f"available C5-through-{checkpoint} checkpoints on the validation panel"
            if (root / "checkpoints" / checkpoint).is_dir()
            else "finish independent exact-byte review and authorized application"
        ),
    }


def agent_guided_status(experiment_root: Path) -> dict[str, Any]:
    root = experiment_root.expanduser().resolve()
    agent, registry, _ = validate_agent_guided_experiment(root)
    batches: list[dict[str, Any]] = []
    reviewed_total = 0
    for batch_id in BATCH_IDS:
        entries = [item for item in registry["entries"] if item["batch_id"] == batch_id]
        reviewed = sum(
            (_review_root(root, item) / "review-record.json").is_file()
            for item in entries
        )
        reviewed_total += reviewed
        checkpoint = checkpoint_after_batch(batch_id)
        aggregate = root / "validation" / "aggregates" / f"{checkpoint}.json"
        sweeps = sorted(
            (root / "checkpoint-sweeps").glob(f"after-{batch_id}-*/manifest.json")
            if (root / "checkpoint-sweeps").is_dir()
            else []
        )
        batches.append(
            {
                "batch_id": batch_id,
                "reviewed_protocols": reviewed,
                "checkpoint": checkpoint,
                "checkpoint_exists": (root / "checkpoints" / checkpoint).is_dir(),
                "canonical_validation_exists": aggregate.is_file(),
                "checkpoint_sweep_count": len(sweeps),
            }
        )
    next_entry = next(
        (
            item
            for item in registry["entries"]
            if not (_review_root(root, item) / "review-record.json").is_file()
        ),
        None,
    )
    return {
        "status": "open",
        "condition": agent["condition"],
        "benchmark_version": agent["verifier_lock"]["benchmark_version"],
        "verifier_lock_digest": agent["verifier_lock"]["lock_digest"],
        "reviewed_protocol_count": reviewed_total,
        "next_protocol": None if next_entry is None else next_entry["protocol_id"],
        "batches": batches,
    }


def _run_protocol_proposer(
    *,
    root: Path,
    agent: Mapping[str, Any],
    registry_entry: Mapping[str, Any],
    comparison: Mapping[str, Any],
    comparison_path: Path,
    review_root: Path,
    model: str,
    version: str,
    reasoning_effort: str,
    created_at: str,
    revision_round: int,
    codex_executable: str,
    idle_timeout_seconds: float,
    hard_timeout_seconds: float,
    runner: LocalCodexRunner,
) -> tuple[Path, dict[str, Any]]:
    suffix = "" if revision_round == 0 else "-r1"
    workspace = review_root / f"agent-proposer-workspace{suffix}"
    proposal_path = review_root / (
        "proposal.json" if revision_round == 0 else "proposal-r1.json"
    )
    if proposal_path.is_file():
        load_and_validate(
            proposal_path,
            schema_filename=HUMAN_PROPOSAL_SCHEMA,
            digest_field="proposal_digest",
            label="agent protocol proposal",
        )
        return proposal_path, {"status": "reused", "workspace": workspace.as_posix()}
    manifest = _prepare_agent_protocol_workspace(
        root=root,
        agent=agent,
        registry_entry=registry_entry,
        comparison=comparison,
        workspace=workspace,
        mode=(
            "agent_protocol_proposer"
            if revision_round == 0
            else "agent_protocol_revision"
        ),
        proposal_path=(review_root / "proposal.json") if revision_round else None,
        decision_path=(review_root / "decision.json") if revision_round else None,
        revision_round=revision_round,
    )
    run = _execute_protocol_worker(
        workspace=workspace,
        manifest=manifest,
        model=model,
        version=version,
        reasoning_effort=reasoning_effort,
        codex_executable=codex_executable,
        idle_timeout_seconds=idle_timeout_seconds,
        hard_timeout_seconds=hard_timeout_seconds,
        runner=runner,
    )
    draft_path = workspace / str(manifest["agent_contract"]["draft_output_path"])
    draft = load_json_object(draft_path, label="agent protocol proposal draft")
    if int(draft.get("revision_round", -1)) != revision_round:
        raise CapabilityImprovementError(
            "agent protocol proposer emitted the wrong revision round"
        )
    try:
        compile_human_protocol_review_proposal(
            experiment_root=root,
            draft_path=draft_path,
            proposer_model=model,
            proposer_version=version,
            reasoning_effort=reasoning_effort,
            created_at=created_at,
            transcript_path=Path(run["event_path"]),
        )
    except CapabilityImprovementError as error:
        # A draft can satisfy the JSON schema while violating a cross-artifact
        # compiler invariant (for example, grouping observations whose verifier
        # categories differ). Give the proposer one narrow pre-review repair so
        # a clerical classification error does not bypass or consume the critic's
        # single scientific revision round.
        repair_workspace = review_root / f"agent-proposer-compile-repair{suffix}"
        repair_manifest = _prepare_agent_protocol_workspace(
            root=root,
            agent=agent,
            registry_entry=registry_entry,
            comparison=comparison,
            workspace=repair_workspace,
            mode="agent_protocol_compile_repair",
            proposal_path=draft_path,
            decision_path=None,
            revision_round=revision_round,
            compiler_feedback=str(error),
        )
        repair_run = _execute_protocol_worker(
            workspace=repair_workspace,
            manifest=repair_manifest,
            model=model,
            version=version,
            reasoning_effort=reasoning_effort,
            codex_executable=codex_executable,
            idle_timeout_seconds=idle_timeout_seconds,
            hard_timeout_seconds=hard_timeout_seconds,
            runner=runner,
        )
        repaired_draft_path = repair_workspace / str(
            repair_manifest["agent_contract"]["draft_output_path"]
        )
        repaired_draft = load_json_object(
            repaired_draft_path, label="repaired agent protocol proposal draft"
        )
        if int(repaired_draft.get("revision_round", -1)) != revision_round:
            raise CapabilityImprovementError(
                "agent protocol compile repair emitted the wrong revision round"
            )
        compile_human_protocol_review_proposal(
            experiment_root=root,
            draft_path=repaired_draft_path,
            proposer_model=model,
            proposer_version=version,
            reasoning_effort=reasoning_effort,
            created_at=created_at,
            transcript_path=Path(repair_run["event_path"]),
        )
        run = {
            "status": "compile_repaired",
            "compiler_feedback": str(error),
            "initial": run,
            "repair": repair_run,
        }
    assert_verifier_lock(agent["verifier_lock"])
    return proposal_path, run


def _run_protocol_critic(
    *,
    root: Path,
    agent: Mapping[str, Any],
    registry_entry: Mapping[str, Any],
    comparison: Mapping[str, Any],
    proposal_path: Path,
    review_root: Path,
    model: str,
    version: str,
    reasoning_effort: str,
    revision_round: int,
    codex_executable: str,
    idle_timeout_seconds: float,
    hard_timeout_seconds: float,
    runner: LocalCodexRunner,
) -> tuple[dict[str, Any], dict[str, Any]]:
    suffix = "" if revision_round == 0 else "-r1"
    workspace = review_root / f"agent-critic-workspace{suffix}"
    manifest = _prepare_agent_protocol_workspace(
        root=root,
        agent=agent,
        registry_entry=registry_entry,
        comparison=comparison,
        workspace=workspace,
        mode="agent_protocol_critic",
        proposal_path=proposal_path,
        decision_path=(review_root / "decision.json") if revision_round else None,
        revision_round=revision_round,
    )
    run = _execute_protocol_worker(
        workspace=workspace,
        manifest=manifest,
        model=model,
        version=version,
        reasoning_effort=reasoning_effort,
        codex_executable=codex_executable,
        idle_timeout_seconds=idle_timeout_seconds,
        hard_timeout_seconds=hard_timeout_seconds,
        runner=runner,
    )
    decision_path = workspace / str(manifest["agent_contract"]["draft_output_path"])
    decision = load_json_object(
        decision_path, label="agent protocol critic decision draft"
    )
    _validate_agent_document(
        decision,
        AGENT_PROTOCOL_DECISION_DRAFT_SCHEMA,
        "agent protocol critic decision draft",
    )
    proposal = load_and_validate(
        proposal_path,
        schema_filename=HUMAN_PROPOSAL_SCHEMA,
        digest_field="proposal_digest",
        label="agent protocol proposal",
    )
    if decision["proposal_digest"] != proposal["proposal_digest"]:
        raise CapabilityImprovementError("agent protocol critic targeted stale bytes")
    if (decision["disposition"] == "revision_requested") != (
        decision["revision_instruction"] is not None
    ):
        raise CapabilityImprovementError(
            "agent protocol critic revision instruction is inconsistent"
        )
    return decision, run


def _record_agent_protocol_decision(
    *,
    root: Path,
    proposal_path: Path,
    decision: Mapping[str, Any],
    critic_event_path: Path | str,
    model: str,
    version: str,
    reasoning_effort: str,
    created_at: str,
) -> dict[str, Any]:
    return record_human_protocol_review_decision(
        experiment_root=root,
        proposal_path=proposal_path,
        reviewer_id="independent-codex-protocol-critic",
        reviewer_kind="independent_codex",
        reviewer_model=model,
        reviewer_version=version,
        reviewer_reasoning_effort=reasoning_effort,
        reviewer_transcript_sha256=sha256_file(Path(critic_event_path)),
        disposition=str(decision["disposition"]),
        rationale=str(decision["rationale"]),
        revision_instruction=(
            str(decision["revision_instruction"])
            if decision["revision_instruction"] is not None
            else None
        ),
        started_at=created_at,
        completed_at=created_at,
    )


def _prepare_agent_protocol_workspace(
    *,
    root: Path,
    agent: Mapping[str, Any],
    registry_entry: Mapping[str, Any],
    comparison: Mapping[str, Any],
    workspace: Path,
    mode: str,
    proposal_path: Path | None,
    decision_path: Path | None,
    revision_round: int,
    compiler_feedback: str | None = None,
) -> dict[str, Any]:
    if workspace.exists():
        manifest = validate_isolated_worker_workspace(workspace)
        if (
            manifest["mode"] != mode
            or manifest["packet_digest"] != comparison["comparison_digest"]
            or manifest["staged_protocol_ids"] != [comparison["protocol_id"]]
        ):
            raise CapabilityImprovementError(
                f"stale agent protocol workspace: {workspace}"
            )
        return manifest
    parent_checkpoint = str(comparison["parent_checkpoint"])
    parent_root = root / "checkpoints" / parent_checkpoint
    checkpoint, _, _ = validate_checkpoint_runtime(parent_root)
    parent_pack = parent_root / "pack"
    memory_root, memory, source_checkpoint = _resolve_parent_exemplar_memory(
        parent_pack,
        experiment_digest=checkpoint["experiment_digest"],
    )
    output = workspace.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.building-", dir=output.parent)
    )
    try:
        inputs = temporary / "inputs"
        copy_capability_pack(parent_pack, inputs / "capability_pack", freeze=True)
        shutil.copytree(memory_root, inputs / "exemplar_memory")
        staged_files: list[dict[str, str]] = []
        for tree, role in (
            (inputs / "capability_pack", "current_capability_pack"),
            (inputs / "exemplar_memory", "current_exemplar_memory"),
        ):
            for path in _regular_files(tree):
                staged_files.append(
                    {
                        "path": path.relative_to(temporary).as_posix(),
                        "sha256": sha256_file(path),
                        "role": role,
                    }
                )
        path_map: dict[str, str] = {}
        evidence_root = inputs / "evidence"
        for index, artifact in enumerate(registry_entry["artifacts"], start=1):
            source = Path(str(artifact["path"])).expanduser().resolve()
            if source.is_symlink() or not source.is_file():
                raise CapabilityImprovementError(
                    f"agent protocol evidence is missing: {source}"
                )
            suffix = "".join(source.suffixes[-2:]) or ".bin"
            role = str(artifact["role"])
            destination = (
                evidence_root / f"{index:02d}-{role}-{artifact['sha256'][:12]}{suffix}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if sha256_file(destination) != artifact["sha256"]:
                raise CapabilityImprovementError(
                    f"agent protocol evidence changed while staging: {source}"
                )
            destination.chmod(0o444)
            relative = destination.relative_to(temporary).as_posix()
            path_map[source.as_posix()] = relative
            staged_files.append(
                {"path": relative, "sha256": artifact["sha256"], "role": role}
            )
        comparison_view = copy.deepcopy(dict(comparison))
        comparison_view.pop("comparison_digest", None)
        for artifact in comparison_view["artifacts"]:
            resolved = Path(str(artifact["path"])).expanduser().resolve().as_posix()
            artifact["path"] = path_map[resolved]
        comparison_view["source_comparison_digest"] = comparison["comparison_digest"]
        comparison_view_path = inputs / "comparison_view.json"
        write_json_atomic(comparison_view_path, comparison_view, mode=0o444)
        staged_files.append(
            {
                "path": comparison_view_path.relative_to(temporary).as_posix(),
                "sha256": sha256_file(comparison_view_path),
                "role": "protocol_comparison_view",
            }
        )
        validation_source = (
            root
            / "validation"
            / "aggregates"
            / f"{comparison['parent_checkpoint']}.json"
        )
        validation = validate_validation_aggregate(
            validation_source,
            expected_checkpoint_label=str(comparison["parent_checkpoint"]),
            expected_checkpoint_root=parent_root,
        )
        from .validation import build_validation_feedback_projection

        feedback_path = inputs / "validation_feedback.json"
        write_json_atomic(
            feedback_path,
            build_validation_feedback_projection(validation),
            mode=0o444,
        )
        feedback_sha = sha256_file(feedback_path)
        staged_files.append(
            {
                "path": feedback_path.relative_to(temporary).as_posix(),
                "sha256": feedback_sha,
                "role": "validation_macro_aggregate",
            }
        )
        review_materials = proposal_path is not None
        if proposal_path is not None:
            review_inputs = inputs / "review"
            review_inputs.mkdir(parents=True)
            staged_proposal = review_inputs / "proposal.json"
            shutil.copy2(proposal_path, staged_proposal)
            staged_proposal.chmod(0o444)
            staged_files.append(
                {
                    "path": staged_proposal.relative_to(temporary).as_posix(),
                    "sha256": sha256_file(staged_proposal),
                    "role": "protocol_review_proposal",
                }
            )
            if decision_path is not None:
                staged_decision = review_inputs / "decision.json"
                shutil.copy2(decision_path, staged_decision)
                staged_decision.chmod(0o444)
                staged_files.append(
                    {
                        "path": staged_decision.relative_to(temporary).as_posix(),
                        "sha256": sha256_file(staged_decision),
                        "role": "protocol_review_decision",
                    }
                )
        if compiler_feedback is not None:
            if mode != "agent_protocol_compile_repair" or proposal_path is None:
                raise CapabilityImprovementError(
                    "compiler feedback is allowed only for a bound compile repair"
                )
            feedback_file = inputs / "review" / "compiler_feedback.txt"
            feedback_file.parent.mkdir(parents=True, exist_ok=True)
            feedback_file.write_text(compiler_feedback.strip() + "\n", encoding="utf-8")
            feedback_file.chmod(0o444)
            staged_files.append(
                {
                    "path": feedback_file.relative_to(temporary).as_posix(),
                    "sha256": sha256_file(feedback_file),
                    "role": "protocol_proposal_compiler_feedback",
                }
            )
        prompt_text, output_schema_name, draft_output, event_output = (
            _protocol_worker_contract(mode=mode, revision_round=revision_round)
        )
        prompt_path = inputs / "worker_prompt.md"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        prompt_path.chmod(0o444)
        canonical_schema = improvement_schema_root() / output_schema_name
        output_schema_path = inputs / output_schema_name
        write_json_atomic(
            output_schema_path,
            compile_codex_output_schema(
                load_json_object(canonical_schema, label="protocol worker schema")
            ),
            mode=0o444,
        )
        for path, role in (
            (prompt_path, "agent_prompt"),
            (output_schema_path, "agent_output_schema"),
        ):
            staged_files.append(
                {
                    "path": path.relative_to(temporary).as_posix(),
                    "sha256": sha256_file(path),
                    "role": role,
                }
            )
        (temporary / "candidates").mkdir()
        (temporary / "outputs").mkdir()
        freeze_tree(inputs)
        human, _ = validate_human_guided_experiment(root)
        access_digest = validate_experiment_manifest(
            root / "design" / "experiment_manifest.json", experiment_root=root
        )["frozen_retrospective_transfer_panel"]["access_policy"]["digest"]
        memory_record = _workspace_exemplar_memory_record(
            inputs / "exemplar_memory", source_checkpoint=source_checkpoint
        )
        payload = {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "mode": mode,
            "experiment_digest": human["execution_experiment"]["experiment_digest"],
            "packet_digest": comparison["comparison_digest"],
            "access_policy_digest": access_digest,
            "staged_protocol_ids": [comparison["protocol_id"]],
            "staged_files": sorted(staged_files, key=lambda item: item["path"]),
            "exemplar_memory": memory_record,
            "review_materials_staged": review_materials,
            "validation_feedback": {
                "checkpoint_label": validation["checkpoint_label"],
                "aggregate_digest": validation["aggregate_digest"],
                "aggregate_sha256": sha256_file(validation_source),
                "projection_sha256": feedback_sha,
            },
            "agent_contract": {
                "prompt_path": prompt_path.relative_to(temporary).as_posix(),
                "output_schema_path": output_schema_path.relative_to(
                    temporary
                ).as_posix(),
                "draft_output_path": draft_output,
                "event_log_path": event_output,
            },
            "host_paths_exposed": False,
            "network_policy": "provider_api_only_no_web",
        }
        manifest = with_digest(payload, "workspace_digest")
        _validate_agent_document(
            manifest,
            "worker_workspace_manifest.schema.json",
            "agent protocol workspace",
        )
        write_json_atomic(temporary / "workspace_manifest.json", manifest, mode=0o444)
        validate_isolated_worker_workspace(temporary)
        temporary.replace(output)
        assert_verifier_lock(agent["verifier_lock"])
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _execute_protocol_worker(
    *,
    workspace: Path,
    manifest: Mapping[str, Any],
    model: str,
    version: str,
    reasoning_effort: str,
    codex_executable: str,
    idle_timeout_seconds: float,
    hard_timeout_seconds: float,
    runner: LocalCodexRunner,
) -> dict[str, Any]:
    contract = manifest["agent_contract"]
    draft_path = workspace / str(contract["draft_output_path"])
    event_path = workspace / str(contract["event_log_path"])
    stderr_path = workspace / "outputs" / "protocol-review.stderr.log"
    run_result = None
    if not draft_path.is_file():
        run_result = runner(
            LocalCodexRunRequest(
                workspace=workspace,
                prompt_path=workspace / str(contract["prompt_path"]),
                output_schema_path=workspace / str(contract["output_schema_path"]),
                draft_output_path=draft_path,
                event_log_path=event_path,
                stderr_log_path=stderr_path,
                model=model,
                version=version,
                reasoning_effort=reasoning_effort,
                codex_executable=codex_executable,
                idle_timeout_seconds=idle_timeout_seconds,
                hard_timeout_seconds=hard_timeout_seconds,
            )
        )
        if run_result.completion_reason == "hard_timeout" or (
            run_result.returncode != 0
            and run_result.completion_reason != "idle_after_draft"
        ):
            raise CapabilityImprovementError(
                f"agent protocol worker failed: {workspace}"
            )
    if not draft_path.is_file() or not event_path.is_file():
        raise CapabilityImprovementError(
            f"agent protocol worker did not emit its bound outputs: {workspace}"
        )
    if any(path.is_file() for path in (workspace / "candidates").rglob("*")):
        raise CapabilityImprovementError(
            "protocol-review workers may not author capability-pack candidates"
        )
    validate_isolated_worker_workspace(workspace)
    return {
        "status": "completed" if run_result is not None else "reused",
        "workspace": workspace.as_posix(),
        "event_path": event_path.as_posix(),
        "draft_path": draft_path.as_posix(),
        "completion_reason": (
            None if run_result is None else run_result.completion_reason
        ),
        "elapsed_seconds": None if run_result is None else run_result.elapsed_seconds,
    }


def _protocol_worker_contract(
    *, mode: str, revision_round: int
) -> tuple[str, str, str, str]:
    shared = """
You are an audit assistant in an offline capability-learning experiment. The
verifier, schemas, canonical ground truth, source bundle, and frozen prediction
must not be edited. An agent finding is a proposal, not canonical-ground-truth
approval. The only eventual mutation target is the capability pack, through a
separate exact-byte proposal/review/application workflow.

Inputs:
- inputs/comparison_view.json: the deterministic score/error comparison
- inputs/evidence/: frozen source, GT, prediction, trajectory, and verifier files
- inputs/capability_pack/: the current pact/pack to inspect for an existing control
- inputs/validation_feedback.json: aggregate-only directional feedback; never
  scientific evidence and never a source for protocol-specific claims

Do not use the web or remembered kit facts. Tie scientific claims to the staged
source/GT/prediction bytes. Cite only staged trajectory paths for process causes.
"""
    if mode in {
        "agent_protocol_proposer",
        "agent_protocol_compile_repair",
        "agent_protocol_revision",
    }:
        revision = ""
        if revision_round:
            revision = """
This is the one permitted revision. Read inputs/review/proposal.json and
inputs/review/decision.json. Implement exactly the bounded revision instruction,
retain correct findings, set revision_round to 1, and introduce no unrelated
diagnosis or remedy.
"""
        if mode == "agent_protocol_compile_repair":
            revision = """
This is a narrow pre-review compiler repair, not a scientific revision. Read the
uncompiled draft at inputs/review/proposal.json and the deterministic compiler
error at inputs/review/compiler_feedback.txt. Make only the minimum changes needed
to satisfy that invariant, preserve every supported diagnosis and remedy, and keep
the same revision_round. Do not add observations, evidence, or new scientific
claims.
"""
        prompt = (
            shared
            + revision
            + """
Classify every substantive err_NNNN exactly once. Group metric symptoms only when
they share one mechanical root cause; split unrelated causes. Explicitly test for
evaluator defects, source-scope mismatches, and equivalent representations before
attributing an error to the agent. Eligible findings must be valid, agent-caused,
and source-recoverable. Assign a process cause only with a trajectory citation.

Compiler invariant: every observation grouped in one finding must have exactly
the same `category` in inputs/comparison_view.json, and the finding's `category`
must equal it. When one mechanical cause produces observations in multiple
categories, use linked findings, identify the dependency in their diagnoses, and
avoid proposing or counting the same pact remedy twice.

For each valid agent finding, make proposed_remedy the concrete, protocol-neutral
pact update you recommend. State whether it belongs in deterministic tooling,
hybrid tooling/instructions, or instructions only. Check whether the current pact
already contains an actionable version of the control. Sequence remains the
largest T2 signal, but kind, orientation, role, modifications, and component
projection are independent dimensions and must not be collapsed into vague prose.

Write only outputs/proposal_draft.json matching the staged schema.
"""
        )
        return (
            prompt,
            "human_protocol_review_proposal_draft.schema.json",
            "outputs/proposal_draft.json",
            "outputs/proposer.events.jsonl",
        )
    prompt = (
        shared
        + """
Independently audit inputs/review/proposal.json against the comparison and every
staged artifact. Approve only if every substantive observation is classified once,
root causes are neither over-bundled nor double-counted, benchmark/evaluator issues
remain neutral, process labels have direct trajectory evidence, and each proposed
pact remedy generalizes beyond this protocol. Request one bounded revision when a
specific repair can make the proposal sound; otherwise reject or mark unresolved.
For a revised proposal, a second revision is forbidden.

Set `proposal_digest` to the value of the `proposal_digest` field inside
inputs/review/proposal.json. It is a canonical document digest and is intentionally
different from the file's SHA-256.

Write only outputs/decision_draft.json matching the staged schema.
"""
    )
    return (
        prompt,
        AGENT_PROTOCOL_DECISION_DRAFT_SCHEMA,
        "outputs/decision_draft.json",
        "outputs/critic.events.jsonl",
    )


def plan_agent_checkpoint_sweep(
    *,
    experiment_root: Path,
    after_batch: str,
    tasks_root: Path,
    base_config_path: Path,
    output_root: Path,
    jobs_dir: Path,
    created_at: str,
) -> dict[str, Any]:
    """Plan all available C5--current validation replays after one batch."""

    root = experiment_root.expanduser().resolve()
    agent, _, experiment = validate_agent_guided_experiment(root)
    if after_batch not in BATCH_IDS:
        raise CapabilityImprovementError(f"unknown agent batch: {after_batch}")
    maximum = BATCH_IDS.index(after_batch) + 1
    labels = [checkpoint_after_batch(item) for item in BATCH_IDS[:maximum]]
    missing = [label for label in labels if not (root / "checkpoints" / label).is_dir()]
    if missing:
        raise CapabilityImprovementError(
            "checkpoint sweep requires completed checkpoints: " + ", ".join(missing)
        )
    output = output_root.expanduser().resolve()
    if output.exists():
        raise CapabilityImprovementError(
            f"agent checkpoint sweep already exists: {output}"
        )
    output.mkdir(parents=True)
    resolved_jobs = jobs_dir.expanduser().resolve()
    jobs: list[dict[str, Any]] = []
    try:
        for order, label in enumerate(labels, start=1):
            checkpoint_root = root / "checkpoints" / label
            _, _, pack = validate_checkpoint_runtime(checkpoint_root)
            integration_root = output / "integrations" / label
            integration = prepare_capability_harbor_integration(
                pack_root=checkpoint_root,
                tasks_root=tasks_root,
                protocol_ids=VALIDATION_PROTOCOL_IDS,
                output_root=integration_root,
                created_at=created_at,
            )
            config_path = output / "jobs" / f"{order:02d}-{label}.json"
            job_name = f"libgen-agent-sweep-{after_batch.lower()}-{label.lower()}"
            build_harbor_job_config(
                base_config_path=base_config_path,
                integration_root=integration_root,
                tasks_root=tasks_root,
                protocol_ids=VALIDATION_PROTOCOL_IDS,
                job_name=job_name,
                jobs_dir=resolved_jobs,
                output_path=config_path,
            )
            jobs.append(
                {
                    "order": order,
                    "checkpoint_label": label,
                    "pack_digest": pack["pack_digest"],
                    "integration_digest": integration["integration_digest"],
                    "config_path": config_path.relative_to(output).as_posix(),
                    "config_sha256": sha256_file(config_path),
                    "job_name": job_name,
                    "expected_result_root": (resolved_jobs / job_name).as_posix(),
                    "harbor_command": _harbor_command(config_path),
                }
            )
        payload = {
            "schema_version": "libstruct.libgen_agent_checkpoint_sweep.v1",
            "experiment_digest": experiment["experiment_digest"],
            "agent_manifest_digest": agent["manifest_digest"],
            "verifier_lock_digest": agent["verifier_lock"]["lock_digest"],
            "after_batch": after_batch,
            "checkpoint_labels": labels,
            "protocol_ids": list(VALIDATION_PROTOCOL_IDS),
            "jobs": jobs,
            "sealed_final_test_used": False,
            "created_at": normalize_timestamp(created_at),
        }
        sweep = with_digest(payload, "sweep_digest")
        _validate_agent_document(sweep, AGENT_SWEEP_SCHEMA, "agent checkpoint sweep")
        write_json_atomic(output / "manifest.json", sweep, mode=0o444)
        return sweep
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


def record_agent_checkpoint_sweep(
    *,
    experiment_root: Path,
    sweep_root: Path,
    created_at: str,
) -> dict[str, Any]:
    root = experiment_root.expanduser().resolve()
    agent, _, experiment = validate_agent_guided_experiment(root)
    sweep_dir = sweep_root.expanduser().resolve()
    sweep = load_and_validate(
        sweep_dir / "manifest.json",
        schema_filename=AGENT_SWEEP_SCHEMA,
        digest_field="sweep_digest",
        label="agent checkpoint sweep",
    )
    if (
        sweep["experiment_digest"] != experiment["experiment_digest"]
        or sweep["agent_manifest_digest"] != agent["manifest_digest"]
        or sweep["verifier_lock_digest"] != agent["verifier_lock"]["lock_digest"]
    ):
        raise CapabilityImprovementError(
            "checkpoint sweep belongs to another condition"
        )
    result_path = sweep_dir / "results.json"
    if result_path.exists():
        return load_and_validate(
            result_path,
            schema_filename=AGENT_SWEEP_RESULTS_SCHEMA,
            digest_field="results_digest",
            label="agent checkpoint sweep results",
        )
    policy = validate_referenced_validation_access_policy(
        experiment_root=root,
        experiment_manifest=experiment,
    )
    c0 = validate_validation_aggregate(
        root / "validation" / "aggregates" / "C0.json",
        experiment_digest=experiment["experiment_digest"],
        validation_access_policy=policy,
        expected_checkpoint_label="C0",
        expected_checkpoint_root=root / "checkpoints" / "C0",
    )
    rows: list[dict[str, Any]] = []
    for job in sweep["jobs"]:
        label = str(job["checkpoint_label"])
        aggregate = build_validation_aggregate(
            experiment_digest=experiment["experiment_digest"],
            checkpoint_label=label,
            checkpoint_root=root / "checkpoints" / label,
            validation_access_policy=policy,
            result_root=Path(str(job["expected_result_root"])),
            created_at=created_at,
        )
        rows.append(
            {
                "checkpoint_label": label,
                "pack_digest": aggregate["pack_digest"],
                "result_root": str(job["expected_result_root"]),
                "result_bundle_digest": aggregate["result_bundle_digest"],
                "macro_means": dict(aggregate["macro_means"]),
                "delta_from_c0": {
                    metric: aggregate["macro_means"][metric] - c0["macro_means"][metric]
                    for metric in VALIDATION_METRICS
                },
            }
        )
    payload = {
        "schema_version": "libstruct.libgen_agent_checkpoint_sweep_results.v1",
        "sweep_digest": sweep["sweep_digest"],
        "verifier_lock_digest": agent["verifier_lock"]["lock_digest"],
        "rows": rows,
        "created_at": normalize_timestamp(created_at),
    }
    results = with_digest(payload, "results_digest")
    _validate_agent_document(
        results,
        AGENT_SWEEP_RESULTS_SCHEMA,
        "agent checkpoint sweep results",
    )
    write_json_atomic(result_path, results, mode=0o444)
    assert_verifier_lock(agent["verifier_lock"])
    return results


def _replace_c0_validation_aggregate(
    *, experiment_root: Path, result_root: Path, created_at: str
) -> None:
    root = experiment_root.expanduser().resolve()
    human, _ = validate_human_guided_experiment(root)
    experiment = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    policy = validate_referenced_validation_access_policy(
        experiment_root=root,
        experiment_manifest=experiment,
    )
    aggregate = build_validation_aggregate(
        experiment_digest=experiment["experiment_digest"],
        checkpoint_label="C0",
        checkpoint_root=root / "checkpoints" / "C0",
        validation_access_policy=policy,
        result_root=result_root,
        created_at=created_at,
    )
    aggregate_path = root / "validation" / "aggregates" / "C0.json"
    legacy_path = root / "agent-history" / "bootstrap" / "source-C0-aggregate.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(aggregate_path, legacy_path)
    legacy_path.chmod(0o444)
    write_json_atomic(aggregate_path, aggregate, mode=0o444)

    human_payload = copy.deepcopy(human)
    human_payload.pop("manifest_digest")
    human_payload["validation"]["c0_reuse"] = {
        "aggregate_path": "validation/aggregates/C0.json",
        "aggregate_sha256": sha256_file(aggregate_path),
        "aggregate_digest": aggregate["aggregate_digest"],
    }
    refreshed = with_digest(human_payload, "manifest_digest")
    _validate_agent_document(
        refreshed,
        "human_guided_experiment.schema.json",
        "agent compatibility review manifest",
    )
    write_json_atomic(root / HUMAN_MANIFEST_PATH, refreshed, mode=0o444)
    validate_human_guided_experiment(root)


def _regular_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in sorted(root.rglob("*")):
        if any(part in _IGNORED_TREE_PARTS for part in path.parts):
            continue
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"verifier-lock tree contains a symlink: {path}"
            )
        if path.is_file():
            result.append(path)
    return result


def _tree_contract(
    *, repository: Path, roots: Sequence[Path], files: Sequence[Path]
) -> dict[str, Any]:
    records = [
        {
            "path": path.relative_to(repository).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(set(files))
    ]
    return {
        "paths": sorted(path.as_posix() for path in roots),
        "file_count": len(records),
        "tree_digest": canonical_digest({"files": records}),
    }


def _verifier_lock_changes(
    expected: Mapping[str, Any], observed: Mapping[str, Any]
) -> list[str]:
    changes: list[str] = []
    for field in ("benchmark_version", "repository_root", "source_contract"):
        if expected.get(field) != observed.get(field):
            changes.append(field)
    expected_tasks = {
        item["protocol_id"]: item for item in expected.get("task_contracts", [])
    }
    observed_tasks = {
        item["protocol_id"]: item for item in observed.get("task_contracts", [])
    }
    for protocol_id in sorted(set(expected_tasks) | set(observed_tasks)):
        if expected_tasks.get(protocol_id) != observed_tasks.get(protocol_id):
            changes.append(f"task:{protocol_id}")
    if not changes and expected.get("lock_digest") != observed.get("lock_digest"):
        changes.append("lock_digest")
    return changes or ["unknown"]


def _harbor_command(config_path: Path) -> list[str]:
    return [
        "env",
        "CODEX_FORCE_AUTH_JSON=1",
        "harbor",
        "run",
        "--config",
        config_path.as_posix(),
        "--agent",
        FIXED_ANCHOR["agent"],
        "--model",
        FIXED_ANCHOR["model"],
        "--agent-kwarg",
        f"version={FIXED_ANCHOR['version']}",
        "--agent-kwarg",
        f"reasoning_effort={FIXED_ANCHOR['reasoning_effort']}",
        "--n-concurrent",
        str(FIXED_ANCHOR["concurrency"]),
        "--max-retries",
        "0",
        "--yes",
    ]


def _validate_agent_document(
    document: Mapping[str, Any], schema: str, label: str
) -> None:
    validate_document(dict(document), improvement_schema_root() / schema, label=label)
