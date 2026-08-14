# Libgen linked T2/T3 benchmark

This Harbor benchmark asks:

> How much protocol-understanding performance comes from the underlying model,
> and how much comes from the harness controlling context, tools, and iteration?

Each of the 20 protocol tasks reconstructs two linked artifacts from the full
approved primary-source bundle:

- T2: oligo identity, source-visible sequence, role, aliases, orientation,
  components, and modifications;
- T3: the molecular state-transition graph for library generation, including
  explicit strand architecture and references to T2 oligos.

This is benchmark execution, not the ground-truth audit. Agents never see
legacy HTML, current labels, audit proposals, human decisions, or canonical
ground truth.

## Data boundary

Use two different Hugging Face dataset repositories:

1. an agent-visible repository containing only the manifest-listed primary
   protocol sources;
2. a private verifier repository containing the human-approved canonical T1,
   T2, and T3 JSON.

Both repositories are pinned by immutable commit hashes. The source image is
built before the trial, so no HF token enters the agent environment. The
private `HF_TOKEN` exists only in Harbor's separate verifier container.
Every downloaded ground-truth file is also checked against the local approved
file hash embedded when the task is generated.
During agent execution E2B allows only model-provider hosts; source browsing is
not available. No skills or MCP servers are attached.

`protocols.json` contains the 58 approved source paths and SHA-256 hashes for
the 20-protocol set. The task generator refuses missing or changed sources,
mutable revisions such as `main`, mixed source/ground-truth trees, an incomplete
T1/T2/T3 set, or a linked ground-truth validation failure.

## Scoring

Overall reward remains:

```text
reward = 0.30 * t2_required_sequence_f1
       + 0.70 * t3_molecular_transition_f1
```

- T2 uses sequence-only global optimal one-to-one soft F1. Names, roles,
  orientations, and modifications are diagnostics. `O_used` contains the T2
  oligos referenced by T3 transition `oligo_ids` or state-segment
  `oligo_derivations`; `O_score` restricts that set to sequence claims that are
  explicit or derivable from the agent-visible source bundle. Canonical but
  externally completed, ambiguous, or unsupported claims are neutral, as are
  exact predictions of optional T2 records. Unknown and duplicate extras reduce
  precision. Equivalent flat sequences and ordered components are compared as
  one molecule for `single`, `assembled`, and `hairpin` ground-truth oligos;
  `double_stranded` components remain separate strand claims. The primary
  metric is `t2_required_sequence_f1`.
- T3 contains one workflow per modality and is scored within matched
  modalities. Common aliases map to the canonical `gene expression`, `genomic
  DNA`, `feature barcode`, `sgRNA`, or `chromatin accessibility` vocabulary.
  Its primary metric, `t3_molecular_transition_f1`, globally
  matches transitions using operation, matched substrate/product states,
  carried/discarded classification, and transition-local T2 sequence
  multisets. Major reagent names are diagnostic. `t3_typed_edge_f1` directly
  compares substrate, carried-product, and discarded-product edges after
  semantic state and transition alignment. T3 sequence and architecture claims
  use the same explicit-or-derivable recoverability mask.
- Schema-invalid or semantically invalid linked predictions receive zero.
  Verifier infrastructure or private-ground-truth failures fail the trial
  instead of being mislabeled as model errors.

The standard Harbor metric surface is intentionally limited to:

- `reward`;
- `t2_required_sequence_f1`;
- `t2_all_required_exact`;
- `t3_molecular_transition_f1`;
- `t3_state_f1`;
- `t3_typed_edge_f1`.

The verifier writes those six values to `reward.json`. Precision/recall,
lexical metadata scores, boundary and reagent diagnostics, and entity counts
remain available under `details.json` at
`scoring.diagnostic_metrics.{t2,t3}`. Validation status and detailed matches
also remain in `details.json`; on failure, `error.json` records the verifier
error. Every trial additionally writes `error_analysis.json`, which turns the
match details into structured discrepancy records with entity IDs, scores,
affected metrics, unresolved validity/attribution fields, a run summary, and
conservative trajectory evidence when available. It does not change any score.
All files are written under `/logs/verifier/`. See
[error-analysis.md](error-analysis.md) for the discrepancy-adjudication and
trajectory-review procedure.

## Experiment design

The balanced core crosses all four models with three model-agnostic harnesses:

- models: GPT 5.6 Sol, Claude Opus 5.0, Gemini 3.6 Flash, Kimi K3;
- harnesses: Kimi Code, mini-SWE-agent v2, Pi.

This gives 12 cells from which model, harness, and interaction effects can be
estimated. Three native extensions are reported separately: GPT–Codex,
Claude–Claude Code, and Gemini–Gemini CLI. They are useful practical baselines
but are not included in the balanced main-effect estimate. The complete design
therefore contains 15 cells and all six requested harnesses.

The pilot uses `s3_atac`, `drop_seq`, `split_seq`, and
`10x_chromium_3_feature_barcoding`: 15 cells × 4 protocols × 1 attempt = 60
trials. The full run is 15 × 20 × 2 = 600 trials.

## Prepare the two HF uploads

