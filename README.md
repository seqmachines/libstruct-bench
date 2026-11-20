# LibStructBench

Run either benchmark with Harbor on a local sequencing protocol. **Final library structure extraction** predicts the final library sequences. **Molecular workflow reconstruction** predicts the oligo families and the molecular states and transitions that connect them.

Start Docker, configure credentials for your chosen agent, and install LibStructBench from the repository root in a Python environment with Harbor installed.

```sh
python -m pip install -e .
```

## 1. Prepare the task

Set the path to a protocol folder, then run **one** of the two preparation commands. The folder name supplies the protocol ID. Each command collects the documents in the folder and prints the prepared Harbor task path.

```sh
bundle="/path/to/protocols_scg_all/split_seq"
```

**Final library structure extraction**

```sh
task=$(libstruct-prep-library "$bundle")
```

**Molecular workflow reconstruction**

```sh
task=$(libstruct-prep-workflow "$bundle")
```

For this layout, both commands find the reference JSON files in `ground_truth/split_seq/` beside `protocols_scg_all/`. To use another reference folder, add `--truth /path/to/references/split_seq`. Keep the generated task private because it contains the verifier's reference answers. [Protocol bundle collection skill](skills/fetch-protocol-bundle/SKILL.md)

## 2. Run the agent

Use the same shell. Set the agent and model to a pair your provider supports.

```sh
agent="codex"
model="your-model-id"
harbor run --path "$task" --agent "$agent" --model "$model" --jobs-dir runs/jobs
```

## 3. Read the results

Open `runs/jobs/<job>/<trial>/verifier/reward.json` for the scores. The verifier writes the diagnostics alongside it.

| Benchmark | Agent predictions | Verifier diagnostics |
| --- | --- | --- |
| Final library structure extraction | `prediction.json` | `audit.json` |
| Molecular workflow reconstruction | `oligo_family_prediction.json`, `molecular_workflow_prediction.json` | `details.json`, `error_analysis.json` |

[Harbor options](benchmarks/README.md) · [Paper results and reproduction](paper/README.md)

The benchmark reference records build on [scg_lib_structs (CC BY 4.0)](https://doi.org/10.5281/zenodo.10042390). [20-protocol reference dataset](https://huggingface.co/datasets/sequencing/scg-protocols-v1-groundtruth) · [Protocol source inventory](docs/release/source_inventory.csv)
