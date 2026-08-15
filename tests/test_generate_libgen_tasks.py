from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import tomllib
from pathlib import Path

import pytest

from libstruct_bench.cli.generate_libgen_tasks import main
from libstruct_bench.cli.plan_libgen_matrix import main as plan_main
from libstruct_bench.cli.prepare_libgen_hf_export import main as export_main
from libstruct_bench.cli.resume_libgen_job import main as resume_main
from libstruct_bench.cli.summarize_libgen_runs import main as summarize_main
from libstruct_bench.libgen.telemetry import normalized_api_cost
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION
from tests.libgen_fixtures import t1_groundtruth, t2_groundtruth, t3_groundtruth


ROOT = Path(__file__).resolve().parents[1]


def _fixture_release(root: Path) -> tuple[Path, Path, Path]:
    source_root = root / "sources"
    truth_root = root / "truth"
    source = source_root / "example_protocol" / "source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"primary protocol fixture")
    truth_dir = truth_root / "example_protocol"
    truth_dir.mkdir(parents=True)
    for filename, document in (
        ("groundtruth_final_lib_struct.json", t1_groundtruth()),
        ("groundtruth_oligos.json", t2_groundtruth()),
        ("groundtruth_library_generation_workflow.json", t3_groundtruth()),
    ):
        (truth_dir / filename).write_text(json.dumps(document), encoding="utf-8")
    protocols = root / "protocols.json"
    protocols.write_text(
        json.dumps(
            {
                "protocols": [
                    {
                        "protocol_id": "example_protocol",
                        "display_name": "Example protocol",
                        "sources": [
                            {
                                "path": "example_protocol/source.pdf",
                                "sha256": hashlib.sha256(
                                    source.read_bytes()
                                ).hexdigest(),
                            }
                        ],
                        "groundtruth_prefix": "example_protocol",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "rules.md").write_text((ROOT / "benchmarks/libgen/rules.md").read_text())
    return protocols, source_root, truth_root


def test_generator_builds_separate_allowlisted_task_without_truth_leakage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        out = root / "tasks"
        assert (
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(out),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/public-protocols",
                    "--input-revision",
                    "a" * 40,
                    "--groundtruth-repo",
                    "org/private-groundtruth",
                    "--groundtruth-revision",
                    "b" * 40,
                ]
            )
            == 0
        )
        task = out / "example_protocol"
        task_toml = (task / "task.toml").read_text()
        dockerfile = (task / "environment/Dockerfile").read_text()
        instruction = (task / "instruction.md").read_text()
        manifest = (task / "input_manifest.json").read_text()
        rules = (task / "rules.md").read_text()
        test_sh = (task / "tests/test.sh").read_text()
        assert 'network_mode = "allowlist"' in task_toml
        assert 'network_profile = "allowlist"' in task_toml
        assert 'environment_mode = "separate"' in task_toml
        assert 'HF_TOKEN = "${HF_TOKEN}"' in task_toml
        assert "RUN python /workspace/fetch_input.py" in dockerfile
        task_config = tomllib.loads(task_toml)
        assert task_config["agent"]["timeout_sec"] == 3600.0
        assert task_config["metadata"]["benchmark_version"] == LIBGEN_BENCHMARK_VERSION
        assert task_config["artifacts"] == [
            {
                "source": "/logs/agent/trajectory.json",
                "destination": "agent_trajectory.json",
            }
        ]
        assert "/logs/artifacts/t2_prediction.json" in rules
        assert "/logs/artifacts/t3_prediction.json" in rules
        assert "one record per oligo family" in rules
        assert "one family template" in instruction
        assert "groundtruth" not in instruction.lower()
        assert "groundtruth" not in manifest.lower()
        assert "org/private-groundtruth" not in dockerfile
        assert '--groundtruth-repo "org/private-groundtruth"' in test_sh
        assert "--error-analysis-out /logs/verifier/error_analysis.json" in test_sh
        assert "--trajectory /logs/agent/trajectory.json" in test_sh
        assert (
            task / "environment/schemas/benchmark/oligo_prediction.schema.json"
        ).is_file()
        assert (task / "tests/libstruct_bench/libgen/scoring.py").is_file()
        assert (task / "tests/libstruct_bench/libgen/error_analysis.py").is_file()


def test_generator_builds_local_docker_task_without_phase_network_overrides() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        out = root / "tasks"
        assert (
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(out),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/public-protocols",
                    "--input-revision",
                    "a" * 40,
                    "--groundtruth-repo",
                    "org/private-groundtruth",
                    "--groundtruth-revision",
                    "b" * 40,
                    "--network-profile",
                    "local-docker",
                ]
            )
            == 0
        )
        task_config = tomllib.loads((out / "example_protocol/task.toml").read_text())
        assert task_config["agent"]["timeout_sec"] == 3600.0
        assert task_config["metadata"]["benchmark_version"] == LIBGEN_BENCHMARK_VERSION
        assert task_config["artifacts"] == [
            {
                "source": "/logs/agent/trajectory.json",
                "destination": "agent_trajectory.json",
            }
        ]
        assert task_config["metadata"]["network_profile"] == "local-docker"
        assert "network_mode" not in task_config["agent"]
        assert "allowed_hosts" not in task_config["agent"]
        assert task_config["verifier"]["environment_mode"] == "separate"
        assert "network_mode" not in task_config["verifier"]
        assert "allowed_hosts" not in task_config["verifier"]
        assert task_config["environment"]["network_mode"] == "public"
        assert task_config["verifier"]["environment"]["network_mode"] == "public"
        assert (
            task_config["verifier"]["environment"]["env"]["HF_TOKEN"] == "${HF_TOKEN}"
        )


