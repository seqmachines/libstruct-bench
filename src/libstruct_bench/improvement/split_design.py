from __future__ import annotations

from typing import Any


# The experiment began with this pre-A5 development/panel partition.  It is
# retained only so a fresh reconstruction of the same v1 lineage cannot expose
# the post-A10 final panel early.  The authorized split-freeze transaction is
# the sole transition from these initial values to the FINAL_* values below.
INITIAL_DEVELOPMENT_BATCHES: tuple[dict[str, Any], ...] = (
    {
        "batch_id": "B1",
        "phase": "retrospective",
        "checkpoint_size": 5,
        "protocol_ids": (
            "s3_atac",
            "10x_chromium_3_gene_expression_v4",
            "drop_seq",
            "split_seq",
            "sci_rna_seq",
        ),
    },
    {
        "batch_id": "B2",
        "phase": "retrospective",
        "checkpoint_size": 10,
        "protocol_ids": (
            "sci_atac_seq",
            "scrrbs",
            "smart_seq",
            "share_seq",
            "ddseq_single_cell_3_rna_seq_kit",
        ),
    },
    {
        "batch_id": "B3",
        "phase": "prospective",
        "checkpoint_size": 15,
        "protocol_ids": (
            "cel_seq2",
            "crispr_sciatac",
            "lianti",
            "snare_seq",
            "strt_seq",
        ),
    },
    {
        "batch_id": "B4",
        "phase": "prospective",
        "checkpoint_size": 20,
        "protocol_ids": (
            "indrop_v2",
            "plate_scatac_seq",
            "scrb_seq",
            "smart_seq2",
            "strt_seq_2i",
        ),
    },
    {
        "batch_id": "B5",
        "phase": "prospective",
        "checkpoint_size": 25,
        "protocol_ids": (
            "ddseq_scatac_seq",
            "malbac",
            "paired_seq",
            "pi_atac_seq",
            "scdnase_seq",
        ),
    },
    {
        "batch_id": "B6",
        "phase": "prospective",
        "checkpoint_size": 30,
        "protocol_ids": (
            "scifi_atac_seq",
            "smart_seq3xpress",
            "spear_atac",
            "strt_seq_c1",
            "tang_2009",
        ),
    },
)

INITIAL_TRANSFER_PANEL: tuple[str, ...] = (
    "10x_chromium_single_cell_atac_v2",
    "10x_chromium_3_feature_barcoding",
    "seq_well_s3",
    "indrop_v1",
    "cel_seq",
    "microwell_seq",
    "pip_seq_v4",
    "scdamid",
    "dr_seq",
    "petri_seq",
)


# This was the active 30/10 split before the single-branch 25/5/10 design was
# approved.  It remains a first-class constant so the migration can validate
# and archive the exact predecessor rather than interpreting it with the new
# active rules.
SUPERSEDED_DEVELOPMENT_BATCHES: tuple[dict[str, Any], ...] = (
    {
        "batch_id": "B1",
        "phase": "retrospective",
        "checkpoint_size": 5,
        "protocol_ids": (
            "s3_atac",
            "10x_chromium_3_gene_expression_v4",
            "drop_seq",
            "split_seq",
            "sci_rna_seq",
        ),
    },
    {
        "batch_id": "B2",
        "phase": "retrospective",
        "checkpoint_size": 10,
        "protocol_ids": (
            "sci_atac_seq",
            "scrrbs",
            "smart_seq",
            "share_seq",
            "ddseq_single_cell_3_rna_seq_kit",
        ),
    },
    {
        "batch_id": "B3",
        "phase": "prospective",
        "checkpoint_size": 15,
        "protocol_ids": (
            "10x_chromium_3_feature_barcoding",
            "10x_chromium_single_cell_atac_v2",
            "crispr_sciatac",
            "lianti",
            "strt_seq",
        ),
    },
    {
        "batch_id": "B4",
        "phase": "prospective",
        "checkpoint_size": 20,
        "protocol_ids": (
            "indrop_v1",
            "plate_scatac_seq",
            "seq_well_s3",
            "smart_seq2",
            "strt_seq_2i",
        ),
    },
    {
        "batch_id": "B5",
        "phase": "prospective",
        "checkpoint_size": 25,
        "protocol_ids": (
            "cel_seq",
            "malbac",
            "microwell_seq",
            "pip_seq_v4",
            "scdamid",
        ),
    },
    {
        "batch_id": "B6",
        "phase": "prospective",
        "checkpoint_size": 30,
        "protocol_ids": (
            "dr_seq",
            "petri_seq",
            "scifi_atac_seq",
            "strt_seq_c1",
            "tang_2009",
        ),
    },
)


