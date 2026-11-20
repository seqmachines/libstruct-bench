from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from libstruct_bench.audit.connected_process import (
    ConnectedProcessMigrationError,
    migrate_connected_process_bundle,
)
from libstruct_bench.cli.grade_libgen import GROUNDTRUTH_FILENAMES
from libstruct_bench.cli.grade_libgen import main as grade_main
from libstruct_bench.libgen.scoring import LIBGEN_PUBLIC_METRIC_KEYS
from libstruct_bench.libgen.validation import (
    LibgenValidationError,
    validate_groundtruth_bundle,
)
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rescore preserved Libgen Harbor predictions into a versioned sidecar "
            "without rewriting the original trial result."
        )
    )
    parser.add_argument("--runs-root", required=True)
    parser.add_argument("--groundtruth-root", required=True)
    parser.add_argument("--schema-root", default="schemas")
    args = parser.parse_args(argv)

    runs_root = Path(args.runs_root).resolve()
    groundtruth_root = Path(args.groundtruth_root).resolve()
    schema_root = Path(args.schema_root).resolve()
    trials = _discover_trials(runs_root)
    if not trials:
        raise ValueError(f"no Harbor trial result.json files found under {runs_root}")

    version_label = f"libgen-{LIBGEN_BENCHMARK_VERSION}"
    targets = {
        trial: trial / "verifier" / "rescore" / version_label for trial in trials
    }
    existing = [path for path in targets.values() if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite existing versioned rescore output: "
            + ", ".join(str(path) for path in existing[:5])
        )

    single_trial_root = len(trials) == 1 and next(iter(trials)) == runs_root
    by_job: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for trial, result in trials.items():
        protocol_id = _protocol_id(result, trial)
        source_truth_dir = groundtruth_root / protocol_id
        source_truth_paths = {
            task: source_truth_dir / filename
            for task, filename in GROUNDTRUTH_FILENAMES.items()
        }
        missing_truth = [
            path for path in source_truth_paths.values() if not path.is_file()
        ]
        if missing_truth:
            raise FileNotFoundError(
                f"{protocol_id} is missing local ground truth: {missing_truth[0]}"
            )

        output = targets[trial]
        output.mkdir(parents=True)
        truth_dir, groundtruth_transform = _prepare_groundtruth(
            protocol_id=protocol_id,
            source_paths=source_truth_paths,
            schema_root=schema_root,
            output_root=output,
        )
        truth_paths = {
            task: truth_dir / filename
            for task, filename in GROUNDTRUTH_FILENAMES.items()
        }
        t2_prediction = _prediction_path(trial, "t2_prediction.json")
        t3_prediction = _prediction_path(trial, "t3_prediction.json")
        trajectory = _trajectory_path(trial)
        command = [
            "--t2-prediction",
            str(t2_prediction),
            "--t3-prediction",
            str(t3_prediction),
            "--protocol-id",
            protocol_id,
            "--groundtruth-dir",
            str(truth_dir),
            "--t1-sha256",
            _sha256(truth_paths["T1"]),
            "--t2-sha256",
            _sha256(truth_paths["T2"]),
            "--t3-sha256",
            _sha256(truth_paths["T3"]),
            "--schema-root",
            str(schema_root),
            "--reward-out",
            str(output / "reward.json"),
            "--details-out",
            str(output / "details.json"),
            "--error-out",
            str(output / "error.json"),
            "--error-analysis-out",
            str(output / "error_analysis.json"),
            "--trial-id",
            trial.name,
        ]
        if trajectory is not None:
            command.extend(["--trajectory", str(trajectory)])
        return_code = grade_main(command)
        if return_code == 2:
            raise RuntimeError(
                f"verifier configuration failed while rescoring {trial.name}"
            )

        metrics = _load_object(output / "reward.json")
        details = _load_object(output / "details.json")
        original_reward = _original_reward(trial)
        record = {
            "trial_name": trial.name,
            "protocol_id": protocol_id,
            "prediction_valid": details.get("prediction_valid") is True,
            "original_reward": original_reward,
            "rescored_metrics": metrics,
            "reward_delta": (
                metrics["reward"] - original_reward
                if original_reward is not None
                else None
            ),
            "groundtruth_transform": groundtruth_transform,
            "output_dir": str(output),
        }
        by_job[trial.parent].append(record)

    for job, records in sorted(by_job.items()):
        summary_dir = (
            targets[next(iter(trials))]
            if single_trial_root
            else job / "rescore" / version_label
        )
        if not single_trial_root:
            summary_dir.mkdir(parents=True, exist_ok=False)
        summary = _summary(job, records)
        _write_json(summary_dir / "summary.json", summary)
        print(summary_dir / "summary.json")
    return 0


