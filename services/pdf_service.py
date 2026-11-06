"""Đọc text layer của PDF trước; chỉ OCR các trang PDF quét khi cần."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
from PIL import Image

from services.extraction_service import extract_personal_information
from services.ocr_service import OCRResult, detect_languages, ocr_pil_image


class PDFProcessingError(RuntimeError):
    """Lỗi xảy ra khi đọc hoặc xử lý tệp PDF thất bại."""


# Mọi mẫu đơn đều cần đủ 3 trường này cộng ít nhất một địa chỉ của người đề
# nghị. Một khi đã có đủ, các trang sau (khai tử, sổ ngân hàng...) không còn
# cản việc dừng OCR sớm ở đây; người dùng vẫn được cảnh báo để tải riêng
# giấy tờ đó thành tệp khác nếu hồ sơ thực sự còn.
_CORE_IDENTITY_FIELDS = ("full_name", "date_of_birth", "citizen_id")
_CORE_ADDRESS_FIELDS = ("residence_address", "contact_address")
# Luôn OCR tối thiểu 2 trang trước khi xét dừng sớm: mặt trước/sau CCCD
# thường nằm ở hai trang liên tiếp và một trang đơn lẻ hiếm khi đủ trường.
_MIN_PAGES_BEFORE_EARLY_STOP = 2


def _has_core_identity_fields(text: str, pages: list[str], mrz_texts: list[str]) -> bool:
    """Kiểm tra đã trích xuất đủ các trường định danh cốt lõi và ít nhất một địa chỉ hay chưa, dùng để quyết định dừng OCR sớm."""
    fields = extract_personal_information(text, page_texts=pages, cccd_mrz_texts=mrz_texts).fields
    if any(not fields.get(name) for name in _CORE_IDENTITY_FIELDS):
        return False
    return any(fields.get(name) for name in _CORE_ADDRESS_FIELDS)


def _combine_pages(pages: list[str]) -> str:
    """Ghép các trang thành một chuỗi văn bản duy nhất, có đánh số thứ tự từng trang."""
    return "\n\n".join(f"--- TRANG {index + 1} ---\n{page}" for index, page in enumerate(pages) if page)


def extract_pdf_pages(file_path: str | Path, max_pages: int) -> list[str]:
    """Trích xuất text layer có sẵn của từng trang PDF (không OCR), dùng cho PDF dạng văn bản gốc."""
    try:
        with fitz.open(file_path) as document:
            if document.page_count == 0:
                raise PDFProcessingError("PDF không có trang.")
            page_count = min(document.page_count, max_pages)
            return [document[index].get_text("text").strip() for index in range(page_count)]
    except fitz.FileDataError as error:
        raise PDFProcessingError("Tệp PDF không hợp lệ hoặc được bảo vệ.") from error


def ocr_pdf(
    file_path: str | Path,
    max_pages: int,
    tesseract_cmd: str | None = None,
    max_workers: int = 5,
    target_width: int = 1600,
    preferred_language: str | None = None,
    fast_mode: bool = False,
) -> OCRResult:
    """OCR từng trang PDF thành ảnh khi PDF là bản quét, dừng sớm nếu đã đủ thông tin cốt lõi của người đề nghị."""
    try:
        # Tra cứu gói ngôn ngữ một lần cho cả tệp: mỗi lần gọi tốn 150 ms-1 s
        # (đo thực tế trên Windows), nên để mỗi trang tự tra lại sẽ nhân chi
        # phí này lên tới `max_pages` lần một cách vô ích.
        languages = detect_languages(tesseract_cmd)
        pages: list[str] = []
        warnings: list[str] = []
        mrz_texts: list[str] = []
        with fitz.open(file_path) as document:
            page_limit = min(document.page_count, max_pages)
            stopped_early_at: int | None = None
            for index in range(page_limit):
                page = document.load_page(index)
                # Render trang PDF thành ảnh bitmap với hệ số phóng 2x để tăng
                # độ phân giải, giúp Tesseract nhận dạng chữ chính xác hơn.
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                result = ocr_pil_image(
                    image,
                    languages=languages,
                    max_workers=max_workers,
                    target_width=target_width,
                    preferred_language=preferred_language,
                    fast_mode=fast_mode,
                )
                # Một trang CCCD có thể có thêm vùng OCR bố cục/MRZ để parser
                # ưu tiên thông tin rõ hơn; vẫn chỉ lặp tối đa số trang PDF cho phép.
                pages.extend(result.pages or [result.text])
                warnings.extend(result.warnings)
                mrz_texts.extend(result.mrz_texts)

                scanned = index + 1
                if (
                    page_limit - scanned > 0
                    and scanned >= _MIN_PAGES_BEFORE_EARLY_STOP
                    and _has_core_identity_fields(_combine_pages(pages), pages, mrz_texts)
                ):
                    # Hồ sơ nộp thường là một CCCD/phiếu dân cư quét thành PDF
                    # nhiều trang; một khi đã đọc đủ thông tin cốt lõi của
                    # người đề nghị, các trang còn lại (thường là mặt sau lặp
                    # lại, trang trắng...) không còn xứng đáng với thời gian
                    # OCR trên CPU hạn chế của môi trường triển khai.
                    stopped_early_at = scanned
                    break
            if stopped_early_at is not None:
                warnings.append(
                    f"Đã đủ thông tin cơ bản sau {stopped_early_at}/{page_limit} trang đầu nên bỏ qua các trang "
                    "còn lại để xử lý nhanh hơn. Nếu hồ sơ còn giấy tờ khác (khai tử, sổ ngân hàng...) ở trang "
                    "sau, hãy tải trang đó thành tệp riêng."
                )
            elif document.page_count > max_pages:
                warnings.append(f"Chỉ OCR {max_pages}/{document.page_count} trang đầu của PDF.")
        return OCRResult(
            text=_combine_pages(pages),
            warnings=list(dict.fromkeys(warnings)),
            source="pdf_ocr",
            pages=pages,
            mrz_texts=mrz_texts,
        )
    except fitz.FileDataError as error:
        raise PDFProcessingError("Tệp PDF không hợp lệ hoặc được bảo vệ.") from error


def process_pdf(
    file_path: str | Path,
    max_pages: int,
    tesseract_cmd: str | None = None,
    max_workers: int = 5,
    target_width: int = 1600,
    preferred_language: str | None = None,
    fast_mode: bool = False,
) -> OCRResult:
    """Xử lý PDF: ưu tiên đọc text layer có sẵn, chỉ chuyển sang OCR ảnh khi text trích xuất được quá ít (nghi là PDF quét)."""
    pages = extract_pdf_pages(file_path, max_pages=max_pages)
    text = _combine_pages(pages)
    # Coi là PDF có text layer hợp lệ nếu trích xuất được ít nhất khoảng 30 ký
    # tự không phải khoảng trắng; ít hơn thì nghi là PDF ảnh quét và cần OCR.
    if len(text.replace(" ", "")) >= 30:
        try:
            with fitz.open(file_path) as document:
                warnings = [f"PDF có nhiều trang; chỉ dùng {max_pages}/{document.page_count} trang đầu để trích xuất."] if document.page_count > max_pages else []
        except fitz.FileDataError:
            warnings = []
        return OCRResult(text=text, warnings=warnings, source="pdf_text", pages=pages)
    return ocr_pdf(
        file_path,
        max_pages=max_pages,
        tesseract_cmd=tesseract_cmd,
        max_workers=max_workers,
        target_width=target_width,
        preferred_language=preferred_language,
        fast_mode=fast_mode,
    )
