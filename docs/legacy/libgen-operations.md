> Benchmark paths below describe the historical layout. See the
> [current benchmark index](../../benchmarks/README.md) and its relocation
> inventory before using these old commands.

> Historical operator notes, retained for provenance. Some plans and comparisons
> were superseded. For selected manuscript trials, final scoring, and current
> statistics, use [`paper/README.md`](../../paper/README.md). References to
> `runs/`, `analysis/`, source bundles, and generated task trees are local-only.
> The old source repositories below are historical identifiers, not authorization
> to redistribute their PDFs. Do not launch these commands to reproduce tables.

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

## Protocol-name-only native control (Tasks 2/3)

The separate `name_only/` task root supplies only canonical protocol name and
version, plus the unchanged generic output schemas and minimally adapted task
rules. It contains the same 20 protocols as Figure 3a. This is a
**protocol-name-only (parametric-knowledge control)**, not a direct measurement
of training-data memorization. Existing tasks, predictions, and canonical
ground truth are not modified.

The prepared configs run one independent attempt per protocol, four in parallel,
with no automatic Harbor retries. They preserve the native baseline settings:

| Config | Model | CLI version | Reasoning |
|---|---|---|---|
| `name_only/codex.json` | GPT-5.6 Sol | Codex 0.147.0 | max |
| `name_only/claude-code.json` | Claude Opus 5 | Claude Code 2.1.233 | max |
| `name_only/antigravity-cli.json` | Gemini 3.7 Flash | Antigravity 1.3.2 | high |

Run from the repository root, with Docker running and the same credentials used
for the native baseline. Claude requires `CLAUDE_CODE_OAUTH_TOKEN` in the host
environment (`claude setup-token` if needed; never put its value in a config).
Codex uses the existing local `~/.codex/auth.json`. Gemini copies only Google
Cloud ADC credentials and bills project `gen-lang-client-0325887617`; the ADC
quota project must match. It does not use personal Antigravity credits.

```bash
CODEX_FORCE_AUTH_JSON=1 PYTHONPATH=src harbor run \
  --config benchmarks/libgen/name_only/codex.json

CLAUDE_FORCE_OAUTH=1 PYTHONPATH=src harbor run \
  --config benchmarks/libgen/name_only/claude-code.json

PYTHONPATH=src harbor run \
  --config benchmarks/libgen/name_only/antigravity-cli.json
```

Each command schedules 20 Docker runs, jointly producing T2 and T3. Results go
under the existing `runs/libgen/{codex,claude-code,antigravity-cli}/` directories
with a `-name-only` job suffix. Run one command at a time to keep total concurrency
at four. Do not use an existing PDF job name or retry only low-scoring outputs.

The corrected Codex config uses `libgen-gpt-5-6-sol-name-only-execfix` for a fresh
run. The original Codex control disabled `features.code_mode_host`, preventing
local shell/file tools from executing and producing missing-output zeros. Those
attempts are infrastructure failures and are not part of the corrected run.
The previous lock, configs, and controller sources are preserved under
`name_only/revisions/20261011-disabled-code-mode/`; the active lock records the
amendment and retains identical tasks, schemas, ground truth, and PDF baselines.
Use the same `name_only/codex.json` command above, not a resume of the failed job.

The corrected Antigravity config uses
`libgen-gemini-3-7-flash-gcp-name-only-profilefix`. Its original profile listed
`command_status` and `send_command_input`, which CLI 1.3.2 advertises in init
events but cannot resolve in custom profiles. Those names have been removed;
the native CLI supplies `manage_task` alongside `run_command`. The offline
regression `tests/smoke_antigravity_name_only.py` reproduces the original startup
failure, then verifies shell execution and JSON file writing/reading with the
corrected profile using localhost authentication/model fixtures and no external
network. It checks that only local tools are offered to the model. This does not
test live GCP credentials or inference availability.

Use the same `name_only/antigravity-cli.json` command above for a fresh run.
The failed Antigravity job and the prior experiment files remain preserved;
the latter are in `name_only/revisions/20261011-antigravity-invalid-profile/`.
The amended lock explicitly accepts the prior isolation-receipt hash for the
unchanged Codex and Claude conditions, preserving their completed results.

