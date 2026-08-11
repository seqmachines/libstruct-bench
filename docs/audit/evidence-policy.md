# Audit evidence policy

Every discovered file that is present is automatically included and
content-addressed in the manifest. Missing files are recorded as unavailable and
excluded from packets. Only included, manifest-listed files may support a
scientific conclusion. Online search, memory, and unlisted kit knowledge are out
of scope.

## Isolation

The audit uses one conversion-first comparison worker, not a separate
primary-evidence reconstruction phase. Within that worker, the order is strict:

1. Read only legacy `scg_lib_structs` HTML and included assets, current T1/T2/T3
   records, and the protocol-only `groundtruth_oligos.tsv` projection. Convert
   those human-curated inputs into canonical candidates without changing their
   scientific claims. T3 comes primarily from the ordered HTML workflow.
2. Only after the conversion is complete, read all included primary papers,
   protocols, supplements, tables/spreadsheets, figures/diagrams, and
   deterministic renditions. Verify the candidate against those sources.
3. Read optional benchmark-run artifacts only for error attribution.

The worker must not use online search, remembered kits, prior agent answers, or
review memory. Primary-source findings may propose changes but must not be
silently folded into the legacy-derived conversion.

Legacy and current curation establish the value being checked, not scientific
correctness. TSV projections retain original row numbers and the full-file
hash. Every included primary source appears once in the proposal's coverage
ledger. The coverage ledger is primary-only: legacy HTML, current
records, TSV projections, renditions, and benchmark-run artifacts are not
separate coverage entries. An unreadable source blocks review until its input
is repaired.

## Deterministic validation repair

A complete comparison artifact that fails the audit schema, a canonical
T1/T2/T3 schema, or linked T1/T2/T3 validation may receive at most two bounded
repair attempts. A repair worker receives only the failed artifact and exact
validator errors. It must not open the source packet, primary evidence,
renditions, current records, online sources, or remembered knowledge.

Repair is representational, not evidentiary. It may reconcile only fields
needed by the reported deterministic failure, such as declaring a pairing for
segments the artifact already labels as paired. It must preserve source
coverage, evidence and locators, audited-field statuses, issue identities and
classifications, current values, recommendations, explanations, and scientific
conclusions. It cannot add or remove findings. The harness re-runs all schema,
semantic, and linked validation after each attempt and preserves the complete
attempt provenance. A repaired proposal remains subject to human review.

## Extraction

- Read complete documents, sheets, tables, figures, diagrams, appendices, and
  alternate products.
- Review T2 and T3 chronologically: register an oligo when first seen, then
  reference it from each molecular transition.
- Build exactly one T3 workflow per modality represented in T1. Preserve
  alternative routes for the same modality as branches and multiple final
  states inside that workflow; do not split them into duplicate workflows.
- T3 `oligo_ids` and segment `oligo_derivations` identify the physical oligo
  used or incorporated during library generation. Do not substitute a platform
  sequencing primer merely because its sequence matches the resulting segment.
- Prefer the smallest scientifically sufficient T3 graph. Create states and
  transitions primarily for changes in molecular sequence architecture or
  strand structure, including barcode, UMI, index, or adapter acquisition.
- By default, do not create a new state or transition when sequence architecture
  and strand structure are unchanged. Fold cleanup, purification, size
  selection, pooling, washing, QC, quantification, dilution, routine reagent
  addition, incubation, quenching, and protein inactivation into the nearest
  substantive transition's operation detail and major reagents. Preserve its
  supporting details and locators in the audit proposal rather than
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
- Use `partially_duplex` for a source-supported intramolecular hairpin. Record
  the molecule as one physical strand, put the two disjoint stem arms on the
  two sides of a paired region with the same `strand_id`, and retain the loop as
  unpaired. Use the state property `hairpin` or
  `covalently_closed_dumbbell` as appropriate. Same-strand pairing is not valid
  for the other controlled architecture classes. Hairpin-adaptor ligation and
  subsequent enzymatic opening are separate transitions when the closed
  dumbbell is a meaningful carried-forward intermediate, because opening it
  changes strand architecture even if the base inventory is unchanged.
- For paired regions with explicit canonical sequences, the two 5′→3′ strings
  must be reverse complements. Preserve documented noncanonical pairing or an
  unknown relationship explicitly; do not alter a source sequence to make it
  pair. For an oligo-derived segment, record its T2 ID and set
  `orientation_to_source` to `same_orientation`, `reverse_complement`, or
  `unknown` relative to the source oligo.
- For every T3 final state matched to T1, prefer a complete
  `sequence_architecture` on its identified reference strand. Preserve the
  physical strand in its actual 5′→3′ direction: it may equal the matching T1
  `library_sequence` or its token-aware reverse complement. Placeholder tokens
  remain opaque while their order reverses. T1 has no separate
  `annotated_library_sequence`; its one canonical sequence retains biological
  insert locations with placeholders such as `[CDNA]`. That strand's T3 segment
  annotations may remain simpler than T1. When the complete
  architecture is absent, its ordered segment representation must match T1 in
  the same or reverse-complement orientation. Matching is a unique one-to-one
  assignment by reference-strand structure and protocol scope; do not store T1
  library IDs or a separate link table.

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

The field ledger and issue list are reciprocal. Every issue is referenced by
the `issue_ids` array of its audited field, every ledger issue ID resolves, and
the union of ledger IDs equals the set of proposal issue IDs. A
`verified_no_change` field has no issue IDs.

Linked T1, T2, and T3 candidates use a coherent scope. Root-level
`protocol_scope` values, when present, have identical `protocol_version` and
`applicable_variants`; child scopes may narrow but not widen their parent, and
linked object scopes overlap. A legacy page that covers a protocol family does
not justify widening only one task artifact; preserve the audited protocol's
scope and report the family/version ambiguity for review.
