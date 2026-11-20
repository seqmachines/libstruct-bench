from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from libstruct_bench.archive.hooks import agent_env, host_hook
from libstruct_bench.audit.artifacts import (
    load_json_object,
    normalize_timestamp,
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.libgen.validation import (
    LibgenValidationError,
    validate_prediction_links,
    validate_t2_prediction,
    validate_t3_prediction,
)
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    improvement_schema_root,
    validate_capability_pack,
    with_digest,
    write_capability_pack_manifest,
)
from .kb_guided import (
    ADJUDICATION_SCHEMA,
    BASELINE_TRAINING_PLAN_SCHEMA,
    BEHAVIORAL_LINEAGE_SCHEMA,
    CHECKPOINT_SCHEMA,
    COMPOSITE_ENDPOINT_SCHEMA,
    DESIGN_SCHEMA,
    MICROSTATE_SCHEMA,
    TRAINING_BATCH_PLAN_SCHEMA,
    VALIDATION_SELECTION_SCHEMA,
    VALIDATION_SELECTION_SCHEMA_VERSION,
    _validate_baseline_trial_pin,
    compile_kb_training_adjudication,
    lock_kb_final,
    plan_next_kb_behavioral_training_batch,
    plan_kb_checkpoint_evaluation,
    plan_kb_composite_evaluation,
    plan_next_kb_training_run,
    validate_kb_training_adjudication,
    validate_kb_experiment,
)


COMPARISON_SCHEMA_VERSION = "libstruct.libgen_kb_comparison.v1"
COMPARISON_SCHEMA = "kb_comparison.schema.json"
ADJUDICATOR_OUTPUT_SCHEMA = "kb_adjudicator_output.schema.json"
CRITIC_OUTPUT_SCHEMA = "kb_pact_critic_output.schema.json"
REPAIR_OUTPUT_SCHEMA = "kb_repair_output.schema.json"
REPAIR_MANIFEST_SCHEMA_VERSION = "libstruct.libgen_kb_repair_manifest.v1"
REPAIR_MANIFEST_SCHEMA = "kb_repair_manifest.schema.json"
RUN_RECEIPT_SCHEMA_VERSION = "libstruct.libgen_kb_run_receipt.v1"
RUN_RECEIPT_SCHEMA = "kb_run_receipt.schema.json"
EVALUATION_AGGREGATE_SCHEMA_VERSION = "libstruct.libgen_kb_evaluation_aggregate.v1"
EVALUATION_AGGREGATE_SCHEMA = "kb_evaluation_aggregate.schema.json"
EVALUATION_RECOVERY_SCHEMA_VERSION = (
    "libstruct.libgen_kb_evaluation_infrastructure_recovery.v1"
)
EVALUATION_RECOVERY_SCHEMA = "kb_evaluation_infrastructure_recovery.schema.json"
CYCLE_RECORD_SCHEMA = "kb_cycle_record.schema.json"
TRAINING_LOOP_RECEIPT_SCHEMA_VERSION = "libstruct.libgen_kb_training_loop_receipt.v1"
TRAINING_LOOP_RECEIPT_SCHEMA = "kb_training_loop_receipt.schema.json"
BATCH_AMENDMENT_SCHEMA_VERSION = (
    "libstruct.libgen_kb_batch_execution_amendment.v2"
)
BATCH_AMENDMENT_SCHEMA = "kb_batch_execution_amendment.schema.json"
BATCH_RECORD_SCHEMA_VERSION = "libstruct.libgen_kb_batch_record.v1"
BATCH_RECORD_SCHEMA = "kb_batch_record.schema.json"
FINAL_REPLAY_RECEIPT_SCHEMA_VERSION = (
    "libstruct.libgen_kb_final_replay_receipt.v1"
)
FINAL_REPLAY_RECEIPT_SCHEMA = "kb_final_replay_receipt.schema.json"
FINAL_REPLAY_RECOVERY_SCHEMA_VERSION = (
    "libstruct.libgen_kb_final_replay_recovery.v1"
)
FINAL_REPLAY_RECOVERY_SCHEMA = "kb_final_replay_recovery.schema.json"
COMPOSITE_ENDPOINT_SCHEMA_VERSION = "libstruct.libgen_kb_composite_endpoint.v1"
COMPOSITE_CONFIRMATION_SCHEMA_VERSION = (
    "libstruct.libgen_kb_composite_confirmation.v1"
)
COMPOSITE_CONFIRMATION_SCHEMA = "kb_composite_confirmation.schema.json"
ERROR_ID_RE = re.compile(r"^err_[0-9]{4,}$")
METRIC_KEYS = (
    "reward",
    "t2_exact_required_family_recall",
    "t2_required_family_f1",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)
VALIDATION_COMPONENT_METRICS = (
    "t2_required_family_f1",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)


CodexRunner = Callable[..., dict[str, Any]]


def validate_kb_pact(pact_root: Path) -> dict[str, Any]:
    """Validate a learned pact and its inherited deterministic control suite."""

    manifest = validate_capability_pack(pact_root)
    playbook = pact_root.expanduser().resolve() / "PLAYBOOK.md"
    if not playbook.is_file() or playbook.is_symlink():
        raise CapabilityImprovementError("learned pact lacks a safe PLAYBOOK.md")
    rules_path = pact_root.expanduser().resolve() / "checklists/learned_rules.json"
    if rules_path.exists():
        rules = load_json_object(rules_path, label="learned pact rules")
        _validate_learned_rules(rules)
    return manifest


def build_kb_comparison(
    *,
    experiment_root: Path,
    trial_root: Path,
    training_ordinal: int,
    output_path: Path,
    created_at: str,
    repair_attempts: int = 0,
    batch_parent_state_id: str | None = None,
) -> dict[str, Any]:
    """Pin one verifier result without reinterpreting its observations."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"], DESIGN_SCHEMA, "design_digest", "KB design"
    )
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "KB microstate",
    )
    if batch_parent_state_id is None:
        if training_ordinal != state["training_count"] + 1:
            raise CapabilityImprovementError(
                "comparison ordinal is not the next state"
            )
    else:
        if batch_parent_state_id != state["state_id"]:
            raise CapabilityImprovementError(
                "batch comparison does not match the current parent state"
            )
        batch_end = next(
            item["after_training_count"]
            for item in design["learned_checkpoints"]
            if item["after_training_count"] > state["training_count"]
        )
        if not state["training_count"] < training_ordinal <= batch_end:
            raise CapabilityImprovementError(
                "batch comparison ordinal lies outside the next checkpoint batch"
            )
    protocol_id = design["split"]["training"][training_ordinal - 1]
    trial = trial_root.expanduser().resolve()
    artifacts = resolve_trial_artifacts(trial, protocol_id=protocol_id)
    t2 = _load_json_or_none(artifacts["t2_prediction"])
    t3 = _load_json_or_none(artifacts["t3_prediction"])
    validation_errors = linked_prediction_errors(protocol_id=protocol_id, t2=t2, t3=t3)
    details = load_json_object(artifacts["verifier_details"], label="verifier details")
    if details.get("benchmark_version") != LIBGEN_BENCHMARK_VERSION:
        raise CapabilityImprovementError(
            "training comparison requires verifier " + LIBGEN_BENCHMARK_VERSION
        )
    if details.get("protocol_id") != protocol_id:
        raise CapabilityImprovementError("verifier details protocol changed")
    reward = load_json_object(artifacts["reward"], label="verifier reward")
    metrics = {
        key: float(reward[key])
        for key in METRIC_KEYS
        if isinstance(reward.get(key), (int, float))
    }
    error_analysis = load_json_object(
        artifacts["error_analysis"], label="verifier error analysis"
    )
    observation_ids = [
        item.get("error_id") for item in error_analysis.get("observations", [])
    ]
    if any(
        not isinstance(value, str) or not ERROR_ID_RE.fullmatch(value)
        for value in observation_ids
    ):
        raise CapabilityImprovementError("verifier emitted malformed observation IDs")
    if len(observation_ids) != len(set(observation_ids)):
        raise CapabilityImprovementError("verifier emitted duplicate observation IDs")
    document = with_digest(
        {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "state_id": state["state_id"],
            "protocol_id": protocol_id,
            "training_ordinal": training_ordinal,
            "trial_root": trial.as_posix(),
            "prediction_validation": {
                "status": (
                    "missing"
                    if t2 is None or t3 is None
                    else "invalid"
                    if validation_errors
                    else "valid"
                ),
                "errors": validation_errors,
                "repair_attempts": repair_attempts,
            },
            "metrics": metrics,
            "observation_ids": observation_ids,
            "artifacts": {
                **{
                    key: _absolute_artifact(path)
                    for key, path in artifacts.items()
                    if key
                    in {
                        "result",
                        "t2_prediction",
                        "t3_prediction",
                        "trajectory",
                        "reward",
                        "verifier_details",
                        "error_analysis",
                    }
                },
                "repair_manifest": (
                    None
                    if artifacts.get("repair_manifest") is None
                    else _absolute_artifact(artifacts["repair_manifest"])
                ),
            },
            "created_at": normalize_timestamp(created_at),
        },
        "comparison_digest",
    )
    _validate_generated(
        document, COMPARISON_SCHEMA, "comparison_digest", "KB comparison"
    )
    destination = output_path.expanduser().resolve()
    if destination.exists():
        existing = _load_validated(
            destination,
            COMPARISON_SCHEMA,
            "comparison_digest",
            "existing KB comparison",
        )
        if existing != document:
            raise CapabilityImprovementError(
                "existing comparison differs from requested bytes"
            )
        return existing
    write_json_atomic(destination, document, mode=0o444)
    return document


def resolve_trial_artifacts(
    trial_root: Path, *, protocol_id: str
) -> dict[str, Path | None]:
    trial = trial_root.expanduser().resolve()
    if not trial.is_dir() or trial.is_symlink():
        raise CapabilityImprovementError(f"unsafe or missing trial root: {trial}")
    result = trial / "result.json"
    prediction_candidates = {
        "t2_prediction": (
            trial / "artifacts/logs/artifacts/t2_prediction.json",
            trial / "artifacts/t2_prediction.json",
        ),
        "t3_prediction": (
            trial / "artifacts/logs/artifacts/t3_prediction.json",
            trial / "artifacts/t3_prediction.json",
        ),
        "trajectory": (
            trial / "agent/trajectory.json",
            trial / "artifacts/agent_trajectory.json",
        ),
    }
    selected: dict[str, Path] = {"result": result}
    for key, candidates in prediction_candidates.items():
        selected[key] = next(
            (path for path in candidates if path.is_file()), candidates[0]
        )
    verifier_roots = [
        trial / "verifier",
        trial / "verifier/rescore" / f"libgen-{LIBGEN_BENCHMARK_VERSION}",
    ]
    verifier_root = next(
        (
            candidate
            for candidate in verifier_roots
            if _verifier_root_matches(candidate, protocol_id)
        ),
        None,
    )
    if verifier_root is None:
        raise CapabilityImprovementError(
            f"trial lacks LibGen {LIBGEN_BENCHMARK_VERSION} verifier artifacts: {trial}"
        )
    selected.update(
        {
            "reward": verifier_root / "reward.json",
            "verifier_details": verifier_root / "details.json",
            "error_analysis": verifier_root / "error_analysis.json",
        }
    )
    repair_manifest = trial / "repair_manifest.json"
    selected["repair_manifest"] = repair_manifest if repair_manifest.is_file() else None
    missing = [
        key
        for key, path in selected.items()
        if key != "repair_manifest"
        and (path is None or not path.is_file() or path.is_symlink())
    ]
    if missing:
        raise CapabilityImprovementError(
            f"trial is missing required artifacts {missing}: {trial}"
        )
    return selected


def linked_prediction_errors(*, protocol_id: str, t2: Any, t3: Any) -> list[str]:
    if not isinstance(t2, dict) or not isinstance(t3, dict):
        return ["both complete T2 and T3 JSON objects are required"]
    errors: list[str] = []
    for label, validator, value in (
        ("T2", validate_t2_prediction, t2),
        ("T3", validate_t3_prediction, t3),
    ):
        try:
            validator(value, protocol_id=protocol_id)
        except LibgenValidationError as error:
            errors.append(f"{label}: {error}")
    if not errors:
        try:
            validate_prediction_links(t2, t3)
        except LibgenValidationError as error:
            errors.append(f"T2/T3 links: {error}")
    return errors


def stage_kb_adjudication_workspace(
    *,
    experiment_root: Path,
    comparison_path: Path,
    output_root: Path,
) -> Path:
    """Stage only the next training target's approved evidence and frozen artifacts."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    comparison = _load_validated(
        comparison_path, COMPARISON_SCHEMA, "comparison_digest", "KB comparison"
    )
    protocol_id = comparison["protocol_id"]
    workspace = output_root.expanduser().resolve()
    if workspace.exists():
        _validate_staged_kb_adjudication_workspace(
            workspace=workspace,
            comparison_path=comparison_path,
            comparison=comparison,
            experiment_root=root,
            manifest=manifest,
        )
        return workspace
    stage = Path(
        tempfile.mkdtemp(prefix=f".{workspace.name}.stage-", dir=workspace.parent)
    )
    try:
        inputs = stage / "inputs"
        (inputs / "groundtruth").mkdir(parents=True)
        (inputs / "predictions").mkdir()
        (inputs / "policies").mkdir()
        (inputs / "sources").mkdir()
        shutil.copy2(comparison_path, inputs / "comparison.json")
        for key, filename in (
            ("t2_prediction", "t2_prediction.json"),
            ("t3_prediction", "t3_prediction.json"),
            ("trajectory", "trajectory.json"),
            ("verifier_details", "verifier_details.json"),
            ("error_analysis", "error_analysis.json"),
        ):
            destination = (
                inputs / "predictions" / filename
                if key.endswith("prediction")
                else inputs / filename
            )
            shutil.copy2(Path(comparison["artifacts"][key]["path"]), destination)
        groundtruth_root = Path(manifest["private_roots"]["groundtruth_root"])
        for filename in (
            "groundtruth_final_lib_struct.json",
            "groundtruth_oligos.json",
            "groundtruth_library_generation_workflow.json",
        ):
            shutil.copy2(
                groundtruth_root / protocol_id / filename,
                inputs / "groundtruth" / filename,
            )
        source_manifest = load_json_object(
            root / "design/source_manifests" / f"{protocol_id}.json",
            label=f"{protocol_id} source manifest",
        )
        source_base = groundtruth_root.parent
        source_index = []
        for index, source in enumerate(source_manifest["sources"], start=1):
            if (
                source.get("role") != "primary_evidence"
                or source.get("approval_status") != "included"
            ):
                continue
            source_path = (source_base / source["path"]).resolve()
            if not source_path.is_file() or source_path.is_symlink():
                raise CapabilityImprovementError(
                    f"approved source is unavailable: {source_path}"
                )
            if sha256_file(source_path) != source["sha256"]:
                raise CapabilityImprovementError(
                    f"approved source digest changed: {source_path}"
                )
            safe_name = f"{index:03d}-{Path(source['path']).name}"
            shutil.copy2(source_path, inputs / "sources" / safe_name)
            source_index.append(
                {
                    "source_id": source["source_id"],
                    "file": safe_name,
                    "original_path": source["path"],
                    "sha256": source["sha256"],
                }
            )
        write_json_atomic(
            inputs / "source_index.json", {"sources": source_index}, mode=0o444
        )
        repository_root = Path(str(manifest["repository"]["path"])).resolve()
        for relative in (
            "docs/audit/README.md",
            "docs/audit/evidence-policy.md",
            "docs/audit/adjudication-policy.md",
            "docs/audit/benchmark-standardization-policy.md",
        ):
            source = repository_root / relative
            destination = inputs / "policies" / Path(relative).name
            if not source.is_file() or source.is_symlink():
                raise CapabilityImprovementError(
                    f"adjudication policy input is unavailable: {source}"
                )
            shutil.copy2(source, destination)
        state = _load_validated(
            root / manifest["current_microstate"]["path"],
            MICROSTATE_SCHEMA,
            "microstate_digest",
            "current KB microstate",
        )
        current_rules = {
            "schema_version": "libstruct.libgen_kb_learned_rules.v1",
            "rules": [],
        }
        if state["pact"]["kind"] == "learned":
            rules_path = root / state["pact"]["path"] / "checklists/learned_rules.json"
            current_rules = load_json_object(rules_path, label="current learned rules")
            _validate_learned_rules(current_rules)
        write_json_atomic(
            inputs / "current_learned_rules.json", current_rules, mode=0o444
        )
        (stage / "outputs").mkdir()
        _freeze_read_inputs(inputs)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, workspace)
        _validate_staged_kb_adjudication_workspace(
            workspace=workspace,
            comparison_path=comparison_path,
            comparison=comparison,
            experiment_root=root,
            manifest=manifest,
        )
        return workspace
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _validate_staged_kb_adjudication_workspace(
    *,
    workspace: Path,
    comparison_path: Path,
    comparison: Mapping[str, Any],
    experiment_root: Path,
    manifest: Mapping[str, Any],
) -> None:
    """Reject partial or stale workspaces before a local Codex process starts."""

    inputs = workspace / "inputs"
    required = {
        "comparison": inputs / "comparison.json",
        "t2_prediction": inputs / "predictions/t2_prediction.json",
        "t3_prediction": inputs / "predictions/t3_prediction.json",
        "trajectory": inputs / "trajectory.json",
        "verifier_details": inputs / "verifier_details.json",
        "error_analysis": inputs / "error_analysis.json",
        "source_index": inputs / "source_index.json",
        "current_rules": inputs / "current_learned_rules.json",
    }
    groundtruth_names = {
        "T1": "groundtruth_final_lib_struct.json",
        "T2": "groundtruth_oligos.json",
        "T3": "groundtruth_library_generation_workflow.json",
    }
    for task, filename in groundtruth_names.items():
        required[f"groundtruth_{task}"] = inputs / "groundtruth" / filename
    for filename in (
        "README.md",
        "evidence-policy.md",
        "adjudication-policy.md",
        "benchmark-standardization-policy.md",
    ):
        required[f"policy_{filename}"] = inputs / "policies" / filename
    missing = [
        label
        for label, path in required.items()
        if not path.is_file() or path.is_symlink()
    ]
    if not (workspace / "outputs").is_dir() or (workspace / "outputs").is_symlink():
        missing.append("outputs")
    if missing:
        raise CapabilityImprovementError(
            "existing adjudication workspace is incomplete: " + ", ".join(missing)
        )
    if sha256_file(required["comparison"]) != sha256_file(
        comparison_path.expanduser().resolve()
    ):
        raise CapabilityImprovementError(
            "existing adjudication workspace pins another comparison"
        )
    for key in (
        "t2_prediction",
        "t3_prediction",
        "trajectory",
        "verifier_details",
        "error_analysis",
    ):
        if sha256_file(required[key]) != comparison["artifacts"][key]["sha256"]:
            raise CapabilityImprovementError(
                f"existing adjudication workspace changed its {key}"
            )
    protocol_entry = next(
        item
        for item in manifest["universe"]["entries"]
        if item["protocol_id"] == comparison["protocol_id"]
    )
    for task in groundtruth_names:
        if (
            sha256_file(required[f"groundtruth_{task}"])
            != protocol_entry["groundtruth"][task]["sha256"]
        ):
            raise CapabilityImprovementError(
                f"existing adjudication workspace changed ground truth {task}"
            )
    source_index = load_json_object(
        required["source_index"], label="staged source index"
    )
    for source in source_index.get("sources", []):
        path = inputs / "sources" / str(source.get("file", ""))
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != source.get("sha256")
        ):
            raise CapabilityImprovementError(
                f"existing adjudication workspace changed source {path.name}"
            )
    state = _load_validated(
        experiment_root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "current KB microstate",
    )
    expected_rules = {
        "schema_version": "libstruct.libgen_kb_learned_rules.v1",
        "rules": [],
    }
    if state["pact"]["kind"] == "learned":
        expected_rules = load_json_object(
            experiment_root
            / state["pact"]["path"]
            / "checklists/learned_rules.json",
            label="current learned rules",
        )
    observed_rules = load_json_object(
        required["current_rules"], label="staged learned rules"
    )
    if observed_rules != expected_rules:
        raise CapabilityImprovementError(
            "existing adjudication workspace pins another cumulative pact"
        )
    repository_root = Path(str(manifest["repository"]["path"])).resolve()
    for filename in (
        "README.md",
        "evidence-policy.md",
        "adjudication-policy.md",
        "benchmark-standardization-policy.md",
    ):
        source = repository_root / "docs/audit" / filename
        if sha256_file(required[f"policy_{filename}"]) != sha256_file(source):
            raise CapabilityImprovementError(
                f"existing adjudication workspace changed policy {filename}"
            )


def _validate_codex_workspace_access(
    *,
    event_path: Path,
    workspace: Path,
    experiment_root: Path,
    manifest: Mapping[str, Any],
) -> None:
    """Reject model commands that read unpinned protocol or repository files."""

    allowed_workspace = workspace.resolve().as_posix()
    repository_root = Path(str(manifest["repository"]["path"])).resolve()
    # The local Codex runtime is required to read the repository's generic audit
    # skill before performing an independent protocol audit.  It contains no
    # protocol evidence, predictions, ground truth, or learned memory, so allow
    # this one exact instruction file while keeping the rest of the repository
    # outside the adjudicator's evidence boundary.
    allowed_instruction_files = {
        (repository_root / "paper/audit/.agents/skills/libstruct-audit/SKILL.md")
        .resolve()
        .as_posix()
    }
    forbidden_roots = {
        experiment_root.resolve().as_posix(),
        repository_root.as_posix(),
        Path(str(manifest["private_roots"]["groundtruth_root"]))
        .resolve()
        .parent.as_posix(),
        Path(str(manifest["private_roots"]["baseline_root"])).resolve().as_posix(),
        Path(str(manifest["private_roots"]["run_root"])).resolve().as_posix(),
        Path(str(manifest["private_roots"]["source_manifest_search_root"]))
        .resolve()
        .as_posix(),
    }
    violations: list[str] = []
    for line_number, event in _read_codex_jsonl_events(event_path):
        item = event.get("item")
        if not isinstance(item, Mapping) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if not isinstance(command, str):
            continue
        external = command.replace(allowed_workspace, "<WORKSPACE>")
        for allowed_file in allowed_instruction_files:
            external = external.replace(allowed_file, "<PINNED_AUDIT_SKILL>")
        matched = sorted(root for root in forbidden_roots if root in external)
        # URL literals commonly occur in local XML namespaces and source text.
        # Detect executable network mechanisms instead of rejecting any URL-like
        # string embedded in an otherwise local parsing command.
        network = bool(
            re.search(
                r"(?:"
                r"\b(?:curl|wget|aria2c|scp|sftp|ssh)\b|"
                r"\bgit\s+(?:clone|fetch|pull)\b|"
                r"/dev/(?:tcp|udp)/|"
                r"\b(?:requests|httpx|aiohttp|urllib\.request|urlopen)\b"
                r")",
                external,
            )
        )
        if matched or network:
            violations.append(
                f"line {line_number}: external_roots={matched}, network={network}"
            )
    if violations:
        raise CapabilityImprovementError(
            "local Codex left its pinned adjudication workspace: "
            + "; ".join(violations[:5])
        )


