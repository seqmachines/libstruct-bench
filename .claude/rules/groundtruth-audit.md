---
paths:
  - "docs/audit/**"
  - "schemas/audit/**"
  - "src/libstruct_bench/audit/**"
  - "tests/audit/**"
---

# Ground-truth audit changes

- Read the canonical policies under `docs/audit/` before editing these paths.
- Keep the three source roles distinct in code, schemas, fixtures, and prose.
- Resolve source availability automatically. Include every discovered file that
  exists and is hashable; mark missing files unavailable and exclude them from
  packets. Never ask the human to approve source inclusion.
- Preserve agent proposal and human decision as separate artifact types.
- Keep approved ground-truth JSON minimal. Do not copy audit evidence,
  provenance lineage, review decisions, inclusion status, or audit notes into
  T1–T3. T1 has no `library_id` or duplicate `strands`; T2 has no
  `baseline_lineage`; T3 stores `modality` once at its document root and uses
  graph topology instead of `workflow_branch`.
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
