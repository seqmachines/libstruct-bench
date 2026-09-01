from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from libstruct_bench.audit.artifacts import write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    validate_capability_pack,
    write_capability_pack_manifest,
)
from libstruct_bench.improvement.agent import (
    compile_independent_decision_draft,
    compile_capability_proposal_draft,
)
from libstruct_bench.improvement.agent_guided import (
    agent_guided_status,
    complete_agent_guided_batch,
    initialize_agent_guided_experiment,
    plan_agent_checkpoint_sweep,
    record_agent_checkpoint_sweep,
    run_agent_guided_batch_synthesis,
    run_next_agent_protocol_review,
)
from libstruct_bench.improvement.experiment import (
    build_batch_packet,
    build_cumulative_leakage_policy,
    build_final_lock,
    build_transfer_panel_authorization,
    validate_cumulative_leakage_policy,
    validate_experiment_manifest,
    validate_final_lock,
    validate_transfer_panel_authorization,
)
from libstruct_bench.improvement.evaluation import build_final_evaluation_report
from libstruct_bench.improvement.exemplar_adoption import adopt_exemplar_memory
from libstruct_bench.improvement.harbor import (
    build_final_replay_plan,
    build_harbor_job_config,
    build_prospective_round_plan,
    prepare_capability_harbor_integration,
)
from libstruct_bench.improvement.human_guided import (
    build_human_guided_batch_packet,
    compile_human_protocol_review_proposal,
    complete_human_guided_batch,
    human_guided_status,
    initialize_human_guided_experiment,
    prepare_next_human_protocol_review,
    record_human_protocol_review_decision,
    record_human_validation_guidance,
    refresh_human_protocol_verifier,
    run_human_guided_batch_synthesis,
)
from libstruct_bench.improvement.human_protocol_review_console import (
    REVIEW_SECTIONS,
    resolve_human_protocol_proposal,
    show_human_protocol_review,
    show_human_protocol_review_section,
)
from libstruct_bench.improvement.isolation import (
    prepare_isolated_worker_workspace,
    validate_isolated_worker_workspace,
)
from libstruct_bench.improvement.local_learning import run_local_learning
from libstruct_bench.improvement.local_completion import (
    run_capability_completion,
)
from libstruct_bench.improvement.mutation_lock import (
    assert_no_interrupted_split_freeze,
    experiment_mutation_lock,
)
from libstruct_bench.improvement.packets import build_batch_packet_from_frozen_runs
from libstruct_bench.improvement.lineage import ACTIVE_BRANCH, BATCH_IDS
from libstruct_bench.improvement.review_console import run_human_review_console
from libstruct_bench.improvement.review_summary import (
    render_capability_review_summary,
    write_or_validate_capability_review_summary,
)
from libstruct_bench.improvement.single_branch_migration import (
    migrate_to_cumulative_experiment,
)
from libstruct_bench.improvement.validation import (
    VALIDATION_CHECKPOINT_LABELS,
    build_validation_result_bundle,
    record_validation_aggregate,
)
from libstruct_bench.improvement.workflow import (
    apply_capability_decision,
    create_decision_template,
    finalize_capability_decision,
    freeze_checkpoint,
    record_change_decision,
    render_capability_review,
    run_pack_synthetic_suite_docker,
    validate_capability_decision,
    validate_capability_proposal,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and operate the frozen LibGen capability-improvement experiment."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_agent = subparsers.add_parser(
        "init-agent-guided",
        help=(
            "create a protocol-by-protocol agent branch with a frozen verifier "
            "and fresh C0 validation"
        ),
    )
    init_agent.add_argument("--source-experiment-root", required=True)
    init_agent.add_argument("--output-root", required=True)
    init_agent.add_argument("--experiment-id", required=True)
    init_agent.add_argument("--repository-root", required=True)
    init_agent.add_argument("--c0-validation-result-root", required=True)
    init_agent.add_argument("--created-at", required=True)

    agent_review = subparsers.add_parser(
        "agent-review-next",
        help="run proposer + independent critic for the next frozen protocol",
    )
    agent_review.add_argument("--experiment-root", required=True)
    agent_review.add_argument("--agent-version", required=True)
    agent_review.add_argument("--created-at", required=True)
    agent_review.add_argument("--codex", default="codex")
    agent_review.add_argument("--idle-timeout-seconds", type=float, default=300.0)
    agent_review.add_argument("--hard-timeout-seconds", type=float, default=7200.0)

    agent_synthesize = subparsers.add_parser(
        "agent-synthesize",
        help="synthesize pact-only candidates after five agent protocol reviews",
    )
    agent_synthesize.add_argument("--experiment-root", required=True)
    agent_synthesize.add_argument("--batch", required=True, choices=BATCH_IDS)
    agent_synthesize.add_argument("--workspace")
    agent_synthesize.add_argument("--codex", default="codex")
    agent_synthesize.add_argument("--idle-timeout-seconds", type=float, default=300.0)
    agent_synthesize.add_argument("--hard-timeout-seconds", type=float, default=7200.0)

    agent_complete = subparsers.add_parser(
        "agent-batch-complete",
        help="independently review/apply pact bytes and freeze a checkpoint",
    )
    agent_complete.add_argument("--experiment-root", required=True)
    agent_complete.add_argument("--batch", required=True, choices=BATCH_IDS)
    agent_complete.add_argument("--groundtruth-root", required=True)
    agent_complete.add_argument("--authorize-apply", action="store_true")
    agent_complete.add_argument(
        "--exemplar-max-results", type=int, choices=(1, 2, 3), default=3
    )
    agent_complete.add_argument("--codex", default="codex")
    agent_complete.add_argument("--idle-timeout-seconds", type=float, default=300.0)
    agent_complete.add_argument("--hard-timeout-seconds", type=float, default=7200.0)
    agent_complete.add_argument("--docker-image", default="python:3.13-slim")

    agent_sweep = subparsers.add_parser(
        "agent-plan-checkpoint-sweep",
        help="plan all available C5-through-current validation replays",
    )
    agent_sweep.add_argument("--experiment-root", required=True)
    agent_sweep.add_argument("--after-batch", required=True, choices=BATCH_IDS)
    agent_sweep.add_argument("--tasks", required=True)
    agent_sweep.add_argument("--base-config", required=True)
    agent_sweep.add_argument("--out", required=True)
    agent_sweep.add_argument("--jobs-dir", required=True)
    agent_sweep.add_argument("--created-at", required=True)

    agent_record_sweep = subparsers.add_parser(
        "agent-record-checkpoint-sweep",
        help="record macro-only performance from one planned checkpoint sweep",
    )
    agent_record_sweep.add_argument("--experiment-root", required=True)
    agent_record_sweep.add_argument("--sweep-root", required=True)
    agent_record_sweep.add_argument("--created-at", required=True)

    agent_status = subparsers.add_parser("agent-status")
    agent_status.add_argument("--experiment-root", required=True)

    init_human = subparsers.add_parser(
        "init-human-guided",
        help="create an open offline human-guided lineage from the completed run",
    )
    init_human.add_argument("--source-experiment-root", required=True)
    init_human.add_argument("--output-root", required=True)
    init_human.add_argument("--experiment-id", required=True)
    init_human.add_argument("--created-at", required=True)

    human_next = subparsers.add_parser(
        "human-review-next",
        help="prepare the next frozen prediction/ground-truth comparison",
    )
    human_next.add_argument("--experiment-root", required=True)
    human_next.add_argument(
        "--protocol-id",
        help="pre-stage one undecided protocol in the active five-protocol batch",
    )
    human_next.add_argument("--created-at", required=True)

    human_compile = subparsers.add_parser(
        "human-review-compile",
        help="compile and hash-bind one Codex protocol-review proposal draft",
    )
    human_compile.add_argument("--experiment-root", required=True)
    human_compile.add_argument("--draft", required=True)
    human_compile.add_argument("--model", required=True)
    human_compile.add_argument("--agent-version", required=True)
    human_compile.add_argument("--reasoning-effort", required=True)
    human_compile.add_argument("--created-at", required=True)
    human_compile.add_argument("--transcript")

    human_show = subparsers.add_parser(
        "human-review-show",
        help="show the active proposal with side-by-side T2/T3 comparisons",
    )
    human_show.add_argument("--experiment-root", required=True)
    human_show.add_argument("--proposal")

    human_section = subparsers.add_parser(
        "human-review-show-section",
        help="show one complete human-review section for a native UI gate",
    )
    human_section.add_argument("--experiment-root", required=True)
    human_section.add_argument("--section", required=True, choices=REVIEW_SECTIONS)
    human_section.add_argument("--proposal")

    human_record = subparsers.add_parser(
        "human-review-record",
        help="record approve, comment, reject, or unresolved for one proposal",
    )
    human_record.add_argument("--experiment-root", required=True)
    human_record.add_argument("--proposal")
    human_record.add_argument("--reviewer-id", required=True)
    human_record.add_argument(
        "--disposition",
        required=True,
        choices=("approve", "comment", "reject", "unresolved"),
    )
    human_record.add_argument("--rationale", required=True)
    human_record.add_argument("--revision-instruction")
    human_record.add_argument("--started-at", required=True)
    human_record.add_argument("--completed-at", required=True)

    human_packet = subparsers.add_parser(
        "human-build-packet",
        help="build the adjudicated packet after five final protocol reviews",
    )
    human_packet.add_argument("--experiment-root", required=True)
    human_packet.add_argument("--batch", required=True, choices=BATCH_IDS)

    human_synthesize = subparsers.add_parser(
        "human-synthesize",
        help="run bounded five-protocol pack synthesis from the adjudicated packet",
    )
    human_synthesize.add_argument("--experiment-root", required=True)
    human_synthesize.add_argument("--batch", required=True, choices=BATCH_IDS)
    human_synthesize.add_argument("--workspace")
    human_synthesize.add_argument("--codex", default="codex")
    human_synthesize.add_argument("--idle-timeout-seconds", type=float, default=300.0)
    human_synthesize.add_argument("--hard-timeout-seconds", type=float, default=7200.0)

    human_complete = subparsers.add_parser(
        "human-batch-complete",
        help="apply a final exact-byte pack decision and freeze its checkpoint",
    )
    human_complete.add_argument("--experiment-root", required=True)
    human_complete.add_argument("--batch", required=True, choices=BATCH_IDS)
    human_complete.add_argument("--reviewer-id", required=True)
    human_complete.add_argument("--authorize-apply", action="store_true")
    human_complete.add_argument(
        "--exemplar-max-results", type=int, choices=(1, 2, 3), default=3
    )
    human_complete.add_argument("--docker-image", default="python:3.13-slim")

    human_status = subparsers.add_parser("human-status")
    human_status.add_argument("--experiment-root", required=True)

    human_refresh = subparsers.add_parser(
        "human-refresh-verifier",
        help=(
            "repin one undecided protocol review to a versioned verifier "
            "sidecar while preserving superseded history"
        ),
    )
    human_refresh.add_argument("--experiment-root", required=True)
    human_refresh.add_argument("--protocol-id", required=True)
    human_refresh.add_argument("--rescore-dir", required=True)
    human_refresh.add_argument("--rescore-summary", required=True)
    human_refresh.add_argument("--created-at", required=True)

    human_guidance = subparsers.add_parser(
        "human-record-validation-guidance",
        help="record non-mutating aggregate-only validation interpretation",
    )
    human_guidance.add_argument("--experiment-root", required=True)
    human_guidance.add_argument(
        "--checkpoint", required=True, choices=VALIDATION_CHECKPOINT_LABELS
    )
    human_guidance.add_argument("--codex-summary", required=True)
    human_guidance.add_argument("--human-note", required=True)
    human_guidance.add_argument("--reviewer-id", required=True)
    human_guidance.add_argument("--created-at", required=True)

    learn = subparsers.add_parser(
        "learn",
        help=(
            "consume one frozen five-protocol batch and build a proposal with "
            "a read-isolated Codex container using local auth"
        ),
    )
    learn.add_argument("--experiment-root", required=True)
    learn.add_argument(
        "--batch",
        required=True,
        choices=("B1", "B2", "B3", "B4", "B5"),
    )
    learn.add_argument("--sources-root", required=True)
    learn.add_argument("--groundtruth-root", required=True)
    learn.add_argument(
        "--run-root",
        required=True,
        help="frozen five-protocol Harbor run for this training batch",
    )
    learn.add_argument(
        "--c0-run-root",
        help="required only for prospective B4-B5 paired diagnostics",
    )
    learn.add_argument("--round-root")
    learn.add_argument(
        "--workspace",
        help="reuse an existing staged proposer workspace, including a Docker run",
    )
    learn.add_argument(
        "--codex",
        default="codex",
        help="image executable name; must remain 'codex' (host paths are rejected)",
    )
    learn.add_argument("--idle-timeout-seconds", type=float, default=300.0)
    learn.add_argument("--hard-timeout-seconds", type=float, default=7200.0)

    complete = subparsers.add_parser(
        "complete",
        help=(
            "complete independent or interactive human review, one bounded "
            "revision, and optionally deterministic application and checkpointing"
        ),
    )
    complete.add_argument(
        "--review-mode",
        choices=("independent", "human"),
        default="independent",
        help="reviewer choice is orthogonal to the single cumulative lineage",
    )
    complete.add_argument("--experiment-root", required=True)
    complete.add_argument(
        "--batch",
        required=True,
        choices=("B1", "B2", "B3", "B4", "B5"),
    )
    complete.add_argument("--groundtruth-root", required=True)
    complete.add_argument("--round-root")
    complete.add_argument("--critic-workspace")
    complete.add_argument("--review-workspace")
    complete.add_argument("--revision-workspace")
    complete.add_argument("--revised-critic-workspace")
    complete.add_argument("--revised-review-workspace")
    complete.add_argument("--reviewer-id")
    complete.add_argument(
        "--manual-revision",
        action="store_true",
        help="seed a human-editable bounded revision workspace and stop",
    )
    complete.add_argument(
        "--manual-revision-ready",
        action="store_true",
        help=(
            "after a prior --manual-revision invocation, explicitly declare the "
            "seeded manual draft and candidate bytes ready for validation and "
            "fresh exact-byte review"
        ),
    )
    complete.add_argument(
        "--codex",
        default="codex",
        help="image executable name; must remain 'codex' (host paths are rejected)",
    )
    complete.add_argument("--idle-timeout-seconds", type=float, default=300.0)
    complete.add_argument("--hard-timeout-seconds", type=float, default=7200.0)
    complete.add_argument("--docker-image", default="python:3.13-slim")
    complete.add_argument(
        "--authorize-apply",
        action="store_true",
        help=(
            "explicitly authorize deterministic application and checkpoint "
            "freezing after the final exact-byte decision"
        ),
    )
    complete.add_argument(
        "--exemplar-max-results",
        type=int,
        choices=(1, 2, 3),
        default=3,
        help=(
            "maximum donor exemplars returned by the frozen checkpoint query "
            "interface; use 1 for deterministic top-1 retrieval"
        ),
    )

    manifest = subparsers.add_parser("manifest-pack")
    manifest.add_argument("--pack", required=True)

    validate_pack = subparsers.add_parser("validate-pack")
    validate_pack.add_argument("--pack", required=True)
    validate_pack.add_argument("--docker-tests", action="store_true")
    validate_pack.add_argument("--docker-image", default="python:3.13-slim")

    migrate = subparsers.add_parser(
        "migrate-single-branch",
        help=(
            "preflight or atomically replace the superseded A/H experiment "
            "with the clean cumulative C0-to-C25 design without launching Harbor"
        ),
    )
    migrate.add_argument("--experiment-root", required=True)
    migrate.add_argument("--sources-root", required=True)
    migrate.add_argument("--groundtruth-root", required=True)
    migrate.add_argument("--recorded-at", required=True)
    migrate.add_argument("--agent-version", required=True)
    migrate.add_argument(
        "--authorize-migration",
        action="store_true",
        help=(
            "authorize the journaled in-place migration; without this flag the "
            "command validates a disposable staged reconstruction"
        ),
    )

    adopt_memory = subparsers.add_parser(
        "adopt-exemplar-memory",
        help=(
            "preflight or atomically add empty prediction-shaped exemplar "
            "memory to the clean C0 experiment without running Harbor"
        ),
    )
    adopt_memory.add_argument("--experiment-root", required=True)
    adopt_memory.add_argument("--recorded-at", required=True)
    adopt_memory.add_argument(
        "--authorize-adoption",
        action="store_true",
        help=(
            "authorize the journaled C0 manifest/checkpoint update; without "
            "this flag the command validates a disposable hard-linked stage"
        ),
    )

    validate_experiment = subparsers.add_parser("validate-experiment")
    validate_experiment.add_argument("--manifest", required=True)
    validate_experiment.add_argument("--experiment-root")

    record_validation = subparsers.add_parser(
        "record-validation",
        help=(
            "sanitize one completed fixed-panel validation run into the "
            "canonical checkpoint aggregate"
        ),
    )
    record_validation.add_argument("--experiment-root", required=True)
    record_validation.add_argument(
        "--checkpoint",
        required=True,
        choices=VALIDATION_CHECKPOINT_LABELS,
    )
    record_validation.add_argument("--result-root", required=True)
    record_validation.add_argument("--created-at", required=True)

    bundle_validation = subparsers.add_parser(
        "bundle-validation-attempts",
        help=(
            "consolidate interrupted Harbor attempts into one deterministic "
            "five-protocol validation result bundle"
        ),
    )
    bundle_validation.add_argument("--canonical-config", required=True)
    bundle_validation.add_argument("--attempt-root", action="append", required=True)
    bundle_validation.add_argument("--output-root", required=True)
    bundle_validation.add_argument("--created-at", required=True)

    packet = subparsers.add_parser("prepare-batch")
    packet.add_argument("--experiment", required=True)
    packet.add_argument("--batch", required=True)
    packet.add_argument("--parent-pack-digest", required=True)
    packet.add_argument(
        "--reveal-state", choices=("concealed", "revealed"), required=True
    )
    packet.add_argument(
        "--artifacts", required=True, help="JSON array of artifact records"
    )
    packet.add_argument(
        "--terminality", required=True, help="JSON array of terminal trial records"
    )
    packet.add_argument("--access-policy", required=True)
    packet.add_argument("--out", required=True)

    auto_packet = subparsers.add_parser("prepare-batch-from-runs")
    auto_packet.add_argument("--experiment", required=True)
    auto_packet.add_argument("--batch", required=True)
    auto_packet.add_argument("--parent-pack-digest", required=True)
    auto_packet.add_argument("--run-root", required=True)
    auto_packet.add_argument("--c0-run-root")
    auto_packet.add_argument("--sources-root", required=True)
    auto_packet.add_argument("--groundtruth-root", required=True)
    auto_packet.add_argument("--access-policy", required=True)
    auto_packet.add_argument("--out", required=True)

    leakage = subparsers.add_parser("build-leakage-policy")
    leakage.add_argument("--experiment", required=True)
    leakage.add_argument("--groundtruth-root", required=True)
    leakage.add_argument("--through-batch", required=True)
    leakage.add_argument("--out", required=True)

    validate_proposal = subparsers.add_parser("validate-proposal")
    validate_proposal.add_argument("--proposal", required=True)
    validate_proposal.add_argument("--candidates", required=True)
    validate_proposal.add_argument("--parent-pack", required=True)
    validate_proposal.add_argument("--packet")
    validate_proposal.add_argument("--prior-proposal")
    validate_proposal.add_argument("--revision-decision")
    validate_proposal.add_argument("--experiment", required=True)
    validate_proposal.add_argument("--access-policy", required=True)

    compile_proposal = subparsers.add_parser("compile-proposal")
    compile_proposal.add_argument("--experiment", required=True)
    compile_proposal.add_argument("--access-policy", required=True)
    compile_proposal.add_argument("--packet", required=True)
    compile_proposal.add_argument("--parent-pack", required=True)
    compile_proposal.add_argument("--candidates", required=True)
    compile_proposal.add_argument("--draft", required=True)
    compile_proposal.add_argument("--transcript", required=True)
    compile_proposal.add_argument("--prior-proposal")
    compile_proposal.add_argument("--revision-decision")
    compile_proposal.add_argument("--out", required=True)

    compile_review = subparsers.add_parser("compile-independent-review")
    compile_review.add_argument("--proposal", required=True)
    compile_review.add_argument("--draft", required=True)
    compile_review.add_argument("--transcript", required=True)
    compile_review.add_argument("--started-at", required=True)
    compile_review.add_argument("--completed-at", required=True)
    compile_review.add_argument("--out", required=True)

    render = subparsers.add_parser("render-review")
    render.add_argument("--proposal", required=True)

    start = subparsers.add_parser("review-start")
    start.add_argument("--proposal", required=True)
    start.add_argument(
        "--reviewer-kind", choices=("independent_codex", "human"), required=True
    )
    start.add_argument("--reviewer-id", required=True)
    start.add_argument("--started-at", required=True)
    start.add_argument("--reviewer-model")
    start.add_argument("--reviewer-version")
    start.add_argument("--transcript-sha256")
    start.add_argument("--out", required=True)

    decide = subparsers.add_parser("review-decide")
    decide.add_argument("--proposal", required=True)
    decide.add_argument("--decision", required=True)
    decide.add_argument("--change-id", required=True)
    decide.add_argument(
        "--disposition",
        choices=("accept", "reject", "modify", "unresolved"),
        required=True,
    )
    decide.add_argument("--rationale", required=True)
    decide.add_argument("--revision-instruction")

    finalize = subparsers.add_parser("review-finalize")
    finalize.add_argument("--proposal", required=True)
    finalize.add_argument("--decision", required=True)
    finalize.add_argument("--completed-at", required=True)

    interactive = subparsers.add_parser("review-interactive")
    interactive.add_argument("--proposal", required=True)
    interactive.add_argument("--parent-pack", required=True)
    interactive.add_argument("--candidates", required=True)
    interactive.add_argument("--decision", required=True)
    interactive.add_argument("--reviewer-id", required=True)
    interactive.add_argument("--started-at")
    interactive.add_argument("--completed-at")
    interactive.add_argument(
        "--experiment-root",
        help=(
            "optional owning experiment root; inferred from review artifact paths "
            "when omitted"
        ),
    )

    validate_decision = subparsers.add_parser("validate-decision")
    validate_decision.add_argument("--proposal", required=True)
    validate_decision.add_argument("--decision", required=True)
    validate_decision.add_argument("--allow-in-progress", action="store_true")

    apply = subparsers.add_parser("apply")
    apply.add_argument("--proposal", required=True)
    apply.add_argument("--decision", required=True)
    apply.add_argument("--candidates", required=True)
    apply.add_argument("--parent-pack", required=True)
    apply.add_argument("--packet")
    apply.add_argument("--prior-proposal")
    apply.add_argument("--revision-decision")
    apply.add_argument("--leakage-policy", required=True)
    apply.add_argument("--experiment", required=True)
    apply.add_argument("--access-policy", required=True)
    apply.add_argument("--out", required=True)
    apply.add_argument("--created-at", required=True)
    apply.add_argument("--docker-image", default="python:3.13-slim")

    checkpoint = subparsers.add_parser("freeze-checkpoint")
    checkpoint.add_argument("--experiment", required=True)
    checkpoint.add_argument("--batch", required=True)
    checkpoint.add_argument("--protocol-count", type=int, required=True)
    checkpoint.add_argument("--parent-checkpoint", required=True)
    checkpoint.add_argument("--proposal", required=True)
    checkpoint.add_argument("--decision", required=True)
    checkpoint.add_argument("--application", required=True)
    checkpoint.add_argument("--out", required=True)
    checkpoint.add_argument("--created-at", required=True)
    checkpoint.add_argument(
        "--exemplar-max-results",
        type=int,
        choices=(1, 2, 3),
        default=3,
    )

    final_lock = subparsers.add_parser("lock-final")
    final_lock.add_argument("--experiment", required=True)
    final_lock.add_argument("--checkpoint", action="append", required=True)
    final_lock.add_argument("--created-at", required=True)
    final_lock.add_argument("--out", required=True)

    validate_lock = subparsers.add_parser("validate-final-lock")
    validate_lock.add_argument("--experiment", required=True)
    validate_lock.add_argument("--lock", required=True)

    authorize = subparsers.add_parser("authorize-transfer-panel")
    authorize.add_argument("--experiment", required=True)
    authorize.add_argument("--lock", required=True)
    authorize.add_argument("--authorized-by", required=True)
    authorize.add_argument("--authorized-at", required=True)
    authorize.add_argument("--out", required=True)

    integration = subparsers.add_parser(
        "prepare-harbor",
        help=(
            "prepare non-final diagnostic Harbor configs; this is distinct "
            "from the official post-lock replay"
        ),
    )
    integration.add_argument(
        "--pack",
        "--checkpoint",
        dest="pack",
        required=True,
        help=(
            "bare pack directory, frozen checkpoint directory, or that "
            "checkpoint's exact checkpoint.json file"
        ),
    )
    integration.add_argument("--tasks", required=True)
    integration.add_argument("--protocol-id", action="append", required=True)
    integration.add_argument("--out", required=True)
    integration.add_argument("--created-at", required=True)
    integration.add_argument("--base-config")
    integration.add_argument("--job-config-out")
    integration.add_argument("--job-name")
    integration.add_argument("--jobs-dir")

    replay = subparsers.add_parser("plan-final-replay")
    replay.add_argument("--experiment", required=True)
    replay.add_argument("--lock", required=True)
    replay.add_argument("--transfer-authorization", required=True)
    replay.add_argument("--pack", action="append", help="LABEL=/absolute/path")
    replay.add_argument("--tasks", required=True)
    replay.add_argument("--base-config", required=True)
    replay.add_argument("--out")
    replay.add_argument("--jobs-dir", required=True)
    replay.add_argument("--created-at", required=True)

    prospective = subparsers.add_parser("plan-prospective-round")
    prospective.add_argument("--experiment", required=True)
    prospective.add_argument("--batch", required=True)
    prospective.add_argument(
        "--pack", action="append", required=True, help="LABEL=/absolute/path"
    )
    prospective.add_argument("--tasks", required=True)
    prospective.add_argument("--base-config", required=True)
    prospective.add_argument("--out", required=True)
    prospective.add_argument("--jobs-dir", required=True)
    prospective.add_argument("--created-at", required=True)

    workspace = subparsers.add_parser("prepare-isolated-workspace")
    workspace.add_argument("--experiment", required=True)
    workspace.add_argument("--packet", required=True)
    workspace.add_argument("--parent-pack", required=True)
    workspace.add_argument("--access-policy", required=True)
    workspace.add_argument(
        "--mode",
        choices=(
            "improvement_worker",
            "revision_worker",
            "independent_critic",
            "human_review_console",
        ),
        required=True,
    )
    workspace.add_argument("--proposal")
    workspace.add_argument("--candidates")
    workspace.add_argument("--decision")
    workspace.add_argument("--prior-proposal")
    workspace.add_argument("--revision-decision")
    workspace.add_argument(
        "--validation-feedback",
        help=(
            "canonical prior-checkpoint validation aggregate; required for "
            "improvement_worker"
        ),
    )
    workspace.add_argument("--out", required=True)

    validate_workspace = subparsers.add_parser("validate-isolated-workspace")
    validate_workspace.add_argument("--workspace", required=True)

    report = subparsers.add_parser("report-final")
    report.add_argument("--manifest", "--plan", dest="manifest", required=True)
    report.add_argument("--result", action="append", help="LABEL=/absolute/result/root")
    report.add_argument("--created-at", required=True)
    report.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except CapabilityImprovementError as error:
        parser.error(str(error))
    return 2


