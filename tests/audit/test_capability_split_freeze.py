from __future__ import annotations

from pathlib import Path

import pytest

from libstruct_bench.audit.artifacts import write_json_atomic
from libstruct_bench.improvement.artifacts import (
    CapabilityImprovementError,
)
from libstruct_bench.improvement.split_design import (
    CUMULATIVE_CHECKPOINT_LABELS,
    EXPECTED_FINAL_TEST_TRIAL_COUNT,
    EXPECTED_VALIDATION_TRIAL_COUNT,
    FINAL_DEVELOPMENT_BATCHES,
    FINAL_REBALANCE_POLICY,
    FINAL_REBALANCE_REPLACEMENTS,
    FINAL_TRANSFER_ANNOTATIONS,
    FINAL_TRANSFER_PANEL,
    FINAL_TRANSFER_PURPOSE,
    FINAL_TRANSFER_STRATA,
    FIXED_VALIDATION_PANEL,
    SUPERSEDED_DEVELOPMENT_BATCHES,
    SUPERSEDED_REBALANCE_POLICY,
    SUPERSEDED_REBALANCE_REPLACEMENTS,
    SUPERSEDED_TRANSFER_ANNOTATIONS,
    validate_final_split_design,
)
from libstruct_bench.improvement.split_freeze import (
    _build_superseded_frozen_split,
    _final_panel_commitment_digest,
    _recover_split_transaction,
    _split_freeze_lock,
    _transfer_panel_commitment,
    _write_transaction_journal,
    build_frozen_split,
    validate_frozen_split,
    validate_transfer_panel_commitment,
)


