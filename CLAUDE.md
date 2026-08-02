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
   records, the reviewed oligo TSV, and optional benchmark-run artifacts.
3. Show only conflicts or unsupported fields to the human for adjudication.
4. Apply accepted human corrections deterministically to separate candidates.

Phase workers are read-only and may use only packet-listed files. They must
preserve source locators, report conflicts instead of resolving them silently,
and never interpret an agent proposal as human approval. Only deterministic
repository tools may apply or promote changes.

In batch mode, use at most ten protocol-scoped workers. A worker writes only
inside its protocol audit directory and stops after its validated proposal.
The controller collects proposals into a review queue; workers never decide,
promote, publish, or edit pipeline code.

Claude may edit pipeline code only when the user explicitly requests
engineering work. Such edits must follow `docs/audit/` and retain appropriate
validation.
