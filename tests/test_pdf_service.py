"""Kiểm thử việc dừng OCR sớm khi PDF nhiều trang đã đủ thông tin cốt lõi."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz

from services.ocr_service import OCRResult
from services.pdf_service import ocr_pdf


def make_blank_pdf_bytes(page_count: int) -> bytes:
    """PDF không có text layer để buộc ocr_pdf đi vào nhánh OCR từng trang."""
    pdf = fitz.open()
    for _ in range(page_count):
        pdf.new_page()
    content = pdf.tobytes()
    pdf.close()
    return content


class OcrPdfEarlyStopTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self._pdf_path = Path(self._tempdir.name) / "sample.pdf"

    def _run_with_canned_pages(self, page_count: int, page_texts: list[str]) -> tuple[OCRResult, int]:
        self._pdf_path.write_bytes(make_blank_pdf_bytes(page_count))
        call_count = 0

        def fake_ocr(image, **kwargs):
            nonlocal call_count
            text = page_texts[call_count] if call_count < len(page_texts) else ""
            call_count += 1
            return OCRResult(text=text, pages=[text])

        with (
            patch("services.pdf_service.detect_languages", return_value=["vie", "eng"]),
            patch("services.pdf_service.ocr_pil_image", side_effect=fake_ocr) as ocr_mock,
        ):
            result = ocr_pdf(str(self._pdf_path), max_pages=8)

        return result, ocr_mock.call_count

    def test_stops_after_core_fields_found_instead_of_scanning_every_page(self):
        page_texts = [
            "HỌ VÀ TÊN: NGUYỄN VĂN AN\nNGÀY SINH: 01/02/1980",
            "SỐ CCCD: 079080001234\nNƠI THƯỜNG TRÚ: 123 Lý Nam Đế, Phường Minh Phụng, TP.HCM",
            "TRANG KHÔNG LIÊN QUAN THỨ 3",
            "TRANG KHÔNG LIÊN QUAN THỨ 4",
        ]

        result, call_count = self._run_with_canned_pages(page_count=4, page_texts=page_texts)

        self.assertEqual(call_count, 2)
        self.assertTrue(any("bỏ qua" in warning for warning in result.warnings))

    def test_never_stops_before_the_minimum_page_floor(self):
        page_texts = [
            "HỌ VÀ TÊN: NGUYỄN VĂN AN\nNGÀY SINH: 01/02/1980\nSỐ CCCD: 079080001234\n"
            "NƠI THƯỜNG TRÚ: 123 Lý Nam Đế, Phường Minh Phụng, TP.HCM",
            "TRANG THỨ 2",
        ]

        _result, call_count = self._run_with_canned_pages(page_count=2, page_texts=page_texts)

        self.assertEqual(call_count, 2)

    def test_scans_every_page_when_core_fields_never_complete(self):
        page_texts = ["TRANG KHÔNG CÓ THÔNG TIN LIÊN QUAN"] * 3

        result, call_count = self._run_with_canned_pages(page_count=3, page_texts=page_texts)

        self.assertEqual(call_count, 3)
        self.assertFalse(any("bỏ qua" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
