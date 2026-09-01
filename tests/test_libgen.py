from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from libstruct_bench.cli.grade_libgen import main as grade_main
from libstruct_bench.cli.rescore_libgen_runs import main as rescore_main
from libstruct_bench.libgen.scoring import (
    LIBGEN_PUBLIC_METRIC_KEYS,
    _pairing_and_discontinuity_similarity,
    _state_assignment_similarity,
    _state_sequence_similarity,
    _transition_assignment_similarity,
    grade_libgen,
    score_t2,
)
from libstruct_bench.libgen.version import LIBGEN_BENCHMARK_VERSION
from libstruct_bench.libgen.validation import (
    LibgenValidationError,
    derive_required_t2_ids,
    validate_groundtruth_bundle,
    validate_prediction_links,
    validate_t2_prediction,
    validate_t3_prediction,
)
from libstruct_bench.matching import best_partial_one_to_one_matching
from tests.libgen_fixtures import (
    renamed_predictions,
    t1_groundtruth,
    t2_groundtruth,
    t2_prediction,
    t3_groundtruth,
    t3_prediction,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"


def test_prediction_schemas_are_unversioned_and_exclude_audit_fields() -> None:
    for filename in (
        "oligo_prediction.schema.json",
        "library_generation_workflow_prediction.schema.json",
    ):
        schema = json.loads((SCHEMA_ROOT / "benchmark" / filename).read_text())
        Draft202012Validator.check_schema(schema)
        assert "schema_version" not in schema.get("properties", {})
        assert "support_status" not in schema["$defs"]

    invalid = t2_prediction()
    invalid["schema_version"] = "not-allowed"
    with pytest.raises(LibgenValidationError):
        validate_t2_prediction(
            invalid, protocol_id="example_protocol", schema_root=SCHEMA_ROOT
        )

    t3_schema = json.loads(
        (
            SCHEMA_ROOT
            / "benchmark"
            / "library_generation_workflow_prediction.schema.json"
        ).read_text()
    )
    assert "modality" not in t3_schema["properties"]
    assert "modality" not in t3_schema["$defs"]["workflow"]["properties"]
    assert "final_outputs" in t3_schema["$defs"]["workflow"]["required"]
    assert "modality" in t3_schema["$defs"]["final_output"]["required"]


def test_exact_truth_projection_scores_one() -> None:
    metrics, details = grade_libgen(
        t2_prediction(), t3_prediction(), t2_groundtruth(), t3_groundtruth()
    )
    assert tuple(metrics) == LIBGEN_PUBLIC_METRIC_KEYS
    assert metrics["reward"] == pytest.approx(1.0)
    assert all(value == pytest.approx(1.0) for value in metrics.values())
    assert details["diagnostic_metrics"]["t2"][
        "required_family_precision"
    ] == pytest.approx(1.0)
    assert details["diagnostic_metrics"]["t3"][
        "molecular_transition_recall"
    ] == pytest.approx(1.0)
    assert details["benchmark_version"] == LIBGEN_BENCHMARK_VERSION


def test_ids_and_collection_order_do_not_affect_score() -> None:
    t2, t3 = renamed_predictions()
    validate_t2_prediction(t2, protocol_id="example_protocol", schema_root=SCHEMA_ROOT)
    validate_t3_prediction(t3, protocol_id="example_protocol", schema_root=SCHEMA_ROOT)
    validate_prediction_links(t2, t3)
    metrics, _ = grade_libgen(t2, t3, t2_groundtruth(), t3_groundtruth())
    assert metrics["reward"] == pytest.approx(1.0)


def test_state_payload_aliases_and_metadata_prose_do_not_reduce_score() -> None:
    truth = t3_groundtruth()
    prediction = t3_prediction()
    truth_state = truth["workflows"][0]["states"][0]
    predicted_state = prediction["workflows"][0]["states"][0]
    for state, architecture in (
        (truth_state, "[GDNA]"),
        (predicted_state, "[GENOMIC_DNA]"),
    ):
        state["molecule_type"] = "DNA"
        state["strands"][0]["molecule_type"] = "DNA"
        state["strands"][0]["sequence_architecture"] = architecture
        state["strands"][0]["segments"][0]["placeholder"] = architecture
    truth_state["physical_state"] = "double-stranded genomic DNA in nuclei"
    truth_state["properties"] = ["input for transposition"]
    predicted_state["physical_state"] = "native chromatin"
    predicted_state["properties"] = ["accessible loci are preferred"]
    predicted_state["strands"][0]["segments"][0]["role"] = (
        "reverse-complementary genomic DNA"
    )

    metrics, details = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )

    assert metrics["t3_state_f1"] == pytest.approx(1.0)
    state_details = details["t3"]["workflows"]["workflow_main"]["state_matches"]
    matched_input = next(
        item for item in state_details if item["groundtruth_state_id"] == "state_input"
    )
    assert matched_input["dimension_scores"] == {
        "reference_strand": pytest.approx(1.0),
        "architecture": pytest.approx(1.0),
        "segments": pytest.approx(1.0),
        "pairing": pytest.approx(1.0),
    }
    assert matched_input["metadata_diagnostics"]["properties_f1"] == 0.0
    assert details["t3"]["weights"]["state"] == {
        "reference_strand": 0.50,
        "architecture": 0.15,
        "segments": 0.20,
        "pairing": 0.15,
    }

    predicted_state["strands"][0]["sequence_architecture"] = "[CDNA]"
    predicted_state["strands"][0]["segments"][0]["placeholder"] = "[CDNA]"
    mismatch_metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )
    assert mismatch_metrics["t3_state_f1"] < 1.0


def test_state_scoring_uses_consistent_segment_projection_over_layout_prose() -> None:
    prediction = t3_prediction()
    predicted_state = prediction["workflows"][0]["states"][1]
    predicted_state["physical_state"] = "different but compatible prose"
    predicted_state["properties"] = ["another source-compatible description"]
    predicted_state["strands"][0]["sequence_architecture"] += (
        " ...descriptive gap text..."
    )
    predicted_state["strands"][0]["segments"][0]["role"] = "different role prose"

    metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), t3_groundtruth()
    )

    assert metrics["t3_state_f1"] == pytest.approx(1.0)


def test_truth_variable_state_region_accepts_specific_same_length_payload() -> None:
    truth = t3_groundtruth()
    prediction = t3_prediction()
    truth_state = truth["workflows"][0]["states"][0]
    predicted_state = prediction["workflows"][0]["states"][0]
    for state, architecture in (
        (truth_state, "[VARIABLE:9]"),
        (predicted_state, "[GENOMIC_DNA:9]"),
    ):
        state["molecule_type"] = "DNA"
        state["strands"][0]["molecule_type"] = "DNA"
        state["strands"][0]["sequence_architecture"] = architecture
        state["strands"][0]["segments"][0]["placeholder"] = architecture

    metrics, _ = grade_libgen(t2_prediction(), prediction, t2_groundtruth(), truth)
    assert metrics["t3_state_f1"] == pytest.approx(1.0)

    truth_state["strands"][0]["sequence_architecture"] = "[UMI:9]"
    truth_state["strands"][0]["segments"][0]["placeholder"] = "[UMI:9]"
    predicted_state["strands"][0]["sequence_architecture"] = "[VARIABLE:9]"
    predicted_state["strands"][0]["segments"][0]["placeholder"] = "[VARIABLE:9]"
    underspecified_metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )
    assert underspecified_metrics["t3_state_f1"] < 1.0


def test_state_anchor_placeholder_accepts_only_two_base_iupac_shorthand() -> None:
    anchor = ["<ANCHOR>", "<ANCHOR>"]

    assert _state_sequence_similarity(["V", "N"], anchor) == pytest.approx(1.0)
    assert _state_sequence_similarity(["N", "B"], anchor) == pytest.approx(1.0)
    assert _state_sequence_similarity(["A", "A"], anchor) < 1.0
    assert _state_sequence_similarity(["N", "N"], anchor) < 1.0
    assert (
        _state_sequence_similarity(["V", "N"], ["<SAMPLE_INDEX>", "<SAMPLE_INDEX>"])
        < 1.0
    )
    assert _state_sequence_similarity(anchor, ["V", "N"]) < 1.0


def test_continuous_duplex_pairing_is_invariant_to_region_partition() -> None:
    def duplex_state(*, split: bool, omit_second_top_segment: bool = False) -> dict:
        regions = [
            {
                "paired_region_id": "pair_all",
                "relationship": "reverse_complementary",
                "side_1": {"strand_id": "top", "segment_ids": ["top_a", "top_b"]},
                "side_2": {
                    "strand_id": "bottom",
                    "segment_ids": ["bottom_b", "bottom_a"],
                },
            }
        ]
        if split:
            regions = [
                {
                    "paired_region_id": "pair_a",
                    "relationship": "reverse_complementary",
                    "side_1": {"strand_id": "top", "segment_ids": ["top_a"]},
                    "side_2": {"strand_id": "bottom", "segment_ids": ["bottom_a"]},
                },
                {
                    "paired_region_id": "pair_b",
                    "relationship": "reverse_complementary",
                    "side_1": {
                        "strand_id": "top",
                        "segment_ids": [] if omit_second_top_segment else ["top_b"],
                    },
                    "side_2": {"strand_id": "bottom", "segment_ids": ["bottom_b"]},
                },
            ]
        return {
            "strands": [
                {
                    "strand_id": "top",
                    "molecule_type": "DNA",
                    "segments": [
                        {
                            "segment_id": "top_a",
                            "structural_role": "paired_region",
                            "sequence": "AAAA",
                        },
                        {
                            "segment_id": "top_b",
                            "structural_role": "paired_region",
                            "sequence": "CCCC",
                        },
                    ],
                },
                {
                    "strand_id": "bottom",
                    "molecule_type": "DNA",
                    "segments": [
                        {
                            "segment_id": "bottom_b",
                            "structural_role": "paired_region",
                            "sequence": "GGGG",
                        },
                        {
                            "segment_id": "bottom_a",
                            "structural_role": "paired_region",
                            "sequence": "TTTT",
                        },
                    ],
                },
            ],
            "paired_regions": regions,
            "discontinuities": [],
        }

    whole = duplex_state(split=False)
    partitioned = duplex_state(split=True)
    incomplete = duplex_state(split=True, omit_second_top_segment=True)

    assert _pairing_and_discontinuity_similarity(
        whole, partitioned, scorable_only=False
    ) == pytest.approx(1.0)
    assert (
        _pairing_and_discontinuity_similarity(
            incomplete, partitioned, scorable_only=False
        )
        < 1.0
    )


