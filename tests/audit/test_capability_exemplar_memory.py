from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import sha256_file, write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
    thaw_tree,
    with_digest,
)
from libstruct_bench.improvement.exemplar_memory import (
    build_exemplar_identity_map,
    create_empty_exemplar_memory,
    extend_exemplar_memory_from_packet,
    project_groundtruth_exemplar,
    validate_exemplar_identity_map,
    validate_exemplar_memory,
    validate_exemplar_memory_projections,
)
from libstruct_bench.improvement.split_design import FINAL_DEVELOPMENT_BATCHES


EXPERIMENT_DIGEST = "a" * 64
SPLIT_DIGEST = "b" * 64
MAPPING_NONCE = "c" * 64


def _t2(protocol_id: str) -> dict:
    return {
        "protocol_id": protocol_id,
        "protocol_name": f"Private {protocol_id} donor",
        "oligos": [
            {
                "oligo_id": f"{protocol_id}:raw-primer",
                "name": "R",
                "aliases": ["RT", "PCR", f"{protocol_id}-secret-alias"],
                "role": "reverse transcription primer with cell barcode and UMI",
                "kind": "single",
                "sequence": "AACCGGTT[CELL_BARCODE:8][UMI:6]",
                "orientation": "5_to_3",
                "components": [
                    {
                        "name": "RT",
                        "role": "cell barcode",
                        "length": 8,
                        "placeholder": "[CELL_BARCODE:8]",
                        "orientation": "5_to_3",
                        "modifications": [],
                        "support_status": "explicit",
                    }
                ],
                "modifications": [],
                "support_status": "explicit",
            }
        ],
    }


def _state(protocol_id: str, index: int) -> dict:
    return {
        "state_id": f"{protocol_id}:state:{index}",
        "name": f"{protocol_id} private state {index}",
        "molecule_type": "DNA",
        "strand_architecture": "single_stranded",
        "reference_strand_id": f"{protocol_id}:strand:{index}",
        "physical_state": "solution",
        "strands": [
            {
                "strand_id": f"{protocol_id}:strand:{index}",
                "name": "S",
                "molecule_type": "DNA",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": f"{protocol_id}:segment:{index}",
                        "role": "template-derived segment",
                        "structural_role": "unpaired",
                        "sequence": "AACCGGTT",
                        "oligo_derivations": [
                            {
                                "oligo_id": f"{protocol_id}:raw-primer",
                                "orientation_to_source": "same_orientation",
                            }
                        ],
                    }
                ],
                "support_status": "explicit",
            }
        ],
        "paired_regions": [],
        "discontinuities": [],
        "properties": ["bead partitioned"],
        "support_status": "explicit",
    }


def _t3(protocol_id: str) -> dict:
    return {
        "protocol_id": protocol_id,
        "protocol_name": f"Private {protocol_id} donor",
        "workflows": [
            {
                "workflow_id": f"{protocol_id}:workflow",
                "states": [_state(protocol_id, 1), _state(protocol_id, 2)],
                "transitions": [
                    {
                        "transition_id": f"{protocol_id}:transition",
                        "substrate_state_ids": [f"{protocol_id}:state:1"],
                        "operation": "reverse_transcription",
                        "operation_detail": (
                            f"Private {protocol_id} donor template switching "
                            "with RT and PCR"
                        ),
                        "oligo_ids": [f"{protocol_id}:raw-primer"],
                        "major_reagents": [
                            {
                                "name": "SuperScript IV Reverse Transcriptase",
                                "role": "reverse transcriptase",
                            }
                        ],
                        "product_state_ids": [f"{protocol_id}:state:2"],
                        "carried_forward_product_ids": [f"{protocol_id}:state:2"],
                        "discarded_product_ids": [],
                        "support_status": "explicit",
                    }
                ],
                "initial_state_ids": [f"{protocol_id}:state:1"],
                "final_outputs": [
                    {
                        "state_id": f"{protocol_id}:state:2",
                        "modality": "gene expression",
                    }
                ],
            }
        ],
    }


def _identity_map() -> dict:
    return build_exemplar_identity_map(
        split_digest=SPLIT_DIGEST,
        mapping_nonce=MAPPING_NONCE,
    )


