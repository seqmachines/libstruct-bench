# Conversion-first ground-truth audit

This is one audit pass, not a benchmark task and not an independent
primary-source reconstruction. Work in the following order.

## Stage 1 — convert existing human curation

First read only:

- packet-listed legacy `scg_lib_structs` HTML and available legacy assets;
- current T1, T2, and T3 JSON when present; and
- the protocol-only projection of `groundtruth_oligos.tsv`.

Do not open `primary_evidence/`, its renditions, benchmark-run artifacts, or use
remembered/external kit knowledge until the complete legacy-derived candidate
is fixed in your working context.

Convert the existing curation into the canonical T1, T2, and T3 schemas without
changing its scientific claims:

- T1 comes from the legacy/current final-library curation;
- T2 comes from current T2, the reviewed TSV projection, and legacy HTML;
- T3 comes primarily from the ordered molecular steps in legacy HTML. Use a
  current T3 record only when one exists, preserving the HTML as its human
  curation lineage;
- preserve exact legacy/current values and source locations in the audit issue;
- omit legacy-only schema, HTML/extraction, normalization, evidence, lineage,
  review-status, and audit-note fields from cleaned candidate JSON. In
  particular, omit `ground_truth_status`, T2 `baseline_lineage`, and T3
  `workflow_branch`;
- emit one `formatting_or_schema_error` root replacement issue for every
  legacy-shaped existing T1/T2/T3 record;
- when T3 JSON is absent but the HTML contains workflow steps, emit one
  `new_groundtruth_record` root-add issue for the HTML-derived T3 candidate;
- classify conversion or a missing JSON representation as migration/schema
  work, not a scientific human-curation error; and
- validate every complete root candidate against its embedded canonical schema.

For a root-converted artifact, later primary-source differences must be
separate, patch-free findings. The controller will compile accepted findings
into the reviewed root candidate. Do not silently blend corrections into the
Stage 1 conversion.

Keep candidates minimal: do not create a T1 `library_id`. T1 has one canonical
`library_sequence` and no duplicate `strands` or `annotated_library_sequence`. T2 uses
`name` plus `aliases`; components are ordered inline descriptions without
`component_id`; represent each connected T3 molecular process as one workflow,
store modality on each terminal in `final_outputs`, retain shared ancestors
once before modality-specific branches, use graph topology rather than
`workflow_branch`, and do not duplicate audit evidence.
Use `protocol_scope` only when a version or variant is known.
Use exactly one canonical modality for every T1 library and T3 final output:
`gene expression`, `genomic DNA`, `feature barcode`, `sgRNA`, or
`chromatin accessibility`. Do not use abbreviations, snake case,
protocol-specific phrases, or the generic value `library`.

Use biological, orientation-free placeholders such as `[I5_INDEX:8]`,
`[TN5_INDEX:8]`, and `[I7_INDEX:8]`; never encode orientation with `_RC`,
`_REVERSE`, or `_FWD`. Put strand-specific bases in T1/T3 segment `sequence`,
keep source-visible oligo bases in T2, and record their relationship with
`orientation_to_source` (`same_orientation`, `reverse_complement`, or
`unknown`).

## Stage 2 — verify the candidate against primary sources

Only after Stage 1 is complete, read every included primary paper, protocol,
supplement, spreadsheet/table, figure/diagram, and packet-listed rendition.
Account for every included primary source exactly once in `source_coverage`.
`source_coverage` is primary-only: do not add legacy HTML, current T1/T2/T3,
the TSV projection, renditions as separate sources, or benchmark-run artifacts.
Renditions are aids linked to their primary source, not coverage entries.
An unreadable included source blocks a validated proposal.

Check the legacy-derived candidate rather than constructing an unrelated second
answer. Review T2 and T3 chronologically: register oligos, follow substrates and
products, and verify T3 references to T2. Optional benchmark-run artifacts may
be read only for error attribution.

For each audited T1, T2, and T3 field assign exactly one status:

- `verified_no_change`
- `proposed_correction`
- `missing_source_evidence`
- `ambiguous`
- `external_knowledge_required`

Every non-verified status has an issue. Include the current and proposed value,
exact source locator, support status, category, severity, responsibility,
explanation, and impact. Use a JSON patch only for an exact correction to a
canonical non-root-converted ground-truth record. Source, policy, harness,
extraction, evaluator, and patch-free root-candidate deltas are still preserved
as issues.

Before returning, make the field ledger and issue list exactly reciprocal:

- every `issue_id` in `issues` appears in the `issue_ids` array of its
  referenced `audited_fields` entry;
