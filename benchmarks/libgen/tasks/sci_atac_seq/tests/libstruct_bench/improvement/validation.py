from __future__ import annotations

import json
import math
import re
import shutil
import statistics
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree

from libstruct_bench.audit.artifacts import sha256_file, validate_document

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    improvement_schema_root,
    load_and_validate,
    normalized_timestamp,
    safe_relative_path,
    validate_capability_pack,
    validate_digest,
    with_digest,
)
from .governance import tree_digest
from .mutation_lock import guard_experiment_mutation
from .split_design import (
    CUMULATIVE_CHECKPOINT_LABELS,
    FINAL_DEVELOPMENT_BATCHES,
    FIXED_VALIDATION_PANEL,
)


VALIDATION_ACCESS_POLICY_SCHEMA_VERSION = "libstruct.libgen_validation_access_policy.v1"
VALIDATION_PLAN_SCHEMA_VERSION = "libstruct.libgen_validation_plan.v1"
VALIDATION_AGGREGATE_SCHEMA_VERSION = "libstruct.libgen_validation_aggregate.v1"
VALIDATION_RESULT_BUNDLE_SCHEMA_VERSION = "libstruct.libgen_validation_result_bundle.v1"
VALIDATION_ISOLATION_AUDIT_SCHEMA_VERSION = (
    "libstruct.libgen_validation_isolation_audit.v1"
)
VALIDATION_SET_ID = "fixed-validation-panel-v1"
VALIDATION_LEARNING_VISIBILITY = "five_protocol_macro_aggregate_only_no_example_memory"
VALIDATION_PROTOCOL_IDS = tuple(FIXED_VALIDATION_PANEL)
VALIDATION_CHECKPOINT_LABELS = tuple(CUMULATIVE_CHECKPOINT_LABELS)
VALIDATION_METRICS = (
    "reward",
    "t2_exact_required_family_recall",
    "t2_required_family_f1",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)
VALIDATION_BLOCKED_ARTIFACT_ROLES = (
    "target_source",
    "approved_groundtruth",
    "solved_t2",
    "solved_t3",
    "trial_result",
    "prediction",
    "trajectory",
    "verifier_reward",
    "verifier_details",
    "verifier_error_analysis",
    "verifier_error",
)
VALIDATION_BLOCKED_CONTEXTS = (
    "learning_packet",
    "learning_ledger",
    "capability_proposal",
    "capability_candidate",
    "synthetic_fixture",
    "capability_pack",
    "cumulative_memory",
    "independent_critic",
    "human_review_console",
)
VALIDATION_AGGREGATE_FIELDS = (
    "schema_version",
    "experiment_digest",
    "checkpoint_label",
    "checkpoint_digest",
    "checkpoint_sha256",
    "pack_digest",
    "pack_manifest_sha256",
    "runtime_digest",
    "runtime_sha256",
    "integration_digest",
    "integration_manifest_sha256",
    "task_bundle_sha256",
    "harbor_config_sha256",
    "result_bundle_digest",
    "diagnostic_bundle_digest",
    "result_file_count",
    "validation_panel_commitment_sha256",
    "access_policy_digest",
    "planned_trials",
    "scored_trials",
    "unscored_trials",
    "metric_counts",
    "macro_means",
    "feedback_scope",
    "guidance_target_batch",
    "created_at",
    "aggregate_digest",
)
VALIDATION_FEEDBACK_SCHEMA_VERSION = "libstruct.libgen_validation_feedback.v1"
VALIDATION_FEEDBACK_FIELDS = (
    "schema_version",
    "checkpoint_label",
    "planned_trials",
    "scored_trials",
    "unscored_trials",
    "metric_counts",
    "macro_means",
    "feedback_scope",
    "guidance_target_batch",
    "aggregate_digest",
)
VALIDATION_PROHIBITED_FEEDBACK_FIELDS = (
    "protocol_ids",
    "protocol_results",
    "trial_results",
    "result_path",
    "result_root",
    "prediction",
    "trajectory",
    "groundtruth",
    "t2",
    "t3",
    "sequence",
    "sequences",
    "observations",
    "error_analysis",
    "error_categories",
    "error_specific_answers",
    "source_locators",
    "raw_artifacts",
)
VALIDATION_BATCH_GATE = {
    "B1": "C0",
    "B2": "C5",
    "B3": "C10",
    "B4": "C15",
    "B5": "C20",
}
VALIDATION_GUIDANCE_TARGET = {
    "C0": "B1",
    "C5": "B2",
    "C10": "B3",
    "C15": "B4",
    "C20": "B5",
    "C25": None,
}
VALIDATION_ACTIVE_LEARNING_ROOTS = (
    "packs",
    "checkpoints",
    "rounds",
    "final",
    "validation",
)
VALIDATION_ISOLATION_CATEGORIES = (
    "validation_protocol_identifier",
    "validation_artifact_copy",
    "validation_exact_sequence",
    "validation_scaffold",
    "validation_raw_result_or_error_detail",
)
VALIDATION_RUNNER = {
    "harness": "harbor",
    "environment": "docker",
    "agent": "codex",
    "model": "gpt-5.6-sol",
    "agent_version": "0.147.0",
    "reasoning_effort": "max",
    "concurrency": 4,
    "semantic_retries": 0,
}

_TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".csv",
        ".htm",
        ".html",
        ".ini",
        ".json",
        ".jsonl",
        ".log",
        ".md",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".tsv",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_NATIVE_DOCUMENT_SUFFIXES = frozenset({".pdf", ".xlsx", ".docx"})
_NUCLEOTIDE_RUN_RE = re.compile(r"(?<![A-Za-z])[ACGTRYSWKMBDHVN]{8,}(?![A-Za-z])", re.I)
_RAW_DETAIL_TOKENS = (
    "error_analysis",
    "verifier_details",
    "verifier_error",
    "prediction",
    "trajectory",
)


def validation_panel_commitment_payload() -> dict[str, Any]:
    return {
        "set_id": VALIDATION_SET_ID,
        "protocol_count": 5,
        "protocol_ids": list(VALIDATION_PROTOCOL_IDS),
        "evaluation_checkpoints": list(VALIDATION_CHECKPOINT_LABELS),
        "learning_visibility": VALIDATION_LEARNING_VISIBILITY,
    }


def validation_panel_commitment_digest() -> str:
    return canonical_digest(validation_panel_commitment_payload())


def build_validation_access_policy(
    *,
    validation_panel_commitment_sha256: str,
    source_root: Path,
    groundtruth_root: Path,
    initial_pack_root: Path,
    created_at: str,
) -> dict[str, Any]:
    """Build the private denylist and aggregate-only validation contract."""

    if validation_panel_commitment_sha256 != validation_panel_commitment_digest():
        raise CapabilityImprovementError(
            "validation access policy has another panel commitment"
        )
    sources = source_root.expanduser().resolve()
    truth = groundtruth_root.expanduser().resolve()
    initial_pack_path = initial_pack_root.expanduser().resolve()
    initial_pack = validate_capability_pack(initial_pack_path)
    protected_trees: list[dict[str, Any]] = []
    protected_files: list[dict[str, str]] = []
    exact_protocol_terms = {item.lower() for item in VALIDATION_PROTOCOL_IDS}
    terms = set(exact_protocol_terms)
    validation_sequences: set[str] = set()
    validation_scaffolds: set[str] = set()
    for protocol_id in VALIDATION_PROTOCOL_IDS:
        for role, root in (
            ("target_source", sources / protocol_id),
            ("approved_groundtruth", truth / protocol_id),
        ):
            digest, count = tree_digest(root)
            protected_trees.append(
                {
                    "protocol_id": protocol_id,
                    "role": role,
                    "path": root.as_posix(),
                    "file_count": count,
                    "tree_digest": digest,
                }
            )
            for path in _regular_tree_files(root):
                protected_files.append(
                    {
                        "protocol_id": protocol_id,
                        "role": role,
                        "relative_path": path.relative_to(root).as_posix(),
                        "sha256": sha256_file(path),
                    }
                )
                _collect_sensitive_text(
                    path,
                    terms=terms,
                    sequences=validation_sequences,
                    scaffolds=validation_scaffolds,
                    include_names=role == "approved_groundtruth",
                )
    training_protocols = tuple(
        protocol_id
        for batch in FINAL_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    )
    allowed_sequences: set[str] = set()
    allowed_scaffolds: set[str] = set()
    allowed_terms: set[str] = set()
    training_tree_fingerprints: list[dict[str, Any]] = []
    ignored_terms: set[str] = set()
    validation_aliases = terms - exact_protocol_terms
    for protocol_id in training_protocols:
        for role, root in (
            ("target_source", sources / protocol_id),
            ("approved_groundtruth", truth / protocol_id),
        ):
            digest, count = tree_digest(root)
            training_tree_fingerprints.append(
                {
                    "protocol_id": protocol_id,
                    "role": role,
                    "file_count": count,
                    "tree_digest": digest,
                }
            )
            for path in _regular_tree_files(root):
                training_text = _collect_sensitive_text(
                    path,
                    terms=ignored_terms,
                    sequences=allowed_sequences,
                    scaffolds=allowed_scaffolds,
                    include_names=role == "approved_groundtruth",
                )
                if training_text is not None:
                    allowed_terms.update(
                        term
                        for term in validation_aliases
                        if _contains_term(training_text, term)
                    )
    for path in _regular_tree_files(initial_pack_path):
        if path.name == "manifest.json":
            continue
        pack_text = _collect_sensitive_text(
            path,
            terms=ignored_terms,
            sequences=allowed_sequences,
            scaffolds=allowed_scaffolds,
            include_names=True,
        )
        if pack_text is not None:
            allowed_terms.update(
                term for term in validation_aliases if _contains_term(pack_text, term)
            )
    terms = exact_protocol_terms | (validation_aliases - allowed_terms)
    sequences = {
        item
        for item in validation_sequences
        if not _sequence_equals_allowlist(item, allowed_sequences)
    }
    scaffolds = {
        item
        for item in validation_scaffolds
        if not _scaffold_equals_allowlist(item, allowed_scaffolds)
    }
    payload: dict[str, Any] = {
        "schema_version": VALIDATION_ACCESS_POLICY_SCHEMA_VERSION,
        "policy_id": "fixed-validation-aggregate-only-access-v1",
        "created_at": normalized_timestamp(created_at),
        "validation_set": validation_panel_commitment_payload(),
        "validation_panel_commitment_sha256": (validation_panel_commitment_sha256),
        "protected_trees": sorted(
            protected_trees,
            key=lambda item: (item["role"], item["protocol_id"]),
        ),
        "protected_files": sorted(
            protected_files,
            key=lambda item: (
                item["role"],
                item["protocol_id"],
                item["relative_path"],
            ),
        ),
        "blocked_artifact_roles": list(VALIDATION_BLOCKED_ARTIFACT_ROLES),
        "blocked_learning_contexts": list(VALIDATION_BLOCKED_CONTEXTS),
        "forbidden_terms": sorted(terms),
        "forbidden_sequences": sorted(sequences),
        "forbidden_scaffolds": sorted(scaffolds),
        "allowlist_provenance": {
            "training_protocol_ids": list(training_protocols),
            "training_tree_fingerprints": sorted(
                training_tree_fingerprints,
                key=lambda item: (item["role"], item["protocol_id"]),
            ),
            "initial_pack_digest": initial_pack["pack_digest"],
            "validation_alias_count_before_filter": len(validation_aliases),
            "shared_alias_count_removed": len(validation_aliases & allowed_terms),
            "validation_sequence_count_before_filter": len(validation_sequences),
            "shared_sequence_count_removed": len(validation_sequences) - len(sequences),
            "validation_scaffold_count_before_filter": len(validation_scaffolds),
            "shared_scaffold_count_removed": len(validation_scaffolds) - len(scaffolds),
            "filtering_policy": (
                "always_block_exact_validation_ids_and_block_only_validation_"
                "aliases_sequences_and_scaffolds_not_independently_present_in_"
                "training_sources_training_groundtruth_or_C0"
            ),
        },
        "sensitive_extraction": {
            "formats": [
                "utf8_text",
                "pdf_native_text_no_ocr",
                "xlsx_cell_values",
                "docx_xml_text",
            ],
            "unreadable_supported_document_policy": "fail_closed",
        },
        "evaluation_access": {
            "agent": "target_sources_and_read_only_capability_pack_only",
            "verifier": "approved_groundtruth_in_separate_environment_only",
            "orchestrator": "raw_results_private_deterministic_aggregation_only",
            "improvement": VALIDATION_LEARNING_VISIBILITY,
        },
        "aggregate_feedback_contract": {
            "allowed_fields": list(VALIDATION_FEEDBACK_FIELDS),
            "prohibited_fields": list(VALIDATION_PROHIBITED_FEEDBACK_FIELDS),
            "may_guide_next_update": True,
            "may_support_change_evidence": False,
            "may_enter_cumulative_memory": False,
            "may_enter_synthetic_fixtures": False,
            "may_enter_capability_pack": False,
        },
        "agent_visibility": "none_orchestrator_only_except_sanitized_aggregate",
    }
    result = with_digest(payload, "policy_digest")
    validate_document(
        result,
        improvement_schema_root() / "validation_access_policy.schema.json",
        label="validation access policy",
    )
    _validate_validation_policy_semantics(result, verify_trees=True)
    return result


