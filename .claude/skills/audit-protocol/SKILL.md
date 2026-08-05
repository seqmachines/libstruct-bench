---
name: audit-protocol
description: Audit one sequencing protocol or prepare up to ten protocol proposals concurrently, then guide human adjudication and deterministic promotion.
hooks:
  Stop:
    - hooks:
        - type: command
          command: python3
          args:
            - "${CLAUDE_PROJECT_DIR}/.claude/hooks/require_audit_question.py"
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

Use Claude Code's `AskUserQuestion` tool for every human gate. Never substitute
a printed question and then end the response while the tool is available. Ask
exactly one single-select question at a time. Use only dispositions valid for
the current target; the tool's free-text choice handles clarification and
navigation. If the human declines or asks a question, answer it and call
`AskUserQuestion` again for the still-undecided gate.

Use one interactive review pass per protocol. If the human asks for a change,
discuss it, update the working decision or candidate, validate it, and continue
in this same Claude Code session. Do not create another review iteration merely
to incorporate human edits. Start a new iteration only when a manifest-listed
source, manifest, pinned baseline, canonical schema, or immutable finalized
decision has changed.

For every individual issue, use this exact two-part sequence in one turn:

1. Print the complete review card as normal console text. Include issue number
   and total, ID, task, severity, category, defect, target kind and patch status,
   title, full current value, full proposed value, exact evidence locators split
   by `supports current` and `supports proposed`, reason and impact, and any
   relevant modeling or policy note. Do not replace this card with a recap.
   Append the literal hidden marker `<!-- audit-question-required -->` after the
   card so the skill-scoped stop hook can prevent an accidental early yield.
2. Immediately call `AskUserQuestion` with the valid dispositions and concise
   consequence descriptions. Its question may be short because the complete
   context must already be visible directly above it.

An issue-review turn is incomplete until the `AskUserQuestion` tool call has
been made. After printing a marked card, never stop, yield, summarize, or wait
for a typed reply. The stop hook will return control with an instruction to call
the tool if this contract is missed; on that continuation, call
`AskUserQuestion` immediately without repeating the card.

1. Read `CLAUDE.md` and `docs/audit/`. Reuse valid immutable artifacts.
   Reuse frozen evidence only when it validates against the current canonical
   evidence schema and its recorded schema hash matches. Otherwise preserve it
   as history and rerun the isolated evidence phase before comparison.
   Do not reuse a comparison proposal for application when a current baseline
   is legacy-shaped and the proposal lacks its canonical root conversion.
   Preserve that proposal and decision as history, reuse the frozen evidence,
   and create a fresh comparison run and review iteration.
2. Use `/Users/seqmachines/playground/protocols-test` as the data root,
   `ground_truth_audit/` for private audit history, and `ground_truth/` for
   human-approved records. Never modify `scg-v1-upload` or upload audit data.
3. Catalog sources without a human gate. Include every discovered file that is
   present and hashable. Mark every missing file `unavailable`, retain it in the
   catalog/manifest for provenance, and exclude it from phase packets. When an
   archived catalog contains `pending` statuses, let the manifest builder
   resolve them by availability instead of asking the human.
4. For each approved protocol:
   - inspect current T1/T2/T3 records against the canonical schemas and require
     one reviewed root conversion for every legacy-shaped record;
   - create deterministic renditions;
   - run primary-evidence extraction in an isolated packet;
   - freeze and validate the evidence;
   - build a comparison packet containing the frozen evidence, legacy HTML,
     current T1/T2/T3 records, reviewed TSV projection, and optional run
     artifacts;
   - run comparison and stop with a validated proposal.
5. Keep packets and runs immutable and hash-pinned. Workers are read-only. If
   invoking a nested Claude process, use `env -u CLAUDECODE`.
   A completed worker run that fails validation is retained beside its intended
   output as `<run>.rejected/`; preserve it for diagnosis and use a new run ID
   after fixing the cause.
   When worker guidance omitted a deterministic validator invariant and that
   omission caused a rejection, attribute the failure to
   `agent_harness_or_context_error` with harness responsibility and confirmed
   cause `agent_harness_or_context_error`, not to human curation. Keep this
   diagnostic attribution separate from scientific ground truth.
