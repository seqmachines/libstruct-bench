# Deterministic comparison-artifact repair

This is a bounded repair pass over a complete comparison artifact that failed
local deterministic validation. It is not a new scientific audit.

Read only `repair-input.json`. Do not open the comparison packet, protocol
sources, renditions, current records, the web, memory, or any other file. The
input contains the complete failed artifact and the exact validator error from
the current validation gate.

Make the smallest change necessary to resolve that error:

- preserve `source_coverage`, the audit summary and disposition;
- preserve the set and identity of audited fields, their comparison statuses,
  and every field other than `issue_ids`;
- preserve the set of issues and each issue's task, field, category, defect,
  responsibility, severity, target, current value, evidence, transformations,
  explanation, recommendation, confidence, and notes;
- do not add or remove a finding, reinterpret evidence, change a source
  locator, or alter an issue conclusion;
- change a complete T1/T2/T3 root candidate only inside the corresponding
  issue's `proposed_value` and root `proposed_patch`, and only when required by
  the reported schema or linked-candidate error;
- keep a repaired root `proposed_value` exactly equal as JSON to the value in
  its root patch;
- change `audited_fields[].issue_ids` only to repair deterministic ledger
  consistency; and
- do not change scientifically supported sequence content merely to make a
  validator pass. Reconcile only an inconsistency already present in the
  artifact, such as a missing declared pairing for segments already marked as
  paired.

Return the entire repaired comparison artifact, not a patch or explanation.
The harness will re-run the full audit schema, ground-truth schemas, semantic
checks, and linked T1/T2/T3 validation. It will reject changes outside this
scope and preserve this attempt for provenance. Never approve, apply, or
promote a proposal.
