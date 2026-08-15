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

Benchmark version: `3.0.0`. This is a major T3 representation and scoring
revision: one workflow now represents one connected molecular process, with
modality-labelled terminal outputs. Shared ancestors are represented and
scored once. Results from the workflow-per-modality 2.x benchmark are not
directly comparable; rerun baselines under 3.0.0.

Overall reward is:

```text
reward = 0.30 * t2_required_family_f1
       + 0.70 * t3_molecular_transition_f1
```

- T2 first canonicalizes flat sequences and ordered components to the same
  molecule-level representation. A ground-truth record containing a
  fixed-length placeholder is one family-level requirement. Concrete
  ground-truth records remain individual member-level requirements. Valid
  concrete prediction members with the same scaffold, role, orientation, and
  modification profile collapse to one predicted family before precision and
  recall are computed. Extra members of a recovered family are therefore
  neutral; missing required families and unrelated extra families still reduce
  `t2_required_family_f1`. Wildcards match fixed-length concrete variable
  regions but do not erase scaffold differences. The secondary
  `t2_exact_required_family_recall` is the fraction of required families with
  an exact molecule-template match and is intentionally unaffected by unrelated
  extras.
- `O_used` contains the T2 families referenced by T3 transition `oligo_ids` or
  state-segment `oligo_derivations`; `O_score` restricts that set to sequence
  claims that are explicit or derivable from the agent-visible source bundle.
  Canonical but externally completed, ambiguous, or unsupported claims are
  neutral, as are exact predictions of optional T2 families.
- T3 contains one weakly connected DAG per molecular process. Each terminal is
  listed in `final_outputs` with its canonical modality; shared upstream states
  and transitions occur once before modality-specific branches. Workflows are
  globally assigned by terminal-modality, state, and transition similarity and
  each connected DAG is scored once without modality projections. The primary
  `t3_molecular_transition_f1` globally matches transitions using operation,
  matched substrate/product states, carried/discarded classification, and
  transition-local T2 sequence family-level multisets. Concrete T2 members
  referenced by an older prediction are collapsed consistently before this
  transition-local comparison. Major reagent names are diagnostic.
  `t3_typed_edge_f1` directly compares substrate, carried-product, and
  discarded-product edges after semantic state and transition alignment. T3
  sequence and architecture claims use the same explicit-or-derivable
  recoverability mask.
- Schema-invalid or semantically invalid linked predictions receive zero.
  Verifier infrastructure or private-ground-truth failures fail the trial
  instead of being mislabeled as model errors.

The standard Harbor metric surface is intentionally limited to:

- `reward`;
- `t2_required_family_f1`;
- `t2_exact_required_family_recall`;
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

To rescore preserved Harbor predictions after a benchmark-version change, use
the versioned sidecar command:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.rescore_libgen_runs \
  --runs-root runs/libgen \
  --groundtruth-root /path/to/private/groundtruth \
  --schema-root schemas
```

For each trial this writes
`verifier/rescore/libgen-3.0.0/{reward,details,error_analysis}.json` and writes a
run-level `rescore/libgen-3.0.0/summary.json`. Original Harbor results are kept
unchanged so incompatible benchmark versions cannot be confused. For the 3.0.0
release, rerunning baselines is preferred because the agent-facing T3 contract
also changed.

## Experiment design

The balanced core crosses all four models with three model-agnostic harnesses:

- models: GPT 5.6 Sol, Claude Opus 5.0, Gemini 3.7 Flash, Kimi K3;
- harnesses: Kimi Code, mini-SWE-agent v2, Pi.

This gives 12 cells from which model, harness, and interaction effects can be
estimated. Five native pairings are reported descriptively: GPT–Codex,
Claude–Claude Code, Gemini–Gemini CLI, Kimi K3–Kimi Code, and Qwen 3.8
Max–Qwen Code. The Kimi pairing is already one of the 12 balanced-core cells,
so it is tagged for both analyses but executed only once. Qwen is native-only.
The complete design therefore contains 16 unique execution cells and seven
harnesses. Native pairings are not included in the balanced main-effect
estimate except through the Kimi cell's ordinary membership in the 4 × 3 core.

The pilot uses `s3_atac`, `drop_seq`, `split_seq`, and
`10x_chromium_3_feature_barcoding`: 16 cells × 4 protocols × 1 attempt = 64
trials. The full run is 16 × 20 × 2 = 640 trials.

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
hash-checked during image construction. Generated Libgen tasks give the agent
one hour (`[agent].timeout_sec = 3600.0`) and the verifier ten minutes.

Each generated task vendors snapshots of the scorer, verifier CLI, analysis
schema, and supporting package. Regenerate all checked-in tasks after changing
that shared implementation; the generator regression suite rejects stale task
copies so verifier behavior cannot silently diverge between protocols.

## Lock and run the matrix

Set every model endpoint ID and harness CLI version explicitly. Core model IDs
should use the same provider/backend for a given model across Kimi Code,
mini-SWE-agent, and Pi. Native IDs may use their native provider endpoint.
The exact required variable names are in `matrix.json`; the planner refuses any
unset pin.

For the Qwen native-only cell, set
`LIBGEN_NATIVE_MODEL_QWEN_3_8_MAX=qwen3.8-max` (or a provider-prefixed form if
your Harbor credential routing requires one) and
`LIBGEN_QWEN_CODE_VERSION=0.21.12`. The planner enforces that Qwen Code stable
release so a later `latest` tag cannot change the experiment silently. Harbor's
agent name is `qwen-coder`; it uses `OPENAI_API_KEY` and `OPENAI_BASE_URL` for
Alibaba's OpenAI-compatible endpoint.

Use the highest stable reasoning setting supported by each adapter. The matrix
pins `high`, `xhigh`, or `max` where Harbor exposes that control. Kimi Code's
current Harbor adapter has no cross-provider reasoning flag, so those cells are
explicitly locked as `provider_default`; use endpoints whose configured default
is the intended high-reasoning mode and report that limitation rather than
silently claiming an equivalent effort setting.

After upgrading Harbor, generate the pilot plan with its exact installed
version:

First run the single-protocol telemetry smoke test. It covers all 12 balanced
model × harness cells and four additional native-only cells (16 unique trials
total). The Kimi native pairing reuses its balanced-core execution:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.plan_libgen_matrix \
  --mode smoke \
  --tasks benchmarks/libgen/tasks \
  --harbor-version "$(harbor --version)" \
  --out runs/libgen/plans/smoke

bash runs/libgen/plans/smoke/run.sh

PYTHONPATH=src python -m libstruct_bench.cli.summarize_libgen_runs \
  --runs-root runs/libgen \
  --experiment-lock runs/libgen/plans/smoke/experiment_lock.json \
  --out analysis/libgen/smoke
```

