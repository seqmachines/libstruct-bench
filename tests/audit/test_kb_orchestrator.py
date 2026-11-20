from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from libstruct_bench.audit.artifacts import sha256_file, write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    with_digest,
)
from libstruct_bench.improvement.kb_orchestrator import (
    _archive_failed_harbor_result,
    _checkpoint_batch_bounds,
    _completed_kb_recovery_attempt,
    _evaluation_infrastructure_failures,
    _evaluation_recovery_waves,
    _is_transient_codex_capacity_failure,
    _proposal_rule_match,
    _run_receipt,
    _validation_score_row,
    _validation_selection_sort_key,
    _validate_staged_kb_adjudication_workspace,
    _validate_codex_workspace_access,
    _validate_adjudicator_output,
    build_learned_pact,
    find_harbor_trial,
    linked_prediction_errors,
    record_kb_evaluation_aggregate,
    validate_kb_pact,
)
from libstruct_bench.improvement.kb_guided import (
    KB_RETRIEVAL_WORKFLOW,
    _build_baseline_trial_pin,
    _build_exposure_catalog,
    _evaluation_retry_policy,
    _force_local_codex_subscription,
    _knowledge_runtime_contract,
    _make_design_read_only,
    _project_oligo_component,
    _repository_lock_files,
    _rewrite_graph_value,
    _validate_knowledge_runtime_contract,
    _validate_baseline_trial_pin,
)
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "schemas/improvement"
PACK_ROOT = REPO_ROOT / "paper/improvement/capability_pack"


def test_all_kb_schemas_are_valid() -> None:
    paths = sorted(SCHEMA_ROOT.glob("kb_*.schema.json"))
    assert len(paths) >= 20
    for path in paths:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_unbounded_kb_fetch_command_is_shell_complete() -> None:
    assert "--max-results" not in KB_RETRIEVAL_WORKFLOW
    output_lines = [
        line
        for line in KB_RETRIEVAL_WORKFLOW.splitlines()
        if "--output /logs/artifacts/kb_retrieval.json" in line
    ]
    assert output_lines == ["  --output /logs/artifacts/kb_retrieval.json"]


def test_checkpoint_batch_bounds_split_the_final_five_and_three() -> None:
    design = {
        "learned_checkpoints": [
            {"after_training_count": value, "label": f"C{value}"}
            for value in (*range(5, 56, 5), 58)
        ]
    }
    assert _checkpoint_batch_bounds(design, 5) == (5, 10, "C10")
    assert _checkpoint_batch_bounds(design, 55) == (55, 58, "C58")