# The active single-branch training sequence.  Validation protocols are not
# development batches: their aggregate scores may guide the next update, but
# their sources, ground truth, solved records, exact sequences, and
# error-specific answers are outside cumulative-learning state.
FINAL_DEVELOPMENT_BATCHES: tuple[dict[str, Any], ...] = (
    {
        "batch_id": "B1",
        "phase": "retrospective",
        "checkpoint_size": 5,
        "protocol_ids": (
            "s3_atac",
            "10x_chromium_3_gene_expression_v4",
            "drop_seq",
            "split_seq",
            "sci_rna_seq",
        ),
    },
    {
        "batch_id": "B2",
        "phase": "retrospective",
        "checkpoint_size": 10,
        "protocol_ids": (
            "10x_chromium_3_feature_barcoding",
            "10x_chromium_single_cell_atac_v2",
            "seq_well_s3",
            "indrop_v1",
            "cel_seq",
        ),
    },
    {
        "batch_id": "B3",
        "phase": "retrospective",
        "checkpoint_size": 15,
        "protocol_ids": (
            "microwell_seq",
            "pip_seq_v4",
            "scdamid",
            "dr_seq",
            "petri_seq",
        ),
    },
    {
        "batch_id": "B4",
        "phase": "prospective",
        "checkpoint_size": 20,
        "protocol_ids": (
            "crispr_sciatac",
            "lianti",
            "strt_seq",
            "smart_seq2",
            "plate_scatac_seq",
        ),
    },
    {
        "batch_id": "B5",
        "phase": "prospective",
        "checkpoint_size": 25,
        "protocol_ids": (
            "malbac",
            "scifi_atac_seq",
            "strt_seq_2i",
            "strt_seq_c1",
            "tang_2009",
        ),
    },
)

FIXED_VALIDATION_PANEL: tuple[str, ...] = (
    "sci_atac_seq",
    "scrrbs",
    "smart_seq",
    "share_seq",
    "ddseq_single_cell_3_rna_seq_kit",
)

CUMULATIVE_CHECKPOINT_LABELS: tuple[str, ...] = (
    "C0",
    "C5",
    "C10",
    "C15",
    "C20",
    "C25",
)

EXPECTED_VALIDATION_TRIAL_COUNT = 30
EXPECTED_FINAL_TEST_TRIAL_COUNT = 60

FINAL_TRANSFER_PANEL: tuple[str, ...] = (
    "cel_seq2",
    "indrop_v2",
    "smart_seq3xpress",
    "ddseq_scatac_seq",
    "snare_seq",
    "scrb_seq",
    "paired_seq",
    "pi_atac_seq",
    "scdnase_seq",
    "spear_atac",
)

FINAL_TRANSFER_STRATA: dict[str, tuple[str, ...]] = {
    "direct_related_family": (
        "cel_seq2",
        "indrop_v2",
        "smart_seq3xpress",
    ),
    "related_platform_or_mechanism": (
        "ddseq_scatac_seq",
        "snare_seq",
    ),
    "broader_architectural_transfer": (
        "scrb_seq",
        "paired_seq",
        "pi_atac_seq",
        "scdnase_seq",
        "spear_atac",
    ),
}

