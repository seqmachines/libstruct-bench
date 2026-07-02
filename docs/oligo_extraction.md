# Oligo Extraction Benchmark

This benchmark scores extraction of oligo names and sequences from sequencing
protocol inputs. Full library construction is out of scope for v1.

## Prediction Format

Agents write `/logs/artifacts/prediction.json`:

```json
{
  "schema_version": "libstruct.oligo_extraction.v1",
  "protocol_id": "10x_chromium_3_gene_expression_v3",
  "oligos": [
    {
      "name": "Beads-oligo-dT",
      "sequence": "CTACACGACGCTCTTCCGATCT[CELL_BARCODE:16][UMI:12]TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTVN",
      "direction": "5_to_3"
    },
    {
      "name": "Double-stranded adapter",
      "sequence": null,
      "kind": "double_stranded",
      "components": [
        {
          "name": "forward strand",
          "role": "forward_strand",
          "sequence": "ACGTACGT",
          "direction": "5_to_3"
        },
        {
          "name": "reverse strand",
          "role": "reverse_strand",
          "sequence": "TGCATGCA",
          "direction": "3_to_5"
        }
      ]
    }
  ]
}
```

Simple oligos should use `sequence`. Double-stranded adapters may either be
reported as separate strand oligos or as one `kind: "double_stranded"` object
with `components`/`strands`. The grader flattens component sequences only when
the parent oligo has no concrete `sequence`, so assembled oligos with a full
sequence are not double-counted.

`oligo_id` and extra fields are allowed but ignored by the v1 reward. The JSON
schema lives at `schemas/oligo_extraction_prediction.v1.schema.json`.

Agents should preserve source-visible strand orientation (`5_to_3`, `3_to_5`,
or `unknown`) and must not reverse-complement, complete, repair, or invent
sequence strings.

## Placeholder Policy

Use canonical `[ROLE:LENGTH]` placeholders in structured oligo extraction,
memory files, graph memory, and debug/display fields. V0 final-library string
scoring uses expanded non-biological placeholder characters instead (`#`, `~`,
`@`, `&`, `=`, `%`, `$`, `?`); v1 oligo extraction keeps the canonical bracket
form.

Canonical placeholder roles:

- `CELL_BARCODE`: cell-identifying barcode and combinatorial cell barcode
  segments.
- `UMI`: unique molecular identifier.
- `SAMPLE_INDEX`, `I5_INDEX`, `I7_INDEX`: sample/library indexes.
- `LIGATION_BARCODE`: ligation barcode.
- `RT_BARCODE`: reverse-transcription barcode.
- `TN5_INDEX`: Tn5/tagmentation barcode or index, including N5/N7
  tagmentation barcodes.
- `FEATURE_BARCODE`: feature, capture, antibody, or other measured-target
  barcode.
- `RANDOM`, `PHASE_BLOCK`, `VARIABLE`: randomer, phase block, degenerate,
  overhang, or other non-biological variable structural bases.

No-length biological payload markers such as `[CDNA]`, `[GDNA]`,
`[VDJ_INSERT]`, and `[SGRNA_SPACER]` are final-library
`annotated_library_sequence` debug/display annotations, not scored oligo
placeholders. Do not convert unknown biological payloads such as `XXX...XXX`,
`...-V-D-J-...`, or `[sgRNA-Spacer]` into `[VARIABLE]`, `[RANDOM]`, or `?`.
For v1 oligo extraction, preserve such source-visible payload text only when it
is explicitly part of a named oligo; otherwise final/product constructs remain
out of scope.

Terms observed in `groundtruth_oligos.tsv` map as follows:

- `CELL_BARCODE`: `cell barcode`, `GEM barcode`, `bead barcode`, `CB1`, `CB2`,
  `CLS1`, `CLS2`, `CLS3`, `VB`, `barcode1`, `barcode2`, `barcode3`,
  `barcode4`, `Round1 barcode`, `Round2 barcode`, `Round3 barcode`,
  `BC#01`-`BC#04`, `HY barcode`, `subarray barcode`, `well barcode`,
  `plate barcode`, and `reverse complement of barcode A` when used for cell or
  combinatorial sample identity.
- `UMI`: `UMI`, `UMI1`, and `UMI2`.
- `SAMPLE_INDEX` / `I5_INDEX` / `I7_INDEX`: `sample index`, `index`, `i5`,
  `i7`, `i5 index`, `i7 index`, `i5 sample index`, `i7 sample index`, and
  `RPI`.
