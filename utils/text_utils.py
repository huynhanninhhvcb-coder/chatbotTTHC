"""Các hàm chuẩn hóa nhỏ, dùng chung cho OCR và kiểm tra dữ liệu."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(value: str | None) -> str:
    """Chuẩn hóa văn bản OCR: đưa về dạng Unicode NFC, gộp khoảng trắng thừa và bỏ khoảng trắng đầu/cuối dòng."""
    value = unicodedata.normalize("NFC", value or "")
    # Thay ký tự non-breaking space (U+00A0) bằng space thường vì một
    # số ảnh scan/PDF chèn ký tự này mà mắt thường không phân biệt được với khoảng trắng.
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt, giữ nguyên chữ hoa/thường ("Đường" -> "Duong").

    Chữ "đ/Đ" là ký tự riêng (NFD không tách thành "d" + dấu) nên phải đổi
    riêng. Với văn bản dựng sẵn (NFC), mỗi ký tự có dấu thành đúng một ký tự
    không dấu nên độ dài chuỗi không đổi: vị trí tìm được trên chuỗi không dấu
    dùng được để cắt lại giá trị có dấu từ chuỗi gốc.
    """
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(character for character in decomposed if unicodedata.category(character) != "Mn")
    return without_marks.replace("đ", "d").replace("Đ", "D")


def normalize_citizen_id(value: str | None) -> str:
    """Chỉ giữ lại chữ số, dùng để chuẩn hóa số CCCD/CMND đọc được từ OCR."""
    return re.sub(r"\D", "", value or "")


def normalize_phone(value: str | None) -> str:
    """Chuẩn hóa số điện thoại về dạng nội địa (bắt đầu bằng 0)."""
    digits = re.sub(r"\D", "", value or "")
    # Số dạng mã quốc gia 84xxxxxxxxx (11 chữ số, không có dấu +) được quy về
    # dạng nội địa 0xxxxxxxxx để khớp định dạng người dùng quen dùng trên đơn.
    if digits.startswith("84") and len(digits) == 11:
        return f"0{digits[2:]}"
    return digits


def normalize_bank_account_number(value: str | None) -> str:
    """Bỏ khoảng trắng và dấu phân tách thường gặp trong số tài khoản.

    Không xóa chữ cái hay ký tự lạ: tầng kiểm tra dữ liệu cần nhận ra chúng
    thay vì âm thầm biến một số tài khoản sai thành số hợp lệ.
    """
    return re.sub(r"[\s.\-]", "", normalize_text(value))


def normalize_date(value: str | None) -> str:
    """Đưa ngày dạng d/m/yyyy hoặc d-m-yyyy về dd/mm/yyyy nếu hợp lệ sơ bộ."""
    value = normalize_text(value).replace("-", "/").replace(".", "/")
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", value)
    if not match:
        return value
    day, month, year = match.groups()
    if len(year) == 2:
        # Năm 2 chữ số: mốc 30 chỉ là ước lượng để suy ra thế kỷ (vd. "95" ->
        # 1995, "05" -> 2005); không có cách nào chắc chắn 100% từ 2 chữ số.
        year = f"19{year}" if int(year) > 30 else f"20{year}"
    return f"{int(day):02d}/{int(month):02d}/{year}"
