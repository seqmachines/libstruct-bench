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
- When the system prompt declares `PHASE: comparison`, act only as the
  conversion-first, read-only worker.
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

1. Read `CLAUDE.md` and `docs/audit/`. Reuse immutable proposals directly for
   application only when their manifest, packet, baselines, schemas, prompt,
   and policy hashes still match. An in-progress review may use the
   representation-only schema-migration exception below. Do not reuse a comparison proposal for application when a current baseline
   is legacy-shaped and the proposal lacks its canonical root conversion.
   Preserve that proposal and decision as history and create a fresh
   conversion-first comparison run and review iteration.
2. Use `/Users/seqmachines/playground/protocols-test` as the data root,
   `ground_truth_audit/` for private audit history, and `ground_truth/` for
   human-approved records. Write manifests, renditions, packets, runs, reviews,
   applications, and promotions directly under their corresponding
   `ground_truth_audit/<kind>/` directories. Do not create or reuse a `pilot/`
   namespace for active work; `archive/` is history only. Never modify
   `scg-v1-upload` or upload audit data.
   When an unfinalized proposal predates the move of T3 `modality` from the
   document root to each workflow, keep the proposal immutable and reshape
   only its working root candidate during the same review. For a single T1
   modality, remove the root field and copy the exact T1 modality to the sole
   workflow. For multiple T1 modalities, keep exactly one workflow per
   modality, retain same-modality alternatives as branches, and assign final
   states by their already-reviewed T1 terminal structures. Freeze nucleotide
   sequences, placeholders, orientations, oligo identities, molecular states,
   operations, and branch order; shared upstream nodes may be duplicated
   without changing their content. Record the current-schema root replacement
   as the human-approved `modify` decision and revalidate T1–T3. If the split
   is scientifically ambiguous, ask the human or start a fresh comparison;
   never guess. A finalized stale-schema decision remains immutable and needs
   a new iteration.
3. Catalog sources without a human gate. Include every discovered file that is
   present and hashable. Mark every missing file `unavailable`, retain it in the
   catalog/manifest for provenance, and exclude it from phase packets. When an
   archived catalog contains `pending` statuses, let the manifest builder
   resolve them by availability instead of asking the human.
4. For each approved protocol:
   - create deterministic renditions;
   - build one packet containing legacy HTML, current T1/T2/T3 records, the
     reviewed TSV projection, primary sources and renditions, and optional run
     artifacts;
   - run one comparison worker. It must finish the canonical legacy conversion
     before reading primary sources, then validate that candidate against them;
   - stop with one validated proposal.
5. Keep packets and runs immutable and hash-pinned. Workers are read-only. If
   invoking a nested Claude process, use `env -u CLAUDECODE`.
   When a completed worker artifact fails the audit schema, a canonical
   T1/T2/T3 schema, or linked validation, let the runner perform at most two
   bounded repair attempts before rejection. Each repair receives only the
   current artifact and exact validator error, cannot read the packet or
   sources, and is followed by full validation. It may change only the root
   candidate representation or deterministic ledger link needed by the error;
   source coverage, evidence, audited-field conclusions, and issue identities
   and classifications remain fixed. Preserve every input, output, transcript,
   validator error, changed-path list, and hash. If repair is exhausted, retain
   the original artifact and all attempts as `<run>.rejected/`; do not rerun the
   full comparison merely to fix a deterministic formatting inconsistency.
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
    prose as an independently applicable patch. Keep primary-source locators
    in the audit proposal and decision; cleaned T1–T3 must not contain
    evidence, lineage, review status, or audit notes.
    Every T3 state must use a controlled `strand_architecture`, contain each
    physical strand explicitly in its own 5′→3′ direction, and identify its
    `reference_strand_id`. A terminal state's reference strand should carry
    `sequence_architecture` matching the single canonical T1 `library_sequence`
    in the same orientation or as its token-aware reverse complement. Preserve
    the physical T3 strand in its actual 5′→3′ direction; never rewrite it merely
    to follow T1's display orientation. Keep biological insert placeholders in that T1 sequence;
    do not emit `annotated_library_sequence` or a benchmark scoring projection.
    T2 components are ordered inline descriptions and must not carry
    `component_id`. Optional T3 segment annotations may be simpler than T1
    and must not be expanded merely to duplicate T1. If the complete
    architecture is omitted, same- or reverse-complement ordered segment
    identity is the validation fallback. Match terminal states and T1 libraries
    one-to-one; do not create
    T1 library IDs or `final_library_links`.
    Before asking for any final T3 root decision or scientific approval, the
    controller must directly open the immutable packet's primary PDFs,
    supplementary tables/spreadsheets, and relevant figures or renditions. Do
    not rely only on the comparison worker's summary, proposal prose,
    transcript, or cited locators. For every T3 state and transition, fact-check
    the substrate/current molecular state, operation, T2 oligos and major
    reagents, products, carried-forward product, strand architecture, and
    sequence change against exact primary-source locations. Show a concise
    console table with one row per state or transition and status `verified`,
    `conflict`, `missing`, or `ambiguous`. A material conflict or missing
    support is a review blocker: present it to the human and keep the review
    working until it is explicitly resolved; never silently change the
    candidate. Only then may the controller open the final approval selector.
