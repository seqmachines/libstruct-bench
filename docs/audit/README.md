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

Protocol version/variant scope is optional at the document level and inherited
by child records. Add a child scope only when it narrows the parent. T2 uses one
`name` plus `aliases`; the original reviewed TSV row remains in audit lineage,
not in cleaned T2. T3 stores modality once at the document root. Every T3 state
records an explicit strand architecture, physical strands in their own 5′→3′
orientations, and the strand that corresponds to T1.

Approved T1–T3 JSON is intentionally minimal. Evidence, lineage, review
decisions, inclusion status, and audit notes remain in the hash-pinned audit
artifacts instead of being duplicated in ground truth.

Schemas use canonical unnumbered filenames. Reproducibility comes from Git
commits, file hashes, run IDs, decision IDs, and checkpoint IDs—not schema or
release labels.

## Workflow

### 1. Convert existing human curation

Current T1/T2 JSON, legacy HTML, and the reviewed TSV projection are converted
into canonical candidates without changing their scientific claims. Legacy
source text and locations remain in audit lineage, while legacy-only HTML,
extraction, normalization, and schema-label fields do not enter cleaned ground
truth. Each legacy-shaped record receives one schema-valid root conversion
proposal and remains unapproved.
An older finalized decision that lacks a required conversion remains immutable
audit history but cannot be applied. Reuse its frozen evidence in a fresh
comparison and review iteration.

### 2. Primary evidence and comparison

Source selection has no human gate. The catalog automatically includes every
discovered file that exists and records missing files as unavailable; unavailable
entries remain provenance but do not enter phase packets. In an isolated packet,
Claude reads every included paper, protocol, supplement, spreadsheet, table,
figure, and diagram. It reconstructs T1–T3 without seeing legacy curation,
current ground truth, earlier answers, or external knowledge. T2 oligos and T3
state transitions are captured in one chronological pass.

After evidence is frozen, Claude compares it with legacy HTML, current T1/T2/T3
records, the protocol projection of `groundtruth_oligos.tsv`, and optional
benchmark-run artifacts. Every field receives one status:

- `verified_no_change`
- `proposed_correction`
- `missing_source_evidence`
- `ambiguous`
- `external_knowledge_required`

Only the last four become review issues.

A completed Claude worker run that fails schema or semantic validation is
retained beside the requested output as `<run>.rejected/`. It contains the
transcript, stderr, the rejected structured artifact when one was recoverable,
and a hash-pinned failure record. It is diagnostic history, not an accepted
proposal; retry with a new run ID after fixing the cause.

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
One interactive review pass per protocol is the default. If the reviewer
wants a different representation or correction, Claude discusses it, updates
the working decision and candidate, and revalidates within that same session;
it does not start another review iteration merely because the human requested
an edit.
For a new T1, T2, or T3 document, source-delta findings are decided first and
then folded into one schema-valid root candidate for final human approval.
Claude Code presents every human gate through its interactive
`AskUserQuestion` control rather than ending with a printed disposition prompt.
For individual issues, it prints the complete evidence card immediately before
opening the selector; the selector never replaces the context. A skill-scoped
stop hook prevents the controller from accidentally yielding between those two
actions. After all issue decisions, Claude shows the complete T1–T3 result and
uses one final scientific-approval question for any root candidate decisions
and review finalization. Deterministic application authorization remains a
separate action.

### 4. Deterministic application

Python applies only accepted or modified patches to the pinned records,
validates linked T1–T3, and generates correction regressions. Promotion writes
new approved files under `ground_truth/<protocol_id>/` and refuses to overwrite
an existing approved directory.

## T3 graph

T3 contains states and transitions. Edges are derived from each transition’s
substrate and product IDs; no separate edge list is maintained. Validation
checks T2 oligo references, carried-product continuity, reachability, cycles,
protocol scopes, and terminal-state/T1 consistency. T3 does not store a
separate final-library link: each terminal state is matched one-to-one to a T1
library by its reference-strand structure and protocol scope.
Each state uses a controlled `strand_architecture` and contains explicit strand
records. A single-stranded product has one strand; a duplex has both strands,
each written 5′→3′. Paired-region links and strand discontinuities preserve
overhangs, unpaired regions, nicks, RNA–DNA hybrids, Y-shaped adapters, and
other partial duplexes. Explicit paired sequences are validated as reverse
complements, while documented noncanonical or unknown pairing remains allowed.
Oligo-derived segments retain their T2 source and record
`orientation_to_source` as `same_orientation`, `reverse_complement`, or
`unknown`. Placeholder roles remain biological and orientation-free:
`[TN5_INDEX:8]` is valid, while `[TN5_INDEX_RC:8]` is not. The segment sequence
contains the bases on its modeled strand; the linked T2 sequence remains the
source-visible oligo.

For terminal-state matching, the preferred consistency key is the identified
reference strand's `sequence_architecture`, which exactly equals a T1 annotated
or plain library sequence. That strand may keep a simpler segment
decomposition than T1; exact ordered segment identity is used only when the
terminal architecture string is absent.
Use the smallest scientifically sufficient graph. New states and transitions
normally represent changes in sequence architecture or strand structure.
Cleanup, purification, size selection, QC, dilution, washes, inactivation, and
other secondary details stay within the nearest substantive transition unless
a separate branch or carried molecular product is essential downstream.

## Running from Claude Code

Use `/audit-protocol s3_atac` from the repository root. Pass up to ten protocol
IDs to prepare proposals concurrently. Workers stop at proposals; human review
and deterministic application remain separate.

See `evidence-policy.md`, `adjudication-policy.md`, and
`benchmark-standardization-policy.md` for normative rules.
