from __future__ import annotations

import copy
import io
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import libstruct_bench.improvement.local_completion as completion_module
import libstruct_bench.improvement.local_learning as learning_module
import libstruct_bench.improvement.validation as validation_module
import libstruct_bench.improvement.workflow as workflow_module
from libstruct_bench.audit.artifacts import sha256_file, write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    canonical_digest,
    copy_capability_pack,
    validate_capability_pack,
    with_digest,
)
from libstruct_bench.improvement.isolation import (
    compile_codex_output_schema,
    validate_codex_output_schema,
)
from libstruct_bench.improvement.evaluation import build_final_evaluation_report
from libstruct_bench.improvement.exemplar_memory import build_exemplar_identity_map
from libstruct_bench.improvement.experiment import (
    FIXED_BATCHES,
    FROZEN_RETROSPECTIVE_TRANSFER_PANEL,
    TRANSFER_ANNOTATIONS,
    build_batch_packet,
    build_final_lock,
    build_transfer_panel_authorization,
)
from libstruct_bench.improvement.harbor import build_final_replay_plan
from libstruct_bench.improvement.lineage import CHECKPOINT_LABELS
from libstruct_bench.improvement.local_completion import run_capability_completion
from libstruct_bench.improvement.local_learning import (
    LocalCodexRunRequest,
    LocalCodexRunResult,
    run_local_learning,
    run_native_codex,
)
from libstruct_bench.improvement.worker_runtime import IsolatedWorkerLaunch
from libstruct_bench.improvement.review_summary import (
    write_or_validate_capability_review_summary,
)
from libstruct_bench.improvement.workflow import (
    _build_checkpoint_runtime,
    apply_capability_decision,
    create_decision_template,
    finalize_capability_decision,
    freeze_checkpoint,
    record_change_decision,
    validate_checkpoint_runtime,
)
from tests.audit.capability_memory_fixtures import (
    TEST_MAPPING_NONCE,
    TEST_SPLIT_DIGEST,
    minimal_t2,
    minimal_t3,
    portable_exemplar_memory,
)
from libstruct_bench.libgen.error_analysis import build_error_analysis


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPO_ROOT / "improvement" / "capability_pack"
SCHEMA_ROOT = REPO_ROOT / "schemas" / "improvement"
NOW = "2026-08-22T12:00:00Z"
LATER = "2026-08-22T12:05:00Z"


def _write(path: Path, value: object, *, mode: int | None = None) -> Path:
    write_json_atomic(path, value, mode=mode)
    return path


def _schema_nodes(value: object):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _schema_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _schema_nodes(item)


def _tasks(root: Path, protocol_ids: tuple[str, ...] | list[str]) -> None:
    for protocol_id in protocol_ids:
        task = root / protocol_id / "task.toml"
        task.parent.mkdir(parents=True, exist_ok=True)
        task.write_text(
            'version = "1.0"\n[verifier]\nenvironment_mode = "separate"\n',
            encoding="utf-8",
        )


def _checkpoint(
    root: Path,
    label: str,
    *,
    experiment_digest: str | None = None,
) -> Path:
    checkpoint_root = root / "checkpoints" / label
    if (checkpoint_root / "checkpoint.json").exists():
        return checkpoint_root
    if not (checkpoint_root / "pack").exists():
        copy_capability_pack(PACK_ROOT, checkpoint_root / "pack", freeze=True)
    pack = validate_capability_pack(checkpoint_root / "pack")
    count = int(label[1:])
    memory = portable_exemplar_memory(checkpoint_root / "memory", count)
    runtime = _build_checkpoint_runtime(
        checkpoint_id=label,
        pack_digest=pack["pack_digest"],
        exemplar_memory=memory,
    )
    runtime_path = _write(
        checkpoint_root / "runtime.json",
        runtime,
        mode=0o444,
    )
    order = count // 5
    if experiment_digest is None:
        experiment_digest = json.loads(
            (root / "checkpoints" / "C0" / "checkpoint.json").read_text(
                encoding="utf-8"
            )
        )["experiment_digest"]
    checkpoint = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_checkpoint.v1",
            "checkpoint_id": label,
            "experiment_digest": experiment_digest,
            "branch": "cumulative",
            "protocol_count": count,
            "batch_id": None if count == 0 else f"B{order}",
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
            "created_at": NOW,
        },
        "checkpoint_digest",
    )
    _write(checkpoint_root / "checkpoint.json", checkpoint, mode=0o444)
    return checkpoint_root


def _experiment(root: Path) -> dict:
    c0 = root / "checkpoints" / "C0"
    copy_capability_pack(PACK_ROOT, c0 / "pack", freeze=True)
    pack = validate_capability_pack(c0 / "pack")
    transfer_policy_path = _write(
        root / "design" / "transfer_access_policy.json",
        {"fixture": "transfer-policy"},
    )
    validation_policy_path = _write(
        root / "design" / "validation_access_policy.json",
        {"fixture": "validation-policy"},
    )
    experiment = {
        "experiment_id": "capability-cumulative-fixture",
        "anchor": {
            "agent": "codex",
            "harness": "native",
            "model": "gpt-5.6-sol",
            "agent_version": "0.147.0",
            "reasoning_effort": "max",
            "concurrency": 1,
        },
        "initial_pack": {
            "pack_digest": pack["pack_digest"],
            "references": {"C0": "checkpoints/C0/pack"},
        },
        "batches": [
            {
                **dict(batch),
                "protocol_ids": list(batch["protocol_ids"]),
                "groundtruth_commitment_sha256": None,
            }
            for batch in FIXED_BATCHES
        ],
        "validation_panel": {
            "set_id": "fixed-validation-panel-v1",
            "classification": "fixed_validation_panel",
            "protocol_ids": [
                "sci_atac_seq",
                "scrrbs",
                "smart_seq",
                "share_seq",
                "ddseq_single_cell_3_rna_seq_kit",
            ],
            "evaluation_checkpoints": list(CHECKPOINT_LABELS),
            "access_policy": {
                "path": validation_policy_path.relative_to(root).as_posix(),
                "digest": "d" * 64,
                "sha256": sha256_file(validation_policy_path),
            },
        },
        "frozen_retrospective_transfer_panel": {
            "set_id": "frozen-retrospective-transfer-panel-v1",
            "classification": "frozen_retrospective_transfer_panel",
            "protocol_ids": list(FROZEN_RETROSPECTIVE_TRANSFER_PANEL),
            "transfer_annotations": [dict(item) for item in TRANSFER_ANNOTATIONS],
            "commitment_sha256": "e" * 64,
            "baseline_mode": "post_lock_c0_replay",
            "endpoint_labels": ["C25"],
            "access_policy": {
                "path": transfer_policy_path.relative_to(root).as_posix(),
                "digest": "f" * 64,
                "sha256": sha256_file(transfer_policy_path),
            },
        },
        "transfer_panel_commitment": {},
        "frozen_split": {},
        "test_isolation_audit": {},
        "validation_isolation_audit": {},
        "single_branch_migration": {},
    }
    experiment = with_digest(experiment, "experiment_digest")
    _checkpoint(root, "C0", experiment_digest=experiment["experiment_digest"])
    _write(root / "design" / "experiment_manifest.json", experiment)
    return experiment