@pytest.mark.parametrize("shorthand", ["VN", "NB"])
def test_state_anchor_shorthand_scores_in_reference_and_segment(
    shorthand: str,
) -> None:
    truth = t3_groundtruth()
    prediction = t3_prediction()
    truth_state = truth["workflows"][0]["states"][0]
    predicted_state = prediction["workflows"][0]["states"][0]
    for state, architecture in (
        (truth_state, "T30[ANCHOR:2][CDNA]"),
        (predicted_state, f"T30{shorthand}[CDNA_INSERT]"),
    ):
        state["molecule_type"] = "DNA"
        strand = state["strands"][0]
        strand["molecule_type"] = "DNA"
        strand["sequence_architecture"] = architecture
        strand["segments"][0].pop("placeholder", None)
        strand["segments"][0]["sequence"] = architecture

    metrics, details = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )

    assert metrics["t3_state_f1"] == pytest.approx(1.0)
    state_details = details["t3"]["workflows"]["workflow_main"]["state_matches"]
    matched_input = next(
        item for item in state_details if item["groundtruth_state_id"] == "state_input"
    )
    assert matched_input["dimension_scores"]["reference_strand"] == pytest.approx(1.0)
    assert matched_input["dimension_scores"]["segments"] == pytest.approx(1.0)


def test_terminal_reference_strand_accepts_token_aware_reverse_complement() -> None:
    prediction = t3_prediction()
    final_state = prediction["workflows"][0]["states"][1]
    reverse = "[CDNA][UMI:4]ACGT"
    final_state["strands"][0]["sequence_architecture"] = reverse
    final_state["strands"][0]["segments"][0]["sequence"] = reverse

    metrics, details = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), t3_groundtruth()
    )

    assert metrics["t3_state_f1"] == pytest.approx(1.0)
    state_matches = details["t3"]["workflows"]["workflow_main"]["state_matches"]
    final_match = next(
        item for item in state_matches if item["groundtruth_state_id"] == "state_cdna"
    )
    assert final_match["dimension_scores"]["reference_strand"] == pytest.approx(1.0)
    assert final_match["dimension_scores"]["segments"] == pytest.approx(1.0)


def test_nonterminal_reference_strand_does_not_hide_reverse_complement_error() -> None:
    truth = t3_groundtruth()
    prediction = t3_prediction()
    truth_state = truth["workflows"][0]["states"][0]
    predicted_state = prediction["workflows"][0]["states"][0]
    truth_state["strands"][0]["sequence_architecture"] = "ACGA[GDNA]"
    truth_state["strands"][0]["segments"][0]["placeholder"] = "ACGA[GDNA]"
    predicted_state["strands"][0]["sequence_architecture"] = "[GENOMIC_DNA]TCGT"
    predicted_state["strands"][0]["segments"][0]["placeholder"] = "[GENOMIC_DNA]TCGT"

    metrics, _ = grade_libgen(t2_prediction(), prediction, t2_groundtruth(), truth)

    assert metrics["t3_state_f1"] < 1.0


def test_missing_and_extra_entities_lower_scores() -> None:
    missing_t2 = t2_prediction()
    missing_t2["oligos"] = []
    metrics, _ = score_t2(
        missing_t2["oligos"],
        t2_groundtruth()["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert metrics["required_family_f1"] < 1.0

    extra_t3 = t3_prediction()
    extra_state = copy.deepcopy(extra_t3["workflows"][0]["states"][0])
    extra_state["state_id"] = "extra_state"
    extra_state["strands"][0]["strand_id"] = "extra_strand"
    extra_state["strands"][0]["segments"][0]["segment_id"] = "extra_segment"
    extra_state["reference_strand_id"] = "extra_strand"
    extra_t3["workflows"][0]["states"].append(extra_state)
    metrics, _ = grade_libgen(
        t2_prediction(), extra_t3, t2_groundtruth(), t3_groundtruth()
    )
    assert metrics["t3_state_f1"] < 1.0
    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)


def test_external_oligo_is_neutral_whether_omitted_or_exactly_predicted() -> None:
    truth = t2_groundtruth()
    external = copy.deepcopy(truth["oligos"][0])
    external.update(
        {
            "oligo_id": "vendor_primer",
            "name": "vendor primer",
            "aliases": [],
            "sequence": "TTTTGGGG",
            "support_status": "externally_completed",
        }
    )
    truth["oligos"].append(external)

    omitted_metrics, _ = score_t2(
        t2_prediction()["oligos"],
        truth["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    included = t2_prediction()
    included_external = copy.deepcopy(included["oligos"][0])
    included_external.update(
        {
            "oligo_id": "pred_vendor",
            "name": "vendor primer",
            "aliases": [],
            "sequence": "TTTTGGGG",
        }
    )
    included["oligos"].append(included_external)
    included_metrics, _ = score_t2(
        included["oligos"],
        truth["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert omitted_metrics["required_family_f1"] == pytest.approx(1.0)
    assert included_metrics["required_family_f1"] == pytest.approx(1.0)


def test_t3_referenced_oligo_is_neutral_when_not_source_recoverable() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["support_status"] = "externally_completed"

    metrics, details = score_t2([], truth["oligos"], required_oligo_ids={"oligo_rt"})

    assert metrics["used_groundtruth_count"] == 1.0
    assert metrics["required_family_count"] == 0.0
    assert metrics["neutral_used_groundtruth_count"] == 1.0
    assert metrics["required_family_recall"] == 1.0
    assert details["used_oligo_ids"] == ["oligo_rt"]
    assert details["scored_family_ids"] == []
    assert details["neutral_used_oligo_ids"] == ["oligo_rt"]
    assert details["unmatched_required_family_ids"] == []


def test_mixed_support_oligo_scores_only_recoverable_components() -> None:
    truth = t2_groundtruth()
    oligo = truth["oligos"][0]
    oligo["sequence"] = None
    oligo["kind"] = "assembled"
    oligo["components"] = [
        {
            "name": "visible",
            "role": "adapter",
            "sequence": "ACGT",
            "orientation": "5_to_3",
            "modifications": [],
            "support_status": "explicit",
        },
        {
            "name": "completed",
            "role": "vendor completion",
            "sequence": "TTTT",
            "orientation": "5_to_3",
            "modifications": [],
            "support_status": "externally_completed",
        },
    ]
    prediction = t2_prediction()
    predicted = prediction["oligos"][0]
    predicted["sequence"] = None
    predicted["kind"] = "assembled"
    predicted["components"] = [
        {
            "name": "visible",
            "role": "adapter",
            "sequence": "ACGT",
            "orientation": "5_to_3",
            "modifications": [],
        }
    ]

    metrics, _ = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )
    assert metrics["required_family_f1"] == pytest.approx(1.0)

    predicted["components"].append(
        {
            "name": "completed",
            "role": "vendor completion",
            "sequence": "TTTT",
            "orientation": "5_to_3",
            "modifications": [],
        }
    )
    metrics, _ = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )
    assert metrics["required_family_f1"] == pytest.approx(1.0)


def _ordered_test_components(*, groundtruth: bool) -> list[dict]:
    components = [
        {
            "name": "left adapter",
            "role": "adapter",
            "sequence": "ACGTAC",
            "orientation": "5_to_3",
            "modifications": [],
        },
        {
            "name": "sample index",
            "role": "sample index",
            "placeholder": "[I7_INDEX:8]",
            "orientation": "5_to_3",
            "modifications": [],
        },
        {
            "name": "modified right adapter",
            "role": "adapter",
            "sequence": "/ideoxyU/TTGGCC",
            "orientation": "5_to_3",
            "modifications": ["internal deoxyuridine"],
        },
    ]
    if groundtruth:
        for component in components:
            component["support_status"] = "explicit"
    return components


@pytest.mark.parametrize(
    ("truth_kind", "prediction_kind"),
    [("single", "assembled"), ("assembled", "unknown")],
)
def test_ordered_components_match_an_equivalent_flat_sequence(
    truth_kind: str,
    prediction_kind: str,
) -> None:
    sequence = "ACGTAC[I7_INDEX:8]/ideoxyU/TTGGCC"
    truth = t2_groundtruth()
    truth["oligos"][0].update(
        {
            "kind": truth_kind,
            "sequence": sequence,
            "components": _ordered_test_components(groundtruth=True),
        }
    )
    prediction = t2_prediction()
    prediction["oligos"][0].update(
        {
            "kind": prediction_kind,
            "sequence": None,
            "components": _ordered_test_components(groundtruth=False),
        }
    )

    metrics, _ = grade_libgen(prediction, t3_prediction(), truth, t3_groundtruth())

    assert metrics["t2_required_family_f1"] == pytest.approx(0.9)
    assert metrics["t2_exact_required_family_recall"] == 0.0
    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)
    assert metrics["reward"] == pytest.approx(0.97)

    prediction["oligos"][0]["components"].reverse()
    reversed_metrics, _ = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )
    assert reversed_metrics["required_family_f1"] < 1.0


def test_flat_sequence_matches_equivalent_ordered_groundtruth_components() -> None:
    sequence = "ACGTAC[I7_INDEX:8]/ideoxyU/TTGGCC"
    truth = t2_groundtruth()
    truth["oligos"][0].update(
        {
            "kind": "assembled",
            "sequence": None,
            "components": _ordered_test_components(groundtruth=True),
        }
    )
    prediction = t2_prediction()
    prediction["oligos"][0].update(
        {
            "kind": "assembled",
            "sequence": sequence,
            "components": [],
        }
    )

    metrics, _ = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert metrics["exact_required_family_recall"] == 1.0


def test_exact_modified_family_sequence_is_not_rewritten_from_role_prose() -> None:
    sequence = "ACGTAC[SAMPLE_INDEX:8]/ideoxyU/TTGGCC"
    truth = t2_groundtruth()
    truth["oligos"][0].update(
        {
            "kind": "assembled",
            "sequence": sequence,
            "components": [
                {
                    "name": "left adapter",
                    "role": "adapter",
                    "sequence": "ACGTAC",
                    "orientation": "5_to_3",
                    "modifications": [],
                    "support_status": "explicit",
                },
                {
                    "name": "sample index",
                    "role": "sample index",
                    "placeholder": "[SAMPLE_INDEX:8]",
                    "orientation": "5_to_3",
                    "modifications": [],
                    "support_status": "explicit",
                },
                {
                    "name": "modified right adapter",
                    "role": "blocking deoxyuridine plus adapter",
                    "sequence": "/ideoxyU/TTGGCC",
                    "orientation": "5_to_3",
                    "modifications": ["internal deoxyuridine"],
                    "support_status": "explicit",
                },
            ],
        }
    )
    prediction = t2_prediction()
    prediction["oligos"][0].update(
        {
            "kind": "single",
            "sequence": sequence,
            "components": [
                {
                    "name": "left adapter",
                    "role": "adapter",
                    "sequence": "ACGTAC",
                    "orientation": "5_to_3",
                    "modifications": [],
                },
                {
                    "name": "sample index",
                    "role": "sample index",
                    "placeholder": "[SAMPLE_INDEX:8]",
                    "orientation": "5_to_3",
                    "modifications": [],
                },
                {
                    "name": "blocking deoxyuridine",
                    "role": "blocks the polymerase before it copies the barcode",
                    "sequence": "U",
                    "length": 1,
                    "orientation": "5_to_3",
                    "modifications": ["deoxyuridine"],
                },
                {
                    "name": "right adapter",
                    "role": "adapter",
                    "sequence": "TTGGCC",
                    "orientation": "5_to_3",
                    "modifications": [],
                },
            ],
        }
    )

    metrics, details = score_t2(
        prediction["oligos"],
        truth["oligos"],
        required_oligo_ids={"oligo_rt"},
    )

    assert metrics["required_family_f1"] == pytest.approx(0.9)
    assert metrics["exact_required_family_recall"] == 0.0
    assert details["matches"][0]["sequence_score"] == pytest.approx(1.0)
    assert details["matches"][0]["dimension_scores"]["modifications"] == 1.0
    assert details["matches"][0]["dimension_scores"]["kind"] == 0.0

    prediction["oligos"][0]["sequence"] = sequence.replace("ACGTAC", "ACGTTC", 1)
    prediction["oligos"][0]["components"][0]["sequence"] = "ACGTTC"
    mismatch_metrics, mismatch_details = score_t2(
        prediction["oligos"],
        truth["oligos"],
        required_oligo_ids={"oligo_rt"},
    )

    assert mismatch_metrics["required_family_f1"] == pytest.approx(
        0.65 * (20 / 21) + 0.25
    )
    assert mismatch_details["matches"][0]["sequence_score"] == pytest.approx(20 / 21)


def test_double_stranded_components_remain_separate_sequence_claims() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0].update(
        {
            "kind": "double_stranded",
            "sequence": None,
            "components": [
                {
                    "name": "forward strand",
                    "role": "forward strand",
                    "sequence": "ACGTACGT",
                    "orientation": "5_to_3",
                    "modifications": [],
                    "support_status": "explicit",
                },
                {
                    "name": "reverse strand",
                    "role": "reverse strand",
                    "sequence": "TGCATGCA",
                    "orientation": "3_to_5",
                    "modifications": [],
                    "support_status": "explicit",
                },
            ],
        }
    )
    prediction = t2_prediction()
    prediction["oligos"][0].update(
        {
            "kind": "double_stranded",
            "sequence": None,
            "components": [
                {
                    key: value
                    for key, value in component.items()
                    if key != "support_status"
                }
                for component in truth["oligos"][0]["components"]
            ],
        }
    )

    component_metrics, _ = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )
    assert component_metrics["required_family_f1"] == pytest.approx(1.0)

    prediction["oligos"][0].update({"sequence": "ACGTACGTTGCATGCA", "components": []})
    flattened_metrics, _ = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )
    assert flattened_metrics["required_family_f1"] < 1.0


