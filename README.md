# LibStructBench

Benchmarks on sequencing protocols with [Harbor](https://github.com/harbor-framework/harbor): 1) **Final library structure extraction** extracts the final library sequences. 2) **Molecular workflow reconstruction** collects the oligo families and the molecular states and transitions for the whole molecular workflow of sequencing library preparation.

## Public ground truth

Ground truth for 73 human-curated and agent-audited [scg_lib_structs](https://teichlab.github.io/scg_lib_structs/) protocols is public on Hugging Face. A 20-protocol benchmark panel is also available as a separate download. 
* [Full collection (73 protocols)](https://huggingface.co/datasets/sequencing/scg-protocols-all-groundtruth/tree/4095c7c523f640a7464f50166d3ba552d0c2ae0e) 
* [Subset (20 protocols)](https://huggingface.co/datasets/sequencing/scg-protocols-v1-groundtruth/tree/87cee8097f882d5005398a75b056ad21601768e7)


## 1. Get the references and prepare a task

From the repository root, install LibStructBench and the Hugging Face CLI in your Harbor Python environment.

```sh
pip install -e . huggingface_hub
```

### Prepare the protocol bundle

Choose the protocol ID from the reference dataset. The bundle is a local folder named with that ID. If you already have the documents, set `bundle` to their folder. To start a new bundle, run

```sh
protocol_id="your_protocol_id"
bundle="protocols/$protocol_id"
mkdir -p "$bundle"
```

Place the original methods paper, protocol or version-specific vendor manual, and relevant supplements directly in that folder. Include oligo and barcode tables in their original formats. Both benchmarks read PDF, Word, Excel, CSV, TSV, and plain-text files. [Protocol source inventory](docs/release/source_inventory.csv). For example we have these input files of SPLiT-seq:
```sh
split_seq/
├── SPLiT-seq_paper.pdf
├── SPLiT-seq_supp.pdf
└── SPLiT-seq.xlsx
```

To have an agent collect the bundle, give it the protocol name, DOI, or source link and the destination folder. The collection skill records each file’s source and checksum in `SOURCE_MANIFEST.tsv`. [Collect a protocol bundle](skills/collect-protocol-bundle.md)

### Get the verifier references

Download the ground truth for all 73 protocols. Keep these JSON files separate from the documents the agent reads.

```sh
hf download sequencing/scg-protocols-all-groundtruth --repo-type dataset
```

Hugging Face prints the path to the downloaded dataset in its local cache. Set `references` to that path.

```sh
references="/path/printed/by/hf"
```

Choose one benchmark below. The first line prepares the task with the references for that protocol. The second sets `task` to the directory Harbor uses in step 2.

**Final library structure extraction**

```sh
libstruct-prep-library "$bundle" --groundtruth-root "$references" --out runs/final_lib_struct
task="runs/final_lib_struct/$protocol_id"
```

**Molecular workflow reconstruction**

```sh
libstruct-prep-workflow "$bundle" --groundtruth-root "$references" --out runs/mol_workflow
task="runs/mol_workflow/$protocol_id"
```

## 2. Run the agent

Start Docker and configure your provider credentials. In the same shell, run Harbor with an agent and model your account can access. This example uses the Codex model from the paper. [Study configuration](paper/benchmarks/task1/codex.json)

```sh
agent="codex"
model="name" # e.g. gpt-5.6-sol
harbor run --path "$task" --agent "$agent" --model "$model" --jobs-dir runs/jobs
```

For **Final library structure extraction**, you can also use `libstruct-run-library` to run a direct OpenRouter API prediction from a protocol inventory. Score the saved prediction with `libstruct-score-library`. [Direct API commands](benchmarks/README.md#run-through-the-direct-api)

## 3. Read the results

Open `runs/jobs/<job>/<trial>/verifier/reward.json` for the scores.

| Benchmark | Agent predictions | Verifier diagnostics |
| --- | --- | --- |
| Final library structure extraction | `prediction.json` | `audit.json` |
| Molecular workflow reconstruction | `oligo_family_prediction.json`, `molecular_workflow_prediction.json` | `details.json`, `error_analysis.json` |

[Harbor options](benchmarks/README.md) · [Paper results and reproduction](paper/README.md)