def validate_validation_access_policy(
    path: Path,
    *,
    verify_trees: bool = True,
) -> dict[str, Any]:
    result = load_and_validate(
        path,
        schema_filename="validation_access_policy.schema.json",
        digest_field="policy_digest",
        label="validation access policy",
    )
    _validate_validation_policy_semantics(result, verify_trees=verify_trees)
    return result


def validate_referenced_validation_access_policy(
    *,
    experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve and verify the manifest-pinned validation policy."""

    root = experiment_root.expanduser().resolve()
    reference = experiment_manifest["validation_panel"]["access_policy"]
    path = (root / reference["path"]).resolve()
    if root not in path.parents:
        raise CapabilityImprovementError(
            "validation access-policy path escapes the experiment root"
        )
    policy = validate_validation_access_policy(path)
    if (
        policy["policy_digest"] != reference["digest"]
        or sha256_file(path) != reference["sha256"]
    ):
        raise CapabilityImprovementError("validation access-policy reference is stale")
    return policy


def build_validation_plan(
    *,
    experiment_digest: str,
    validation_access_policy: Mapping[str, Any],
    task_bundle_sha256: str,
    created_at: str,
    runner: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_digest(validation_access_policy, "policy_digest")
    _validate_validation_policy_semantics(validation_access_policy, verify_trees=False)
    selected_runner = dict(runner or VALIDATION_RUNNER)
    if selected_runner != VALIDATION_RUNNER:
        raise CapabilityImprovementError(
            "validation runner differs from the fixed Harbor/Codex anchor"
        )
    runner_record = {
        **selected_runner,
        "configuration_digest": canonical_digest(selected_runner),
    }
    conditions = []
    for order, label in enumerate(VALIDATION_CHECKPOINT_LABELS, start=1):
        count = int(label[1:])
        conditions.append(
            {
                "order": order,
                "checkpoint_label": label,
                "trained_protocol_count": count,
                "guidance_target_batch": VALIDATION_GUIDANCE_TARGET[label],
                "expected_trial_count": 5,
                "aggregate_path": f"validation/aggregates/{label}.json",
            }
        )
    payload: dict[str, Any] = {
        "schema_version": VALIDATION_PLAN_SCHEMA_VERSION,
        "plan_id": "fixed-validation-learning-curve-v1",
        "experiment_digest": experiment_digest,
        "validation_panel_commitment_sha256": validation_access_policy[
            "validation_panel_commitment_sha256"
        ],
        "access_policy_digest": validation_access_policy["policy_digest"],
        "task_bundle_sha256": task_bundle_sha256,
        "runner": runner_record,
        "checkpoint_labels": list(VALIDATION_CHECKPOINT_LABELS),
        "conditions": conditions,
        "expected_job_count": 6,
        "trials_per_checkpoint": 5,
        "expected_trial_count": 30,
        "feedback_policy": (
            "macro_aggregate_may_guide_next_update_but_validation_examples_"
            "never_enter_memory_fixtures_or_pack"
        ),
        "created_at": normalized_timestamp(created_at),
    }
    result = with_digest(payload, "plan_digest")
    validate_document(
        result,
        improvement_schema_root() / "validation_plan.schema.json",
        label="validation evaluation plan",
    )
    _validate_validation_plan_semantics(result)
    return result


def validate_validation_plan(
    path: Path,
    *,
    validation_access_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = load_and_validate(
        path,
        schema_filename="validation_plan.schema.json",
        digest_field="plan_digest",
        label="validation evaluation plan",
    )
    _validate_validation_plan_semantics(result)
    if validation_access_policy is not None and (
        result["access_policy_digest"] != validation_access_policy["policy_digest"]
        or result["validation_panel_commitment_sha256"]
        != validation_access_policy["validation_panel_commitment_sha256"]
    ):
        raise CapabilityImprovementError(
            "validation plan references another access policy"
        )
    return result


def build_validation_aggregate(
    *,
    experiment_digest: str,
    checkpoint_label: str,
    checkpoint_root: Path,
    validation_access_policy: Mapping[str, Any],
    result_root: Path,
    created_at: str,
) -> dict[str, Any]:
    """Reduce one checkpoint-bound five-trial run to a macro-only summary."""

    _validate_validation_policy_semantics(validation_access_policy, verify_trees=False)
    if checkpoint_label not in VALIDATION_CHECKPOINT_LABELS:
        raise CapabilityImprovementError(
            f"unknown validation checkpoint: {checkpoint_label}"
        )
    from .workflow import validate_checkpoint_runtime

    checkpoint_path = checkpoint_root.expanduser().resolve()
    checkpoint, runtime, pack = validate_checkpoint_runtime(checkpoint_path)
    if checkpoint["checkpoint_id"] != checkpoint_label:
        raise CapabilityImprovementError(
            "validation result is bound to another checkpoint"
        )
    if checkpoint["experiment_digest"] != experiment_digest:
        raise CapabilityImprovementError(
            "validation checkpoint belongs to another experiment"
        )
    root = result_root.expanduser().resolve()
    result_path = root / "result.json"
    config_path = root / "config.json"
    if not result_path.is_file() or not config_path.is_file():
        raise CapabilityImprovementError(
            f"validation Harbor result is incomplete: {root}"
        )
    config = _load_json_object(config_path, "validation Harbor config")
    run_binding = _validate_validation_harbor_config(
        config,
        config_path=config_path,
        checkpoint_root=checkpoint_path,
        checkpoint=checkpoint,
        runtime=runtime,
        pack=pack,
    )
    job = _load_json_object(result_path, "validation Harbor result")
    if job.get("schema_version") == VALIDATION_RESULT_BUNDLE_SCHEMA_VERSION:
        validate_document(
            job,
            improvement_schema_root() / "validation_result_bundle.schema.json",
            label="validation result bundle",
        )
        validate_digest(job, "bundle_digest")
        if job.get("attempt_count") != len(job.get("attempts", ())) or [
            item.get("protocol_id") for item in job.get("selections", ())
        ] != list(VALIDATION_PROTOCOL_IDS):
            raise CapabilityImprovementError(
                "validation result bundle provenance is inconsistent"
            )
    if job.get("n_total_trials") != 5:
        raise CapabilityImprovementError(
            "validation Harbor result must plan exactly five trials"
        )
    results: dict[str, dict[str, Any]] = {}
    trial_result_hashes: list[str] = []
    diagnostic_artifact_hashes: list[str] = []
    for path in sorted(root.glob("*/result.json")):
        trial = _load_json_object(path, "validation Harbor trial")
        task_path = (trial.get("task_id") or {}).get("path")
        protocol_id = Path(task_path).name if isinstance(task_path, str) else None
        if protocol_id not in VALIDATION_PROTOCOL_IDS or protocol_id in results:
            raise CapabilityImprovementError(
                f"invalid or duplicate validation trial: {path}"
            )
        rewards = (trial.get("verifier_result") or {}).get("rewards") or {}
        metrics: dict[str, float] = {}
        for metric in VALIDATION_METRICS:
            raw_value = rewards.get(metric)
            if (
                isinstance(raw_value, bool)
                or not isinstance(raw_value, (int, float))
                or not math.isfinite(float(raw_value))
                or not 0.0 <= float(raw_value) <= 1.0
            ):
                raise CapabilityImprovementError(
                    "validation gate requires every metric to be a finite score "
                    f"in [0,1]: {path} / {metric}"
                )
            metrics[metric] = float(raw_value)
        results[protocol_id] = {
            "metrics": metrics,
            "scored": True,
        }
        trial_result_hashes.append(sha256_file(path))
        diagnostics = _validation_trial_diagnostics(path.parent, checkpoint=checkpoint)
        diagnostic_artifact_hashes.extend(
            (
                diagnostics["exemplar_usage_sha256"],
                diagnostics["target_evidence_guard_sha256"],
            )
        )
    if set(results) != set(VALIDATION_PROTOCOL_IDS) or len(results) != 5:
        raise CapabilityImprovementError(
            "validation Harbor trial membership differs from the fixed panel"
        )
    rows = list(results.values())
    metric_counts = {
        metric: sum(item["metrics"][metric] is not None for item in rows)
        for metric in VALIDATION_METRICS
    }
    macro_means = {
        metric: statistics.fmean(item["metrics"][metric] for item in rows)
        for metric in VALIDATION_METRICS
    }
    result_bundle_digest = canonical_digest(
        {
            "job_result_sha256": sha256_file(result_path),
            "trial_result_sha256s": sorted(trial_result_hashes),
        }
    )
    diagnostic_bundle_digest = canonical_digest(
        {"diagnostic_artifact_sha256s": sorted(diagnostic_artifact_hashes)}
    )
    payload: dict[str, Any] = {
        "schema_version": VALIDATION_AGGREGATE_SCHEMA_VERSION,
        "experiment_digest": experiment_digest,
        "checkpoint_label": checkpoint_label,
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "checkpoint_sha256": sha256_file(checkpoint_path / "checkpoint.json"),
        "pack_digest": pack["pack_digest"],
        "pack_manifest_sha256": checkpoint["pack_manifest_sha256"],
        "runtime_digest": runtime["runtime_digest"],
        "runtime_sha256": sha256_file(checkpoint_path / "runtime.json"),
        **run_binding,
        "result_bundle_digest": result_bundle_digest,
        "diagnostic_bundle_digest": diagnostic_bundle_digest,
        "result_file_count": 6,
        "validation_panel_commitment_sha256": validation_access_policy[
            "validation_panel_commitment_sha256"
        ],
        "access_policy_digest": validation_access_policy["policy_digest"],
        "planned_trials": 5,
        "scored_trials": 5,
        "unscored_trials": 0,
        "metric_counts": metric_counts,
        "macro_means": macro_means,
        "feedback_scope": VALIDATION_LEARNING_VISIBILITY,
        "guidance_target_batch": VALIDATION_GUIDANCE_TARGET[checkpoint_label],
        "created_at": normalized_timestamp(created_at),
    }
    result = with_digest(payload, "aggregate_digest")
    validate_document(
        result,
        improvement_schema_root() / "validation_aggregate.schema.json",
        label="validation aggregate",
    )
    _validate_validation_aggregate_semantics(result)
    return result


def build_validation_result_bundle(
    *,
    canonical_config_path: Path,
    attempt_roots: Sequence[Path],
    output_root: Path,
    created_at: str,
) -> tuple[Path, dict[str, Any]]:
    """Consolidate interrupted Harbor attempts without rerunning clean trials.

    The output keeps the canonical five-protocol config and one byte-identical
    successful ``result.json`` per protocol.  Its top-level ``result.json`` is
    a deterministic provenance manifest, so the existing aggregate recorder
    can consume the bundle while binding every selected row to its source
    attempt hashes.
    """

    if not attempt_roots:
        raise CapabilityImprovementError(
            "validation result bundling requires at least one Harbor attempt"
        )
    raw_config_path = canonical_config_path.expanduser()
    _assert_no_symlink_chain(raw_config_path, label="canonical validation config")
    config_path = raw_config_path.resolve()
    canonical_config = _load_json_object(config_path, "canonical validation config")
    canonical_identity = _validation_attempt_identity(canonical_config)
    if canonical_identity["protocol_ids"] != list(VALIDATION_PROTOCOL_IDS):
        raise CapabilityImprovementError(
            "canonical validation bundle config must contain the fixed panel"
        )

    selected: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    seen_attempt_ids: set[str] = set()
    for raw_attempt_root in attempt_roots:
        _assert_no_symlink_chain(raw_attempt_root, label="validation attempt root")
        attempt_root = raw_attempt_root.expanduser().resolve()
        attempt_id = attempt_root.name
        if attempt_id in seen_attempt_ids:
            raise CapabilityImprovementError(
                f"duplicate validation attempt id: {attempt_id}"
            )
        seen_attempt_ids.add(attempt_id)
        attempt_config_path = attempt_root / "config.json"
        attempt_result_path = attempt_root / "result.json"
        if not attempt_config_path.is_file() or not attempt_result_path.is_file():
            raise CapabilityImprovementError(
                f"validation attempt is incomplete: {attempt_root}"
            )
        attempt_config = _load_json_object(
            attempt_config_path, "validation attempt config"
        )
        attempt_identity = _validation_attempt_identity(
            attempt_config,
            canonical_tasks_root=Path(canonical_identity["tasks_root"]),
        )
        for field in (
            "agent",
            "environment",
            "mounts",
            "extra_instruction_paths",
            "artifacts",
            "semantic_retries",
        ):
            if attempt_identity[field] != canonical_identity[field]:
                raise CapabilityImprovementError(
                    f"validation attempt changes canonical {field}: {attempt_id}"
                )
        protocol_ids = attempt_identity["protocol_ids"]
        if not protocol_ids or not set(protocol_ids).issubset(VALIDATION_PROTOCOL_IDS):
            raise CapabilityImprovementError(
                f"validation attempt contains a non-panel task: {attempt_id}"
            )
        job = _load_json_object(attempt_result_path, "validation attempt result")
        if job.get("n_total_trials") != len(protocol_ids):
            raise CapabilityImprovementError(
                f"validation attempt trial count differs from its config: {attempt_id}"
            )
        successful_protocol_ids: list[str] = []
        unsuccessful_protocol_ids: list[str] = []
        observed_protocol_ids: set[str] = set()
        for trial_result_path in sorted(attempt_root.glob("*/result.json")):
            if trial_result_path.is_symlink():
                raise CapabilityImprovementError(
                    f"validation attempt result is a symlink: {trial_result_path}"
                )
            trial = _load_json_object(trial_result_path, "validation Harbor trial")
            task_path = (trial.get("task_id") or {}).get("path")
            protocol_id = Path(task_path).name if isinstance(task_path, str) else None
            if protocol_id not in protocol_ids or protocol_id in observed_protocol_ids:
                raise CapabilityImprovementError(
                    f"invalid or duplicate validation attempt trial: {trial_result_path}"
                )
            observed_protocol_ids.add(protocol_id)
            if not _validation_trial_is_fully_scored(trial):
                unsuccessful_protocol_ids.append(protocol_id)
                continue
            diagnostics = _validation_trial_diagnostics(trial_result_path.parent)
            if protocol_id in selected:
                raise CapabilityImprovementError(
                    "multiple successful validation attempts exist for " + protocol_id
                )
            successful_protocol_ids.append(protocol_id)
            selected[protocol_id] = {
                "attempt_id": attempt_id,
                "source_path": trial_result_path,
                "source_trial_result_sha256": sha256_file(trial_result_path),
                **diagnostics,
            }
        missing_from_attempt = set(protocol_ids) - observed_protocol_ids
        unsuccessful_protocol_ids.extend(sorted(missing_from_attempt))
        attempts.append(
            {
                "attempt_id": attempt_id,
                "config_sha256": sha256_file(attempt_config_path),
                "job_result_sha256": sha256_file(attempt_result_path),
                "planned_protocol_ids": list(protocol_ids),
                "successful_protocol_ids": sorted(successful_protocol_ids),
                "unsuccessful_protocol_ids": sorted(set(unsuccessful_protocol_ids)),
                "agent_timeout_multiplier": attempt_identity[
                    "agent_timeout_multiplier"
                ],
                "job_concurrency": attempt_identity["job_concurrency"],
            }
        )
    if set(selected) != set(VALIDATION_PROTOCOL_IDS):
        missing = sorted(set(VALIDATION_PROTOCOL_IDS) - set(selected))
        raise CapabilityImprovementError(
            "validation attempts do not supply exactly one successful fixed-panel "
            "result; missing=" + ",".join(missing)
        )

    raw_output_root = output_root.expanduser()
    _assert_no_symlink_chain(raw_output_root.parent, label="validation bundle parent")
    resolved_output_root = raw_output_root.resolve()
    if resolved_output_root.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite validation result bundle: {resolved_output_root}"
        )
    resolved_output_root.mkdir(parents=True)
    from libstruct_bench.audit.artifacts import write_json_atomic

    write_json_atomic(resolved_output_root / "config.json", canonical_config)
    selections = []
    for order, protocol_id in enumerate(VALIDATION_PROTOCOL_IDS, start=1):
        item = selected[protocol_id]
        trial_root = resolved_output_root / f"trial-{order:02d}"
        trial_root.mkdir()
        target = trial_root / "result.json"
        shutil.copyfile(item["source_path"], target)
        if sha256_file(target) != item["source_trial_result_sha256"]:
            raise CapabilityImprovementError(
                f"validation result copy changed bytes: {protocol_id}"
            )
        diagnostic_root = trial_root / "artifacts" / "logs" / "artifacts"
        diagnostic_root.mkdir(parents=True)
        for source_key, filename, digest_key in (
            (
                "exemplar_usage_path",
                "exemplar_usage.json",
                "exemplar_usage_sha256",
            ),
            (
                "target_evidence_guard_path",
                "target_evidence_guard.json",
                "target_evidence_guard_sha256",
            ),
        ):
            diagnostic_target = diagnostic_root / filename
            shutil.copyfile(item[source_key], diagnostic_target)
            if sha256_file(diagnostic_target) != item[digest_key]:
                raise CapabilityImprovementError(
                    f"validation diagnostic copy changed bytes: {protocol_id} / "
                    f"{filename}"
                )
        selections.append(
            {
                "protocol_id": protocol_id,
                "attempt_id": item["attempt_id"],
                "source_trial_result_sha256": item["source_trial_result_sha256"],
                "source_exemplar_usage_sha256": item["exemplar_usage_sha256"],
                "source_target_evidence_guard_sha256": item[
                    "target_evidence_guard_sha256"
                ],
            }
        )
    payload: dict[str, Any] = {
        "schema_version": VALIDATION_RESULT_BUNDLE_SCHEMA_VERSION,
        "n_total_trials": 5,
        "attempt_count": len(attempts),
        "canonical_config_sha256": sha256_file(resolved_output_root / "config.json"),
        "attempts": attempts,
        "selections": selections,
        "created_at": normalized_timestamp(created_at),
    }
    result = with_digest(payload, "bundle_digest")
    validate_document(
        result,
        improvement_schema_root() / "validation_result_bundle.schema.json",
        label="validation result bundle",
    )
    write_json_atomic(resolved_output_root / "result.json", result)
    return resolved_output_root, result


def _validation_attempt_identity(
    config: Mapping[str, Any],
    *,
    canonical_tasks_root: Path | None = None,
) -> dict[str, Any]:
    agents = config.get("agents")
    if not isinstance(agents, list) or len(agents) != 1:
        raise CapabilityImprovementError(
            "validation attempt requires exactly one agent"
        )
    environment = config.get("environment")
    if not isinstance(environment, Mapping):
        raise CapabilityImprovementError("validation attempt environment is missing")
    mounts = environment.get("mounts")
    if not isinstance(mounts, list):
        raise CapabilityImprovementError("validation attempt mounts are missing")
    instructions = config.get("extra_instruction_paths")
    artifacts = config.get("artifacts")
    if not isinstance(instructions, list) or not isinstance(artifacts, list):
        raise CapabilityImprovementError(
            "validation attempt instructions or artifacts are missing"
        )
    datasets = config.get("datasets")
    tasks = config.get("tasks")
    if isinstance(datasets, list) and len(datasets) == 1:
        dataset = datasets[0]
        if not isinstance(dataset, Mapping):
            raise CapabilityImprovementError("validation attempt dataset is invalid")
        tasks_root = Path(str(dataset.get("path", ""))).expanduser().resolve()
        protocol_ids = dataset.get("task_names")
        if not isinstance(protocol_ids, list) or not all(
            isinstance(item, str) for item in protocol_ids
        ):
            raise CapabilityImprovementError(
                "validation attempt task_names are invalid"
            )
    elif isinstance(tasks, list) and tasks:
        if canonical_tasks_root is None:
            raise CapabilityImprovementError(
                "task-path validation attempt requires a canonical task root"
            )
        tasks_root = canonical_tasks_root.expanduser().resolve()
        protocol_ids = []
        for item in tasks:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                raise CapabilityImprovementError(
                    "validation attempt task path is invalid"
                )
            task_path = Path(item["path"]).expanduser().resolve()
            try:
                relative = task_path.relative_to(tasks_root)
            except ValueError as error:
                raise CapabilityImprovementError(
                    "validation retry task escapes the canonical task root"
                ) from error
            if len(relative.parts) != 1:
                raise CapabilityImprovementError(
                    "validation retry task must identify one protocol directory"
                )
            protocol_ids.append(relative.name)
    else:
        raise CapabilityImprovementError("validation attempt tasks are missing")
    if len(protocol_ids) != len(set(protocol_ids)):
        raise CapabilityImprovementError("validation attempt repeats a task")
    retries = config.get("retry") or {}
    if not isinstance(retries, Mapping) or retries.get("max_retries", 0) != 0:
        raise CapabilityImprovementError(
            "validation attempt must keep semantic retries disabled"
        )
    timeout = config.get("agent_timeout_multiplier", 1.0)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or float(timeout) < 1.0
    ):
        raise CapabilityImprovementError(
            "validation attempt has an invalid agent timeout multiplier"
        )
    concurrency = config.get("n_concurrent_trials", 4)
    if (
        isinstance(concurrency, bool)
        or not isinstance(concurrency, int)
        or concurrency < 1
        or concurrency > VALIDATION_RUNNER["concurrency"]
    ):
        raise CapabilityImprovementError(
            "validation attempt exceeds the fixed job concurrency"
        )
    return {
        "agent": agents[0],
        "environment": environment.get("type", "docker"),
        "mounts": list(mounts),
        "extra_instruction_paths": [
            Path(str(item)).expanduser().resolve().as_posix() for item in instructions
        ],
        "artifacts": list(artifacts),
        "semantic_retries": retries.get("max_retries", 0),
        "tasks_root": tasks_root.as_posix(),
        "protocol_ids": list(protocol_ids),
        "agent_timeout_multiplier": float(timeout),
        "job_concurrency": concurrency,
    }


def _validation_trial_is_fully_scored(trial: Mapping[str, Any]) -> bool:
    if trial.get("exception_info") is not None:
        return False
    rewards = (trial.get("verifier_result") or {}).get("rewards") or {}
    for metric in VALIDATION_METRICS:
        value = rewards.get(metric)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
        ):
            return False
    return True


def _validation_trial_diagnostics(
    trial_root: Path,
    *,
    checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    diagnostic_root = trial_root / "artifacts" / "logs" / "artifacts"
    usage_path = diagnostic_root / "exemplar_usage.json"
    guard_path = diagnostic_root / "target_evidence_guard.json"
    for label, path in (
        ("exemplar usage", usage_path),
        ("target-evidence guard", guard_path),
    ):
        if not path.is_file() or path.is_symlink():
            raise CapabilityImprovementError(
                f"validation trial is missing its {label} artifact: {trial_root}"
            )
    usage = _load_json_object(usage_path, "validation exemplar usage")
    guard = _load_json_object(guard_path, "validation target-evidence guard")
    validate_document(
        usage,
        improvement_schema_root() / "exemplar_usage.schema.json",
        label="validation exemplar usage",
    )
    validate_document(
        guard,
        improvement_schema_root() / "target_evidence_guard_report.schema.json",
        label="validation target-evidence guard",
    )
    validate_digest(usage, "usage_digest")
    validate_digest(guard, "report_digest")
    if (
        guard.get("status") != "pass"
        or guard.get("finding_count") != 0
        or guard.get("findings") != []
    ):
        raise CapabilityImprovementError(
            f"validation target-evidence guard did not pass: {trial_root}"
        )
    for field in (
        "retrieval_digest",
        "usage_digest",
        "target_work_record_sha256",
    ):
        if guard.get(field) != usage.get(field):
            raise CapabilityImprovementError(
                f"validation diagnostic artifacts disagree on {field}: {trial_root}"
            )
    if checkpoint is not None:
        memory = checkpoint.get("exemplar_memory")
        if not isinstance(memory, Mapping) or (
            guard.get("memory_digest") != memory.get("memory_digest")
            or guard.get("catalog_digest") != memory.get("catalog_digest")
        ):
            raise CapabilityImprovementError(
                "validation target-evidence guard belongs to another memory: "
                f"{trial_root}"
            )
    return {
        "exemplar_usage_path": usage_path,
        "exemplar_usage_sha256": sha256_file(usage_path),
        "target_evidence_guard_path": guard_path,
        "target_evidence_guard_sha256": sha256_file(guard_path),
    }


def validate_validation_aggregate(
    path: Path,
    *,
    experiment_digest: str | None = None,
    validation_access_policy: Mapping[str, Any] | None = None,
    expected_checkpoint_label: str | None = None,
    expected_pack_digest: str | None = None,
    expected_checkpoint_root: Path | None = None,
) -> dict[str, Any]:
    result = load_and_validate(
        path,
        schema_filename="validation_aggregate.schema.json",
        digest_field="aggregate_digest",
        label="validation aggregate",
    )
    _validate_validation_aggregate_semantics(result)
    expected = {
        "experiment_digest": experiment_digest,
        "checkpoint_label": expected_checkpoint_label,
        "pack_digest": expected_pack_digest,
    }
    for field, value in expected.items():
        if value is not None and result[field] != value:
            raise CapabilityImprovementError(f"validation aggregate has stale {field}")
    if validation_access_policy is not None and (
        result["access_policy_digest"] != validation_access_policy["policy_digest"]
        or result["validation_panel_commitment_sha256"]
        != validation_access_policy["validation_panel_commitment_sha256"]
    ):
        raise CapabilityImprovementError(
            "validation aggregate references another access policy"
        )
    if expected_checkpoint_root is not None:
        from .workflow import validate_checkpoint_runtime

        checkpoint_root = expected_checkpoint_root.expanduser().resolve()
        checkpoint, runtime, pack = validate_checkpoint_runtime(checkpoint_root)
        bound = {
            "experiment_digest": checkpoint["experiment_digest"],
            "checkpoint_label": checkpoint["checkpoint_id"],
            "checkpoint_digest": checkpoint["checkpoint_digest"],
            "checkpoint_sha256": sha256_file(checkpoint_root / "checkpoint.json"),
            "pack_digest": pack["pack_digest"],
            "pack_manifest_sha256": checkpoint["pack_manifest_sha256"],
            "runtime_digest": runtime["runtime_digest"],
            "runtime_sha256": sha256_file(checkpoint_root / "runtime.json"),
        }
        for field, value in bound.items():
            if result[field] != value:
                raise CapabilityImprovementError(
                    f"validation aggregate has stale {field}"
                )
    return result


def build_validation_feedback_projection(
    aggregate: Mapping[str, Any],
) -> dict[str, Any]:
    """Project an orchestrator aggregate to the only agent-visible fields."""

    validate_digest(aggregate, "aggregate_digest")
    _validate_validation_aggregate_semantics(aggregate)
    feedback: dict[str, Any] = {
        "schema_version": VALIDATION_FEEDBACK_SCHEMA_VERSION,
        "checkpoint_label": aggregate["checkpoint_label"],
        "planned_trials": aggregate["planned_trials"],
        "scored_trials": aggregate["scored_trials"],
        "unscored_trials": aggregate["unscored_trials"],
        "metric_counts": dict(aggregate["metric_counts"]),
        "macro_means": dict(aggregate["macro_means"]),
        "feedback_scope": aggregate["feedback_scope"],
        "guidance_target_batch": aggregate["guidance_target_batch"],
        "aggregate_digest": aggregate["aggregate_digest"],
    }
    validate_document(
        feedback,
        improvement_schema_root() / "validation_feedback.schema.json",
        label="validation feedback projection",
    )
    if set(feedback) != set(VALIDATION_FEEDBACK_FIELDS):
        raise CapabilityImprovementError(
            "validation feedback projection changed its aggregate-only contract"
        )
    return feedback


def validate_validation_feedback_projection(
    path: Path,
    *,
    source_aggregate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    feedback = _load_json_object(path, "validation feedback projection")
    validate_document(
        feedback,
        improvement_schema_root() / "validation_feedback.schema.json",
        label="validation feedback projection",
    )
    if set(feedback) != set(VALIDATION_FEEDBACK_FIELDS):
        raise CapabilityImprovementError(
            "validation feedback projection changed its aggregate-only contract"
        )
    if source_aggregate is not None and feedback != (
        build_validation_feedback_projection(source_aggregate)
    ):
        raise CapabilityImprovementError(
            "validation feedback projection differs from its source aggregate"
        )
    return feedback


@guard_experiment_mutation("record validation aggregate")
def record_validation_aggregate(
    *,
    experiment_root: Path,
    checkpoint_label: str,
    result_root: Path,
    created_at: str,
) -> tuple[Path, dict[str, Any]]:
    """Sanitize one completed validation run into its canonical aggregate.

    The recorder resolves the pack digest from the frozen checkpoint, verifies
    every experiment and policy binding, and never overwrites an aggregate.
    Raw Harbor paths and per-protocol rows remain outside the written artifact.
    """

    if checkpoint_label not in VALIDATION_CHECKPOINT_LABELS:
        raise CapabilityImprovementError(
            f"unknown validation checkpoint: {checkpoint_label}"
        )
    raw_experiment_root = experiment_root.expanduser()
    _assert_no_symlink_chain(raw_experiment_root, label="experiment root")
    root = raw_experiment_root.resolve()
    raw_result_root = result_root.expanduser()
    _assert_no_symlink_chain(raw_result_root, label="validation result root")
    resolved_result_root = raw_result_root.resolve()
    if resolved_result_root == root or root in resolved_result_root.parents:
        raise CapabilityImprovementError(
            "raw validation results must remain outside the active experiment"
        )
    from .experiment import validate_experiment_manifest
    from .governance import assert_capability_modification_open
    from .workflow import validate_checkpoint_runtime

    assert_capability_modification_open(root)
    experiment = validate_experiment_manifest(
        root / "design" / "experiment_manifest.json",
        experiment_root=root,
    )
    policy = validate_referenced_validation_access_policy(
        experiment_root=root,
        experiment_manifest=experiment,
    )
    checkpoint_root = root / "checkpoints" / checkpoint_label
    checkpoint, _, pack = validate_checkpoint_runtime(checkpoint_root)
    if checkpoint["checkpoint_id"] != checkpoint_label:
        raise CapabilityImprovementError(
            "validation checkpoint path contains another checkpoint"
        )
    if checkpoint["experiment_digest"] != experiment["experiment_digest"]:
        raise CapabilityImprovementError(
            "validation checkpoint belongs to another experiment"
        )
    output_path = root / "validation" / "aggregates" / f"{checkpoint_label}.json"
    _assert_canonical_output_path(root, output_path)
    if output_path.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite validation aggregate: {output_path}"
        )
    aggregate = build_validation_aggregate(
        experiment_digest=experiment["experiment_digest"],
        checkpoint_label=checkpoint_label,
        checkpoint_root=checkpoint_root,
        validation_access_policy=policy,
        result_root=resolved_result_root,
        created_at=created_at,
    )
    for label in VALIDATION_CHECKPOINT_LABELS:
        prior = root / "validation" / "aggregates" / f"{label}.json"
        if not prior.is_file():
            continue
        recorded = validate_validation_aggregate(
            prior,
            experiment_digest=experiment["experiment_digest"],
            validation_access_policy=policy,
            expected_checkpoint_label=label,
            expected_checkpoint_root=root / "checkpoints" / label,
        )
        for field in (
            "harbor_config_sha256",
            "integration_digest",
            "integration_manifest_sha256",
            "result_bundle_digest",
            "diagnostic_bundle_digest",
        ):
            if recorded[field] == aggregate[field]:
                raise CapabilityImprovementError(
                    f"validation run identity was already used by {label}: {field}"
                )
    from libstruct_bench.audit.artifacts import write_json_atomic

    write_json_atomic(output_path, aggregate, mode=0o444)
    return output_path, aggregate


def validate_required_validation_aggregate(
    *,
    experiment_root: Path,
    experiment_digest: str,
    validation_access_policy: Mapping[str, Any],
    batch_id: str,
    expected_pack_digest: str,
) -> dict[str, Any]:
    try:
        checkpoint_label = VALIDATION_BATCH_GATE[batch_id]
    except KeyError as error:
        raise CapabilityImprovementError(
            f"unknown validation-gated batch: {batch_id}"
        ) from error
    return validate_validation_aggregate(
        experiment_root.expanduser().resolve()
        / "validation"
        / "aggregates"
        / f"{checkpoint_label}.json",
        experiment_digest=experiment_digest,
        validation_access_policy=validation_access_policy,
        expected_checkpoint_label=checkpoint_label,
        expected_pack_digest=expected_pack_digest,
        expected_checkpoint_root=(
            experiment_root.expanduser().resolve() / "checkpoints" / checkpoint_label
        ),
    )


def build_validation_guidance_record(
    *,
    experiment_root: Path,
    experiment_digest: str,
    validation_access_policy: Mapping[str, Any],
    batch_id: str,
    expected_pack_digest: str,
    workspace_manifest_path: Path,
) -> dict[str, str]:
    """Bind one learned update to its canonical aggregate and staged workspace."""

    aggregate = validate_required_validation_aggregate(
        experiment_root=experiment_root,
        experiment_digest=experiment_digest,
        validation_access_policy=validation_access_policy,
        batch_id=batch_id,
        expected_pack_digest=expected_pack_digest,
    )
    aggregate_path = (
        experiment_root.expanduser().resolve()
        / "validation"
        / "aggregates"
        / f"{aggregate['checkpoint_label']}.json"
    )
    from .isolation import validate_isolated_worker_workspace

    manifest_path = workspace_manifest_path.expanduser().resolve()
    manifest = validate_isolated_worker_workspace(manifest_path.parent)
    feedback = manifest.get("validation_feedback")
    expected_feedback = {
        "checkpoint_label": aggregate["checkpoint_label"],
        "aggregate_digest": aggregate["aggregate_digest"],
        "aggregate_sha256": sha256_file(aggregate_path),
    }
    if not isinstance(feedback, Mapping) or any(
        feedback.get(field) != value for field, value in expected_feedback.items()
    ):
        raise CapabilityImprovementError(
            "proposal workspace is not bound to the required validation aggregate"
        )
    validate_validation_feedback_projection(
        manifest_path.parent / "inputs" / "validation_feedback.json",
        source_aggregate=aggregate,
    )
    return {
        **expected_feedback,
        "workspace_digest": manifest["workspace_digest"],
        "workspace_manifest_sha256": sha256_file(manifest_path),
    }


def validate_validation_guidance_record(
    record: Mapping[str, Any],
    *,
    experiment_root: Path,
    experiment_digest: str,
    validation_access_policy: Mapping[str, Any],
    batch_id: str,
    expected_pack_digest: str,
    workspace_search_root: Path | None = None,
) -> dict[str, Any]:
    aggregate = validate_required_validation_aggregate(
        experiment_root=experiment_root,
        experiment_digest=experiment_digest,
        validation_access_policy=validation_access_policy,
        batch_id=batch_id,
        expected_pack_digest=expected_pack_digest,
    )
    aggregate_path = (
        experiment_root.expanduser().resolve()
        / "validation"
        / "aggregates"
        / f"{aggregate['checkpoint_label']}.json"
    )
    expected = {
        "checkpoint_label": aggregate["checkpoint_label"],
        "aggregate_digest": aggregate["aggregate_digest"],
        "aggregate_sha256": sha256_file(aggregate_path),
    }
    if any(record.get(field) != value for field, value in expected.items()):
        raise CapabilityImprovementError(
            "capability lineage references stale validation guidance"
        )
    if workspace_search_root is not None:
        from .isolation import validate_isolated_worker_workspace

        matches = []
        search_root = workspace_search_root.expanduser().resolve()
        for path in sorted(search_root.rglob("workspace_manifest.json")):
            if path.is_symlink() or not path.is_file():
                continue
            if sha256_file(path) != record.get("workspace_manifest_sha256"):
                continue
            manifest = validate_isolated_worker_workspace(path.parent)
            if manifest["workspace_digest"] == record.get("workspace_digest"):
                matches.append(path)
        if len(matches) != 1:
            raise CapabilityImprovementError(
                "validation-guidance workspace provenance is missing or ambiguous"
            )
    return aggregate


def validate_complete_validation_curve(
    *,
    experiment_root: Path,
    experiment_digest: str,
    validation_access_policy: Mapping[str, Any],
) -> dict[str, Any]:
    root = experiment_root.expanduser().resolve()
    records = []
    for label in VALIDATION_CHECKPOINT_LABELS:
        path = root / "validation" / "aggregates" / f"{label}.json"
        aggregate = validate_validation_aggregate(
            path,
            experiment_digest=experiment_digest,
            validation_access_policy=validation_access_policy,
            expected_checkpoint_label=label,
            expected_checkpoint_root=root / "checkpoints" / label,
        )
        records.append(
            {
                "checkpoint_label": label,
                "aggregate_digest": aggregate["aggregate_digest"],
                "aggregate_sha256": sha256_file(path),
                "pack_digest": aggregate["pack_digest"],
                "checkpoint_digest": aggregate["checkpoint_digest"],
                "runtime_digest": aggregate["runtime_digest"],
                "integration_digest": aggregate["integration_digest"],
                "integration_manifest_sha256": aggregate["integration_manifest_sha256"],
                "task_bundle_sha256": aggregate["task_bundle_sha256"],
                "harbor_config_sha256": aggregate["harbor_config_sha256"],
                "result_bundle_digest": aggregate["result_bundle_digest"],
            }
        )
    for field in (
        "integration_digest",
        "integration_manifest_sha256",
        "harbor_config_sha256",
        "result_bundle_digest",
    ):
        values = [item[field] for item in records]
        if len(values) != len(set(values)):
            raise CapabilityImprovementError(
                f"validation curve reuses a run identity: {field}"
            )
    task_digests = {item["task_bundle_sha256"] for item in records}
    if len(task_digests) != 1:
        raise CapabilityImprovementError("validation curve used different task bundles")
    return {
        "access_policy_digest": validation_access_policy["policy_digest"],
        "checkpoint_labels": list(VALIDATION_CHECKPOINT_LABELS),
        "aggregate_records": records,
        "expected_trial_count": 30,
    }


def assert_validation_learning_artifact_isolated(
    document: Mapping[str, Any],
    *,
    validation_access_policy: Mapping[str, Any],
    label: str,
) -> None:
    """Reject validation examples or raw artifacts in a learning document."""

    issues = _scan_json_value(document, validation_access_policy)
    if issues:
        raise CapabilityImprovementError(
            f"{label} crosses the validation learning boundary: "
            + "; ".join(issues[:8])
        )


def scan_validation_pack_leakage(
    pack_root: Path,
    validation_access_policy: Mapping[str, Any],
) -> list[str]:
    """Scan every pack file, including synthetic fixtures, without exemptions."""

    root = pack_root.expanduser().resolve()
    protected_hashes = _protected_hashes(validation_access_policy)
    issues: list[str] = []
    for path in _regular_tree_files(root, require_nonempty=False):
        relative = path.relative_to(root).as_posix()
        if sha256_file(path) in protected_hashes:
            issues.append(f"protected validation artifact copied into {relative}")
        issues.extend(
            f"{relative}: {item}"
            for item in _scan_validation_file(path, validation_access_policy)
        )
    return sorted(set(issues))


def scan_validation_feedback_copy(
    root: Path,
    aggregate: Mapping[str, Any],
) -> list[str]:
    """Reject deterministic copies of agent-visible validation score payloads."""

    projection = build_validation_feedback_projection(aggregate)
    issues: list[str] = []
    metric_names = tuple(VALIDATION_METRICS)
    for path in _regular_tree_files(
        root.expanduser().resolve(), require_nonempty=False
    ):
        relative = path.relative_to(root.expanduser().resolve()).as_posix()
        suffix = path.suffix.lower()
        if suffix not in _TEXT_SUFFIXES | _NATIVE_DOCUMENT_SUFFIXES:
            continue
        try:
            text = _native_source_text(path)
        except CapabilityImprovementError:
            continue
        lowered = text.lower()
        if "macro_means" in lowered and all(name in lowered for name in metric_names):
            issues.append(f"{relative}: copied validation macro metric payload")
            continue
        if suffix in {".json", ".jsonl"}:
            values: list[Any] = []
            try:
                if suffix == ".json":
                    values = [json.loads(text)]
                else:
                    values = [
                        json.loads(line) for line in text.splitlines() if line.strip()
                    ]
            except json.JSONDecodeError:
                values = []
            if any(
                item.get("macro_means") == projection["macro_means"]
                or item.get("metric_counts") == projection["metric_counts"]
                for value in values
                for item in _iter_mappings(value)
            ):
                issues.append(f"{relative}: copied validation aggregate map")
    return sorted(set(issues))


def build_validation_isolation_audit(
    *,
    experiment_root: Path,
    validation_access_policy: Mapping[str, Any],
    audited_at: str,
    active_learning_roots: Sequence[str] = VALIDATION_ACTIVE_LEARNING_ROOTS,
) -> dict[str, Any]:
    """Audit active cumulative-learning state while excluding superseded history."""

    root = experiment_root.expanduser().resolve()
    if tuple(active_learning_roots) != VALIDATION_ACTIVE_LEARNING_ROOTS:
        raise CapabilityImprovementError(
            "validation isolation audit requires the canonical active roots"
        )
    categories: dict[str, list[dict[str, str]]] = {
        item: [] for item in VALIDATION_ISOLATION_CATEGORIES
    }
    protected = _protected_hash_owners(validation_access_policy)
    training_evidence = _pinned_training_workspace_evidence(
        root, validation_access_policy
    )
    training_exemplars = _pinned_training_exemplar_projections(root)
    orchestrator_policies = _pinned_orchestrator_leakage_policies(root)
    sealed_transcripts = _pinned_compiled_worker_transcripts(root)
    for relative_root in active_learning_roots:
        active = root / relative_root
        if not active.exists():
            continue
        for path in _regular_tree_files(active, require_nonempty=False):
            relative = path.relative_to(root).as_posix()
            lowered_path = relative.lower()
            path_sha256 = sha256_file(path)
            if training_evidence.get(relative) == path_sha256:
                # Raw training artifacts are expected transient inputs.  They can
                # legitimately share adapters, sequence fragments, or mechanism
                # names with validation protocols.  Exempt only bytes proven to
                # match both the immutable batch packet and its staged manifest;
                # proposals, candidates, packs, checkpoints, and worker outputs
                # remain fully scanned.
                continue
            if training_exemplars.get(relative) == path_sha256:
                # These exact public documents have been independently
                # recomputed from the approved training-GT bytes pinned by the
                # canonical cumulative packets.  Coincidental overlap with a
                # validation-only motif is therefore not validation leakage.
                continue
            if orchestrator_policies.get(relative) == path_sha256:
                # The canonical per-round denylist is orchestrator-only.  It
                # intentionally contains protected fingerprints and must not be
                # mistaken for agent-visible learned content.  Copies at any
                # other path remain scanned.
                continue
            if sealed_transcripts.get(relative) == path_sha256:
                # Compiled proposal and finalized decision records pin these
                # exact canonical event logs as immutable execution provenance.
                # They are not staged into a later checkpoint or agent context.
                # Candidate bytes, decisions, packs, memories, copied logs, and
                # any stale or noncanonical transcript remain fully scanned.
                continue
            if relative_root == "validation":
                match = re.fullmatch(
                    r"validation/aggregates/(C0|C5|C10|C15|C20|C25)\.json",
                    relative,
                )
                if match is None:
                    categories["validation_raw_result_or_error_detail"].append(
                        _finding(relative, "noncanonical validation artifact")
                    )
                    continue
                try:
                    validate_validation_aggregate(
                        path,
                        validation_access_policy=validation_access_policy,
                        expected_checkpoint_label=match.group(1),
                        expected_checkpoint_root=(
                            root / "checkpoints" / match.group(1)
                        ),
                    )
                except CapabilityImprovementError as error:
                    categories["validation_raw_result_or_error_detail"].append(
                        _finding(relative, f"invalid canonical aggregate: {error}")
                    )
                continue
            digest = path_sha256
            if digest in protected:
                categories["validation_artifact_copy"].append(
                    _finding(relative, protected[digest])
                )
            path_protocols = [
                protocol_id
                for protocol_id in VALIDATION_PROTOCOL_IDS
                if _contains_term(lowered_path, protocol_id.lower())
            ]
            for protocol_id in path_protocols:
                category = (
                    "validation_raw_result_or_error_detail"
                    if any(token in lowered_path for token in _RAW_DETAIL_TOKENS)
                    else "validation_protocol_identifier"
                )
                categories[category].append(_finding(relative, protocol_id))
            for issue in _scan_validation_file(path, validation_access_policy):
                if issue.startswith("protocol term"):
                    category = (
                        "validation_raw_result_or_error_detail"
                        if any(token in lowered_path for token in _RAW_DETAIL_TOKENS)
                        else "validation_protocol_identifier"
                    )
                elif issue.startswith("exact sequence"):
                    category = "validation_exact_sequence"
                elif issue.startswith(
                    ("unsupported", "cannot extract", "cannot decode")
                ):
                    category = "validation_raw_result_or_error_detail"
                else:
                    category = "validation_scaffold"
                categories[category].append(_finding(relative, issue))
    checks = []
    for category in VALIDATION_ISOLATION_CATEGORIES:
        findings = sorted(
            {
                (item["path"], item["detail_sha256"]): item
                for item in categories[category]
            }.values(),
            key=lambda item: (item["path"], item["detail_sha256"]),
        )
        checks.append(
            {
                "category": category,
                "status": "pass" if not findings else "fail",
                "findings": findings,
            }
        )
    payload: dict[str, Any] = {
        "schema_version": VALIDATION_ISOLATION_AUDIT_SCHEMA_VERSION,
        "audit_id": "fixed-validation-learning-isolation-v1",
        "validation_panel_commitment_sha256": validation_access_policy[
            "validation_panel_commitment_sha256"
        ],
        "access_policy_digest": validation_access_policy["policy_digest"],
        "protocol_count": 5,
        "active_learning_roots": list(VALIDATION_ACTIVE_LEARNING_ROOTS),
        "superseded_history_policy": (
            "preserved_immutable_but_never_staged_or_used_for_active_learning"
        ),
        "checks": checks,
        "learning_isolation": (
            "pass" if all(item["status"] == "pass" for item in checks) else "fail"
        ),
        "audited_at": normalized_timestamp(audited_at),
    }
    result = with_digest(payload, "audit_digest")
    validate_document(
        result,
        improvement_schema_root() / "validation_isolation_audit.schema.json",
        label="validation isolation audit",
    )
    _validate_validation_isolation_semantics(result)
    return result


def _pinned_training_exemplar_projections(experiment_root: Path) -> dict[str, str]:
    """Return only checkpoint exemplar files reproducible from training GT."""

    from .exemplar_memory import (
        IDENTITY_MAP_RELATIVE_PATH,
        validate_exemplar_identity_map,
        validate_exemplar_memory,
        validate_exemplar_memory_projections,
    )
    from .workflow import validate_checkpoint_runtime

    root = experiment_root.expanduser().resolve()
    manifest_path = root / "design" / "experiment_manifest.json"
    split_path = root / "design" / "frozen_split.json"
    identity_path = root / IDENTITY_MAP_RELATIVE_PATH
    if not (
        manifest_path.is_file() and split_path.is_file() and identity_path.is_file()
    ):
        return {}
    try:
        experiment = load_and_validate(
            manifest_path,
            schema_filename="experiment_manifest.schema.json",
            digest_field="experiment_digest",
            label="capability experiment for exemplar isolation",
        )
        split = load_and_validate(
            split_path,
            schema_filename="frozen_split.schema.json",
            digest_field="split_manifest_digest",
            label="frozen split for exemplar isolation",
        )
        split_ref = experiment["frozen_split"]
        if (
            safe_relative_path(split_ref["path"]) != Path("design/frozen_split.json")
            or split_ref["digest"] != split["split_manifest_digest"]
            or split_ref["sha256"] != sha256_file(split_path)
        ):
            return {}
        identity_map = validate_exemplar_identity_map(
            identity_path,
            split_digest=split["split_digest"],
        )
        exemplar_model = experiment["memory_model"]["exemplar"]
        adoption_ref = exemplar_model["adoption"]
        adoption_path = root / safe_relative_path(adoption_ref["path"])
        adoption = load_and_validate(
            adoption_path,
            schema_filename="exemplar_memory_adoption.schema.json",
            digest_field="adoption_digest",
            label="exemplar-memory adoption for isolation",
        )
        if (
            adoption_ref["digest"] != adoption["adoption_digest"]
            or adoption_ref["sha256"] != sha256_file(adoption_path)
            or adoption["identity_map_digest"] != identity_map["identity_map_digest"]
            or adoption["identity_map_sha256"] != sha256_file(identity_path)
            or exemplar_model["identity_map_public_commitment_sha256"]
            != identity_map["identity_map_digest"]
        ):
            return {}
    except (CapabilityImprovementError, KeyError, TypeError):
        return {}

    verified: dict[str, str] = {}
    checkpoint_records: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for checkpoint_label in VALIDATION_CHECKPOINT_LABELS[1:]:
        checkpoint_root = root / "checkpoints" / checkpoint_label
        if not checkpoint_root.exists():
            continue
        expected_count = int(checkpoint_label[1:])
        packet_paths = [
            root / "rounds" / f"B{index}" / "cumulative" / "packet.json"
            for index in range(1, expected_count // 5 + 1)
        ]
        if not all(item.is_file() for item in packet_paths):
            continue
        try:
            checkpoint, runtime, _ = validate_checkpoint_runtime(checkpoint_root)
            if (
                checkpoint["checkpoint_id"] != checkpoint_label
                or checkpoint["experiment_digest"] != experiment["experiment_digest"]
                or checkpoint["protocol_count"] != expected_count
            ):
                continue
            relative_hashes = validate_exemplar_memory_projections(
                memory_root=checkpoint_root / "memory",
                packet_paths=packet_paths,
                identity_map=identity_map,
                experiment_digest=experiment["experiment_digest"],
                expected_count=expected_count,
            )
        except (CapabilityImprovementError, KeyError, TypeError):
            continue
        for relative, digest in relative_hashes.items():
            verified[f"checkpoints/{checkpoint_label}/memory/{relative}"] = digest
        checkpoint_records[checkpoint_label] = (checkpoint, runtime)

    # Isolated proposer, critic, and revision workspaces receive a frozen copy
    # of the current checkpoint memory.  Prove those bytes a second time from
    # the same approved training packets and require both the source-checkpoint
    # record and the self-digested workspace inventory to pin every exemption.
    workspace_manifests = sorted(
        (root / "rounds").glob("B*/cumulative/*-workspace/workspace_manifest.json")
    )
    for workspace_manifest_path in workspace_manifests:
        try:
            workspace = load_and_validate(
                workspace_manifest_path,
                schema_filename="worker_workspace_manifest.schema.json",
                digest_field="workspace_digest",
                label="isolated workspace for exemplar isolation",
            )
            memory_record = workspace.get("exemplar_memory")
            if not isinstance(memory_record, Mapping):
                continue
            source = memory_record.get("source_checkpoint")
            if not isinstance(source, Mapping):
                continue
            checkpoint_label = source.get("checkpoint_id")
            if checkpoint_label not in checkpoint_records:
                continue
            checkpoint, runtime = checkpoint_records[checkpoint_label]
            checkpoint_root = root / "checkpoints" / str(checkpoint_label)
            if (
                source.get("checkpoint_digest") != checkpoint["checkpoint_digest"]
                or source.get("checkpoint_sha256")
                != sha256_file(checkpoint_root / "checkpoint.json")
                or source.get("runtime_digest") != runtime["runtime_digest"]
                or source.get("runtime_sha256")
                != sha256_file(checkpoint_root / "runtime.json")
                or memory_record.get("private_identity_map_staged") is not False
            ):
                continue
            expected_count = int(str(checkpoint_label)[1:])
            packet_paths = [
                root / "rounds" / f"B{index}" / "cumulative" / "packet.json"
                for index in range(1, expected_count // 5 + 1)
            ]
            workspace_root = workspace_manifest_path.parent
            staged_memory_root = workspace_root / safe_relative_path(
                memory_record["path"]
            )
            staged_hashes = validate_exemplar_memory_projections(
                memory_root=staged_memory_root,
                packet_paths=packet_paths,
                identity_map=identity_map,
                experiment_digest=experiment["experiment_digest"],
                expected_count=expected_count,
            )
            staged_memory = validate_exemplar_memory(
                staged_memory_root,
                expected_count=expected_count,
                identity_map=identity_map,
            )
            if (
                memory_record.get("memory_digest") != staged_memory["memory_digest"]
                or memory_record.get("memory_manifest_sha256")
                != sha256_file(staged_memory_root / "manifest.json")
                or memory_record.get("catalog_digest")
                != staged_memory["catalog_digest"]
                or memory_record.get("catalog_sha256")
                != sha256_file(staged_memory_root / "catalog.json")
                or memory_record.get("exemplar_count") != expected_count
                or memory_record.get("identity_map_commitment")
                != identity_map["identity_map_digest"]
            ):
                continue
            staged_index = {
                item["path"]: item
                for item in workspace["staged_files"]
                if item["role"] == "current_exemplar_memory"
            }
            relative_memory_root = safe_relative_path(memory_record["path"])
            if any(
                (
                    staged_item := staged_index.get(
                        (relative_memory_root / relative).as_posix()
                    )
                )
                is None
                or staged_item["sha256"] != digest
                for relative, digest in staged_hashes.items()
            ):
                continue
        except (CapabilityImprovementError, KeyError, TypeError, ValueError):
            continue
        workspace_relative = workspace_root.relative_to(root).as_posix()
        for relative, digest in staged_hashes.items():
            verified[
                f"{workspace_relative}/{relative_memory_root.as_posix()}/{relative}"
            ] = digest
    return dict(sorted(verified.items()))


def _pinned_orchestrator_leakage_policies(
    experiment_root: Path,
) -> dict[str, str]:
    """Return canonical, self-digested, orchestrator-only round denylists."""

    from .experiment import validate_cumulative_leakage_policy

    root = experiment_root.expanduser().resolve()
    manifest_path = root / "design" / "experiment_manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        experiment = load_and_validate(
            manifest_path,
            schema_filename="experiment_manifest.schema.json",
            digest_field="experiment_digest",
            label="capability experiment for leakage-policy isolation",
        )
    except CapabilityImprovementError:
        return {}
    verified: dict[str, str] = {}
    expected_prefix: list[str] = []
    for batch in FINAL_DEVELOPMENT_BATCHES:
        expected_prefix.extend(batch["protocol_ids"])
        relative = f"rounds/{batch['batch_id']}/cumulative/leakage-policy.json"
        policy_path = root / relative
        if not policy_path.exists():
            continue
        try:
            policy = validate_cumulative_leakage_policy(
                policy_path,
                experiment_manifest=experiment,
            )
        except CapabilityImprovementError:
            continue
        if (
            policy["through_batch"] == batch["batch_id"]
            and policy["protocol_ids"] == expected_prefix
            and policy["agent_visibility"] == "none_orchestrator_only"
        ):
            verified[relative] = sha256_file(policy_path)
    return dict(sorted(verified.items()))


def _pinned_compiled_worker_transcripts(
    experiment_root: Path,
) -> dict[str, str]:
    """Return canonical event logs pinned by compiled round artifacts.

    A proposal pins its proposer transcript after deterministic compilation.
    An independent decision pins its critic transcript only after the decision
    is terminal.  These logs remain immutable provenance but are never learned
    checkpoint content.  Invalid records, unexpected paths, hash mismatches,
    in-progress decisions, and copies receive no exemption.
    """

    root = experiment_root.expanduser().resolve()
    verified: dict[str, str] = {}
    for batch in FINAL_DEVELOPMENT_BATCHES:
        batch_id = batch["batch_id"]
        round_root = root / "rounds" / batch_id / "cumulative"
        for revision_round, proposal_name, transcript_relative in (
            (0, "proposal.json", "proposer-workspace/outputs/proposal.events.jsonl"),
            (1, "proposal-r1.json", "revision-workspace/outputs/revision.events.jsonl"),
        ):
            proposal_path = round_root / proposal_name
            transcript_path = round_root / transcript_relative
            if not proposal_path.is_file() or not transcript_path.is_file():
                continue
            try:
                proposal = load_and_validate(
                    proposal_path,
                    schema_filename="capability_proposal.schema.json",
                    digest_field="proposal_digest",
                    label="compiled proposal for validation isolation",
                )
            except CapabilityImprovementError:
                continue
            transcript_sha256 = sha256_file(transcript_path)
            if (
                proposal.get("batch_id") == batch_id
                and proposal.get("branch") == "cumulative"
                and proposal.get("revision_round") == revision_round
                and proposal.get("proposer", {}).get("transcript_sha256")
                == transcript_sha256
            ):
                relative = transcript_path.relative_to(root).as_posix()
                verified[relative] = transcript_sha256

        for revision_round, decision_name, transcript_relative in (
            (0, "decision.json", "critic-workspace/outputs/critic.events.jsonl"),
            (1, "decision-r1.json", "critic-workspace-r1/outputs/critic.events.jsonl"),
        ):
            decision_path = round_root / decision_name
            transcript_path = round_root / transcript_relative
            if not decision_path.is_file() or not transcript_path.is_file():
                continue
            try:
                decision = load_and_validate(
                    decision_path,
                    schema_filename="capability_decision.schema.json",
                    digest_field="decision_digest",
                    label="compiled decision for validation isolation",
                )
            except CapabilityImprovementError:
                continue
            transcript_sha256 = sha256_file(transcript_path)
            if (
                decision.get("batch_id") == batch_id
                and decision.get("branch") == "cumulative"
                and decision.get("revision_round") == revision_round
                and decision.get("reviewer_kind") == "independent_codex"
                and decision.get("review_state") in {"revision_requested", "final"}
                and decision.get("completed_at") is not None
                and decision.get("reviewer", {}).get("transcript_sha256")
                == transcript_sha256
            ):
                relative = transcript_path.relative_to(root).as_posix()
                verified[relative] = transcript_sha256
    return dict(sorted(verified.items()))


def _pinned_training_workspace_evidence(
    experiment_root: Path,
    validation_access_policy: Mapping[str, Any],
) -> dict[str, str]:
    """Return exact staged training-input bytes that are safe to skip.

    The exemption is deliberately narrower than a path allowlist.  A staged
    artifact must agree with the self-digested workspace manifest, the redacted
    packet view, the schema-valid canonical packet, and the still-present
    original artifact bytes.  The packet must also use the predefined cumulative
    training batch.  Any mismatch receives no exemption and is scanned normally.
    """

    root = experiment_root.expanduser().resolve()
    rounds = root / "rounds"
    if not rounds.is_dir():
        return {}
    training_ids = set(
        validation_access_policy.get("allowlist_provenance", {}).get(
            "training_protocol_ids", ()
        )
    )
    expected_batches = {
        str(batch["batch_id"]): tuple(batch["protocol_ids"])
        for batch in FINAL_DEVELOPMENT_BATCHES
    }
    allowed: dict[str, str] = {}
    for manifest_path in sorted(rounds.rglob("workspace_manifest.json")):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        workspace = manifest_path.parent
        try:
            workspace_relative = workspace.relative_to(root)
        except ValueError:
            continue
        parts = workspace_relative.parts
        if len(parts) < 4 or parts[0] != "rounds" or parts[2] != "cumulative":
            continue
        batch_id = parts[1]
        expected_protocols = expected_batches.get(batch_id)
        if expected_protocols is None or not set(expected_protocols) <= training_ids:
            continue
        packet_path = root / "rounds" / batch_id / "cumulative" / "packet.json"
        packet_view_path = workspace / "inputs" / "packet_view.json"
        try:
            manifest = _load_json_object(manifest_path, "worker workspace manifest")
            validate_digest(manifest, "workspace_digest")
            packet = load_and_validate(
                packet_path,
                schema_filename="batch_packet.schema.json",
                digest_field="packet_digest",
                label="capability batch packet",
            )
            packet_view = _load_json_object(packet_view_path, "staged packet view")
        except CapabilityImprovementError:
            continue
        if (
            manifest.get("mode")
            not in {
                "improvement_worker",
                "revision_worker",
                "independent_critic",
                "human_review_console",
            }
            or packet.get("batch_id") != batch_id
            or packet.get("branch") != "cumulative"
            or tuple(packet.get("protocol_ids", ())) != expected_protocols
            or packet.get("reveal_state") != "revealed"
            or packet.get("eligibility_status") != "eligible_for_improvement"
            or manifest.get("packet_digest") != packet.get("packet_digest")
            or packet_view.get("packet_digest") is not None
            or packet_view.get("source_packet_digest") != packet.get("packet_digest")
            or tuple(packet_view.get("protocol_ids", ())) != expected_protocols
        ):
            continue
        staged = {
            item.get("path"): item
            for item in manifest.get("staged_files", ())
            if isinstance(item, Mapping) and isinstance(item.get("path"), str)
        }
        packet_view_record = staged.get("inputs/packet_view.json")
        if (
            not isinstance(packet_view_record, Mapping)
            or packet_view_path.is_symlink()
            or not packet_view_path.is_file()
            or packet_view_record.get("sha256") != sha256_file(packet_view_path)
        ):
            continue
        original_artifacts = packet.get("artifacts", ())
        view_artifacts = packet_view.get("artifacts", ())
        if len(original_artifacts) != len(view_artifacts):
            continue
        for original, view in zip(original_artifacts, view_artifacts, strict=True):
            if not isinstance(original, Mapping) or not isinstance(view, Mapping):
                continue
            protocol_id = original.get("protocol_id")
            relative_value = view.get("path")
            if (
                protocol_id not in expected_protocols
                or protocol_id not in training_ids
                or not isinstance(relative_value, str)
                or {key: value for key, value in original.items() if key != "path"}
                != {key: value for key, value in view.items() if key != "path"}
            ):
                continue
            relative_path = Path(relative_value)
            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or len(relative_path.parts) < 4
                or relative_path.parts[:2] != ("inputs", "evidence")
                or relative_path.parts[2] != protocol_id
            ):
                continue
            staged_record = staged.get(relative_value)
            original_path = Path(str(original.get("path", ""))).expanduser()
            evidence_path = workspace / relative_path
            expected_sha = original.get("sha256")
            if (
                not isinstance(staged_record, Mapping)
                or staged_record.get("role") != original.get("role")
                or staged_record.get("sha256") != expected_sha
                or not isinstance(expected_sha, str)
                or original_path.is_symlink()
                or not original_path.is_file()
                or evidence_path.is_symlink()
                or not evidence_path.is_file()
                or sha256_file(original_path) != expected_sha
                or sha256_file(evidence_path) != expected_sha
            ):
                continue
            allowed[evidence_path.relative_to(root).as_posix()] = expected_sha
    return allowed


def validate_validation_isolation_audit(
    path: Path,
    *,
    validation_access_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = load_and_validate(
        path,
        schema_filename="validation_isolation_audit.schema.json",
        digest_field="audit_digest",
        label="validation isolation audit",
    )
    _validate_validation_isolation_semantics(result)
    if validation_access_policy is not None and (
        result["access_policy_digest"] != validation_access_policy["policy_digest"]
        or result["validation_panel_commitment_sha256"]
        != validation_access_policy["validation_panel_commitment_sha256"]
    ):
        raise CapabilityImprovementError(
            "validation isolation audit references another policy"
        )
    return result


def _validate_validation_policy_semantics(
    document: Mapping[str, Any], *, verify_trees: bool
) -> None:
    if document.get("validation_set") != validation_panel_commitment_payload():
        raise CapabilityImprovementError(
            "validation access policy covers another validation set"
        )
    if document.get("validation_panel_commitment_sha256") != (
        validation_panel_commitment_digest()
    ):
        raise CapabilityImprovementError(
            "validation access policy has a stale panel commitment"
        )
    if tuple(document.get("blocked_artifact_roles", ())) != (
        VALIDATION_BLOCKED_ARTIFACT_ROLES
    ) or tuple(document.get("blocked_learning_contexts", ())) != (
        VALIDATION_BLOCKED_CONTEXTS
    ):
        raise CapabilityImprovementError(
            "validation access policy changed the blocked learning boundary"
        )
    contract = document.get("aggregate_feedback_contract") or {}
    if tuple(contract.get("allowed_fields", ())) != VALIDATION_FEEDBACK_FIELDS or (
        tuple(contract.get("prohibited_fields", ()))
        != VALIDATION_PROHIBITED_FEEDBACK_FIELDS
    ):
        raise CapabilityImprovementError(
            "validation aggregate feedback contract changed"
        )
    trees = document.get("protected_trees", ())
    expected_pairs = {
        (protocol_id, role)
        for protocol_id in VALIDATION_PROTOCOL_IDS
        for role in ("target_source", "approved_groundtruth")
    }
    observed_pairs = {(item.get("protocol_id"), item.get("role")) for item in trees}
    if observed_pairs != expected_pairs or len(trees) != 10:
        raise CapabilityImprovementError(
            "validation access policy must pin one source and truth tree per protocol"
        )
    if not set(VALIDATION_PROTOCOL_IDS).issubset(
        set(document.get("forbidden_terms", ()))
    ):
        raise CapabilityImprovementError(
            "validation access policy does not forbid every protocol identifier"
        )
    expected_training = tuple(
        protocol_id
        for batch in FINAL_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    )
    allowlist = document.get("allowlist_provenance") or {}
    if tuple(allowlist.get("training_protocol_ids", ())) != expected_training:
        raise CapabilityImprovementError(
            "validation allowlist covers another training set"
        )
    expected_training_pairs = {
        (protocol_id, role)
        for protocol_id in expected_training
        for role in ("target_source", "approved_groundtruth")
    }
    fingerprints = allowlist.get("training_tree_fingerprints", ())
    if {
        (item.get("protocol_id"), item.get("role")) for item in fingerprints
    } != expected_training_pairs or len(fingerprints) != 50:
        raise CapabilityImprovementError(
            "validation allowlist must pin every training source and truth tree"
        )
    if document.get("sensitive_extraction") != {
        "formats": [
            "utf8_text",
            "pdf_native_text_no_ocr",
            "xlsx_cell_values",
            "docx_xml_text",
        ],
        "unreadable_supported_document_policy": "fail_closed",
    }:
        raise CapabilityImprovementError(
            "validation sensitive extraction policy changed"
        )
    if verify_trees:
        expected_files: list[dict[str, str]] = []
        role_roots: dict[str, set[Path]] = {
            "target_source": set(),
            "approved_groundtruth": set(),
        }
        for item in trees:
            path = Path(item["path"])
            role_roots[item["role"]].add(path.parent)
            digest, count = tree_digest(path)
            if digest != item["tree_digest"] or count != item["file_count"]:
                raise CapabilityImprovementError(
                    "validation protected tree changed: " + path.as_posix()
                )
            for child in _regular_tree_files(path):
                expected_files.append(
                    {
                        "protocol_id": item["protocol_id"],
                        "role": item["role"],
                        "relative_path": child.relative_to(path).as_posix(),
                        "sha256": sha256_file(child),
                    }
                )
        if sorted(
            expected_files,
            key=lambda item: (
                item["role"],
                item["protocol_id"],
                item["relative_path"],
            ),
        ) != document.get("protected_files"):
            raise CapabilityImprovementError(
                "validation protected file inventory changed"
            )
        if any(len(values) != 1 for values in role_roots.values()):
            raise CapabilityImprovementError(
                "validation protected trees do not share canonical role roots"
            )
        training_index = {
            (item["protocol_id"], item["role"]): item for item in fingerprints
        }
        for protocol_id, role in sorted(expected_training_pairs):
            path = next(iter(role_roots[role])) / protocol_id
            digest, count = tree_digest(path)
            expected = training_index[(protocol_id, role)]
            if digest != expected["tree_digest"] or count != expected["file_count"]:
                raise CapabilityImprovementError(
                    "validation training allowlist tree changed: " + path.as_posix()
                )


def _validate_validation_plan_semantics(document: Mapping[str, Any]) -> None:
    if tuple(document.get("checkpoint_labels", ())) != VALIDATION_CHECKPOINT_LABELS:
        raise CapabilityImprovementError("validation plan checkpoint labels changed")
    expected_conditions = [
        {
            "order": order,
            "checkpoint_label": label,
            "trained_protocol_count": int(label[1:]),
            "guidance_target_batch": VALIDATION_GUIDANCE_TARGET[label],
            "expected_trial_count": 5,
            "aggregate_path": f"validation/aggregates/{label}.json",
        }
        for order, label in enumerate(VALIDATION_CHECKPOINT_LABELS, start=1)
    ]
    if document.get("conditions") != expected_conditions:
        raise CapabilityImprovementError("validation plan conditions changed")
    expected_runner = {
        **VALIDATION_RUNNER,
        "configuration_digest": canonical_digest(VALIDATION_RUNNER),
    }
    if document.get("runner") != expected_runner:
        raise CapabilityImprovementError("validation runner configuration changed")
    if (
        document.get("expected_job_count") != 6
        or document.get("trials_per_checkpoint") != 5
        or document.get("expected_trial_count") != 30
    ):
        raise CapabilityImprovementError("validation plan trial counts changed")


def _validate_validation_aggregate_semantics(document: Mapping[str, Any]) -> None:
    if set(document) != set(VALIDATION_AGGREGATE_FIELDS):
        raise CapabilityImprovementError(
            "validation aggregate contains fields outside the aggregate-only contract"
        )
    label = document.get("checkpoint_label")
    if (
        label not in VALIDATION_CHECKPOINT_LABELS
        or document.get("guidance_target_batch") != VALIDATION_GUIDANCE_TARGET[label]
    ):
        raise CapabilityImprovementError(
            "validation aggregate has an invalid checkpoint-to-batch gate"
        )
    if document.get("validation_panel_commitment_sha256") != (
        validation_panel_commitment_digest()
    ):
        raise CapabilityImprovementError(
            "validation aggregate has another panel commitment"
        )
    if (
        document.get("planned_trials") != 5
        or document.get("scored_trials") != 5
        or document.get("unscored_trials") != 0
    ):
        raise CapabilityImprovementError(
            "validation gate requires five fully scored trials"
        )
    if set(document.get("metric_counts", {})) != set(VALIDATION_METRICS) or set(
        document.get("macro_means", {})
    ) != set(VALIDATION_METRICS):
        raise CapabilityImprovementError("validation aggregate metric keys changed")
    for metric in VALIDATION_METRICS:
        count = document["metric_counts"][metric]
        value = document["macro_means"][metric]
        if count != 5 or value is None:
            raise CapabilityImprovementError(
                f"validation gate requires all five values for {metric}"
            )
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
        ):
            raise CapabilityImprovementError(
                f"validation aggregate has invalid mean for {metric}"
            )


def _validate_validation_isolation_semantics(document: Mapping[str, Any]) -> None:
    if tuple(document.get("active_learning_roots", ())) != (
        VALIDATION_ACTIVE_LEARNING_ROOTS
    ):
        raise CapabilityImprovementError(
            "validation isolation audit changed the canonical active roots"
        )
    checks = document.get("checks", ())
    if tuple(item.get("category") for item in checks) != (
        VALIDATION_ISOLATION_CATEGORIES
    ):
        raise CapabilityImprovementError(
            "validation isolation audit categories changed"
        )
    for item in checks:
        expected = "pass" if not item.get("findings") else "fail"
        if item.get("status") != expected:
            raise CapabilityImprovementError(
                "validation isolation status contradicts findings"
            )
    expected_status = (
        "pass" if all(item["status"] == "pass" for item in checks) else "fail"
    )
    if document.get("learning_isolation") != expected_status:
        raise CapabilityImprovementError(
            "validation learning-isolation status contradicts checks"
        )


def _validate_validation_harbor_config(
    config: Mapping[str, Any],
    *,
    config_path: Path,
    checkpoint_root: Path,
    checkpoint: Mapping[str, Any],
    runtime: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> dict[str, str]:
    datasets = config.get("datasets")
    if (
        not isinstance(datasets, list)
        or len(datasets) != 1
        or not isinstance(datasets[0], Mapping)
        or datasets[0].get("task_names") != list(VALIDATION_PROTOCOL_IDS)
    ):
        raise CapabilityImprovementError(
            "validation Harbor config differs from the fixed validation panel"
        )
    tasks_value = datasets[0].get("path")
    if not isinstance(tasks_value, str) or not Path(tasks_value).is_absolute():
        raise CapabilityImprovementError(
            "validation Harbor config requires an absolute frozen task root"
        )
    tasks_root = Path(tasks_value).resolve()
    agents = config.get("agents")
    if not isinstance(agents, list) or len(agents) != 1:
        raise CapabilityImprovementError(
            "validation Harbor config requires one Codex agent"
        )
    agent = agents[0]
    kwargs = agent.get("kwargs") if isinstance(agent, Mapping) else None
    if not isinstance(kwargs, Mapping) or set(kwargs) != {
        "version",
        "reasoning_effort",
    }:
        raise CapabilityImprovementError(
            "validation Harbor config has unpinned Codex kwargs"
        )
    observed = {
        "harness": "harbor",
        "environment": (config.get("environment") or {}).get("type", "docker"),
        "agent": agent.get("name") if isinstance(agent, Mapping) else None,
        "model": agent.get("model_name") if isinstance(agent, Mapping) else None,
        "agent_version": kwargs.get("version") if isinstance(kwargs, Mapping) else None,
        "reasoning_effort": (
            kwargs.get("reasoning_effort") if isinstance(kwargs, Mapping) else None
        ),
        # Harbor omits fields that equal its defaults from resolved config
        # JSON. Its default concurrency is four, which is also the fixed
        # validation runner used by this experiment.
        "concurrency": config.get("n_concurrent_trials", 4),
        "semantic_retries": (config.get("retry") or {}).get("max_retries", 0),
    }
    if observed != VALIDATION_RUNNER:
        raise CapabilityImprovementError(
            "validation Harbor config differs from the fixed runner"
        )
    environment = config.get("environment")
    mounts = environment.get("mounts") if isinstance(environment, Mapping) else None
    if not isinstance(mounts, list) or len(mounts) != 2:
        raise CapabilityImprovementError(
            "validation Harbor config requires exactly the capability-pack and "
            "exemplar-memory mounts"
        )
    if any(not isinstance(item, Mapping) for item in mounts):
        raise CapabilityImprovementError(
            "validation Harbor capability mounts must be objects"
        )
    from .harbor import (
        CAPABILITY_MOUNT_TARGET,
        EXEMPLAR_DIAGNOSTIC_ARTIFACTS,
        EXEMPLAR_MEMORY_MOUNT_TARGET,
        validate_capability_harbor_integration,
    )

    mounts_by_target = {str(item.get("target")): item for item in mounts}
    if set(mounts_by_target) != {
        CAPABILITY_MOUNT_TARGET,
        EXEMPLAR_MEMORY_MOUNT_TARGET,
    }:
        raise CapabilityImprovementError(
            "validation Harbor config is missing a frozen checkpoint mount"
        )
    mount = mounts_by_target[CAPABILITY_MOUNT_TARGET]
    memory_mount = mounts_by_target[EXEMPLAR_MEMORY_MOUNT_TARGET]
    source_value = mount.get("source")
    if not isinstance(source_value, str) or not Path(source_value).is_absolute():
        raise CapabilityImprovementError(
            "validation capability mount source must be absolute"
        )
    exposure_root = Path(source_value).resolve()
    integration_root = exposure_root.parent
    integration = validate_capability_harbor_integration(
        integration_root,
        tasks_root=tasks_root,
    )
    if (
        dict(mount) != integration["mount"]
        or dict(memory_mount) != integration["memory_mount"]
        or exposure_root != (integration_root / "exposure")
        or Path(str(memory_mount.get("source"))).resolve()
        != (integration_root / "exemplar_memory")
    ):
        raise CapabilityImprovementError(
            "validation Harbor config mount differs from its integration manifest"
        )
    from .workflow import checkpoint_exemplar_max_results

    expected_runtime = {
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "checkpoint_sha256": sha256_file(checkpoint_root / "checkpoint.json"),
        "runtime_digest": runtime["runtime_digest"],
        "runtime_sha256": sha256_file(checkpoint_root / "runtime.json"),
        "exemplar_memory": checkpoint["exemplar_memory"],
    }
    observed_runtime = integration.get("checkpoint_runtime")
    exemplar_max_results = checkpoint_exemplar_max_results(runtime)
    if isinstance(observed_runtime, Mapping) and (
        "exemplar_max_results" in observed_runtime
    ):
        expected_runtime["exemplar_max_results"] = exemplar_max_results
    elif exemplar_max_results != 3:
        raise CapabilityImprovementError(
            "legacy validation integration cannot represent a nondefault donor limit"
        )
    if observed_runtime != expected_runtime:
        raise CapabilityImprovementError(
            "validation integration belongs to another checkpoint runtime"
        )
    if (
        integration.get("pack_digest") != pack["pack_digest"]
        or integration.get("pack_manifest_sha256") != checkpoint["pack_manifest_sha256"]
        or integration.get("exemplar_memory") != checkpoint["exemplar_memory"]
        or integration.get("protocol_ids") != list(VALIDATION_PROTOCOL_IDS)
    ):
        raise CapabilityImprovementError(
            "validation integration pack or panel binding is stale"
        )
    instructions = config.get("extra_instruction_paths")
    if instructions != [integration["extra_instruction_path"]]:
        raise CapabilityImprovementError(
            "validation Harbor config instruction differs from its integration"
        )
    artifacts = config.get("artifacts")
    if not isinstance(artifacts, list) or not set(
        EXEMPLAR_DIAGNOSTIC_ARTIFACTS
    ).issubset(artifacts):
        raise CapabilityImprovementError(
            "validation Harbor config does not retain the non-scored exemplar "
            "usage and target-evidence guard artifacts"
        )
    integration_manifest_path = integration_root / "integration_manifest.json"
    return {
        "integration_digest": integration["integration_digest"],
        "integration_manifest_sha256": sha256_file(integration_manifest_path),
        "task_bundle_sha256": integration["task_bundle_sha256"],
        "harbor_config_sha256": sha256_file(config_path),
    }


def _collect_sensitive_text(
    path: Path,
    *,
    terms: set[str],
    sequences: set[str],
    scaffolds: set[str],
    include_names: bool,
) -> str | None:
    if path.suffix.lower() not in _TEXT_SUFFIXES | _NATIVE_DOCUMENT_SUFFIXES:
        return None
    text = _native_source_text(path)
    sequences.update(
        match.group(0).upper() for match in _NUCLEOTIDE_RUN_RE.finditer(text)
    )
    for value in _iter_json_strings(path, text):
        stripped = value.strip()
        if "[" in stripped and "]" in stripped and len(stripped) >= 8:
            scaffolds.add(stripped.lower())
    if include_names and path.suffix.lower() == ".json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(document, Mapping):
            name = document.get("protocol_name")
            if isinstance(name, str) and name.strip():
                terms.add(name.strip().lower())
    return text


def _native_source_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise CapabilityImprovementError(
                f"cannot decode validation text source: {path}"
            ) from error
    if suffix == ".pdf":
        try:
            import fitz
        except ImportError as error:
            raise CapabilityImprovementError(
                "validation PDF extraction requires PyMuPDF"
            ) from error
        try:
            with fitz.open(path) as document:
                return "\n".join(page.get_text("text", sort=True) for page in document)
        except Exception as error:
            raise CapabilityImprovementError(
                f"cannot extract native validation PDF text: {path}: {error}"
            ) from error
    if suffix == ".xlsx":
        try:
            import openpyxl
        except ImportError as error:
            raise CapabilityImprovementError(
                "validation XLSX extraction requires openpyxl"
            ) from error
        try:
            workbook = openpyxl.load_workbook(
                path,
                read_only=True,
                data_only=False,
            )
            try:
                return "\n".join(
                    str(cell.value)
                    for sheet in workbook.worksheets
                    for row in sheet.iter_rows()
                    for cell in row
                    if cell.value is not None
                )
            finally:
                workbook.close()
        except Exception as error:
            raise CapabilityImprovementError(
                f"cannot extract validation XLSX cell text: {path}: {error}"
            ) from error
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
        except (
            KeyError,
            OSError,
            zipfile.BadZipFile,
            ElementTree.ParseError,
        ) as error:
            raise CapabilityImprovementError(
                f"cannot extract validation DOCX text: {path}: {error}"
            ) from error
        return "\n".join(
            node.text or "" for node in root.iter() if node.tag.endswith("}t")
        )
    raise CapabilityImprovementError(
        f"unsupported validation source text format: {path}"
    )


def _scan_validation_file(
    path: Path,
    policy: Mapping[str, Any],
) -> list[str]:
    suffix = path.suffix.lower()
    if suffix not in _TEXT_SUFFIXES | _NATIVE_DOCUMENT_SUFFIXES:
        return [f"unsupported validation-scan file format {suffix or '<none>'}"]
    try:
        text = _native_source_text(path)
    except CapabilityImprovementError as error:
        return [str(error)]
    return _scan_text(text, policy)


def _sequence_equals_allowlist(
    sequence: str,
    allowed_sequences: Iterable[str],
) -> bool:
    normalized = sequence.upper()
    reverse = _reverse_complement(normalized)
    allowed = {item.upper() for item in allowed_sequences}
    return normalized in allowed or reverse in allowed


def _scaffold_equals_allowlist(
    scaffold: str,
    allowed_scaffolds: Iterable[str],
) -> bool:
    normalized = scaffold.strip().lower()
    return normalized in {item.strip().lower() for item in allowed_scaffolds}


def _iter_json_strings(path: Path, text: str) -> Iterable[str]:
    if path.suffix.lower() != ".json":
        return (text,)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return (text,)
    return tuple(_iter_strings(value))


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _iter_strings(item)


def _regular_tree_files(root: Path, *, require_nonempty: bool = True) -> list[Path]:
    if not root.is_dir():
        raise CapabilityImprovementError(f"validation tree is missing: {root}")
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(
                f"validation tree contains a symlink: {path}"
            )
        if path.is_file():
            result.append(path)
    if require_nonempty and not result:
        raise CapabilityImprovementError(f"validation tree is empty: {root}")
    return result


def _scan_json_value(
    document: Mapping[str, Any], policy: Mapping[str, Any]
) -> list[str]:
    issues = _scan_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True), policy
    )
    protected_hashes = _protected_hashes(policy)
    blocked_protocols = set(VALIDATION_PROTOCOL_IDS)
    blocked_roles = set(VALIDATION_BLOCKED_ARTIFACT_ROLES)
    for item in _iter_mappings(document):
        protocol_id = item.get("protocol_id")
        artifact_hash = item.get("sha256") or item.get("artifact_sha256")
        if (
            item.get("schema_version") == VALIDATION_AGGREGATE_SCHEMA_VERSION
            or item.get("feedback_scope") == VALIDATION_LEARNING_VISIBILITY
            or item.get("role") == "validation_macro_aggregate"
        ):
            issues.append("sanitized validation aggregate embedded in learning data")
        if protocol_id in blocked_protocols:
            issues.append(f"validation protocol reference {protocol_id}")
        values = item.get("protocol_ids")
        if isinstance(values, list) and blocked_protocols & set(values):
            issues.append("validation protocol list")
        if artifact_hash in protected_hashes:
            issues.append("protected validation artifact hash")
        if protocol_id in blocked_protocols and item.get("role") in blocked_roles:
            issues.append("raw validation artifact role")
    return sorted(set(issues))


def _scan_text(text: str, policy: Mapping[str, Any]) -> list[str]:
    lowered = text.lower()
    issues = [
        f"protocol term {term}"
        for term in policy.get("forbidden_terms", ())
        if term and _contains_term(lowered, term)
    ]
    issues.extend(
        f"scaffold digest {canonical_digest(scaffold)}"
        for scaffold in policy.get("forbidden_scaffolds", ())
        if scaffold and scaffold in lowered
    )
    forbidden = set(policy.get("forbidden_sequences", ()))
    forbidden_pairs = tuple(
        (item, _reverse_complement(item)) for item in sorted(forbidden)
    )
    for match in _NUCLEOTIDE_RUN_RE.finditer(text):
        value = match.group(0).upper()
        if any(
            value in sequence
            or sequence in value
            or value in reverse
            or reverse in value
            for sequence, reverse in forbidden_pairs
        ):
            issues.append("exact sequence overlap at text byte " + str(match.start()))
    return sorted(set(issues))


def _contains_term(text: str, term: str) -> bool:
    """Match a protected name without treating it as an identifier prefix."""

    return (
        re.search(
            rf"(?<![a-z0-9_]){re.escape(term.lower())}(?![a-z0-9_])",
            text.lower(),
        )
        is not None
    )


def _iter_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _iter_mappings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _iter_mappings(item)


def _protected_hashes(policy: Mapping[str, Any]) -> set[str]:
    return {str(item["sha256"]) for item in policy.get("protected_files", ())}


def _protected_hash_owners(policy: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item["sha256"]): str(item["protocol_id"])
        for item in policy.get("protected_files", ())
    }


def _finding(path: str, detail: str) -> dict[str, str]:
    return {"path": path, "detail_sha256": canonical_digest(detail)}


def _reverse_complement(value: str) -> str:
    return value.translate(
        str.maketrans(
            "ACGTRYSWKMBDHVN",
            "TGCAYRSWMKVHDBN",
        )
    )[::-1]


def _mean_or_none(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CapabilityImprovementError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise CapabilityImprovementError(f"{label} must be a JSON object")
    return value


def _assert_no_symlink_chain(path: Path, *, label: str) -> None:
    absolute = path.absolute()
    for candidate in reversed((absolute, *absolute.parents)):
        if candidate.is_symlink():
            raise CapabilityImprovementError(
                f"{label} contains a symlink component: {candidate}"
            )


def _assert_canonical_output_path(root: Path, output_path: Path) -> None:
    expected_parent = root / "validation" / "aggregates"
    if output_path.parent != expected_parent or root not in output_path.parents:
        raise CapabilityImprovementError(
            "validation aggregate output escapes its canonical directory"
        )
    current = root
    for component in ("validation", "aggregates"):
        current = current / component
        if current.exists() and current.is_symlink():
            raise CapabilityImprovementError(
                f"validation aggregate output contains a symlink: {current}"
            )