def _additional_oligo(
    *, oligo_id: str, sequence: str, name: str = "additional oligo"
) -> dict:
    item = copy.deepcopy(t2_prediction()["oligos"][0])
    item.update(
        {
            "oligo_id": oligo_id,
            "name": name,
            "aliases": [],
            "sequence": sequence,
        }
    )
    return item


def _panel_member(
    *,
    oligo_id: str,
    barcode: str,
    prefix: str = "AAA",
    suffix: str = "TTT",
    role: str = "barcoded reverse-transcription primer",
    orientation: str = "5_to_3",
    modifications: list[str] | None = None,
) -> dict:
    return {
        "oligo_id": oligo_id,
        "name": oligo_id,
        "aliases": [],
        "role": role,
        "kind": "assembled",
        "sequence": f"{prefix}{barcode}{suffix}",
        "orientation": orientation,
        "components": [
            {
                "name": "fixed 5-prime scaffold",
                "role": "ligation handle",
                "sequence": prefix,
                "orientation": orientation,
                "modifications": [],
            },
            {
                "name": "round 1 cell barcode",
                "role": "cell barcode",
                "sequence": barcode,
                "orientation": orientation,
                "modifications": [],
            },
            {
                "name": "fixed 3-prime scaffold",
                "role": "primer binding site",
                "sequence": suffix,
                "orientation": orientation,
                "modifications": [],
            },
        ],
        "modifications": list(modifications or []),
    }


def _panel_family_truth() -> dict:
    result = _panel_member(oligo_id="family_rt", barcode="ACGT")
    result["sequence"] = "AAA[CELL_BARCODE:4]TTT"
    result["components"][1].pop("sequence")
    result["components"][1].update({"placeholder": "[CELL_BARCODE:4]", "length": 4})
    result["support_status"] = "explicit"
    for component in result["components"]:
        component["support_status"] = "explicit"
    return result


def test_concrete_panel_members_collapse_to_one_required_family() -> None:
    predictions = [
        _panel_member(oligo_id="member_1", barcode="ACGT"),
        _panel_member(oligo_id="member_2", barcode="TGCA"),
        _panel_member(oligo_id="member_3", barcode="CAGT"),
    ]

    metrics, details = score_t2(
        predictions,
        [_panel_family_truth()],
        required_oligo_ids={"family_rt"},
    )

    assert metrics["predicted_member_count"] == 3.0
    assert metrics["predicted_family_count"] == 1.0
    assert metrics["required_family_precision"] == pytest.approx(1.0)
    assert metrics["required_family_recall"] == pytest.approx(1.0)
    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert metrics["exact_required_family_recall"] == pytest.approx(1.0)
    assert details["matches"][0]["prediction_oligo_ids"] == [
        "member_1",
        "member_2",
        "member_3",
    ]


def test_unrelated_concrete_panel_counts_as_one_extra_family() -> None:
    predictions = [
        _panel_member(oligo_id="required_1", barcode="ACGT"),
        _panel_member(oligo_id="required_2", barcode="TGCA"),
        _panel_member(
            oligo_id="extra_1",
            barcode="AAAA",
            prefix="CCC",
            suffix="GGG",
        ),
        _panel_member(
            oligo_id="extra_2",
            barcode="CCCC",
            prefix="CCC",
            suffix="GGG",
        ),
    ]

    metrics, details = score_t2(
        predictions,
        [_panel_family_truth()],
        required_oligo_ids={"family_rt"},
    )

    assert metrics["predicted_member_count"] == 4.0
    assert metrics["predicted_family_count"] == 2.0
    assert metrics["required_family_precision"] == pytest.approx(0.5)
    assert metrics["required_family_recall"] == pytest.approx(1.0)
    assert metrics["required_family_f1"] == pytest.approx(2 / 3)
    assert metrics["exact_required_family_recall"] == pytest.approx(1.0)
    assert len(details["unmatched_prediction_families"]) == 1
    assert details["unmatched_prediction_families"][0]["member_oligo_ids"] == [
        "extra_1",
        "extra_2",
    ]


