from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import sha256_file

from .artifacts import CapabilityImprovementError
from .experiment import GROUNDTRUTH_FILENAMES, build_batch_packet


RUN_ARTIFACTS = (
    ("result.json", "trial_result"),
    ("agent/trajectory.json", "trajectory"),
    ("artifacts/logs/artifacts/t2_prediction.json", "prediction"),
    ("artifacts/logs/artifacts/t3_prediction.json", "prediction"),
    ("verifier/reward.json", "verifier_reward"),
    ("verifier/details.json", "verifier_details"),
    ("verifier/error_analysis.json", "verifier_error_analysis"),
)


def build_batch_packet_from_frozen_runs(
    *,
    experiment_manifest: Mapping[str, Any],
    branch: str,
    batch_id: str,
    parent_pack_digest: str,
    run_root: Path,
    source_root: Path,
    groundtruth_root: Path,
    c0_run_root: Path | None = None,
    transfer_access_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Collect one revealed packet without asking an operator to hand-list files."""

    batch = _batch(experiment_manifest, batch_id)
    protocols = list(batch["protocol_ids"])
    trials = _trial_map(run_root, protocols)
    if batch["phase"] == "retrospective":
        terminality = _terminality(trials, branch=branch)
        if c0_run_root is not None:
            raise CapabilityImprovementError(
                "retrospective packet does not accept a C0 run"
            )
    else:
        if c0_run_root is None:
            raise CapabilityImprovementError(
                "prospective reveal requires a frozen C0 run"
            )
        c0_trials = _trial_map(c0_run_root, protocols)
        terminality = _terminality(c0_trials, branch="C0") + _terminality(
            trials, branch=branch
        )
    artifacts = _source_artifacts(source_root, protocols)
    artifacts.extend(_run_artifacts(trials))
    if batch["phase"] == "prospective":
        artifacts.extend(_c0_comparison_artifacts(c0_trials))
    artifacts.extend(_groundtruth_artifacts(groundtruth_root, protocols))
    return build_batch_packet(
        experiment_manifest=experiment_manifest,
        branch=branch,
        batch_id=batch_id,
        parent_pack_digest=parent_pack_digest,
        reveal_state="revealed",
        artifacts=artifacts,
        trial_terminality=terminality,
        transfer_access_policy=transfer_access_policy,
    )


def _trial_map(root: Path, protocol_ids: Sequence[str]) -> dict[str, Path]:
    resolved = root.expanduser().resolve()
    if (
        not (resolved / "config.json").is_file()
        or not (resolved / "result.json").is_file()
    ):
        raise CapabilityImprovementError(
            f"frozen Harbor run root is incomplete: {resolved}"
        )
    expected = set(protocol_ids)
    result: dict[str, Path] = {}
    for path in sorted(resolved.glob("*/result.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CapabilityImprovementError(
                f"cannot read trial result {path}: {error}"
            ) from error
        task_path = (document.get("task_id") or {}).get("path")
        protocol_id = Path(task_path).name if isinstance(task_path, str) else None
        if protocol_id not in expected:
            continue
        if protocol_id in result:
            raise CapabilityImprovementError(
                f"run has multiple attempts for {protocol_id}; freeze one selected attempt first"
            )
        result[protocol_id] = path.parent
    if set(result) != expected:
        raise CapabilityImprovementError(
            "run membership differs from batch; missing="
            + ",".join(sorted(expected - set(result)))
        )
    return result


def _source_artifacts(root: Path, protocol_ids: Sequence[str]) -> list[dict[str, Any]]:
    resolved = root.expanduser().resolve()
    result: list[dict[str, Any]] = []
    for protocol_id in protocol_ids:
        protocol_root = resolved / protocol_id
        files = [
            path
            for path in sorted(protocol_root.rglob("*"))
            if path.is_file()
            and not any(
                part.startswith(".") for part in path.relative_to(protocol_root).parts
            )
        ]
        if not files:
            raise CapabilityImprovementError(f"source bundle is empty: {protocol_id}")
        for path in files:
            if path.is_symlink():
                raise CapabilityImprovementError(
                    f"source bundle contains a symlink: {path}"
                )
            result.append(_artifact(protocol_id, "target_source", path))
    return result


def _run_artifacts(trials: Mapping[str, Path]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for protocol_id in sorted(trials):
        root = trials[protocol_id]
        for relative, role in RUN_ARTIFACTS:
            path = root / relative
            if not path.is_file():
                raise CapabilityImprovementError(
                    f"frozen run artifact is missing for {protocol_id}: {relative}"
                )
            result.append(_artifact(protocol_id, role, path))
    return result


def _c0_comparison_artifacts(
    trials: Mapping[str, Path],
) -> list[dict[str, Any]]:
    """Expose only pinned C0 verifier summaries for paired diagnostics."""

    result: list[dict[str, Any]] = []
    for protocol_id in sorted(trials):
        root = trials[protocol_id]
        for relative, role in (
            ("verifier/reward.json", "c0_verifier_reward"),
            ("verifier/error_analysis.json", "c0_verifier_error_analysis"),
        ):
            path = root / relative
            if not path.is_file():
                raise CapabilityImprovementError(
                    f"frozen C0 summary is missing for {protocol_id}: {relative}"
                )
            result.append(_artifact(protocol_id, role, path))
    return result


def _groundtruth_artifacts(
    root: Path,
    protocol_ids: Sequence[str],
) -> list[dict[str, Any]]:
    resolved = root.expanduser().resolve()
    result = []
    for protocol_id in protocol_ids:
        for filename in GROUNDTRUTH_FILENAMES:
            path = resolved / protocol_id / filename
            if not path.is_file():
                raise CapabilityImprovementError(
                    f"ground truth is missing for packet: {protocol_id}/{filename}"
                )
            result.append(_artifact(protocol_id, "approved_groundtruth", path))
    return result


def _terminality(
    trials: Mapping[str, Path],
    *,
    branch: str,
) -> list[dict[str, Any]]:
    result = []
    for protocol_id in sorted(trials):
        path = trials[protocol_id] / "result.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        rewards = (document.get("verifier_result") or {}).get("rewards") or {}
        exception_type = (document.get("exception_info") or {}).get("exception_type")
        if rewards:
            status = "completed"
        elif exception_type == "AgentTimeoutError":
            status = "timed_out"
        elif exception_type:
            status = "agent_failed"
        else:
            status = "invalid_output"
        result.append(
            {
                "protocol_id": protocol_id,
                "branch": branch,
                "status": status,
                "attempt_count": 1,
                "frozen_output_sha256": sha256_file(path),
            }
        )
    return result


def _artifact(protocol_id: str, role: str, path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "protocol_id": protocol_id,
        "role": role,
        "path": resolved.as_posix(),
        "sha256": sha256_file(resolved),
        "visibility": "agent_after_reveal",
    }


def _batch(experiment: Mapping[str, Any], batch_id: str) -> Mapping[str, Any]:
    values = [item for item in experiment["batches"] if item["batch_id"] == batch_id]
    if len(values) != 1:
        raise CapabilityImprovementError(f"unknown batch: {batch_id}")
    return values[0]
