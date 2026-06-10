import unittest

from libstruct_bench.normalization import normalize_sequence, sequence_tokens


class SequenceNormalizationTests(unittest.TestCase):
    def test_cell_barcode_placeholders(self):
        examples = [
            "[16-bp cell barcode]",
            "[16 bp cell barcode]",
            "[16-bp 10x barcode]",
            "[16-bp GEM barcode]",
            "[16-bp bead barcode]",
        ]
        for example in examples:
            with self.subTest(example=example):
                self.assertEqual(normalize_sequence(example), "[CELL_BARCODE:16]")

    def test_umi_sample_and_index_placeholders(self):
        cases = {
            "[10-bp UMI]": "[UMI:10]",
            "[12-bp UMI]": "[UMI:12]",
            "[8-bp UMI]": "[UMI:8]",
            "[8-bp sample index]": "[SAMPLE_INDEX:8]",
            "[6-bp sample index]": "[SAMPLE_INDEX:6]",
            "[10-bp i5]": "[I5_INDEX:10]",
            "[10-bp i5 index]": "[I5_INDEX:10]",
            "[8-bp i5 index]": "[I5_INDEX:8]",
            "[10-bp i7]": "[I7_INDEX:10]",
            "[10-bp i7 index]": "[I7_INDEX:10]",
            "[8-bp i7 index]": "[I7_INDEX:8]",
        }
        for raw, normalized in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_sequence(raw), normalized)

    def test_random_placeholders_only_when_labeled(self):
        self.assertEqual(normalize_sequence("[random 9-mer]"), "[RANDOM:9]")
        self.assertEqual(normalize_sequence("[9-bp randomer]"), "[RANDOM:9]")
        self.assertEqual(normalize_sequence("ACGTNNNNNNNNN"), "ACGTNNNNNNNNN")
        self.assertEqual(normalize_sequence("[NNNNNNNNN randomer]"), "[RANDOM:9]")

    def test_benchmark_run_conventions(self):
        self.assertEqual(normalize_sequence("BBBBBBBBBBBBBBBB"), "[CELL_BARCODE:16]")
        self.assertEqual(normalize_sequence("UUUUUUUU"), "[UMI:8]")
        self.assertEqual(normalize_sequence("UUU"), "UUU")
        self.assertEqual(normalize_sequence("IIIIII"), "[SAMPLE_INDEX:6]")

    def test_preserves_modifications_and_rna_markers(self):
        sequence = "/5Phos/ACGTrGrGrG/3SpC3/"
        self.assertEqual(normalize_sequence(sequence), "/5Phos/ACGTrGrGrG/3SpC3/")
        self.assertEqual(normalize_sequence("/5Bio/ACGT"), "/5Bio/ACGT")
        self.assertEqual(normalize_sequence("Bio-ACGT"), "/5Bio/ACGT")
        self.assertEqual(normalize_sequence("/5Phos/-ACGT"), "/5Phos/ACGT")
        self.assertEqual(normalize_sequence("A(dU)C+G*T"), "A(dU)C+G*T")

    def test_expands_unambiguous_homopolymer_shorthand(self):
        self.assertEqual(normalize_sequence("(T)5"), "TTTTT")
        self.assertEqual(normalize_sequence("T10"), "TTTTTTTTTT")
        self.assertEqual(normalize_sequence("(A)4CG"), "AAAACG")

    def test_placeholder_tokens_expand_by_length(self):
        tokens = sequence_tokens("[CELL_BARCODE:4]AC[UMI:4]")
        self.assertEqual(tokens, ["<CELL_BARCODE>"] * 4 + ["A", "C"] + ["<UMI>"] * 4)


if __name__ == "__main__":
    unittest.main()
