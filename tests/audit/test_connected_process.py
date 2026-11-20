from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import sha256_file
from libstruct_bench.audit.connected_process import (
    ConnectedProcessMigrationError,
    connected_process_counts,
    migrate_connected_process_bundle,
    migrate_connected_process_t3,
)
from libstruct_bench.audit.connected_process_final_approval import (
    ConnectedProcessFinalApprovalError,
    record_connected_process_final_approval,
    validate_connected_process_final_approval,
)
from libstruct_bench.audit.connected_process_preview import (
    ConnectedProcessPreviewError,
    compile_connected_process_preview,
)
from libstruct_bench.audit.connected_process_source_check import (
    ConnectedProcessSourceCheckError,
    validate_connected_process_source_check,
)
from libstruct_bench.audit.groundtruth import (
    validate_cross_task_links,
    validate_task_document,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "schemas"


def _state(state_id: str, sequence: str = "AAA") -> dict:
    strand_id = f"{state_id}_strand"
    return {
        "state_id": state_id,
        "name": state_id,
        "molecule_type": "DNA",
        "strand_architecture": "single_stranded",
        "reference_strand_id": strand_id,
        "physical_state": "solution",
        "strands": [
            {
                "strand_id": strand_id,
                "name": f"{state_id} strand",
                "molecule_type": "DNA",
                "orientation": "5_to_3",
                "sequence_architecture": sequence,
                "segments": [
                    {
                        "segment_id": f"{state_id}_segment",
                        "role": "molecule",
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


def _transition(
    transition_id: str,
    substrates: list[str],
    products: list[str],
    *,
    discarded: list[str] | None = None,
) -> dict:
    discarded = discarded or []
    carried = [item for item in products if item not in discarded]
    return {
        "transition_id": transition_id,
        "substrate_state_ids": substrates,
        "operation": "other",
        "operation_detail": f"{transition_id} detail",
        "oligo_ids": [],
        "major_reagents": [],
        "product_state_ids": products,
        "carried_forward_product_ids": carried,
        "discarded_product_ids": discarded,
        "support_status": "explicit",
    }


def _dr_seq_p2_amplicon_state() -> dict:
    sequence = "GTGAGTGATGGTTGAGGTAGTGTGGAG"
    reverse_complement = "CTCCACACTACCTCAACCATCACTCAC"

    def strand(strand_id: str, prefix: str) -> dict:
        return {
            "strand_id": strand_id,
            "name": strand_id,
            "molecule_type": "DNA",
            "orientation": "5_to_3",
            "segments": [
                {
                    "segment_id": f"{prefix}_h5",
                    "role": "MALBAC Ad-2 common sequence",
                    "structural_role": "paired_region",
                    "sequence": sequence,
                    "oligo_derivations": [
                        {
                            "oligo_id": "oligo_malbac_pcr_primer",
                            "orientation_to_source": "same_orientation",
                        }
                    ],
                },
                {
                    "segment_id": f"{prefix}_body",
                    "role": "gDNA- or cDNA-derived copy",
                    "structural_role": "paired_region",
                    "placeholder": "[GDNA_OR_CDNA]",
                },
                {
                    "segment_id": f"{prefix}_h3",
                    "role": "MALBAC Ad-2 common sequence, opposite end",
                    "structural_role": "paired_region",
                    "sequence": reverse_complement,
                    "oligo_derivations": [
                        {
                            "oligo_id": "oligo_malbac_pcr_primer",
                            "orientation_to_source": "reverse_complement",
                        }
                    ],
                },
            ],
            "support_status": "explicit",
        }

    return {
        "state_id": "st_gdna_ds_amplicon",
        "name": "Double-stranded PCR-amplified MALBAC product",
        "molecule_type": "DNA",
        "strand_architecture": "double_stranded",
        "reference_strand_id": "pcr_top",
        "physical_state": "purified PCR product",
        "strands": [strand("pcr_top", "pcr_top"), strand("pcr_bottom", "pcr_bot")],
        "paired_regions": [
            {
                "paired_region_id": "pr_pcr",
                "side_1": {
                    "strand_id": "pcr_top",
                    "segment_ids": [
                        "pcr_top_h5",
                        "pcr_top_body",
                        "pcr_top_h3",
                    ],
                },
                "side_2": {
                    "strand_id": "pcr_bottom",
                    "segment_ids": [
                        "pcr_bot_h5",
                        "pcr_bot_body",
                        "pcr_bot_h3",
                    ],
                },
                "relationship": "reverse_complementary",
                "support_status": "explicit",
            }
        ],
        "discontinuities": [],
        "properties": [],
        "support_status": "explicit",
    }


def _dr_seq_t2() -> dict:
    return {
        "protocol_id": "dr_seq",
        "protocol_name": "dr_seq",
        "oligos": [
            {
                "oligo_id": "oligo_malbac_pcr_primer",
                "name": "MALBAC PCR primer",
                "aliases": [],
                "sequence": "GTGAGTGATGGTTGAGGTAGTGTGGAG",
                "orientation": "5_to_3",
                "kind": "single",
                "role": "PCR amplification primer",
                "modifications": [],
                "components": [],
                "support_status": "explicit",
            },
            {
                "oligo_id": "oligo_p3_adaptor_removal_primer",
                "name": "P3 adaptor-removal primer",
                "aliases": [],
                "sequence": "GTGAGCTGGAGTTGAGGTAGTGTGGAG",
                "orientation": "5_to_3",
                "kind": "single",
                "role": "biotinylating PCR primer",
                "modifications": ["5-prime biotin"],
                "components": [],
                "support_status": "explicit",
            },
        ],
    }


def _dr_seq_t1() -> dict:
    return {
        "protocol_id": "dr_seq",
        "protocol_name": "dr_seq",
        "libraries": [
            {
                "modality": "genomic DNA",
                "final_molecule": (
                    "TruSeq-type single-indexed Illumina library prepared from "
                    "MALBAC-amplified single-cell genomic DNA"
                ),
                "library_sequence": "AAA[GDNA]CCC",
                "strand": "top",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": "t1_ts_r1",
                        "kind": "constant",
                        "role": "TruSeq Read 1 sequencing primer",
                        "sequence": "AAA",
                        "orientation": "5_to_3",
                        "support_status": "explicit",
                    },
                    {
                        "segment_id": "t1_ts_insert",
                        "kind": "biological_insert",
                        "role": "genomic DNA insert",
                        "placeholder": "[GDNA]",
                        "orientation": "5_to_3",
                        "support_status": "explicit",
                    },
                    {
                        "segment_id": "t1_ts_r2",
                        "kind": "constant",
                        "role": "TruSeq Read 2 sequencing primer",
                        "sequence": "CCC",
                        "orientation": "5_to_3",
                        "support_status": "explicit",
                    },
                ],
                "support_status": "explicit",
            },
            {
                "modality": "gene expression",
                "final_molecule": "RNA library",
                "library_sequence": "GGG",
                "strand": "top",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": "t1_rna",
                        "kind": "constant",
                        "role": "RNA library segment",
                        "sequence": "GGG",
                        "orientation": "5_to_3",
                        "support_status": "explicit",
                    }
                ],
                "support_status": "explicit",
            },
        ],
    }


def _workflow(
    workflow_id: str,
    modality: str,
    state_ids: list[str],
    transitions: list[dict],
    initial_ids: list[str],
    final_ids: list[str],
) -> dict:
    return {
        "workflow_id": workflow_id,
        "modality": modality,
        "states": [_state(state_id) for state_id in state_ids],
        "transitions": transitions,
        "initial_state_ids": initial_ids,
        "final_state_ids": final_ids,
    }


def _document(protocol_id: str, workflows: list[dict]) -> dict:
    return {
        "protocol_id": protocol_id,
        "protocol_name": protocol_id,
        "workflows": workflows,
    }


def _validate_t3(document: dict) -> None:
    validate_task_document(
        "T3",
        document,
        protocol_id=document["protocol_id"],
        schema_dir=SCHEMA_ROOT / "groundtruth",
    )
    validate_cross_task_links({"T3": document})


def test_generic_migration_moves_modality_to_each_terminal_output() -> None:
    legacy = _document(
        "ordinary_protocol",
        [
            _workflow(
                "workflow",
                "gene expression",
                ["input", "final_a", "final_b"],
                [
                    _transition("branch", ["input"], ["final_a", "final_b"]),
                ],
                ["input"],
                ["final_a", "final_b"],
            )
        ],
    )
    before = copy.deepcopy(legacy)

    migrated = migrate_connected_process_t3(legacy)

    assert legacy == before
    workflow = migrated["workflows"][0]
    assert "modality" not in workflow
    assert "final_state_ids" not in workflow
    assert workflow["final_outputs"] == [
        {"state_id": "final_a", "modality": "gene expression"},
        {"state_id": "final_b", "modality": "gene expression"},
    ]
    _validate_t3(migrated)


def test_10x_feature_barcoding_merges_shared_operations_once() -> None:
    legacy = _document(
        "10x_chromium_3_feature_barcoding",
        [
            _workflow(
                "wf_10x3fb_gene_expression",
                "gene expression",
                [
                    "st_gem_mrna_on_polydt_primer",
                    "st_mrna_cdna_hybrid",
                    "st_cdna_from_mrna",
                    "st_amplified_cdna_from_mrna",
                    "st_gex_fragmented",
                    "st_gex_adaptor_ligated",
                    "st_final_gex_library",
                ],
                [
                    _transition(
                        "tr_gem_rt",
                        ["st_gem_mrna_on_polydt_primer"],
                        ["st_mrna_cdna_hybrid"],
                    ),
                    _transition(
                        "tr_template_switching",
                        ["st_mrna_cdna_hybrid"],
                        ["st_cdna_from_mrna"],
                    ),
                    _transition(
                        "tr_pooled_cdna_amplification",
                        ["st_cdna_from_mrna"],
                        ["st_amplified_cdna_from_mrna"],
                    ),
                    _transition(
                        "tr_gex_fragmentation",
                        ["st_amplified_cdna_from_mrna"],
                        ["st_gex_fragmented"],
                    ),
                    _transition(
                        "tr_gex_adaptor_ligation",
                        ["st_gex_fragmented"],
                        ["st_gex_adaptor_ligated"],
                    ),
                    _transition(
                        "tr_gex_sample_index_pcr",
                        ["st_gex_adaptor_ligated"],
                        ["st_final_gex_library"],
                    ),
                ],
                ["st_gem_mrna_on_polydt_primer"],
                ["st_final_gex_library"],
            ),
            _workflow(
                "wf_10x3fb_feature_barcode",
                "feature barcode",
                [
                    "st_gem_adt_on_capture_primer",
                    "st_adt_cdna_duplex",
                    "st_amplified_feature_barcode_dna",
                    "st_final_cell_surface_protein_library",
                ],
                [
                    _transition(
                        "tr_gem_rt",
                        ["st_gem_adt_on_capture_primer"],
                        ["st_adt_cdna_duplex"],
                    ),
                    _transition(
                        "tr_pooled_cdna_amplification",
                        ["st_adt_cdna_duplex"],
                        ["st_amplified_feature_barcode_dna"],
                    ),
                    _transition(
                        "tr_feature_library_pcr",
                        ["st_amplified_feature_barcode_dna"],
                        ["st_final_cell_surface_protein_library"],
                    ),
                ],
                ["st_gem_adt_on_capture_primer"],
                ["st_final_cell_surface_protein_library"],
            ),
        ],
    )

    migrated = migrate_connected_process_t3(legacy)
    workflow = migrated["workflows"][0]
    transition_ids = [item["transition_id"] for item in workflow["transitions"]]

    assert workflow["workflow_id"] == "wf_10x3fb"
    assert transition_ids.count("tr_gem_rt") == 1
    assert transition_ids.count("tr_pooled_cdna_amplification") == 1
    assert "tr_size_separation" in transition_ids
    assert len(workflow["final_outputs"]) == 2
    _validate_t3(migrated)


def test_ddseq_connects_cdna_and_do_streams_through_fraction_separation() -> None:
    legacy = _document(
        "ddseq_single_cell_3_rna_seq_kit",
        [
            _workflow(
                "wf_ddseq_gene_expression",
                "gene expression",
                [
                    "st_mrna",
                    "st_mrna_cdna_hybrid",
                    "st_cdna_duplex",
                    "st_cdna_tagmented",
                    "st_cdna_library",
                    "st_do_annealed",
                    "st_do_duplex",
                    "st_do_library",
                ],
                [
                    _transition(
                        "tr_reverse_transcription", ["st_mrna"], ["st_mrna_cdna_hybrid"]
                    ),
                    _transition(
                        "tr_second_strand_synthesis",
                        ["st_mrna_cdna_hybrid"],
                        ["st_cdna_duplex"],
                    ),
                    _transition(
                        "tr_tagmentation", ["st_cdna_duplex"], ["st_cdna_tagmented"]
                    ),
                    _transition(
                        "tr_cdna_index_pcr", ["st_cdna_tagmented"], ["st_cdna_library"]
                    ),
                    _transition(
                        "tr_do_extension", ["st_do_annealed"], ["st_do_duplex"]
                    ),
                    _transition("tr_do_index_pcr", ["st_do_duplex"], ["st_do_library"]),
                ],
                ["st_mrna", "st_do_annealed"],
                ["st_cdna_library", "st_do_library"],
            )
        ],
    )

    migrated = migrate_connected_process_t3(legacy)
    workflow = migrated["workflows"][0]
    separation = next(
        item
        for item in workflow["transitions"]
        if item["transition_id"] == "tr_cdna_do_fraction_separation"
    )

    assert separation["operation"] == "size_selection"
    assert set(separation["substrate_state_ids"]) == {"st_cdna_duplex", "st_do_duplex"}
    assert {item["modality"] for item in workflow["final_outputs"]} == {
        "gene expression"
    }
    _validate_t3(migrated)


def test_dr_seq_has_one_joint_amplification_then_one_sample_split() -> None:
    gdna_states = [
        "st_gdna",
        "st_mrna",
        "st_rt_hybrid",
        "st_ad2_single_end",
        "st_ad2_both_ends",
        "st_gdna_ds_amplicon",
        "st_gdna_sheared",
        "st_gdna_ad2_removed",
        "st_lib_gdna_truseq",
    ]
    rna_states = [
        "st_mrna",
        "st_rt_hybrid",
        "st_ad2_single_end",
        "st_ad2_ad1x",
        "st_ds_cdna",
        "st_arna",
        "st_lib_rna",
    ]
    legacy = _document(
        "dr_seq",
        [
            _workflow(
                "wf_dr_seq_gdna",
                "genomic DNA",
                gdna_states,
                [
                    _transition(
                        "tr_reverse_transcription", ["st_mrna"], ["st_rt_hybrid"]
                    ),
                    _transition(
                        "tr_quasilinear_amplification",
                        ["st_gdna", "st_rt_hybrid"],
                        ["st_ad2_single_end", "st_ad2_both_ends"],
                        discarded=["st_ad2_single_end"],
                    ),
                    _transition(
                        "tr_gdna_pcr", ["st_ad2_both_ends"], ["st_gdna_ds_amplicon"]
                    ),
                    _transition(
                        "tr_gdna_sonication",
                        ["st_gdna_ds_amplicon"],
                        ["st_gdna_sheared"],
                    ),
                    _transition(
                        "tr_gdna_adaptor_removal",
                        ["st_gdna_sheared"],
                        ["st_gdna_ad2_removed"],
                    ),
                    _transition(
                        "tr_truseq_library_prep",
                        ["st_gdna_ad2_removed"],
                        ["st_lib_gdna_truseq"],
                    ),
                ],
                ["st_gdna", "st_mrna"],
                ["st_lib_gdna_truseq"],
            ),
            _workflow(
                "wf_dr_seq_rna",
                "gene expression",
                rna_states,
                [
                    _transition(
                        "tr_reverse_transcription", ["st_mrna"], ["st_rt_hybrid"]
                    ),
                    _transition(
                        "tr_quasilinear_amplification",
                        ["st_rt_hybrid"],
                        ["st_ad2_single_end", "st_ad2_ad1x"],
                        discarded=["st_ad2_single_end"],
                    ),
                    _transition(
                        "tr_second_strand_synthesis", ["st_ad2_ad1x"], ["st_ds_cdna"]
                    ),
                    _transition("tr_ivt", ["st_ds_cdna"], ["st_arna"]),
                    _transition("tr_celseq_library_prep", ["st_arna"], ["st_lib_rna"]),
                ],
                ["st_mrna"],
                ["st_lib_rna"],
            ),
        ],
    )
    gdna_workflow = legacy["workflows"][0]
    gdna_workflow["states"] = [
        _dr_seq_p2_amplicon_state()
        if state["state_id"] == "st_gdna_ds_amplicon"
        else state
        for state in gdna_workflow["states"]
    ]
    final_gdna_state = next(
        state
        for state in gdna_workflow["states"]
        if state["state_id"] == "st_lib_gdna_truseq"
    )
    final_gdna_state["name"] = "Final gDNA library, TruSeq architecture"
    library_prep = next(
        transition
        for transition in gdna_workflow["transitions"]
        if transition["transition_id"] == "tr_truseq_library_prep"
    )
    library_prep.update(
        {
            "operation": "ligation",
            "operation_detail": (
                "Optional traditional gDNA library preparation: fragmentation, "
                "end repair, A-tailing, TruSeq-type adapter ligation and "
                "single-indexed PCR; kit steps are not detailed by the curated "
                "source."
            ),
            "major_reagents": [
                {
                    "name": "TruSeq-type indexed adapters",
                    "role": "adapter ligation",
                }
            ],
            "support_status": "ambiguous",
        }
    )

    before_t1 = _dr_seq_t1()
    before_t3_final = copy.deepcopy(final_gdna_state)
    bundle = migrate_connected_process_bundle(
        {"T1": before_t1, "T2": _dr_seq_t2(), "T3": legacy}
    )
    migrated = bundle["T3"]
    workflow = migrated["workflows"][0]
    transitions = {item["transition_id"]: item for item in workflow["transitions"]}
    states = {item["state_id"]: item for item in workflow["states"]}

    assert set(transitions["tr_quasilinear_amplification"]["substrate_state_ids"]) == {
        "st_gdna",
        "st_rt_hybrid",
    }
    assert transitions["tr_sample_split"]["operation"] == "sample_split"
    assert transitions["tr_gdna_pcr"]["substrate_state_ids"] == [
        "st_postamp_gdna_aliquot"
    ]
    assert transitions["tr_gdna_p3_pcr"]["substrate_state_ids"] == [
        "st_gdna_ds_amplicon"
    ]
    assert transitions["tr_gdna_p3_pcr"]["oligo_ids"] == [
        "oligo_p3_adaptor_removal_primer"
    ]
    assert transitions["tr_gdna_sonication"]["substrate_state_ids"] == [
        "st_gdna_p3_biotin_amplicon"
    ]
    assert transitions["tr_gdna_adaptor_removal"]["oligo_ids"] == []
    assert (
        states["st_gdna_p3_biotin_amplicon"]["strands"][0]["segments"][0]["sequence"]
        == "GTGAGCTGGAGTTGAGGTAGTGTGGAG"
    )
    assert transitions["tr_second_strand_synthesis"]["substrate_state_ids"] == [
        "st_postamp_rna_aliquot"
    ]
    assert states["st_lib_gdna_truseq"]["name"] == (
        "Final gDNA library prepared with NEBNext Ultra DNA Library Prep Kit "
        "for Illumina"
    )
    assert states["st_lib_gdna_truseq"]["strands"] == before_t3_final["strands"]
    assert transitions["tr_truseq_library_prep"]["operation"] == "other"
    assert transitions["tr_truseq_library_prep"]["major_reagents"] == [
        {
            "name": "NEBNext Ultra DNA Library Prep Kit for Illumina",
            "role": "indexed Illumina library preparation",
        }
    ]
    assert transitions["tr_truseq_library_prep"]["support_status"] == "explicit"
    genomic_library = next(
        library
        for library in bundle["T1"]["libraries"]
        if library["modality"] == "genomic DNA"
    )
    assert genomic_library["final_molecule"] == (
        "NEBNext Ultra DNA library prepared from MALBAC-amplified single-cell "
        "genomic DNA"
    )
    assert genomic_library["library_sequence"] == "AAA[GDNA]CCC"
    support_by_id = {
        segment["segment_id"]: segment["support_status"]
        for segment in genomic_library["segments"]
    }
    assert support_by_id["t1_ts_r1"] == "externally_completed"
    assert support_by_id["t1_ts_r2"] == "externally_completed"
    t2 = bundle["T2"]
    validate_task_document(
        "T2", t2, protocol_id="dr_seq", schema_dir=SCHEMA_ROOT / "groundtruth"
    )
    validate_task_document(
        "T3", migrated, protocol_id="dr_seq", schema_dir=SCHEMA_ROOT / "groundtruth"
    )
    validate_cross_task_links({"T2": t2, "T3": migrated})


def test_dr_seq_linked_migration_rejects_a_stale_t1_source_scope() -> None:
    t1 = _dr_seq_t1()
    genomic_library = next(
        library for library in t1["libraries"] if library["modality"] == "genomic DNA"
    )
    genomic_library["final_molecule"] = "unexpected label"

    with pytest.raises(ConnectedProcessMigrationError, match="final-molecule"):
        migrate_connected_process_bundle(
            {"T1": t1, "T2": _dr_seq_t2(), "T3": _document("dr_seq", [])}
        )


def test_share_seq_ligates_both_streams_once_then_affinity_separates() -> None:
    atac_states = [
        "st_chromatin_gdna",
        "st_gdna_tagmented",
        "st_gdna_bc1",
        "st_gdna_bc2",
        "st_gdna_bc3",
        "st_atac_final",
    ]
    rna_states = [
        "st_mrna",
        "st_mrna_cdna_hybrid",
        "st_cdna_bc1",
        "st_cdna_bc2",
        "st_cdna_bc3",
        "st_cdna_ts",
        "st_cdna_amplified",
        "st_cdna_tagmented",
        "st_rna_final",
    ]
    legacy = _document(
        "share_seq",
        [
            _workflow(
                "wf_share_seq_atac",
                "chromatin accessibility",
                atac_states,
                [
                    _transition(
                        "tr_tagmentation_atac",
                        ["st_chromatin_gdna"],
                        ["st_gdna_tagmented"],
                    ),
                    _transition(
                        "tr_ligation_round1", ["st_gdna_tagmented"], ["st_gdna_bc1"]
                    ),
                    _transition("tr_ligation_round2", ["st_gdna_bc1"], ["st_gdna_bc2"]),
                    _transition("tr_ligation_round3", ["st_gdna_bc2"], ["st_gdna_bc3"]),
                    _transition("tr_atac_pcr", ["st_gdna_bc3"], ["st_atac_final"]),
                ],
                ["st_chromatin_gdna"],
                ["st_atac_final"],
            ),
            _workflow(
                "wf_share_seq_rna",
                "gene expression",
                rna_states,
                [
                    _transition(
                        "tr_reverse_transcription", ["st_mrna"], ["st_mrna_cdna_hybrid"]
                    ),
                    _transition(
                        "tr_ligation_round1", ["st_mrna_cdna_hybrid"], ["st_cdna_bc1"]
                    ),
                    _transition("tr_ligation_round2", ["st_cdna_bc1"], ["st_cdna_bc2"]),
                    _transition("tr_ligation_round3", ["st_cdna_bc2"], ["st_cdna_bc3"]),
                    _transition(
                        "tr_template_switching", ["st_cdna_bc3"], ["st_cdna_ts"]
                    ),
                    _transition("tr_cdna_pcr", ["st_cdna_ts"], ["st_cdna_amplified"]),
                    _transition(
                        "tr_cdna_tagmentation",
                        ["st_cdna_amplified"],
                        ["st_cdna_tagmented"],
                    ),
                    _transition(
                        "tr_rna_library_pcr", ["st_cdna_tagmented"], ["st_rna_final"]
                    ),
                ],
                ["st_mrna"],
                ["st_rna_final"],
            ),
        ],
    )

    migrated = migrate_connected_process_t3(legacy)
    workflow = migrated["workflows"][0]
    transition_ids = [item["transition_id"] for item in workflow["transitions"]]

    assert transition_ids.count("tr_ligation_round1") == 1
    assert transition_ids.count("tr_ligation_round2") == 1
    assert transition_ids.count("tr_ligation_round3") == 1
    assert "tr_reverse_crosslink_affinity_separation" in transition_ids
    _validate_t3(migrated)


def test_migration_rejects_new_or_stale_contracts() -> None:
    already_migrated = _document(
        "ordinary_protocol",
        [
            {
                "workflow_id": "workflow",
                "states": [_state("final")],
                "transitions": [],
                "initial_state_ids": ["final"],
                "final_outputs": [{"state_id": "final", "modality": "gene expression"}],
            }
        ],
    )
    with pytest.raises(ConnectedProcessMigrationError, match="legacy contract"):
        migrate_connected_process_t3(already_migrated)


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def _preview_bundle(protocol_id: str) -> tuple[dict, dict, dict]:
    t1 = {
        "protocol_id": protocol_id,
        "protocol_name": protocol_id,
        "libraries": [
            {
                "modality": "gene expression",
                "final_molecule": "DNA",
                "library_sequence": "AAA",
                "strand": "single",
                "orientation": "5_to_3",
                "segments": [
                    {
                        "segment_id": "final_segment",
                        "kind": "constant",
                        "role": "molecule",
                        "sequence": "AAA",
                        "orientation": "5_to_3",
                        "oligo_derivations": [],
                        "support_status": "explicit",
                    }
                ],
                "support_status": "explicit",
            }
        ],
    }
    t2 = {"protocol_id": protocol_id, "protocol_name": protocol_id, "oligos": []}
    t3 = _document(
        protocol_id,
        [
            _workflow(
                "workflow",
                "gene expression",
                ["input", "final"],
                [_transition("make_final", ["input"], ["final"])],
                ["input"],
                ["final"],
            )
        ],
    )
    return t1, t2, t3


def _policy_proposal() -> dict:
    return {
        "proposal_id": "connected-policy",
        "created_at": "2026-08-15T12:00:00Z",
        "status": "unapproved",
        "current_contract": {},
        "proposed_contract": {},
        "benchmark_inventory": {
            "protocol_count": 1,
            "current_workflow_count": 1,
            "current_terminal_output_count": 1,
            "proposed_workflow_count": 1,
            "proposed_terminal_output_count": 1,
            "workflow_merges": [],
            "in_place_graph_connection": [],
            "terminal_annotation_only": [],
        },
        "scoring_semantics": {},
        "human_decisions_required": ["approve preview"],
        "application_gate": "Preview only.",
    }


def _policy_decision(proposal_path: Path) -> dict:
    return {
        "decision_id": "connected-policy-decision",
        "proposal_id": "connected-policy",
        "proposal_sha256": sha256_file(proposal_path),
        "reviewer": {"reviewer_id": "reviewer"},
        "decided_at": "2026-08-15T12:05:00Z",
        "disposition": "accept",
        "accepted_contract": {
            "one_connected_process_per_workflow": True,
            "modality_labeled_final_outputs": True,
            "score_shared_ancestors_once": True,
        },
        "accepted_benchmark_version": "3.0.0",
        "accepted_rescore_policy": {
            "compatible_predictions": "rescore_in_versioned_sidecars",
            "incompatible_predictions": "rerun",
        },
        "final_scientific_approval": "pending",
        "application_authorized": False,
        "notes": "The human accepted compilation of a preview only.",
    }


def _preview_plan(audit_root: Path, baseline_root: Path, protocol_id: str) -> Path:
    policy_proposal = _write_json(
        audit_root / "runs/migration/policy-proposal.json", _policy_proposal()
    )
    policy_decision = _write_json(
        audit_root / "runs/migration/policy-decision.json",
        _policy_decision(policy_proposal),
    )
    artifacts = [
        {
            "filename": filename,
            "sha256": sha256_file(baseline_root / protocol_id / filename),
        }
        for filename in (
            "groundtruth_final_lib_struct.json",
            "groundtruth_oligos.json",
            "groundtruth_library_generation_workflow.json",
        )
    ]
    plan = {
        "migration_id": "connected-preview-test",
        "created_at": "2026-08-15T12:10:00Z",
        "status": "approved_for_preview",
        "benchmark_version_from": "2.0.0",
        "benchmark_version_to": "3.0.0",
        "policy_proposal": {
            "path": "runs/migration/policy-proposal.json",
            "sha256": sha256_file(policy_proposal),
        },
        "policy_decision": {
            "path": "runs/migration/policy-decision.json",
            "sha256": sha256_file(policy_decision),
        },
        "expected_structure": {
            "protocols": 1,
            "workflows": 1,
            "terminal_outputs": 1,
        },
        "protocols": [{"protocol_id": protocol_id, "baseline_artifacts": artifacts}],
        "canonical_ground_truth_write_authorized": False,
    }
    return _write_json(audit_root / "runs/migration/plan.json", plan)


def test_preview_compiler_is_hash_pinned_and_does_not_modify_baseline(
    tmp_path: Path,
) -> None:
    protocol_id = "ordinary_protocol"
    audit_root = tmp_path / "audit"
    baseline_root = tmp_path / "ground_truth"
    t1, t2, t3 = _preview_bundle(protocol_id)
    protocol_dir = baseline_root / protocol_id
    paths = [
        _write_json(protocol_dir / "groundtruth_final_lib_struct.json", t1),
        _write_json(protocol_dir / "groundtruth_oligos.json", t2),
        _write_json(protocol_dir / "groundtruth_library_generation_workflow.json", t3),
    ]
    before = {path: path.read_bytes() for path in paths}
    plan = _preview_plan(audit_root, baseline_root, protocol_id)

    result = compile_connected_process_preview(
        plan_path=plan,
        audit_root=audit_root,
        baseline_root=baseline_root,
        output_dir=audit_root / "runs/migration/preview",
        schema_root=SCHEMA_ROOT,
    )

    assert result.observed_structure == {
        "protocols": 1,
        "workflows": 1,
        "terminal_outputs": 1,
    }
    assert all(path.read_bytes() == before[path] for path in paths)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "preview_only"
    assert manifest["canonical_ground_truth_modified"] is False
    migrated = json.loads(
        (
            result.output_dir
            / "groundtruth"
            / protocol_id
            / "groundtruth_library_generation_workflow.json"
        ).read_text(encoding="utf-8")
    )
    assert migrated["workflows"][0]["final_outputs"] == [
        {"state_id": "final", "modality": "gene expression"}
    ]


def test_preview_compiler_rejects_a_stale_baseline(tmp_path: Path) -> None:
    protocol_id = "ordinary_protocol"
    audit_root = tmp_path / "audit"
    baseline_root = tmp_path / "ground_truth"
    t1, t2, t3 = _preview_bundle(protocol_id)
    protocol_dir = baseline_root / protocol_id
    _write_json(protocol_dir / "groundtruth_final_lib_struct.json", t1)
    _write_json(protocol_dir / "groundtruth_oligos.json", t2)
    t3_path = _write_json(
        protocol_dir / "groundtruth_library_generation_workflow.json", t3
    )
    plan = _preview_plan(audit_root, baseline_root, protocol_id)
    t3["protocol_name"] = "changed after approval"
    _write_json(t3_path, t3)

    with pytest.raises(ConnectedProcessPreviewError, match="stale"):
        compile_connected_process_preview(
            plan_path=plan,
            audit_root=audit_root,
            baseline_root=baseline_root,
            output_dir=audit_root / "runs/migration/preview",
            schema_root=SCHEMA_ROOT,
        )


def test_source_check_is_hash_pinned_and_covers_every_candidate_record(
    tmp_path: Path,
) -> None:
    protocol_id = "ordinary_protocol"
    audit_root = tmp_path / "audit"
    baseline_root = tmp_path / "ground_truth"
    t1, t2, t3 = _preview_bundle(protocol_id)
    protocol_dir = baseline_root / protocol_id
    _write_json(protocol_dir / "groundtruth_final_lib_struct.json", t1)
    _write_json(protocol_dir / "groundtruth_oligos.json", t2)
    _write_json(protocol_dir / "groundtruth_library_generation_workflow.json", t3)
    plan = _preview_plan(audit_root, baseline_root, protocol_id)
    preview = compile_connected_process_preview(
        plan_path=plan,
        audit_root=audit_root,
        baseline_root=baseline_root,
        output_dir=audit_root / "runs/migration/preview",
        schema_root=SCHEMA_ROOT,
    )
    packet = _write_json(
        audit_root / "packets" / protocol_id / "connected-preview-test" / "packet.json",
        {"packet_id": "test-packet", "protocol_id": protocol_id},
    )
    candidate = (
        preview.output_dir
        / "groundtruth"
        / protocol_id
        / "groundtruth_library_generation_workflow.json"
    )
    report = {
        "check_id": "source-check-001",
        "migration_id": "connected-preview-test",
        "checked_at": "2026-08-15T12:20:00Z",
        "status": "complete",
        "checker": {"checker_id": "codex", "role": "audit_controller"},
        "preview_manifest": {
            "path": "preview-manifest.json",
            "sha256": sha256_file(preview.manifest_path),
        },
        "protocols": [
            {
                "protocol_id": protocol_id,
                "packet_sha256": sha256_file(packet),
                "candidate_t3_sha256": sha256_file(candidate),
                "counts": {"states": 2, "transitions": 1},
                "records": [
                    {
                        "record_type": "state",
                        "record_id": "input",
                        "status": "verified",
                        "source_refs": [
                            {"source_id": "primary:test", "locator": "p.1"}
                        ],
                        "notes": "Input state checked directly.",
                    },
                    {
                        "record_type": "state",
                        "record_id": "final",
                        "status": "verified",
                        "source_refs": [
                            {"source_id": "primary:test", "locator": "p.1"}
                        ],
                        "notes": "Final state checked directly.",
                    },
                    {
                        "record_type": "transition",
                        "record_id": "make_final",
                        "status": "verified",
                        "source_refs": [
                            {"source_id": "primary:test", "locator": "p.1"}
                        ],
                        "notes": "Transition checked directly.",
                    },
                ],
            }
        ],
        "resolved_findings": [],
        "summary": {
            "protocols": 1,
            "states": 2,
            "transitions": 1,
            "verified": 3,
            "conflict": 0,
            "missing": 0,
            "ambiguous": 0,
            "unresolved": 0,
        },
    }
    report_path = _write_json(audit_root / "runs/migration/source-check.json", report)

    validated = validate_connected_process_source_check(
        report_path=report_path,
        preview_dir=preview.output_dir,
        audit_root=audit_root,
        schema_root=SCHEMA_ROOT,
    )

    assert validated["summary"]["verified"] == 3
    report["protocols"][0]["records"].pop()
    report["protocols"][0]["counts"]["transitions"] = 0
    report["summary"]["transitions"] = 0
    report["summary"]["verified"] = 2
    _write_json(report_path, report)
    with pytest.raises(ConnectedProcessSourceCheckError, match="coverage"):
        validate_connected_process_source_check(
            report_path=report_path,
            preview_dir=preview.output_dir,
            audit_root=audit_root,
            schema_root=SCHEMA_ROOT,
        )


def test_source_check_rejects_a_stale_packet_hash(tmp_path: Path) -> None:
    protocol_id = "ordinary_protocol"
    audit_root = tmp_path / "audit"
    baseline_root = tmp_path / "ground_truth"
    t1, t2, t3 = _preview_bundle(protocol_id)
    protocol_dir = baseline_root / protocol_id
    _write_json(protocol_dir / "groundtruth_final_lib_struct.json", t1)
    _write_json(protocol_dir / "groundtruth_oligos.json", t2)
    _write_json(protocol_dir / "groundtruth_library_generation_workflow.json", t3)
    plan = _preview_plan(audit_root, baseline_root, protocol_id)
    preview = compile_connected_process_preview(
        plan_path=plan,
        audit_root=audit_root,
        baseline_root=baseline_root,
        output_dir=audit_root / "runs/migration/preview",
        schema_root=SCHEMA_ROOT,
    )
    packet = _write_json(
        audit_root / "packets" / protocol_id / "connected-preview-test" / "packet.json",
        {"packet_id": "test-packet", "protocol_id": protocol_id},
    )
    candidate = (
        preview.output_dir
        / "groundtruth"
        / protocol_id
        / "groundtruth_library_generation_workflow.json"
    )
    report = {
        "check_id": "source-check-stale-packet",
        "migration_id": "connected-preview-test",
        "checked_at": "2026-08-15T12:20:00Z",
        "status": "complete",
        "checker": {"checker_id": "codex", "role": "audit_controller"},
        "preview_manifest": {
            "path": "preview-manifest.json",
            "sha256": sha256_file(preview.manifest_path),
        },
        "protocols": [
            {
                "protocol_id": protocol_id,
                "packet_sha256": "a" * 64,
                "candidate_t3_sha256": sha256_file(candidate),
                "counts": {"states": 2, "transitions": 1},
                "records": [
                    {
                        "record_type": "state",
                        "record_id": record_id,
                        "status": "verified",
                        "source_refs": [
                            {"source_id": "primary:test", "locator": "p.1"}
                        ],
                        "notes": "Record checked directly.",
                    }
                    for record_id in ("input", "final")
                ]
                + [
                    {
                        "record_type": "transition",
                        "record_id": "make_final",
                        "status": "verified",
                        "source_refs": [
                            {"source_id": "primary:test", "locator": "p.1"}
                        ],
                        "notes": "Record checked directly.",
                    }
                ],
            }
        ],
        "resolved_findings": [],
        "summary": {
            "protocols": 1,
            "states": 2,
            "transitions": 1,
            "verified": 3,
            "conflict": 0,
            "missing": 0,
            "ambiguous": 0,
            "unresolved": 0,
        },
    }
    report_path = _write_json(audit_root / "runs/migration/source-check.json", report)

    assert packet.is_file()
    with pytest.raises(ConnectedProcessSourceCheckError, match="packet hash"):
        validate_connected_process_source_check(
            report_path=report_path,
            preview_dir=preview.output_dir,
            audit_root=audit_root,
            schema_root=SCHEMA_ROOT,
        )


def test_final_approval_pins_preview_and_keeps_application_unauthorized(
    tmp_path: Path,
) -> None:
    protocol_id = "ordinary_protocol"
    audit_root = tmp_path / "audit"
    baseline_root = tmp_path / "ground_truth"
    t1, t2, t3 = _preview_bundle(protocol_id)
    protocol_dir = baseline_root / protocol_id
    _write_json(protocol_dir / "groundtruth_final_lib_struct.json", t1)
    _write_json(protocol_dir / "groundtruth_oligos.json", t2)
    _write_json(protocol_dir / "groundtruth_library_generation_workflow.json", t3)
    plan_path = _preview_plan(audit_root, baseline_root, protocol_id)
    preview = compile_connected_process_preview(
        plan_path=plan_path,
        audit_root=audit_root,
        baseline_root=baseline_root,
        output_dir=audit_root / "runs/migration/preview",
        schema_root=SCHEMA_ROOT,
    )
    packet = _write_json(
        audit_root / "packets" / protocol_id / "connected-preview-test" / "packet.json",
        {"packet_id": "test-packet", "protocol_id": protocol_id},
    )
    candidate = (
        preview.output_dir
        / "groundtruth"
        / protocol_id
        / "groundtruth_library_generation_workflow.json"
    )
    source_check = {
        "check_id": "source-check-final-approval",
        "migration_id": "connected-preview-test",
        "checked_at": "2026-08-15T12:20:00Z",
        "status": "complete",
        "checker": {"checker_id": "codex", "role": "audit_controller"},
        "preview_manifest": {
            "path": "preview-manifest.json",
            "sha256": sha256_file(preview.manifest_path),
        },
        "protocols": [
            {
                "protocol_id": protocol_id,
                "packet_sha256": sha256_file(packet),
                "candidate_t3_sha256": sha256_file(candidate),
                "counts": {"states": 2, "transitions": 1},
                "records": [
                    {
                        "record_type": record_type,
                        "record_id": record_id,
                        "status": "verified",
                        "source_refs": [
                            {"source_id": "primary:test", "locator": "p.1"}
                        ],
                        "notes": "Record checked directly.",
                    }
                    for record_type, record_id in (
                        ("state", "input"),
                        ("state", "final"),
                        ("transition", "make_final"),
                    )
                ],
            }
        ],
        "resolved_findings": [],
        "summary": {
            "protocols": 1,
            "states": 2,
            "transitions": 1,
            "verified": 3,
            "conflict": 0,
            "missing": 0,
            "ambiguous": 0,
            "unresolved": 0,
        },
    }
    source_check_path = _write_json(
        audit_root / "runs/migration/source-check.json", source_check
    )
    approval_path = audit_root / "runs/migration/final-approval.json"
    approval = record_connected_process_final_approval(
        preview_manifest_path=preview.manifest_path,
        source_check_path=source_check_path,
        migration_plan_path=plan_path,
        approval_path=approval_path,
        audit_root=audit_root,
        schema_root=SCHEMA_ROOT,
        reviewer={"reviewer_id": "reviewer"},
        approved_at="2026-08-15T12:30:00Z",
    )

    validated = validate_connected_process_final_approval(
        approval_path=approval_path,
        audit_root=audit_root,
        schema_root=SCHEMA_ROOT,
    )

    assert validated["scientific_disposition"] == "approved"
    assert validated["application_authorized"] is False
    approval["preview_manifest"]["sha256"] = "a" * 64
    _write_json(approval_path, approval)
    with pytest.raises(ConnectedProcessFinalApprovalError, match="preview manifest"):
        validate_connected_process_final_approval(
            approval_path=approval_path,
            audit_root=audit_root,
            schema_root=SCHEMA_ROOT,
        )


def test_connected_process_counts_counts_graphs_once() -> None:
    migrated = migrate_connected_process_t3(
        _document(
            "ordinary_protocol",
            [
                _workflow(
                    "workflow",
                    "gene expression",
                    ["input", "a", "b"],
                    [_transition("branch", ["input"], ["a", "b"])],
                    ["input"],
                    ["a", "b"],
                )
            ],
        )
    )
    assert connected_process_counts([migrated]) == {
        "protocols": 1,
        "workflows": 1,
        "terminal_outputs": 2,
    }
