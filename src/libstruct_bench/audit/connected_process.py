from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from typing import Any


class ConnectedProcessMigrationError(ValueError):
    """Raised when a T3 record cannot be migrated without an assumption."""


SPECIAL_MIGRATIONS = frozenset(
    {
        "10x_chromium_3_feature_barcoding",
        "ddseq_single_cell_3_rna_seq_kit",
        "dr_seq",
        "share_seq",
    }
)


_DR_SEQ_P2_SEQUENCE = "GTGAGTGATGGTTGAGGTAGTGTGGAG"
_DR_SEQ_P2_REVERSE_COMPLEMENT = "CTCCACACTACCTCAACCATCACTCAC"
_DR_SEQ_P3_SEQUENCE = "GTGAGCTGGAGTTGAGGTAGTGTGGAG"
_DR_SEQ_P3_REVERSE_COMPLEMENT = "CTCCACACTACCTCAACTCCAGCTCAC"
_DR_SEQ_LEGACY_FINAL_MOLECULE = (
    "TruSeq-type single-indexed Illumina library prepared from "
    "MALBAC-amplified single-cell genomic DNA"
)
_DR_SEQ_NEBNEXT_FINAL_MOLECULE = (
    "NEBNext Ultra DNA library prepared from MALBAC-amplified single-cell genomic DNA"
)
_DR_SEQ_LEGACY_FINAL_STATE_NAME = "Final gDNA library, TruSeq architecture"
_DR_SEQ_NEBNEXT_FINAL_STATE_NAME = (
    "Final gDNA library prepared with NEBNext Ultra DNA Library Prep Kit for Illumina"
)
_DR_SEQ_LEGACY_LIBRARY_PREP_DETAIL = (
    "Optional traditional gDNA library preparation: fragmentation, end repair, "
    "A-tailing, TruSeq-type adapter ligation and single-indexed PCR; kit steps "
    "are not detailed by the curated source."
)
_DR_SEQ_NEBNEXT_LIBRARY_PREP_DETAIL = (
    "The Ad-2-depleted genomic DNA was converted to an indexed Illumina library "
    "with the NEBNext Ultra DNA Library Prep Kit for Illumina; the packet does "
    "not print the kit adapter architecture or index-primer sequences."
)


