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
built before the trial, so no HF token enters the agent environment. When
supplied, the private `HF_TOKEN` exists only in Harbor's separate verifier
container; without it, the verifier uses its generation-time hash-checked
local fallback.
Every downloaded ground-truth file is also checked against the local approved
file hash embedded when the task is generated.
During agent execution private Docker places `main` on an internal-only network.
An exact-host CONNECT proxy permits the pinned harness's setup downloads before
the model starts; the first model-provider connection irreversibly switches the
proxy to provider-only mode. General web access and direct external connections
then fail. Generated Debian images rewrite APT repositories to HTTPS so Harbor's
agent-install phase also traverses this proxy. Setup hosts include both
`astral.sh` and its exact UV-installer redirect target, `releases.astral.sh`,
plus the Antigravity CLI bootstrap and release-manifest hosts. The Antigravity
release payload remains restricted to the existing `storage.googleapis.com`
setup host.
The frozen provider list includes Antigravity's authenticated Code Assist
endpoint, `daily-cloudcode-pa.googleapis.com`, its runtime eligibility and
feature-configuration hosts, `play.googleapis.com` and
`antigravity-unleash.goog`, and the exact Google profile image host required by
its eligibility check, `lh3.googleusercontent.com`; it also includes Alibaba
Qwen Code's international Coding Plan endpoint,
`coding-intl.dashscope.aliyuncs.com`. No skills or MCP servers are attached.

The proxy keeps request parsing and upstream connection setup bounded at 30
seconds, but permits 900 seconds of inactivity inside an established CONNECT
tunnel so a long model prefill cannot be mistaken for a dead connection. Each
Docker-profile trial collects `provider_egress.jsonl` from the proxy sidecar.
Its structured events record allowed or denied targets, tunnel close reasons,
lifetimes, and byte counts; they never contain tunneled request or response
payloads.

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

Harbor persists the separate verifier directory automatically. Do not add
`/logs/verifier/*` as job-level `--artifact` sources: job artifacts are
collected from the agent service, where those verifier-only paths do not exist,
and only produce misleading best-effort copy errors.

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

The default `docker-provider-only` profile is the production profile. It vendors
the internal-network proxy and smoke probe into each generated task. Harbor's
native allowlist remains available only as an alternative on compatible Linux
Docker hosts:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.generate_libgen_tasks \
  --source-root /tmp/libgen-hf-export/protocol_sources \
  --groundtruth-root /tmp/libgen-hf-export/groundtruth \
  --input-repo ORG/PROTOCOL_SOURCES \
  --input-revision INPUT_COMMIT_SHA \
  --groundtruth-repo ORG/PRIVATE_GROUNDTRUTH \
  --groundtruth-revision GROUNDTRUTH_COMMIT_SHA \
  --network-profile harbor-allowlist \
  --out benchmarks/libgen/tasks
```

Do not give the agent general public-network access for benchmark runs. The
verifier remains a separate container, and the private `HF_TOKEN` remains
confined to it. At scoring time the verifier reads the pinned Hugging Face
ground truth first. Generated verifier images also contain the same
generation-time hash-checked bundle as a private fallback, so transient DNS or
provider availability cannot discard a completed prediction. Both paths are
verified against the frozen per-file hashes before scoring.

The generated environment includes Docling, PyMuPDF, pypdf, OpenPyXL, Pillow,
`antiword`, `file`, `unzip`, and `rg`. Source bytes are downloaded and
hash-checked during image construction. Generated Libgen tasks give the agent
one hour (`[agent].timeout_sec = 3600.0`) and the verifier ten minutes.

The agent image contains only the prediction schemas and generic local
prediction-validation runtime. It does not contain audit modules, ground-truth
schemas, scoring, error analysis, or protocol-specific migration code. The
separate verifier image vendors the full scorer, verifier package, and private
ground-truth fallback. Regenerate all checked-in tasks after changing shared
verifier code; regression tests reject stale copies and agent-image leakage.

## Lock and run the matrix

Set every model endpoint ID and harness CLI version explicitly. Core model IDs
should use the same provider/backend for a given model across Kimi Code,
mini-SWE-agent, and Pi. Native IDs may use their native provider endpoint.
The exact required variable names are in `matrix.json`; the planner refuses any
unset pin.

The Codex, Claude Code, and Gemini CLI native cells use the host's existing
subscription login, not API-key authentication. Harbor copies only the required
credential into the ephemeral agent container and removes its temporary copy
after the run. The generated plan contains selectors and environment-variable
templates, never credential contents:

```bash
# Codex: creates ~/.codex/auth.json
codex login

