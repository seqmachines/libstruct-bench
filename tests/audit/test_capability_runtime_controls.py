from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from libstruct_bench.audit.artifacts import sha256_file, write_json_atomic
from libstruct_bench.cli import libgen_capability_improvement as capability_cli
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    copy_capability_pack,
    load_capability_control_bundle,
    thaw_tree,
    validate_capability_pack,
    with_digest,
)
from libstruct_bench.improvement.harbor import (
    CAPABILITY_MOUNT_TARGET,
    EXEMPLAR_DIAGNOSTIC_ARTIFACTS,
    EXEMPLAR_MEMORY_MOUNT_TARGET,
    build_harbor_job_config,
    prepare_capability_harbor_integration,
    validate_capability_harbor_integration,
)
from libstruct_bench.improvement import workflow
import libstruct_bench.improvement.isolation as isolation_module
from libstruct_bench.improvement.isolation import (
    _resolve_parent_exemplar_memory,
    prepare_isolated_worker_workspace,
    validate_isolated_worker_workspace,
)
from tests.audit.capability_memory_fixtures import portable_exemplar_memory


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPO_ROOT / "improvement" / "capability_pack"


def _portable_checkpoint(
    tmp_path: Path,
    checkpoint_id: str = "C5",
    *,
    exemplar_max_results: int = 3,
) -> Path:
    checkpoint_root = tmp_path / checkpoint_id
    count = int(checkpoint_id[1:])
    pack = validate_capability_pack(PACK_ROOT)
    copy_capability_pack(PACK_ROOT, checkpoint_root / "pack", freeze=True)
    memory = portable_exemplar_memory(checkpoint_root / "memory", count)
    runtime = workflow._build_checkpoint_runtime(  # noqa: SLF001
        checkpoint_id=checkpoint_id,
        pack_digest=pack["pack_digest"],
        exemplar_memory=memory,
        exemplar_max_results=exemplar_max_results,
    )
    runtime_path = checkpoint_root / "runtime.json"
    write_json_atomic(runtime_path, runtime, mode=0o444)
    checkpoint = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_checkpoint.v1",
            "checkpoint_id": checkpoint_id,
            "experiment_digest": "0" * 64,
            "branch": "cumulative",
            "protocol_count": count,
            "batch_id": None if count == 0 else f"B{count // 5}",
            "parent_checkpoint_id": None if count == 0 else f"C{count - 5}",
            "validation_guidance": (
                None
                if count == 0
                else {
                    "checkpoint_label": f"C{count - 5}",
                    "aggregate_digest": "4" * 64,
                    "aggregate_sha256": "5" * 64,
                    "workspace_digest": "6" * 64,
                    "workspace_manifest_sha256": "7" * 64,
                }
            ),
            "pack_digest": pack["pack_digest"],
            "pack_manifest_sha256": sha256_file(
                checkpoint_root / "pack" / "manifest.json"
            ),
            "exemplar_memory": memory,
            "runtime_manifest_sha256": sha256_file(runtime_path),
            "proposal_sha256": None if count == 0 else "1" * 64,
            "decision_sha256": None if count == 0 else "2" * 64,
            "application_sha256": None if count == 0 else "3" * 64,
            "status": "baseline" if count == 0 else "procedural_and_exemplar",
            "frozen": True,
            "created_at": "2026-08-21T00:00:00Z",
        },
        "checkpoint_digest",
    )
    write_json_atomic(checkpoint_root / "checkpoint.json", checkpoint, mode=0o444)
    return checkpoint_root


