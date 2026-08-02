# Audit evidence policy

Only approved, manifest-listed files may support a scientific conclusion.
Online search, memory, and unlisted kit knowledge are out of scope.

## Isolation

Primary-evidence extraction may see only approved papers, protocols,
supplements, tables/spreadsheets, figures/diagrams, and deterministic
renditions. It must not see legacy HTML, current ground truth, reviewed TSV
rows, prior agent answers, or benchmark outputs.

After that evidence is frozen, comparison may see:

- legacy `scg_lib_structs` HTML and included assets;
- current T1, T2, and T3 records;
- the protocol-only projection of `groundtruth_oligos.tsv`;
- optional benchmark-run artifacts for error attribution.

Legacy and current curation establish the value being checked, not scientific
correctness. TSV projections retain original row numbers and the full-file
hash. Every included primary source appears once in the coverage ledger. An
unreadable source blocks comparison until a human repairs or reclassifies it.

## Extraction

- Read complete documents, sheets, tables, figures, diagrams, appendices, and
  alternate products.
- Review T2 and T3 chronologically: register an oligo when first seen, then
  reference it from each molecular transition.
- Create a T3 state only for a meaningful change in molecule type, strand or
  sequence architecture, barcode/UMI/index/adapter content, physical state,
  selected fraction, amplifiability, or workflow branch.
- Record every transition’s substrates, normalized operation, T2 oligos,
  major reagents, products, carried products, and important dead ends.
- A display-only paragraph or figure is not a transition. PCR cycling is one
  transition, not a graph cycle.
- Record reverse complements, assembly, placeholder normalization, and every
  other derivation. Preserve conflicts and do not invent missing sequences.

Support is `explicit`, `derivable`, `externally_completed`, `ambiguous`, or
`unsupported`. Confidence does not change evidentiary status.

## Comparison statuses

Each T1–T3 field is `verified_no_change`, `proposed_correction`,
`missing_source_evidence`, `ambiguous`, or `external_knowledge_required`.
Every non-verified status is preserved as a human-review issue. Only an exact
`proposed_correction` may carry a ground-truth patch.
