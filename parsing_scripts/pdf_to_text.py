"""
Convert protocol PDFs to text files for text-only LLM benchmarking.

Usage:
    python src/pdf_to_text.py
    python src/pdf_to_text.py --input-dir protocol_sources --output-dir protocol_texts
    python src/pdf_to_text.py --file protocol_sources/10x-chromium-3prime-v4.pdf
"""

import argparse
from pathlib import Path

import pymupdf  # PyMuPDF


def pdf_to_text(pdf_path: Path) -> str:
    """Extract text from a PDF file, page by page."""
    doc = pymupdf.open(str(pdf_path))
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            pages.append(f"--- Page {i + 1} ---\n{text}")
    doc.close()
    return "\n\n".join(pages)


def convert_file(pdf_path: Path, output_dir: Path) -> Path:
    """Convert a single PDF to text and save."""
    text = pdf_to_text(pdf_path)
    output_path = output_dir / f"{pdf_path.stem}.txt"
    output_path.write_text(text, encoding="utf-8")
    chars = len(text)
    lines = text.count("\n")
    print(f"  {pdf_path.name} → {output_path.name} ({chars:,} chars, {lines:,} lines)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Convert protocol PDFs to text")
    parser.add_argument("--input-dir", default="protocol_sources", help="Directory with PDFs")
    parser.add_argument("--output-dir", default="protocol_texts", help="Output directory for text files")
    parser.add_argument("--file", default=None, help="Convert a single PDF file")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    if args.file:
        pdf_path = Path(args.file)
        if not pdf_path.exists():
            print(f"File not found: {pdf_path}")
            return
        convert_file(pdf_path, output_dir)
        return

    input_dir = Path(args.input_dir)
    pdfs = sorted(input_dir.glob("*.pdf"))

    if not pdfs:
        print(f"No PDFs found in {input_dir}/")
        return

    print(f"Converting {len(pdfs)} PDFs → {output_dir}/\n")

    for pdf_path in pdfs:
        convert_file(pdf_path, output_dir)

    print(f"\nDone. {len(pdfs)} files converted.")


if __name__ == "__main__":
    main()

