# Parser-Control Oligo Sequence Recovery: Drop-seq

## Shared Protocol Processing Rules

Read `protocol_processing_rules.md` before working. It defines source-grounding, oligo-extraction, normalization, and library-construction rules shared by protocol-processing tasks. The task-specific instructions below define the required output schema and which section or sections to complete.


You are given curated human text plus four PDF-parser text outputs for the Drop-seq protocol. Your task is to extract the oligo/library-related nucleotide sequence strings that are recoverable from each input source.

Run:

```bash
python fetch_input.py
```

This creates an `input/` directory containing:

- `drop-seq.human_text.txt`
- `drop-seq_paper.mineru_ocr.txt`
- `drop-seq_supp.mineru_ocr.txt`
- `drop-seq_paper.pymupdf_text.txt`
- `drop-seq_supp.pymupdf_text.txt`
- `drop-seq_paper.pypdf_text.txt`
- `drop-seq_supp.pypdf_text.txt`
- `drop-seq_paper.docling_text.txt`
- `drop-seq_supp.docling_text.txt`

Treat the files as five independent input sources:

- `human`: the single `*.human_text.txt` file.
- `mineru`: all `*.mineru_ocr.txt` files for this protocol.
- `pymupdf`: all `*.pymupdf_text.txt` files for this protocol.
- `pypdf`: all `*.pypdf_text.txt` files for this protocol.
- `docling`: all `*.docling_text.txt` files for this protocol.

For sources with multiple files, combine the source-visible sequences across those files into one list for that `input_id`. Extract the nucleotide strings as they appear in the text, including IUPAC ambiguity codes, UMI/barcode placeholders, and source-visible modifications such as `*`, `/5BiosG/`, or terminal `5'`/`3'` notation when present. When filling `mineru`, `pymupdf`, `pypdf`, or `docling`, do not copy from `human`; only list strings visible in that parser source. Do not infer omitted sequences, fill missing bases from memory, reverse-complement sequences, or construct final library products. Oligo names are not required and are not scored.

Search broadly within each input source. Check both explicit oligo/primer/adapter tables or appendices (for example `Oligonucleotide Sequences`, `Adapter and primer sequences`, and `Primer sequences`) and final-library/library-structure diagrams or prose (for example `Final library structure`, `Library structure`, `Sequencing library`, and `Sample Index PCR product`). Include source-visible nucleotide strings from both kinds of sections. Do not stop after finding only named oligo tables.

Clean parser/OCR typography artifacts inside sequence strings: convert terminal prime variants such as `5′`, `5’`, `5ʹ`, `3′`, `3’`, and `3ʹ` to ASCII `5'` and `3'`; convert dash variants such as `–`, `—`, and `−` to ASCII `-`; remove spaces inserted around sequence hyphens or terminal markers; and use compact terminal notation like `5'-...-3'` when the source plainly denotes that molecule. For example, emit `5'-Bead-Linker-...-3'`, not `5’ –Bead–Linker-...-3’`. Keep meaningful source-visible descriptors and modifications such as `Bead-Linker`, `*`, `/5BiosG/`, and `rG`. Do not add reagent names, prose, external sequences, or sequences that are absent from the specific input source being reported.

Write exactly one JSON file at `/logs/artifacts/prediction.json` with this shape:

```json
{
  "schema_version": "libstruct.parser_control_text5.v1",
  "protocol_id": "drop_seq",
  "results": [
    {"input_id": "human", "sequences": ["..."]},
    {"input_id": "mineru", "sequences": ["..."]},
    {"input_id": "pymupdf", "sequences": ["..."]},
    {"input_id": "pypdf", "sequences": ["..."]},
    {"input_id": "docling", "sequences": ["..."]}
  ]
}
```

Before finishing, validate that the file exists and is valid JSON:

```bash
test -s /logs/artifacts/prediction.json
python -m json.tool /logs/artifacts/prediction.json >/dev/null
```
