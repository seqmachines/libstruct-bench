# Claude Code guidance

Use the project `audit-protocol` skill for protocol-level ground-truth audits.
During an audit:

- Read the protocol's manifest and keep primary evidence, legacy HTML, and
  current benchmark JSON distinct.
- Work read-only and emit a `libstruct.protocol_audit.v1` proposal.
- Cite source locations and record every sequence transformation.
- Report conflicts and unsupported assumptions instead of resolving them
  silently.
- Do not browse for missing kit knowledge or edit ground truth, decisions, or
  releases.

Claude may edit pipeline code when the user explicitly requests engineering
work. Those edits must still follow `docs/audit/` and include appropriate tests.