- `RT_BARCODE`: `RT barcode`.
- `TN5_INDEX`: `Tn5 index`, `Tn5 barcode`, `Tn5 index A`, `Tn5 index B`,
  `N5 barcode`, and `N7 barcode` when they are tagmentation barcode segments.
- `FEATURE_BARCODE`: `FB`, `feature barcode`, and `antibody barcodes`.
- `RANDOM`, `PHASE_BLOCK`, or `VARIABLE`: `PB`, `phase block`,
  `random 9-mer`, `None/T/GT/TGA`, `None/A/TA/GTA/NNNNNNNN`, and
  source-visible uncertain overhang or structural variable bases.

The v1 normalizer accepts legacy/source-style placeholders such as
`[8-bp barcode2]`, `[BARCODE:8]`, `[12-bp UMI]`, `[10-bp RT barcode]`,
`[0-4 bp PB]`, and `[None/T/GT/TGA]`, and rewrites them to canonical roles
before scoring. If a source gives a range such as `[0-4 bp PB]`, use the
maximum explicit length in the canonical placeholder (`[PHASE_BLOCK:4]`).

Bare source placeholders without a length, such as `[i7]`, `[barcode1]`, or
`[CLS1]`, are not enough for scoring by themselves. Infer their length from the
protocol or surrounding table before writing a curated ground truth sequence.
Agents should preserve them only when no length can be established from the
source.

Literal base letters and IUPAC ambiguity codes are preserved. `B`, `U`, `I`,
`R`, `T`, and `V` are never interpreted as placeholders outside canonical
brackets. Plain `N` runs are not converted to random placeholders. Random
placeholders are only normalized when bracketed or explicitly labeled as
random/randomer. Slash modification tags, bracketed chemistry markers such as
`[ddC]`, RNA base markers such as `rG`, and source-specific chemistry markers
are preserved as chemistry.

Additional source-normalization carryovers:

- Strip terminal `5'-` / `-3'` wrappers.
- Normalize leading `Bio-` to `/5Bio/`.
- Normalize phosphate and common slash-tag variants such as `/Phos/` and
  `/phos/` to `/5Phos/`.
- Remove the source-formatting hyphen in terminal forms such as `/5Phos/-ACGT`.
- Expand unambiguous `(T)n`, `Tn`, and `(A)n` shorthand homopolymers.

## Metrics

The grader:

1. Expands component-only double-stranded oligos into one comparable sequence
   per concrete component/strand.
2. Normalizes every predicted and ground-truth sequence.
3. Scores every predicted-vs-ground-truth pair with normalized edit similarity
   over sequence tokens.
4. Expands canonical placeholders by length during token scoring.
5. Selects the best one-to-one maximum-score assignment.
6. Emits precision, recall, F1, exact match, and diagnostic name similarity.

The Harbor reward is `sequence_f1`. Oligo IDs are ignored. Name similarity is
computed on sequence-matched pairs and does not affect reward.

`/logs/verifier/reward.json` contains numeric metrics only. Detailed pairings
and errors are written to `/logs/verifier/matches.json` and
`/logs/verifier/error.txt`.

## Harbor Data Layout

The Harbor manifest is `benchmarks/oligo_extraction/protocols.json`.
It defines the 20-protocol benchmark set, pins both Hugging Face dataset
revisions by commit SHA, and lists every raw file exposed to each protocol task.

- Raw inputs: `sequencing/scg-protocols-v1`, public, agent-visible.
- Ground truth: `sequencing/scg-oligo-groundtruth-v1`, verifier-only.
- Generated tasks live under `benchmarks/oligo_extraction/tasks/<protocol_id>/`.
- `benchmarks/oligo_extraction/dataset.toml` pins the generated task digests
  for Harbor publish/registry workflows.
- Local Docker runs use `network_mode = "public"` because Harbor's Docker
  provider does not enforce host allowlists.

Each task downloads all listed raw files into `input/` via `python fetch_input.py`.
The separate verifier downloads the matching `groundtruth_oligos.json` using
`HF_TOKEN` and grades only `/logs/artifacts/prediction.json`.

Generated Harbor agent images install PyMuPDF. Agents should parse PDFs with
PyMuPDF (`import fitz`) or a stronger parser, and should not use `pypdf` or
`PyPDF2`.
