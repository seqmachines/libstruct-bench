# Oligo Extraction Harbor Benchmark V1

This directory contains the v1 Harbor benchmark scaffold for oligo name and
sequence extraction. The benchmark intentionally separates agent-visible raw
inputs from verifier-only ground truth.

## Data Contract

At Harbor run time:

- `LIBSTRUCT_HF_INPUT_REPO` points to the Hugging Face dataset containing raw
  protocol inputs visible to the agent.
- `LIBSTRUCT_HF_GROUNDTRUTH_REPO` points to the Hugging Face dataset containing
  ground-truth oligo snapshots visible only to the separate verifier.
- `HF_TOKEN` is passed only to the verifier environment for private ground-truth
  access.

Protocol entries are defined in `protocols.json`. Until the HF repos are final,
copy `protocols.example.json` to `protocols.json` and adjust paths/revisions.

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
harbor run \
  -p benchmarks/oligo_extraction_v1/tasks \
  -a codex \
  -m "<model>"
```

The agent must write `/logs/artifacts/prediction.json`; the verifier writes
`/logs/verifier/reward.json` and `/logs/verifier/matches.json`.
