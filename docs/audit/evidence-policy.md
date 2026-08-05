# Audit evidence policy

Every discovered file that is present is automatically included and
content-addressed in the manifest. Missing files are recorded as unavailable and
excluded from packets. Only included, manifest-listed files may support a
scientific conclusion. Online search, memory, and unlisted kit knowledge are out
of scope.

## Isolation

Primary-evidence extraction may see only included papers, protocols,
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
- Prefer the smallest scientifically sufficient T3 graph. Create states and
  transitions primarily for changes in molecular sequence architecture or
  strand structure, including barcode, UMI, index, or adapter acquisition.
- By default, do not create a new state or transition when sequence architecture
  and strand structure are unchanged. Fold cleanup, purification, size
  selection, pooling, washing, QC, quantification, dilution, routine reagent
  addition, incubation, quenching, and protein inactivation into the nearest
  substantive transition's operation detail and major reagents. Preserve its
  supporting details and locators in the audit evidence artifact rather than
  copying them into cleaned T3.
- Use a separate non-sequence state or transition only when it is essential to
  represent a distinct workflow branch or a carried molecular product needed
  to understand downstream library generation. Explain that exception. Do not
  enumerate incidental discarded fractions or procedural details; retain only
  products that matter to molecular interpretation, selection, or branching.
- Record every transition’s substrates, normalized operation, T2 oligos,
  major reagents, products, carried products, and important dead ends.
- A display-only paragraph or figure is not a transition. PCR cycling is one
  transition, not a graph cycle.
- Record reverse complements, assembly, placeholder normalization, and every
  other derivation. Record `orientation` for every represented T1 and T2
  sequence, using `unknown` when the source does not establish it. Preserve
  conflicts and do not invent missing sequences.
- Keep placeholder roles biological and orientation-free. Use canonical
  `[ROLE:LENGTH]` values such as `[I5_INDEX:8]`, `[TN5_INDEX:8]`, and
  `[I7_INDEX:8]`; never create `_RC`, `_REVCOMP`, `_REVERSE`, `_FWD`, or other
  directional role variants. Store strand-specific bases in the segment
  `sequence`, retain source-visible bases in the linked T2 oligo `sequence`,
  and record their `orientation_to_source` separately.
- Every T3 state uses `strand_architecture`: `single_stranded`,
  `double_stranded`, `partially_duplex`, `rna_dna_hybrid`,
  `y_shaped_duplex`, `mixed_population`, or `unknown`. Never infer single- or
  double-strandedness from the operation name alone.
- Represent one explicit strand for a single-stranded state and both strands
  for a duplex. Write each physical strand independently in its own 5′→3′
  direction, list its segments in that same order, and identify the
  `reference_strand_id` that follows the canonical T1 strand. Split segments at
  paired/unpaired boundaries. Each paired-region side lists contiguous segment
  IDs in its strand's own 5′→3′ order. Use those links plus structural roles to
  preserve overhangs, internal unpaired regions, partial duplexes, Y-shaped
  adapters, and RNA–DNA hybrids. Record nicks, gaps, and breaks as strand
  discontinuities.
- For paired regions with explicit canonical sequences, the two 5′→3′ strings
  must be reverse complements. Preserve documented noncanonical pairing or an
  unknown relationship explicitly; do not alter a source sequence to make it
  pair. For an oligo-derived segment, record its T2 ID and set
  `orientation_to_source` to `same_orientation`, `reverse_complement`, or
  `unknown` relative to the source oligo.
- For every T3 final state matched to T1, prefer a complete
  `sequence_architecture` on its identified reference strand, copied exactly
  from the matching T1 annotated or plain library sequence. That strand's T3
  segment annotations may remain simpler than T1. When the complete
  architecture is absent, its ordered segment representation must match T1
  exactly. Matching is a unique one-to-one assignment by reference-strand
  structure and protocol scope; do not store T1 library IDs or a separate link
  table.

Evidence, provenance lineage, review decisions, inclusion status, and audit
notes belong to audit artifacts. Do not copy those fields into approved T1,
T2, or T3 JSON.

Support is `explicit`, `derivable`, `externally_completed`, `ambiguous`, or
`unsupported`. Confidence does not change evidentiary status.

## Comparison statuses

Each T1–T3 field is `verified_no_change`, `proposed_correction`,
`missing_source_evidence`, `ambiguous`, or `external_knowledge_required`.
Every non-verified status is preserved as a human-review issue. Only an exact
`proposed_correction` may carry a ground-truth patch.
