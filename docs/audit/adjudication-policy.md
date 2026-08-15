# Human adjudication policy

The audit agent proposes; the human reviewer decides.

## Review

Each issue requiring human review receives one disposition:

- `accept`: confirm the finding and use the agent's exact patch when one exists;
- `reject`: keep the current ground-truth value;
- `modify`: use a human-supplied value and patch for a ground-truth target;
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

In Claude Code, every human gate uses `AskUserQuestion` as a single-select
interactive prompt. Source availability is resolved automatically and is not a
human gate. Human gates include individual and grouped issue decisions, the
combined final candidate/finalization approval, and authorization to apply.
Printing disposition options is not an interactive gate and must not end the
controller turn while `AskUserQuestion` is available. After clarification or a
requested edit, update the working candidate when needed and ask the same
unresolved question interactively again.

For each individual issue, the controller first prints a complete evidence card
in normal console text and then invokes `AskUserQuestion` in the same turn. The
card includes identity and classification, target and patch status, full current
and proposed values, exact locators grouped by which value they support, reason,
impact, and relevant policy notes. The question-tool prompt is a decision
control, not a substitute for this context, and must not reduce the card to a
one-sentence summary.

The `/audit-protocol` skill enforces this with a skill-scoped `Stop` hook. A
rendered issue card carries an internal pending-question marker. If Claude tries
to finish before invoking `AskUserQuestion`, the hook blocks that stop and tells
the controller to call the selector without repeating the card. The hook does
not run outside the audit skill and does not make or infer a human decision.

Individual review is required for any patch, blocker/high/medium finding,
unresolved scientific ambiguity, protocol-version conflict, evaluator/matching
conflict, or recommendation to change the evaluator, harness, or scoring
inclusion. Low findings that do not change ground truth or scoring remain
preserved in the proposal and are summarized by task and count. The reviewer
must make one explicit grouped decision for them: accept as observations with
no ground-truth edit, reject and keep current, leave unresolved, or expand for
individual review. A grouped answer is expanded into a separate decision record
for every issue ID; no finding is accepted or changed by default.

Use one interactive review pass per protocol by default. The in-progress
working decision is updated atomically after each answer and may be applied
with `--working` to make a separate preview. If the reviewer asks for a change
or clarification, discuss it, update the working decision or root candidate,
revalidate it, and continue in the same Claude Code session. Do not create a
second review iteration merely to incorporate human edits.

A new proposal or review iteration is justified only when a manifest-listed source,
input manifest, pinned baseline, canonical schema, or immutable finalized
decision has changed. Every preview is regenerated from the pinned input
records, so revisions do not stack untracked edits. The finalized decision
receives its immutable decision ID and is never edited.

An unfinalized proposal may continue through a representation-only canonical
schema migration without repeating its scientific review. Keep the proposal
immutable and compile the current-schema document as a complete root
replacement in the working decision. Freeze sequences, placeholders,
orientations, oligo identities, molecular states, operations, and branch order.
For a T3 representation migration, use one workflow per connected molecular
process, place exact T1 modalities on `final_outputs`, and
retain shared upstream graph records once before modality-specific branches.
Freeze the reviewed scientific payload; any ambiguous connection, terminal
assignment, or branch structure returns to the human or a fresh comparison.
The final combined candidate and root `modify` decision remain subject to
normal human approval and linked validation. This exception does not permit
editing a finalized decision.

Final review must decide every proposal issue. After issue review and root
candidate compilation, show one concise T1–T3 result and ask one final
scientific-approval question. That answer records any required root candidate
decision and confirms finalization. Completion of the walkthrough alone does
not authorize application or promotion; deterministic application remains a
separate explicitly authorized action.

T3 requires a direct primary-source fact-check before that final question. The
interactive controller must open the immutable packet's primary PDFs,
supplementary tables/spreadsheets, and relevant figures or renditions itself;
the comparison worker's summary, transcript, proposal prose, and locators alone
are insufficient. For every T3 state and transition, verify the substrate or
current molecular state, operation, T2 oligos and major reagents, products,
carried-forward product, strand architecture, and sequence change against exact
primary-source locations. Present a concise console table with status
`verified`, `conflict`, `missing`, or `ambiguous` for every state and
transition. A material conflict, missing source support, or unresolved
interpretation blocks finalization and must be presented for an explicit human
resolution; it must not be silently folded into the candidate.

After finalization, the interactive controller must not silently stop with an
unpromoted review. For a single protocol it immediately asks the separate
application question. For a batch it completes the selected human-review queue,
lists the exact finalized and unpromoted protocol IDs, and asks once whether to
apply and promote that set. Declining preserves the finalized decisions without
writing approved ground truth; accepting authorizes only the listed protocols.

Evidence, source-bundle, policy, harness, and evaluator findings do not directly
edit ground truth and therefore cannot use `modify`. Accept or reject the
finding itself. If an accepted finding changes a newly proposed T1, T2, or T3
document, incorporate the reviewed resolution into one complete replacement
patch on that document's `new_groundtruth_record` issue.

When a proposal creates a new ground-truth document or converts a legacy-shaped
existing record, review its associated delta issues first. Then build one final
candidate from the conversion candidate and the recorded human decisions,
validate it against the canonical task schema, show the human a concise change
summary, and record `accept` for an unchanged candidate or `modify` with a
complete root replacement patch. This final root decision is the only operation
that creates or converts the artifact. Representation-only conversion is a
schema migration, not a scientific human-curation error.

The approved candidate contains only scientific ground-truth fields. Evidence,
baseline lineage, review decisions, inclusion status, and audit notes remain in
the proposal, decision, and application records.

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