The agent image has generic validation code only: no PDFs, protocol tables,
ground truth, scoring code, host workspace, or cached conversations. Native
shell/file tools support writing and validating outputs. Codex disables web,
browser, app, plugin, memory, retrieval and delegation features; Claude uses an
explicit local-tool list, safe mode and an empty strict MCP configuration;
Antigravity uses a container-local custom agent with an explicit local-tool list
and no inherited MCP. Its discovery is checked before inference. See the
[Codex config reference](https://developers.openai.com/codex/config-reference/)
and [Antigravity agent configuration](https://antigravity.google/docs/subagents?tab=cli).

Codex keeps `features.code_mode_host=true` because the pinned model dispatches
its local tools through Code Mode. Native startup errors reporting an unavailable
Code Mode host raise `CodexExecutionHostError` after logs are collected. The
offline regression `tests/smoke_codex_name_only.py` uses the actual 0.147.0 CLI
with a local Responses stub to reproduce the disabled-host failure and verify
read/write/JSON validation with the corrected flags; it makes no model calls.

An internal Docker network blocks direct external connections. The exact-host
proxy allows installation destinations only during setup, then a pre-inference
check closes them and verifies that web/document sites are blocked. Thereafter
only the locked inference/authentication hosts are allowed. These are network
and native-tool restrictions, not TLS inspection of provider requests or an
adversarial security guarantee. The verifier runs separately without networking
and reads a frozen, hash-checked copy of approved ground truth, including the
Feature Barcoding VN and inDrop amendments. Every run records an isolation receipt.

After all three jobs finish:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.analyze_libgen_name_only
```

This prints the comparison and writes
`analysis/paper_results/libgen_name_only_statistics.json`, including every
protocol, mean scores, PDF-minus-name-only differences, percentile paired
protocol-bootstrap 95% CIs (100,000 draws, seed 20261011), wins/ties and failure
counts. It rescores both conditions with the frozen verifier contract. The
primary baseline is the mean of the three frozen native PDF attempts per
protocol, never the best attempt; the original PDF attempt is also reported as
an n=1 sensitivity comparison. Main comparisons require valid completed outputs
in both conditions. Failures are listed separately, with an additional scheduled
attempt analysis assigning completed failures zero; missing or unfinished runs
are never zero-filled. `--allow-incomplete` explicitly labels a provisional report.

For `N_req=0`, T2 is not applicable rather than vacuously perfect: ddSEQ is
excluded from T2 summaries (at most 19 pairs), retained for T3 (at most 20),
and retained in failure reporting. Use the analysis output for paper statistics,
not Harbor's unadjusted aggregate reward table.

The reproducible preparer is
`PYTHONPATH=src python -m libstruct_bench.cli.prepare_libgen_name_only`. It freezes
the approved local GT files, baseline predictions, generated tasks and controller
code in `name_only/experiment.json`; on an already prepared experiment it verifies
the lock instead of replacing it. It makes no model calls. Private generated task
snapshots are ignored by Git.

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

The cumulative capability experiment keeps its prospective training-only
sources in `capability-training-protocols.json` and its generated tasks outside
the original 20-task root. Protocols absent from the pinned public input
revision use the generator's explicit `--source-delivery embedded` mode. That
mode copies only catalogued source bytes into the agent image and verifies every
hash during the Docker build. Approved legacy terminal-contract ground truth is
compiled deterministically into the current `final_outputs` representation only
inside the isolated verifier snapshot; the canonical private source record is
not changed, and both source and derived bundle digests remain in task metadata.

## Scoring

Benchmark version: `4.6.0`. Version 3.0 introduced one connected molecular
process per T3 workflow. Version 3.1 repaired molecular-state equivalence by
canonicalizing a narrow set of biological-payload aliases, accepting terminal
token-aware reverse complements, removing free-text state metadata from
reward, and weighting reference-strand sequence at 0.50. Version 3.2 adds one
directional state-sequence equivalence: the IUPAC anchored-primer shorthand
`VN`, or its opposite-strand form `NB`, may satisfy `[ANCHOR:2]`; other literal
strings and placeholder roles remain distinct. Version 4.0 replaces T2's
sequence-only reward with sequence-dominant scientific-family scoring over
canonical nucleotide structure, positional chemistry, kind, orientation, and
controlled role. Version 4.1 applies the first completed human-review batch's
evaluator adjudications: it canonicalizes RT/cell-barcode and RNA-insert
placeholder aliases, keeps referenced handles from creating extra oligo roles,
compares continuous duplex coverage independently of paired-region partition,
normalizes extension and strand-synthesis operations, and uses molecular-event
identity only as a bounded transition-assignment tie-break. Version 4.2 makes
grader eligibility use the exact agent-visible prediction contract and replaces
forced T3 entity pairing with a minimum-confidence partial assignment plus an
assignment-only workflow-position tie-break for states. Version 4.3 makes an
empty ground-truth modification list neutral instead of treating it as verified
chemical absence, canonicalizes bead-attachment wording, and removes redundant
generic ribonucleotide prose when an explicit riboguanosine claim is present.
Version 4.4 preserves the fixed T2 weighting when that canonical modification
list is empty by imputing full wildcard credit instead of dropping the
dimension; this avoids both false chemistry penalties and incidental
reweighting of imperfect sequence, kind, orientation, or role dimensions.
Version 4.5 makes broad canonical `primer` and `adapter` roles directionally
scorable from specific primary or component functions without globally
equating primers and adapters.
Version 4.6 applies the protocol-neutral evaluator fixes confirmed in seven
human-reviewed C0/C30 comparisons. It masks used source-unrecoverable oligos by
one-to-one functional correspondence while retaining exact-only handling for
optional inventory, prevents zero-sequence T2 assignments, keeps explicitly
distinct functional panels separate, canonicalizes equivalent positional
chemistry and component-carried roles, adds directional generic-index and
sample-genomic-DNA normalization, removes state-level free-text molecule type
from architecture reward, and strengthens T3 assignment with producer-event,
payload, reagent/product, and controlled branch context. A transition whose
referenced target oligos are all source-unrecoverable now treats that dimension
as neutral rather than incorrect. This release changes only scoring and
normalization of preserved outputs; it does not add an agent-visible operation
or modality and does not alter ground truth.
Preserved predictions can be rescored, but T2 results across 3.x and 4.x are
not directly comparable; corrected state or event alignment can also change T3
graph alignment across releases.

Overall reward is:

```text
reward = 0.30 * t2_required_family_f1
       + 0.70 * t3_molecular_transition_f1
```

- T2 first canonicalizes flat sequences and ordered components to the same
  molecule-level representation. A ground-truth record containing a
  fixed-length placeholder is one family-level requirement. Concrete
  ground-truth records remain individual member-level requirements. Valid
  concrete prediction members with the same scaffold, kind, controlled role,
  orientation, and canonical modification profile collapse to one predicted
  family before precision and recall are computed. Assignment remains globally
  optimal and nucleotide-sequence-first. Matched-family reward weights
  nucleotide sequence and ordered molecule structure `0.65`, positional
  chemistry `0.15`, kind `0.10`, orientation `0.05`, and controlled functional
  role `0.05`. Inline terminal chemistry and equivalent structured modification
  declarations are one claim, so a correct `/5Phos/` need not be duplicated in
  both the sequence string and metadata. Names, aliases, and free-text role
  wording remain diagnostic. Extra members of a recovered family are neutral;
  missing required families and unrelated extra families still reduce
  `t2_required_family_f1`. Wildcards match fixed-length concrete variable
  regions but do not erase scaffold differences. The secondary
  `t2_exact_required_family_recall` requires equality on all source-supported
  scientific dimensions and is intentionally unaffected by unrelated extras.
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
  recoverability mask. State similarity weights reference-strand sequence
  `0.50`, controlled architecture `0.15`, ordered strand/segment structure
  `0.20`, and pairing/discontinuities `0.15`. State sequence comparison uses
  canonical biological-payload aliases and both full-architecture and segment
  projections. Free-text `physical_state`, `properties`, and segment `role`
  remain diagnostics rather than reward inputs. Terminal states with a shared
  modality accept the token-aware reverse complement of the physical reference
  strand.
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
[error-analysis.md](../../benchmarks/tasks2_3/error-analysis.md) for the discrepancy-adjudication and
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
`verifier/rescore/libgen-4.6.0/{reward,details,error_analysis}.json` and writes a
run-level `rescore/libgen-4.6.0/summary.json`. Original Harbor results are kept
unchanged so incompatible benchmark versions cannot be confused. Rerunning was
required for 3.0 because the agent-facing T3 contract changed; 3.1, 3.2, 4.0,
4.1 through 4.6 may rescore preserved predictions because their changes are
verifier-only.

The state scorer keeps very large single-placeholder spans compact and uses
an exact linear-time shortcut for constant-token predictions. This prevents
declared genomic lengths such as one million or one billion bases from
exhausting verifier time or memory; lengths, wildcard rules, and scores are
unchanged. Completed predictions from verifier failures can be scored locally
without another model call. Preserve original failure records and input hashes
when recovering a verifier result; agent timeouts remain subject to the
primary-analysis policy below.

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

### Extend the native baselines to three replicates

For the shared 20-protocol panel, the saved Codex, Claude Code, and Kimi Code
baselines each contain one execution per protocol. Two further attempts per
protocol produce three replicates. Gemini instead uses three fresh executions
on Antigravity 1.3.2 with Google Cloud ADC, as described below; its original
1.1.13 account-auth execution is a separate condition. Codex's additional 53
protocols are outside this shared panel; `benchmarks/libgen/tasks` contains only the 20
shared tasks. These commands retain the original T2/T3 prediction contract and
use the original model IDs, with the Gemini version/authentication change
recorded explicitly. They do not run the Task 1 tasks.

Harbor rejects a changed `n_attempts` in an existing completed job, so Codex,
Claude, and Kimi use one additional `-repeats` job inside each existing harness
directory; Gemini uses one `-gcp-n3` job. No copied task tree or new configuration
directory is needed. For the three `-repeats` jobs, the original job is
replicate 1; each schedules 20 protocols × 2 fresh attempts.
Reuse the exact same command to continue an interrupted repeats job; completed
trials are retained. Do not use `harbor job resume` to manufacture replicates by
deleting failed results. Model failures remain part of the scheduled sample.

From the repository root, run these commands sequentially. Each job runs four
trials concurrently. Load the same credentials used by the original runs into
the shell first. Authentication selectors belong in the host shell, not
`--agent-env` or task environment settings, to avoid artifact redaction of
short boolean values. These commands omit the obsolete job-level verifier
artifact entries; Harbor already collects the separate verifier logs.

If the credentials are in the repository `.env`, export them into the same
shell before running Harbor (a `.env` entry alone is insufficient):

```bash
set -a
source .env
set +a
```

```bash
CODEX_FORCE_AUTH_JSON=1 harbor run \
  --path benchmarks/libgen/tasks \
  --agent codex --model gpt-5.6-sol \
  --agent-kwarg version=0.147.0 \
  --agent-kwarg reasoning_effort=max \
  --n-attempts 2 --n-concurrent 4 \
  --jobs-dir runs/libgen/codex \
  --job-name libgen-gpt-5-6-sol-repeats

test -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" && CLAUDE_FORCE_OAUTH=1 harbor run \
  --path benchmarks/libgen/tasks \
  --agent claude-code --model claude-opus-5 \
  --agent-kwarg version=2.1.233 \
  --agent-kwarg reasoning_effort=max \
  --n-attempts 2 --n-concurrent 4 \
  --jobs-dir runs/libgen/claude-code \
  --job-name libgen-claude-code-opus-5-repeats

harbor run \
  --path benchmarks/libgen/tasks \
  --agent kimi-cli --model kimi/kimi-k3 \
  --agent-kwarg version=1.49.0 \
  --agent-kwarg reasoning_effort=max \
  --agent-kwarg base_url=https://api.moonshot.ai/v1 \
  --n-attempts 2 --n-concurrent 4 \
  --jobs-dir runs/libgen/kimi-code \
  --job-name libgen-kimi-code-kimi-k3-repeats
```

Claude requires `CLAUDE_CODE_OAUTH_TOKEN`. Kimi uses `KIMI_API_KEY` or
`MOONSHOT_API_KEY`. The Kimi `reasoning_effort=max` kwarg preserves the original
recorded configuration; the installed adapter does not expose a corresponding
CLI effort flag, so it must not be reported as a verified effective effort.
Use `--print-config` to inspect configuration without starting any trials.

Gemini uses Antigravity **1.3.2** with Google Cloud ADC and the
`gemini-3.7-flash-high` model. Schedule **three fresh attempts per protocol**
(60 trials total). This forms the complete Gemini n=3 sample; retain the old
1.1.13 account-auth results separately. The pinned GCP adapter downloads the
fixed release, verifies its SHA-512 checksum and installed version, disables
automatic updates, and records the billing project in each trial's
`agent/gcp-auth.json`.

```bash
PYTHONPATH=src harbor run \
  --path benchmarks/libgen/tasks \
  --agent libstruct_bench.harbor_agents.antigravity_gcp:AntigravityGcp \
  --model google/gemini-3.7-flash-high \
  --agent-kwarg version=1.3.2 \
  --agent-kwarg reasoning_effort=high \
  --agent-kwarg gcp_project=gen-lang-client-0325887617 \
  --agent-kwarg gcp_location=global \
  --extra-docker-compose benchmarks/libgen/antigravity-gcp.compose.yaml \
  --n-attempts 3 --n-concurrent 4 \
  --jobs-dir runs/libgen/antigravity-cli \
  --job-name libgen-gemini-3-7-flash-gcp-n3
```

This uses the ADC credentials already created for project
`gen-lang-client-0325887617` in Task 1 round2. The adapter validates their quota
project before installation. It retains the native harness, tools, and
55-minute print timeout, and selects ADC even when personal OAuth/API-key
variables are present. Usage is routed through the Google Cloud project;
credit coverage follows the billing account's credit terms. See
[Google's ADC instructions](https://www.antigravity.google/docs/enterprise#setting-up-application-default-credentials-adc).

The Gemini-only Compose overlay adds `aiplatform.googleapis.com` to the task's
provider allowlist. It keeps the internal network, exact-host HTTPS checks,
and setup-to-agent transition, and records the effective policy hashes in
`provider_egress.jsonl`. Harbor hashes the overlay in the job lock. Shared task
files and the other models' network policy remain fixed, so existing jobs
can still resume. No copied task tree is needed; Harbor creates only the new
Gemini job directory when the command runs.

For analysis, pool the original execution with the two repeats for Codex,
Claude, and Kimi; use the three new GCP executions for Gemini. Score every
execution with the same evaluator release and ground-truth snapshot. Freeze the
agent-visible task files before launch; Harbor records their hashes in each
job lock. Keep prediction schema version, scorer version, and replicate
identity distinct in downstream exports.

## Prepare the two HF uploads

First check the combined local staging inputs. The existing upload tree keeps
protocols under its `protocols/` subdirectory:

```bash
PYTHONPATH=src python -m libstruct_bench.cli.check_libgen_staging \
  --source-root ${LIBSTRUCT_DATA_ROOT}/scg-v1-upload/protocols \
  --groundtruth-root ${LIBSTRUCT_DATA_ROOT}/ground_truth \
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
  --source-root ${LIBSTRUCT_DATA_ROOT}/protocols \
  --groundtruth-root ${LIBSTRUCT_DATA_ROOT}/ground_truth \
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
logs under `<jobs-dir>/.libgen_telemetry/<job-name>/resume_snapshots/` before invoking
`harbor job resume`. This location is outside the job directory: Harbor deletes
child directories without a trial `result.json`, including hidden backup folders.
The wrapper copies any surviving legacy history to this safe location before
resuming; the summarizer supports both locations without counting duplicate copies.
It also preserves interrupted trial directories with a config but no result.
For jobs that list individual tasks with a named `source`, the wrapper supplies
missing Harbor aggregate metrics inside the resume process. This avoids the
`_update_metric_display` empty-list `IndexError` in affected Harbor versions
without editing the installed package, saved configs, or completed predictions.
After that display crash, resume with `-f CancelledError`: successful trials are
retained, cancelled trials restart, and trials not yet started remain scheduled.
A confirmed infrastructure/provider outage is the sole
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

Kimi CLI can terminate with a JSON-RPC provider transport error while Harbor
records no exception and the missing prediction receives zero. The resume
wrapper's `--include-kimi-provider-errors` flag recognizes terminal upstream
idle timeouts, terminated streams, and lost connections in `agent/kimi-cli.txt`.
With no `-f`, it selects only these failures, preserving agent timeouts and
valid predictions. It archives the original result and wire log before adding
the `KimiProviderError` classification for Harbor's resume filter. Use
`--snapshot-only` to inspect the selection without modifying results or making
model calls; use the confirmation fields above when documenting a provider
failure as a primary replacement. Do not retry every zero-valued prediction.

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
LIBGEN_PRIVATE_ROOT=${LIBSTRUCT_DATA_ROOT}

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