@pytest.mark.parametrize(
    "change",
    ("role", "orientation", "modification"),
)
def test_family_collapse_preserves_scientifically_distinct_records(
    change: str,
) -> None:
    correct = _panel_member(oligo_id="correct", barcode="ACGT")
    distinct = _panel_member(oligo_id="distinct", barcode="TGCA")
    if change == "role":
        distinct["role"] = "sample-index primer"
    elif change == "orientation":
        distinct["orientation"] = "3_to_5"
        for component in distinct["components"]:
            component["orientation"] = "3_to_5"
    else:
        distinct["modifications"] = ["5-prime phosphate"]

    metrics, _ = score_t2(
        [correct, distinct],
        [_panel_family_truth()],
        required_oligo_ids={"family_rt"},
    )

    assert metrics["predicted_family_count"] == 2.0
    assert metrics["required_family_precision"] == pytest.approx(0.5)
    assert metrics["exact_required_family_recall"] == pytest.approx(1.0)


def test_concrete_groundtruth_members_remain_member_level_requirements() -> None:
    truth = [
        _panel_member(oligo_id="truth_1", barcode="ACGT"),
        _panel_member(oligo_id="truth_2", barcode="TGCA"),
    ]
    for item in truth:
        item["support_status"] = "explicit"
        for component in item["components"]:
            component["support_status"] = "explicit"
    predictions = [
        _panel_member(oligo_id="prediction_1", barcode="ACGT"),
        _panel_member(oligo_id="prediction_2", barcode="TGCA"),
    ]

    complete, complete_details = score_t2(
        predictions,
        truth,
        required_oligo_ids={"truth_1", "truth_2"},
    )
    missing, _ = score_t2(
        predictions[:1],
        truth,
        required_oligo_ids={"truth_1", "truth_2"},
    )

    assert complete["predicted_family_count"] == 2.0
    assert complete["required_family_f1"] == pytest.approx(1.0)
    assert {
        match["groundtruth_scoring_level"] for match in complete_details["matches"]
    } == {"member"}
    assert missing["required_family_recall"] == pytest.approx(0.5)
    assert missing["exact_required_family_recall"] == pytest.approx(0.5)


def test_t3_transition_oligo_members_collapse_to_family_reference() -> None:
    truth_t2 = t2_groundtruth()
    truth_t2["oligos"] = [_panel_family_truth()]
    truth_t3 = t3_groundtruth()
    truth_t3["workflows"][0]["transitions"][0]["oligo_ids"] = ["family_rt"]
    prediction_t2 = t2_prediction()
    prediction_t2["oligos"] = [
        _panel_member(oligo_id="member_1", barcode="ACGT"),
        _panel_member(oligo_id="member_2", barcode="TGCA"),
    ]
    prediction_t3 = t3_prediction()
    prediction_t3["workflows"][0]["transitions"][0]["oligo_ids"] = [
        "member_1",
        "member_2",
    ]

    metrics, _ = grade_libgen(
        prediction_t2,
        prediction_t3,
        truth_t2,
        truth_t3,
    )

    assert metrics["t2_required_family_f1"] == pytest.approx(1.0)
    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)


def test_missing_required_oligo_family_lowers_required_family_f1() -> None:
    metrics, details = score_t2(
        [], t2_groundtruth()["oligos"], required_oligo_ids={"oligo_rt"}
    )
    assert metrics["required_family_f1"] == 0.0
    assert metrics["exact_required_family_recall"] == 0.0
    assert details["unmatched_required_family_ids"] == ["oligo_rt"]


