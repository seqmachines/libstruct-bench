# Linked T2/T3 protocol-understanding rules

Use only the files preloaded under `/workspace/input/`. Read every listed paper,
protocol, supplement, table, spreadsheet, figure, and diagram. Do not browse the
web, use remembered kit sequences, inspect legacy `scg_lib_structs` pages, or
look for benchmark ground truth or prior answers.

Reconstruct T2 oligos and T3 molecular library generation together in one
chronological pass:

1. Register an oligo in T2 when it first appears.
2. Identify the current molecular substrate.
3. Identify the sequence- or strand-changing operation.
4. Record the oligo IDs and major non-oligo reagents used by that operation.
5. Record the scientifically meaningful product and which product continues.
6. Reference the same local T2 oligo IDs from T3.

T3 must contain exactly one workflow per modality. Put `modality` on each
workflow, not at the document root. Keep alternative routes for the same
modality as branches with multiple final states in that one workflow.
Prefer one canonical modality: `gene expression`, `genomic DNA`,
`feature barcode`, `sgRNA`, or `chromatin accessibility`. Common prediction
aliases such as `RNA`, `scRNA-seq`, `gDNA`, `feature_barcode`, `ATAC`,
`scATAC`, and `chromatin_accessibility` are normalized for matching.

For PDFs, prefer native text, layout-sorted text blocks, and table cells. Use
Docling with OCR disabled, PyMuPDF, and pypdf as complementary parsers. Render
pages for visual inspection of figures and diagrams, but do not use OCR-derived
sequence text. Use OpenPyXL for spreadsheets and `antiword` for legacy Word
tables when applicable.

## T2

- Return one record per oligo family. When several physical oligos share the
  same fixed scaffold and differ only in a barcode, index, UMI, randomer, or
  other fixed-length variable region, write one molecule-level template, for
  example `FIXED_SCAFFOLD[BARCODE:8]FIXED_SCAFFOLD`. Do not enumerate the
  concrete panel members.
- Keep separate records when the fixed scaffold, molecular role, orientation,
  or chemical modification differs. A source table containing many barcode
  rows does not by itself require many T2 records.
- Give each family one local `oligo_id`. Every T3 transition and segment
  derivation that uses a member of that family must reference that family-level
  T2 ID, not a list of concrete member IDs.
- Preserve source-visible oligo sequences and their stated 5′→3′, 3′→5′, or
  unknown orientation. Do not silently reverse-complement or complete them.
- Use a concise, specific biological role. Names, aliases, roles, orientation,
  and modifications delimit scientifically distinct families; the
  molecule-level family sequence is the primary T2 score.
- Use canonical, orientation-free placeholders such as `[CELL_BARCODE:16]`,
  `[UMI:12]`, `[I5_INDEX:8]`, `[TN5_INDEX:8]`, and `[I7_INDEX:8]`. Never encode
  orientation in a role (`_RC`, `_FWD`, and similar suffixes are forbidden).
- Preserve assembled, double-stranded, and hairpin oligos with ordered
  components when one flat sequence would be misleading.
- Do not invent vendor or kit sequences absent from the provided sources.
- T3 provenance must reference the physical oligo that is used or incorporated
  during library generation. A platform sequencing primer with the same
  sequence is not a substitute for that oligo.

## T3

- Model a molecular state-transition graph, not a prose protocol summary.
- Use the smallest scientifically sufficient graph. Add a state or transition
  primarily when the carried product changes sequence architecture or strand
  structure. Fold cleanup, washes, pooling, QC, dilution, inactivation, and
  other non-sequence handling into the nearest substantive transition unless a
  distinct branch or carried molecular product is essential downstream.
- A displayed product is not a second transition. PCR cycling is one
  transition, not a graph cycle.
- Classify every product as carried forward or discarded. Every nonfinal
  carried product must later be a substrate. Final states must be reachable
  from initial states, and the graph must be acyclic.
- Record every physical strand independently in its own 5′→3′ orientation.
  Preserve overhangs, unpaired regions, nicks, gaps, RNA–DNA hybrids,
  Y-shaped adapters, and partial duplexes when the sources support them.
- `single_stranded` has exactly one unpaired strand.
  `rna_dna_hybrid` has exactly two logical strands, one RNA and one DNA.
  `double_stranded` and `y_shaped_duplex` have exactly two logical strands.
  `partially_duplex` has at least two strands and at least one genuinely
  unpaired region.
- Every segment labelled `paired_region` must occur in exactly one declared
  paired region unless the state is a documented mixed population. Do not mark
  unpaired random-primer, SMART, linker, overhang, or adapter regions as paired.
  Explicit canonical paired sequences must be reverse-complementary.
- Template-switching states are meaningful carried products, not transient
  reaction complexes. Represent the pre-switch state as the two-strand
  mRNA:cDNA hybrid. Treat the template-switch oligo as a reagent of the
  transition and represent its incorporated sequence in the cDNA product. Do
  not persist a third TSO strand unless a source explicitly says that complex
  is carried forward.
- Identify the reference strand for every state. For an oligo-derived segment,
  store bases on the modeled strand and record `same_orientation`,
  `reverse_complement`, or `unknown` relative to its T2 oligo.
- Before returning the graph, fact-check every state and transition directly
  against the primary PDFs, supplementary tables/spreadsheets, and relevant
  figures. Do not accept a plausible mechanistic step merely because adjacent
  predicted states are internally consistent.

## Output and validation

Write exactly:

- `/logs/artifacts/t2_prediction.json`
- `/logs/artifacts/t3_prediction.json`

The schemas are under `/workspace/schemas/benchmark/`. Prediction files must
not contain audit evidence, support status, lineage, reviewer decisions,
schema-version labels, or ground-truth-only protocol scope.

Before finishing, run:

```bash
PYTHONPATH=/workspace python -m libstruct_bench.cli.validate_libgen_predictions \
  --t2 /logs/artifacts/t2_prediction.json \
  --t3 /logs/artifacts/t3_prediction.json \
  --protocol-id "$LIBGEN_PROTOCOL_ID" \
  --schema-root /workspace/schemas
```

Repair representation or bookkeeping errors reported by this validator. Do
not change a scientifically supported sequence, orientation, oligo identity,
state count, or transition order merely to make two predictions agree.
