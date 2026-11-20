# Validation/test verifier diagnostic review

The validator-diagnostic review is a read-only human-review overlay for a
completed knowledge-base experiment. It reuses the prior human-guided branch's
scientific attribution discipline without re-entering the learning loop.

For each of the five validation and ten test protocols, the overlay pins:

- the compatible C0 result and selected C30 result scored by LibGen v4.5.0;
- both predictions, trajectories, score details, and error analyses;
- the exact task-side T1/T2/T3 target bytes consumed by the verifier; and
- only primary sources already included by the protocol's source manifest.

Only substantive C30 observations require adjudication. C0 is comparison
context for persistent behavior and metric deltas, which avoids duplicating
the review queue. Codex consolidates dependent observations and classifies
each root finding as a valid agent difference, evaluator defect, ground-truth
defect, source-scope mismatch, representation-policy ambiguity, source
conflict, infrastructure problem, or unresolved. Only `evaluator_defect` is
shown as a proposed validator issue.

The overlay deliberately provides no synthesize, apply, promote, rerun, or
update command. A human `approve` decision accepts only the diagnostic
classification package. It cannot change the agent, prompt, capability pact,
knowledge base, verifier, ground truth, or preserved output.

Initialize the 15-protocol queue:

```bash
libstruct-libgen-capability-improvement kb-validator-review-init \
  --source-experiment-root "$EXPERIMENT_ROOT" \
  --output-root "$PRIVATE_REVIEW_ROOT" \
  --workspace-root "$RUN_WORKSPACE_ROOT" \
  --validation-aggregate "$C30_VALIDATION_AGGREGATE" \
  --test-aggregate "$C30_TEST_AGGREGATE" \
  --review-id agent-guided-kb-v2-c30-validator-review \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Generate, show, and decide one proposal at a time:

```bash
libstruct-libgen-capability-improvement kb-validator-review-run-next \
  --review-root "$PRIVATE_REVIEW_ROOT" \
  --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

libstruct-libgen-capability-improvement kb-validator-review-show \
  --review-root "$PRIVATE_REVIEW_ROOT"

libstruct-libgen-capability-improvement kb-validator-review-decide \
  --review-root "$PRIVATE_REVIEW_ROOT" \
  --protocol-id "$PROTOCOL_ID" \
  --reviewer-id seqmachines \
  --disposition approve \
  --rationale "The proposed diagnostic classifications are accepted." \
  --started-at "$STARTED_AT" \
  --completed-at "$COMPLETED_AT"
```

One bounded `revision_requested` round is available. Status is resumable and
reports proposals, decisions, and the number of proposed validator issues:

```bash
libstruct-libgen-capability-improvement kb-validator-review-status \
  --review-root "$PRIVATE_REVIEW_ROOT"
```