def _transfer_policy(experiment: dict) -> dict:
    return {
        "policy_digest": experiment["frozen_retrospective_transfer_panel"][
            "access_policy"
        ]["digest"],
        "blocked_protocol_ids": list(FROZEN_RETROSPECTIVE_TRANSFER_PANEL),
        "blocked_trees": [],
        "blocked_files": [],
    }


def _packet(tmp_path: Path, experiment: dict) -> dict:
    records: list[dict] = []
    for protocol_id in FIXED_BATCHES[0]["protocol_ids"]:
        analysis = _write(
            tmp_path / "evidence" / protocol_id / "error-analysis.json",
            build_error_analysis(
                trial_id=f"{protocol_id}:trial",
                protocol_id=protocol_id,
                result=None,
                details={"prediction_valid": False},
                verifier_error={
                    "kind": "invalid_prediction",
                    "message": "synthetic invalid prediction",
                },
                artifact_inventory=[],
            ),
        )
        reward = _write(
            tmp_path / "evidence" / protocol_id / "reward.json",
            {
                "reward": 0.5,
                "t3_molecular_transition_f1": 0.5,
                "t3_state_f1": 0.5,
                "t3_typed_edge_f1": 0.5,
            },
        )
        for role, path in (
            ("verifier_error_analysis", analysis),
            ("verifier_reward", reward),
        ):
            records.append(
                {
                    "protocol_id": protocol_id,
                    "role": role,
                    "path": path.resolve().as_posix(),
                    "sha256": sha256_file(path),
                    "visibility": "agent_after_reveal",
                }
            )
        truth_root = tmp_path / "truth" / protocol_id
        for filename, document in (
            ("groundtruth_oligos.json", minimal_t2(protocol_id)),
            (
                "groundtruth_library_generation_workflow.json",
                minimal_t3(protocol_id),
            ),
        ):
            path = _write(truth_root / filename, document)
            records.append(
                {
                    "protocol_id": protocol_id,
                    "role": "approved_groundtruth",
                    "path": path.resolve().as_posix(),
                    "sha256": sha256_file(path),
                    "visibility": "agent_after_reveal",
                }
            )
    return build_batch_packet(
        experiment_manifest=experiment,
        branch="cumulative",
        batch_id="B1",
        parent_pack_digest=experiment["initial_pack"]["pack_digest"],
        reveal_state="revealed",
        artifacts=records,
        trial_terminality=[],
        transfer_access_policy=_transfer_policy(experiment),
    )


def _proposal(root: Path, experiment: dict, packet: dict) -> tuple[Path, Path, dict]:
    round_root = root / "rounds" / "B1" / "cumulative"
    candidate_root = round_root / "proposer-workspace" / "candidates"
    candidate = candidate_root / "PLAYBOOK.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(
        (PACK_ROOT / "PLAYBOOK.md").read_text(encoding="utf-8")
        + "\nRequire explicit transition accounting.\n",
        encoding="utf-8",
    )
    cluster = packet["learning_ledger"]["clusters"][0]
    proposal = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_proposal.v1",
            "proposal_id": "B1:cumulative:r0:fixture",
            "experiment_digest": experiment["experiment_digest"],
            "branch": "cumulative",
            "batch_id": "B1",
            "checkpoint_from": "C0",
            "packet_digest": packet["packet_digest"],
            "parent_pack_digest": experiment["initial_pack"]["pack_digest"],
            "learning_ledger_digest": packet["learning_ledger"]["ledger_digest"],
            "validation_guidance": {
                "checkpoint_label": "C0",
                "aggregate_digest": "4" * 64,
                "aggregate_sha256": "5" * 64,
                "workspace_digest": "6" * 64,
                "workspace_manifest_sha256": "7" * 64,
            },
            "protocol_ids": list(FIXED_BATCHES[0]["protocol_ids"]),
            "proposer": {
                "agent": "codex",
                "harness": "native",
                "model": "gpt-5.6-sol",
                "version": "0.147.0",
                "reasoning_effort": "max",
                "transcript_sha256": "8" * 64,
            },
            "revision_round": 0,
            "revision_of_proposal_digest": None,
            "revision_request_decision_digest": None,
            "change_units": [
                {
                    "change_id": "transition-accounting",
                    "cluster_ids": [cluster["cluster_id"]],
                    "evidence_refs": copy.deepcopy(cluster["evidence_refs"]),
                    "generalized_failure_pattern": "products were misclassified",
                    "update_type": "procedural_or_tool",
                    "admission_basis": "recurring_root_error",
                    "synthetic_regression_case_ids": [],
                    "capability_class": "instruction_only",
                    "applicability": ["molecular products"],
                    "exclusions": ["handling-only operations"],
                    "finding_codes": list(cluster["finding_codes"]),
                    "residual_judgment": (
                        "Source interpretation determines product disposition."
                    ),
                    "counterexample": "A wash is not a molecular edge.",
                    "expected_invariant": "every product is classified once",
                    "enforcement_paths": [],
                    "fixtures": [],
                    "instruction_only_rationale": (
                        "Source-specific disposition cannot be inferred generically."
                    ),
                    "mutations": [
                        {
                            "operation": "replace",
                            "path": "PLAYBOOK.md",
                            "baseline_sha256": sha256_file(PACK_ROOT / "PLAYBOOK.md"),
                            "candidate_sha256": sha256_file(candidate),
                        }
                    ],
                    "leakage_attestation": True,
                }
            ],
        },
        "proposal_digest",
    )
    proposal_path = _write(round_root / "proposal.json", proposal, mode=0o444)
    _write(round_root / "packet.json", packet, mode=0o444)
    return proposal_path, candidate_root, proposal


