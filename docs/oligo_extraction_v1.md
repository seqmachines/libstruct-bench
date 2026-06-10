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
- Generic barcode: `[8-bp barcode2]`, `[3-bp BC#01]`, `[8-bp Round1 barcode]`, `[10-bp RT barcode]`, `[9-bp plate barcode]`, `[5-bp well barcode]` -> `[BARCODE:8]`, `[BARCODE:3]`, etc.
- UMI: `[10-bp UMI]`, `[12-bp UMI]`, `[8-bp UMI]` -> `[UMI:10]`, `[UMI:12]`, `[UMI:8]`
- Sample index: `[8-bp sample index]`, `[6-bp sample index]` -> `[SAMPLE_INDEX:8]`, `[SAMPLE_INDEX:6]`
- i5 index: `[10-bp i5]`, `[10-bp i5 index]`, `[8-bp i5 index]` -> `[I5_INDEX:10]`, `[I5_INDEX:10]`, `[I5_INDEX:8]`
- i7 index: `[10-bp i7]`, `[10-bp i7 index]`, `[8-bp i7 index]` -> `[I7_INDEX:10]`, `[I7_INDEX:10]`, `[I7_INDEX:8]`
- Tn5 barcode/index: `[8-bp Tn5 index]`, `[6-bp Tn5 barcode]` -> `[TN5_INDEX:8]`, `[TN5_INDEX:6]`
- Feature barcode: `[15-bp FB]`, `[15-bp antibody barcodes]` -> `[FEATURE_BARCODE:15]`
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

## Harbor Data Layout

The v1 Harbor manifest is `benchmarks/oligo_extraction_v1/protocols.json`.
It defines the 20-protocol benchmark set, pins both Hugging Face dataset
revisions by commit SHA, and lists every raw file exposed to each protocol task.

- Raw inputs: `seqmachines/scg-protocols-v1`, public, agent-visible.
- Ground truth: `seqmachines/scg-oligo-groundtruth-v1`, verifier-only.
- Generated tasks live under `benchmarks/oligo_extraction_v1/tasks/<protocol_id>/`.
- `benchmarks/oligo_extraction_v1/dataset.toml` pins the generated task digests
  for Harbor publish/registry workflows.
- Local Docker runs use `network_mode = "public"` because Harbor's Docker
  provider does not enforce host allowlists.

Each task downloads all listed raw files into `input/` via `python fetch_input.py`.
The separate verifier downloads the matching `groundtruth_oligos.json` using
`HF_TOKEN` and grades only `/logs/artifacts/prediction.json`.
