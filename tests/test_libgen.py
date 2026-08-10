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
    derive_required_t2_ids,
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

    t3_schema = json.loads(
        (
            SCHEMA_ROOT
            / "benchmark"
            / "library_generation_workflow_prediction.schema.json"
        ).read_text()
    )
    assert "modality" not in t3_schema["properties"]
    assert "modality" in t3_schema["$defs"]["workflow"]["required"]


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
    metrics, _ = score_t2(
        missing_t2["oligos"],
        t2_groundtruth()["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert metrics["required_sequence_f1"] < 1.0

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
    assert metrics["t3_score"] == pytest.approx(1.0)


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
    assert omitted_metrics["required_sequence_f1"] == pytest.approx(1.0)
    assert included_metrics["required_sequence_f1"] == pytest.approx(1.0)


def test_t3_referenced_oligo_is_required_regardless_of_support_status() -> None:
    truth = t2_groundtruth()
    truth["oligos"][0]["support_status"] = "externally_completed"

    metrics, details = score_t2(
        [], truth["oligos"], required_oligo_ids={"oligo_rt"}
    )

    assert metrics["required_groundtruth_count"] == 1.0
    assert metrics["required_sequence_recall"] == 0.0
    assert details["unmatched_required_oligo_ids"] == ["oligo_rt"]


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


def test_missing_required_oligo_lowers_required_sequence_f1() -> None:
    metrics, details = score_t2(
        [], t2_groundtruth()["oligos"], required_oligo_ids={"oligo_rt"}
    )
    assert metrics["required_sequence_f1"] == 0.0
    assert metrics["all_required_exact"] == 0.0
    assert details["unmatched_required_oligo_ids"] == ["oligo_rt"]


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
    assert metrics["required_sequence_f1"] == pytest.approx(1.0)
    assert metrics["all_required_exact"] == 1.0
    assert details["neutralized_prediction_indices"] == [1]


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
    assert metrics["required_sequence_precision"] < 1.0
    assert metrics["required_sequence_recall"] == pytest.approx(1.0)
    assert metrics["all_required_exact"] == 0.0


def test_duplicate_identical_prediction_counts_as_an_extra() -> None:
    prediction = t2_prediction()
    duplicate = copy.deepcopy(prediction["oligos"][0])
    duplicate["oligo_id"] = "duplicate_rt"
    prediction["oligos"].append(duplicate)
    metrics, _ = score_t2(
        prediction["oligos"],
        t2_groundtruth()["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert metrics["required_sequence_recall"] == pytest.approx(1.0)
    assert metrics["required_sequence_precision"] == pytest.approx(0.5)
    assert metrics["required_sequence_f1"] < 1.0


def test_t2_metadata_does_not_affect_assignment_or_reward() -> None:
    prediction = t2_prediction()
    prediction["oligos"][0].update(
        {
            "name": "unrelated name",
            "aliases": ["unrelated alias"],
            "role": "unrelated role",
            "orientation": "3_to_5",
            "modifications": ["unknown modification"],
        }
    )
    metrics, _ = score_t2(
        prediction["oligos"],
        t2_groundtruth()["oligos"],
        required_oligo_ids={"oligo_rt"},
    )
    assert metrics["required_sequence_f1"] == pytest.approx(1.0)


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
    metrics, _ = grade_libgen(
        t2_prediction(), t3_prediction(), t2_groundtruth(), truth
    )
    assert metrics["t3_score"] == pytest.approx(1.0)


def _rename_workflow_ids(workflow: dict, suffix: str) -> dict:
    result = copy.deepcopy(workflow)
    state_ids = {
        state["state_id"]: f"{state['state_id']}_{suffix}"
        for state in result["states"]
    }
    transition_ids = {
        transition["transition_id"]: f"{transition['transition_id']}_{suffix}"
        for transition in result["transitions"]
    }
    for state in result["states"]:
        state["state_id"] = state_ids[state["state_id"]]
        strand_ids = {
            strand["strand_id"]: f"{strand['strand_id']}_{suffix}"
            for strand in state["strands"]
        }
        segment_ids = {
            segment["segment_id"]: f"{segment['segment_id']}_{suffix}"
            for strand in state["strands"]
            for segment in strand["segments"]
        }
        state["reference_strand_id"] = strand_ids[state["reference_strand_id"]]
        for strand in state["strands"]:
            strand["strand_id"] = strand_ids[strand["strand_id"]]
            for segment in strand["segments"]:
                segment["segment_id"] = segment_ids[segment["segment_id"]]
        for region in state["paired_regions"]:
            region["paired_region_id"] = f"{region['paired_region_id']}_{suffix}"
            for side_name in ("side_1", "side_2"):
                side = region[side_name]
                side["strand_id"] = strand_ids[side["strand_id"]]
                side["segment_ids"] = [segment_ids[item] for item in side["segment_ids"]]
        for discontinuity in state["discontinuities"]:
            discontinuity["discontinuity_id"] = (
                f"{discontinuity['discontinuity_id']}_{suffix}"
            )
            discontinuity["strand_id"] = strand_ids[discontinuity["strand_id"]]
            discontinuity["after_segment_id"] = segment_ids[
                discontinuity["after_segment_id"]
            ]
            discontinuity["before_segment_id"] = segment_ids[
                discontinuity["before_segment_id"]
            ]
    for transition in result["transitions"]:
        transition["transition_id"] = transition_ids[transition["transition_id"]]
        for field in (
            "substrate_state_ids",
            "product_state_ids",
            "carried_forward_product_ids",
            "discarded_product_ids",
        ):
            transition[field] = [state_ids[item] for item in transition[field]]
    result["workflow_id"] = f"{result['workflow_id']}_{suffix}"
    result["initial_state_ids"] = [state_ids[item] for item in result["initial_state_ids"]]
    result["final_state_ids"] = [state_ids[item] for item in result["final_state_ids"]]
    return result


def _multimodal_t3() -> dict:
    result = t3_groundtruth()
    atac = _rename_workflow_ids(result["workflows"][0], "atac")
    atac["modality"] = "ATAC"
    atac["transitions"][0]["operation"] = "tagmentation"
    input_state, final_state = atac["states"]
    input_state["name"] = "chromatin input"
    input_state["molecule_type"] = "DNA"
    input_state["strands"][0]["molecule_type"] = "DNA"
    input_state["strands"][0]["sequence_architecture"] = "[GDNA]"
    input_segment = input_state["strands"][0]["segments"][0]
    input_segment.pop("sequence", None)
    input_segment["placeholder"] = "[GDNA]"
    final_state["name"] = "ATAC library"
    final_state["strands"][0]["sequence_architecture"] = "TTTT[GDNA]"
    final_segment = final_state["strands"][0]["segments"][0]
    final_segment["sequence"] = "TTTT[GDNA]"
    final_segment.pop("placeholder", None)
    result["workflows"].append(atac)
    return result


def test_multimodal_workflows_score_independently_by_modality() -> None:
    truth = _multimodal_t3()
    prediction = copy.deepcopy(truth)
    for workflow in prediction["workflows"]:
        for state in workflow["states"]:
            state.pop("support_status", None)
            for strand in state["strands"]:
                strand.pop("support_status", None)
        for transition in workflow["transitions"]:
            transition.pop("support_status", None)
    metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )
    assert metrics["t3_molecular_transition_f1"] == pytest.approx(1.0)
    assert metrics["t3_typed_edge_f1"] == pytest.approx(1.0)


def test_modality_swapped_across_graphs_lowers_transition_score() -> None:
    truth = _multimodal_t3()
    prediction = copy.deepcopy(truth)
    prediction["workflows"][0]["modality"] = "ATAC"
    prediction["workflows"][1]["modality"] = "RNA"
    metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )
    assert metrics["t3_molecular_transition_f1"] < 1.0


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
    prediction["workflows"][0]["transitions"][0]["substrate_state_ids"] = [
        "state_cdna"
    ]
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
    workflow["final_state_ids"].append("state_branch")
    return result


def test_one_missing_graph_branch_lowers_transition_and_edge_recall() -> None:
    truth = _branched_t3()
    prediction = t3_prediction()
    metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )
    assert metrics["t3_molecular_transition_recall"] < 1.0
    assert metrics["t3_typed_edge_f1"] < 1.0


def test_human_approved_transition_is_scored_regardless_of_support_status() -> None:
    truth = t3_groundtruth()
    truth["workflows"][0]["transitions"][0]["support_status"] = "ambiguous"
    prediction = t3_prediction()
    prediction["workflows"][0]["transitions"] = []

    metrics, _ = grade_libgen(
        t2_prediction(), prediction, t2_groundtruth(), truth
    )

    assert metrics["t3_molecular_transition_recall"] == 0.0
    assert metrics["t3_typed_edge_f1"] == 0.0


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
    reward_document = json.loads(reward.read_text())
    assert reward_document["reward"] == pytest.approx(1.0)
    assert reward_document["t2_required_sequence_f1"] == pytest.approx(1.0)
    assert reward_document["t2_all_required_exact"] == pytest.approx(1.0)
    assert reward_document["t3_molecular_transition_f1"] == pytest.approx(1.0)
    assert reward_document["t3_typed_edge_f1"] == pytest.approx(1.0)
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