def _decision(proposal_path: Path, *, reviewer_kind: str) -> Path:
    independent = reviewer_kind == "independent_codex"
    decision = create_decision_template(
        proposal_path=proposal_path,
        reviewer_kind=reviewer_kind,
        reviewer_id="critic" if independent else "reviewer",
        reviewer_model="gpt-5.6-sol" if independent else None,
        reviewer_version="0.147.0" if independent else None,
        transcript_sha256="9" * 64 if independent else None,
        started_at=NOW,
    )
    path = _write(proposal_path.with_name(f"{reviewer_kind}-decision.json"), decision)
    record_change_decision(
        proposal_path=proposal_path,
        decision_path=path,
        change_id="transition-accounting",
        disposition="accept",
        rationale="The source-bounded control is protocol neutral.",
    )
    finalize_capability_decision(
        proposal_path=proposal_path,
        decision_path=path,
        completed_at=LATER,
    )
    return path


def _workspace_contract(workspace: Path) -> dict:
    (workspace / "inputs").mkdir(parents=True, exist_ok=True)
    (workspace / "outputs").mkdir(exist_ok=True)
    for relative, value in (
        ("inputs/prompt.md", "fixture prompt\n"),
        ("inputs/schema.json", "{}\n"),
    ):
        path = workspace / relative
        path.write_text(value, encoding="utf-8")
    return {
        "agent_contract": {
            "prompt_path": "inputs/prompt.md",
            "output_schema_path": "inputs/schema.json",
            "draft_output_path": "outputs/draft.json",
            "event_log_path": "outputs/events.jsonl",
        }
    }


def _patch_active_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    root: Path,
    experiment: dict,
) -> None:
    real_load_and_validate = workflow_module.load_and_validate

    def load_and_validate(path, **kwargs):
        if kwargs.get("schema_filename") == "experiment_manifest.schema.json":
            return experiment
        return real_load_and_validate(path, **kwargs)

    monkeypatch.setattr(workflow_module, "load_and_validate", load_and_validate)
    monkeypatch.setattr(
        workflow_module,
        "_load_active_frozen_split",
        lambda *_args, **_kwargs: {"split_digest": TEST_SPLIT_DIGEST},
    )
    monkeypatch.setattr(
        workflow_module,
        "ensure_exemplar_identity_map",
        lambda **_kwargs: build_exemplar_identity_map(
            split_digest=TEST_SPLIT_DIGEST,
            mapping_nonce=TEST_MAPPING_NONCE,
        ),
    )
    monkeypatch.setattr(
        completion_module,
        "validate_experiment_manifest",
        lambda *_args, **_kwargs: experiment,
    )
    monkeypatch.setattr(
        workflow_module,
        "validate_capability_proposal",
        lambda **kwargs: json.loads(Path(kwargs["proposal_path"]).read_text()),
    )
    monkeypatch.setattr(
        completion_module,
        "validate_capability_proposal",
        lambda **kwargs: json.loads(Path(kwargs["proposal_path"]).read_text()),
    )
    monkeypatch.setattr(
        workflow_module,
        "_assert_pack_validation_clean",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        validation_module,
        "validate_referenced_validation_access_policy",
        lambda **_kwargs: {
            "forbidden_terms": [],
            "forbidden_sequences": [],
            "forbidden_scaffolds": [],
        },
    )
    monkeypatch.setattr(
        validation_module,
        "validate_required_validation_aggregate",
        lambda **_kwargs: {
            "checkpoint_label": "C0",
            "aggregate_digest": "4" * 64,
        },
    )
    monkeypatch.setattr(
        validation_module,
        "validate_validation_guidance_record",
        lambda *_args, **_kwargs: {
            "checkpoint_label": "C0",
            "aggregate_digest": "4" * 64,
        },
    )
    monkeypatch.setattr(
        validation_module,
        "scan_validation_pack_leakage",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        validation_module,
        "scan_validation_feedback_copy",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        completion_module,
        "_prepare_or_validate_leakage_policy",
        lambda **_kwargs: {
            "forbidden_terms": [],
            "forbidden_sequences": [],
            "forbidden_scaffolds": [],
            "allowed_synthetic_paths": [],
        },
    )

    def critic_workspace(**kwargs):
        workspace = Path(kwargs["workspace"])
        if (workspace / "inputs" / "review" / "proposal.json").is_file():
            return _workspace_contract(workspace)
        contract = _workspace_contract(workspace)
        review = workspace / "inputs" / "review"
        review.mkdir(parents=True, exist_ok=True)
        shutil.copy2(kwargs["proposal_path"], review / "proposal.json")
        shutil.copytree(kwargs["candidate_root"], review / "candidates")
        copy_capability_pack(
            root / "checkpoints" / "C0" / "pack",
            workspace / "inputs" / "capability_pack",
            freeze=False,
        )
        return contract

    monkeypatch.setattr(
        completion_module,
        "_prepare_or_validate_critic_workspace",
        critic_workspace,
    )

    def human_workspace(**kwargs):
        workspace = Path(kwargs["workspace"])
        if (workspace / "inputs" / "review" / "proposal.json").is_file():
            return {"mode": "human_review_console"}
        review = workspace / "inputs" / "review"
        review.mkdir(parents=True, exist_ok=True)
        shutil.copy2(kwargs["proposal_path"], review / "proposal.json")
        shutil.copytree(kwargs["candidate_root"], review / "candidates")
        copy_capability_pack(
            root / "checkpoints" / "C0" / "pack",
            workspace / "inputs" / "capability_pack",
            freeze=False,
        )
        return {"mode": "human_review_console"}

    monkeypatch.setattr(
        completion_module,
        "_prepare_or_validate_human_review_workspace",
        human_workspace,
    )


