# Library Structure Benchmark

V0 evaluates reconstruction of final sequencing library structure strings. Most
protocols have one final library; multimodal protocols have one final library
entry per measured modality or final sequencing product.

## Prediction Format

```json
{
  "schema_version": "libstruct.library_structure.v0",
  "protocol_id": "drop_seq",
  "libraries": [
    {
      "library_id": "rna",
      "modality": "RNA",
      "library_sequence": "AATGATACGGCGACCACCGAGATCTACAC############~~~~~~~~...",
      "annotated_library_sequence": "AATGATACGGCGACCACCGAGATCTACAC[CELL_BARCODE:12][UMI:8]..."
    }
  ]
}
```

The benchmark does not score oligo lists, segment labels, reports, diagrams, or
library-generation explanations.

## Source Policy

Online search is strictly prohibited for both v0.1 LLM runs and v0.2 coding
agent runs. Predictions must use only the protocol input files attached to the
LLM request or downloaded into the task `input/` directory. Do not browse,
search, retrieve protocol pages, consult `scg_lib_struct`, inspect benchmark
ground truth, or look up prior answer keys, repository copies, papers,
supplements, vendor pages, or any other external source.

For Harbor coding agents, the only permitted task-evidence network operation is
`python fetch_input.py`, which downloads pinned protocol inputs from the
configured Hugging Face input dataset. The verifier may separately access the
private ground-truth dataset. Local Docker Harbor runs still require network
access for the agent model API; that model API access is not permission to use
the web as evidence.

Harbor coding agents should use Docling with OCR disabled when it is available.
PyMuPDF (`import fitz`) and `pypdf` layout extraction are acceptable stable
alternatives and cross-checks. Agents should not run OCR or use OCR-derived
text as evidence for raw PDFs because OCR can introduce unstable sequence
conversions. They should combine native text, layout-sorted text blocks, table
cells, appendix text, and stable vector/text-layer labels from diagrams, and
use rendered pages only for visual layout checks.

## Derivation Policy

Final library structures are usually derived constructs, not strings printed
verbatim in the source. v0.1 LLM calls and v0.2 coding-agent tasks should build
the final construct by simulating library construction forward from the
source-visible components.

When deriving a structure:

1. Inventory the source-printed building blocks: capture or RT primer, bead
   oligo, template-switch oligo, ligation or round adapters, RT or Tn5
   adapters, PCR primers, sequencing primers, and flow-cell adapters.
2. Chain them through the workflow steps: capture, reverse transcription,
   template switch or second strand, amplification, fragmentation or
   tagmentation, ligation, library PCR, and final construct.
3. Write one consistent 5'->3' top-strand sequence. Reverse-complement
   bottom-strand oligos before placing them in the scored string.
4. For fragmented or tagmented products, keep only the fragment retained by the
   final library-PCR primer pair and exclude non-amplified dead-end fragments.
5. Include enzyme-deposited constant junctions only when they are grounded in
   source-visible adapter or primer sequence. Do not supply remembered bases.
6. Ground every barcode, UMI, index, and placeholder length in an explicit
   source statement, read structure, oligo diagram, or table.

Before writing a prediction, reconcile the assembled construct against the
source read structure. For Read 1, Read 2, Index 1, and Index 2, confirm which
primer primes the read, which strand is read, what region is read, and how many
cycles are expected. If the reads cannot be explained by the assembled
structure, fix the order, orientation, or missing region before scoring.

Each `libraries[].library_sequence` is a scoring target. It should use expanded
placeholder characters. `annotated_library_sequence` is optional debug/display
metadata and may use `[ROLE:LENGTH]` placeholders.

Every `libraries[].library_sequence` must be non-empty. If the source confirms
a final library exists but no scored sequence can be derived from the provided
inputs, omit that library entry instead of writing an empty string.

Unknown biological payloads are not scoring targets. If the final construct
contains an unknown cDNA, gDNA, VDJ, sgRNA spacer/protospacer, or other
biological insert, mark it in `annotated_library_sequence` with a no-length
placeholder such as `[CDNA]`, `[GDNA]`, `[VDJ_INSERT]`, or `[SGRNA_SPACER]`,
and omit it from `library_sequence`.

For single-modal protocols, one entry in `libraries` is preferred. Legacy
top-level `library_sequence` predictions are still accepted for compatibility.

For multimodal protocols, write separate entries rather than concatenating
modalities into one string. Examples: RNA + ATAC should have separate `rna` and
`atac` entries; RNA + feature barcoding should have separate `rna` and
`feature` entries; VDJ, sgRNA, gDNA, and cDNA products should each be separated
when the protocol shows them as distinct final libraries.

## Placeholder Policy

Use two representations:

- Structured oligo extraction, memory files, graph memory, and debug/display
  fields: canonical `[ROLE:LENGTH]`, for example `[CELL_BARCODE:16]`.
- V0 final-string scoring: expanded non-biological placeholder characters, for
  example `################`.
