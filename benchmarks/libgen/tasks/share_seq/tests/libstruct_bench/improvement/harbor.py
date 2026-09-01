from __future__ import annotations

import copy
import json
import random
import re
import shutil
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import (
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.libgen.error_analysis import task_bundle_sha256

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    copy_capability_pack,
    freeze_tree,
    improvement_schema_root,
    normalized_timestamp,
    validate_capability_pack,
    validate_digest,
    with_digest,
)
from .exemplar_memory import exemplar_memory_record, validate_exemplar_memory
from .experiment import (
    ENDPOINT_LABELS,
    REPLAY_LABELS,
    assert_final_split_frozen,
    validate_final_lock,
    validate_transfer_panel_authorization,
)
from .lineage import ACTIVE_BRANCH, checkpoint_before_batch
from .mutation_lock import guard_experiment_mutation
from .split_design import FINAL_TRANSFER_PANEL


CAPABILITY_MOUNT_TARGET = "/workspace/capability_pack"
EXEMPLAR_MEMORY_MOUNT_TARGET = "/workspace/capability_memory"
HARBOR_INTEGRATION_SCHEMA_VERSION = "libstruct.libgen_capability_harbor_integration.v1"
FIXED_PANEL_REPLAY_SCHEMA_VERSION = "libstruct.libgen_capability_fixed_panel_replay.v1"
PROSPECTIVE_ROUND_PLAN_SCHEMA_VERSION = (
    "libstruct.libgen_capability_prospective_round_plan.v1"
)
VERIFIER_ARTIFACTS = (
    "/logs/verifier/reward.json",
    "/logs/verifier/details.json",
    "/logs/verifier/error_analysis.json",
    "/logs/verifier/error.json",
)
EXEMPLAR_DIAGNOSTIC_ARTIFACTS = (
    "/logs/artifacts/exemplar_usage.json",
    "/logs/artifacts/target_evidence_guard.json",
)
FIXED_ANCHOR = {
    "agent": "codex",
    "model": "gpt-5.6-sol",
    "version": "0.147.0",
    "reasoning_effort": "max",
    # Harbor job-level trial concurrency. Each trial still runs exactly one
    # Codex agent; this only schedules independent protocols in parallel.
    "concurrency": 4,
}


@dataclass(frozen=True)
class _FinalReplayAuthorization:
    experiment_root: Path
    experiment_digest: str
    experiment_manifest_sha256: str
    lock_digest: str
    lock_sha256: str
    authorization_digest: str
    authorization_sha256: str


def prepare_capability_harbor_integration(
    *,
    pack_root: Path,
    tasks_root: Path,
    protocol_ids: Sequence[str],
    output_root: Path,
    created_at: str,
) -> dict[str, Any]:
    """Prepare one read-only, agent-only checkpoint exposure without editing tasks.

    ``pack_root`` accepts a bare capability pack, a frozen
    checkpoint root containing ``checkpoint.json``, ``runtime.json``, and
    ``pack/``, or that checkpoint's exact ``checkpoint.json`` file. Frozen
    checkpoints are consumed through their declared runtime contract so the
    Harbor adapter does not invent a second interface.
    """

    return _prepare_capability_harbor_integration(
        pack_root=pack_root,
        tasks_root=tasks_root,
        protocol_ids=protocol_ids,
        output_root=output_root,
        created_at=created_at,
        final_replay_authorization=None,
    )