def test_local_learning_uses_one_fake_runner_and_resumes_cumulative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "experiment"
    experiment = _experiment(root)
    packet = _packet(tmp_path, experiment)
    aggregate = {
        "checkpoint_label": "C0",
        "aggregate_digest": "4" * 64,
        "pack_digest": experiment["initial_pack"]["pack_digest"],
    }
    aggregate_path = root / "validation" / "aggregates" / "C0.json"
    _write(aggregate_path, aggregate)
    monkeypatch.setattr(
        learning_module,
        "validate_experiment_manifest",
        lambda *_args, **_kwargs: experiment,
    )
    monkeypatch.setattr(
        learning_module,
        "validate_transfer_access_policy",
        lambda _path: _transfer_policy(experiment),
    )
    monkeypatch.setattr(
        validation_module,
        "validate_referenced_validation_access_policy",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        validation_module,
        "validate_required_validation_aggregate",
        lambda **_kwargs: aggregate,
    )
    monkeypatch.setattr(
        learning_module,
        "_prepare_or_validate_packet",
        lambda **_kwargs: packet,
    )

    def workspace(**kwargs):
        workspace_root = Path(kwargs["workspace"])
        contract = _workspace_contract(workspace_root)
        _write(workspace_root / "inputs" / "packet_view.json", packet)
        projection = _write(
            workspace_root / "inputs" / "validation_feedback.json",
            {"aggregate_digest": aggregate["aggregate_digest"]},
        )
        return {
            **contract,
            "validation_feedback": {
                "checkpoint_label": "C0",
                "aggregate_digest": aggregate["aggregate_digest"],
                "aggregate_sha256": sha256_file(aggregate_path),
                "projection_sha256": sha256_file(projection),
            },
        }

    monkeypatch.setattr(
        learning_module,
        "_prepare_or_validate_workspace",
        workspace,
    )
    monkeypatch.setattr(
        learning_module,
        "_prepare_compilable_draft",
        lambda **kwargs: (
            Path(kwargs["draft_path"]),
            [],
            Path(kwargs["event_log_path"]),
        ),
    )
    calls: list[LocalCodexRunRequest] = []

    def runner(request: LocalCodexRunRequest) -> LocalCodexRunResult:
        calls.append(request)
        candidate = request.workspace / "candidates" / "PLAYBOOK.md"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("candidate\n", encoding="utf-8")
        _write(request.draft_output_path, {"change_units": []})
        request.event_log_path.write_text("{}\n", encoding="utf-8")
        return LocalCodexRunResult(0, "exit", 1.0)

    compiled = {
        "proposal_digest": "b" * 64,
        "change_units": [{"change_id": "fixture"}],
    }

    def compile_draft(**kwargs):
        _write(Path(kwargs["output_path"]), compiled)
        return compiled

    monkeypatch.setattr(
        learning_module,
        "compile_capability_proposal_draft",
        compile_draft,
    )
    monkeypatch.setattr(
        learning_module,
        "validate_capability_proposal",
        lambda **_kwargs: compiled,
    )
    run_root = tmp_path / "frozen-B1-run"
    run_root.mkdir()
    result = run_local_learning(
        experiment_root=root,
        batch_id="B1",
        branch="cumulative",
        source_root=tmp_path / "sources",
        groundtruth_root=tmp_path / "truth",
        run_root=run_root,
        agent_runner=runner,
    )
    assert result["status"] == "proposal_ready"
    assert result["branch"] == "cumulative"
    assert result["change_unit_count"] == 1
    assert len(calls) == 1

    resumed = run_local_learning(
        experiment_root=root,
        batch_id="B1",
        branch="cumulative",
        source_root=tmp_path / "sources",
        groundtruth_root=tmp_path / "truth",
        run_root=run_root,
        agent_runner=lambda _request: (_ for _ in ()).throw(
            AssertionError("completed proposal reran Codex")
        ),
    )
    assert resumed["proposal_digest"] == result["proposal_digest"]
    assert resumed["codex_run"] is None


def _stage_completion(
    root: Path, tmp_path: Path, experiment: dict
) -> tuple[Path, dict]:
    packet = _packet(tmp_path, experiment)
    proposal_path, _, proposal = _proposal(root, experiment, packet)
    return proposal_path, proposal


def _accepting_critic(request: LocalCodexRunRequest) -> LocalCodexRunResult:
    proposal = json.loads(
        (request.workspace / "inputs" / "review" / "proposal.json").read_text()
    )
    _write(
        request.draft_output_path,
        {
            "schema_version": "libstruct.libgen_capability_decision_draft.v1",
            "change_decisions": [
                {
                    "change_id": unit["change_id"],
                    "atomicity_verified": True,
                    "disposition": "accept",
                    "rationale": "The change is general and source bounded.",
                    "revision_instruction": None,
                }
                for unit in proposal["change_units"]
            ],
        },
    )
    request.event_log_path.write_text('{"type":"turn.completed"}\n', encoding="utf-8")
    return LocalCodexRunResult(0, "exit", 1.0)


def test_independent_review_applies_and_freezes_c5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "experiment"
    experiment = _experiment(root)
    proposal_path, _ = _stage_completion(root, tmp_path, experiment)
    _patch_active_runtime(monkeypatch, root=root, experiment=experiment)

    reviewed = run_capability_completion(
        branch="cumulative",
        review_mode="independent",
        experiment_root=root,
        batch_id="B1",
        groundtruth_root=tmp_path / "truth",
        critic_runner=_accepting_critic,
        timestamp=lambda: NOW,
    )
    assert reviewed["status"] == "decision_ready"
    assert reviewed["branch"] == "cumulative"
    assert reviewed["review_counts"]["accept"] == 1

    completed = run_capability_completion(
        branch="cumulative",
        review_mode="independent",
        experiment_root=root,
        batch_id="B1",
        groundtruth_root=tmp_path / "truth",
        authorize_apply=True,
        exemplar_max_results=1,
        critic_runner=lambda _request: (_ for _ in ()).throw(
            AssertionError("final decision reran critic")
        ),
        synthetic_runner=lambda _pack: [],
        timestamp=lambda: LATER,
    )
    assert completed["status"] == "checkpoint_ready"
    assert completed["checkpoint_id"] == "C5"
    assert completed["exemplar_max_results"] == 1
    checkpoint, runtime, _ = validate_checkpoint_runtime(root / "checkpoints" / "C5")
    assert checkpoint["branch"] == "cumulative"
    assert checkpoint["parent_checkpoint_id"] == "C0"
    assert checkpoint["proposal_sha256"] == sha256_file(proposal_path)
    assert runtime["checkpoint_id"] == "C5"
    assert runtime["interfaces"]["exemplar_query"]["argv_template"][-2:] == [
        "--max-results",
        "1",
    ]