def _packet(tmp_path: Path, batch_index: int = 0) -> tuple[Path, dict]:
    batch = FINAL_DEVELOPMENT_BATCHES[batch_index]
    artifacts = []
    for protocol_id in batch["protocol_ids"]:
        truth_root = tmp_path / "truth" / protocol_id
        t2_path = truth_root / "groundtruth_oligos.json"
        t3_path = truth_root / "groundtruth_library_generation_workflow.json"
        write_json_atomic(t2_path, _t2(protocol_id))
        write_json_atomic(t3_path, _t3(protocol_id))
        for path in (t2_path, t3_path):
            artifacts.append(
                {
                    "protocol_id": protocol_id,
                    "role": "approved_groundtruth",
                    "path": path.resolve().as_posix(),
                    "sha256": sha256_file(path),
                    "visibility": "agent_after_reveal",
                }
            )
    packet = with_digest(
        {
            "schema_version": "libstruct.libgen_capability_batch_packet.v1",
            "packet_id": f"{batch['batch_id']}:cumulative:revealed",
            "experiment_digest": EXPERIMENT_DIGEST,
            "batch_id": batch["batch_id"],
            "branch": "cumulative",
            "phase": batch["phase"],
            "parent_pack_digest": "d" * 64,
            "protocol_ids": list(batch["protocol_ids"]),
            "reveal_state": "revealed",
            "eligibility_status": "eligible_for_improvement",
            "transfer_access_policy_digest": "e" * 64,
            "artifacts": artifacts,
            "trial_terminality": [],
            "learning_ledger": None,
        },
        "packet_digest",
    )
    path = tmp_path / f"packet-{batch['batch_id']}.json"
    write_json_atomic(path, packet)
    return path, packet


def test_private_identity_map_is_opaque_pinned_and_training_only(
    tmp_path: Path,
) -> None:
    identity_map = _identity_map()
    assert identity_map["lineage_id"] == "cumulative-C0-C25-v1"
    assert identity_map["split_digest"] == SPLIT_DIGEST
    assert len(identity_map["entries"]) == 25
    assert len({item["exemplar_id"] for item in identity_map["entries"]}) == 25
    assert all(
        item["protocol_id"] not in item["exemplar_id"]
        for item in identity_map["entries"]
    )

    forged = copy.deepcopy(identity_map)
    forged.pop("identity_map_digest")
    forged["entries"][0]["protocol_id"] = "sci_atac_seq"
    forged = with_digest(forged, "identity_map_digest")
    path = tmp_path / "forged-map.json"
    write_json_atomic(path, forged)
    with pytest.raises(CapabilityImprovementError, match="training protocols"):
        validate_exemplar_identity_map(path, split_digest=SPLIT_DIGEST)


def test_projection_is_prediction_shaped_deterministic_and_pseudonymous() -> None:
    protocol_id = "s3_atac"
    exemplar_id = _identity_map()["entries"][0]["exemplar_id"]
    first = project_groundtruth_exemplar(
        protocol_id=protocol_id,
        exemplar_id=exemplar_id,
        t2_groundtruth=_t2(protocol_id),
        t3_groundtruth=_t3(protocol_id),
    )
    second = project_groundtruth_exemplar(
        protocol_id=protocol_id,
        exemplar_id=exemplar_id,
        t2_groundtruth=_t2(protocol_id),
        t3_groundtruth=_t3(protocol_id),
    )
    assert first == second
    summary, t2, t3 = first
    assert t2["example"]["protocol_id"] == exemplar_id
    assert t3["example"]["protocol_id"] == exemplar_id
    assert t2["example"]["oligos"][0]["name"] == "R"
    assert t2["example"]["oligos"][0]["aliases"] == [
        "RT",
        "PCR",
        "[REDACTED_PROTOCOL]-secret-alias",
    ]
    assert t2["example"]["oligos"][0]["components"][0]["name"] == "RT"
    assert t3["example"]["workflows"][0]["states"][0]["name"] == (
        "[REDACTED_PROTOCOL] private state 1"
    )
    assert t3["example"]["workflows"][0]["states"][0]["strands"][0]["name"] == "S"
    assert (
        t3["example"]["workflows"][0]["transitions"][0]["major_reagents"][0]["name"]
        == "SuperScript IV Reverse Transcriptase"
    )
    assert (
        t3["example"]["workflows"][0]["transitions"][0]["operation_detail"]
        == "[REDACTED_PROTOCOL] template switching with RT and PCR"
    )
    serialized = json.dumps(first, sort_keys=True)
    for forbidden in (
        protocol_id,
        "Private s3_atac donor",
        "s3_atac-secret-alias",
        "support_status",
        "protocol_scope",
        "groundtruth_oligos.json",
        "raw-primer",
    ):
        assert forbidden not in serialized
    assert summary["barcoding_partitioning"]["cell_barcode"] is True
    assert summary["barcoding_partitioning"]["umi"] is True
    assert summary["barcoding_partitioning"]["bead_partitioning"] is True
    assert summary["barcoding_partitioning"]["plate_partitioning"] is False
    assert summary["chemistry_flags"]["reverse_transcription"] is True
    assert summary["chemistry_flags"]["template_switching"] is True


