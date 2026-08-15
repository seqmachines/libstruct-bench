from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from libstruct_bench.libgen.telemetry import trial_telemetry


PLOT_COST_METRIC = "normalized_api_cost_usd"
PLOT_PERFORMANCE_METRIC = "t3_molecular_transition_f1"
PLOT_RUNTIME_METRIC = "agent_duration_seconds"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create telemetry-complete Libgen trial rows and effect summaries."
    )
    parser.add_argument("--runs-root", required=True)
    parser.add_argument("--experiment-lock", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--pricing-snapshot",
        default="benchmarks/libgen/pricing-2026-08-15.json",
        help="fallback for legacy locks that do not embed their frozen snapshot",
    )
    args = parser.parse_args(argv)

    lock = _read_required_json(Path(args.experiment_lock))
    pricing = lock.get("pricing_snapshot")
    if not isinstance(pricing, dict):
        pricing = _read_required_json(Path(args.pricing_snapshot))
    cells = {
        f"libgen-{lock['mode']}-{item['model_key']}-{item['harness_key']}": item
        for item in lock["cells"]
    }
    rows: list[dict[str, Any]] = []
    for job_name, cell in cells.items():
        job_dir = Path(args.runs_root) / job_name
        if not job_dir.is_dir():
            continue
        job_config = _read_json(job_dir / "config.json") or {}
        configured_max_retries = (
            (job_config.get("retry") or {}).get("max_retries")
            if isinstance(job_config, dict)
            else None
        )
        if configured_max_retries is None:
            configured_max_retries = (lock.get("telemetry_policy") or {}).get(
                "automatic_retries"
            )
        records = _job_records(job_dir)
        by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            by_protocol[record["protocol_id"]].append(record)
        for protocol_id, protocol_records in sorted(by_protocol.items()):
            protocol_records.sort(key=lambda item: _started_at(item["result"]))
            current_records = [item for item in protocol_records if item["is_current"]]
            current_attempt = {
                id(item): index
                for index, item in enumerate(
                    sorted(
                        current_records, key=lambda item: _started_at(item["result"])
                    ),
                    start=1,
                )
            }
            resume_invocations = sorted(
                {
                    item["resume_invocation"]
                    for item in protocol_records
                    if item["resume_invocation"] is not None
                }
            )
            resume_index = {
                name: index for index, name in enumerate(resume_invocations, start=1)
            }
            for execution_index, record in enumerate(protocol_records, start=1):
                result = record["result"]
                trial_dir = record["path"].parent
                rewards = _rewards(trial_dir, result)
                details = _read_json(trial_dir / "verifier" / "details.json") or {}
                prediction_valid = _prediction_valid(details, rewards)
                rewards.pop("prediction_valid", None)
                verifier_completed = bool(rewards) and isinstance(
                    rewards.get("reward"), (int, float)
                )
                exception_type = (result.get("exception_info") or {}).get(
                    "exception_type"
                )
                is_current = record["is_current"]
                row: dict[str, Any] = {
                    "design": cell["design"],
                    "native_pairing": cell.get(
                        "native_pairing", cell["design"] == "native_extension"
                    ),
                    "model": cell["model_key"],
                    "model_id": cell["model_id"],
                    "harness": cell["harness_key"],
                    "harbor_agent": cell.get("harbor_agent"),
                    "harness_version": cell["harness_version"],
                    "protocol_id": protocol_id,
                    "attempt": current_attempt.get(id(record)),
                    "execution_index": execution_index,
                    "is_current_execution": is_current,
                    "superseded_by_resume": not is_current,
                    "resume_count": len(resume_invocations),
                    "resume_index": (
                        resume_index.get(record["resume_invocation"])
                        if record["resume_invocation"] is not None
                        else len(resume_invocations)
                    ),
                    "retry_count": 0 if configured_max_retries == 0 else None,
                    "configured_max_retries": configured_max_retries,
                    "status": _status(exception_type, verifier_completed),
                    "exception_type": exception_type,
                    "prediction_valid": prediction_valid,
                    "verifier_completed": verifier_completed,
                    # A timeout may still leave a valid, scored prediction.
                    "valid_completion": _valid_completion(
                        verifier_completed, prediction_valid
                    ),
                    "trial_name": result.get("trial_name"),
                    "result_path": str(record["path"]),
                    "duration_seconds": _duration(
                        result.get("started_at"), result.get("finished_at")
                    ),
                    "agent_duration_seconds": _phase_duration(
                        result.get("agent_execution")
                    ),
                    "verifier_duration_seconds": _phase_duration(
                        result.get("verifier")
                    ),
                    "pricing_snapshot_sha256": lock.get("pricing_snapshot_sha256"),
                }
                row.update(trial_telemetry(trial_dir, result, cell, pricing))
                row.update(rewards)
                missing = _missing_telemetry(row)
                row["telemetry_missing_fields_json"] = _compact_json(missing)
                rows.append(row)

    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "trials.csv", rows)
    summary = _summary(rows, lock)
    _write_json(output / "summary.json", summary)
    audit = _telemetry_audit(rows, lock)
    _write_json(output / "telemetry_audit.json", audit)
    _write_csv(output / "telemetry_missing.csv", audit["rows_with_missing_fields"])
    observed_current = sum(row["is_current_execution"] for row in rows)
    print(
        f"summarized {observed_current}/{lock['expected_trial_count']} current trials "
        f"({len(rows)} preserved executions)"
    )
    return 0 if observed_current == lock["expected_trial_count"] else 1


