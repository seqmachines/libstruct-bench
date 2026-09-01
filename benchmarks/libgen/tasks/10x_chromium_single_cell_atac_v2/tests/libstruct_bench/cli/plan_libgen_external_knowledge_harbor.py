from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from libstruct_bench.audit.external_knowledge import load_json
from libstruct_bench.audit.external_knowledge_harbor import (
    CONDITION_IDS,
    build_external_knowledge_harbor_plan,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Clone completed Libgen baseline Harbor configs into a locked "
            "external-knowledge intervention plan."
        )
    )
    parser.add_argument("--integration-root", required=True)
    parser.add_argument("--tasks", default="benchmarks/libgen/tasks")
    parser.add_argument(
        "--base-job-config",
        action="append",
        required=True,
        help="completed baseline job config; repeat for each native agent",
    )
    parser.add_argument(
        "--condition",
        action="append",
        choices=CONDITION_IDS,
        help="condition to plan; defaults to all three",
    )
    parser.add_argument("--jobs-dir", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    integration_manifest = load_json(
        Path(args.integration_root) / "integration_manifest.json"
    )
    installed_harbor = _harbor_version()
    if installed_harbor != integration_manifest["harbor_version"]:
        raise ValueError(
            "Harbor version drift: integration was prepared for "
            f"{integration_manifest['harbor_version']!r}, installed "
            f"{installed_harbor!r}"
        )
    plan = build_external_knowledge_harbor_plan(
        integration_root=Path(args.integration_root),
        tasks_root=Path(args.tasks),
        base_job_config_paths=[Path(value) for value in args.base_job_config],
        output_root=Path(args.out),
        jobs_dir=Path(args.jobs_dir),
        created_at=args.created_at,
        condition_ids=args.condition or CONDITION_IDS,
    )
    print(f"planned {len(plan['planned_jobs'])} jobs")
    print(f"planned {plan['expected_trial_count']} trials")
    print(Path(args.out) / "experiment_lock.json")
    return 0


def _harbor_version() -> str:
    result = subprocess.run(
        ["harbor", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
