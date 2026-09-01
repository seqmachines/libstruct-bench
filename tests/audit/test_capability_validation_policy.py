from __future__ import annotations

import copy
import json
import shutil
import threading
import zipfile
from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import sha256_file, write_json_atomic
from libstruct_bench.improvement import experiment as experiment_module
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    copy_capability_pack,
    validate_capability_pack,
    with_digest,
)
from libstruct_bench.improvement.harbor import (
    build_harbor_job_config,
    prepare_capability_harbor_integration,
)
from libstruct_bench.improvement.isolation import (
    _workspace_exemplar_memory_record,
    prepare_isolated_worker_workspace,
)
from libstruct_bench.improvement.validation import (
    VALIDATION_AGGREGATE_FIELDS,
    VALIDATION_CHECKPOINT_LABELS,
    VALIDATION_PROTOCOL_IDS,
    _isolated_exemplar_workspace_manifest_paths,
    assert_validation_learning_artifact_isolated,
    build_validation_access_policy,
    build_validation_aggregate,
    build_validation_feedback_projection,
    build_validation_guidance_record,
    build_validation_isolation_audit,
    build_validation_plan,
    build_validation_result_bundle,
    record_validation_aggregate,
    scan_validation_pack_leakage,
    scan_validation_feedback_copy,
    validate_complete_validation_curve,
    validate_required_validation_aggregate,
    validate_validation_access_policy,
    validate_validation_aggregate,
    validate_validation_feedback_projection,
    validate_validation_isolation_audit,
    validate_validation_plan,
    validation_panel_commitment_digest,
    validation_panel_commitment_payload,
)
from libstruct_bench.improvement.split_design import FINAL_DEVELOPMENT_BATCHES
from libstruct_bench.improvement.workflow import _build_checkpoint_runtime
from tests.audit.capability_memory_fixtures import portable_exemplar_memory


NOW = "2026-08-22T12:00:00Z"
EXPERIMENT_DIGEST = "a" * 64
TASK_DIGEST = "c" * 64
REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPO_ROOT / "improvement" / "capability_pack"
SHARED_SEQUENCE = "TTGGAACCTTGG"
SEQUENCES = (
    "ACGTTGCAACGT",
    "CGTACCGTAGCA",
    "GATCTAGCGTAC",
    "TGCAGATCCGTA",
    "AGCTTCGAGTCA",
)


def _policy(
    tmp_path: Path, *, include_native_documents: bool = False
) -> tuple[dict, Path, Path]:
    sources = tmp_path / "sources"
    truth = tmp_path / "truth"
    for index, protocol_id in enumerate(VALIDATION_PROTOCOL_IDS):
        source = sources / protocol_id / "source.txt"
        source.parent.mkdir(parents=True)
        source.write_text(
            f"source for {protocol_id}: {SEQUENCES[index]} {SHARED_SEQUENCE}\n",
            encoding="utf-8",
        )
        groundtruth = truth / protocol_id / "groundtruth_oligos.json"
        groundtruth.parent.mkdir(parents=True)
        groundtruth.write_text(
            json.dumps(
                {
                    "protocol_name": f"Validation protocol {index}",
                    "sequence": SEQUENCES[index],
                    "library": f"AAA[BARCODE:8]{SEQUENCES[index]}",
                }
            ),
            encoding="utf-8",
        )
    training = [
        protocol_id
        for batch in FINAL_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    ]
    for protocol_id in training:
        source = sources / protocol_id / "source.txt"
        source.parent.mkdir(parents=True)
        source.write_text(
            f"training source {protocol_id}: {SHARED_SEQUENCE} Validation protocol 0\n",
            encoding="utf-8",
        )
        groundtruth = truth / protocol_id / "groundtruth_oligos.json"
        groundtruth.parent.mkdir(parents=True)
        groundtruth.write_text(
            json.dumps(
                {
                    "protocol_name": f"Training {protocol_id}",
                    "sequence": SHARED_SEQUENCE,
                }
            ),
            encoding="utf-8",
        )
    if include_native_documents:
        fitz = pytest.importorskip("fitz")
        openpyxl = pytest.importorskip("openpyxl")
        pdf_path = sources / VALIDATION_PROTOCOL_IDS[0] / "sequence-table.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "PDF sequence CCAATGGTCCAA")
        document.save(pdf_path)
        document.close()
        workbook = openpyxl.Workbook()
        workbook.active["A1"] = "GGCCTTAAGGCC"
        workbook.save(sources / VALIDATION_PROTOCOL_IDS[1] / "sequence-table.xlsx")
        workbook.close()
        with zipfile.ZipFile(
            sources / VALIDATION_PROTOCOL_IDS[2] / "sequence-table.docx",
            mode="w",
        ) as archive:
            archive.writestr(
                "word/document.xml",
                (
                    '<w:document xmlns:w="urn:test"><w:body><w:p><w:r><w:t>'
                    "AATTCCGGAATT"
                    "</w:t></w:r></w:p></w:body></w:document>"
                ),
            )
    return (
        build_validation_access_policy(
            validation_panel_commitment_sha256=(validation_panel_commitment_digest()),
            source_root=sources,
            groundtruth_root=truth,
            initial_pack_root=PACK_ROOT,
            created_at=NOW,
        ),
        sources,
        truth,
    )


def test_validation_policy_extracts_native_pdf_and_xlsx_sequences(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path, include_native_documents=True)
    assert "CCAATGGTCCAA" in policy["forbidden_sequences"]
    assert "GGCCTTAAGGCC" in policy["forbidden_sequences"]
    assert "AATTCCGGAATT" in policy["forbidden_sequences"]
    assert policy["sensitive_extraction"] == {
        "formats": [
            "utf8_text",
            "pdf_native_text_no_ocr",
            "xlsx_cell_values",
            "docx_xml_text",
        ],
        "unreadable_supported_document_policy": "fail_closed",
    }


