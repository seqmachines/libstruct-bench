from __future__ import annotations

import argparse
import hashlib
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
        description="Resolve and lock the 16-cell libgen Harbor experiment matrix."
    )
    parser.add_argument("--matrix", default="benchmarks/libgen/matrix.json")
    parser.add_argument("--tasks", default="benchmarks/libgen/tasks")
    parser.add_argument("--mode", choices=["smoke", "pilot", "full"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--jobs-dir", default="runs/libgen")
    parser.add_argument("--harbor-version", required=True)
    parser.add_argument(
        "--pricing-snapshot",
        default="benchmarks/libgen/pricing-2026-08-15.json",
        help="frozen direct-API pricing snapshot embedded in the experiment lock",
    )
    parser.add_argument(
        "--pilot-clearance",
        help="validated pilot-review status; required before planning the full run",
    )
    parser.add_argument(
        "--network-smoke-report",
        required=True,
        help="successful Docker network smoke report to bind into the lock",
    )
    args = parser.parse_args(argv)

    installed_harbor = _harbor_version()
    if installed_harbor != args.harbor_version:
        raise ValueError(
            f"Harbor version mismatch: expected {args.harbor_version}, installed {installed_harbor}"
        )
    matrix = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    if matrix.get("environment") != "docker":
        raise ValueError("Libgen v3 must run in the private Docker environment")
    network_policy_path = Path(args.matrix).parent / matrix["network_policy"]
    network_policy = json.loads(network_policy_path.read_text(encoding="utf-8"))
    network_policy_sha256 = _canonical_json_sha256(network_policy)
    network_smoke_report = json.loads(
        Path(args.network_smoke_report).read_text(encoding="utf-8")
    )
    _validate_network_smoke_report(network_smoke_report, network_policy_sha256)
    core_models = matrix["models"]
    native_only_models = matrix.get("native_only_models", [])
    all_models = core_models + native_only_models
    native_core_pairings = {
        (item["model_key"], item["harness_key"])
        for item in matrix.get("native_core_pairings", [])
    }
    core_cell_keys = {
        (model["model_key"], harness["harness_key"])
        for model in core_models
        for harness in matrix["core_harnesses"]
    }
    if not native_core_pairings <= core_cell_keys:
        raise ValueError(
            "native_core_pairings contains a non-core model × harness cell"
        )
    expected_unique_cells = len(core_models) * len(matrix["core_harnesses"]) + len(
        matrix["native_extensions"]
    )
    expected_pilot_trials = (
        expected_unique_cells
        * len(matrix["pilot_protocols"])
        * int(matrix["pilot_attempts"])
    )
    pricing_path = Path(args.pricing_snapshot)
    pricing_bytes = pricing_path.read_bytes()
    pricing_snapshot = json.loads(pricing_bytes)
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
    if args.mode == "smoke":
        selected = [matrix["smoke_protocol"]]
    elif args.mode == "pilot":
        selected = matrix["pilot_protocols"]
    else:
        selected = task_ids
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
        if pilot_clearance.get("expected_trial_count") != expected_pilot_trials:
            raise ValueError(
                "pilot clearance does not cover the approved "
                f"{expected_pilot_trials} trials"
            )
        if pilot_clearance.get("task_bundle_sha256") != task_digest:
            raise ValueError(
                "generated tasks changed after the benchmark was refrozen and cleared"
            )

    models = {item["model_key"]: item for item in all_models}
    missing_pricing = set(models) - set(pricing_snapshot.get("models", {}))
    if missing_pricing:
        raise ValueError(
            "pricing snapshot lacks matrix models: "
            + ", ".join(sorted(missing_pricing))
        )
    required_pin_names = {
        item[key]
        for item in all_models
        for key in ("core_model_id_env", "native_model_id_env")
        if item.get(key)
    }
    required_pin_names.update(
        item["version_env"]
        for item in matrix["core_harnesses"] + matrix["native_extensions"]
    )
    required_pin_names.update(
        item["base_url_env"]
        for item in matrix["native_extensions"]
        if item.get("base_url_env")
    )
    unset_pins = sorted(name for name in required_pin_names if not os.environ.get(name))
    if unset_pins:
        raise ValueError("required experiment pins are unset: " + ", ".join(unset_pins))
    cells: list[dict[str, Any]] = []
    for model in core_models:
        model_id = _required_env(model["core_model_id_env"])
        for harness in matrix["core_harnesses"]:
            pair = (model["model_key"], harness["harness_key"])
            cells.append(
                _cell(
                    model=model,
                    harness=harness,
                    model_id=model_id,
                    design="balanced_core",
                    native_pairing=pair in native_core_pairings,
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
                native_pairing=True,
            )
        )
    if len(cells) != expected_unique_cells:
        raise ValueError(
            f"expected the approved {expected_unique_cells}-cell design, "
            f"found {len(cells)} cells"
        )
    cell_keys = {(cell["model_key"], cell["harness_key"]) for cell in cells}
    if len(cell_keys) != len(cells):
        raise ValueError("the matrix contains duplicate model × harness executions")
    output_root = Path(args.out)
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite matrix plan: {output_root}")
    config_root = output_root / "jobs"
    config_root.mkdir(parents=True)
    attempts = matrix[f"{args.mode}_attempts"]
    commands: list[str] = []
    for cell in cells:
        job_name = f"libgen-{args.mode}-{cell['model_key']}-{cell['harness_key']}"
        agent_config = {
            "name": cell["harbor_agent"],
            "model_name": cell["model_id"],
            "kwargs": cell["agent_kwargs"],
            "skills": [],
            "mcp_servers": [],
            "include_logs": [],
            "exclude_logs": [],
        }
        if cell.get("provider_base_url"):
            agent_config["env"] = {"OPENAI_BASE_URL": cell["provider_base_url"]}
        config = {
            "job_name": job_name,
            "jobs_dir": str(Path(args.jobs_dir).resolve()),
            "n_attempts": attempts,
            "n_concurrent_trials": matrix["n_concurrent_trials"],
            "retry": {"max_retries": 0},
            "environment": {"type": matrix["environment"]},
            "agents": [agent_config],
            "datasets": [
                {
                    "path": str(tasks_root),
                    "task_names": selected if args.mode != "full" else None,
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
        "network_policy": network_policy,
        "network_policy_sha256": network_policy_sha256,
        "network_smoke_report": network_smoke_report,
        "network_smoke_required_before_execution": True,
        "attempts": attempts,
        "protocol_ids": selected,
        "task_bundle_sha256": task_digest,
        "pricing_snapshot": pricing_snapshot,
        "pricing_snapshot_sha256": hashlib.sha256(pricing_bytes).hexdigest(),
        "cells": cells,
        "expected_trial_count": len(cells) * len(selected) * attempts,
        "analysis_design": {
            "unique_execution_cells": len(cells),
            "balanced_core_cells": len(core_models) * len(matrix["core_harnesses"]),
            "native_extension_cells": len(matrix["native_extensions"]),
            "native_only_cells": len(matrix["native_extensions"]),
            "native_descriptive_pairings": sum(
                cell["native_pairing"] for cell in cells
            ),
            "overlapping_core_native_pairings": len(native_core_pairings),
            "model_and_harness_effects_use": "balanced_core",
            "native_pairings_are_reported_separately": True,
            "overlapping_pairings_execute_once": True,
        },
        "error_analysis_policy": {
            "preserve_all_agent_logs": True,
            "preserve_all_verifier_diagnostics": True,
            "automatic_process_attribution": False,
            "pilot_substantive_mismatches_require_adjudication": True,
            "full_run_requires_refrozen_benchmark": True,
        },
        "telemetry_policy": {
            "automatic_retries": 0,
            "resume_command": "libstruct-resume-libgen-job",
            "preserve_superseded_executions": True,
            "agent_exception_does_not_imply_invalid_prediction": True,
        },
        "primary_aggregation_policy": {
            "population": "all_scheduled_attempts",
            "valid_prediction": "normal_score",
            "invalid_prediction": "zero",
            "incomplete_output": "zero",
            "agent_timeout": "zero",
            "agent_timeout_rerun_eligible": False,
            "infrastructure_provider_rerun": (
                "eligible_only_with_documented_confirmation"
            ),
            "valid_output_only_performance_is_diagnostic": True,
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
    *,
    model: dict[str, Any],
    harness: dict[str, Any],
    model_id: str,
    design: str,
    native_pairing: bool,
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
    required_version = harness.get("required_version")
    if required_version and version != required_version:
        raise ValueError(
            f"{harness['display_name']} must be pinned to {required_version}; "
            f"found {version!r} in {harness['version_env']}"
        )
    kwargs = dict(harness.get("agent_kwargs", {}))
    kwargs["version"] = version
    result = {
        "design": design,
        "native_pairing": native_pairing,
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
    if base_url_env := harness.get("base_url_env"):
        base_url = _required_env(base_url_env)
        required_base_url = harness.get("required_base_url")
        if required_base_url and base_url.rstrip("/") != required_base_url.rstrip("/"):
            raise ValueError(
                f"{harness['display_name']} must use {required_base_url}; "
                f"found {base_url!r} in {base_url_env}"
            )
        result["provider_base_url"] = base_url.rstrip("/")
    return result


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"required experiment pin is unset: {name}")
    return value


def _validate_network_smoke_report(
    report: dict[str, Any], network_policy_sha256: str
) -> None:
    if report.get("ready") is not True:
        raise ValueError("Docker network smoke report is not ready")
    if report.get("network_policy_sha256") != network_policy_sha256:
        raise ValueError("Docker network smoke report covers a different policy")
    probe = report.get("probe")
    if not isinstance(probe, dict):
        raise ValueError("Docker network smoke report lacks probe results")
    expected_reachability = {
        "provider_api_access": True,
        "qwen_api_access": True,
        "unrelated_public_web_access": False,
        "direct_external_access": False,
    }
    for probe_name, expected in expected_reachability.items():
        result = probe.get(probe_name)
        if not isinstance(result, dict) or result.get("reachable") is not expected:
            raise ValueError(
                f"Docker network smoke report failed {probe_name}: {result!r}"
            )


def _harbor_version() -> str:
    result = subprocess.run(
        ["harbor", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _canonical_json_sha256(document: Any) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
