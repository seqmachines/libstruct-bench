# Frozen-evidence comparison phase

Read `frozen_evidence/evidence.json` first. It is immutable and was created
without legacy curation, current ground truth, prior agent answers, or external
knowledge. Then read every packet-listed comparison input:

- legacy `scg_lib_structs` HTML and included assets;
- current T1, T2, and T3 records when present;
- the protocol-only projection of `groundtruth_oligos.tsv`;
- optional benchmark-run artifacts used only for error attribution.

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

If a current T1, T2, or T3 file is absent, propose a complete new document only
when the frozen evidence supports it. Use the canonical task filename and one
root-level patch. Otherwise record missing, ambiguous, or external evidence
without inventing a document.

Do not approve or apply a proposal. A human reviewer is the final authority.
