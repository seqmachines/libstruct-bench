---
name: libstruct-audit
description: Build, review, or independently exercise the sequencing-library ground-truth audit pipeline. Use when Codex changes audit schemas, validators, CLIs, deterministic correction logic, release tooling, or tests, or when Codex performs a second audit of a protocol packet.
---

# Library-structure audit

Read `docs/audit/README.md` and the two policy documents it links before work.

For pipeline engineering:

1. Preserve the separation between primary evidence, legacy HTML, current
   benchmark records, agent proposals, human decisions, and releases.
2. Keep public artifacts schema-versioned and reject stale hashes or
   unapproved corrections.
3. Implement deterministic behavior in `src/libstruct_bench/audit/` and cover
   it under `tests/audit/`.
4. Do not copy private source bundles or ground truth into this repository.

For an independent protocol audit:

1. Use only the files in the validated input manifest.
2. Follow `docs/audit/evidence-policy.md`.
3. Emit a `libstruct.protocol_audit.v1` proposal and identify the run as an
   independent Codex review.
4. Do not approve or apply your own findings.
