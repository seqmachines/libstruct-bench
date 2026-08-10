# Claude Code guidance

Use `/audit-protocol <protocol_id>` for one protocol or pass up to ten protocol
IDs to prepare proposals concurrently. The skill runs the conversion-first audit,
resumes existing work, includes every available discovered source, marks missing
sources unavailable, and pauses only for scientific adjudication.

The local input root is `/Users/seqmachines/playground/protocols-test`:

- `protocols/` and `scg_html/` contain source material;
- `groundtruth_oligos.tsv` is reviewed legacy curation, not unquestionable
  truth;
- `ground_truth_audit/` contains private catalogs, packets, agent runs,
  decisions, applications, and review history;
- `ground_truth/` contains only human-approved T1, T2, and T3 records.

Never modify `scg-v1-upload` or upload to Hugging Face during the audit.

The workflow is:

1. Identify every legacy-shaped current T1–T3 record and require a canonical
   root conversion candidate derived only from legacy HTML, current records,
   and the reviewed oligo TSV. T3 comes primarily from the HTML workflow. The
   candidate remains unapproved.
2. In the same read-only worker, only after conversion is complete, read every
   available manifest-listed primary source and verify the candidate against
   it. Missing sources remain recorded as unavailable. Report primary-source
   differences as separate issues; do not blend them into the conversion.
3. Review conflicts or unsupported fields interactively in this console, one
   issue at a time. Save each explicit human decision immediately to the
   working decision JSON and resume at the first undecided issue. Do not
   generate HTML review files. Use one interactive review pass per protocol. If
   the human requests edits, discuss them and revise the working candidate in
   this session rather than starting another review iteration.
4. Apply accepted human corrections deterministically to separate candidates.

Legacy conversion preserves original values and locations in audit lineage but
omits legacy-only schema labels, source HTML, extraction, and normalization
fields from cleaned ground truth. Cleaned T1–T3 is deliberately minimal:
evidence, lineage, decisions, inclusion status, and review notes remain in audit
artifacts rather than being copied into the approved JSON. T1 libraries have no
stored ID or duplicate strand list. T2 uses `name` plus `aliases`. T3 requires
exactly one workflow per modality; same-modality alternatives are branches in
that workflow. Graph topology replaces `workflow_branch`. Protocol scope is optional and inherited. Every T3 state uses a controlled strand architecture,
stores each physical strand separately in its own 5′→3′ direction, and names
the reference strand corresponding to T1. Primary-source deltas against a
converted artifact remain patch-free until human decisions are compiled into
its one final root patch.
If an older finalized review lacks required root conversions, preserve it as
history and start a fresh conversion-first comparison/review iteration; never
try to apply or rewrite that decision.

Comparison workers are read-only and may use only packet-listed files. They must
preserve source locators, report conflicts instead of resolving them silently,
and never interpret an agent proposal as human approval. Only deterministic
repository tools may apply or promote changes.

If a complete comparison artifact fails the audit schema, a canonical
T1/T2/T3 schema, or linked validation, the runner may make at most two bounded
repair attempts before rejecting the run. Repair is evidence-isolated: the
worker receives only the artifact and exact validator error and cannot reopen
the packet or sources. It may repair only the root candidate representation or
deterministic ledger linkage required by that error. Source coverage, evidence,
audited-field conclusions, and issue identities and classifications are fixed.
Re-run full validation and preserve the inputs, candidates, errors, transcripts,
changed paths, and hashes for every attempt.

For T3, prefer the smallest scientifically sufficient graph. Create new states
and transitions primarily for changes in sequence architecture or strand
structure. When those are unchanged, fold cleanup, purification, size
selection, pooling, washing, QC, quantification, dilution, reagent handling,
and inactivation into the nearest substantive transition. Use a separate
non-sequence node only when a distinct branch or carried molecular product is
essential to understand downstream library generation, and explain why.
Within every state, preserve the actual molecular strands rather than assuming
single- or double-strandedness. Record paired regions, overhangs, unpaired
regions, nicks/gaps, RNA–DNA hybrids, and Y-shaped duplexes when supported.
Explicit paired sequence regions must be reverse-complementary unless the
source documents noncanonical or unknown pairing. Oligo-derived segments record
their T2 ID and `orientation_to_source` as `same_orientation`,
`reverse_complement`, or `unknown`. Never encode orientation in a placeholder
role: use `[I5_INDEX:8]`, `[TN5_INDEX:8]`, or `[I7_INDEX:8]`, never an `_RC`,
`_REVERSE`, or `_FWD` variant. A segment's `sequence` is the sequence on its
modeled strand; the linked T2 record retains the source-visible oligo sequence.

