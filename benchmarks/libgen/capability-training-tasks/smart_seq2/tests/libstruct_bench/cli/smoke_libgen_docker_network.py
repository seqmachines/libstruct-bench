from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exercise the generated Libgen Docker provider-only network."
    )
    parser.add_argument("--task", default="benchmarks/libgen/tasks/s3_atac")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    environment = Path(args.task).resolve() / "environment"
    compose = environment / "docker-compose.yaml"
    policy_path = environment / "egress_policy.json"
    if not compose.is_file() or not policy_path.is_file():
        raise ValueError(
            f"task lacks Docker provider-only network assets: {environment}"
        )
    project = "libgen-network-smoke-" + uuid.uuid4().hex[:12]
    override = {"services": {"main": {"image": "python:3.12-alpine"}}}
    with tempfile.TemporaryDirectory(prefix="libgen-network-smoke-") as temp:
        override_path = Path(temp) / "override.json"
        override_path.write_text(json.dumps(override), encoding="utf-8")
        base_command = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--file",
            str(compose),
            "--file",
            str(override_path),
        ]
        completed = subprocess.run(
            base_command
            + ["--profile", "network-smoke", "run", "--rm", "network-smoke"],
            cwd=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        subprocess.run(
            base_command + ["down", "--volumes", "--remove-orphans"],
            cwd=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    probe = _last_json_object(completed.stdout)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy_bytes = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    report: dict[str, Any] = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ready": completed.returncode == 0 and probe is not None,
        "docker_compose_returncode": completed.returncode,
        "network_policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "probe": probe,
    }
    if completed.returncode != 0:
        report["stderr_tail"] = completed.stderr[-4000:]
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ready"] else 1


def _last_json_object(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict):
            return document
    return None


if __name__ == "__main__":
    raise SystemExit(main())