def _checkpoint(
    tmp_path: Path,
    *,
    checkpoint_label: str,
    experiment_root: Path | None = None,
) -> Path:
    root = (experiment_root or tmp_path) / "checkpoints" / checkpoint_label
    if root.exists():
        return root
    root.mkdir(parents=True)
    copy_capability_pack(PACK_ROOT, root / "pack", freeze=True)
    pack = validate_capability_pack(root / "pack")
    count = int(checkpoint_label[1:])
    memory = portable_exemplar_memory(root / "memory", count)
    runtime = _build_checkpoint_runtime(
        checkpoint_id=checkpoint_label,
        pack_digest=pack["pack_digest"],
        exemplar_memory=memory,
    )
    write_json_atomic(root / "runtime.json", runtime, mode=0o444)
    order = count // 5
    checkpoint = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_checkpoint.v1",
            "checkpoint_id": checkpoint_label,
            "experiment_digest": EXPERIMENT_DIGEST,
            "branch": "cumulative",
            "protocol_count": count,
            "batch_id": None if count == 0 else f"B{order}",
            "parent_checkpoint_id": (None if count == 0 else f"C{count - 5}"),
            "validation_guidance": (
                None
                if count == 0
                else {
                    "checkpoint_label": f"C{count - 5}",
                    "aggregate_digest": "1" * 64,
                    "aggregate_sha256": "2" * 64,
                    "workspace_digest": "3" * 64,
                    "workspace_manifest_sha256": "4" * 64,
                }
            ),
            "pack_digest": pack["pack_digest"],
            "pack_manifest_sha256": sha256_file(root / "pack" / "manifest.json"),
            "exemplar_memory": memory,
            "runtime_manifest_sha256": sha256_file(root / "runtime.json"),
            "proposal_sha256": None if count == 0 else "d" * 64,
            "decision_sha256": None if count == 0 else "e" * 64,
            "application_sha256": None if count == 0 else "f" * 64,
            "status": "baseline" if count == 0 else "procedural_and_exemplar",
            "frozen": True,
            "created_at": NOW,
        },
        "checkpoint_digest",
    )
    write_json_atomic(root / "checkpoint.json", checkpoint, mode=0o444)
    return root


def _harbor_result(
    tmp_path: Path,
    *,
    checkpoint_label: str,
    checkpoint_root: Path | None = None,
    result_name: str | None = None,
) -> Path:
    checkpoint_root = checkpoint_root or _checkpoint(
        tmp_path, checkpoint_label=checkpoint_label
    )
    tasks = tmp_path / "validation-tasks"
    for protocol_id in VALIDATION_PROTOCOL_IDS:
        task_file = tasks / protocol_id / "task.toml"
        if not task_file.exists():
            task_file.parent.mkdir(parents=True)
            task_file.write_text('environment_mode = "separate"\n', encoding="utf-8")
    integration_root = tmp_path / "integrations" / checkpoint_label
    if not integration_root.exists():
        prepare_capability_harbor_integration(
            pack_root=checkpoint_root,
            tasks_root=tasks,
            protocol_ids=VALIDATION_PROTOCOL_IDS,
            output_root=integration_root,
            created_at=NOW,
        )
    base_config = tmp_path / "validation-base.json"
    if not base_config.exists():
        write_json_atomic(
            base_config,
            {
                "agents": [
                    {
                        "name": "codex",
                        "model_name": "gpt-5.6-sol",
                        "kwargs": {"version": "0.147.0", "reasoning_effort": "max"},
                    }
                ],
                "datasets": [{}],
                "environment": {"type": "docker"},
                "n_concurrent_trials": 4,
            },
        )
    root = tmp_path / (result_name or f"validation-{checkpoint_label}")
    root.mkdir(parents=True, exist_ok=True)
    build_harbor_job_config(
        base_config_path=base_config,
        integration_root=integration_root,
        tasks_root=tasks,
        protocol_ids=VALIDATION_PROTOCOL_IDS,
        job_name=f"validation-{checkpoint_label}",
        jobs_dir=tmp_path / "jobs",
        output_path=root / "config.json",
    )
    write_json_atomic(
        root / "result.json",
        {"n_total_trials": 5, "test_run_identity": checkpoint_label},
    )
    checkpoint = json.loads((checkpoint_root / "checkpoint.json").read_text())
    for index, protocol_id in enumerate(VALIDATION_PROTOCOL_IDS):
        value = 0.5 + index * 0.1
        rewards = {
            "reward": value,
            "t2_exact_required_family_recall": value,
            "t2_required_family_f1": value,
            "t3_molecular_transition_f1": value,
            "t3_state_f1": value,
            "t3_typed_edge_f1": value,
        }
        trial_root = root / f"trial-{index}"
        write_json_atomic(
            trial_root / "result.json",
            {
                "task_id": {"path": f"benchmarks/libgen/tasks/{protocol_id}"},
                "verifier_result": {"rewards": rewards},
            },
        )
        retrieval_digest = f"{index + 1:x}" * 64
        target_work_record_sha256 = f"{index + 6:x}" * 64
        usage = with_digest(
            {
                "schema_version": "libstruct.libgen_exemplar_usage.v1",
                "query_digest": "a" * 64,
                "target_work_record_sha256": target_work_record_sha256,
                "retrieval_digest": retrieval_digest,
                "scoring_scope": "diagnostic_only_excluded_from_benchmark_scores",
                "score_inclusion": False,
                "retrieved_exemplars": [],
            },
            "usage_digest",
        )
        guard = with_digest(
            {
                "schema_version": ("libstruct.libgen_target_evidence_guard_report.v1"),
                "status": "pass",
                "memory_digest": checkpoint["exemplar_memory"]["memory_digest"],
                "catalog_digest": checkpoint["exemplar_memory"]["catalog_digest"],
                "retrieval_digest": retrieval_digest,
                "usage_digest": usage["usage_digest"],
                "target_work_record_sha256": target_work_record_sha256,
                "finding_count": 0,
                "checked_overlap_count": 0,
                "findings": [],
            },
            "report_digest",
        )
        diagnostic_root = trial_root / "artifacts" / "logs" / "artifacts"
        write_json_atomic(diagnostic_root / "exemplar_usage.json", usage)
        write_json_atomic(diagnostic_root / "target_evidence_guard.json", guard)
    return root


def _aggregate(
    tmp_path: Path,
    *,
    policy: dict,
    checkpoint_label: str,
    experiment_root: Path | None = None,
) -> dict:
    checkpoint_root = _checkpoint(
        tmp_path,
        checkpoint_label=checkpoint_label,
        experiment_root=experiment_root,
    )
    return build_validation_aggregate(
        experiment_digest=EXPERIMENT_DIGEST,
        checkpoint_label=checkpoint_label,
        checkpoint_root=checkpoint_root,
        validation_access_policy=policy,
        result_root=_harbor_result(
            tmp_path,
            checkpoint_label=checkpoint_label,
            checkpoint_root=checkpoint_root,
        ),
        created_at=NOW,
    )


