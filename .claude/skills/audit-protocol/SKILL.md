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
6. Prepare `review.txt` and a working decision JSON for the protocol iteration.
   Do not generate HTML; adjudication happens entirely in this console.
7. Walk review-required issues interactively by default. An issue requires an
   individual decision when it has a patch; has blocker, high, or medium
   severity; is unresolved scientific ambiguity, protocol-version confusion,
   or an evaluator/matching conflict; or recommends changing the evaluator,
   harness, or scoring inclusion.
   Show exactly one such undecided issue at a time: task/field, current value,
   proposed value, source locator, reason, severity, and impact.
8. Summarize `verified_no_change` fields and low informational findings once by
   task and count. Ask for one explicit grouped decision on the low findings:
   accept as observations with no ground-truth edit, reject and keep current,
   leave unresolved, or expand for individual review. If the human chooses a
   group disposition, write a separate issue decision for every issue ID using
   that explicit answer. Never treat the summary as approval.
9. Record only an explicit human disposition: `accept`, `reject`, `modify`,
   `unresolved`, or `exclude`, with rationale and confirmed cause. “Continue”
   is never scientific approval. `back`, `skip`, `status`, `quit`, and `resume`
   navigate the review and do not imply a disposition.
10. Immediately after each answer, atomically update the working decision JSON
   and validate it in working mode. On restart, preserve recorded decisions and
   resume at the first undecided issue. Never alter the immutable proposal.
11. After every proposal issue has a recorded decision, show a concise decision
    summary and ask the human to confirm finalization. Only then mark the
    decision final and save the review iteration immutably. Do not apply or
    promote merely because the walkthrough ended.
12. After explicit human authorization to apply, generate a fresh preview from
    pinned baselines, apply accepted patches deterministically, validate linked
    T1–T3, run correction regressions, and promote the three canonical files
    without overwriting an approved protocol directory.

## Batch mode

1. Complete source approval for each protocol before starting its worker.
2. Launch at most ten protocol-scoped workers concurrently. Each worker owns
   only its protocol audit directory and runs evidence then comparison.
3. A worker stops after its validated proposal. It cannot adjudicate, apply,
   promote, publish, or edit another protocol.
4. Isolate failures. Keep completed proposals when another protocol blocks.
5. Sort the review queue by protocol ID and severity. Human review happens
   later, one protocol and one issue at a time, using the interactive controller
   rules above.

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
- If current T3 JSON is absent but legacy HTML contains human-curated workflow
  steps, first translate those steps in document order into the complete T3
  candidate. Preserve legacy wording and locators on its states and transitions.
  Use current T1/T2 IDs only as explicit identifier normalization.
- Compare that legacy-derived T3 candidate with frozen primary evidence. Emit
  every primary-supported correction, addition, conflict, or unsupported claim
  as a separate issue; never silently fold it into the root candidate patch.
  Classify the absent T3 JSON as a migration/schema omission, not an original
  human-curation error. An HTML reference to a missing asset does not establish
  the unseen asset's contents.
- Reference only T2 oligos actually used during library generation. Sequencing
  primers may remain valid T2 records without being referenced from T3.
- Assign each audited field one status: `verified_no_change`,
  `proposed_correction`, `missing_source_evidence`, `ambiguous`, or
  `external_knowledge_required`.
- Every non-verified status has an issue. Only an exact ground-truth correction
  may carry an RFC 6902 patch.
- Keep the issue count proportional to human decisions. Group related fields
  with the same cause and remedy into one issue. Do not create separate issues
  for optional aliases, conditions, family sizes, or descriptive metadata when
  the scientific/scoring value is unchanged; summarize those observations in
  notes. Calibrate molecular or scoring conflicts at medium severity or above.
  Use `unresolved_scientific_ambiguity`, not a low generic source conflict, when
  conflicting sources leave the molecular interpretation genuinely unresolved.
- Validate T1/T2/T3 links and graph consistency. Propose, but never approve or
  apply, changes.

The audit agent assists. The human reviewer is the final authority.
