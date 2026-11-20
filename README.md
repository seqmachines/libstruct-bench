# LibStructBench

Run either benchmark with Harbor on a local sequencing protocol. **Final library structure extraction** predicts the final library sequences. **Molecular workflow reconstruction** predicts the oligo families and the molecular states and transitions that connect them.

Start Docker, configure credentials for your chosen agent, and install LibStructBench from the repository root in a Python environment with Harbor installed.

```sh
python -m pip install -e .
```

## 1. Prepare the task

Set the protocol ID and the two parent directories. Put the documents the agent may read in `$source_root/$protocol/` and the approved reference files in `$truth_root/$protocol/`. The [bundle-fetch skill](skills/fetch-protocol-bundle/SKILL.md) can help collect documents from a protocol name, paper, DOI, or link.

```sh
protocol="my_protocol"
source_root="/path/to/protocols"
truth_root="/path/to/ground-truth"
```

Choose one command below. Replace the example filenames with yours and repeat `--document` for any supplements. Each command packages the instructions, output format, and verifier. Keep the prepared folder private because the verifier contains the reference answers.

### Final library structure extraction

The verifier needs `groundtruth_final_lib_struct.json`.

```sh
prepared="runs/prepared/final_library_structure_extraction"
libstruct-prepare-final-library --protocol-id "$protocol" \
  --source-root "$source_root" --document manual.pdf \
  --groundtruth-root "$truth_root" --out "$prepared"
```

### Molecular workflow reconstruction

The verifier needs `groundtruth_final_lib_struct.json`, `groundtruth_oligos.json`, and `groundtruth_library_generation_workflow.json`.

```sh
prepared="runs/prepared/molecular_workflow_reconstruction"
libstruct-prepare-molecular-workflow --protocol-id "$protocol" \
  --source-root "$source_root" --document manual.pdf --document oligos.xlsx \
  --groundtruth-root "$truth_root" --out "$prepared"
```

## 2. Run the agent

Use the same shell. Set the agent and model to a pair your provider supports. You can reuse the prepared folder to compare agents or models.

```sh
agent="codex"
model="your-model-id"
harbor run --path "$prepared/$protocol" --agent "$agent" --model "$model" --jobs-dir runs/jobs
```

## 3. Read the results

Open `runs/jobs/<job>/<trial>/verifier/reward.json` for the scores. The verifier writes the diagnostics alongside it.

| Benchmark | Agent predictions | Verifier diagnostics |
| --- | --- | --- |
| Final library structure extraction | `prediction.json` | `audit.json` |
| Molecular workflow reconstruction | `oligo_family_prediction.json`, `molecular_workflow_prediction.json` | `details.json`, `error_analysis.json` |

The molecular workflow quickstart uses verifier 4.6.0. The paper reports scores from 4.7.0. [Harbor options](benchmarks/README.md) · [Paper results and reproduction](paper/README.md)

The benchmark reference records build on [scg_lib_structs (CC BY 4.0)](https://doi.org/10.5281/zenodo.10042390). [20-protocol reference dataset](https://huggingface.co/datasets/sequencing/scg-protocols-v1-groundtruth) · [Protocol source inventory](docs/release/source_inventory.csv)
