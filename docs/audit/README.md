# Ground-truth audit

Claude Code audits sequencing-library ground truth; a human makes every final
scientific decision. T1 final libraries, T2 oligos, and T3 molecular workflows
are reviewed together but remain separate linked artifacts.

## Data layout

- `libstruct-bench/`: code, canonical schemas, prompts, policies, and tests.
- `protocols-test/protocols/`: primary protocol bundles.
- `protocols-test/scg_html/`: legacy `scg_lib_structs` curation.
- `protocols-test/groundtruth_oligos.tsv`: reviewed legacy oligo table.
- `protocols-test/ground_truth_audit/`: private packets, runs, reviews, and
  provenance.
- `protocols-test/ground_truth/`: final human-approved records only.

Never modify `scg-v1-upload` or upload audit data before review is complete.

Each approved protocol has:

- `groundtruth_final_lib_struct.json` (T1)
- `groundtruth_oligos.json` (T2)
- `groundtruth_library_generation_workflow.json` (T3)

Schemas use canonical unnumbered filenames. Reproducibility comes from Git
commits, file hashes, run IDs, decision IDs, and checkpoint IDs—not schema or
release labels.

## Workflow

### 1. Primary evidence

In an isolated packet, Claude reads every approved paper, protocol,
supplement, spreadsheet, table, figure, and diagram. It reconstructs T1–T3
without seeing legacy curation, current ground truth, earlier answers, or
external knowledge. T2 oligos and T3 state transitions are captured in one
chronological pass.

### 2. Comparison

After evidence is frozen, Claude compares it with legacy HTML, current T1/T2/T3
records, the protocol projection of `groundtruth_oligos.tsv`, and optional
benchmark-run artifacts. Every field receives one status:

- `verified_no_change`
- `proposed_correction`
- `missing_source_evidence`
- `ambiguous`
- `external_knowledge_required`

Only the last four become review issues.

When no T3 JSON exists but the legacy HTML contains a curated workflow, the
comparison first converts that HTML into the T3 candidate with legacy
provenance. Primary-source corrections and additions remain separate review
issues rather than being silently incorporated into the candidate.

### 3. Human adjudication

The console walks through only review-required findings one issue at a time,
with current and proposed values, evidence location, explanation, severity,
and impact. Each explicit human decision is checkpointed immediately so review
can stop and resume. Low, non-changing findings are summarized by task and
count, then receive one explicit grouped human decision that is stored once per
issue ID. Nothing is accepted automatically. Review HTML is not generated.

### 4. Deterministic application

Python applies only accepted or modified patches to the pinned records,
validates linked T1–T3, and generates correction regressions. Promotion writes
new approved files under `ground_truth/<protocol_id>/` and refuses to overwrite
an existing approved directory.

## T3 graph

T3 contains states and transitions. Edges are derived from each transition’s
substrate and product IDs; no separate edge list is maintained. Validation
checks T2 oligo references, T1 final-library links, carried-product continuity,
reachability, cycles, protocol scopes, and terminal-state/T1 consistency.

## Running from Claude Code

Use `/audit-protocol s3_atac` from the repository root. Pass up to ten protocol
IDs to prepare proposals concurrently. Workers stop at proposals; human review
and deterministic application remain separate.

See `evidence-policy.md`, `adjudication-policy.md`, and
`benchmark-standardization-policy.md` for normative rules.