First check the combined local staging inputs. The existing upload tree keeps
protocols under its `protocols/` subdirectory:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.check_libgen_staging \
  --source-root /Users/seqmachines/playground/protocols-test/scg-v1-upload/protocols \
  --groundtruth-root /Users/seqmachines/playground/protocols-test/ground_truth \
  --schema-root schemas
```

The complete approved `protocols/` tree and all 20 canonical T1/T2/T3 bundles
must pass this gate before export.

Once the check passes, create clean, separate upload trees (the command never
uploads anything). The complete approved source tree currently passes all 58
source hash checks, so it can be used directly without modifying the older
upload snapshot:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.prepare_libgen_hf_export \
  --source-root /Users/seqmachines/playground/protocols-test/protocols \
  --groundtruth-root /Users/seqmachines/playground/protocols-test/ground_truth \
  --schema-root schemas \
  --out /tmp/libgen-hf-export
```

Upload `/tmp/libgen-hf-export/protocol_sources` to the agent-visible repository
and `/tmp/libgen-hf-export/groundtruth` to the private verifier repository.
Record each resulting HF commit hash.

## Generate tasks

```bash
PYTHONPATH=src python -m libstruct_bench.cli.generate_libgen_tasks \
  --source-root /tmp/libgen-hf-export/protocol_sources \
  --groundtruth-root /tmp/libgen-hf-export/groundtruth \
  --input-repo ORG/PROTOCOL_SOURCES \
  --input-revision INPUT_COMMIT_SHA \
  --groundtruth-repo ORG/PRIVATE_GROUNDTRUTH \
  --groundtruth-revision GROUNDTRUTH_COMMIT_SHA \
  --out benchmarks/libgen/tasks
```

The default `allowlist` network profile is intended for providers such as E2B
that support phase-level network-policy switching. For a local Docker Desktop
run, generate Docker-compatible tasks instead:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.generate_libgen_tasks \
  --source-root /tmp/libgen-hf-export/protocol_sources \
  --groundtruth-root /tmp/libgen-hf-export/groundtruth \
  --input-repo ORG/PROTOCOL_SOURCES \
  --input-revision INPUT_COMMIT_SHA \
  --groundtruth-repo ORG/PRIVATE_GROUNDTRUTH \
  --groundtruth-revision GROUNDTRUTH_COMMIT_SHA \
  --network-profile local-docker \
  --out benchmarks/libgen/tasks
```

The local profile uses public network baselines because plain Docker cannot
enforce dynamic allowlists. It still uses a separate verifier container, and
the private `HF_TOKEN` remains confined to that verifier.

The generated environment includes Docling, PyMuPDF, pypdf, OpenPyXL, Pillow,
`antiword`, `file`, `unzip`, and `rg`. Source bytes are downloaded and
hash-checked during image construction.

## Lock and run the matrix

Set every model endpoint ID and harness CLI version explicitly. Core model IDs
should use the same provider/backend for a given model across Kimi Code,
mini-SWE-agent, and Pi. Native IDs may use their native provider endpoint.
The exact required variable names are in `matrix.json`; the planner refuses any
unset pin.

Use the highest stable reasoning setting supported by each adapter. The matrix
pins `high`, `xhigh`, or `max` where Harbor exposes that control. Kimi Code's
current Harbor adapter has no cross-provider reasoning flag, so those cells are
explicitly locked as `provider_default`; use endpoints whose configured default
is the intended high-reasoning mode and report that limitation rather than
silently claiming an equivalent effort setting.

After upgrading Harbor, generate the pilot plan with its exact installed
version:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.plan_libgen_matrix \
  --mode pilot \
  --tasks benchmarks/libgen/tasks \
  --harbor-version "$(harbor --version)" \
  --out runs/libgen/plans/pilot

bash runs/libgen/plans/pilot/run.sh
```

After the pilot, preserve and adjudicate its 60 trial records:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.prepare_libgen_error_review \
  --runs-root runs/libgen \
  --experiment-lock runs/libgen/plans/pilot/experiment_lock.json \
  --out analysis/libgen/pilot-error-review

PYTHONPATH=src python -m libstruct_bench.cli.validate_libgen_error_review \
  --review-root analysis/libgen/pilot-error-review \
  --tasks benchmarks/libgen/tasks \
  --record-refreeze \
  --recorded-by CURATOR_ID \
  --out analysis/libgen/pilot-review-status.json
```

Fix confirmed benchmark or evaluator defects before recording the refreeze.
Only after the pilot status is clear should the full plan be created:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.plan_libgen_matrix \
  --mode full \
  --tasks benchmarks/libgen/tasks \
  --harbor-version "$(harbor --version)" \
  --pilot-clearance analysis/libgen/pilot-review-status.json \
  --out runs/libgen/plans/full
```

The planner records model IDs, harness versions, reasoning settings, Harbor
version, protocols, attempts, the frozen task digest, and expected trial count
in `experiment_lock.json`.

## Summarize

```bash
PYTHONPATH=src python -m libstruct_bench.cli.summarize_libgen_runs \
  --runs-root runs/libgen \
  --experiment-lock runs/libgen/plans/full/experiment_lock.json \
  --out analysis/libgen/full
```

This produces tidy trial rows plus balanced-core model means, harness means,
and model×harness interaction residuals. Native extensions are summarized in a
separate section.