def test_independent_completion_permits_exactly_one_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "experiment"
    experiment = _experiment(root)
    _, initial = _stage_completion(root, tmp_path, experiment)
    _patch_active_runtime(monkeypatch, root=root, experiment=experiment)
    critic_calls = 0
    revision_calls = 0

    def critic(request: LocalCodexRunRequest) -> LocalCodexRunResult:
        nonlocal critic_calls
        critic_calls += 1
        proposal = json.loads(
            (request.workspace / "inputs" / "review" / "proposal.json").read_text()
        )
        revised = request.workspace.name.endswith("r1")
        disposition = "accept" if revised else "modify"
        _write(
            request.draft_output_path,
            {
                "schema_version": "libstruct.libgen_capability_decision_draft.v1",
                "change_decisions": [
                    {
                        "change_id": proposal["change_units"][0]["change_id"],
                        "atomicity_verified": True,
                        "disposition": disposition,
                        "rationale": "The bounded revision is exact.",
                        "revision_instruction": (
                            None if revised else "Narrow the product scope."
                        ),
                    }
                ],
            },
        )
        request.event_log_path.write_text("{}\n", encoding="utf-8")
        return LocalCodexRunResult(0, "exit", 1.0)

    def revision_workspace(**kwargs):
        workspace = Path(kwargs["workspace"])
        contract = _workspace_contract(workspace)
        review = workspace / "inputs" / "review"
        review.mkdir(parents=True, exist_ok=True)
        shutil.copy2(kwargs["proposal_path"], review / "proposal.json")
        shutil.copy2(kwargs["decision_path"], review / "decision.json")
        shutil.copytree(kwargs["candidate_root"], review / "candidates")
        return contract

    monkeypatch.setattr(
        completion_module,
        "_prepare_or_validate_revision_workspace",
        revision_workspace,
    )

    def revised_proposal(**kwargs):
        nonlocal revision_calls
        revision_calls += 1
        workspace = Path(kwargs["workspace"])
        request = LocalCodexRunRequest(
            workspace=workspace,
            prompt_path=workspace / "inputs" / "prompt.md",
            output_schema_path=workspace / "inputs" / "schema.json",
            draft_output_path=workspace / "outputs" / "draft.json",
            event_log_path=workspace / "outputs" / "events.jsonl",
            stderr_log_path=workspace / "outputs" / "stderr.log",
            model="gpt-5.6-sol",
            version="0.147.0",
            reasoning_effort="max",
            codex_executable="codex",
            idle_timeout_seconds=300,
            hard_timeout_seconds=7200,
        )
        result = kwargs["revision_runner"](request)
        candidate_root = Path(kwargs["candidate_root"])
        candidate_root.mkdir(parents=True, exist_ok=True)
        candidate = candidate_root / "PLAYBOOK.md"
        candidate.write_text(
            (PACK_ROOT / "PLAYBOOK.md").read_text(encoding="utf-8")
            + "\nNarrow product accounting.\n",
            encoding="utf-8",
        )
        proposal = copy.deepcopy(initial)
        proposal.pop("proposal_digest")
        proposal["proposal_id"] = "B1:cumulative:r1:fixture"
        proposal["revision_round"] = 1
        proposal["revision_of_proposal_digest"] = initial["proposal_digest"]
        decision = json.loads(Path(kwargs["revision_decision_path"]).read_text())
        proposal["revision_request_decision_digest"] = decision["decision_digest"]
        proposal["change_units"][0]["mutations"][0]["candidate_sha256"] = sha256_file(
            candidate
        )
        proposal = with_digest(proposal, "proposal_digest")
        _write(Path(kwargs["proposal_path"]), proposal, mode=0o444)
        return proposal, result

    monkeypatch.setattr(
        completion_module,
        "_prepare_or_validate_revised_proposal",
        revised_proposal,
    )

    def revision_runner(request: LocalCodexRunRequest) -> LocalCodexRunResult:
        _write(request.draft_output_path, {"change_units": []})
        request.event_log_path.write_text("{}\n", encoding="utf-8")
        return LocalCodexRunResult(0, "exit", 1.0)

    result = run_capability_completion(
        branch="cumulative",
        review_mode="independent",
        experiment_root=root,
        batch_id="B1",
        groundtruth_root=tmp_path / "truth",
        authorize_apply=True,
        critic_runner=critic,
        revision_runner=revision_runner,
        synthetic_runner=lambda _pack: [],
        timestamp=lambda: NOW,
    )
    assert result["checkpoint_id"] == "C5"
    assert result["revision_round"] == 1
    assert result["initial_review_counts"]["modify"] == 1
    assert result["review_counts"]["accept"] == 1
    assert critic_calls == 2
    assert revision_calls == 1


def test_human_review_resumes_then_applies_to_same_c5_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "experiment"
    experiment = _experiment(root)
    _stage_completion(root, tmp_path, experiment)
    _patch_active_runtime(monkeypatch, root=root, experiment=experiment)

    first_answers = iter(("accept", "The control is general and source bounded.", "no"))
    first = run_capability_completion(
        branch="cumulative",
        review_mode="human",
        experiment_root=root,
        batch_id="B1",
        groundtruth_root=tmp_path / "truth",
        reviewer_id="reviewer-1",
        input_function=lambda _prompt: next(first_answers),
        output=io.StringIO(),
        timestamp=lambda: NOW,
    )
    assert first["status"] == "review_in_progress"

    reviewed = run_capability_completion(
        branch="cumulative",
        review_mode="human",
        experiment_root=root,
        batch_id="B1",
        groundtruth_root=tmp_path / "truth",
        reviewer_id="reviewer-1",
        input_function=lambda _prompt: "yes",
        output=io.StringIO(),
        timestamp=lambda: LATER,
    )
    assert reviewed["status"] == "decision_ready"
    assert reviewed["review_counts"]["accept"] == 1

    completed = run_capability_completion(
        branch="cumulative",
        review_mode="human",
        experiment_root=root,
        batch_id="B1",
        groundtruth_root=tmp_path / "truth",
        reviewer_id="reviewer-1",
        authorize_apply=True,
        input_function=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("final human review was not reused")
        ),
        output=io.StringIO(),
        synthetic_runner=lambda _pack: [],
        timestamp=lambda: LATER,
    )
    assert completed["checkpoint_id"] == "C5"
    checkpoint, _, _ = validate_checkpoint_runtime(root / "checkpoints" / "C5")
    assert checkpoint["branch"] == "cumulative"
    assert checkpoint["parent_checkpoint_id"] == "C0"


