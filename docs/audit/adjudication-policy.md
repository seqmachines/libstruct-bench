# Human adjudication policy

The audit agent proposes; the human reviewer decides.

## Review

Each issue requiring human review receives one disposition:

- `accept`: confirm the finding and use the agent's exact patch when one exists;
- `reject`: keep the current ground-truth value;
- `modify`: use a human-supplied value and patch;
- `unresolved`: preserve the uncertainty;
- `exclude`: exclude the field, task, or protocol from scoring.

The console should show only issues requiring attention. Verified fields are
summarized by count. Each decision records a reviewer, rationale, confirmed
cause, timestamps, and review iteration.

Interactive console review is the default. Present exactly one undecided issue
at a time and checkpoint each explicit decision immediately in the working
decision artifact. `back`, `skip`, `status`, `quit`, and `resume` are navigation
commands, not dispositions. Resume at the first undecided issue without
discarding earlier decisions. Do not generate HTML review files.

Individual review is required for any patch, blocker/high/medium finding,
unresolved scientific ambiguity, protocol-version conflict, evaluator/matching
conflict, or recommendation to change the evaluator, harness, or scoring
inclusion. Low findings that do not change ground truth or scoring remain
preserved in the proposal and are summarized by task and count. The reviewer
must make one explicit grouped decision for them: accept as observations with
no ground-truth edit, reject and keep current, leave unresolved, or expand for
individual review. A grouped answer is expanded into a separate decision record
for every issue ID; no finding is accepted or changed by default.

Review may be iterative. The in-progress working decision is updated atomically
after each answer and may be applied with `--working` to make a separate
preview. Every preview is regenerated from the pinned input records, so
iterations do not stack untracked edits. The finalized decision receives its
immutable decision ID and is never edited. Final review must decide every
proposal issue. Before changing the review state to final, show the
disposition summary and obtain explicit human confirmation. Completion of the
walkthrough alone does not authorize application or promotion.

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
