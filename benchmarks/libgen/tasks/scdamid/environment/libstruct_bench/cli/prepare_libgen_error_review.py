from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from libstruct_bench.libgen.error_analysis import (
    ERROR_ANALYSIS_SCHEMA_VERSION,
    artifact_record,
    build_error_analysis,
    summarize_error_analysis,
)


PACK_SCHEMA_VERSION = "libstruct.libgen_error_review_pack.v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preserve Libgen pilot traces and diagnostics and prepare "
            "deterministic discrepancy records for human adjudication."
        )
    )
    parser.add_argument("--runs-root", required=True)
    parser.add_argument("--experiment-lock", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--schema",
        default="schemas/analysis/libgen_error_analysis.schema.json",
    )
    args = parser.parse_args(argv)

    output_root = Path(args.out)
    if output_root.exists():
        raise FileExistsError(
            f"refusing to overwrite an error-review pack: {output_root}"
        )

    lock_path = Path(args.experiment_lock)
    lock = _load_json(lock_path)
    if lock.get("mode") != "pilot":
        raise ValueError("error-review preparation currently requires a pilot lock")
    if lock.get("expected_trial_count") != 60:
        raise ValueError(
            "the approved pilot must contain exactly 60 trials; found "
            f"{lock.get('expected_trial_count')!r}"
        )

    schema = _load_json(Path(args.schema))
    validator = Draft202012Validator(schema)
    runs_root = Path(args.runs_root)
    trial_records: list[dict[str, Any]] = []
    observation_rows: list[dict[str, Any]] = []
    pack_issues: list[str] = []

    for cell in lock["cells"]:
        job_name = (
            f"libgen-{lock['mode']}-{cell['model_key']}-{cell['harness_key']}"
        )
        job_dir = runs_root / job_name
        if not job_dir.is_dir():
            pack_issues.append(f"missing Harbor job directory: {job_dir}")
            continue

        sources = _discover_trials(job_dir, lock["protocol_ids"])
        by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for source in sources:
            by_protocol[source["protocol_id"]].append(source)

        for protocol_id, protocol_sources in sorted(by_protocol.items()):
            protocol_sources.sort(
                key=lambda item: (
                    (item.get("result") or {}).get("started_at") or "",
                    item["trial_dir"].name,
                )
            )
            for attempt, source in enumerate(protocol_sources, start=1):
                record, rows, issues = _prepare_trial(
                    source=source,
                    output_root=output_root,
                    job_name=job_name,
                    protocol_id=protocol_id,
                    model=cell["model_key"],
                    harness=cell["harness_key"],
                    attempt=attempt,
                    validator=validator,
                )
                trial_records.append(record)
                observation_rows.extend(rows)
                pack_issues.extend(issues)

    expected = int(lock["expected_trial_count"])
    if len(trial_records) != expected:
        pack_issues.append(
            f"observed {len(trial_records)} trials but the experiment lock expects {expected}"
        )

    output_root.mkdir(parents=True, exist_ok=True)
    review_manifest = {
        "schema_version": PACK_SCHEMA_VERSION,
        "experiment_lock": str(lock_path.resolve()),
        "experiment_lock_sha256": artifact_record(
            lock_path,
            role="trial_lock",
        )["sha256"],
        "expected_trial_count": expected,
        "observed_trial_count": len(trial_records),
        "preservation_complete": not pack_issues,
        "preservation_issues": pack_issues,
        "pilot_task_bundle_sha256": lock.get("task_bundle_sha256"),
        "benchmark_refreeze": {
            "status": "pending",
            "task_bundle_sha256": None,
            "recorded_by": None,
            "recorded_at": None,
            "notes": None,
        },
        "trials": trial_records,
    }
    _write_json(output_root / "review_manifest.json", review_manifest)
    _write_csv(output_root / "observations.csv", observation_rows)
    print(
        f"prepared {len(trial_records)}/{expected} pilot trial review records "
        f"with {len(observation_rows)} deterministic observations"
    )
    if pack_issues:
        print(f"preservation gate found {len(pack_issues)} issue(s)")
    return 0 if not pack_issues else 1