def test_deterministic_application_and_checkpoint_reject_failed_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "experiment"
    experiment = _experiment(root)
    packet = _packet(tmp_path, experiment)
    proposal_path, candidate_root, _ = _proposal(root, experiment, packet)
    decision_path = _decision(proposal_path, reviewer_kind="human")
    _patch_active_runtime(monkeypatch, root=root, experiment=experiment)
    application_dir = root / "rounds" / "B1" / "cumulative" / "application"
    application = apply_capability_decision(
        experiment_root=root,
        experiment_manifest=experiment,
        access_policy_path=root / "design" / "transfer_access_policy.json",
        proposal_path=proposal_path,
        decision_path=decision_path,
        candidate_root=candidate_root,
        parent_pack_root=root / "checkpoints" / "C0" / "pack",
        packet_path=root / "rounds" / "B1" / "cumulative" / "packet.json",
        output_dir=application_dir,
        created_at=LATER,
        leakage_policy={
            "forbidden_terms": [],
            "forbidden_sequences": [],
            "forbidden_scaffolds": [],
            "allowed_synthetic_paths": [],
        },
        synthetic_runner=lambda _pack: [],
    )
    assert application["status"] == "applied"
    failed = copy.deepcopy(application)
    failed.pop("application_digest")
    failed["status"] = "no_op_validation_failed"
    failed = with_digest(failed, "application_digest")
    failed_dir = root / "rounds" / "B1" / "cumulative" / "failed-application"
    copy_capability_pack(application_dir / "pack", failed_dir / "pack", freeze=True)
    _write(failed_dir / "application.json", failed)
    with pytest.raises(Exception, match="failed no-op validation"):
        freeze_checkpoint(
            experiment_root=root,
            experiment_digest=experiment["experiment_digest"],
            branch="cumulative",
            batch_id="B1",
            protocol_count=5,
            parent_checkpoint_id="C0",
            proposal_path=proposal_path,
            decision_path=decision_path,
            application_dir=failed_dir,
            output_dir=root / "checkpoints" / "C5",
            created_at=LATER,
        )
    assert list((root / "checkpoints").glob(".C5.building-*")) == []
    checkpoint = freeze_checkpoint(
        experiment_root=root,
        experiment_digest=experiment["experiment_digest"],
        branch="cumulative",
        batch_id="B1",
        protocol_count=5,
        parent_checkpoint_id="C0",
        proposal_path=proposal_path,
        decision_path=decision_path,
        application_dir=application_dir,
        output_dir=root / "checkpoints" / "C5",
        created_at=LATER,
    )
    assert checkpoint["checkpoint_id"] == "C5"
    assert checkpoint["status"] == "procedural_and_exemplar"


def _validation_curve(root: Path) -> dict:
    records = []
    for label in CHECKPOINT_LABELS:
        checkpoint, runtime, _ = validate_checkpoint_runtime(
            root / "checkpoints" / label
        )
        records.append(
            {
                "checkpoint_label": label,
                "aggregate_digest": canonical_digest({"aggregate": label}),
                "aggregate_sha256": canonical_digest({"file": label}),
                "pack_digest": checkpoint["pack_digest"],
                "checkpoint_digest": checkpoint["checkpoint_digest"],
                "runtime_digest": runtime["runtime_digest"],
                "integration_digest": canonical_digest({"integration": label}),
                "integration_manifest_sha256": canonical_digest(
                    {"integration-manifest": label}
                ),
                "task_bundle_sha256": "a" * 64,
                "harbor_config_sha256": canonical_digest({"config": label}),
                "result_bundle_digest": canonical_digest({"result": label}),
            }
        )
    return {
        "access_policy_digest": "d" * 64,
        "checkpoint_labels": list(CHECKPOINT_LABELS),
        "aggregate_records": records,
        "expected_trial_count": 30,
    }


