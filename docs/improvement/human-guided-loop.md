# Offline human-guided improvement loop

This condition reuses the 25 frozen C0 training outputs from the completed
autonomous experiment. It does not rerun each training protocol after every
pack update. Therefore it is an offline human-guided comparison, not online
learning and not a pure causal estimate of the human contribution.

The implementation keeps the standard cumulative execution labels C0, C5,
C10, C15, C20, and C25 so the existing validators, pack application, exemplar
projection, and checkpoint machinery remain authoritative. Reports display
the same points as H0, H5, H10, H15, H20, and H25.

## Initialize without mutating the autonomous run

The source experiment must have a complete frozen checkpoint chain, final
lock, and transfer authorization. Initialization creates a separate private
root. It pins all 25 C0 results, T2/T3 predictions, trajectories, verifier
artifacts, input sources, and ground truth. Immutable history and C0 artifacts
are hard-linked when the filesystem permits and copied otherwise. The source
final-lock and transfer-authorization markers are not copied, so the new
lineage is open.

```bash
libstruct-libgen-capability-improvement init-human-guided \
  --source-experiment-root "$AUTONOMOUS_ROOT" \
  --output-root "$HUMAN_ROOT" \
  --experiment-id libgen-human-guided-v1 \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

The final ten-protocol panel had already been unsealed in the source run. It is
blocked from all protocol review and synthesis inputs and is described only as
a fixed posthoc transfer comparison. The human condition makes no unseen or
sealed-test claim.

## Review one protocol at a time

Reviews follow the frozen B1--B5 order. The parent checkpoint and its sanitized
five-protocol validation aggregate must exist before the next batch starts.

If a deterministic evaluator defect is corrected, rescore each preserved
prediction into a benchmark-versioned sidecar and repin only undecided
reviews. A completed review remains bound to its immutable manifest ancestor
and exact unchanged registry entry; its proposal and decision are never
rewritten. The original Harbor outputs, human manifest, registry, and any
unfinalized target review are retained under
`human-history/superseded/human-verifier-refresh/`. This operation is rejected
for a protocol that already has a decision, after batch synthesis, or after a
later checkpoint exists; it never reruns the stochastic agent.

```bash
uv run python -m libstruct_bench.cli.rescore_libgen_runs \
  --runs-root "$TRIAL_ROOT" \
  --groundtruth-root "$GROUNDTRUTH_ROOT" \
  --schema-root schemas

libstruct-libgen-capability-improvement human-refresh-verifier \
  --experiment-root "$HUMAN_ROOT" \
  --protocol-id "$PROTOCOL_ID" \
  --rescore-dir "$TRIAL_ROOT/verifier/rescore/libgen-$BENCHMARK_VERSION" \
  --rescore-summary "$TRIAL_ROOT/verifier/rescore/libgen-$BENCHMARK_VERSION/summary.json" \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

```bash
libstruct-libgen-capability-improvement human-review-next \
  --experiment-root "$HUMAN_ROOT" \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

After a verifier-wide preflight, the other undecided protocols in the active
five-protocol batch may be staged ahead of time with `--protocol-id`. This does
not change the review order or record a decision; it only materializes their
hash-bound comparisons and draft templates against the same parent checkpoint.

The command writes a comparison, a concise review page, and a proposal-draft
template. In the active Codex chat, Codex inspects the frozen prediction,
ground truth, source bundle, trajectory, and current pack. It groups duplicate
metric symptoms into root findings and proposes protocol-neutral remedies.

Every substantive mismatch must be classified exactly once. Only findings
that are benchmark-valid, agent-attributed, and recoverable from the visible
source bundle are eligible for capability learning. Source-scope mismatches,
ground-truth defects, policy ambiguity, source conflicts, evaluator defects,
infrastructure failures, and unresolved findings remain neutral. A process
cause requires a trajectory citation; the tool does not infer process root
cause from the output difference alone.

Compile the Codex-authored draft. The command prints the complete proposal in
readable text. The immutable JSON remains the hash-bound audit record, but it
is not the human review interface.

```bash
libstruct-libgen-capability-improvement human-review-compile \
  --experiment-root "$HUMAN_ROOT" \
  --draft "$DRAFT" \
  --model gpt-5.6-sol \
  --agent-version "$CODEX_VERSION" \
  --reasoning-effort max \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

The human review controller runs in Claude Code so every gate uses Claude's
native `AskUserQuestion` selector. Codex remains the immutable proposal author;
Claude presents evidence and records only the explicit human disposition.
Start Claude Code from the repository root:

```bash
cd /Users/seqmachines/playground/libstruct-bench
claude
```

Then invoke the skill with the expanded absolute experiment path and reviewer
ID:

```text
/review-capability /absolute/path/to/human-guided-v1 reviewer-id
```

Claude displays and gates exactly one section at a time: T2, workflow boundary,
states, transitions, typed edges, and the complete proposal. T2 enumerates
every ground-truth and agent oligo. Each T3 section likewise enumerates every
entity or edge on both sides. A complete section is printed before one native
single-select question. Section feedback does not mutate the proposal or admit
learning evidence; only the final proposal selector records a hash-bound
decision. A revision request permits one and only one revised proposal, and
stopping records nothing.

The same review can be reopened read-only without risking a decision. The full
legacy view is:

```bash
libstruct-libgen-capability-improvement human-review-show \
  --experiment-root "$HUMAN_ROOT"
```

One deterministic section can be inspected directly with:

```bash
libstruct-libgen-capability-improvement human-review-show-section \
  --experiment-root "$HUMAN_ROOT" \
  --section t2
```

For automation, `human-review-record` remains available as a noninteractive
interface. For a revision request, update the draft with `revision_round` 1,
compile it, and review the revised proposal. Decisions are hash-bound to the
exact comparison and proposal. Approved attribution is written to a new
adjudicated error-analysis overlay; the original verifier analysis and
canonical ground truth are never modified.

## Synthesize and checkpoint after five reviews

After the fifth final review in a batch, build the packet and run the standard
isolated proposer. It may propose at most two pack changes.

```bash
libstruct-libgen-capability-improvement human-synthesize \
  --experiment-root "$HUMAN_ROOT" \
  --batch B1
```

Review the resulting exact candidate bytes with the existing `review-start`,
`review-decide`, and `review-finalize` commands. At most one pack change may be
accepted. Then apply deterministically and freeze the checkpoint:

```bash
libstruct-libgen-capability-improvement human-batch-complete \
  --experiment-root "$HUMAN_ROOT" \
  --batch B1 \
  --reviewer-id "$REVIEWER" \
  --authorize-apply
```

Run the fixed five-protocol validation panel on the new checkpoint and record
its canonical aggregate before starting the next batch. Aggregate scores may
be discussed and recorded as non-mutating guidance; they cannot serve as
change evidence or directly edit the pack:

```bash
libstruct-libgen-capability-improvement human-record-validation-guidance \
  --experiment-root "$HUMAN_ROOT" \
  --checkpoint C5 \
  --codex-summary "Concise aggregate-level interpretation." \
  --human-note "Decision for the next review batch." \
  --reviewer-id "$REVIEWER" \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Inspect resumable progress at any point with:

```bash
libstruct-libgen-capability-improvement human-status \
  --experiment-root "$HUMAN_ROOT"
```

After H25, run validation once and evaluate the fixed posthoc transfer panel
only at the human H25 endpoint. Compare it descriptively with the source C0
and autonomous C25 references; do not use it to select or revise the pack.