def test_checked_in_libgen_tasks_keep_verifier_snapshots_synchronized() -> None:
    protocol_config = json.loads(
        (ROOT / "benchmarks/libgen/protocols.json").read_text()
    )
    protocol_ids = {item["protocol_id"] for item in protocol_config["protocols"]}
    task_root = ROOT / "benchmarks/libgen/tasks"
    task_dirs = {path.name: path for path in task_root.iterdir() if path.is_dir()}
    assert set(task_dirs) == protocol_ids

    source_package = ROOT / "src/libstruct_bench"
    synchronized_files = (
        "audit/groundtruth.py",
        "cli/grade_libgen.py",
        "libgen/error_analysis.py",
        "libgen/scoring.py",
        "libgen/validation.py",
        "libgen/version.py",
        "normalization.py",
    )
    synchronized_schemas = (
        "analysis/libgen_error_analysis.schema.json",
        "benchmark/library_generation_workflow_prediction.schema.json",
        "benchmark/oligo_prediction.schema.json",
        "groundtruth/library_generation_workflow.schema.json",
    )

    for protocol_id, task in sorted(task_dirs.items()):
        task_config = tomllib.loads((task / "task.toml").read_text())
        assert task_config["agent"]["timeout_sec"] == 3600.0, protocol_id
        assert (
            task_config["metadata"]["benchmark_version"] == LIBGEN_BENCHMARK_VERSION
        ), protocol_id
        assert task_config["artifacts"] == [
            {
                "source": "/logs/agent/trajectory.json",
                "destination": "agent_trajectory.json",
            }
        ], protocol_id

        test_sh = (task / "tests/test.sh").read_text()
        assert "--error-analysis-out /logs/verifier/error_analysis.json" in test_sh
        assert "--trajectory /logs/agent/trajectory.json" in test_sh

        for package_copy in (
            task / "environment/libstruct_bench",
            task / "tests/libstruct_bench",
        ):
            for relative in synchronized_files:
                assert (package_copy / relative).read_bytes() == (
                    source_package / relative
                ).read_bytes(), f"{protocol_id}: stale {package_copy / relative}"

        for schema_copy in (task / "environment/schemas", task / "tests/schemas"):
            for schema_relative in synchronized_schemas:
                assert (schema_copy / schema_relative).read_bytes() == (
                    ROOT / "schemas" / schema_relative
                ).read_bytes(), f"{protocol_id}: stale {schema_copy / schema_relative}"
        assert (task / "rules.md").read_bytes() == (
            ROOT / "benchmarks/libgen/rules.md"
        ).read_bytes(), f"{protocol_id}: stale rules.md"
        assert (task / "environment/rules.md").read_bytes() == (
            ROOT / "benchmarks/libgen/rules.md"
        ).read_bytes(), f"{protocol_id}: stale environment/rules.md"


def test_generator_refuses_mutable_revisions_and_mixed_source_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        with pytest.raises(ValueError, match="immutable"):
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(root / "tasks"),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/source",
                    "--input-revision",
                    "main",
                    "--groundtruth-repo",
                    "org/truth",
                    "--groundtruth-revision",
                    "b" * 40,
                ]
            )

        leaked = source_root / "example_protocol/groundtruth_oligos.json"
        leaked.write_text("{}")
        with pytest.raises(ValueError, match="split export"):
            main(
                [
                    "--protocols",
                    str(protocols),
                    "--out",
                    str(root / "tasks"),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--input-repo",
                    "org/source",
                    "--input-revision",
                    "a" * 40,
                    "--groundtruth-repo",
                    "org/truth",
                    "--groundtruth-revision",
                    "b" * 40,
                ]
            )


