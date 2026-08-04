# Frozen-evidence comparison phase

Read `frozen_evidence/evidence.json` first. It is immutable and was created
without legacy curation, current ground truth, prior agent answers, or external
knowledge. Then read every packet-listed comparison input:

- legacy `scg_lib_structs` HTML and included assets;
- current T1, T2, and T3 records when present;
- the protocol-only projection of `groundtruth_oligos.tsv`;
- optional benchmark-run artifacts used only for error attribution.

If current T3 JSON is absent and the legacy HTML contains a human-curated
step-by-step workflow, build the initial T3 candidate from that HTML before
comparing it with frozen primary evidence:

- translate the legacy steps in document order into molecular states and
  transitions;
- preserve legacy wording and exact HTML locators on every candidate state and
  transition;
- use current T1/T2 identifiers only as an explicit normalization mapping;
- keep every primary-source correction, addition, conflict, or unsupported
  legacy claim as a separate issue rather than folding it into the candidate;
- classify the absent JSON record as a migration/schema omission, not an
  original human-curation error;
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
7. Check the linked design: T3 oligo IDs resolve to T2, final T3 states link to
   T1 libraries, carried products continue downstream, final states are
   reachable, the graph is acyclic, scopes agree, and terminal states match T1.

Keep human workload small:

- create one issue per human decision, not one issue per observation or field;
- group related fields when they share one cause and remedy;
- do not create separate issues for optional aliases, experimental conditions,
  family sizes, or descriptive metadata when the scientific and scoring values
  are unchanged; summarize them in `notes` instead;
- reserve medium/high severity for findings that could change ground truth,
  molecular interpretation, scoring, inclusion, or cross-task consistency;
- classify an unresolved conflict affecting molecular interpretation as
  `unresolved_scientific_ambiguity`, rather than a low generic source conflict;
  and
- preserve genuine low findings when needed for audit history, but make clear
  that they are informational and do not propose a change.

If current T1 or T2 is absent, propose a complete new document only when frozen
evidence supports it. For missing T3, follow the legacy-first rule above when
legacy workflow curation exists; otherwise use frozen evidence. Use the
canonical task filename and one root-level patch. Otherwise record missing,
ambiguous, or external evidence without inventing a document.

Do not approve or apply a proposal. A human reviewer is the final authority.
