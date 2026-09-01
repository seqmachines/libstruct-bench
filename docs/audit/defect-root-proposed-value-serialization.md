# Defect report: root issues serialize prose into `proposed_value`, and repair cannot converge on it

Status: fixed in harness 0.8.0 (Defect 2 withdrawn and corrected 2026-08-30
after human review).
Filed from the `10x_chromium_3_gene_expression_v1` audit,
2026-08-30. Engineering hand-off — not a normative policy document.

## Summary

The comparison worker wrote a **prose description** into
`issues[].proposed_value` on all three root-conversion issues, where the
validator requires the same complete document that appears in
`proposed_patch[0].value`. Bounded repair then ran twice, failed to fix it, and
the run was rejected after 2186 s (36 min).

Observed at `runs/10x_chromium_3_gene_expression_v1/comparison-003.rejected`.
This was the **third consecutive rejection** of this protocol, each for a
different harness reason (429 quota, inadmissible citation, this).

## Defect 1 — the worker writes prose where a document is required

Rejection message:

```
root candidate iss.t3.root must serialize the same complete document
in proposed_value and proposed_patch
```

The artifact's shape:

```
iss.t3.root
  proposed_value       -> str          (a description of the document)
  proposed_patch[0]    -> {"op": "add", "path": "", "value": {...}}
  proposed_patch value -> dict         keys: protocol_id, protocol_name,
                                             protocol_scope, workflows
```

All **three** root issues had `proposed_value` as `str`:

```
[0] iss.t1.root   proposed_value = str
[1] iss.t2.root   proposed_value = str
[2] iss.t3.root   proposed_value = str    <- the one the error named
```

`.claude/prompts/audit-comparison.md` does not state that a root issue's
`proposed_value` must be the complete document rather than a summary of it. As
with the frozen-conversion citation defect, the invariant is enforced but never
stated, so the natural behaviour is wrong.

## Defect 2 (WITHDRAWN) — repair did NOT rewrite prose as prose

**This section originally claimed the repair worker replaced one prose
description with another. That was wrong, and the error was mine.** Corrected
2026-08-30 after human review.

What repair actually did, read from the retained attempt artifacts:

```
attempt-001  changed /issues/0/proposed_value  ->  iss.t1.root  becomes a dict
                                                   keys: libraries, limitations,
                                                   protocol_id, protocol_name, protocol_scope
attempt-002  changed /issues/1/proposed_value  ->  iss.t2.root  becomes a dict
                                                   keys: oligos, protocol_id,
                                                   protocol_name, protocol_scope
                                                   iss.t3.root  never reached
```

Repair converted T1 correctly on attempt 1 and T2 correctly on attempt 2. It
behaved exactly as designed. `iss.t3.root` was still prose only because the
budget ran out before a third attempt.

The original analysis inspected `iss.t3.root` in *both* attempts' artifacts,
observed a string each time, and generalised — but that issue was never the one
under repair in either attempt. The correct check is the issue named in each
attempt's `changed-paths.json`.

`.claude/prompts/audit-comparison-repair.md` already carries the right
instruction: "keep a repaired root `proposed_value` exactly equal as JSON to the
value in its root patch". No repair-prompt change is needed.

## Defect 2 (actual) — the validator reports one root mismatch at a time

The validator surfaces a single failing root issue per pass, so N malformed root
issues require N repair attempts even though the fix is identical and mechanical
for each.

## Defect 3 — the attempt budget cannot cover the defect count

`max_attempts` is 2; this run had 3 malformed root issues. Combined with the
one-at-a-time reporting above, the run was **unrepairable by construction** —
not through any fault of the repair worker.

## Suggested remedy

1. **Prompt.** State in `audit-comparison.md` that for any root conversion or
   new-document issue, `proposed_value` must be the identical complete document
   serialized in `proposed_patch[0].value` — not a summary, not a hash
   reference, not prose.
2. **Validator reporting.** Report *all* root serialization mismatches in one
   pass rather than one at a time, so a single repair attempt can fix them
   together. This is the highest-value change: it alone would have made this run
   repairable.
3. **Repair budget.** Alternatively (or additionally) count attempts per
   *distinct defect class* rather than per run. With one-at-a-time reporting and
   `max_attempts` = 2, any run with 3+ same-class mismatches cannot be repaired
   however well the repair worker performs.
   NOT needed: a repair-prompt change. `audit-comparison-repair.md` already
   instructs repair to keep a repaired root `proposed_value` exactly equal as
   JSON to its root patch value, and repair demonstrably followed it.
4. **Cheaper failure.** Like the citation check, this runs only at the end, so
   the full generation cost is paid before rejection. A pre-serialization
   self-check in the worker would catch it for free.

## Resolution

Harness 0.8.0 removes the redundant serialization requirement from staged
comparisons. The comparison worker audits the frozen phase-1 candidates but
returns only scientific delta findings. The deterministic harness then attaches
the exact hash-pinned root replacement/add envelope and mirrors each complete
candidate in `proposed_value` and the root patch in one pass. If a legacy worker
does emit an exact staged root, the harness normalizes its envelope; if its root
candidate differs from the frozen bytes, validation still rejects it.

Complete runs rejected under the old invariant can be recovered with
`libstruct-revalidate-claude-audit` against their original frozen packet. This
path makes no model call and writes a separate hash-pinned revalidation receipt;
the rejected history remains immutable.

This is deliberately narrower than general auto-repair. The generated artifact
is retained, the normalization paths and digests are recorded, and the harness
does not invent or repair scientific issues. Thus the four same-class
serialization failures are eliminated without allowing an incomplete proposal
to pass.

## Attribution

Per `.claude/skills/audit-protocol/SKILL.md`: worker guidance omitted a
deterministic validator invariant and that omission caused the rejection, so
this is `agent_harness_or_context_error` with harness responsibility, not human
curation.

## Cost note

`10x_chromium_3_gene_expression_v1` alone has now consumed two legacy-conversion
runs and three comparison runs. Its scientific content was sound in every
rejected artifact — the 9-issue proposal from `comparison-003` was reviewed and
adjudicated in full, and its findings independently reproduced an earlier
17-node adversarial fact-check. Every rejection was a serialization or
provenance envelope fault, never a scientific one.