# These annotations are part of the frozen panel design, not conclusions drawn
# from test ground truth or scores.  They preserve the predeclared transfer
# relationship for each protocol so later reports cannot flatten the panel into
# an inaccurate all-"novel family" characterization.
FINAL_TRANSFER_ANNOTATIONS: tuple[dict[str, str], ...] = (
    {
        "protocol_id": "cel_seq2",
        "stratum": "direct_related_family",
        "rationale": (
            "CEL-seq is represented in development; this evaluates direct "
            "family transfer to CEL-seq2."
        ),
    },
    {
        "protocol_id": "indrop_v2",
        "stratum": "direct_related_family",
        "rationale": (
            "inDrop v1 is represented in development; this evaluates direct "
            "version-family transfer to inDrop v2."
        ),
    },
    {
        "protocol_id": "smart_seq3xpress",
        "stratum": "direct_related_family",
        "rationale": (
            "The SMART-seq family is represented in development; this evaluates "
            "transfer within that family."
        ),
    },
    {
        "protocol_id": "ddseq_scatac_seq",
        "stratum": "related_platform_or_mechanism",
        "rationale": (
            "A ddSEQ RNA workflow is held in the fixed validation panel, not "
            "training; this evaluates final transfer across a related platform."
        ),
    },
    {
        "protocol_id": "snare_seq",
        "stratum": "related_platform_or_mechanism",
        "rationale": (
            "The fixed validation panel includes the related multimodal "
            "RNA/chromatin mechanism SHARE-seq, but validation is excluded from "
            "training memory; SNARE-seq remains a related mechanism rather than "
            "a direct version."
        ),
    },
    {
        "protocol_id": "scrb_seq",
        "stratum": "broader_architectural_transfer",
        "rationale": (
            "SCRB-seq has no predeclared direct-family or related-platform "
            "counterpart in development; it is reserved for broader "
            "architectural transfer."
        ),
    },
    {
        "protocol_id": "paired_seq",
        "stratum": "broader_architectural_transfer",
        "rationale": (
            "PAIRED-seq has no predeclared direct-family or related-platform "
            "counterpart in development; it is reserved for broader "
            "architectural transfer."
        ),
    },
    {
        "protocol_id": "pi_atac_seq",
        "stratum": "broader_architectural_transfer",
        "rationale": (
            "Pi-ATAC-seq has no predeclared direct-family or related-platform "
            "counterpart in development; it is reserved for broader "
            "architectural transfer."
        ),
    },
    {
        "protocol_id": "scdnase_seq",
        "stratum": "broader_architectural_transfer",
        "rationale": (
            "scDNase-seq has no predeclared direct-family or related-platform "
            "counterpart in development; it is reserved for broader "
            "architectural transfer."
        ),
    },
    {
        "protocol_id": "spear_atac",
        "stratum": "broader_architectural_transfer",
        "rationale": (
            "SPEAR-ATAC has no predeclared direct-family or related-platform "
            "counterpart in development; it is reserved for broader "
            "architectural transfer."
        ),
    },
)

# Exact predecessor annotations are retained to validate the archived 30/10
# split byte-for-byte and semantically.  Only the two rationales whose role
# changed from development to validation differ in the active design.
_SUPERSEDED_ANNOTATION_RATIONALES = {
    "ddseq_scatac_seq": (
        "A ddSEQ RNA workflow is represented in development; this evaluates "
        "transfer across a related platform."
    ),
    "snare_seq": (
        "Development includes related multimodal RNA/chromatin mechanisms such "
        "as SHARE-seq; SNARE-seq is related mechanistically but is not treated "
        "as a direct version."
    ),
}
SUPERSEDED_TRANSFER_ANNOTATIONS: tuple[dict[str, str], ...] = tuple(
    {
        **item,
        "rationale": _SUPERSEDED_ANNOTATION_RATIONALES.get(
            item["protocol_id"], item["rationale"]
        ),
    }
    for item in FINAL_TRANSFER_ANNOTATIONS
)

FINAL_TRANSFER_PURPOSE: tuple[str, ...] = (
    "transfer_within_related_protocol_or_platform_families",
    "transfer_to_distinct_molecular_architectures",
)

