# Defect report: comparison-worker output ceiling is unpinned and can terminate runs

Status: open. Filed from the `microwell_seq` audit, 2026-08-08. Engineering
hand-off — not a normative policy document.

## Summary

`run_claude_audit` spawns the comparison worker without pinning the model's
maximum output tokens. The installed CLI applies 64,000 for `claude-opus-5`.
Measured across all 33 comparison runs in `ground_truth_audit/runs/`, the
structured-output generation now reaches 96% of that ceiling on the largest
protocols, and four runs report exactly 64,000 with no recoverable artifact.

The ceiling is neither configured, recorded, nor detectable after the fact.

## Evidence

Structured-output tokens are the single `usage.iterations[]` entry on the
transcript's `result` record. Across every run with a transcript:

| Run | struct-output tokens | artifact |
|---|---:|---|
| share_seq/comparison-001.rejected | 64,000 | none |
| scrrbs/comparison-001.rejected | 64,000 | none |
| microwell_seq/comparison-003.rejected | 64,000 | none |
| ddseq_single_cell_3_rna_seq_kit/comparison-001.rejected | 64,000 | none |
| pip_seq_v4/comparison-001 | 61,179 | 166,134 B |
| petri_seq/comparison-001 | 59,692 | 146,359 B |
| split_seq/comparison-001.rejected | 56,571 | 163,983 B |
| indrop_v1/comparison-001.rejected | 55,823 | 139,868 B |
| ... 21 further runs, 25,778–50,198 | | artifact present |

- n = 29 completed generations below the cap; mean 37,961; max 61,179.
- Every run below the cap produced an artifact. Every run at the cap produced
  none.
- Artifacts run 99,676–167,234 B, i.e. roughly 2.9–3.9 bytes per output token.

The four at-cap runs, and the five other no-artifact runs, cost $26.24 in total
and yielded no proposal.

### Provenance of this evidence

The nine rejected runs that produced no artifact were deleted on 2026-08-08 to
reclaim 13.0 MB. Their measurements were extracted first into
`defect-max-output-tokens.evidence.json` beside this file: per run, the full
failure reason, `run_id`, packet/prompt/policy/schema/skill hashes, token usage
including every `usage.iterations[]` entry, the model's applied
`maxOutputTokens`, cost, turn and tool-call counts, and the transcript SHA-256
(verified against the recorded hash at extraction time, nine of nine matching).

The raw transcripts are therefore **not** recoverable; the digest is the record.
The ten rejected runs that did produce a `rejected-artifact.json` were retained
in full, per `docs/audit/README.md`.

## What the original evidence did not establish

All four 64,000-token runs also terminated on a `429` five-hour session limit,
and all four were first attempts inside the same quota-exhausted batch. Their
`failure.json` records the 429 as the reason. Truncation and quota exhaustion
are therefore **confounded in every observed instance**; this report does not
claim the ceiling caused those four failures.

What is independently established is the headroom: two protocols completed
within 5% of the cap. That alone makes the unpinned ceiling a latent failure
mode for the most complex protocols, whatever caused the four.

## Subsequent unconfounded confirmation — the ceiling does bind

On 2026-08-14, `hydrop_rna/comparison-006.rejected` supplied the distinguishing
case that the original campaign lacked. The retained run lasted 3444.009 s,
cost $14.286858, reported one structured-output iteration of exactly 64,000
tokens, and ended with the explicit CLI error:

> Claude's response exceeded the 64000 output token maximum.

There was no simultaneous 429 in the failure reason. The retained transcript's
`modelUsage` records `claude-opus-5.maxOutputTokens = 64000`, and the run has no
recoverable proposal. This demonstrates that the ceiling can independently
terminate a comparison run in practice. The earlier four at-cap runs remain
confounded; this later run resolves the general causal question without
retroactively reclassifying them.

## Earlier distinguishing test — truncation was not the cause in that run

