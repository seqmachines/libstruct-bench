# Per-Input Normalized Oligo Summary: 10x Chromium Single Cell ATAC v2

## Shared Protocol Processing Rules

Read `protocol_processing_rules.md` before working. It defines source-grounding, oligo-extraction, normalization, and library-construction rules shared by protocol-processing tasks. The task-specific instructions below define the required output schema and which section or sections to complete.


You are given human-curated text plus four PDF-parser text outputs for the 10x Chromium Single Cell ATAC v2 protocol. Your task is to produce one normalized/collapsed oligo summary for each input source independently. This benchmark tests whether each text source has enough evidence for an agent to summarize the protocol's oligos and normalize variable regions such as cell barcodes, UMIs, and sample indexes.

Run:

```bash
python fetch_input.py
```

This creates an `input/` directory containing:

- `10xChromium_scATACv2.human_text.txt`
- `10xChromium_scATACv2.mineru_ocr.txt`
- `10xChromium_scATACv2.pymupdf_text.txt`
- `10xChromium_scATACv2.pypdf_text.txt`
- `10xChromium_scATACv2.docling_text.txt`

Treat the files as five independent input sources:

- `human`: `10xChromium_scATACv2.human_text.txt`
- `mineru`: `10xChromium_scATACv2.mineru_ocr.txt`
- `pymupdf`: `10xChromium_scATACv2.pymupdf_text.txt`
- `pypdf`: `10xChromium_scATACv2.pypdf_text.txt`
- `docling`: `10xChromium_scATACv2.docling_text.txt`

For input sources with multiple files, combine evidence across those files into one summary for that `input_id`. Do not use `human` evidence to fill `mineru`, `pymupdf`, `pypdf`, or `docling`; each result must be based only on files belonging to that input source. If a parser source omits or corrupts a sequence, report only the normalized oligos recoverable from that source.

Write exactly one JSON file at `/logs/artifacts/prediction.json` using this schema:

```json
{
  "schema_version": "libstruct.oligo_summary_text5.inputs.v1",
  "protocol_id": "10x_chromium_single_cell_atac_v2",
  "results": [
    {
      "input_id": "human",
      "oligos": [
        {
          "name": "Collapsed oligo name",
          "sequence": "ACGT[CELL_BARCODE:16][UMI:12]TTTT",
          "direction": "5_to_3",
          "components": [
            {"name": "cell barcode", "role": "cell_barcode", "sequence": "[CELL_BARCODE:16]"}
          ]
        }
      ]
    },
    {"input_id": "mineru", "oligos": []},
    {"input_id": "pymupdf", "oligos": []},
    {"input_id": "pypdf", "oligos": []},
    {"input_id": "docling", "oligos": []}
  ]
}
```

Include exactly one `results` entry for each of: `human`, `mineru`, `pymupdf`, `pypdf`, and `docling`.

Normalization and collapse rules:

- Search broadly within each `input_id` before summarizing. Check both explicit oligo/primer/adapter tables or appendices (for example `Oligonucleotide Sequences`, `Adapter and primer sequences`, and `Primer sequences`) and final-library/library-structure diagrams or prose (for example `Final library structure`, `Library structure`, `Sequencing library`, and `Sample Index PCR product`). Use evidence from both kinds of sections when normalizing and collapsing oligos for that same input source. Do not use one input source to fill another.
- Within each `input_id`, produce normalized oligos, primers, adapters, and adapter strands supported by that source.
- Collapse repeated oligos that share the same architecture into one canonical summary. For example, CEL-seq lists many RT primers with different 8-bp barcodes; summarize that family as one RT primer architecture using `[CELL_BARCODE:8]` rather than emitting all listed barcode instances.
- If the source supports two truly different architectures, output separate oligo entries. Do not over-collapse oligos that have different handles, barcode lengths, UMI lengths, adapters, or primer roles.
- Use canonical placeholders in the form `[ROLE:LENGTH]`, for example `[CELL_BARCODE:16]`, `[UMI:12]`, `[SAMPLE_INDEX:8]`, `[I5_INDEX:10]`, `[I7_INDEX:10]`, `[RT_BARCODE:10]`, `[TN5_INDEX:8]`, `[FEATURE_BARCODE:15]`, `[PHASE_BLOCK:4]`, `[RANDOM:9]`, and `[VARIABLE:4]`.
- Use `CELL_BARCODE` for cell/GEM/bead/barcode identifiers, `UMI` for molecular identifiers, `SAMPLE_INDEX`/`I5_INDEX`/`I7_INDEX` for library indexes, and `RANDOM` or `VARIABLE` for random/degenerate bases only when no more specific role is supported by the text.
- Replace explicit variable runs with canonical placeholders when the surrounding text identifies their role and length. For example, a 16-base 10x barcode becomes `[CELL_BARCODE:16]`; an 8-base UMI becomes `[UMI:8]`.
- Preserve fixed nucleotide sequence and modified base notation such as `/5Phos/`, `/5BiosG/`, `rG`, `rA`, `rU`, and `rC` when they are part of the normalized oligo architecture. Strip orientation wrappers, non-nucleotide descriptors such as `Bead-Linker`, and chemical linkage markers such as `*` from the normalized `sequence`; preserve those details in names/components/notes only when the schema supports them.
- Clean parser/OCR typography artifacts inside sequences: convert `5′`, `5’`, `5ʹ`, `3′`, `3’`, and `3ʹ` to `5'` and `3'`; convert dash variants such as `–`, `—`, and `−` to `-`; and remove spaces inserted around sequence hyphens or terminal markers.
- Do not reverse-complement, complete, repair, or invent sequence strings.
- Preserve source orientation with `direction`: use `5_to_3`, `3_to_5`, or `unknown`.
- Oligo names should be concise and functional, but sequence architecture is the primary scoring target.

Before finishing, validate that the file exists and is valid JSON:

```bash
test -s /logs/artifacts/prediction.json
python -m json.tool /logs/artifacts/prediction.json >/dev/null
```
