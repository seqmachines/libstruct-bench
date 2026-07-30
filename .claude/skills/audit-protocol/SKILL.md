---
name: audit-protocol
description: Audit one sequencing-library protocol by comparing primary protocol files, legacy scg_lib_structs HTML, and current benchmark JSON. Use for evidence-grounded protocol review, discrepancy discovery, correction proposals, or structured human-adjudication preparation.
---

# Audit one protocol

1. Read `docs/audit/evidence-policy.md`,
   `docs/audit/adjudication-policy.md`, and
   `schemas/audit/protocol_audit.v1.schema.json`.
2. Read the supplied `libstruct.audit_input_manifest.v1` document. Use only
   its listed files and keep each source role distinct.
3. Inventory all final sequencing products and relevant oligos before
   comparing sequences.
4. Compare current JSON to legacy HTML, then compare both with every primary
   document. Check source versions, multi-library completeness, variable-region
   lengths, strand orientation, normalization, and evaluator identifiers.
5. Cite stable source locations and record each transformation used to derive a
   proposed value. Preserve conflicting interpretations.
6. Emit one `libstruct.protocol_audit.v1` JSON object. Propose RFC 6902-style
   patches only when the evidence supports an exact change.

Work read-only. Do not browse, use remembered kit sequence, edit source or
ground-truth files, or record a human decision.