@pytest.mark.parametrize("protocol_id", ["sci_atac_seq", "cel_seq2"])
def test_projection_rejects_validation_and_final_test(protocol_id: str) -> None:
    with pytest.raises(CapabilityImprovementError, match="training protocols"):
        project_groundtruth_exemplar(
            protocol_id=protocol_id,
            exemplar_id="exm-" + "1" * 32,
            t2_groundtruth=_t2(protocol_id),
            t3_groundtruth=_t3(protocol_id),
        )


def test_memory_is_cumulative_closed_and_packet_hash_bound(tmp_path: Path) -> None:
    identity_map = _identity_map()
    c0 = tmp_path / "C0-memory"
    create_empty_exemplar_memory(memory_root=c0, identity_map=identity_map)
    assert (
        validate_exemplar_memory(c0, expected_count=0, identity_map=identity_map)[
            "exemplar_count"
        ]
        == 0
    )

    packet_path, packet = _packet(tmp_path)
    c5 = tmp_path / "C5-memory"
    manifest = extend_exemplar_memory_from_packet(
        parent_memory_root=c0,
        output_memory_root=c5,
        packet_path=packet_path,
        identity_map=identity_map,
        experiment_digest=EXPERIMENT_DIGEST,
        batch_id="B1",
        expected_count=5,
    )
    assert manifest["exemplar_count"] == 5
    verified = validate_exemplar_memory_projections(
        memory_root=c5,
        packet_paths=[packet_path],
        identity_map=identity_map,
        experiment_digest=EXPERIMENT_DIGEST,
        expected_count=5,
    )
    assert len(verified) == 15
    assert all(
        item.endswith(("mechanism_summary.json", "t2_example.json", "t3_example.json"))
        for item in verified
    )
    public_bytes = "\n".join(
        path.read_text(encoding="utf-8")
        for path in c5.rglob("*.json")
        if path.is_file()
    )
    for protocol_id in FINAL_DEVELOPMENT_BATCHES[0]["protocol_ids"]:
        assert protocol_id not in public_bytes

    stale = copy.deepcopy(packet)
    stale.pop("packet_digest")
    stale["artifacts"][0]["sha256"] = "0" * 64
    stale = with_digest(stale, "packet_digest")
    stale_path = tmp_path / "stale-packet.json"
    write_json_atomic(stale_path, stale)
    with pytest.raises(CapabilityImprovementError, match="hash is stale"):
        extend_exemplar_memory_from_packet(
            parent_memory_root=c0,
            output_memory_root=tmp_path / "stale-memory",
            packet_path=stale_path,
            identity_map=identity_map,
            experiment_digest=EXPERIMENT_DIGEST,
            batch_id="B1",
            expected_count=5,
        )

    packet_path, packet = _packet(tmp_path / "changed-source")
    changed_source = Path(packet["artifacts"][0]["path"])
    changed_document = json.loads(changed_source.read_text(encoding="utf-8"))
    changed_document["oligos"][0]["name"] = "changed after projection"
    write_json_atomic(changed_source, changed_document)
    changed_packet = copy.deepcopy(packet)
    changed_packet.pop("packet_digest")
    changed_packet["artifacts"][0]["sha256"] = sha256_file(changed_source)
    changed_packet = with_digest(changed_packet, "packet_digest")
    write_json_atomic(packet_path, changed_packet)
    with pytest.raises(
        CapabilityImprovementError,
        match="differs from its deterministic approved-GT projection",
    ):
        validate_exemplar_memory_projections(
            memory_root=c5,
            packet_paths=[packet_path],
            identity_map=identity_map,
            experiment_digest=EXPERIMENT_DIGEST,
            expected_count=5,
        )