def _prepare_capability_harbor_integration(
    *,
    pack_root: Path,
    tasks_root: Path,
    protocol_ids: Sequence[str],
    output_root: Path,
    created_at: str,
    final_replay_authorization: _FinalReplayAuthorization | None,
) -> dict[str, Any]:
    """Implement pack exposure after enforcing the frozen-panel boundary."""

    requested = tuple(protocol_ids)
    frozen = set(FINAL_TRANSFER_PANEL)
    overlap = sorted(frozen.intersection(requested))
    if overlap and final_replay_authorization is None:
        raise CapabilityImprovementError(
            "frozen final-test protocols may be staged only by the post-lock "
            "final-replay planner: " + ", ".join(overlap)
        )
    if final_replay_authorization is not None and requested != FINAL_TRANSFER_PANEL:
        raise CapabilityImprovementError(
            "authorized final replay must stage the exact frozen ten-protocol panel"
        )

    (
        source_pack_root,
        pack,
        checkpoint_runtime,
        source_memory_root,
        source_memory,
    ) = _resolve_capability_source(pack_root)
    final_test_authorization = None
    if final_replay_authorization is not None:
        final_test_authorization = _validate_final_replay_integration_authorization(
            authorization=final_replay_authorization,
            output_root=output_root,
            checkpoint_runtime=checkpoint_runtime,
        )
    _validate_tasks(tasks_root, protocol_ids)
    tasks_root = tasks_root.expanduser().resolve()
    task_digest_before = task_bundle_sha256(tasks_root, list(protocol_ids))
    output_root = output_root.expanduser().resolve()
    if output_root.exists():
        raise CapabilityImprovementError(
            f"refusing to overwrite Harbor integration: {output_root}"
        )
    exposure = output_root / "exposure"
    memory_exposure = output_root / "exemplar_memory"
    output_root.mkdir(parents=True)
    try:
        copy_capability_pack(source_pack_root, exposure, freeze=True)
        exposed_memory: dict[str, Any] | None = None
        if source_memory_root is not None:
            if source_memory is None:
                raise CapabilityImprovementError(
                    "frozen checkpoint resolved memory without its binding"
                )
            shutil.copytree(source_memory_root, memory_exposure)
            freeze_tree(memory_exposure)
            exposed_memory = exemplar_memory_record(memory_exposure)
            if exposed_memory != source_memory:
                raise CapabilityImprovementError(
                    "exposed exemplar memory differs from its frozen checkpoint"
                )
        instruction_path = output_root / "extra_instruction.md"
        instruction_path.write_text(
            _extra_instruction(
                pack_digest=pack["pack_digest"],
                checkpoint_runtime=checkpoint_runtime,
                exemplar_memory=exposed_memory,
            ),
            encoding="utf-8",
        )
        instruction_path.chmod(0o444)
        mount = {
            "type": "bind",
            "source": exposure.as_posix(),
            "target": CAPABILITY_MOUNT_TARGET,
            "read_only": True,
            "bind": {"create_host_path": False},
        }
        memory_mount = (
            {
                "type": "bind",
                "source": memory_exposure.as_posix(),
                "target": EXEMPLAR_MEMORY_MOUNT_TARGET,
                "read_only": True,
                "bind": {"create_host_path": False},
            }
            if exposed_memory is not None
            else None
        )
        payload: dict[str, Any] = {
            "schema_version": HARBOR_INTEGRATION_SCHEMA_VERSION,
            "created_at": created_at,
            "pack_digest": pack["pack_digest"],
            "pack_manifest_sha256": sha256_file(exposure / "manifest.json"),
            "exemplar_memory": exposed_memory,
            "checkpoint_runtime": checkpoint_runtime,
            "final_test_authorization": final_test_authorization,
            "protocol_ids": list(protocol_ids),
            "task_bundle_sha256": task_digest_before,
            "exposure_root": exposure.as_posix(),
            "memory_exposure_root": (
                memory_exposure.as_posix() if exposed_memory is not None else None
            ),
            "extra_instruction_path": instruction_path.as_posix(),
            "extra_instruction_sha256": sha256_file(instruction_path),
            "mount": mount,
            "memory_mount": memory_mount,
            "agent_visibility": (
                "read_only_allowlisted_pack_and_exemplar_memory"
                if exposed_memory is not None
                else "read_only_allowlisted_pack"
            ),
            "verifier_visibility": "none_separate_environment",
            "exemplar_usage_scored": False,
            "target_evidence_guard": (
                {
                    "scope": "full_frozen_exemplar_catalog",
                    "required_before_finalization": True,
                    "success_exit": 0,
                    "findings_exit": 1,
                    "input_error_exit": 2,
                    "nonzero_blocks_finalization": True,
                    "usage_artifact": EXEMPLAR_DIAGNOSTIC_ARTIFACTS[0],
                    "report_artifact": EXEMPLAR_DIAGNOSTIC_ARTIFACTS[1],
                }
                if exposed_memory is not None
                else None
            ),
            "baseline_tasks_modified": False,
        }
        integration = with_digest(payload, "integration_digest")
        write_json_atomic(
            output_root / "integration_manifest.json", integration, mode=0o444
        )
        if task_bundle_sha256(tasks_root, list(protocol_ids)) != task_digest_before:
            raise CapabilityImprovementError(
                "task bytes changed while preparing pack exposure"
            )
        validate_capability_harbor_integration(output_root, tasks_root=tasks_root)
        return integration
    except BaseException:
        shutil.rmtree(output_root, ignore_errors=True)
        raise