def test_validation_commitment_and_policy_are_exact_and_private(
    tmp_path: Path,
) -> None:
    assert validation_panel_commitment_payload() == {
        "set_id": "fixed-validation-panel-v1",
        "protocol_count": 5,
        "protocol_ids": list(VALIDATION_PROTOCOL_IDS),
        "evaluation_checkpoints": list(VALIDATION_CHECKPOINT_LABELS),
        "learning_visibility": ("five_protocol_macro_aggregate_only_no_example_memory"),
    }
    policy, _, _ = _policy(tmp_path)
    assert len(policy["protected_trees"]) == 10
    assert SHARED_SEQUENCE not in policy["forbidden_sequences"]
    assert "validation protocol 0" not in policy["forbidden_terms"]
    assert policy["allowlist_provenance"]["shared_alias_count_removed"] == 1
    assert policy["allowlist_provenance"]["shared_sequence_count_removed"] >= 1
    assert policy["agent_visibility"] == (
        "none_orchestrator_only_except_sanitized_aggregate"
    )
    assert policy["aggregate_feedback_contract"]["may_guide_next_update"] is True
    assert (
        policy["aggregate_feedback_contract"]["may_enter_synthetic_fixtures"] is False
    )
    path = tmp_path / "validation-access.json"
    write_json_atomic(path, policy)
    assert validate_validation_access_policy(path) == policy


def test_validation_policy_rejects_changed_protected_tree(tmp_path: Path) -> None:
    policy, sources, _ = _policy(tmp_path)
    path = tmp_path / "validation-access.json"
    write_json_atomic(path, policy)
    source = sources / VALIDATION_PROTOCOL_IDS[0] / "source.txt"
    source.write_text(
        source.read_text(encoding="utf-8") + "changed\n", encoding="utf-8"
    )
    with pytest.raises(CapabilityImprovementError, match="protected tree changed"):
        validate_validation_access_policy(path)


def test_validation_plan_has_six_jobs_and_thirty_trials(tmp_path: Path) -> None:
    policy, _, _ = _policy(tmp_path)
    plan = build_validation_plan(
        experiment_digest=EXPERIMENT_DIGEST,
        validation_access_policy=policy,
        task_bundle_sha256=TASK_DIGEST,
        created_at=NOW,
    )
    assert plan["checkpoint_labels"] == list(VALIDATION_CHECKPOINT_LABELS)
    assert plan["expected_job_count"] == 6
    assert plan["expected_trial_count"] == 30
    assert [item["guidance_target_batch"] for item in plan["conditions"]] == [
        "B1",
        "B2",
        "B3",
        "B4",
        "B5",
        None,
    ]
    path = tmp_path / "validation-plan.json"
    write_json_atomic(path, plan)
    assert validate_validation_plan(path, validation_access_policy=policy) == plan


