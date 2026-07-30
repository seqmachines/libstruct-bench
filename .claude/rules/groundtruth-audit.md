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
- Never add a path that lets an audit proposal overwrite canonical ground
  truth without a validated human decision and baseline hash.
- Add or update tests for schema and workflow behavior.