SUPERSEDED_REBALANCE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("cel_seq2", "10x_chromium_3_feature_barcoding"),
    ("snare_seq", "10x_chromium_single_cell_atac_v2"),
    ("indrop_v2", "indrop_v1"),
    ("scrb_seq", "seq_well_s3"),
    ("ddseq_scatac_seq", "cel_seq"),
    ("paired_seq", "microwell_seq"),
    ("pi_atac_seq", "pip_seq_v4"),
    ("scdnase_seq", "scdamid"),
    ("smart_seq3xpress", "dr_seq"),
    ("spear_atac", "petri_seq"),
)

SUPERSEDED_REBALANCE_POLICY = (
    "source_and_score_blind_explicit_per_slot_replacement_map_then_sort_each_"
    "B3_B6_batch_lexicographically"
)

# No protocol enters or leaves the frozen final-test panel in this revision.
# The predecessor's second development batch becomes the fixed validation
# panel, and its other 25 development protocols are placed in the five exact
# user-approved training batches above.
FINAL_REBALANCE_REPLACEMENTS: tuple[tuple[str, str], ...] = ()
FINAL_REBALANCE_POLICY = (
    "explicit_score_blind_reclassification_of_the_superseded_30_protocol_"
    "development_set_into_25_training_and_5_fixed_validation_protocols"
)


def validate_final_split_design() -> None:
    development = [
        protocol_id
        for batch in FINAL_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    ]
    if len(FINAL_DEVELOPMENT_BATCHES) != 5:
        raise ValueError("the final experiment split must contain five batches")
    if any(len(batch["protocol_ids"]) != 5 for batch in FINAL_DEVELOPMENT_BATCHES):
        raise ValueError("each final development batch must contain five protocols")
    if len(development) != 25 or len(set(development)) != 25:
        raise ValueError("the final training split must contain 25 unique protocols")
    expected_batch_metadata = tuple(
        (
            f"B{index}",
            "retrospective" if index <= 3 else "prospective",
            index * 5,
        )
        for index in range(1, 6)
    )
    actual_batch_metadata = tuple(
        (batch["batch_id"], batch["phase"], batch["checkpoint_size"])
        for batch in FINAL_DEVELOPMENT_BATCHES
    )
    if actual_batch_metadata != expected_batch_metadata:
        raise ValueError("the five training batches have invalid chronology")
    if len(FIXED_VALIDATION_PANEL) != 5 or len(set(FIXED_VALIDATION_PANEL)) != 5:
        raise ValueError("the validation panel must contain five unique protocols")
    if len(FINAL_TRANSFER_PANEL) != 10 or len(set(FINAL_TRANSFER_PANEL)) != 10:
        raise ValueError("the final test panel must contain ten unique protocols")
    if (
        set(development) & set(FIXED_VALIDATION_PANEL)
        or set(development) & set(FINAL_TRANSFER_PANEL)
        or set(FIXED_VALIDATION_PANEL) & set(FINAL_TRANSFER_PANEL)
    ):
        raise ValueError(
            "training, validation, and final-test protocols must be disjoint"
        )
    stratified = [
        protocol_id
        for values in FINAL_TRANSFER_STRATA.values()
        for protocol_id in values
    ]
    if len(stratified) != 10 or set(stratified) != set(FINAL_TRANSFER_PANEL):
        raise ValueError("the transfer strata must partition the final test panel")
    annotated = [item["protocol_id"] for item in FINAL_TRANSFER_ANNOTATIONS]
    if tuple(annotated) != FINAL_TRANSFER_PANEL:
        raise ValueError(
            "transfer annotations must cover the final test panel exactly in order"
        )
    expected_strata = {
        protocol_id: name
        for name, protocol_ids in FINAL_TRANSFER_STRATA.items()
        for protocol_id in protocol_ids
    }
    if any(
        item["stratum"] != expected_strata[item["protocol_id"]]
        or not item["rationale"].strip()
        for item in FINAL_TRANSFER_ANNOTATIONS
    ):
        raise ValueError("transfer annotations must match the frozen strata")

    superseded_development = [
        protocol_id
        for batch in SUPERSEDED_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    ]
    if len(superseded_development) != 30 or len(set(superseded_development)) != 30:
        raise ValueError("the superseded split must contain 30 development protocols")
    if set(superseded_development) != set(development) | set(FIXED_VALIDATION_PANEL):
        raise ValueError(
            "training and validation must exactly partition the superseded development set"
        )
    if tuple(SUPERSEDED_DEVELOPMENT_BATCHES[1]["protocol_ids"]) != (
        FIXED_VALIDATION_PANEL
    ):
        raise ValueError("the fixed validation panel must be the superseded B2")
    if set(superseded_development) | set(FINAL_TRANSFER_PANEL) != (
        set(development) | set(FIXED_VALIDATION_PANEL) | set(FINAL_TRANSFER_PANEL)
    ):
        raise ValueError("the 25/5/10 revision must preserve the 40-protocol universe")
    if CUMULATIVE_CHECKPOINT_LABELS != ("C0", "C5", "C10", "C15", "C20", "C25"):
        raise ValueError("the cumulative checkpoint labels are not canonical")
    if EXPECTED_VALIDATION_TRIAL_COUNT != 30 or EXPECTED_FINAL_TEST_TRIAL_COUNT != 60:
        raise ValueError("the fixed-panel trial counts are not canonical")


