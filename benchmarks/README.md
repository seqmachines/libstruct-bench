# Prepare and run a Harbor benchmark

Install LibStructBench from the checkout with `python -m pip install -e .`. Run
the preparation commands from the repository root. Set `protocol` to your
protocol ID, `source_root` to the parent of its local document folder, and
`truth_root` to the parent of its separate, approved ground-truth folder.

Use the actual filenames from `$source_root/$protocol/` for each `--document`.
Repeat the flag to include supplements. The examples use `manual.pdf` and
`oligos.xlsx`. Preparation embeds the selected files and records their SHA-256
hashes. Keep the generated directory private because it also holds the verifier.

## Final library structure extraction

```sh
libstruct-prepare-final-library --protocol-id "$protocol" \
  --source-root "$source_root" --document manual.pdf \
  --groundtruth-root "$truth_root" --out runs/prepared/final_library_structure_extraction
```

The agent reconstructs the final library sequences and writes `prediction.json`.
The verifier compares the prediction with `groundtruth_final_lib_struct.json`
under `$truth_root/$protocol/`. [Extraction rules](final_library_structure_extraction/rules.md)

## Molecular workflow reconstruction

```sh
libstruct-prepare-molecular-workflow --protocol-id "$protocol" \
  --source-root "$source_root" --document manual.pdf --document oligos.xlsx \
  --groundtruth-root "$truth_root" --out runs/prepared/molecular_workflow_reconstruction
```

The agent writes a linked oligo-family catalog (`oligo_family_prediction.json`)
and a molecular state-transition graph (`molecular_workflow_prediction.json`).
The verifier scores each separately. It requires `groundtruth_final_lib_struct.json`,
`groundtruth_oligos.json`, and `groundtruth_library_generation_workflow.json`
under `$truth_root/$protocol/`. [Reconstruction rules](molecular_workflow_reconstruction/rules.md)

## Run with Harbor

Set `task_dir` to the prepared protocol directory. Choose a native agent and
model supported by your Harbor installation. Execution requires Docker and
credentials for that provider.

```sh
task_dir="runs/prepared/molecular_workflow_reconstruction/$protocol"
harbor run --path "$task_dir" --agent "$agent" --model "$model" --jobs-dir runs/jobs
```

For Codex, add `--agent-kwarg "version=$codex_version"` to pin the agent CLI.
Append `--print-config` to preview the configuration. Harbor delivers the
instructions and rules to the agent, runs the verifier separately, and saves
each trial's scores in `verifier/reward.json` inside the job directory.

To reuse a saved protocol inventory, pass `--protocols PATH` in place of the
document flags. For molecular workflow inventories, place `rules.md` beside
the inventory or pass `--rules benchmarks/molecular_workflow_reconstruction/rules.md`.
The paper inventories pin its 20-protocol panel. The generated
molecular workflow verifier uses 4.6.0. The paper reports scores from 4.7.0.
[Study configurations and reproduction](../paper/README.md)