- if several issues reference one field, that field lists every one of them;
- every ID listed by any field resolves to an issue; and
- `verified_no_change` fields have no issue IDs.

There must be no orphan issue and no dangling ledger reference. Compare the set
of all `issues[].issue_id` values with the union of all
`audited_fields[].issue_ids`; the two sets must be identical.

Keep human workload proportional to scientific impact. Group fields that share
one cause and decision. Do not create separate issues for optional aliases,
minor conditions, family counts, or descriptive metadata when the library
structure, oligo identity/sequence, and molecular workflow are unchanged.
Summarize such observations. Treat molecular or scoring conflicts as medium or
higher, and use `unresolved_scientific_ambiguity` when sources genuinely leave
the interpretation unresolved.

## T3 modeling and validator contract

Use the smallest scientifically sufficient molecular graph. Create a state or
transition primarily when sequence architecture or strand structure changes.
When neither changes, fold cleanup, purification, size selection, pooling,
washing, QC, quantification, dilution, routine reagent handling, and
inactivation into the nearest substantive transition. Add a non-sequence node
only for an essential branch or distinct carried-forward molecular product.
PCR cycling is one transition; a display-only paragraph or figure is not one.

States are meaningful carried-forward products, not transient reaction
complexes. For template switching, the pre-switch state is an mRNA:cDNA hybrid;
the TSO belongs in the template-switching transition's `oligo_ids`; the product
is cDNA containing the incorporated TSO-derived sequence. Do not persist a
third TSO strand unless a source explicitly says that complex is carried
forward.

Before returning the proposal, satisfy
`validate_molecular_state_architecture` in `groundtruth.py`. Every state must
set `strand_architecture` and obey this deterministic contract:

- `single_stranded` has exactly one strand and no paired region;
- `double_stranded` has exactly two strands, paired regions, and no unpaired
  segments;
- `partially_duplex` has at least one strand, a paired region, and at least one
  unpaired segment; it may use one strand when two disjoint arms pair
  intramolecularly to form a hairpin;
- `rna_dna_hybrid` has exactly two logical strands, one RNA and one DNA, and a
  paired region;
- `y_shaped_duplex` has exactly two logical strands, a paired region, and an
  unpaired arm on both strands;
- strand and segment IDs are unique, `reference_strand_id` resolves, and every
  strand is written independently 5′→3′;
- every segment labeled `paired_region` appears in one declared paired region;
  segments absent from paired regions are not labeled `paired_region`;
- each pairing side names nonempty contiguous, non-overlapping segments in its
  strand's 5′→3′ order; sides normally name different strands, but a
  `partially_duplex` intramolecular hairpin names two disjoint arms on the same
  strand;
- explicit `reverse_complementary` pairs are reverse-complementary; preserve a
  supported noncanonical or unknown relationship instead of changing source
  content; and
- every discontinuity references adjacent segments in 5′→3′ order.

Preserve genuinely unpaired random-primer, SMART, overhang, linker, and adapter
regions. Do not invent pairings. Do not change scientifically supported sequence,
strand identity, or architecture merely to pass validation.
For a source-supported hairpin, keep one physical strand and use the property
`hairpin` or `covalently_closed_dumbbell`. If hairpin-adaptor ligation creates a
closed dumbbell that is carried forward before enzymatic opening, preserve the
dumbbell and opened product as separate states and transitions because strand
architecture changes.

Validate all cross-task links: every T3 oligo resolves to T2, carried products
continue, final states are reachable, the graph is acyclic, scopes agree, and
terminal T3 states match T1 libraries one-to-one. Prefer a terminal reference
strand `sequence_architecture` equal to T1 `library_sequence` or its token-aware
reverse complement. Preserve the physical T3 strand in its actual 5′→3′
direction; do not rewrite it to follow T1's canonical display orientation.
T3 segment annotations may be simpler. If that string is absent, ordered
segments must match in the same or reverse-complement orientation. Do not create
T1 library IDs. Do not emit `final_library_links`.

For linked root candidates, use one coherent protocol scope across T1, T2, and
T3. Any root-level `protocol_scope` values that are present must have the same
`protocol_version` and the same `applicable_variants`. A child scope may only
narrow its parent, and scopes of linked libraries, oligos, states, and
transitions must overlap. If a legacy family page spans variants (for example,
one task includes a related protocol that the other tasks do not), do not
silently widen only one artifact. Keep the audited protocol's scope consistent
and report the family/version ambiguity as a review issue.

Return only the structured audit proposal. Propose changes, but never approve,
apply, or promote them.