def test_exact_optional_oligo_is_neutral() -> None:
    truth = t2_groundtruth()
    optional = _additional_oligo(oligo_id="optional", sequence="TTTTGGGG")
    optional["support_status"] = "explicit"
    truth["oligos"].append(optional)
    prediction = t2_prediction()
    prediction["oligos"].append(
        _additional_oligo(oligo_id="pred_optional", sequence="TTTTGGGG")
    )

    metrics, details = score_t2(
        prediction["oligos"],
        truth["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert metrics["exact_required_family_recall"] == 1.0
    assert details["neutralized_prediction_indices"] == [1]


def test_exact_optional_is_neutral_before_soft_required_matching() -> None:
    truth = t2_groundtruth()
    optional = _additional_oligo(
        oligo_id="optional",
        sequence="ACGT[UMI:3]",
    )
    optional["support_status"] = "explicit"
    truth["oligos"].append(optional)
    prediction = {
        "protocol_id": "example_protocol",
        "oligos": [
            _additional_oligo(
                oligo_id="pred_optional",
                sequence="ACGT[UMI:3]",
            )
        ],
    }

    metrics, details = score_t2(
        prediction["oligos"],
        truth["oligos"],
        required_oligo_ids={"oligo_rt"},
    )

    assert metrics["required_family_recall"] == 0.0
    assert metrics["required_family_precision"] == 0.0
    assert details["neutralized_prediction_indices"] == [0]


def test_extra_unknown_oligo_reduces_precision() -> None:
    prediction = t2_prediction()
    prediction["oligos"].append(
        _additional_oligo(oligo_id="unknown", sequence="CCCCAAAA")
    )
    metrics, _ = score_t2(
        prediction["oligos"],
        t2_groundtruth()["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert metrics["required_family_precision"] < 1.0
    assert metrics["required_family_recall"] == pytest.approx(1.0)
    assert metrics["exact_required_family_recall"] == 1.0


def test_duplicate_identical_prediction_collapses_to_one_family() -> None:
    prediction = t2_prediction()
    duplicate = copy.deepcopy(prediction["oligos"][0])
    duplicate["oligo_id"] = "duplicate_rt"
    prediction["oligos"].append(duplicate)
    metrics, details = score_t2(
        prediction["oligos"],
        t2_groundtruth()["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert metrics["required_family_recall"] == pytest.approx(1.0)
    assert metrics["required_family_precision"] == pytest.approx(1.0)
    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert metrics["predicted_member_count"] == 2.0
    assert metrics["predicted_family_count"] == 1.0
    assert details["prediction_families"][0]["member_oligo_ids"] == [
        "oligo_rt",
        "duplicate_rt",
    ]


def test_t2_names_and_aliases_do_not_affect_assignment_or_reward() -> None:
    prediction = t2_prediction()
    prediction["oligos"][0].update(
        {
            "name": "unrelated name",
            "aliases": ["unrelated alias"],
        }
    )
    metrics, _ = score_t2(
        prediction["oligos"],
        t2_groundtruth()["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert metrics["required_family_f1"] == pytest.approx(1.0)


def test_t2_structured_identity_claims_affect_reward() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["modifications"] = ["5' phosphate"]
    prediction = t2_prediction()
    prediction["oligos"][0].update(
        {
            "role": "unrelated role",
            "orientation": "3_to_5",
            "modifications": ["unknown modification"],
        }
    )
    metrics, _ = score_t2(
        prediction["oligos"],
        truth["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert metrics["required_family_f1"] == pytest.approx(0.75)
    assert metrics["exact_required_family_recall"] == 0.0


def test_t2_empty_truth_modifications_receive_full_credit() -> None:
    truth = t2_groundtruth()
    prediction = t2_prediction()
    prediction["oligos"][0]["modifications"] = [
        "5' immobilization on Single Cell 3' v3 Gel Bead"
    ]

    metrics, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert metrics["exact_required_family_recall"] == 1.0
    assert metrics["modification_f1"] == pytest.approx(1.0)
    assert details["matches"][0]["dimension_scores"]["modifications"] == 1.0


def test_t2_empty_truth_modifications_do_not_reweight_other_dimensions() -> None:
    truth = t2_groundtruth()
    prediction = t2_prediction()
    prediction["oligos"][0].update(
        {
            "kind": "assembled",
            "modifications": ["5' immobilization on Single Cell 3' v3 Gel Bead"],
        }
    )

    metrics, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    # Empty canonical chemistry is an unannotated wildcard worth full credit,
    # not a removed dimension that changes the relative weight of kind.
    assert metrics["required_family_f1"] == pytest.approx(0.9)
    assert details["matches"][0]["score"] == pytest.approx(0.9)
    assert details["matches"][0]["dimension_scores"]["modifications"] == 1.0


def test_t2_bead_immobilization_phrases_are_equivalent() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["modifications"] = ["5' bead-immobilized"]
    prediction = t2_prediction()
    prediction["oligos"][0]["modifications"] = [
        "5' immobilization on Single Cell 3' v3 Gel Bead",
        "5' gel-bead attachment",
    ]

    metrics, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert details["matches"][0]["dimension_scores"]["modifications"] == 1.0


def test_t2_generic_rna_wording_is_subsumed_by_explicit_rgrgrg() -> None:
    truth = t2_groundtruth()
    truth_oligo = truth["oligos"][0]
    truth_oligo["sequence"] = "AAGCAGTGGTATCAACGCAGAGTACATrGrGrG"
    truth_oligo["modifications"] = ["3'-rGrGrG (three riboguanosines)"]
    prediction = t2_prediction()
    predicted_oligo = prediction["oligos"][0]
    predicted_oligo["sequence"] = "AAGCAGTGGTATCAACGCAGAGTACATrGrGrG"
    predicted_oligo["modifications"] = [
        "three 3' riboguanosines",
        "RNA ribonucleotides",
    ]

    metrics, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert details["matches"][0]["dimension_scores"]["modifications"] == 1.0


def test_t2_inline_terminal_chemistry_matches_structured_recording() -> None:
    truth = t2_groundtruth()
    truth_oligo = truth["oligos"][0]
    truth_oligo.update(
        {
            "kind": "assembled",
            "sequence": "/5Phos/ACGT[CELL_BARCODE:8]TTTT",
            "modifications": ["5' phosphate"],
            "components": [
                {
                    "name": "fixed arm",
                    "role": "adapter",
                    "sequence": "ACGT",
                    "orientation": "5_to_3",
                    "modifications": ["5' phosphate"],
                    "support_status": "explicit",
                },
                {
                    "name": "barcode",
                    "role": "cell barcode",
                    "placeholder": "[CELL_BARCODE:8]",
                    "orientation": "5_to_3",
                    "modifications": [],
                    "support_status": "explicit",
                },
                {
                    "name": "capture tract",
                    "role": "reverse transcription primer",
                    "sequence": "TTTT",
                    "orientation": "5_to_3",
                    "modifications": [],
                    "support_status": "explicit",
                },
            ],
        }
    )
    prediction = t2_prediction()
    predicted_oligo = prediction["oligos"][0]
    predicted_oligo.update(
        {
            "kind": "assembled",
            "sequence": "ACGT[CELL_BARCODE:8]TTTT",
            "modifications": ["5-prime phosphate (/5Phos/)"],
            "components": [
                {
                    "name": "fixed arm",
                    "role": "adapter",
                    "sequence": "ACGT",
                    "orientation": "5_to_3",
                    "modifications": ["5-prime phosphate at the oligo terminus"],
                },
                {
                    "name": "barcode",
                    "role": "cell barcode",
                    "placeholder": "[CELL_BARCODE:8]",
                    "orientation": "5_to_3",
                    "modifications": [],
                },
                {
                    "name": "capture tract",
                    "role": "reverse transcription primer",
                    "sequence": "TTTT",
                    "orientation": "5_to_3",
                    "modifications": [],
                },
            ],
        }
    )

    metrics, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert metrics["exact_required_family_recall"] == 1.0
    assert details["matches"][0]["dimension_scores"] == {
        "sequence": pytest.approx(1.0),
        "modifications": pytest.approx(1.0),
        "kind": pytest.approx(1.0),
        "orientation": pytest.approx(1.0),
        "role": pytest.approx(1.0),
    }

    predicted_oligo["modifications"] = []
    predicted_oligo["components"][0]["modifications"] = []
    missing_chemistry, _ = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )
    assert missing_chemistry["required_family_f1"] == pytest.approx(0.85)


def test_t2_controlled_role_matches_semantic_paraphrase() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["role"] = "ligation linker (round 2)"
    prediction = t2_prediction()
    prediction["oligos"][0]["role"] = (
        "Universal bridge that anneals a barcode strand for ligation"
    )

    metrics, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert details["matches"][0]["dimension_scores"]["role"] == 1.0


def test_t2_generic_primer_role_accepts_specific_primer_function() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["role"] = "primer"
    prediction = t2_prediction()
    prediction["oligos"][0]["role"] = (
        "Feature-barcode indexing-PCR primer that appends P5 and Read 1N"
    )

    _, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert details["matches"][0]["dimension_scores"]["role"] == 1.0


def test_t2_generic_adapter_role_accepts_explicit_adapter_component() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["role"] = "adapter"
    prediction = t2_prediction()
    prediction["oligos"][0]["role"] = (
        "Gene-expression indexing-PCR primer that appends P5"
    )
    prediction["oligos"][0]["components"] = [
        {
            "name": "P5",
            "role": "Illumina P5 flow-cell adaptor",
            "sequence": "ACGT",
            "orientation": "5_to_3",
            "modifications": [],
        }
    ]

    _, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert details["matches"][0]["dimension_scores"]["role"] == 1.0


def test_t2_generic_adapter_role_does_not_accept_bare_indexing_primer() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["role"] = "adapter"
    prediction = t2_prediction()
    prediction["oligos"][0]["role"] = "Indexing-PCR primer"

    _, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert details["matches"][0]["dimension_scores"]["role"] == 0.0


def test_t2_generic_primer_prediction_does_not_satisfy_specific_role() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["role"] = "indexing PCR primer"
    prediction = t2_prediction()
    prediction["oligos"][0]["role"] = "primer"

    _, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert details["matches"][0]["dimension_scores"]["role"] == 0.0


@pytest.mark.parametrize(
    "predicted_role",
    [
        (
            "first-round indexed PCR primer at the RT-handle end "
            "that adds P5 and i5 sequence"
        ),
        (
            "second-round indexed PCR primer at the Tn5-adaptor end "
            "that adds P7 and i7 sequence"
        ),
    ],
)
def test_t2_indexing_primer_does_not_inherit_referenced_target_role(
    predicted_role: str,
) -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["role"] = "indexing PCR primer (P7 side)"
    prediction = t2_prediction()
    prediction["oligos"][0]["role"] = predicted_role

    metrics, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert details["matches"][0]["dimension_scores"]["role"] == 1.0


def test_t2_randomer_placeholder_matches_family_n_run() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["sequence"] = "ACGT[CELL_BARCODE:8]NNNNNN"
    prediction = t2_prediction()
    prediction["oligos"][0]["sequence"] = "ACGT[CELL_BARCODE:8][RANDOMER:6]"

    metrics, details = score_t2(
        prediction["oligos"], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert metrics["required_family_f1"] == pytest.approx(1.0)
    assert details["matches"][0]["sequence_score"] == pytest.approx(1.0)


def test_t3_oligo_use_matches_local_t2_sequences_not_ids_or_names() -> None:
    predicted_t2, predicted_t3 = renamed_predictions()
    predicted_t2["oligos"][0]["name"] = "unrelated predicted name"

    exact_metrics, _ = grade_libgen(
        predicted_t2, predicted_t3, t2_groundtruth(), t3_groundtruth()
    )
    assert exact_metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)

    predicted_t2["oligos"][0]["sequence"] = "TTTTTTTT"
    wrong_sequence_metrics, _ = grade_libgen(
        predicted_t2, predicted_t3, t2_groundtruth(), t3_groundtruth()
    )
    assert wrong_sequence_metrics["t3_molecular_transition_f1"] < 1.0


def test_extension_and_strand_synthesis_operations_are_equivalent() -> None:
    truth = t3_groundtruth()
    prediction = t3_prediction()
    truth["workflows"][0]["transitions"][0]["operation"] = "strand_synthesis"
    prediction["workflows"][0]["transitions"][0]["operation"] = "extension"

    metrics, details = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )

    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)
    transition = details["t3"]["workflows"]["workflow_main"]["transition_matches"][0]
    assert transition["dimension_scores"]["operation"] == 1.0


def test_indexing_and_pcr_operations_remain_distinct() -> None:
    truth = t3_groundtruth()
    prediction = t3_prediction()
    truth["workflows"][0]["transitions"][0]["operation"] = "pcr"
    prediction["workflows"][0]["transitions"][0]["operation"] = "indexing"

    metrics, _ = grade_libgen(t2_prediction(), prediction, t2_groundtruth(), truth)

    assert metrics["t3_molecular_transition_f1"] < 1.0


def test_transition_assignment_prefers_event_identity_over_folded_cleanup() -> None:
    truth = {
        "operation": "extension",
        "substrate_state_ids": ["truth_input"],
        "product_state_ids": ["truth_final"],
        "carried_forward_product_ids": ["truth_final"],
        "discarded_product_ids": [],
        "oligo_ids": [],
    }
    explicit_event = {
        "operation": "extension",
        "substrate_state_ids": ["pred_input"],
        "product_state_ids": ["pred_intermediate"],
        "carried_forward_product_ids": ["pred_intermediate"],
        "discarded_product_ids": [],
        "oligo_ids": [],
    }
    cleanup = {
        "operation": "cleanup",
        "substrate_state_ids": ["pred_intermediate"],
        "product_state_ids": ["pred_final"],
        "carried_forward_product_ids": ["pred_final"],
        "discarded_product_ids": [],
        "oligo_ids": [],
    }
    state_map = {"pred_input": "truth_input", "pred_final": "truth_final"}

    explicit_score = _transition_assignment_similarity(
        explicit_event,
        truth,
        state_map=state_map,
        predicted_oligos={},
        truth_oligos={},
    )
    cleanup_score = _transition_assignment_similarity(
        cleanup,
        truth,
        state_map=state_map,
        predicted_oligos={},
        truth_oligos={},
    )

    assert explicit_score > cleanup_score


def test_partial_assignment_leaves_low_similarity_entities_unmatched() -> None:
    assert best_partial_one_to_one_matching(
        [[0.90, 0.10], [0.20, 0.10]],
        minimum_score=0.25,
    ) == [(0, 0, 0.90)]
    assert best_partial_one_to_one_matching(
        [[0.90, 0.80], [0.85, 0.20]],
        minimum_score=0.25,
    ) == [(0, 1, 0.80), (1, 0, 0.85)]


def test_state_assignment_uses_workflow_position_only_as_a_tie_break() -> None:
    prediction = {"state_id": "predicted"}
    truth = {"state_id": "truth"}
    same_position = _state_assignment_similarity(
        scientific_score=0.40,
        prediction=prediction,
        truth=truth,
        predicted_positions={"predicted": "initial"},
        truth_positions={"truth": "initial"},
    )
    different_position = _state_assignment_similarity(
        scientific_score=0.40,
        prediction=prediction,
        truth=truth,
        predicted_positions={"predicted": "initial"},
        truth_positions={"truth": "intermediate"},
    )

    assert same_position > different_position
    assert different_position == pytest.approx(0.40 / 1.10)


def test_required_t2_ids_come_from_transitions_and_segment_derivations() -> None:
    truth = t3_groundtruth()
    truth["workflows"][0]["transitions"][0]["oligo_ids"] = ["transition_oligo"]
    derivation = truth["workflows"][0]["states"][1]["strands"][0]["segments"][0][
        "oligo_derivations"
    ][0]
    derivation["oligo_id"] = "segment_oligo"
    assert derive_required_t2_ids(truth) == {
        "transition_oligo",
        "segment_oligo",
    }


def test_external_t3_state_is_neutral_when_omitted() -> None:
    truth = t3_groundtruth()
    external = copy.deepcopy(truth["workflows"][0]["states"][0])
    external["state_id"] = "external_state"
    external["reference_strand_id"] = "external_strand"
    external["support_status"] = "externally_completed"
    external["strands"][0]["strand_id"] = "external_strand"
    external["strands"][0]["support_status"] = "externally_completed"
    external["strands"][0]["segments"][0]["segment_id"] = "external_segment"
    truth["workflows"][0]["states"].append(external)
    metrics, _ = grade_libgen(t2_prediction(), t3_prediction(), t2_groundtruth(), truth)
    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)


def _multimodal_t3() -> dict:
    result = t3_groundtruth()
    workflow = result["workflows"][0]
    final_state = copy.deepcopy(workflow["states"][1])
    final_state["state_id"] = "state_atac"
    final_state["name"] = "ATAC library"
    final_state["reference_strand_id"] = "strand_atac"
    final_state["strands"][0]["strand_id"] = "strand_atac"
    final_state["strands"][0]["sequence_architecture"] = "TTTT[GDNA]"
    final_segment = final_state["strands"][0]["segments"][0]
    final_segment["segment_id"] = "segment_atac"
    final_segment["sequence"] = "TTTT[GDNA]"
    final_segment.pop("placeholder", None)
    workflow["states"].append(final_state)
    atac_transition = copy.deepcopy(workflow["transitions"][0])
    atac_transition["transition_id"] = "transition_atac"
    atac_transition["operation"] = "tagmentation"
    atac_transition["product_state_ids"] = ["state_atac"]
    atac_transition["carried_forward_product_ids"] = ["state_atac"]
    workflow["transitions"].append(atac_transition)
    workflow["final_outputs"].append(
        {"state_id": "state_atac", "modality": "chromatin accessibility"}
    )
    return result


def test_connected_multimodal_workflow_scores_shared_graph_once() -> None:
    truth = _multimodal_t3()
    prediction = copy.deepcopy(truth)
    for workflow in prediction["workflows"]:
        for state in workflow["states"]:
            state.pop("support_status", None)
            for strand in state["strands"]:
                strand.pop("support_status", None)
        for transition in workflow["transitions"]:
            transition.pop("support_status", None)
    metrics, details = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )
    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)
    assert metrics["t3_typed_edge_f1"] == pytest.approx(1.0)
    assert details["diagnostic_metrics"]["t3"]["groundtruth_workflow_count"] == 1.0


@pytest.mark.parametrize(
    ("canonical", "alias"),
    [
        ("chromatin accessibility", "ATAC"),
        ("chromatin accessibility", "scATAC"),
        ("chromatin accessibility", "chromatin_accessibility"),
        ("gene expression", "RNA"),
        ("gene expression", "scRNA-seq"),
        ("gene expression", "single_cell_rna_seq"),
        ("genomic DNA", "gDNA"),
        ("genomic DNA", "gdna"),
        ("feature barcode", "feature_barcode"),
        ("sgRNA", "sgrna"),
    ],
)
def test_prediction_alias_matches_canonical_modality(
    canonical: str, alias: str
) -> None:
    truth = t3_groundtruth()
    truth["workflows"][0]["final_outputs"][0]["modality"] = canonical
    prediction = t3_prediction()
    prediction["workflows"][0]["final_outputs"][0]["modality"] = alias

    metrics, details = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )

    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)
    assert (
        details["t3"]["terminal_modalities"][canonical]["predicted_outputs"][0][
            "reported_modality"
        ]
        == alias
    )


def test_swapped_terminal_modalities_are_reported_without_rescoring_shared_graph() -> (
    None
):
    truth = _multimodal_t3()
    prediction = copy.deepcopy(truth)
    outputs = prediction["workflows"][0]["final_outputs"]
    outputs[0]["modality"], outputs[1]["modality"] = (
        outputs[1]["modality"],
        outputs[0]["modality"],
    )
    metrics, details = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )
    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)
    workflow = next(iter(details["t3"]["workflows"].values()))
    assert workflow["terminal_output_f1"] == pytest.approx(0.0)