6. Prepare `review.txt` and a working decision JSON for the protocol iteration.
   Do not generate HTML; adjudication happens entirely in this console.
7. Walk review-required issues interactively by default. An issue requires an
   individual decision when it has a patch; has blocker, high, or medium
   severity; is unresolved scientific ambiguity, protocol-version confusion,
   or an evaluator/matching conflict; or recommends changing the evaluator,
   harness, or scoring inclusion.
   Show exactly one such undecided issue at a time: task/field, current value,
   proposed value, source locator, reason, severity, and impact. Then invoke
   `AskUserQuestion` in the same turn; do not merely print the disposition
   options and do not omit the full review card just because the selector also
   contains a short summary.
8. Summarize `verified_no_change` fields and low informational findings once by
   task and count. Ask for one explicit grouped decision on the low findings:
   accept as observations with no ground-truth edit, reject and keep current,
   leave unresolved, or expand for individual review. If the human chooses a
   group disposition, write a separate issue decision for every issue ID using
   that explicit answer. Obtain it with `AskUserQuestion`. Never treat the
   summary as approval.
9. Record only an explicit human disposition: `accept`, `reject`, `modify`,
   `unresolved`, or `exclude`, with rationale and confirmed cause. “Continue”
   is never scientific approval. `back`, `skip`, `status`, `quit`, and `resume`
   navigate the review and do not imply a disposition.
   Offer `modify` only for `groundtruth_record` or `new_groundtruth_record`
   targets. For evidence-only, source, policy, harness, or evaluator findings,
   accept or reject the finding and record any representational qualification
   in the rationale.
10. Immediately after each answer, atomically update the working decision JSON
    and validate it in working mode. On restart, preserve recorded decisions and
    resume at the first undecided issue. Never alter the immutable proposal. A
    reviewer may chat, clarify, and revise the working candidate repeatedly
    before final approval without creating a new review iteration.
11. When a proposal creates a new T1/T2/T3 document or root-converts a
    legacy-shaped existing record, defer its root decision until its delta
    issues are decided. Build one final document from the conversion candidate
    and those explicit decisions, using the current T3 granularity policy.
    Validate it against the canonical task schema. Do not treat accepted delta
    prose as an independently applicable patch. Keep frozen primary
    locators in the audit proposal and decision; cleaned T1–T3 must not contain
    evidence, lineage, review status, or audit notes.
    Every T3 state must use a controlled `strand_architecture`, contain each
    physical strand explicitly in its own 5′→3′ direction, and identify its
    `reference_strand_id`. A terminal state's reference strand should carry
    `sequence_architecture` exactly matching a T1 annotated or plain
    library sequence. Its optional segment annotations may be simpler than T1
    and must not be expanded merely to duplicate T1. If the complete
    architecture is omitted, exact ordered segment identity is the validation
    fallback. Match terminal states and T1 libraries one-to-one; do not create
    T1 library IDs or `final_library_links`.
12. After every proposal issue has a recorded decision, show one concise
    combined summary of the final T1–T3 candidates and dispositions. Use one
    `AskUserQuestion` call to approve any complete root decisions and confirm
    review finalization. If the human requests an edit, update and revalidate
    the same working candidate, then return to this final question; do not open
    another iteration. Only after approval mark the decision final and save it
    immutably. Do not apply or promote merely because the walkthrough ended.
13. After explicit human authorization to apply, generate a fresh preview from
    pinned baselines, apply accepted patches deterministically, validate linked
    T1–T3, run correction regressions, and promote the three canonical files
    without overwriting an approved protocol directory. Obtain the application
    authorization with `AskUserQuestion`.

## Batch mode

1. Build the availability-resolved manifest for each protocol before starting
   its worker. Do not stop for source approval.
