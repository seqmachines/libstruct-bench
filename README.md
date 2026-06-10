# libstruct-bench

Benchmarking agentic systems on sequencing library structure extraction.

This repository now starts with the narrow 1st task: extracting oligo names and sequences from protocol inputs.

## V1 Scope

- **Task:** extract named oligo sequences from a protocol input.
- **Execution:** one Harbor task per protocol.
- **Data:** raw protocol inputs and ground-truth snapshots live in Hugging Face
  datasets and are pulled at Harbor run time.
- **Grading:** compare normalized oligo sequences without requiring exact
  `oligo_id` matches.
- **Reward:** sequence F1 from best one-to-one sequence matching.
- **Diagnostics:** exact match and oligo-name similarity on matched pairs.

Agents must write `/logs/artifacts/prediction.json`. The JSON schema is in `schemas/oligo_extraction_prediction.v1.schema.json`. Detailed metric and normalization rules are in `docs/oligo_extraction_v1.md`.

## Run

Install Harbor separately, then run a generated local task dataset:

```bash
harbor run \
  -p benchmarks/oligo_extraction_v1/tasks \
  -a codex \
  -m "<model>"
```

The verifier writes numeric Harbor metrics to `/logs/verifier/reward.json` and
pair-level audit details to `/logs/verifier/matches.json`.

## Development

Run the local unit tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```