12. After every proposal issue has a recorded decision, show one concise
    combined summary of the final T1–T3 candidates and dispositions. Use one
    `AskUserQuestion` call to approve any complete root decisions and confirm
    review finalization. If the human requests an edit, update and revalidate
    the same working candidate, then return to this final question; do not open
    another iteration. Only after approval mark the decision final and save it
    immutably. Do not apply or promote merely because the walkthrough ended.
13. Treat every finalized decision without a corresponding approved
    `ground_truth/<protocol_id>/` directory as finalized but unpromoted. On
    controller start or resume, detect that state and do not rerun its review.
    For a single protocol, immediately after finalization print the exact
    protocol and destination, append
    `<!-- audit-application-question-required -->`, and call `AskUserQuestion`
    with two choices: apply and promote now, or leave the finalized review
    unapplied. In batch mode, retain each finalized protocol in an unpromoted
    queue, finish the remaining human reviews, then show the exact queued
    protocol IDs and ask one grouped application question with the same two
    choices. Do not stop at a finalization recap or silently move past the
    application gate once the selected review queue is complete.
14. Only an explicit apply-and-promote answer authorizes the deterministic
    action. Generate fresh candidates from the pinned baselines, verify proposal
    and decision hashes, apply accepted patches, validate linked T1–T3, run
    correction regressions, and promote each successful protocol's three files
    to `/Users/seqmachines/playground/protocols-test/ground_truth/<protocol_id>/`.
    Keep application and promotion logs under `ground_truth_audit/`, isolate a
    failure in one batch protocol, and never overwrite an existing approved
    protocol directory. A leave-unapplied answer preserves the finalized review
    and performs no application or promotion.

## Batch mode

1. Build the availability-resolved manifest for each protocol before starting
   its worker. Do not stop for source approval.
2. Launch at most ten protocol-scoped workers concurrently. Each worker owns
   only its protocol audit directory and runs one conversion-first comparison.
3. A worker stops after its validated proposal. It cannot adjudicate, apply,
   promote, publish, or edit another protocol.
4. Isolate failures. Keep completed proposals when another protocol blocks.
5. Sort the review queue by protocol ID and severity. Human review happens
   later, one protocol and one issue at a time, using the interactive controller
   rules above. After the selected queue is fully reviewed, ask once whether to
   apply and promote the finalized, unpromoted protocols.

## Comparison worker

- This is one audit pass, not a benchmark task. First read only legacy HTML,
  current T1/T2/T3 records, and the reviewed TSV projection. Do not open primary
  sources or renditions until the complete legacy-derived candidate is fixed in
  working context.
- Convert every legacy-shaped current T1/T2/T3 record into a canonical
  candidate without changing its scientific claims. T3 comes primarily from
  the ordered legacy HTML workflow. Emit one complete root replacement issue
  for each legacy-shaped record, or a root add when HTML-derived T3 has no JSON.
  Preserve legacy values and locators in audit lineage, remove legacy-only
  metadata, and do not call migration a scientific human-curation error.
- Keep approved candidates minimal. T1 has no `evidence`,
  `ground_truth_status`, `library_id`, or `strands`. T2 has no `limitations`,
  `baseline_lineage`, `evidence`, `ground_truth_status`, or `notes`. T3 has no
  `limitations`, `ground_truth_status`, `notes`, `evidence`, or
  `workflow_branch`; store `modality` on each workflow and require exactly one
  workflow per modality. Keep same-modality alternatives as branches in that
  workflow.
- During conversion, normalize placeholders to orientation-free
  `[ROLE:LENGTH]` values. Preserve the strand-specific bases in T1/T3 segment
  `sequence`, the source-visible oligo bases in T2, and their relationship in
  `oligo_derivations[].orientation_to_source`; never generate an `_RC`,
  `_REVERSE`, or `_FWD` placeholder role.
  Use canonical examples such as `[I5_INDEX:8]`, `[TN5_INDEX:8]`, and
  `[I7_INDEX:8]`. Set `orientation_to_source` to `same_orientation`,
  `reverse_complement`, or `unknown`.
- For a root-converted artifact, keep primary-source deltas patch-free so the
  controller can compile human decisions into one final root replacement.
- The harness supplies the canonical T1, T2, and T3 schemas as non-evidentiary
  formatting constraints. Every complete new ground-truth root patch must
  satisfy its task schema exactly; never reconstruct an outer document shape
  from legacy fields or sibling style.