2. Launch at most ten protocol-scoped workers concurrently. Each worker owns
   only its protocol audit directory and runs evidence then comparison.
3. A worker stops after its validated proposal. It cannot adjudicate, apply,
   promote, publish, or edit another protocol.
4. Isolate failures. Keep completed proposals when another protocol blocks.
5. Sort the review queue by protocol ID and severity. Human review happens
   later, one protocol and one issue at a time, using the interactive controller
   rules above.

## Evidence worker

- Read every included primary paper, protocol, supplement, spreadsheet, table,
  figure, diagram, and rendition completely. Account for every included source
  once. Never infer content from entries marked unavailable.
- Do not use legacy curation, current ground truth, prior answers, web search,
  remembered kits, or review memory.
- Review T2 and T3 chronologically. Register each oligo on first appearance,
  then reference its T2 ID from T3 transitions.
- Model T3 as the smallest scientifically sufficient molecular graph. Create
  states and transitions primarily for sequence architecture or strand
  structure changes. When neither changes, fold cleanup, purification, size
  selection, pooling, washing, QC, quantification, dilution, routine reagent
  handling, and inactivation into the nearest substantive transition's
  operation detail and major reagents; retain source details in the audit
  evidence artifact. Add a separate
  non-sequence node only when a distinct branch or carried molecular product is
  essential downstream, and explain why. Keep only scientifically important
  products and dead ends;
  represent PCR cycling as one transition; do not create a transition for a
  display-only paragraph or figure.
- States represent meaningful carried-forward products, not transient reaction
  complexes. For template switching, model the pre-switch state as an
  mRNA:cDNA hybrid with exactly two logical strands, one RNA and one DNA. Put
  the template-switch oligo in the template-switching transition's `oligo_ids`
  and represent the product as cDNA containing the incorporated TSO-derived
  sequence. Do not create a persistent third TSO strand unless a packet-listed
  source explicitly establishes that the three-strand complex is carried
  forward.
- Do not assume a product is single- or double-stranded. For every state,
  choose `single_stranded`, `double_stranded`, `partially_duplex`,
  `rna_dna_hybrid`, `y_shaped_duplex`, `mixed_population`, or `unknown` from
  the source evidence. Represent one strand for a single-stranded state and
  both strands for a duplex, each written independently 5′→3′ with its segments
  in that order. Identify the reference strand corresponding to T1; split
  segments at pairing boundaries and list each paired side 5′→3′;
  preserve paired regions, overhangs, internal unpaired regions, nicks, gaps,
  and RNA/DNA strand identity. Explicit paired sequences must be reverse
  complements unless the source documents noncanonical or unknown pairing.
- Before returning evidence, preflight every state against
  `validate_molecular_state_architecture` in `groundtruth.py` and repair
  representation or bookkeeping inconsistencies:
  - strand IDs and state-wide segment IDs are unique, `reference_strand_id`
    resolves, and every strand is written `5_to_3`;
  - `single_stranded` has exactly one strand and no paired region;
    `double_stranded` has exactly two strands, a paired region, and no unpaired
    segment; `partially_duplex` has at least two strands, a paired region, and
    at least one unpaired segment; `rna_dna_hybrid` has exactly two logical
    strands, one RNA and one DNA, plus a paired region; and `y_shaped_duplex`
    has exactly two logical strands, a paired region, and an unpaired arm on
    both strands. Pairing declared for `mixed_population` or `unknown` still
    obeys the reference and ordering rules;
  - paired-region and discontinuity IDs are unique. Each pairing side resolves
    to a different known strand and lists nonempty, contiguous segment IDs in
    that strand's 5′→3′ order. Except for `mixed_population`, a segment may not
    appear in more than one paired region;
  - every segment labeled `paired_region` appears in `paired_regions`, and a
    segment absent from `paired_regions` is not labeled `paired_region`.
    Preserve genuinely unpaired random-primer, SMART, overhang, linker, and
    adapter regions with an unpaired structural role; never invent a pairing
    merely to pass validation. A declared paired side contains only
    `paired_region`, `mixed`, or `unknown` segments;
  - an explicit `reverse_complementary` relationship must be
    reverse-complementary; preserve supported `documented_noncanonical` or
    `unknown` pairing instead of changing source sequence; and
  - every discontinuity references adjacent segments on its declared strand in
    5′→3′ order.
  Do not change scientifically supported sequence, strand identity, or pairing
  merely to satisfy the validator.
