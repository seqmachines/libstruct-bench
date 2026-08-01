# Frozen-evidence comparison phase

Read `frozen_evidence/evidence.json` first. It is immutable. Then inspect every
packet-listed legacy HTML page, current benchmark record, primary source, and
optional benchmark-run artifact.

For every T1, T2, and T3 field:

1. Record a field-ledger entry even when the current value agrees.
2. Compare primary evidence, legacy curation, current records, and optional
   agent/evaluator artifacts without treating any one of them as automatic
   truth.
3. Emit every possible defect or ambiguity with exact current/proposed values,
   source locations, support status, severity, category, defect type, and
   responsibility attribution.
4. Distinguish ground-truth corrections from source-bundle, policy, harness,
   model-reasoning, extraction, and evaluator problems. Only an exact
   ground-truth correction may carry a JSON patch.
5. Preserve multiple final products, conflicting protocol versions,
   schematic-versus-final-product disagreements, strand alternatives, and
   unresolved scientific ambiguity.
6. For T2, compare the protocol-only `groundtruth_oligos.tsv` projection as a
   non-authoritative baseline. Preserve each `source_row_number`, the full TSV
   `source_sha256` from `packet.json`, and old name/sequence in
   `baseline_lineage` when proposing the audited T2 document.

When no current T3 artifact exists, audit that absence explicitly. If the
evidence supports a complete workflow artifact, propose one
`new_groundtruth_record` issue using source ID `new-t3`, filename
`groundtruth_library_generation_workflow.json`, JSON pointer `""`, and one
root-level `add` patch containing the complete proposed document. It remains
only a proposal until a human accepts or modifies it.

Do not approve a proposal. A human reviewer will decide every issue.