def _job_records(job_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for result_path in sorted(job_dir.glob("*/result.json")):
        result = _read_json(result_path)
        if isinstance(result, dict):
            records.append(
                {
                    "path": result_path,
                    "result": result,
                    "protocol_id": _protocol_id(result, result_path.parent.name),
                    "is_current": True,
                    "resume_invocation": None,
                }
            )
    snapshot_pattern = ".libgen_telemetry/resume_snapshots/*/*/result.json"
    for result_path in sorted(job_dir.glob(snapshot_pattern)):
        result = _read_json(result_path)
        if isinstance(result, dict):
            records.append(
                {
                    "path": result_path,
                    "result": result,
                    "protocol_id": _protocol_id(result, result_path.parent.name),
                    "is_current": False,
                    "resume_invocation": result_path.parents[1].name,
                }
            )
    return records


def _protocol_id(result: dict[str, Any], trial_name: str) -> str:
    path = (
        ((result.get("config") or {}).get("task") or {}).get("path")
        or ((result.get("task_id") or {}).get("path"))
        or ""
    )
    return Path(path).name or trial_name.split("__", 1)[0]


def _rewards(trial_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    rewards = (result.get("verifier_result") or {}).get("rewards")
    if isinstance(rewards, dict):
        return dict(rewards)
    reward_file = _read_json(trial_dir / "verifier" / "reward.json")
    return dict(reward_file) if isinstance(reward_file, dict) else {}


def _prediction_valid(details: dict[str, Any], rewards: dict[str, Any]) -> bool | None:
    value = details.get("prediction_valid")
    if isinstance(value, bool):
        return value
    value = rewards.get("prediction_valid")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    return None


def _status(exception_type: str | None, verifier_completed: bool) -> str:
    if exception_type and verifier_completed:
        return "verifier_completed_with_exception"
    if exception_type:
        return "exception"
    if verifier_completed:
        return "verifier_completed"
    return "incomplete"


def _valid_completion(
    verifier_completed: bool, prediction_valid: bool | None
) -> bool | None:
    if not verifier_completed:
        return False
    if prediction_valid is None:
        return None
    return prediction_valid


def _summary(rows: list[dict[str, Any]], lock: dict[str, Any]) -> dict[str, Any]:
    current = [row for row in rows if row["is_current_execution"]]
    scored = [row for row in current if isinstance(row.get("reward"), (int, float))]
    core = [row for row in scored if row["design"] == "balanced_core"]
    native_pairings = [
        row
        for row in scored
        if row.get("native_pairing") is True or row["design"] == "native_extension"
    ]
    native_extensions = [row for row in scored if row["design"] == "native_extension"]
    grand = mean(row["reward"] for row in core) if core else None
    model_means = _group_means(core, "model")
    harness_means = _group_means(core, "harness")
    cell_means = _cell_means(core)
    interactions = {}
    if grand is not None:
        for key, value in cell_means.items():
            model, harness = key.split("__", 1)
            interactions[key] = (
                value - model_means[model] - harness_means[harness] + grand
            )
    valid_count = sum(row["valid_completion"] is True for row in current)
    known_completion = [
        row for row in current if isinstance(row.get("valid_completion"), bool)
    ]
    return {
        "expected_trial_count": lock["expected_trial_count"],
        "observed_current_trial_count": len(current),
        "preserved_execution_count": len(rows),
        "scored_current_trial_count": len(scored),
        "exception_trial_count": sum(
            row["exception_type"] is not None for row in current
        ),
        "agent_exception_count": sum(
            _is_agent_exception(row["exception_type"]) for row in current
        ),
        "verifier_completed_count": sum(row["verifier_completed"] for row in current),
        "valid_completion_count": valid_count,
        "valid_completion_unknown_count": len(current) - len(known_completion),
        "valid_completion_rate": (
            valid_count / len(known_completion) if known_completion else None
        ),
        "plot_readiness": {
            "api_equivalent_cost_vs_t3_transition_f1": {
                "x": PLOT_COST_METRIC,
                "y": PLOT_PERFORMANCE_METRIC,
                "complete_row_count": sum(
                    _has_number(row, PLOT_COST_METRIC)
                    and _has_number(row, PLOT_PERFORMANCE_METRIC)
                    for row in current
                ),
            },
            "runtime_vs_valid_completion": {
                "x": PLOT_RUNTIME_METRIC,
                "y": "valid_completion",
                "complete_row_count": sum(
                    _has_number(row, PLOT_RUNTIME_METRIC)
                    and isinstance(row.get("valid_completion"), bool)
                    for row in current
                ),
            },
        },
        "balanced_core": {
            "grand_mean_reward": grand,
            "model_mean_reward": model_means,
            "harness_mean_reward": harness_means,
            "cell_mean_reward": cell_means,
            "additive_interaction_residual": interactions,
            "note": "Model and harness main effects use only the balanced 4x3 core.",
        },
        "native_pairings": {
            "cell_mean_reward": _cell_means(native_pairings),
            "note": (
                "Native pairings are descriptive. The Kimi K3 + Kimi Code cell "
                "also belongs to the balanced core and is executed only once."
            ),
        },
        "native_extensions": {
            "cell_mean_reward": _cell_means(native_extensions),
            "note": (
                "Compatibility view containing only native-only extension cells; "
                "use native_pairings for the complete descriptive analysis."
            ),
        },
    }


def _telemetry_audit(
    rows: list[dict[str, Any]], lock: dict[str, Any]
) -> dict[str, Any]:
    current = [row for row in rows if row["is_current_execution"]]
    missing_rows = []
    counts: dict[str, int] = defaultdict(int)
    cell_counts: dict[str, dict[str, int]] = {}
    for row in current:
        missing = json.loads(row["telemetry_missing_fields_json"])
        for field in missing:
            counts[field] += 1
        if missing:
            missing_rows.append(
                {
                    "model": row["model"],
                    "harness": row["harness"],
                    "protocol_id": row["protocol_id"],
                    "trial_name": row["trial_name"],
                    "exception_type": row["exception_type"],
                    "missing_fields_json": _compact_json(missing),
                }
            )
    for cell in lock["cells"]:
        key = f"{cell['model_key']}__{cell['harness_key']}"
        cell_rows = [
            row
            for row in current
            if row["model"] == cell["model_key"]
            and row["harness"] == cell["harness_key"]
        ]
        cell_counts[key] = {
            "expected": (
                len(lock.get("protocol_ids") or []) * int(lock.get("attempts") or 1)
            ),
            "observed": len(cell_rows),
            "rows_with_missing_fields": sum(
                bool(json.loads(row["telemetry_missing_fields_json"]))
                for row in cell_rows
            ),
            "rows_with_provider_token_fields": sum(
                row.get("provider_token_fields_present") is True for row in cell_rows
            ),
        }
    return {
        "schema_version": 1,
        "pricing_snapshot_id": (
            (lock.get("pricing_snapshot") or {}).get("snapshot_id")
        ),
        "required_trial_fields": [
            "prediction_valid",
            "verifier_completed",
            "exception_type",
            "agent_duration_seconds",
            "verifier_duration_seconds",
            "reported_cost_usd",
            "reported_cost_kind",
            "normalized_api_cost_usd",
            "pricing_date",
            "pricing_source_url",
            "pricing_status",
            "input_tokens",
            "cache_read_tokens",
            "output_tokens",
            "retry_count",
            "resume_count",
        ],
        "missing_field_counts": dict(sorted(counts.items())),
        "cells": cell_counts,
        "rows_with_missing_fields": missing_rows,
    }


def _missing_telemetry(row: dict[str, Any]) -> list[str]:
    fields = [
        "prediction_valid",
        "agent_duration_seconds",
        "verifier_duration_seconds",
        "input_tokens",
        "output_tokens",
        "reported_cost_usd",
        "normalized_api_cost_usd",
        "pricing_date",
        "pricing_source_url",
        "retry_count",
        "resume_count",
    ]
    missing = [field for field in fields if row.get(field) is None]
    if row.get("status") == "exception" and row.get("exception_type") is None:
        missing.append("exception_type")
    return sorted(missing)


def _group_means(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        groups[row[key]].append(float(row["reward"]))
    return {name: mean(values) for name, values in sorted(groups.items())}


def _cell_means(rows: list[dict[str, Any]]) -> dict[str, float]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        groups[f"{row['model']}__{row['harness']}"].append(float(row["reward"]))
    return {name: mean(values) for name, values in sorted(groups.items())}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _phase_duration(phase: Any) -> float | None:
    if not isinstance(phase, dict):
        return None
    return _duration(phase.get("started_at"), phase.get("finished_at"))


def _duration(start: str | None, finish: str | None) -> float | None:
    if not start or not finish:
        return None
    try:
        return (_datetime(finish) - _datetime(start)).total_seconds()
    except ValueError:
        return None


def _datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _started_at(result: dict[str, Any]) -> str:
    return str(result.get("started_at") or "")


def _has_number(row: dict[str, Any], field: str) -> bool:
    return isinstance(row.get(field), (int, float)) and not isinstance(
        row.get(field), bool
    )


def _is_agent_exception(exception_type: Any) -> bool:
    return isinstance(exception_type, str) and exception_type.startswith("Agent")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_required_json(path: Path) -> dict[str, Any]:
    document = _read_json(path)
    if not isinstance(document, dict):
        raise ValueError(f"invalid or unreadable JSON object: {path}")
    return document


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    raise SystemExit(main())
