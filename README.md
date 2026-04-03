# librarystructdb

This repo stores protocol JSON files in `protocols/` and a small CLI to download the source document referenced by each protocol.

Each protocol JSON now keeps only:

```json
{
  "references": {
    "protocol_pdf": "https://example.com/reference"
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

## Notes

- Files without a `protocol_pdf` URL are skipped.
- Non-protocol JSON files in `protocols/` are ignored.
- The `protocol_pdf` field now stores the best single source link for the oligo details. Some links are PDFs and some are HTML method pages.
- The downloader infers the output file extension from the response and leaves no partial file behind on failure.
