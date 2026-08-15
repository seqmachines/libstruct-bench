from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Snapshot failed Libgen trial telemetry, then delegate to Harbor's "
            "destructive job-resume command."
        )
    )
    parser.add_argument("--job-path", "-p", required=True)
    parser.add_argument(
        "--filter-error-type", "-f", action="append", default=[]
    )
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="create the preservation snapshot without invoking Harbor",
    )
    args = parser.parse_args(argv)

    job_path = Path(args.job_path).resolve()
    if not (job_path / "config.json").is_file():
        raise ValueError(f"not a Harbor job directory: {job_path}")
    filters = set(args.filter_error_type)
    selected: list[tuple[Path, dict[str, Any]]] = []
    for result_path in sorted(job_path.glob("*/result.json")):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        error_type = (result.get("exception_info") or {}).get("exception_type")
        if error_type and (not filters or error_type in filters):
            selected.append((result_path.parent, result))

    invocation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    snapshot_root = (
        job_path / ".libgen_telemetry" / "resume_snapshots" / invocation_id
    )
    snapshot_root.mkdir(parents=True)
    snapshots = []
    for trial_dir, result in selected:
        destination = snapshot_root / trial_dir.name
        destination.mkdir()
        for name in ("result.json", "config.json", "lock.json", "trial.log"):
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
            }
        )
    event_path = snapshot_root / "resume_event.json"
    event = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_path": str(job_path),
        "filter_error_types": sorted(filters),
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
        "harbor_invoked": not args.snapshot_only,
        "harbor_returncode": None,
    }
    _write_json(event_path, event)
    print(f"preserved {len(snapshots)} failed execution(s) under {snapshot_root}")
    if args.snapshot_only:
        return 0

    command = ["harbor", "job", "resume", "-p", str(job_path)]
    for error_type in args.filter_error_type:
        command.extend(["-f", error_type])
    completed = subprocess.run(command, check=False)
    event["harbor_returncode"] = completed.returncode
    event["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(event_path, event)
    return completed.returncode


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
