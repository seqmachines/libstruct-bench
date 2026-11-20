# Run a benchmark

Run from the repository root after installing LibStructBench with `python -m pip install -e .`.
Start with a local folder containing the original documents for one protocol. [Prepare the protocol bundle](../README.md#prepare-the-protocol-bundle)

Preparation includes PDF, Word, Excel, CSV, TSV, and plain-text files directly inside it.
Hidden files, Office temporary files, and `SOURCE_MANIFEST.tsv` are skipped.
The protocol ID is the folder name. Both Harbor prep commands copy the same shared
rules into the agent workspace as `/workspace/protocol_processing_rules.md`. Harbor
delivers each task's `instruction.md` to the agent. For molecular workflow reconstruction,
`/workspace/rules.md` also defines family grouping, sequence notation, and the two required
outputs. [Shared protocol-processing rules](shared/protocol_processing_rules.md)

The public Hugging Face collection provides verifier references for all 73 scg_lib_structs protocols in LibStructBench, including the paper’s 20-protocol subset. Download the dataset and set `references` to the path printed by `hf download`. [Hugging Face download example](../README.md#1-get-the-references-and-prepare-a-task)

The quickstart downloads the current release. To reproduce the exact 73-protocol snapshot, add `--revision 4095c7c523f640a7464f50166d3ba552d0c2ae0e` to the same `hf download` command.

```sh
protocol_id="your_protocol_id"
bundle="/path/to/collection/protocols/$protocol_id"
references="/path/printed/by/hf"
```

## Final library structure extraction

```sh
libstruct-prep-library "$bundle" --groundtruth-root "$references"
task="runs/prepared/final_library_structure_extraction/$protocol_id"
```

The verifier requires `groundtruth_final_lib_struct.json`. Harbor agents and the
direct API write `prediction.json` with a `protocol_id` and one entry in `libraries`
per final library. The paper's verifier scores sequence
similarity and requires every library to match exactly for a protocol-level exact match.
[Prediction schema](../schemas/benchmark/final_library_structure_prediction.schema.json)

## Molecular workflow reconstruction

```sh
libstruct-prep-workflow "$bundle" --groundtruth-root "$references"
task="runs/prepared/molecular_workflow_reconstruction/$protocol_id"
```

The verifier requires `groundtruth_final_lib_struct.json`, `groundtruth_oligos.json`,
and `groundtruth_library_generation_workflow.json`. Verifier 4.7.0 scores the agent's
`oligo_family_prediction.json` and `molecular_workflow_prediction.json`.
[Reconstruction rules](molecular_workflow_reconstruction/rules.md)

## Reference folders and output

For a bundle at `collection/protocols/<protocol_id>/`, both commands look for references in
`collection/ground_truth/<protocol_id>/`. For the 20-protocol paper subset, add
`--paper-truth` to retrieve and verify references during prep. It checks the files the selected benchmark needs
against the paper's SHA-256 hashes and stops if a local file differs.
Add `--truth DIR` to select a different reference folder.
[Pinned reference dataset](https://huggingface.co/datasets/sequencing/scg-protocols-v1-groundtruth/tree/87cee8097f882d5005398a75b056ad21601768e7)

For a custom protocol, supply the required ground-truth JSON files from your own
curation. Omit both flags to use the default folder offline, or give its path explicitly.

```sh
libstruct-prep-workflow "$bundle" --truth "/path/to/reference-folder"
task="runs/prepared/molecular_workflow_reconstruction/$protocol_id"
```

Preparation embeds the evidence, records SHA-256 hashes, and stages references
in the separate verifier. The command prints the generated task path under
`runs/prepared/`. Use `--out` to choose another parent directory.

## Run with Harbor

Start Docker and configure your provider credentials. In the same shell, run

```sh
agent="codex"
model="gpt-5.6-sol"
harbor run --path "$task" --agent "$agent" --model "$model" --jobs-dir runs/jobs
```

Append `--print-config` to preview the job. For Codex, add
`--agent-kwarg "version=$codex_version"` to pin the agent CLI. Each trial writes
scores to `verifier/reward.json`. [Study configurations and reproduction](../paper/README.md)

## Run through the direct API

For final library structure extraction, set `OPENROUTER_API_KEY` in your environment.
Choose a protocol ID from an inventory whose Hugging Face documents allow public downloads.
Use a fresh output directory for each run.

```sh
libstruct-run-library --protocols "$inventory" --protocol-id "$protocol_id" \
  --model "$model" --out "$out"
```

The runner records the prediction path in `$out/summary.json`. Set `prediction` to
that path and `truth` to the reference folder for the same protocol. To get the
same metrics as Harbor, run

```sh
libstruct-score-library --prediction "$prediction" --protocol-id "$protocol_id" \
  --groundtruth "$truth/groundtruth_final_lib_struct.json" \
  --reward-out "$out/reward.json" --audit-out "$out/audit.json"
```
