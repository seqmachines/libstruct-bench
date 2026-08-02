# Human adjudication policy

The audit agent proposes; the human reviewer decides.

## Review

Each issue receives one disposition:

- `accept`: use the agent's exact patch;
- `reject`: keep the current ground-truth value;
- `modify`: use a human-supplied value and patch;
- `unresolved`: preserve the uncertainty;
- `exclude`: exclude the field, task, or protocol from scoring.

The console should show only issues requiring attention. Verified fields are
summarized by count. Each decision records a reviewer, rationale, confirmed
cause, timestamps, and review iteration.

Review may be iterative. Each working decision is saved under a new immutable
ID and may be applied with `--working` to make a separate preview. Every preview
is regenerated from the pinned input records, so iterations do not stack
untracked edits. Final review must decide every issue.

## Deterministic application

Application validates proposal and decision hashes, applies only accepted or
modified RFC 6902 patches, refuses stale or overlapping operations, writes new
candidate files, and generates one regression fixture per accepted correction.
The pinned inputs and all earlier proposals and decisions remain unchanged.

## Promotion

Promotion requires a final decision, no unresolved included issue, complete
schema and cross-task validation, and passing correction regressions. It writes
approved T1–T3 files into a new `ground_truth/<protocol_id>/` directory and
refuses to overwrite an existing approved protocol.

An unresolved protocol is never forced into one truth value. A human may keep
it pending or explicitly exclude it from scoring.

## Error tracking

Confirmed causes are retained as:

- original human-curation error;
- audit-agent reasoning error;
- agent-harness or context error;
- PDF, table, or figure extraction error;
- schema or formatting error;
- naming or normalization inconsistency;
- source or protocol ambiguity;
- protocol-version error.

Checkpoint reports aggregate these causes, confirmed error rates, human versus
agent proportions, regressions, and review time after 0, 5, 10, and later
reviewed protocols.
