# Library Structure Harbor Benchmark V0

This benchmark scores one thing: final library-structure sequence similarity.
Multimodal protocols are scored as separate final-library entries.

Agents write `/logs/artifacts/prediction.json`:

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

The verifier compares each `libraries[].library_sequence` with curated
`groundtruth_final_lib_struct.json` using normalized Levenshtein similarity.
Entries are matched by `library_id`, then `modality`, then best remaining
sequence similarity. Strict `sequence_similarity` averages over all
ground-truth libraries, with missing libraries scored as zero. Diagnostic
metrics include `matched_sequence_similarity`, `library_recall`,
`library_precision`, and `library_f1`. `annotated_library_sequence` is optional
display/debug metadata and is not scored.
Every `libraries[].library_sequence` must be non-empty; if a source-visible
library cannot be converted into any scored sequence from the provided inputs,
omit that entry rather than writing an empty string.

Online search is strictly prohibited. v0.1 LLM runs and v0.2 Harbor coding
agent runs must use only the protocol input files attached to the request or
downloaded into `input/`. Do not browse, search, retrieve protocol pages, or
consult `scg_lib_struct`, benchmark ground truth, prior answer keys, repository
copies, papers, supplements, vendor pages, or other external sources.
Generated Harbor agent images install PyMuPDF, OpenPyXL, Pillow, and the
`file` utility. Agents should use PyMuPDF (`import fitz`) or a stronger parser
for PDFs, not `pypdf` or `PyPDF2`; use OpenPyXL for `.xlsx` spreadsheets and
Pillow for local image crops or rendered-page inspection.

For single-modal protocols, one entry in `libraries` is preferred. Legacy
top-level `library_sequence` predictions are still accepted for compatibility.
For protocols that profile multiple modalities or produce separate final
sequencing libraries, such as RNA + ATAC, RNA + feature barcoding, VDJ, sgRNA,
gDNA, or cDNA products, write one entry per final library instead of
concatenating modalities into one string.

## Derivation Rules

Final library structures are often not printed verbatim. Derive them by
inventorying source-visible oligos/adapters/primers, chaining them through the
workflow, and writing one consistent 5'->3' top-strand sequence. Reverse
complement bottom-strand oligos before placing them, keep only the fragment
retained by the final library-PCR primer pair, and include enzyme-deposited
junctions only when grounded in source-visible sequence. Before writing
`prediction.json`, reconcile the construct against Read 1, Read 2, Index 1, and
Index 2 so every read lands on the expected region with a source-grounded
length.

## Placeholder Rules

Keep structured oligos and memory artifacts in canonical `[ROLE:LENGTH]` form,
but write the v0 scored `library_sequence` with expanded non-biological
placeholder characters:

- `#`: cell, bead, GEM, combinatorial, round, well, plate, subarray, or generic
  barcode when it identifies cell/combinatorial origin.
- `~`: UMI.
- `@`: sample/library index, including i5, i7, RPI, and generic sample index.
- `&`: ligation barcode.
- `=`: RT barcode.
- `%`: Tn5/tagmentation barcode or index, including N5/N7 Tn5 barcodes.
- `$`: feature, capture, antibody, or other measured-target barcode.
- `?`: spacer, linker, phase block, randomer, degenerate, overhang, or other
  structural variable bases.

Literal source-visible nucleotide/IUPAC motif letters that are part of a primer
or adapter stay literal in `library_sequence`. Anchored oligo-dT suffixes such
as `VN`, `TVN`, `(dT)VN`, or `(T)30VN` should keep the `VN` after any T-run
expansion, not become `??`. The `?` placeholder is for named non-biological
variable structural regions, not for literal IUPAC bases printed as part of a
primer sequence.

The grader also expands recognized bracket placeholders in `library_sequence`
before scoring, so `[UMI:12]` and `[12-bp UMI]` both compare as `~~~~~~~~~~~~`.
For ranged phase blocks such as `[0-4 bp PB]`, use the maximum explicit length
in the single scored string. Bare labels without length, such as `[i7]` or
`[barcode1]`, should stay in `annotated_library_sequence` unless the protocol
gives enough context to expand them in `library_sequence`.

Unknown biological payloads are annotated but not scored. Use `[CDNA]`,
`[GDNA]`, `[VDJ_INSERT]`, or `[SGRNA_SPACER]` in
`annotated_library_sequence` for source-visible payloads such as `XXX...XXX`,
`...-V-D-J-...`, or `[sgRNA-Spacer]`, and omit those regions from
`library_sequence`.

## Data Contract

- Raw inputs come from `sequencing/scg-protocols-v1`.
- Curated final library structures are expected in
  `sequencing/scg-oligo-groundtruth-v1` alongside the oligo ground truth.
- Each ground truth file should contain the same JSON shape as the prediction,
  with a `libraries` array and one `library_sequence` target per final library.
- `protocols.json` currently points every protocol at
  `<protocol_id>/groundtruth_final_lib_struct.json`; update `groundtruth_repo`
  or `groundtruth_revision` after upload if needed.

## Generate Harbor Tasks

```bash
PYTHONPATH=src python -m libstruct_bench.cli.generate_library_v0_tasks \
  --protocols benchmarks/library_structure_v0/protocols.json \
  --out benchmarks/library_structure_v0/tasks \
  --force
```

## Run v0.1 With OpenRouter

```bash
export OPENROUTER_API_KEY="<key>"

PYTHONPATH=src python -m libstruct_bench.cli.run_openrouter_library_v0 \
  --protocols benchmarks/library_structure_v0/protocols.json \
  --out runs/library_structure_v0/openrouter
```

Protocol files are attached directly in the chat request as data URLs by
default, without forcing OpenRouter's `file-parser` plugin. This tries native
file pass-through first. Use `--pdf-engine cloudflare-ai` or
`--pdf-engine mistral-ocr` only when you intentionally want OpenRouter to parse
PDFs before the model call. Use `--file-mode upload` only if you explicitly
want to exercise OpenRouter's separate `/api/v1/files` upload endpoint.

Use `--dry-run` to write request payloads without calling OpenRouter.

For reasoning models, pass an explicit effort level if provider defaults spend
too much completion budget on reasoning and return no JSON content:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.run_openrouter_library_v0 \
  --protocols benchmarks/library_structure_v0/protocols.json \
  --out runs/library_structure_v0/openrouter \
  --model openai/gpt-5.5 \
  --reasoning-effort minimal
```

## Run v0.2 With Harbor

```bash
export HF_TOKEN="<token-with-groundtruth-read-access>"

harbor run \
  -p benchmarks/library_structure_v0/tasks/${protocol} \
  -a codex \
  -m gpt-5.4
```

The verifier writes `/logs/verifier/reward.json` with `sequence_similarity` as
the primary metric.
