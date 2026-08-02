from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.groundtruth import (
    GroundtruthValidationError,
    validate_cross_task_links,
    validate_task_document,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = REPO_ROOT / "schemas" / "groundtruth"


def _scope(version: str = "paper") -> dict:
    return {"protocol_version": version, "applicable_variants": ["default"]}


def _evidence() -> list[dict]:
    return [{"source_id": "primary:paper", "locator": {"page": 1}}]


def _documents() -> dict[str, dict]:
    t1 = {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "libraries": [
            {
                "library_id": "library",
                "modality": "rna",
                "protocol_scope": _scope(),
                "final_molecule": "DNA",
                "library_sequence": "AAA",
                "annotated_library_sequence": "AAA",
                "strand": "single",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": "library-adapter",
                        "kind": "constant",
                        "role": "adapter",
                        "sequence": "AAA",
                        "orientation": "5_to_3",
                        "oligo_ids": ["adapter"],
                        "ground_truth_status": "included",
                        "support_status": "explicit",
                        "evidence": _evidence(),
                    }
                ],
                "ground_truth_status": "included",
                "support_status": "explicit",
                "evidence": _evidence(),
            }
        ],
    }
    t2 = {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "oligos": [
            {
                "oligo_id": "adapter",
                "canonical_oligo_id": "common:adapter",
                "family_id": "adapter-family",
                "name": "Adapter",
                "source_name": "Assay adapter",
                "source_names": ["Assay adapter"],
                "aliases": ["Adapter"],
                "role": "adapter",
                "kind": "single",
                "sequence": "AAA",
                "orientation": "5_to_3",
                "components": [],
                "modifications": [],
                "protocol_scope": _scope(),
                "ground_truth_status": "included",
                "support_status": "explicit",
                "evidence": _evidence(),
                "baseline_lineage": [],
            }
        ],
    }
    t3 = {
        "protocol_id": "example_protocol",
        "protocol_name": "Example",
        "protocol_scope": _scope(),
        "workflows": [
            {
                "workflow_id": "workflow",
                "modality": "rna",
                "workflow_branch": None,
                "protocol_scope": _scope(),
                "ground_truth_status": "included",
                "states": [
                    {
                        "state_id": "input",
                        "name": "Input",
                        "molecule_type": "RNA",
                        "strand_state": "single_stranded",
                        "physical_state": "solution",
                        "modality": "rna",
                        "workflow_branch": None,
                        "segments": [{"segment_id": "input-rna", "role": "input", "sequence": "GGG"}],
                        "properties": [],
                        "protocol_scope": _scope(),
                        "support_status": "explicit",
                        "evidence": _evidence(),
                    },
                    {
                        "state_id": "final",
                        "name": "Final library",
                        "molecule_type": "DNA",
                        "strand_state": "single_stranded",
                        "physical_state": "solution",
                        "modality": "rna",
                        "workflow_branch": None,
                        "segments": [{"segment_id": "final-adapter", "role": "adapter", "sequence": "AAA", "oligo_ids": ["adapter"]}],
                        "properties": ["amplifiable"],
                        "protocol_scope": _scope(),
                        "support_status": "explicit",
                        "evidence": _evidence(),
                    },
                ],
                "transitions": [
                    {
                        "transition_id": "ligation",
                        "substrate_state_ids": ["input"],
                        "operation": "ligation",
                        "operation_detail": None,
                        "oligo_ids": ["adapter"],
                        "major_reagents": [{"name": "ligase", "role": "enzyme"}],
                        "product_state_ids": ["final"],
                        "carried_forward_product_ids": ["final"],
                        "discarded_product_ids": [],
                        "protocol_scope": _scope(),
                        "support_status": "explicit",
                        "evidence": _evidence(),
                    }
                ],
                "initial_state_ids": ["input"],
                "final_state_ids": ["final"],
                "final_library_links": [{"state_id": "final", "library_id": "library"}],
            }
        ],
    }
    return {"T1": t1, "T2": t2, "T3": t3}


def test_canonical_documents_validate_and_link() -> None:
    documents = _documents()
    for task, document in documents.items():
        validate_task_document(
            task,
            document,
            protocol_id="example_protocol",
            schema_dir=SCHEMAS,
        )
    validate_cross_task_links(documents)


def test_t3_oligo_reference_must_resolve() -> None:
    documents = _documents()
    documents["T3"]["workflows"][0]["transitions"][0]["oligo_ids"] = ["missing"]
    with pytest.raises(GroundtruthValidationError, match="unknown IDs"):
        validate_cross_task_links(documents)


def test_nonfinal_carried_product_must_continue() -> None:
    documents = _documents()
    workflow = documents["T3"]["workflows"][0]
    unused = copy.deepcopy(workflow["states"][0])
    unused["state_id"] = "unused"
    workflow["states"].append(unused)
    transition = workflow["transitions"][0]
    transition["product_state_ids"].append("unused")
    transition["carried_forward_product_ids"].append("unused")
    with pytest.raises(GroundtruthValidationError, match="not downstream substrates"):
        validate_cross_task_links(documents)


def test_final_state_must_be_reachable() -> None:
    documents = _documents()
    documents["T3"]["workflows"][0]["transitions"] = []
    with pytest.raises(GroundtruthValidationError, match="unreachable"):
        validate_cross_task_links(documents)


def test_graph_cycles_are_rejected() -> None:
    documents = _documents()
    workflow = documents["T3"]["workflows"][0]
    workflow["transitions"].append(
        {
            "transition_id": "cycle",
            "substrate_state_ids": ["final"],
            "operation": "other",
            "operation_detail": "invalid cycle",
            "oligo_ids": [],
            "major_reagents": [],
            "product_state_ids": ["input"],
            "carried_forward_product_ids": ["input"],
            "discarded_product_ids": [],
            "protocol_scope": _scope(),
            "support_status": "explicit",
            "evidence": _evidence(),
        }
    )
    with pytest.raises(GroundtruthValidationError, match="graph cycle"):
        validate_cross_task_links(documents)


def test_terminal_state_must_match_t1() -> None:
    documents = _documents()
    documents["T3"]["workflows"][0]["states"][1]["segments"][0]["sequence"] = "CCC"
    with pytest.raises(GroundtruthValidationError, match="inconsistent"):
        validate_cross_task_links(documents)


def test_task_scopes_must_agree() -> None:
    documents = _documents()
    documents["T2"]["protocol_scope"] = _scope("other")
    with pytest.raises(GroundtruthValidationError, match="scopes must agree"):
        validate_cross_task_links(documents)
