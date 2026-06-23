# Library Structure V0: PETRI-seq

Extract the final sequencing library structure from the provided raw protocol files.

Run this helper to download the task input:

```bash
python fetch_input.py
```

Inspect all downloaded files in `input/`:

- `input/PETRI-seq.xlsx`
- `input/PETRI-seq_paper.pdf`
- `input/PETRI-seq_protocol.pdf`

Create `/logs/artifacts/prediction.json` using exactly this schema. The
verifier reads only that file; JSON returned only in your final chat response is
ignored and will score as missing output.

```json
{
  "schema_version": "libstruct.library_structure.v0",
  "protocol_id": "petri_seq",
  "libraries": [
    {
      "library_id": "rna",
      "modality": "RNA",
      "library_sequence": "AATGATACGGCGACCACCGAGATCTACAC################~~~~~~~~~~~~ATCTCGTATGCCGTCTTCTGCTTG",
      "annotated_library_sequence": "AATGATACGGCGACCACCGAGATCTACAC[CELL_BARCODE:16][UMI:12]ATCTCGTATGCCGTCTTCTGCTTG"
    }
  ]
}
```

Rules:
- STRICTLY PROHIBITED: do not search the web, browse the internet, retrieve
  protocol pages, query search engines, or look up scg_lib_struct, benchmark
  ground truth, prior answer keys, repository copies, papers, supplements,
  vendor pages, or any external source. Use only the files downloaded into
  `input/` for this task. The only network use allowed for task evidence is
  running `python fetch_input.py` to fetch the pinned input files.
- Do not answer from protocol name, prior knowledge, memory, or expected common
  constructs. The prediction must be grounded in the downloaded source files.
- Output one 5'->3' final library structure entry in `libraries` for each final
  sequencing library shown by the protocol.
- For PDF parsing, use PyMuPDF (`import fitz`) or a stronger parser. Do not
  use `pypdf` or `PyPDF2`.
- For spreadsheet parsing, use `openpyxl` for `.xlsx` files. If a workbook is
  malformed or `openpyxl` cannot read it, then fall back to inspecting the
  zipped XML parts directly.
- If the protocol profiles multiple modalities or products, write separate
  entries instead of concatenating them. Examples: RNA + ATAC should have
  separate `rna` and `atac` entries; RNA + feature barcoding should have
  separate `rna` and `feature` entries; VDJ, sgRNA, gDNA, and cDNA products
  should each be separated when the source shows them as distinct final
  libraries.
- Each `libraries[].library_sequence` is scored. Use expanded placeholder
  characters there. Optional `annotated_library_sequence` may use
  `[ROLE:LENGTH]` placeholders for display/debug.
- Every `libraries[].library_sequence` must be a non-empty string. If the
  source confirms a final library exists but you cannot derive any scored
  sequence for it from the input files, omit that library entry rather than
  writing an empty string.
- This is a full library construct task, not an oligo extraction task.
- Include fixed adapter, primer, linker, and flow-cell sequence bases when the
  final construct contains them.
- Do not include the variable cDNA, genomic DNA, or insert sequence. Concatenate
  the flanking library-structure segments across the insert.
- In `annotated_library_sequence`, make omitted biological payloads explicit
  with no-length debug placeholders such as `[CDNA]`, `[GDNA]`,
  `[VDJ_INSERT]`, or `[SGRNA_SPACER]`. Do not include those placeholders in
  scored `library_sequence`.
- Use repeated non-biological placeholder characters for variable regions: `#`
  for cell-identifying or combinatorial barcode; `~` for UMI; `@` for
  sample/library index including i5, i7, and RPI; `&` for ligation barcode; `=`
  for RT barcode; `%` for Tn5/tagmentation barcode or index; `$` for feature,
  capture, or antibody barcode; `?` for spacer, linker, phase block, randomer,
  degenerate, overhang, or other structural variable bases.
- Do not use literal base letters or IUPAC ambiguity symbols such as `B`, `U`,
  `I`, `R`, `T`, or `V` as placeholders in `library_sequence`.
- Preserve explicit source-visible nucleotide/IUPAC motif letters when they are
  part of a primer or adapter sequence. In particular, anchored oligo-dT suffixes
  such as `VN`, `TVN`, `(dT)VN`, or `(T)30VN` should remain literal `VN` after
  any T-run expansion, not become `??`.
- Source terms seen in the ground truth include: cell/GEM/bead barcode,
  barcode1/barcode2/barcode3/barcode4, Round1/Round2/Round3 barcode, BC#01-04,
  CB1/CB2, CLS1/CLS2/CLS3, VB, HY barcode, plate/well/subarray barcode, UMI1,
  UMI2, sample index, i5/i7 index, Tn5 index/barcode, N5/N7 barcode, RT barcode,
  FB, feature barcode, antibody barcodes, PB, phase block, and random 9-mer.
- The number of repeated placeholder characters must match the region length
  when the length is known.
- If the source gives a range such as `[0-4 bp PB]`, use the maximum explicit
  length for this single scored string.
- If the source shows unknown biological payload as `XXX...XXX`,
  `...-V-D-J-...`, `[sgRNA-Spacer]`, or similar, annotate it as a biological
  payload and omit it from `library_sequence`. Do not encode biological payloads
  with `?`; `?` is only for non-biological structural variable bases.
- Bare labels without length, such as `[i7]`, `[barcode1]`, or `[CLS1]`, are not
  enough for scoring by themselves. Infer their length from the protocol before
  expanding them in `library_sequence`, or keep them only in
  `annotated_library_sequence`.
- The file `/logs/artifacts/prediction.json` must contain only JSON. Do not put
  Markdown, comments, or explanations in that file.
- Before finishing, run `test -s /logs/artifacts/prediction.json` and
  `python -m json.tool /logs/artifacts/prediction.json` to confirm the artifact
  exists and is valid JSON.
- Also inspect the JSON after validation and make sure no
  `libraries[].library_sequence` value is empty.
