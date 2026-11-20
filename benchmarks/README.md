# Prepare and run a Harbor task

Run these commands from the LibStructBench checkout after installing the package
with `python -m pip install -e .`. For execution, use a Harbor installation
with Docker and authentication for your chosen native agent.

Set `protocol` to the protocol ID. Set `source_root` to the local directory
containing `<protocol_id>/<document>` and `truth_root` to the **separate**
directory containing the approved records under `<protocol_id>/`. Both commands
select evidence by inventory. Keep only primary documents in the source tree.
Generated tasks contain verifier-only answers and belong in a private directory.

## Task 1 — Final library structure

```sh
libstruct-generate-library-v0-tasks --protocol-id "$protocol" \
  --source-root "$source_root" --groundtruth-root "$truth_root" \
  --out runs/prepared/task1
```

The generator embeds the selected documents, records their SHA-256 hashes,
and places `groundtruth_final_lib_struct.json` in the separate verifier.
The default [inventory](task1/protocols.json) selects the paper's Task 1 PDFs.
Use `--protocols PATH` for a different inventory. The agent writes
`prediction.json` according to the task instructions and [shared rules](shared/protocol_processing_rules.md).

## Tasks 2–3 — Workflow reconstruction

```sh
libstruct-generate-libgen-tasks --protocol-id "$protocol" \
  --source-root "$source_root" --groundtruth-root "$truth_root" \
  --out runs/prepared/tasks2_3
```

One task produces Task 2 oligo-family predictions and Task 3 molecular-workflow
predictions, with separate metrics for each. The [inventory](tasks2_3/protocols.json)
pins the source hashes. The generator packages the [rules](tasks2_3/rules.md),
prediction schemas, Docker network policy, and verifier with the approved T1,
T2, and T3 reference files. For a custom inventory, pass `--protocols PATH` and
`--rules benchmarks/tasks2_3/rules.md`. The generated verifier uses 4.6.0.
The final paper scores use 4.7.0.

## Run either task

Harbor gives the agent the generated `instruction.md` and the task's rules.
The verifier runs separately when the agent finishes. No agent skill is required.

Choose the prepared directory for the requested task. For all three tasks, run
each directory once. Each invocation creates a new timestamped job by default.

```sh
task_dir="runs/prepared/task1/$protocol"  # or runs/prepared/tasks2_3/$protocol
harbor run --path "$task_dir" --agent "$agent" --model "$model" --jobs-dir runs/jobs
```

For Codex, add `--agent-kwarg "version=$codex_version"` to select a CLI version.
Append `--print-config` to preview the job. Changing the native agent, model,
or its version reuses the same prepared task. Harbor saves each trial's results
in the job directory, including the verifier's `reward.json`.

The [paper configurations](../paper/benchmarks/) preserve the original agent
pins and study conditions. The generators also support the original pinned
remote-source workflow. Run either setup command with `--help` for its options.
