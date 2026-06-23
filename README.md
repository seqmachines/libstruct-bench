# libstruct-bench

Benchmarking LLMs and coding agents on sequencing library structure extraction.

## Baselines

- **v0.1 LLM API:** raw protocol inputs sent directly to LLM APIs like gpt, gemini, claude.
- **v0.2 Coding agent:** raw protocol inputs solved by coding agents such as codex & claude code.

Input: raw protocol files or paper https://huggingface.co/datasets/sequencing/scg-protocols-v1

## Run v0.1: LLM API

```bash
export OPENROUTER_API_KEY="<openrouter-key>"

python -m libstruct_bench.cli.run_openrouter_library_v0 \
    --protocols benchmarks/library_structure_v0/protocols.json \
    --out runs/library_structure_v0/llm_api \
    --model openai/gpt-5.5 \
    --protocol-id "$protocol" \
    --reasoning-effort xhigh \
    --max-completion-tokens 10000 \
    --force
```

Grade predictions:

```bash
export GROUNDTRUTH_DIR="/Users/seqmachines/playground/protocols-test/scg-v1-upload/groundtruth"

pred="runs/library_structure_v0/llm_api/gpt-5.5/${protocol}/prediction.json"
grade_dir="runs/library_structure_v0/llm_api/gpt-5.5/${protocol}/grade"
mkdir -p "$grade_dir"

python -m libstruct_bench.cli.grade_library_v0 \
  --prediction "$pred" \
  --groundtruth-repo sequencing/scg-oligo-groundtruth-v1 \
  --groundtruth-path "${protocol}/groundtruth_final_lib_struct.json" \
  --revision main \
  --protocol-id "$protocol" \
  --reward-out "$grade_dir/reward.json" \
  --audit-out "$grade_dir/audit.json"
```

## Run v0.2: Coding Agent

```bash
export HF_TOKEN="<token-with-groundtruth-read-access>"
export CODEX_FORCE_AUTH_JSON=1

harbor run \
  -p "benchmarks/library_structure_v0/tasks/${protocol}" \
  -a codex \
  -m gpt-5.5 \
  --ak reasoning_effort=xhigh \
  --jobs-dir runs/library_structure_v0/harbor \
  --artifact /logs/verifier/reward.json \
  --artifact /logs/verifier/audit.json \
  --job-name "codex-gpt55-xhigh-${protocol}"

```

## Initial test with 5 protocols

Sequence similarity, regraded against the current corrected local ground truth:

| Protocol | gpt-5.5 API | gpt-5.5 Codex |
| --- | ---: | ---: |
| `10x_chromium_3_feature_barcoding` | 0.998 | 0.998 |
| `dr_seq` | 0.410 | 0.061 |
| `petri_seq` | 0.917 | 0.917 |
| `s3_atac` | 1.000 | 1.000 |
| `sci_rna_seq` | 0.795 | 0.900 |
| **Mean** | **0.824** | **0.775** |

Current readout: the direct LLM API baseline is slightly higher overall on this
small set, mainly because Codex performs poorly on `dr_seq`. Codex is better on
`sci_rna_seq`; both methods tie or nearly tie on the other three protocols.

Detailed benchmark docs live in `docs/library_structure_v0.md`.


## Prior v0 LLM snapshot

The earlier v0 snapshot evaluated full library-structure reconstruction through
large language model APIs on 13 curated `scg_lib_structs` targets.

- **Ground truth:** 13 curated library structures from `scg_lib_structs`,
  generated with LLM-assisted parsing and validated against source protocols.
- **Inputs:** each assay was submitted as `pdf`, extracted `text`, or assay
  `name` only.
- **Task:** return the full library as one 5'->3' sequence string using symbols
  for functional elements such as barcodes, UMIs, adapters, primers, ligation
  regions, and indexes.
- **Scoring:** normalized Levenshtein similarity against ground truth.

Nine frontier LLMs across five provider families were evaluated once per
protocol and input mode. Winner: **Gemini 3.1 Pro (April 2026)**.

| Model | Text Similarity | Text Failed | PDF Similarity | PDF Failed | Name Similarity | Name Failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini-3.1-pro | 0.872 | 0 | 0.884 | 0 | 0.865 | 0 |
| claude-opus-4.6 | 0.904 | 3 | 0.828 | 0 | 0.985 | 7 |
| gpt-5.4 | 0.732 | 0 | 0.720 | 0 | 0.760 | 0 |
| gemini-3.1-flash-lite | 0.670 | 0 | 0.773 | 0 | 0.784 | 0 |
| grok-4.1-fast-reasoning | 0.891 | 5 | N/A | 13 | 0.785 | 1 |
| claude-sonnet-4.6 | 0.817 | 4 | N/A | 0 | N/A | 0 |
| kimi-k2.5 | 0.969 | 10 | N/A | 0 | N/A | 0 |
| gpt-5.4-mini | 0.583 | 0 | N/A | 0 | N/A | 0 |