def test_split_export_copies_only_manifest_sources_and_canonical_truth() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        protocols, source_root, truth_root = _fixture_release(root)
        (source_root / "example_protocol/legacy.html").write_text("legacy")
        (source_root / "example_protocol/groundtruth_oligos.json").write_text("{}")
        out = root / "export"
        assert (
            export_main(
                [
                    "--protocols",
                    str(protocols),
                    "--source-root",
                    str(source_root),
                    "--groundtruth-root",
                    str(truth_root),
                    "--schema-root",
                    str(ROOT / "schemas"),
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        assert (out / "protocol_sources/example_protocol/source.pdf").is_file()
        assert not (out / "protocol_sources/example_protocol/legacy.html").exists()
        assert not (
            out / "protocol_sources/example_protocol/groundtruth_oligos.json"
        ).exists()
        assert (out / "groundtruth/example_protocol/groundtruth_oligos.json").is_file()


def test_matrix_planner_creates_16_unique_cells_and_64_trial_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tasks = root / "tasks"
        protocols = json.loads((ROOT / "benchmarks/libgen/protocols.json").read_text())
        for protocol in protocols["protocols"]:
            task = tasks / protocol["protocol_id"]
            task.mkdir(parents=True)
            (task / "task.toml").write_text('schema_version = "1.3"\n')
        env_names = [
            "LIBGEN_CORE_MODEL_GPT_5_6_SOL",
            "LIBGEN_NATIVE_MODEL_GPT_5_6_SOL",
            "LIBGEN_CORE_MODEL_CLAUDE_OPUS_5",
            "LIBGEN_NATIVE_MODEL_CLAUDE_OPUS_5",
            "LIBGEN_CORE_MODEL_GEMINI_3_7_FLASH",
            "LIBGEN_NATIVE_MODEL_GEMINI_3_7_FLASH",
            "LIBGEN_CORE_MODEL_KIMI_K3",
            "LIBGEN_NATIVE_MODEL_QWEN_3_8_MAX",
            "LIBGEN_KIMI_CLI_VERSION",
            "LIBGEN_MINI_SWE_AGENT_VERSION",
            "LIBGEN_PI_VERSION",
            "LIBGEN_CODEX_VERSION",
            "LIBGEN_CLAUDE_CODE_VERSION",
            "LIBGEN_GEMINI_CLI_VERSION",
            "LIBGEN_QWEN_CODE_VERSION",
        ]
        for name in env_names:
            value = (
                f"test-provider/{name.lower()}"
                if "MODEL" in name
                else f"pinned-{name.lower()}"
            )
            if name == "LIBGEN_QWEN_CODE_VERSION":
                value = "0.21.12"
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(
            "libstruct_bench.cli.plan_libgen_matrix._harbor_version", lambda: "9.9.9"
        )
        out = root / "plan"
        assert (
            plan_main(
                [
                    "--matrix",
                    str(ROOT / "benchmarks/libgen/matrix.json"),
                    "--tasks",
                    str(tasks),
                    "--mode",
                    "pilot",
                    "--out",
                    str(out),
                    "--harbor-version",
                    "9.9.9",
                ]
            )
            == 0
        )
        lock = json.loads((out / "experiment_lock.json").read_text())
        assert len(lock["cells"]) == 16
        assert lock["benchmark_version"] == LIBGEN_BENCHMARK_VERSION
        assert lock["expected_trial_count"] == 64
        assert sum(item["design"] == "balanced_core" for item in lock["cells"]) == 12
        assert sum(item["native_pairing"] for item in lock["cells"]) == 5
        assert len(list((out / "jobs").glob("*.json"))) == 16
        kimi_native = [
            item
            for item in lock["cells"]
            if item["model_key"] == "kimi_k3" and item["harness_key"] == "kimi_code"
        ]
        assert len(kimi_native) == 1
        assert kimi_native[0]["design"] == "balanced_core"
        assert kimi_native[0]["native_pairing"] is True
        qwen_native = [
            item
            for item in lock["cells"]
            if item["model_key"] == "qwen_3_8_max"
            and item["harness_key"] == "qwen_code"
        ]
        assert len(qwen_native) == 1
        assert qwen_native[0]["design"] == "native_extension"
        assert qwen_native[0]["harbor_agent"] == "qwen-coder"
        assert qwen_native[0]["harness_version"] == "0.21.12"
        assert lock["analysis_design"] == {
            "balanced_core_cells": 12,
            "model_and_harness_effects_use": "balanced_core",
            "native_descriptive_pairings": 5,
            "native_extension_cells": 4,
            "native_only_cells": 4,
            "native_pairings_are_reported_separately": True,
            "overlapping_core_native_pairings": 1,
            "overlapping_pairings_execute_once": True,
            "unique_execution_cells": 16,
        }
        first = json.loads(next((out / "jobs").glob("*.json")).read_text())
        assert first["environment"]["type"] == "e2b"
        assert first["agents"][0]["skills"] == []
        assert first["agents"][0]["mcp_servers"] == []
        assert first["agents"][0]["include_logs"] == []
        assert first["agents"][0]["exclude_logs"] == []
        assert first["retry"]["max_retries"] == 0
        assert "/logs/verifier/error_analysis.json" in first["artifacts"]
        assert len(lock["task_bundle_sha256"]) == 64
        assert len(lock["pricing_snapshot_sha256"]) == 64
        assert lock["pricing_snapshot"]["snapshot_id"].startswith("libgen-api-pricing-")
        assert lock["error_analysis_policy"]["automatic_process_attribution"] is False

        with pytest.raises(ValueError, match="pilot-clearance"):
            plan_main(
                [
                    "--matrix",
                    str(ROOT / "benchmarks/libgen/matrix.json"),
                    "--tasks",
                    str(tasks),
                    "--mode",
                    "full",
                    "--out",
                    str(root / "full-without-clearance"),
                    "--harbor-version",
                    "9.9.9",
                ]
            )

        clearance = root / "pilot_review_status.json"
        clearance.write_text(
            json.dumps(
                {
                    "full_run_ready": True,
                    "expected_trial_count": 64,
                    "task_bundle_sha256": lock["task_bundle_sha256"],
                }
            ),
            encoding="utf-8",
        )
        full_out = root / "full-plan"
        assert (
            plan_main(
                [
                    "--matrix",
                    str(ROOT / "benchmarks/libgen/matrix.json"),
                    "--tasks",
                    str(tasks),
                    "--mode",
                    "full",
                    "--out",
                    str(full_out),
                    "--harbor-version",
                    "9.9.9",
                    "--pilot-clearance",
                    str(clearance),
                ]
            )
            == 0
        )
        full_lock = json.loads((full_out / "experiment_lock.json").read_text())
        assert full_lock["expected_trial_count"] == 640
        assert full_lock["pilot_clearance"]["full_run_ready"] is True

        smoke_out = root / "smoke-plan"
        assert (
            plan_main(
                [
                    "--matrix",
                    str(ROOT / "benchmarks/libgen/matrix.json"),
                    "--tasks",
                    str(tasks),
                    "--mode",
                    "smoke",
                    "--out",
                    str(smoke_out),
                    "--harbor-version",
                    "9.9.9",
                ]
            )
            == 0
        )
        smoke_lock = json.loads((smoke_out / "experiment_lock.json").read_text())
        assert smoke_lock["protocol_ids"] == ["s3_atac"]
        assert smoke_lock["expected_trial_count"] == 16
        smoke_config = json.loads(next((smoke_out / "jobs").glob("*.json")).read_text())
        assert smoke_config["datasets"][0]["task_names"] == ["s3_atac"]


def test_run_summarizer_keeps_core_and_native_estimands_separate(
    tmp_path: Path,
) -> None:
    lock = {
        "mode": "pilot",
        "expected_trial_count": 2,
        "cells": [
            {
                "design": "balanced_core",
                "native_pairing": True,
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "harness_a",
                "harness_version": "1.0",
            },
            {
                "design": "native_extension",
                "native_pairing": True,
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "native_a",
                "harness_version": "2.0",
            },
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    runs = tmp_path / "runs"
    for harness, reward in (("harness_a", 0.75), ("native_a", 0.9)):
        trial = runs / f"libgen-pilot-model_a-{harness}" / "example_protocol__abc"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "trial_name": trial.name,
                    "config": {"task": {"path": "tasks/example_protocol"}},
                    "agent_result": {"n_input_tokens": 10, "n_output_tokens": 2},
                    "verifier_result": {
                        "rewards": {
                            "reward": reward,
                            "t2_required_family_f1": reward,
                        }
                    },
                    "exception_info": None,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "finished_at": "2026-01-01T00:01:00+00:00",
                }
            )
        )
    output = tmp_path / "summary"
    assert (
        summarize_main(
            [
                "--runs-root",
                str(runs),
                "--experiment-lock",
                str(lock_path),
                "--out",
                str(output),
            ]
        )
        == 0
    )
    summary = json.loads((output / "summary.json").read_text())
    assert summary["balanced_core"]["grand_mean_reward"] == 0.75
    assert summary["native_pairings"]["cell_mean_reward"] == {
        "model_a__harness_a": 0.75,
        "model_a__native_a": 0.9,
    }
    assert summary["native_extensions"]["cell_mean_reward"] == {
        "model_a__native_a": 0.9
    }


def test_telemetry_keeps_timeout_usage_and_does_not_invalidate_prediction(
    tmp_path: Path,
) -> None:
    pricing = {
        "schema_version": 1,
        "snapshot_id": "fixture-pricing",
        "as_of": "2026-01-01",
        "unit_tokens": 1_000_000,
        "models": {
            "model_a": {
                "source_url": "https://provider.example/pricing",
                "source_retrieved_at": "2026-01-01",
                "rates_per_million_tokens": {
                    "uncached_input": 1.0,
                    "cached_input": 0.1,
                    "cache_creation_5m": 1.25,
                    "output": 2.0,
                },
            }
        },
    }
    lock = {
        "mode": "smoke",
        "attempts": 1,
        "protocol_ids": ["example_protocol"],
        "expected_trial_count": 1,
        "pricing_snapshot": pricing,
        "telemetry_policy": {"automatic_retries": 0},
        "cells": [
            {
                "design": "balanced_core",
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "codex",
                "harbor_agent": "codex",
                "harness_version": "1.0",
            }
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    trial = tmp_path / "runs/libgen-smoke-model_a-codex/example_protocol__timeout"
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "config": {"task": {"path": "tasks/example_protocol"}},
                "agent_result": {
                    "n_input_tokens": 100,
                    "n_cache_tokens": 40,
                    "n_output_tokens": 10,
                    "cost_usd": 0.0123,
                },
                "verifier_result": {
                    "rewards": {
                        "reward": 0.7,
                        "t3_molecular_transition_f1": 0.8,
                    }
                },
                "exception_info": {"exception_type": "AgentTimeoutError"},
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:01:10Z",
                "agent_execution": {
                    "started_at": "2026-01-01T00:00:05Z",
                    "finished_at": "2026-01-01T00:01:05Z",
                },
                "verifier": {
                    "started_at": "2026-01-01T00:01:05Z",
                    "finished_at": "2026-01-01T00:01:10Z",
                },
            }
        )
    )
    (trial / "verifier/details.json").write_text(json.dumps({"prediction_valid": True}))
    (trial / "agent/trajectory.json").write_text(
        json.dumps(
            {
                "schema_version": "ATIF-v1.7",
                "steps": [
                    {
                        "source": "agent",
                        "metrics": {
                            "prompt_tokens": 100,
                            "completion_tokens": 10,
                            "cached_tokens": 40,
                            "extra": {
                                "reasoning_output_tokens": 4,
                                "cache_creation_input_tokens": 20,
                            },
                        },
                    }
                ],
                "final_metrics": {
                    "total_prompt_tokens": 100,
                    "total_completion_tokens": 10,
                    "total_cached_tokens": 40,
                    "total_cost_usd": 0.0123,
                    "extra": {"reasoning_output_tokens": 4},
                },
            }
        )
    )
    out = tmp_path / "out"
    assert (
        summarize_main(
            [
                "--runs-root",
                str(tmp_path / "runs"),
                "--experiment-lock",
                str(lock_path),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    with (out / "trials.csv").open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["exception_type"] == "AgentTimeoutError"
    assert row["prediction_valid"] == "True"
    assert row["verifier_completed"] == "True"
    assert row["valid_completion"] == "True"
    assert row["agent_duration_seconds"] == "60.0"
    assert row["verifier_duration_seconds"] == "5.0"
    assert row["input_tokens"] == "100"
    assert row["reasoning_tokens"] == "4"
    assert row["cache_creation_tokens"] == "20"
    assert row["reported_cost_usd"] == "0.0123"
    assert row["reported_cost_kind"] == "estimated"
    assert float(row["normalized_api_cost_usd"]) == pytest.approx(89 / 1_000_000)
    assert json.loads(row["telemetry_missing_fields_json"]) == []
    summary = json.loads((out / "summary.json").read_text())
    assert summary["agent_exception_count"] == 1
    assert summary["valid_completion_rate"] == 1.0
    assert summary["scored_current_trial_count"] == 1


def test_resume_wrapper_preserves_superseded_timeout_execution(tmp_path: Path) -> None:
    job = tmp_path / "runs/libgen-smoke-model_a-codex"
    trial = job / "example_protocol__failed"
    trial.mkdir(parents=True)
    (job / "config.json").write_text(json.dumps({"retry": {"max_retries": 0}}))
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "config": {"task": {"path": "tasks/example_protocol"}},
                "agent_result": {
                    "n_input_tokens": 123,
                    "n_output_tokens": 9,
                    "cost_usd": 0.4,
                },
                "exception_info": {"exception_type": "AgentTimeoutError"},
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:01:00Z",
            }
        )
    )
    assert (
        resume_main(
            [
                "-p",
                str(job),
                "-f",
                "AgentTimeoutError",
                "--snapshot-only",
            ]
        )
        == 0
    )
    snapshots = list(job.glob(".libgen_telemetry/resume_snapshots/*/*/result.json"))
    assert len(snapshots) == 1
    assert json.loads(snapshots[0].read_text())["agent_result"]["n_input_tokens"] == 123

    pricing = {
        "snapshot_id": "fixture",
        "as_of": "2026-01-01",
        "unit_tokens": 1_000_000,
        "models": {
            "model_a": {
                "source_url": "https://provider.example/pricing",
                "source_retrieved_at": "2026-01-01",
                "rates_per_million_tokens": {
                    "uncached_input": 1.0,
                    "cached_input": 0.1,
                    "output": 2.0,
                },
            }
        },
    }
    lock = {
        "mode": "smoke",
        "attempts": 1,
        "protocol_ids": ["example_protocol"],
        "expected_trial_count": 1,
        "pricing_snapshot": pricing,
        "cells": [
            {
                "design": "balanced_core",
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "codex",
                "harbor_agent": "codex",
                "harness_version": "1.0",
            }
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    out = tmp_path / "summary"
    assert (
        summarize_main(
            [
                "--runs-root",
                str(tmp_path / "runs"),
                "--experiment-lock",
                str(lock_path),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    with (out / "trials.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    preserved = next(row for row in rows if row["is_current_execution"] == "False")
    assert preserved["superseded_by_resume"] == "True"
    assert preserved["input_tokens"] == "123"
    assert preserved["reported_cost_usd"] == "0.4"
    assert preserved["resume_count"] == "1"


def test_api_cost_normalization_applies_per_call_long_context_and_cache_ttl() -> None:
    snapshot = {
        "unit_tokens": 1_000_000,
        "models": {
            "m": {
                "rates_per_million_tokens": {
                    "uncached_input": 2.0,
                    "cached_input": 0.2,
                    "cache_creation_5m": 2.5,
                    "cache_creation_1h": 4.0,
                    "output": 10.0,
                },
                "long_context": {
                    "threshold_input_tokens_exclusive": 100,
                    "input_multiplier": 2.0,
                    "output_multiplier": 1.5,
                },
            }
        },
    }
    result = normalized_api_cost(
        snapshot,
        "m",
        [
            {
                "prompt_tokens": 110,
                "completion_tokens": 10,
                "cached_tokens": 20,
                "cache_creation_tokens": 30,
                "cache_creation_5m_tokens": 10,
                "cache_creation_1h_tokens": 20,
            }
        ],
        {},
    )
    # Input: 60*2 + 20*.2 + 10*2.5 + 20*4 = 229, then 2x.
    # Output: 10*10 = 100, then 1.5x.
    assert result["cost_usd"] == pytest.approx(608 / 1_000_000)
    assert result["precision"] == "per_api_call"


def test_api_cost_normalization_does_not_invent_a_missing_rate() -> None:
    result = normalized_api_cost(
        {
            "unit_tokens": 1_000_000,
            "models": {
                "qwen": {
                    "pricing_status": "per_token_usd_rate_unavailable",
                    "source_url": "https://provider.example/token-plan",
                }
            },
        },
        "qwen",
        [],
        {"prompt_tokens": 100, "completion_tokens": 20},
    )

    assert result == {
        "cost_usd": None,
        "precision": "pricing_rate_unavailable",
    }
