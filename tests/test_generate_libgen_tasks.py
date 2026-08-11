from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from libstruct_bench.cli.generate_libgen_tasks import main
from libstruct_bench.cli.plan_libgen_matrix import main as plan_main
from libstruct_bench.cli.prepare_libgen_hf_export import main as export_main
from libstruct_bench.cli.summarize_libgen_runs import main as summarize_main
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
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
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
        test_sh = (task / "tests/test.sh").read_text()
        assert 'network_mode = "allowlist"' in task_toml
        assert 'environment_mode = "separate"' in task_toml
        assert 'HF_TOKEN = "${HF_TOKEN}"' in task_toml
        assert "RUN python /workspace/fetch_input.py" in dockerfile
        assert "t2_prediction.json" in task_toml
        assert "t3_prediction.json" in task_toml
        assert "groundtruth" not in instruction.lower()
        assert "groundtruth" not in manifest.lower()
        assert "org/private-groundtruth" not in dockerfile
        assert "--groundtruth-repo \"org/private-groundtruth\"" in test_sh
        assert (task / "environment/schemas/benchmark/oligo_prediction.schema.json").is_file()
        assert (task / "tests/libstruct_bench/libgen/scoring.py").is_file()


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
        assert not (out / "protocol_sources/example_protocol/groundtruth_oligos.json").exists()
        assert (out / "groundtruth/example_protocol/groundtruth_oligos.json").is_file()


def test_matrix_planner_creates_15_cells_and_60_trial_pilot(monkeypatch: pytest.MonkeyPatch) -> None:
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
            "LIBGEN_CORE_MODEL_GEMINI_3_6_FLASH",
            "LIBGEN_NATIVE_MODEL_GEMINI_3_6_FLASH",
            "LIBGEN_CORE_MODEL_KIMI_K3",
            "LIBGEN_KIMI_CLI_VERSION",
            "LIBGEN_MINI_SWE_AGENT_VERSION",
            "LIBGEN_PI_VERSION",
            "LIBGEN_CODEX_VERSION",
            "LIBGEN_CLAUDE_CODE_VERSION",
            "LIBGEN_GEMINI_CLI_VERSION",
        ]
        for name in env_names:
            value = (
                f"test-provider/{name.lower()}"
                if "MODEL" in name
                else f"pinned-{name.lower()}"
            )
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
        assert len(lock["cells"]) == 15
        assert lock["expected_trial_count"] == 60
        assert sum(item["design"] == "balanced_core" for item in lock["cells"]) == 12
        assert len(list((out / "jobs").glob("*.json"))) == 15
        first = json.loads(next((out / "jobs").glob("*.json")).read_text())
        assert first["environment"]["type"] == "e2b"
        assert first["agents"][0]["skills"] == []
        assert first["agents"][0]["mcp_servers"] == []
        assert first["agents"][0]["include_logs"] == []
        assert first["agents"][0]["exclude_logs"] == []
        assert len(lock["task_bundle_sha256"]) == 64
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
                    "expected_trial_count": 60,
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
        assert full_lock["expected_trial_count"] == 600
        assert full_lock["pilot_clearance"]["full_run_ready"] is True


def test_run_summarizer_keeps_core_and_native_estimands_separate(tmp_path: Path) -> None:
    lock = {
        "mode": "pilot",
        "expected_trial_count": 2,
        "cells": [
            {
                "design": "balanced_core",
                "model_key": "model_a",
                "model_id": "provider/model-a",
                "harness_key": "harness_a",
                "harness_version": "1.0",
            },
            {
                "design": "native_extension",
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
                    "verifier_result": {"rewards": {"reward": reward, "t2_score": reward}},
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
    assert summary["native_extensions"]["cell_mean_reward"]["model_a__native_a"] == 0.9
