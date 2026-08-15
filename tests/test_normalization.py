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
            "[3-bp UMI1]": "[UMI:3]",
            "[3-bp UMI2]": "[UMI:3]",
            "[8-bp sample index]": "[SAMPLE_INDEX:8]",
            "[6-bp sample index]": "[SAMPLE_INDEX:6]",
            "[10-bp i5]": "[I5_INDEX:10]",
            "[10-bp i5 index]": "[I5_INDEX:10]",
            "[8-bp i5 index]": "[I5_INDEX:8]",
            "[8-bp i5 sample index]": "[I5_INDEX:8]",
            "[10-bp i7]": "[I7_INDEX:10]",
            "[10-bp i7 index]": "[I7_INDEX:10]",
            "[8-bp i7 index]": "[I7_INDEX:8]",
            "[6-bp i7 sample index]": "[I7_INDEX:6]",
            "[10-bp N5 barcode]": "[TN5_INDEX:10]",
            "[10-bp N7 barcode]": "[TN5_INDEX:10]",
            "[I5:8]": "[I5_INDEX:8]",
            "[I7:8]": "[I7_INDEX:8]",
        }
        for raw, normalized in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_sequence(raw), normalized)

    def test_general_barcode_placeholders(self):
        cases = {
            "[3-bp BC#01]": "[CELL_BARCODE:3]",
            "[7-bp BC#04]": "[CELL_BARCODE:7]",
            "[4-bp CB1]": "[CELL_BARCODE:4]",
            "[8-bp barcode2]": "[CELL_BARCODE:8]",
            "[8-bp Round1 barcode]": "[CELL_BARCODE:8]",
            "[10-bp RT barcode]": "[RT_BARCODE:10]",
            "[9-bp plate barcode]": "[CELL_BARCODE:9]",
            "[5-bp well barcode]": "[CELL_BARCODE:5]",
            "[8-bp subarray barcode]": "[CELL_BARCODE:8]",
            "[10-bp HY barcode]": "[CELL_BARCODE:10]",
            "[BARCODE:8]": "[CELL_BARCODE:8]",
        }
        for raw, normalized in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_sequence(raw), normalized)

    def test_specialized_barcode_placeholders(self):
        cases = {
            "[8-bp Tn5 index]": "[TN5_INDEX:8]",
            "[6-bp Tn5 barcode]": "[TN5_INDEX:6]",
            "[5-bp Tn5 index A]": "[TN5_INDEX:5]",
            "[15-bp FB]": "[FEATURE_BARCODE:15]",
            "[15-bp antibody barcodes]": "[FEATURE_BARCODE:15]",
            "[6-bp RPI]": "[SAMPLE_INDEX:6]",
            "[0-4 bp PB]": "[PHASE_BLOCK:4]",
            "[None/T/GT/TGA]": "[VARIABLE:3]",
            "[None/A/TA/GTA/NNNNNNNN]": "[VARIABLE:8]",
        }
        for raw, normalized in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_sequence(raw), normalized)

    def test_random_placeholders_only_when_labeled(self):
        self.assertEqual(normalize_sequence("[random 9-mer]"), "[RANDOM:9]")
        self.assertEqual(normalize_sequence("[9-bp randomer]"), "[RANDOM:9]")
        self.assertEqual(normalize_sequence("[RANDOMER:6]"), "[RANDOM:6]")
        self.assertEqual(normalize_sequence("[RANDOM_HEXAMER:6]"), "[RANDOM:6]")
        self.assertEqual(normalize_sequence("ACGTNNNNNNNNN"), "ACGTNNNNNNNNN")
        self.assertEqual(normalize_sequence("[NNNNNNNNN randomer]"), "[RANDOM:9]")

    def test_letter_runs_are_preserved_as_sequence(self):
        self.assertEqual(normalize_sequence("BBBBBBBBBBBBBBBB"), "BBBBBBBBBBBBBBBB")
        self.assertEqual(normalize_sequence("UUUUUUUU"), "UUUUUUUU")
        self.assertEqual(normalize_sequence("UUU"), "UUU")
        self.assertEqual(normalize_sequence("IIIIII"), "IIIIII")
        self.assertEqual(
            normalize_sequence("BAAAAAAAAAAAAAAAAAAA"), "BAAAAAAAAAAAAAAAAAAA"
        )
        self.assertEqual(normalize_sequence("III"), "III")

    def test_preserves_modifications_and_rna_markers(self):
        sequence = "/5Phos/ACGTrGrGrG/3SpC3/"
        self.assertEqual(normalize_sequence(sequence), "/5Phos/ACGTrGrGrG/3SpC3/")
        self.assertEqual(normalize_sequence("/5Bio/ACGT"), "/5Bio/ACGT")
        self.assertEqual(normalize_sequence("Bio-ACGT"), "/5Bio/ACGT")
        self.assertEqual(normalize_sequence("/5Phos/-ACGT"), "/5Phos/ACGT")
        self.assertEqual(normalize_sequence("A(dU)C+G*T"), "A(dU)C+GT")
        self.assertEqual(normalize_sequence("AGAGT*A*C"), "AGAGTAC")
        self.assertEqual(normalize_sequence("ACGT[ddC]"), "ACGT/ddC/")

    def test_expands_unambiguous_homopolymer_shorthand(self):
        self.assertEqual(normalize_sequence("(T)5"), "TTTTT")
        self.assertEqual(normalize_sequence("T10"), "TTTTTTTTTT")
        self.assertEqual(normalize_sequence("(A)4CG"), "AAAACG")

    def test_placeholder_tokens_expand_by_length(self):
        tokens = sequence_tokens("[CELL_BARCODE:4]AC[UMI:4]")
        self.assertEqual(tokens, ["<CELL_BARCODE>"] * 4 + ["A", "C"] + ["<UMI>"] * 4)
        self.assertEqual(sequence_tokens("[I5_INDEX:2]"), ["<I5_INDEX>", "<I5_INDEX>"])


if __name__ == "__main__":
    unittest.main()
