# Frozen-evidence comparison phase

Read `frozen_evidence/evidence.json` first. It is immutable and was created
without legacy curation, current ground truth, prior agent answers, or external
knowledge. Then read every packet-listed comparison input:

- legacy `scg_lib_structs` HTML and included assets;
- current T1, T2, and T3 records when present;
- the protocol-only projection of `groundtruth_oligos.tsv`;
- optional benchmark-run artifacts used only for error attribution.

## Stage 1 conversion gate

Before scientific comparison, inspect every current T1, T2, and T3 JSON against
the embedded canonical schema. A legacy-shaped record is input curation, not a
canonical candidate. Convert it without changing its scientific claims:

- derive the conversion only from the current record, legacy HTML, and reviewed
  TSV projection; do not silently add a primary-source correction;
- preserve legacy values and locations in the audit issue and lineage, but omit
  legacy-only `schema_version`, HTML/extraction, and normalization fields from
  the cleaned candidate;
- keep evidence, lineage, review decisions, inclusion status, limitations, and
  audit notes in audit artifacts. Cleaned T1 never contains `evidence`,
  `ground_truth_status`, `library_id`, or `strands`; cleaned T2 never contains
  `limitations`, `baseline_lineage`, `evidence`, `ground_truth_status`, or
  `notes`; cleaned T3 never contains `limitations`, `ground_truth_status`,
  `notes`, `evidence`, or `workflow_branch`;
- do not create a T1 `library_id` or duplicate `strands` collection. T1 stores
  one canonical final-library strand; T3 stores complete molecular strands;
- store T3 `modality` once at the document root. Do not repeat it on workflows
  or states, and use graph topology instead of a `workflow_branch` label;
- use `name` plus `aliases` for T2 source-visible names; do not add redundant
  `source_name` or `source_names` properties;
- use `orientation` for T1 and T2; in T3, replace state-level orientation and
  strand-state shortcuts with explicit 5′→3′ `strands`, a controlled
  `strand_architecture`, and a resolving `reference_strand_id`;
- normalize variable-region placeholders to biological `[ROLE:LENGTH]` names
  such as `[I5_INDEX:8]`, `[TN5_INDEX:8]`, and `[I7_INDEX:8]`; never encode
  orientation with `_RC`, `_REVERSE`, `_FWD`, or similar role suffixes;
- keep `protocol_scope` only when the sources identify an applicable version or
  variant; child records inherit it and do not repeat it unnecessarily;
- emit exactly one `formatting_or_schema_error` issue with a complete root
  `replace` patch for each legacy-shaped existing record; classify this as
  conversion/migration, not a scientific human-curation error; and
- ensure the complete replacement validates against its canonical task schema.

The root conversion is a candidate requiring human approval. For a
root-converted artifact, report all primary-source disagreements as separate
patch-free findings. Do not also emit field patches against that artifact.
After adjudication, the controller compiles accepted findings into the one
reviewed root replacement.

If current T3 JSON is absent and the legacy HTML contains a human-curated
step-by-step workflow, build the initial T3 candidate from that HTML before
comparing it with frozen primary evidence:

- prefer the smallest scientifically sufficient graph; create states and
  transitions primarily for changes in sequence architecture or strand
  structure, and fold cleanup, purification, size selection, pooling, washing,
  QC, quantification, dilution, routine reagent handling, and inactivation into
  the nearest substantive transition when those are unchanged;
- add a separate non-sequence node only when a distinct branch or carried
  molecular product is essential downstream, and explain why; do not enumerate
  incidental fractions, dead ends, or minor procedural details;
- translate the legacy steps in document order into molecular states and
  transitions;
- preserve every strand actually shown or described by legacy curation. Do not
  collapse a duplex, partial duplex, hybrid, Y-shaped construct, overhang, or
  nick into a one-strand sequence merely to simplify conversion;
- preserve legacy wording and exact HTML locators in the audit issue and
  lineage, not as evidence or notes fields on candidate states and transitions;
- use current T2 identifiers only as an explicit normalization mapping. Do not
  invent a T1 library ID;
- keep every primary-source correction, addition, conflict, or unsupported
  legacy claim as a separate issue rather than folding it into the candidate;