def _read_codex_jsonl_events(event_path: Path) -> list[tuple[int, Mapping[str, Any]]]:
    """Read JSONL while tolerating two known Codex output-framing defects.

    Literal output newlines are rejoined. A command-completion event truncated
    inside its final unicode escape is ignored only when a valid earlier
    command-start event with the same id and exact command was already parsed;
    the security-relevant command has therefore already been inspected.
    """

    try:
        lines = event_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise CapabilityImprovementError(
            f"cannot inspect local Codex transcript: {error}"
        ) from error
    events: list[tuple[int, Mapping[str, Any]]] = []
    started_commands: dict[str, str] = {}
    buffer: list[str] = []
    start_line = 1
    for line_number, line in enumerate(lines, start=1):
        if not buffer:
            start_line = line_number
        buffer.append(line)
        try:
            candidate = _join_codex_json_fragments(buffer)
            event = json.loads(candidate)
        except json.JSONDecodeError as error:
            if error.msg.startswith("Unterminated string"):
                continue
            if (
                error.msg == "Invalid \\uXXXX escape"
                and error.pos >= len(candidate) - 8
            ):
                continue
            raise CapabilityImprovementError(
                f"local Codex transcript line {start_line} is invalid JSON"
            ) from error
        if not isinstance(event, Mapping):
            raise CapabilityImprovementError(
                f"local Codex transcript line {start_line} is not an object"
            )
        events.append((start_line, event))
        item = event.get("item")
        if isinstance(item, Mapping) and item.get("type") == "command_execution":
            item_id = item.get("id")
            command = item.get("command")
            if (
                event.get("type") == "item.started"
                and isinstance(item_id, str)
                and isinstance(command, str)
            ):
                started_commands[item_id] = command
        buffer = []
    if buffer and _safe_truncated_command_completion(
        _join_codex_json_fragments(buffer), started_commands=started_commands
    ):
        buffer = []
    if buffer:
        raise CapabilityImprovementError(
            f"local Codex transcript line {start_line} is truncated JSON"
        )
    return events


def _join_codex_json_fragments(fragments: Sequence[str]) -> str:
    candidate = ""
    for fragment in fragments:
        if not candidate:
            candidate = fragment
        elif re.search(r"\\u[0-9a-fA-F]{0,3}$", candidate):
            # Codex occasionally inserts a physical JSONL boundary in the
            # middle of a JSON unicode escape. That boundary is framing, not
            # command output, so concatenate it directly.
            candidate += fragment
        else:
            # Other physical boundaries inside an unterminated JSON string are
            # literal command-output newlines and must become an escaped JSON
            # newline before parsing.
            candidate += "\\n" + fragment
    return candidate


def _safe_truncated_command_completion(
    candidate: str, *, started_commands: Mapping[str, str]
) -> bool:
    prefix = re.match(
        r'^\{"type":"item\.completed","item":\{"id":"([^"]+)",'
        r'"type":"command_execution",',
        candidate,
    )
    if prefix is None:
        return False
    item_id = prefix.group(1)
    expected_command = started_commands.get(item_id)
    if expected_command is None:
        return False
    command_match = re.search(
        r'"command":("(?:\\.|[^"\\])*")', candidate[prefix.end() :]
    )
    if command_match is None:
        return False
    try:
        observed_command = json.loads(command_match.group(1))
    except json.JSONDecodeError:
        return False
    return observed_command == expected_command


def run_kb_adjudication(
    *,
    experiment_root: Path,
    comparison_path: Path,
    workspace_root: Path,
    adjudication_path: Path,
    created_at: str,
    codex_executable: str = "codex",
    timeout_seconds: int = 7200,
    runner: CodexRunner | None = None,
) -> dict[str, Any]:
    """Run one source-grounded Codex adjudication and conditional independent critic."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    comparison = _load_validated(
        comparison_path, COMPARISON_SCHEMA, "comparison_digest", "KB comparison"
    )
    destination = adjudication_path.expanduser().resolve()
    if destination.is_file() and not destination.is_symlink():
        existing = _load_validated(
            destination,
            ADJUDICATION_SCHEMA,
            "decision_digest",
            "existing KB training adjudication",
        )
        validate_kb_training_adjudication(existing)
        if (
            existing["design_digest"] != comparison["design_digest"]
            or existing["protocol_id"] != comparison["protocol_id"]
            or existing["training_ordinal"] != comparison["training_ordinal"]
        ):
            raise CapabilityImprovementError(
                "existing adjudication does not match the requested comparison"
            )
        comparison_artifact = existing["artifacts"]["comparison"]
        if comparison_artifact["sha256"] != sha256_file(
            comparison_path.expanduser().resolve()
        ):
            raise CapabilityImprovementError(
                "existing adjudication pins a different comparison"
            )
        candidate_pact: Path | None = None
        if existing["pact_decision"]["action"] == "accept_change":
            candidate_pact = (
                workspace_root.expanduser().resolve() / "outputs/candidate_pact"
            )
            pack = validate_kb_pact(candidate_pact)
            if (
                pack["pack_digest"]
                != existing["pact_decision"]["candidate_pact_digest"]
            ):
                raise CapabilityImprovementError(
                    "existing candidate pact differs from its adjudication"
                )
        return {
            "adjudication": existing,
            "adjudication_path": destination.as_posix(),
            "pact_root": (
                None if candidate_pact is None else candidate_pact.as_posix()
            ),
        }
    workspace = stage_kb_adjudication_workspace(
        experiment_root=root,
        comparison_path=comparison_path,
        output_root=workspace_root,
    )
    outputs = workspace / "outputs"
    adjudicator_output = outputs / "adjudicator.json"
    adjudicator_events = outputs / "adjudicator.events.jsonl"
    adjudicator_stderr = outputs / "adjudicator.stderr.log"
    prompt = _adjudicator_prompt(comparison)
    actual_runner = runner or run_local_codex_json
    if not adjudicator_output.is_file() or not adjudicator_events.is_file():
        actual_runner(
            workspace=workspace,
            prompt=prompt,
            schema_path=improvement_schema_root() / ADJUDICATOR_OUTPUT_SCHEMA,
            output_path=adjudicator_output,
            event_path=adjudicator_events,
            stderr_path=adjudicator_stderr,
            codex_executable=codex_executable,
            timeout_seconds=timeout_seconds,
            reasoning_effort="max",
        )
    _validate_codex_workspace_access(
        event_path=adjudicator_events,
        workspace=workspace,
        experiment_root=root,
        manifest=manifest,
    )
    adjudicator = load_json_object(adjudicator_output, label="Codex adjudicator output")
    validate_document(
        adjudicator,
        improvement_schema_root() / ADJUDICATOR_OUTPUT_SCHEMA,
        label="Codex adjudicator output",
    )
    try:
        _validate_adjudicator_output(adjudicator, comparison, workspace)
    except CapabilityImprovementError as error:
        corrected_output = outputs / "adjudicator.corrected.json"
        corrected_events = outputs / "adjudicator.corrected.events.jsonl"
        corrected_stderr = outputs / "adjudicator.corrected.stderr.log"
        if not corrected_output.is_file() or not corrected_events.is_file():
            actual_runner(
                workspace=workspace,
                prompt=_adjudicator_contract_repair_prompt(
                    adjudicator=adjudicator, error=str(error)
                ),
                schema_path=improvement_schema_root() / ADJUDICATOR_OUTPUT_SCHEMA,
                output_path=corrected_output,
                event_path=corrected_events,
                stderr_path=corrected_stderr,
                codex_executable=codex_executable,
                timeout_seconds=min(timeout_seconds, 1800),
                reasoning_effort="high",
            )
        _validate_codex_workspace_access(
            event_path=corrected_events,
            workspace=workspace,
            experiment_root=root,
            manifest=manifest,
        )
        corrected = load_json_object(
            corrected_output, label="corrected Codex adjudicator output"
        )
        validate_document(
            corrected,
            improvement_schema_root() / ADJUDICATOR_OUTPUT_SCHEMA,
            label="corrected Codex adjudicator output",
        )
        _validate_adjudicator_output(corrected, comparison, workspace)
        invalid_action_ids = {
            item["finding_id"]
            for item in adjudicator["findings"]
            if not item["learning_eligible"]
            and item["recommended_action"] not in {"none", "knowledge_card_only"}
        }
        raw_projection = dict(adjudicator)
        corrected_projection = dict(corrected)
        raw_projection["findings"] = [
            (
                {
                    key: value
                    for key, value in item.items()
                    if key != "recommended_action"
                }
                if item["finding_id"] in invalid_action_ids
                else item
            )
            for item in adjudicator["findings"]
        ]
        corrected_projection["findings"] = [
            (
                {
                    key: value
                    for key, value in item.items()
                    if key != "recommended_action"
                }
                if item["finding_id"] in invalid_action_ids
                else item
            )
            for item in corrected["findings"]
        ]
        if not invalid_action_ids or corrected_projection != raw_projection:
            raise CapabilityImprovementError(
                "adjudicator contract repair changed scientific scope or proposal"
            )
        transcript_manifest = outputs / "adjudicator.transcript.json"
        transcript = {
            "schema_version": "libstruct.libgen_kb_adjudicator_transcript.v1",
            "contract_error": str(error),
            "raw_output": _absolute_artifact(adjudicator_output),
            "raw_transcript": _absolute_artifact(adjudicator_events),
            "corrected_output": _absolute_artifact(corrected_output),
            "correction_transcript": _absolute_artifact(corrected_events),
        }
        write_json_atomic(transcript_manifest, transcript, mode=0o444)
        adjudicator = corrected
        adjudicator_output = corrected_output
        adjudicator_events = transcript_manifest

    proposal = adjudicator["pact_proposal"]
    critic_output: Path | None = None
    critic_events: Path | None = None
    critic: dict[str, Any] | None = None
    candidate_pact: Path | None = None
    candidate_pact_report: Path | None = None
    action = "no_change"
    rationale = "No protocol-neutral pact change was proposed."
    if proposal is not None:
        proposal_path = outputs / "pact_proposal.json"
        write_json_atomic(proposal_path, proposal, mode=0o444)
        proposal_digest = canonical_digest(proposal)
        critic_output = outputs / "critic.json"
        critic_events = outputs / "critic.events.jsonl"
        critic_stderr = outputs / "critic.stderr.log"
        if not critic_output.is_file() or not critic_events.is_file():
            actual_runner(
                workspace=workspace,
                prompt=_critic_prompt(
                    proposal, proposal_digest, adjudicator_output.name
                ),
                schema_path=improvement_schema_root() / CRITIC_OUTPUT_SCHEMA,
                output_path=critic_output,
                event_path=critic_events,
                stderr_path=critic_stderr,
                codex_executable=codex_executable,
                timeout_seconds=timeout_seconds,
                reasoning_effort="high",
            )
        _validate_codex_workspace_access(
            event_path=critic_events,
            workspace=workspace,
            experiment_root=root,
            manifest=manifest,
        )
        critic = load_json_object(critic_output, label="Codex pact critic output")
        validate_document(
            critic,
            improvement_schema_root() / CRITIC_OUTPUT_SCHEMA,
            label="Codex pact critic output",
        )
        if critic["proposal_digest"] != proposal_digest:
            raise CapabilityImprovementError(
                "critic reviewed a different pact proposal"
            )
        accepted = (
            critic["disposition"] == "accept"
            and critic["protocol_neutral"]
            and critic["evidence_bound"]
            and critic["checker_supported"]
            and critic["regression_risk"] != "high"
        )
        if accepted and _pact_proposal_is_duplicate(root, manifest, proposal):
            accepted = False
            critic = dict(critic)
            critic["rationale"] = (
                "Rejected by deterministic admission policy because the proposal "
                "duplicates an already active learned rule."
            )
        action = "accept_change" if accepted else "reject_change"
        rationale = critic["rationale"]
        if accepted:
            current = _load_validated(
                root / manifest["current_microstate"]["path"],
                MICROSTATE_SCHEMA,
                "microstate_digest",
                "current KB microstate",
            )
            parent_pact = (
                root / current["pact"]["path"]
                if current["pact"]["kind"] == "learned"
                else None
            )
            candidate_pact = outputs / "candidate_pact"
            build_learned_pact(
                repository_root=Path(manifest["repository"]["path"]),
                parent_pact=parent_pact,
                proposal=proposal,
                output_root=candidate_pact,
            )
            from .workflow import run_pack_synthetic_suite_docker_report

            report = run_pack_synthetic_suite_docker_report(candidate_pact)
            if report["status"] != "pass" or report["issues"]:
                raise CapabilityImprovementError(
                    "critic-approved candidate pact failed its synthetic suite"
                )
            candidate_pact_report = outputs / "candidate_pact.synthetic_report.json"
            write_json_atomic(candidate_pact_report, report, mode=0o444)

    draft = {
        "protocol_id": comparison["protocol_id"],
        "training_ordinal": comparison["training_ordinal"],
        "reviewer": {
            "name": "codex",
            "model": "gpt-5.6-sol",
            "version": local_codex_version(codex_executable),
            "reasoning_effort": "max",
        },
        "artifacts": {
            **{
                key: value["path"]
                for key, value in comparison["artifacts"].items()
                if key
                in {
                    "result",
                    "t2_prediction",
                    "t3_prediction",
                    "trajectory",
                    "verifier_details",
                    "error_analysis",
                }
            },
            "comparison": comparison_path.expanduser().resolve().as_posix(),
            "adjudicator_output": adjudicator_output.as_posix(),
            "adjudicator_transcript": adjudicator_events.as_posix(),
            "critic_output": None
            if critic_output is None
            else critic_output.as_posix(),
            "critic_transcript": None
            if critic_events is None
            else critic_events.as_posix(),
            "candidate_pact_synthetic_report": (
                None
                if candidate_pact_report is None
                else candidate_pact_report.as_posix()
            ),
        },
        "findings": adjudicator["findings"],
        "pact_decision": {
            "action": action,
            "rationale": rationale,
            "critic_required": proposal is not None,
            "candidate_pact_digest": (
                None
                if candidate_pact is None
                else validate_kb_pact(candidate_pact)["pack_digest"]
            ),
        },
        "completed_at": normalize_timestamp(created_at),
    }
    draft_path = outputs / "adjudication.draft.json"
    write_json_atomic(draft_path, draft, mode=0o444)
    adjudication = compile_kb_training_adjudication(
        experiment_root=root,
        draft_path=draft_path,
        output_path=adjudication_path,
        expected_training_ordinal=comparison["training_ordinal"],
        batch_parent_state_id=comparison["state_id"],
    )
    return {
        "adjudication": adjudication,
        "adjudication_path": adjudication_path.expanduser().resolve().as_posix(),
        "pact_root": None if candidate_pact is None else candidate_pact.as_posix(),
    }


def run_local_codex_json(
    *,
    workspace: Path,
    prompt: str,
    schema_path: Path,
    output_path: Path,
    event_path: Path,
    stderr_path: Path,
    codex_executable: str = "codex",
    timeout_seconds: int = 7200,
    reasoning_effort: str = "max",
    max_capacity_retries: int = 3,
) -> dict[str, Any]:
    """Use the signed-in local Codex CLI and explicitly forbid API-key fallback."""

    auth = (
        Path(os.environ.get("CODEX_AUTH_JSON_PATH", Path.home() / ".codex/auth.json"))
        .expanduser()
        .resolve()
    )
    if not auth.is_file() or auth.is_symlink():
        raise CapabilityImprovementError(
            f"local Codex auth file is unavailable: {auth}"
        )
    if timeout_seconds <= 0:
        raise CapabilityImprovementError("local Codex timeout must be positive")
    if reasoning_effort not in {"high", "max"}:
        raise CapabilityImprovementError(
            "local Codex reasoning effort must be high or max"
        )
    if max_capacity_retries < 0:
        raise CapabilityImprovementError(
            "local Codex capacity retries must be non-negative"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        codex_executable,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--cd",
        workspace.resolve().as_posix(),
        "--model",
        "gpt-5.6-sol",
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--output-schema",
        schema_path.resolve().as_posix(),
        "--output-last-message",
        output_path.resolve().as_posix(),
        "--json",
        "-",
    ]
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("OPENAI_BASE_URL", None)
    environment["CODEX_FORCE_AUTH_JSON"] = "1"
    environment["CODEX_AUTH_JSON_PATH"] = auth.as_posix()
    if _is_transient_codex_capacity_failure(event_path, stderr_path):
        _archive_codex_capacity_attempt(
            event_path=event_path,
            stderr_path=stderr_path,
            attempt=0,
        )
    started = time.monotonic()
    capacity_retries = 0
    while True:
        output_path.unlink(missing_ok=True)
        with event_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=stdout,
                    stderr=stderr,
                    env=agent_env(environment),
                    start_new_session=True,
                )
                process.communicate(prompt.encode("utf-8"), timeout=timeout_seconds)
            except subprocess.TimeoutExpired as error:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise CapabilityImprovementError(
                    f"local Codex timed out after {timeout_seconds}s"
                ) from error
            except BaseException:
                if "process" in locals() and process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                raise
        if process.returncode == 0:
            break
        if (
            capacity_retries < max_capacity_retries
            and _is_transient_codex_capacity_failure(event_path, stderr_path)
        ):
            capacity_retries += 1
            _archive_codex_capacity_attempt(
                event_path=event_path,
                stderr_path=stderr_path,
                attempt=capacity_retries,
            )
            time.sleep((15, 30, 60)[min(capacity_retries - 1, 2)])
            continue
        raise CapabilityImprovementError(
            f"local Codex failed with return code {process.returncode}; "
            f"see {stderr_path} and {event_path}"
        )
    result = load_json_object(output_path, label="local Codex structured output")
    validate_document(result, schema_path, label="local Codex structured output")
    return {
        "returncode": process.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "capacity_retries": capacity_retries,
        "version": local_codex_version(codex_executable),
    }


def _is_transient_codex_capacity_failure(
    event_path: Path, stderr_path: Path
) -> bool:
    """Recognize only the local CLI's explicit, retryable capacity response."""

    marker = "selected model is at capacity. please try a different model."
    return marker in (
        _read_file_tail(event_path, limit=131_072)
        + "\n"
        + _read_file_tail(stderr_path, limit=131_072)
    ).lower()


def _read_file_tail(path: Path, *, limit: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit), os.SEEK_SET)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _archive_codex_capacity_attempt(
    *, event_path: Path, stderr_path: Path, attempt: int
) -> None:
    """Keep transient failed transcripts as immutable debugging provenance."""

    for path in (event_path, stderr_path):
        archive = path.with_name(
            f"{path.stem}.capacity-retry-{attempt:02d}{path.suffix}"
        )
        shutil.copy2(path, archive)


