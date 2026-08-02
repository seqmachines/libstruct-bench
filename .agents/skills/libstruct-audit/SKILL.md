---
name: libstruct-audit
description: Build, review, or independently exercise the sequencing-library ground-truth audit pipeline. Use when Codex changes audit schemas, validators, CLIs, deterministic correction logic, release tooling, or tests, or when Codex performs a second audit of a protocol packet.
---

# Library-structure audit

Read `docs/audit/README.md`, `evidence-policy.md`,
`adjudication-policy.md`, and `benchmark-standardization-policy.md` before
work.

For pipeline engineering:

1. Preserve the separation between primary evidence, legacy HTML, current
   benchmark records, agent proposals, human decisions, and releases.
2. Keep audit artifacts schema-validated and reject stale hashes or unapproved
   corrections.
3. Require an accepted human decision and deterministic root patch when a
   ground-truth artifact, such as the first T3 workflow, does not yet exist.
4. Implement deterministic behavior in `src/libstruct_bench/audit/` and cover
   it under `tests/audit/`.
5. Do not copy private source bundles or ground truth into this repository.

For an independent protocol audit:

1. Use only the files in the validated phase packet.
2. Follow `docs/audit/evidence-policy.md`.
3. Respect the isolated primary-evidence phase and use deterministic
   renditions only as aids for the original sources.
4. Emit a protocol audit proposal and identify the run as an
   independent Codex review.
5. Do not approve or apply your own findings.
