from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from libstruct_bench.libgen.error_analysis import task_bundle_sha256
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve and lock the 15-cell libgen Harbor experiment matrix."
    )
    parser.add_argument("--matrix", default="benchmarks/libgen/matrix.json")
    parser.add_argument("--tasks", default="benchmarks/libgen/tasks")
    parser.add_argument("--mode", choices=["pilot", "full"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--jobs-dir", default="runs/libgen")
    parser.add_argument("--harbor-version", required=True)
    parser.add_argument(
        "--pilot-clearance",
        help="validated pilot-review status; required before planning the full run",
    )
    args = parser.parse_args(argv)

    installed_harbor = _harbor_version()
    if installed_harbor != args.harbor_version:
        raise ValueError(
            f"Harbor version mismatch: expected {args.harbor_version}, installed {installed_harbor}"
        )
    matrix = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    if matrix.get("benchmark_version") != LIBGEN_BENCHMARK_VERSION:
        raise ValueError(
            "matrix benchmark version does not match the installed scorer: "
            f"{matrix.get('benchmark_version')!r} != {LIBGEN_BENCHMARK_VERSION!r}"
        )
    required_agents = {
        item["harbor_agent"]
        for item in matrix["core_harnesses"] + matrix["native_extensions"]
    }
    unavailable_agents = required_agents - _available_harbor_agents()
    if unavailable_agents:
        raise ValueError(
            "installed Harbor lacks required agents: "
            + ", ".join(sorted(unavailable_agents))
        )
    tasks_root = Path(args.tasks).resolve()
    task_ids = sorted(
        path.name for path in tasks_root.iterdir() if (path / "task.toml").is_file()
    )
    selected = matrix["pilot_protocols"] if args.mode == "pilot" else task_ids
    missing = set(selected) - set(task_ids)
    if missing:
        raise ValueError("generated tasks are missing: " + ", ".join(sorted(missing)))
    if args.mode == "full" and len(task_ids) != 20:
        raise ValueError(
            f"full matrix requires exactly 20 generated tasks, found {len(task_ids)}"
        )
    task_digest = task_bundle_sha256(tasks_root, task_ids)
    pilot_clearance = None
    if args.mode == "full":
        if not args.pilot_clearance:
            raise ValueError("--pilot-clearance is required for the full matrix")
        pilot_clearance = json.loads(
            Path(args.pilot_clearance).read_text(encoding="utf-8")
        )
        if pilot_clearance.get("full_run_ready") is not True:
            raise ValueError(
                "pilot error review has not cleared the full production run"
            )
        if pilot_clearance.get("expected_trial_count") != 60:
            raise ValueError("pilot clearance does not cover the approved 60 trials")
        if pilot_clearance.get("task_bundle_sha256") != task_digest:
            raise ValueError(
                "generated tasks changed after the benchmark was refrozen and cleared"
            )

    models = {item["model_key"]: item for item in matrix["models"]}
    cells: list[dict[str, Any]] = []
    for model in matrix["models"]:
        model_id = _required_env(model["core_model_id_env"])
        for harness in matrix["core_harnesses"]:
            cells.append(
                _cell(
                    model=model,
                    harness=harness,
                    model_id=model_id,
                    design="balanced_core",
                )
            )
    for harness in matrix["native_extensions"]:
        model = models[harness["model_key"]]
        model_id = _required_env(model["native_model_id_env"])
        cells.append(
            _cell(
                model=model,
                harness=harness,
                model_id=model_id,
                design="native_extension",
            )
        )
    if len(cells) != 15:
        raise ValueError(
            f"expected the approved 15-cell design, found {len(cells)} cells"
        )

    output_root = Path(args.out)
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite matrix plan: {output_root}")
    config_root = output_root / "jobs"
    config_root.mkdir(parents=True)
    attempts = matrix[f"{args.mode}_attempts"]
    commands: list[str] = []
    for cell in cells:
        job_name = f"libgen-{args.mode}-{cell['model_key']}-{cell['harness_key']}"
        config = {
            "job_name": job_name,
            "jobs_dir": str(Path(args.jobs_dir).resolve()),
            "n_attempts": attempts,
            "n_concurrent_trials": matrix["n_concurrent_trials"],
            "environment": {"type": matrix["environment"]},
            "agents": [
                {
                    "name": cell["harbor_agent"],
                    "model_name": cell["model_id"],
                    "kwargs": cell["agent_kwargs"],
                    "skills": [],
                    "mcp_servers": [],
                    "include_logs": [],
                    "exclude_logs": [],
                }
            ],
            "datasets": [
                {
                    "path": str(tasks_root),
                    "task_names": selected if args.mode == "pilot" else None,
                }
            ],
            "artifacts": [
                "/logs/artifacts/t2_prediction.json",
                "/logs/artifacts/t3_prediction.json",
                "/logs/verifier/reward.json",
                "/logs/verifier/details.json",
                "/logs/verifier/error_analysis.json",
                "/logs/verifier/error.json",
            ],
        }
        config_path = config_root / f"{cell['model_key']}__{cell['harness_key']}.json"
        config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        commands.append("harbor run -c " + shlex.quote(str(config_path.resolve())))

    lock = {
        "mode": args.mode,
        "benchmark_version": matrix["benchmark_version"],
        "research_question": matrix["research_question"],
        "harbor_version": installed_harbor,
        "environment": matrix["environment"],
        "attempts": attempts,
        "protocol_ids": selected,
        "task_bundle_sha256": task_digest,
        "cells": cells,
        "expected_trial_count": len(cells) * len(selected) * attempts,
        "analysis_design": {
            "balanced_core_cells": 12,
            "native_extension_cells": 3,
            "model_and_harness_effects_use": "balanced_core",
            "native_extensions_are_reported_separately": True,
        },
        "error_analysis_policy": {
            "preserve_all_agent_logs": True,
            "preserve_all_verifier_diagnostics": True,
            "automatic_process_attribution": False,
            "pilot_substantive_mismatches_require_adjudication": True,
            "full_run_requires_refrozen_benchmark": True,
        },
        "pilot_clearance": pilot_clearance,
    }
    (output_root / "experiment_lock.json").write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_root / "run.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + "\n".join(commands) + "\n",
        encoding="utf-8",
    )
    print(f"planned {len(cells)} cells and {lock['expected_trial_count']} trials")
    print(output_root / "run.sh")
    return 0


def _cell(
    *, model: dict[str, Any], harness: dict[str, Any], model_id: str, design: str
) -> dict[str, Any]:
    if (
        harness["harbor_agent"]
        in {
            "gemini-cli",
            "kimi-cli",
            "mini-swe-agent",
            "pi",
        }
        and "/" not in model_id
    ):
        raise ValueError(
            f"{harness['harbor_agent']} requires a provider/model model ID, found {model_id!r}"
        )
    version = _required_env(harness["version_env"])
    kwargs = dict(harness.get("agent_kwargs", {}))
    kwargs["version"] = version
    return {
        "design": design,
        "model_key": model["model_key"],
        "model_display_name": model["display_name"],
        "model_id": model_id,
        "harness_key": harness["harness_key"],
        "harness_display_name": harness["display_name"],
        "harbor_agent": harness["harbor_agent"],
        "harness_version": version,
        "reasoning_setting": harness["reasoning_setting"],
        "agent_kwargs": kwargs,
    }


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"required experiment pin is unset: {name}")
    return value


def _harbor_version() -> str:
    result = subprocess.run(
        ["harbor", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _available_harbor_agents() -> set[str]:
    try:
        from harbor.models.agent.name import AgentName
    except ImportError as error:
        raise ValueError(
            "Harbor is not importable in the planning environment"
        ) from error
    return {item.value for item in AgentName}


if __name__ == "__main__":
    raise SystemExit(main())
