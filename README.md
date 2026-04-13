# libstruct-bench

### Benchmarking large language models on sequencing library structure extraction.


Sequencing technology development depends on accurate raw-data QC to catch
protocol-level issues before they compound. The first step — deriving the
library structure (cell barcodes, UMIs, adapters, primers, linkers, and their
order) from a heterogeneous experimental protocol — is still a manual
bottleneck that requires substantial domain expertise.

Frontier LLMs are a natural candidate for automating this step, but in
practice even state-of-the-art models fail to reliably reconstruct correct
library structures or capture the biochemical logic behind them. This
benchmark exists to quantify that gap and to separate genuine protocol
comprehension from memorization.

## Approach

- **Ground truth.** 13 library structures curated from
  [scg_lib_structs](https://teichlab.github.io/scg_lib_structs/), generated
  with LLM-assisted parsing and validated against source protocols. Some protocol PDFs may got trimmed for lighter and cleaner loading.
- **Harness.** Each protocol is submitted to a model through the [cDNA API](https://github.com/seqmachines/cdna)
  `/api/benchmark` endpoint in one of three input modes:
  - `pdf` — the raw protocol PDF
  - `text` — pre-extracted text from the PDF
  - `name` — the assay name only
- **Task.** The model returns the full library as a single 5′→3′ sequence
  string using a symbol alphabet for functional elements (barcodes, UMIs,
  adapters, primers, ligation regions).
- **Scoring.** Normalized Levenshtein similarity against ground truth:
  `1 − d(ŝ, s) / max(|ŝ|, |s|)`. Segment-level and region-stratified scores
  are also recorded (`known`, `barcode`, `umi`, `index`, `ligation`,
  `rt_barcode`, `tn5_barcode`, `linker`, `capture`).

## Results

Nine frontier LLMs across five provider families were evaluated, all models only ran once for each protocol and input mode.

Winner: **Gemini 3.1 Pro** (April 2026)


| Model | Text Similarity | Text Failed | PDF Similarity | PDF Failed | Name Similarity | Name Failed |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| gemini-3.1-pro | 0.872 | 0 | 0.884 | 0 | 0.865 | 0 |
| claude-opus-4.6 | 0.904 | 3 | 0.828 | 0 | 0.985 | 7 |
| gpt-5.4 | 0.732 | 0 | 0.720 | 0 | 0.760 | 0 |
| gemini-3.1-flash-lite | 0.670 | 0 | 0.773 | 0 | 0.784 | 0 |
| grok-4.1-fast-reasoning | 0.891 | 5 | — | 13 | 0.785 | 1 |
| claude-sonnet-4.6 | 0.817 | 4 | — | 0 | — | 0 |
| kimi-k2.5 | 0.969 | 10 | — | 0 | — | 0 |
| gpt-5.4-mini | 0.583 | 0 | — | 0 | — | 0 |

**Takeaway.** Benchmark performance depends strongly on both assay identity
and prompting mode. Reaching the zero-edit-distance accuracy that downstream
QC automation needs will require dedicated agent fine-tuning and
specialized training data, not just improvements in general-purpose LLMs.

## Repository layout

```
protocols/           Curated ground-truth JSON, one file per assay
protocol_sources/    Downloaded source PDFs
protocol_texts/      Text extracted from PDFs (for text-mode benchmarking)
parsing_scripts/     CLI tools: download, OCR, PDF→text, benchmark runner
models.example.json  Starter model configuration
```

Each `protocols/*.json` can reference both a remote source and a local PDF:

```json
{
  "references": {
    "protocol_link": "https://example.com/reference.pdf",
    "protocol_pdf": "protocol_sources/example.pdf"
  }
}
```

## Setup

First set up [cDNA](https://github.com/seqmachines/cdna) and get it running at http://localhost:3000. For now you'll need to use your own API keys for LLMs, configure them in the `.env.local` file.


Configure models in `models.json` (start from `models.example.json`):

```json
{
  "models": [
    {
      "name": "gemini-3.1-pro",
      "model": "google/gemini-3.1-pro-preview",
      "api": "http://localhost:3000/api/benchmark"
    }
  ]
}
```

Run across all curated protocols:

```bash
python parsing_scripts/benchmark_protocol_parsing.py \
  --models models.example.json
```


## Acknowledgements

Ground-truth library structures are derived from the
[scg_lib_structs](https://teichlab.github.io/scg_lib_structs/) reference
collection.
