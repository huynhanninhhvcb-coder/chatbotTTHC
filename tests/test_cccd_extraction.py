"""Hồi quy cho việc trích xuất thông tin từ CCCD Việt Nam.

Các fixture dưới đây được tạo hoàn toàn trong mã kiểm thử. Chúng không chứa ảnh,
tên, số CCCD, hay địa chỉ của người dùng thực tế.
"""

from __future__ import annotations

import unittest

from services.extraction_service import extract_personal_information


MRZ_WEIGHTS = (7, 3, 1)


def mrz_check(value: str) -> str:
    """Tính chữ số kiểm tra ICAO 9303 với trọng số 7-3-1."""
    total = 0
    for index, character in enumerate(value):
        if character.isdigit():
            numeric_value = int(character)
        elif "A" <= character <= "Z":
            numeric_value = ord(character) - ord("A") + 10
        elif character == "<":
            numeric_value = 0
        else:
            raise ValueError(f"Ký tự MRZ không hợp lệ: {character!r}")
        total += numeric_value * MRZ_WEIGHTS[index % len(MRZ_WEIGHTS)]
    return str(total % 10)


def make_valid_cccd_mrz(
    citizen_id: str = "001123456789",
    birth: str = "910403",
    sex: str = "F",
    expiry: str = "350403",
    name: str = "TEST<<CONG<DAN",
) -> str:
    """Tạo MRZ TD1 3 dòng, đúng schema CCCD và có toàn bộ checksum hợp lệ.

    ``citizen_id`` là định danh giả: mã tỉnh ``001`` cùng chữ số giới tính/thế
    kỷ ``1`` (nữ, sinh trong giai đoạn 1900--1999), khớp với ngày sinh mẫu
    1991-04-03.
    """
    if len(citizen_id) != 12 or not citizen_id.isdigit():
        raise ValueError("CCCD fixture phải có đúng 12 chữ số.")
    if len(birth) != 6 or len(expiry) != 6 or sex not in {"M", "F"}:
        raise ValueError("Dữ liệu MRZ fixture không đúng định dạng.")

    document_number = "123456789"  # Số chứng từ giả, không phải CCCD.
    optional_data = f"{citizen_id}<<"
    line_1 = (
        f"IDVNM{document_number}{mrz_check(document_number)}"
        f"{optional_data}{mrz_check(optional_data)}"
    )
    line_2_without_composite = (
        f"{birth}{mrz_check(birth)}{sex}{expiry}{mrz_check(expiry)}VNM" + "<" * 11
    )
    composite_source = (
        line_1[5:15]
        + line_1[15:30]
        + line_2_without_composite[0:7]
        + line_2_without_composite[8:15]
        + line_2_without_composite[18:29]
    )
    line_2 = line_2_without_composite + mrz_check(composite_source)
    line_3 = name.ljust(30, "<")

    assert len(line_1) == len(line_2) == len(line_3) == 30
    return "\n".join((line_1, line_2, line_3))


class VietnameseCccdExtractionTests(unittest.TestCase):
    def test_reads_bilingual_front_labels_name_on_next_line_and_address_continuation(self):
        # Chuỗi OCR giả lập mặt trước CCCD. ``Ni`` là lỗi OCR thường gặp của ``Nữ``.
        front_ocr = """CĂN CƯỚC CÔNG DÂN
Số / No.: 123456789012
Họ và tên / Full name:
NGƯỜI THỬ NGHIỆM
Ngày sinh / Date of birth: 09/05/1983
Giới tính / Sex: Ni
Quốc tịch / Nationality: Việt Nam
Nơi thường trú / Place of residence:
Số 42, Đường Kiểm Thử
Phường Mẫu, Thành phố Giả Lập
"""

        result = extract_personal_information(front_ocr)

        self.assertEqual(result.fields["citizen_id"], "123456789012")
        self.assertEqual(result.fields["full_name"], "NGƯỜI THỬ NGHIỆM")
        self.assertEqual(result.fields["date_of_birth"], "09/05/1983")
        self.assertEqual(result.fields["gender"], "Nữ")
        self.assertEqual(
            result.fields["residence_address"],
            "Số 42, Đường Kiểm Thử Phường Mẫu, Thành phố Giả Lập",
        )

    def test_valid_mrz_populates_trusted_cccd_birth_gender_and_name(self):
        result = extract_personal_information(
            "",
            cccd_mrz_texts=[make_valid_cccd_mrz()],
        )

        self.assertEqual(result.fields["citizen_id"], "001123456789")
        self.assertEqual(result.fields["date_of_birth"], "03/04/1991")
        self.assertEqual(result.fields["gender"], "Nữ")
        self.assertEqual(result.fields["full_name"], "TEST CONG DAN")
        for field in ("citizen_id", "date_of_birth", "gender", "full_name"):
            self.assertEqual(result.confidence[field], "high")

    def test_cccd_address_requires_explicit_review(self):
        address_ocr = """--- CCCD ĐỊA CHỈ ---
Nơi thường trú: Số 42, Đường Kiểm Thử
Phường Mẫu, Thành phố Giả Lập
"""

        result = extract_personal_information(address_ocr)

        self.assertEqual(result.confidence["residence_address"], "medium")
        self.assertEqual(result.confidence["contact_address"], "medium")
        self.assertTrue(any("Địa chỉ đọc từ ảnh CCCD" in warning for warning in result.warnings))

    def test_reads_bank_slip_labels_including_a_wrapped_bank_name_line(self):
        bank_ocr = """TÊN CHỦ TÀI KHOẢN: NGUYEN THI PHIEU
SỐ TÀI KHOẢN: 6615205248060
NGÂN HÀNG NÔNG NGHIỆP VÀ PHÁT TRIỂN
NÔNG THÔN (AGRIBANK)
"""

        result = extract_personal_information(bank_ocr)

        self.assertEqual(result.fields["bank_account_name"], "NGUYEN THI PHIEU")
        self.assertEqual(result.fields["bank_account_number"], "6615205248060")
        self.assertEqual(
            result.fields["bank_name"],
            "NGÂN HÀNG NÔNG NGHIỆP VÀ PHÁT TRIỂN NÔNG THÔN (AGRIBANK)",
        )

    def test_checksum_corrupted_mrz_does_not_populate_trusted_fields(self):
        lines = make_valid_cccd_mrz().splitlines()
        # Đổi checksum ngày sinh ở dòng 2, giữ nguyên các ký tự dữ liệu khác.
        lines[1] = lines[1][:6] + ("0" if lines[1][6] != "0" else "1") + lines[1][7:]
        corrupted_mrz = "\n".join(lines)

        result = extract_personal_information("", cccd_mrz_texts=[corrupted_mrz])

        for field in ("citizen_id", "date_of_birth", "gender", "full_name"):
            self.assertEqual(result.fields[field], "")
            self.assertEqual(result.confidence[field], "missing")


if __name__ == "__main__":
    unittest.main()
