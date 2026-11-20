from __future__ import annotations

import json
from pathlib import Path

from libstruct_bench.audit.artifacts import write_json_atomic
from libstruct_bench.improvement.artifacts import freeze_tree, thaw_tree, with_digest
from libstruct_bench.improvement.exemplar_memory import (
    _write_exemplar_item,
    build_exemplar_identity_map,
    create_empty_exemplar_memory,
    exemplar_memory_record,
    project_groundtruth_exemplar,
    training_protocol_ids,
    validate_exemplar_memory,
    write_exemplar_memory_manifest,
)


TEST_SPLIT_DIGEST = "9" * 64
TEST_MAPPING_NONCE = "8" * 64


def portable_exemplar_memory(root: Path, count: int) -> dict:
    identity_map = build_exemplar_identity_map(
        split_digest=TEST_SPLIT_DIGEST,
        mapping_nonce=TEST_MAPPING_NONCE,
    )
    create_empty_exemplar_memory(memory_root=root, identity_map=identity_map)
    if count:
        thaw_tree(root)
        (root / "manifest.json").unlink()
        catalog_path = root / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        mapping = {
            item["protocol_id"]: item["exemplar_id"]
            for item in identity_map["entries"]
        }
        items = []
        for protocol_id in training_protocol_ids()[:count]:
            exemplar_id = mapping[protocol_id]
            summary, t2, t3 = project_groundtruth_exemplar(
                protocol_id=protocol_id,
                exemplar_id=exemplar_id,
                t2_groundtruth=minimal_t2(protocol_id),
                t3_groundtruth=minimal_t3(protocol_id),
            )
            items.append(
                _write_exemplar_item(
                    memory_root=root,
                    exemplar_id=exemplar_id,
                    mechanism_summary=summary,
                    t2_example=t2,
                    t3_example=t3,
                )
            )
        updated = with_digest(
            {
                "schema_version": catalog["schema_version"],
                "identity_map_commitment": catalog["identity_map_commitment"],
                "exemplar_count": count,
                "exemplars": sorted(items, key=lambda item: item["exemplar_id"]),
            },
            "catalog_digest",
        )
        write_json_atomic(catalog_path, updated)
        write_exemplar_memory_manifest(root)
        freeze_tree(root)
    validate_exemplar_memory(root, expected_count=count, identity_map=identity_map)
    return exemplar_memory_record(root)


def minimal_t2(protocol_id: str) -> dict:
    return {
        "protocol_id": protocol_id,
        "protocol_name": f"Private donor {protocol_id}",
        "oligos": [],
    }


def minimal_t3(protocol_id: str) -> dict:
    return {
        "protocol_id": protocol_id,
        "protocol_name": f"Private donor {protocol_id}",
        "workflows": [
            {
                "workflow_id": f"{protocol_id}:workflow",
                "states": [
                    {
                        "state_id": f"{protocol_id}:state",
                        "name": f"Private {protocol_id} state",
                        "molecule_type": "DNA",
                        "strand_architecture": "single_stranded",
                        "reference_strand_id": f"{protocol_id}:strand",
                        "physical_state": "solution",
                        "strands": [
                            {
                                "strand_id": f"{protocol_id}:strand",
                                "name": f"Private {protocol_id} strand",
                                "molecule_type": "DNA",
                                "orientation": "5_to_3",
                                "segments": [
                                    {
                                        "segment_id": f"{protocol_id}:segment",
                                        "role": "synthetic fixture",
                                        "structural_role": "unpaired",
                                        "sequence": "AACCGGTT",
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
                "initial_state_ids": [f"{protocol_id}:state"],
                "final_outputs": [
                    {
                        "state_id": f"{protocol_id}:state",
                        "modality": "gene expression",
                    }
                ],
            }
        ],
    }