def test_final_split_has_exact_batches_panel_and_strata() -> None:
    validate_final_split_design()

    assert [list(batch["protocol_ids"]) for batch in FINAL_DEVELOPMENT_BATCHES] == [
        [
            "s3_atac",
            "10x_chromium_3_gene_expression_v4",
            "drop_seq",
            "split_seq",
            "sci_rna_seq",
        ],
        [
            "10x_chromium_3_feature_barcoding",
            "10x_chromium_single_cell_atac_v2",
            "seq_well_s3",
            "indrop_v1",
            "cel_seq",
        ],
        [
            "microwell_seq",
            "pip_seq_v4",
            "scdamid",
            "dr_seq",
            "petri_seq",
        ],
        [
            "crispr_sciatac",
            "lianti",
            "strt_seq",
            "smart_seq2",
            "plate_scatac_seq",
        ],
        [
            "malbac",
            "scifi_atac_seq",
            "strt_seq_2i",
            "strt_seq_c1",
            "tang_2009",
        ],
    ]
    assert FIXED_VALIDATION_PANEL == (
        "sci_atac_seq",
        "scrrbs",
        "smart_seq",
        "share_seq",
        "ddseq_single_cell_3_rna_seq_kit",
    )
    assert CUMULATIVE_CHECKPOINT_LABELS == (
        "C0",
        "C5",
        "C10",
        "C15",
        "C20",
        "C25",
    )
    assert EXPECTED_VALIDATION_TRIAL_COUNT == 30
    assert EXPECTED_FINAL_TEST_TRIAL_COUNT == 60
    assert FINAL_TRANSFER_PANEL == (
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
    assert FINAL_TRANSFER_STRATA == {
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
    assert FINAL_TRANSFER_PURPOSE == (
        "transfer_within_related_protocol_or_platform_families",
        "transfer_to_distinct_molecular_architectures",
    )
    assert tuple(item["protocol_id"] for item in FINAL_TRANSFER_ANNOTATIONS) == (
        FINAL_TRANSFER_PANEL
    )
    assert all(item["rationale"] for item in FINAL_TRANSFER_ANNOTATIONS)


def test_final_rebalance_map_reconstructs_batches_without_source_or_score_input() -> (
    None
):
    assert FINAL_REBALANCE_POLICY == (
        "explicit_score_blind_reclassification_of_the_superseded_30_protocol_"
        "development_set_into_25_training_and_5_fixed_validation_protocols"
    )


def test_final_transfer_annotations_are_persisted_and_hash_bound(
    tmp_path: Path,
) -> None:
    commitment = _transfer_panel_commitment(_final_panel_commitment_digest())
    assert commitment["protocol_ids"] == list(FINAL_TRANSFER_PANEL)
    assert commitment["transfer_annotations"] == [
        dict(item) for item in FINAL_TRANSFER_ANNOTATIONS
    ]
    commitment_path = tmp_path / "transfer_panel_commitment.json"
    write_json_atomic(commitment_path, commitment)
    assert validate_transfer_panel_commitment(commitment_path) == commitment

    split = build_frozen_split(
        batches=FINAL_DEVELOPMENT_BATCHES,
        validation_protocol_ids=FIXED_VALIDATION_PANEL,
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        recorded_at="2026-08-22T12:00:00Z",
        status="active",
        transfer_strata=FINAL_TRANSFER_STRATA,
        transfer_annotations=FINAL_TRANSFER_ANNOTATIONS,
        purposes=FINAL_TRANSFER_PURPOSE,
        rebalance_policy=FINAL_REBALANCE_POLICY,
        split_id="annotation-fixture",
    )
    split_path = tmp_path / "frozen_split.json"
    write_json_atomic(split_path, split)
    validated = validate_frozen_split(split_path)
    assert validated["validation_panel"]["protocol_ids"] == list(FIXED_VALIDATION_PANEL)
    assert validated["final_test_panel"]["transfer_annotations"] == [
        dict(item) for item in FINAL_TRANSFER_ANNOTATIONS
    ]
    assert FINAL_REBALANCE_REPLACEMENTS == ()
    assert SUPERSEDED_REBALANCE_REPLACEMENTS == (
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


def test_active_split_requires_validation_and_preserves_superseded_digest(
    tmp_path: Path,
) -> None:
    with pytest.raises(CapabilityImprovementError, match="validation panel"):
        build_frozen_split(
            batches=FINAL_DEVELOPMENT_BATCHES,
            panel_protocol_ids=FINAL_TRANSFER_PANEL,
            recorded_at="2026-08-22T12:00:00Z",
            status="active",
            transfer_strata=FINAL_TRANSFER_STRATA,
            transfer_annotations=FINAL_TRANSFER_ANNOTATIONS,
            purposes=FINAL_TRANSFER_PURPOSE,
            rebalance_policy=FINAL_REBALANCE_POLICY,
            split_id="missing-validation",
        )

    predecessor = _build_superseded_frozen_split(
        batches=SUPERSEDED_DEVELOPMENT_BATCHES,
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        recorded_at="2026-08-22T12:00:00Z",
        transfer_strata=FINAL_TRANSFER_STRATA,
        transfer_annotations=SUPERSEDED_TRANSFER_ANNOTATIONS,
        purposes=FINAL_TRANSFER_PURPOSE,
        rebalance_policy=SUPERSEDED_REBALANCE_POLICY,
        split_id="superseded-30-10",
    )
    assert predecessor["split_digest"] == (
        "2c7bd9b618e4a3cb6bb2b4e0dd51dd8f17d7e2eac36174450aa9e104e9cc2b43"
    )

    active = build_frozen_split(
        batches=FINAL_DEVELOPMENT_BATCHES,
        validation_protocol_ids=FIXED_VALIDATION_PANEL,
        panel_protocol_ids=FINAL_TRANSFER_PANEL,
        recorded_at="2026-08-22T12:00:00Z",
        status="active",
        transfer_strata=FINAL_TRANSFER_STRATA,
        transfer_annotations=FINAL_TRANSFER_ANNOTATIONS,
        purposes=FINAL_TRANSFER_PURPOSE,
        rebalance_policy=FINAL_REBALANCE_POLICY,
        split_id="active-25-5-10",
    )
    assert active["split_digest"] == (
        "56ea0564de2283c7f74fafeefb59be54e192938a16cf0c9754cdc787de379538"
    )
    path = tmp_path / "active.json"
    write_json_atomic(path, active)
    assert validate_frozen_split(path) == active


def test_final_split_keeps_learning_and_test_protocols_disjoint() -> None:
    development = {
        protocol_id
        for batch in FINAL_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    }

    assert len(development) == 25
    assert len(FIXED_VALIDATION_PANEL) == 5
    assert len(FINAL_TRANSFER_PANEL) == 10
    assert development.isdisjoint(FINAL_TRANSFER_PANEL)
    assert development.isdisjoint(FIXED_VALIDATION_PANEL)
    assert set(FIXED_VALIDATION_PANEL).isdisjoint(FINAL_TRANSFER_PANEL)
    assert {"malbac", "lianti"} <= development
    assert {
        protocol_id
        for batch in SUPERSEDED_DEVELOPMENT_BATCHES
        for protocol_id in batch["protocol_ids"]
    } == development | set(FIXED_VALIDATION_PANEL)
    assert set(FINAL_TRANSFER_PANEL) == {
        protocol_id
        for protocols in FINAL_TRANSFER_STRATA.values()
        for protocol_id in protocols
    }


def test_split_freeze_lock_is_reentrant_for_restaging(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    root.mkdir()

    with _split_freeze_lock(root):
        with _split_freeze_lock(root):
            pass


def test_prepared_split_journal_recovers_prior_tree(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    (root / "design").mkdir(parents=True)
    stage = tmp_path / ".v1.split-freeze-fixture"
    stage.mkdir()
    journal = tmp_path / ".v1.split-freeze-journal.json"
    _write_transaction_journal(
        journal,
        root=root,
        archive_relative=Path("history/superseded/test-split-fixture"),
        stage=stage,
        prior_experiment_digest="1" * 64,
        new_experiment_digest="2" * 64,
        phase="prepared",
    )

    assert _recover_split_transaction(root) is None
    assert (root / "design").is_dir()
    assert not stage.exists()
    assert not journal.exists()
