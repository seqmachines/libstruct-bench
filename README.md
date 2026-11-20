# LibStructBench

Run either benchmark with Harbor on a local sequencing protocol. **Final library structure extraction** predicts the final library sequences. **Molecular workflow reconstruction** predicts the oligo families and the molecular states and transitions that connect them.

Start Docker, configure credentials for your chosen agent, and install LibStructBench from the repository root in a Python environment with Harbor installed.

```sh
python -m pip install -e .
```

## 1. Prepare the task

Set the path to one protocol's document folder. Its name supplies the protocol ID. For any of the paper's 20 protocols, use one of the commands below. `--paper-truth` downloads missing ground-truth files, checks them against the paper's SHA-256 hashes, and reuses verified local copies.

```sh
bundle="/path/to/collection/protocols/my_protocol"
```

**Final library structure extraction**

```sh
task=$(libstruct-prep-library "$bundle" --paper-truth)
```

**Molecular workflow reconstruction**

```sh
task=$(libstruct-prep-workflow "$bundle" --paper-truth)
```

For your own ground truth, replace `--paper-truth` with `--truth "/path/to/reference-folder"`. Both commands also work offline with just `"$bundle"` when the required files are already in `collection/ground_truth/<protocol_id>/`. The command prints the prepared task path. Keep that directory private because the verifier contains the answers. [Protocol bundle collection skill](skills/fetch-protocol-bundle/SKILL.md)

Both Harbor tasks include the shared rules for reading protocol evidence alongside their task-specific instructions. [Shared protocol-processing rules](benchmarks/shared/protocol_processing_rules.md)

## 2. Run the agent

Use the same shell. Set the agent and model to a pair your provider supports.

```sh
agent="codex"
model="your-model-id"
harbor run --path "$task" --agent "$agent" --model "$model" --jobs-dir runs/jobs
```

For **Final library structure extraction**, you can also use `libstruct-run-library` to run a direct OpenRouter API prediction from a protocol inventory. Score the saved prediction with `libstruct-score-library`. [Direct API commands](benchmarks/README.md#run-through-the-direct-api)

## 3. Read the results

Open `runs/jobs/<job>/<trial>/verifier/reward.json` for the scores. Final library extraction reports the paper's sequence similarity and protocol-level exact match. Molecular workflow reconstruction uses verifier 4.7.0. The verifier writes diagnostics alongside the scores.

| Benchmark | Agent predictions | Verifier diagnostics |
| --- | --- | --- |
| Final library structure extraction | `prediction.json` | `audit.json` |
| Molecular workflow reconstruction | `oligo_family_prediction.json`, `molecular_workflow_prediction.json` | `details.json`, `error_analysis.json` |

[Harbor options](benchmarks/README.md) · [Paper results and reproduction](paper/README.md)

The benchmark reference records build on [scg_lib_structs (CC BY 4.0)](https://doi.org/10.5281/zenodo.10042390). [20-protocol reference dataset](https://huggingface.co/datasets/sequencing/scg-protocols-v1-groundtruth) · [Protocol source inventory](docs/release/source_inventory.csv)
