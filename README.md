# LibStructBench

LibStructBench evaluates reconstruction of sequencing-library preparation from
protocol evidence: **Task 1**, final library sequences; **Task 2**, oligo
families; and **Task 3**, molecular states, transitions, and typed graph edges.
This is the code and results preparation for the NeurIPS 2026 manuscript.
The public paper/preprint link will be added by the authors.

## Install and test

Run from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,audit-docs,archive,archive-test]' -r paper/requirements.txt
python -m pytest -q
```

Core utilities require Python 3.10 or newer. The saved paper analyses used
Python 3.13; see [the analysis environment](paper/requirements.txt). Docker,
Harbor, and provider credentials are needed only for new agent executions,
not for checking the published tables. Credentials belong in the shell or an
ignored local `.env`, never in configuration files or Git.

## Data and source documents

Ground-truth release destinations:

- [20-protocol benchmark](https://huggingface.co/datasets/sequencing/scg-protocols-v1-groundtruth)
- [Full protocol collection](https://huggingface.co/datasets/sequencing/scg-protocols-all-groundtruth)

The records build on [scg_lib_structs](https://github.com/Teichlab/scg_lib_structs)
([archived release](https://doi.org/10.5281/zenodo.10042390), CC BY 4.0).
Consult each dataset card for attribution and release terms. These terms do
not grant redistribution rights to associated journal articles or vendor guides.
Dataset upload availability and immutable release revisions must be confirmed
before the public release; repository links alone do not pin a version.

**This working-tree release contains no source PDFs or full-text trajectories.**
Obtain lawful copies from their original sources and verify their hashes against
the protocol inventories. The [source inventory](docs/release/source_inventory.csv)
preserves available links, document versions, hashes, and rights-review status;
missing links and unresolved licenses are explicit. Source bundles, raw audit
records, and generated private verifier contexts stay outside the public tree.
The [release review](docs/release/cleanup_report.md) records the public file
selection and the outstanding source-rights and provenance questions.

## Reproduce the reported analysis

The [paper results package](paper/README.md) contains per-protocol scores,
aggregates, selected-trial/reference manifests, and analysis programs. Checks
below make no model calls and do not change any recorded score:

```bash
python paper/reproduce.py
# Also rederive the saved paired tests and bootstrap confidence intervals:
python -m pip install -r paper/requirements.txt
python paper/reproduce.py --statistics
```

The final paper uses **verifier 4.7.0**, available explicitly as
`libstruct_bench.libgen.v4_7_0`. The historical default
`libstruct-grade-libgen` remains **4.6.0**. Do not use that default to claim a
4.7.0 reproduction. Both implementations and regression tests are retained;
see the [verifier changelog](src/libstruct_bench/libgen/v4_7_0/CHANGELOG.md).
Task 1 has its separate sequence scorer.

| Manuscript analysis | Public results and methods |
| --- | --- |
| Task 1 API/agent and PDF controls | [Task 1 configuration](benchmarks/task1/README.md), `paper/data/main/task1_*.csv`, `paper/data/bonferroni/` |
| Native and model–harness baselines | `paper/data/main/`, `paper/data/model_harness/`, `paper/data/secondary/` |
| Error profiles | `paper/data/main/error_profiles.csv`, [analysis definitions](benchmarks/tasks2_3/error-analysis.md) |
| External knowledge and cumulative learning | Figures 4a/4c/4d in `paper/data/main/`, [learning code and designs](docs/improvement/README.md) |
| Weight and threshold sensitivity | `paper/data/sensitivity/`, `paper/methods/camera_ready/sensitivity/` |
| Repeated GPT/Codex executions | `paper/data/repeats/` (60 executions; descriptive analysis) |
| Protocol-name-only controls | `paper/data/name_only/` (primary comparison: one selected execution per condition) |
| Ground-truth validation and adjudication | [Audit guide](docs/audit/README.md), [agent instructions](paper/audit/), `schemas/`, `tests/audit/` |

Figures 3a and 3b retain one selected execution per system and protocol.
Task 2 primary macro-F1 excludes protocols with no required recoverable oligo
families; Task 3 retains its original panels. The historical C30 selection is
unchanged. Three GPT/Codex executions are a separate variability analysis,
not three attempts for every Figure 3 system. Name-only results distinguish
Task 1 API calls from Tasks 2–3 native agents and use the verified Claude
SHARE-seq retry. Final statistical tables use the recorded Bonferroni families.

## Repository layout

```text
src/libstruct_bench/  scorers, validators, audit tools, harness adapters, learning
schemas/             prediction, ground-truth, audit, and analysis contracts
benchmarks/          task1/, tasks2_3/, shared/: inventories, rules and run configs
improvement/         procedural packs and learning runtime
tests/               synthetic regression and isolation tests
docs/                audit policies, experiment workflows, release review
paper/               version-locked numerical results and analysis provenance
tools/               public-export and release-audit utilities
```

`runs/`, `analysis/`, generated task images, local caches, and
`.release-archive/` are ignored local artifacts. Older operator plans are
retained as [historical notes](docs/legacy/libgen-operations.md), not as the
definition of the final paper panels.

To browse saved local runs by task, condition, model, and recorded configuration:

```bash
python tools/build_run_browser.py
```

Open `runs/by_setting/index.html` or browse its grouped folders. The folders are
relative symbolic links: original results and agent traces remain at their
existing paths. The browser flags paper-selected runs and archived copies;
its directory counts are not independent-execution counts. Make separate copies
for proofreading edits, since editing through a link changes the original.
The tool refuses to overwrite an existing index; use `--output runs/by_setting_new`
to create another view. All generated views remain local and Git-ignored.

## Archive native experiments

On a configured benchmark host, start a new native Harbor job from the repository
root with the command below. Set `config` to the path of a prepared job JSON.

```sh
./tools/libstruct-archive launch -c "$config" --job-name "native-$(date -u +%Y%m%dT%H%M%SZ)"
```

The launcher loads the private host profile and archives eligible trials on exit.
Retry pending transfers with `./tools/libstruct-archive reconcile`.

Use the [experiment archive guide](docs/experiment-archive.md) to inventory local runs,
identify verifier versions, restore one trial by checksum, or configure private R2 and
Atlas archiving. The default inventory covers native scg_lib_structs trials and records
the sibling parser-control experiments as local-only. Production writes require a
confirmed target and bulk migration requires the reviewed inventory digest.

## Executing or rescoring a benchmark

To run one protocol, point the Harbor config at the local prepared task
collection and put the protocol ID in its `task_names` list. The
[single-protocol example](docs/experiment-archive.md#run-a-single-protocol)
shows how to select a folder, launch a native agent, and archive the result.

See the [benchmark index](benchmarks/README.md), [Tasks 2–3 setup](benchmarks/tasks2_3/README.md) and
[Task 1 setup](benchmarks/task1/README.md). Task generators
require hash-pinned source copies and approved ground truth. They separate
agent inputs from verifier-only answers. Generated tasks are deliberately not
checked in. No new inference is necessary to reproduce saved-score statistics.

Full raw-prediction rescoring and regeneration of every historical figure also
require the frozen local inputs named in the manifests. Those inputs are not
all part of this public candidate. [Reproduction scope and remaining gaps](paper/README.md)
distinguish available table-level reproduction from these additional steps.

Ground-truth changes must follow [human adjudication](docs/audit/adjudication-policy.md)
and deterministic, hash-verified application. Agent proposals are not approval.
