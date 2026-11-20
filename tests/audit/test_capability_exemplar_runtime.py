from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import (
    sha256_file,
    validate_document,
    write_json_atomic,
)
from libstruct_bench.improvement.artifacts import (
    freeze_tree,
    improvement_schema_root,
    thaw_tree,
)
from libstruct_bench.improvement.exemplar_memory import (
    build_exemplar_identity_map,
    create_empty_exemplar_memory,
    validate_exemplar_memory,
    write_exemplar_memory_manifest,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_TOOLS = REPOSITORY_ROOT / "improvement" / "exemplar_memory_runtime" / "tools"
if str(RUNTIME_TOOLS) not in sys.path:
    sys.path.insert(0, str(RUNTIME_TOOLS))

import exemplar_memory as portable  # noqa: E402
import guard_target_evidence as guard  # noqa: E402


def _state(
    index: int,
    *,
    sequence: str,
    oligo_id: str | None = None,
    architecture: str = "single_stranded",
) -> dict[str, object]:
    segment: dict[str, object] = {
        "segment_id": f"segment-{index:03d}",
        "role": "synthetic segment",
        "structural_role": "unpaired",
        "sequence": sequence,
    }
    if oligo_id is not None:
        segment["oligo_derivations"] = [
            {"oligo_id": oligo_id, "orientation_to_source": "same_orientation"}
        ]
    return {
        "state_id": f"state-{index:03d}",
        "name": f"State {index:03d}",
        "molecule_type": "DNA",
        "strand_architecture": architecture,
        "reference_strand_id": f"strand-{index:03d}",
        "physical_state": "solution",
        "strands": [
            {
                "strand_id": f"strand-{index:03d}",
                "name": f"Strand {index:03d}",
                "molecule_type": "DNA",
                "orientation": "5_to_3",
                "segments": [segment],
            }
        ],
        "paired_regions": [],
        "discontinuities": [],
        "properties": [],
    }


def _exemplar_documents(
    exemplar_id: str,
    *,
    operation: str = "extension",
    modality: str = "gene expression",
    sequence: str = "AACCGG",
    modification: str = "phosphorothioate",
    branching: bool = False,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    t2 = portable.with_digest(
        {
            "schema_version": portable.T2_EXAMPLE_SCHEMA_VERSION,
            "exemplar_id": exemplar_id,
            "example": {
                "protocol_id": exemplar_id,
                "oligos": [
                    {
                        "oligo_id": "oligo-001",
                        "name": "Oligo family 001",
                        "aliases": [],
                        "role": "synthetic primer",
                        "kind": "single",
                        "sequence": sequence,
                        "orientation": "5_to_3",
                        "components": [],
                        "modifications": [modification],
                    }
                ],
            },
        },
        "example_digest",
    )
    states = [
        _state(1, sequence="CCGGTT"),
        _state(2, sequence=sequence, oligo_id="oligo-001"),
    ]
    if branching:
        states.append(_state(3, sequence="GGGGTT"))
    transition = {
        "transition_id": "transition-001",
        "substrate_state_ids": ["state-001"],
        "operation": operation,
        "operation_detail": "Synthetic molecular event.",
        "oligo_ids": ["oligo-001"],
        "major_reagents": [],
        "product_state_ids": ["state-002", "state-003"] if branching else ["state-002"],
        "carried_forward_product_ids": ["state-002"],
        "discarded_product_ids": ["state-003"] if branching else [],
    }
    t3 = portable.with_digest(
        {
            "schema_version": portable.T3_EXAMPLE_SCHEMA_VERSION,
            "exemplar_id": exemplar_id,
            "example": {
                "protocol_id": exemplar_id,
                "workflows": [
                    {
                        "workflow_id": "workflow-001",
                        "states": states,
                        "transitions": [transition],
                        "initial_state_ids": ["state-001"],
                        "final_outputs": [
                            {"state_id": "state-002", "modality": modality}
                        ],
                    }
                ],
            },
        },
        "example_digest",
    )
    summary = portable.with_digest(
        {
            "schema_version": portable.SUMMARY_SCHEMA_VERSION,
            "exemplar_id": exemplar_id,
            "counts": {
                "oligo_families": 1,
                "workflows": 1,
                "states": 3 if branching else 2,
                "transitions": 1,
            },
            "operation_counts": [{"operation": operation, "count": 1}],
            "architecture_counts": [
                {
                    "strand_architecture": "single_stranded",
                    "count": 3 if branching else 2,
                }
            ],
            "modality_counts": [{"modality": modality, "count": 1}],
            "graph_features": {
                "has_branching": branching,
                "has_discarded_products": branching,
                "has_multiple_workflows": False,
                "max_branch_factor": 2 if branching else 1,
            },
            "barcoding_partitioning": {
                "cell_barcode": False,
                "umi": False,
                "sample_index": False,
                "round_barcode": False,
                "combinatorial": False,
                "droplet_partitioning": False,
                "microwell_partitioning": False,
                "plate_partitioning": False,
                "bead_partitioning": False,
                "split_pool_partitioning": False,
            },
            "selection_branching": {
                "affinity_selection": False,
                "size_selection": False,
                "capture": False,
                "discarded_product_branch": branching,
                "sample_split": False,
                "modality_branching": False,
                "alternative_branching": False,
            },
            "chemistry_flags": {
                "reverse_transcription": False,
                "template_switching": False,
                "ligation": False,
                "tagmentation": False,
                "pcr": False,
                "restriction": False,
                "conversion": False,
            },
        },
        "summary_digest",
    )
    return summary, t2, t3


def _build_memory(root: Path, *, branching: bool = False) -> Path:
    memory = root / "memory"
    identity_map = build_exemplar_identity_map(
        split_digest="a" * 64,
        mapping_nonce="b" * 64,
    )
    create_empty_exemplar_memory(
        memory_root=memory,
        identity_map=identity_map,
    )
    thaw_tree(memory)
    (memory / "manifest.json").unlink()
    (memory / "catalog.json").unlink()
    catalog_items = []
    for index in range(1, 6):
        exemplar_id = f"exm-{index:032x}"
        summary, t2, t3 = _exemplar_documents(exemplar_id, branching=branching)
        item_root = memory / "exemplars" / exemplar_id
        item_root.mkdir()
        refs = {}
        for role, document in (
            ("mechanism_summary", summary),
            ("t2_example", t2),
            ("t3_example", t3),
        ):
            path = item_root / f"{role}.json"
            write_json_atomic(path, document)
            refs[role] = {
                "path": path.relative_to(memory).as_posix(),
                "sha256": sha256_file(path),
            }
        catalog_items.append(
            portable.with_digest(
                {"exemplar_id": exemplar_id, **refs},
                "exemplar_digest",
            )
        )
    catalog = portable.with_digest(
        {
            "schema_version": portable.CATALOG_SCHEMA_VERSION,
            "identity_map_commitment": identity_map["identity_map_digest"],
            "exemplar_count": 5,
            "exemplars": catalog_items,
        },
        "catalog_digest",
    )
    write_json_atomic(memory / "catalog.json", catalog)
    write_exemplar_memory_manifest(memory)
    freeze_tree(memory)
    validate_exemplar_memory(memory, expected_count=5)
    return memory


def _work_record() -> dict[str, object]:
    record = json.loads(
        (
            REPOSITORY_ROOT
            / "improvement"
            / "capability_pack"
            / "synthetic_tests"
            / "valid"
            / "work_record.json"
        ).read_text(encoding="utf-8")
    )
    record["claims"] = [
        claim for claim in record["claims"] if claim["claim_id"] != "t3-006"
    ]
    return record


def _query(operation: str, claim_id: str = "t3-010") -> dict[str, object]:
    return {
        "schema_version": portable.QUERY_SCHEMA_VERSION,
        "modalities": [],
        "operations": [operation],
        "barcoding_partitioning": [],
        "architectures": [],
        "selection_branching": [],
        "chemistries": [],
        "feature_evidence": [
            {
                "feature_group": "operations",
                "feature_value": operation,
                "evidence_refs": [{"record_kind": "claim", "record_id": claim_id}],
            }
        ],
    }


def _feature_query(
    group: str,
    feature: str,
    *,
    record_kind: str,
    record_id: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": portable.QUERY_SCHEMA_VERSION,
        **{name: [] for name in portable.FEATURE_GROUPS},
        "feature_evidence": [
            {
                "feature_group": group,
                "feature_value": feature,
                "evidence_refs": [{"record_kind": record_kind, "record_id": record_id}],
            }
        ],
    }
    result[group] = [feature]
    return result


def _document_sha(document: object) -> str:
    data = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def memory_root(tmp_path: Path) -> Path:
    return _build_memory(tmp_path)


def test_retrieval_is_deterministic_bounded_and_non_scored(
    memory_root: Path,
) -> None:
    record = _work_record()
    manifest, catalog, exemplars = portable.load_catalog(memory_root / "catalog.json")
    retrieval = portable.retrieve(
        _query("extension"),
        record,
        manifest,
        catalog,
        exemplars,
        target_work_record_sha256=_document_sha(record),
        max_results=3,
    )
    usage = portable.build_usage(retrieval)

    assert retrieval["match_count"] == 3
    assert [item["exemplar_id"] for item in retrieval["matches"]] == [
        f"exm-{index:032x}" for index in range(1, 4)
    ]
    for match in retrieval["matches"]:
        assert match["mechanism_summary"]["counts"]["transitions"] == 1
        assert sum(len(item["transitions"]) for item in match["donor_subgraphs"]) <= 3
        assert match["donor_subgraphs"][0]["linked_t2"][0]["oligo_id"] == "oligo-001"
        assert {
            state["state_id"] for state in match["donor_subgraphs"][0]["states"]
        } == {"state-001", "state-002"}
    assert usage["score_inclusion"] is False
    assert usage["scoring_scope"] == "diagnostic_only_excluded_from_benchmark_scores"
    portable.validate_usage(usage, retrieval)
    for filename, document in (
        ("exemplar_retrieval.schema.json", retrieval),
        ("exemplar_usage.schema.json", usage),
    ):
        validate_document(
            document,
            improvement_schema_root() / filename,
            label=filename,
        )


def test_query_rejects_unbacked_and_identity_bearing_features() -> None:
    record = _work_record()
    invalid = _query("extension") | {"protocol_id": "forbidden-target"}
    with pytest.raises(portable.ExemplarMemoryError, match="query fields differ"):
        portable.validate_query(invalid, record)

    unbacked = _query("cleanup")
    with pytest.raises(portable.ExemplarMemoryError, match="not mechanically derived"):
        portable.validate_query(unbacked, record)

    broken_locator = copy.deepcopy(record)
    broken_locator["claims"][9]["source_locators"] = ["unregistered source"]
    with pytest.raises(portable.ExemplarMemoryError, match="source_coverage"):
        portable.validate_query(_query("extension"), broken_locator)

    forbidden_nested = copy.deepcopy(record)
    forbidden_nested["source_coverage"][0]["verifier_score"] = 1
    with pytest.raises(portable.ExemplarMemoryError, match="forbidden input key"):
        portable.validate_query(_query("extension"), forbidden_nested)

    unavailable = copy.deepcopy(record)
    unavailable["source_coverage"][0]["coverage_status"] = "unavailable"
    with pytest.raises(portable.ExemplarMemoryError, match="source_coverage"):
        portable.validate_query(_query("extension"), unavailable)


def test_zero_retrieval_still_guards_direct_memory_copy(memory_root: Path) -> None:
    record = _work_record()
    record["drafts"]["t3"]["workflows"][0]["transitions"][0]["operation"] = "cleanup"
    record["event_records"][0]["operation"] = "cleanup"
    for claim in record["claims"]:
        if claim["claim_id"] == "t3-010":
            claim["source_locators"] = ["synthetic fixture"]
    record["claims"] = [
        claim
        for claim in record["claims"]
        if claim["claim_id"] not in {"t2-002", "t2-004", "t3-003", "t3-008", "t3-009"}
    ]
    manifest, catalog, exemplars = portable.load_catalog(memory_root / "catalog.json")
    retrieval = portable.retrieve(
        _query("cleanup"),
        record,
        manifest,
        catalog,
        exemplars,
        target_work_record_sha256=_document_sha(record),
    )
    usage = portable.build_usage(retrieval)
    assert retrieval["match_count"] == 0

    report = guard.audit_target_evidence(
        record,
        manifest,
        catalog,
        exemplars,
        retrieval,
        usage,
        target_work_record_sha256=_document_sha(record),
    )
    assert report["status"] == "findings"
    assert "sequence" in {item["category"] for item in report["findings"]}
    assert {item["exemplar_id"] for item in report["findings"]} == {
        "exm-00000000000000000000000000000001"
    }
    validate_document(
        report,
        improvement_schema_root() / "target_evidence_guard_report.schema.json",
        label="target evidence guard report",
    )


def test_guard_checks_individual_modifications_and_near_values_are_distinct(
    memory_root: Path,
) -> None:
    record = _work_record()
    record["drafts"]["t2"]["oligos"][0]["modifications"] = [
        "phosphorothioate",
        "target-only modification",
    ]
    record["drafts"]["t2"]["oligos"][0]["sequence"] = "AACCGT"
    manifest, catalog, exemplars = portable.load_catalog(memory_root / "catalog.json")
    retrieval = portable.retrieve(
        _query("extension"),
        record,
        manifest,
        catalog,
        exemplars,
        target_work_record_sha256=_document_sha(record),
    )
    usage = portable.build_usage(retrieval)
    report = guard.audit_target_evidence(
        record,
        manifest,
        catalog,
        exemplars,
        retrieval,
        usage,
        target_work_record_sha256=_document_sha(record),
    )
    assert any(
        item["category"] == "modification"
        and item["json_pointer"] == "/oligos/0/modifications/0"
        for item in report["findings"]
    )
    assert not any(
        item["category"] == "sequence" and item["json_pointer"] == "/oligos/0/sequence"
        for item in report["findings"]
    )


def test_subgraph_selection_uses_modality_barcode_architecture_and_branch() -> None:
    t2 = {
        "protocol_id": "exm-00000000000000000000000000000001",
        "oligos": [
            {
                "oligo_id": "oligo-001",
                "role": "cell barcode primer",
                "sequence": "AAAA",
            },
            {"oligo_id": "oligo-002", "role": "adapter", "sequence": "CCCC"},
        ],
    }
    source = _state(1, sequence="TTTT", architecture="single_stranded")
    expression = _state(
        2,
        sequence="AAAA",
        oligo_id="oligo-001",
        architecture="single_stranded",
    )
    chromatin = _state(
        3,
        sequence="CCCC",
        oligo_id="oligo-002",
        architecture="double_stranded",
    )
    transitions = [
        {
            "transition_id": "transition-001",
            "substrate_state_ids": ["state-001"],
            "operation": "extension",
            "oligo_ids": ["oligo-001"],
            "product_state_ids": ["state-002"],
            "carried_forward_product_ids": ["state-002"],
            "discarded_product_ids": [],
        },
        {
            "transition_id": "transition-002",
            "substrate_state_ids": ["state-001"],
            "operation": "tagmentation",
            "oligo_ids": ["oligo-002"],
            "product_state_ids": ["state-003"],
            "carried_forward_product_ids": ["state-003"],
            "discarded_product_ids": [],
        },
    ]
    t3 = {
        "protocol_id": "exm-00000000000000000000000000000001",
        "workflows": [
            {
                "workflow_id": "workflow-001",
                "states": [source, expression, chromatin],
                "transitions": transitions,
                "initial_state_ids": ["state-001"],
                "final_outputs": [
                    {"state_id": "state-002", "modality": "gene expression"},
                    {
                        "state_id": "state-003",
                        "modality": "chromatin accessibility",
                    },
                ],
            }
        ],
    }

    def query(group: str, feature: str) -> dict[str, list[str]]:
        result = {name: [] for name in portable.FEATURE_GROUPS}
        result[group] = [feature]
        return result

    modality = portable.extract_donor_subgraphs(
        t2,
        t3,
        query("modalities", "chromatin accessibility"),
        transition_cap=1,
    )
    assert modality[0]["focus_transition_ids"] == ["transition-002"]
    barcode = portable.extract_donor_subgraphs(
        t2,
        t3,
        query("barcoding_partitioning", "cell_barcode"),
        transition_cap=1,
    )
    assert barcode[0]["focus_transition_ids"] == ["transition-001"]
    architecture = portable.extract_donor_subgraphs(
        t2,
        t3,
        query("architectures", "double_stranded"),
        transition_cap=1,
    )
    assert architecture[0]["focus_transition_ids"] == ["transition-002"]
    branch = portable.extract_donor_subgraphs(
        t2,
        t3,
        query("selection_branching", "modality_branching"),
        transition_cap=3,
    )
    assert {
        transition_id
        for item in branch
        for transition_id in item["focus_transition_ids"]
    } == {"transition-001", "transition-002"}
    branch_values = guard._workflow_branch_values(t3["workflows"][0], "/workflows/0")
    assert {item[3]["kind"] for item in branch_values} == {
        "workflow_fanout",
        "modality_branching",
    }


def test_guard_rejects_stale_final_work_record_binding(memory_root: Path) -> None:
    record = _work_record()
    manifest, catalog, exemplars = portable.load_catalog(memory_root / "catalog.json")
    retrieval = portable.retrieve(
        _query("extension"),
        record,
        manifest,
        catalog,
        exemplars,
        target_work_record_sha256="b" * 64,
    )
    usage = portable.build_usage(retrieval)
    with pytest.raises(guard.TargetEvidenceGuardError, match="must be rerun"):
        guard.audit_target_evidence(
            record,
            manifest,
            catalog,
            exemplars,
            retrieval,
            usage,
            target_work_record_sha256=_document_sha(record),
        )


def test_guard_flags_unsupported_operation_and_branch(
    tmp_path: Path,
) -> None:
    memory = _build_memory(tmp_path / "ordinary")
    record = _work_record()
    record["event_records"] = []
    record["claims"] = [
        claim for claim in record["claims"] if claim["claim_id"] != "t3-010"
    ]
    manifest, catalog, exemplars = portable.load_catalog(memory / "catalog.json")
    modality_query = _feature_query(
        "modalities",
        "gene expression",
        record_kind="claim",
        record_id="t3-014",
    )
    retrieval = portable.retrieve(
        modality_query,
        record,
        manifest,
        catalog,
        exemplars,
        target_work_record_sha256=_document_sha(record),
    )
    usage = portable.build_usage(retrieval)
    report = guard.audit_target_evidence(
        record,
        manifest,
        catalog,
        exemplars,
        retrieval,
        usage,
        target_work_record_sha256=_document_sha(record),
    )
    assert any(item["category"] == "operation" for item in report["findings"])

    branch_memory = _build_memory(tmp_path / "branching", branching=True)
    branch_record = _work_record()
    workflow = branch_record["drafts"]["t3"]["workflows"][0]
    discarded_state = copy.deepcopy(workflow["states"][0])
    discarded_state["state_id"] = "discarded"
    discarded_state["reference_strand_id"] = "discarded-strand"
    discarded_state["strands"][0]["strand_id"] = "discarded-strand"
    discarded_state["strands"][0]["segments"][0]["segment_id"] = "discarded-segment"
    discarded_state["strands"][0]["segments"][0]["sequence"] = "GGGGTT"
    workflow["states"].append(discarded_state)
    transition = workflow["transitions"][0]
    transition["product_state_ids"] = ["extended", "discarded"]
    transition["discarded_product_ids"] = ["discarded"]
    branch_record["event_records"] = []
    branch_manifest, branch_catalog, branch_exemplars = portable.load_catalog(
        branch_memory / "catalog.json"
    )
    branch_query = _feature_query(
        "selection_branching",
        "discard_branching",
        record_kind="inventory",
        record_id="inv-process",
    )
    branch_retrieval = portable.retrieve(
        branch_query,
        branch_record,
        branch_manifest,
        branch_catalog,
        branch_exemplars,
        target_work_record_sha256=_document_sha(branch_record),
    )
    assert branch_retrieval["match_count"] == 3
    branch_usage = portable.build_usage(branch_retrieval)
    branch_report = guard.audit_target_evidence(
        branch_record,
        branch_manifest,
        branch_catalog,
        branch_exemplars,
        branch_retrieval,
        branch_usage,
        target_work_record_sha256=_document_sha(branch_record),
    )
    assert any(item["category"] == "branch" for item in branch_report["findings"])


def test_catalog_tamper_and_forbidden_nested_keys_fail_closed(
    memory_root: Path,
) -> None:
    thaw_tree(memory_root)
    summary_path = next((memory_root / "exemplars").glob("*/mechanism_summary.json"))
    summary_path.write_text(
        summary_path.read_text(encoding="utf-8") + " ", encoding="utf-8"
    )
    with pytest.raises(portable.ExemplarMemoryError, match="differ from manifest"):
        portable.load_catalog(memory_root / "catalog.json")

    exemplar_id = "exm-00000000000000000000000000000001"
    valid = {
        "schema_version": portable.T2_EXAMPLE_SCHEMA_VERSION,
        "exemplar_id": exemplar_id,
        "example": {"protocol_id": exemplar_id, "oligos": []},
        "example_digest": "a" * 64,
    }
    portable._reject_forbidden_memory_keys(
        valid,
        "valid wrapper",
        exemplar_id=exemplar_id,
    )
    invalid = copy.deepcopy(valid)
    invalid["example"]["prediction"] = {}
    with pytest.raises(portable.ExemplarMemoryError, match="forbidden memory key"):
        portable._reject_forbidden_memory_keys(
            invalid,
            "invalid wrapper",
            exemplar_id=exemplar_id,
        )


def test_portable_query_and_guard_cli_end_to_end(
    memory_root: Path,
    tmp_path: Path,
) -> None:
    record = _work_record()
    record["drafts"]["t2"]["oligos"][0]["sequence"] = "TTTTAA"
    record["drafts"]["t2"]["oligos"][0]["components"][0]["sequence"] = "TTTTAA"
    record["drafts"]["t2"]["oligos"][0]["modifications"] = []
    for state_index, state in enumerate(
        record["drafts"]["t3"]["workflows"][0]["states"]
    ):
        for strand in state["strands"]:
            for segment_index, segment in enumerate(strand["segments"]):
                segment["sequence"] = f"TTTT{state_index}{segment_index}"
    record["drafts"]["t3"]["workflows"][0]["transitions"][0]["operation"] = "cleanup"
    record["event_records"][0]["operation"] = "cleanup"
    query = _query("cleanup")
    record_path = tmp_path / "work-record.json"
    query_path = tmp_path / "query.json"
    retrieval_path = tmp_path / "retrieval.json"
    usage_path = tmp_path / "usage.json"
    report_path = tmp_path / "guard-report.json"
    write_json_atomic(record_path, record)
    write_json_atomic(query_path, query)

    query_result = subprocess.run(
        [
            sys.executable,
            str(memory_root / "runtime" / "tools" / "query_exemplars.py"),
            "--query",
            str(query_path),
            "--work-record",
            str(record_path),
            "--catalog",
            str(memory_root / "catalog.json"),
            "--retrieval-out",
            str(retrieval_path),
            "--usage-out",
            str(usage_path),
            "--max-results",
            "3",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert query_result.returncode == 0, query_result.stdout + query_result.stderr
    assert json.loads(retrieval_path.read_text())["match_count"] == 0

    guard_result = subprocess.run(
        [
            sys.executable,
            str(memory_root / "runtime" / "tools" / "guard_target_evidence.py"),
            "--work-record",
            str(record_path),
            "--catalog",
            str(memory_root / "catalog.json"),
            "--retrieval",
            str(retrieval_path),
            "--usage",
            str(usage_path),
            "--report-out",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert guard_result.returncode == 0, guard_result.stdout + guard_result.stderr
    assert json.loads(report_path.read_text())["status"] == "pass"
