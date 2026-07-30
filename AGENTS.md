# Repository guidance

## Ground-truth audit

- Read `docs/audit/README.md`, `docs/audit/evidence-policy.md`, and
  `docs/audit/adjudication-policy.md` before changing audit code, schemas, or
  workflows.
- Keep pipeline code, schemas, tests, and agent workflows in this repository.
  Keep source bundles, baseline labels, audit runs, decisions, and releases in
  the separate private audit-data repository.
- Preserve the three input roles: primary protocol evidence, legacy curated
  HTML, and the current benchmark record. Never treat the current record as
  independent evidence.
- Treat every agent as an audit assistant. An agent finding or proposed JSON
  patch is not approval and must not directly change canonical ground truth.
- Apply accepted corrections only through deterministic code after verifying
  the proposal hash, baseline hash, and recorded human decision.
- Do not silently add web or remembered kit knowledge. New evidence must enter
  the versioned input manifest before it can support a benchmark change.
- Add schema, unit, and regression coverage for changes to audit behavior.
