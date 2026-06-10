# Oligo Extraction Benchmark V1

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
    }
  ]
}
```

`oligo_id` and extra fields are allowed but ignored by the v1 grader. The JSON
schema lives at `schemas/oligo_extraction_prediction.v1.schema.json`.

## Sequence Normalization

Canonical placeholders use `[ROLE:LENGTH]`.

Mapped placeholders:

- Cell barcode: `[16-bp cell barcode]`, `[16 bp cell barcode]`, `[16-bp 10x barcode]`, `[16-bp GEM barcode]`, `[16-bp bead barcode]` -> `[CELL_BARCODE:16]`
- UMI: `[10-bp UMI]`, `[12-bp UMI]`, `[8-bp UMI]` -> `[UMI:10]`, `[UMI:12]`, `[UMI:8]`
- Sample index: `[8-bp sample index]`, `[6-bp sample index]` -> `[SAMPLE_INDEX:8]`, `[SAMPLE_INDEX:6]`
- i5 index: `[10-bp i5]`, `[10-bp i5 index]`, `[8-bp i5 index]` -> `[I5_INDEX:10]`, `[I5_INDEX:10]`, `[I5_INDEX:8]`
- i7 index: `[10-bp i7]`, `[10-bp i7 index]`, `[8-bp i7 index]` -> `[I7_INDEX:10]`, `[I7_INDEX:10]`, `[I7_INDEX:8]`
- Random: `[random 9-mer]`, `[9-bp randomer]` -> `[RANDOM:9]`

Benchmark shorthand outside bracket and modification tokens:

- `B` repeated `n` times -> `[CELL_BARCODE:n]`
- `U` repeated `n >= 4` times -> `[UMI:n]`
- `I` repeated `n` times -> `[SAMPLE_INDEX:n]`

Plain `N` runs are not converted to random placeholders. Random placeholders
are only normalized when bracketed or explicitly labeled as random/randomer.
Slash modification tags, RNA base markers such as `rG`, and source-specific
chemistry markers are preserved.

Additional source-normalization carryovers:

- Strip terminal `5'-` / `-3'` wrappers.
- Normalize leading `Bio-` to `/5Bio/`.
- Normalize phosphate and common slash-tag variants such as `/Phos/` and
  `/phos/` to `/5Phos/`.
- Remove the source-formatting hyphen in terminal forms such as `/5Phos/-ACGT`.
- Expand unambiguous `(T)n`, `Tn`, and `(A)n` shorthand homopolymers.

## Metrics

The grader:

1. Normalizes every predicted and ground-truth sequence.
2. Scores every predicted-vs-ground-truth pair with normalized edit similarity
   over sequence tokens.
3. Expands canonical placeholders by length during token scoring.
4. Selects the best one-to-one maximum-score assignment.
5. Emits precision, recall, F1, exact match, and diagnostic name similarity.

The Harbor reward is `sequence_f1`. Oligo IDs are ignored. Name similarity is
computed on sequence-matched pairs and does not affect reward.

`/logs/verifier/reward.json` contains numeric metrics only. Detailed pairings
and errors are written to `/logs/verifier/matches.json` and
`/logs/verifier/error.txt`.
