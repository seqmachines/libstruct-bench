---
name: audit-protocol
description: Audit one sequencing protocol or prepare up to ten protocol proposals concurrently, then guide human adjudication and deterministic promotion.
---

# Audit sequencing-library ground truth

## Modes

- `/audit-protocol <protocol_id>` audits one protocol.
- `/audit-protocol <id1> ... <id10>` prepares up to ten protocols concurrently.
- When the system prompt declares `PHASE: evidence` or `PHASE: comparison`,
  act only as that read-only worker.
- Otherwise act as the interactive controller and keep commands/details out of
  the console unless they are needed to resolve a blocker.

## Interactive controller

1. Read `CLAUDE.md` and `docs/audit/`. Reuse valid immutable artifacts.
2. Use `/Users/seqmachines/playground/protocols-test` as the data root,
   `ground_truth_audit/` for private audit history, and `ground_truth/` for
   human-approved records. Never modify `scg-v1-upload` or upload audit data.
3. Catalog sources and ask the human to explicitly include, exclude, or mark
   unavailable every pending source. Do not infer source approval.
4. For each approved protocol:
   - create deterministic renditions;
   - run primary-evidence extraction in an isolated packet;
   - freeze and validate the evidence;
   - build a comparison packet containing the frozen evidence, legacy HTML,
     current T1/T2/T3 records, reviewed TSV projection, and optional run
     artifacts;
   - run comparison and stop with a validated proposal.
5. Keep packets and runs immutable and hash-pinned. Workers are read-only. If
   invoking a nested Claude process, use `env -u CLAUDECODE`.
6. In the console show only review cases: task/field, current value, proposed
   value, source locator, reason, severity, and impact. Summarize
   `verified_no_change` fields by count.
7. Record only explicit human dispositions: `accept`, `reject`, `modify`,
   `unresolved`, or `exclude`, with rationale and confirmed cause. “Continue”
   is never scientific approval.
8. Save each review iteration immutably. A working decision may generate a
   fresh preview from the pinned baselines. Continue until the human confirms
   it is correct.
9. Finalize only after every issue has a decision. Apply accepted patches,
   validate linked T1–T3, run correction regressions, and promote the three
   canonical files without overwriting an approved protocol directory.

## Batch mode

1. Complete source approval for each protocol before starting its worker.
2. Launch at most ten protocol-scoped workers concurrently. Each worker owns
   only its protocol audit directory and runs evidence then comparison.
3. A worker stops after its validated proposal. It cannot adjudicate, apply,
   promote, publish, or edit another protocol.
4. Isolate failures. Keep completed proposals when another protocol blocks.
5. Sort the review queue by protocol ID and severity. Human review can happen
   later, one protocol at a time.

## Evidence worker

- Read every approved primary paper, protocol, supplement, spreadsheet, table,
  figure, diagram, and rendition completely. Account for every source once.
- Do not use legacy curation, current ground truth, prior answers, web search,
  remembered kits, or review memory.
- Review T2 and T3 chronologically. Register each oligo on first appearance,
  then reference its T2 ID from T3 transitions.
- Model T3 as molecular states and transitions. Create a state only for a
  meaningful molecular/physical change; classify carried and discarded
  products; represent PCR cycling as one transition; do not create a
  transition for a display-only paragraph or figure.
- Preserve missing data, alternatives, and conflicts. Never fill a sequence
  from external memory.

## Comparison worker

- Read frozen evidence first, then only the packet-listed comparison inputs.
- Assign each audited field one status: `verified_no_change`,
  `proposed_correction`, `missing_source_evidence`, `ambiguous`, or
  `external_knowledge_required`.
- Every non-verified status has an issue. Only an exact ground-truth correction
  may carry an RFC 6902 patch.
- Validate T1/T2/T3 links and graph consistency. Propose, but never approve or
  apply, changes.

The audit agent assists. The human reviewer is the final authority.