- Use the smallest scientifically sufficient T3 graph. Create states and
  transitions primarily for sequence architecture or strand-structure changes.
  Fold cleanup, purification, size selection, pooling, washing, QC,
  quantification, dilution, routine reagent handling, and inactivation into the
  nearest substantive transition when those structures do not change. A
  separate non-sequence node requires an essential branch or distinct
  carried-forward molecular product. PCR cycling is one transition, and a
  display-only paragraph or figure is not one.
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
- Only after conversion is complete, read every included primary source and
  rendition. Account for each primary source exactly once in `source_coverage`.
  Coverage is primary-only: never add legacy HTML, current records, the TSV
  projection, renditions as separate sources, or benchmark-run artifacts to
  `source_coverage`.
  Review T2 and T3 chronologically, then verify the legacy-derived candidate
  rather than constructing an unrelated second answer.
- Emit every primary-supported correction, addition, conflict, or unsupported
  claim as a separate issue; never silently fold it into the root candidate patch.
  Classify the absent T3 JSON as a migration/schema omission, not an original
  human-curation error. An HTML reference to a missing asset does not establish
  the unseen asset's contents.
- Reference only T2 oligos actually used during library generation. Sequencing
  primers may remain valid T2 records without being referenced from T3.
- States are meaningful carried-forward products, not transient complexes. For
  template switching, represent the pre-switch product as an mRNA:cDNA hybrid
  with exactly two logical strands, put the TSO in the template-switching
  transition's `oligo_ids`, and represent the product as cDNA containing its
  incorporated sequence. Do not persist a third TSO strand unless a source
  explicitly establishes that it is carried forward.
- Before returning the proposal, preflight every state against
  `validate_molecular_state_architecture` in `groundtruth.py`: strand and
  segment IDs are unique; `reference_strand_id` resolves; every strand is
  `5_to_3`; `single_stranded` has exactly one strand and no pairing;
  `double_stranded` has exactly two strands, pairing, and no unpaired segment;
  `partially_duplex` has at least one strand, pairing, and an unpaired segment;
  one strand is valid when two disjoint arms pair intramolecularly as a hairpin;
  `rna_dna_hybrid` has exactly two logical strands, one RNA and one DNA, plus
  pairing; and `y_shaped_duplex` has exactly two logical strands, pairing, and
  unpaired arms on both. Pairing sides contain nonempty, contiguous,
  non-overlapping segment IDs in 5′→3′ order and normally resolve to different
  strands; a `partially_duplex` intramolecular hairpin instead pairs two
  disjoint arms on the same strand; every segment labeled
  `paired_region` must occur in a declared paired region, and absent segments
  must not carry that label. Preserve genuinely unpaired random-primer, SMART,
  overhang, linker, or adapter regions. Explicit `reverse_complementary`
  regions must be reverse-complementary. Preserve documented noncanonical or
  unknown pairing rather than changing source content; every discontinuity
  references adjacent segments. Do not change scientifically supported
  sequence, strand identity, or pairing merely to validate.
  Represent a supported hairpin with one physical strand and state property
  `hairpin` or `covalently_closed_dumbbell`. Keep a carried closed dumbbell and
  its enzymatically opened product as separate states/transitions because the
  opening changes strand architecture.
- Assign each audited field one status: `verified_no_change`,
  `proposed_correction`, `missing_source_evidence`, `ambiguous`, or
  `external_knowledge_required`.
- Every non-verified status has an issue. Only an exact ground-truth correction
  may carry an RFC 6902 patch.
- Before returning, make issues and the field ledger exactly reciprocal. Every
  `issues[].issue_id` appears in the `issue_ids` of its referenced field; a
  field with multiple issues lists all of them; every ledger ID resolves to an
  issue; and verified fields list none. The union of
  `audited_fields[].issue_ids` must equal the set of `issues[].issue_id` values.
- Keep the issue count proportional to human decisions. Group related fields
  with the same cause and remedy into one issue. Do not create separate issues
  for optional aliases, conditions, family sizes, or descriptive metadata when
  the scientific/scoring value is unchanged; summarize those observations in
  the audit issue. Calibrate molecular or scoring conflicts at medium severity or above.
  Use `unresolved_scientific_ambiguity`, not a low generic source conflict, when
  conflicting sources leave the molecular interpretation genuinely unresolved.
- Validate T1/T2/T3 links and graph consistency. Present root-level
  `protocol_scope` values must be identical across T1, T2, and T3 (same
  `protocol_version` and `applicable_variants`); child scopes may only narrow
  their parent, and linked object scopes must overlap. When legacy curation is
  a multi-protocol family page, keep the audited protocol consistently scoped
  and report any family/version ambiguity instead of widening one task alone.
  Preserve missing data,
  alternatives, and conflicts; never fill a sequence from memory or the web.
  Propose, but never approve or apply, changes.

The audit agent assists. The human reviewer is the final authority.
