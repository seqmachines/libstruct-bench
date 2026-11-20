from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from libstruct_bench.archive.hooks import agent_env, host_hook, isolated


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--harbor-cli":
        # Keep this workaround process-local; do not edit the installed Harbor
        # package, saved experiment config, or completed trial results.
        _install_explicit_task_metrics_workaround()
        from harbor.cli.main import app
        app(args=argv[1:], prog_name="harbor")
        return 0
    parser = argparse.ArgumentParser(
        description=(
            "Snapshot failed Libgen trial telemetry, then delegate to Harbor's "
            "destructive job-resume command."
        )
    )
    parser.add_argument("--job-path", "-p", required=True)
    parser.add_argument("--filter-error-type", "-f", action="append", default=[])
    parser.add_argument(
        "--include-kimi-provider-errors",
        action="store_true",
        help=(
            "also retry incomplete Kimi outputs whose terminal wire response "
            "records a transport or temporary in-flight budget failure missed by Harbor; with no -f, "
            "select only these failures"
        ),
    )
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="create the preservation snapshot without invoking Harbor",
    )
    parser.add_argument(
        "--confirmed-infrastructure-outage",
        action="store_true",
        help=(
            "mark the selected rerun as an approved primary replacement for a "
            "confirmed infrastructure or provider outage"
        ),
    )
    parser.add_argument("--confirmed-by")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)

    job_path = Path(args.job_path).resolve()
    if not (job_path / "config.json").is_file():
        raise ValueError(f"not a Harbor job directory: {job_path}")
    job_config = json.loads((job_path / "config.json").read_text())
    explicit_task_metrics_workaround = any(t.get("source") for t in job_config.get("tasks", []))
    filters = set(args.filter_error_type)
    selected: list[tuple[Path, dict[str, Any]]] = []
    kimi_errors: dict[str, dict[str, Any]] = {}
    for result_path in sorted(job_path.glob("*/result.json")):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        error_type = (result.get("exception_info") or {}).get("exception_type")
        if error_type and (
            error_type in filters or (not filters and not args.include_kimi_provider_errors)
            or (args.include_kimi_provider_errors and error_type == "KimiProviderError")
        ):
            selected.append((result_path.parent, result))
        elif args.include_kimi_provider_errors and not error_type:
            evidence = _kimi_provider_error(result_path.parent, result)
            if evidence is not None:
                selected.append((result_path.parent, result))
                kimi_errors[result_path.parent.name] = evidence

    if not selected and not args.snapshot_only:
        print("No matching failed executions; nothing to resume.")
        return 0

    if args.confirmed_infrastructure_outage:
        if not args.confirmed_by or not args.reason:
            raise ValueError(
                "confirmed infrastructure reruns require --confirmed-by and --reason"
            )
        timeout_trials = [
            result.get("trial_name") or trial_dir.name
            for trial_dir, result in selected
            if (result.get("exception_info") or {}).get("exception_type")
            == "AgentTimeoutError"
        ]
        if timeout_trials:
            raise ValueError(
                "AgentTimeoutError is not eligible for a primary baseline rerun: "
                + ", ".join(timeout_trials)
            )

    invocation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    # Harbor treats every child directory without a trial result.json as an
    # incomplete trial and deletes it, including hidden metadata directories.
    # Keep durable snapshots beside the job, never inside that cleanup boundary.
    telemetry_root = _telemetry_root(job_path)
    _preserve_legacy_telemetry(job_path, telemetry_root)
    snapshot_root = telemetry_root / "resume_snapshots" / invocation_id
    snapshot_root.mkdir(parents=True)
    # Harbor also removes directories interrupted before result.json exists.
    # Preserve their partial outputs/configs as execution evidence first.
    incomplete = []
    for trial_dir in sorted(job_path.iterdir()):
        if (trial_dir.is_dir() and (trial_dir / "config.json").is_file()
                and not (trial_dir / "result.json").exists()):
            shutil.copytree(trial_dir, snapshot_root / "incomplete" / trial_dir.name)
            incomplete.append(trial_dir.name)
    snapshots = []
    for trial_dir, result in selected:
        destination = snapshot_root / trial_dir.name
        destination.mkdir()
        for name in ("result.json", "config.json", "lock.json", "trial.log", "exception.txt"):
            source = trial_dir / name
            if source.is_file():
                shutil.copy2(source, destination / name)
        for name in ("agent", "verifier", "artifacts"):
            source = trial_dir / name
            if source.is_dir():
                shutil.copytree(source, destination / name)
        snapshots.append(
            {
                "trial_name": result.get("trial_name") or trial_dir.name,
                "protocol_id": _protocol_id(result, trial_dir.name),
                "exception_type": (result.get("exception_info") or {}).get(
                    "exception_type"
                ),
                "result_sha256": _sha256(destination / "result.json"),
                "kimi_provider_error": kimi_errors.get(trial_dir.name),
            }
        )
    event_path = snapshot_root / "resume_event.json"
    event = {
        "schema_version": 2,
        "invocation_id": invocation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_path": str(job_path),
        "filter_error_types": sorted(filters),
        "snapshot_count": len(snapshots),
        "incomplete_trial_snapshots": incomplete,
        "harbor_explicit_task_metrics_workaround": explicit_task_metrics_workaround,
        "rerun_classification": (
            "confirmed_infrastructure_provider_outage"
            if args.confirmed_infrastructure_outage
            else "diagnostic_only"
        ),
        "primary_rerun_eligible": args.confirmed_infrastructure_outage,
        "confirmed_by": args.confirmed_by,
        "reason": args.reason,
        "snapshots": snapshots,
        "harbor_invoked": not args.snapshot_only,
        "harbor_returncode": None,
    }
    _write_json(event_path, event)
    print(f"preserved {len(snapshots)} failed execution(s) under {snapshot_root}")
    if incomplete:
        print(f"also preserved {len(incomplete)} interrupted trial directory/directories without result.json")
    if args.snapshot_only:
        return 0

    # Harbor filters only exception_info. Annotate missed provider failures
    # only after preserving the original, unmodified result and wire evidence.
    for trial_dir, result in selected:
        evidence = kimi_errors.get(trial_dir.name)
        if evidence is None:
            continue
        corrected = dict(result)
        corrected["exception_info"] = {
            "exception_type": "KimiProviderError",
            "exception_message": evidence["message"],
            "exception_traceback": (
                "Recovered from terminal Kimi JSON-RPC response; original "
                f"result and evidence preserved at {snapshot_root / trial_dir.name}"
            ),
            "occurred_at": result.get("finished_at") or event["created_at"],
        }
        _write_json(trial_dir / "result.json", corrected)

    command = ["harbor", "job", "resume", "-p", str(job_path)]
    if explicit_task_metrics_workaround:
        print("Applying Harbor's missing explicit-task metrics workaround for this resume process.")
        command = [sys.executable, "-m", "libstruct_bench.cli.resume_libgen_job",
                   "--harbor-cli", *command[1:]]
    effective_filters = sorted({
        (result.get("exception_info") or {}).get("exception_type")
        or "KimiProviderError"
        for _, result in selected
    })
    for error_type in effective_filters:
        command.extend(["-f", error_type])
    host_hook(job_path, starting=True)
    try:
        options = {"env": agent_env()} if isolated() else {}
        completed = subprocess.run(command, check=False, **options)
    finally:
        host_hook(job_path)
    event["harbor_returncode"] = completed.returncode
    event["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(event_path, event)
    return completed.returncode


def _install_explicit_task_metrics_workaround() -> None:
    """Initialize missing named-task metrics before Harbor's progress hooks.

    Affected Harbor versions initialize metrics for datasets and ``adhoc`` but
    omit explicit TaskConfig.source values. The completion hook later indexes
    an empty metric list and cancels the whole job. Preserve existing/custom
    metrics and add Harbor's normal default Mean only where a source lacks one.
    """
    from harbor.job import Job
    from harbor.metrics.factory import MetricFactory
    from harbor.metrics.mean import Mean

    original = Job._resolve_metrics
    if getattr(original, "_libgen_explicit_task_metrics", False):
        return

    async def resolve(config, task_configs):
        metrics = await original(config, task_configs)
        for task in task_configs:
            source = task.source or "adhoc"
            if not metrics.get(source):
                metrics[source] = [
                    MetricFactory.create_metric(m.type, **m.kwargs) for m in config.metrics
                ] or [Mean()]
        return metrics

    resolve._libgen_explicit_task_metrics = True
    Job._resolve_metrics = staticmethod(resolve)


def _kimi_provider_error(trial_dir: Path, result: dict[str, Any]) -> dict[str, Any] | None:
    """Recognize terminal transport/temporary budget errors, never task failures."""
    if (result.get("agent_info") or {}).get("name") != "kimi-cli":
        return None
    if result.get("exception_info"):
        return None
    details_path = trial_dir / "verifier" / "details.json"
    if details_path.is_file():
        details = json.loads(details_path.read_text(encoding="utf-8"))
        if details.get("prediction_valid") is True:
            return None
    wire = trial_dir / "agent" / "kimi-cli.txt"
    if not wire.is_file():
        return None
    last = ""
    with wire.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                last = line
    try:
        terminal = json.loads(last)
    except json.JSONDecodeError:
        return None
    if not isinstance(terminal, dict):
        return None
    error = terminal.get("error")
    if (
        terminal.get("jsonrpc") != "2.0"
        or str(terminal.get("id")) != "1"
        or not isinstance(error, dict)
        or error.get("code") != -32003
    ):
        return None
    message = str(error.get("message", ""))
    transport_error = any(marker in message.lower() for marker in (
        "upstream idle timeout exceeded",
        "upstream provider terminated the stream before completion",
        "network connection lost",
    ))
    if not transport_error and not _kimi_in_flight_budget_error(message):
        return None
    return {
        "source": "agent/kimi-cli.txt",
        "sha256": _sha256(wire),
        "code": error["code"],
        "message": message,
    }


def _kimi_in_flight_budget_error(message: str) -> bool:
    """Match OpenRouter's temporary reservation limit, not arbitrary HTTP 402s."""
    prefix = "Error code: 402 - "
    if not message.startswith(prefix):
        return False
    payload = message[len(prefix):]
    try:
        document = json.loads(payload)
    except ValueError:
        try:
            # Kimi may render the provider body as a Python dict representation.
            document = ast.literal_eval(payload)
        except (ValueError, SyntaxError):
            return False
    error = document.get("error") if isinstance(document, dict) else None
    if not isinstance(error, dict) or error.get("code") != 402:
        return False
    metadata = error.get("metadata")
    return (isinstance(metadata, dict)
            and metadata.get("reason") == "in_flight_budget_exhausted"
            and metadata.get("limit_source") == "openrouter_in_flight_budget")


def _telemetry_root(job_path: Path) -> Path:
    return job_path.parent / ".libgen_telemetry" / job_path.name


def _preserve_legacy_telemetry(job_path: Path, destination: Path) -> None:
    """Copy surviving old history out of Harbor's cleanup area before resume.

    Never overwrite conflicting history. Leave the old copy in place so a
    snapshot-only invocation is non-destructive; the summarizer deduplicates it.
    """
    source_root = job_path / ".libgen_telemetry"
    if not source_root.is_dir():
        return
    for source in sorted(source_root.rglob("*")):
        if not source.is_file():
            continue
        target = destination / source.relative_to(source_root)
        if target.exists():
            if _sha256(source) != _sha256(target):
                raise ValueError(f"conflicting preserved telemetry: {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _protocol_id(result: dict[str, Any], trial_name: str) -> str:
    path = (
        ((result.get("config") or {}).get("task") or {}).get("path")
        or ((result.get("task_id") or {}).get("path"))
        or ""
    )
    return Path(path).name or trial_name.split("__", 1)[0]


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