def local_codex_version(codex_executable: str = "codex") -> str:
    try:
        value = subprocess.run(
            [codex_executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise CapabilityImprovementError(
            f"cannot inspect local Codex: {error}"
        ) from error
    match = re.search(r"([0-9]+(?:\.[0-9]+){2})", value)
    if match is None:
        raise CapabilityImprovementError(f"unrecognized Codex version: {value!r}")
    return match.group(1)


def build_learned_pact(
    *,
    repository_root: Path,
    parent_pact: Path | None,
    proposal: Mapping[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    """Append one critic-approved rule to a fixed, already-tested control bundle."""

    source = (
        parent_pact.expanduser().resolve()
        if parent_pact is not None
        else repository_root.expanduser().resolve() / "paper/improvement/capability_pack"
    )
    validate_kb_pact(source)
    target = output_root.expanduser().resolve()
    if target.exists():
        return validate_kb_pact(target)
    shutil.copytree(source, target, copy_function=shutil.copy2)
    _make_tree_writable(target)
    rules_path = target / "checklists/learned_rules.json"
    if rules_path.exists():
        rules = load_json_object(rules_path, label="parent learned rules")
        _validate_learned_rules(rules)
    else:
        rules = {"schema_version": "libstruct.libgen_kb_learned_rules.v1", "rules": []}
    rule_id = f"R{len(rules['rules']) + 1:03d}"
    rule = {
        "rule_id": rule_id,
        "title": proposal["title"],
        "category": proposal["category"],
        "trigger": proposal["trigger"],
        "instruction": proposal["instruction"],
        "checker": proposal["recommended_checker"],
    }
    rules["rules"].append(rule)
    write_json_atomic(rules_path, rules, mode=0o444)
    playbook = target / "PLAYBOOK.md"
    prior = playbook.read_text(encoding="utf-8").rstrip()
    checker_line = (
        "No additional deterministic checker is selected."
        if rule["checker"] == "none"
        else f"Run the registered `{rule['checker']}` control through the unified audit."
    )
    learned = (
        f"\n\n## Learned protocol-neutral rule {rule_id}: {rule['title']}\n\n"
        f"Trigger: {rule['trigger']}\n\n"
        f"Required action: {rule['instruction']}\n\n{checker_line}\n"
    )
    playbook.chmod(0o644)
    playbook.write_text(prior + learned, encoding="utf-8")
    manifest_path = target / "manifest.json"
    manifest_path.unlink(missing_ok=True)
    write_capability_pack_manifest(target)
    manifest = validate_kb_pact(target)
    _freeze_read_inputs(target)
    return manifest


def repair_invalid_training_trial(
    *,
    experiment_root: Path,
    trial_root: Path,
    protocol_id: str,
    output_root: Path,
    created_at: str,
    max_attempts: int = 2,
    codex_executable: str = "codex",
    timeout_seconds: int = 3600,
    runner: CodexRunner | None = None,
) -> dict[str, Any]:
    """Repair only complete linked predictions using exact validation feedback."""

    if max_attempts not in {0, 1, 2}:
        raise CapabilityImprovementError("bounded repair permits at most two attempts")
    original = resolve_trial_artifacts(trial_root, protocol_id=protocol_id)
    t2 = _load_json_or_none(original["t2_prediction"])
    t3 = _load_json_or_none(original["t3_prediction"])
    if not isinstance(t2, dict) or not isinstance(t3, dict):
        return {
            "trial_root": trial_root.expanduser().resolve().as_posix(),
            "repair_attempts": 0,
            "status": "not_eligible_incomplete",
        }
    errors = linked_prediction_errors(protocol_id=protocol_id, t2=t2, t3=t3)
    if not errors:
        return {
            "trial_root": trial_root.expanduser().resolve().as_posix(),
            "repair_attempts": 0,
            "status": "already_valid",
        }
    if max_attempts == 0:
        return {
            "trial_root": trial_root.expanduser().resolve().as_posix(),
            "repair_attempts": 0,
            "status": "invalid_unrepaired",
        }
    target = output_root.expanduser().resolve()
    workspace = target / "workspace"
    inputs = workspace / "inputs"
    outputs = workspace / "outputs"
    if not workspace.exists():
        inputs.mkdir(parents=True)
        outputs.mkdir()
        shutil.copy2(original["t2_prediction"], inputs / "t2.json")
        shutil.copy2(original["t3_prediction"], inputs / "t3.json")
        _freeze_read_inputs(inputs)
    actual_runner = runner or run_local_codex_json
    attempts: list[dict[str, Any]] = []
    current_t2 = t2
    current_t3 = t3
    current_errors = errors
    for attempt_number in range(1, max_attempts + 1):
        attempt_root = outputs / f"attempt-{attempt_number}"
        attempt_root.mkdir(exist_ok=True)
        input_t2 = attempt_root / "input-t2.json"
        input_t3 = attempt_root / "input-t3.json"
        write_json_atomic(input_t2, current_t2, mode=0o444)
        write_json_atomic(input_t3, current_t3, mode=0o444)
        output = attempt_root / "repair.json"
        events = attempt_root / "events.jsonl"
        stderr = attempt_root / "stderr.log"
        if not output.is_file() or not events.is_file():
            actual_runner(
                workspace=workspace,
                prompt=_repair_prompt(
                    protocol_id=protocol_id,
                    attempt=attempt_number,
                    t2_path=input_t2,
                    t3_path=input_t3,
                    errors=current_errors,
                ),
                schema_path=improvement_schema_root() / REPAIR_OUTPUT_SCHEMA,
                output_path=output,
                event_path=events,
                stderr_path=stderr,
                codex_executable=codex_executable,
                timeout_seconds=timeout_seconds,
                reasoning_effort="high",
            )
        repaired = load_json_object(output, label="bounded repair output")
        validate_document(
            repaired,
            improvement_schema_root() / REPAIR_OUTPUT_SCHEMA,
            label="bounded repair output",
        )
        try:
            repaired_t2 = json.loads(repaired["t2_json"])
            repaired_t3 = json.loads(repaired["t3_json"])
        except json.JSONDecodeError as error:
            repaired_t2 = {}
            repaired_t3 = {}
            next_errors = [f"repair JSON encoding: {error}"]
        else:
            next_errors = linked_prediction_errors(
                protocol_id=protocol_id, t2=repaired_t2, t3=repaired_t3
            )
        attempts.append(
            {
                "attempt": attempt_number,
                "input_errors": current_errors,
                "output": _absolute_artifact(output),
                "events": _absolute_artifact(events),
                "validation_errors": next_errors,
            }
        )
        current_t2 = repaired_t2
        current_t3 = repaired_t3
        current_errors = next_errors
        if not next_errors:
            break

    repaired_trial = target / "final_trial"
    if repaired_trial.exists():
        manifest_path = repaired_trial / "repair_manifest.json"
        if manifest_path.is_file():
            manifest = _load_validated(
                manifest_path,
                REPAIR_MANIFEST_SCHEMA,
                "repair_digest",
                "existing repair manifest",
            )
            return {
                "trial_root": repaired_trial.as_posix(),
                "repair_attempts": len(manifest["attempts"]),
                "status": manifest["final_status"],
            }
        raise CapabilityImprovementError("partial repaired trial already exists")
    (repaired_trial / "artifacts/logs/artifacts").mkdir(parents=True)
    (repaired_trial / "agent").mkdir()
    (repaired_trial / "verifier").mkdir()
    shutil.copy2(original["result"], repaired_trial / "result.json")
    shutil.copy2(original["trajectory"], repaired_trial / "agent/trajectory.json")
    t2_path = repaired_trial / "artifacts/logs/artifacts/t2_prediction.json"
    t3_path = repaired_trial / "artifacts/logs/artifacts/t3_prediction.json"
    write_json_atomic(t2_path, current_t2, mode=0o444)
    write_json_atomic(t3_path, current_t3, mode=0o444)
    _score_repaired_trial(
        experiment_root=experiment_root,
        protocol_id=protocol_id,
        t2_path=t2_path,
        t3_path=t3_path,
        trajectory_path=repaired_trial / "agent/trajectory.json",
        verifier_root=repaired_trial / "verifier",
    )
    repair_manifest = with_digest(
        {
            "schema_version": REPAIR_MANIFEST_SCHEMA_VERSION,
            "protocol_id": protocol_id,
            "original_trial_root": trial_root.expanduser().resolve().as_posix(),
            "original_predictions": {
                "t2": _absolute_artifact(original["t2_prediction"]),
                "t3": _absolute_artifact(original["t3_prediction"]),
            },
            "attempts": attempts,
            "final_status": "valid" if not current_errors else "invalid",
            "final_predictions": {
                "t2": _absolute_artifact(t2_path),
                "t3": _absolute_artifact(t3_path),
            },
            "created_at": normalize_timestamp(created_at),
        },
        "repair_digest",
    )
    _validate_generated(
        repair_manifest,
        REPAIR_MANIFEST_SCHEMA,
        "repair_digest",
        "bounded repair manifest",
    )
    write_json_atomic(
        repaired_trial / "repair_manifest.json", repair_manifest, mode=0o444
    )
    return {
        "trial_root": repaired_trial.as_posix(),
        "repair_attempts": len(attempts),
        "status": repair_manifest["final_status"],
    }


def _score_repaired_trial(
    *,
    experiment_root: Path,
    protocol_id: str,
    t2_path: Path,
    t3_path: Path,
    trajectory_path: Path,
    verifier_root: Path,
) -> None:
    from libstruct_bench.cli.grade_libgen import main as grade_libgen_main

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    entry = next(
        item
        for item in manifest["universe"]["entries"]
        if item["protocol_id"] == protocol_id
    )
    truth_root = Path(manifest["private_roots"]["groundtruth_root"]) / protocol_id
    repository = Path(manifest["repository"]["path"])
    arguments = [
        "--t2-prediction",
        t2_path.as_posix(),
        "--t3-prediction",
        t3_path.as_posix(),
        "--protocol-id",
        protocol_id,
        "--groundtruth-dir",
        truth_root.as_posix(),
        "--t1-sha256",
        entry["groundtruth"]["T1"]["sha256"],
        "--t2-sha256",
        entry["groundtruth"]["T2"]["sha256"],
        "--t3-sha256",
        entry["groundtruth"]["T3"]["sha256"],
        "--schema-root",
        (repository / "schemas").as_posix(),
        "--reward-out",
        (verifier_root / "reward.json").as_posix(),
        "--details-out",
        (verifier_root / "details.json").as_posix(),
        "--error-analysis-out",
        (verifier_root / "error_analysis.json").as_posix(),
        "--error-out",
        (verifier_root / "error.json").as_posix(),
        "--trajectory",
        trajectory_path.as_posix(),
        "--trial-id",
        f"{protocol_id}-bounded-repair",
    ]
    returncode = grade_libgen_main(arguments)
    if returncode != 0:
        raise CapabilityImprovementError(
            f"v{LIBGEN_BENCHMARK_VERSION} rescore failed for repaired {protocol_id}"
        )


def _repair_prompt(
    *,
    protocol_id: str,
    attempt: int,
    t2_path: Path,
    t3_path: Path,
    errors: Sequence[str],
) -> str:
    return f"""You are performing bounded deterministic repair attempt {attempt} of 2 for `{protocol_id}`.
You may inspect only these complete prediction files:
- {t2_path.as_posix()}
- {t3_path.as_posix()}

Exact validator errors:
{json.dumps(list(errors), indent=2)}

Return corrected complete T2 and T3 documents as JSON-encoded strings in `t2_json` and `t3_json`. Change only what is necessary to satisfy schema and linked-identity/orientation validation. You have no sources or ground truth and must not add scientific claims, infer missing chemistry, browse the web, or optimize a score. Return only schema-conforming JSON."""


def execute_harbor_plan(
    *, plan_path: Path, run_kind: str, receipt_path: Path
) -> dict[str, Any]:
    """Execute one immutable plan with subscription auth and a durable receipt."""

    if run_kind not in {"c0", "training", "validation", "test"}:
        raise CapabilityImprovementError(
            "Harbor run kind must be c0, training, validation, or test"
        )
    plan_schema = {
        "c0": "kb_c0_backfill_plan.schema.json",
        "training": "kb_training_run_plan.schema.json",
        "validation": "kb_evaluation_run_plan.schema.json",
        "test": "kb_evaluation_run_plan.schema.json",
    }[run_kind]
    if run_kind == "training":
        raw_plan = load_json_object(
            plan_path.expanduser().resolve(), label="KB training plan"
        )
        if (
            raw_plan.get("schema_version")
            == "libstruct.libgen_kb_training_batch_run_plan.v1"
        ):
            plan_schema = TRAINING_BATCH_PLAN_SCHEMA
    plan = _load_validated(
        plan_path.expanduser().resolve(),
        plan_schema,
        "plan_digest",
        f"KB {run_kind} plan",
    )
    config_path = Path(plan["config"]["path"]).expanduser().resolve()
    if sha256_file(config_path) != plan["config"]["sha256"]:
        raise CapabilityImprovementError("Harbor config changed after planning")
    config = load_json_object(config_path, label="KB Harbor config")
    result_root = Path(config["jobs_dir"]).expanduser().resolve() / config["job_name"]
    receipt_file = receipt_path.expanduser().resolve()
    prior_receipt: dict[str, Any] | None = None
    if receipt_file.exists():
        prior_receipt = _load_validated(
            receipt_file,
            RUN_RECEIPT_SCHEMA,
            "receipt_digest",
            "existing Harbor receipt",
        )
        if prior_receipt["status"] == "succeeded":
            return prior_receipt
        if prior_receipt["status"] == "running" and _pid_alive(prior_receipt["pid"]):
            raise CapabilityImprovementError(
                f"Harbor {run_kind} plan is already running as PID {prior_receipt['pid']}"
            )
    if prior_receipt is not None:
        _archive_failed_harbor_result(result_root, prior_receipt)
    auth = (
        Path(os.environ.get("CODEX_AUTH_JSON_PATH", Path.home() / ".codex/auth.json"))
        .expanduser()
        .resolve()
    )
    if not auth.is_file() or auth.is_symlink():
        raise CapabilityImprovementError(
            f"local Codex auth file is unavailable: {auth}"
        )
    command = ["harbor", "run", "--config", config_path.as_posix(), "--yes"]
    stdout_path = receipt_file.with_suffix(".stdout.log")
    stderr_path = receipt_file.with_suffix(".stderr.log")
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("OPENAI_BASE_URL", None)
    environment["CODEX_FORCE_AUTH_JSON"] = "1"
    environment["CODEX_AUTH_JSON_PATH"] = auth.as_posix()
    host_hook(result_root, starting=True)
    started_at = _now()
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=agent_env(environment),
            start_new_session=True,
        )
        running = _run_receipt(
            run_kind=run_kind,
            run_id=plan["plan_digest"],
            status="running",
            plan_path=plan_path.expanduser().resolve(),
            command=command,
            pid=process.pid,
            started_at=started_at,
            finished_at=None,
            returncode=None,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            result_root=result_root,
            aggregate_path=None,
        )
        write_json_atomic(receipt_file, running, mode=0o644)
        try:
            returncode = process.wait(timeout=int(plan["timeout_seconds"]) + 3600)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            returncode = 124
    aggregate_path: Path | None = None
    status = (
        "succeeded"
        if returncode == 0 and (result_root / "result.json").is_file()
        else "failed"
    )
    if status == "succeeded" and run_kind in {"validation", "test"}:
        aggregate_path = result_root / "kb_evaluation_aggregate.json"
        record_kb_evaluation_aggregate(
            plan_path=plan_path,
            result_root=result_root,
            output_path=aggregate_path,
            created_at=_now(),
        )
    finished = _run_receipt(
        run_kind=run_kind,
        run_id=plan["plan_digest"],
        status=status,
        plan_path=plan_path.expanduser().resolve(),
        command=command,
        pid=process.pid,
        started_at=started_at,
        finished_at=_now(),
        returncode=returncode,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        result_root=result_root,
        aggregate_path=aggregate_path,
    )
    write_json_atomic(receipt_file, finished, mode=0o444)
    host_hook(result_root)
    if status != "succeeded":
        raise CapabilityImprovementError(f"Harbor {run_kind} failed; see {stderr_path}")
    return finished


def launch_harbor_plan_async(
    *, plan_path: Path, run_kind: str, receipt_path: Path, launcher_log: Path
) -> dict[str, Any]:
    """Launch the durable executor as a detached local process."""

    receipt = receipt_path.expanduser().resolve()
    if receipt.is_file():
        value = _load_validated(
            receipt, RUN_RECEIPT_SCHEMA, "receipt_digest", "Harbor receipt"
        )
        if value["status"] in {"running", "succeeded"}:
            return {
                "status": value["status"],
                "receipt": receipt.as_posix(),
                "pid": value["pid"],
            }
    command = [
        sys.executable,
        "-m",
        "libstruct_bench.cli.libgen_capability_improvement",
        "kb-execute-harbor-plan",
        "--plan",
        plan_path.expanduser().resolve().as_posix(),
        "--run-kind",
        run_kind,
        "--receipt",
        receipt.as_posix(),
    ]
    launcher_log = launcher_log.expanduser().resolve()
    launcher_log.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("OPENAI_BASE_URL", None)
    environment["CODEX_FORCE_AUTH_JSON"] = "1"
    environment["CODEX_AUTH_JSON_PATH"] = str(
        Path(environment.get("CODEX_AUTH_JSON_PATH", Path.home() / ".codex/auth.json"))
        .expanduser()
        .resolve()
    )
    with launcher_log.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
    return {"status": "launched", "receipt": receipt.as_posix(), "pid": process.pid}


def record_kb_evaluation_aggregate(
    *,
    plan_path: Path,
    result_root: Path,
    output_path: Path,
    created_at: str,
    trial_overrides: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    plan = _load_validated(
        plan_path.expanduser().resolve(),
        "kb_evaluation_run_plan.schema.json",
        "plan_digest",
        "KB evaluation plan",
    )
    result = result_root.expanduser().resolve()
    overrides = dict(trial_overrides or {})
    unknown = sorted(set(overrides) - set(plan["protocol_ids"]))
    if unknown:
        raise CapabilityImprovementError(
            f"evaluation trial overrides contain unknown protocols: {unknown}"
        )
    trial_rows: list[dict[str, Any]] = []
    sums: dict[str, float] = defaultdict(float)
    for protocol_id in plan["protocol_ids"]:
        if protocol_id in overrides:
            override = overrides[protocol_id].expanduser().resolve()
            if not override.is_dir() or override.is_symlink():
                raise CapabilityImprovementError(
                    f"unsafe evaluation trial override for {protocol_id}: {override}"
                )
            observed = _harbor_trial_protocol_id(override)
            if observed != protocol_id:
                raise CapabilityImprovementError(
                    f"evaluation trial override for {protocol_id} belongs to {observed}"
                )
            candidates = [override]
        else:
            candidates = _harbor_trial_candidates(result, protocol_id)
        if len(candidates) != 1:
            trial_rows.append(
                {
                    "protocol_id": protocol_id,
                    "status": "missing",
                    "trial_root": None,
                    "metrics": {},
                }
            )
            continue
        trial = candidates[0]
        try:
            artifacts = resolve_trial_artifacts(trial, protocol_id=protocol_id)
            details = load_json_object(
                artifacts["verifier_details"], label="evaluation details"
            )
            reward = load_json_object(artifacts["reward"], label="evaluation reward")
        except Exception:
            trial_rows.append(
                {
                    "protocol_id": protocol_id,
                    "status": "missing",
                    "trial_root": trial.as_posix(),
                    "metrics": {},
                }
            )
            continue
        metrics = {
            key: float(reward[key])
            for key in METRIC_KEYS
            if isinstance(reward.get(key), (int, float))
        }
        for key, value in metrics.items():
            sums[key] += value
        trial_rows.append(
            {
                "protocol_id": protocol_id,
                "status": (
                    "valid" if details.get("prediction_valid") is True else "invalid"
                ),
                "trial_root": trial.as_posix(),
                "metrics": metrics,
            }
        )
    count = len(plan["protocol_ids"])
    valid_count = sum(item["status"] == "valid" for item in trial_rows)
    document = with_digest(
        {
            "schema_version": EVALUATION_AGGREGATE_SCHEMA_VERSION,
            "design_digest": plan["design_digest"],
            "checkpoint": plan["checkpoint"],
            **(
                {"composite_endpoint": dict(plan["composite_endpoint"])}
                if plan.get("composite_endpoint")
                else {}
            ),
            "panel": plan["panel"],
            "benchmark_version": LIBGEN_BENCHMARK_VERSION,
            "protocol_count": count,
            "valid_count": valid_count,
            "invalid_or_missing_count": count - valid_count,
            "mean_metrics": {key: sums[key] / count for key in sorted(sums)},
            "trials": trial_rows,
            "created_at": normalize_timestamp(created_at),
        },
        "aggregate_digest",
    )
    _validate_generated(
        document,
        EVALUATION_AGGREGATE_SCHEMA,
        "aggregate_digest",
        "KB evaluation aggregate",
    )
    destination = output_path.expanduser().resolve()
    if destination.exists():
        return _load_validated(
            destination,
            EVALUATION_AGGREGATE_SCHEMA,
            "aggregate_digest",
            "existing KB evaluation aggregate",
        )
    write_json_atomic(destination, document, mode=0o444)
    return document


def recover_kb_validation_infrastructure_failures(
    *, experiment_root: Path, max_parallel_cells: int = 5
) -> dict[str, Any]:
    """Rerun only validation cells that never reached an agent result.

    Original valid/invalid scientific outcomes remain frozen. Recovery is
    limited to explicit Docker address-pool failures and explicit local-model
    capacity exits, and every replacement is pinned in a recovery manifest.
    """

    if not 1 <= max_parallel_cells <= 5:
        raise CapabilityImprovementError(
            "validation infrastructure recovery concurrency must be 1 through 5"
        )
    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    run_root = Path(str(manifest["private_roots"]["run_root"])).resolve()
    specifications: list[dict[str, Any]] = []
    already_recovered: list[str] = []
    for checkpoint_spec in design["learned_checkpoints"]:
        label = checkpoint_spec["label"]
        recovery_path = run_root / f"recoveries/validation/{label}/recovery.json"
        receipt_path = run_root / f"receipts/validation/{label}.json"
        if recovery_path.is_file():
            _load_validated(
                recovery_path,
                EVALUATION_RECOVERY_SCHEMA,
                "recovery_digest",
                f"{label} validation infrastructure recovery",
            )
            receipt = _load_validated(
                receipt_path,
                RUN_RECEIPT_SCHEMA,
                "receipt_digest",
                f"{label} recovered validation receipt",
            )
            if receipt["status"] != "succeeded":
                raise CapabilityImprovementError(
                    f"{label} recovered validation receipt is not successful"
                )
            already_recovered.append(label)
            continue
        receipt = _load_validated(
            receipt_path,
            RUN_RECEIPT_SCHEMA,
            "receipt_digest",
            f"{label} validation receipt",
        )
        if receipt["status"] != "succeeded" or not receipt["aggregate_path"]:
            raise CapabilityImprovementError(
                f"{label} validation must finish before infrastructure recovery"
            )
        plan_path = run_root / f"plans/evaluation/{label}/validation/plan.json"
        plan = _load_validated(
            plan_path,
            "kb_evaluation_run_plan.schema.json",
            "plan_digest",
            f"{label} validation plan",
        )
        if plan["design_digest"] != design["design_digest"]:
            raise CapabilityImprovementError(
                f"{label} validation plan belongs to another design"
            )
        result_root = Path(receipt["result_root"]).expanduser().resolve()
        failures = _evaluation_infrastructure_failures(
            result_root=result_root, protocol_ids=plan["protocol_ids"]
        )
        if not failures:
            continue
        aggregate_path = Path(receipt["aggregate_path"]).expanduser().resolve()
        aggregate = _load_validated(
            aggregate_path,
            EVALUATION_AGGREGATE_SCHEMA,
            "aggregate_digest",
            f"{label} original validation aggregate",
        )
        specifications.append(
            {
                "checkpoint": label,
                "panel": "validation",
                "plan_path": plan_path,
                "plan": plan,
                "receipt_path": receipt_path,
                "receipt": receipt,
                "aggregate_path": aggregate_path,
                "aggregate": aggregate,
                "result_root": result_root,
                "failures": failures,
                "run_root": run_root,
                "design_digest": design["design_digest"],
            }
        )

    recovered: list[dict[str, Any]] = []
    for wave in _evaluation_recovery_waves(
        specifications, max_parallel_cells=max_parallel_cells
    ):
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            futures = {
                pool.submit(
                    _recover_one_kb_validation_checkpoint,
                    specification=specification,
                    max_parallel_cells=max_parallel_cells,
                ): specification["checkpoint"]
                for specification in wave
            }
            completed: dict[str, dict[str, Any]] = {}
            for future in as_completed(futures):
                completed[futures[future]] = future.result()
        for specification in wave:
            recovered.append(completed[specification["checkpoint"]])
    return {
        "status": "succeeded",
        "recovered_checkpoint_count": len(recovered),
        "recovered_cell_count": sum(
            len(item["replacements"]) for item in recovered
        ),
        "recovered_checkpoints": [item["checkpoint"] for item in recovered],
        "already_recovered_checkpoints": already_recovered,
        "recovery_manifests": [item["manifest_path"] for item in recovered],
    }


def recover_kb_test_infrastructure_failures(
    *, experiment_root: Path
) -> dict[str, Any]:
    """Recover only test cells with an explicit infrastructure exception."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    if not manifest["final_lock"]:
        raise CapabilityImprovementError("test recovery requires the final lock")
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    selection = _load_validated(
        root / "validation_selection.json",
        VALIDATION_SELECTION_SCHEMA,
        "selection_digest",
        "KB validation selection",
    )
    label = selection["selected_checkpoint"]
    run_root = Path(str(manifest["private_roots"]["run_root"])).resolve()
    recovery_dir = run_root / f"recoveries/test/{label}"
    final_recovery_path = recovery_dir / "final-replay-recovery.json"
    if final_recovery_path.is_file():
        recovery = _load_validated(
            final_recovery_path,
            FINAL_REPLAY_RECOVERY_SCHEMA,
            "recovery_digest",
            "existing KB final replay recovery",
        )
        return {
            "status": "succeeded",
            "selected_checkpoint": label,
            "recovery_manifest": final_recovery_path.as_posix(),
            "recovery_digest": recovery["recovery_digest"],
            "recovered_aggregate": recovery["recovered_test_aggregate"]["path"],
        }
    plan_path = run_root / f"plans/evaluation/{label}/test/plan.json"
    plan = _load_validated(
        plan_path,
        "kb_evaluation_run_plan.schema.json",
        "plan_digest",
        f"{label} test plan",
    )
    receipt_path = run_root / f"receipts/test/{label}.json"
    receipt = _load_validated(
        receipt_path,
        RUN_RECEIPT_SCHEMA,
        "receipt_digest",
        f"{label} test receipt",
    )
    if receipt["status"] != "succeeded" or not receipt["aggregate_path"]:
        raise CapabilityImprovementError("test replay must finish before recovery")
    aggregate_path = Path(receipt["aggregate_path"]).expanduser().resolve()
    aggregate = _load_validated(
        aggregate_path,
        EVALUATION_AGGREGATE_SCHEMA,
        "aggregate_digest",
        f"{label} test aggregate",
    )
    result_root = Path(receipt["result_root"]).expanduser().resolve()
    failures = _evaluation_infrastructure_failures(
        result_root=result_root, protocol_ids=plan["protocol_ids"]
    )
    if not failures:
        raise CapabilityImprovementError(
            "test aggregate has no eligible infrastructure failure"
        )
    specification = {
        "checkpoint": label,
        "panel": "test",
        "plan_path": plan_path,
        "plan": plan,
        "receipt_path": receipt_path,
        "receipt": receipt,
        "aggregate_path": aggregate_path,
        "aggregate": aggregate,
        "result_root": result_root,
        "failures": failures,
        "run_root": run_root,
        "design_digest": design["design_digest"],
    }
    recovered = _recover_one_kb_validation_checkpoint(
        specification=specification, max_parallel_cells=1
    )
    test_recovery_path = Path(recovered["manifest_path"])
    test_recovery = _load_validated(
        test_recovery_path,
        EVALUATION_RECOVERY_SCHEMA,
        "recovery_digest",
        f"{label} test infrastructure recovery",
    )
    recovered_aggregate_path = Path(recovered["aggregate_path"])
    recovered_aggregate = _load_validated(
        recovered_aggregate_path,
        EVALUATION_AGGREGATE_SCHEMA,
        "aggregate_digest",
        f"{label} recovered test aggregate",
    )
    final_receipt_path = run_root / "receipts/final-replay.json"
    final_receipt = _load_validated(
        final_receipt_path,
        FINAL_REPLAY_RECEIPT_SCHEMA,
        "receipt_digest",
        "original KB final replay receipt",
    )
    if final_receipt["status"] != "succeeded" or final_receipt["result"] is None:
        raise CapabilityImprovementError("final replay is not complete")
    archived_final_receipt = recovery_dir / "original-final-replay-receipt.json"
    shutil.copy2(final_receipt_path, archived_final_receipt)

    def artifact_reference(path: Path, digest: str) -> dict[str, str]:
        return {
            "path": path.resolve().as_posix(),
            "sha256": sha256_file(path),
            "digest": digest,
        }

    recovered_at = _now()
    final_recovery = with_digest(
        {
            "schema_version": FINAL_REPLAY_RECOVERY_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "selected_checkpoint": label,
            "original_final_receipt": artifact_reference(
                archived_final_receipt, final_receipt["receipt_digest"]
            ),
            "test_recovery": artifact_reference(
                test_recovery_path, test_recovery["recovery_digest"]
            ),
            "recovered_test_aggregate": artifact_reference(
                recovered_aggregate_path, recovered_aggregate["aggregate_digest"]
            ),
            "created_at": normalize_timestamp(recovered_at),
        },
        "recovery_digest",
    )
    _validate_generated(
        final_recovery,
        FINAL_REPLAY_RECOVERY_SCHEMA,
        "recovery_digest",
        "KB final replay recovery",
    )
    write_json_atomic(final_recovery_path, final_recovery, mode=0o444)
    updated_final_receipt = dict(final_receipt)
    updated_result = dict(final_receipt["result"])
    updated_result["aggregate_paths"] = [recovered_aggregate_path.as_posix()]
    updated_result["infrastructure_recovery"] = artifact_reference(
        final_recovery_path, final_recovery["recovery_digest"]
    )
    updated_final_receipt["result"] = updated_result
    updated_final_receipt["finished_at"] = normalize_timestamp(recovered_at)
    updated_final_receipt.pop("receipt_digest", None)
    updated_final_receipt = with_digest(updated_final_receipt, "receipt_digest")
    _validate_generated(
        updated_final_receipt,
        FINAL_REPLAY_RECEIPT_SCHEMA,
        "receipt_digest",
        "recovered KB final replay receipt",
    )
    write_json_atomic(final_receipt_path, updated_final_receipt, mode=0o444)
    return {
        "status": "succeeded",
        "selected_checkpoint": label,
        "recovered_protocol_ids": [item["protocol_id"] for item in failures],
        "recovery_manifest": final_recovery_path.as_posix(),
        "recovery_digest": final_recovery["recovery_digest"],
        "recovered_aggregate": recovered_aggregate_path.as_posix(),
        "final_receipt_digest": updated_final_receipt["receipt_digest"],
    }


def _evaluation_infrastructure_failures(
    *, result_root: Path, protocol_ids: Sequence[str]
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for protocol_id in protocol_ids:
        candidates = _harbor_trial_candidates(result_root, protocol_id)
        if len(candidates) != 1:
            continue
        trial = candidates[0]
        result_path = trial / "result.json"
        if not result_path.is_file() or result_path.is_symlink():
            continue
        result = load_json_object(result_path, label=f"{protocol_id} Harbor result")
        exception = result.get("exception_info")
        if not isinstance(exception, Mapping):
            continue
        exception_type = exception.get("exception_type")
        message = exception.get("exception_message")
        if not isinstance(exception_type, str) or not isinstance(message, str):
            continue
        reason = None
        if (
            exception_type == "RuntimeError"
            and "all predefined address pools have been fully subnetted" in message
        ):
            reason = "docker_address_pool_exhausted"
        elif exception_type == "NonZeroAgentExitCodeError":
            transcript = "\n".join(
                (
                    message,
                    _read_file_tail(trial / "agent/codex.txt", limit=262_144),
                    _read_file_tail(trial / "trial.log", limit=262_144),
                )
            ).lower()
            if "selected model is at capacity. please try a different model." in transcript:
                reason = "local_model_capacity"
            elif (
                "stream disconnected before completion" in transcript
                and "invalid prompt: your prompt was flagged as potentially violating"
                in transcript
            ):
                reason = "provider_transport_policy_false_positive"
        if reason is None:
            continue
        failures.append(
            {
                "protocol_id": protocol_id,
                "reason": reason,
                "exception_type": exception_type,
                "trial_root": trial.resolve().as_posix(),
                "result_sha256": sha256_file(result_path),
            }
        )
    return failures


def _evaluation_recovery_waves(
    specifications: Sequence[Mapping[str, Any]], *, max_parallel_cells: int
) -> list[list[Mapping[str, Any]]]:
    pending = list(specifications)
    waves: list[list[Mapping[str, Any]]] = []
    while pending:
        wave: list[Mapping[str, Any]] = []
        used = 0
        for specification in list(pending):
            count = len(specification["failures"])
            if count > max_parallel_cells:
                raise CapabilityImprovementError(
                    f"{specification['checkpoint']} has too many recovery cells"
                )
            if used + count <= max_parallel_cells:
                wave.append(specification)
                pending.remove(specification)
                used += count
        if not wave:
            raise CapabilityImprovementError("cannot schedule validation recovery wave")
        waves.append(wave)
    return waves


def _recover_one_kb_validation_checkpoint(
    *, specification: Mapping[str, Any], max_parallel_cells: int
) -> dict[str, Any]:
    label = str(specification["checkpoint"])
    panel = str(specification.get("panel", "validation"))
    if panel not in {"validation", "test"}:
        raise CapabilityImprovementError("evaluation recovery panel is invalid")
    plan = specification["plan"]
    plan_path = Path(specification["plan_path"])
    receipt_path = Path(specification["receipt_path"])
    original_receipt = specification["receipt"]
    original_aggregate = specification["aggregate"]
    original_aggregate_path = Path(specification["aggregate_path"])
    original_result_root = Path(specification["result_root"])
    run_root = Path(specification["run_root"])
    failures = list(specification["failures"])
    recovery_dir = run_root / f"recoveries/{panel}/{label}"
    if recovery_dir.is_dir() and not (recovery_dir / "recovery.json").exists():
        return _finalize_interrupted_kb_validation_recovery(
            specification=specification,
            recovery_dir=recovery_dir,
        )
    recovery_dir.mkdir(parents=True, exist_ok=False)
    original_config_path = Path(plan["config"]["path"]).expanduser().resolve()
    if sha256_file(original_config_path) != plan["config"]["sha256"]:
        raise CapabilityImprovementError(f"{label} validation config changed")
    config = load_json_object(
        original_config_path, label=f"{label} validation Harbor config"
    )
    protocols = [item["protocol_id"] for item in failures]
    config = dict(config)
    config["job_name"] = (
        f"agent-guided-kb-v1-{label.lower()}-{panel}-infra-recovery"
    )
    config["jobs_dir"] = (
        run_root / f"evaluation/{panel}-infrastructure-recovery"
    ).as_posix()
    config["n_concurrent_trials"] = min(len(protocols), max_parallel_cells)
    datasets = [dict(item) for item in config["datasets"]]
    datasets[0]["task_names"] = protocols
    config["datasets"] = datasets
    config["retry"] = {
        "max_retries": 2,
        "include_exceptions": ["RuntimeError", "NonZeroAgentExitCodeError"],
        "min_wait_sec": 5.0,
        "max_wait_sec": 30.0,
    }
    config_path = recovery_dir / "harbor.json"
    write_json_atomic(config_path, config, mode=0o444)
    recovery_result_root = Path(config["jobs_dir"]) / config["job_name"]
    if recovery_result_root.exists():
        raise CapabilityImprovementError(
            f"{label} recovery result root already exists"
        )
    stdout_path = recovery_dir / "harbor.stdout.log"
    stderr_path = recovery_dir / "harbor.stderr.log"
    command = ["harbor", "run", "--config", config_path.as_posix(), "--yes"]
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("OPENAI_BASE_URL", None)
    environment["CODEX_FORCE_AUTH_JSON"] = "1"
    environment["CODEX_AUTH_JSON_PATH"] = str(
        Path(environment.get("CODEX_AUTH_JSON_PATH", Path.home() / ".codex/auth.json"))
        .expanduser()
        .resolve()
    )
    host_hook(recovery_result_root, starting=True)
    started_at = _now()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=agent_env(environment),
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=int(plan["timeout_seconds"]) + 3600)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            returncode = 124
    host_hook(recovery_result_root)
    if returncode != 0 or not (recovery_result_root / "result.json").is_file():
        raise CapabilityImprovementError(
            f"{label} {panel} infrastructure recovery failed; see {stderr_path}"
        )
    overrides: dict[str, Path] = {}
    replacements = []
    original_by_protocol = {item["protocol_id"]: item for item in failures}
    for protocol_id in protocols:
        candidates = _harbor_trial_candidates(recovery_result_root, protocol_id)
        if len(candidates) != 1:
            raise CapabilityImprovementError(
                f"{label} recovery produced {len(candidates)} {protocol_id} trials"
            )
        trial = candidates[0]
        result_path = trial / "result.json"
        recovered_result = load_json_object(
            result_path, label=f"recovered {protocol_id} Harbor result"
        )
        if recovered_result.get("exception_info") is not None:
            raise CapabilityImprovementError(
                f"{label} recovery retained an exception for {protocol_id}"
            )
        resolve_trial_artifacts(trial, protocol_id=protocol_id)
        overrides[protocol_id] = trial
        replacements.append(
            {
                "protocol_id": protocol_id,
                "original_trial_root": original_by_protocol[protocol_id]["trial_root"],
                "recovered_trial_root": trial.resolve().as_posix(),
                "recovered_result_sha256": sha256_file(result_path),
            }
        )
    aggregate_path = recovery_dir / "kb_evaluation_aggregate.json"
    aggregate = record_kb_evaluation_aggregate(
        plan_path=plan_path,
        result_root=original_result_root,
        output_path=aggregate_path,
        created_at=_now(),
        trial_overrides=overrides,
    )
    finished_at = _now()
    recovered_receipt = _run_receipt(
        run_kind=panel,
        run_id=plan["plan_digest"],
        status="succeeded",
        plan_path=plan_path,
        command=command,
        pid=process.pid,
        started_at=started_at,
        finished_at=finished_at,
        returncode=0,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        result_root=original_result_root,
        aggregate_path=aggregate_path,
    )
    archived_receipt = recovery_dir / "original-receipt.json"
    shutil.copy2(receipt_path, archived_receipt)
    recovery = with_digest(
        {
            "schema_version": EVALUATION_RECOVERY_SCHEMA_VERSION,
            "design_digest": specification["design_digest"],
            "checkpoint": label,
            "panel": panel,
            "plan_digest": plan["plan_digest"],
            "original_receipt": {
                "path": archived_receipt.resolve().as_posix(),
                "sha256": sha256_file(archived_receipt),
                "digest": original_receipt["receipt_digest"],
            },
            "original_aggregate": {
                "path": original_aggregate_path.as_posix(),
                "sha256": sha256_file(original_aggregate_path),
                "digest": original_aggregate["aggregate_digest"],
            },
            "infrastructure_failures": failures,
            "recovery_config": {
                "path": config_path.resolve().as_posix(),
                "sha256": sha256_file(config_path),
            },
            "recovery_result_root": recovery_result_root.resolve().as_posix(),
            "recovery_command": command,
            "recovery_returncode": 0,
            "recovery_stdout_path": stdout_path.resolve().as_posix(),
            "recovery_stderr_path": stderr_path.resolve().as_posix(),
            "replacements": replacements,
            "recovered_aggregate": {
                "path": aggregate_path.resolve().as_posix(),
                "sha256": sha256_file(aggregate_path),
                "digest": aggregate["aggregate_digest"],
            },
            "recovered_receipt_digest": recovered_receipt["receipt_digest"],
            "created_at": normalize_timestamp(finished_at),
        },
        "recovery_digest",
    )
    _validate_generated(
        recovery,
        EVALUATION_RECOVERY_SCHEMA,
        "recovery_digest",
        f"{label} {panel} infrastructure recovery",
    )
    recovery_path = recovery_dir / "recovery.json"
    write_json_atomic(recovery_path, recovery, mode=0o444)
    write_json_atomic(receipt_path, recovered_receipt, mode=0o444)
    return {
        "checkpoint": label,
        "replacements": replacements,
        "manifest_path": recovery_path.resolve().as_posix(),
        "aggregate_path": aggregate_path.resolve().as_posix(),
    }


def _completed_kb_recovery_attempt(
    *,
    attempt_id: str,
    config_path: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Describe one completed Harbor recovery attempt and its usable trials."""

    config = load_json_object(config_path, label=f"{attempt_id} recovery config")
    result_root = Path(config["jobs_dir"]).expanduser().resolve() / config["job_name"]
    protocols = list(config["datasets"][0]["task_names"])
    outcomes: list[dict[str, Any]] = []
    usable: dict[str, Path] = {}
    for protocol_id in protocols:
        candidates = _harbor_trial_candidates(result_root, protocol_id)
        if len(candidates) != 1:
            raise CapabilityImprovementError(
                f"{attempt_id} recovery is not complete for {protocol_id}"
            )
        trial = candidates[0]
        result_path = trial / "result.json"
        if not result_path.is_file() or result_path.is_symlink():
            raise CapabilityImprovementError(
                f"{attempt_id} recovery is not complete for {protocol_id}"
            )
        result = load_json_object(
            result_path, label=f"{attempt_id} {protocol_id} Harbor result"
        )
        exception = result.get("exception_info")
        exception_type = (
            exception.get("exception_type")
            if isinstance(exception, Mapping)
            else None
        )
        outcomes.append(
            {
                "protocol_id": protocol_id,
                "trial_root": trial.resolve().as_posix(),
                "result_sha256": sha256_file(result_path),
                "exception_type": exception_type,
            }
        )
        if exception is None:
            resolve_trial_artifacts(trial, protocol_id=protocol_id)
            usable[protocol_id] = trial
    command = ["harbor", "run", "--config", config_path.resolve().as_posix(), "--yes"]
    attempt = {
        "attempt_id": attempt_id,
        "config": {
            "path": config_path.resolve().as_posix(),
            "sha256": sha256_file(config_path),
        },
        "result_root": result_root.as_posix(),
        "command": command,
        "returncode": 0,
        "stdout_path": stdout_path.resolve().as_posix(),
        "stderr_path": stderr_path.resolve().as_posix(),
        "agent_timeout_multiplier": float(config.get("agent_timeout_multiplier", 1)),
        "protocol_ids": protocols,
        "trial_outcomes": outcomes,
    }
    return attempt, usable


def _finalize_interrupted_kb_validation_recovery(
    *, specification: Mapping[str, Any], recovery_dir: Path
) -> dict[str, Any]:
    """Finish a checkpoint recovery from preserved completed Harbor attempts.

    This path is used when an infrastructure-only recovery itself reached the
    agent watchdog. Successful cells from the first attempt remain frozen; a
    separately recorded extended-timeout attempt may replace only the timed-out
    cell.
    """

    label = str(specification["checkpoint"])
    panel = str(specification.get("panel", "validation"))
    attempt_specs = [
        (
            "initial",
            recovery_dir / "harbor.json",
            recovery_dir / "harbor.stdout.log",
            recovery_dir / "harbor.stderr.log",
        )
    ]
    extended_dir = recovery_dir / "extended-timeout"
    if (extended_dir / "harbor.json").is_file():
        attempt_specs.append(
            (
                "extended-timeout",
                extended_dir / "harbor.json",
                extended_dir / "harbor.stdout.log",
                extended_dir / "harbor.stderr.log",
            )
        )
    attempts: list[dict[str, Any]] = []
    usable: dict[str, Path] = {}
    for attempt_id, config_path, stdout_path, stderr_path in attempt_specs:
        attempt, attempt_usable = _completed_kb_recovery_attempt(
            attempt_id=attempt_id,
            config_path=config_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        attempts.append(attempt)
        usable.update(attempt_usable)

    failures = list(specification["failures"])
    protocols = [item["protocol_id"] for item in failures]
    missing = [protocol_id for protocol_id in protocols if protocol_id not in usable]
    if missing:
        raise CapabilityImprovementError(
            f"{label} recovery still lacks usable trials for {', '.join(missing)}"
        )
    original_by_protocol = {item["protocol_id"]: item for item in failures}
    replacements = [
        {
            "protocol_id": protocol_id,
            "original_trial_root": original_by_protocol[protocol_id]["trial_root"],
            "recovered_trial_root": usable[protocol_id].resolve().as_posix(),
            "recovered_result_sha256": sha256_file(
                usable[protocol_id] / "result.json"
            ),
        }
        for protocol_id in protocols
    ]

    plan = specification["plan"]
    plan_path = Path(specification["plan_path"])
    aggregate_path = recovery_dir / "kb_evaluation_aggregate.json"
    aggregate = record_kb_evaluation_aggregate(
        plan_path=plan_path,
        result_root=Path(specification["result_root"]),
        output_path=aggregate_path,
        created_at=_now(),
        trial_overrides=usable,
    )
    primary_attempt = attempts[-1]
    attempt_metadata_path = extended_dir / "attempt.json"
    attempt_metadata = (
        load_json_object(attempt_metadata_path, label="extended recovery metadata")
        if attempt_metadata_path.is_file()
        else {}
    )
    started_at = str(
        attempt_metadata.get("started_at")
        or load_json_object(
            Path(primary_attempt["result_root"]) / "lock.json",
            label="extended recovery lock",
        )["created_at"]
    )
    pid = attempt_metadata.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        pid = os.getpid()
    finished_at = _now()
    recovered_receipt = _run_receipt(
        run_kind=panel,
        run_id=plan["plan_digest"],
        status="succeeded",
        plan_path=plan_path,
        command=primary_attempt["command"],
        pid=pid,
        started_at=started_at,
        finished_at=finished_at,
        returncode=0,
        stdout_path=Path(primary_attempt["stdout_path"]),
        stderr_path=Path(primary_attempt["stderr_path"]),
        result_root=Path(specification["result_root"]),
        aggregate_path=aggregate_path,
    )

    receipt_path = Path(specification["receipt_path"])
    archived_receipt = recovery_dir / "original-receipt.json"
    if not archived_receipt.exists():
        shutil.copy2(receipt_path, archived_receipt)
    original_receipt = specification["receipt"]
    original_aggregate = specification["aggregate"]
    original_aggregate_path = Path(specification["aggregate_path"])
    recovery = with_digest(
        {
            "schema_version": EVALUATION_RECOVERY_SCHEMA_VERSION,
            "design_digest": specification["design_digest"],
            "checkpoint": label,
            "panel": panel,
            "plan_digest": plan["plan_digest"],
            "original_receipt": {
                "path": archived_receipt.resolve().as_posix(),
                "sha256": sha256_file(archived_receipt),
                "digest": original_receipt["receipt_digest"],
            },
            "original_aggregate": {
                "path": original_aggregate_path.as_posix(),
                "sha256": sha256_file(original_aggregate_path),
                "digest": original_aggregate["aggregate_digest"],
            },
            "infrastructure_failures": failures,
            "recovery_config": primary_attempt["config"],
            "recovery_result_root": primary_attempt["result_root"],
            "recovery_command": primary_attempt["command"],
            "recovery_returncode": 0,
            "recovery_stdout_path": primary_attempt["stdout_path"],
            "recovery_stderr_path": primary_attempt["stderr_path"],
            "recovery_attempts": attempts,
            "replacements": replacements,
            "recovered_aggregate": {
                "path": aggregate_path.resolve().as_posix(),
                "sha256": sha256_file(aggregate_path),
                "digest": aggregate["aggregate_digest"],
            },
            "recovered_receipt_digest": recovered_receipt["receipt_digest"],
            "created_at": normalize_timestamp(finished_at),
        },
        "recovery_digest",
    )
    _validate_generated(
        recovery,
        EVALUATION_RECOVERY_SCHEMA,
        "recovery_digest",
        f"{label} {panel} infrastructure recovery",
    )
    recovery_path = recovery_dir / "recovery.json"
    write_json_atomic(recovery_path, recovery, mode=0o444)
    write_json_atomic(receipt_path, recovered_receipt, mode=0o444)
    return {
        "checkpoint": label,
        "replacements": replacements,
        "manifest_path": recovery_path.resolve().as_posix(),
        "aggregate_path": aggregate_path.resolve().as_posix(),
    }


def _validation_score_row(
    *, aggregate: Mapping[str, Any], training_count: int
) -> dict[str, Any]:
    metrics = aggregate["mean_metrics"]
    absent = [
        name
        for name in (*VALIDATION_COMPONENT_METRICS, "reward")
        if name not in metrics
    ]
    if absent:
        raise CapabilityImprovementError(
            f"validation aggregate lacks selection metrics: {absent}"
        )
    components = {
        name: float(metrics[name]) for name in VALIDATION_COMPONENT_METRICS
    }
    if any(value < 0 or value > 1 for value in components.values()):
        raise CapabilityImprovementError(
            "validation aggregate has an out-of-range component"
        )
    return {
        "balanced_core_mean": round(
            sum(components.values()) / len(VALIDATION_COMPONENT_METRICS), 12
        ),
        "invalid_or_missing_count": int(aggregate["invalid_or_missing_count"]),
        "component_minimum": round(min(components.values()), 12),
        "reward": float(metrics["reward"]),
        "training_count": int(training_count),
        "component_metrics": components,
    }


def _validation_selection_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """The single frozen ordering used for checkpoint selection and confirmation."""

    return (
        -row["balanced_core_mean"],
        row["invalid_or_missing_count"],
        -row["component_minimum"],
        -row["reward"],
        row["training_count"],
    )


def select_kb_validation_checkpoint(
    *, experiment_root: Path, created_at: str
) -> dict[str, Any]:
    """Select the primary test endpoint from complete macro validation results.

    This is deliberately downstream of learning: neither per-protocol validation
    output nor the selected score can alter the pact, cards, or ground truth.
    All learned checkpoints remain authorized for the frozen test trajectory.
    """

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    if design["policies"]["validation_score_use"] != (
        "primary_test_endpoint_selection_and_regression_diagnostics_only"
    ):
        raise CapabilityImprovementError(
            "KB design does not authorize validation-based endpoint selection"
        )
    destination = root / "validation_selection.json"
    if destination.exists():
        return _load_validated(
            destination,
            VALIDATION_SELECTION_SCHEMA,
            "selection_digest",
            "existing KB validation checkpoint selection",
        )

    run_root = Path(str(manifest["private_roots"]["run_root"])).resolve()
    chronological: list[dict[str, Any]] = []
    aggregate_refs: list[dict[str, Any]] = []
    previous_score: float | None = None
    for checkpoint_spec in design["learned_checkpoints"]:
        label = checkpoint_spec["label"]
        receipt_path = run_root / "receipts" / "validation" / f"{label}.json"
        receipt = _load_validated(
            receipt_path,
            RUN_RECEIPT_SCHEMA,
            "receipt_digest",
            f"{label} validation receipt",
        )
        if receipt["status"] != "succeeded" or not receipt["aggregate_path"]:
            raise CapabilityImprovementError(
                f"{label} validation is not complete and successful"
            )
        aggregate_path = Path(receipt["aggregate_path"]).expanduser().resolve()
        aggregate = _load_validated(
            aggregate_path,
            EVALUATION_AGGREGATE_SCHEMA,
            "aggregate_digest",
            f"{label} validation aggregate",
        )
        if (
            aggregate["design_digest"] != design["design_digest"]
            or aggregate["checkpoint"] != label
            or aggregate["panel"] != "validation"
            or aggregate["benchmark_version"] != design["benchmark_version"]
            or aggregate["protocol_count"] != len(design["split"]["validation"])
        ):
            raise CapabilityImprovementError(
                f"{label} validation aggregate violates the frozen design"
            )
        checkpoint_path = root / "checkpoints" / label / "checkpoint.json"
        checkpoint = _load_validated(
            checkpoint_path,
            CHECKPOINT_SCHEMA,
            "checkpoint_digest",
            f"{label} KB checkpoint",
        )
        score_row = _validation_score_row(
            aggregate=aggregate,
            training_count=int(checkpoint_spec["after_training_count"]),
        )
        row = {
            "checkpoint": label,
            "checkpoint_digest": checkpoint["checkpoint_digest"],
            **score_row,
            "valid_count": int(aggregate["valid_count"]),
            "regressed_from_previous": (
                None
                if previous_score is None
                else score_row["balanced_core_mean"] < previous_score
            ),
        }
        chronological.append(row)
        previous_score = score_row["balanced_core_mean"]
        aggregate_refs.append(
            {
                "checkpoint": label,
                "path": aggregate_path.as_posix(),
                "sha256": sha256_file(aggregate_path),
                "digest": aggregate["aggregate_digest"],
            }
        )

    ranked = sorted(chronological, key=_validation_selection_sort_key)
    ranking = []
    for rank, row in enumerate(ranked, start=1):
        visible = {key: value for key, value in row.items() if key != "training_count"}
        ranking.append({"rank": rank, **visible})
    selected = ranking[0]
    selection = with_digest(
        {
            "schema_version": VALIDATION_SELECTION_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "selection_policy": design["policies"][
                "validation_selection_policy"
            ],
            "component_metrics": list(VALIDATION_COMPONENT_METRICS),
            "aggregate_refs": aggregate_refs,
            "ranking": ranking,
            "selected_checkpoint": selected["checkpoint"],
            "selected_checkpoint_digest": selected["checkpoint_digest"],
            "validation_use": design["policies"]["validation_score_use"],
            "prohibited_uses": [
                "pact_learning",
                "knowledge_base_content",
                "protocol_specific_error_feedback",
                "groundtruth_mutation",
            ],
            "created_at": normalize_timestamp(created_at),
        },
        "selection_digest",
    )
    _validate_generated(
        selection,
        VALIDATION_SELECTION_SCHEMA,
        "selection_digest",
        "KB validation checkpoint selection",
    )
    write_json_atomic(destination, selection, mode=0o444)
    return selection


def freeze_kb_composite_endpoint(
    *, experiment_root: Path, created_at: str
) -> dict[str, Any]:
    """Pin the selected coherent pact together with the complete C58 KB."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    destination = root / "final/composite_endpoint.json"
    if destination.exists():
        return _load_validated(
            destination,
            COMPOSITE_ENDPOINT_SCHEMA,
            "endpoint_digest",
            "existing KB composite endpoint",
        )
    selection_path = root / "validation_selection.json"
    selection = _load_validated(
        selection_path,
        VALIDATION_SELECTION_SCHEMA,
        "selection_digest",
        "KB validation checkpoint selection",
    )
    if selection["design_digest"] != design["design_digest"]:
        raise CapabilityImprovementError(
            "validation selection belongs to another KB design"
        )
    pact_label = selection["selected_checkpoint"]
    knowledge_label = design["learned_checkpoints"][-1]["label"]
    if knowledge_label != "C58":
        raise CapabilityImprovementError(
            "composite endpoint requires the frozen C58 knowledge checkpoint"
        )
    pact_path = root / "checkpoints" / pact_label / "checkpoint.json"
    knowledge_path = root / "checkpoints" / knowledge_label / "checkpoint.json"
    pact_checkpoint = _load_validated(
        pact_path,
        CHECKPOINT_SCHEMA,
        "checkpoint_digest",
        f"{pact_label} pact checkpoint",
    )
    knowledge_checkpoint = _load_validated(
        knowledge_path,
        CHECKPOINT_SCHEMA,
        "checkpoint_digest",
        f"{knowledge_label} knowledge checkpoint",
    )
    if pact_checkpoint["pact"]["kind"] != "learned":
        raise CapabilityImprovementError("selected endpoint does not have a learned pact")
    catalog_path = root / knowledge_checkpoint["catalog"]["path"]
    catalog = _load_validated(
        catalog_path,
        "kb_catalog.schema.json",
        "catalog_digest",
        "C58 KB catalog",
    )
    if catalog["training_count"] != len(design["split"]["training"]):
        raise CapabilityImprovementError("C58 catalog is not complete")
    endpoint = with_digest(
        {
            "schema_version": COMPOSITE_ENDPOINT_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "endpoint_id": f"pact-{pact_label}__kb-{knowledge_label}",
            "validation_selection": {
                "path": selection_path.relative_to(root).as_posix(),
                "sha256": sha256_file(selection_path),
                "digest": selection["selection_digest"],
            },
            "pact_checkpoint": {
                "label": pact_label,
                "path": pact_path.relative_to(root).as_posix(),
                "sha256": sha256_file(pact_path),
                "digest": pact_checkpoint["checkpoint_digest"],
                "pact": dict(pact_checkpoint["pact"]),
            },
            "knowledge_checkpoint": {
                "label": knowledge_label,
                "path": knowledge_path.relative_to(root).as_posix(),
                "sha256": sha256_file(knowledge_path),
                "digest": knowledge_checkpoint["checkpoint_digest"],
                "catalog": {
                    **dict(knowledge_checkpoint["catalog"]),
                    "training_count": int(catalog["training_count"]),
                },
            },
            "confirmation_panel": list(design["split"]["validation"]),
            "confirmation_gate": (
                "all_valid_and_same_selection_order_not_worse_v1"
            ),
            "created_at": normalize_timestamp(created_at),
        },
        "endpoint_digest",
    )
    _validate_generated(
        endpoint,
        COMPOSITE_ENDPOINT_SCHEMA,
        "endpoint_digest",
        "KB composite endpoint",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(destination, endpoint, mode=0o444)
    return endpoint


def confirm_kb_composite_endpoint(
    *,
    experiment_root: Path,
    composite_aggregate_path: Path,
    created_at: str,
) -> dict[str, Any]:
    """Apply the predeclared five-protocol gate without changing learning."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    destination = root / "final/composite_confirmation.json"
    if destination.exists():
        return _load_validated(
            destination,
            COMPOSITE_CONFIRMATION_SCHEMA,
            "confirmation_digest",
            "existing KB composite confirmation",
        )
    endpoint_path = root / "final/composite_endpoint.json"
    endpoint = _load_validated(
        endpoint_path,
        COMPOSITE_ENDPOINT_SCHEMA,
        "endpoint_digest",
        "KB composite endpoint",
    )
    selection = _load_validated(
        root / "validation_selection.json",
        VALIDATION_SELECTION_SCHEMA,
        "selection_digest",
        "KB validation checkpoint selection",
    )
    pact_label = endpoint["pact_checkpoint"]["label"]
    aggregate_reference = next(
        (
            item
            for item in selection["aggregate_refs"]
            if item["checkpoint"] == pact_label
        ),
        None,
    )
    if aggregate_reference is None:
        raise CapabilityImprovementError(
            "selected checkpoint lacks a validation aggregate reference"
        )
    coupled_path = Path(aggregate_reference["path"]).expanduser().resolve()
    coupled = _load_validated(
        coupled_path,
        EVALUATION_AGGREGATE_SCHEMA,
        "aggregate_digest",
        f"{pact_label} coupled validation aggregate",
    )
    composite_path = composite_aggregate_path.expanduser().resolve()
    composite = _load_validated(
        composite_path,
        EVALUATION_AGGREGATE_SCHEMA,
        "aggregate_digest",
        "composite validation aggregate",
    )
    composite_reference = composite.get("composite_endpoint") or {}
    if (
        composite["design_digest"] != design["design_digest"]
        or composite["panel"] != "validation"
        or composite["checkpoint"] != pact_label
        or composite_reference.get("digest") != endpoint["endpoint_digest"]
        or [item["protocol_id"] for item in composite["trials"]]
        != endpoint["confirmation_panel"]
    ):
        raise CapabilityImprovementError(
            "composite confirmation aggregate does not match the frozen endpoint"
        )
    training_count = next(
        int(item["after_training_count"])
        for item in design["learned_checkpoints"]
        if item["label"] == pact_label
    )
    coupled_score = _validation_score_row(
        aggregate=coupled, training_count=training_count
    )
    composite_score = _validation_score_row(
        aggregate=composite, training_count=training_count
    )
    all_valid = (
        composite["protocol_count"] == len(endpoint["confirmation_panel"])
        and composite["valid_count"] == len(endpoint["confirmation_panel"])
        and composite["invalid_or_missing_count"] == 0
    )
    not_worse = _validation_selection_sort_key(
        composite_score
    ) <= _validation_selection_sort_key(coupled_score)
    passed = all_valid and not_worse
    effective_mode = "composite" if passed else "coupled_checkpoint_fallback"
    effective_id = endpoint["endpoint_id"] if passed else pact_label
    knowledge_label = (
        endpoint["knowledge_checkpoint"]["label"] if passed else pact_label
    )

    def artifact_reference(path: Path, digest: str) -> dict[str, str]:
        return {
            "path": path.as_posix(),
            "sha256": sha256_file(path),
            "digest": digest,
        }

    confirmation = with_digest(
        {
            "schema_version": COMPOSITE_CONFIRMATION_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "composite_endpoint": artifact_reference(
                endpoint_path, endpoint["endpoint_digest"]
            ),
            "coupled_validation_aggregate": artifact_reference(
                coupled_path, coupled["aggregate_digest"]
            ),
            "composite_validation_aggregate": artifact_reference(
                composite_path, composite["aggregate_digest"]
            ),
            "gate_policy": endpoint["confirmation_gate"],
            "coupled_score": coupled_score,
            "composite_score": composite_score,
            "all_predictions_valid": all_valid,
            "composite_not_worse": not_worse,
            "passed": passed,
            "effective_endpoint_mode": effective_mode,
            "effective_endpoint_id": effective_id,
            "pact_checkpoint": pact_label,
            "knowledge_checkpoint": knowledge_label,
            "created_at": normalize_timestamp(created_at),
        },
        "confirmation_digest",
    )
    _validate_generated(
        confirmation,
        COMPOSITE_CONFIRMATION_SCHEMA,
        "confirmation_digest",
        "KB composite confirmation",
    )
    write_json_atomic(destination, confirmation, mode=0o444)
    return confirmation


def find_harbor_trial(result_root: Path, protocol_id: str) -> Path:
    candidates = _harbor_trial_candidates(result_root, protocol_id)
    if len(candidates) != 1:
        raise CapabilityImprovementError(
            f"expected one {protocol_id} trial, found {len(candidates)} in {result_root}"
        )
    return candidates[0]


def _harbor_trial_candidates(result_root: Path, protocol_id: str) -> list[Path]:
    """Find trials by Harbor's immutable task metadata, not its truncated directory name."""

    root = result_root.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        return []
    candidates: list[Path] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.is_symlink():
            continue
        observed = _harbor_trial_protocol_id(path)
        if observed == protocol_id or (
            observed is None and path.name.startswith(f"{protocol_id}__")
        ):
            candidates.append(path)
    return candidates


def _harbor_trial_protocol_id(trial: Path) -> str | None:
    lock_path = trial / "lock.json"
    if lock_path.is_file() and not lock_path.is_symlink():
        try:
            lock = load_json_object(lock_path, label="Harbor trial lock")
        except Exception:
            return None
        task = lock.get("task")
        if isinstance(task, dict) and isinstance(task.get("name"), str):
            return task["name"]
    result_path = trial / "result.json"
    if result_path.is_file() and not result_path.is_symlink():
        try:
            result = load_json_object(result_path, label="Harbor trial result")
        except Exception:
            return None
        task_name = result.get("task_name")
        if isinstance(task_name, str) and task_name:
            return task_name.rsplit("/", maxsplit=1)[-1]
    return None


def record_kb_training_cycle(
    *,
    experiment_root: Path,
    plan_path: Path,
    comparison_path: Path,
    adjudication_path: Path,
    run_source: str,
    repair_status: str,
    validation_launch: Mapping[str, Any] | None,
    output_path: Path,
) -> dict[str, Any]:
    """Record a completed admission from its independently pinned artifacts."""

    if run_source not in {"reused_c0", "harbor"}:
        raise CapabilityImprovementError("cycle run_source is invalid")
    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "admitted KB microstate",
    )
    ordinal = state["training_count"]
    if ordinal < 1 or state["protocol_id"] is None:
        raise CapabilityImprovementError("no admitted training cycle can be recorded")
    plan_file = plan_path.expanduser().resolve()
    raw_plan = load_json_object(plan_file, label="KB training plan")
    plan_schema = (
        BASELINE_TRAINING_PLAN_SCHEMA
        if raw_plan.get("schema_version")
        == "libstruct.libgen_kb_training_baseline_plan.v1"
        else (
            TRAINING_BATCH_PLAN_SCHEMA
            if raw_plan.get("schema_version")
            == "libstruct.libgen_kb_training_batch_run_plan.v1"
            else "kb_training_run_plan.schema.json"
        )
    )
    plan = _load_validated(
        plan_file,
        plan_schema,
        "plan_digest",
        "KB training plan",
    )
    if run_source == "reused_c0" and plan_schema == BASELINE_TRAINING_PLAN_SCHEMA:
        _validate_baseline_trial_pin(plan["baseline_trial"])
    comparison = _load_validated(
        comparison_path.expanduser().resolve(),
        COMPARISON_SCHEMA,
        "comparison_digest",
        "KB comparison",
    )
    adjudication = _load_validated(
        adjudication_path.expanduser().resolve(),
        ADJUDICATION_SCHEMA,
        "decision_digest",
        "KB training adjudication",
    )
    validate_kb_training_adjudication(adjudication)
    expected = {
        "design_digest": design["design_digest"],
        "training_ordinal": ordinal,
        "protocol_id": state["protocol_id"],
    }
    if plan_schema == TRAINING_BATCH_PLAN_SCHEMA:
        try:
            position = plan["training_ordinals"].index(ordinal)
        except ValueError as error:
            raise CapabilityImprovementError(
                "batch plan does not contain the admitted training ordinal"
            ) from error
        if (
            plan["design_digest"] != expected["design_digest"]
            or plan["protocol_ids"][position] != expected["protocol_id"]
        ):
            raise CapabilityImprovementError(
                "batch plan does not match the admitted training cycle"
            )
        checked_documents = (
            ("comparison", comparison),
            ("adjudication", adjudication),
        )
    else:
        checked_documents = (
            ("plan", plan),
            ("comparison", comparison),
            ("adjudication", adjudication),
        )
    for label, document in checked_documents:
        if any(document.get(key) != value for key, value in expected.items()):
            raise CapabilityImprovementError(
                f"{label} does not match the admitted training cycle"
            )
    if state["adjudication"]["digest"] != adjudication["decision_digest"]:
        raise CapabilityImprovementError(
            "admitted microstate pins a different adjudication"
        )
    repair_attempts = int(comparison["prediction_validation"]["repair_attempts"])
    checkpoint_label = next(
        (
            item["label"]
            for item in design["learned_checkpoints"]
            if item["after_training_count"] == ordinal
        ),
        None,
    )
    if (checkpoint_label is None) != (validation_launch is None):
        raise CapabilityImprovementError(
            "validation launch must exist exactly at a checkpoint cycle"
        )
    cycle = with_digest(
        {
            "schema_version": "libstruct.libgen_kb_cycle_record.v1",
            "design_digest": design["design_digest"],
            "state_id": state["state_id"],
            "protocol_id": state["protocol_id"],
            "run_source": run_source,
            "trial_root": comparison["trial_root"],
            "plan_digest": plan["plan_digest"],
            "comparison_digest": comparison["comparison_digest"],
            "adjudication_digest": adjudication["decision_digest"],
            "repair_attempts": repair_attempts,
            "repair_status": repair_status,
            "checkpoint": checkpoint_label,
            "validation_launch": (
                None if validation_launch is None else dict(validation_launch)
            ),
            "completed_at": plan["created_at"],
        },
        "cycle_digest",
    )
    _validate_generated(
        cycle, CYCLE_RECORD_SCHEMA, "cycle_digest", "KB training cycle record"
    )
    destination = output_path.expanduser().resolve()
    if destination.exists():
        existing = _load_validated(
            destination,
            CYCLE_RECORD_SCHEMA,
            "cycle_digest",
            "existing KB training cycle record",
        )
        if existing != cycle:
            raise CapabilityImprovementError(
                "existing training cycle differs from requested bytes"
            )
        return existing
    write_json_atomic(destination, cycle, mode=0o444)
    return cycle


def ensure_kb_checkpoint_validations(
    *, experiment_root: Path, base_config_path: Path
) -> list[dict[str, Any]]:
    """Launch or resume every validation checkpoint already reached by training."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "KB microstate",
    )
    run_root = Path(manifest["private_roots"]["run_root"])
    launches = []
    for checkpoint_spec in design["learned_checkpoints"]:
        if checkpoint_spec["after_training_count"] > state["training_count"]:
            break
        label = checkpoint_spec["label"]
        checkpoint = _load_validated(
            root / f"checkpoints/{label}/checkpoint.json",
            "kb_checkpoint.schema.json",
            "checkpoint_digest",
            f"{label} KB checkpoint",
        )
        plan_kb_checkpoint_evaluation(
            experiment_root=root,
            checkpoint_label=label,
            panel="validation",
            base_config_path=base_config_path,
            created_at=checkpoint["created_at"],
        )
        plan_path = run_root / f"plans/evaluation/{label}/validation/plan.json"
        launch = launch_harbor_plan_async(
            plan_path=plan_path,
            run_kind="validation",
            receipt_path=run_root / f"receipts/validation/{label}.json",
            launcher_log=run_root / f"receipts/validation/{label}.launcher.log",
        )
        launches.append({"checkpoint": label, **launch})
    return launches


def ensure_kb_batch_execution_amendment(
    *, experiment_root: Path, analysis_concurrency: int, created_at: str
) -> dict[str, Any]:
    """Record the user-authorized C5 batch-parallel continuation once."""

    if not 2 <= analysis_concurrency <= 8:
        raise CapabilityImprovementError(
            "batch adjudication concurrency must be between 2 and 8"
        )
    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    parent = _load_validated(
        root / "microstates/S005.json",
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "C5 parent microstate",
    )
    if parent["training_count"] != 5 or not (root / "checkpoints/C5").is_dir():
        raise CapabilityImprovementError(
            "batch-parallel continuation requires the frozen C5 parent"
        )
    run_root = Path(manifest["private_roots"]["run_root"])
    path = run_root / "receipts/training-batches/execution-amendment.json"
    if path.is_file():
        existing = _load_validated(
            path,
            BATCH_AMENDMENT_SCHEMA,
            "amendment_digest",
            "batch execution amendment",
        )
        if existing["analysis_concurrency"] != analysis_concurrency:
            raise CapabilityImprovementError(
                "batch execution concurrency differs from its frozen amendment"
            )
        return existing
    amendment = with_digest(
        {
            "schema_version": BATCH_AMENDMENT_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "parent_checkpoint": "C5",
            "parent_state_id": parent["state_id"],
            "parent_microstate_digest": parent["microstate_digest"],
            "applies_from_training_ordinal": 6,
            "analysis_concurrency": analysis_concurrency,
            "analysis_visibility": "batch_start_pact",
            "training_generation": "harbor_previous_checkpoint_snapshot",
            "retrieval_policy": (
                "source_evidenced_full_similarity_ranking_agent_selected_unbounded_v3"
            ),
            "knowledge_admission": (
                "canonical_groundtruth_cards_after_whole_batch"
            ),
            "validation_feedback": "aggregate_evaluation_only_no_rule_learning",
            "test_policy": "deferred_by_user",
            "pact_update_policy": (
                "after_all_batch_adjudications_then_ordinal_reconciliation"
            ),
            "admission_order": "frozen_training_order",
            "authorized_by": "user_delegated",
            "created_at": normalize_timestamp(created_at),
        },
        "amendment_digest",
    )
    _validate_generated(
        amendment,
        BATCH_AMENDMENT_SCHEMA,
        "amendment_digest",
        "batch execution amendment",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, amendment, mode=0o444)
    return amendment


def _checkpoint_batch_bounds(
    design: Mapping[str, Any], training_count: int
) -> tuple[int, int, str]:
    """Return the checkpoint-bounded batch containing the next protocol."""

    previous = max(
        (item["after_training_count"] for item in design["learned_checkpoints"]
         if item["after_training_count"] <= training_count),
        default=0,
    )
    next_spec = next(
        item
        for item in design["learned_checkpoints"]
        if item["after_training_count"] > training_count
    )
    return previous, next_spec["after_training_count"], next_spec["label"]


def _prepare_kb_batch_analysis(
    *,
    experiment_root: Path,
    ordinal: int,
    parent_state_id: str,
    created_at: str,
    codex_executable: str,
    adjudication_timeout_seconds: int,
) -> dict[str, Any]:
    """Prepare one preserved-baseline item and run its source adjudication."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    protocol_id = design["split"]["training"][ordinal - 1]
    run_root = Path(manifest["private_roots"]["run_root"])
    record_root = run_root / "records" / f"S{ordinal:03d}-{protocol_id}"
    record_root.mkdir(parents=True, exist_ok=True)
    plan = plan_next_kb_training_run(
        experiment_root=root,
        created_at=created_at,
        training_ordinal=ordinal,
        batch_parent_state_id=parent_state_id,
    )
    plan_path = (
        run_root
        / "plans"
        / "training"
        / f"S{ordinal:03d}"
        / "baseline-reuse"
        / "plan.json"
    )
    _validate_baseline_trial_pin(plan["baseline_trial"])
    comparison_path = record_root / "comparison.json"
    if not comparison_path.is_file():
        build_kb_comparison(
            experiment_root=root,
            trial_root=Path(plan["baseline_trial"]["trial_root"]),
            training_ordinal=ordinal,
            output_path=comparison_path,
            created_at=plan["created_at"],
            repair_attempts=0,
            batch_parent_state_id=parent_state_id,
        )
    adjudication_path = record_root / "adjudication.json"
    review = run_kb_adjudication(
        experiment_root=root,
        comparison_path=comparison_path,
        workspace_root=record_root / "adjudication_workspace",
        adjudication_path=adjudication_path,
        created_at=plan["created_at"],
        codex_executable=codex_executable,
        timeout_seconds=adjudication_timeout_seconds,
    )
    return {
        "ordinal": ordinal,
        "protocol_id": protocol_id,
        "record_root": record_root,
        "plan": plan,
        "plan_path": plan_path,
        "comparison_path": comparison_path,
        "adjudication_path": adjudication_path,
        "review": review,
    }


def _prepare_behavioral_batch_analysis(
    *,
    experiment_root: Path,
    plan: Mapping[str, Any],
    plan_path: Path,
    result_root: Path,
    ordinal: int,
    protocol_id: str,
    codex_executable: str,
    adjudication_timeout_seconds: int,
) -> dict[str, Any]:
    """Repair, compare, and adjudicate one newly generated batch trial."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    run_root = Path(manifest["private_roots"]["run_root"])
    record_root = run_root / "records" / f"S{ordinal:03d}-{protocol_id}"
    record_root.mkdir(parents=True, exist_ok=True)
    original_trial = find_harbor_trial(result_root, protocol_id)
    repair = repair_invalid_training_trial(
        experiment_root=root,
        trial_root=original_trial,
        protocol_id=protocol_id,
        output_root=record_root / "bounded_repair",
        created_at=plan["created_at"],
        max_attempts=2,
        codex_executable=codex_executable,
    )
    trial_root = Path(repair["trial_root"])
    repair_status = str(repair["status"])
    repair_attempts = int(repair["repair_attempts"])
    comparison_path = record_root / "comparison.json"
    if not comparison_path.is_file():
        build_kb_comparison(
            experiment_root=root,
            trial_root=trial_root,
            training_ordinal=ordinal,
            output_path=comparison_path,
            created_at=plan["created_at"],
            repair_attempts=repair_attempts,
            batch_parent_state_id=plan["parent_state_id"],
        )
    adjudication_path = record_root / "adjudication.json"
    review = run_kb_adjudication(
        experiment_root=root,
        comparison_path=comparison_path,
        workspace_root=record_root / "adjudication_workspace",
        adjudication_path=adjudication_path,
        created_at=plan["created_at"],
        codex_executable=codex_executable,
        timeout_seconds=adjudication_timeout_seconds,
    )
    return {
        "ordinal": ordinal,
        "protocol_id": protocol_id,
        "record_root": record_root,
        "plan": plan,
        "plan_path": plan_path,
        "comparison_path": comparison_path,
        "adjudication_path": adjudication_path,
        "review": review,
        "repair_status": repair_status,
        "repair_attempts": repair_attempts,
    }


def _batch_reconcile_adjudication(
    *, experiment_root: Path, item: Mapping[str, Any]
) -> tuple[Path, Path | None]:
    """Rebase one accepted rule on earlier admissions from the same batch."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    current = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "current KB microstate",
    )
    original_path = Path(item["adjudication_path"])
    original = _load_validated(
        original_path,
        ADJUDICATION_SCHEMA,
        "decision_digest",
        "batch-source adjudication",
    )
    if original["training_ordinal"] != current["training_count"] + 1:
        raise CapabilityImprovementError(
            "batch reconciliation is not following frozen training order"
        )
    if original["pact_decision"]["action"] != "accept_change":
        return original_path, None
    plan = item["plan"]
    original_candidate = Path(item["review"]["pact_root"])
    adjudicator_path = Path(original["artifacts"]["adjudicator_output"]["path"])
    adjudicator = load_json_object(
        adjudicator_path, label="batch-source adjudicator output"
    )
    proposal = adjudicator.get("pact_proposal")
    if not isinstance(proposal, Mapping):
        raise CapabilityImprovementError(
            "accepted batch adjudication lacks its pact proposal"
        )
    outputs = Path(item["record_root"]) / "adjudication_workspace/outputs"
    reconciled_path = Path(item["record_root"]) / "adjudication.batch.json"
    candidate_pact: Path | None = None
    candidate_report: Path | None = None
    duplicate = _find_similar_pact_rule(root, manifest, proposal)
    if duplicate is not None:
        match_kind, matching_rule, trigger_overlap, instruction_overlap = duplicate
        action = "reject_change"
        rationale = (
            "Deduplicated during the single post-batch pact reconciliation: "
            f"this proposal is a {match_kind} match for active rule "
            f"{matching_rule['rule_id']} ({matching_rule['title']!r}); "
            f"trigger containment={trigger_overlap:.3f}, instruction "
            f"containment={instruction_overlap:.3f}. The protocol-specific "
            "adjudication remains preserved as supporting audit evidence."
        )
        candidate_digest = None
    else:
        action = "accept_change"
        rationale = (
            f"{original['pact_decision']['rationale']} Admitted during the "
            "single post-batch reconciliation after deterministic semantic "
            "deduplication against the progressively accumulated pact."
        )
        if current["pact"]["digest"] == plan["pact_digest"]:
            candidate_pact = original_candidate
            pack = validate_kb_pact(candidate_pact)
            candidate_report = Path(
                original["artifacts"]["candidate_pact_synthetic_report"]["path"]
            )
        else:
            parent_pact = (
                root / current["pact"]["path"]
                if current["pact"]["kind"] == "learned"
                else None
            )
            candidate_pact = outputs / f"candidate_pact.batch-{current['state_id']}"
            pack = build_learned_pact(
                repository_root=Path(manifest["repository"]["path"]),
                parent_pact=parent_pact,
                proposal=proposal,
                output_root=candidate_pact,
            )
            from .workflow import run_pack_synthetic_suite_docker_report

            candidate_report = outputs / (
                f"candidate_pact.batch-{current['state_id']}.synthetic_report.json"
            )
            if not candidate_report.is_file():
                report = run_pack_synthetic_suite_docker_report(candidate_pact)
                if report["status"] != "pass" or report["issues"]:
                    raise CapabilityImprovementError(
                        "batch-rebased candidate pact failed its synthetic suite"
                    )
                write_json_atomic(candidate_report, report, mode=0o444)
        candidate_digest = pack["pack_digest"]

    if reconciled_path.is_file():
        reconciled = _load_validated(
            reconciled_path,
            ADJUDICATION_SCHEMA,
            "decision_digest",
            "reconciled batch adjudication",
        )
        expected_digest = candidate_digest
        if reconciled["pact_decision"]["candidate_pact_digest"] != expected_digest:
            raise CapabilityImprovementError(
                "existing batch reconciliation pins another candidate pact"
            )
        return reconciled_path, candidate_pact

    artifacts = {
        key: None if value is None else value["path"]
        for key, value in original["artifacts"].items()
    }
    artifacts["candidate_pact_synthetic_report"] = (
        None if candidate_report is None else candidate_report.as_posix()
    )
    draft = {
        "protocol_id": original["protocol_id"],
        "training_ordinal": original["training_ordinal"],
        "reviewer": original["reviewer"],
        "artifacts": artifacts,
        "findings": original["findings"],
        "pact_decision": {
            "action": action,
            "rationale": rationale,
            "critic_required": True,
            "candidate_pact_digest": candidate_digest,
        },
        "completed_at": original["completed_at"],
    }
    draft_path = outputs / f"adjudication.batch-{current['state_id']}.draft.json"
    if not draft_path.is_file():
        write_json_atomic(draft_path, draft, mode=0o444)
    compile_kb_training_adjudication(
        experiment_root=root,
        draft_path=draft_path,
        output_path=reconciled_path,
    )
    return reconciled_path, candidate_pact


def run_next_kb_behavioral_training_batch(
    *,
    experiment_root: Path,
    base_config_path: Path,
    created_at: str,
    analysis_concurrency: int = 5,
    codex_executable: str = "codex",
    adjudication_timeout_seconds: int = 7200,
) -> dict[str, Any]:
    """Generate one five-protocol Harbor batch, then learn exactly once."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "behavioral batch parent",
    )
    if state["training_count"] >= len(design["split"]["training"]):
        return {"status": "complete", "training_count": state["training_count"]}
    amendment = ensure_kb_batch_execution_amendment(
        experiment_root=root,
        analysis_concurrency=analysis_concurrency,
        created_at=created_at,
    )
    # Validation measures the frozen parent while generation proceeds; it is
    # never exposed to the adjudicator or pact-writing path.
    ensure_kb_checkpoint_validations(
        experiment_root=root,
        base_config_path=base_config_path,
    )
    plan = plan_next_kb_behavioral_training_batch(
        experiment_root=root,
        base_config_path=base_config_path,
        created_at=created_at,
    )
    run_root = Path(manifest["private_roots"]["run_root"])
    plan_path = (
        run_root
        / "plans/training-batches"
        / f"{plan['parent_checkpoint']}-to-{plan['next_checkpoint']}"
        / "plan.json"
    )
    harbor_receipt_path = (
        run_root
        / "receipts/training-batches"
        / f"{plan['parent_checkpoint']}-to-{plan['next_checkpoint']}.harbor.json"
    )
    harbor_receipt = execute_harbor_plan(
        plan_path=plan_path,
        run_kind="training",
        receipt_path=harbor_receipt_path,
    )
    result_root = Path(harbor_receipt["result_root"])
    items: dict[int, dict[str, Any]] = {}
    workers = min(analysis_concurrency, len(plan["training_ordinals"]))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _prepare_behavioral_batch_analysis,
                experiment_root=root,
                plan=plan,
                plan_path=plan_path,
                result_root=result_root,
                ordinal=ordinal,
                protocol_id=protocol_id,
                codex_executable=codex_executable,
                adjudication_timeout_seconds=adjudication_timeout_seconds,
            ): ordinal
            for ordinal, protocol_id in zip(
                plan["training_ordinals"], plan["protocol_ids"], strict=True
            )
        }
        for future in as_completed(futures):
            ordinal = futures[future]
            items[ordinal] = future.result()

    parent_state = state
    validation_launch: Mapping[str, Any] | None = None
    cycle_records: list[dict[str, Any]] = []
    final_ordinal = plan["training_ordinals"][-1]
    from .kb_guided import admit_kb_training_protocol

    # No state or pact mutation occurs until all Harbor trials and all source
    # adjudications above have completed. Admission remains deterministic.
    for ordinal in plan["training_ordinals"]:
        item = items[ordinal]
        adjudication_path, pact_root = _batch_reconcile_adjudication(
            experiment_root=root,
            item=item,
        )
        admit_kb_training_protocol(
            experiment_root=root,
            adjudication_path=adjudication_path,
            created_at=plan["created_at"],
            pact_root=pact_root,
        )
        if ordinal == final_ordinal:
            validation_launch = next(
                launch
                for launch in ensure_kb_checkpoint_validations(
                    experiment_root=root,
                    base_config_path=base_config_path,
                )
                if launch["checkpoint"] == plan["next_checkpoint"]
            )
        cycle_records.append(
            record_kb_training_cycle(
                experiment_root=root,
                plan_path=plan_path,
                comparison_path=item["comparison_path"],
                adjudication_path=adjudication_path,
                run_source="harbor",
                repair_status=item["repair_status"],
                validation_launch=(
                    validation_launch if ordinal == final_ordinal else None
                ),
                output_path=Path(item["record_root"]) / "cycle.json",
            )
        )
    if validation_launch is None:
        raise CapabilityImprovementError(
            "completed behavioral batch lacks its validation launch"
        )
    batch = with_digest(
        {
            "schema_version": BATCH_RECORD_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "amendment_digest": amendment["amendment_digest"],
            "parent_state_id": parent_state["state_id"],
            "parent_microstate_digest": parent_state["microstate_digest"],
            "checkpoint": plan["next_checkpoint"],
            "analysis_concurrency": workers,
            "protocols": [
                {
                    "state_id": cycle["state_id"],
                    "protocol_id": cycle["protocol_id"],
                    "adjudication_digest": cycle["adjudication_digest"],
                    "cycle_digest": cycle["cycle_digest"],
                }
                for cycle in cycle_records
            ],
            "validation_launch": dict(validation_launch),
            "started_at": plan["created_at"],
            "completed_at": _now(),
        },
        "batch_digest",
    )
    _validate_generated(
        batch,
        BATCH_RECORD_SCHEMA,
        "batch_digest",
        "completed behavioral training batch",
    )
    batch_record_path = (
        run_root
        / "receipts/training-batches"
        / f"{plan['parent_checkpoint']}-to-{plan['next_checkpoint']}.json"
    )
    write_json_atomic(batch_record_path, batch, mode=0o444)
    return {"status": "admitted_batch", "batch": batch}


def run_next_kb_training_batch(
    *,
    experiment_root: Path,
    base_config_path: Path,
    created_at: str,
    analysis_concurrency: int = 5,
    codex_executable: str = "codex",
    adjudication_timeout_seconds: int = 7200,
) -> dict[str, Any]:
    """Analyze one checkpoint batch concurrently, then reconcile it in order."""

    root = experiment_root.expanduser().resolve()
    if (root / "lineage/behavioral.json").is_file():
        return run_next_kb_behavioral_training_batch(
            experiment_root=root,
            base_config_path=base_config_path,
            created_at=created_at,
            analysis_concurrency=analysis_concurrency,
            codex_executable=codex_executable,
            adjudication_timeout_seconds=adjudication_timeout_seconds,
        )
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "KB microstate",
    )
    if state["training_count"] >= len(design["split"]["training"]):
        return {"status": "complete", "training_count": state["training_count"]}
    amendment = ensure_kb_batch_execution_amendment(
        experiment_root=root,
        analysis_concurrency=analysis_concurrency,
        created_at=created_at,
    )
    parent_count, batch_end, checkpoint_label = _checkpoint_batch_bounds(
        design, state["training_count"]
    )
    parent_state = _load_validated(
        root / f"microstates/S{parent_count:03d}.json",
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "batch parent microstate",
    )
    if state["training_count"] < parent_count:
        raise CapabilityImprovementError("current state precedes its batch parent")
    run_root = Path(manifest["private_roots"]["run_root"])
    batch_record_path = (
        run_root
        / "receipts/training-batches"
        / f"C{parent_count}-to-{checkpoint_label}.json"
    )
    if batch_record_path.is_file():
        return {
            "status": "admitted_batch",
            "batch": _load_validated(
                batch_record_path,
                BATCH_RECORD_SCHEMA,
                "batch_digest",
                "completed KB training batch",
            ),
        }

    ordinals = list(range(parent_count + 1, batch_end + 1))
    items: dict[int, dict[str, Any]] = {}
    workers = min(analysis_concurrency, len(ordinals))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _prepare_kb_batch_analysis,
                experiment_root=root,
                ordinal=ordinal,
                parent_state_id=parent_state["state_id"],
                created_at=created_at,
                codex_executable=codex_executable,
                adjudication_timeout_seconds=adjudication_timeout_seconds,
            ): ordinal
            for ordinal in ordinals
        }
        for future in as_completed(futures):
            ordinal = futures[future]
            items[ordinal] = future.result()

    cycle_records: list[dict[str, Any]] = []
    validation_launch: Mapping[str, Any] | None = None
    current_count = state["training_count"]
    from .kb_guided import admit_kb_training_protocol

    for ordinal in range(current_count + 1, batch_end + 1):
        item = items[ordinal]
        adjudication_path, pact_root = _batch_reconcile_adjudication(
            experiment_root=root, item=item
        )
        admit_kb_training_protocol(
            experiment_root=root,
            adjudication_path=adjudication_path,
            created_at=item["plan"]["created_at"],
            pact_root=pact_root,
        )
        if ordinal == batch_end:
            validation_launch = next(
                launch
                for launch in ensure_kb_checkpoint_validations(
                    experiment_root=root, base_config_path=base_config_path
                )
                if launch["checkpoint"] == checkpoint_label
            )
        cycle = record_kb_training_cycle(
            experiment_root=root,
            plan_path=item["plan_path"],
            comparison_path=item["comparison_path"],
            adjudication_path=adjudication_path,
            run_source="reused_c0",
            repair_status="preserved_baseline_unmodified",
            validation_launch=validation_launch if ordinal == batch_end else None,
            output_path=Path(item["record_root"]) / "cycle.json",
        )
        cycle_records.append(cycle)

    if validation_launch is None:
        raise CapabilityImprovementError("completed batch lacks validation launch")
    all_cycles = []
    for ordinal in ordinals:
        protocol_id = design["split"]["training"][ordinal - 1]
        path = run_root / "records" / f"S{ordinal:03d}-{protocol_id}" / "cycle.json"
        all_cycles.append(
            _load_validated(
                path,
                CYCLE_RECORD_SCHEMA,
                "cycle_digest",
                "batch cycle record",
            )
        )
    batch = with_digest(
        {
            "schema_version": BATCH_RECORD_SCHEMA_VERSION,
            "design_digest": design["design_digest"],
            "amendment_digest": amendment["amendment_digest"],
            "parent_state_id": parent_state["state_id"],
            "parent_microstate_digest": parent_state["microstate_digest"],
            "checkpoint": checkpoint_label,
            "analysis_concurrency": analysis_concurrency,
            "protocols": [
                {
                    "state_id": cycle["state_id"],
                    "protocol_id": cycle["protocol_id"],
                    "adjudication_digest": cycle["adjudication_digest"],
                    "cycle_digest": cycle["cycle_digest"],
                }
                for cycle in all_cycles
            ],
            "validation_launch": dict(validation_launch),
            "started_at": normalize_timestamp(created_at),
            "completed_at": _now(),
        },
        "batch_digest",
    )
    _validate_generated(
        batch,
        BATCH_RECORD_SCHEMA,
        "batch_digest",
        "completed KB training batch",
    )
    batch_record_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(batch_record_path, batch, mode=0o444)
    return {"status": "admitted_batch", "batch": batch}


def run_kb_training_batch_loop(
    *,
    experiment_root: Path,
    base_config_path: Path,
    max_protocols: int,
    analysis_concurrency: int = 5,
    codex_executable: str = "codex",
    adjudication_timeout_seconds: int = 7200,
) -> dict[str, Any]:
    """Resume checkpoint batches; Harbor validation remains asynchronous."""

    root = experiment_root.expanduser().resolve()
    if max_protocols < 1:
        raise CapabilityImprovementError("batch-loop max_protocols must be positive")
    manifest = validate_kb_experiment(root)
    initial = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "initial KB microstate",
    )
    completed = 0
    while completed < max_protocols:
        manifest = validate_kb_experiment(root)
        state = _load_validated(
            root / manifest["current_microstate"]["path"],
            MICROSTATE_SCHEMA,
            "microstate_digest",
            "KB microstate",
        )
        design = _load_validated(
            root / manifest["design"]["path"],
            DESIGN_SCHEMA,
            "design_digest",
            "KB design",
        )
        if state["training_count"] >= len(design["split"]["training"]):
            break
        _, batch_end, _ = _checkpoint_batch_bounds(design, state["training_count"])
        remaining = batch_end - state["training_count"]
        if completed + remaining > max_protocols:
            break
        run_next_kb_training_batch(
            experiment_root=root,
            base_config_path=base_config_path,
            created_at=_now(),
            analysis_concurrency=analysis_concurrency,
            codex_executable=codex_executable,
            adjudication_timeout_seconds=adjudication_timeout_seconds,
        )
        completed += remaining
    manifest = validate_kb_experiment(root)
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "final KB microstate",
    )
    return {
        "completed_this_invocation": state["training_count"]
        - initial["training_count"],
        "training_count": state["training_count"],
        "last_state": state["state_id"],
    }


def run_next_kb_training_cycle(
    *,
    experiment_root: Path,
    base_config_path: Path,
    created_at: str,
    codex_executable: str = "codex",
    adjudication_timeout_seconds: int = 7200,
    codex_runner: CodexRunner | None = None,
) -> dict[str, Any]:
    """Run, compare, adjudicate, admit, and checkpoint one training protocol."""

    root = experiment_root.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"], DESIGN_SCHEMA, "design_digest", "KB design"
    )
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "KB microstate",
    )
    ensure_kb_checkpoint_validations(
        experiment_root=root, base_config_path=base_config_path
    )
    ordinal = state["training_count"] + 1
    if ordinal > len(design["split"]["training"]):
        return {"status": "complete", "training_count": state["training_count"]}
    protocol_id = design["split"]["training"][ordinal - 1]
    run_root = Path(manifest["private_roots"]["run_root"])
    record_root = run_root / "records" / f"S{ordinal:03d}-{protocol_id}"
    record_root.mkdir(parents=True, exist_ok=True)
    plan = plan_next_kb_training_run(experiment_root=root, created_at=created_at)
    plan_path = (
        run_root
        / "plans"
        / "training"
        / f"S{ordinal:03d}"
        / "baseline-reuse"
        / "plan.json"
    )
    cycle_timestamp = plan["created_at"]
    _validate_baseline_trial_pin(plan["baseline_trial"])
    trial = Path(plan["baseline_trial"]["trial_root"])
    run_source = "reused_c0"
    repair_attempts = 0
    repair_status = "preserved_baseline_unmodified"
    comparison_path = record_root / "comparison.json"
    build_kb_comparison(
        experiment_root=root,
        trial_root=trial,
        training_ordinal=ordinal,
        output_path=comparison_path,
        created_at=cycle_timestamp,
        repair_attempts=repair_attempts,
    )
    adjudication_path = record_root / "adjudication.json"
    review = run_kb_adjudication(
        experiment_root=root,
        comparison_path=comparison_path,
        workspace_root=record_root / "adjudication_workspace",
        adjudication_path=adjudication_path,
        created_at=cycle_timestamp,
        codex_executable=codex_executable,
        timeout_seconds=adjudication_timeout_seconds,
        runner=codex_runner,
    )
    from .kb_guided import admit_kb_training_protocol

    status = admit_kb_training_protocol(
        experiment_root=root,
        adjudication_path=adjudication_path,
        created_at=cycle_timestamp,
        pact_root=(None if review["pact_root"] is None else Path(review["pact_root"])),
    )
    checkpoint_label = next(
        (
            item["label"]
            for item in design["learned_checkpoints"]
            if item["after_training_count"] == ordinal
        ),
        None,
    )
    validation_launch = None
    if checkpoint_label is not None:
        validation_launch = next(
            item
            for item in ensure_kb_checkpoint_validations(
                experiment_root=root, base_config_path=base_config_path
            )
            if item["checkpoint"] == checkpoint_label
        )
    cycle = record_kb_training_cycle(
        experiment_root=root,
        plan_path=plan_path,
        comparison_path=comparison_path,
        adjudication_path=adjudication_path,
        run_source=run_source,
        repair_status=repair_status,
        validation_launch=validation_launch,
        output_path=record_root / "cycle.json",
    )
    return {"status": "admitted", "cycle": cycle, "experiment": status}


def run_kb_training_loop(
    *,
    experiment_root: Path,
    base_config_path: Path,
    max_protocols: int,
    codex_executable: str = "codex",
    adjudication_timeout_seconds: int = 7200,
) -> dict[str, Any]:
    """Resume the sequential online loop; validation processes remain asynchronous."""

    if max_protocols < 1:
        raise CapabilityImprovementError("max_protocols must be positive")
    completed = []
    for _ in range(max_protocols):
        result = run_next_kb_training_cycle(
            experiment_root=experiment_root,
            base_config_path=base_config_path,
            created_at=_now(),
            codex_executable=codex_executable,
            adjudication_timeout_seconds=adjudication_timeout_seconds,
        )
        if result["status"] == "complete":
            break
        completed.append(result["cycle"])
    manifest = validate_kb_experiment(experiment_root)
    state = _load_validated(
        experiment_root.resolve() / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "KB microstate",
    )
    return {
        "completed_this_invocation": len(completed),
        "training_count": state["training_count"],
        "last_state": state["state_id"],
    }


def execute_kb_training_loop(
    *,
    experiment_root: Path,
    base_config_path: Path,
    max_protocols: int,
    receipt_path: Path,
    log_path: Path,
    codex_executable: str = "codex",
    adjudication_timeout_seconds: int = 7200,
    analysis_concurrency: int = 1,
) -> dict[str, Any]:
    """Run the resumable online loop under one durable supervisor receipt."""

    root = experiment_root.expanduser().resolve()
    config = base_config_path.expanduser().resolve()
    receipt = receipt_path.expanduser().resolve()
    log = log_path.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "KB microstate",
    )
    if max_protocols < 1 or max_protocols > len(design["split"]["training"]):
        raise CapabilityImprovementError(
            "training-loop max_protocols is outside the frozen training panel"
        )
    if not config.is_file() or config.is_symlink():
        raise CapabilityImprovementError(f"unsafe or missing base config: {config}")
    started_at = _now()
    if receipt.is_file():
        existing = _load_validated(
            receipt,
            TRAINING_LOOP_RECEIPT_SCHEMA,
            "receipt_digest",
            "existing KB training-loop receipt",
        )
        if existing["status"] == "succeeded":
            return existing
        if existing["status"] == "running" and _pid_alive(existing["pid"]):
            if existing["pid"] != os.getpid():
                raise CapabilityImprovementError(
                    f"KB training loop is already running as PID {existing['pid']}"
                )
            started_at = existing["started_at"]
    common = {
        "schema_version": TRAINING_LOOP_RECEIPT_SCHEMA_VERSION,
        "design_digest": design["design_digest"],
        "experiment_root": root.as_posix(),
        "base_config": {"path": config.as_posix(), "sha256": sha256_file(config)},
        "pid": os.getpid(),
        "max_protocols": max_protocols,
        "initial_training_count": state["training_count"],
        "started_at": started_at,
        "log_path": log.as_posix(),
    }
    running = with_digest(
        {
            **common,
            "status": "running",
            "result": None,
            "error": None,
            "finished_at": None,
        },
        "receipt_digest",
    )
    _validate_generated(
        running,
        TRAINING_LOOP_RECEIPT_SCHEMA,
        "receipt_digest",
        "running KB training-loop receipt",
    )
    receipt.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(receipt, running, mode=0o644)
    try:
        if analysis_concurrency == 1:
            result = run_kb_training_loop(
                experiment_root=root,
                base_config_path=config,
                max_protocols=max_protocols,
                codex_executable=codex_executable,
                adjudication_timeout_seconds=adjudication_timeout_seconds,
            )
        else:
            result = run_kb_training_batch_loop(
                experiment_root=root,
                base_config_path=config,
                max_protocols=max_protocols,
                analysis_concurrency=analysis_concurrency,
                codex_executable=codex_executable,
                adjudication_timeout_seconds=adjudication_timeout_seconds,
            )
    except BaseException as error:
        failed = with_digest(
            {
                **common,
                "status": "failed",
                "result": None,
                "error": f"{type(error).__name__}: {error}",
                "finished_at": _now(),
            },
            "receipt_digest",
        )
        _validate_generated(
            failed,
            TRAINING_LOOP_RECEIPT_SCHEMA,
            "receipt_digest",
            "failed KB training-loop receipt",
        )
        write_json_atomic(receipt, failed, mode=0o444)
        raise
    succeeded = with_digest(
        {
            **common,
            "status": "succeeded",
            "result": result,
            "error": None,
            "finished_at": _now(),
        },
        "receipt_digest",
    )
    _validate_generated(
        succeeded,
        TRAINING_LOOP_RECEIPT_SCHEMA,
        "receipt_digest",
        "succeeded KB training-loop receipt",
    )
    write_json_atomic(receipt, succeeded, mode=0o444)
    return succeeded


def launch_kb_training_loop_async(
    *,
    experiment_root: Path,
    base_config_path: Path,
    max_protocols: int,
    receipt_path: Path,
    log_path: Path,
    codex_executable: str = "codex",
    adjudication_timeout_seconds: int = 7200,
    analysis_concurrency: int = 1,
) -> dict[str, Any]:
    """Detach the loop supervisor and confirm that its running receipt exists."""

    receipt = receipt_path.expanduser().resolve()
    log = log_path.expanduser().resolve()
    if receipt.is_file():
        existing = _load_validated(
            receipt,
            TRAINING_LOOP_RECEIPT_SCHEMA,
            "receipt_digest",
            "KB training-loop receipt",
        )
        if existing["status"] == "succeeded" or (
            existing["status"] == "running" and _pid_alive(existing["pid"])
        ):
            return existing
    command = [
        sys.executable,
        "-m",
        "libstruct_bench.cli.libgen_capability_improvement",
        "kb-execute-loop",
        "--experiment-root",
        experiment_root.expanduser().resolve().as_posix(),
        "--base-config",
        base_config_path.expanduser().resolve().as_posix(),
        "--max-protocols",
        str(max_protocols),
        "--receipt",
        receipt.as_posix(),
        "--log",
        log.as_posix(),
        "--codex",
        codex_executable,
        "--adjudication-timeout-seconds",
        str(adjudication_timeout_seconds),
        "--analysis-concurrency",
        str(analysis_concurrency),
    ]
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("OPENAI_BASE_URL", None)
    environment["CODEX_FORCE_AUTH_JSON"] = "1"
    environment["CODEX_AUTH_JSON_PATH"] = str(
        Path(environment.get("CODEX_AUTH_JSON_PATH", Path.home() / ".codex/auth.json"))
        .expanduser()
        .resolve()
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as stream:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
    for _ in range(100):
        if receipt.is_file():
            observed = _load_validated(
                receipt,
                TRAINING_LOOP_RECEIPT_SCHEMA,
                "receipt_digest",
                "launched KB training-loop receipt",
            )
            if observed["status"] == "succeeded" or (
                observed["status"] == "running" and observed["pid"] == process.pid
            ):
                return observed
        returncode = process.poll()
        if returncode is not None:
            raise CapabilityImprovementError(
                f"KB training-loop supervisor exited {returncode}; see {log}"
            )
        time.sleep(0.1)
    raise CapabilityImprovementError(
        f"KB training-loop supervisor did not create its receipt; see {log}"
    )


def execute_kb_final_replay(
    *,
    experiment_root: Path,
    base_config_path: Path,
    receipt_path: Path,
    log_path: Path,
    poll_interval_seconds: int = 30,
    wait_timeout_seconds: int = 172800,
    composite_final_kb: bool = False,
) -> dict[str, Any]:
    """Wait for training/validation, lock the design, and run sealed tests.

    Historical lineages retain their frozen all-checkpoint replay. The C5-seeded
    behavioral lineage runs only the checkpoint selected by the frozen
    validation rule. Optionally, that coherent pact is paired with the complete
    C58 knowledge base and must pass one predeclared five-protocol confirmation
    before it can become the sealed-test endpoint. The ten test protocols still
    run concurrently in Harbor.
    """

    if not 1 <= poll_interval_seconds <= 300:
        raise CapabilityImprovementError("final-replay poll interval is invalid")
    if wait_timeout_seconds < 1:
        raise CapabilityImprovementError("final-replay wait timeout is invalid")
    root = experiment_root.expanduser().resolve()
    config = base_config_path.expanduser().resolve()
    receipt = receipt_path.expanduser().resolve()
    log = log_path.expanduser().resolve()
    manifest = validate_kb_experiment(root)
    design = _load_validated(
        root / manifest["design"]["path"],
        DESIGN_SCHEMA,
        "design_digest",
        "KB design",
    )
    if not config.is_file() or config.is_symlink():
        raise CapabilityImprovementError(f"unsafe or missing base config: {config}")
    candidate_checkpoint_labels = [
        item["label"] for item in design["learned_checkpoints"]
    ]
    lineage_path = root / "lineage/behavioral.json"
    selected_checkpoint_only = False
    if lineage_path.is_file():
        lineage = _load_validated(
            lineage_path,
            BEHAVIORAL_LINEAGE_SCHEMA,
            "lineage_digest",
            "behavioral KB lineage",
        )
        selected_checkpoint_only = lineage["test_evaluation"] == (
            "validation_selected_checkpoint_only_after_all_validation"
        )
    if composite_final_kb and not selected_checkpoint_only:
        raise CapabilityImprovementError(
            "composite final KB is only authorized for selected-checkpoint lineages"
        )
    checkpoint_labels = list(candidate_checkpoint_labels)
    expected_test_cells = (
        len(design["split"]["test"])
        if selected_checkpoint_only
        else len(checkpoint_labels) * len(design["split"]["test"])
    )
    if not selected_checkpoint_only and (
        expected_test_cells != design["expected_counts"]["new_test_cells"]
    ):
        raise CapabilityImprovementError("final replay does not match the frozen matrix")
    run_root = Path(str(manifest["private_roots"]["run_root"])).resolve()
    started_at = _now()
    if receipt.is_file():
        existing = _load_validated(
            receipt,
            FINAL_REPLAY_RECEIPT_SCHEMA,
            "receipt_digest",
            "existing KB final-replay receipt",
        )
        if existing["status"] == "succeeded":
            expected_policy = (
                "validation_selected_coherent_pact_with_c58_kb_confirmed_v1"
                if composite_final_kb
                else "coupled_validation_selected_checkpoint_v1"
            )
            if existing.get("endpoint_policy") != expected_policy:
                raise CapabilityImprovementError(
                    "existing final replay used another endpoint policy"
                )
            return existing
        if existing["status"] in {"waiting", "running"} and _pid_alive(
            existing["pid"]
        ):
            if existing["pid"] != os.getpid():
                raise CapabilityImprovementError(
                    f"KB final replay is already running as PID {existing['pid']}"
                )
            started_at = existing["started_at"]

    common = {
        "schema_version": FINAL_REPLAY_RECEIPT_SCHEMA_VERSION,
        "design_digest": design["design_digest"],
        "experiment_root": root.as_posix(),
        "base_config": {"path": config.as_posix(), "sha256": sha256_file(config)},
        "endpoint_policy": (
            "validation_selected_coherent_pact_with_c58_kb_confirmed_v1"
            if composite_final_kb
            else "coupled_validation_selected_checkpoint_v1"
        ),
        "pid": os.getpid(),
        "checkpoint_labels": checkpoint_labels,
        "expected_test_cells": expected_test_cells,
        "poll_interval_seconds": poll_interval_seconds,
        "wait_timeout_seconds": wait_timeout_seconds,
        "started_at": started_at,
        "log_path": log.as_posix(),
    }

    def write_progress(
        *,
        status: str,
        completed: Sequence[str],
        current: str | None,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        document = with_digest(
            {
                **common,
                "status": status,
                "completed_checkpoints": list(completed),
                "current_checkpoint": current,
                "result": None if result is None else dict(result),
                "error": error,
                "finished_at": finished_at,
            },
            "receipt_digest",
        )
        _validate_generated(
            document,
            FINAL_REPLAY_RECEIPT_SCHEMA,
            "receipt_digest",
            "KB final-replay receipt",
        )
        receipt.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            receipt,
            document,
            mode=0o444 if status in {"succeeded", "failed"} else 0o644,
        )
        return document

    completed: list[str] = []
    if not selected_checkpoint_only:
        for label in checkpoint_labels:
            test_receipt = run_root / "receipts/test" / f"{label}.json"
            if not test_receipt.is_file():
                continue
            value = _load_validated(
                test_receipt,
                RUN_RECEIPT_SCHEMA,
                "receipt_digest",
                f"{label} test receipt",
            )
            if value["status"] == "succeeded":
                completed.append(label)
            else:
                break
    write_progress(
        status="waiting", completed=completed, current=None, finished_at=None
    )

    try:
        deadline = time.monotonic() + wait_timeout_seconds
        while True:
            manifest = validate_kb_experiment(root)
            state = _load_validated(
                root / manifest["current_microstate"]["path"],
                MICROSTATE_SCHEMA,
                "microstate_digest",
                "KB microstate",
            )
            validations_ready = state["training_count"] == len(
                design["split"]["training"]
            )
            if validations_ready:
                for label in candidate_checkpoint_labels:
                    validation_receipt = (
                        run_root / "receipts/validation" / f"{label}.json"
                    )
                    if not validation_receipt.is_file():
                        validations_ready = False
                        break
                    value = _load_validated(
                        validation_receipt,
                        RUN_RECEIPT_SCHEMA,
                        "receipt_digest",
                        f"{label} validation receipt",
                    )
                    if value["status"] == "failed":
                        raise CapabilityImprovementError(
                            f"{label} validation failed before final replay"
                        )
                    if value["status"] != "succeeded":
                        validations_ready = False
                        break
            if validations_ready:
                break
            if time.monotonic() >= deadline:
                raise CapabilityImprovementError(
                    "timed out waiting for S058 and all validation checkpoints"
                )
            time.sleep(poll_interval_seconds)

        selection = select_kb_validation_checkpoint(
            experiment_root=root, created_at=_now()
        )
        endpoint: dict[str, Any] | None = None
        confirmation: dict[str, Any] | None = None
        effective_mode = "coupled_checkpoint"
        effective_endpoint_id = selection["selected_checkpoint"]
        pact_checkpoint = selection["selected_checkpoint"]
        knowledge_checkpoint = selection["selected_checkpoint"]
        endpoint_reference: dict[str, str] | None = None
        confirmation_reference: dict[str, str] | None = None
        if composite_final_kb:
            endpoint = freeze_kb_composite_endpoint(
                experiment_root=root, created_at=_now()
            )
            endpoint_path = root / "final/composite_endpoint.json"
            endpoint_reference = {
                "path": endpoint_path.as_posix(),
                "sha256": sha256_file(endpoint_path),
                "digest": endpoint["endpoint_digest"],
            }
            plan_kb_composite_evaluation(
                experiment_root=root,
                composite_endpoint_path=endpoint_path,
                panel="validation",
                base_config_path=config,
                created_at=_now(),
            )
            composite_plan_path = (
                run_root
                / "plans/evaluation/composite"
                / endpoint["endpoint_id"]
                / "validation/plan.json"
            )
            composite_receipt_path = (
                run_root
                / "receipts/validation-composite"
                / f"{endpoint['endpoint_id']}.json"
            )
            write_progress(
                status="running",
                completed=[],
                current=selection["selected_checkpoint"],
                finished_at=None,
            )
            composite_result = execute_harbor_plan(
                plan_path=composite_plan_path,
                run_kind="validation",
                receipt_path=composite_receipt_path,
            )
            if (
                composite_result["status"] != "succeeded"
                or not composite_result["aggregate_path"]
            ):
                raise CapabilityImprovementError(
                    "composite confirmation lacks a successful aggregate"
                )
            confirmation = confirm_kb_composite_endpoint(
                experiment_root=root,
                composite_aggregate_path=Path(composite_result["aggregate_path"]),
                created_at=_now(),
            )
            confirmation_path = root / "final/composite_confirmation.json"
            confirmation_reference = {
                "path": confirmation_path.as_posix(),
                "sha256": sha256_file(confirmation_path),
                "digest": confirmation["confirmation_digest"],
            }
            effective_mode = confirmation["effective_endpoint_mode"]
            effective_endpoint_id = confirmation["effective_endpoint_id"]
            pact_checkpoint = confirmation["pact_checkpoint"]
            knowledge_checkpoint = confirmation["knowledge_checkpoint"]
            endpoint_selection = {
                "effective_endpoint_id": effective_endpoint_id,
                "pact_checkpoint": pact_checkpoint,
                "knowledge_checkpoint": knowledge_checkpoint,
                "composite_endpoint": endpoint_reference,
                "composite_confirmation": confirmation_reference,
            }
            lock = lock_kb_final(
                experiment_root=root,
                created_at=_now(),
                primary_endpoint_mode=effective_mode,
                endpoint_selection=endpoint_selection,
            )
        else:
            lock = lock_kb_final(experiment_root=root, created_at=_now())
        checkpoint_labels = (
            [selection["selected_checkpoint"]]
            if selected_checkpoint_only
            else candidate_checkpoint_labels
        )
        common["checkpoint_labels"] = list(checkpoint_labels)
        aggregate_paths = []
        completed = []
        composite_test = effective_mode == "composite"
        for label in checkpoint_labels:
            test_receipt = (
                run_root
                / "receipts/test/composite"
                / f"{endpoint['endpoint_id']}.json"
                if composite_test and endpoint is not None
                else run_root / "receipts/test" / f"{label}.json"
            )
            if not test_receipt.is_file():
                continue
            value = _load_validated(
                test_receipt,
                RUN_RECEIPT_SCHEMA,
                "receipt_digest",
                f"{label} test receipt",
            )
            if value["status"] == "succeeded":
                completed.append(label)
                if value["aggregate_path"]:
                    aggregate_paths.append(value["aggregate_path"])
        for label in checkpoint_labels:
            if label in completed:
                continue
            write_progress(
                status="running",
                completed=completed,
                current=label,
                finished_at=None,
            )
            if composite_test and endpoint is not None:
                endpoint_path = root / "final/composite_endpoint.json"
                plan_kb_composite_evaluation(
                    experiment_root=root,
                    composite_endpoint_path=endpoint_path,
                    panel="test",
                    base_config_path=config,
                    created_at=_now(),
                )
                plan_path = (
                    run_root
                    / "plans/evaluation/composite"
                    / endpoint["endpoint_id"]
                    / "test/plan.json"
                )
                test_receipt = (
                    run_root
                    / "receipts/test/composite"
                    / f"{endpoint['endpoint_id']}.json"
                )
            else:
                plan_kb_checkpoint_evaluation(
                    experiment_root=root,
                    checkpoint_label=label,
                    panel="test",
                    base_config_path=config,
                    created_at=_now(),
                )
                plan_path = (
                    run_root / "plans/evaluation" / label / "test/plan.json"
                )
                test_receipt = run_root / "receipts/test" / f"{label}.json"
            result = execute_harbor_plan(
                plan_path=plan_path, run_kind="test", receipt_path=test_receipt
            )
            if result["status"] != "succeeded" or not result["aggregate_path"]:
                raise CapabilityImprovementError(
                    f"{label} test replay lacks a successful aggregate"
                )
            aggregate_paths.append(result["aggregate_path"])
            completed.append(label)
            write_progress(
                status="running",
                completed=completed,
                current=None,
                finished_at=None,
            )

        final_result = {
            "validation_selection_digest": selection["selection_digest"],
            "selected_checkpoint": selection["selected_checkpoint"],
            "final_lock_digest": lock["lock_digest"],
            "checkpoint_count": len(checkpoint_labels),
            "protocol_count": len(design["split"]["test"]),
            "completed_test_cells": expected_test_cells,
            "aggregate_paths": aggregate_paths,
            "effective_endpoint_mode": effective_mode,
            "effective_endpoint_id": effective_endpoint_id,
            "pact_checkpoint": pact_checkpoint,
            "knowledge_checkpoint": knowledge_checkpoint,
            **(
                {
                    "composite_endpoint": endpoint_reference,
                    "composite_confirmation": confirmation_reference,
                }
                if endpoint_reference is not None
                and confirmation_reference is not None
                else {}
            ),
        }
        return write_progress(
            status="succeeded",
            completed=completed,
            current=None,
            result=final_result,
            finished_at=_now(),
        )
    except BaseException as exc:
        write_progress(
            status="failed",
            completed=completed,
            current=None,
            error=f"{type(exc).__name__}: {exc}",
            finished_at=_now(),
        )
        raise


def launch_kb_final_replay_async(
    *,
    experiment_root: Path,
    base_config_path: Path,
    receipt_path: Path,
    log_path: Path,
    poll_interval_seconds: int = 30,
    wait_timeout_seconds: int = 172800,
    composite_final_kb: bool = False,
) -> dict[str, Any]:
    """Detach the sealed-test replay supervisor and confirm its receipt."""

    receipt = receipt_path.expanduser().resolve()
    log = log_path.expanduser().resolve()
    if receipt.is_file():
        existing = _load_validated(
            receipt,
            FINAL_REPLAY_RECEIPT_SCHEMA,
            "receipt_digest",
            "KB final-replay receipt",
        )
        if existing["status"] == "succeeded" or (
            existing["status"] in {"waiting", "running"}
            and _pid_alive(existing["pid"])
        ):
            expected_policy = (
                "validation_selected_coherent_pact_with_c58_kb_confirmed_v1"
                if composite_final_kb
                else "coupled_validation_selected_checkpoint_v1"
            )
            if existing.get("endpoint_policy") != expected_policy:
                raise CapabilityImprovementError(
                    "live final replay uses another endpoint policy"
                )
            return existing
    command = [
        sys.executable,
        "-m",
        "libstruct_bench.cli.libgen_capability_improvement",
        "kb-execute-final-replay",
        "--experiment-root",
        experiment_root.expanduser().resolve().as_posix(),
        "--base-config",
        base_config_path.expanduser().resolve().as_posix(),
        "--receipt",
        receipt.as_posix(),
        "--log",
        log.as_posix(),
        "--poll-interval-seconds",
        str(poll_interval_seconds),
        "--wait-timeout-seconds",
        str(wait_timeout_seconds),
    ]
    if composite_final_kb:
        command.append("--composite-final-kb")
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("OPENAI_BASE_URL", None)
    environment["CODEX_FORCE_AUTH_JSON"] = "1"
    environment["CODEX_AUTH_JSON_PATH"] = str(
        Path(environment.get("CODEX_AUTH_JSON_PATH", Path.home() / ".codex/auth.json"))
        .expanduser()
        .resolve()
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as stream:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
    for _ in range(100):
        if receipt.is_file():
            observed = _load_validated(
                receipt,
                FINAL_REPLAY_RECEIPT_SCHEMA,
                "receipt_digest",
                "launched KB final-replay receipt",
            )
            if observed["status"] == "succeeded" or (
                observed["status"] in {"waiting", "running"}
                and observed["pid"] == process.pid
            ):
                return observed
        returncode = process.poll()
        if returncode is not None:
            raise CapabilityImprovementError(
                f"KB final-replay supervisor exited {returncode}; see {log}"
            )
        time.sleep(0.1)
    raise CapabilityImprovementError(
        f"KB final-replay supervisor did not create its receipt; see {log}"
    )


def _find_compatible_baseline_trial(
    baseline_root: Path, *, protocol_id: str, expected_agent: Mapping[str, Any]
) -> Path:
    candidates = _harbor_trial_candidates(baseline_root, protocol_id)
    compatible = []
    for candidate in candidates:
        try:
            resolve_trial_artifacts(candidate, protocol_id=protocol_id)
            config = load_json_object(
                candidate / "config.json", label="baseline config"
            )
            agent = config.get("agent") or {}
            observed = {
                "name": agent.get("name"),
                "model": agent.get("model_name"),
                "version": (agent.get("kwargs") or {}).get("version"),
                "reasoning_effort": (agent.get("kwargs") or {}).get("reasoning_effort"),
            }
            if observed == dict(expected_agent):
                compatible.append(candidate)
        except Exception:
            continue
    if len(compatible) != 1:
        raise CapabilityImprovementError(
            f"expected one compatible C0 trial for {protocol_id}, found {len(compatible)}"
        )
    return compatible[0]


def _run_receipt(
    *,
    run_kind: str,
    run_id: str,
    status: str,
    plan_path: Path,
    command: Sequence[str],
    pid: int | None,
    started_at: str,
    finished_at: str | None,
    returncode: int | None,
    stdout_path: Path,
    stderr_path: Path,
    result_root: Path,
    aggregate_path: Path | None,
) -> dict[str, Any]:
    document = with_digest(
        {
            "schema_version": RUN_RECEIPT_SCHEMA_VERSION,
            "run_kind": run_kind,
            "run_id": run_id,
            "status": status,
            "plan_path": plan_path.as_posix(),
            "plan_sha256": sha256_file(plan_path),
            "command": list(command),
            "pid": pid,
            "started_at": normalize_timestamp(started_at),
            "finished_at": (
                None if finished_at is None else normalize_timestamp(finished_at)
            ),
            "returncode": returncode,
            "stdout_path": stdout_path.resolve().as_posix(),
            "stderr_path": stderr_path.resolve().as_posix(),
            "result_root": result_root.resolve().as_posix(),
            "aggregate_path": (
                None if aggregate_path is None else aggregate_path.resolve().as_posix()
            ),
        },
        "receipt_digest",
    )
    _validate_generated(
        document, RUN_RECEIPT_SCHEMA, "receipt_digest", "KB run receipt"
    )
    return document


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _archive_failed_harbor_result(
    result_root: Path, receipt: Mapping[str, Any]
) -> Path | None:
    """Move a failed/abandoned immutable-plan attempt aside before retrying."""

    if not result_root.exists():
        return None
    if not result_root.is_dir() or result_root.is_symlink():
        raise CapabilityImprovementError(
            f"unsafe failed Harbor result root: {result_root}"
        )
    digest = receipt.get("receipt_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise CapabilityImprovementError("failed Harbor receipt digest is invalid")
    failed_parent = result_root.parent / "failed-attempts"
    failed_parent.mkdir(exist_ok=True)
    failed_root = failed_parent / f"{result_root.name}-{digest[:12]}"
    if failed_root.exists():
        raise CapabilityImprovementError(
            f"failed Harbor attempt archive already exists: {failed_root}"
        )
    os.replace(result_root, failed_root)
    return failed_root


def _adjudicator_prompt(comparison: Mapping[str, Any]) -> str:
    return f"""You are the sole scientific audit assistant for one preserved Codex C0 diagnostic item.
Do not edit any input or canonical ground truth. Do not browse the web or use remembered kit knowledge. Inspect only files beneath the current workspace's `inputs/` directory. Never search for or open repository, archive, baseline, ground-truth, or protocol files outside this workspace; doing so deterministically rejects the attempt. The applicable audit policies are already staged under `inputs/policies/`.

Protocol: {comparison["protocol_id"]}
Training ordinal: {comparison["training_ordinal"]}

Tasks:
1. Read `inputs/error_analysis.json`; every observation with `substantive: true` must appear in exactly one finding's `observation_ids`. Consolidate dependent symptoms into causal root findings instead of copying observations one by one.
2. Verify each root finding against the included primary sources and ground truth. The current ground truth is a comparison target, never independent evidence.
3. Inspect `inputs/trajectory.json` to determine why the fixed baseline prediction diverged. An eligible finding needs at least one precise primary-source locator and one precise trajectory locator.
4. Set `learning_eligible` true exactly when benchmark_validity=valid, attribution=agent, source_recoverability=recoverable, and consolidated_root_event=true.
5. For a non-eligible finding, `recommended_action` must be `none` or `knowledge_card_only`; it cannot request an agent prompt/checker change.
6. Read `inputs/current_learned_rules.json`. The pact is cumulative and must not start fresh. Propose at most one short protocol-neutral pact rule, only if supported by an eligible root event and materially new relative to the active rules. Do not include protocol names, sequences, kit identifiers, document names, or target-specific values in the rule. Prefer no proposal over a weak or redundant generalization. Select only a checker allowed by the output schema.
7. The baseline prediction, verifier discrepancy, and trajectory are diagnostic inputs only. They must never become retrievable knowledge-card content. Oligo and graph cards are generated separately and deterministically from canonical ground truth.

Use consecutive F001... IDs in source order. Return only the schema-conforming JSON object."""


def _adjudicator_contract_repair_prompt(
    *, adjudicator: Mapping[str, Any], error: str
) -> str:
    return f"""Repair one output-contract error in the prior adjudicator JSON.

Inspect only files beneath the current workspace. Do not browse, search the repository, or open any external file. The staged inputs and prior JSON are the complete evidence boundary.

Exact contract error: {error}
Prior JSON: {json.dumps(adjudicator, sort_keys=True)}

Preserve the protocol, ordinal, scientific summaries, evidence attribution, observation partition, finding order, eligibility facts, and pact proposal exactly. Change only the minimum label field needed to satisfy the exact contract error. In particular, a non-eligible finding may recommend only `none` or `knowledge_card_only`. Do not inspect new evidence, broaden a finding, or create a new proposal. Return only the complete schema-conforming JSON object."""


def _critic_prompt(
    proposal: Mapping[str, Any], proposal_digest: str, adjudicator_filename: str
) -> str:
    return f"""Independently review the pact proposal below. Do not defer to the first reviewer, edit anything, or browse the web. Inspect only files beneath the current workspace; never search the repository or open an external path. The staged policies and inputs are the complete evidence boundary. This is a bounded rule review, not a second whole-protocol audit: read `outputs/{adjudicator_filename}`, follow only the supporting finding IDs and their cited source/trajectory locators, and expand beyond those locations only if the cited evidence is insufficient. The caller already schema-validates the recommended checker against the allowed checker vocabulary, so do not inspect its implementation.

Proposal digest: {proposal_digest}
Proposal JSON: {json.dumps(proposal, sort_keys=True)}

Accept only if the rule is protocol-neutral, follows from source-linked agent error rather than benchmark/policy noise, selects an existing checker when one is named, and has no high regression risk. A useful-sounding rule is insufficient. Return only the schema-conforming JSON object with the exact proposal digest."""


def _validate_adjudicator_output(
    adjudicator: Mapping[str, Any],
    comparison: Mapping[str, Any],
    workspace: Path,
) -> None:
    if (
        adjudicator["protocol_id"] != comparison["protocol_id"]
        or adjudicator["training_ordinal"] != comparison["training_ordinal"]
    ):
        raise CapabilityImprovementError("adjudicator output targets another protocol")
    error_analysis = load_json_object(
        workspace / "inputs/error_analysis.json", label="staged error analysis"
    )
    expected = {
        item["error_id"]
        for item in error_analysis.get("observations", [])
        if item.get("substantive") is True
    }
    observed_list = [
        observation_id
        for finding in adjudicator["findings"]
        for observation_id in finding["observation_ids"]
    ]
    if len(observed_list) != len(set(observed_list)) or set(observed_list) != expected:
        raise CapabilityImprovementError(
            "Codex findings do not exactly partition substantive observations"
        )
    expected_finding_ids = [
        f"F{index:03d}" for index in range(1, len(adjudicator["findings"]) + 1)
    ]
    if [item["finding_id"] for item in adjudicator["findings"]] != expected_finding_ids:
        raise CapabilityImprovementError("Codex finding IDs are not consecutive")
    eligible_ids = set()
    for finding in adjudicator["findings"]:
        eligible = (
            finding["benchmark_validity"] == "valid"
            and finding["attribution"] == "agent"
            and finding["source_recoverability"] == "recoverable"
            and finding["consolidated_root_event"] is True
        )
        if finding["learning_eligible"] is not eligible:
            raise CapabilityImprovementError(
                f"Codex mislabeled learning eligibility for {finding['finding_id']}"
            )
        if eligible and (
            not finding["evidence_locators"] or not finding["trajectory_locators"]
        ):
            raise CapabilityImprovementError(
                f"eligible finding {finding['finding_id']} lacks dual provenance"
            )
        if eligible:
            eligible_ids.add(finding["finding_id"])
        elif finding["recommended_action"] not in {"none", "knowledge_card_only"}:
            raise CapabilityImprovementError(
                f"{finding['finding_id']} cannot change the procedural pact"
            )
    proposal = adjudicator["pact_proposal"]
    if proposal is not None:
        supporting = set(proposal["supporting_finding_ids"])
        if not supporting or not supporting.issubset(eligible_ids):
            raise CapabilityImprovementError(
                "pact proposal is not supported only by eligible root findings"
            )


def _validate_learned_rules(document: Mapping[str, Any]) -> None:
    if (
        set(document) != {"schema_version", "rules"}
        or document.get("schema_version") != "libstruct.libgen_kb_learned_rules.v1"
    ):
        raise CapabilityImprovementError("learned_rules.json has an invalid contract")
    rules = document.get("rules")
    if not isinstance(rules, list):
        raise CapabilityImprovementError("learned pact rules must be an array")
    expected = [f"R{index:03d}" for index in range(1, len(rules) + 1)]
    if [item.get("rule_id") for item in rules if isinstance(item, Mapping)] != expected:
        raise CapabilityImprovementError("learned pact rule IDs must be consecutive")
    allowed = {
        "none",
        "physical_oligo_linkage",
        "product_conservation",
        "source_reconciliation",
        "state_event_projection",
        "strand_pairing",
        "terminal_reachability",
        "typed_edges",
    }
    for item in rules:
        if not isinstance(item, Mapping) or set(item) != {
            "rule_id",
            "title",
            "category",
            "trigger",
            "instruction",
            "checker",
        }:
            raise CapabilityImprovementError("learned pact rule shape is invalid")
        if item["checker"] not in allowed:
            raise CapabilityImprovementError("learned pact names an unknown checker")


_RULE_STOP_WORDS = frozenset(
    "a an and any as at be by each every for from has have in into is it its "
    "of on only or that the their then this to using when where whose with".split()
)
_RULE_TOKEN_EQUIVALENTS = {
    "architectures": "architecture",
    "bases": "base",
    "encode": "represent",
    "encoded": "represent",
    "determine": "deterministic",
    "labels": "label",
    "full": "complete",
    "materialize": "represent",
    "materialized": "represent",
    "ordered": "order",
    "placeholders": "placeholder",
    "projections": "projection",
    "sequence": "architecture",
    "segments": "segment",
    "strings": "string",
    "write": "represent",
    "writes": "represent",
}


def _rule_semantic_tokens(value: Any) -> frozenset[str]:
    text = str(value).casefold().replace("sequence_architecture", "architecture")
    tokens = re.findall(r"[a-z0-9]+", text)
    return frozenset(
        _RULE_TOKEN_EQUIVALENTS.get(token, token)
        for token in tokens
        if token not in _RULE_STOP_WORDS
    )


def _rule_containment(left: Any, right: Any) -> tuple[float, int]:
    left_tokens = _rule_semantic_tokens(left)
    right_tokens = _rule_semantic_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0, 0
    common = len(left_tokens & right_tokens)
    return common / min(len(left_tokens), len(right_tokens)), common


def _proposal_rule_match(
    proposal: Mapping[str, Any], rule: Mapping[str, Any]
) -> tuple[str, float, float] | None:
    """Return a conservative exact/semantic match for pact-rule deduplication."""

    def normalized(value: Any) -> str:
        return " ".join(str(value).casefold().split())

    signature = (
        normalized(proposal["category"]),
        normalized(proposal["trigger"]),
        normalized(proposal["instruction"]),
        normalized(proposal["recommended_checker"]),
    )
    rule_signature = (
        normalized(rule["category"]),
        normalized(rule["trigger"]),
        normalized(rule["instruction"]),
        normalized(rule["checker"]),
    )
    if signature == rule_signature:
        return "exact", 1.0, 1.0
    if signature[0] != rule_signature[0]:
        return None
    trigger_overlap, trigger_common = _rule_containment(
        proposal["trigger"], rule["trigger"]
    )
    instruction_overlap, instruction_common = _rule_containment(
        proposal["instruction"], rule["instruction"]
    )
    if (
        trigger_overlap >= 0.50
        and trigger_common >= 3
        and instruction_overlap >= 0.60
        and instruction_common >= 5
    ):
        return "semantic-near-duplicate", trigger_overlap, instruction_overlap
    return None


def _find_similar_pact_rule(
    root: Path, manifest: Mapping[str, Any], proposal: Mapping[str, Any]
) -> tuple[str, Mapping[str, Any], float, float] | None:
    state = _load_validated(
        root / manifest["current_microstate"]["path"],
        MICROSTATE_SCHEMA,
        "microstate_digest",
        "current KB microstate",
    )
    if state["pact"]["kind"] != "learned":
        return None
    rules_path = root / state["pact"]["path"] / "checklists/learned_rules.json"
    rules = load_json_object(rules_path, label="current learned rules")
    _validate_learned_rules(rules)
    matches = []
    for rule in rules["rules"]:
        match = _proposal_rule_match(proposal, rule)
        if match is not None:
            kind, trigger_overlap, instruction_overlap = match
            matches.append(
                (kind, rule, trigger_overlap, instruction_overlap)
            )
    if not matches:
        return None
    return max(matches, key=lambda item: (item[2] + item[3], item[1]["rule_id"]))


def _pact_proposal_is_duplicate(
    root: Path, manifest: Mapping[str, Any], proposal: Mapping[str, Any]
) -> bool:
    return _find_similar_pact_rule(root, manifest, proposal) is not None


def _verifier_root_matches(root: Path, protocol_id: str) -> bool:
    details = root / "details.json"
    if not details.is_file() or details.is_symlink():
        return False
    try:
        value = load_json_object(details, label="candidate verifier details")
    except Exception:
        return False
    return (
        value.get("benchmark_version") == LIBGEN_BENCHMARK_VERSION
        and value.get("protocol_id") == protocol_id
    )


def _load_json_or_none(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _absolute_artifact(path: Path) -> dict[str, str]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise CapabilityImprovementError(f"artifact is missing or unsafe: {resolved}")
    return {"path": resolved.as_posix(), "sha256": sha256_file(resolved)}


def _load_validated(
    path: Path, schema: str, digest_field: str, label: str
) -> dict[str, Any]:
    document = load_json_object(path, label=label)
    _validate_generated(document, schema, digest_field, label)
    return document


def _validate_generated(
    document: Mapping[str, Any], schema: str, digest_field: str, label: str
) -> None:
    validate_document(dict(document), improvement_schema_root() / schema, label=label)
    expected = canonical_digest(
        {key: value for key, value in document.items() if key != digest_field}
    )
    if document.get(digest_field) != expected:
        raise CapabilityImprovementError(f"{label} has a stale {digest_field}")


def _freeze_read_inputs(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise CapabilityImprovementError(f"symlink is forbidden: {path}")
        if path.is_file():
            executable = bool(path.stat().st_mode & 0o100)
            path.chmod(0o555 if executable else 0o444)
        elif path.is_dir():
            path.chmod(0o555)
    root.chmod(0o555)


def _make_tree_writable(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CapabilityImprovementError(f"symlink is forbidden: {path}")
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            executable = bool(path.stat().st_mode & 0o100)
            path.chmod(0o755 if executable else 0o644)
    root.chmod(0o755)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
