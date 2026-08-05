# Claude Code guidance

Use `/audit-protocol <protocol_id>` for one protocol or pass up to ten protocol
IDs to prepare proposals concurrently. The skill runs the evidence-first audit,
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
   root conversion candidate derived only from legacy curation. The candidate
   remains unapproved.
2. Independently reconstruct T1–T3 evidence from every available,
   manifest-listed primary source in an isolated phase that cannot see the
   conversion candidate. Missing sources remain recorded as unavailable and do
   not enter the packet.
3. Freeze that evidence, then compare it with legacy HTML, current T1–T3
   records, the reviewed oligo TSV, and optional benchmark-run artifacts. When
   T3 JSON is missing, first convert the legacy HTML workflow into the T3
   candidate with legacy locators, then report primary-source differences as
   separate issues. Do not blend those differences into the legacy candidate.
4. Review conflicts or unsupported fields interactively in this console, one
   issue at a time. Save each explicit human decision immediately to the
   working decision JSON and resume at the first undecided issue. Do not
   generate HTML review files. Use one interactive review pass per protocol. If
   the human requests edits, discuss them and revise the working candidate in
   this session rather than starting another review iteration.
5. Apply accepted human corrections deterministically to separate candidates.

Legacy conversion preserves original values and locations in audit lineage but
omits legacy-only schema labels, source HTML, extraction, and normalization
fields from cleaned ground truth. Cleaned T1–T3 is deliberately minimal:
evidence, lineage, decisions, inclusion status, and review notes remain in audit
artifacts rather than being copied into the approved JSON. T1 libraries have no
stored ID or duplicate strand list. T2 uses `name` plus `aliases`. T3 stores
`modality` once at the document root; graph topology replaces
`workflow_branch`. Protocol scope is optional and inherited. Every T3 state uses a controlled strand architecture,
stores each physical strand separately in its own 5′→3′ direction, and names
the reference strand corresponding to T1. Primary-source deltas against a
converted artifact remain patch-free until human decisions are compiled into
its one final root patch.
If an older finalized review lacks required root conversions, preserve it as
history and start a fresh comparison/review iteration from the frozen evidence;
never try to apply or rewrite that decision.

Phase workers are read-only and may use only packet-listed files. They must
preserve source locators, report conflicts instead of resolving them silently,
and never interpret an agent proposal as human approval. Only deterministic
repository tools may apply or promote changes.

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