def migrate_connected_process_bundle(
    documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Compile a linked T1--T3 bundle into the approved connected contract.

    Most protocols change only in T3. DR-seq also has an accepted, source-scoped
    T1 correction that must be applied in the same deterministic compilation so
    the final-library description and workflow terminal remain synchronized.
    """

    required_tasks = {"T1", "T2", "T3"}
    if set(documents) != required_tasks:
        raise ConnectedProcessMigrationError(
            "connected-process migration requires exactly T1, T2, and T3"
        )
    result = {
        task: copy.deepcopy(dict(documents[task])) for task in sorted(required_tasks)
    }
    protocol_ids = {
        _required_string(document, "protocol_id", f"{task} document")
        for task, document in result.items()
    }
    if len(protocol_ids) != 1:
        raise ConnectedProcessMigrationError(
            "connected-process T1--T3 documents have inconsistent protocol IDs"
        )
    protocol_id = protocol_ids.pop()
    if protocol_id == "dr_seq":
        _migrate_dr_seq_t1(result["T1"])
    result["T3"] = migrate_connected_process_t3(result["T3"])
    return result


def migrate_connected_process_t3(document: Mapping[str, Any]) -> dict[str, Any]:
    """Compile an approved legacy T3 record into the connected-process contract.

    The transformation is deliberately strict. It accepts only the legacy
    ``workflow.modality`` plus ``final_state_ids`` representation and refuses
    inputs whose reviewed workflow/state/transition identifiers do not match
    the deterministic recipe.
    """

    result = copy.deepcopy(dict(document))
    protocol_id = _required_string(result, "protocol_id", "T3 document")
    workflows = result.get("workflows")
    if not isinstance(workflows, list) or not workflows:
        raise ConnectedProcessMigrationError("T3 document has no workflows")
    _require_legacy_contract(workflows, protocol_id)

    if protocol_id == "10x_chromium_3_feature_barcoding":
        result["workflows"] = [_migrate_10x_feature_barcoding(workflows)]
    elif protocol_id == "ddseq_single_cell_3_rna_seq_kit":
        result["workflows"] = [_migrate_ddseq(workflows)]
    elif protocol_id == "dr_seq":
        result["workflows"] = [_migrate_dr_seq(workflows)]
    elif protocol_id == "share_seq":
        result["workflows"] = [_migrate_share_seq(workflows)]
    else:
        result["workflows"] = [_terminal_contract(workflow) for workflow in workflows]

    return result


def connected_process_counts(
    documents: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    """Return release-level protocol, workflow, and terminal-output counts."""

    protocols = 0
    workflows = 0
    terminal_outputs = 0
    for document in documents:
        protocols += 1
        records = document.get("workflows", [])
        workflows += len(records)
        terminal_outputs += sum(
            len(workflow.get("final_outputs", [])) for workflow in records
        )
    return {
        "protocols": protocols,
        "workflows": workflows,
        "terminal_outputs": terminal_outputs,
    }


def _migrate_10x_feature_barcoding(
    workflows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = _workflows_by_id(
        workflows,
        {
            "wf_10x3fb_gene_expression",
            "wf_10x3fb_feature_barcode",
        },
    )
    gex = by_id["wf_10x3fb_gene_expression"]
    feature = by_id["wf_10x3fb_feature_barcode"]
    states = _merged_records((gex, feature), "states", "state_id")
    state_map = _records_by_id(states, "state_id", "10x state")

    gex_amplified = copy.deepcopy(
        _required_record(state_map, "st_amplified_cdna_from_mrna", "10x state")
    )
    feature_amplified = copy.deepcopy(
        _required_record(state_map, "st_amplified_feature_barcode_dna", "10x state")
    )
    _set_state_context(
        state_map["st_amplified_cdna_from_mrna"],
        name=(
            "Amplified double-stranded cDNA from poly-adenylated mRNA before "
            "size separation"
        ),
        physical_state=(
            "bulk pooled amplification product before 0.6X SPRIselect separation"
        ),
        properties=["co-amplified with the Feature Barcode DNA product"],
    )
    _set_state_context(
        state_map["st_amplified_feature_barcode_dna"],
        name="Amplified double-stranded Feature Barcode DNA (~129 bp) before size separation",
        physical_state=(
            "bulk pooled amplification product before 0.6X SPRIselect separation"
        ),
        properties=["co-amplified with the long gene-expression cDNA product"],
    )
    gex_fraction = _clone_state(
        gex_amplified,
        state_id="st_size_selected_cdna_from_mrna",
        name="Size-selected amplified cDNA from poly-adenylated mRNA",
    )
    feature_fraction = _clone_state(
        feature_amplified,
        state_id="st_size_selected_feature_barcode_dna",
        name="Size-selected amplified Feature Barcode DNA (~129 bp)",
    )
    states.extend([gex_fraction, feature_fraction])

    gex_transitions = _transitions_by_id(gex)
    feature_transitions = _transitions_by_id(feature)
    gem_rt = _combine_transition(
        [gex_transitions["tr_gem_rt"], feature_transitions["tr_gem_rt"]],
        substrate_state_ids=[
            "st_gem_mrna_on_polydt_primer",
            "st_gem_adt_on_capture_primer",
        ],
        product_state_ids=["st_mrna_cdna_hybrid", "st_adt_cdna_duplex"],
        carried_forward_product_ids=[
            "st_mrna_cdna_hybrid",
            "st_adt_cdna_duplex",
        ],
        discarded_product_ids=[],
    )
    pooled_amplification = _combine_transition(
        [
            gex_transitions["tr_pooled_cdna_amplification"],
            feature_transitions["tr_pooled_cdna_amplification"],
        ],
        substrate_state_ids=["st_cdna_from_mrna", "st_adt_cdna_duplex"],
        product_state_ids=[
            "st_amplified_cdna_from_mrna",
            "st_amplified_feature_barcode_dna",
        ],
        carried_forward_product_ids=[
            "st_amplified_cdna_from_mrna",
            "st_amplified_feature_barcode_dna",
        ],
        discarded_product_ids=[],
    )
    pooled_amplification["operation_detail"] = (
        "Pooled amplification with Feature cDNA Primers 2 (PN-2000097), a "
        "mixture of the mRNA and Feature Barcode primer pairs, produces the "
        "long gene-expression cDNA and ~129 bp Feature Barcode DNA together."
    )
    pooled_amplification["major_reagents"] = [
        reagent
        for reagent in pooled_amplification["major_reagents"]
        if reagent.get("name") != "SPRIselect reagent"
    ]
    separation = _new_transition(
        transition_id="tr_size_separation",
        substrate_state_ids=[
            "st_amplified_cdna_from_mrna",
            "st_amplified_feature_barcode_dna",
        ],
        operation="size_selection",
        operation_detail=(
            "A 0.6X SPRIselect separation retains long gene-expression cDNA on "
            "the beads and carries the ~129 bp Feature Barcode product in the "
            "supernatant; the two fractions then enter their library-specific "
            "branches."
        ),
        major_reagents=[
            {
                "name": "SPRIselect reagent",
                "role": "size separation into bead-bound and supernatant fractions",
            }
        ],
        product_state_ids=[
            "st_size_selected_cdna_from_mrna",
            "st_size_selected_feature_barcode_dna",
        ],
        support_status="explicit",
    )
    gex_fragmentation = _rewire_transition(
        gex_transitions["tr_gex_fragmentation"],
        substrate_state_ids=["st_size_selected_cdna_from_mrna"],
    )
    feature_pcr = _rewire_transition(
        feature_transitions["tr_feature_library_pcr"],
        substrate_state_ids=["st_size_selected_feature_barcode_dna"],
    )

    return _workflow(
        workflow_id="wf_10x3fb",
        source_workflows=(gex, feature),
        states=states,
        transitions=[
            gem_rt,
            copy.deepcopy(gex_transitions["tr_template_switching"]),
            pooled_amplification,
            separation,
            gex_fragmentation,
            copy.deepcopy(gex_transitions["tr_gex_adaptor_ligation"]),
            copy.deepcopy(gex_transitions["tr_gex_sample_index_pcr"]),
            feature_pcr,
        ],
        initial_state_ids=[
            "st_gem_mrna_on_polydt_primer",
            "st_gem_adt_on_capture_primer",
        ],
        final_outputs=[
            {"state_id": "st_final_gex_library", "modality": "gene expression"},
            {
                "state_id": "st_final_cell_surface_protein_library",
                "modality": "feature barcode",
            },
        ],
    )


def _migrate_ddseq(workflows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = _workflows_by_id(workflows, {"wf_ddseq_gene_expression"})
    source = by_id["wf_ddseq_gene_expression"]
    states = copy.deepcopy(source["states"])
    state_map = _records_by_id(states, "state_id", "ddSEQ state")
    cdna = copy.deepcopy(_required_record(state_map, "st_cdna_duplex", "ddSEQ state"))
    do = copy.deepcopy(_required_record(state_map, "st_do_duplex", "ddSEQ state"))
    _set_state_context(
        state_map["st_cdna_duplex"],
        name="Barcoded double-stranded cDNA before cDNA/DO fraction separation",
        physical_state="in the disrupted-emulsion mixture before SPRI fraction separation",
        properties=["generated in droplets alongside filled-in DO dimers"],
    )
    _set_state_context(
        state_map["st_do_duplex"],
        name="Filled-in deconvolution oligo dimer before cDNA/DO fraction separation",
        physical_state="in the disrupted-emulsion mixture before SPRI fraction separation",
        properties=[
            "carries the barcodes of both connected beads",
            "generated in droplets alongside barcoded cDNA",
            "not tagmented",
        ],
    )
    states.extend(
        [
            _clone_state(
                cdna,
                state_id="st_purified_cdna_fraction",
                name="Purified bead-bound barcoded double-stranded cDNA fraction",
            ),
            _clone_state(
                do,
                state_id="st_purified_do_fraction",
                name="Purified supernatant deconvolution-oligo dimer fraction",
            ),
        ]
    )

    transitions = _transitions_by_id(source)
    second_strand = copy.deepcopy(transitions["tr_second_strand_synthesis"])
    second_strand["operation_detail"] = (
        "In-droplet second-strand cDNA synthesis converts the mRNA:cDNA hybrid "
        "into barcoded double-stranded cDNA. Droplet disruption is folded into "
        "this transition."
    )
    second_strand["major_reagents"] = [
        reagent
        for reagent in second_strand["major_reagents"]
        if reagent.get("name") != "purification beads"
    ]
    do_extension = copy.deepcopy(transitions["tr_do_extension"])
    do_extension["operation_detail"] = (
        "In-droplet polymerase extension of both recessed 3' ends of the "
        "annealed DO-A/DO-B pair produces a full-length double-stranded DO "
        "dimer that records which beads shared a droplet."
    )
    do_extension["major_reagents"] = [
        reagent
        for reagent in do_extension["major_reagents"]
        if reagent.get("name") != "purification beads"
    ]
    separation = _new_transition(
        transition_id="tr_cdna_do_fraction_separation",
        substrate_state_ids=["st_cdna_duplex", "st_do_duplex"],
        operation="size_selection",
        operation_detail=(
            "After droplet disruption, SPRI purification retains the cDNA in "
            "the bead-bound fraction while DO dimers remain in the supernatant; "
            "the two fractions are purified separately before library preparation."
        ),
        major_reagents=[
            {
                "name": "purification beads",
                "role": "separation of bead-bound cDNA and supernatant DO dimers",
            }
        ],
        product_state_ids=[
            "st_purified_cdna_fraction",
            "st_purified_do_fraction",
        ],
        support_status="explicit",
    )

    return _workflow(
        workflow_id="wf_ddseq_gene_expression",
        source_workflows=(source,),
        states=states,
        transitions=[
            copy.deepcopy(transitions["tr_reverse_transcription"]),
            second_strand,
            do_extension,
            separation,
            _rewire_transition(
                transitions["tr_tagmentation"],
                substrate_state_ids=["st_purified_cdna_fraction"],
            ),
            copy.deepcopy(transitions["tr_cdna_index_pcr"]),
            _rewire_transition(
                transitions["tr_do_index_pcr"],
                substrate_state_ids=["st_purified_do_fraction"],
            ),
        ],
        initial_state_ids=["st_mrna", "st_do_annealed"],
        final_outputs=[
            {"state_id": "st_cdna_library", "modality": "gene expression"},
            {"state_id": "st_do_library", "modality": "gene expression"},
        ],
    )


def _migrate_dr_seq(workflows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = _workflows_by_id(workflows, {"wf_dr_seq_gdna", "wf_dr_seq_rna"})
    gdna = by_id["wf_dr_seq_gdna"]
    rna = by_id["wf_dr_seq_rna"]
    states = _merged_records((gdna, rna), "states", "state_id")
    state_map = _records_by_id(states, "state_id", "DR-seq state")
    _migrate_dr_seq_final_state(
        _required_record(state_map, "st_lib_gdna_truseq", "DR-seq state")
    )
    both = _required_record(state_map, "st_ad2_both_ends", "DR-seq state")
    ad1x = _required_record(state_map, "st_ad2_ad1x", "DR-seq state")
    p3_amplicon = _dr_seq_p3_amplicon_state(
        _required_record(state_map, "st_gdna_ds_amplicon", "DR-seq state")
    )
    _set_state_context(
        state_map["st_gdna_sheared"],
        name=(
            "Sonicated P3-biotin-tagged MALBAC product mixture (~300 bp) "
            "before streptavidin depletion"
        ),
        physical_state="in solution after sonication and PCR-column cleanup",
        properties=[
            "average product size approximately 300 bp",
            (
                "contains non-biotinylated internal fragments and "
                "biotinylated terminal fragments"
            ),
        ],
    )
    states.extend(
        [
            p3_amplicon,
            _mixed_aliquot_state(
                state_id="st_postamp_gdna_aliquot",
                name="Post-amplification mixed molecular population, gDNA-processing aliquot",
                physical_state="one half of the split reaction reserved for gDNA processing",
                sources=(both, ad1x),
                branch_property="gDNA-processing half of the split reaction",
            ),
            _mixed_aliquot_state(
                state_id="st_postamp_rna_aliquot",
                name="Post-amplification mixed molecular population, RNA-processing aliquot",
                physical_state="one half of the split reaction reserved for RNA processing",
                sources=(both, ad1x),
                branch_property="RNA-processing half of the split reaction",
            ),
        ]
    )

    gdna_transitions = _transitions_by_id(gdna)
    rna_transitions = _transitions_by_id(rna)
    reverse_transcription = _combine_transition(
        [
            gdna_transitions["tr_reverse_transcription"],
            rna_transitions["tr_reverse_transcription"],
        ],
        substrate_state_ids=["st_mrna"],
        product_state_ids=["st_rt_hybrid"],
        carried_forward_product_ids=["st_rt_hybrid"],
        discarded_product_ids=[],
    )
    amplification = _combine_transition(
        [
            gdna_transitions["tr_quasilinear_amplification"],
            rna_transitions["tr_quasilinear_amplification"],
        ],
        substrate_state_ids=["st_gdna", "st_rt_hybrid"],
        product_state_ids=[
            "st_ad2_single_end",
            "st_ad2_both_ends",
            "st_ad2_ad1x",
        ],
        carried_forward_product_ids=["st_ad2_both_ends", "st_ad2_ad1x"],
        discarded_product_ids=["st_ad2_single_end"],
    )
    split = _new_transition(
        transition_id="tr_sample_split",
        substrate_state_ids=["st_ad2_both_ends", "st_ad2_ad1x"],
        operation="sample_split",
        operation_detail=(
            "After seven quasilinear amplification cycles, the unsplit mixture "
            "is divided into equal gDNA-processing and RNA-processing aliquots; "
            "each aliquot retains the carried Ad-2/Ad-2 and Ad-2/Ad-1x molecular classes."
        ),
        major_reagents=[],
        product_state_ids=["st_postamp_gdna_aliquot", "st_postamp_rna_aliquot"],
        support_status="explicit",
    )
    p3_pcr = _new_transition(
        transition_id="tr_gdna_p3_pcr",
        substrate_state_ids=["st_gdna_ds_amplicon"],
        operation="pcr",
        operation_detail=(
            "A second PCR with 5-prime-biotinylated primer P3 tags the "
            "Ad-2-containing termini before sonication."
        ),
        oligo_ids=["oligo_p3_adaptor_removal_primer"],
        major_reagents=[
            {
                "name": "Deep VentR (exo-) polymerase",
                "role": "P3 primer-substitution PCR",
            }
        ],
        product_state_ids=["st_gdna_p3_biotin_amplicon"],
        support_status="explicit",
    )
    sonication = _rewire_transition(
        gdna_transitions["tr_gdna_sonication"],
        substrate_state_ids=["st_gdna_p3_biotin_amplicon"],
    )
    sonication["operation_detail"] = (
        "The P3-biotin-tagged PCR product was sheared by sonication, purified, "
        "and verified at an average product size of approximately 300 bp."
    )
    adaptor_removal = copy.deepcopy(gdna_transitions["tr_gdna_adaptor_removal"])
    adaptor_removal["oligo_ids"] = []
    adaptor_removal["operation_detail"] = (
        "The sheared products were incubated with Dynabeads MyOne Streptavidin "
        "C1; biotinylated terminal fragments were immobilized, and the combined "
        "non-biotinylated supernatant and washes were carried forward."
    )

    library_prep = copy.deepcopy(gdna_transitions["tr_truseq_library_prep"])
    _migrate_dr_seq_library_prep(library_prep)

    return _workflow(
        workflow_id="wf_dr_seq",
        source_workflows=(gdna, rna),
        states=states,
        transitions=[
            reverse_transcription,
            amplification,
            split,
            _rewire_transition(
                gdna_transitions["tr_gdna_pcr"],
                substrate_state_ids=["st_postamp_gdna_aliquot"],
            ),
            p3_pcr,
            sonication,
            adaptor_removal,
            library_prep,
            _rewire_transition(
                rna_transitions["tr_second_strand_synthesis"],
                substrate_state_ids=["st_postamp_rna_aliquot"],
            ),
            copy.deepcopy(rna_transitions["tr_ivt"]),
            copy.deepcopy(rna_transitions["tr_celseq_library_prep"]),
        ],
        initial_state_ids=["st_gdna", "st_mrna"],
        final_outputs=[
            {"state_id": "st_lib_gdna_truseq", "modality": "genomic DNA"},
            {"state_id": "st_lib_rna", "modality": "gene expression"},
        ],
    )


def _migrate_dr_seq_t1(document: dict[str, Any]) -> None:
    libraries = document.get("libraries")
    if not isinstance(libraries, list):
        raise ConnectedProcessMigrationError("DR-seq T1 document has no libraries")
    genomic_libraries = [
        library for library in libraries if library.get("modality") == "genomic DNA"
    ]
    if len(genomic_libraries) != 1:
        raise ConnectedProcessMigrationError(
            "DR-seq T1 must contain exactly one genomic DNA library"
        )
    library = genomic_libraries[0]
    if library.get("final_molecule") != _DR_SEQ_LEGACY_FINAL_MOLECULE:
        raise ConnectedProcessMigrationError(
            "DR-seq T1 genomic final-molecule label differs from the reviewed baseline"
        )
    segments = library.get("segments")
    if not isinstance(segments, list):
        raise ConnectedProcessMigrationError(
            "DR-seq T1 genomic library has invalid segments"
        )
    by_id = _records_by_id(segments, "segment_id", "DR-seq T1 segment")
    for segment_id in ("t1_ts_r1", "t1_ts_r2"):
        segment = _required_record(by_id, segment_id, "DR-seq T1 segment")
        if segment.get("support_status") != "explicit":
            raise ConnectedProcessMigrationError(
                f"DR-seq T1 {segment_id} support differs from the reviewed baseline"
            )
        segment["support_status"] = "externally_completed"
    library["final_molecule"] = _DR_SEQ_NEBNEXT_FINAL_MOLECULE


def _migrate_dr_seq_final_state(state: dict[str, Any]) -> None:
    if state.get("name") != _DR_SEQ_LEGACY_FINAL_STATE_NAME:
        raise ConnectedProcessMigrationError(
            "DR-seq final gDNA state label differs from the reviewed baseline"
        )
    state["name"] = _DR_SEQ_NEBNEXT_FINAL_STATE_NAME


def _migrate_dr_seq_library_prep(transition: dict[str, Any]) -> None:
    expected_reagents = [
        {"name": "TruSeq-type indexed adapters", "role": "adapter ligation"}
    ]
    expected = {
        "operation": "ligation",
        "operation_detail": _DR_SEQ_LEGACY_LIBRARY_PREP_DETAIL,
        "major_reagents": expected_reagents,
        "support_status": "ambiguous",
    }
    stale_fields = [
        field for field, value in expected.items() if transition.get(field) != value
    ]
    if stale_fields:
        raise ConnectedProcessMigrationError(
            "DR-seq terminal gDNA library-prep fields differ from the reviewed "
            "baseline: " + ", ".join(stale_fields)
        )
    transition.update(
        {
            "operation": "other",
            "operation_detail": _DR_SEQ_NEBNEXT_LIBRARY_PREP_DETAIL,
            "major_reagents": [
                {
                    "name": "NEBNext Ultra DNA Library Prep Kit for Illumina",
                    "role": "indexed Illumina library preparation",
                }
            ],
            "support_status": "explicit",
        }
    )


def _dr_seq_p3_amplicon_state(source: Mapping[str, Any]) -> dict[str, Any]:
    """Return the reviewed P3-biotinylated PCR product from the pinned P2 state."""

    result = copy.deepcopy(dict(source))
    if result.get("state_id") != "st_gdna_ds_amplicon":
        raise ConnectedProcessMigrationError(
            "DR-seq P3 PCR requires the reviewed st_gdna_ds_amplicon state"
        )
    strand_id_map = {"pcr_top": "p3_top", "pcr_bottom": "p3_bottom"}
    segment_id_map = {
        "pcr_top_h5": "p3_top_h5",
        "pcr_top_body": "p3_top_body",
        "pcr_top_h3": "p3_top_h3",
        "pcr_bot_h5": "p3_bot_h5",
        "pcr_bot_body": "p3_bot_body",
        "pcr_bot_h3": "p3_bot_h3",
    }
    strands = result.get("strands")
    if not isinstance(strands, list) or {
        strand.get("strand_id") for strand in strands
    } != set(strand_id_map):
        raise ConnectedProcessMigrationError(
            "DR-seq st_gdna_ds_amplicon strand IDs differ from the reviewed baseline"
        )

    for strand in strands:
        old_strand_id = strand["strand_id"]
        strand["strand_id"] = strand_id_map[old_strand_id]
        segments = strand.get("segments")
        if not isinstance(segments, list):
            raise ConnectedProcessMigrationError(
                "DR-seq st_gdna_ds_amplicon has invalid segments"
            )
        for segment in segments:
            old_segment_id = segment.get("segment_id")
            if old_segment_id not in segment_id_map:
                raise ConnectedProcessMigrationError(
                    "DR-seq st_gdna_ds_amplicon segment IDs differ from the "
                    "reviewed baseline"
                )
            segment["segment_id"] = segment_id_map[old_segment_id]
            if old_segment_id.endswith("h5"):
                _replace_dr_seq_primer_derivation(
                    segment,
                    expected_sequence=_DR_SEQ_P2_SEQUENCE,
                    replacement_sequence=_DR_SEQ_P3_SEQUENCE,
                )
            elif old_segment_id.endswith("h3"):
                _replace_dr_seq_primer_derivation(
                    segment,
                    expected_sequence=_DR_SEQ_P2_REVERSE_COMPLEMENT,
                    replacement_sequence=_DR_SEQ_P3_REVERSE_COMPLEMENT,
                )

    paired_regions = result.get("paired_regions")
    if not isinstance(paired_regions, list) or len(paired_regions) != 1:
        raise ConnectedProcessMigrationError(
            "DR-seq st_gdna_ds_amplicon pairing differs from the reviewed baseline"
        )
    region = paired_regions[0]
    if region.get("paired_region_id") != "pr_pcr":
        raise ConnectedProcessMigrationError(
            "DR-seq st_gdna_ds_amplicon paired-region ID differs from the "
            "reviewed baseline"
        )
    region["paired_region_id"] = "pr_p3"
    for side_key in ("side_1", "side_2"):
        side = region.get(side_key)
        if not isinstance(side, dict) or side.get("strand_id") not in strand_id_map:
            raise ConnectedProcessMigrationError(
                "DR-seq st_gdna_ds_amplicon paired-region strands differ from "
                "the reviewed baseline"
            )
        side["strand_id"] = strand_id_map[side["strand_id"]]
        try:
            side["segment_ids"] = [
                segment_id_map[segment_id] for segment_id in side["segment_ids"]
            ]
        except (KeyError, TypeError) as error:
            raise ConnectedProcessMigrationError(
                "DR-seq st_gdna_ds_amplicon paired-region segments differ from "
                "the reviewed baseline"
            ) from error

    result.update(
        {
            "state_id": "st_gdna_p3_biotin_amplicon",
            "name": (
                "P3-biotin-tagged double-stranded MALBAC amplicon before sonication"
            ),
            "reference_strand_id": "p3_top",
            "physical_state": "purified P3 PCR product before sonication",
            "properties": [
                "gDNA-processing aliquot",
                "Ad-2 termini replaced by 5-prime-biotinylated P3 termini",
            ],
        }
    )
    return result


def _replace_dr_seq_primer_derivation(
    segment: dict[str, Any],
    *,
    expected_sequence: str,
    replacement_sequence: str,
) -> None:
    if segment.get("sequence") != expected_sequence:
        raise ConnectedProcessMigrationError(
            "DR-seq MALBAC PCR-primer sequence differs from the reviewed baseline"
        )
    derivations = segment.get("oligo_derivations")
    if not isinstance(derivations, list) or len(derivations) != 1:
        raise ConnectedProcessMigrationError(
            "DR-seq MALBAC PCR-primer derivation differs from the reviewed baseline"
        )
    derivation = derivations[0]
    if derivation.get("oligo_id") != "oligo_malbac_pcr_primer":
        raise ConnectedProcessMigrationError(
            "DR-seq MALBAC PCR-primer ID differs from the reviewed baseline"
        )
    derivation["oligo_id"] = "oligo_p3_adaptor_removal_primer"
    segment["sequence"] = replacement_sequence


def _migrate_share_seq(workflows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = _workflows_by_id(workflows, {"wf_share_seq_atac", "wf_share_seq_rna"})
    atac = by_id["wf_share_seq_atac"]
    rna = by_id["wf_share_seq_rna"]
    states = _merged_records((atac, rna), "states", "state_id")
    state_map = _records_by_id(states, "state_id", "SHARE-seq state")
    gdna_bc3 = copy.deepcopy(
        _required_record(state_map, "st_gdna_bc3", "SHARE-seq state")
    )
    cdna_bc3 = copy.deepcopy(
        _required_record(state_map, "st_cdna_bc3", "SHARE-seq state")
    )
    _set_state_context(
        state_map["st_gdna_bc3"],
        name=(
            "Tagmented genomic DNA after round-3 barcode ligation (P7 acquired), "
            "before affinity separation"
        ),
        physical_state=(
            "fully barcoded chromatin fragment before reverse crosslinking and "
            "streptavidin separation"
        ),
        properties=[
            item
            for item in state_map["st_gdna_bc3"]["properties"]
            if "supernatant" not in item
        ],
    )
    _set_state_context(
        state_map["st_cdna_bc3"],
        name=(
            "mRNA:cDNA hybrid after round-3 barcode ligation (P7 acquired), "
            "before affinity separation"
        ),
        physical_state=(
            "fully barcoded hybrid before reverse crosslinking and streptavidin separation"
        ),
        properties=[
            item
            for item in state_map["st_cdna_bc3"]["properties"]
            if "streptavidin" not in item
        ],
    )
    states.extend(
        [
            _clone_state(
                gdna_bc3,
                state_id="st_atac_supernatant_fraction",
                name="Barcoded transposed DNA in the post-capture supernatant",
            ),
            _clone_state(
                cdna_bc3,
                state_id="st_cdna_streptavidin_fraction",
                name="Barcoded cDNA captured on streptavidin beads",
            ),
        ]
    )

    atac_transitions = _transitions_by_id(atac)
    rna_transitions = _transitions_by_id(rna)
    ligations = [
        _combine_transition(
            [atac_transitions[transition_id], rna_transitions[transition_id]],
            substrate_state_ids=substrates,
            product_state_ids=products,
            carried_forward_product_ids=products,
            discarded_product_ids=[],
        )
        for transition_id, substrates, products in (
            (
                "tr_ligation_round1",
                ["st_gdna_tagmented", "st_mrna_cdna_hybrid"],
                ["st_gdna_bc1", "st_cdna_bc1"],
            ),
            (
                "tr_ligation_round2",
                ["st_gdna_bc1", "st_cdna_bc1"],
                ["st_gdna_bc2", "st_cdna_bc2"],
            ),
            (
                "tr_ligation_round3",
                ["st_gdna_bc2", "st_cdna_bc2"],
                ["st_gdna_bc3", "st_cdna_bc3"],
            ),
        )
    ]
    separation = _new_transition(
        transition_id="tr_reverse_crosslink_affinity_separation",
        substrate_state_ids=["st_gdna_bc3", "st_cdna_bc3"],
        operation="affinity_selection",
        operation_detail=(
            "Reverse crosslinking releases both barcoded molecular streams, "
            "after which streptavidin beads capture the biotinylated cDNA while "
            "barcoded transposed DNA remains in the supernatant."
        ),
        major_reagents=[
            {"name": "proteinase K", "role": "reverse crosslinking"},
            {"name": "SDS", "role": "reverse crosslinking"},
            {
                "name": "MyOne C1 streptavidin Dynabeads",
                "role": "affinity separation of biotinylated cDNA",
            },
        ],
        product_state_ids=[
            "st_atac_supernatant_fraction",
            "st_cdna_streptavidin_fraction",
        ],
        support_status="explicit",
    )

    return _workflow(
        workflow_id="wf_share_seq",
        source_workflows=(atac, rna),
        states=states,
        transitions=[
            copy.deepcopy(atac_transitions["tr_tagmentation_atac"]),
            copy.deepcopy(rna_transitions["tr_reverse_transcription"]),
            *ligations,
            separation,
            _rewire_transition(
                atac_transitions["tr_atac_pcr"],
                substrate_state_ids=["st_atac_supernatant_fraction"],
            ),
            _rewire_transition(
                rna_transitions["tr_template_switching"],
                substrate_state_ids=["st_cdna_streptavidin_fraction"],
            ),
            copy.deepcopy(rna_transitions["tr_cdna_pcr"]),
            copy.deepcopy(rna_transitions["tr_cdna_tagmentation"]),
            copy.deepcopy(rna_transitions["tr_rna_library_pcr"]),
        ],
        initial_state_ids=["st_chromatin_gdna", "st_mrna"],
        final_outputs=[
            {"state_id": "st_atac_final", "modality": "chromatin accessibility"},
            {"state_id": "st_rna_final", "modality": "gene expression"},
        ],
    )


def _terminal_contract(workflow: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(workflow))
    modality = _required_string(result, "modality", "legacy T3 workflow")
    final_state_ids = result.get("final_state_ids")
    if not isinstance(final_state_ids, list) or not final_state_ids:
        raise ConnectedProcessMigrationError(
            f"workflow {result.get('workflow_id')!r} has no final_state_ids"
        )
    result.pop("modality")
    result.pop("final_state_ids")
    result["final_outputs"] = [
        {"state_id": state_id, "modality": modality} for state_id in final_state_ids
    ]
    return result


def _workflow(
    *,
    workflow_id: str,
    source_workflows: tuple[Mapping[str, Any], ...],
    states: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    initial_state_ids: list[str],
    final_outputs: list[dict[str, str]],
) -> dict[str, Any]:
    result: dict[str, Any] = {"workflow_id": workflow_id}
    scopes = [workflow.get("protocol_scope") for workflow in source_workflows]
    if any(scope != scopes[0] for scope in scopes[1:]):
        raise ConnectedProcessMigrationError(
            f"cannot merge {workflow_id}: workflow protocol scopes differ"
        )
    if scopes[0] is not None:
        result["protocol_scope"] = copy.deepcopy(scopes[0])
    result.update(
        {
            "states": states,
            "transitions": transitions,
            "initial_state_ids": initial_state_ids,
            "final_outputs": final_outputs,
        }
    )
    return result


def _mixed_aliquot_state(
    *,
    state_id: str,
    name: str,
    physical_state: str,
    sources: tuple[Mapping[str, Any], Mapping[str, Any]],
    branch_property: str,
) -> dict[str, Any]:
    strands: list[dict[str, Any]] = []
    paired_regions: list[dict[str, Any]] = []
    discontinuities: list[dict[str, Any]] = []
    for source in sources:
        strands.extend(copy.deepcopy(source["strands"]))
        paired_regions.extend(copy.deepcopy(source["paired_regions"]))
        discontinuities.extend(copy.deepcopy(source["discontinuities"]))
    return {
        "state_id": state_id,
        "name": name,
        "molecule_type": "DNA mixed population",
        "strand_architecture": "mixed_population",
        "reference_strand_id": sources[0]["reference_strand_id"],
        "physical_state": physical_state,
        "strands": strands,
        "paired_regions": paired_regions,
        "discontinuities": discontinuities,
        "properties": [
            "contains the carried Ad-2/Ad-2 and Ad-2/Ad-1x molecular classes",
            branch_property,
        ],
        "support_status": "derivable",
    }


def _new_transition(
    *,
    transition_id: str,
    substrate_state_ids: list[str],
    operation: str,
    operation_detail: str,
    major_reagents: list[dict[str, str]],
    product_state_ids: list[str],
    support_status: str,
    oligo_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "transition_id": transition_id,
        "substrate_state_ids": substrate_state_ids,
        "operation": operation,
        "operation_detail": operation_detail,
        "oligo_ids": copy.deepcopy(oligo_ids or []),
        "major_reagents": major_reagents,
        "product_state_ids": product_state_ids,
        "carried_forward_product_ids": copy.deepcopy(product_state_ids),
        "discarded_product_ids": [],
        "support_status": support_status,
    }


def _combine_transition(
    transitions: list[Mapping[str, Any]],
    *,
    substrate_state_ids: list[str],
    product_state_ids: list[str],
    carried_forward_product_ids: list[str],
    discarded_product_ids: list[str],
) -> dict[str, Any]:
    if not transitions:
        raise ConnectedProcessMigrationError("cannot combine zero transitions")
    transition_id = transitions[0].get("transition_id")
    ignored = {
        "substrate_state_ids",
        "product_state_ids",
        "carried_forward_product_ids",
        "discarded_product_ids",
        "oligo_ids",
        "major_reagents",
    }
    expected = {
        key: value for key, value in transitions[0].items() if key not in ignored
    }
    for candidate in transitions[1:]:
        if candidate.get("transition_id") != transition_id:
            raise ConnectedProcessMigrationError(
                f"cannot combine unlike transitions {transition_id!r} and "
                f"{candidate.get('transition_id')!r}"
            )
        comparable = {
            key: value for key, value in candidate.items() if key not in ignored
        }
        if comparable != expected:
            raise ConnectedProcessMigrationError(
                f"transition copies for {transition_id!r} differ outside graph endpoints"
            )
    result = copy.deepcopy(dict(transitions[0]))
    result["substrate_state_ids"] = substrate_state_ids
    result["product_state_ids"] = product_state_ids
    result["carried_forward_product_ids"] = carried_forward_product_ids
    result["discarded_product_ids"] = discarded_product_ids
    result["oligo_ids"] = _unique(
        item for transition in transitions for item in transition.get("oligo_ids", [])
    )
    result["major_reagents"] = _unique_objects(
        item
        for transition in transitions
        for item in transition.get("major_reagents", [])
    )
    return result


def _rewire_transition(
    transition: Mapping[str, Any],
    *,
    substrate_state_ids: list[str],
) -> dict[str, Any]:
    result = copy.deepcopy(dict(transition))
    result["substrate_state_ids"] = substrate_state_ids
    return result


def _clone_state(
    state: Mapping[str, Any],
    *,
    state_id: str,
    name: str,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(state))
    result["state_id"] = state_id
    result["name"] = name
    return result


def _set_state_context(
    state: dict[str, Any],
    *,
    name: str,
    physical_state: str,
    properties: list[str],
) -> None:
    state["name"] = name
    state["physical_state"] = physical_state
    state["properties"] = properties


def _merged_records(
    workflows: tuple[Mapping[str, Any], ...],
    collection: str,
    id_key: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for workflow in workflows:
        for record in workflow[collection]:
            record_id = record[id_key]
            previous = seen.get(record_id)
            if previous is not None:
                if previous != record:
                    raise ConnectedProcessMigrationError(
                        f"duplicate {id_key} {record_id!r} differs across workflows"
                    )
                continue
            copied = copy.deepcopy(record)
            result.append(copied)
            seen[record_id] = copied
    return result


def _workflows_by_id(
    workflows: list[dict[str, Any]], expected_ids: set[str]
) -> dict[str, dict[str, Any]]:
    result = _records_by_id(workflows, "workflow_id", "workflow")
    if set(result) != expected_ids:
        raise ConnectedProcessMigrationError(
            "reviewed workflow IDs changed: "
            f"expected {sorted(expected_ids)}, found {sorted(result)}"
        )
    return result


def _transitions_by_id(workflow: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return _records_by_id(
        workflow["transitions"],
        "transition_id",
        f"workflow {workflow.get('workflow_id')} transition",
    )


def _records_by_id(
    records: Iterable[dict[str, Any]], id_key: str, label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = _required_string(record, id_key, label)
        if record_id in result:
            raise ConnectedProcessMigrationError(f"duplicate {label} {record_id!r}")
        result[record_id] = record
    return result


def _required_record(
    records: Mapping[str, dict[str, Any]], record_id: str, label: str
) -> dict[str, Any]:
    try:
        return records[record_id]
    except KeyError as error:
        raise ConnectedProcessMigrationError(
            f"reviewed {label} {record_id!r} is missing"
        ) from error


def _required_string(record: Mapping[str, Any], key: str, label: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectedProcessMigrationError(f"{label} has invalid {key}")
    return value


def _require_legacy_contract(
    workflows: Iterable[Mapping[str, Any]], protocol_id: str
) -> None:
    for workflow in workflows:
        workflow_id = workflow.get("workflow_id")
        if "modality" not in workflow or "final_state_ids" not in workflow:
            raise ConnectedProcessMigrationError(
                f"{protocol_id} workflow {workflow_id!r} is not in the pinned legacy contract"
            )
        if "final_outputs" in workflow:
            raise ConnectedProcessMigrationError(
                f"{protocol_id} workflow {workflow_id!r} is already migrated"
            )


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _unique_objects(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in values:
        copied = copy.deepcopy(dict(value))
        if copied not in result:
            result.append(copied)
    return result