def test_only_explicit_codex_capacity_failure_is_retryable(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    stderr = tmp_path / "stderr.log"
    events.write_text(
        '{"type":"error","message":"Selected model is at capacity. '
        'Please try a different model."}\n',
        encoding="utf-8",
    )
    stderr.write_text("", encoding="utf-8")
    assert _is_transient_codex_capacity_failure(events, stderr)
    events.write_text(
        '{"type":"turn.failed","error":{"message":"invalid request"}}\n',
        encoding="utf-8",
    )
    assert not _is_transient_codex_capacity_failure(events, stderr)


def test_test_replay_retries_only_runtime_exceptions() -> None:
    assert _evaluation_retry_policy("validation") == {"max_retries": 0}
    policy = _evaluation_retry_policy("test")
    assert policy["max_retries"] == 2
    assert policy["include_exceptions"] == ["RuntimeError"]


def test_composite_confirmation_uses_one_coherent_selection_order() -> None:
    def aggregate(*, value: float, invalid: int = 0) -> dict[str, object]:
        return {
            "invalid_or_missing_count": invalid,
            "mean_metrics": {
                "reward": value,
                "t2_required_family_f1": value,
                "t3_molecular_transition_f1": value,
                "t3_state_f1": value,
                "t3_typed_edge_f1": value,
            },
        }

    coupled = _validation_score_row(
        aggregate=aggregate(value=0.6), training_count=30
    )
    stronger_composite = _validation_score_row(
        aggregate=aggregate(value=0.61), training_count=30
    )
    weaker_composite = _validation_score_row(
        aggregate=aggregate(value=0.59), training_count=30
    )
    assert _validation_selection_sort_key(
        stronger_composite
    ) <= _validation_selection_sort_key(coupled)
    assert not (
        _validation_selection_sort_key(weaker_composite)
        <= _validation_selection_sort_key(coupled)
    )


def test_validation_recovery_selects_only_explicit_infrastructure_failures(
    tmp_path: Path,
) -> None:
    root = tmp_path / "job"
    for protocol_id, exception_type, message in (
        (
            "address_pool",
            "RuntimeError",
            "all predefined address pools have been fully subnetted",
        ),
        (
            "capacity",
            "NonZeroAgentExitCodeError",
            "Selected model is at capacity. Please try a different model.",
        ),
        (
            "transport_policy",
            "NonZeroAgentExitCodeError",
            "Reconnecting... stream disconnected before completion. Invalid prompt: "
            "your prompt was flagged as potentially violating our usage policy.",
        ),
        ("scientific_invalid", "ValueError", "prediction failed validation"),
    ):
        trial = root / f"{protocol_id}__trial"
        trial.mkdir(parents=True)
        write_json_atomic(
            trial / "result.json",
            {
                "exception_info": {
                    "exception_type": exception_type,
                    "exception_message": message,
                },
            },
        )
    failures = _evaluation_infrastructure_failures(
        result_root=root,
        protocol_ids=[
            "address_pool",
            "capacity",
            "transport_policy",
            "scientific_invalid",
        ],
    )
    assert [(item["protocol_id"], item["reason"]) for item in failures] == [
        ("address_pool", "docker_address_pool_exhausted"),
        ("capacity", "local_model_capacity"),
        ("transport_policy", "provider_transport_policy_false_positive"),
    ]


def test_validation_recovery_waves_cap_total_cells() -> None:
    specs = [
        {"checkpoint": "C10", "failures": [1]},
        {"checkpoint": "C15", "failures": [1, 2, 3, 4]},
        {"checkpoint": "C20", "failures": [1]},
        {"checkpoint": "C25", "failures": [1, 2, 3]},
    ]
    waves = _evaluation_recovery_waves(specs, max_parallel_cells=5)
    assert [[item["checkpoint"] for item in wave] for wave in waves] == [
        ["C10", "C15"],
        ["C20", "C25"],
    ]


def test_completed_recovery_attempt_preserves_success_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_root = tmp_path / "jobs" / "recovery"
    config_path = tmp_path / "harbor.json"
    write_json_atomic(
        config_path,
        {
            "job_name": "recovery",
            "jobs_dir": (tmp_path / "jobs").as_posix(),
            "agent_timeout_multiplier": 3.0,
            "datasets": [{"task_names": ["good", "timed_out"]}],
        },
    )
    for protocol_id, exception in (
        ("good", None),
        (
            "timed_out",
            {
                "exception_type": "AgentTimeoutError",
                "exception_message": "Agent execution timed out after 5400 seconds",
            },
        ),
    ):
        trial = result_root / f"{protocol_id}__trial"
        trial.mkdir(parents=True)
        write_json_atomic(trial / "result.json", {"exception_info": exception})
    monkeypatch.setattr(
        "libstruct_bench.improvement.kb_orchestrator.resolve_trial_artifacts",
        lambda trial, protocol_id: {"result": trial / "result.json"},
    )
    attempt, usable = _completed_kb_recovery_attempt(
        attempt_id="attempt-1",
        config_path=config_path,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
    )
    assert attempt["agent_timeout_multiplier"] == 3.0
    assert [item["exception_type"] for item in attempt["trial_outcomes"]] == [
        None,
        "AgentTimeoutError",
    ]
    assert list(usable) == ["good"]


def test_batch_pact_dedup_collapses_semantic_paraphrases() -> None:
    canonical = {
        "rule_id": "R006",
        "title": "Require literal assembled state architectures",
        "category": "state",
        "trigger": (
            "A state has a complete assembled sequence architecture and "
            "deterministic ordered segments."
        ),
        "instruction": (
            "Encode the architecture as the complete token-aware "
            "bases/placeholder string and require the ordered segment "
            "projection to consume it end-to-end; never substitute prose labels."
        ),
        "checker": "product_conservation",
    }
    paraphrase = {
        "title": "Materialize deterministic strand architectures",
        "category": "state",
        "trigger": (
            "A strand has ordered segments whose bases or placeholders "
            "determine its full architecture."
        ),
        "instruction": (
            "Write sequence_architecture as the complete ordered "
            "bases-and-placeholders projection, never as prose, and reject any "
            "mismatch with the segment ledger."
        ),
        "recommended_checker": "state_event_projection",
    }
    distinct = {
        **paraphrase,
        "trigger": "A transition changes or carries molecular strand inventory.",
        "instruction": (
            "Derive each product from its substrate using source-supported "
            "additions and removals, then carry it to the next transition."
        ),
    }
    match = _proposal_rule_match(paraphrase, canonical)
    assert match is not None and match[0] == "semantic-near-duplicate"
    assert _proposal_rule_match(distinct, canonical) is None


def test_learned_pact_appends_one_rule_without_changing_base(tmp_path: Path) -> None:
    before = validate_kb_pact(PACK_ROOT)["pack_digest"]
    learned = build_learned_pact(
        repository_root=REPO_ROOT,
        parent_pact=None,
        proposal={
            "title": "Reconcile event participants",
            "category": "bookkeeping",
            "trigger": "A transition is compiled from a source event table.",
            "instruction": "Reconcile substrates, products, used oligos, carries, and discards before emission.",
            "recommended_checker": "state_event_projection",
            "supporting_finding_ids": ["F001"],
            "protocol_neutral": True,
        },
        output_root=tmp_path / "pact",
    )
    assert learned["pack_digest"] != before
    assert validate_kb_pact(PACK_ROOT)["pack_digest"] == before
    rules = json.loads(
        (tmp_path / "pact/checklists/learned_rules.json").read_text(encoding="utf-8")
    )
    assert rules["rules"][0]["rule_id"] == "R001"
    assert rules["rules"][0]["checker"] == "state_event_projection"
    assert "Learned protocol-neutral rule R001" in (
        tmp_path / "pact/PLAYBOOK.md"
    ).read_text(encoding="utf-8")


def test_linked_prediction_validation_reports_identity_contract() -> None:
    t2 = json.loads(
        (PACK_ROOT / "synthetic_tests/valid/t2.json").read_text(encoding="utf-8")
    )
    t3 = json.loads(
        (PACK_ROOT / "synthetic_tests/valid/t3.json").read_text(encoding="utf-8")
    )
    assert (
        linked_prediction_errors(protocol_id="synthetic_protocol", t2=t2, t3=t3) == []
    )
    segment = next(
        segment
        for state in t3["workflows"][0]["states"]
        for strand in state["strands"]
        for segment in strand["segments"]
        if segment.get("oligo_derivations")
    )
    segment["oligo_derivations"][0]["oligo_id"] = "oligo_missing"
    errors = linked_prediction_errors(protocol_id="synthetic_protocol", t2=t2, t3=t3)
    assert errors and errors[0].startswith("T2/T3 links:")


def test_adjudicator_must_partition_observations_and_cite_trajectory(
    tmp_path: Path,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    write_json_atomic(
        inputs / "error_analysis.json",
        {
            "observations": [
                {"error_id": "err_0001", "substantive": True},
                {"error_id": "err_0002", "substantive": False},
            ]
        },
    )
    comparison = {"protocol_id": "example", "training_ordinal": 1}
    output = {
        "protocol_id": "example",
        "training_ordinal": 1,
        "findings": [
            {
                "finding_id": "F001",
                "observation_ids": ["err_0001"],
                "benchmark_validity": "valid",
                "attribution": "agent",
                "source_recoverability": "recoverable",
                "consolidated_root_event": True,
                "learning_eligible": True,
                "evidence_locators": ["sources/001.pdf p.2"],
                "trajectory_locators": ["trajectory.json /messages/4"],
            }
        ],
        "pact_proposal": None,
    }
    _validate_adjudicator_output(output, comparison, tmp_path)
    output["findings"][0]["trajectory_locators"] = []
    with pytest.raises(CapabilityImprovementError, match="dual provenance"):
        _validate_adjudicator_output(output, comparison, tmp_path)
    output["findings"][0].update(
        {
            "benchmark_validity": "invalid",
            "attribution": "ground_truth",
            "source_recoverability": "recoverable",
            "learning_eligible": False,
            "recommended_action": "deterministic_checker",
        }
    )
    with pytest.raises(CapabilityImprovementError, match="cannot change"):
        _validate_adjudicator_output(output, comparison, tmp_path)


@pytest.mark.parametrize("run_kind", ["training", "test"])
def test_run_receipt_is_hash_bound_and_excludes_credentials(
    tmp_path: Path, run_kind: str
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text("{}\n", encoding="utf-8")
    receipt = _run_receipt(
        run_kind=run_kind,
        run_id="a" * 64,
        status="running",
        plan_path=plan,
        command=["harbor", "run", "--config", "/tmp/config.json", "--yes"],
        pid=123,
        started_at="2026-09-01T00:00:00Z",
        finished_at=None,
        returncode=None,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
        result_root=tmp_path / "result",
        aggregate_path=None,
    )
    assert receipt["plan_sha256"] == sha256_file(plan)
    assert receipt["run_kind"] == run_kind
    assert "OPENAI_API_KEY" not in json.dumps(receipt)


def test_harbor_agent_config_does_not_expose_auth_values_to_redaction() -> None:
    config = {
        "agents": [
            {
                "name": "codex",
                "env": {
                    "OPENAI_API_KEY": "forbidden",
                    "OPENAI_BASE_URL": "forbidden",
                    "CODEX_FORCE_AUTH_JSON": "1",
                    "CODEX_AUTH_JSON_PATH": "/private/auth.json",
                },
            }
        ]
    }
    _force_local_codex_subscription(config)
    assert "env" not in config["agents"][0]


def test_graph_card_projection_removes_protocol_scope_identity() -> None:
    projected = _rewrite_graph_value(
        {
            "protocol_id": "private_protocol",
            "protocol_name": "Private Protocol",
            "protocol_scope": {
                "protocol_version": "private version",
                "applicable_variants": ["private variant"],
            },
            "operation": "ligation",
        },
        {
            "state": {},
            "strand": {},
            "segment": {},
            "pair": {},
            "discontinuity": {},
            "transition": {},
            "oligo": {},
        },
        {"private_protocol", "Private Protocol"},
    )
    assert projected == {"operation": "ligation"}


def test_oligo_component_projection_omits_null_optional_fields() -> None:
    projected = _project_oligo_component(
        {
            "name": "barcode component",
            "role": "cell barcode",
            "orientation": "same_orientation",
            "modifications": [],
            "support_status": "explicit",
            "sequence": None,
            "placeholder": "[CELL_BARCODE:8]",
            "length": None,
        },
        set(),
    )
    assert projected["placeholder"] == "[CELL_BARCODE:8]"
    assert "sequence" not in projected
    assert "length" not in projected


def test_exposure_catalog_contains_only_groundtruth_cards() -> None:
    source = with_digest(
        {
            "schema_version": "libstruct.libgen_kb_catalog.v1",
            "design_digest": "a" * 64,
            "identity_map_digest": "b" * 64,
            "training_count": 1,
            "entries": [
                {
                    "exemplar_id": "exm-" + "1" * 32,
                    "training_ordinal": 1,
                    "oligo_card": {
                        "path": "oligos.json",
                        "sha256": "c" * 64,
                        "digest": "d" * 64,
                    },
                    "graph_card": {
                        "path": "graph.json",
                        "sha256": "e" * 64,
                        "digest": "f" * 64,
                    },
                    "mechanism_summary": {
                        "path": "private-mechanism.json",
                        "sha256": "1" * 64,
                        "digest": "2" * 64,
                    },
                }
            ],
        },
        "catalog_digest",
    )
    exposed = _build_exposure_catalog(source)
    assert exposed["source_catalog_digest"] == source["catalog_digest"]
    assert set(exposed["entries"][0]) == {
        "exemplar_id",
        "oligo_card",
        "graph_card",
    }
    assert "private-mechanism.json" not in json.dumps(exposed)


def test_preserved_baseline_trial_pin_detects_artifact_mutation(
    tmp_path: Path,
) -> None:
    trial = tmp_path / "trial"
    prediction_root = trial / "artifacts/logs/artifacts"
    prediction_root.mkdir(parents=True)
    (trial / "agent").mkdir()
    (trial / "verifier").mkdir()
    for path, value in (
        (trial / "config.json", {}),
        (trial / "result.json", {}),
        (prediction_root / "t2_prediction.json", {}),
        (prediction_root / "t3_prediction.json", {}),
        (trial / "agent/trajectory.json", {}),
        (trial / "verifier/reward.json", {"reward": 0.5}),
        (
            trial / "verifier/details.json",
            {
                "benchmark_version": LIBGEN_BENCHMARK_VERSION,
                "protocol_id": "synthetic_protocol",
            },
        ),
        (trial / "verifier/error_analysis.json", {"observations": []}),
    ):
        write_json_atomic(path, value)
    condition, pin = _build_baseline_trial_pin(
        trial_root=trial,
        result={"config": {}},
        protocol_id="synthetic_protocol",
    )
    assert condition == "raw_codex"
    _validate_baseline_trial_pin(pin)
    write_json_atomic(trial / "verifier/reward.json", {"reward": 0.6})
    with pytest.raises(CapabilityImprovementError, match="baseline artifact changed"):
        _validate_baseline_trial_pin(pin)


def test_repository_lock_covers_shared_improvement_runtime() -> None:
    relative = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in _repository_lock_files(REPO_ROOT)
    }
    assert "src/libstruct_bench/improvement/kb_guided.py" in relative
    assert "src/libstruct_bench/improvement/kb_orchestrator.py" in relative
    assert "src/libstruct_bench/improvement/workflow.py" in relative


def test_read_only_pack_preserves_executable_contract(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    tools = pack / "tools"
    tools.mkdir(parents=True)
    checker = tools / "checker.py"
    data = pack / "rules.json"
    checker.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    data.write_text("{}\n", encoding="utf-8")
    checker.chmod(0o755)
    data.chmod(0o644)

    _make_design_read_only(pack)

    assert checker.stat().st_mode & 0o111
    assert not data.stat().st_mode & 0o111
    assert not checker.stat().st_mode & 0o222
    assert not data.stat().st_mode & 0o222


def test_failed_harbor_attempt_is_archived_before_retry(tmp_path: Path) -> None:
    result_root = tmp_path / "jobs/frozen-plan"
    result_root.mkdir(parents=True)
    (result_root / "partial.log").write_text("partial\n", encoding="utf-8")
    archived = _archive_failed_harbor_result(result_root, {"receipt_digest": "f" * 64})
    assert archived == tmp_path / "jobs/failed-attempts/frozen-plan-ffffffffffff"
    assert not result_root.exists()
    assert (archived / "partial.log").read_text(encoding="utf-8") == "partial\n"


def test_partial_existing_adjudication_workspace_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    comparison = tmp_path / "comparison.json"
    comparison.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CapabilityImprovementError, match="workspace is incomplete"):
        _validate_staged_kb_adjudication_workspace(
            workspace=workspace,
            comparison_path=comparison,
            comparison={},
            experiment_root=tmp_path,
            manifest={},
        )


def test_codex_adjudicator_transcript_cannot_leave_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "run/workspace"
    workspace.mkdir(parents=True)
    repository = tmp_path / "repository"
    repository.mkdir()
    event_path = workspace / "events.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "item": {
                    "type": "command_execution",
                    "command": f"cat {repository}/secret.json",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "repository": {"path": repository.as_posix()},
        "private_roots": {
            "groundtruth_root": (tmp_path / "private/ground_truth").as_posix(),
            "baseline_root": (tmp_path / "baseline").as_posix(),
            "run_root": (tmp_path / "run").as_posix(),
            "source_manifest_search_root": (tmp_path / "private/manifests").as_posix(),
        },
    }
    with pytest.raises(CapabilityImprovementError, match="left its pinned"):
        _validate_codex_workspace_access(
            event_path=event_path,
            workspace=workspace,
            experiment_root=tmp_path / "experiment",
            manifest=manifest,
        )

    event_path.write_text(
        json.dumps(
            {
                "item": {
                    "type": "command_execution",
                    "command": f"cat {workspace}/inputs/source.pdf",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _validate_codex_workspace_access(
        event_path=event_path,
        workspace=workspace,
        experiment_root=tmp_path / "experiment",
        manifest=manifest,
    )

    # The Codex JSONL writer can also truncate a very large command-completion
    # payload inside its final unicode escape. The exact command was already
    # present in the valid start event, so retain the transcript and ignore only
    # that malformed completion payload instead of rerunning the adjudication.
    command = f'python -c "print({json.dumps((workspace / "inputs/source.pdf").as_posix())})"'
    started = {
        "type": "item.started",
        "item": {
            "id": "item_7",
            "type": "command_execution",
            "command": command,
        },
    }
    completed = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "id": "item_7",
                "type": "command_execution",
                "command": command,
                "aggregated_output": "before\u0000after\nsecond line",
            },
        }
    )
    completed = completed.replace("\\u0000", "\\u000\n0").replace(
        "after\\nsecond", "after\nsecond"
    )
    event_path.write_text(
        json.dumps(started) + "\n" + completed + "\n", encoding="utf-8"
    )
    _validate_codex_workspace_access(
        event_path=event_path,
        workspace=workspace,
        experiment_root=tmp_path / "experiment",
        manifest=manifest,
    )

    truncated = (
        '{"type":"item.completed","item":{"id":"item_7",'
        '"type":"command_execution","command":'
        + json.dumps(command)
        + ',"aggregated_output":"large output\\u000'
    )
    event_path.write_text(
        json.dumps(started) + "\n" + truncated + "\n", encoding="utf-8"
    )
    _validate_codex_workspace_access(
        event_path=event_path,
        workspace=workspace,
        experiment_root=tmp_path / "experiment",
        manifest=manifest,
    )

    # Codex can occasionally emit a command result with literal newlines inside
    # one JSON string. Preserve and validate that event instead of rerunning the
    # scientific adjudication merely to normalize transcript framing.
    multiline_event = json.dumps(
        {
            "item": {
                "type": "command_execution",
                "command": f"cat {workspace}/inputs/source.pdf",
                "aggregated_output": "first line\nsecond line",
            }
        }
    ).replace("first line\\nsecond line", "first line\nsecond line")
    event_path.write_text(multiline_event + "\n", encoding="utf-8")
    _validate_codex_workspace_access(
        event_path=event_path,
        workspace=workspace,
        experiment_root=tmp_path / "experiment",
        manifest=manifest,
    )

    audit_skill = repository / "paper/audit/.agents/skills/libstruct-audit/SKILL.md"
    audit_skill.parent.mkdir(parents=True)
    audit_skill.write_text("generic audit instructions\n", encoding="utf-8")
    event_path.write_text(
        json.dumps(
            {
                "item": {
                    "type": "command_execution",
                    "command": f"cat {audit_skill}",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _validate_codex_workspace_access(
        event_path=event_path,
        workspace=workspace,
        experiment_root=tmp_path / "experiment",
        manifest=manifest,
    )

    event_path.write_text(
        json.dumps(
            {
                "item": {
                    "type": "command_execution",
                    "command": (
                        "python -c 'namespace = "
                        '"http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
                        "'"
                    ),
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _validate_codex_workspace_access(
        event_path=event_path,
        workspace=workspace,
        experiment_root=tmp_path / "experiment",
        manifest=manifest,
    )

    event_path.write_text(
        json.dumps(
            {
                "item": {
                    "type": "command_execution",
                    "command": "curl https://example.invalid/source.pdf",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CapabilityImprovementError, match="left its pinned"):
        _validate_codex_workspace_access(
            event_path=event_path,
            workspace=workspace,
            experiment_root=tmp_path / "experiment",
            manifest=manifest,
        )


def test_harbor_trial_resolution_uses_task_metadata_when_name_is_truncated(
    tmp_path: Path,
) -> None:
    result_root = tmp_path / "results"
    trial = result_root / "10x_chromium_3_gene_expression_v__abc123"
    trial.mkdir(parents=True)
    write_json_atomic(
        trial / "lock.json",
        {"task": {"name": "10x_chromium_3_gene_expression_v4"}},
    )

    assert (
        find_harbor_trial(result_root, "10x_chromium_3_gene_expression_v4")
        == trial
    )


def test_harbor_trial_resolution_rejects_truncated_name_with_other_task_metadata(
    tmp_path: Path,
) -> None:
    result_root = tmp_path / "results"
    trial = result_root / "10x_chromium_3_gene_expression_v__abc123"
    trial.mkdir(parents=True)
    write_json_atomic(
        trial / "lock.json",
        {"task": {"name": "10x_chromium_3_gene_expression_v3"}},
    )

    with pytest.raises(CapabilityImprovementError, match="found 0"):
        find_harbor_trial(result_root, "10x_chromium_3_gene_expression_v4")


def test_kb_query_ranks_every_donor_then_fetches_agent_selection(
    tmp_path: Path,
) -> None:
    runtime = runpy.run_path(
        (REPO_ROOT / "paper/improvement/kb_runtime/query_kb.py").as_posix()
    )
    rank_donors = runtime["rank_donors"]
    fetch_selected = runtime["fetch_selected"]
    resolve_protocol_id = runtime["resolve_protocol_id"]
    query_error = runtime["QueryError"]
    entries = []
    donor_rows = (
        ("exm-" + "1" * 32, "gene expression"),
        ("exm-" + "2" * 32, "genomic DNA"),
        ("exm-" + "3" * 32, "feature barcode"),
        ("exm-" + "4" * 32, "sgRNA"),
    )
    for index, (exemplar_id, modality) in enumerate(
        donor_rows,
        start=1,
    ):
        root = tmp_path / f"cards/{index}"
        root.mkdir(parents=True)
        write_json_atomic(root / "oligos.json", {"kind": "oligo", "index": index})
        write_json_atomic(root / "graph.json", {"kind": "graph", "index": index})
        entries.append(
            {
                "exemplar_id": exemplar_id,
                "oligo_card": {"path": f"cards/{index}/oligos.json"},
                "graph_card": {"path": f"cards/{index}/graph.json"},
            }
        )
    catalog = {
        "schema_version": "libstruct.libgen_kb_exposure_catalog.v1",
        "source_catalog_digest": "b" * 64,
        "training_count": 4,
        "entries": entries,
    }
    catalog["catalog_digest"] = runtime["canonical_digest"](catalog)
    write_json_atomic(tmp_path / "catalog.json", catalog)
    ranking_index = {
        "schema_version": "libstruct.libgen_kb_ranking_index.v1",
        "catalog_digest": catalog["catalog_digest"],
        "entries": [
            {
                "exemplar_id": exemplar_id,
                "features": {
                    group: ([modality] if group == "modalities" else [])
                    for group in runtime["GROUPS"]
                },
            }
            for exemplar_id, modality in donor_rows
        ],
    }
    ranking_index["index_digest"] = runtime["canonical_digest"](ranking_index)
    write_json_atomic(tmp_path / "ranking_index.json", ranking_index)
    write_json_atomic(
        tmp_path / "family_priorities.json",
        {"target_protocol": ["exm-" + "2" * 32]},
    )
    query = {
        "schema_version": "libstruct.libgen_kb_query.v1",
        "features": {
            group: (["gene expression"] if group == "modalities" else [])
            for group in runtime["GROUPS"]
        },
        "evidence": [
            {
                "feature_group": "modalities",
                "feature_value": "gene expression",
                "source_locator": "source.pdf p.2",
            }
        ],
    }
    ranking = rank_donors(
        root=tmp_path,
        query=query,
        protocol_id="target_protocol",
    )
    assert [item["exemplar_id"] for item in ranking["candidates"]] == [
        "exm-" + "1" * 32,
        "exm-" + "2" * 32,
        "exm-" + "3" * 32,
        "exm-" + "4" * 32,
    ]
    assert ranking["candidates"][0]["score"]["value"] > 0
    assert ranking["candidates"][1]["score"]["value"] == 0
    assert ranking["candidates"][1]["family_priority_rank"] == 1

    selection = {
        "schema_version": "libstruct.libgen_kb_selection.v1",
        "ranking_digest": ranking["ranking_digest"],
        "selected_exemplar_ids": [
            "exm-" + "2" * 32,
            "exm-" + "3" * 32,
            "exm-" + "4" * 32,
        ],
        "rationale": "Three lower-ranked donors resolve different analogies.",
    }
    retrieval = fetch_selected(
        root=tmp_path,
        query=query,
        ranking=ranking,
        selection=selection,
        protocol_id="target_protocol",
    )
    assert retrieval["schema_version"] == "libstruct.libgen_kb_retrieval.v3"
    assert retrieval["candidate_count"] == 4
    assert retrieval["selection_count"] == 3
    assert retrieval["max_results"] is None
    assert retrieval["matches"][0]["exemplar_id"] == "exm-" + "2" * 32
    assert retrieval["matches"][0]["similarity_rank"] == 2
    assert retrieval["matches"][0]["oligo_card"] == {
        "kind": "oligo",
        "index": 2,
    }
    assert retrieval["matches"][0]["graph_card"] == {
        "kind": "graph",
        "index": 2,
    }
    assert [item["exemplar_id"] for item in retrieval["matches"]] == selection[
        "selected_exemplar_ids"
    ]

    with pytest.raises(query_error, match="at most 2 donors"):
        fetch_selected(
            root=tmp_path,
            query=query,
            ranking=ranking,
            selection=selection,
            protocol_id="target_protocol",
            max_results=2,
        )

    modified_ranking = json.loads(json.dumps(ranking))
    modified_ranking["candidates"].reverse()
    with pytest.raises(query_error, match="stale, modified"):
        fetch_selected(
            root=tmp_path,
            query=query,
            ranking=modified_ranking,
            selection=selection,
            protocol_id="target_protocol",
            max_results=2,
        )

    query["features"]["modalities"] = ["not_a_controlled_modality"]
    query["evidence"][0]["feature_value"] = "not_a_controlled_modality"
    with pytest.raises(query_error, match="unsupported values"):
        rank_donors(
            root=tmp_path,
            query=query,
            protocol_id="target_protocol",
        )
    write_json_atomic(
        tmp_path / "input_manifest.json", {"protocol_id": "target_protocol"}
    )
    assert (
        resolve_protocol_id(None, tmp_path / "input_manifest.json")
        == "target_protocol"
    )
    with pytest.raises(query_error, match="differs from the target"):
        resolve_protocol_id("another_protocol", tmp_path / "input_manifest.json")


def test_v2_knowledge_runtime_is_bound_to_the_mounted_bytes(tmp_path: Path) -> None:
    exposure = tmp_path / "knowledge"
    exposure.mkdir()
    for name in (
        "query_kb.py",
        "catalog.json",
        "ranking_index.json",
        "family_priorities.json",
    ):
        (exposure / name).write_text(f"{name}\n", encoding="utf-8")
    instruction = tmp_path / "instruction.md"
    instruction.write_text("rank, select, fetch\n", encoding="utf-8")
    runtime = _knowledge_runtime_contract(
        exposure=exposure,
        instruction_path=instruction,
    )
    config_path = tmp_path / "harbor.json"
    write_json_atomic(
        config_path,
        {
            "environment": {
                "mounts": [
                    {
                        "source": exposure.as_posix(),
                        "target": "/workspace/knowledge_base",
                    }
                ]
            },
            "extra_instruction_paths": [instruction.as_posix()],
        },
    )
    plan = {
        "retrieval_policy": runtime["retrieval_policy"],
        "knowledge_runtime": runtime,
        "config": {
            "path": config_path.as_posix(),
            "sha256": sha256_file(config_path),
        },
    }
    _validate_knowledge_runtime_contract(plan)

    (exposure / "query_kb.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(CapabilityImprovementError, match="query_tool_sha256"):
        _validate_knowledge_runtime_contract(plan)


def test_evaluation_aggregate_counts_invalid_and_missing_as_zero(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    config.write_text("{}\n", encoding="utf-8")
    plan = with_digest(
        {
            "schema_version": "libstruct.libgen_kb_evaluation_run_plan.v1",
            "design_digest": "a" * 64,
            "checkpoint": "C5",
            "checkpoint_digest": "b" * 64,
            "composite_endpoint": {
                "endpoint_id": "pact-C5__kb-C58",
                "path": "/tmp/composite_endpoint.json",
                "sha256": "c" * 64,
                "digest": "d" * 64,
                "pact_checkpoint": "C5",
                "knowledge_checkpoint": "C58",
            },
            "panel": "validation",
            "protocol_ids": ["synthetic_protocol", "missing_protocol"],
            "concurrency": 2,
            "timeout_seconds": 900,
            "test_authorized": False,
            "config": {"path": config.as_posix(), "sha256": sha256_file(config)},
            "created_at": "2026-09-01T00:00:00Z",
        },
        "plan_digest",
    )
    plan_path = tmp_path / "plan.json"
    write_json_atomic(plan_path, plan)
    trial = tmp_path / "results/synthetic_protocol__abc"
    (trial / "artifacts/logs/artifacts").mkdir(parents=True)
    (trial / "agent").mkdir()
    (trial / "verifier").mkdir()
    for name in ("t2", "t3"):
        source = PACK_ROOT / f"synthetic_tests/valid/{name}.json"
        destination = trial / f"artifacts/logs/artifacts/{name}_prediction.json"
        destination.write_bytes(source.read_bytes())
    for path, value in (
        (trial / "result.json", {}),
        (trial / "agent/trajectory.json", {}),
        (
            trial / "verifier/details.json",
            {
                "benchmark_version": LIBGEN_BENCHMARK_VERSION,
                "protocol_id": "synthetic_protocol",
                "prediction_valid": True,
            },
        ),
        (trial / "verifier/error_analysis.json", {"observations": []}),
        (
            trial / "verifier/reward.json",
            {
                key: 1.0
                for key in (
                    "reward",
                    "t2_exact_required_family_recall",
                    "t2_required_family_f1",
                    "t3_molecular_transition_f1",
                    "t3_state_f1",
                    "t3_typed_edge_f1",
                )
            },
        ),
    ):
        write_json_atomic(path, value)
    aggregate = record_kb_evaluation_aggregate(
        plan_path=plan_path,
        result_root=tmp_path / "results",
        output_path=tmp_path / "aggregate.json",
        created_at="2026-09-01T01:00:00Z",
    )
    assert aggregate["valid_count"] == 1
    assert aggregate["invalid_or_missing_count"] == 1
    assert aggregate["mean_metrics"]["reward"] == 0.5
    assert aggregate["composite_endpoint"]["endpoint_id"] == "pact-C5__kb-C58"
