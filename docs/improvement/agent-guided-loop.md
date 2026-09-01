# Protocol-by-protocol agent-guided loop

This condition mirrors the human review loop without changing the verifier.
For each frozen Codex C0 protocol output, one read-isolated Codex proposer
compares source, ground truth, prediction, trajectory, and the current
capability pack. A separate read-isolated Codex critic approves, rejects,
marks unresolved, or requests one bounded revision. Agent findings remain
audit proposals; they never approve or mutate canonical ground truth.

The implementation reuses the human protocol-review artifact format so the
two conditions have identical comparison, grouping, attribution, and packet
semantics. Every agent decision is explicitly marked `independent_codex`; no
human decision is claimed.

## Frozen-verifier boundary

Initialization fingerprints the canonical scorer/validator sources and every
generated LibGen task bundle. Every review, synthesis, application, and sweep
checks that lock before and after work. Agent-authored mutations are restricted
by the capability-proposal contract to paths under `pack/`. Checkpoint exemplar
memory is projected deterministically by the orchestrator; it is not authored
by the agent. Ground truth and verifier bytes are immutable.

All 25 preserved baseline predictions must first have a sidecar rescore for the
current benchmark version. Initialization also requires a fresh C0 run on the
fixed five-protocol validation panel, so the first batch never consumes a
validation aggregate from an older verifier.

```bash
libstruct-libgen-capability-improvement init-agent-guided \
  --source-experiment-root "$AUTONOMOUS_ROOT" \
  --output-root "$AGENT_ROOT" \
  --experiment-id libgen-agent-guided-v1 \
  --repository-root "$REPOSITORY_ROOT" \
  --c0-validation-result-root "$C0_VALIDATION_RESULT" \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

## Review and update one batch

Run the next protocol review five times. Each invocation is resumable and
performs proposal, independent review, and at most one revision.
A schema-valid draft that fails a deterministic cross-artifact compiler
invariant may receive one narrow clerical repair before review; that repair
cannot add evidence or scientific claims and does not consume the critic's
single revision round. Review workers include local PDF and XLSX readers so
primary evidence remains inspectable without web access.

```bash
CODEX_FORCE_AUTH_JSON=1 caffeinate -i \
  libstruct-libgen-capability-improvement agent-review-next \
  --experiment-root "$AGENT_ROOT" \
  --agent-version "$CODEX_VERSION" \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

After five finalized reviews, synthesize at most two pact candidates. Validation
macro means from the parent checkpoint can prioritize general controls, but
cannot serve as scientific evidence or directly mutate the same checkpoint.

```bash
libstruct-libgen-capability-improvement agent-synthesize \
  --experiment-root "$AGENT_ROOT" --batch B1

libstruct-libgen-capability-improvement agent-batch-complete \
  --experiment-root "$AGENT_ROOT" --batch B1 \
  --groundtruth-root "$GROUNDTRUTH_ROOT" --authorize-apply
```

The second command runs the standard independent exact-byte pact critic,
deterministic application, synthetic suite, and checkpoint freeze. At most one
change unit is accepted.

## Validation and cumulative checkpoint sweeps

Run and record the canonical five-protocol validation job for the new
checkpoint before beginning the next batch. That aggregate guides the next
batch only.

After each batch, also replay every available learned checkpoint from C5
through the current checkpoint on the same fixed validation panel:

```bash
libstruct-libgen-capability-improvement agent-plan-checkpoint-sweep \
  --experiment-root "$AGENT_ROOT" --after-batch B2 \
  --tasks "$TASKS_ROOT" --base-config "$HARBOR_BASE" \
  --out "$SWEEP_ROOT" --jobs-dir "$JOBS_ROOT" \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Run each emitted `harbor_command`, then record the macro-only matrix:

The emitted command explicitly sets `CODEX_FORCE_AUTH_JSON=1`, because Harbor
otherwise defaults to API-key authentication even when the host Codex CLI is
logged in through ChatGPT. The host login file is injected as a secret and is
never copied into an experiment artifact.

```bash
libstruct-libgen-capability-improvement agent-record-checkpoint-sweep \
  --experiment-root "$AGENT_ROOT" --sweep-root "$SWEEP_ROOT" \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

The sequence is C5 after B1; C5/C10 after B2; then three, four, and five
checkpoints after B3--B5. These are repeated development diagnostics on the
validation set—not the sealed ten-protocol final test and not change evidence.
The final test remains unavailable until the C25 lock.

Use `agent-status` for resumable progress.
