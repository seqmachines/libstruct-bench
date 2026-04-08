# librarystructdb

This repo stores protocol JSON files in `protocols/` and a small CLI to download the source document referenced by each protocol.

Protocol references can separate the remote source URL from the local cached PDF path:

```json
{
  "references": {
    "protocol_link": "https://example.com/reference.pdf",
    "protocol_pdf": "protocol_sources/example.pdf"
  }
}
```

## Usage

Run the downloader against the default `protocols/` directory:

```bash
python3 download_protocol_pdfs.py
```

Files are written to `protocol_sources/`.

Useful flags:

```bash
python3 download_protocol_pdfs.py --overwrite
python3 download_protocol_pdfs.py --protocol-dir protocols --output-dir protocol_sources
```

Run OCR over local PDFs with docTR:

```bash
python3 run_ocr.py
python3 run_ocr.py --file protocol_sources/petri-seq.pdf --overwrite
```

Install the Python dependencies:

```bash
uv pip install --python ../.venv/bin/python "python-doctr[torch]" pypdfium2 torchvision
```

On Apple Silicon, use Metal via PyTorch MPS:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=1 ../.venv/bin/python run_ocr.py --device mps
```

## Benchmarking cDNA extraction

`parsing_scripts/benchmark_protocol_parsing.py` benchmarks the cDNA `/api/benchmark`
endpoint against the curated protocol JSON files in `protocols/`.

The runner uses the protocol PDF by default, sends each protocol to the configured
benchmark API for every model in `models.json`, then scores:

- full `library_sequence` reconstruction
- ordered segment recovery
- region-stratified performance for `known`, `barcode`, `umi`, `index`,
  `ligation`, `rt_barcode`, `tn5_barcode`, `linker`, and `capture`

Example `models.json`:

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

You can start from `models.example.json`.

Run the benchmark from `librarystructdb/`:

```bash
../.venv/bin/python parsing_scripts/benchmark_protocol_parsing.py \
  --models models.example.json
```

Useful flags:

```bash
../.venv/bin/python parsing_scripts/benchmark_protocol_parsing.py \
  --models models.example.json \
  --protocol 10x-chromium-3prime-v3 \
  --protocol split-seq

../.venv/bin/python parsing_scripts/benchmark_protocol_parsing.py \
  --models models.example.json \
  --input-mode auto \
  --limit 5 \
  --include-raw
```

Outputs are written to `benchmark_results/`:

- `benchmark-<timestamp>.json`: full run payload, predictions, and summaries
- `benchmark-runs-<timestamp>.csv`: one row per model x protocol run
- `benchmark-regions-<timestamp>.csv`: one row per ground-truth segment comparison

`--input-mode pdf` is the default and is the mode to use when you want to measure
LLM reconstruction directly from protocol PDFs. `auto` falls back to OCR/text/URL
only when needed.

## Notes

- Files without a `protocol_link` URL are skipped.
- Non-protocol JSON files in `protocols/` are ignored.
- `protocol_link` stores the remote source URL.
- `protocol_pdf` stores the local PDF path when a PDF has already been downloaded or curated.
- Legacy records that still store a URL in `protocol_pdf` remain supported by the downloader.
- The downloader infers the output file extension from the response and leaves no partial file behind on failure.
- `run_ocr.py` now uses docTR instead of DeepSeek-OCR because docTR documents Apple Silicon MPS support and works directly with PDFs.
- The script reads PDFs with `DocumentFile.from_pdf(...)` and writes plain text using `result.render()`.
- `PYTORCH_ENABLE_MPS_FALLBACK=1` is still recommended on Apple Silicon in case individual ops fall back to CPU.