def learn_main(argv: list[str] | None = None) -> int:
    """Dedicated entry point for the common one-command learning path."""

    values = sys.argv[1:] if argv is None else argv
    return main(["learn", *values])


def complete_main(argv: list[str] | None = None) -> int:
    """Dedicated entry point for shared review, application, and freeze."""

    values = sys.argv[1:] if argv is None else argv
    return main(["complete", *values])


def record_validation_main(argv: list[str] | None = None) -> int:
    """Dedicated entry point for deterministic validation aggregation."""

    values = sys.argv[1:] if argv is None else argv
    return main(["record-validation", *values])


def adopt_exemplar_memory_main(argv: list[str] | None = None) -> int:
    """Dedicated entry point for the one-time clean-C0 memory adoption."""

    values = sys.argv[1:] if argv is None else argv
    return main(["adopt-exemplar-memory", *values])


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "init-agent-guided":
        result = initialize_agent_guided_experiment(
            source_experiment_root=Path(args.source_experiment_root),
            output_root=Path(args.output_root),
            experiment_id=args.experiment_id,
            repository_root=Path(args.repository_root),
            c0_validation_result_root=Path(args.c0_validation_result_root),
            created_at=args.created_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "agent-review-next":
        result = run_next_agent_protocol_review(
            experiment_root=Path(args.experiment_root),
            agent_version=args.agent_version,
            created_at=args.created_at,
            codex_executable=args.codex,
            idle_timeout_seconds=args.idle_timeout_seconds,
            hard_timeout_seconds=args.hard_timeout_seconds,
            progress=lambda message: print(
                f"[agent-review] {message}", file=sys.stderr
            ),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "agent-synthesize":
        result = run_agent_guided_batch_synthesis(
            experiment_root=Path(args.experiment_root),
            batch_id=args.batch,
            workspace_root=Path(args.workspace) if args.workspace else None,
            codex_executable=args.codex,
            idle_timeout_seconds=args.idle_timeout_seconds,
            hard_timeout_seconds=args.hard_timeout_seconds,
            progress=lambda message: print(
                f"[agent-synthesize] {message}", file=sys.stderr
            ),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "agent-batch-complete":
        result = complete_agent_guided_batch(
            experiment_root=Path(args.experiment_root),
            batch_id=args.batch,
            groundtruth_root=Path(args.groundtruth_root),
            authorize_apply=args.authorize_apply,
            exemplar_max_results=args.exemplar_max_results,
            codex_executable=args.codex,
            idle_timeout_seconds=args.idle_timeout_seconds,
            hard_timeout_seconds=args.hard_timeout_seconds,
            docker_image=args.docker_image,
            progress=lambda message: print(
                f"[agent-complete] {message}", file=sys.stderr
            ),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result.get("status") == "application_validation_failed" else 0
    if args.command == "agent-plan-checkpoint-sweep":
        result = plan_agent_checkpoint_sweep(
            experiment_root=Path(args.experiment_root),
            after_batch=args.after_batch,
            tasks_root=Path(args.tasks),
            base_config_path=Path(args.base_config),
            output_root=Path(args.out),
            jobs_dir=Path(args.jobs_dir),
            created_at=args.created_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "agent-record-checkpoint-sweep":
        result = record_agent_checkpoint_sweep(
            experiment_root=Path(args.experiment_root),
            sweep_root=Path(args.sweep_root),
            created_at=args.created_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "agent-status":
        print(
            json.dumps(
                agent_guided_status(Path(args.experiment_root)),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "init-human-guided":
        result = initialize_human_guided_experiment(
            source_experiment_root=Path(args.source_experiment_root),
            output_root=Path(args.output_root),
            experiment_id=args.experiment_id,
            created_at=args.created_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "human-review-next":
        result = prepare_next_human_protocol_review(
            experiment_root=Path(args.experiment_root),
            protocol_id=args.protocol_id,
            created_at=args.created_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "human-review-compile":
        compile_human_protocol_review_proposal(
            experiment_root=Path(args.experiment_root),
            draft_path=Path(args.draft),
            proposer_model=args.model,
            proposer_version=args.agent_version,
            reasoning_effort=args.reasoning_effort,
            created_at=args.created_at,
            transcript_path=Path(args.transcript) if args.transcript else None,
        )
        show_human_protocol_review_section(
            experiment_root=Path(args.experiment_root),
            section="proposal",
        )
        print(
            "Proposal compiled and hash-bound. Start Claude Code and run "
            "/review-capability to begin the section-by-section human review."
        )
        return 0
    if args.command == "human-review-show":
        show_human_protocol_review(
            experiment_root=Path(args.experiment_root),
            proposal_path=Path(args.proposal) if args.proposal else None,
        )
        return 0
    if args.command == "human-review-show-section":
        show_human_protocol_review_section(
            experiment_root=Path(args.experiment_root),
            section=args.section,
            proposal_path=Path(args.proposal) if args.proposal else None,
        )
        return 0
    if args.command == "human-review-record":
        experiment_root = Path(args.experiment_root)
        result = record_human_protocol_review_decision(
            experiment_root=experiment_root,
            proposal_path=(
                Path(args.proposal)
                if args.proposal
                else resolve_human_protocol_proposal(experiment_root=experiment_root)
            ),
            reviewer_id=args.reviewer_id,
            disposition=(
                "revision_requested"
                if args.disposition == "comment"
                else args.disposition
            ),
            rationale=args.rationale,
            revision_instruction=args.revision_instruction,
            started_at=args.started_at,
            completed_at=args.completed_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "human-build-packet":
        path, packet = build_human_guided_batch_packet(
            experiment_root=Path(args.experiment_root),
            batch_id=args.batch,
        )
        print(
            json.dumps(
                {
                    "status": "packet_ready",
                    "packet_path": path.as_posix(),
                    "packet_digest": packet["packet_digest"],
                    "admitted_root_event_count": packet["learning_ledger"][
                        "filter_summary"
                    ]["admitted_root_event_count"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "human-synthesize":
        result = run_human_guided_batch_synthesis(
            experiment_root=Path(args.experiment_root),
            batch_id=args.batch,
            workspace_root=Path(args.workspace) if args.workspace else None,
            codex_executable=args.codex,
            idle_timeout_seconds=args.idle_timeout_seconds,
            hard_timeout_seconds=args.hard_timeout_seconds,
            progress=lambda message: print(f"[human] {message}", file=sys.stderr),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "human-batch-complete":
        result = complete_human_guided_batch(
            experiment_root=Path(args.experiment_root),
            batch_id=args.batch,
            reviewer_id=args.reviewer_id,
            authorize_apply=args.authorize_apply,
            exemplar_max_results=args.exemplar_max_results,
            docker_image=args.docker_image,
            progress=lambda message: print(f"[human] {message}", file=sys.stderr),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result.get("status") == "application_validation_failed" else 0
    if args.command == "human-status":
        print(
            json.dumps(
                human_guided_status(Path(args.experiment_root)),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "human-refresh-verifier":
        result = refresh_human_protocol_verifier(
            experiment_root=Path(args.experiment_root),
            protocol_id=args.protocol_id,
            rescore_dir=Path(args.rescore_dir),
            rescore_summary_path=Path(args.rescore_summary),
            created_at=args.created_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "human-record-validation-guidance":
        path, guidance = record_human_validation_guidance(
            experiment_root=Path(args.experiment_root),
            checkpoint_label=args.checkpoint,
            codex_summary=args.codex_summary,
            human_note=args.human_note,
            reviewer_id=args.reviewer_id,
            created_at=args.created_at,
        )
        print(
            json.dumps(
                {
                    "status": "guidance_recorded",
                    "guidance_path": path.as_posix(),
                    "guidance_digest": guidance["guidance_digest"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "learn":
        result = run_local_learning(
            experiment_root=Path(args.experiment_root),
            batch_id=args.batch,
            branch=ACTIVE_BRANCH,
            c0_run_root=Path(args.c0_run_root) if args.c0_run_root else None,
            source_root=Path(args.sources_root),
            groundtruth_root=Path(args.groundtruth_root),
            run_root=Path(args.run_root) if args.run_root else None,
            parent_pack_root=None,
            round_root=Path(args.round_root) if args.round_root else None,
            workspace_root=Path(args.workspace) if args.workspace else None,
            codex_executable=args.codex,
            idle_timeout_seconds=args.idle_timeout_seconds,
            hard_timeout_seconds=args.hard_timeout_seconds,
            progress=lambda message: print(f"[learn] {message}", file=sys.stderr),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "complete":
        result = run_capability_completion(
            branch=ACTIVE_BRANCH,
            review_mode=args.review_mode,
            experiment_root=Path(args.experiment_root),
            batch_id=args.batch,
            groundtruth_root=Path(args.groundtruth_root),
            authorize_apply=args.authorize_apply,
            exemplar_max_results=args.exemplar_max_results,
            reviewer_id=args.reviewer_id,
            manual_revision=args.manual_revision,
            manual_revision_ready=args.manual_revision_ready,
            parent_pack_root=None,
            round_root=Path(args.round_root) if args.round_root else None,
            critic_workspace_root=(
                Path(args.critic_workspace) if args.critic_workspace else None
            ),
            revision_workspace_root=(
                Path(args.revision_workspace) if args.revision_workspace else None
            ),
            revised_critic_workspace_root=(
                Path(args.revised_critic_workspace)
                if args.revised_critic_workspace
                else None
            ),
            review_workspace_root=(
                Path(args.review_workspace) if args.review_workspace else None
            ),
            revised_review_workspace_root=(
                Path(args.revised_review_workspace)
                if args.revised_review_workspace
                else None
            ),
            codex_executable=args.codex,
            idle_timeout_seconds=args.idle_timeout_seconds,
            hard_timeout_seconds=args.hard_timeout_seconds,
            docker_image=args.docker_image,
            progress=lambda message: print(f"[complete] {message}", file=sys.stderr),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result["status"] == "application_validation_failed" else 0
    if args.command == "record-validation":
        output_path, aggregate = record_validation_aggregate(
            experiment_root=Path(args.experiment_root),
            checkpoint_label=args.checkpoint,
            result_root=Path(args.result_root),
            created_at=args.created_at,
        )
        print(
            json.dumps(
                {
                    "status": "validation_aggregate_recorded",
                    "checkpoint_label": aggregate["checkpoint_label"],
                    "aggregate_digest": aggregate["aggregate_digest"],
                    "pack_digest": aggregate["pack_digest"],
                    "scored_trials": aggregate["scored_trials"],
                    "macro_means": aggregate["macro_means"],
                    "aggregate_path": output_path.as_posix(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "bundle-validation-attempts":
        output_root, bundle = build_validation_result_bundle(
            canonical_config_path=Path(args.canonical_config),
            attempt_roots=[Path(item) for item in args.attempt_root],
            output_root=Path(args.output_root),
            created_at=args.created_at,
        )
        print(
            json.dumps(
                {
                    "status": "validation_result_bundle_ready",
                    "attempt_count": bundle["attempt_count"],
                    "scored_trials": bundle["n_total_trials"],
                    "bundle_digest": bundle["bundle_digest"],
                    "result_root": output_root.as_posix(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "manifest-pack":
        path = write_capability_pack_manifest(Path(args.pack))
        print(path)
        return 0
    if args.command == "validate-pack":
        manifest = validate_capability_pack(Path(args.pack))
        issues = (
            run_pack_synthetic_suite_docker(Path(args.pack), image=args.docker_image)
            if args.docker_tests
            else []
        )
        print(
            json.dumps(
                {
                    "status": "pass" if not issues else "fail",
                    "pack_digest": manifest["pack_digest"],
                    "synthetic_issues": issues,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if not issues else 1
    if args.command == "migrate-single-branch":
        result = migrate_to_cumulative_experiment(
            experiment_root=Path(args.experiment_root),
            source_root=Path(args.sources_root),
            groundtruth_root=Path(args.groundtruth_root),
            recorded_at=args.recorded_at,
            agent_version=args.agent_version,
            authorize_migration=args.authorize_migration,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "adopt-exemplar-memory":
        result = adopt_exemplar_memory(
            experiment_root=Path(args.experiment_root),
            recorded_at=args.recorded_at,
            authorize_adoption=args.authorize_adoption,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "validate-experiment":
        result = validate_experiment_manifest(
            Path(args.manifest),
            experiment_root=Path(args.experiment_root)
            if args.experiment_root
            else None,
        )
        print(
            json.dumps(
                {"status": "pass", "experiment_digest": result["experiment_digest"]},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "prepare-batch":
        experiment = validate_experiment_manifest(Path(args.experiment))
        result = build_batch_packet(
            experiment_manifest=experiment,
            branch=ACTIVE_BRANCH,
            batch_id=args.batch,
            parent_pack_digest=args.parent_pack_digest,
            reveal_state=args.reveal_state,
            artifacts=_load_array(Path(args.artifacts)),
            trial_terminality=_load_array(Path(args.terminality)),
            transfer_access_policy=_load_object(Path(args.access_policy)),
        )
        write_json_atomic(Path(args.out), result, mode=0o444)
        print(
            json.dumps(
                {"status": "pass", "packet_digest": result["packet_digest"]},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "prepare-batch-from-runs":
        experiment = validate_experiment_manifest(Path(args.experiment))
        result = build_batch_packet_from_frozen_runs(
            experiment_manifest=experiment,
            branch=ACTIVE_BRANCH,
            batch_id=args.batch,
            parent_pack_digest=args.parent_pack_digest,
            run_root=Path(args.run_root),
            c0_run_root=Path(args.c0_run_root) if args.c0_run_root else None,
            source_root=Path(args.sources_root),
            groundtruth_root=Path(args.groundtruth_root),
            transfer_access_policy=_load_object(Path(args.access_policy)),
        )
        write_json_atomic(Path(args.out), result, mode=0o444)
        print(
            json.dumps(
                {"status": "pass", "packet_digest": result["packet_digest"]},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "build-leakage-policy":
        experiment = validate_experiment_manifest(Path(args.experiment))
        result = build_cumulative_leakage_policy(
            experiment_manifest=experiment,
            private_groundtruth_root=Path(args.groundtruth_root),
            through_batch=args.through_batch,
        )
        write_json_atomic(Path(args.out), result, mode=0o400)
        print(
            json.dumps(
                {"status": "pass", "policy_digest": result["policy_digest"]},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "validate-proposal":
        experiment = validate_experiment_manifest(Path(args.experiment))
        result = validate_capability_proposal(
            experiment_manifest=experiment,
            access_policy_path=Path(args.access_policy),
            proposal_path=Path(args.proposal),
            candidate_root=Path(args.candidates),
            parent_pack_root=Path(args.parent_pack),
            packet_path=Path(args.packet) if args.packet else None,
            prior_proposal_path=(
                Path(args.prior_proposal) if args.prior_proposal else None
            ),
            revision_decision_path=(
                Path(args.revision_decision) if args.revision_decision else None
            ),
        )
        print(
            json.dumps(
                {"status": "pass", "proposal_digest": result["proposal_digest"]},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "compile-proposal":
        experiment = validate_experiment_manifest(Path(args.experiment))
        result = compile_capability_proposal_draft(
            experiment_manifest=experiment,
            access_policy_path=Path(args.access_policy),
            packet_path=Path(args.packet),
            parent_pack_root=Path(args.parent_pack),
            candidate_root=Path(args.candidates),
            draft_path=Path(args.draft),
            transcript_path=Path(args.transcript),
            output_path=Path(args.out),
            prior_proposal_path=(
                Path(args.prior_proposal) if args.prior_proposal else None
            ),
            revision_decision_path=(
                Path(args.revision_decision) if args.revision_decision else None
            ),
        )
        print(
            json.dumps(
                {"status": "pass", "proposal_digest": result["proposal_digest"]},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "compile-independent-review":
        result = compile_independent_decision_draft(
            proposal_path=Path(args.proposal),
            draft_path=Path(args.draft),
            transcript_path=Path(args.transcript),
            output_path=Path(args.out),
            started_at=args.started_at,
            completed_at=args.completed_at,
            experiment_root=(
                Path(args.experiment_root) if args.experiment_root else None
            ),
        )
        summary_path, summary = write_or_validate_capability_review_summary(
            proposal_path=Path(args.proposal),
            decision_path=Path(args.out),
        )
        print(
            render_capability_review_summary(
                summary,
                summary_path=summary_path,
            ),
            file=sys.stderr,
            end="",
        )
        print(
            json.dumps(
                {
                    "status": result["review_state"],
                    "decision_digest": result["decision_digest"],
                    "review_summary_path": summary_path.as_posix(),
                    "review_summary_digest": summary["summary_digest"],
                    "review_counts": summary["counts"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "render-review":
        proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
        print(render_capability_review(proposal), end="")
        return 0
    if args.command == "review-start":
        result = create_decision_template(
            proposal_path=Path(args.proposal),
            reviewer_kind=args.reviewer_kind,
            reviewer_id=args.reviewer_id,
            started_at=args.started_at,
            reviewer_model=args.reviewer_model,
            reviewer_version=args.reviewer_version,
            transcript_sha256=args.transcript_sha256,
        )
        write_json_atomic(Path(args.out), result)
        print(
            render_capability_review(
                json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            ),
            end="",
        )
        return 0
    if args.command == "review-decide":
        result = record_change_decision(
            proposal_path=Path(args.proposal),
            decision_path=Path(args.decision),
            change_id=args.change_id,
            disposition=args.disposition,
            rationale=args.rationale,
            revision_instruction=args.revision_instruction,
        )
        print(
            json.dumps(
                {
                    "status": "checkpointed",
                    "decision_digest": result["decision_digest"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "review-finalize":
        result = finalize_capability_decision(
            proposal_path=Path(args.proposal),
            decision_path=Path(args.decision),
            completed_at=args.completed_at,
        )
        summary_path, summary = write_or_validate_capability_review_summary(
            proposal_path=Path(args.proposal),
            decision_path=Path(args.decision),
        )
        print(
            render_capability_review_summary(
                summary,
                summary_path=summary_path,
            ),
            file=sys.stderr,
            end="",
        )
        print(
            json.dumps(
                {
                    "status": result["review_state"],
                    "decision_digest": result["decision_digest"],
                    "review_summary_path": summary_path.as_posix(),
                    "review_summary_digest": summary["summary_digest"],
                    "review_counts": summary["counts"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "review-interactive":
        result = run_human_review_console(
            proposal_path=Path(args.proposal),
            decision_path=Path(args.decision),
            reviewer_id=args.reviewer_id,
            parent_pack_root=Path(args.parent_pack),
            candidate_root=Path(args.candidates),
            started_at=args.started_at,
            completed_at=args.completed_at,
        )
        summary_fields: dict[str, Any] = {}
        if result["review_state"] in {"final", "revision_requested"}:
            summary_path, summary = write_or_validate_capability_review_summary(
                proposal_path=Path(args.proposal),
                decision_path=Path(args.decision),
            )
            summary_fields = {
                "review_summary_path": summary_path.as_posix(),
                "review_summary_digest": summary["summary_digest"],
                "review_counts": summary["counts"],
            }
        print(
            json.dumps(
                {
                    "status": result["review_state"],
                    "decision_digest": result["decision_digest"],
                    **summary_fields,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "validate-decision":
        _, result = validate_capability_decision(
            proposal_path=Path(args.proposal),
            decision_path=Path(args.decision),
            require_final=not args.allow_in_progress,
        )
        print(
            json.dumps(
                {
                    "status": "pass",
                    "review_state": result["review_state"],
                    "decision_digest": result["decision_digest"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "apply":
        experiment_path = Path(args.experiment).expanduser().resolve()
        experiment = validate_experiment_manifest(experiment_path)
        leakage = validate_cumulative_leakage_policy(Path(args.leakage_policy))
        result = apply_capability_decision(
            experiment_root=experiment_path.parent.parent,
            experiment_manifest=experiment,
            access_policy_path=Path(args.access_policy),
            proposal_path=Path(args.proposal),
            decision_path=Path(args.decision),
            candidate_root=Path(args.candidates),
            parent_pack_root=Path(args.parent_pack),
            output_dir=Path(args.out),
            created_at=args.created_at,
            packet_path=Path(args.packet) if args.packet else None,
            prior_proposal_path=(
                Path(args.prior_proposal) if args.prior_proposal else None
            ),
            revision_decision_path=(
                Path(args.revision_decision) if args.revision_decision else None
            ),
            leakage_policy=leakage,
            synthetic_runner=lambda root: run_pack_synthetic_suite_docker(
                root, image=args.docker_image
            ),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] != "no_op_validation_failed" else 1
    if args.command == "freeze-checkpoint":
        experiment_path = Path(args.experiment).expanduser().resolve()
        experiment = validate_experiment_manifest(experiment_path)
        result = freeze_checkpoint(
            experiment_root=experiment_path.parent.parent,
            experiment_digest=experiment["experiment_digest"],
            branch=ACTIVE_BRANCH,
            batch_id=args.batch,
            protocol_count=args.protocol_count,
            parent_checkpoint_id=args.parent_checkpoint,
            proposal_path=Path(args.proposal),
            decision_path=Path(args.decision),
            application_dir=Path(args.application),
            output_dir=Path(args.out),
            created_at=args.created_at,
            exemplar_max_results=args.exemplar_max_results,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "lock-final":
        experiment_path = Path(args.experiment).expanduser().resolve()
        experiment_root = experiment_path.parent.parent
        with experiment_mutation_lock(
            experiment_root,
            operation="freeze final capability-development lock",
        ):
            assert_no_interrupted_split_freeze(experiment_root)
            output_path = Path(args.out).expanduser().resolve()
            expected_output = experiment_root / "design" / "final_lock.json"
            if output_path != expected_output:
                raise CapabilityImprovementError(
                    f"final lock must use the canonical path: {expected_output}"
                )
            if output_path.exists():
                raise CapabilityImprovementError(
                    f"refusing to overwrite final lock: {output_path}"
                )
            experiment = validate_experiment_manifest(
                experiment_path,
                experiment_root=experiment_root,
            )
            result = build_final_lock(
                experiment_manifest=experiment,
                checkpoint_paths=[Path(item) for item in args.checkpoint],
                created_at=args.created_at,
            )
            write_json_atomic(output_path, result, mode=0o444)
            try:
                result = validate_final_lock(
                    output_path,
                    experiment_root=experiment_root,
                    experiment_manifest=experiment,
                )
            except BaseException:
                output_path.unlink(missing_ok=True)
                raise
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "validate-final-lock":
        experiment_path = Path(args.experiment).expanduser().resolve()
        experiment_root = experiment_path.parent.parent
        experiment = validate_experiment_manifest(
            experiment_path,
            experiment_root=experiment_root,
        )
        result = validate_final_lock(
            Path(args.lock),
            experiment_root=experiment_root,
            experiment_manifest=experiment,
        )
        print(
            json.dumps(
                {"status": "pass", "lock_digest": result["lock_digest"]},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "authorize-transfer-panel":
        experiment_path = Path(args.experiment).expanduser().resolve()
        experiment_root = experiment_path.parent.parent
        with experiment_mutation_lock(
            experiment_root,
            operation="authorize frozen final-test panel",
        ):
            assert_no_interrupted_split_freeze(experiment_root)
            output_path = Path(args.out).expanduser().resolve()
            expected_output = (
                experiment_root / "design" / "transfer_panel_authorization.json"
            )
            if output_path != expected_output:
                raise CapabilityImprovementError(
                    "transfer-panel authorization must use the canonical path: "
                    f"{expected_output}"
                )
            if output_path.exists():
                raise CapabilityImprovementError(
                    f"refusing to overwrite transfer-panel authorization: {output_path}"
                )
            experiment = validate_experiment_manifest(
                experiment_path,
                experiment_root=experiment_root,
            )
            result = build_transfer_panel_authorization(
                experiment_root=experiment_root,
                experiment_manifest=experiment,
                final_lock_path=Path(args.lock),
                authorized_by=args.authorized_by,
                authorized_at=args.authorized_at,
            )
            write_json_atomic(output_path, result, mode=0o444)
            try:
                result = validate_transfer_panel_authorization(
                    output_path,
                    experiment_root=experiment_root,
                    experiment_manifest=experiment,
                    final_lock=validate_final_lock(
                        Path(args.lock),
                        experiment_root=experiment_root,
                        experiment_manifest=experiment,
                    ),
                )
            except BaseException:
                output_path.unlink(missing_ok=True)
                raise
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "prepare-harbor":
        integration = prepare_capability_harbor_integration(
            pack_root=Path(args.pack),
            tasks_root=Path(args.tasks),
            protocol_ids=args.protocol_id,
            output_root=Path(args.out),
            created_at=args.created_at,
        )
        if args.base_config or args.job_config_out or args.job_name or args.jobs_dir:
            if not all(
                (args.base_config, args.job_config_out, args.job_name, args.jobs_dir)
            ):
                raise CapabilityImprovementError(
                    "all Harbor job-config options must be supplied together"
                )
            build_harbor_job_config(
                base_config_path=Path(args.base_config),
                integration_root=Path(args.out),
                tasks_root=Path(args.tasks),
                protocol_ids=args.protocol_id,
                job_name=args.job_name,
                jobs_dir=Path(args.jobs_dir),
                output_path=Path(args.job_config_out),
            )
        print(json.dumps(integration, indent=2, sort_keys=True))
        return 0
    if args.command == "plan-final-replay":
        experiment_path = Path(args.experiment).expanduser().resolve()
        experiment_root = experiment_path.parent.parent
        experiment = validate_experiment_manifest(
            experiment_path,
            experiment_root=experiment_root,
        )
        result = build_final_replay_plan(
            experiment_root=experiment_root,
            experiment_manifest=experiment,
            final_lock_path=Path(args.lock),
            transfer_panel_authorization_path=Path(args.transfer_authorization),
            checkpoint_pack_roots=_pack_map(args.pack) if args.pack else None,
            tasks_root=Path(args.tasks),
            base_config_path=Path(args.base_config),
            output_root=(
                Path(args.out)
                if args.out
                else experiment_root / "final" / "fixed-panel-replay"
            ),
            jobs_dir=Path(args.jobs_dir),
            created_at=args.created_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "plan-prospective-round":
        experiment = validate_experiment_manifest(Path(args.experiment))
        result = build_prospective_round_plan(
            experiment_manifest=experiment,
            batch_id=args.batch,
            checkpoint_pack_roots=_pack_map(args.pack),
            tasks_root=Path(args.tasks),
            base_config_path=Path(args.base_config),
            output_root=Path(args.out),
            jobs_dir=Path(args.jobs_dir),
            created_at=args.created_at,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "prepare-isolated-workspace":
        experiment = validate_experiment_manifest(Path(args.experiment))
        result = prepare_isolated_worker_workspace(
            experiment_manifest=experiment,
            packet_path=Path(args.packet),
            parent_pack_root=Path(args.parent_pack),
            access_policy_path=Path(args.access_policy),
            output_root=Path(args.out),
            mode=args.mode,
            proposal_path=Path(args.proposal) if args.proposal else None,
            candidate_root=Path(args.candidates) if args.candidates else None,
            decision_path=Path(args.decision) if args.decision else None,
            prior_proposal_path=(
                Path(args.prior_proposal) if args.prior_proposal else None
            ),
            revision_decision_path=(
                Path(args.revision_decision) if args.revision_decision else None
            ),
            validation_feedback_path=(
                Path(args.validation_feedback) if args.validation_feedback else None
            ),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "validate-isolated-workspace":
        result = validate_isolated_worker_workspace(Path(args.workspace))
        print(
            json.dumps(
                {"status": "pass", "workspace_digest": result["workspace_digest"]},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "report-final":
        result = build_final_evaluation_report(
            replay_manifest_path=Path(args.manifest),
            result_roots=_pack_map(args.result) if args.result else None,
            created_at=args.created_at,
        )
        write_json_atomic(Path(args.out), result, mode=0o444)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    raise AssertionError(args.command)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CapabilityImprovementError(f"expected JSON object: {path}")
    return value


def _load_array(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CapabilityImprovementError(f"expected JSON array of objects: {path}")
    return value


def _pack_map(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise CapabilityImprovementError(
                "path mapping must use LABEL=/absolute/path"
            )
        label, path = value.split("=", 1)
        if label in result:
            raise CapabilityImprovementError(f"duplicate pack label: {label}")
        result[label] = Path(path)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