def validate_superseded_split_design() -> None:
    """Retain deterministic validation for the immutable pre-revision split."""

    initial_development = [
        protocol_id
        for batch in INITIAL_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    ]
    superseded_development = [
        protocol_id
        for batch in SUPERSEDED_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    ]
    initial_universe = set(initial_development) | set(INITIAL_TRANSFER_PANEL)
    superseded_universe = set(superseded_development) | set(FINAL_TRANSFER_PANEL)
    if (
        len(initial_development) != 30
        or len(set(initial_development)) != 30
        or len(INITIAL_TRANSFER_PANEL) != 10
        or len(set(INITIAL_TRANSFER_PANEL)) != 10
        or set(initial_development) & set(INITIAL_TRANSFER_PANEL)
        or initial_universe != superseded_universe
    ):
        raise ValueError(
            "the initial and final splits must partition the same 40 protocols"
        )
    if INITIAL_DEVELOPMENT_BATCHES[:2] != SUPERSEDED_DEVELOPMENT_BATCHES[:2]:
        raise ValueError("B1 and B2 must be unchanged by the final split freeze")
    replacement_map = dict(SUPERSEDED_REBALANCE_REPLACEMENTS)
    if len(replacement_map) != len(SUPERSEDED_REBALANCE_REPLACEMENTS):
        raise ValueError("final rebalance replacement sources must be unique")
    replacement_targets = tuple(replacement_map.values())
    if len(set(replacement_targets)) != len(replacement_targets):
        raise ValueError("final rebalance replacement targets must be unique")
    expected_sources = set(initial_development) & set(FINAL_TRANSFER_PANEL)
    expected_targets = set(INITIAL_TRANSFER_PANEL) & set(superseded_development)
    if set(replacement_map) != expected_sources or set(replacement_targets) != (
        expected_targets
    ):
        raise ValueError(
            "the final rebalance map must exchange exactly the protocols that "
            "change development/test roles"
        )
    reconstructed = []
    for batch in INITIAL_DEVELOPMENT_BATCHES:
        replaced = tuple(
            replacement_map.get(protocol_id, protocol_id)
            for protocol_id in batch["protocol_ids"]
        )
        protocol_ids = (
            tuple(sorted(replaced)) if batch["batch_id"] >= "B3" else replaced
        )
        reconstructed.append({**batch, "protocol_ids": protocol_ids})
    if tuple(reconstructed) != SUPERSEDED_DEVELOPMENT_BATCHES:
        raise ValueError(
            "the explicit source- and score-blind replacement map does not "
            "reconstruct the final batches"
        )


validate_final_split_design()
validate_superseded_split_design()
