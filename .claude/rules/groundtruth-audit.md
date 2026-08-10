---
paths:
  - "docs/audit/**"
  - "schemas/audit/**"
  - "src/libstruct_bench/audit/**"
  - "tests/audit/**"
---

# Ground-truth audit changes

- Read the canonical policies under `docs/audit/` before editing these paths.
- Put active audit artifacts directly in `ground_truth_audit/manifests/`,
  `renditions/`, `packets/`, `runs/`, `reviews/`, `applications/`, and
  `promotions/`. Do not create a `pilot/` namespace for new work; retain old
  pilot artifacts under `archive/` when history must be preserved.
- Keep the three source roles distinct in code, schemas, fixtures, and prose.
- Resolve source availability automatically. Include every discovered file that
  exists and is hashable; mark missing files unavailable and exclude them from
  packets. Never ask the human to approve source inclusion.
- Preserve agent proposal and human decision as separate artifact types.
- Use one conversion-first comparison worker per protocol. It must finish the
  T1/T2/T3 candidate from legacy HTML, current JSON, and reviewed TSV before it
  opens primary sources. Do not run a separate source-only T1/T2/T3 extraction
  phase or create a frozen evidence artifact.
- If a complete comparison artifact fails deterministic audit/T1/T2/T3 schema
  or linked validation, use at most two evidence-isolated repair attempts.
  Give the repair worker only the artifact and exact validator error, preserve
  all attempt provenance, re-run full validation, and reject any change to
  source coverage, evidence, issue conclusions, or audited-field statuses.
- T3 conversion comes primarily from the ordered molecular workflow in the
  legacy HTML; primary sources verify and may propose corrections to that
  candidate but do not silently replace it.
- Keep approved ground-truth JSON minimal. Do not copy audit evidence,
  provenance lineage, review decisions, inclusion status, or audit notes into
  T1–T3. T1 has no `library_id` or duplicate `strands`; T2 has no
  `baseline_lineage`; T3 stores `modality` on each workflow, requires one
  workflow per modality, and uses graph topology instead of `workflow_branch`.
  Alternative routes for the same modality remain branches in that workflow.
- T1 has one canonical `library_sequence`, which retains biological insert
  placeholders such as `[CDNA]`; never duplicate it as
  `annotated_library_sequence` or store a benchmark scoring projection in
  approved ground truth. T2 components are ordered inline descriptions and do
  not carry `component_id`.
- Require one schema-valid, human-reviewed root conversion for every
  legacy-shaped current T1/T2/T3 record. Preserve legacy values in audit
  lineage, not as legacy-only fields in cleaned ground truth. Keep source
  deltas patch-free until they are compiled into that root conversion.
- Default human adjudication to one issue at a time in the Claude console,
  checkpointing each explicit decision in the working decision artifact.
- Use one interactive review pass per protocol by default. Handle questions and
  human-requested edits by revising and validating the same working candidate
  in the same Claude Code session. Start another iteration only when sources,
  manifest, baseline, schema, or an immutable finalized decision changed.
- Use `AskUserQuestion` for every human gate while it is available. Never end
  the controller response with only a printed disposition question.
- Before each issue selector, print the complete evidence card with current and
  proposed values, exact locators, classification, reason, impact, and policy
  notes. The selector must not replace this context with a short recap.
- Mark the card as required by the audit skill and let its scoped stop hook
  prevent any yield before `AskUserQuestion`; the hook never decides for the
  human.
- Do not generate HTML review files. Present material review-gate issues
  individually. Summarize low informational findings and require one explicit
  grouped human decision, recorded separately for every issue ID.
- After issue decisions, show the complete T1–T3 result and use one combined
  final scientific-approval question for root decisions and finalization.
  Application authorization remains separate.
- Before any final T3 approval, directly inspect the packet-listed primary
  PDFs, supplementary tables/spreadsheets, and relevant figures or renditions.
  Fact-check every T3 state and transition—substrate, operation,
  oligos/reagents, products, carried product, strand architecture, and sequence
  change—against exact primary locators. Show a concise
  verified/conflict/missing/ambiguous table in the console. Do not rely only on
  the worker's summary or proposal; any material gap remains a human-review
  blocker.
- Do not stop with a finalized-but-unpromoted review. After one protocol, or
  after the selected batch review queue is complete, show the exact unpromoted
  protocol IDs, append `<!-- audit-application-question-required -->`, and use
  `AskUserQuestion` for a separate apply-and-promote authorization. A yes writes
  validated T1–T3 to `protocols-test/ground_truth/<protocol_id>/`; a no leaves
  the immutable finalized decision unapplied.
- Keep T3 graphs compact. Do not create standalone states or transitions for
  cleanup, purification, size selection, QC, dilution, washing, or routine
  handling when sequence architecture and strand structure are unchanged. Fold
  them into the nearest major transition. Include only scientifically important
  details and products; a non-sequence node needs an essential branch or
  carried-product justification.
- Keep each T3 state structurally complete even when the graph is compact. Use
  the controlled `strand_architecture`, represent every physical strand 5′→3′,
  identify the T1 reference strand, preserve paired/unpaired regions and
  discontinuities, and never infer a missing complementary strand. Validate
  explicit paired sequences as reverse complements while allowing documented
  noncanonical or unknown pairing.
- Keep placeholder roles canonical and orientation-free. Use `[ROLE:LENGTH]`
  such as `[TN5_INDEX:8]`, never `[TN5_INDEX_RC:8]` or another directional
  suffix. Put strand-specific bases in the T1/T3 segment `sequence`, keep the
  source-visible sequence in its linked T2 oligo, and record
  `oligo_derivations[].orientation_to_source` separately.
- Match every terminal T3 state one-to-one to a distinct T1 library by its
  reference-strand architecture and protocol scope. Do not emit
  `final_library_links` or recreate a removed T1 library identifier.
- Never add a path that lets an audit proposal overwrite canonical ground
  truth without a validated human decision and baseline hash.
- Add or update tests for schema and workflow behavior.