def test_work_record_compiler_and_unified_audit_are_deterministic(
    tmp_path: Path,
) -> None:
    validate_capability_pack(PACK_ROOT)
    record = PACK_ROOT / "synthetic_tests" / "valid" / "work_record.json"
    t2 = tmp_path / "t2.json"
    t3 = tmp_path / "t3.json"
    command = [
        "python3",
        str(PACK_ROOT / "tools" / "compile_work_record.py"),
        "--work-record",
        str(record),
        "--t2-out",
        str(t2),
        "--t3-out",
        str(t3),
    ]
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    first_t2 = t2.read_bytes()
    first_t3 = t3.read_bytes()
    second = subprocess.run(command, check=False, capture_output=True, text=True)
    assert first.returncode == second.returncode == 0
    assert json.loads(first.stdout)["outputs"] == json.loads(second.stdout)["outputs"]
    assert t2.read_bytes() == first_t2
    assert t3.read_bytes() == first_t3
    assert json.loads(t2.read_text()) == json.loads(
        (PACK_ROOT / "synthetic_tests" / "valid" / "t2.json").read_text()
    )
    assert json.loads(t3.read_text()) == json.loads(
        (PACK_ROOT / "synthetic_tests" / "valid" / "t3.json").read_text()
    )

    audit = subprocess.run(
        [
            "python3",
            str(PACK_ROOT / "tools" / "audit_predictions.py"),
            "--work-record",
            str(record),
            "--t2",
            str(t2),
            "--t3",
            str(t3),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(audit.stdout)
    assert audit.returncode == 0
    assert report["status"] == "pass"
    assert report["finding_count"] == 0
    Draft202012Validator(
        json.loads((PACK_ROOT / "schemas" / "audit_report.schema.json").read_text())
    ).validate(report)


def test_manifest_driven_suite_reports_exact_case_ids(
    monkeypatch,
) -> None:
    _, suite = load_capability_control_bundle(PACK_ROOT)
    real_run = subprocess.run

    def local_container(command, **kwargs):
        image_index = command.index("python:3.13-slim")
        argv = [
            value.replace("/pack", str(PACK_ROOT))
            for value in command[image_index + 1 :]
        ]
        return real_run(argv, **kwargs)

    monkeypatch.setattr(workflow.subprocess, "run", local_container)
    report = workflow.run_pack_synthetic_suite_docker_report(PACK_ROOT)
    expected_ids = [case["case_id"] for case in suite["cases"]]
    assert report["status"] == "pass"
    assert report["issues"] == []
    assert report["case_count"] == len(expected_ids)
    assert report["executed_case_ids"] == expected_ids


def test_runtime_contract_exposes_portable_control_interfaces(
    tmp_path: Path,
) -> None:
    pack = validate_capability_pack(PACK_ROOT)
    memory = portable_exemplar_memory(tmp_path / "memory", 5)
    runtime = workflow._build_checkpoint_runtime(  # noqa: SLF001
        checkpoint_id="C5",
        pack_digest=pack["pack_digest"],
        exemplar_memory=memory,
    )
    interfaces = runtime["interfaces"]
    for key in (
        "work_record_schema",
        "audit_report_schema",
        "control_index",
        "synthetic_suite",
    ):
        relative = Path(interfaces[key]).relative_to("pack")
        assert (PACK_ROOT / relative).is_file()
    for key in ("compiler", "audit"):
        relative = Path(interfaces[key]["path"]).relative_to("pack")
        assert (PACK_ROOT / relative).is_file()
    assert runtime["content"]["resource_roots"]["schemas"] == "pack/schemas"
    assert runtime["exemplar_memory"] == memory
    assert runtime["interfaces"]["exemplar_query"]["path"] == (
        "memory/runtime/tools/query_exemplars.py"
    )
    assert runtime["interfaces"]["target_evidence_guard"]["path"] == (
        "memory/runtime/tools/guard_target_evidence.py"
    )
    assert workflow.checkpoint_exemplar_max_results(runtime) == 3
    assert runtime["consumer_contract"]["exemplar_access"] == (
        "query_only_maximum_three_retrieved_exemplars"
    )
    assert interfaces["output_contract"] == {
        "format": "json",
        "success_exit": 0,
        "findings_exit": 1,
        "input_error_exit": 2,
    }


def test_top_one_runtime_and_harbor_instruction_are_bound_together(
    tmp_path: Path,
) -> None:
    checkpoint_root = _portable_checkpoint(
        tmp_path,
        exemplar_max_results=1,
    )
    _, runtime, _ = workflow.validate_checkpoint_runtime(checkpoint_root)
    assert workflow.checkpoint_exemplar_max_results(runtime) == 1
    assert runtime["consumer_contract"]["exemplar_access"] == (
        "query_only_maximum_one_retrieved_exemplar"
    )
    assert runtime["interfaces"]["exemplar_query"]["argv_template"][-2:] == [
        "--max-results",
        "1",
    ]

    tasks = tmp_path / "top-one-tasks"
    task = tasks / "synthetic_protocol" / "task.toml"
    task.parent.mkdir(parents=True)
    task.write_text(
        'version = "1.0"\n[verifier]\nenvironment_mode = "separate"\n',
        encoding="utf-8",
    )
    integration_root = tmp_path / "top-one-integration"
    prepare_capability_harbor_integration(
        pack_root=checkpoint_root,
        tasks_root=tasks,
        protocol_ids=["synthetic_protocol"],
        output_root=integration_root,
        created_at="2026-08-21T00:00:00Z",
    )
    instruction = (integration_root / "extra_instruction.md").read_text()
    assert "at most one donor subgraph" in instruction
    assert "at most three donor subgraphs" not in instruction
    validate_capability_harbor_integration(integration_root, tasks_root=tasks)


def test_non_codex_consumer_executes_checkpoint_declared_interfaces(
    tmp_path: Path,
) -> None:
    checkpoint_root = _portable_checkpoint(tmp_path)
    _, runtime, _ = workflow.validate_checkpoint_runtime(checkpoint_root)
    record = checkpoint_root / "pack" / "synthetic_tests" / "valid" / "work_record.json"
    t2 = tmp_path / "generic-t2.json"
    t3 = tmp_path / "generic-t3.json"
    replacements = {
        "{work_record}": str(record),
        "{t2_output}": str(t2),
        "{t3_output}": str(t3),
    }

    def command(interface: str) -> list[str]:
        return [
            replacements.get(value, value)
            for value in runtime["interfaces"][interface]["argv_template"]
        ]

    compile_result = subprocess.run(
        command("compiler"),
        cwd=checkpoint_root,
        check=False,
        capture_output=True,
        text=True,
    )
    audit_result = subprocess.run(
        command("audit"),
        cwd=checkpoint_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0
    assert audit_result.returncode == 0
    assert json.loads(audit_result.stdout)["status"] == "pass"


def test_learned_checkpoint_guidance_names_exact_parent(tmp_path: Path) -> None:
    checkpoint_root = _portable_checkpoint(tmp_path)
    checkpoint_path = checkpoint_root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["validation_guidance"]["checkpoint_label"] = "C5"
    checkpoint.pop("checkpoint_digest")
    checkpoint = with_digest(checkpoint, "checkpoint_digest")
    checkpoint_path.chmod(0o600)
    write_json_atomic(checkpoint_path, checkpoint, mode=0o444)

    with pytest.raises(
        CapabilityImprovementError,
        match="learned checkpoint has invalid cumulative lineage",
    ):
        workflow.validate_checkpoint_runtime(checkpoint_root)


def test_harbor_consumes_whole_checkpoint_runtime(tmp_path: Path) -> None:
    checkpoint_root = _portable_checkpoint(tmp_path)
    tasks = tmp_path / "tasks"
    task = tasks / "synthetic_protocol" / "task.toml"
    task.parent.mkdir(parents=True)
    task.write_text(
        'version = "1.0"\n[verifier]\nenvironment_mode = "separate"\n',
        encoding="utf-8",
    )
    integration_root = tmp_path / "integration"
    integration = prepare_capability_harbor_integration(
        pack_root=checkpoint_root,
        tasks_root=tasks,
        protocol_ids=["synthetic_protocol"],
        output_root=integration_root,
        created_at="2026-08-21T00:00:00Z",
    )
    assert integration["checkpoint_runtime"]["checkpoint_id"] == "C5"
    assert integration["checkpoint_runtime"]["runtime_sha256"] == sha256_file(
        checkpoint_root / "runtime.json"
    )
    assert integration["exemplar_memory"]["exemplar_count"] == 5
    assert (
        integration["checkpoint_runtime"]["exemplar_memory"]
        == integration["exemplar_memory"]
    )
    assert integration["mount"]["target"] == CAPABILITY_MOUNT_TARGET
    assert integration["memory_mount"]["target"] == EXEMPLAR_MEMORY_MOUNT_TARGET
    assert integration["target_evidence_guard"]["nonzero_blocks_finalization"] is True
    instruction = (integration_root / "extra_instruction.md").read_text()
    assert "tools/control_index.json" in instruction
    assert "declared compiler command" in instruction
    assert "declared unified audit command" in instruction
    assert "at most three donor subgraphs" in instruction
    assert "full frozen catalog" in instruction
    assert "Exit 0 is required before finalization" in instruction
    validate_capability_harbor_integration(integration_root, tasks_root=tasks)

    # Integrations created before per-checkpoint donor caps were added are
    # retained as hash-bound max-three records so an in-flight job remains
    # auditable without mutating or rerunning it.
    legacy_manifest_path = integration_root / "integration_manifest.json"
    legacy = json.loads(legacy_manifest_path.read_text())
    legacy["checkpoint_runtime"].pop("exemplar_max_results")
    legacy.pop("integration_digest")
    legacy = with_digest(legacy, "integration_digest")
    legacy_manifest_path.chmod(0o600)
    write_json_atomic(legacy_manifest_path, legacy, mode=0o444)
    validate_capability_harbor_integration(integration_root, tasks_root=tasks)

    manifest_integration_root = tmp_path / "manifest-integration"
    manifest_integration = prepare_capability_harbor_integration(
        pack_root=checkpoint_root / "checkpoint.json",
        tasks_root=tasks,
        protocol_ids=["synthetic_protocol"],
        output_root=manifest_integration_root,
        created_at="2026-08-21T00:00:00Z",
    )
    assert manifest_integration["pack_digest"] == integration["pack_digest"]
    assert (
        manifest_integration["checkpoint_runtime"] == integration["checkpoint_runtime"]
    )
    validate_capability_harbor_integration(
        manifest_integration_root,
        tasks_root=tasks,
    )


def test_harbor_checkpoint_memory_is_read_only_non_scored_and_tamper_evident(
    tmp_path: Path,
) -> None:
    checkpoint_root = _portable_checkpoint(tmp_path, "C5")
    tasks = tmp_path / "tasks"
    task = tasks / "synthetic_protocol" / "task.toml"
    task.parent.mkdir(parents=True)
    task.write_text(
        'version = "1.0"\n[verifier]\nenvironment_mode = "separate"\n',
        encoding="utf-8",
    )
    integration_root = tmp_path / "integration-c5"
    integration = prepare_capability_harbor_integration(
        pack_root=checkpoint_root / "checkpoint.json",
        tasks_root=tasks,
        protocol_ids=["synthetic_protocol"],
        output_root=integration_root,
        created_at="2026-08-21T00:00:00Z",
    )
    assert integration["exemplar_memory"]["exemplar_count"] == 5
    assert integration["exemplar_usage_scored"] is False
    assert integration["verifier_visibility"] == "none_separate_environment"
    assert integration["memory_mount"]["read_only"] is True

    base_config = tmp_path / "base.json"
    write_json_atomic(
        base_config,
        {
            "agents": [
                {
                    "name": "codex",
                    "model_name": "gpt-5.6-sol",
                    "kwargs": {
                        "version": "0.147.0",
                        "reasoning_effort": "max",
                    },
                }
            ],
            "datasets": [{}],
            "environment": {"type": "docker"},
            "n_concurrent_trials": 4,
        },
    )
    config = build_harbor_job_config(
        base_config_path=base_config,
        integration_root=integration_root,
        tasks_root=tasks,
        protocol_ids=["synthetic_protocol"],
        job_name="memory-c0",
        jobs_dir=tmp_path / "jobs",
        output_path=tmp_path / "job.json",
    )
    assert {item["target"] for item in config["environment"]["mounts"]} == {
        CAPABILITY_MOUNT_TARGET,
        EXEMPLAR_MEMORY_MOUNT_TARGET,
    }
    assert set(EXEMPLAR_DIAGNOSTIC_ARTIFACTS).issubset(config["artifacts"])

    c0_root = _portable_checkpoint(tmp_path, "C0")
    c0_integration = prepare_capability_harbor_integration(
        pack_root=c0_root,
        tasks_root=tasks,
        protocol_ids=["synthetic_protocol"],
        output_root=tmp_path / "integration-c0",
        created_at="2026-08-21T00:00:00Z",
    )
    assert c0_integration["exemplar_memory"]["exemplar_count"] == 0

    exposed_memory = integration_root / "exemplar_memory"
    thaw_tree(exposed_memory)
    catalog_path = exposed_memory / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["exemplar_count"] = 0
    write_json_atomic(catalog_path, catalog)
    with pytest.raises(CapabilityImprovementError):
        validate_capability_harbor_integration(
            integration_root,
            tasks_root=tasks,
        )


def test_worker_resolves_only_checkpoint_bound_parent_memory(tmp_path: Path) -> None:
    checkpoint_root = _portable_checkpoint(tmp_path, "C5")
    memory_root, memory, source = _resolve_parent_exemplar_memory(
        checkpoint_root / "pack",
        experiment_digest="0" * 64,
    )
    assert memory_root == checkpoint_root / "memory"
    assert memory["exemplar_count"] == 5
    assert source["checkpoint_id"] == "C5"

    thaw_tree(checkpoint_root / "memory")
    catalog_path = checkpoint_root / "memory" / "catalog.json"
    catalog_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CapabilityImprovementError):
        _resolve_parent_exemplar_memory(
            checkpoint_root / "pack",
            experiment_digest="0" * 64,
        )

    with pytest.raises(CapabilityImprovementError, match="validated frozen"):
        _resolve_parent_exemplar_memory(
            PACK_ROOT,
            experiment_digest="0" * 64,
        )


def test_worker_stages_parent_memory_without_private_identity_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_root = _portable_checkpoint(tmp_path, "C0")
    experiment_root = tmp_path / "experiment"
    access_policy_path = experiment_root / "design" / "transfer-policy.json"
    validation_policy_path = experiment_root / "design" / "validation-policy.json"
    write_json_atomic(access_policy_path, {"fixture": True})
    write_json_atomic(validation_policy_path, {"fixture": True})
    aggregate_path = experiment_root / "validation" / "aggregates" / "C0.json"
    write_json_atomic(aggregate_path, {"fixture": True})
    packet = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_batch_packet.v1",
            "packet_id": "B1:cumulative:revealed",
            "experiment_digest": "0" * 64,
            "batch_id": "B1",
            "branch": "cumulative",
            "phase": "retrospective",
            "parent_pack_digest": validate_capability_pack(checkpoint_root / "pack")[
                "pack_digest"
            ],
            "protocol_ids": [f"training_{index}" for index in range(5)],
            "reveal_state": "revealed",
            "eligibility_status": "eligible_for_improvement",
            "transfer_access_policy_digest": "a" * 64,
            "artifacts": [],
            "trial_terminality": [],
            "learning_ledger": None,
        },
        "packet_digest",
    )
    packet_path = experiment_root / "rounds" / "B1" / "packet.json"
    write_json_atomic(packet_path, packet)
    experiment = {
        "experiment_digest": "0" * 64,
        "frozen_retrospective_transfer_panel": {"access_policy": {"digest": "a" * 64}},
        "validation_panel": {
            "access_policy": {
                "path": validation_policy_path.relative_to(experiment_root).as_posix(),
                "digest": "b" * 64,
                "sha256": sha256_file(validation_policy_path),
            }
        },
    }
    monkeypatch.setattr(
        isolation_module,
        "validate_transfer_access_policy",
        lambda _path: {"policy_digest": "a" * 64},
    )
    monkeypatch.setattr(
        isolation_module,
        "assert_transfer_panel_isolation",
        lambda **_kwargs: None,
    )
    import libstruct_bench.improvement.validation as validation_module

    monkeypatch.setattr(
        validation_module,
        "validate_validation_access_policy",
        lambda _path: {"policy_digest": "b" * 64},
    )
    feedback = {"checkpoint_label": "C0", "aggregate_digest": "c" * 64}
    monkeypatch.setattr(
        validation_module,
        "validate_validation_aggregate",
        lambda *_args, **_kwargs: feedback,
    )
    monkeypatch.setattr(
        validation_module,
        "build_validation_feedback_projection",
        lambda _value: {
            "checkpoint_label": "C0",
            "aggregate_digest": "c" * 64,
        },
    )
    monkeypatch.setattr(
        validation_module,
        "validate_validation_feedback_projection",
        lambda *_args, **_kwargs: feedback,
    )

    workspace = tmp_path / "worker"
    manifest = prepare_isolated_worker_workspace(
        experiment_manifest=experiment,
        packet_path=packet_path,
        parent_pack_root=checkpoint_root / "pack",
        access_policy_path=access_policy_path,
        output_root=workspace,
        mode="improvement_worker",
        validation_feedback_path=aggregate_path,
    )
    assert manifest["exemplar_memory"]["exemplar_count"] == 0
    assert manifest["exemplar_memory"]["source_checkpoint"]["checkpoint_id"] == "C0"
    assert manifest["exemplar_memory"]["private_identity_map_staged"] is False
    assert (workspace / "inputs" / "exemplar_memory" / "manifest.json").is_file()
    assert not (workspace / "inputs" / "private").exists()
    assert not list(workspace.rglob("exemplar_identity_map.json"))
    validate_isolated_worker_workspace(workspace)

    staged_memory = workspace / "inputs" / "exemplar_memory"
    thaw_tree(staged_memory)
    (staged_memory / "catalog.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(CapabilityImprovementError):
        validate_isolated_worker_workspace(workspace)


def test_harbor_rejects_arbitrary_or_invalid_checkpoint_files(
    tmp_path: Path,
) -> None:
    checkpoint_root = _portable_checkpoint(tmp_path)
    tasks = tmp_path / "tasks"
    task = tasks / "synthetic_protocol" / "task.toml"
    task.parent.mkdir(parents=True)
    task.write_text(
        'version = "1.0"\n[verifier]\nenvironment_mode = "separate"\n',
        encoding="utf-8",
    )

    arbitrary = tmp_path / "runtime.json"
    arbitrary.write_text("{}\n", encoding="utf-8")
    orphan = tmp_path / "orphan" / "checkpoint.json"
    orphan.parent.mkdir()
    orphan.write_bytes((checkpoint_root / "checkpoint.json").read_bytes())
    linked = tmp_path / "linked" / "checkpoint.json"
    linked.parent.mkdir()
    linked.symlink_to(checkpoint_root / "checkpoint.json")

    with pytest.raises(CapabilityImprovementError, match="exact checkpoint.json"):
        prepare_capability_harbor_integration(
            pack_root=arbitrary,
            tasks_root=tasks,
            protocol_ids=["synthetic_protocol"],
            output_root=tmp_path / "arbitrary-integration",
            created_at="2026-08-21T00:00:00Z",
        )
    with pytest.raises(CapabilityImprovementError):
        prepare_capability_harbor_integration(
            pack_root=orphan,
            tasks_root=tasks,
            protocol_ids=["synthetic_protocol"],
            output_root=tmp_path / "orphan-integration",
            created_at="2026-08-21T00:00:00Z",
        )
    with pytest.raises(CapabilityImprovementError, match="may not be a symlink"):
        prepare_capability_harbor_integration(
            pack_root=linked,
            tasks_root=tasks,
            protocol_ids=["synthetic_protocol"],
            output_root=tmp_path / "linked-integration",
            created_at="2026-08-21T00:00:00Z",
        )


def test_generic_harbor_preparation_cannot_unseal_final_test(
    tmp_path: Path,
) -> None:
    checkpoint_root = _portable_checkpoint(tmp_path)
    tasks = tmp_path / "tasks"
    task = tasks / "cel_seq2" / "task.toml"
    task.parent.mkdir(parents=True)
    task.write_text(
        'version = "1.0"\n[verifier]\nenvironment_mode = "separate"\n',
        encoding="utf-8",
    )

    with pytest.raises(
        CapabilityImprovementError,
        match="only by the post-lock final-replay planner",
    ):
        prepare_capability_harbor_integration(
            pack_root=checkpoint_root,
            tasks_root=tasks,
            protocol_ids=["cel_seq2"],
            output_root=tmp_path / "forbidden-final-test-integration",
            created_at="2026-08-21T00:00:00Z",
        )

    assert not (tmp_path / "forbidden-final-test-integration").exists()

    alias = tasks / "final_alias"
    alias.symlink_to(tasks / "cel_seq2", target_is_directory=True)
    invalid_ids = (
        "cel_seq2/../cel_seq2",
        (tasks / "cel_seq2").as_posix(),
        "final_alias",
    )
    for index, protocol_id in enumerate(invalid_ids):
        with pytest.raises(CapabilityImprovementError):
            prepare_capability_harbor_integration(
                pack_root=checkpoint_root,
                tasks_root=tasks,
                protocol_ids=[protocol_id],
                output_root=tmp_path / f"forbidden-final-test-alias-{index}",
                created_at="2026-08-21T00:00:00Z",
            )


def test_prepare_harbor_cli_accepts_checkpoint_alias(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "C5" / "checkpoint.json"
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return {"status": "prepared"}

    monkeypatch.setattr(
        capability_cli,
        "prepare_capability_harbor_integration",
        fake_prepare,
    )
    assert (
        capability_cli.main(
            [
                "prepare-harbor",
                "--checkpoint",
                str(checkpoint),
                "--tasks",
                str(tmp_path / "tasks"),
                "--protocol-id",
                "synthetic_protocol",
                "--out",
                str(tmp_path / "integration"),
                "--created-at",
                "2026-08-21T00:00:00Z",
            ]
        )
        == 0
    )
    assert captured["pack_root"] == checkpoint