def test_wrong_operation_with_correct_topology_preserves_typed_edges() -> None:
    prediction = t3_prediction()
    prediction["workflows"][0]["transitions"][0]["operation"] = "ligation"
    metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), t3_groundtruth()
    )
    assert metrics["t3_molecular_transition_f1"] < 1.0
    assert metrics["t3_typed_edge_f1"] == pytest.approx(1.0)


def test_correct_operation_with_wrong_edge_lowers_typed_edge_f1() -> None:
    prediction = t3_prediction()
    prediction["workflows"][0]["transitions"][0]["substrate_state_ids"] = ["state_cdna"]
    metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), t3_groundtruth()
    )
    assert metrics["t3_molecular_transition_f1"] < 1.0
    assert metrics["t3_typed_edge_f1"] == pytest.approx(0.5)


def test_carried_and_discarded_product_swap_lowers_both_t3_metrics() -> None:
    prediction = t3_prediction()
    transition = prediction["workflows"][0]["transitions"][0]
    transition["carried_forward_product_ids"] = []
    transition["discarded_product_ids"] = ["state_cdna"]
    metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), t3_groundtruth()
    )
    assert metrics["t3_molecular_transition_f1"] < 1.0
    assert metrics["t3_typed_edge_f1"] == pytest.approx(0.5)


def _branched_t3() -> dict:
    result = t3_groundtruth()
    workflow = result["workflows"][0]
    branch_state = copy.deepcopy(workflow["states"][1])
    branch_state["state_id"] = "state_branch"
    branch_state["reference_strand_id"] = "strand_branch"
    branch_state["strands"][0]["strand_id"] = "strand_branch"
    branch_state["strands"][0]["segments"][0]["segment_id"] = "segment_branch"
    branch_state["strands"][0]["sequence_architecture"] = "GGGG[CDNA]"
    branch_state["strands"][0]["segments"][0]["sequence"] = "GGGG[CDNA]"
    workflow["states"].append(branch_state)
    branch_transition = copy.deepcopy(workflow["transitions"][0])
    branch_transition["transition_id"] = "transition_branch"
    branch_transition["operation"] = "pcr"
    branch_transition["product_state_ids"] = ["state_branch"]
    branch_transition["carried_forward_product_ids"] = ["state_branch"]
    workflow["transitions"].append(branch_transition)
    workflow["final_outputs"].append(
        {"state_id": "state_branch", "modality": "gene expression"}
    )
    return result


def test_one_missing_graph_branch_lowers_transition_and_edge_recall() -> None:
    truth = _branched_t3()
    prediction = t3_prediction()
    metrics, details = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )
    assert details["diagnostic_metrics"]["t3"]["molecular_transition_recall"] < 1.0
    assert metrics["t3_typed_edge_f1"] < 1.0


def test_ambiguous_transition_and_its_edges_are_neutral() -> None:
    truth = t3_groundtruth()
    truth["workflows"][0]["transitions"][0]["support_status"] = "ambiguous"
    prediction = t3_prediction()
    prediction["workflows"][0]["transitions"] = []

    metrics, details = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )

    assert details["diagnostic_metrics"]["t3"]["molecular_transition_recall"] == 1.0
    assert metrics["t3_typed_edge_f1"] == 1.0


def test_externally_completed_t3_sequence_architecture_is_neutral() -> None:
    truth = t3_groundtruth()
    truth_strand = truth["workflows"][0]["states"][1]["strands"][0]
    truth_strand["support_status"] = "externally_completed"
    prediction = t3_prediction()
    predicted_strand = prediction["workflows"][0]["states"][1]["strands"][0]
    predicted_strand["sequence_architecture"] = "TTTT[CDNA]"
    predicted_strand["segments"][0]["sequence"] = "TTTT[CDNA]"

    metrics, _ = grade_libgen(t2_prediction(), prediction, t2_groundtruth(), truth)

    assert metrics["t3_state_f1"] == pytest.approx(1.0)


def test_externally_completed_t3_linked_oligo_sequence_is_neutral() -> None:
    truth_t2 = t2_groundtruth()
    truth_t2["oligos"][0]["support_status"] = "externally_completed"
    prediction_t2 = t2_prediction()
    prediction_t2["oligos"] = []
    prediction_t3 = t3_prediction()
    prediction_t3["workflows"][0]["transitions"][0]["oligo_ids"] = []
    prediction_t3["workflows"][0]["states"][1]["strands"][0]["segments"][0][
        "oligo_derivations"
    ] = []

    metrics, _ = grade_libgen(
        prediction_t2,
        prediction_t3,
        truth_t2,
        t3_groundtruth(),
    )

    assert metrics["t2_required_family_f1"] == pytest.approx(1.0)
    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)


def test_prediction_validator_rejects_dangling_refs_and_cycles() -> None:
    t2 = t2_prediction()
    dangling = t3_prediction()
    dangling["workflows"][0]["transitions"][0]["oligo_ids"] = ["missing_oligo"]
    validate_t3_prediction(
        dangling, protocol_id="example_protocol", schema_root=SCHEMA_ROOT
    )
    with pytest.raises(LibgenValidationError, match="missing_oligo"):
        validate_prediction_links(t2, dangling)

    cyclic = t3_prediction()
    transition = cyclic["workflows"][0]["transitions"][0]
    transition["substrate_state_ids"] = ["state_cdna"]
    transition["product_state_ids"] = ["state_input"]
    transition["carried_forward_product_ids"] = ["state_input"]
    cyclic["workflows"][0]["initial_state_ids"] = ["state_cdna"]
    cyclic["workflows"][0]["final_outputs"] = [
        {"state_id": "state_input", "modality": "RNA"}
    ]
    second = copy.deepcopy(transition)
    second["transition_id"] = "transition_cycle"
    second["substrate_state_ids"] = ["state_input"]
    second["product_state_ids"] = ["state_cdna"]
    second["carried_forward_product_ids"] = ["state_cdna"]
    cyclic["workflows"][0]["transitions"].append(second)
    with pytest.raises(LibgenValidationError, match="cycle"):
        validate_prediction_links(t2, cyclic)


