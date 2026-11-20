from __future__ import annotations

import json
from pathlib import Path

import pytest

from libstruct_bench.audited_library import (
    AuditedLibraryValidationError,
    grade_audited_library_prediction,
)
from libstruct_bench.cli.grade_library import main as grade_main


def _prediction(libraries: list[dict]) -> dict:
    return {
        "schema_version": "libstruct.library_structure.v1",
        "protocol_id": "example_protocol",
        "libraries": libraries,
    }


def _truth(libraries: list[dict]) -> dict:
    return {
        "schema_version": "libstruct.final_library_groundtruth.v1",
        "protocol_id": "example_protocol",
        "protocol_name": "Example protocol",
        "libraries": [
            {
                **library,
                "benchmark_status": library.get("benchmark_status", "included"),
                "support_status": "explicit",
                "evidence": [
                    {"source_id": "primary-paper", "locator": {"page": 1}}
                ],
                "segments": [],
            }
            for library in libraries
        ],
    }


def _library(library_id: str, modality: str, sequence: str) -> dict:
    return {
        "library_id": library_id,
        "modality": modality,
        "library_sequence": sequence,
    }


def test_exact_match_scores_one() -> None:
    libraries = [_library("rna", "RNA", "AA[CELL_BARCODE:2]TT")]
    metrics, audit = grade_audited_library_prediction(
        _prediction(libraries),
        _truth(libraries),
        expected_protocol_id="example_protocol",
    )
    assert metrics["reward"] == 1.0
    assert metrics["exact_match"] == 1.0
    assert audit["library_matches"][0]["matched_by"] == "library_id"


def test_global_assignment_avoids_greedy_target_mismatch() -> None:
    prediction = _prediction(
        [_library("p1", "pred-one", "AC"), _library("p2", "pred-two", "ACC")]
    )
    truth = _truth(
        [_library("t1", "truth-one", "AA"), _library("t2", "truth-two", "AC")]
    )
    metrics, audit = grade_audited_library_prediction(prediction, truth)
    matches = audit["library_matches"]
    assert [item["predicted_library_id"] for item in matches] == ["p2", "p1"]
    assert all(item["matched_by"] == "global_sequence" for item in matches)
    assert metrics["sequence_recall"] == pytest.approx((1 / 3 + 1) / 2)


def test_unique_library_ids_lock_the_scientific_target() -> None:
    prediction = _prediction(
        [_library("rna", "RNA", "CCCC"), _library("atac", "ATAC", "AAAA")]
    )
    truth = _truth(
        [_library("rna", "RNA", "AAAA"), _library("atac", "ATAC", "CCCC")]
    )
    metrics, audit = grade_audited_library_prediction(prediction, truth)
    assert [item["predicted_library_id"] for item in audit["library_matches"]] == [
        "rna",
        "atac",
    ]
    assert metrics["reward"] == 0.0


def test_extra_library_reduces_soft_precision_and_reward() -> None:
    truth = _truth([_library("rna", "RNA", "AAAA")])
    prediction = _prediction(
        [_library("rna", "RNA", "AAAA"), _library("extra", "other", "CCCC")]
    )
    metrics, _ = grade_audited_library_prediction(prediction, truth)
    assert metrics["sequence_precision"] == 0.5
    assert metrics["sequence_recall"] == 1.0
    assert metrics["reward"] == pytest.approx(2 / 3)


def test_explicitly_excluded_groundtruth_library_is_not_scored() -> None:
    included = _library("rna", "RNA", "AAAA")
    excluded = {
        **_library("ambiguous", "other", "CCCC"),
        "benchmark_status": "excluded",
        "exclusion_reason": "Primary sources conflict.",
    }
    metrics, _ = grade_audited_library_prediction(
        _prediction([included]), _truth([included, excluded])
    )
    assert metrics["ground_truth_library_count"] == 1.0
    assert metrics["reward"] == 1.0


def test_rejects_legacy_prediction_and_groundtruth_contracts() -> None:
    with pytest.raises(AuditedLibraryValidationError, match="schema_version"):
        grade_audited_library_prediction(
            {
                "schema_version": "libstruct.library_structure.v0",
                "protocol_id": "example_protocol",
                "library_sequence": "AAAA",
            },
            _truth([_library("rna", "RNA", "AAAA")]),
        )
    with pytest.raises(AuditedLibraryValidationError, match="ground_truth.schema_version"):
        grade_audited_library_prediction(
            _prediction([_library("rna", "RNA", "AAAA")]),
            {"protocol_id": "example_protocol", "libraries": []},
        )


def test_duplicate_normalized_library_ids_are_rejected() -> None:
    with pytest.raises(AuditedLibraryValidationError, match="duplicate normalized"):
        grade_audited_library_prediction(
            _prediction(
                [_library("RNA-1", "RNA", "AAAA"), _library("rna_1", "RNA", "CCCC")]
            ),
            _truth([_library("rna", "RNA", "AAAA")]),
        )


def test_cli_writes_numeric_zero_for_legacy_groundtruth(tmp_path: Path) -> None:
    prediction_path = tmp_path / "prediction.json"
    truth_path = tmp_path / "truth.json"
    reward_path = tmp_path / "reward.json"
    audit_path = tmp_path / "audit.json"
    prediction_path.write_text(
        json.dumps(_prediction([_library("rna", "RNA", "AAAA")])),
        encoding="utf-8",
    )
    truth_path.write_text(
        json.dumps({"protocol_id": "example_protocol", "library_sequence": "AAAA"}),
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
    assert json.loads(reward_path.read_text(encoding="utf-8"))[
        "prediction_parse_valid"
    ] == 0.0