def validate_capability_harbor_integration(
    integration_root: Path,
    *,
    tasks_root: Path,
) -> dict[str, Any]:
    path = integration_root / "integration_manifest.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    validate_digest(document, "integration_digest")
    if document.get("schema_version") != HARBOR_INTEGRATION_SCHEMA_VERSION:
        raise CapabilityImprovementError("unknown capability Harbor integration schema")
    if "final_test_authorization" not in document:
        raise CapabilityImprovementError(
            "capability Harbor integration lacks its final-test access state"
        )
    if (
        not isinstance(document.get("protocol_ids"), list)
        or not document["protocol_ids"]
    ):
        raise CapabilityImprovementError(
            "capability Harbor integration requires a nonempty protocol list"
        )
    pack = validate_capability_pack(integration_root / "exposure")
    if pack["pack_digest"] != document.get("pack_digest") or sha256_file(
        integration_root / "exposure" / "manifest.json"
    ) != document.get("pack_manifest_sha256"):
        raise CapabilityImprovementError(
            "exposed pack differs from integration manifest"
        )
    if document.get("exposure_root") != (integration_root / "exposure").as_posix():
        raise CapabilityImprovementError(
            "capability-pack exposure path differs from integration manifest"
        )
    checkpoint_runtime = document.get("checkpoint_runtime")
    memory_record = document.get("exemplar_memory")
    if checkpoint_runtime is not None:
        if not isinstance(checkpoint_runtime, Mapping):
            raise CapabilityImprovementError(
                "capability checkpoint runtime record must be an object"
            )
        legacy_runtime_fields = {
            "checkpoint_id",
            "checkpoint_digest",
            "checkpoint_sha256",
            "runtime_digest",
            "runtime_sha256",
            "exemplar_memory",
        }
        current_runtime_fields = legacy_runtime_fields | {"exemplar_max_results"}
        observed_runtime_fields = frozenset(checkpoint_runtime)
        if observed_runtime_fields not in {
            frozenset(legacy_runtime_fields),
            frozenset(current_runtime_fields),
        }:
            raise CapabilityImprovementError(
                "capability checkpoint runtime record has unexpected fields"
            )
        if any(
            not isinstance(checkpoint_runtime[field], str)
            or not checkpoint_runtime[field]
            for field in legacy_runtime_fields - {"exemplar_memory"}
        ):
            raise CapabilityImprovementError(
                "capability checkpoint runtime record is incomplete"
            )
        exemplar_max_results = checkpoint_runtime.get("exemplar_max_results", 3)
        if exemplar_max_results not in {1, 2, 3}:
            raise CapabilityImprovementError(
                "capability checkpoint runtime has an invalid donor limit"
            )
        if not isinstance(memory_record, Mapping):
            raise CapabilityImprovementError(
                "frozen checkpoint integration lacks exemplar memory"
            )
        if checkpoint_runtime.get("exemplar_memory") != memory_record:
            raise CapabilityImprovementError(
                "checkpoint runtime and Harbor exemplar memory disagree"
            )
    elif memory_record is not None:
        raise CapabilityImprovementError(
            "bare capability pack integration cannot attach checkpoint memory"
        )
    memory_mount = document.get("memory_mount")
    if memory_record is not None:
        memory_root = integration_root / "exemplar_memory"
        memory = validate_exemplar_memory(
            memory_root,
            expected_count=memory_record["exemplar_count"],
        )
        if memory["memory_digest"] != memory_record.get("memory_digest") or (
            exemplar_memory_record(memory_root) != memory_record
        ):
            raise CapabilityImprovementError(
                "exposed exemplar memory differs from integration manifest"
            )
        if document.get("memory_exposure_root") != memory_root.as_posix():
            raise CapabilityImprovementError(
                "exemplar-memory exposure path differs from integration manifest"
            )
        if (
            not isinstance(memory_mount, Mapping)
            or memory_mount.get("source") != memory_root.as_posix()
            or memory_mount.get("target") != EXEMPLAR_MEMORY_MOUNT_TARGET
            or memory_mount.get("read_only") is not True
        ):
            raise CapabilityImprovementError(
                "exemplar-memory mount is missing or not read-only"
            )
        expected_guard = {
            "scope": "full_frozen_exemplar_catalog",
            "required_before_finalization": True,
            "success_exit": 0,
            "findings_exit": 1,
            "input_error_exit": 2,
            "nonzero_blocks_finalization": True,
            "usage_artifact": EXEMPLAR_DIAGNOSTIC_ARTIFACTS[0],
            "report_artifact": EXEMPLAR_DIAGNOSTIC_ARTIFACTS[1],
        }
        if document.get("target_evidence_guard") != expected_guard:
            raise CapabilityImprovementError(
                "target-evidence guard is not a mandatory full-catalog gate"
            )
        if document.get("agent_visibility") != (
            "read_only_allowlisted_pack_and_exemplar_memory"
        ):
            raise CapabilityImprovementError(
                "checkpoint exemplar memory is not declared agent-only"
            )
    elif (
        memory_mount is not None
        or document.get("memory_exposure_root") is not None
        or document.get("target_evidence_guard") is not None
        or document.get("agent_visibility") != "read_only_allowlisted_pack"
    ):
        raise CapabilityImprovementError(
            "bare pack integration has an unexpected exemplar-memory exposure"
        )
    if document.get("exemplar_usage_scored") is not False:
        raise CapabilityImprovementError(
            "exemplar usage must remain a non-scored diagnostic artifact"
        )
    if sha256_file(integration_root / "extra_instruction.md") != document.get(
        "extra_instruction_sha256"
    ):
        raise CapabilityImprovementError("capability extra instruction changed")
    mount = document.get("mount", {})
    if (
        mount.get("source") != (integration_root / "exposure").as_posix()
        or mount.get("target") != CAPABILITY_MOUNT_TARGET
        or mount.get("read_only") is not True
    ):
        raise CapabilityImprovementError("capability pack mount is not read-only")
    if document.get("verifier_visibility") != "none_separate_environment":
        raise CapabilityImprovementError(
            "capability pack and exemplar memory must remain hidden from verifier"
        )
    _validate_tasks(tasks_root, document["protocol_ids"])
    final_protocols = set(FINAL_TRANSFER_PANEL).intersection(document["protocol_ids"])
    final_authorization = document.get("final_test_authorization")
    if final_protocols:
        if tuple(document["protocol_ids"]) != FINAL_TRANSFER_PANEL:
            raise CapabilityImprovementError(
                "final-test integration must contain the exact frozen panel"
            )
        _validate_recorded_final_test_authorization(
            integration_root=integration_root,
            checkpoint_runtime=checkpoint_runtime,
            record=final_authorization,
        )
    elif final_authorization is not None:
        raise CapabilityImprovementError(
            "non-final Harbor integration may not carry final-test authorization"
        )
    current = task_bundle_sha256(tasks_root, document["protocol_ids"])
    if current != document["task_bundle_sha256"]:
        raise CapabilityImprovementError("target task bundle changed after integration")
    return document


def _validate_final_replay_integration_authorization(
    *,
    authorization: _FinalReplayAuthorization,
    output_root: Path,
    checkpoint_runtime: Mapping[str, Any] | None,
) -> dict[str, str]:
    if checkpoint_runtime is None:
        raise CapabilityImprovementError(
            "final replay requires a frozen cumulative checkpoint"
        )
    root = authorization.experiment_root.expanduser().resolve()
    manifest_path = root / "design" / "experiment_manifest.json"
    try:
        experiment = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(
            f"cannot read canonical experiment manifest: {error}"
        ) from error
    if not isinstance(experiment, dict):
        raise CapabilityImprovementError(
            "canonical experiment manifest must be a JSON object"
        )
    validate_digest(experiment, "experiment_digest")
    lock_path = root / "design" / "final_lock.json"
    lock = validate_final_lock(
        lock_path,
        experiment_root=root,
        experiment_manifest=experiment,
    )
    authorization_path = root / "design" / "transfer_panel_authorization.json"
    transfer_authorization = validate_transfer_panel_authorization(
        authorization_path,
        experiment_root=root,
        experiment_manifest=experiment,
        final_lock=lock,
    )
    expected_output = (
        root
        / "final"
        / "fixed-panel-replay"
        / "integrations"
        / checkpoint_runtime["checkpoint_id"]
    )
    if output_root.expanduser().resolve() != expected_output:
        raise CapabilityImprovementError(
            "final-test integration must use its canonical replay path: "
            f"{expected_output}"
        )
    expected = {
        "experiment_digest": experiment["experiment_digest"],
        "experiment_manifest_sha256": sha256_file(manifest_path),
        "lock_digest": lock["lock_digest"],
        "lock_sha256": sha256_file(lock_path),
        "authorization_digest": transfer_authorization["authorization_digest"],
        "authorization_sha256": sha256_file(authorization_path),
    }
    observed = {
        "experiment_digest": authorization.experiment_digest,
        "experiment_manifest_sha256": authorization.experiment_manifest_sha256,
        "lock_digest": authorization.lock_digest,
        "lock_sha256": authorization.lock_sha256,
        "authorization_digest": authorization.authorization_digest,
        "authorization_sha256": authorization.authorization_sha256,
    }
    if observed != expected:
        raise CapabilityImprovementError(
            "final replay integration authorization is stale"
        )
    return expected


