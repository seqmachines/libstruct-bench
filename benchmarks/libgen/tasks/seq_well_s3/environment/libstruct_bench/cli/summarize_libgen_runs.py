from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create tidy libgen trial rows and balanced-core effect summaries."
    )
    parser.add_argument("--runs-root", required=True)
    parser.add_argument("--experiment-lock", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    lock = json.loads(Path(args.experiment_lock).read_text(encoding="utf-8"))
    cells = {
        f"libgen-{lock['mode']}-{item['model_key']}-{item['harness_key']}": item
        for item in lock["cells"]
    }
    rows: list[dict[str, Any]] = []
    for job_name, cell in cells.items():
        job_dir = Path(args.runs_root) / job_name
        if not job_dir.is_dir():
            continue
        trials = []
        for result_path in job_dir.glob("*/result.json"):
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            protocol_id = Path(result.get("config", {}).get("task", {}).get("path", "")).name
            if not protocol_id:
                protocol_id = result_path.parent.name.split("__", 1)[0]
            trials.append((protocol_id, result_path, result))
        by_protocol: dict[str, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
        for protocol_id, path, result in trials:
            by_protocol[protocol_id].append((path, result))
        for protocol_id, protocol_trials in by_protocol.items():
            protocol_trials.sort(key=lambda item: item[1].get("started_at") or "")
            for attempt, (path, result) in enumerate(protocol_trials, start=1):
                rewards = (result.get("verifier_result") or {}).get("rewards") or {}
                agent_result = result.get("agent_result") or {}
                row: dict[str, Any] = {
                    "design": cell["design"],
                    "model": cell["model_key"],
                    "model_id": cell["model_id"],
                    "harness": cell["harness_key"],
                    "harness_version": cell["harness_version"],
                    "protocol_id": protocol_id,
                    "attempt": attempt,
                    "status": "error" if result.get("exception_info") else "completed",
                    "trial_name": result.get("trial_name"),
                    "result_path": str(path),
                    "duration_seconds": _duration(result.get("started_at"), result.get("finished_at")),
                    "input_tokens": agent_result.get("n_input_tokens"),
                    "cache_tokens": agent_result.get("n_cache_tokens"),
                    "output_tokens": agent_result.get("n_output_tokens"),
                    "cost_usd": agent_result.get("cost_usd"),
                }
                row.update(rewards)
                rows.append(row)

    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "trials.csv", rows)
    summary = _summary(rows, lock)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"summarized {len(rows)}/{lock['expected_trial_count']} expected trials")
    return 0 if len(rows) == lock["expected_trial_count"] else 1


def _summary(rows: list[dict[str, Any]], lock: dict[str, Any]) -> dict[str, Any]:
    completed = [
        row
        for row in rows
        if row["status"] == "completed" and isinstance(row.get("reward"), (int, float))
    ]
    core = [row for row in completed if row["design"] == "balanced_core"]
    native = [row for row in completed if row["design"] == "native_extension"]
    grand = mean(row["reward"] for row in core) if core else None
    model_means = _group_means(core, "model")
    harness_means = _group_means(core, "harness")
    cell_means = _cell_means(core)
    interactions = {}
    if grand is not None:
        for key, value in cell_means.items():
            model, harness = key.split("__", 1)
            interactions[key] = value - model_means[model] - harness_means[harness] + grand
    return {
        "expected_trial_count": lock["expected_trial_count"],
        "observed_trial_count": len(rows),
        "completed_trial_count": len(completed),
        "error_trial_count": sum(row["status"] == "error" for row in rows),
        "balanced_core": {
            "grand_mean_reward": grand,
            "model_mean_reward": model_means,
            "harness_mean_reward": harness_means,
            "cell_mean_reward": cell_means,
            "additive_interaction_residual": interactions,
            "note": "Model and harness main effects are estimated only from the balanced 4x3 core.",
        },
        "native_extensions": {
            "cell_mean_reward": _cell_means(native),
            "note": "Native model-harness pairings are descriptive extensions, not part of the factorial main-effect estimates.",
        },
    }


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


def _duration(start: str | None, finish: str | None) -> float | None:
    if not start or not finish:
        return None
    return (datetime.fromisoformat(finish) - datetime.fromisoformat(start)).total_seconds()


if __name__ == "__main__":
    raise SystemExit(main())