# Claude Code: create a subscription OAuth token, then export the value it prints
claude setup-token
export CLAUDE_CODE_OAUTH_TOKEN='...'

# Gemini CLI: start it once and choose "Login with Google"; this creates
# ~/.gemini/oauth_creds.json
gemini
```

The planner forces `CODEX_FORCE_AUTH_JSON=1`, `CLAUDE_FORCE_OAUTH=1`, and
`GEMINI_FORCE_OAUTH=1` for those three cells, so adding API keys later for the
balanced core cannot silently change native-cell authentication. `run.sh`
checks the three local subscription credentials before starting any trials.
Qwen Code remains an OpenAI-compatible provider-key cell.

For the Qwen native-only cell, set
`LIBGEN_NATIVE_MODEL_QWEN_3_8_MAX=qwen3.8-max` (or a provider-prefixed form if
your Harbor credential routing requires one) and
`LIBGEN_QWEN_CODE_VERSION=0.21.12`. The planner enforces that Qwen Code stable
release so a later `latest` tag cannot change the experiment silently. Harbor's
agent name is `qwen-coder`; also set
`LIBGEN_QWEN_OPENAI_BASE_URL=https://coding-intl.dashscope.aliyuncs.com/v1`.
The planner passes that exact Alibaba endpoint as `OPENAI_BASE_URL` and rejects
another value.

Use the highest stable reasoning setting supported by each adapter. The matrix
pins `high`, `xhigh`, or `max` where Harbor exposes that control. Kimi Code's
current Harbor adapter has no cross-provider reasoning flag, so those cells are
explicitly locked as `provider_default`; use endpoints whose configured default
is the intended high-reasoning mode and report that limitation rather than
silently claiming an equivalent effort setting.

After upgrading Harbor, generate the pilot plan with its exact installed
version:

First verify the Docker boundary itself:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.smoke_libgen_docker_network \
  --task benchmarks/libgen/tasks/s3_atac \
  --out analysis/libgen/docker-network-smoke.json
```

The probe must show setup-phase HTTPS access to Debian, the redirected UV
installer, and the Antigravity CLI bootstrap and release manifest; successful
unauthenticated TLS/HTTP access to the API and native subscription endpoints
for Codex, Claude Code, Gemini CLI/Antigravity, and Qwen Code;
denial of the Debian setup host after the provider phase begins, failure for
`example.com`, and failure for a direct external socket. Bind the successful
report into the experiment lock with
`--network-smoke-report analysis/libgen/docker-network-smoke.json`.

Then run the single-protocol telemetry smoke test. It covers all 12 balanced
model × harness cells and four additional native-only cells (16 unique trials
total). The Kimi native pairing reuses its balanced-core execution:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.plan_libgen_matrix \
  --mode smoke \
  --tasks benchmarks/libgen/tasks \
  --harbor-version "$(harbor --version)" \
  --network-smoke-report analysis/libgen/docker-network-smoke.json \
  --out runs/libgen/plans/smoke

bash runs/libgen/plans/smoke/run.sh

PYTHONPATH=src python -m libstruct_bench.cli.summarize_libgen_runs \
  --runs-root runs/libgen \
  --experiment-lock runs/libgen/plans/smoke/experiment_lock.json \
  --out analysis/libgen/smoke
```

Inspect `analysis/libgen/smoke/telemetry_audit.json` before starting the pilot.
The planner explicitly disables Harbor's automatic retry. Agent timeouts are
zero-valued scheduled attempts in the primary analysis and must not be replaced
there. A post-freeze diagnostic rerun may still be preserved with the wrapper,
but it remains excluded from the primary result:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.resume_libgen_job \
  -p runs/libgen/libgen-smoke-MODEL-HARNESS \
  -f AgentTimeoutError