def _validate_recorded_final_test_authorization(
    *,
    integration_root: Path,
    checkpoint_runtime: Mapping[str, Any] | None,
    record: Any,
) -> None:
    if not isinstance(record, Mapping):
        raise CapabilityImprovementError(
            "final-test integration lacks canonical lock authorization"
        )
    resolved = integration_root.expanduser().resolve()
    if len(resolved.parents) < 4:
        raise CapabilityImprovementError(
            "final-test integration is outside the canonical replay root"
        )
    root = resolved.parents[3]
    _validate_final_replay_integration_authorization(
        authorization=_FinalReplayAuthorization(
            experiment_root=root,
            experiment_digest=str(record.get("experiment_digest", "")),
            experiment_manifest_sha256=str(
                record.get("experiment_manifest_sha256", "")
            ),
            lock_digest=str(record.get("lock_digest", "")),
            lock_sha256=str(record.get("lock_sha256", "")),
            authorization_digest=str(record.get("authorization_digest", "")),
            authorization_sha256=str(record.get("authorization_sha256", "")),
        ),
        output_root=resolved,
        checkpoint_runtime=checkpoint_runtime,
    )
    if set(record) != {
        "experiment_digest",
        "experiment_manifest_sha256",
        "lock_digest",
        "lock_sha256",
        "authorization_digest",
        "authorization_sha256",
    }:
        raise CapabilityImprovementError(
            "final-test authorization record has unexpected fields"
        )


