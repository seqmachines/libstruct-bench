import unittest

from libstruct_bench.grader import grade_prediction
from libstruct_bench.schema import PREDICTION_SCHEMA_VERSION, PredictionValidationError, parse_prediction_document


def prediction(oligos):
    return {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "protocol_id": "example_protocol",
        "oligos": oligos,
    }


GROUND_TRUTH = {
    "protocol_id": "example_protocol",
    "oligos": [
        {"name": "Barcode RT primer", "sequence": "ACGT[16-bp cell barcode][8-bp UMI]TTTT"},
        {"name": "Illumina P5 adapter", "sequence": "AATGATACGGCGACCACCGAGATCTACAC"},
    ],
}


class GraderTests(unittest.TestCase):
    def test_exact_match_ignores_oligo_ids(self):
        metrics, audit = grade_prediction(
            prediction(
                [
                    {
                        "oligo_id": "wrong_id",
                        "name": "Adapter P5",
                        "sequence": "AATGATACGGCGACCACCGAGATCTACAC",
                    },
                    {
                        "oligo_id": "also_wrong",
                        "name": "RT barcode primer",
                        "sequence": "ACGTBBBBBBBBBBBBBBBBUUUUUUUUTTTT",
                    },
                ]
            ),
            GROUND_TRUTH,
            expected_protocol_id="example_protocol",
        )
        self.assertEqual(metrics["sequence_precision"], 1.0)
        self.assertEqual(metrics["sequence_recall"], 1.0)
        self.assertEqual(metrics["sequence_f1"], 1.0)
        self.assertEqual(metrics["exact_match"], 1.0)
        self.assertEqual(len(audit["matches"]), 2)

    def test_extra_prediction_lowers_precision(self):
        metrics, _ = grade_prediction(
            prediction(
                [
                    {"name": "RT", "sequence": "ACGT[CELL_BARCODE:16][UMI:8]TTTT"},
                    {"name": "P5", "sequence": "AATGATACGGCGACCACCGAGATCTACAC"},
                    {"name": "Extra", "sequence": "CCCCCCCC"},
                ]
            ),
            GROUND_TRUTH,
            expected_protocol_id="example_protocol",
        )
        self.assertLess(metrics["sequence_precision"], 1.0)
        self.assertEqual(metrics["sequence_recall"], 1.0)
        self.assertEqual(metrics["exact_match"], 0.0)

    def test_missing_prediction_lowers_recall(self):
        metrics, _ = grade_prediction(
            prediction([{"name": "P5", "sequence": "AATGATACGGCGACCACCGAGATCTACAC"}]),
            GROUND_TRUTH,
            expected_protocol_id="example_protocol",
        )
        self.assertEqual(metrics["sequence_precision"], 1.0)
        self.assertLess(metrics["sequence_recall"], 1.0)
        self.assertEqual(metrics["exact_match"], 0.0)

    def test_name_similarity_is_diagnostic_only(self):
        good_names, _ = grade_prediction(
            prediction(
                [
                    {"name": "Barcode RT primer", "sequence": "ACGT[CELL_BARCODE:16][UMI:8]TTTT"},
                    {"name": "Illumina P5 adapter", "sequence": "AATGATACGGCGACCACCGAGATCTACAC"},
                ]
            ),
            GROUND_TRUTH,
            expected_protocol_id="example_protocol",
        )
        bad_names, _ = grade_prediction(
            prediction(
                [
                    {"name": "foo", "sequence": "ACGT[CELL_BARCODE:16][UMI:8]TTTT"},
                    {"name": "bar", "sequence": "AATGATACGGCGACCACCGAGATCTACAC"},
                ]
            ),
            GROUND_TRUTH,
            expected_protocol_id="example_protocol",
        )
        self.assertEqual(good_names["reward"], bad_names["reward"])
        self.assertGreater(good_names["name_similarity_mean"], bad_names["name_similarity_mean"])

    def test_ground_truth_double_stranded_components_are_matchable(self):
        ground_truth = {
            "protocol_id": "example_protocol",
            "oligos": [
                {
                    "name": "Double-stranded adapter",
                    "sequence": None,
                    "kind": "double_stranded",
                    "components": [
                        {
                            "name": "Double-stranded adapter forward",
                            "sequence": "ACGTACGT",
                            "role": "forward_strand",
                        },
                        {
                            "name": "Double-stranded adapter reverse",
                            "sequence": "TGCATGCA",
                            "role": "reverse_strand",
                        },
                    ],
                }
            ],
        }
        metrics, audit = grade_prediction(
            prediction(
                [
                    {"name": "Double-stranded adapter forward", "sequence": "ACGTACGT"},
                    {"name": "Double-stranded adapter reverse", "sequence": "TGCATGCA", "direction": "3_to_5"},
                ]
            ),
            ground_truth,
            expected_protocol_id="example_protocol",
        )
        self.assertEqual(metrics["sequence_f1"], 1.0)
        self.assertEqual(metrics["ground_truth_count"], 2.0)
        self.assertEqual(metrics["predicted_count"], 2.0)
        self.assertEqual(len(audit["matches"]), 2)

    def test_prediction_double_stranded_components_are_matchable(self):
        ground_truth = {
            "protocol_id": "example_protocol",
            "oligos": [
                {"name": "Double-stranded adapter forward", "sequence": "ACGTACGT"},
                {"name": "Double-stranded adapter reverse", "sequence": "TGCATGCA"},
            ],
        }
        metrics, audit = grade_prediction(
            prediction(
                [
                    {
                        "name": "Double-stranded adapter",
                        "sequence": None,
                        "kind": "double_stranded",
                        "components": [
                            {
                                "name": "forward strand",
                                "sequence": "ACGTACGT",
                                "role": "forward_strand",
                            },
                            {
                                "name": "reverse strand",
                                "sequence": "TGCATGCA",
                                "role": "reverse_strand",
                            },
                        ],
                    }
                ]
            ),
            ground_truth,
            expected_protocol_id="example_protocol",
        )
        self.assertEqual(metrics["sequence_f1"], 1.0)
        self.assertEqual(metrics["ground_truth_count"], 2.0)
        self.assertEqual(metrics["predicted_count"], 2.0)
        self.assertEqual(len(audit["matches"]), 2)

    def test_schema_validation_requires_version(self):
        with self.assertRaises(PredictionValidationError):
            parse_prediction_document({"protocol_id": "example_protocol", "oligos": []})


if __name__ == "__main__":
    unittest.main()
