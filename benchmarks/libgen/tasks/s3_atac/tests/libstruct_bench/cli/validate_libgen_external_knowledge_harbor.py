from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from libstruct_bench.audit.external_knowledge import load_json
from libstruct_bench.audit.external_knowledge_harbor import (
    validate_external_knowledge_harbor_integration,
    validate_external_knowledge_harbor_plan,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a frozen Libgen external-knowledge Harbor integration."
    )
    parser.add_argument("--integration-root", required=True)
    parser.add_argument("--tasks", default="benchmarks/libgen/tasks")
    parser.add_argument("--plan-root")
    args = parser.parse_args(argv)

    integration_root = Path(args.integration_root)
    tasks_root = Path(args.tasks)
    manifest = load_json(integration_root / "integration_manifest.json")
    installed_harbor = _harbor_version()
    if installed_harbor != manifest["harbor_version"]:
        raise ValueError(
            "Harbor version drift: integration was prepared for "
            f"{manifest['harbor_version']!r}, installed {installed_harbor!r}"
        )
    report = validate_external_knowledge_harbor_integration(
        integration_root,
        tasks_root=tasks_root,
    )
    if args.plan_root:
        report["plan"] = validate_external_knowledge_harbor_plan(
            Path(args.plan_root),
            integration_root=integration_root,
            tasks_root=tasks_root,
        )
    print(report)
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
