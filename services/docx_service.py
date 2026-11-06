"""Tạo tệp Word đã điền từ mẫu gốc trong templates_word."""

from __future__ import annotations

import logging
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentType
from docx.opc.exceptions import PackageNotFoundError

from services.extraction_service import FIELD_NAMES
from services.form_filler import FormCursor, drop_repeated_copy
from services.form_mappings import FILLERS
from services.template_service import FormTemplate
from utils.text_utils import normalize_citizen_id, normalize_date, normalize_phone, normalize_text

logger = logging.getLogger(__name__)

_DATE_FIELDS = (
    "date_of_birth",
    "citizen_id_issue_date",
    "guardian_date_of_birth",
    "guardian_citizen_id_issue_date",
    "deceased_date_of_birth",
    "deceased_death_date",
    "death_certificate_date",
)
_CITIZEN_ID_FIELDS = ("citizen_id", "guardian_citizen_id", "deceased_citizen_id")
_PHONE_FIELDS = ("phone_number", "guardian_phone", "org_phone")


class DocumentGenerationError(RuntimeError):
    """Lỗi phát sinh khi không thể tạo tệp Word từ mẫu (thiếu mẫu, ghi tệp lỗi...)."""


def prepare_fields(raw_fields: dict) -> dict[str, str]:
    """Chuẩn hoá dữ liệu đã trích xuất/nhập tay; mọi trường đã biết luôn có mặt (rỗng nếu thiếu)."""
    fields = {key: "" for key in FIELD_NAMES}
    for key, value in (raw_fields or {}).items():
        fields[str(key)] = normalize_text("" if value is None else str(value))
    for key in _CITIZEN_ID_FIELDS:
        fields[key] = normalize_citizen_id(fields.get(key))
    for key in _DATE_FIELDS:
        fields[key] = normalize_date(fields.get(key))
    for key in _PHONE_FIELDS:
        fields[key] = normalize_phone(fields.get(key))
    return fields


def fill_document(template: FormTemplate, raw_fields: dict) -> tuple[DocumentType, list[str]]:
    """Mở mẫu Word, điền dữ liệu và trả về (tài liệu, các nhãn không tìm thấy trong mẫu)."""
    if not template.path.is_file():
        raise DocumentGenerationError(f"Không tìm thấy mẫu Word: {template.file_name}")
    filler = FILLERS.get(template.id)
    if filler is None:
        raise DocumentGenerationError(f"Chưa có cách điền cho mẫu: {template.id}")
    document = Document(template.path)
    # Một số tệp mẫu kèm sẵn trang ví dụ đã điền thông tin thật; không đưa vào đơn.
    drop_repeated_copy(document)
    cursor = FormCursor(document)
    filler(cursor, prepare_fields(raw_fields))
    return document, cursor.missing


def generate_document(template: FormTemplate, raw_fields: dict, output_path: str | Path) -> Path:
    """Điền dữ liệu vào mẫu rồi ghi ra tệp .docx tại output_path."""
    try:
        document, missing = fill_document(template, raw_fields)
        if missing:
            # Mẫu đã bị sửa chữ ở các nhãn này: tệp vẫn được tạo, các ô đó để trống.
            logger.warning("Mẫu %s thiếu nhãn: %s", template.file_name, "; ".join(missing))
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        document.save(target)
        return target
    except (OSError, ValueError, KeyError, PackageNotFoundError) as error:
        raise DocumentGenerationError(str(error)) from error
