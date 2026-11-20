# LibStructBench

LibStructBench evaluates how native agents interpret sequencing protocol documents with Harbor.

- **Final library structure extraction** reconstructs the sequences of the final libraries.
- **Molecular workflow reconstruction** produces an oligo-family catalog and a graph of molecular states and transitions in one run.

[Prepare a local task and run Harbor](benchmarks/README.md).

This is the code and results preparation for the NeurIPS 2026 manuscript.
The public paper/preprint link will be added by the authors.

## Collect protocol documents

The [bundle-fetch skill](skills/fetch-protocol-bundle/SKILL.md) collects the original documents for a protocol from its name, paper, DOI, or link. Copy the skill into any agent workspace. Harbor supplies the task instructions during a benchmark run.

## Install and test

For a benchmark run, install the core package with `python -m pip install -e .`.
For the full developer test suite, run from the repository root:

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
Final library structure extraction uses its own sequence scorer. The paper package
documents the selected protocols, controls, metrics, and statistical comparisons.
[Tables and study definitions](paper/README.md#authoritative-tables)

## Repository layout

```text
src/libstruct_bench/  scorers, validators, audit tools, harness adapters, learning
schemas/             prediction, ground-truth, and verifier output contracts
benchmarks/          public Harbor preparation guide, rules, and Docker network assets
skills/              portable protocol bundle collection
tests/               synthetic regression and isolation tests
docs/                audit policies, experiment workflows, release review
paper/               frozen results, panel inventories, audit and learning workflows and their schemas
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

## Internal archiving

Host operators can use the [experiment archive guide](docs/experiment-archive.md).

## Reproducing earlier runs

Full raw-prediction rescoring and regeneration of every historical figure also
require the frozen local inputs named in the manifests. Those inputs are not
all part of this public candidate. [Reproduction scope and remaining gaps](paper/README.md)
distinguish available table-level reproduction from these additional steps.

Ground-truth changes must follow [human adjudication](docs/audit/adjudication-policy.md)
and deterministic, hash-verified application. Agent proposals are not approval.
