from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from libstruct_bench.audit.artifacts import sha256_file, validate_document

from .artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    improvement_schema_root,
    load_and_validate,
    normalized_timestamp,
    with_digest,
)
from .experiment import (
    ENDPOINT_LABELS,
    REPLAY_LABELS,
    validate_final_lock,
    validate_transfer_panel_authorization,
)
from .split_design import FINAL_TRANSFER_PANEL


REPORT_SCHEMA_VERSION = "libstruct.libgen_capability_evaluation_report.v1"
REPORT_METRICS = (
    "reward",
    "t2_exact_required_family_recall",
    "t2_required_family_f1",
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)
BOOTSTRAP_METRICS = (
    "t3_molecular_transition_f1",
    "t3_state_f1",
    "t3_typed_edge_f1",
)
BOOTSTRAP_CONFIDENCE_LEVEL = 0.95
BOOTSTRAP_RESAMPLES = 10_000


def build_final_evaluation_report(
    *,
    replay_manifest_path: Path,
    result_roots: Mapping[str, Path] | None,
    created_at: str,
) -> dict[str, Any]:
    """Summarize the single post-lock replay without selecting a checkpoint."""

    manifest_path = replay_manifest_path.expanduser().resolve()
    replay = _load_replay_manifest(manifest_path)
    jobs = {item["label"]: item for item in replay["jobs"]}
    roots = (
        {label: Path(jobs[label]["expected_result_root"]) for label in REPLAY_LABELS}
        if result_roots is None
        else {label: Path(path) for label, path in result_roots.items()}
    )
    if set(roots) != set(REPLAY_LABELS):
        raise CapabilityImprovementError(
            "fixed-panel report requires exactly these Harbor result roots: "
            + ", ".join(REPLAY_LABELS)
        )
    conditions = [
        _condition_result(
            label=label,
            result_root=roots[label],
            planned=jobs[label],
            replay_root=manifest_path.parent,
            protocol_ids=replay["panel"]["protocol_ids"],
            runner=replay["runner"],
        )
        for label in REPLAY_LABELS
    ]
    by_label = {item["label"]: item for item in conditions}
    paired_changes = [
        _paired_change(
            replay_digest=replay["replay_digest"],
            label=label,
            metric=metric,
            baseline=by_label["C0"],
            current=by_label[label],
        )
        for label in REPLAY_LABELS
        if label != "C0"
        for metric in REPORT_METRICS
    ]
    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "experiment_digest": replay["experiment_digest"],
        "replay_digest": replay["replay_digest"],
        "replay_manifest_sha256": sha256_file(manifest_path),
        "final_lock_digest": replay["final_lock"]["digest"],
        "transfer_panel_authorization_digest": replay["transfer_panel_authorization"][
            "digest"
        ],
        "panel_digest": replay["panel"]["panel_digest"],
        "panel_classification": replay["panel"]["classification"],
        "protocol_ids": list(replay["panel"]["protocol_ids"]),
        "metrics": list(REPORT_METRICS),
        "analysis_policy": {
            "paired_baseline": "C0",
            "bootstrap_method": "paired_protocol_resampling_with_replacement",
            "bootstrap_confidence_level": BOOTSTRAP_CONFIDENCE_LEVEL,
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "checkpoint_selection": "prohibited_from_replay_performance",
            "predefined_endpoints": list(ENDPOINT_LABELS),
            "rolling_development_diagnostics": (
                "B4_and_B5_not_the_final_learning_curve"
            ),
            "unscored_failure_policy": "missing_not_zero_report_separately",
        },
        "conditions": conditions,
        "paired_changes_from_c0": paired_changes,
        "created_at": normalized_timestamp(created_at),
    }
    report = with_digest(payload, "report_digest")
    validate_document(
        report,
        improvement_schema_root() / "evaluation_report.schema.json",
        label="fixed-panel capability replay report",
    )
    return report


