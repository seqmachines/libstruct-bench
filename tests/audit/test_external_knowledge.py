from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.external_knowledge import (
    DONOR_PROTOCOL_IDS,
    EVIDENCE_COLUMNS,
    PRIMER_CARD_FIELDS,
    PRIMER_SECTION_HEADINGS,
    SOURCE_FILES,
    TARGET_PROTOCOL_IDS,
    ExternalKnowledgeBuildError,
    build_external_knowledge_assets,
    build_external_knowledge_review_candidate,
    build_overlap_rows,
    canonical_digest,
    project_document_to_schema,
    validate_external_knowledge_assets,
    validate_external_knowledge_review_candidate,
)
from libstruct_bench.audit.external_knowledge_harbor import (
    CONDITION_IDS,
    EXTERNAL_KNOWLEDGE_MOUNT_TARGET,
    build_external_knowledge_final_approval,
    build_external_knowledge_harbor_integration,
    build_external_knowledge_harbor_plan,
    validate_external_knowledge_final_approval,
    validate_external_knowledge_harbor_integration,
    validate_external_knowledge_harbor_plan,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _t2(protocol_id: str, sequence: str) -> dict[str, object]:
    return {
        "protocol_id": protocol_id,
        "protocol_name": f"Private name for {protocol_id}",
        "oligos": [
            {
                "oligo_id": "oligo-1",
                "name": "Example oligo",
                "aliases": [],
                "role": "generic test role",
                "kind": "single",
                "sequence": sequence,
                "orientation": "5_to_3",
                "components": [],
                "modifications": [],
                "support_status": "explicit",
            }
        ],
    }


def _t3(protocol_id: str, sequence: str) -> dict[str, object]:
    return {
        "protocol_id": protocol_id,
        "protocol_name": f"Private name for {protocol_id}",
        "workflows": [
            {
                "workflow_id": "workflow-1",
                "states": [
                    {
                        "state_id": "state-1",
                        "name": "Example state",
                        "molecule_type": "DNA",
                        "strand_architecture": "single_stranded",
                        "reference_strand_id": "strand-1",
                        "physical_state": "single DNA molecule",
                        "strands": [
                            {
                                "strand_id": "strand-1",
                                "name": "reference strand",
                                "molecule_type": "DNA",
                                "orientation": "5_to_3",
                                "segments": [
                                    {
                                        "segment_id": "segment-1",
                                        "role": "insert",
                                        "structural_role": "unpaired",
                                        "sequence": sequence,
                                    }
                                ],
                                "support_status": "explicit",
                            }
                        ],
                        "paired_regions": [],
                        "discontinuities": [],
                        "properties": [],
                        "support_status": "explicit",
                    }
                ],
                "transitions": [],
                "initial_state_ids": ["state-1"],
                "final_outputs": [
                    {"state_id": "state-1", "modality": "gene expression"}
                ],
            }
        ],
    }


def _primer_markdown() -> str:
    parts = ["# General molecular methods v1", "", "# Source coverage", ""]
    for source_id, filename, _ in SOURCE_FILES:
        parts.append(f"- `{source_id}`: `{filename}`")
    for index, heading in enumerate(PRIMER_SECTION_HEADINGS, start=1):
        card_id = f"CARD-{index:02d}"
        parts.extend(["", heading, "", f"### {card_id} — Test card", ""])
        for field in PRIMER_CARD_FIELDS:
            parts.append(f"- **{field}:** Test value.")
    return "\n".join(parts) + "\n"


def test_projection_is_recursive_allowlist_and_preserves_values() -> None:
    schema = {
        "type": "object",
        "properties": {
            "protocol_id": {"type": "string"},
            "records": {"type": "array", "items": {"$ref": "#/$defs/record"}},
        },
        "$defs": {
            "record": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "values": {"type": "array", "items": {"type": "integer"}},
                },
            }
        },
    }
    source = {
        "protocol_id": "p",
        "protocol_name": "remove",
        "records": [{"id": "r", "support_status": "remove", "values": [3, 1]}],
    }
    result = project_document_to_schema(source, schema)
    assert result.document == {
        "protocol_id": "p",
        "records": [{"id": "r", "values": [3, 1]}],
    }
    assert result.removed_json_pointers == (
        "/protocol_name",
        "/records/0/support_status",
    )


