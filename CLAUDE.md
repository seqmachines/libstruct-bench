# Claude Code guidance

Use the project `audit-protocol` skill for protocol-level ground-truth audits.
During an audit:

- Read the protocol's manifest and keep primary evidence, legacy HTML, and
  current benchmark JSON distinct.
- Work read-only. Complete the isolated primary-evidence phase and emit
  `libstruct.protocol_evidence.v1` before the comparison phase can expose
  legacy HTML or current labels.
- Read all packet-listed deterministic renditions, while treating the original
  document as the scientific source.
- In comparison, use the frozen evidence and emit a
  `libstruct.protocol_audit.v2` proposal.
- Cite source locations and record every sequence transformation.
- Report conflicts and unsupported assumptions instead of resolving them
  silently.
- If no current T3 file exists, propose its complete creation as a
  `new_groundtruth_record`; never create or approve it directly.
- Do not browse for missing kit knowledge or edit ground truth, decisions, or
  releases.

Claude may edit pipeline code when the user explicitly requests engineering
work. Those edits must still follow `docs/audit/` and include appropriate tests.