def test_final_lock_and_fixed_panel_replay_plan_exactly_sixty_trials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "experiment"
    experiment = _experiment(root)
    for label in CHECKPOINT_LABELS[1:]:
        _checkpoint(root, label)
    curve = _validation_curve(root)
    monkeypatch.setattr(
        validation_module,
        "validate_validation_access_policy",
        lambda _path: {"policy_digest": "d" * 64},
    )
    monkeypatch.setattr(
        validation_module,
        "validate_complete_validation_curve",
        lambda **_kwargs: curve,
    )
    with pytest.raises(
        CapabilityImprovementError,
        match="canonical checkpoint.json files",
    ):
        build_final_lock(
            experiment_manifest=experiment,
            checkpoint_paths=[
                root / "checkpoints" / label / "runtime.json"
                for label in CHECKPOINT_LABELS
            ],
            created_at=NOW,
        )
    lock = build_final_lock(
        experiment_manifest=experiment,
        checkpoint_paths=[
            root / "checkpoints" / label / "checkpoint.json"
            for label in CHECKPOINT_LABELS
        ],
        created_at=NOW,
    )
    lock_path = _write(root / "design" / "final_lock.json", lock, mode=0o444)
    copied_lock_path = _write(tmp_path / "copied-final-lock.json", lock)
    with pytest.raises(
        CapabilityImprovementError,
        match="canonical experiment marker",
    ):
        build_transfer_panel_authorization(
            experiment_root=root,
            experiment_manifest=experiment,
            final_lock_path=copied_lock_path,
            authorized_by="reviewer",
            authorized_at=LATER,
        )
    authorization = build_transfer_panel_authorization(
        experiment_root=root,
        experiment_manifest=experiment,
        final_lock_path=lock_path,
        authorized_by="reviewer",
        authorized_at=LATER,
    )
    authorization_path = _write(
        root / "design" / "transfer_panel_authorization.json",
        authorization,
        mode=0o444,
    )
    copied_authorization_path = _write(
        tmp_path / "copied-transfer-panel-authorization.json",
        authorization,
    )
    tasks = tmp_path / "tasks"
    _tasks(tasks, list(FROZEN_RETROSPECTIVE_TRANSFER_PANEL))
    base_config = _write(
        tmp_path / "base.json",
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
            # Harbor defaults this omitted type to Docker, then records the
            # explicit normalized type in the result config.
            "environment": {},
            "n_concurrent_trials": 4,
        },
    )
    replay_root = root / "final" / "fixed-panel-replay"
    with pytest.raises(
        CapabilityImprovementError,
        match="canonical experiment marker",
    ):
        build_final_replay_plan(
            experiment_root=root,
            experiment_manifest=experiment,
            final_lock_path=lock_path,
            transfer_panel_authorization_path=copied_authorization_path,
            checkpoint_pack_roots=None,
            tasks_root=tasks,
            base_config_path=base_config,
            output_root=replay_root,
            jobs_dir=tmp_path / "results",
            created_at=LATER,
        )
    assert not replay_root.exists()
    replay = build_final_replay_plan(
        experiment_root=root,
        experiment_manifest=experiment,
        final_lock_path=lock_path,
        transfer_panel_authorization_path=authorization_path,
        checkpoint_pack_roots=None,
        tasks_root=tasks,
        base_config_path=base_config,
        output_root=replay_root,
        jobs_dir=tmp_path / "results",
        created_at=LATER,
    )
    assert replay["replay_labels"] == list(CHECKPOINT_LABELS)
    assert replay["endpoint_labels"] == ["C25"]
    assert replay["expected_trial_count"] == 60
    assert len(replay["jobs"]) == 6
    assert all(item["expected_trial_count"] == 10 for item in replay["jobs"])

    for job in replay["jobs"]:
        result_root = Path(job["expected_result_root"])
        result_root.mkdir(parents=True)
        planned_config = json.loads(
            (replay_root / job["config_path"]).read_text(encoding="utf-8")
        )
        planned_config["environment"]["type"] = "docker"
        planned_config.pop("n_concurrent_trials")
        planned_config.pop("retry")
        _write(result_root / "config.json", planned_config)
        _write(result_root / "result.json", {"n_total_trials": 10})
        checkpoint_score = int(job["label"][1:]) / 100
        for index, protocol_id in enumerate(FROZEN_RETROSPECTIVE_TRANSFER_PANEL):
            score = 0.5 + checkpoint_score + index / 1000
            _write(
                result_root / f"trial-{index}" / "result.json",
                {
                    "task_id": {"path": f"benchmarks/libgen/tasks/{protocol_id}"},
                    "trial_name": f"{job['label']}-{protocol_id}",
                    "verifier_result": {
                        "rewards": {
                            "reward": score,
                            "t2_exact_required_family_recall": score,
                            "t2_required_family_f1": score,
                            "t3_molecular_transition_f1": score,
                            "t3_state_f1": score,
                            "t3_typed_edge_f1": score,
                        }
                    },
                    "exception_info": None,
                },
            )
    replay_path = replay_root / "manifest.json"
    tampered_replay = copy.deepcopy(replay)
    tampered_replay.pop("replay_digest")
    tampered_replay["panel"]["protocol_ids"][-1] = "substituted_protocol"
    tampered_replay["panel"]["panel_digest"] = canonical_digest(
        {
            key: value
            for key, value in tampered_replay["panel"].items()
            if key != "panel_digest"
        }
    )
    tampered_replay = with_digest(tampered_replay, "replay_digest")
    _write(replay_path, tampered_replay, mode=0o444)
    with pytest.raises(CapabilityImprovementError):
        build_final_evaluation_report(
            replay_manifest_path=replay_path,
            result_roots=None,
            created_at=LATER,
        )
    _write(replay_path, replay, mode=0o444)

    c0_job = next(item for item in replay["jobs"] if item["label"] == "C0")
    c0_trial_path = Path(c0_job["expected_result_root"]) / "trial-0" / "result.json"
    c0_trial = json.loads(c0_trial_path.read_text(encoding="utf-8"))
    invalid_trial = copy.deepcopy(c0_trial)
    invalid_trial["verifier_result"]["rewards"]["reward"] = True
    _write(c0_trial_path, invalid_trial)
    with pytest.raises(CapabilityImprovementError, match="nonnumeric reward"):
        build_final_evaluation_report(
            replay_manifest_path=replay_path,
            result_roots=None,
            created_at=LATER,
        )
    _write(c0_trial_path, c0_trial)

    report = build_final_evaluation_report(
        replay_manifest_path=replay_path,
        result_roots=None,
        created_at=LATER,
    )
    assert len(report["conditions"]) == 6
    assert len(report["paired_changes_from_c0"]) == 30
    transition = next(
        item
        for item in report["paired_changes_from_c0"]
        if item["label"] == "C25" and item["metric"] == "t3_molecular_transition_f1"
    )
    assert transition["mean_change"] == pytest.approx(0.25)
    assert transition["bootstrap_interval"]["lower"] == pytest.approx(0.25)


def test_review_summary_records_reviewer_mode_without_forking_lineage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "experiment"
    experiment = _experiment(root)
    packet = _packet(tmp_path, experiment)
    proposal_path, _, _ = _proposal(root, experiment, packet)
    for reviewer_kind in ("independent_codex", "human"):
        path = _decision(proposal_path, reviewer_kind=reviewer_kind)
        summary_path = path.with_name(f"{reviewer_kind}-summary.json")
        _, summary = write_or_validate_capability_review_summary(
            proposal_path=proposal_path,
            decision_path=path,
            output_path=summary_path,
        )
        assert summary["branch"] == "cumulative"
        assert summary["reviewer_kind"] == reviewer_kind