def _one_arm_y_shaped_state() -> dict:
    return {
        "state_id": "state_input",
        "name": "one-arm Y-shaped prediction",
        "molecule_type": "DNA",
        "strand_architecture": "y_shaped_duplex",
        "reference_strand_id": "top",
        "physical_state": "solution",
        "strands": [
            {
                "strand_id": "top",
                "name": "top",
                "molecule_type": "DNA",
                "orientation": "5_to_3",
                "sequence_architecture": "ACGTAA",
                "segments": [
                    {
                        "segment_id": "top_paired",
                        "role": "paired",
                        "structural_role": "paired_region",
                        "sequence": "ACGT",
                    },
                    {
                        "segment_id": "top_overhang",
                        "role": "overhang",
                        "structural_role": "three_prime_overhang",
                        "sequence": "AA",
                    },
                ],
            },
            {
                "strand_id": "bottom",
                "name": "bottom",
                "molecule_type": "DNA",
                "orientation": "5_to_3",
                "sequence_architecture": "ACGT",
                "segments": [
                    {
                        "segment_id": "bottom_paired",
                        "role": "paired",
                        "structural_role": "paired_region",
                        "sequence": "ACGT",
                    }
                ],
            },
        ],
        "paired_regions": [
            {
                "paired_region_id": "pair",
                "side_1": {"strand_id": "top", "segment_ids": ["top_paired"]},
                "side_2": {
                    "strand_id": "bottom",
                    "segment_ids": ["bottom_paired"],
                },
                "relationship": "reverse_complementary",
            }
        ],
        "discontinuities": [],
        "properties": [],
    }


def test_prediction_validation_does_not_apply_groundtruth_only_y_rule() -> None:
    prediction = t3_prediction()
    prediction["workflows"][0]["states"][0] = _one_arm_y_shaped_state()

    validate_t3_prediction(
        prediction,
        protocol_id="example_protocol",
        schema_root=SCHEMA_ROOT,
    )
    validate_prediction_links(t2_prediction(), prediction)

    truth = t3_groundtruth()
    truth_state = _one_arm_y_shaped_state()
    truth_state["support_status"] = "explicit"
    for strand in truth_state["strands"]:
        strand["support_status"] = "explicit"
    for region in truth_state["paired_regions"]:
        region["support_status"] = "explicit"
    truth["workflows"][0]["states"][0] = truth_state
    with pytest.raises(LibgenValidationError, match="unpaired arm on both strands"):
        validate_groundtruth_bundle(
            {"T1": t1_groundtruth(), "T2": t2_groundtruth(), "T3": truth},
            protocol_id="example_protocol",
            schema_root=SCHEMA_ROOT,
        )


def test_partial_duplex_pairing_must_be_reverse_complementary() -> None:
    t3 = t3_prediction()
    state = t3["workflows"][0]["states"][0]
    state.update(
        {
            "strand_architecture": "partially_duplex",
            "reference_strand_id": "top",
            "strands": [
                {
                    "strand_id": "top",
                    "name": "top",
                    "molecule_type": "DNA",
                    "orientation": "5_to_3",
                    "segments": [
                        {
                            "segment_id": "top_paired",
                            "role": "paired",
                            "structural_role": "paired_region",
                            "sequence": "ACGT",
                        },
                        {
                            "segment_id": "top_overhang",
                            "role": "overhang",
                            "structural_role": "three_prime_overhang",
                            "sequence": "AA",
                        },
                    ],
                },
                {
                    "strand_id": "bottom",
                    "name": "bottom",
                    "molecule_type": "DNA",
                    "orientation": "5_to_3",
                    "segments": [
                        {
                            "segment_id": "bottom_paired",
                            "role": "paired",
                            "structural_role": "paired_region",
                            "sequence": "AAAA",
                        }
                    ],
                },
            ],
            "paired_regions": [
                {
                    "paired_region_id": "pair",
                    "side_1": {"strand_id": "top", "segment_ids": ["top_paired"]},
                    "side_2": {
                        "strand_id": "bottom",
                        "segment_ids": ["bottom_paired"],
                    },
                    "relationship": "reverse_complementary",
                }
            ],
        }
    )
    validate_t3_prediction(t3, protocol_id="example_protocol", schema_root=SCHEMA_ROOT)
    with pytest.raises(LibgenValidationError, match="reverse-complementary"):
        validate_prediction_links(t2_prediction(), t3)


def test_verifier_cli_scores_valid_output_and_zeroes_invalid_prediction(
    tmp_path: Path,
) -> None:
    truth = tmp_path / "truth"
    truth.mkdir()
    for filename, document in (
        ("groundtruth_final_lib_struct.json", t1_groundtruth()),
        ("groundtruth_oligos.json", t2_groundtruth()),
        ("groundtruth_library_generation_workflow.json", t3_groundtruth()),
    ):
        (truth / filename).write_text(json.dumps(document))
    hashes = {
        filename: hashlib.sha256((truth / filename).read_bytes()).hexdigest()
        for filename in (
            "groundtruth_final_lib_struct.json",
            "groundtruth_oligos.json",
            "groundtruth_library_generation_workflow.json",
        )
    }
    t2_path = tmp_path / "t2.json"
    t3_path = tmp_path / "t3.json"
    t2_path.write_text(json.dumps(t2_prediction()))
    t3_path.write_text(json.dumps(t3_prediction()))
    reward = tmp_path / "reward.json"
    details = tmp_path / "details.json"
    error = tmp_path / "error.json"
    error_analysis = tmp_path / "error_analysis.json"
    common = [
        "--t2-prediction",
        str(t2_path),
        "--t3-prediction",
        str(t3_path),
        "--protocol-id",
        "example_protocol",
        "--groundtruth-dir",
        str(truth),
        "--schema-root",
        str(SCHEMA_ROOT),
        "--t1-sha256",
        hashes["groundtruth_final_lib_struct.json"],
        "--t2-sha256",
        hashes["groundtruth_oligos.json"],
        "--t3-sha256",
        hashes["groundtruth_library_generation_workflow.json"],
        "--reward-out",
        str(reward),
        "--details-out",
        str(details),
        "--error-out",
        str(error),
    ]
    assert grade_main(common) == 0
    reward_document = json.loads(reward.read_text())
    assert set(reward_document) == set(LIBGEN_PUBLIC_METRIC_KEYS)
    assert reward_document["reward"] == pytest.approx(1.0)
    assert reward_document["t2_required_family_f1"] == pytest.approx(1.0)
    assert reward_document["t2_exact_required_family_recall"] == pytest.approx(1.0)
    assert reward_document["t3_molecular_transition_f1"] == pytest.approx(1.0)
    assert reward_document["t3_state_f1"] == pytest.approx(1.0)
    assert reward_document["t3_typed_edge_f1"] == pytest.approx(1.0)
    details_document = json.loads(details.read_text())
    assert details_document["prediction_valid"] is True
    assert details_document["groundtruth_source"] == {
        "source": "local",
        "fallback_used": False,
        "primary_error": None,
    }
    assert details_document["scoring"]["diagnostic_metrics"]["t2"][
        "name_similarity"
    ] == pytest.approx(1.0)
    assert details_document["scoring"]["diagnostic_metrics"]["t3"][
        "boundary_f1"
    ] == pytest.approx(1.0)
    assert not error.exists()
    analysis_document = json.loads(error_analysis.read_text())
    assert analysis_document["schema_version"] == "libstruct.libgen_error_analysis.v2"
    assert analysis_document["run_outcome"] == "valid_prediction"
    assert analysis_document["summary"]["substantive_discrepancy_count"] == 0
    assert analysis_document["observations"] == []

    y_shaped_prediction = t3_prediction()
    y_shaped_prediction["workflows"][0]["states"][0] = _one_arm_y_shaped_state()
    t3_path.write_text(json.dumps(y_shaped_prediction))
    assert grade_main(common) == 0
    assert json.loads(details.read_text())["prediction_valid"] is True
    assert 0.0 < json.loads(reward.read_text())["reward"] < 1.0
    t3_path.write_text(json.dumps(t3_prediction()))

    t1_file = truth / "groundtruth_final_lib_struct.json"
    original_t1 = t1_file.read_bytes()
    t1_file.write_bytes(original_t1 + b"\n")
    assert grade_main(common) == 2
    assert json.loads(error.read_text())["kind"] == "verifier_configuration_error"
    assert json.loads(error_analysis.read_text())["run_outcome"] == "verifier_failure"
    t1_file.write_bytes(original_t1)

    t3_path.write_text("not json")
    assert grade_main(common) == 0
    invalid_reward = json.loads(reward.read_text())
    assert set(invalid_reward) == set(LIBGEN_PUBLIC_METRIC_KEYS)
    assert all(value == 0.0 for value in invalid_reward.values())
    assert json.loads(details.read_text())["prediction_valid"] is False
    assert json.loads(error.read_text())["kind"] == "invalid_prediction"
    invalid_analysis = json.loads(error_analysis.read_text())
    assert invalid_analysis["run_outcome"] == "invalid_prediction"
    assert (
        invalid_analysis["observations"][0]["category"]
        == "representation_or_schema_error"
    )