- Keep variable-region placeholder roles biological and orientation-free. Use
  canonical `[ROLE:LENGTH]` placeholders such as `[I5_INDEX:8]`,
  `[TN5_INDEX:8]`, and `[I7_INDEX:8]`; never create `_RC`, `_REVERSE`, `_FWD`,
  or similar directional role variants.
- On every oligo-derived strand segment, retain the T2 oligo ID, store the
  bases on that modeled strand in `sequence`, and set `orientation_to_source`
  to `same_orientation`, `reverse_complement`, or `unknown`. The linked T2
  record, not the segment, owns the source-visible oligo sequence.
- Preserve missing data, alternatives, and conflicts. Never fill a sequence
  from external memory.

## Comparison worker

- Read frozen evidence first, then only the packet-listed comparison inputs.
- Before scientific comparison, convert every legacy-shaped current T1/T2/T3
  record into a canonical candidate using only the current record, legacy HTML,
  and reviewed TSV projection. Emit one complete root replacement issue for
  each such record. Preserve legacy values in audit lineage, remove legacy-only
  metadata from the candidate, and do not call a representation migration a
  scientific human-curation error.
- Keep approved candidates minimal. T1 has no `evidence`,
  `ground_truth_status`, `library_id`, or `strands`. T2 has no `limitations`,
  `baseline_lineage`, `evidence`, `ground_truth_status`, or `notes`. T3 has no
  `limitations`, `ground_truth_status`, `notes`, `evidence`, or
  `workflow_branch`, and no repeated `modality`; store modality once at the
  document root.
- During conversion, normalize placeholders to orientation-free
  `[ROLE:LENGTH]` values. Preserve the strand-specific bases in T1/T3 segment
  `sequence`, the source-visible oligo bases in T2, and their relationship in
  `oligo_derivations[].orientation_to_source`; never generate an `_RC`,
  `_REVERSE`, or `_FWD` placeholder role.
- For a root-converted artifact, keep primary-source deltas patch-free so the
  controller can compile human decisions into one final root replacement.
- The harness supplies the canonical T1, T2, and T3 schemas as non-evidentiary
  formatting constraints. Every complete new ground-truth root patch must
  satisfy its task schema exactly; never reconstruct an outer document shape
  from legacy fields or sibling style.
- Apply the same compact T3 rule as the evidence worker when translating legacy
  steps and proposing primary-source deltas. Do not propose a standalone state
  or transition for a non-sequence preparation detail unless the narrow branch
  or essential-carried-product exception applies.
- Preserve the strand architecture actually curated in legacy HTML before
  comparison. Do not collapse top/bottom strands, partial duplexes, Y-shaped
  constructs, hybrids, overhangs, or nicks into a single sequence. Treat a
  disagreement about molecular strand architecture as a
  `strand_architecture` defect and send material conflicts for human review.
- If current T3 JSON is absent but legacy HTML contains human-curated workflow
  steps, first translate those steps in document order into the complete T3
  candidate. Preserve legacy wording and locators in audit lineage, not on its
  states and transitions. Use current T2 IDs only as explicit identifier
  normalization; match T3 terminal states to T1 by structure.
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
  the audit issue. Calibrate molecular or scoring conflicts at medium severity or above.
  Use `unresolved_scientific_ambiguity`, not a low generic source conflict, when
  conflicting sources leave the molecular interpretation genuinely unresolved.
- Validate T1/T2/T3 links and graph consistency. Propose, but never approve or
  apply, changes.

The audit agent assists. The human reviewer is the final authority.