def _discover_trials(runs_root: Path) -> dict[Path, dict[str, Any]]:
    result: dict[Path, dict[str, Any]] = {}
    for path in sorted(runs_root.rglob("result.json")):
        relative_parts = path.relative_to(runs_root).parts
        if ".libgen_telemetry" in relative_parts or "rescore" in relative_parts:
            continue
        document = _load_object(path)
        if not isinstance(document.get("trial_name"), str):
            continue
        result[path.parent] = document
    return result


def _protocol_id(result: dict[str, Any], trial: Path) -> str:
    task_name = result.get("task_name")
    prefix = "sequencing/libgen-"
    if isinstance(task_name, str) and task_name.startswith(prefix):
        return task_name[len(prefix) :]
    task_path = ((result.get("config") or {}).get("task") or {}).get("path")
    if isinstance(task_path, str) and task_path:
        return Path(task_path).name
    if "__" in trial.name:
        return trial.name.split("__", 1)[0]
    raise ValueError(f"cannot determine protocol ID for trial {trial}")


def _prediction_path(trial: Path, filename: str) -> Path:
    candidates = (
        trial / "artifacts" / "logs" / "artifacts" / filename,
        trial / "artifacts" / filename,
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def _trajectory_path(trial: Path) -> Path | None:
    candidates = (
        trial / "artifacts" / "agent_trajectory.json",
        trial / "artifacts" / "logs" / "agent" / "trajectory.json",
    )
    return next((path for path in candidates if path.is_file()), None)


def _original_reward(trial: Path) -> float | None:
    path = trial / "verifier" / "reward.json"
    if not path.is_file():
        return None
    value = _load_object(path).get("reward")
    return float(value) if isinstance(value, (int, float)) else None


def _summary(job: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: item["protocol_id"])
    all_metrics = [item["rescored_metrics"] for item in ordered]
    valid_metrics = [
        item["rescored_metrics"] for item in ordered if item["prediction_valid"]
    ]
    return {
        "benchmark_version": LIBGEN_BENCHMARK_VERSION,
        "job_dir": str(job),
        "trial_count": len(ordered),
        "valid_prediction_count": len(valid_metrics),
        "invalid_or_missing_prediction_count": len(ordered) - len(valid_metrics),
        "mean_metrics_all_trials": _mean_metrics(all_metrics),
        "mean_metrics_valid_predictions": _mean_metrics(valid_metrics),
        "trials": ordered,
        "provenance_policy": (
            "versioned sidecar rescore; original Harbor reward, details, and "
            "result.json files are unchanged"
        ),
    }


def _prepare_groundtruth(
    *,
    protocol_id: str,
    source_paths: dict[str, Path],
    schema_root: Path,
    output_root: Path,
) -> tuple[Path, str]:
    documents = {task: _load_object(path) for task, path in source_paths.items()}
    try:
        validate_groundtruth_bundle(
            documents,
            protocol_id=protocol_id,
            schema_root=schema_root,
        )
    except LibgenValidationError as original_error:
        try:
            effective = migrate_connected_process_bundle(documents)
            validate_groundtruth_bundle(
                effective,
                protocol_id=protocol_id,
                schema_root=schema_root,
            )
        except (ConnectedProcessMigrationError, LibgenValidationError) as error:
            raise original_error from error
        effective_root = output_root / "effective_groundtruth"
        effective_root.mkdir()
        for task, filename in GROUNDTRUTH_FILENAMES.items():
            _write_json(effective_root / filename, effective[task])
        return effective_root, "legacy_workflow_terminal_contract_to_final_outputs_v1"
    return next(iter(source_paths.values())).parent, "none"


def _mean_metrics(documents: Iterable[dict[str, Any]]) -> dict[str, float]:
    values = list(documents)
    if not values:
        return {key: 0.0 for key in LIBGEN_PUBLIC_METRIC_KEYS}
    return {
        key: sum(float(document[key]) for document in values) / len(values)
        for key in LIBGEN_PUBLIC_METRIC_KEYS
    }


def _load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return document


def _write_json(path: Path, document: Any) -> None:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