def test_canonical_digest_does_not_depend_on_object_order() -> None:
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})


def test_overlap_reports_exact_reverse_complement_and_family(tmp_path: Path) -> None:
    target_id = TARGET_PROTOCOL_IDS[0]
    target_root = tmp_path / "groundtruth"
    _write_json(
        target_root / target_id / "groundtruth_oligos.json",
        {
            "protocol_id": target_id,
            "oligos": [
                {**_t2(target_id, "ACGT")["oligos"][0], "oligo_id": "exact"},
                {**_t2(target_id, "GCAT")["oligos"][0], "oligo_id": "reverse"},
                {
                    **_t2(target_id, "AAA[BARCODE:4]CCC")["oligos"][0],
                    "oligo_id": "family",
                },
            ],
        },
    )
    donor_documents = {
        donor_id: {"t2": _t2(donor_id, "TTTT"), "t3": _t3(donor_id, "TTTT")}
        for donor_id in DONOR_PROTOCOL_IDS
    }
    donor_documents[DONOR_PROTOCOL_IDS[0]]["t2"] = {
        "protocol_id": DONOR_PROTOCOL_IDS[0],
        "oligos": [
            {**_t2("d", "ACGT")["oligos"][0], "oligo_id": "d-exact"},
            {**_t2("d", "ATGC")["oligos"][0], "oligo_id": "d-reverse"},
            {
                **_t2("d", "AAA[BARCODE:4]CCC")["oligos"][0],
                "oligo_id": "d-family",
            },
        ],
    }
    for donor_id in DONOR_PROTOCOL_IDS:
        path = target_root / donor_id / "groundtruth_oligos.json"
        _write_json(path, donor_documents[donor_id]["t2"])
    rows = build_overlap_rows(
        donor_documents=donor_documents,
        target_protocol_ids=[target_id],
        groundtruth_root=target_root,
    )
    assert any(row["exact_sequence"] == "true" for row in rows)
    assert any(row["reverse_complement"] == "true" for row in rows)
    assert any(row["family_scaffold"] == "true" for row in rows)