def test_decision_admission_allows_only_one_supported_procedural_update(
    tmp_path: Path,
) -> None:
    root = tmp_path / "experiment"
    experiment = _experiment(root)
    packet = _packet(tmp_path, experiment)
    proposal_path, _, proposal = _proposal(root, experiment, packet)
    two_units = copy.deepcopy(proposal)
    two_units.pop("proposal_digest")
    second = copy.deepcopy(two_units["change_units"][0])
    second["change_id"] = "transition-accounting-second"
    two_units["change_units"].append(second)
    two_units = with_digest(two_units, "proposal_digest")
    _write(proposal_path, two_units)
    decision = create_decision_template(
        proposal_path=proposal_path,
        reviewer_kind="human",
        reviewer_id="reviewer",
        started_at=NOW,
    )
    decision_path = _write(proposal_path.with_name("admission-decision.json"), decision)
    record_change_decision(
        proposal_path=proposal_path,
        decision_path=decision_path,
        change_id="transition-accounting",
        disposition="accept",
        rationale="Recurring root error supports one procedural update.",
    )
    with pytest.raises(CapabilityImprovementError, match="at most one"):
        record_change_decision(
            proposal_path=proposal_path,
            decision_path=decision_path,
            change_id="transition-accounting-second",
            disposition="accept",
            rationale="A second update is outside the batch budget.",
        )

    unsupported = copy.deepcopy(proposal)
    unsupported.pop("proposal_digest")
    unsupported["change_units"][0]["admission_basis"] = "insufficient"
    unsupported = with_digest(unsupported, "proposal_digest")
    unsupported_path = _write(
        proposal_path.with_name("unsupported-proposal.json"), unsupported
    )
    unsupported_decision = create_decision_template(
        proposal_path=unsupported_path,
        reviewer_kind="human",
        reviewer_id="reviewer",
        started_at=NOW,
    )
    unsupported_decision_path = _write(
        proposal_path.with_name("unsupported-decision.json"),
        unsupported_decision,
    )
    with pytest.raises(CapabilityImprovementError, match="lacks recurring"):
        record_change_decision(
            proposal_path=unsupported_path,
            decision_path=unsupported_decision_path,
            change_id="transition-accounting",
            disposition="accept",
            rationale="Unsupported updates cannot pass admission.",
        )


def test_improvement_schemas_and_codex_draft_projections_are_valid() -> None:
    for path in SCHEMA_ROOT.glob("*.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))

    for filename in (
        "capability_proposal_draft.schema.json",
        "capability_decision_draft.schema.json",
    ):
        canonical = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        projected = compile_codex_output_schema(canonical)
        assert "$schema" not in projected
        assert "$id" not in projected
        for node in _schema_nodes(projected):
            assert "oneOf" not in node
            assert "uniqueItems" not in node
            assert (
                not {
                    "allOf",
                    "not",
                    "dependentRequired",
                    "dependentSchemas",
                    "if",
                    "then",
                    "else",
                }
                & node.keys()
            )

    proposal = compile_codex_output_schema(
        json.loads(
            (SCHEMA_ROOT / "capability_proposal_draft.schema.json").read_text(
                encoding="utf-8"
            )
        )
    )
    invalid = copy.deepcopy(proposal)
    invalid["$defs"]["unit"]["properties"]["leakage_attestation"].pop("type")
    with pytest.raises(CapabilityImprovementError, match="leakage_attestation"):
        validate_codex_output_schema(invalid)


def test_native_codex_runner_stops_an_idle_process_after_a_draft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    inputs = workspace / "inputs"
    outputs = workspace / "outputs"
    inputs.mkdir(parents=True)
    outputs.mkdir()
    prompt = inputs / "prompt.md"
    schema = inputs / "schema.json"
    prompt.write_text("Produce the draft.\n", encoding="utf-8")
    schema.write_text("{}\n", encoding="utf-8")
    draft = outputs / "proposal_draft.json"
    request = LocalCodexRunRequest(
        workspace=workspace,
        prompt_path=prompt,
        output_schema_path=schema,
        draft_output_path=draft,
        event_log_path=outputs / "proposal.events.jsonl",
        stderr_log_path=outputs / "proposal.stderr.log",
        model="gpt-5.6-sol",
        version="0.147.0",
        reasoning_effort="max",
        codex_executable="codex",
        idle_timeout_seconds=0.02,
        hard_timeout_seconds=1.0,
    )
    launch = IsolatedWorkerLaunch(
        command=("docker", "compose", "run", "worker"),
        cleanup_command=("docker", "compose", "down"),
        environment={},
        workspace=workspace,
        auth_file=tmp_path / "auth.json",
        compose_path=tmp_path / "compose.yaml",
        project_name="test",
    )

    class FakeComposeProcess:
        pid = 999_999

        def __init__(self) -> None:
            self.stopped = False
            draft.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "libstruct.libgen_capability_proposal_draft.v1"
                        ),
                        "change_units": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

        def poll(self):
            return -15 if self.stopped else None

        def wait(self, timeout=None):
            del timeout
            return -15

    process = FakeComposeProcess()
    monkeypatch.setattr(
        learning_module,
        "prepare_isolated_codex_launch",
        lambda _request: launch,
    )
    monkeypatch.setattr(
        learning_module,
        "run_compose_cleanup",
        lambda _launch: None,
    )
    monkeypatch.setattr(
        learning_module,
        "validate_isolated_codex_outputs",
        lambda _request: None,
    )
    monkeypatch.setattr(
        learning_module.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        learning_module,
        "_terminate_process_group",
        lambda _process: setattr(process, "stopped", True),
    )
    result = run_native_codex(request)
    assert result.completion_reason == "idle_after_draft"
    assert result.returncode != 0
    assert result.elapsed_seconds < 1


def test_initial_pack_is_valid_and_tamper_evident(tmp_path: Path) -> None:
    manifest = validate_capability_pack(PACK_ROOT)
    copied = tmp_path / "pack"
    copy_capability_pack(PACK_ROOT, copied)
    assert validate_capability_pack(copied)["pack_digest"] == manifest["pack_digest"]
    (copied / "PLAYBOOK.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(CapabilityImprovementError, match="do not match manifest"):
        validate_capability_pack(copied)