The test was run on 2026-08-08: `microwell_seq/comparison-004`, same pinned
packet `comparison-001`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000` exported into
the child environment (verified present on the running process via `ps eww`),
launched 31 minutes into a fresh quota window.

It succeeded, and the numbers refute the truncation hypothesis:

| | comparison-003 (failed) | comparison-004 (passed) |
|---|---:|---:|
| structured-output tokens | 64,000 | **41,340** |
| total output tokens | 83,758 | 127,505 |
| `modelUsage.maxOutputTokens` | 64,000 | **64,000** |
| artifact | none | 143,045 B, validated |

Two conclusions:

1. **The same protocol on the same packet needed only 41,340 tokens when it ran
   cleanly — 35% below the old ceiling.** The 64,000 reading in comparison-003
   was therefore not a genuine size requirement. The at-cap readings are far
   more consistent with a generation interrupted by quota exhaustion and billed
   at the ceiling than with a proposal that genuinely did not fit.
2. **`CLAUDE_CODE_MAX_OUTPUT_TOKENS` did not change the applied ceiling.**
   `modelUsage` still reported 64,000 with the variable set to 128,000, yet the
   run passed regardless. The variable is not a working lever here, and should
   not be relied on as the remedy.

The likely root cause of those original nine no-artifact runs remains quota
exhaustion, not output truncation. See the batch-pacing analysis: the failing
batch launched its waves 49 minutes apart into an already-drained five-hour
window, whereas the clean batch spaced them 9h16m apart and completed 3x the
work without a single failure. That protocol-specific conclusion does not
contradict the later unconfounded `hydrop_rna` ceiling failure.

## Revised assessment

All three defects below now have direct operational evidence. The ceiling is
unpinned and unrecorded, and `hydrop_rna/comparison-006` proves that reaching it
can destroy an otherwise complete comparison run after nearly an hour and more
than $14 of generation. Priority is therefore high for large protocols.

The earlier environment-variable experiment still matters: setting
`CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000` did not change the CLI's recorded
64,000-token limit. Any remedy must verify the applied limit from the child
process rather than assuming that environment variable works in the installed
CLI.

## Defects

1. **Ceiling not pinned.** `_run_streaming` calls `subprocess.Popen(command,
   cwd=cwd, ...)` with no `env=`, so the worker inherits the ambient
   environment. `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (supported by CLI 2.1.226)
   silently changes worker behaviour between runs of an otherwise
   byte-identical pinned packet. This breaks the reproducibility contract that
   the rest of the harness enforces through packet, prompt, policy, schema and
   skill hashes.

2. **Ceiling not recorded.** Neither `run-metadata.json` nor `failure.json`
   captures the applied maximum. `run.tool_version` records the CLI version but
   not its output configuration, so a truncated run cannot be told apart from a
   validation failure by inspecting the run record.

3. **Truncation not detected or attributed.** A generation that stops at the
   ceiling surfaces only as a downstream schema-validation or transport error.
   Per `docs/audit/adjudication-policy.md` this class belongs to
   `agent_harness_or_context_error` with harness responsibility, but nothing in
   the runner makes that attribution, so it can be misread as agent reasoning
   error during checkpoint aggregation.

## Suggested remedy

- Add an explicit `max_output_tokens` parameter to `run_claude_audit`, default
  it well above the observed peak, and pass it to the child through an explicit
  `env` mapping rather than inheritance. `claude-opus-5` documents a 128,000
  output ceiling, and the runner already streams via
  `--output-format stream-json`, which is the precondition for large outputs.
- Record the applied value in `run-metadata.json` and `failure.json`, alongside
  the existing hash pins.
- After the run, compare each `usage.iterations[].output_tokens` against the
  applied ceiling; on equality, fail with an explicit truncation reason and set
  the confirmed cause to `agent_harness_or_context_error`.
- Consider warning when a successful generation exceeds ~85% of the ceiling, so
  the margin is visible before it is crossed.

## Affected protocols

Five protocols currently have no successful comparison run:
`microwell_seq`, `share_seq`, `scrrbs`, `ddseq_single_cell_3_rna_seq_kit`,
`dr_seq`. Four of the five recorded a 64,000-token first attempt. Their packets
remain intact and hash-pinned, so each can be re-run without rebuilding.