def test_builds_valid_frozen_review_package(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    for source_id, filename, _ in SOURCE_FILES:
        (source_root / filename).write_bytes(f"source:{source_id}\n".encode())

    groundtruth_root = tmp_path / "groundtruth"
    sequence_by_protocol = {
        protocol_id: "ACGT" for protocol_id in (*DONOR_PROTOCOL_IDS, *TARGET_PROTOCOL_IDS)
    }
    for protocol_id, sequence in sequence_by_protocol.items():
        _write_json(
            groundtruth_root / protocol_id / "groundtruth_oligos.json",
            _t2(protocol_id, sequence),
        )
        _write_json(
            groundtruth_root
            / protocol_id
            / "groundtruth_library_generation_workflow.json",
            _t3(protocol_id, sequence),
        )

    audit_root = tmp_path / "audit"
    approval_protocols = []
    for protocol_id in DONOR_PROTOCOL_IDS:
        t2_path = groundtruth_root / protocol_id / "groundtruth_oligos.json"
        t3_path = (
            groundtruth_root
            / protocol_id
            / "groundtruth_library_generation_workflow.json"
        )
        promotion = {
            "promotion_id": f"{protocol_id}:promotion:test",
            "protocol_id": protocol_id,
            "artifacts": [
                {"filename": t2_path.name, "sha256": _sha(t2_path)},
                {"filename": t3_path.name, "sha256": _sha(t3_path)},
            ],
        }
        _write_json(audit_root / "promotions" / protocol_id / "promotion.json", promotion)
        approval_protocols.append(
            {
                "protocol_id": protocol_id,
                "artifacts": [
                    {"filename": t2_path.name, "candidate_sha256": _sha(t2_path)},
                    {"filename": t3_path.name, "candidate_sha256": _sha(t3_path)},
                ],
            }
        )
    _write_json(
        audit_root
        / "runs"
        / "connected-process-migration-001"
        / "final-approval-preview-003.json",
        {
            "approval_id": "approval:test",
            "status": "final",
            "scientific_disposition": "approved",
            "protocols": approval_protocols,
        },
    )

    primer_path = tmp_path / "primer.md"
    primer_path.write_text(_primer_markdown(), encoding="utf-8")
    evidence_path = tmp_path / "evidence.tsv"
    with evidence_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=EVIDENCE_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for index, (source_id, filename, _) in enumerate(SOURCE_FILES, start=1):
            writer.writerow(
                {
                    "evidence_id": f"E-{index:02d}",
                    "card_id": f"CARD-{index if index <= 9 else 9:02d}",
                    "operation": "test",
                    "source_id": source_id,
                    "source_file": filename,
                    "source_sha256": _sha(source_root / filename),
                    "coverage_status": "direct",
                    "pdf_page_1based": "1",
                    "printed_page": "",
                    "section": "test",
                    "table": "",
                    "figure": "",
                    "support_summary": "test support",
                }
            )

    asset_root = tmp_path / "assets"
    result = build_external_knowledge_assets(
        asset_root=asset_root,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        audit_root=audit_root,
        schema_root=Path("schemas").resolve(),
        primer_markdown=primer_path,
        primer_evidence_tsv=evidence_path,
        created_at="2026-08-17T00:00:00Z",
    )
    assert result["validation"]["status"] == "pass"
    assert validate_external_knowledge_assets(asset_root)["status"] == "pass"

    donor_t2 = json.loads(
        (
            asset_root
            / "memory"
            / "cross_protocol_memory_v1"
            / "donors"
            / DONOR_PROTOCOL_IDS[0]
            / "t2_prediction.json"
        ).read_text()
    )
    assert "protocol_name" not in donor_t2
    assert "support_status" not in donor_t2["oligos"][0]

    for condition_id in (
        "general_methods_v1",
        "cross_protocol_memory_v1",
        "general_methods_plus_memory_v1",
    ):
        manifest = json.loads(
            (asset_root / "conditions" / condition_id / "manifest.json").read_text()
        )
        assert all(entry["visibility"] == "agent" for entry in manifest["included_files"])
        assert all("overlap" not in entry["path"] for entry in manifest["included_files"])
        assert manifest["mount_mode"] == "read_only"
        assert manifest["contents_merged_or_rewritten"] is False

    general = json.loads(
        (
            asset_root / "conditions" / "general_methods_v1" / "manifest.json"
        ).read_text()
    )
    memory = json.loads(
        (
            asset_root / "conditions" / "cross_protocol_memory_v1" / "manifest.json"
        ).read_text()
    )
    combined = json.loads(
        (
            asset_root
            / "conditions"
            / "general_methods_plus_memory_v1"
            / "manifest.json"
        ).read_text()
    )
    general_paths = {entry["path"] for entry in general["included_files"]}
    memory_paths = {entry["path"] for entry in memory["included_files"]}
    combined_paths = {entry["path"] for entry in combined["included_files"]}
    assert combined_paths == general_paths | memory_paths

    revised_primer_path = tmp_path / "primer-revised.md"
    revised_primer_path.write_text(
        primer_path.read_text(encoding="utf-8")
        .replace(
            "### CARD-01 — Test card\n",
            "### CARD-01 — Test card\n\n"
            "> **Source-limit/caution card:** Constrains inference.\n",
        )
        .replace(
            "### CARD-02 — Test card\n",
            "### CARD-02 — Test card\n\n"
            "> **Artifact warning:** Not a canonical operation.\n",
        )
        .replace("- **Molecular inputs:** Test value.", "- **Molecular inputs:** Revised.", 1),
        encoding="utf-8",
    )
    revised_evidence_path = tmp_path / "evidence-revised.tsv"
    with evidence_path.open(encoding="utf-8", newline="") as source_handle:
        evidence_rows = list(csv.DictReader(source_handle, delimiter="\t"))
    evidence_rows[0]["support_summary"] = "revised support"
    with revised_evidence_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=EVIDENCE_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(evidence_rows)

    revised_asset_root = tmp_path / "assets-revised"
    build_external_knowledge_assets(
        asset_root=revised_asset_root,
        source_root=source_root,
        groundtruth_root=groundtruth_root,
        audit_root=audit_root,
        schema_root=Path("schemas").resolve(),
        primer_markdown=revised_primer_path,
        primer_evidence_tsv=revised_evidence_path,
        created_at="2026-08-17T00:00:00Z",
    )
    review_request = {
        "review_request_id": "external-knowledge-review-001",
        "reviewer_identity": None,
        "primer": {
            "decision": "revise",
            "revised_card_ids": ["CARD-01"],
            "provisionally_accepted_card_ids": [
                f"CARD-{index:02d}" for index in range(2, 10)
            ],
            "caution_card_ids": ["CARD-01"],
            "artifact_warning_card_ids": ["CARD-02"],
        },
        "donor_projection": {
            "lineage_selection": "approved_current_t3_sha256"
        },
        "overlap": {"accepted": True, "hidden_from_agents": True},
        "analysis_requirements": {
            "primary_memory_outcome": "t3_molecular_transition_f1",
            "t2_family_f1_strata": [
                "donor_overlapping_target_families",
                "target_families_absent_from_donor_memory",
            ],
            "structure_only_memory_sensitivity": {
                "status": "planned",
                "fixed_sequences": "masked",
                "preserved": [
                    "graph_structure",
                    "roles",
                    "placeholders",
                    "strand_architecture",
                    "operations",
                ],
            },
        },
    }
    review_candidate = build_external_knowledge_review_candidate(
        prior_asset_root=asset_root,
        revised_asset_root=revised_asset_root,
        review_request=review_request,
        created_at="2026-08-17T01:00:00Z",
    )
    validate_external_knowledge_review_candidate(review_candidate)
    assert review_candidate["change_scope"]["unexpected_changed_file_count"] == 0
    assert review_candidate["change_scope"]["donor_memory_byte_identical"] is True
    assert review_candidate["overlap_review"]["agent_visibility"] == (
        "review_only_hidden"
    )
    assert review_candidate["authorization"] == {
        "harbor_integration_authorized": False,
        "experiment_run_authorized": False,
        "remaining_gate": (
            "Complete all 11 source-locator checks, record reviewer identity, and "
            "explicitly approve the revised condition digests."
        ),
    }

    approval = build_external_knowledge_final_approval(
        review_candidate=review_candidate,
        reviewer_identity="test-reviewer",
        approved_at="2026-08-17T02:00:00Z",
        rationale="The revised frozen conditions passed human review.",
    )
    validate_external_knowledge_final_approval(
        approval,
        review_candidate=review_candidate,
    )
    assert approval["authorization"] == {
        "harbor_integration_authorized": True,
        "experiment_run_authorized": False,
    }

    tasks_root = tmp_path / "tasks"
    for protocol_id in TARGET_PROTOCOL_IDS:
        task_root = tasks_root / protocol_id
        task_root.mkdir(parents=True)
        (task_root / "task.toml").write_text(
            "schema_version = \"1.3\"\n\n"
            "[task]\n"
            f"name = \"sequencing/libgen-{protocol_id}\"\n\n"
            "[verifier]\n"
            "environment_mode = \"separate\"\n\n"
            "[environment]\n"
            "network_mode = \"public\"\n",
            encoding="utf-8",
        )
        (task_root / "instruction.md").write_text(
            f"Solve {protocol_id}.\n",
            encoding="utf-8",
        )

    integration_root = tmp_path / "harbor-integration"
    integration = build_external_knowledge_harbor_integration(
        asset_root=revised_asset_root,
        tasks_root=tasks_root,
        review_candidate=review_candidate,
        approval=approval,
        integration_root=integration_root,
        harbor_version="0.20.0",
        created_at="2026-08-17T03:00:00Z",
    )
    integration_report = validate_external_knowledge_harbor_integration(
        integration_root,
        tasks_root=tasks_root,
    )
    assert integration_report["status"] == "pass"
    assert integration["isolation"]["baseline_task_files_modified"] is False
    assert integration["isolation"]["agent_only_mount"] is True
    assert integration["authorization"]["experiment_run_authorized"] is False

    for condition_id in CONDITION_IDS:
        condition_root = integration_root / "conditions" / condition_id
        condition = json.loads(
            (condition_root / "integration_manifest.json").read_text()
        )
        exposure_files = {
            path.relative_to(condition_root / "exposure").as_posix()
            for path in (condition_root / "exposure").rglob("*")
            if path.is_file()
        }
        assert exposure_files == {
            item["path"] for item in condition["exposed_files"]
        }
        assert "memory/cross_protocol_memory_v1/donor_target_overlap.tsv" not in (
            exposure_files
        )
        assert all(not path.endswith(".pdf") for path in exposure_files)
        assert condition["mount"] == {
            "type": "bind",
            "source": (condition_root / "exposure").resolve().as_posix(),
            "target": EXTERNAL_KNOWLEDGE_MOUNT_TARGET,
            "read_only": True,
            "bind": {"create_host_path": False},
        }

    base_config_path = tmp_path / "baseline-config.json"
    _write_json(
        base_config_path,
        {
            "job_name": "libgen-test-codex",
            "jobs_dir": "runs/libgen/codex",
            "agents": [
                {
                    "name": "codex",
                    "model_name": "gpt-test",
                    "kwargs": {"version": "test", "reasoning_effort": "max"},
                }
            ],
            "datasets": [{"path": "benchmarks/libgen/tasks"}],
            "artifacts": ["/logs/verifier/reward.json"],
        },
    )
    plan_root = tmp_path / "harbor-plan"
    plan = build_external_knowledge_harbor_plan(
        integration_root=integration_root,
        tasks_root=tasks_root,
        base_job_config_paths=[base_config_path],
        output_root=plan_root,
        jobs_dir=tmp_path / "jobs",
        created_at="2026-08-17T04:00:00Z",
    )
    plan_report = validate_external_knowledge_harbor_plan(
        plan_root,
        integration_root=integration_root,
        tasks_root=tasks_root,
    )
    assert plan_report == {
        "status": "pass",
        "plan_digest": plan["plan_digest"],
        "planned_job_count": 3,
        "expected_trial_count": 15,
    }
    for job in plan["planned_jobs"]:
        config = json.loads(Path(job["job_config_path"]).read_text())
        assert config["datasets"][0]["task_names"] == list(TARGET_PROTOCOL_IDS)
        assert config["environment"]["mounts"][0]["read_only"] is True
        assert config["environment"]["mounts"][0]["target"] == (
            EXTERNAL_KNOWLEDGE_MOUNT_TARGET
        )
        assert len(config["extra_instruction_paths"]) == 1
        assert config["retry"] == {"max_retries": 0}

    hidden_path = (
        integration_root
        / "conditions"
        / "general_methods_v1"
        / "exposure"
        / "donor_target_overlap.tsv"
    )
    hidden_path.write_text("must remain hidden\n", encoding="utf-8")
    with pytest.raises(
        ExternalKnowledgeBuildError,
        match="condition exposure bytes changed",
    ):
        validate_external_knowledge_harbor_integration(
            integration_root,
            tasks_root=tasks_root,
        )
    hidden_path.unlink()

    planned_config_path = Path(plan["planned_jobs"][0]["job_config_path"])
    original_planned_config = planned_config_path.read_text(encoding="utf-8")
    tampered_config = json.loads(original_planned_config)
    tampered_config["environment"]["mounts"][0]["read_only"] = False
    _write_json(planned_config_path, tampered_config)
    with pytest.raises(
        ExternalKnowledgeBuildError,
        match="planned job config changed",
    ):
        validate_external_knowledge_harbor_plan(
            plan_root,
            integration_root=integration_root,
            tasks_root=tasks_root,
        )
    planned_config_path.write_text(original_planned_config, encoding="utf-8")
    assert validate_external_knowledge_harbor_plan(
        plan_root,
        integration_root=integration_root,
        tasks_root=tasks_root,
    )["status"] == "pass"
