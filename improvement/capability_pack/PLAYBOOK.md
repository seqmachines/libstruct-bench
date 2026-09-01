# LibGen molecular-capability playbook

This pack is a protocol-neutral reasoning aid. The target protocol's supplied
sources are always authoritative. Do not use this pack as evidence for a
target-specific sequence, oligo, operation, state, or topology.

## Required working order

1. Create one work record conforming to `schemas/work_record.schema.json`.
   Inventory every supplied source and mark its coverage. Inventory every
   source-visible oligo, molecular event, state, branch, and terminal before
   drafting predictions. Record support, exact source locators, usage scope,
   and exactly one disposition: modeled, merged, folded, excluded, or
   unresolved. Never repair a source gap from memory.
2. Build T2 as oligo families. Represent variable barcode and index panels with
   one family template and fixed-length placeholders. Keep source-visible
   orientation, components, and chemical modifications.
3. Build one chronological T3 molecular process. Add a state when sequence
   architecture, strand structure, or a downstream-relevant branch changes.
4. For every transition, account for all substrates and products. Classify each
   product exactly once as carried forward or discarded. A discarded product
   cannot reappear downstream.
5. Model each physical strand independently in its own 5′→3′ direction. Record
   paired regions, overhangs, nicks, gaps, and breaks instead of flattening them
   into prose.
6. Link every physically used or incorporated oligo to its T2 family. Sequence
   equality alone does not establish oligo identity.
7. Audit terminal states against the complete process. Do not invent missing
   repair, extension, ligation, indexing, or sequencing-ready structures.
8. Record a state signature for every modeled state and an event record for
   every modeled transition. Put the T2 and T3 drafts in the work record; do
   not maintain independent hand-edited output copies.
9. Compile both predictions together, then run the one unified audit below and
   the benchmark validator. Resolve each finding from target evidence or keep
   the inventory item unresolved; never change evidence merely to silence a
   checker.

```bash
python3 capability_pack/tools/compile_work_record.py \
  --work-record /tmp/libgen-work-record.json \
  --t2-out /logs/artifacts/t2_prediction.json \
  --t3-out /logs/artifacts/t3_prediction.json

python3 capability_pack/tools/audit_predictions.py \
  --work-record /tmp/libgen-work-record.json \
  --t2 /logs/artifacts/t2_prediction.json \
  --t3 /logs/artifacts/t3_prediction.json
```

Exit 0 means all registered deterministic controls passed, exit 1 means the
JSON report contains findings, and exit 2 means the inputs or execution were
invalid. The machine-readable command templates and complete control registry
are in `tools/control_index.json`; consumers should use that file instead of
hard-coding pack paths.

## Governing invariants

- Evidence precedes modeling; a plausible mechanism is not evidence.
- Molecular material is conserved across every transition unless an explicit
  synthesis, degradation, separation, or discard explains the change.
- Every noninitial substrate was previously carried forward.
- Every carried product is consumed later or is a terminal output.
- Every product is classified, and no product is both carried and discarded.
- Paired explicit sequences are reverse complements when the relationship is
  declared reverse-complementary.
- Cleanup and routine handling do not create molecular states unless they
  produce a continuing branch or materially change molecular architecture.
- Final outputs are reachable from initial states along carried-product paths.

Use the checklists in `checklists/` as working controls and the programs in
`tools/` as deterministic diagnostics. Checker success establishes internal
consistency only; it never establishes scientific support.
