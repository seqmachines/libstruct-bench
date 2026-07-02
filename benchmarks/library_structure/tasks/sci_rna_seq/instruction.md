# Library Structure: sci-RNA-seq

## Shared Protocol Processing Rules

Read `protocol_processing_rules.md` before working. It defines source-grounding, oligo-extraction, normalization, and library-construction rules shared by protocol-processing tasks. The task-specific instructions below define the required output schema and which section or sections to complete.


Extract the final sequencing library structure from the provided raw protocol files.

Run this helper to download the task input:

```bash
python fetch_input.py
```

Inspect all downloaded files in `input/`:

- `input/paper.pdf`
- `input/supplementary.pdf`

Create `/logs/artifacts/prediction.json` using this JSON shape. The verifier reads only that file.
JSON returned only in your final chat response is ignored and will score as
missing output. The values in the block below are dummy schema examples only. Do
not copy the example `library_id`, `modality`,
`library_sequence`, `annotated_library_sequence`, bases, placeholder lengths, or
segment order unless the downloaded source files support them.

```json
{
  "schema_version": "libstruct.library_structure.v0",
  "protocol_id": "sci_rna_seq",
  "libraries": [
    {
      "library_id": "example_library",
      "modality": "example",
      "library_sequence": "ACGT####~~~~TGCA",
      "annotated_library_sequence": "ACGT[CELL_BARCODE:4][UMI:4]TGCA"
    }
  ]
}
```

## How to derive the structure

The final library is almost never printed verbatim in the source. Build it by
simulating library construction forward, then concatenate the fixed flanking
sequence on a single 5'->3' top strand. Do not search for a finished construct
to copy; assemble it.

1. Inventory the building blocks the source actually prints: capture/RT primer
   or bead oligo, template-switch oligo, ligation/round adapters, RT or Tn5
   adapters, PCR primers, sequencing primers, and flow-cell (P5/P7) adapters.
   Record each sequence and its length exactly as written.
2. Chain them in workflow order, tracking what each enzymatic step adds to or
   removes from the molecule: capture -> reverse transcription -> template
   switch or second-strand -> amplification -> fragmentation/tagmentation ->
   ligation -> library PCR -> final construct.
3. Keep one orientation. Write the whole library 5'->3' on the top strand. When
   a source oligo is given for the bottom strand (commonly the i7/P7 and Read 2
   primers), reverse-complement it before placing it. An oligo listed 5'->3' in
   a table is not necessarily 5'->3' in the final top strand; getting this wrong
   corrupts the literal adapter bases that are scored.
4. Resolve fragment selection. When the workflow fragments or tagments the
   molecule (Tn5, sonication, restriction), only the fragment retained by the
   final library-PCR primer pair reaches the sequencer. Work out which primer
   anneals to which end, decide which single fragment carries both flow-cell
   adapters, and build only that fragment. Internal and non-amplified fragments
   are dead ends and are excluded. The choice of which end survives is what sets
   3' vs 5' bias and is usually implied by an asymmetric primer pair, not stated
   outright.
5. Reconstruct enzyme-deposited junctions from source sequence only. Some
   constant bases (e.g. a transposase mosaic end, an A/T overhang) are added
   during a step rather than printed as a named oligo. Recover them only from
   adapter/primer sequence the source provides (for example, a constant tail
   shared by the tagmentation primers). If the source gives no sequence basis
   for a constant region, record it as an unknown gap in
   `annotated_library_sequence` and omit it from `library_sequence` rather than
   supplying remembered bases.
6. Ground every length in the source. Take each barcode, UMI, and index length
   from an explicit source statement (read structure, oligo diagram, or table),
   never from a default or a remembered construct.

## Reconcile against the sequencing reads (required)

Before writing the artifact, check the assembled structure against the read
configuration the source describes. For each read (Read 1, Read 2, Index 1,
Index 2): confirm which primer primes it, on which strand, what region it reads,
and how many cycles. The structure must explain every read: the bases under
Read 1 must match what the source says Read 1 reads, index reads must land on
the index regions, and read lengths must fit the region lengths. If a read
cannot be explained by your structure, the structure is wrong; fix the order,
orientation, or a missing region before finishing. This reconciliation is the
primary guard against a plausible-but-wrong construct, so do not skip it even
when the assembled string looks complete.

Rules:
- STRICTLY PROHIBITED: do not search the web, browse the internet, retrieve
  protocol pages, query search engines, or look up scg_lib_struct, benchmark
  ground truth, prior answer keys, repository copies, papers, supplements,
  vendor pages, or any external source. Use only the files
  downloaded into `input/` for this task. The only network use allowed for task
  evidence is running `python fetch_input.py` to fetch the pinned input files.
- Do not answer from protocol name, prior knowledge, memory, or expected common
  constructs. The prediction must be grounded in the downloaded source files.
- The schema block above is only a JSON shape example, not a partial answer and
  not source evidence. If any source-derived sequence conflicts with the
  example, the source wins.
- Output one 5'->3' final library structure entry in `libraries` for each final
  sequencing library shown by the protocol.
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
- For PDF parsing, use PyMuPDF (`import fitz`) or a stronger parser. Do not
  use `pypdf` or `PyPDF2`.
- For spreadsheet parsing, use `openpyxl` for `.xlsx` files. If a workbook is
  malformed or `openpyxl` cannot read it, then fall back to inspecting the
  zipped XML parts directly.
- Do not include the variable cDNA, genomic DNA, or insert sequence. Concatenate
  the flanking library-structure segments across the insert.
- In `annotated_library_sequence`, make omitted biological payloads explicit
  with no-length debug placeholders such as `[CDNA]`, `[GDNA]`,
  `[VDJ_INSERT]`, or `[SGRNA_SPACER]`. Do not include those placeholders in
  scored `library_sequence`.
- If the source shows unknown biological payload as `XXX...XXX`,
  `...-V-D-J-...`, `[sgRNA-Spacer]`, or similar, annotate it as a biological
  payload and omit it from `library_sequence`. Do not encode biological payloads
  with `?`; `?` is only for non-biological structural variable bases.
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
- Bare labels without length, such as `[i7]`, `[barcode1]`, or `[CLS1]`, are not
  enough for scoring by themselves. Infer their length from the protocol before
  expanding them in `library_sequence`, or keep them only in
  `annotated_library_sequence`.
- The file `/logs/artifacts/prediction.json` must contain only JSON. Do not put
  Markdown, comments, or explanations in that file.
- Before finishing, run `test -s /logs/artifacts/prediction.json` and
  `python -m json.tool /logs/artifacts/prediction.json` to confirm the artifact
  exists and is valid JSON.
- Before finishing, self-check each entry: every contiguous segment in
  `library_sequence` traces to a specific source oligo or to an enzymatic step
  the source describes; the string is a single consistent 5'->3' top strand; the
  read reconciliation above passes; the insert is omitted with flanks
  concatenated; and every placeholder run length matches a source-grounded
  length. Fix any segment that fails before writing the file.
- Also inspect the JSON after validation and make sure no
  `libraries[].library_sequence` value is empty.
