# LibGen cumulative capability-improvement experiment

This repository implements one cumulative capability lineage:

```text
C0 -- B1 --> C5 -- B2 --> C10 -- B3 --> C15 -- B4 --> C20 -- B5 --> C25
```

The separate offline human-guided condition reuses the same checkpoint and
application machinery while displaying those checkpoints as H0--H25. Its
operator workflow is documented in
[`human-guided-loop.md`](human-guided-loop.md).

The protocol-by-protocol agent condition uses the same scientific review
format with an independent Codex critic, pact-only mutations, a frozen
verifier fingerprint, and cumulative validation checkpoint sweeps. See
[`agent-guided-loop.md`](agent-guided-loop.md).

`C0` is a real frozen checkpoint containing the byte-identical original S0
pack. Independent Codex review and interactive human review are two reviewer
modes for the same proposal and checkpoint lineage. They do not create
separate A/H branches.

Proposals and reviewer outputs are audit artifacts, not authority. Accepted
bytes are applied only by the deterministic, hash-guarded application stage.
Every checkpoint contains two cumulative capabilities: the procedural `pack/`
and the approved-training exemplar `memory/`, plus a `runtime.json` loading
contract and `checkpoint.json` lineage record.

The procedural pack contains instructions, checklists, protocol-neutral
schemas and tools, and synthetic regression fixtures. The exemplar memory is
a separately projected, prediction-shaped representation of approved training
ground truth.

Raw GT and audit records are not exposed. Approved training GT is projected into prediction-shaped exemplars and retained as cumulative memory.

The two update paths are deliberately separate. A reviewer may accept at most
one procedural/tool change per batch. The deterministic projector adds the
five newly approved training exemplars independently of that procedural
decision; it is not a proposal change unit.

## Frozen 25/5/10 design

The 25 training protocols are divided into five ordered batches.

| Batch | Phase | Protocols | Output checkpoint |
| --- | --- | --- | --- |
| B1 | retrospective | `s3_atac`, `10x_chromium_3_gene_expression_v4`, `drop_seq`, `split_seq`, `sci_rna_seq` | C5 |
| B2 | retrospective | `10x_chromium_3_feature_barcoding`, `10x_chromium_single_cell_atac_v2`, `seq_well_s3`, `indrop_v1`, `cel_seq` | C10 |
| B3 | retrospective | `microwell_seq`, `pip_seq_v4`, `scdamid`, `dr_seq`, `petri_seq` | C15 |
| B4 | prospective | `crispr_sciatac`, `lianti`, `strt_seq`, `smart_seq2`, `plate_scatac_seq` | C20 |
| B5 | prospective | `malbac`, `scifi_atac_seq`, `strt_seq_2i`, `strt_seq_c1`, `tang_2009` | C25 |

The fixed validation panel is:

- `sci_atac_seq`
- `scrrbs`
- `smart_seq`
- `share_seq`
- `ddseq_single_cell_3_rna_seq_kit`

The same five validation protocols are evaluated at C0, C5, C10, C15, C20,
and C25. Only a sanitized macro/count aggregate may guide the next update.
Each five-protocol Harbor job schedules up to four independent trials in
parallel while retaining one Codex agent per trial and zero semantic retries.
Validation sources, ground truth, solved T2/T3 records, exact sequences,
per-protocol rows, verifier errors, and error-specific answers are forbidden
from packets, prompts, proposals, candidate files, synthetic fixtures,
applications, frozen packs, and exemplar memory. No validation result is
projected into either cumulative capability.

The validation mapping is fixed: C0 guides B1, C5 guides B2, C10 guides B3,
C15 guides B4, and C20 guides B5. C25 validation is a final-lock requirement
and cannot trigger another update. Six evaluations of five protocols produce
exactly 30 validation trials.

The frozen final-test panel is:

- `cel_seq2`
- `indrop_v2`
- `smart_seq3xpress`
- `ddseq_scatac_seq`
- `snare_seq`
- `scrb_seq`
- `paired_seq`
- `pi_atac_seq`
- `scdnase_seq`
- `spear_atac`