In batch mode, use at most ten protocol-scoped workers. A worker writes only
inside its protocol audit directory and stops after its validated proposal.
The controller collects proposals into a review queue; workers never decide,
promote, publish, or edit pipeline code.

During adjudication, accept only `accept`, `reject`, `modify`, `unresolved`, or
`exclude` as decisions. Support `back`, `skip`, `status`, `quit`, and `resume`
as navigation commands; they are not scientific decisions. Show a final
T1–T3 result and obtain one explicit scientific approval before marking the
review final. Human-requested refinements happen conversationally against the
same working candidate. Never apply or promote while any issue is undecided or
merely because the walkthrough ended.

Before that final approval when T3 is included, directly read the immutable
packet's primary PDFs, supplementary tables/spreadsheets, and relevant figures
or renditions. Fact-check every T3 state and transition against exact primary
locators, including substrate, operation, oligos/reagents, products, carried
product, strand architecture, and sequence change. Show a concise
verified/conflict/missing/ambiguous table. Do not rely only on the comparison
worker's summary or proposal, and do not finalize while a material T3 gap is
unresolved.

The interactive controller must call Claude Code's `AskUserQuestion` tool for
every scientific human gate: each individual issue, the grouped low-issue
decision, the combined final candidate/finalization approval, and application
authorization. Source availability is deterministic and is not a human gate.
Ask one single-select question at a time with only the valid choices for that
target. Do not finish a response with a plain-text question such as “Your
disposition.” If the human asks for clarification or requests an edit, answer,
update and validate the working candidate when appropriate, then invoke
`AskUserQuestion` again for the same gate. Use a plain-text fallback only when
the tool is genuinely unavailable.

A finalized review is not the end of the controller workflow while its approved
T1–T3 files remain unpromoted. For one protocol, immediately ask a separate
application-and-promotion question after finalization. For a batch, finish the
selected review queue and then ask once for the exact finalized, unpromoted
protocols. Append `<!-- audit-application-question-required -->` immediately
before that `AskUserQuestion` call. An affirmative answer runs deterministic
application, linked validation, regressions, and promotion to
`/Users/seqmachines/playground/protocols-test/ground_truth/<protocol_id>/`; a
decline leaves the immutable review unapplied. Never infer this authorization
from scientific approval.

For an individual issue, first print the complete review card in normal console
text, then invoke `AskUserQuestion` in the same turn. The card must include the
issue number and ID, task, severity, category, defect, target and patch status,
title, full current and proposed values, exact evidence locators grouped by what
they support, reason and impact, and relevant policy notes. The interactive
question is only the decision control; it must not replace or compress the
evidence card into a one-sentence recap.
Append `<!-- audit-question-required -->` to an issue card immediately before
the tool call. The audit skill's scoped `Stop` hook treats a marked card—or a
recognizable legacy card—as incomplete and prevents yielding until
`AskUserQuestion` is called.

Use `modify` only for a `groundtruth_record` or `new_groundtruth_record` target.
For evidence-only or source-bundle deltas, record `accept`, `reject`,
`unresolved`, or `exclude`. If accepted deltas affect a proposed new artifact,
review the deltas first, compile their resolutions into one complete canonical
document, and validate it against the task schema. Include its root decision in
the one final scientific-approval question. Never apply accepted delta prose
directly.

Present an issue individually only when it carries a patch,
has blocker/high/medium severity, represents unresolved scientific ambiguity,
protocol-version confusion, or an evaluator/matching error, or recommends an
evaluator, harness, or scoring-exclusion change.
Preserve other low, non-changing findings and summarize them by task and count.
Ask for one explicit grouped human decision: accept them as observations with no
ground-truth edit, reject them and keep current values, leave them unresolved, or
expand them for individual review. Expand the group answer into a separate
recorded decision for every issue ID. Never infer a decision or update ground
truth from the summary alone. Group related findings that require the same
decision instead of creating one issue per metadata field.

Claude may edit pipeline code only when the user explicitly requests
engineering work. Such edits must follow `docs/audit/` and retain appropriate
validation.