- Biological payload placeholders such as `[CDNA]`, `[GDNA]`, `[VDJ_INSERT]`,
  and `[SGRNA_SPACER]` are display/debug annotations only and must not appear in
  the scored `library_sequence`.

Scored placeholder characters:

- `#`: cell-identifying barcode and combinatorial cell barcode segments.
- `~`: UMI.
- `@`: sample/library index, including i5, i7, RPI, and generic sample indexes.
- `&`: ligation barcode.
- `=`: reverse-transcription barcode.
- `%`: Tn5/tagmentation barcode or index, including N5/N7 Tn5 barcodes.
- `$`: feature, capture, antibody, or other measured-target barcode.
- `?`: spacer, linker, phase block, randomer, degenerate, overhang, or other
  non-biological variable structural bases.

Do not confuse placeholder characters with real sequence symbols. Literal
source-visible nucleotide/IUPAC motif letters that are part of a primer or
adapter stay literal in `library_sequence`. Anchored oligo-dT suffixes such as
`VN`, `TVN`, `(dT)VN`, or `(T)30VN` should keep the `VN` after any T-run
expansion; they should not become `??`. The `?` placeholder is for named
non-biological variable structural regions, not for literal IUPAC bases printed
as part of a primer sequence.

Terms observed in ground truth oligo map as follows:

- `#`: `cell barcode`, `GEM barcode`, `bead barcode`, `CB1`, `CB2`, `CLS1`,
  `CLS2`, `CLS3`, `VB`, `barcode1`, `barcode2`, `barcode3`, `barcode4`,
  `Round1 barcode`, `Round2 barcode`, `Round3 barcode`, `BC#01`-`BC#04`,
  `HY barcode`, `subarray barcode`, `well barcode`, `plate barcode`, and
  `reverse complement of barcode A` when used for cell or combinatorial sample
  identity.
- `~`: `UMI`, `UMI1`, and `UMI2`.
- `@`: `sample index`, `index`, `i5`, `i7`, `i5 index`, `i7 index`,
  `i5 sample index`, `i7 sample index`, and `RPI`.
- `=`: `RT barcode`.
- `%`: `Tn5 index`, `Tn5 barcode`, `Tn5 index A`, `Tn5 index B`, `N5 barcode`,
  and `N7 barcode` when they are tagmentation barcode segments.
- `$`: `FB`, `feature barcode`, and `antibody barcodes`.
- `?`: `PB`, `phase block`, `random 9-mer`, `None/T/GT/TGA`,
  `None/A/TA/GTA/NNNNNNNN`, and source-visible uncertain overhang or structural
  variable bases.

When a source gives `[ROLE:LENGTH]` or `[8-bp UMI]`, the v0 grader expands it to
the scored character repeated by length before computing similarity. If a source
gives a range such as `[0-4 bp PB]`, use the maximum explicit length for the
single scored string (`????`). If the exact function of a generic barcode is
ambiguous, choose by biological role in the final construct: cell/combinatorial
identity -> `#`, sample multiplexing -> `@`, capture target -> `$`,
tagmentation -> `%`.

Bare source placeholders without a length, such as `[i7]`, `[barcode1]`, or
`[CLS1]`, are not enough for scoring by themselves. Infer their length from the
protocol or surrounding table before writing `library_sequence`; otherwise keep
them only in `annotated_library_sequence`.

If the source shows an unknown biological insert as `XXX...XXX`,
`XXXXXXXX...XXXXXXXX`, `...-V-D-J-...`, `[sgRNA-Spacer]`, or similar, do not
turn it into `?` characters. `?` is reserved for non-biological variable
library-architecture bases such as spacers, phase blocks, randomers,
degenerate overhangs, and dark-cycle bases.

## Metrics

The reward target is strict mean `sequence_similarity` across ground-truth final
library entries. Missing ground-truth libraries score `0` and stay in the
average:

1. Expand recognized `[ROLE:LENGTH]` and source-style bracket placeholders into
   repeated scored characters.
2. Remove whitespace from predicted and ground-truth `library_sequence`.
3. Uppercase both strings.
4. Match predicted entries to ground truth by `library_id`, then `modality`,
   then best remaining sequence similarity.
5. Compute normalized Levenshtein similarity for each matched entry:
   `1 - edit_distance / max(predicted_length, ground_truth_length)`.
6. Average entry-level similarities across all ground-truth libraries,
   including missing libraries as zero.

The verifier also reports diagnostic library-detection metrics:

- `matched_sequence_similarity`: average sequence similarity only over
  ground-truth libraries assigned to a predicted entry.
- `library_recall`: matched ground-truth libraries divided by total
  ground-truth libraries.
- `library_precision`: matched predicted libraries divided by total predicted
  libraries. Extra predicted libraries reduce this value.
- `library_f1`: harmonic mean of `library_precision` and `library_recall`.

Malformed or missing predictions receive `sequence_similarity = 0`.

## Run Analysis

See [Codex Library Structure Five-Task Error Analysis](codex_library_structure_5task_error_analysis.md)
for a detailed comparison of predictions, ground truth, source evidence, and
evaluator behavior from a five-task Harbor run.