Inspect `analysis/libgen/smoke/telemetry_audit.json` before starting the pilot.
The planner explicitly disables Harbor's automatic retry because Harbor can
replace the failed trial directory. Resume failures through the preserving
wrapper instead:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.resume_libgen_job \
  -p runs/libgen/libgen-smoke-MODEL-HARNESS \
  -f AgentTimeoutError
```

The wrapper snapshots the prior result, trajectory, verifier output, and agent
logs under `.libgen_telemetry/resume_snapshots/` before invoking
`harbor job resume`. Superseded executions remain rows in `trials.csv`; filter
`is_current_execution == true` for primary performance analyses.

Then generate the pilot plan:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.plan_libgen_matrix \
  --mode pilot \
  --tasks benchmarks/libgen/tasks \
  --harbor-version "$(harbor --version)" \
  --out runs/libgen/plans/pilot

bash runs/libgen/plans/pilot/run.sh
```

After the pilot, preserve and adjudicate its 64 trial records:

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
in `experiment_lock.json`. It also embeds the immutable pricing snapshot from
`pricing-2026-08-15.json` and its SHA-256 digest.

The snapshot includes the official Qwen model and pricing-source metadata, but
leaves Qwen 3.8 Max API-equivalent cost unavailable until a first-party frozen
per-token USD rate exists. It does not substitute an older Qwen price or infer
a variable Token Plan credit-to-USD conversion.

## Summarize

```bash
PYTHONPATH=src python -m libstruct_bench.cli.summarize_libgen_runs \
  --runs-root runs/libgen \
  --experiment-lock runs/libgen/plans/full/experiment_lock.json \
  --out analysis/libgen/full
```

This produces:

- `trials.csv`: one row per preserved execution, including the current one;
- `summary.json`: balanced-core effects, valid completion rate, and plot
  readiness;
- `telemetry_audit.json`: missing fields by trial and model × harness cell;
- `telemetry_missing.csv`: the incomplete rows in a compact review table.

`trials.csv` separates `prediction_valid`, `verifier_completed`, and
`exception_type`. An agent exception therefore does not turn an otherwise
valid, verified prediction into an invalid prediction. It also keeps total,
agent, and verifier duration; standard and provider-specific token fields;
retry/resume lineage; and usage/cost from timed-out executions.

Use `normalized_api_cost_usd` versus `t3_molecular_transition_f1` for the
API-equivalent cost/performance plot. Use `agent_duration_seconds` versus
`valid_completion` (aggregated to a rate by cell) for the runtime/completion
plot. `reported_cost_usd` is the amount surfaced by the Harbor adapter, while
`reported_cost_kind` says whether that amount came from the provider CLI or was
estimated. `normalized_api_cost_usd` is always an estimate from the frozen
first-party standard API rates. Its precision field distinguishes per-call
normalization from aggregate-token fallbacks. Subscription fees, routing
markups, tool-call charges, explicit-cache storage, taxes, and negotiated or
nonstandard service tiers are outside that normalization.

Balanced-core model means, harness means, and model×harness interaction
residuals use scored current executions only. Native extensions are summarized
in a separate descriptive section.
