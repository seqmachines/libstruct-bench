# Prepare and run a Harbor benchmark

Run from the repository root after installing LibStructBench with `python -m pip install -e .`.
Supply a local folder containing the original documents for one protocol. Preparation
includes PDF, Word, Excel, CSV, TSV, and plain-text files directly inside it.
Hidden files, Office temporary files, and `SOURCE_MANIFEST.tsv` are skipped.
The protocol ID is the folder name.

## Final library structure extraction

```sh
task=$(libstruct-prep-library /path/to/protocols_scg_all/split_seq)
```

The verifier requires `groundtruth_final_lib_struct.json`. The agent writes
`prediction.json`. [Extraction rules](final_library_structure_extraction/rules.md)

## Molecular workflow reconstruction

```sh
task=$(libstruct-prep-workflow /path/to/protocols_scg_all/split_seq)
```

The verifier requires `groundtruth_final_lib_struct.json`, `groundtruth_oligos.json`,
and `groundtruth_library_generation_workflow.json`. The agent writes
`oligo_family_prediction.json` and `molecular_workflow_prediction.json`.
[Reconstruction rules](molecular_workflow_reconstruction/rules.md)

## Reference folders and output

For a bundle at `collection/protocols_scg_all/split_seq/`, the default reference
folder is `collection/ground_truth/split_seq/`. To choose it explicitly, pass
the directory that directly contains the JSON files.

```sh
task=$(libstruct-prep-workflow "$bundle" --truth "$reference_folder")
```

Preparation embeds the evidence, records SHA-256 hashes, and stages references
in the separate verifier. The command prints the generated task path under
`runs/prepared/`. Use `--out` to choose another parent directory.

## Run with Harbor

Start Docker and configure your provider credentials. In the same shell, run

```sh
harbor run --path "$task" --agent "$agent" --model "$model" --jobs-dir runs/jobs
```

Append `--print-config` to preview the job. For Codex, add
`--agent-kwarg "version=$codex_version"` to pin the agent CLI. Each trial writes
scores to `verifier/reward.json`. [Study configurations and reproduction](../paper/README.md)