def _discover_trials(
    job_dir: Path,
    protocol_ids: list[str],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for trial_dir in sorted(path for path in job_dir.iterdir() if path.is_dir()):
        result_path = _first_existing(
            trial_dir / "result.json",
            trial_dir / "results.json",
        )
        result = _load_json_or_none(result_path)
        if result_path is None and not any(
            (trial_dir / name).exists()
            for name in ("agent", "verifier", "artifacts", "config.json", "lock.json")
        ):
            continue
        protocol_id = _protocol_id(
            trial_dir=trial_dir,
            result=result,
            expected=protocol_ids,
        )
        sources.append(
            {
                "trial_dir": trial_dir,
                "result_path": result_path,
                "result": result,
                "protocol_id": protocol_id,
            }
        )
    return sources


def _prepare_trial(
    *,
    source: dict[str, Any],
    output_root: Path,
    job_name: str,
    protocol_id: str,
    model: str,
    harness: str,
    attempt: int,
    validator: Draft202012Validator,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    trial_dir: Path = source["trial_dir"]
    trial_id = (source.get("result") or {}).get("trial_name") or trial_dir.name
    destination = output_root / "trials" / job_name / trial_dir.name
    raw_root = destination / "raw"
    raw_root.mkdir(parents=True)

    inventory = _copy_trial_evidence(trial_dir, raw_root, destination)
    details_path = _first_existing(
        trial_dir / "verifier/details.json",
        trial_dir / "artifacts/logs/verifier/details.json",
    )
    error_path = _first_existing(
        trial_dir / "verifier/error.json",
        trial_dir / "artifacts/logs/verifier/error.json",
    )
    generated_analysis_path = _first_existing(
        trial_dir / "verifier/error_analysis.json",
        trial_dir / "artifacts/logs/verifier/error_analysis.json",
    )
    trajectory_path = _first_existing(
        trial_dir / "agent/trajectory.json",
        trial_dir / "artifacts/agent_trajectory.json",
        trial_dir / "artifacts/logs/agent/trajectory.json",
    )
    details = _load_json_or_none(details_path)
    verifier_error = _load_json_or_none(error_path)
    generated_analysis = _load_json_or_none(generated_analysis_path)
    trajectory = _load_json_or_none(trajectory_path)
    result = source.get("result")
    issues = _preservation_issues(
        trial_id=trial_id,
        result_path=source.get("result_path"),
        result=result,
        details=details,
        verifier_error=verifier_error,
        inventory=inventory,
    )
    if details is None and verifier_error is None and not (result or {}).get(
        "exception_info"
    ):
        verifier_error = {
            "kind": "verifier_configuration_error",
            "message": "No preserved verifier details or error artifact was found.",
        }

    if (
        generated_analysis is not None
        and generated_analysis.get("schema_version")
        == ERROR_ANALYSIS_SCHEMA_VERSION
    ):
        analysis = generated_analysis
        analysis.update(
            {
                "trial_id": trial_id,
                "protocol_id": protocol_id,
                "model": model,
                "harness": harness,
                "attempt": attempt,
                "artifact_inventory": inventory,
            }
        )
        analysis["summary"] = summarize_error_analysis(analysis)
    else:
        analysis = build_error_analysis(
            trial_id=trial_id,
            protocol_id=protocol_id,
            result=result,
            details=details,
            verifier_error=verifier_error,
            artifact_inventory=inventory,
            model=model,
            harness=harness,
            attempt=attempt,
            trajectory=trajectory,
            trajectory_path=(
                trajectory_path.relative_to(trial_dir).as_posix()
                if trajectory_path is not None
                else None
            ),
        )
    validator.validate(analysis)
    analysis_path = destination / "error_analysis.json"
    _write_json(analysis_path, analysis)

    relative_analysis = analysis_path.relative_to(output_root).as_posix()
    record = {
        "trial_id": trial_id,
        "protocol_id": protocol_id,
        "model": model,
        "harness": harness,
        "attempt": attempt,
        "run_outcome": analysis["run_outcome"],
        "analysis_path": relative_analysis,
        "artifact_count": len(inventory),
        "preservation_issues": issues,
    }
    rows = [
        {
            "trial_id": trial_id,
            "protocol_id": protocol_id,
            "model": model,
            "harness": harness,
            "attempt": attempt,
            "run_outcome": analysis["run_outcome"],
            **observation,
        }
        for observation in analysis["observations"]
    ]
    return record, rows, [f"{trial_id}: {issue}" for issue in issues]


def _copy_trial_evidence(
    trial_dir: Path,
    raw_root: Path,
    review_trial_root: Path,
) -> list[dict[str, Any]]:
    sources: set[Path] = set()
    for name in (
        "result.json",
        "results.json",
        "config.json",
        "lock.json",
        "trial.log",
    ):
        path = trial_dir / name
        if path.is_file():
            sources.add(path)
    for directory_name in ("agent", "verifier", "artifacts"):
        directory = trial_dir / directory_name
        if directory.is_dir():
            sources.update(path for path in directory.rglob("*") if path.is_file())

    inventory: list[dict[str, Any]] = []
    for source in sorted(sources):
        relative = source.relative_to(trial_dir)
        target = raw_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        inventory.append(
            artifact_record(
                target,
                role=_artifact_role(relative),
                relative_to=review_trial_root,
            )
        )
    return inventory


def _artifact_role(path: Path) -> str:
    name = path.name
    if name == "agent_trajectory.json":
        return "agent_trajectory"
    if path.parts[0] == "agent":
        if name.startswith("trajectory") and name.endswith(".json"):
            return "agent_trajectory"
        return "agent_log"
    if name == "t2_prediction.json":
        return "t2_prediction"
    if name == "t3_prediction.json":
        return "t3_prediction"
    if name == "reward.json":
        return "verifier_reward"
    if name == "details.json":
        return "verifier_details"
    if name == "error.json":
        return "verifier_error"
    if path.parts[0] == "verifier":
        return "verifier_diagnostic"
    if name == "manifest.json" and path.parts[0] == "artifacts":
        return "artifact_manifest"
    return {
        "result.json": "result",
        "results.json": "result",
        "config.json": "trial_config",
        "lock.json": "trial_lock",
        "trial.log": "trial_log",
    }.get(name, "trial_artifact")


def _preservation_issues(
    *,
    trial_id: str,
    result_path: Path | None,
    result: dict[str, Any] | None,
    details: dict[str, Any] | None,
    verifier_error: dict[str, Any] | None,
    inventory: list[dict[str, Any]],
) -> list[str]:
    del trial_id
    issues: list[str] = []
    roles = [item["role"] for item in inventory]
    if result_path is None or result is None:
        issues.append("missing or unreadable Harbor result")
    if not any(role in {"agent_trajectory", "agent_log"} for role in roles):
        issues.append("no observable agent trajectory or action log was preserved")
    if details is None and verifier_error is None and not (result or {}).get(
        "exception_info"
    ):
        issues.append("no readable verifier details or error artifact was preserved")
    if details and details.get("prediction_valid") is True:
        for role in ("t2_prediction", "t3_prediction", "verifier_reward"):
            if role not in roles:
                issues.append(f"valid prediction is missing preserved {role}")
    return issues


def _protocol_id(
    *,
    trial_dir: Path,
    result: dict[str, Any] | None,
    expected: list[str],
) -> str:
    task_path = (result or {}).get("config", {}).get("task", {}).get("path", "")
    from_result = Path(task_path).name
    if from_result in expected:
        return from_result
    for protocol_id in sorted(expected, key=len, reverse=True):
        if trial_dir.name == protocol_id or trial_dir.name.startswith(protocol_id + "__"):
            return protocol_id
    return from_result or trial_dir.name.split("__", 1)[0]


def _first_existing(*paths: Path) -> Path | None:
    return next((path for path in paths if path.is_file()), None)


def _load_json_or_none(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return document


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    if not fields:
        fields = ["trial_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(fieldnames=fields, extrasaction="ignore", f=handle)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
