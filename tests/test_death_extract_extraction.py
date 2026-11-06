"""Hồi quy cho việc trích xuất thông tin người mất từ Trích lục khai tử.

Fixture dưới đây được viết hoàn toàn trong mã kiểm thử, mô phỏng đúng cấu
trúc nhãn của mẫu Trích lục khai tử (bản sao) do UBND phường cấp; không chứa
tên, số định danh hay địa chỉ của người dùng thực tế.
"""

from __future__ import annotations

import unittest

from services.extraction_service import extract_personal_information


SAMPLE_DEATH_EXTRACT = """THÀNH PHỐ HỒ CHÍ MINH
UBND PHƯỜNG MINH PHỤNG
CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập - Tự do - Hạnh phúc

Số: 3000/2026/TLKT-BS
Minh Phụng, ngày 28 tháng 8 năm 2026

TRÍCH LỤC KHAI TỬ
(BẢN SAO)

Họ, chữ đệm, tên: NGUYỄN THỊ TÁM
Ngày, tháng, năm sinh: 12/02/1942
Giới tính: Nữ    Dân tộc: Kinh    Quốc tịch: Việt Nam
Số định danh cá nhân: 079142005196
Giấy tờ tùy thân: Thẻ căn cước công dân số 079142005196, Cục CS QLHC về trật tự xã hội
cấp ngày 13/11/2023
Đã chết vào lúc 18 giờ 10 phút, ngày 15/08/2026 ghi bằng chữ: Mười tám giờ, mười phút,
ngày mười lăm, tháng tám, năm hai nghìn không trăm hai mươi sáu
Nơi chết: 152/54/27G Lạc Long Quân, phường Bình Thới, Thành phố Hồ Chí Minh
Đã được đăng ký khai tử tại: Ủy ban nhân dân phường Minh Phụng, Thành phố Hồ Chí
Minh
Số: 386/2026 ngày 28 tháng 8 năm 2026
Thực hiện trích lục từ: Cơ sở dữ liệu hộ tịch điện tử
"""


class DeathExtractExtractionTests(unittest.TestCase):
    def test_reads_deceased_identity_and_death_details(self):
        result = extract_personal_information(SAMPLE_DEATH_EXTRACT, page_texts=[SAMPLE_DEATH_EXTRACT])

        self.assertEqual(result.fields["deceased_full_name"], "NGUYỄN THỊ TÁM")
        self.assertEqual(result.fields["deceased_date_of_birth"], "12/02/1942")
        self.assertEqual(result.fields["deceased_gender"], "Nữ")
        self.assertEqual(result.fields["deceased_ethnic_group"], "Kinh")
        self.assertEqual(result.fields["deceased_nationality"], "Việt Nam")
        self.assertEqual(result.fields["deceased_citizen_id"], "079142005196")
        self.assertEqual(result.fields["deceased_death_date"], "15/08/2026")
        self.assertIn("Lạc Long Quân", result.fields["deceased_death_place"])
        self.assertEqual(result.fields["death_certificate_number"], "386/2026")
        self.assertEqual(result.fields["death_certificate_date"], "28/08/2026")
        self.assertIn("Minh Phụng", result.fields["death_certificate_issuer"])

    def test_does_not_leak_deceased_identity_into_applicant_fields(self):
        result = extract_personal_information(SAMPLE_DEATH_EXTRACT, page_texts=[SAMPLE_DEATH_EXTRACT])

        # Một trích lục khai tử đứng một mình không mô tả người đề nghị: các
        # trường của người đề nghị phải trống, không bị điền nhầm bằng thông
        # tin người mất.
        self.assertEqual(result.fields["full_name"], "")
        self.assertEqual(result.fields["citizen_id"], "")
        self.assertEqual(result.fields["date_of_birth"], "")
        self.assertEqual(result.fields["gender"], "")

    def test_applicant_cccd_and_death_extract_fill_independently_in_one_batch(self):
        applicant_cccd = """CĂN CƯỚC CÔNG DÂN
Họ và tên / Full name: TRẦN VĂN BÌNH
Số định danh / Personal identification number: 079080001234
Ngày sinh / Date of birth: 05/06/1985
"""
        combined = applicant_cccd + "\n\n" + SAMPLE_DEATH_EXTRACT
        result = extract_personal_information(combined, page_texts=[applicant_cccd, SAMPLE_DEATH_EXTRACT])

        self.assertEqual(result.fields["full_name"], "TRẦN VĂN BÌNH")
        self.assertEqual(result.fields["citizen_id"], "079080001234")
        self.assertEqual(result.fields["deceased_full_name"], "NGUYỄN THỊ TÁM")
        self.assertEqual(result.fields["deceased_citizen_id"], "079142005196")


if __name__ == "__main__":
    unittest.main()
