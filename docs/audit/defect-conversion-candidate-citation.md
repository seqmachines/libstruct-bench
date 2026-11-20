# Defect report: the comparison prompt never states the evidence-citation invariant the validator enforces

Status: fixed in the audit harness. Filed from the
`10x_chromium_3_gene_expression_v1` audit,
2026-08-30. Engineering hand-off — not a normative policy document.

## Summary

The two-phase pipeline tells the comparison worker to **read** the frozen
`legacy_conversion_candidate` first, and never tells it that the candidate may
not be **cited** as evidence. `claude_runner.py` enforces exactly that, so a
worker that cites it is rejected deterministically after paying the full cost of
the run.

One rejection observed so far: `runs/10x_chromium_3_gene_expression_v1/
comparison-002.rejected`, **1270 s (21 min)**, ~$8, complete 7-issue artifact
discarded because **one** of the seven issues carried the inadmissible citation.

## The mismatch

`src/libstruct_bench/audit/claude_runner.py:1570` subtracts the conversion
candidate from admissible evidence:

```python
allowed_evidence_ids = {
    item["source_id"]
    for item in packet["files"]
    if item["role"] != "legacy_conversion_candidate"
}
...
cited_ids = {item["source_id"] for item in issue["evidence"]}
if not cited_ids.issubset(allowed_evidence_ids):
    raise ClaudeAuditError(
        f"issue {issue_id} cites sources outside the comparison packet"
    )
```

`.claude/prompts/audit-comparison.md` says only:

- l.10 — "First read: the packet-listed `legacy_conversion_candidate`;"
- l.76–78 — "`source_coverage` is primary-only: do not add legacy HTML, current
  T1/T2/T3, the TSV projection, renditions as separate sources, or
  benchmark-run artifacts."

The `source_coverage` restriction is stated. The `issues[].evidence[].source_id`
restriction is not stated anywhere. The worker is instructed to treat the
conversion as its starting claim, so citing it when an issue is *about* that
claim is the natural behaviour.

The rejected issue, `iss_umi_token_missing`, cited five source_ids. Four were
admissible. The fifth was
`10x_chromium_3_gene_expression_v1:legacy-conversion:legacy-conversion-001`.

## Why repair could not save it

`validation_repair` recorded `status: "not_attempted"`, `attempt_count: 0`. That
is correct: repair may change only the root candidate representation or
deterministic ledger linkage, and "source coverage, evidence, audited-field
conclusions, and issue identities and classifications are fixed". Dropping an
inadmissible citation edits evidence, so repair is forbidden from touching it.

## Suggested remedy

1. **Prompt (the actual fix).** In `audit-comparison.md`, beside the existing
   `source_coverage` rule, state that the `legacy_conversion_candidate` may be
   read and quoted in prose but must never appear in
   `issues[].evidence[].source_id`; evidence for a claim about the conversion
   should cite the underlying legacy HTML / current record instead.
2. **Optional, narrow repair exemption.** Consider making a wholly-inadmissible
   citation the one thing repair may drop. Removing a citation that the
   validator already refuses to accept cannot change an audited conclusion, and
   it converts a total loss of a 21-minute run into a bounded fix.
3. **Cheaper failure.** The check runs only at the end. Nothing detects the
   problem earlier, so the full generation cost is always paid before rejection.

## Attribution

Per `.claude/skills/audit-protocol/SKILL.md`: "When worker guidance omitted a
deterministic validator invariant and that omission caused a rejection,
attribute the failure to `agent_harness_or_context_error` with harness
responsibility and confirmed cause `agent_harness_or_context_error`, not to
human curation." This rejection is harness-attributed.

## Resolution

The comparison prompt, audit skill, and evidence policy now say explicitly
that a frozen conversion is readable starting context but is never an
`issues[].evidence[].source_id`. Claims about the starting value cite the
packet-listed legacy/current lineage; scientific corrections cite primary
evidence.

The validator now identifies this exact failure as a bounded repair case only
when the affected issue already retains admissible evidence. Repair scope may
delete the complete frozen-conversion citation entry and nothing else: it may
not add, substitute, reorder, rewrite, or relocate evidence, and it may not
leave an issue without admissible evidence. Regression coverage exercises both
the allowed deletion and rejected evidence mutations.

The same investigation exposed a separate consistency gap: the frozen T1 and
terminal T3 strings both omitted `[UMI:10]` while their ordered segments
declared it. Canonical validation now checks each complete assembled string
against its own token-aware ordered segment projection before T1 and T3 are
compared. Literal IUPAC spans may satisfy fixed-length placeholders, and
modified nucleotide notation retains its underlying base. The 40 promoted
records pass this rule; among the 33 new conversions it isolates the T1 and T3
terminal records for this protocol. The compatibility matcher requires every
declared segment to map contiguously from the 5' end while allowing a legacy
trailing unsegmented suffix; new conversions remain instructed to segment the
complete molecule.

## Related, and worth fixing in the same pass

`docs/audit/defect-max-output-tokens.md` withdrew the truncation hypothesis
after a distinguishing test. On 2026-08-14 a clean, unconfounded instance
finally occurred: `runs/hydrop_rna/comparison-006.rejected` ran 3444 s, cost
$14.29, and died on `Claude's response exceeded the 64000 output token maximum.
To configure this behavior, set the CLAUDE_CODE_MAX_OUTPUT_TOKENS environment
variable.` That document's "Revised assessment" section should be updated: the
ceiling is now demonstrated to bind in practice, not merely to be unpinned.