```

The wrapper snapshots the prior result, trajectory, verifier output, and agent
logs under `.libgen_telemetry/resume_snapshots/` before invoking
`harbor job resume`. A confirmed infrastructure/provider outage is the sole
eligible primary replacement and requires explicit documentation:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.resume_libgen_job \
  -p runs/libgen/libgen-smoke-MODEL-HARNESS \
  -f ApiRateLimitError \
  --confirmed-infrastructure-outage \
  --confirmed-by CURATOR_ID \
  --reason "provider incident URL or frozen incident note"
```

`AgentTimeoutError` is rejected under this confirmation flag. All executions
remain in `trials.csv`; `is_primary_execution` identifies the selected scheduled
execution and `primary_attempts.csv` is the one-row-per-attempt analysis table.

Then generate the pilot plan:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.plan_libgen_matrix \
  --mode pilot \
  --tasks benchmarks/libgen/tasks \
  --harbor-version "$(harbor --version)" \
  --network-smoke-report analysis/libgen/docker-network-smoke.json \
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
  --network-smoke-report analysis/libgen/docker-network-smoke.json \
  --pilot-clearance analysis/libgen/pilot-review-status.json \
  --out runs/libgen/plans/full
```

The planner records model IDs, harness versions, reasoning settings, Harbor
version, protocols, attempts, the frozen task digest, expected trial count,
Docker network policy and smoke evidence, and the primary aggregation policy in
`experiment_lock.json`. It also embeds the immutable pricing snapshot from
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

- `trials.csv`: one row per preserved execution, with primary-selection fields;
- `primary_attempts.csv`: one row per scheduled attempt, including explicit
  zero rows for missing attempts and scheduled-population `primary_*` versions
  of reward and all five public component metrics;
- `summary.json`: balanced-core effects, valid completion rate, and plot
  readiness;
- `telemetry_audit.json`: missing fields by trial and model × harness cell;
- `telemetry_missing.csv`: the incomplete rows in a compact review table.

`trials.csv` separates `prediction_valid`, `verifier_completed`, and
`exception_type`. An agent exception therefore does not turn an otherwise
valid, verified prediction into an invalid prediction. It also keeps total,
agent, and verifier duration; standard and provider-specific token fields;
retry/resume lineage; and usage/cost from timed-out executions.

Use `normalized_api_cost_usd` versus
`primary_t3_molecular_transition_f1` for the API-equivalent cost/performance
plot, so invalid, incomplete, missing, and agent-timeout attempts contribute
zero performance. Use `agent_duration_seconds` versus
`valid_completion` (aggregated to a rate by cell) for the runtime/completion
plot. `reported_cost_usd` is the amount surfaced by the Harbor adapter, while
`reported_cost_kind` says whether that amount came from the provider CLI or was
estimated. `normalized_api_cost_usd` is always an estimate from the frozen
first-party standard API rates. Its precision field distinguishes per-call
normalization from aggregate-token fallbacks. Subscription fees, routing
markups, tool-call charges, explicit-cache storage, taxes, and negotiated or
nonstandard service tiers are outside that normalization.

The corresponding raw metric columns remain unchanged for error analysis.
`valid_output_*` columns and their separate summary are diagnostic-only and
exclude attempts without a valid, verifier-completed prediction. A confirmed
infrastructure replacement supplies all `primary_*` metrics from the same
approved replacement execution selected for `primary_reward`.

Primary balanced-core model means, harness means, and model×harness interaction
residuals use all scheduled attempts. Valid predictions receive their normal
score; invalid, incomplete, missing, and agent-timeout attempts receive zero.
Confirmed and documented infrastructure/provider reruns may replace the affected
execution. Valid-output-only means remain a separate diagnostic. Native
extensions are summarized in a separate descriptive section.

## Frozen external-knowledge interventions

The Libgen improvement experiment reuses the unchanged v3 tasks and separate
verifier. External knowledge is supplied only through a read-only bind mount at
`/workspace/external_knowledge` plus a hash-pinned appended instruction. Harbor
adds job-level environment mounts to the agent environment; its separate
verifier environment receives a different mount list, so the verifier cannot
read the intervention files.

The three approved intervention conditions are:

- `general_methods_v1`;
- `cross_protocol_memory_v1`;
- `general_methods_plus_memory_v1`.

They run on `sci_atac_seq`, `scrrbs`, `smart_seq`, `share_seq`, and
`ddseq_single_cell_3_rna_seq_kit`. The hidden donor-target overlap report and
projection-validation report are never placed under an agent exposure root.
The baseline task directories, schemas, scorer, ground truth, network policy,
and verifier remain byte-identical.

After human review, first record a detached approval; do not edit the frozen
condition manifests to change their status:

```bash
LIBGEN_PRIVATE_ROOT=/Users/seqmachines/playground/protocols-test