def test_validation_aggregate_exposes_only_macro_metrics_and_counts(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    aggregate = _aggregate(tmp_path, policy=policy, checkpoint_label="C5")
    assert set(aggregate) == set(VALIDATION_AGGREGATE_FIELDS)
    assert aggregate["planned_trials"] == 5
    assert aggregate["scored_trials"] == 5
    assert aggregate["unscored_trials"] == 0
    assert aggregate["guidance_target_batch"] == "B2"
    assert aggregate["macro_means"]["t3_state_f1"] == pytest.approx(0.7)
    serialized = json.dumps(aggregate)
    assert not any(protocol_id in serialized for protocol_id in VALIDATION_PROTOCOL_IDS)
    assert "protocol_results" not in aggregate
    assert "result_root" not in aggregate
    assert "error_analysis" not in aggregate
    feedback = build_validation_feedback_projection(aggregate)
    assert "checkpoint_digest" not in feedback
    assert "harbor_config_sha256" not in feedback
    assert feedback["aggregate_digest"] == aggregate["aggregate_digest"]
    feedback_path = tmp_path / "feedback.json"
    write_json_atomic(feedback_path, feedback)
    assert (
        validate_validation_feedback_projection(
            feedback_path, source_aggregate=aggregate
        )
        == feedback
    )
    path = tmp_path / "aggregate.json"
    write_json_atomic(path, aggregate)
    assert (
        validate_validation_aggregate(
            path,
            experiment_digest=EXPERIMENT_DIGEST,
            validation_access_policy=policy,
            expected_checkpoint_label="C5",
            expected_pack_digest=aggregate["pack_digest"],
        )
        == aggregate
    )
    with pytest.raises(
        CapabilityImprovementError, match="validation learning boundary"
    ):
        assert_validation_learning_artifact_isolated(
            {"learning_ledger": aggregate},
            validation_access_policy=policy,
            label="proposal",
        )


def test_validation_aggregate_schema_rejects_protocol_rows(tmp_path: Path) -> None:
    policy, _, _ = _policy(tmp_path)
    aggregate = _aggregate(tmp_path, policy=policy, checkpoint_label="C0")
    invalid = copy.deepcopy(aggregate)
    invalid.pop("aggregate_digest")
    invalid["protocol_results"] = [{"protocol_id": VALIDATION_PROTOCOL_IDS[0]}]
    invalid = with_digest(invalid, "aggregate_digest")
    path = tmp_path / "invalid-aggregate.json"
    write_json_atomic(path, invalid)
    with pytest.raises(CapabilityImprovementError, match="schema error"):
        validate_validation_aggregate(path)


def test_record_validation_aggregate_uses_frozen_pack_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, _, _ = _policy(tmp_path)
    root = tmp_path / "experiment"
    policy_path = root / "design" / "validation_access_policy.json"
    write_json_atomic(policy_path, policy, mode=0o444)
    experiment = {
        "experiment_digest": EXPERIMENT_DIGEST,
        "validation_panel": {
            "access_policy": {
                "path": "design/validation_access_policy.json",
                "digest": policy["policy_digest"],
                "sha256": sha256_file(policy_path),
            }
        },
    }
    monkeypatch.setattr(
        experiment_module,
        "validate_experiment_manifest",
        lambda *_args, **_kwargs: experiment,
    )
    checkpoint_root = _checkpoint(
        tmp_path,
        checkpoint_label="C0",
        experiment_root=root,
    )
    output_path, aggregate = record_validation_aggregate(
        experiment_root=root,
        checkpoint_label="C0",
        result_root=_harbor_result(
            tmp_path,
            checkpoint_label="C0",
            checkpoint_root=checkpoint_root,
        ),
        created_at=NOW,
    )
    assert output_path == root / "validation" / "aggregates" / "C0.json"
    assert (
        aggregate["pack_digest"]
        == validate_capability_pack(checkpoint_root / "pack")["pack_digest"]
    )
    with pytest.raises(CapabilityImprovementError, match="refusing to overwrite"):
        record_validation_aggregate(
            experiment_root=root,
            checkpoint_label="C0",
            result_root=_harbor_result(
                tmp_path,
                checkpoint_label="C0",
                checkpoint_root=checkpoint_root,
            ),
            created_at=NOW,
        )


def test_validation_result_bundle_salvages_clean_trials_and_one_retry(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    checkpoint_root = _checkpoint(tmp_path, checkpoint_label="C0")
    primary = _harbor_result(
        tmp_path,
        checkpoint_label="C0",
        checkpoint_root=checkpoint_root,
        result_name="validation-c0-primary",
    )
    primary_config = json.loads((primary / "config.json").read_text())
    share_result = next(
        path
        for path in primary.glob("*/result.json")
        if Path(json.loads(path.read_text())["task_id"]["path"]).name == "share_seq"
    )
    retry = tmp_path / "validation-c0-share-retry"
    write_json_atomic(
        retry / "config.json",
        {
            "agents": primary_config["agents"],
            "environment": primary_config["environment"],
            "tasks": [
                {
                    "path": (
                        Path(primary_config["datasets"][0]["path"]) / "share_seq"
                    ).as_posix()
                }
            ],
            "extra_instruction_paths": primary_config["extra_instruction_paths"],
            "artifacts": primary_config["artifacts"],
            "agent_timeout_multiplier": 2.0,
            "n_concurrent_trials": 1,
        },
    )
    write_json_atomic(retry / "result.json", {"n_total_trials": 1})
    (retry / "share-retry").mkdir(parents=True)
    shutil.copyfile(share_result, retry / "share-retry" / "result.json")
    shutil.copytree(
        share_result.parent / "artifacts",
        retry / "share-retry" / "artifacts",
    )

    with pytest.raises(
        CapabilityImprovementError,
        match="multiple successful validation attempts",
    ):
        build_validation_result_bundle(
            canonical_config_path=primary / "config.json",
            attempt_roots=[primary, retry],
            output_root=tmp_path / "duplicate-bundle",
            created_at=NOW,
        )

    shutil.rmtree(share_result.parent)
    bundle_root, bundle = build_validation_result_bundle(
        canonical_config_path=primary / "config.json",
        attempt_roots=[primary, retry],
        output_root=tmp_path / "validation-c0-bundle",
        created_at=NOW,
    )
    assert bundle["attempt_count"] == 2
    assert [item["protocol_id"] for item in bundle["selections"]] == list(
        VALIDATION_PROTOCOL_IDS
    )
    assert (
        next(
            item for item in bundle["selections"] if item["protocol_id"] == "share_seq"
        )["attempt_id"]
        == retry.name
    )
    aggregate = build_validation_aggregate(
        experiment_digest=EXPERIMENT_DIGEST,
        checkpoint_label="C0",
        checkpoint_root=checkpoint_root,
        validation_access_policy=policy,
        result_root=bundle_root,
        created_at=NOW,
    )
    assert aggregate["scored_trials"] == 5
    assert aggregate["result_file_count"] == 6
    next(
        bundle_root.glob("*/artifacts/logs/artifacts/target_evidence_guard.json")
    ).unlink()
    with pytest.raises(
        CapabilityImprovementError,
        match="missing its target-evidence guard artifact",
    ):
        build_validation_aggregate(
            experiment_digest=EXPERIMENT_DIGEST,
            checkpoint_label="C0",
            checkpoint_root=checkpoint_root,
            validation_access_policy=policy,
            result_root=bundle_root,
            created_at=NOW,
        )


def test_each_batch_is_gated_by_preceding_validation_aggregate(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    root = tmp_path / "experiment"
    c0 = _aggregate(
        tmp_path,
        policy=policy,
        checkpoint_label="C0",
        experiment_root=root,
    )
    write_json_atomic(root / "validation" / "aggregates" / "C0.json", c0)
    assert (
        validate_required_validation_aggregate(
            experiment_root=root,
            experiment_digest=EXPERIMENT_DIGEST,
            validation_access_policy=policy,
            batch_id="B1",
            expected_pack_digest=c0["pack_digest"],
        )
        == c0
    )
    with pytest.raises(CapabilityImprovementError, match="does not exist"):
        validate_required_validation_aggregate(
            experiment_root=root,
            experiment_digest=EXPERIMENT_DIGEST,
            validation_access_policy=policy,
            batch_id="B2",
            expected_pack_digest=c0["pack_digest"],
        )


def test_final_lock_curve_requires_all_six_validation_aggregates(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    root = tmp_path / "experiment"
    for label in VALIDATION_CHECKPOINT_LABELS[:-1]:
        aggregate = _aggregate(
            tmp_path,
            policy=policy,
            checkpoint_label=label,
            experiment_root=root,
        )
        write_json_atomic(
            root / "validation" / "aggregates" / f"{label}.json", aggregate
        )
    with pytest.raises(CapabilityImprovementError, match="does not exist"):
        validate_complete_validation_curve(
            experiment_root=root,
            experiment_digest=EXPERIMENT_DIGEST,
            validation_access_policy=policy,
        )
    c25 = _aggregate(
        tmp_path,
        policy=policy,
        checkpoint_label="C25",
        experiment_root=root,
    )
    write_json_atomic(root / "validation" / "aggregates" / "C25.json", c25)
    curve = validate_complete_validation_curve(
        experiment_root=root,
        experiment_digest=EXPERIMENT_DIGEST,
        validation_access_policy=policy,
    )
    assert curve["checkpoint_labels"] == list(VALIDATION_CHECKPOINT_LABELS)
    assert len(curve["aggregate_records"]) == 6
    assert curve["expected_trial_count"] == 30


def test_learning_documents_and_synthetic_fixtures_cannot_contain_validation_examples(
    tmp_path: Path,
) -> None:
    policy, sources, _ = _policy(tmp_path)
    with pytest.raises(
        CapabilityImprovementError, match="validation learning boundary"
    ):
        assert_validation_learning_artifact_isolated(
            {
                "protocol_ids": [VALIDATION_PROTOCOL_IDS[0]],
                "artifacts": [
                    {
                        "protocol_id": VALIDATION_PROTOCOL_IDS[0],
                        "role": "verifier_error_analysis",
                    }
                ],
            },
            validation_access_policy=policy,
            label="packet",
        )
    pack = tmp_path / "pack"
    fixture = pack / "synthetic_tests" / "invalid" / "validation-copy.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(json.dumps({"sequence": SEQUENCES[0]}), encoding="utf-8")
    issues = scan_validation_pack_leakage(pack, policy)
    assert any("exact sequence overlap" in item for item in issues)
    fixture.write_bytes(
        (sources / VALIDATION_PROTOCOL_IDS[0] / "source.txt").read_bytes()
    )
    issues = scan_validation_pack_leakage(pack, policy)
    assert any("protected validation artifact copied" in item for item in issues)


def test_validation_protocol_matching_does_not_block_training_identifier_prefixes(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    assert_validation_learning_artifact_isolated(
        {"protocol_id": "smart_seq2"},
        validation_access_policy=policy,
        label="training packet",
    )
    with pytest.raises(
        CapabilityImprovementError, match="validation learning boundary"
    ):
        assert_validation_learning_artifact_isolated(
            {"protocol_id": "smart_seq"},
            validation_access_policy=policy,
            label="validation packet",
        )


def test_empty_candidate_tree_is_a_valid_leakage_scan_target(tmp_path: Path) -> None:
    policy, _, _ = _policy(tmp_path)
    candidate_root = tmp_path / "empty-candidates"
    candidate_root.mkdir()
    assert scan_validation_pack_leakage(candidate_root, policy) == []


def test_exemplar_isolation_discovers_second_review_workspaces(
    tmp_path: Path,
) -> None:
    cumulative = tmp_path / "rounds" / "B2" / "cumulative"
    expected = [
        cumulative / "critic-workspace" / "workspace_manifest.json",
        cumulative / "critic-workspace-r1" / "workspace_manifest.json",
        cumulative / "review-workspace-r1" / "workspace_manifest.json",
    ]
    ignored = cumulative / "critic-workspace-r2" / "workspace_manifest.json"
    for path in [*expected, ignored]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    assert _isolated_exemplar_workspace_manifest_paths(tmp_path) == expected


def test_validation_isolation_audit_scans_active_state_but_not_superseded_history(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    archived = tmp_path / "experiment" / "history" / "superseded" / "old"
    archived.mkdir(parents=True)
    (archived / "manifest.json").write_text(
        VALIDATION_PROTOCOL_IDS[0], encoding="utf-8"
    )
    clean = build_validation_isolation_audit(
        experiment_root=tmp_path / "experiment",
        validation_access_policy=policy,
        audited_at=NOW,
    )
    assert clean["learning_isolation"] == "pass"

    active = tmp_path / "experiment" / "rounds" / "B1" / "proposal.json"
    active.parent.mkdir(parents=True)
    active.write_text(VALIDATION_PROTOCOL_IDS[0], encoding="utf-8")
    failed = build_validation_isolation_audit(
        experiment_root=tmp_path / "experiment",
        validation_access_policy=policy,
        audited_at=NOW,
    )
    assert failed["learning_isolation"] == "fail"
    path = tmp_path / "validation-isolation.json"
    write_json_atomic(path, failed)
    assert (
        validate_validation_isolation_audit(path, validation_access_policy=policy)
        == failed
    )


def test_validation_isolation_exempts_only_packet_pinned_training_inputs(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    experiment = tmp_path / "experiment"
    batch = FINAL_DEVELOPMENT_BATCHES[0]
    external = tmp_path / "frozen-training-run" / "prediction.json"
    external.parent.mkdir(parents=True)
    external.write_text(json.dumps({"sequence": SEQUENCES[0]}), encoding="utf-8")
    artifact_sha256 = sha256_file(external)
    artifact = {
        "protocol_id": batch["protocol_ids"][0],
        "role": "prediction",
        "path": external.resolve().as_posix(),
        "sha256": artifact_sha256,
        "visibility": "agent_after_reveal",
    }
    packet = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_batch_packet.v1",
            "packet_id": "B1:cumulative:revealed",
            "experiment_digest": EXPERIMENT_DIGEST,
            "batch_id": "B1",
            "branch": "cumulative",
            "phase": "retrospective",
            "parent_pack_digest": "d" * 64,
            "protocol_ids": list(batch["protocol_ids"]),
            "reveal_state": "revealed",
            "eligibility_status": "eligible_for_improvement",
            "transfer_access_policy_digest": "e" * 64,
            "artifacts": [artifact],
            "trial_terminality": [],
            "learning_ledger": None,
        },
        "packet_digest",
    )
    round_root = experiment / "rounds" / "B1" / "cumulative"
    write_json_atomic(round_root / "packet.json", packet)
    workspace = round_root / "proposer-workspace"
    staged_relative = (
        f"inputs/evidence/{batch['protocol_ids'][0]}/0001-{artifact_sha256[:16]}.json"
    )
    staged_path = workspace / staged_relative
    staged_path.parent.mkdir(parents=True)
    shutil.copy2(external, staged_path)
    packet_view = copy.deepcopy(packet)
    packet_view["source_packet_digest"] = packet["packet_digest"]
    packet_view["packet_digest"] = None
    packet_view["artifacts"][0]["path"] = staged_relative
    write_json_atomic(workspace / "inputs" / "packet_view.json", packet_view)
    packet_view_sha256 = sha256_file(workspace / "inputs" / "packet_view.json")
    workspace_manifest = with_digest(
        {
            "schema_version": "libstruct.libgen_worker_workspace.v1",
            "mode": "improvement_worker",
            "packet_digest": packet["packet_digest"],
            "staged_files": [
                {
                    "path": "inputs/packet_view.json",
                    "role": "batch_packet_view",
                    "sha256": packet_view_sha256,
                },
                {
                    "path": staged_relative,
                    "role": "prediction",
                    "sha256": artifact_sha256,
                },
            ],
        },
        "workspace_digest",
    )
    write_json_atomic(workspace / "workspace_manifest.json", workspace_manifest)

    clean = build_validation_isolation_audit(
        experiment_root=experiment,
        validation_access_policy=policy,
        audited_at=NOW,
    )
    assert clean["learning_isolation"] == "pass"

    candidate = workspace / "candidates" / "copied-validation-example.json"
    candidate.parent.mkdir()
    candidate.write_text(json.dumps({"sequence": SEQUENCES[0]}), encoding="utf-8")
    leaked = build_validation_isolation_audit(
        experiment_root=experiment,
        validation_access_policy=policy,
        audited_at=NOW,
    )
    assert leaked["learning_isolation"] == "fail"
    assert any(
        item["category"] == "validation_exact_sequence" and item["findings"]
        for item in leaked["checks"]
    )

    candidate.unlink()
    staged_path.write_text(json.dumps({"sequence": SEQUENCES[1]}), encoding="utf-8")
    tampered = build_validation_isolation_audit(
        experiment_root=experiment,
        validation_access_policy=policy,
        audited_at=NOW,
    )
    assert tampered["learning_isolation"] == "fail"


def test_validation_isolation_exempts_only_canonical_hash_pinned_transcripts(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    experiment = tmp_path / "experiment"
    round_root = experiment / "rounds" / "B1" / "cumulative"
    proposer_events = (
        round_root / "proposer-workspace" / "outputs" / "proposal.events.jsonl"
    )
    proposer_events.parent.mkdir(parents=True)
    proposer_events.write_text(SEQUENCES[0], encoding="utf-8")
    proposal = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_proposal.v1",
            "proposal_id": "B1:cumulative:r0:transcript-fixture",
            "experiment_digest": EXPERIMENT_DIGEST,
            "branch": "cumulative",
            "batch_id": "B1",
            "checkpoint_from": "C0",
            "packet_digest": "1" * 64,
            "parent_pack_digest": "2" * 64,
            "learning_ledger_digest": "3" * 64,
            "validation_guidance": {
                "checkpoint_label": "C0",
                "aggregate_digest": "4" * 64,
                "aggregate_sha256": "5" * 64,
                "workspace_digest": "6" * 64,
                "workspace_manifest_sha256": "7" * 64,
            },
            "protocol_ids": list(FINAL_DEVELOPMENT_BATCHES[0]["protocol_ids"]),
            "proposer": {
                "agent": "codex",
                "harness": "native",
                "model": "gpt-5.6-sol",
                "version": "0.147.0",
                "reasoning_effort": "max",
                "transcript_sha256": sha256_file(proposer_events),
            },
            "revision_round": 0,
            "revision_of_proposal_digest": None,
            "revision_request_decision_digest": None,
            "change_units": [],
        },
        "proposal_digest",
    )
    proposal_path = round_root / "proposal.json"
    write_json_atomic(proposal_path, proposal)

    critic_events = round_root / "critic-workspace" / "outputs" / "critic.events.jsonl"
    critic_events.parent.mkdir(parents=True)
    critic_events.write_text(SEQUENCES[1], encoding="utf-8")
    decision = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_decision.v1",
            "decision_id": proposal["proposal_id"] + ":decision",
            "proposal_digest": proposal["proposal_digest"],
            "proposal_sha256": sha256_file(proposal_path),
            "branch": "cumulative",
            "batch_id": "B1",
            "reviewer_kind": "independent_codex",
            "reviewer": {
                "reviewer_id": "fixture-critic",
                "model": "gpt-5.6-sol",
                "version": "0.147.0",
                "transcript_sha256": sha256_file(critic_events),
            },
            "revision_round": 0,
            "review_state": "final",
            "started_at": NOW,
            "completed_at": NOW,
            "change_decisions": [],
        },
        "decision_digest",
    )
    write_json_atomic(round_root / "decision.json", decision)

    clean = build_validation_isolation_audit(
        experiment_root=experiment,
        validation_access_policy=policy,
        audited_at=NOW,
    )
    assert clean["learning_isolation"] == "pass"

    copied = round_root / "copied-critic.events.jsonl"
    shutil.copy2(critic_events, copied)
    leaked = build_validation_isolation_audit(
        experiment_root=experiment,
        validation_access_policy=policy,
        audited_at=NOW,
    )
    assert leaked["learning_isolation"] == "fail"
    assert any(
        item["category"] == "validation_exact_sequence" and item["findings"]
        for item in leaked["checks"]
    )


def test_validation_result_cannot_be_relabelled_to_another_checkpoint(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    c0 = _checkpoint(tmp_path, checkpoint_label="C0")
    c5 = _checkpoint(tmp_path, checkpoint_label="C5")
    result_root = _harbor_result(tmp_path, checkpoint_label="C0", checkpoint_root=c0)
    with pytest.raises(CapabilityImprovementError, match="another checkpoint"):
        build_validation_aggregate(
            experiment_digest=EXPERIMENT_DIGEST,
            checkpoint_label="C5",
            checkpoint_root=c5,
            validation_access_policy=policy,
            result_root=result_root,
            created_at=NOW,
        )


def test_validation_result_rejects_noncanonical_concurrency(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    checkpoint = _checkpoint(tmp_path, checkpoint_label="C0")
    result_root = _harbor_result(
        tmp_path, checkpoint_label="C0", checkpoint_root=checkpoint
    )
    config_path = result_root / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["n_concurrent_trials"] = 1
    write_json_atomic(config_path, config)

    with pytest.raises(CapabilityImprovementError, match="fixed runner"):
        build_validation_aggregate(
            experiment_digest=EXPERIMENT_DIGEST,
            checkpoint_label="C0",
            checkpoint_root=checkpoint,
            validation_access_policy=policy,
            result_root=result_root,
            created_at=NOW,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "requires every metric"),
        ("out_of_range", "finite score"),
        ("non_finite", "cannot read validation Harbor trial"),
    ],
)
def test_validation_gate_requires_complete_finite_scores(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    policy, _, _ = _policy(tmp_path)
    checkpoint = _checkpoint(tmp_path, checkpoint_label="C0")
    result_root = _harbor_result(
        tmp_path, checkpoint_label="C0", checkpoint_root=checkpoint
    )
    trial_path = result_root / "trial-0" / "result.json"
    trial = json.loads(trial_path.read_text(encoding="utf-8"))
    rewards = trial["verifier_result"]["rewards"]
    if mutation == "missing":
        rewards.pop("t3_state_f1")
        write_json_atomic(trial_path, trial)
    elif mutation == "out_of_range":
        rewards["t3_state_f1"] = 1.01
        write_json_atomic(trial_path, trial)
    else:
        trial_path.write_text(
            trial_path.read_text(encoding="utf-8").replace(
                '"t3_state_f1": 0.5', '"t3_state_f1": NaN'
            ),
            encoding="utf-8",
        )
    with pytest.raises(CapabilityImprovementError, match=message):
        build_validation_aggregate(
            experiment_digest=EXPERIMENT_DIGEST,
            checkpoint_label="C0",
            checkpoint_root=checkpoint,
            validation_access_policy=policy,
            result_root=result_root,
            created_at=NOW,
        )


def test_validation_recorder_rejects_results_inside_experiment(
    tmp_path: Path,
) -> None:
    root = tmp_path / "experiment"
    with pytest.raises(CapabilityImprovementError, match="outside the active"):
        record_validation_aggregate(
            experiment_root=root,
            checkpoint_label="C0",
            result_root=root / "validation" / "raw" / "job",
            created_at=NOW,
        )


def test_validation_isolation_rejects_forged_roots_and_raw_validation_files(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    root = tmp_path / "experiment"
    with pytest.raises(CapabilityImprovementError, match="canonical active roots"):
        build_validation_isolation_audit(
            experiment_root=root,
            validation_access_policy=policy,
            audited_at=NOW,
            active_learning_roots=("empty",),
        )
    raw = root / "validation" / "raw" / "prediction.json"
    raw.parent.mkdir(parents=True)
    raw.write_text("{}\n", encoding="utf-8")
    audit = build_validation_isolation_audit(
        experiment_root=root,
        validation_access_policy=policy,
        audited_at=NOW,
    )
    assert audit["learning_isolation"] == "fail"
    assert any(
        item["category"] == "validation_raw_result_or_error_detail" and item["findings"]
        for item in audit["checks"]
    )


def test_validation_scanner_covers_jsonl_log_native_and_unknown_binary(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "events.jsonl").write_text(SEQUENCES[0] + "\n", encoding="utf-8")
    (pack / "worker.log").write_text(VALIDATION_PROTOCOL_IDS[0], encoding="utf-8")
    (pack / "opaque.bin").write_bytes(b"\x00\xff\x01")
    issues = scan_validation_pack_leakage(pack, policy)
    assert any("events.jsonl: exact sequence overlap" in item for item in issues)
    assert any("worker.log: protocol term" in item for item in issues)
    assert any("opaque.bin: unsupported validation-scan" in item for item in issues)


def test_validation_denylist_subtracts_only_exact_shared_sequences(
    tmp_path: Path,
) -> None:
    _, sources, truth = _policy(tmp_path)
    validation_long = "AACCGGTTAACCGGTT"
    training_short = "CCGGTTAACC"
    validation_source = sources / VALIDATION_PROTOCOL_IDS[0] / "source.txt"
    validation_source.write_text(validation_long + "\n", encoding="utf-8")
    training_id = FINAL_DEVELOPMENT_BATCHES[0]["protocol_ids"][0]
    training_source = sources / training_id / "source.txt"
    training_source.write_text(training_short + "\n", encoding="utf-8")
    policy = build_validation_access_policy(
        validation_panel_commitment_sha256=validation_panel_commitment_digest(),
        source_root=sources,
        groundtruth_root=truth,
        initial_pack_root=PACK_ROOT,
        created_at=NOW,
    )
    assert validation_long in policy["forbidden_sequences"]


def test_validation_recorder_shared_mutation_lock_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "experiment"
    entered = threading.Event()
    release = threading.Event()

    def blocked_manifest(*_args: object, **_kwargs: object) -> dict:
        entered.set()
        release.wait(timeout=5)
        raise CapabilityImprovementError("test stop")

    monkeypatch.setattr(
        experiment_module,
        "validate_experiment_manifest",
        blocked_manifest,
    )
    failures: list[BaseException] = []

    def first() -> None:
        try:
            record_validation_aggregate(
                experiment_root=root,
                checkpoint_label="C0",
                result_root=tmp_path / "external-result",
                created_at=NOW,
            )
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(timeout=5)
    with pytest.raises(CapabilityImprovementError, match="already running"):
        record_validation_aggregate(
            experiment_root=root,
            checkpoint_label="C0",
            result_root=tmp_path / "another-result",
            created_at=NOW,
        )
    release.set()
    thread.join(timeout=5)
    assert failures


def test_validation_recorder_rejects_symlinked_aggregate_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, _, _ = _policy(tmp_path)
    root = tmp_path / "experiment"
    policy_path = root / "design" / "validation_access_policy.json"
    write_json_atomic(policy_path, policy, mode=0o444)
    experiment = {
        "experiment_digest": EXPERIMENT_DIGEST,
        "validation_panel": {
            "access_policy": {
                "path": "design/validation_access_policy.json",
                "digest": policy["policy_digest"],
                "sha256": sha256_file(policy_path),
            }
        },
    }
    monkeypatch.setattr(
        experiment_module,
        "validate_experiment_manifest",
        lambda *_args, **_kwargs: experiment,
    )
    checkpoint = _checkpoint(tmp_path, checkpoint_label="C0", experiment_root=root)
    external = tmp_path / "external-validation-dir"
    external.mkdir()
    (root / "validation").symlink_to(external, target_is_directory=True)
    with pytest.raises(CapabilityImprovementError, match="symlink"):
        record_validation_aggregate(
            experiment_root=root,
            checkpoint_label="C0",
            result_root=_harbor_result(
                tmp_path,
                checkpoint_label="C0",
                checkpoint_root=checkpoint,
            ),
            created_at=NOW,
        )


def test_validation_recorder_rejects_reused_raw_result_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, _, _ = _policy(tmp_path)
    root = tmp_path / "experiment"
    policy_path = root / "design" / "validation_access_policy.json"
    write_json_atomic(policy_path, policy, mode=0o444)
    experiment = {
        "experiment_digest": EXPERIMENT_DIGEST,
        "validation_panel": {
            "access_policy": {
                "path": "design/validation_access_policy.json",
                "digest": policy["policy_digest"],
                "sha256": sha256_file(policy_path),
            }
        },
    }
    monkeypatch.setattr(
        experiment_module,
        "validate_experiment_manifest",
        lambda *_args, **_kwargs: experiment,
    )
    c0 = _checkpoint(tmp_path, checkpoint_label="C0", experiment_root=root)
    c5 = _checkpoint(tmp_path, checkpoint_label="C5", experiment_root=root)
    c0_result = _harbor_result(
        tmp_path,
        checkpoint_label="C0",
        checkpoint_root=c0,
        result_name="raw-C0",
    )
    c5_result = _harbor_result(
        tmp_path,
        checkpoint_label="C5",
        checkpoint_root=c5,
        result_name="raw-C5",
    )
    (c5_result / "result.json").write_bytes((c0_result / "result.json").read_bytes())
    for index in range(5):
        destination = c5_result / f"trial-{index}" / "result.json"
        destination.write_bytes(
            (c0_result / f"trial-{index}" / "result.json").read_bytes()
        )
    record_validation_aggregate(
        experiment_root=root,
        checkpoint_label="C0",
        result_root=c0_result,
        created_at=NOW,
    )
    with pytest.raises(CapabilityImprovementError, match="already used"):
        record_validation_aggregate(
            experiment_root=root,
            checkpoint_label="C5",
            result_root=c5_result,
            created_at=NOW,
        )


def test_validation_pack_scanner_extracts_native_pdf_text(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    policy, _, _ = _policy(tmp_path)
    pack = tmp_path / "native-pack"
    pack.mkdir()
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), SEQUENCES[0])
    document.save(pack / "leak.pdf")
    document.close()
    assert any(
        "leak.pdf: exact sequence overlap" in item
        for item in scan_validation_pack_leakage(pack, policy)
    )


def test_validation_guidance_binds_aggregate_and_staged_workspace(
    tmp_path: Path,
) -> None:
    policy, _, _ = _policy(tmp_path)
    root = tmp_path / "experiment"
    aggregate = _aggregate(
        tmp_path,
        policy=policy,
        checkpoint_label="C0",
        experiment_root=root,
    )
    aggregate_path = root / "validation" / "aggregates" / "C0.json"
    write_json_atomic(aggregate_path, aggregate)
    workspace = root / "rounds" / "B1" / "cumulative" / "proposer-workspace"
    projection_path = workspace / "inputs" / "validation_feedback.json"
    write_json_atomic(
        projection_path,
        build_validation_feedback_projection(aggregate),
        mode=0o444,
    )
    checkpoint_root = root / "checkpoints" / "C0"
    copy_capability_pack(
        checkpoint_root / "pack",
        workspace / "inputs" / "capability_pack",
        freeze=True,
    )
    shutil.copytree(
        checkpoint_root / "memory",
        workspace / "inputs" / "exemplar_memory",
    )
    checkpoint = json.loads((checkpoint_root / "checkpoint.json").read_text())
    runtime = json.loads((checkpoint_root / "runtime.json").read_text())
    memory_record = _workspace_exemplar_memory_record(
        workspace / "inputs" / "exemplar_memory",
        source_checkpoint={
            "checkpoint_id": "C0",
            "checkpoint_digest": checkpoint["checkpoint_digest"],
            "checkpoint_sha256": sha256_file(checkpoint_root / "checkpoint.json"),
            "runtime_digest": runtime["runtime_digest"],
            "runtime_sha256": sha256_file(checkpoint_root / "runtime.json"),
        },
    )
    staged_files = [
        {
            "path": "inputs/validation_feedback.json",
            "sha256": sha256_file(projection_path),
            "role": "validation_macro_aggregate",
        }
    ]
    for staged_root, role in (
        (workspace / "inputs" / "capability_pack", "current_capability_pack"),
        (workspace / "inputs" / "exemplar_memory", "current_exemplar_memory"),
    ):
        staged_files.extend(
            {
                "path": path.relative_to(workspace).as_posix(),
                "sha256": sha256_file(path),
                "role": role,
            }
            for path in sorted(staged_root.rglob("*"))
            if path.is_file()
        )
    manifest = with_digest(
        {
            "schema_version": "libstruct.libgen_worker_workspace.v1",
            "mode": "improvement_worker",
            "experiment_digest": EXPERIMENT_DIGEST,
            "packet_digest": "a" * 64,
            "access_policy_digest": "b" * 64,
            "staged_protocol_ids": [f"training_{index}" for index in range(5)],
            "staged_files": sorted(staged_files, key=lambda item: item["path"]),
            "exemplar_memory": memory_record,
            "review_materials_staged": False,
            "validation_feedback": {
                "checkpoint_label": "C0",
                "aggregate_digest": aggregate["aggregate_digest"],
                "aggregate_sha256": sha256_file(aggregate_path),
                "projection_sha256": sha256_file(projection_path),
            },
            "agent_contract": {
                "prompt_path": None,
                "output_schema_path": None,
                "draft_output_path": None,
                "event_log_path": None,
            },
            "host_paths_exposed": False,
            "network_policy": "provider_api_only_no_web",
        },
        "workspace_digest",
    )
    manifest_path = workspace / "workspace_manifest.json"
    write_json_atomic(manifest_path, manifest, mode=0o444)
    guidance = build_validation_guidance_record(
        experiment_root=root,
        experiment_digest=EXPERIMENT_DIGEST,
        validation_access_policy=policy,
        batch_id="B1",
        expected_pack_digest=aggregate["pack_digest"],
        workspace_manifest_path=manifest_path,
    )
    assert guidance == {
        "checkpoint_label": "C0",
        "aggregate_digest": aggregate["aggregate_digest"],
        "aggregate_sha256": sha256_file(aggregate_path),
        "workspace_digest": manifest["workspace_digest"],
        "workspace_manifest_sha256": sha256_file(manifest_path),
    }


def test_improvement_workspace_cannot_omit_validation_gate(tmp_path: Path) -> None:
    with pytest.raises(
        CapabilityImprovementError,
        match="requires the canonical prior-checkpoint validation aggregate",
    ):
        prepare_isolated_worker_workspace(
            experiment_manifest={},
            packet_path=tmp_path / "missing-packet.json",
            parent_pack_root=tmp_path / "missing-pack",
            access_policy_path=tmp_path / "missing-policy.json",
            output_root=tmp_path / "workspace",
            mode="improvement_worker",
        )


def test_candidate_cannot_copy_validation_metric_payload(tmp_path: Path) -> None:
    policy, _, _ = _policy(tmp_path)
    aggregate = _aggregate(tmp_path, policy=policy, checkpoint_label="C0")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "fixture.json").write_text(
        json.dumps({"macro_means": aggregate["macro_means"]}),
        encoding="utf-8",
    )
    assert scan_validation_feedback_copy(candidate, aggregate)
