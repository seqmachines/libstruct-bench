# Oligo Extraction: s3-ATAC

Extract named oligo sequences from the provided raw protocol files. This task is only for
oligo name and sequence extraction; do not construct the full step-by-step library structure.

Run this helper to download the task input:

```bash
python fetch_input.py
```

Inspect all downloaded files in `input/`:

- `input/s3-ATAC.pdf`
- `input/s3-ATAC.xlsx`

Write the final answer to `/logs/artifacts/prediction.json` using exactly this schema:

```json
{
  "schema_version": "libstruct.oligo_extraction.v1",
  "protocol_id": "s3_atac",
  "oligos": [
    {
      "name": "Oligo name",
      "sequence": "ACGT[CELL_BARCODE:16][UMI:12]",
      "direction": "5_to_3"
    }
  ]
}
```

Use canonical placeholders in the form `[ROLE:LENGTH]`, for example
`[CELL_BARCODE:16]`, `[BARCODE:8]`, `[UMI:10]`, `[SAMPLE_INDEX:8]`,
`[I5_INDEX:10]`, `[I7_INDEX:10]`, `[TN5_INDEX:8]`,
`[FEATURE_BARCODE:15]`, and `[RANDOM:9]`.