def _load_replay_manifest(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if (
        resolved.name != "manifest.json"
        or resolved.parent.name != "fixed-panel-replay"
        or resolved.parent.parent.name != "final"
    ):
        raise CapabilityImprovementError(
            "final report requires the canonical fixed-panel replay manifest"
        )
    experiment_root = resolved.parents[2]
    experiment_path = experiment_root / "design" / "experiment_manifest.json"
    experiment = _load_json_object(experiment_path, "capability experiment manifest")
    from .artifacts import validate_digest

    validate_digest(experiment, "experiment_digest")
    final_lock_path = experiment_root / "design" / "final_lock.json"
    final_lock = validate_final_lock(
        final_lock_path,
        experiment_root=experiment_root,
        experiment_manifest=experiment,
    )
    authorization_path = (
        experiment_root / "design" / "transfer_panel_authorization.json"
    )
    authorization = validate_transfer_panel_authorization(
        authorization_path,
        experiment_root=experiment_root,
        experiment_manifest=experiment,
        final_lock=final_lock,
    )
    replay = load_and_validate(
        resolved,
        schema_filename="fixed_panel_replay.schema.json",
        digest_field="replay_digest",
        label="fixed-panel capability replay",
    )
    if replay["expected_trial_count"] != 60:
        raise CapabilityImprovementError("fixed-panel replay must contain 60 trials")
    if replay["experiment_digest"] != experiment["experiment_digest"]:
        raise CapabilityImprovementError(
            "fixed-panel replay belongs to another experiment"
        )
    expected_lock = {
        "digest": final_lock["lock_digest"],
        "sha256": sha256_file(final_lock_path),
    }
    expected_authorization = {
        "digest": authorization["authorization_digest"],
        "sha256": sha256_file(authorization_path),
    }
    if (
        replay["final_lock"] != expected_lock
        or replay["transfer_panel_authorization"] != expected_authorization
    ):
        raise CapabilityImprovementError(
            "fixed-panel replay is detached from canonical lock authorization"
        )
    panel = replay["panel"]
    if (
        tuple(panel["protocol_ids"]) != FINAL_TRANSFER_PANEL
        or panel["commitment_sha256"]
        != experiment["frozen_retrospective_transfer_panel"]["commitment_sha256"]
    ):
        raise CapabilityImprovementError(
            "fixed-panel replay changes the frozen final-test panel"
        )
    panel_payload = {
        key: value for key, value in panel.items() if key != "panel_digest"
    }
    if canonical_digest(panel_payload) != panel["panel_digest"]:
        raise CapabilityImprovementError("fixed-panel replay panel digest is stale")
    runner = replay["runner"]
    runner_payload = {
        key: value for key, value in runner.items() if key != "configuration_digest"
    }
    if canonical_digest(runner_payload) != runner["configuration_digest"]:
        raise CapabilityImprovementError(
            "fixed-panel replay runner configuration digest is stale"
        )
    if replay["replay_labels"] != list(REPLAY_LABELS):
        raise CapabilityImprovementError("fixed-panel replay labels are incomplete")
    if replay["endpoint_labels"] != list(ENDPOINT_LABELS):
        raise CapabilityImprovementError("fixed-panel endpoint policy changed")
    for collection in ("packs", "jobs"):
        if {item["label"] for item in replay[collection]} != set(REPLAY_LABELS):
            raise CapabilityImprovementError(
                f"fixed-panel replay {collection} are incomplete"
            )
    pack_digests = {item["label"]: item["pack_digest"] for item in replay["packs"]}
    if any(
        item["pack_digest"] != pack_digests[item["label"]] for item in replay["jobs"]
    ) or any(
        item["checkpoint_experiment_digest"] != replay["experiment_digest"]
        for item in replay["packs"]
    ):
        raise CapabilityImprovementError(
            "fixed-panel replay jobs or packs have stale lineage"
        )
    return replay


def _condition_result(
    *,
    label: str,
    result_root: Path,
    planned: Mapping[str, Any],
    replay_root: Path,
    protocol_ids: Sequence[str],
    runner: Mapping[str, Any],
) -> dict[str, Any]:
    root = result_root.expanduser().resolve()
    config_path = root / "config.json"
    result_path = root / "result.json"
    if not config_path.is_file() or not result_path.is_file():
        raise CapabilityImprovementError(
            f"Harbor result root is incomplete for {label}: {root}"
        )
    planned_config = replay_root / planned["config_path"]
    if sha256_file(planned_config) != planned["config_sha256"]:
        raise CapabilityImprovementError(f"planned Harbor config changed for {label}")
    actual_config = _load_json_object(config_path, f"Harbor config for {label}")
    expected_config = _load_json_object(
        planned_config,
        f"planned Harbor config for {label}",
    )
    datasets = expected_config.get("datasets")
    if (
        not isinstance(datasets, list)
        or len(datasets) != 1
        or not isinstance(datasets[0], Mapping)
        or datasets[0].get("task_names") != list(protocol_ids)
    ):
        raise CapabilityImprovementError(
            f"planned Harbor protocols differ from the fixed panel for {label}"
        )
    for key in (
        "job_name",
        "jobs_dir",
        "agents",
        "datasets",
        "extra_instruction_paths",
    ):
        if actual_config.get(key) != expected_config.get(key):
            raise CapabilityImprovementError(
                f"executed Harbor config differs for {label}: {key}"
            )
    _validate_persisted_harbor_environment(
        actual=actual_config.get("environment"),
        expected=expected_config.get("environment"),
        runner=runner,
        label=label,
    )
    expected_concurrency = expected_config.get("n_concurrent_trials")
    expected_retry = expected_config.get("retry")
    if (
        expected_concurrency != runner.get("concurrency")
        or not isinstance(expected_retry, Mapping)
        or expected_retry.get("max_retries") != runner.get("semantic_retries")
    ):
        raise CapabilityImprovementError(
            f"planned Harbor scheduling differs from the replay runner for {label}"
        )
    # Harbor's persisted result config omits scheduling-only CLI fields. If a
    # Harbor version does persist them, still reject any conflicting value.
    for key in ("n_concurrent_trials", "retry"):
        if key in actual_config and actual_config[key] != expected_config.get(key):
            raise CapabilityImprovementError(
                f"executed Harbor config differs for {label}: {key}"
            )
    job_result = _load_json_object(result_path, f"Harbor result for {label}")
    if job_result.get("n_total_trials") != 10:
        raise CapabilityImprovementError(
            f"Harbor job did not plan ten trials for {label}"
        )
    protocol_results: dict[str, dict[str, Any]] = {}
    for trial_path in sorted(root.glob("*/result.json")):
        trial = _load_json_object(trial_path, f"Harbor trial for {label}")
        task_path = (trial.get("task_id") or {}).get("path")
        protocol_id = Path(task_path).name if isinstance(task_path, str) else None
        if protocol_id is None or protocol_id in protocol_results:
            raise CapabilityImprovementError(
                f"invalid or duplicate trial in {label}: {trial_path}"
            )
        rewards = (trial.get("verifier_result") or {}).get("rewards") or {}
        metrics = {
            metric: _validated_metric_score(
                rewards.get(metric),
                label=label,
                metric=metric,
            )
            for metric in REPORT_METRICS
        }
        exception = trial.get("exception_info") or {}
        exception_type = exception.get("exception_type")
        if any(value is not None for value in metrics.values()):
            status = "scored_with_exception" if exception_type else "scored"
        else:
            status = "unscored_failure"
        protocol_results[protocol_id] = {
            "protocol_id": protocol_id,
            "trial_name": trial["trial_name"],
            "result_sha256": sha256_file(trial_path),
            "status": status,
            "exception_type": exception_type,
            "metrics": metrics,
        }
    expected_protocols = set(protocol_ids)
    if set(protocol_results) != expected_protocols or len(protocol_results) != 10:
        raise CapabilityImprovementError(f"Harbor trial membership differs for {label}")
    rows = [protocol_results[item] for item in sorted(protocol_results)]
    macro_means = {
        metric: _mean_or_none(
            [
                row["metrics"][metric]
                for row in rows
                if row["metrics"][metric] is not None
            ]
        )
        for metric in REPORT_METRICS
    }
    metric_counts = {
        metric: sum(row["metrics"][metric] is not None for row in rows)
        for metric in REPORT_METRICS
    }
    exception_counts = Counter(
        row["exception_type"] for row in rows if row["exception_type"] is not None
    )
    return {
        "label": label,
        "pack_digest": planned["pack_digest"],
        "execution_mode": "new_harbor_run",
        "result_root": root.as_posix(),
        "result_sha256": sha256_file(result_path),
        "planned_trials": 10,
        "scored_trials": sum(row["status"] != "unscored_failure" for row in rows),
        "unscored_trials": sum(row["status"] == "unscored_failure" for row in rows),
        "scored_with_exception_trials": sum(
            row["status"] == "scored_with_exception" for row in rows
        ),
        "exception_counts": dict(sorted(exception_counts.items())),
        "metric_counts": metric_counts,
        "macro_means": macro_means,
        "protocol_results": rows,
    }


def _validate_persisted_harbor_environment(
    *,
    actual: Any,
    expected: Any,
    runner: Mapping[str, Any],
    label: str,
) -> None:
    """Compare a planned environment with Harbor's normalized saved form."""

    if not isinstance(actual, Mapping) or not isinstance(expected, Mapping):
        raise CapabilityImprovementError(
            f"executed Harbor config differs for {label}: environment"
        )
    actual_type = actual.get("type", "docker")
    expected_type = expected.get("type", "docker")
    if (
        actual_type != expected_type
        or actual_type != runner.get("environment")
        or {key: value for key, value in actual.items() if key != "type"}
        != {key: value for key, value in expected.items() if key != "type"}
    ):
        raise CapabilityImprovementError(
            f"executed Harbor config differs for {label}: environment"
        )


def _validated_metric_score(value: Any, *, label: str, metric: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapabilityImprovementError(
            f"Harbor result has a nonnumeric {metric} score for {label}"
        )
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise CapabilityImprovementError(
            f"Harbor result has an out-of-range {metric} score for {label}"
        )
    return score


def _paired_change(
    *,
    replay_digest: str,
    label: str,
    metric: str,
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_values = _metric_by_protocol(baseline, metric)
    current_values = _metric_by_protocol(current, metric)
    shared = sorted(set(baseline_values) & set(current_values))
    changes = {
        protocol_id: current_values[protocol_id] - baseline_values[protocol_id]
        for protocol_id in shared
    }
    seed_sha256 = hashlib.sha256(
        f"{replay_digest}\0{label}\0{metric}".encode("utf-8")
    ).hexdigest()
    result: dict[str, Any] = {
        "label": label,
        "baseline_label": "C0",
        "metric": metric,
        "paired_protocol_count": len(shared),
        "mean_change": _mean_or_none(list(changes.values())),
        "per_protocol_changes": changes,
    }
    if metric in BOOTSTRAP_METRICS:
        lower, upper = _paired_bootstrap_interval(
            list(changes.values()),
            seed_sha256=seed_sha256,
        )
        result["bootstrap_interval"] = {
            "method": "paired_protocol_resampling_with_replacement",
            "confidence_level": BOOTSTRAP_CONFIDENCE_LEVEL,
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed_sha256": seed_sha256,
            "lower": lower,
            "upper": upper,
        }
    return result


def _paired_bootstrap_interval(
    changes: Sequence[float],
    *,
    seed_sha256: str,
) -> tuple[float | None, float | None]:
    if not changes:
        return None, None
    generator = random.Random(int(seed_sha256, 16))
    count = len(changes)
    means = sorted(
        statistics.fmean(generator.choice(changes) for _ in range(count))
        for _ in range(BOOTSTRAP_RESAMPLES)
    )
    tail = (1.0 - BOOTSTRAP_CONFIDENCE_LEVEL) / 2.0
    return _quantile(means, tail), _quantile(means, 1.0 - tail)


def _quantile(values: Sequence[float], probability: float) -> float:
    position = (len(values) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return values[lower_index]
    weight = position - lower_index
    return values[lower_index] * (1.0 - weight) + values[upper_index] * weight


def _metric_by_protocol(
    condition: Mapping[str, Any],
    metric: str,
) -> dict[str, float]:
    return {
        row["protocol_id"]: row["metrics"][metric]
        for row in condition["protocol_results"]
        if row["metrics"][metric] is not None
    }


def _mean_or_none(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapabilityImprovementError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise CapabilityImprovementError(f"{label} must be a JSON object")
    return value