PYTHONPATH=src python -m \
  libstruct_bench.cli.approve_libgen_external_knowledge \
  --review-candidate \
  "$LIBGEN_PRIVATE_ROOT/ground_truth_audit/reviews/external_knowledge/libgen_improvement_v1/iteration-002/review-candidate-002.json" \
  --reviewer CURATOR_ID \
  --approved-at 2026-08-17T00:00:00Z \
  --rationale "Revised primer, donor lineage, overlap interpretation, and condition digests approved." \
  --out \
  "$LIBGEN_PRIVATE_ROOT/ground_truth_audit/reviews/external_knowledge/libgen_improvement_v1/iteration-002/final-approval-001.json"
```

Prepare the allowlisted Harbor exposure roots in the private data repository:

```bash
PYTHONPATH=src python -m \
  libstruct_bench.cli.prepare_libgen_external_knowledge_harbor \
  --asset-root \
  "$LIBGEN_PRIVATE_ROOT/external_knowledge/libgen_improvement_v1_review_candidate_002" \
  --review-candidate \
  "$LIBGEN_PRIVATE_ROOT/ground_truth_audit/reviews/external_knowledge/libgen_improvement_v1/iteration-002/review-candidate-002.json" \
  --approval \
  "$LIBGEN_PRIVATE_ROOT/ground_truth_audit/reviews/external_knowledge/libgen_improvement_v1/iteration-002/final-approval-001.json" \
  --tasks benchmarks/libgen/tasks \
  --harbor-version "$(harbor --version)" \
  --created-at 2026-08-17T00:00:00Z \
  --out \
  "$LIBGEN_PRIVATE_ROOT/external_knowledge/libgen_improvement_v1_harbor_integration_001"
```

Create intervention jobs by cloning the completed native baseline configs. The
planner preserves each agent/model/version/reasoning configuration, pins the
five targets, disables automatic retry, and changes only the condition mount,
appended instruction, condition labels, output job name, and diagnostic
artifact collection:

```bash
PYTHONPATH=src python -m \
  libstruct_bench.cli.plan_libgen_external_knowledge_harbor \
  --integration-root \
  "$LIBGEN_PRIVATE_ROOT/external_knowledge/libgen_improvement_v1_harbor_integration_001" \
  --tasks benchmarks/libgen/tasks \
  --base-job-config runs/libgen/codex/libgen-gpt-5-6-sol/config.json \
  --base-job-config runs/libgen/claude-code/libgen-claude-code-opus-5/config.json \
  --base-job-config runs/libgen/antigravity-cli/libgen-gemini-3-7-flash/config.json \
  --base-job-config runs/libgen/kimi-code/libgen-kimi-code-kimi-k3/config.json \
  --jobs-dir runs/libgen/external-knowledge/results \
  --created-at 2026-08-17T00:00:00Z \
  --out runs/libgen/external-knowledge/plans/native-v1
```

This produces one script per baseline agent and a convenience `run_all.sh`, but
the planner does not start a trial. Validate immediately before any run:

```bash
PYTHONPATH=src python -m \
  libstruct_bench.cli.validate_libgen_external_knowledge_harbor \
  --integration-root \
  "$LIBGEN_PRIVATE_ROOT/external_knowledge/libgen_improvement_v1_harbor_integration_001" \
  --tasks benchmarks/libgen/tasks \
  --plan-root runs/libgen/external-knowledge/plans/native-v1
```

Each generated run script repeats that preflight before invoking Harbor. Run
one agent script at a time when subscription or API quotas make the full
60-trial plan impractical. The experiment lock retains the approved condition
digests and preregisters T3 molecular-transition F1 as the primary memory
outcome plus overlap-stratified post-hoc T2 reporting.
