# libstruct-bench

Benchmarking agentic systems on sequencing library structure extraction.

This repository now starts with the narrow 1st task: extracting oligo names and sequences from protocol inputs.

## v0 benchmark with LLMs and coding agents

The new v0 baseline evaluates final-library reconstruction as one or more
sequence strings. It reuses the 20 protocol inputs from the oligo benchmark but
expects curated `groundtruth_final_lib_struct.json` files.

- **v0.1:** raw protocol inputs + direct OpenRouter LLM calls.
- **v0.2:** raw protocol inputs + Harbor coding agents.
- **Task:** write `/logs/artifacts/prediction.json` with `protocol_id` and a
  `libraries` array. Multimodal protocols use one entry per final library or
  modality, for example separate RNA and ATAC entries.
- **Source policy:** online search is strictly prohibited. v0.1 and v0.2 must
  use only the attached/downloaded protocol input files, not web search,
  `scg_lib_struct`, benchmark ground truth, prior answer keys, repository
  copies, papers, supplements, or vendor pages.
- **Agent PDF parsing:** generated Harbor tasks install PyMuPDF; agents should
  use PyMuPDF (`import fitz`) or a stronger parser, not `pypdf` or `PyPDF2`.
- **Reward:** strict normalized Levenshtein `sequence_similarity`, averaged
  across ground-truth final-library entries. Missing libraries score zero.
- **Diagnostics:** `matched_sequence_similarity`, `library_recall`,
  `library_precision`, and `library_f1` separate sequence quality from
  modality/library detection.
- **Placeholder policy:** structured oligos and memory can use `[ROLE:LENGTH]`;
  v0 scoring uses non-biological expanded placeholder characters (`#`, `~`,
  `@`, `&`, `=`, `%`, `$`, `?`) in the final string.

The v0 scaffold lives in `benchmarks/library_structure_v0/`. Details are in
`docs/library_structure_v0.md`.


## v1 benchmark with coding agents

- **Task:** extract named oligo sequences from a protocol input.
- **Execution:** one Harbor task per protocol.
- **Data:** raw protocol inputs and ground-truth snapshots live in Hugging Face
  datasets and are pulled at Harbor run time. The raw protocol repo is public;
  the ground-truth repo is verifier-only.
- **Grading:** compare normalized oligo sequences without requiring exact
  `oligo_id` matches.
- **Agent PDF parsing:** generated Harbor tasks install PyMuPDF; agents should
  use PyMuPDF (`import fitz`) or a stronger parser.
- **Reward:** sequence F1 from best one-to-one sequence matching.
- **Diagnostics:** exact match and oligo-name similarity on matched pairs.

Agents must write `/logs/artifacts/prediction.json`. The JSON schema is in `schemas/oligo_extraction_prediction.v1.schema.json`. Detailed metric and normalization rules are in `docs/oligo_extraction_v1.md`.

## Run

Install Harbor separately, then run a generated local task dataset:

```bash
export HF_TOKEN="<token-with-groundtruth-read-access>"
export CODEX_FORCE_AUTH_JSON=1

harbor run \
  -p benchmarks/oligo_extraction_v1/tasks/${protocol} \
  -a codex \
  -m gpt-5.5 \
  --ak reasoning_effort=xhigh \
  --job-name codex-gpt55-xhigh-${protocol}
```

The verifier writes numeric Harbor metrics to `/logs/verifier/reward.json` and
pair-level audit details to `/logs/verifier/matches.json`.



## Prior v0 LLM snapshot

The earlier v0 snapshot evaluated full library-structure reconstruction through
large language model APIs on 13 curated `scg_lib_structs` targets. The current
20-protocol v0 scaffold above supersedes this setup for new baseline runs.

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