def test_verifier_uses_huggingface_first_and_local_truth_as_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = tmp_path / "truth"
    truth.mkdir()
    for filename, document in (
        ("groundtruth_final_lib_struct.json", t1_groundtruth()),
        ("groundtruth_oligos.json", t2_groundtruth()),
        ("groundtruth_library_generation_workflow.json", t3_groundtruth()),
    ):
        (truth / filename).write_text(json.dumps(document))
    hashes = {
        filename: hashlib.sha256((truth / filename).read_bytes()).hexdigest()
        for filename in (
            "groundtruth_final_lib_struct.json",
            "groundtruth_oligos.json",
            "groundtruth_library_generation_workflow.json",
        )
    }
    t2_path = tmp_path / "t2.json"
    t3_path = tmp_path / "t3.json"
    t2_path.write_text(json.dumps(t2_prediction()))
    t3_path.write_text(json.dumps(t3_prediction()))

    hf_attempts: list[str] = []

    def fail_hf_download(**kwargs: object) -> bytes:
        hf_attempts.append(str(kwargs["path"]))
        raise OSError("simulated verifier DNS failure")

    monkeypatch.setenv("HF_TOKEN", "fixture-token")
    monkeypatch.setattr(
        "libstruct_bench.cli.grade_libgen.download_hf_dataset_file",
        fail_hf_download,
    )
    reward = tmp_path / "reward.json"
    details = tmp_path / "details.json"
    assert (
        grade_main(
            [
                "--t2-prediction",
                str(t2_path),
                "--t3-prediction",
                str(t3_path),
                "--protocol-id",
                "example_protocol",
                "--groundtruth-repo",
                "org/private-groundtruth",
                "--groundtruth-revision",
                "a" * 40,
                "--groundtruth-dir",
                str(truth),
                "--schema-root",
                str(SCHEMA_ROOT),
                "--t1-sha256",
                hashes["groundtruth_final_lib_struct.json"],
                "--t2-sha256",
                hashes["groundtruth_oligos.json"],
                "--t3-sha256",
                hashes["groundtruth_library_generation_workflow.json"],
                "--reward-out",
                str(reward),
                "--details-out",
                str(details),
                "--error-analysis-out",
                str(tmp_path / "error_analysis.json"),
                "--error-out",
                str(tmp_path / "error.json"),
            ]
        )
        == 0
    )
    assert hf_attempts == ["example_protocol/groundtruth_final_lib_struct.json"]
    source = json.loads(details.read_text())["groundtruth_source"]
    assert source["source"] == "local_fallback"
    assert source["fallback_used"] is True
    assert "simulated verifier DNS failure" in source["primary_error"]
    assert json.loads(reward.read_text())["reward"] == pytest.approx(1.0)


def test_versioned_rescore_preserves_original_harbor_outputs(tmp_path: Path) -> None:
    groundtruth = tmp_path / "groundtruth" / "example_protocol"
    groundtruth.mkdir(parents=True)
    for filename, document in (
        ("groundtruth_final_lib_struct.json", t1_groundtruth()),
        ("groundtruth_oligos.json", t2_groundtruth()),
        ("groundtruth_library_generation_workflow.json", t3_groundtruth()),
    ):
        (groundtruth / filename).write_text(json.dumps(document))

    trial = tmp_path / "runs" / "job" / "example_protocol__abc"
    artifacts = trial / "artifacts" / "logs" / "artifacts"
    verifier = trial / "verifier"
    artifacts.mkdir(parents=True)
    verifier.mkdir()
    (artifacts / "t2_prediction.json").write_text(json.dumps(t2_prediction()))
    (artifacts / "t3_prediction.json").write_text(json.dumps(t3_prediction()))
    original_reward = {
        "reward": 0.25,
        "t2_required_sequence_f1": 0.25,
    }
    original_bytes = json.dumps(original_reward).encode()
    (verifier / "reward.json").write_bytes(original_bytes)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "task_name": "sequencing/libgen-example_protocol",
            }
        )
    )

    assert (
        rescore_main(
            [
                "--runs-root",
                str(tmp_path / "runs"),
                "--groundtruth-root",
                str(tmp_path / "groundtruth"),
                "--schema-root",
                str(SCHEMA_ROOT),
            ]
        )
        == 0
    )

    assert (verifier / "reward.json").read_bytes() == original_bytes
    versioned = verifier / "rescore" / f"libgen-{LIBGEN_BENCHMARK_VERSION}"
    rescored_reward = json.loads((versioned / "reward.json").read_text())
    assert tuple(rescored_reward) == tuple(sorted(LIBGEN_PUBLIC_METRIC_KEYS))
    assert rescored_reward["reward"] == pytest.approx(1.0)
    summary = json.loads(
        (
            trial.parent
            / "rescore"
            / f"libgen-{LIBGEN_BENCHMARK_VERSION}"
            / "summary.json"
        ).read_text()
    )
    assert summary["benchmark_version"] == LIBGEN_BENCHMARK_VERSION
    assert summary["trial_count"] == 1
    assert summary["valid_prediction_count"] == 1
    assert summary["trials"][0]["original_reward"] == 0.25
    assert summary["trials"][0]["reward_delta"] == pytest.approx(0.75)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        rescore_main(
            [
                "--runs-root",
                str(tmp_path / "runs"),
                "--groundtruth-root",
                str(tmp_path / "groundtruth"),
                "--schema-root",
                str(SCHEMA_ROOT),
            ]
        )


def test_versioned_rescore_ignores_telemetry_snapshots(tmp_path: Path) -> None:
    groundtruth = tmp_path / "groundtruth" / "example_protocol"
    groundtruth.mkdir(parents=True)
    for filename, document in (
        ("groundtruth_final_lib_struct.json", t1_groundtruth()),
        ("groundtruth_oligos.json", t2_groundtruth()),
        ("groundtruth_library_generation_workflow.json", t3_groundtruth()),
    ):
        (groundtruth / filename).write_text(json.dumps(document))

    job = tmp_path / "runs" / "job"
    trial = job / "example_protocol__authoritative"
    artifacts = trial / "artifacts" / "logs" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "t2_prediction.json").write_text(json.dumps(t2_prediction()))
    (artifacts / "t3_prediction.json").write_text(json.dumps(t3_prediction()))
    result = {
        "trial_name": trial.name,
        "task_name": "sequencing/libgen-example_protocol",
    }
    (trial / "result.json").write_text(json.dumps(result))

    snapshot = job / ".libgen_telemetry" / "resume_snapshots" / "snapshot"
    snapshot_trial = snapshot / "example_protocol__snapshot"
    snapshot_trial.mkdir(parents=True)
    (snapshot_trial / "result.json").write_text(
        json.dumps({**result, "trial_name": snapshot_trial.name})
    )

    assert (
        rescore_main(
            [
                "--runs-root",
                str(job),
                "--groundtruth-root",
                str(tmp_path / "groundtruth"),
                "--schema-root",
                str(SCHEMA_ROOT),
            ]
        )
        == 0
    )

    summary = json.loads(
        (
            job
            / "rescore"
            / f"libgen-{LIBGEN_BENCHMARK_VERSION}"
            / "summary.json"
        ).read_text()
    )
    assert summary["trial_count"] == 1
    assert summary["trials"][0]["trial_name"] == trial.name
    assert not (snapshot_trial / "verifier" / "rescore").exists()


def test_single_trial_rescore_writes_immutable_local_summary(tmp_path: Path) -> None:
    groundtruth = tmp_path / "groundtruth" / "example_protocol"
    groundtruth.mkdir(parents=True)
    legacy_t3 = t3_groundtruth()
    legacy_workflow = legacy_t3["workflows"][0]
    final_outputs = legacy_workflow.pop("final_outputs")
    legacy_workflow["modality"] = final_outputs[0]["modality"]
    legacy_workflow["final_state_ids"] = [item["state_id"] for item in final_outputs]
    for filename, document in (
        ("groundtruth_final_lib_struct.json", t1_groundtruth()),
        ("groundtruth_oligos.json", t2_groundtruth()),
        ("groundtruth_library_generation_workflow.json", legacy_t3),
    ):
        (groundtruth / filename).write_text(json.dumps(document))

    trial = tmp_path / "runs" / "job" / "example_protocol__local"
    artifacts = trial / "artifacts" / "logs" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "t2_prediction.json").write_text(json.dumps(t2_prediction()))
    (artifacts / "t3_prediction.json").write_text(json.dumps(t3_prediction()))
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial_name": trial.name,
                "task_name": "sequencing/libgen-example_protocol",
            }
        )
    )

    assert (
        rescore_main(
            [
                "--runs-root",
                str(trial),
                "--groundtruth-root",
                str(tmp_path / "groundtruth"),
                "--schema-root",
                str(SCHEMA_ROOT),
            ]
        )
        == 0
    )

    versioned = trial / "verifier" / "rescore" / f"libgen-{LIBGEN_BENCHMARK_VERSION}"
    summary = json.loads((versioned / "summary.json").read_text())
    assert summary["trial_count"] == 1
    assert summary["trials"][0]["trial_name"] == trial.name
    assert summary["trials"][0]["groundtruth_transform"] == (
        "legacy_workflow_terminal_contract_to_final_outputs_v1"
    )
    effective_t3 = json.loads(
        (
            versioned
            / "effective_groundtruth"
            / "groundtruth_library_generation_workflow.json"
        ).read_text()
    )
    assert effective_t3["workflows"][0]["final_outputs"] == final_outputs
    assert not (
        trial.parent / "rescore" / f"libgen-{LIBGEN_BENCHMARK_VERSION}" / "summary.json"
    ).exists()