def build_harbor_job_config(
    *,
    base_config_path: Path,
    integration_root: Path,
    tasks_root: Path,
    protocol_ids: Sequence[str],
    job_name: str,
    jobs_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    integration = validate_capability_harbor_integration(
        integration_root,
        tasks_root=tasks_root,
    )
    if list(protocol_ids) != integration["protocol_ids"]:
        raise CapabilityImprovementError("job protocols differ from pack integration")
    config = json.loads(base_config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise CapabilityImprovementError("base Harbor config must be a JSON object")
    _validate_anchor_config(config)
    datasets = config.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != 1:
        raise CapabilityImprovementError(
            "base Harbor config requires exactly one dataset"
        )
    result = copy.deepcopy(config)
    result["job_name"] = job_name
    result["jobs_dir"] = jobs_dir.as_posix()
    result["n_concurrent_trials"] = FIXED_ANCHOR["concurrency"]
    result["datasets"][0]["path"] = tasks_root.as_posix()
    result["datasets"][0]["task_names"] = list(protocol_ids)
    result.setdefault("environment", {})
    mounts = list(result["environment"].get("mounts") or [])
    reserved_targets = {CAPABILITY_MOUNT_TARGET, EXEMPLAR_MEMORY_MOUNT_TARGET}
    if any(item.get("target") in reserved_targets for item in mounts):
        raise CapabilityImprovementError(
            "base config already mounts a capability or exemplar-memory target"
        )
    mounts.append(copy.deepcopy(integration["mount"]))
    if integration["memory_mount"] is not None:
        mounts.append(copy.deepcopy(integration["memory_mount"]))
    result["environment"]["mounts"] = mounts
    instructions = list(result.get("extra_instruction_paths") or [])
    instructions.append(integration["extra_instruction_path"])
    result["extra_instruction_paths"] = instructions
    result["retry"] = {"max_retries": 0}
    artifacts = list(result.get("artifacts") or [])
    for artifact in VERIFIER_ARTIFACTS:
        if artifact not in artifacts:
            artifacts.append(artifact)
    if integration["exemplar_memory"] is not None:
        for artifact in EXEMPLAR_DIAGNOSTIC_ARTIFACTS:
            if artifact not in artifacts:
                artifacts.append(artifact)
    result["artifacts"] = artifacts
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_path, result)
    return result


def _validate_anchor_config(config: Mapping[str, Any]) -> dict[str, Any]:
    agents = config.get("agents")
    if not isinstance(agents, list) or len(agents) != 1:
        raise CapabilityImprovementError(
            "capability experiment requires exactly one native Codex agent"
        )
    agent = agents[0]
    if not isinstance(agent, Mapping):
        raise CapabilityImprovementError("Harbor agent config must be an object")
    kwargs = agent.get("kwargs")
    if not isinstance(kwargs, Mapping):
        raise CapabilityImprovementError(
            "capability experiment requires pinned native Codex kwargs"
        )
    observed = {
        "agent": agent.get("name"),
        "model": agent.get("model_name"),
        "version": kwargs.get("version"),
        "reasoning_effort": kwargs.get("reasoning_effort"),
        "concurrency": config.get("n_concurrent_trials", FIXED_ANCHOR["concurrency"]),
    }
    if observed != FIXED_ANCHOR:
        raise CapabilityImprovementError(
            "base Harbor config differs from the frozen native Codex/GPT-5.6-sol "
            f"anchor: observed={observed}"
        )
    environment = config.get("environment") or {}
    if not isinstance(environment, Mapping):
        raise CapabilityImprovementError("Harbor environment config must be an object")
    if environment.get("type", "docker") != "docker":
        raise CapabilityImprovementError(
            "fixed-panel replay requires Harbor's Docker environment"
        )
    return observed


@guard_experiment_mutation("plan final capability replay and unseal final test")
def build_final_replay_plan(
    *,
    experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
    final_lock_path: Path,
    transfer_panel_authorization_path: Path,
    checkpoint_pack_roots: Mapping[str, Path] | None,
    tasks_root: Path,
    base_config_path: Path,
    output_root: Path,
    jobs_dir: Path,
    created_at: str,
) -> dict[str, Any]:
    """Plan one post-lock, fixed-panel Harbor job for every frozen pack."""

    root = experiment_root.expanduser().resolve()
    lock = validate_final_lock(
        final_lock_path,
        experiment_root=root,
        experiment_manifest=experiment_manifest,
    )
    authorization = validate_transfer_panel_authorization(
        transfer_panel_authorization_path,
        experiment_root=root,
        experiment_manifest=experiment_manifest,
        final_lock=lock,
    )
    if lock.get("checkpoint_modification_closed") is not True:
        raise CapabilityImprovementError(
            "fixed-panel replay requires a final development lock"
        )
    pack_roots = dict(
        checkpoint_pack_roots
        if checkpoint_pack_roots is not None
        else _default_replay_pack_roots(root, experiment_manifest)
    )
    if set(pack_roots) != set(REPLAY_LABELS):
        raise CapabilityImprovementError(
            "fixed-panel replay requires exactly " + ", ".join(REPLAY_LABELS)
        )
    expected_digests = {
        item["checkpoint_id"]: item["pack_digest"]
        for item in lock["checkpoint_records"]
    }
    lock_records = {item["checkpoint_id"]: item for item in lock["checkpoint_records"]}
    packs: list[dict[str, Any]] = []
    for label in REPLAY_LABELS:
        checkpoint_root = _require_replay_checkpoint_root(
            pack_roots[label],
            experiment_root=root,
            label=label,
        )
        from .workflow import validate_checkpoint_runtime

        checkpoint, runtime, pack = validate_checkpoint_runtime(checkpoint_root)
        if pack["pack_digest"] != expected_digests[label]:
            raise CapabilityImprovementError(
                f"fixed-panel replay pack is stale: {label}"
            )
        checkpoint_path = checkpoint_root / "checkpoint.json"
        runtime_path = checkpoint_root / "runtime.json"
        record = lock_records[label]
        if sha256_file(checkpoint_path) != record["checkpoint_sha256"]:
            raise CapabilityImprovementError(
                f"fixed-panel checkpoint changed after final lock: {label}"
            )
        if checkpoint["checkpoint_digest"] != record["checkpoint_digest"]:
            raise CapabilityImprovementError(
                f"fixed-panel checkpoint digest differs from final lock: {label}"
            )
        if checkpoint["experiment_digest"] != experiment_manifest["experiment_digest"]:
            raise CapabilityImprovementError(
                f"fixed-panel checkpoint lineage differs from experiment: {label}"
            )
        if checkpoint["checkpoint_id"] != label:
            raise CapabilityImprovementError(
                f"fixed-panel checkpoint label differs from its record: {label}"
            )
        packs.append(
            {
                "label": label,
                "branch": checkpoint["branch"],
                "protocol_count": checkpoint["protocol_count"],
                "pack_digest": pack["pack_digest"],
                "pack_manifest_sha256": sha256_file(
                    checkpoint_root / "pack" / "manifest.json"
                ),
                "memory_digest": checkpoint["exemplar_memory"]["memory_digest"],
                "memory_manifest_sha256": checkpoint["exemplar_memory"][
                    "memory_manifest_sha256"
                ],
                "memory_catalog_digest": checkpoint["exemplar_memory"][
                    "catalog_digest"
                ],
                "memory_catalog_sha256": checkpoint["exemplar_memory"][
                    "catalog_sha256"
                ],
                "exemplar_count": checkpoint["exemplar_memory"]["exemplar_count"],
                "identity_map_commitment": checkpoint["exemplar_memory"][
                    "identity_map_commitment"
                ],
                "checkpoint_digest": checkpoint["checkpoint_digest"],
                "checkpoint_sha256": record["checkpoint_sha256"],
                "checkpoint_experiment_digest": checkpoint["experiment_digest"],
                "runtime_digest": runtime["runtime_digest"],
                "runtime_sha256": sha256_file(runtime_path),
            }
        )
    output_root = output_root.expanduser().resolve()
    expected_output_root = root / "final" / "fixed-panel-replay"
    if output_root != expected_output_root:
        raise CapabilityImprovementError(
            "fixed-panel replay must use the canonical output root: "
            f"{expected_output_root}"
        )
    if output_root.exists():
        raise CapabilityImprovementError(
            f"fixed-panel replay manifest already exists: {output_root}"
        )
    panel = experiment_manifest["frozen_retrospective_transfer_panel"]
    _validate_tasks(tasks_root, panel["protocol_ids"])
    tasks_root = tasks_root.expanduser().resolve()
    task_digest = task_bundle_sha256(tasks_root, panel["protocol_ids"])
    panel_without_digest = {
        "set_id": panel["set_id"],
        "classification": panel["classification"],
        "commitment_sha256": panel["commitment_sha256"],
        "protocol_count": len(panel["protocol_ids"]),
        "protocol_ids": list(panel["protocol_ids"]),
        "task_bundle_sha256": task_digest,
    }
    panel_record = {
        **panel_without_digest,
        "panel_digest": canonical_digest(panel_without_digest),
    }
    base_config_path = base_config_path.expanduser().resolve()
    base_config = json.loads(base_config_path.read_text(encoding="utf-8"))
    if not isinstance(base_config, Mapping):
        raise CapabilityImprovementError("base Harbor config must be an object")
    anchor = _validate_anchor_config(base_config)
    try:
        harbor_version = package_version("harbor")
    except PackageNotFoundError:
        harbor_version = "unknown"
    command_template = _harbor_command(
        config_path="{config_path}",
        anchor=anchor,
    )
    runner_without_digest = {
        "harness": "harbor",
        "harness_version": harbor_version,
        "environment": "docker",
        "agent": anchor["agent"],
        "model": anchor["model"],
        "agent_version": anchor["version"],
        "reasoning_effort": anchor["reasoning_effort"],
        "concurrency": anchor["concurrency"],
        "semantic_retries": 0,
        "base_config_sha256": sha256_file(base_config_path),
        "harbor_command_template": command_template,
    }
    runner = {
        **runner_without_digest,
        "configuration_digest": canonical_digest(runner_without_digest),
    }
    final_replay_authorization = _FinalReplayAuthorization(
        experiment_root=root,
        experiment_digest=experiment_manifest["experiment_digest"],
        experiment_manifest_sha256=sha256_file(
            root / "design" / "experiment_manifest.json"
        ),
        lock_digest=lock["lock_digest"],
        lock_sha256=sha256_file(final_lock_path),
        authorization_digest=authorization["authorization_digest"],
        authorization_sha256=sha256_file(transfer_panel_authorization_path),
    )
    output_root.mkdir(parents=True)
    labels = list(REPLAY_LABELS)
    random.Random(int(lock["lock_digest"], 16)).shuffle(labels)
    jobs: list[dict[str, Any]] = []
    resolved_jobs_dir = jobs_dir.expanduser().resolve()
    try:
        for order, label in enumerate(labels, start=1):
            integration_root = output_root / "integrations" / label
            integration = _prepare_capability_harbor_integration(
                pack_root=_require_replay_checkpoint_root(
                    pack_roots[label],
                    experiment_root=root,
                    label=label,
                ),
                tasks_root=tasks_root,
                protocol_ids=panel["protocol_ids"],
                output_root=integration_root,
                created_at=created_at,
                final_replay_authorization=final_replay_authorization,
            )
            if integration["task_bundle_sha256"] != task_digest:
                raise CapabilityImprovementError(
                    f"fixed-panel task digest differs for {label}"
                )
            config_path = output_root / "jobs" / f"{order:02d}-{label}.json"
            job_name = f"libgen-capability-fixed-panel-{label.lower()}"
            build_harbor_job_config(
                base_config_path=base_config_path,
                integration_root=integration_root,
                tasks_root=tasks_root,
                protocol_ids=panel["protocol_ids"],
                job_name=job_name,
                jobs_dir=resolved_jobs_dir,
                output_path=config_path,
            )
            jobs.append(
                {
                    "order": order,
                    "label": label,
                    "pack_digest": expected_digests[label],
                    "memory_digest": integration["exemplar_memory"]["memory_digest"],
                    "integration_digest": integration["integration_digest"],
                    "integration_manifest_sha256": sha256_file(
                        integration_root / "integration_manifest.json"
                    ),
                    "config_path": config_path.relative_to(output_root).as_posix(),
                    "config_sha256": sha256_file(config_path),
                    "job_name": job_name,
                    "expected_result_root": (resolved_jobs_dir / job_name).as_posix(),
                    "expected_trial_count": len(panel["protocol_ids"]),
                    "harbor_command": _harbor_command(
                        config_path=config_path.as_posix(),
                        anchor=anchor,
                    ),
                }
            )
        payload: dict[str, Any] = {
            "schema_version": FIXED_PANEL_REPLAY_SCHEMA_VERSION,
            "replay_id": (
                experiment_manifest["experiment_id"] + ":fixed-panel-learning-curve"
            ),
            "experiment_digest": experiment_manifest["experiment_digest"],
            "final_lock": {
                "digest": lock["lock_digest"],
                "sha256": sha256_file(final_lock_path),
            },
            "transfer_panel_authorization": {
                "digest": authorization["authorization_digest"],
                "sha256": sha256_file(transfer_panel_authorization_path),
            },
            "panel": panel_record,
            "runner": runner,
            "replay_labels": list(REPLAY_LABELS),
            "endpoint_labels": list(ENDPOINT_LABELS),
            "packs": packs,
            "jobs": jobs,
            "expected_trial_count": len(REPLAY_LABELS) * len(panel["protocol_ids"]),
            "checkpoint_modification_closed": True,
            "selection_policy": (
                "C25_is_the_predefined_endpoint_no_replay_based_checkpoint_selection"
            ),
            "development_diagnostics_policy": (
                "B4_and_B5_are_rolling_development_diagnostics_"
                "not_the_final_learning_curve"
            ),
            "created_at": normalized_timestamp(created_at),
        }
        replay = with_digest(payload, "replay_digest")
        validate_document(
            replay,
            improvement_schema_root() / "fixed_panel_replay.schema.json",
            label="fixed-panel capability replay",
        )
        write_json_atomic(output_root / "manifest.json", replay, mode=0o444)
        return replay
    except BaseException:
        shutil.rmtree(output_root, ignore_errors=True)
        raise


def _default_replay_pack_roots(
    experiment_root: Path,
    experiment_manifest: Mapping[str, Any],
) -> dict[str, Path]:
    del experiment_manifest
    return {label: experiment_root / "checkpoints" / label for label in REPLAY_LABELS}


def _require_replay_checkpoint_root(
    candidate: Path,
    *,
    experiment_root: Path,
    label: str,
) -> Path:
    unresolved = candidate.expanduser()
    root = unresolved.parent if unresolved.is_file() else unresolved
    resolved = root.resolve()
    expected = (experiment_root / "checkpoints" / label).resolve()
    if resolved != expected:
        raise CapabilityImprovementError(
            f"fixed-panel replay requires canonical frozen checkpoint {label}: "
            f"{expected}"
        )
    return resolved


def _harbor_command(
    *,
    config_path: str,
    anchor: Mapping[str, Any],
) -> list[str]:
    return [
        "harbor",
        "run",
        "--config",
        config_path,
        "--agent",
        anchor["agent"],
        "--model",
        anchor["model"],
        "--agent-kwarg",
        f"version={anchor['version']}",
        "--agent-kwarg",
        f"reasoning_effort={anchor['reasoning_effort']}",
        "--n-concurrent",
        str(anchor["concurrency"]),
        "--max-retries",
        "0",
        "--yes",
    ]


def build_prospective_round_plan(
    *,
    experiment_manifest: Mapping[str, Any],
    batch_id: str,
    checkpoint_pack_roots: Mapping[str, Path],
    tasks_root: Path,
    base_config_path: Path,
    output_root: Path,
    jobs_dir: Path,
    created_at: str,
) -> dict[str, Any]:
    """Plan C0 and the current cumulative pack without revealing truth."""

    assert_final_split_frozen(experiment_manifest)
    matches = [
        item for item in experiment_manifest["batches"] if item["batch_id"] == batch_id
    ]
    if len(matches) != 1 or matches[0]["phase"] != "prospective":
        raise CapabilityImprovementError(f"batch is not prospective: {batch_id}")
    batch = matches[0]
    prior_count = batch["checkpoint_size"] - 5
    current_label = checkpoint_before_batch(batch_id)
    if current_label != f"C{prior_count}":
        raise CapabilityImprovementError(
            "prospective checkpoint size differs from cumulative lineage"
        )
    labels = ("C0", current_label)
    if set(checkpoint_pack_roots) != set(labels):
        raise CapabilityImprovementError(
            "prospective round requires exactly " + ", ".join(labels)
        )
    from .workflow import validate_checkpoint_runtime

    expected_digests: dict[str, str] = {}
    for label in labels:
        checkpoint, _, pack = validate_checkpoint_runtime(checkpoint_pack_roots[label])
        if (
            checkpoint["checkpoint_id"] != label
            or checkpoint["experiment_digest"]
            != experiment_manifest["experiment_digest"]
        ):
            raise CapabilityImprovementError(
                f"prospective checkpoint has stale lineage: {label}"
            )
        expected_digests[label] = pack["pack_digest"]
    output_root = output_root.expanduser().resolve()
    if output_root.exists():
        raise CapabilityImprovementError(
            f"prospective round plan already exists: {output_root}"
        )
    output_root.mkdir(parents=True)
    jobs = []
    try:
        for order, label in enumerate(labels, start=1):
            _, _, pack = validate_checkpoint_runtime(checkpoint_pack_roots[label])
            if pack["pack_digest"] != expected_digests[label]:
                raise CapabilityImprovementError(f"prospective pack changed: {label}")
            integration_root = output_root / "integrations" / label
            integration = prepare_capability_harbor_integration(
                pack_root=checkpoint_pack_roots[label],
                tasks_root=tasks_root,
                protocol_ids=batch["protocol_ids"],
                output_root=integration_root,
                created_at=created_at,
            )
            config_path = output_root / "jobs" / f"{order:02d}-{label}.json"
            build_harbor_job_config(
                base_config_path=base_config_path,
                integration_root=integration_root,
                tasks_root=tasks_root,
                protocol_ids=batch["protocol_ids"],
                job_name=f"libgen-capability-{batch_id.lower()}-{label.lower()}",
                jobs_dir=jobs_dir,
                output_path=config_path,
            )
            jobs.append(
                {
                    "order": order,
                    "label": label,
                    "branch": ACTIVE_BRANCH,
                    "pack_digest": pack["pack_digest"],
                    "memory_digest": integration["exemplar_memory"]["memory_digest"],
                    "integration_digest": integration["integration_digest"],
                    "config_path": config_path.relative_to(output_root).as_posix(),
                    "config_sha256": sha256_file(config_path),
                    "expected_trial_count": 5,
                }
            )
        payload: dict[str, Any] = {
            "schema_version": PROSPECTIVE_ROUND_PLAN_SCHEMA_VERSION,
            "experiment_digest": experiment_manifest["experiment_digest"],
            "batch_id": batch_id,
            "phase": "prospective",
            "protocol_ids": list(batch["protocol_ids"]),
            "groundtruth_reveal_state": "concealed_until_both_jobs_terminal",
            "jobs": jobs,
            "expected_new_trial_count": 10,
            "created_at": created_at,
        }
        plan = with_digest(payload, "plan_digest")
        write_json_atomic(output_root / "plan.json", plan, mode=0o444)
        return plan
    except BaseException:
        shutil.rmtree(output_root, ignore_errors=True)
        raise


def _validate_tasks(tasks_root: Path, protocol_ids: Sequence[str]) -> None:
    unresolved_root = tasks_root.expanduser()
    if unresolved_root.is_symlink() or not unresolved_root.is_dir():
        raise CapabilityImprovementError(
            f"Harbor task root is missing or symlinked: {unresolved_root}"
        )
    root = unresolved_root.resolve()
    if len(protocol_ids) != len(set(protocol_ids)):
        raise CapabilityImprovementError("Harbor protocol IDs must be unique")
    for protocol_id in protocol_ids:
        if not isinstance(protocol_id, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_]*", protocol_id
        ):
            raise CapabilityImprovementError(
                f"Harbor protocol ID is not a stable task-directory name: {protocol_id!r}"
            )
        task = root / protocol_id
        task_file = task / "task.toml"
        if (
            task.is_symlink()
            or not task.is_dir()
            or task.resolve().parent != root
            or task_file.is_symlink()
            or not task_file.is_file()
        ):
            raise CapabilityImprovementError(f"Harbor task is missing: {protocol_id}")
        text = task_file.read_text(encoding="utf-8")
        if 'environment_mode = "separate"' not in text:
            raise CapabilityImprovementError(
                f"task verifier must use a separate environment: {protocol_id}"
            )


