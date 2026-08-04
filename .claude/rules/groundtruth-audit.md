---
paths:
  - "docs/audit/**"
  - "schemas/audit/**"
  - "src/libstruct_bench/audit/**"
  - "tests/audit/**"
---

# Ground-truth audit changes

- Read the canonical policies under `docs/audit/` before editing these paths.
- Keep the three source roles distinct in code, schemas, fixtures, and prose.
- Preserve agent proposal and human decision as separate artifact types.
- Default human adjudication to one issue at a time in the Claude console,
  checkpointing each explicit decision in the working decision artifact.
- Do not generate HTML review files. Present material review-gate issues
  individually. Summarize low informational findings and require one explicit
  grouped human decision, recorded separately for every issue ID.
- Never add a path that lets an audit proposal overwrite canonical ground
  truth without a validated human decision and baseline hash.
- Add or update tests for schema and workflow behavior.
