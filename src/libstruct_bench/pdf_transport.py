"""Optional, verified PDF transport copies for providers with body-size limits."""

from __future__ import annotations

import hashlib
import math
from typing import Any


def optimize_pdf(data: bytes, *, image_dpi: int | None = None) -> tuple[bytes, dict[str, Any]]:
    """Compress streams, discard private editing data, optionally resize images.

    Source PDFs are never modified. This is not byte-identical normalization:
    Illustrator's private editing representation is removed, while the PDF page
    content, images, fonts, text, annotations and embedded attachments remain.
    Without image_dpi, require identical content streams and rendered pixels.
    Explicit image resizing instead verifies text/positions and bounded visual
    differences. It preserves vector text and encodes resized images losslessly.
    """
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise RuntimeError("--optimize-pdfs requires PyMuPDF: python -m pip install pymupdf") from exc
    if image_dpi is not None and (type(image_dpi) is not int or image_dpi <= 0):
        raise ValueError("image_dpi must be a positive integer")

    report: dict[str, Any] = {
        "method": "deflate_and_remove_page_illustrator_editing_data",
        "pymupdf_version": fitz.VersionBind,
        "source_size_bytes": len(data),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "render_check_dpi": 100,
        "render_check": "pixel_identical" if image_dpi is None else "mean_absolute_pixel_difference",
    }
    with fitz.open(stream=data, filetype="pdf") as source, \
            fitz.open(stream=data, filetype="pdf") as working:
        if source.is_encrypted:
            raise ValueError("Cannot optimize an encrypted PDF")
        removed_pages = []
        for page in working:
            if working.xref_get_key(page.xref, "PieceInfo/Illustrator")[0] != "null":
                working.xref_set_key(page.xref, "PieceInfo/Illustrator", "null")
                removed_pages.append(page.number + 1)
        if image_dpi is not None:
            report["method"] += "_and_resample_images"
            if not hasattr(working, "rewrite_images"):
                raise RuntimeError("--pdf-image-dpi requires a newer PyMuPDF: python -m pip install -U pymupdf")
            threshold = math.ceil(image_dpi * 1.125)
            options = fitz.mupdf.PdfImageRewriterOptions()
            for prefix in ("color_lossless", "color_lossy", "gray_lossless", "gray_lossy"):
                setattr(options, prefix + "_image_recompress_method", fitz.mupdf.FZ_RECOMPRESS_LOSSLESS)
                setattr(options, prefix + "_image_subsample_method", fitz.mupdf.FZ_SUBSAMPLE_BICUBIC)
                setattr(options, prefix + "_image_subsample_to", image_dpi)
                setattr(options, prefix + "_image_subsample_threshold", threshold)
            working.rewrite_images(options=options)
            report["image_resampling"] = {
                "target_dpi": image_dpi, "threshold_dpi": threshold,
                "interpolation": "bicubic", "recompression": "lossless",
                "bitonal_images": "unchanged", "page_rasterization": False,
            }
        candidate = working.tobytes(
            garbage=4, deflate=True, deflate_images=True, deflate_fonts=True,
            no_new_id=True,
        )
        with fitz.open(stream=candidate, filetype="pdf") as result:
            if len(source) != len(result):
                raise ValueError("PDF optimization changed the page count")
            if source.embfile_names() != result.embfile_names() or any(
                source.embfile_get(name) != result.embfile_get(name)
                for name in source.embfile_names()
            ):
                raise ValueError("PDF optimization changed embedded attachments")
            page_pixel_errors = []
            for index in range(len(source)):
                before, after = source[index], result[index]
                if (
                    before.rect != after.rect
                    or before.rotation != after.rotation
                    or (image_dpi is None and before.read_contents() != after.read_contents())
                    or before.get_text() != after.get_text()
                ):
                    raise ValueError(f"PDF optimization changed content on page {index + 1}")
                if image_dpi is not None:
                    original_words, new_words = before.get_text("words"), after.get_text("words")
                    if len(original_words) != len(new_words) or any(
                        a[4] != b[4] or any(abs(x - y) > 0.05 for x, y in zip(a[:4], b[:4]))
                        for a, b in zip(original_words, new_words)
                    ):
                        raise ValueError(f"PDF optimization changed text positions on page {index + 1}")
                a, b = before.get_pixmap(dpi=100), after.get_pixmap(dpi=100)
                if (a.width, a.height, a.n) != (b.width, b.height, b.n):
                    raise ValueError(f"PDF optimization changed rendering on page {index + 1}")
                original_pixels, new_pixels = a.samples, b.samples
                if image_dpi is None:
                    if original_pixels != new_pixels:
                        raise ValueError(f"PDF optimization changed rendering on page {index + 1}")
                else:
                    error = sum(abs(x - y) for x, y in zip(original_pixels, new_pixels)) / len(original_pixels)
                    page_pixel_errors.append(error)
                    # On the 0..255 channel scale, catch large appearance changes;
                    # exact equality is impossible after authorized downsampling.
                    if error > 2.0:
                        raise ValueError(f"PDF optimization changed rendering excessively on page {index + 1}: {error:.3f}")
            if image_dpi is not None:
                report["page_mean_absolute_pixel_errors"] = page_pixel_errors
                report["max_mean_absolute_pixel_error"] = max(page_pixel_errors, default=0.0)
                report["text_position_tolerance_points"] = 0.05
            report["pages_verified"] = len(source)
            report["illustrator_editing_data_removed_from_pages"] = removed_pages
    used = len(candidate) < len(data)
    output = candidate if used else data
    report.update({
        "applied": used,
        "size_bytes": len(output),
        "sha256": hashlib.sha256(output).hexdigest(),
    })
    return output, report
