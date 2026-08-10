from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from libstruct_bench.cli.grade_libgen import main as grade_main
from libstruct_bench.libgen.scoring import grade_libgen, score_t2
from libstruct_bench.libgen.validation import (
    LibgenValidationError,
    validate_prediction_links,
    validate_t2_prediction,
    validate_t3_prediction,
)
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


def test_exact_truth_projection_scores_one() -> None:
    metrics, _ = grade_libgen(
        t2_prediction(), t3_prediction(), t2_groundtruth(), t3_groundtruth()
    )
    assert metrics["reward"] == pytest.approx(1.0)
    assert metrics["t2_score"] == pytest.approx(1.0)
    assert metrics["t3_score"] == pytest.approx(1.0)


def test_ids_and_collection_order_do_not_affect_score() -> None:
    t2, t3 = renamed_predictions()
    validate_t2_prediction(t2, protocol_id="example_protocol", schema_root=SCHEMA_ROOT)
    validate_t3_prediction(t3, protocol_id="example_protocol", schema_root=SCHEMA_ROOT)
    validate_prediction_links(t2, t3)
    metrics, _ = grade_libgen(t2, t3, t2_groundtruth(), t3_groundtruth())
    assert metrics["reward"] == pytest.approx(1.0)


def test_missing_and_extra_entities_lower_scores() -> None:
    missing_t2 = t2_prediction()
    missing_t2["oligos"] = []
    metrics, _ = grade_libgen(
        missing_t2, t3_prediction(), t2_groundtruth(), t3_groundtruth()
    )
    assert metrics["t2_score"] < 1.0

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
    assert metrics["t3_score"] < 1.0


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

    omitted_metrics, _, _ = score_t2(t2_prediction()["oligos"], truth["oligos"])
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
    included_metrics, _, _ = score_t2(included["oligos"], truth["oligos"])
    assert omitted_metrics["sequence_f1"] == pytest.approx(1.0)
    assert included_metrics["sequence_f1"] == pytest.approx(1.0)


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
    metrics, _ = grade_libgen(
        t2_prediction(), t3_prediction(), t2_groundtruth(), truth
    )
    assert metrics["t3_score"] == pytest.approx(1.0)


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
    cyclic["workflows"][0]["final_state_ids"] = ["state_input"]
    second = copy.deepcopy(transition)
    second["transition_id"] = "transition_cycle"
    second["substrate_state_ids"] = ["state_input"]
    second["product_state_ids"] = ["state_cdna"]
    second["carried_forward_product_ids"] = ["state_cdna"]
    cyclic["workflows"][0]["transitions"].append(second)
    with pytest.raises(LibgenValidationError, match="cycle"):
        validate_prediction_links(t2, cyclic)


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


def test_verifier_cli_scores_valid_output_and_zeroes_invalid_prediction(tmp_path: Path) -> None:
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
    assert json.loads(reward.read_text())["reward"] == pytest.approx(1.0)
    assert not error.exists()

    t1_file = truth / "groundtruth_final_lib_struct.json"
    original_t1 = t1_file.read_bytes()
    t1_file.write_bytes(original_t1 + b"\n")
    assert grade_main(common) == 2
    assert json.loads(error.read_text())["kind"] == "verifier_configuration_error"
    t1_file.write_bytes(original_t1)

    t3_path.write_text("not json")
    assert grade_main(common) == 0
    assert json.loads(reward.read_text())["reward"] == 0.0
    assert json.loads(error.read_text())["kind"] == "invalid_prediction"
