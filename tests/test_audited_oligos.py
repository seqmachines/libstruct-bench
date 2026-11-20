from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from libstruct_bench.audited_oligos import (
    grade_audited_oligo_prediction,
    parse_audited_prediction,
)
from libstruct_bench.cli.grade_oligos import main as grade_main
from libstruct_bench.schema import PredictionValidationError


REPO_ROOT = Path(__file__).resolve().parents[1]


def _prediction(oligos: list[dict]) -> dict:
    return {
        "schema_version": "libstruct.oligo_extraction.v2",
        "protocol_id": "example_protocol",
        "oligos": oligos,
    }


def _predicted_oligo(
    name: str, sequence: str, direction: str = "5_to_3"
) -> dict:
    return {"name": name, "sequence": sequence, "direction": direction}


def _truth(oligos: list[dict]) -> dict:
    values = []
    for index, oligo in enumerate(oligos, start=1):
        values.append(
            {
                "oligo_id": oligo.get("oligo_id", f"oligo-{index}"),
                "name": oligo["name"],
                "source_names": [oligo["name"]],
                "aliases": [],
                "role": "primer",
                "kind": oligo.get("kind", "single"),
                "sequence": oligo.get("sequence"),
                "direction": oligo.get("direction", "5_to_3"),
                "components": oligo.get("components", []),
                "modifications": [],
                "benchmark_status": oligo.get("benchmark_status", "included"),
                **(
                    {"exclusion_reason": oligo["exclusion_reason"]}
                    if "exclusion_reason" in oligo
                    else {}
                ),
                "support_status": "explicit",
                "evidence": [
                    {"source_id": "primary-paper", "locator": {"page": 1}}
                ],
                "baseline_lineage": [],
            }
        )
    return {
        "schema_version": "libstruct.oligo_groundtruth.v1",
        "protocol_id": "example_protocol",
        "protocol_name": "Example protocol",
        "oligos": values,
    }


def test_audited_t2_exact_sequence_match() -> None:
    prediction = _prediction(
        [_predicted_oligo("RT primer", "ACGT[CELL_BARCODE:4][UMI:2]")]
    )
    truth = _truth(
        [
            {
                "name": "RT primer",
                "sequence": "ACGT[4-bp cell barcode][2-bp UMI]",
            }
        ]
    )
    metrics, audit = grade_audited_oligo_prediction(prediction, truth)
    assert metrics["reward"] == 1.0
    assert metrics["direction_accuracy"] == 1.0
    assert audit["excluded_ground_truth_count"] == 0


def test_excluded_ambiguous_oligo_is_not_scored() -> None:
    truth = _truth(
        [
            {"name": "Included", "sequence": "AAAA"},
            {
                "name": "Ambiguous",
                "sequence": "CCCC",
                "benchmark_status": "excluded",
                "exclusion_reason": "Top and bottom strand labels conflict.",
            },
        ]
    )
    metrics, audit = grade_audited_oligo_prediction(
        _prediction([_predicted_oligo("Included", "AAAA")]), truth
    )
    assert metrics["ground_truth_count"] == 1.0
    assert metrics["reward"] == 1.0
    assert audit["excluded_ground_truth_count"] == 1


def test_direction_is_recorded_as_a_diagnostic_not_silently_normalized() -> None:
    metrics, audit = grade_audited_oligo_prediction(
        _prediction([_predicted_oligo("Primer", "AAAA", "3_to_5")]),
        _truth([{"name": "Primer", "sequence": "AAAA", "direction": "5_to_3"}]),
    )
    assert metrics["sequence_f1"] == 1.0
    assert metrics["direction_accuracy"] == 0.0
    assert audit["matches"][0]["predicted_direction"] == "3_to_5"


def test_prediction_requires_explicit_valid_direction() -> None:
    with pytest.raises(PredictionValidationError, match="direction"):
        parse_audited_prediction(
            _prediction([{"name": "Primer", "sequence": "AAAA"}])
        )


def test_rejects_legacy_groundtruth() -> None:
    with pytest.raises(ValueError, match="ground_truth.schema_version"):
        grade_audited_oligo_prediction(
            _prediction([_predicted_oligo("Primer", "AAAA")]),
            {"protocol_id": "example_protocol", "oligos": []},
        )


def test_cli_returns_numeric_zero_for_legacy_groundtruth(tmp_path: Path) -> None:
    prediction_path = tmp_path / "prediction.json"
    truth_path = tmp_path / "truth.json"
    reward_path = tmp_path / "reward.json"
    audit_path = tmp_path / "audit.json"
    prediction_path.write_text(
        json.dumps(_prediction([_predicted_oligo("Primer", "AAAA")])),
        encoding="utf-8",
    )
    truth_path.write_text(
        json.dumps({"protocol_id": "example_protocol", "oligos": []}),
        encoding="utf-8",
    )
    assert (
        grade_main(
            [
                "--prediction",
                str(prediction_path),
                "--groundtruth",
                str(truth_path),
                "--protocol-id",
                "example_protocol",
                "--reward-out",
                str(reward_path),
                "--audit-out",
                str(audit_path),
            ]
        )
        == 0
    )
    assert json.loads(reward_path.read_text(encoding="utf-8"))["schema_valid"] == 0.0


def test_new_prediction_schemas_are_valid_draft_2020_12() -> None:
    for name in (
        "library_structure_prediction.v1.schema.json",
        "oligo_extraction_prediction.v2.schema.json",
    ):
        schema = json.loads((REPO_ROOT / "schemas" / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
