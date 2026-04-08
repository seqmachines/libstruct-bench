#!/usr/bin/env python3
"""
Run OCR over local protocol PDFs with docTR and write one text file per PDF.

This runner is intended to work on Apple Silicon through PyTorch MPS, which is
documented by docTR. It keeps the same basic workflow as the previous OCR
script: read PDFs from protocol_sources/ and write plain text files to
protocol_texts/.

Typical usage:
    python3 run_ocr.py
    python3 run_ocr.py --file protocol_sources/petri-seq.pdf --overwrite
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


os.environ.setdefault("USE_TORCH", "1")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run docTR OCR on every PDF in a directory and write .txt output."
    )
    parser.add_argument(
        "--input-dir",
        default="protocol_sources",
        help="Directory containing source PDFs.",
    )
    parser.add_argument(
        "--output-dir",
        default="protocol_texts",
        help="Directory where OCR text files are written.",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Process a single PDF instead of scanning the input directory.",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "mps", "cuda", "cpu"),
        default="auto",
        help="Torch device to use for OCR.",
    )
    parser.add_argument(
        "--det-arch",
        default="db_resnet50",
        help="docTR detection architecture.",
    )
    parser.add_argument(
        "--reco-arch",
        default="crnn_vgg16_bn",
        help="docTR recognition architecture.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .txt outputs.",
    )
    return parser.parse_args()


def require_module(module_name: str, package_hint: str):
    try:
        return __import__(module_name)
    except ImportError as exc:
        raise SystemExit(
            f"Missing dependency '{module_name}'. Install it with: {package_hint}"
        ) from exc


def resolve_pdfs(args: argparse.Namespace) -> list[Path]:
    if args.file:
        pdf_path = Path(args.file)
        if not pdf_path.exists():
            raise SystemExit(f"File not found: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise SystemExit(f"Expected a PDF file, got: {pdf_path}")
        return [pdf_path]

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        raise SystemExit(f"Input directory not found: {input_dir}")

    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDFs found in {input_dir}/")
    return pdfs


def pick_device(requested: str, torch) -> str:
    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    if requested == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA requested but torch.cuda.is_available() is false.")
    if requested == "mps" and not torch.backends.mps.is_available():
        raise SystemExit("MPS requested but torch.backends.mps.is_available() is false.")
    return requested


def load_predictor(args: argparse.Namespace):
    torch = require_module("torch", "pip install torch torchvision")
    require_module("doctr", "pip install 'python-doctr[torch]' pypdfium2")
    from doctr.models import ocr_predictor

    device = pick_device(args.device, torch)
    print(
        f"Loading docTR on {device} with det_arch={args.det_arch} "
        f"reco_arch={args.reco_arch}"
    )
    predictor = ocr_predictor(
        det_arch=args.det_arch,
        reco_arch=args.reco_arch,
        pretrained=True,
    ).to(torch.device(device))

    return torch, predictor, device


def process_pdf(pdf_path: Path, output_dir: Path, predictor, args: argparse.Namespace) -> tuple[bool, str]:
    output_path = output_dir / f"{pdf_path.stem}.txt"
    if output_path.exists() and not args.overwrite:
        return False, f"{pdf_path.name}: {output_path.name} already exists"

    from doctr.io import DocumentFile

    print(f"{pdf_path.name}: loading PDF")
    doc = DocumentFile.from_pdf(str(pdf_path))
    print(f"{pdf_path.name}: OCRing {len(doc)} page(s)")
    result = predictor(doc)
    text = result.render().strip()

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + ("\n" if text else ""), encoding="utf-8")
    chars = len(text)
    return True, f"{pdf_path.name}: wrote {output_path.name} ({chars:,} chars)"


def main() -> int:
    args = parse_args()
    pdfs = resolve_pdfs(args)
    _, predictor, device = load_predictor(args)

    if device != "mps":
        print(
            f"Warning: running on device={device}. Apple Silicon users typically want mps.",
            file=sys.stderr,
        )

    output_dir = Path(args.output_dir)
    converted = 0
    skipped = 0
    failed = 0

    for pdf_path in pdfs:
        try:
            did_convert, message = process_pdf(pdf_path, output_dir, predictor, args)
            print(message)
            if did_convert:
                converted += 1
            else:
                skipped += 1
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # pragma: no cover - runtime integration path
            failed += 1
            print(f"{pdf_path.name}: OCR failed ({exc})", file=sys.stderr)

    print(
        f"Summary: converted={converted} skipped={skipped} failed={failed}",
        file=sys.stderr if failed else sys.stdout,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