def _resolve_capability_source(
    source_root: Path,
) -> tuple[
    Path,
    dict[str, Any],
    dict[str, Any] | None,
    Path | None,
    dict[str, Any] | None,
]:
    """Resolve and validate a bare pack or either portable-checkpoint form."""

    unresolved = source_root.expanduser()
    if unresolved.is_symlink():
        raise CapabilityImprovementError(
            f"capability source may not be a symlink: {unresolved}"
        )
    if unresolved.is_file():
        if unresolved.name != "checkpoint.json":
            raise CapabilityImprovementError(
                "capability source file must be the exact checkpoint.json "
                f"inside a frozen checkpoint: {unresolved}"
            )
        return _resolve_frozen_checkpoint(unresolved.parent)
    root = unresolved.resolve()
    if (root / "manifest.json").is_file():
        return root, validate_capability_pack(root), None, None, None
    if (root / "checkpoint.json").is_file():
        return _resolve_frozen_checkpoint(root)
    raise CapabilityImprovementError(
        "capability source must be a bare pack directory, a frozen checkpoint "
        f"directory, or its exact checkpoint.json file: {root}"
    )


def _resolve_frozen_checkpoint(
    checkpoint_root: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    """Resolve a checkpoint only after validating its complete runtime contract."""

    from .workflow import (
        checkpoint_exemplar_max_results,
        validate_checkpoint_runtime,
    )

    checkpoint, runtime, pack = validate_checkpoint_runtime(checkpoint_root)
    root = checkpoint_root.expanduser().resolve()
    memory_root = root / "memory"
    memory = exemplar_memory_record(memory_root)
    if memory != checkpoint["exemplar_memory"] or memory != runtime["exemplar_memory"]:
        raise CapabilityImprovementError(
            "frozen checkpoint runtime has a stale exemplar-memory binding"
        )
    return (
        root / "pack",
        pack,
        {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_digest": checkpoint["checkpoint_digest"],
            "checkpoint_sha256": sha256_file(root / "checkpoint.json"),
            "runtime_digest": runtime["runtime_digest"],
            "runtime_sha256": sha256_file(root / "runtime.json"),
            "exemplar_max_results": checkpoint_exemplar_max_results(runtime),
            "exemplar_memory": memory,
        },
        memory_root,
        memory,
    )


def _extra_instruction(
    *,
    pack_digest: str,
    checkpoint_runtime: Mapping[str, Any] | None,
    exemplar_memory: Mapping[str, Any] | None,
) -> str:
    checkpoint_text = (
        "This exposure was resolved from frozen checkpoint "
        f"`{checkpoint_runtime['checkpoint_id']}` (checkpoint digest "
        f"`{checkpoint_runtime['checkpoint_digest']}`, checkpoint SHA-256 "
        f"`{checkpoint_runtime['checkpoint_sha256']}`) with runtime digest "
        f"`{checkpoint_runtime['runtime_digest']}` and runtime SHA-256 "
        f"`{checkpoint_runtime['runtime_sha256']}`.\n\n"
        if checkpoint_runtime is not None
        else "This exposure was resolved from a bare capability pack.\n\n"
    )
    memory_text = ""
    if exemplar_memory is not None:
        if checkpoint_runtime is None:
            raise CapabilityImprovementError(
                "exemplar memory requires a frozen checkpoint runtime"
            )
        exemplar_max_results = checkpoint_runtime["exemplar_max_results"]
        exemplar_count_word = {1: "one", 2: "two", 3: "three"}[
            exemplar_max_results
        ]
        donor_noun = "subgraph" if exemplar_max_results == 1 else "subgraphs"
        memory_text = f"""
The checkpoint's cumulative prediction-shaped exemplar memory is mounted
read-only at `{EXEMPLAR_MEMORY_MOUNT_TARGET}` with digest
`{exemplar_memory["memory_digest"]}` and contains
`{exemplar_memory["exemplar_count"]}` pseudonymous donor exemplars. Raw GT and
audit records are not exposed. Approved training GT is projected into
prediction-shaped exemplars and retained as cumulative memory.

Frozen memory binding: manifest SHA-256
`{exemplar_memory["memory_manifest_sha256"]}`, catalog digest
`{exemplar_memory["catalog_digest"]}`, catalog SHA-256
`{exemplar_memory["catalog_sha256"]}`, private-map public commitment
`{exemplar_memory["identity_map_commitment"]}`. The private map itself is not
mounted or otherwise agent-visible.

Do not read `catalog.json` or `exemplars/**` directly, and do not enumerate or
load all donor files into context. The query tool is the only canonical donor
access interface. Build an exemplar query from target-source-supported features
recorded in the current work record, then
run the declared query interface at
`{EXEMPLAR_MEMORY_MOUNT_TARGET}/runtime/tools/query_exemplars.py`. It may return
at most {exemplar_count_word} donor {donor_noun}. Write its non-scored usage record to
`{EXEMPLAR_DIAGNOSTIC_ARTIFACTS[0]}`. A zero-match result is valid and must not
be repaired by browsing the catalog.

After the final source-backed work-record edit and after compiling T2/T3, run
`{EXEMPLAR_MEMORY_MOUNT_TARGET}/runtime/tools/guard_target_evidence.py` using
the full frozen catalog at `{EXEMPLAR_MEMORY_MOUNT_TARGET}/catalog.json`, the
exact retrieval and usage records, and write the report to
`{EXEMPLAR_DIAGNOSTIC_ARTIFACTS[1]}`. This guard scans the full catalog even
when retrieval returned no match. Exit 0 is required before finalization; exit
1 (unsupported memory overlap) or exit 2 (invalid or stale inputs) blocks
finalization. Revise the work record from target evidence and rerun compilation,
the unified pack audit, and the guard until all pass. Never treat a donor
sequence, operation, state, modification, or branch as target evidence.
"""
    return f"""# Frozen LibGen capability pack

The read-only model-neutral capability pack is mounted at
`{CAPABILITY_MOUNT_TARGET}` with digest `{pack_digest}`. {checkpoint_text}Read
`PLAYBOOK.md` and follow its required working order. Read
`tools/control_index.json` as the machine-readable interface; create one work
record using `schemas/work_record.schema.json`, compile T2 and T3 together with
the declared compiler command, and run the declared unified audit command.
Honor its JSON output and exit-code contract. If `adapters/` contains an
adapter for this harness, treat it as optional framework-specific convenience.
{memory_text}

The target protocol's own supplied sources are authoritative. The pack is not
target evidence. Do not import a sequence, operation, state, or topology unless
the target sources support it. Do not browse the web, search for benchmark
answers, or inspect any path outside the task workspace and this read-only pack.
"""
