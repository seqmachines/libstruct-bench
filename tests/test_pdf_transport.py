import hashlib
import unittest
from unittest.mock import patch

from libstruct_bench.pdf_transport import optimize_pdf

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


@unittest.skipIf(fitz is None, "PyMuPDF is optional")
class PdfTransportTests(unittest.TestCase):
    def source(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), "Protocol sequence: ACGT")
            page.draw_rect(fitz.Rect(72, 100, 200, 200), color=(1, 0, 0), fill=(0, 1, 0))
            private = doc.get_new_xref()
            doc.update_object(private, "<<>>")
            doc.update_stream(private, bytes(range(256)) * 1000, compress=False)
            doc.xref_set_key(page.xref, "PieceInfo", f"<< /Illustrator << /Private {private} 0 R >> /Other << /Keep true >> >>")
            doc.embfile_add("supporting.txt", b"keep all attachments")
            return doc.tobytes()

    def test_optimization_preserves_pages_attachments_and_other_metadata(self):
        data = self.source()
        output, report = optimize_pdf(data)
        self.assertLess(len(output), len(data))
        self.assertTrue(report["applied"])
        self.assertEqual(report["pages_verified"], 1)
        self.assertEqual(report["illustrator_editing_data_removed_from_pages"], [1])
        self.assertEqual(report["source_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(report["sha256"], hashlib.sha256(output).hexdigest())
        with fitz.open(stream=output, filetype="pdf") as doc:
            self.assertEqual(doc.embfile_get("supporting.txt"), b"keep all attachments")
            self.assertEqual(doc.xref_get_key(doc[0].xref, "PieceInfo/Other/Keep"), ("bool", "true"))
            self.assertEqual(doc.xref_get_key(doc[0].xref, "PieceInfo/Illustrator")[0], "null")

    def test_verification_rejects_changed_page_content(self):
        source = self.source()
        with fitz.open() as changed:
            changed.new_page().insert_text((72, 72), "Changed text")
            changed.embfile_add("supporting.txt", b"keep all attachments")
            bad_output = changed.tobytes()
        with patch.object(fitz.Document, "tobytes", return_value=bad_output):
            with self.assertRaisesRegex(ValueError, "changed content"):
                optimize_pdf(source)

    def test_verification_rejects_removed_attachment(self):
        source = self.source()
        with fitz.open(stream=source, filetype="pdf") as changed:
            changed.embfile_del("supporting.txt")
            bad_output = changed.tobytes()
        with patch.object(fitz.Document, "tobytes", return_value=bad_output):
            with self.assertRaisesRegex(ValueError, "changed embedded attachments"):
                optimize_pdf(source)

    def test_verification_rejects_visual_change_with_same_page_text(self):
        source = self.source()
        with fitz.open(stream=source, filetype="pdf") as changed:
            changed[0].add_rect_annot(fitz.Rect(20, 20, 100, 100))
            bad_output = changed.tobytes()
        with patch.object(fitz.Document, "tobytes", return_value=bad_output):
            with self.assertRaisesRegex(ValueError, "changed rendering"):
                optimize_pdf(source)

    def test_explicit_resampling_reduces_image_resolution_preserving_text(self):
        with fitz.open(stream=self.source(), filetype="pdf") as doc:
            width = 1200
            row = bytes(channel for x in range(width) for channel in (x // 5, 120, 80))
            pixmap = fitz.Pixmap(fitz.csRGB, width, width, row * width, False)
            doc[0].insert_image(fitz.Rect(72, 250, 216, 394), pixmap=pixmap)
            source = doc.tobytes()
        output, report = optimize_pdf(source, image_dpi=200)
        self.assertLess(len(output), len(source))
        self.assertEqual(report["image_resampling"]["target_dpi"], 200)
        self.assertFalse(report["image_resampling"]["page_rasterization"])
        self.assertLessEqual(report["max_mean_absolute_pixel_error"], 2.0)
        with fitz.open(stream=source, filetype="pdf") as before, \
                fitz.open(stream=output, filetype="pdf") as after:
            self.assertEqual(before[0].get_text(), after[0].get_text())
            self.assertEqual(before[0].get_text("words"), after[0].get_text("words"))
            self.assertEqual(after.embfile_get("supporting.txt"), b"keep all attachments")
            images = after[0].get_images()
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0][2:4], (400, 400))

    def test_resampling_verification_rejects_moved_text(self):
        source = self.source()
        with fitz.open(stream=source, filetype="pdf") as changed:
            page = changed[0]
            content = changed.get_new_xref()
            changed.update_object(content, "<<>>")
            changed.update_stream(content, b"q 1 0 0 1 1 0 cm\n" + page.read_contents() + b"\nQ")
            page.set_contents(content)
            bad_output = changed.tobytes()
        with patch.object(fitz.Document, "tobytes", return_value=bad_output):
            with self.assertRaisesRegex(ValueError, "changed text positions"):
                optimize_pdf(source, image_dpi=200)

    def test_resampling_verification_rejects_large_visual_change(self):
        source = self.source()
        with fitz.open(stream=source, filetype="pdf") as changed:
            changed[0].draw_rect(fitz.Rect(0, 200, 400, 700), fill=(0, 0, 0))
            bad_output = changed.tobytes()
        with patch.object(fitz.Document, "tobytes", return_value=bad_output):
            with self.assertRaisesRegex(ValueError, "changed rendering excessively"):
                optimize_pdf(source, image_dpi=200)
