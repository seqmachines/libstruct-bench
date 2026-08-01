---
name: audit-protocol
description: Audit one sequencing-library protocol by comparing primary protocol files, legacy scg_lib_structs HTML, and current benchmark JSON. Use for evidence-grounded protocol review, discrepancy discovery, correction proposals, or structured human-adjudication preparation.
---

# Audit one protocol

1. Read the versioned audit policies and the schema supplied by the harness.
2. Read `packet.json` and its phase-projected `manifest.json`. Use only listed
   files and keep source roles distinct.
3. In the `evidence` phase, read every primary source completely and emit
   `libstruct.protocol_evidence.v1`. Read all packet-listed text, table, and
   figure renditions, but ground claims in the originals. Do not compare with
   legacy or benchmark records; they are intentionally unavailable.
4. In the `comparison` phase, read the frozen evidence artifact first. Only
   then compare it with legacy HTML, current records, primary sources, and any
   optional run artifacts. Emit `libstruct.protocol_audit.v2`.
5. Check source versions, multi-library completeness, variable-region lengths,
   strand orientation, naming, normalization, extraction, harness behavior,
   and evaluator assignments. Preserve conflicting interpretations.
6. Cite stable locations and transformations. Propose RFC 6902-style patches
   only for exact ground-truth changes. When T3 has no current artifact, use
   one `new_groundtruth_record` issue and one root-level patch containing the
   complete proposed workflow document.
7. Preserve projected global-TSV row numbers, its full-file source hash, and
   old T2 values as baseline lineage. Never expose or infer rows belonging to
   another protocol.

Work read-only. Do not browse, use remembered kit sequence, consult training or
review memory, edit any file, or record a human decision.