It stays inaccessible until C25, all six validation aggregates, and the final
development lock are frozen. Unsealing permanently closes capability mutation.
The one final replay evaluates C0, C5, C10, C15, C20, and C25 on all ten test
protocols: 60 trials. The validation curve contains 30 trials. Rolling B4/B5
diagnostics remain development diagnostics, not the final learning curve.

## Clean single-version migration

The former A/H experiment is not reinterpreted as the new lineage. Its A10
checkpoint learned from protocols that are now validation-only, so the entire
old active design, checkpoints, packs, and rounds are copied into immutable
superseded history. Existing history is preserved byte for byte. The active
experiment restarts from the predecessor's original S0 bytes as C0.

The first command is a full disposable reconstruction and makes no active
change:

```bash
libstruct-libgen-capability-improvement migrate-single-branch \
  --experiment-root "$CAP_ROOT" \
  --sources-root "$SOURCE_ROOT" \
  --groundtruth-root "$GROUNDTRUTH_ROOT" \
  --recorded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --agent-version 0.147.0
```

After reviewing that output, authorize the journaled atomic swap:

```bash
libstruct-libgen-capability-improvement migrate-single-branch \
  --experiment-root "$CAP_ROOT" \
  --sources-root "$SOURCE_ROOT" \
  --groundtruth-root "$GROUNDTRUTH_ROOT" \
  --recorded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --agent-version 0.147.0 \
  --authorize-migration
```

The transaction validates the immediate predecessor, binds the supplied
private roots to previously committed bytes, inventories the old active trees
and pre-existing history, stages and validates the complete replacement,
atomically swaps it under the shared experiment lock, and supports idempotent
recovery. It never runs Harbor.

An experiment that was already migrated to a clean C0 before exemplar memory
was introduced uses the narrower one-time adoption transaction. Its default
form is a disposable preflight; add `--authorize-adoption` only after review:

```bash
libstruct-adopt-exemplar-memory \
  --experiment-root "$CAP_ROOT" \
  --recorded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

This preserves the previous manifest and C0 checkpoint/runtime under immutable
superseded history, keeps the C0 procedural pack byte-for-byte unchanged, and
adds an empty, hash-bound `memory/` plus the private pseudonym map. It refuses
to run after a training round, validation run, or final-test unseal has begun.

## Data boundaries

Training evidence may enter only the batch that owns it. Once that batch is
terminal and revealed, its approved sources and ground truth may support the
deterministic learning ledger and the review of protocol-neutral controls.
The same approved training GT is deterministically projected into the
checkpoint's prediction-shaped exemplar memory. The raw source, GT, audit, and
verifier artifacts remain outside the checkpoint; the portable projections
and accepted procedural controls persist cumulatively.

The exact memory growth is fixed:

| Checkpoint | Newly projected batch | Cumulative exemplars |
| --- | --- | ---: |
| C0 | none | 0 |
| C5 | B1 | 5 |
| C10 | B2 | 10 |
| C15 | B3 | 15 |
| C20 | B4 | 20 |
| C25 | B5 | 25 |

Checkpoint freeze therefore requires both the next procedural pack and the
complete cumulative exemplar projection for that point in the lineage.

Validation evaluation is orchestrator-only. A worker may receive one
hash-pinned sanitized aggregate for the immediately preceding checkpoint, but
that aggregate is guidance, never scientific evidence. Worker Codex processes
run inside an external read boundary containing a read-only staged root, its
two writable `candidates/` and `outputs/` subdirectories, and one read-only
bind of the local `auth.json`; the experiment, source, ground-truth, run,
history, and home trees are not mounted. The process runs as the invoking
UID/GID with a read-only image root. Network access is limited to the model
provider.

Final-test sources, ground truth, runs, and scores remain blocked until the
final lock and explicit transfer authorization. Final replay performance is
descriptive and cannot select an earlier checkpoint or trigger another update.

## Deterministic error admission

Each five-protocol batch is reduced before the proposal worker runs:

1. infrastructure failures and evaluator, policy/source-scope, and
   ground-truth defects are counted and excluded from learning;
2. metric-level manifestations with the same task, error category, entity
   type, and process cause collapse into one root event;
3. a root event is recurring only when it occurs in at least two distinct
   training protocols; and
4. the ledger retains aggregate exclusion counts and source-located admitted
   evidence, not excluded exemplars.

A proposal may contain at most two atomic `procedural_or_tool` units. Review
may accept at most one. An accepted unit must either cite a recurring root
event or encode a protocol-neutral general invariant together with a newly
added or changed negative synthetic regression fixture. A singleton without
that invariant/regression gate is marked insufficient and cannot be accepted.
These limits apply identically to independent and interactive-human review.
They do not limit deterministic projection of the batch's five approved
training exemplars.

## Normal batch loop

Before B1, record the five-protocol C0 validation result as the canonical
aggregate. Before every later batch, do the same for its parent checkpoint.
The validation runner and recorder verify the exact checkpoint, runtime, pack,
integration, task bundle, config, result bundle, and unique trial identities.

Build the proposal for a training batch from its frozen five-protocol run:

```bash
CODEX_FORCE_AUTH_JSON=1 caffeinate -i libstruct-learn-capability \
  --experiment-root "$CAP_ROOT" \
  --batch B1 \
  --sources-root "$SOURCE_ROOT" \
  --groundtruth-root "$GROUNDTRUTH_ROOT" \
  --run-root "$B1_RUN_ROOT"
