# Oligo Extraction: Seq-Well S3

Extract named oligo sequences from the provided raw protocol files. This task is only for
oligo name and sequence extraction; do not construct the full step-by-step library structure.

Run this helper to download the task input:

```bash
python fetch_input.py
```

Inspect all downloaded files in `input/`:

- `input/SeqWell_S3_paper.pdf`
- `input/SeqWell_S3_supp.pdf`

Write the final answer to `/logs/artifacts/prediction.json` using exactly this schema:

```json
{
  "schema_version": "libstruct.oligo_extraction.v1",
  "protocol_id": "seq_well_s3",
  "oligos": [
    {
      "name": "Oligo name",
      "sequence": "ACGT[CELL_BARCODE:16][UMI:12]",
      "direction": "5_to_3"
    },
    {
      "name": "Double-stranded adapter name",
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

Use canonical placeholders in the form `[ROLE:LENGTH]`, for example
`[CELL_BARCODE:16]`, `[BARCODE:8]`, `[UMI:10]`, `[SAMPLE_INDEX:8]`,
`[I5_INDEX:10]`, `[I7_INDEX:10]`, `[TN5_INDEX:8]`,
`[FEATURE_BARCODE:15]`, and `[RANDOM:9]`.

Rules:
- Extract only named oligos, primers, adapters, and adapter strands with
  explicit sequence evidence in the source.
- For double-stranded adapters, preserve both source-visible strands. Either
  emit each strand as a separate oligo object or emit one object with
  `kind: "double_stranded"` and strand `components`.
- Do not reverse-complement, complete, repair, or invent sequence strings.
- Preserve source orientation with `direction`: use `5_to_3`, `3_to_5`, or
  `unknown`.
- Do not output final library/product constructs unless the source identifies
  them as a distinct synthesized oligo, primer, or adapter.
