from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from dirhash import dirhash

from libstruct_bench.audit.artifacts import sha256_file, validate_document

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    improvement_schema_root,
    load_and_validate,
    normalized_timestamp,
    with_digest,
)


BASELINE_REGISTRY_SCHEMA_VERSION = "libstruct.libgen_baseline_registry.v1"
REQUIRED_METRICS = (
    "reward",
    "t2_exact_required_family_recall",
    "t2_required_family_f1",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)
TRIAL_ARTIFACTS = (
    ("result.json", "trial_result", True),
    ("artifacts/logs/artifacts/t2_prediction.json", "t2_prediction", True),
    ("artifacts/logs/artifacts/t3_prediction.json", "t3_prediction", True),
    ("agent/trajectory.json", "trajectory", False),
    ("artifacts/agent_trajectory.json", "trajectory_export", False),
    ("verifier/reward.json", "verifier_reward", True),
    ("verifier/details.json", "verifier_details", True),
    ("verifier/error_analysis.json", "verifier_error_analysis", True),
    ("verifier/error.json", "verifier_error", False),
)


def build_baseline_registry(
    *,
    panel_protocol_ids: Sequence[str],
    panel_commitment_sha256: str,
    run_roots: Sequence[Path],
    tasks_root: Path,
    created_at: str,
) -> dict[str, Any]:
    """Index no-capability-pack attempts without treating failures as zeroes."""

    panel = tuple(panel_protocol_ids)
    if len(panel) != 10 or len(set(panel)) != 10:
        raise CapabilityImprovementError(
            "baseline registry requires ten unique panel protocols"
        )
    if not run_roots:
        raise CapabilityImprovementError(
            "baseline registry requires at least one run root"
        )
    tasks = tasks_root.expanduser().resolve()
    frozen_task_checksums = {
        protocol_id: _task_checksum(tasks / protocol_id) for protocol_id in panel
    }
    sources: list[dict[str, Any]] = []
    trials: list[dict[str, Any]] = []
    for priority, raw_root in enumerate(run_roots, start=1):
        root = raw_root.expanduser().resolve()
        config_path = root / "config.json"
        lock_path = root / "lock.json"
        result_path = root / "result.json"
        if not all(path.is_file() for path in (config_path, lock_path, result_path)):
            raise CapabilityImprovementError(f"baseline job root is incomplete: {root}")
        config = _load_object(config_path, "baseline config")
        semantic = _semantic_configuration(config)
        semantic_digest = canonical_digest(semantic)
        no_pack = _has_no_capability_pack(config)
        source_id = f"baseline-{priority:03d}"
        sources.append(
            {
                "source_id": source_id,
                "priority": priority,
                "job_root": root.as_posix(),
                "config_sha256": sha256_file(config_path),
                "lock_sha256": sha256_file(lock_path),
                "result_sha256": sha256_file(result_path),
                "semantic_configuration": semantic,
                "semantic_configuration_digest": semantic_digest,
                "no_capability_pack": no_pack,
            }
        )
        seen: set[str] = set()
        for trial_path in sorted(root.glob("*/result.json")):
            result = _load_object(trial_path, "baseline trial result")
            task_path = (result.get("task_id") or {}).get("path")
            protocol_id = Path(task_path).name if isinstance(task_path, str) else None
            if protocol_id not in panel:
                continue
            if protocol_id in seen:
                raise CapabilityImprovementError(
                    f"baseline source repeats protocol {protocol_id}: {root}"
                )
            seen.add(protocol_id)
            trials.append(
                _trial_record(
                    source_id=source_id,
                    protocol_id=protocol_id,
                    trial_root=trial_path.parent,
                    result=result,
                    semantic_configuration_digest=semantic_digest,
                    no_capability_pack=no_pack,
                    expected_task_checksum=frozen_task_checksums[protocol_id],
                )
            )
    selected: set[tuple[str, str]] = set()
    for trial in trials:
        key = (trial["semantic_configuration_digest"], trial["protocol_id"])
        choose = trial["status"] == "valid_reusable" and key not in selected
        trial["selected_for_reuse"] = choose
        if choose:
            selected.add(key)
    source_by_id = {item["source_id"]: item for item in sources}
    by_config: dict[str, set[str]] = defaultdict(set)
    for trial in trials:
        if trial["selected_for_reuse"]:
            by_config[trial["semantic_configuration_digest"]].add(trial["protocol_id"])
    coverage = []
    for digest in sorted({item["semantic_configuration_digest"] for item in sources}):
        source = next(
            item for item in sources if item["semantic_configuration_digest"] == digest
        )
        covered = by_config[digest]
        semantic = source["semantic_configuration"]
        coverage.append(
            {
                "semantic_configuration_digest": digest,
                "agent": semantic["agent"],
                "model": semantic["model"],
                "valid_selected_protocol_count": len(covered),
                "missing_protocol_ids": [item for item in panel if item not in covered],
            }
        )
    payload: dict[str, Any] = {
        "schema_version": BASELINE_REGISTRY_SCHEMA_VERSION,
        "registry_id": "libgen-frozen-transfer-panel-baselines-v1",
        "panel_id": "frozen-retrospective-transfer-panel-v1",
        "panel_commitment_sha256": panel_commitment_sha256,
        "created_at": normalized_timestamp(created_at),
        "tasks_root": tasks.as_posix(),
        "frozen_task_checksums": frozen_task_checksums,
        "matching_policy": {
            "required_fields": [
                "agent",
                "model",
                "agent_kwargs",
                "environment_type",
                "task_checksum",
                "no_capability_pack",
            ],
            "validity": "complete_predictions_complete_required_metrics_no_exception_no_capability_pack",
            "selection": "first_valid_source_by_declared_priority_per_semantic_configuration_and_protocol",
        },
        "sources": sources,
        "trial_records": trials,
        "coverage": coverage,
        "agent_visibility": "none_orchestrator_only",
    }
    registry = with_digest(payload, "registry_digest")
    validate_document(
        registry,
        improvement_schema_root() / "baseline_registry.schema.json",
        label="capability baseline registry",
    )
    # Keep this lookup here so a malformed source reference cannot be hidden by schema validity.
    if any(item["source_id"] not in source_by_id for item in trials):
        raise CapabilityImprovementError("baseline trial references an unknown source")
    return registry