def test_memory_projects_strict_legacy_terminal_contract_without_mutating_gt(
    tmp_path: Path,
) -> None:
    identity_map = _identity_map()
    c0 = tmp_path / "C0-memory"
    create_empty_exemplar_memory(memory_root=c0, identity_map=identity_map)
    packet_path, packet = _packet(tmp_path)
    protocol_id = FINAL_DEVELOPMENT_BATCHES[0]["protocol_ids"][0]
    t3_artifact = next(
        item
        for item in packet["artifacts"]
        if item["protocol_id"] == protocol_id
        and Path(item["path"]).name == "groundtruth_library_generation_workflow.json"
    )
    t3_path = Path(t3_artifact["path"])
    legacy_t3 = json.loads(t3_path.read_text(encoding="utf-8"))
    workflow = legacy_t3["workflows"][0]
    final_outputs = workflow.pop("final_outputs")
    workflow["modality"] = final_outputs[0]["modality"]
    workflow["final_state_ids"] = [item["state_id"] for item in final_outputs]
    write_json_atomic(t3_path, legacy_t3)
    packet.pop("packet_digest")
    t3_artifact["sha256"] = sha256_file(t3_path)
    packet = with_digest(packet, "packet_digest")
    write_json_atomic(packet_path, packet)

    c5 = tmp_path / "C5-memory"
    manifest = extend_exemplar_memory_from_packet(
        parent_memory_root=c0,
        output_memory_root=c5,
        packet_path=packet_path,
        identity_map=identity_map,
        experiment_digest=EXPERIMENT_DIGEST,
        batch_id="B1",
        expected_count=5,
    )

    exemplar_id = next(
        item["exemplar_id"]
        for item in identity_map["entries"]
        if item["protocol_id"] == protocol_id
    )
    projected_t3 = json.loads(
        (c5 / "exemplars" / exemplar_id / "t3_example.json").read_text(encoding="utf-8")
    )
    projected_workflow = projected_t3["example"]["workflows"][0]
    assert manifest["exemplar_count"] == 5
    assert projected_workflow["final_outputs"] == [
        {"state_id": "state-002", "modality": final_outputs[0]["modality"]}
    ]
    assert "modality" not in projected_workflow
    assert "final_state_ids" not in projected_workflow
    assert json.loads(t3_path.read_text(encoding="utf-8")) == legacy_t3
    assert (
        len(
            validate_exemplar_memory_projections(
                memory_root=c5,
                packet_paths=[packet_path],
                identity_map=identity_map,
                experiment_digest=EXPERIMENT_DIGEST,
                expected_count=5,
            )
        )
        == 15
    )


def test_memory_rejects_unresolved_legacy_terminal_reference(
    tmp_path: Path,
) -> None:
    identity_map = _identity_map()
    c0 = tmp_path / "C0-memory"
    create_empty_exemplar_memory(memory_root=c0, identity_map=identity_map)
    packet_path, packet = _packet(tmp_path)
    protocol_id = FINAL_DEVELOPMENT_BATCHES[0]["protocol_ids"][0]
    t3_artifact = next(
        item
        for item in packet["artifacts"]
        if item["protocol_id"] == protocol_id
        and Path(item["path"]).name == "groundtruth_library_generation_workflow.json"
    )
    t3_path = Path(t3_artifact["path"])
    legacy_t3 = json.loads(t3_path.read_text(encoding="utf-8"))
    workflow = legacy_t3["workflows"][0]
    workflow.pop("final_outputs")
    workflow["modality"] = "gene expression"
    workflow["final_state_ids"] = ["unknown-terminal-state"]
    write_json_atomic(t3_path, legacy_t3)
    packet.pop("packet_digest")
    t3_artifact["sha256"] = sha256_file(t3_path)
    packet = with_digest(packet, "packet_digest")
    write_json_atomic(packet_path, packet)

    with pytest.raises(
        CapabilityImprovementError,
        match="unresolved terminal state reference",
    ):
        extend_exemplar_memory_from_packet(
            parent_memory_root=c0,
            output_memory_root=tmp_path / "invalid-memory",
            packet_path=packet_path,
            identity_map=identity_map,
            experiment_digest=EXPERIMENT_DIGEST,
            batch_id="B1",
            expected_count=5,
        )


