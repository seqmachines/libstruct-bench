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
