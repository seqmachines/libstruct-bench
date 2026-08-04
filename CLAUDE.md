# Claude Code guidance

Use `/audit-protocol <protocol_id>` for one protocol or pass up to ten protocol
IDs to prepare proposals concurrently. The skill runs the evidence-first audit,
resumes existing work, and pauses for human source approval and scientific
adjudication.

The local input root is `/Users/seqmachines/playground/protocols-test`:

- `protocols/` and `scg_html/` contain source material;
- `groundtruth_oligos.tsv` is reviewed legacy curation, not unquestionable
  truth;
- `ground_truth_audit/` contains private catalogs, packets, agent runs,
  decisions, applications, and review history;
- `ground_truth/` contains only human-approved T1, T2, and T3 records.

Never modify `scg-v1-upload` or upload to Hugging Face during the audit.

The workflow is:

1. Independently reconstruct T1–T3 evidence from approved primary sources.
2. Freeze that evidence, then compare it with legacy HTML, current T1–T3
   records, the reviewed oligo TSV, and optional benchmark-run artifacts. When
   T3 JSON is missing, first convert the legacy HTML workflow into the T3
   candidate with legacy locators, then report primary-source differences as
   separate issues. Do not blend those differences into the legacy candidate.
3. Review conflicts or unsupported fields interactively in this console, one
   issue at a time. Save each explicit human decision immediately to the
   working decision JSON and resume at the first undecided issue. Do not
   generate HTML review files.
4. Apply accepted human corrections deterministically to separate candidates.

Phase workers are read-only and may use only packet-listed files. They must
preserve source locators, report conflicts instead of resolving them silently,
and never interpret an agent proposal as human approval. Only deterministic
repository tools may apply or promote changes.

In batch mode, use at most ten protocol-scoped workers. A worker writes only
inside its protocol audit directory and stops after its validated proposal.
The controller collects proposals into a review queue; workers never decide,
promote, publish, or edit pipeline code.

During adjudication, accept only `accept`, `reject`, `modify`, `unresolved`, or
`exclude` as decisions. Support `back`, `skip`, `status`, `quit`, and `resume`
as navigation commands; they are not scientific decisions. Show a final
decision summary and obtain explicit human confirmation before marking the
review final. Never apply or promote while any issue is undecided or merely
because the walkthrough ended.

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
