import json
import tempfile
import unittest
from pathlib import Path

from libstruct_bench.cli.grade_library_v0 import main as grade_main
from libstruct_bench.library_structure import (
    PREDICTION_SCHEMA_VERSION,
    LibraryStructureValidationError,
    grade_library_prediction,
    normalize_library_sequence,
    parse_prediction_document,
)


def prediction(sequence: str):
    return {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "protocol_id": "example_protocol",
        "library_sequence": sequence,
    }


GROUND_TRUTH = {
    "protocol_id": "example_protocol",
    "library_sequence": "AACCGG###~~~TT",
}


class LibraryStructureTests(unittest.TestCase):
    def test_exact_match_scores_one(self):
        metrics, audit = grade_library_prediction(
            prediction("AACCGG###~~~TT"),
            GROUND_TRUTH,
            expected_protocol_id="example_protocol",
        )

        self.assertEqual(metrics["sequence_similarity"], 1.0)
        self.assertEqual(metrics["reward"], 1.0)
        self.assertEqual(metrics["edit_distance"], 0.0)
        self.assertEqual(audit["library_matches"][0]["edit_distance"], 0)

    def test_normalizes_whitespace_and_case(self):
        self.assertEqual(normalize_library_sequence(" aa cc\nBB "), "AACCBB")
        metrics, _ = grade_library_prediction(
            prediction("aa cc gg\n###~~~ tt"),
            GROUND_TRUTH,
            expected_protocol_id="example_protocol",
        )
        self.assertEqual(metrics["sequence_similarity"], 1.0)

    def test_multimodal_libraries_score_separately(self):
        ground_truth = {
            "protocol_id": "example_protocol",
            "libraries": [
                {"library_id": "rna", "modality": "RNA", "library_sequence": "AA[CELL_BARCODE:2]TT"},
                {"library_id": "atac", "modality": "ATAC", "library_sequence": "GG[SAMPLE_INDEX:2]CC"},
            ],
        }
        pred = {
            "schema_version": PREDICTION_SCHEMA_VERSION,
            "protocol_id": "example_protocol",
            "libraries": [
                {"library_id": "atac", "modality": "ATAC", "library_sequence": "GG@@CC"},
                {"library_id": "rna", "modality": "RNA", "library_sequence": "AA##TA"},
            ],
        }

        metrics, audit = grade_library_prediction(pred, ground_truth, expected_protocol_id="example_protocol")

        self.assertEqual(metrics["library_count"], 2.0)
        self.assertAlmostEqual(metrics["sequence_similarity"], (1.0 + (1 - 1 / 6)) / 2)
        self.assertAlmostEqual(metrics["matched_sequence_similarity"], (1.0 + (1 - 1 / 6)) / 2)
        self.assertEqual(metrics["library_recall"], 1.0)
        self.assertEqual(metrics["library_precision"], 1.0)
        self.assertEqual(metrics["library_f1"], 1.0)
        self.assertEqual([match["library_id"] for match in audit["library_matches"]], ["rna", "atac"])

    def test_missing_library_keeps_strict_score_zero_but_reports_matched_diagnostic(self):
        ground_truth = {
            "protocol_id": "example_protocol",
            "libraries": [
                {"library_id": "rna", "modality": "RNA", "library_sequence": "AA##TT"},
                {"library_id": "sgrna", "modality": "sgRNA", "library_sequence": "GG$$CC"},
            ],
        }
        pred = {
            "schema_version": PREDICTION_SCHEMA_VERSION,
            "protocol_id": "example_protocol",
            "libraries": [
                {"library_id": "rna", "modality": "RNA", "library_sequence": "AA##TT"},
            ],
        }

        metrics, audit = grade_library_prediction(pred, ground_truth, expected_protocol_id="example_protocol")

        self.assertEqual(metrics["matched_library_count"], 1.0)
        self.assertEqual(metrics["ground_truth_library_count"], 2.0)
        self.assertEqual(metrics["predicted_library_count"], 1.0)
        self.assertEqual(metrics["sequence_similarity"], 0.5)
        self.assertEqual(metrics["matched_sequence_similarity"], 1.0)
        self.assertEqual(metrics["library_recall"], 0.5)
        self.assertEqual(metrics["library_precision"], 1.0)
        self.assertAlmostEqual(metrics["library_f1"], 2 / 3)
        self.assertIsNone(audit["library_matches"][1]["predicted_index"])

    def test_extra_prediction_reduces_library_precision(self):
        ground_truth = {
            "protocol_id": "example_protocol",
            "libraries": [
                {"library_id": "rna", "modality": "RNA", "library_sequence": "AA##TT"},
            ],
        }
        pred = {
            "schema_version": PREDICTION_SCHEMA_VERSION,
            "protocol_id": "example_protocol",
            "libraries": [
                {"library_id": "rna", "modality": "RNA", "library_sequence": "AA##TT"},
                {"library_id": "extra", "modality": "extra", "library_sequence": "CCCC"},
            ],
        }

        metrics, _ = grade_library_prediction(pred, ground_truth, expected_protocol_id="example_protocol")

        self.assertEqual(metrics["sequence_similarity"], 1.0)
        self.assertEqual(metrics["matched_sequence_similarity"], 1.0)
        self.assertEqual(metrics["library_recall"], 1.0)
        self.assertEqual(metrics["library_precision"], 0.5)
        self.assertAlmostEqual(metrics["library_f1"], 2 / 3)

    def test_expands_canonical_placeholders_for_scoring(self):
        self.assertEqual(
            normalize_library_sequence("AA[CELL_BARCODE:3][UMI:4][SAMPLE_INDEX:2]"),
            "AA###~~~~@@",
        )
        metrics, _ = grade_library_prediction(
            prediction("AACC GG[CELL_BARCODE:3][UMI:3]TT"),
            GROUND_TRUTH,
            expected_protocol_id="example_protocol",
        )
        self.assertEqual(metrics["sequence_similarity"], 1.0)

    def test_expands_source_placeholder_terms_for_scoring(self):
        cases = {
            "[16-bp cell barcode]": "#" * 16,
            "[8-bp UMI]": "~" * 8,
            "[8-bp i5 index]": "@" * 8,
            "[8-bp sample index]": "@" * 8,
            "[10-bp RT barcode]": "=" * 10,
            "[8-bp Tn5 index]": "%" * 8,
            "[10-bp N5 barcode]": "%" * 10,
            "[15-bp FB]": "$" * 15,
            "[0-4 bp PB]": "?" * 4,
            "[None/T/GT/TGA]": "?" * 3,
            "[None/A/TA/GTA/NNNNNNNN]": "?" * 8,
            "[random 9-mer]": "?" * 9,
        }
        for raw, expanded in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_library_sequence(raw), expanded)
        self.assertEqual(normalize_library_sequence("[Illumina P5]"), "[ILLUMINAP5]")

    def test_one_edit_similarity(self):
        metrics, _ = grade_library_prediction(
            prediction("AACCGG###~~~TA"),
            GROUND_TRUTH,
            expected_protocol_id="example_protocol",
        )
        self.assertAlmostEqual(metrics["sequence_similarity"], 1 - 1 / 14)
        self.assertEqual(metrics["edit_distance"], 1.0)

    def test_prediction_requires_schema_version(self):
        with self.assertRaises(LibraryStructureValidationError):
            parse_prediction_document(
                {"protocol_id": "example_protocol", "library_sequence": "ACGT"},
                expected_protocol_id="example_protocol",
            )

    def test_cli_writes_zero_metrics_for_invalid_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prediction_path = tmp_path / "prediction.json"
            groundtruth_path = tmp_path / "groundtruth.json"
            reward_path = tmp_path / "reward.json"
            audit_path = tmp_path / "audit.json"
            prediction_path.write_text(json.dumps({"protocol_id": "example_protocol"}), encoding="utf-8")
            groundtruth_path.write_text(json.dumps(GROUND_TRUTH), encoding="utf-8")

            self.assertEqual(
                grade_main(
                    [
                        "--prediction",
                        str(prediction_path),
                        "--groundtruth",
                        str(groundtruth_path),
                        "--protocol-id",
                        "example_protocol",
                        "--reward-out",
                        str(reward_path),
                        "--audit-out",
                        str(audit_path),
                    ]
                ),
                0,
            )

            metrics = json.loads(reward_path.read_text(encoding="utf-8"))
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(metrics["sequence_similarity"], 0.0)
            self.assertEqual(metrics["prediction_parse_valid"], 0.0)
            self.assertIn("error", audit)


if __name__ == "__main__":
    unittest.main()
