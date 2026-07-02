# Oligo Extraction: PETRI-seq

## Shared Protocol Processing Rules

Read `protocol_processing_rules.md` before working. It defines source-grounding, oligo-extraction, normalization, and library-construction rules shared by protocol-processing tasks. The task-specific instructions below define the required output schema and which section or sections to complete.


Extract oligo, primer, adapter, adapter-strand, and source-visible final-library-related nucleotide sequences from the provided raw protocol files. This task is for sequence extraction; do not construct the full step-by-step library structure.

Run this helper to download the task input:

```bash
python fetch_input.py
```

Inspect all downloaded files in `input/`:

- `input/PETRI-seq.xlsx`
- `input/PETRI-seq_paper.pdf`
- `input/PETRI-seq_protocol.pdf`

Write the final answer to `/logs/artifacts/prediction.json` using exactly this schema:

```json
{
  "schema_version": "libstruct.oligo_extraction.v1",
  "protocol_id": "petri_seq",
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
`[CELL_BARCODE:16]`, `[UMI:10]`, `[SAMPLE_INDEX:8]`, `[I5_INDEX:10]`,
`[I7_INDEX:10]`, `[RT_BARCODE:10]`, `[TN5_INDEX:8]`,
`[FEATURE_BARCODE:15]`, `[PHASE_BLOCK:4]`, and `[RANDOM:9]`.

Placeholder policy:
- Use `CELL_BARCODE` for cell-identifying and combinatorial barcode segments,
  including source terms such as cell/GEM/bead barcode, barcode1/barcode2,
  Round barcode, BC# barcode, well barcode, plate barcode, and subarray barcode.
- Use `UMI` for UMI/UMI1/UMI2.
- Use `SAMPLE_INDEX`, `I5_INDEX`, or `I7_INDEX` for sample/library indexes.
- Use `RT_BARCODE` for reverse-transcription barcodes.
- Use `TN5_INDEX` for Tn5/tagmentation barcodes or indexes, including N5/N7
  tagmentation barcodes.
- Use `FEATURE_BARCODE` for feature, capture, and antibody barcodes.
- Use `RANDOM`, `PHASE_BLOCK`, or `VARIABLE` for randomers, phase blocks,
  degenerate alternatives, overhangs, and other non-biological variable bases.
- If a source gives a range such as `[0-4 bp PB]`, use the maximum explicit
  length, for example `[PHASE_BLOCK:4]`.
- No-length biological payload markers such as `[CDNA]`, `[GDNA]`,
  `[VDJ_INSERT]`, and `[SGRNA_SPACER]` are for final-library
  `annotated_library_sequence` debug/display fields. Do not use them as
  scored oligo placeholders, and do not convert biological payloads to
  `[VARIABLE]` or `[RANDOM]`.
- If a source shows unknown biological payload as `XXX...XXX`,
  `...-V-D-J-...`, `[sgRNA-Spacer]`, or similar, preserve it only when it is
  explicitly part of a named oligo; otherwise treat it as a final-library
  payload outside this oligo extraction task.
- Bare labels without length, such as `[i7]`, `[barcode1]`, or `[CLS1]`, are
  not enough for scoring by themselves. Infer their length from the protocol
  before emitting a canonical placeholder.
- Do not use literal base letters or IUPAC ambiguity symbols such as `B`, `U`,
  `I`, `R`, `T`, or `V` as placeholder runs. Use canonical brackets instead.

Rules:
- Extract oligos, primers, adapters, adapter strands, and source-visible final-library/product sequence strings with explicit sequence evidence in the source.
- For PDF parsing, use PyMuPDF (`import fitz`) or a stronger parser. Do not
  use `pypdf` or `PyPDF2`.
- For spreadsheet parsing, use `openpyxl` for `.xlsx` files. If a workbook is
  malformed or `openpyxl` cannot read it, then fall back to inspecting the
  zipped XML parts directly.
- For double-stranded adapters, preserve both source-visible strands. Either
  emit each strand as a separate oligo object or emit one object with
  `kind: "double_stranded"` and strand `components`.
- Do not reverse-complement, complete, repair, or invent sequence strings.
- Preserve source orientation with `direction`: use `5_to_3`, `3_to_5`, or
  `unknown`.
- Include source-visible final library/product sequence strings when the source
  explicitly shows them as oligo/library-related nucleotide evidence. Do not
  derive final library products by protocol simulation in this extraction-only
  section; derived construction belongs to a library-construction task or section.