def validate_baseline_registry(path: Path) -> dict[str, Any]:
    return load_and_validate(
        path,
        schema_filename="baseline_registry.schema.json",
        digest_field="registry_digest",
        label="capability baseline registry",
    )


def selected_matching_baselines(
    registry: Mapping[str, Any],
    *,
    semantic_configuration_digest: str,
    protocol_ids: Sequence[str],
    task_checksums: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return one pinned reusable baseline per requested protocol or fail closed."""

    requested = list(protocol_ids)
    records = [
        item
        for item in registry["trial_records"]
        if item["selected_for_reuse"]
        and item["semantic_configuration_digest"] == semantic_configuration_digest
        and item["protocol_id"] in requested
    ]
    by_protocol = {item["protocol_id"]: item for item in records}
    if set(by_protocol) != set(requested) or len(records) != len(requested):
        raise CapabilityImprovementError(
            "baseline registry lacks one reusable matching trial per transfer-panel protocol"
        )
    if task_checksums is not None:
        for protocol_id, record in by_protocol.items():
            if record["task_checksum"] != task_checksums.get(protocol_id):
                raise CapabilityImprovementError(
                    f"baseline task checksum is stale for {protocol_id}"
                )
    return [by_protocol[item] for item in requested]


def _semantic_configuration(config: Mapping[str, Any]) -> dict[str, Any]:
    agents = config.get("agents")
    if (
        not isinstance(agents, list)
        or len(agents) != 1
        or not isinstance(agents[0], Mapping)
    ):
        raise CapabilityImprovementError("baseline config requires exactly one agent")
    agent = agents[0]
    environment = config.get("environment")
    environment_type = None
    if isinstance(environment, Mapping):
        environment_type = environment.get("type") or environment.get("name")
    return {
        "agent": str(agent.get("name") or ""),
        "model": str(agent.get("model_name") or ""),
        "agent_kwargs": dict(sorted((agent.get("kwargs") or {}).items())),
        "environment_type": environment_type or "docker",
    }


def _has_no_capability_pack(config: Mapping[str, Any]) -> bool:
    if config.get("extra_instruction_paths"):
        return False
    environment = config.get("environment") or {}
    mounts = environment.get("mounts") if isinstance(environment, Mapping) else None
    for mount in mounts or []:
        if not isinstance(mount, Mapping):
            return False
        target = str(mount.get("target") or "")
        source = str(mount.get("source") or "")
        if "capability_pack" in target or "capability_pack" in source:
            return False
    return True


def _trial_record(
    *,
    source_id: str,
    protocol_id: str,
    trial_root: Path,
    result: Mapping[str, Any],
    semantic_configuration_digest: str,
    no_capability_pack: bool,
    expected_task_checksum: str,
) -> dict[str, Any]:
    artifacts = []
    missing = []
    for relative, role, required in TRIAL_ARTIFACTS:
        path = trial_root / relative
        if path.is_file():
            artifacts.append(
                {
                    "role": role,
                    "path": path.resolve().as_posix(),
                    "sha256": sha256_file(path),
                }
            )
        elif required:
            missing.append(relative)
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    missing_metrics = [
        item
        for item in REQUIRED_METRICS
        if not isinstance(rewards.get(item), (int, float))
    ]
    exception_type = (result.get("exception_info") or {}).get("exception_type")
    task_checksum = result.get("task_checksum")
    valid_checksum = isinstance(task_checksum, str) and len(task_checksum) == 64
    reasons = []
    if not no_capability_pack:
        reasons.append("capability pack or extra instruction present")
    if exception_type:
        reasons.append(f"exception={exception_type}")
    if missing:
        reasons.append("missing=" + ",".join(missing))
    if missing_metrics:
        reasons.append("missing_metrics=" + ",".join(missing_metrics))
    if not valid_checksum:
        reasons.append("missing_or_invalid_task_checksum")
    elif task_checksum != expected_task_checksum:
        reasons.append("task_checksum_differs_from_frozen_task")
    status = "valid_reusable" if not reasons else "invalid"
    return {
        "source_id": source_id,
        "protocol_id": protocol_id,
        "trial_name": str(result.get("trial_name") or trial_root.name),
        "semantic_configuration_digest": semantic_configuration_digest,
        "task_checksum": task_checksum if valid_checksum else None,
        "result_sha256": sha256_file(trial_root / "result.json"),
        "artifact_hashes": artifacts,
        "metrics": {
            metric: float(rewards[metric])
            if isinstance(rewards.get(metric), (int, float))
            else None
            for metric in REQUIRED_METRICS
        },
        "status": status,
        "reason": "all reuse checks passed" if not reasons else "; ".join(reasons),
        "selected_for_reuse": False,
    }


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            f"cannot read {label} {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise CapabilityImprovementError(f"{label} must be an object: {path}")
    return value


def current_task_checksums(
    tasks_root: Path,
    protocol_ids: Sequence[str],
) -> dict[str, str]:
    root = tasks_root.expanduser().resolve()
    return {
        protocol_id: _task_checksum(root / protocol_id) for protocol_id in protocol_ids
    }


def _task_checksum(path: Path) -> str:
    if not path.is_dir():
        raise CapabilityImprovementError(f"baseline task is missing: {path}")
    try:
        return str(dirhash(path, "sha256"))
    except (OSError, ValueError, TypeError) as error:
        raise CapabilityImprovementError(
            f"cannot checksum baseline task {path}: {error}"
        ) from error