```

For B4 and B5, also supply the corresponding frozen C0 diagnostic run with
`--c0-run-root`. The learning command stages only allowlisted inputs and
resumes completed hash-valid stages.

Its proposal worker sees the deterministic root-error ledger, not an
unfiltered metric mismatch list. The worker may propose no more than two
procedural/tool controls. It cannot author or edit exemplar memory: the
orchestrator projects that memory separately from approved training GT.

Complete the same cumulative proposal with an independent review:

```bash
CODEX_FORCE_AUTH_JSON=1 caffeinate -i libstruct-complete-capability \
  --experiment-root "$CAP_ROOT" \
  --batch B1 \
  --review-mode independent \
  --groundtruth-root "$GROUNDTRUTH_ROOT" \
  --authorize-apply
```

Or use the interactive human reviewer:

```bash
libstruct-complete-capability \
  --experiment-root "$CAP_ROOT" \
  --batch B1 \
  --review-mode human \
  --reviewer-id seqmachines \
  --groundtruth-root "$GROUNDTRUTH_ROOT"
```

The human console offers accept, reject, modify, unresolved, back, skip,
status, and quit. Every answer is written atomically and can be resumed. A
modify decision permits one bounded revision followed by a fresh exact-byte
review. The console cannot accept a second unit in the same batch or accept a
unit that lacks either recurring-root support or the general-invariant plus
synthetic-regression gate. Application remains a separate `--authorize-apply`
action.

## Checkpoint portability

A checkpoint directory is the runnable capability:

```text
checkpoints/C15/
├── checkpoint.json
├── runtime.json
├── pack/
└── memory/
    ├── manifest.json
    ├── catalog.json
    ├── exemplars/<exemplar_id>/
    │   ├── mechanism_summary.json
    │   ├── t2_example.json
    │   └── t3_example.json
    └── runtime/
        ├── tools/
        └── schemas/
```

Any agent framework can verify `checkpoint.json`, load the hash-pinned
`runtime.json`, mount both `pack/` and `memory/` read-only, follow
`content.required_read_order`, and use the declared compiler, control index,
schemas, unified audit, and output contract. `memory/manifest.json` pins the
whole memory tree and `memory/catalog.json` is the retrieval catalog. The
portable `query_exemplars` interface retrieves at most three pseudonymous
examples from source-linked controlled features; `guard_target_evidence`
audits that target-evidence use remains authoritative. An adapter is optional;
it is not the learned capability itself.

## Final lock and replay

After all six checkpoints and validation aggregates exist, `lock-final`
records the complete C lineage. `authorize-transfer-panel` is a separate
explicit action. `plan-final-replay` rejects missing or stale checkpoints,
validation records, policy commitments, task bundles, and authorization. Its
creation is the irreversible unseal event.

The generated replay has six matched checkpoint conditions and ten protocols,
for exactly 60 ordinary Harbor trials. `report-final` reports protocol-level
scores, macro means, changes paired to C0, and deterministic paired-bootstrap
intervals for T3 transition, state, and typed-edge F1. No Harbor command is
part of migration, checkpoint learning, or this repository update.

See [agent-loop.md](agent-loop.md) for the operational state machine and exact
artifact gates.