def test_non_groundtruth_role_future_batch_and_extra_file_fail_closed(
    tmp_path: Path,
) -> None:
    identity_map = _identity_map()
    c0 = tmp_path / "C0-memory"
    create_empty_exemplar_memory(memory_root=c0, identity_map=identity_map)
    packet_path, packet = _packet(tmp_path)
    forged = copy.deepcopy(packet)
    forged.pop("packet_digest")
    forged["artifacts"][0]["role"] = "prediction"
    forged = with_digest(forged, "packet_digest")
    forged_path = tmp_path / "forged-role-packet.json"
    write_json_atomic(forged_path, forged)
    with pytest.raises(CapabilityImprovementError, match="non-ground-truth role"):
        extend_exemplar_memory_from_packet(
            parent_memory_root=c0,
            output_memory_root=tmp_path / "forged-role-memory",
            packet_path=forged_path,
            identity_map=identity_map,
            experiment_digest=EXPERIMENT_DIGEST,
            batch_id="B1",
            expected_count=5,
        )

    b2_packet_path, _ = _packet(tmp_path / "b2", batch_index=1)
    with pytest.raises(CapabilityImprovementError, match="cumulative training prefix"):
        extend_exemplar_memory_from_packet(
            parent_memory_root=c0,
            output_memory_root=tmp_path / "future-memory",
            packet_path=b2_packet_path,
            identity_map=identity_map,
            experiment_digest=EXPERIMENT_DIGEST,
            batch_id="B2",
            expected_count=5,
        )

    thaw_tree(c0)
    (c0 / "unapproved.bin").write_bytes(b"not allowlisted")
    with pytest.raises(CapabilityImprovementError, match="inventory is not closed"):
        validate_exemplar_memory(c0, expected_count=0, identity_map=identity_map)


def test_duplicate_key_and_symlink_groundtruth_are_rejected(tmp_path: Path) -> None:
    identity_map = _identity_map()
    c0 = tmp_path / "C0-memory"
    create_empty_exemplar_memory(memory_root=c0, identity_map=identity_map)
    packet_path, packet = _packet(tmp_path)
    source = Path(packet["artifacts"][0]["path"])
    source.write_text(
        '{"protocol_id":"s3_atac","protocol_id":"s3_atac","protocol_name":"x","oligos":[]}',
        encoding="utf-8",
    )
    duplicate = copy.deepcopy(packet)
    duplicate.pop("packet_digest")
    duplicate["artifacts"][0]["sha256"] = sha256_file(source)
    duplicate = with_digest(duplicate, "packet_digest")
    write_json_atomic(packet_path, duplicate)
    with pytest.raises(CapabilityImprovementError, match="duplicate JSON key"):
        extend_exemplar_memory_from_packet(
            parent_memory_root=c0,
            output_memory_root=tmp_path / "duplicate-memory",
            packet_path=packet_path,
            identity_map=identity_map,
            experiment_digest=EXPERIMENT_DIGEST,
            batch_id="B1",
            expected_count=5,
        )

    packet_path, packet = _packet(tmp_path / "symlink")
    source = Path(packet["artifacts"][0]["path"])
    target = source.with_name("real-groundtruth.json")
    source.replace(target)
    source.symlink_to(target)
    symlinked = copy.deepcopy(packet)
    symlinked.pop("packet_digest")
    symlinked["artifacts"][0]["sha256"] = sha256_file(target)
    symlinked = with_digest(symlinked, "packet_digest")
    write_json_atomic(packet_path, symlinked)
    with pytest.raises(CapabilityImprovementError, match="non-symlink"):
        extend_exemplar_memory_from_packet(
            parent_memory_root=c0,
            output_memory_root=tmp_path / "symlink-memory",
            packet_path=packet_path,
            identity_map=identity_map,
            experiment_digest=EXPERIMENT_DIGEST,
            batch_id="B1",
            expected_count=5,
        )