- classify the absent JSON record as a migration/schema omission, not an
  original human-curation error;
- construct every complete new T1, T2, or T3 document using the canonical
  ground-truth schema embedded in the system instructions, with no legacy-only
  keys or missing required fields;
- never infer content from a missing HTML-linked asset; and
- do not attach sequencing-primer T2 records to T3 merely to make every T2
  record referenced, because sequencing is downstream of library generation.

For every T1, T2, and T3 field:

1. Record a field-ledger entry for each current scientific field and each
   source-supported field missing from the current records.
2. Assign exactly one status: `verified_no_change`, `proposed_correction`,
   `missing_source_evidence`, `ambiguous`, or `external_knowledge_required`.
3. Compare the frozen evidence with the existing curation without treating
   either the human curation or the audit agent as automatic truth.
4. Emit an issue for every status except `verified_no_change`. Include exact
   current/proposed values, source locations, support status, category,
   severity, responsibility, explanation, and impact.
5. Use a JSON patch only for an exact T1, T2, or T3 ground-truth correction.
   Source-bundle, policy, harness, extraction, prediction, and evaluator
   defects are issues but never ground-truth patches.
6. Preserve multiple final products, source conflicts, protocol-version
   conflicts, strand alternatives, schematic-versus-final-product differences,
   and unresolved scientific ambiguity.
7. Check the linked design: T3 oligo IDs resolve to T2, carried products
   continue downstream, final states are reachable, the graph is acyclic,
   scopes agree, and terminal T3 states match T1 libraries one-to-one by
   reference-strand structure. Do not emit `final_library_links` or T1 library
   IDs.
   Every T3 state must represent its actual strands: one explicit 5′→3′ strand
   for a single-stranded state, both explicit 5′→3′ strands for a duplex, with
   each strand's segments ordered 5′→3′, and
   structured `paired_regions`, overhang/unpaired segment roles, and
   `discontinuities` for partial duplexes, RNA–DNA hybrids, Y-shaped products,
   nicks, and gaps. Do not assume a generic single or double strand when the
   source is silent; use `unknown` and report the missing evidence.
   For each terminal T3 state, put `sequence_architecture` on the strand
   named by `reference_strand_id` and make it exactly a matching T1
   `annotated_library_sequence` or `library_sequence` (prefer the annotated
   value). That reference strand's segment annotations may remain useful
   simplifications and need not duplicate T1's decomposition. If its complete
   `sequence_architecture` is omitted, its segment list and T1 must instead
   match exactly in order, role, sequence/length/placeholder, and orientation.
   For oligo-derived segments, store bases as they occur on the modeled strand
   in `sequence`, leave the source-visible sequence in the linked T2 record,
   and set `orientation_to_source` to `same_orientation`,
   `reverse_complement`, or `unknown`. Never encode that relationship in the
   placeholder role.

Keep human workload small:

- create one issue per human decision, not one issue per observation or field;
- group related fields when they share one cause and remedy;
- do not create separate issues for optional aliases, experimental conditions,
  family sizes, or descriptive metadata when the scientific and scoring values
  are unchanged; summarize them in the audit issue instead of adding cleaned
  ground-truth fields;
- reserve medium/high severity for findings that could change ground truth,
  molecular interpretation, scoring, inclusion, or cross-task consistency;
- classify an unresolved conflict affecting molecular interpretation as
  `unresolved_scientific_ambiguity`, rather than a low generic source conflict;
  and
- preserve genuine low findings when needed for audit history, but make clear
  that they are informational and do not propose a change.

Do not report cleanup, purification, size selection, QC, dilution, washing,
inactivation, or another routine preparation detail as a missing standalone T3
state or transition when sequence architecture and strand structure are
unchanged. Preserve it on the nearest substantive transition. A separate
non-sequence node requires a distinct branch or carried molecular product that
is essential downstream and an explicit explanation.

If current T1 or T2 is absent, propose a complete new document only when frozen
evidence supports it. For missing T3, follow the legacy-first rule above when
legacy workflow curation exists; otherwise use frozen evidence. Use the
canonical task filename and one root-level patch. Otherwise record missing,
ambiguous, or external evidence without inventing a document.

Do not approve or apply a proposal. A human reviewer is the final authority.
