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

Overall reward is `0.30 * T2 + 0.70 * T3`.

- T2 uses sequence-only global optimal one-to-one soft F1. Names, roles,
  orientations, and modifications are diagnostics. Ground-truth claims marked
  `explicit` or `derivable` are scored. `externally_completed`, `ambiguous`,
  and `unsupported` claims are neutral and cannot turn missing packet evidence
  into an apparent model error.
- T3 uses deterministic, ID-invariant global matching. State score is 40%
  reference-strand structure, 25% architecture, 20% segment structure, and 15%
  pairing/discontinuities. Transition score is 30% operation, 35% mapped graph
  topology, 15% mapped T2 oligos, 10% carried/discarded products, and 10% major
  reagents. T3 combines state F1 (45%), transition F1 (45%), and initial/final
  boundary F1 (10%). Missing and extra entities are penalized.
- Schema-invalid or semantically invalid linked predictions receive zero.
  Verifier infrastructure or private-ground-truth failures fail the trial
  instead of being mislabeled as model errors.

The verifier writes `reward.json`, `details.json`, and, on failure,
`error.json` under `/logs/verifier/`.

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

At implementation time this correctly blocks production: the staging source
tree is missing 23 approved primary files, and canonical ground truth is still
missing for `share_seq`. Sync those files or point `--source-root` at the
complete approved `protocols/` tree, and finish that remaining audit before
proceeding.

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

Only after all 15 pilot cells pass their compatibility and data checks, create
the full plan by changing `--mode pilot` to `--mode full` and choosing a new
output directory. The planner records model IDs, harness versions, reasoning
settings, Harbor version, protocols, attempts, and expected trial count in
`experiment_lock.json`.

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
