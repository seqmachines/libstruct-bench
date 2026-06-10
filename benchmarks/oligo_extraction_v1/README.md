# Oligo Extraction Harbor Benchmark V1

This directory contains the v1 Harbor benchmark scaffold for oligo name and
sequence extraction. The benchmark intentionally separates agent-visible raw
inputs from verifier-only ground truth.

## Data Contract

At Harbor run time:

- Raw protocol inputs are loaded from the public Hugging Face dataset
  `sequencing/scg-protocols-v1`.
- Ground-truth oligo snapshots are loaded by the separate verifier from
  `sequencing/scg-oligo-groundtruth-v1`.
- `HF_TOKEN` is passed only to the verifier environment for private
  ground-truth access.
- Generated tasks use `network_mode = "public"` for local Docker compatibility;
  Docker isolation does not enforce Harbor host allowlists.

Protocol entries are defined in `protocols.json`. The manifest pins both HF
repos by commit SHA and lists every raw input file exposed to each task.

## Generate Tasks

```bash
PYTHONPATH=src python -m libstruct_bench.cli.generate_harbor_tasks \
  --protocols benchmarks/oligo_extraction_v1/protocols.json \
  --out benchmarks/oligo_extraction_v1/tasks \
  --force
```

Each generated task is self-contained: its verifier image vendors the grader
package under `tests/libstruct_bench`, so the verifier does not need to install
this repository over the network.

## Run With Harbor

```bash
export HF_TOKEN="<token-with-groundtruth-read-access>"

harbor run \
  -p benchmarks/oligo_extraction_v1/tasks \
  -a codex \
  -m "<model>"
```

The agent must write `/logs/artifacts/prediction.json`; the verifier writes
`/logs/verifier/reward.json` and `/logs/verifier/matches.json`.

For a single protocol, add `--include-task-name '*drop_seq'` or point `-p` at
`benchmarks/oligo_extraction_v1/tasks/drop_seq`.

`dataset.toml` pins task digests for Harbor publish/registry workflows. Local
benchmark runs use the generated task directories under `tasks/`.
