"""Kiểm tra việc điền các mẫu Word gốc trong templates_word theo nhãn in sẵn."""

from __future__ import annotations

from datetime import date
import re
import tempfile
import unittest
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from services.docx_service import fill_document, generate_document
from services.form_mappings import FILLERS
from services.template_service import TEMPLATES, get_template

TODAY = date.today()
TODAY_TEXT = f"ngày {TODAY.day:02d} tháng {TODAY.month:02d} năm {TODAY.year}"

# Dữ liệu giả định, không phải của người dân thật.
SAMPLE = {
    "full_name": "Nguyễn Văn An",
    "date_of_birth": "01/02/1945",
    "gender": "Nam",
    "ethnic_group": "Hoa",
    "citizen_id": "079045001234",
    "citizen_id_issue_date": "10/08/2021",
    "citizen_id_issue_place": "Bộ Công an",
    "residence_address": "12 Lý Nam Đế, phường Minh Phụng",
    "contact_address": "34 Hồng Bàng, phường Gia Định",
    "temporary_address": "56 Minh Phụng",
    "phone_number": "0901234567",
    "occupation": "Nội trợ",
    "employer": "Tự do",
    "support_category": "Hộ cận nghèo",
    "support_detail": "CN-123",
    "health_insurance_number": "HT2797922344567",
    "guardian_full_name": "Nguyễn Thị Bình",
    "guardian_date_of_birth": "02/03/1975",
    "guardian_citizen_id": "079175001234",
    "guardian_citizen_id_issue_date": "11/09/2021",
    "guardian_citizen_id_issue_place": "Cục Cảnh sát QLHC về TTXH",
    "guardian_residence_address": "78 Hậu Giang",
    "guardian_current_address": "90 Hậu Giang",
    "guardian_phone": "0912345678",
    "guardian_relationship": "con",
    "guardian_address": "78 Hậu Giang",
    "subject_relationship": "mẹ",
    "payment_method": "Tài khoản ngân hàng",
    "bank_account_name": "NGUYEN VAN AN",
    "bank_account_number": "0123456789",
    "bank_name": "Ngân hàng Kiểm Thử",
    "deceased_full_name": "Trần Văn Cường",
    "deceased_date_of_birth": "05/06/1940",
    "deceased_gender": "Nam",
    "deceased_ethnic_group": "Kinh",
    "deceased_nationality": "Việt Nam",
    "deceased_residence_address": "12 Lý Nam Đế",
    "deceased_citizen_id": "079040001234",
    "deceased_death_date": "15/08/2026",
    "deceased_death_hour": "14",
    "deceased_death_minute": "30",
    "deceased_death_place": "Bệnh viện Chợ Rẫy",
    "deceased_death_cause": "Bệnh già",
    "death_certificate_number": "123/TB",
    "death_certificate_date": "16/08/2026",
    "death_certificate_issuer": "Bệnh viện Chợ Rẫy",
    "deceased_relationship": "con",
    "burial_support_category": "Người chết trong trường hợp khẩn cấp (thiên tai, hỏa hoạn, tai nạn giao thông...)",
    "cremation_support_category": "Hộ cận nghèo",
    "cremation_support_detail": "CN-77",
    "disability_procedure_type": "Cấp lại Giấy xác nhận khuyết tật",
    "destination_ward": "phường Gia Định",
    "email": "an@example.com",
    "birth_registration_ward": "phường Minh Phụng",
    "birth_registration_province": "TP. Hồ Chí Minh",
    "primary_care_facility": "Trạm y tế phường Minh Phụng",
    "request_content": "Cấp lại thẻ BHYT",
}
REQUIRED_ONLY = {
    "full_name": "Nguyễn Văn An",
    "date_of_birth": "01/02/1945",
    "citizen_id": "079045001234",
    "residence_address": "12 Lý Nam Đế, phường Minh Phụng",
}


def _paragraphs(document) -> list[str]:
    """Văn bản từng đoạn theo đúng thứ tự in, kể cả đoạn trong bảng."""
    return [Paragraph(element, None).text for element in document.element.body.iter(qn("w:p"))]


def _line(document, text: str) -> str:
    for paragraph in _paragraphs(document):
        if text in paragraph:
            return paragraph
    raise AssertionError(f"Không tìm thấy dòng chứa {text!r}")


def _fill(template_id: str, **overrides):
    document, missing = fill_document(get_template(template_id), {**SAMPLE, **overrides})
    return document, missing


class EveryTemplateTests(unittest.TestCase):
    def test_every_template_has_a_word_file_and_a_filler(self):
        for template in TEMPLATES.values():
            with self.subTest(template=template.id):
                self.assertTrue(template.path.is_file(), template.file_name)
                self.assertIn(template.id, FILLERS)

    def test_every_label_used_by_the_fillers_is_still_in_the_word_files(self):
        # Nếu test này lỗi: chữ của một nhãn trong tệp Word đã bị sửa; cập
        # nhật nhãn tương ứng trong services/form_mappings.py.
        for template_id in TEMPLATES:
            for data in (SAMPLE, REQUIRED_ONLY):
                with self.subTest(template=template_id, data="full" if data is SAMPLE else "required"):
                    _document, missing = fill_document(get_template(template_id), data)
                    self.assertEqual(missing, [])

    def test_applicant_signature_date_is_today_and_authority_date_is_left_blank(self):
        document, _ = _fill("xn_nq40")
        lines = _paragraphs(document)
        self.assertIn(f"Ngày {TODAY.day:02d} tháng {TODAY.month:02d} năm {TODAY.year}", lines)
        # Ô xác nhận của UBND phường vẫn để trống cho cán bộ ghi.
        self.assertTrue(any(line.startswith("Ngày …… tháng") for line in lines))

    def test_missing_optional_values_keep_the_printed_dots(self):
        document, _ = fill_document(get_template("xn_nq40"), REQUIRED_ONLY)
        self.assertIn("……", _line(document, "- Số điện thoại:"))
        self.assertIn("Nguyễn Văn An", _line(document, "- Tôi tên:"))

    def test_generate_document_writes_a_readable_docx(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "don.docx"
            generate_document(get_template("ho_tro_nq32"), SAMPLE, output)
            self.assertIn("Nguyễn Văn An", _line(Document(output), "1. Họ và tên:"))


class RetirementPensionTests(unittest.TestCase):
    def test_fills_identity_lines_and_ticks_the_elderly_pension_boxes(self):
        document, _ = _fill("tro_cap_huu_tri", benefit_recipient="Người hưởng trợ cấp")
        self.assertEqual(_line(document, "1.1. Họ và tên:"), "1.1. Họ và tên: Nguyễn Văn An")
        self.assertEqual(
            _line(document, "Ngày sinh: 01/02/1945"), "Ngày sinh: 01/02/1945 Giới tính: Nam Dân tộc: Hoa"
        )
        self.assertIn("079045001234, ngày cấp 10/08/2021", _line(document, "Số căn cước/Số định danh cá nhân:"))
        self.assertTrue(_line(document, "Người cao tuổi đề nghị hưởng trợ cấp hưu trí").startswith("☒"))
        self.assertTrue(_line(document, "1.5. Thẻ BHYT").endswith("☒ Có"))
        # Sinh năm 1945 => đủ 75 tuổi: chỉ đánh dấu ô "từ đủ 75 tuổi".
        self.assertTrue(_line(document, "Người từ đủ 75 tuổi trở lên").startswith("☒"))
        self.assertTrue(_line(document, "Người từ đủ 70 tuổi đến dưới 75").startswith("□"))
        self.assertEqual(_line(document, "b) Tài khoản ngân hàng"), "b) Tài khoản ngân hàng ☒")
        self.assertEqual(_line(document, "a) Tiền mặt"), "a) Tiền mặt □")
        self.assertIn(TODAY_TEXT, _line(document, "NGƯỜI KHAI"))

    def test_payment_recipient_can_be_the_guardian(self):
        document, _ = _fill("tro_cap_huu_tri", benefit_recipient="Người giám hộ")
        lines = _paragraphs(document)
        section_three = lines.index(next(line for line in lines if line.startswith("III. Phương thức")))
        self.assertIn("Họ và tên: Nguyễn Thị Bình", lines[section_three:])


class BurialSupportTests(unittest.TestCase):
    def test_fills_deceased_details_category_and_payment(self):
        document, _ = _fill("ho_tro_mai_tang", request_content="")
        self.assertEqual(
            _line(document, "Đã chết vào lúc:"), "Đã chết vào lúc: 14 giờ 30 phút, ngày 15 tháng 08 năm 2026"
        )
        self.assertIn("Đề nghị hỗ trợ chi phí mai táng", _line(document, "Nội dung đề nghị:"))
        checked = [line for line in _paragraphs(document) if line.startswith("☒")]
        self.assertEqual(
            checked,
            ["☒ Người chết trong trường hợp khẩn cấp (thiên tai, hỏa hoạn, tai nạn giao thông,...).", "☒ Tài khoản ngân hàng:"],
        )
        # Không có tổ chức => mục 3.1 là chính người đề nghị.
        self.assertIn("Mối quan hệ với người chết: con", _paragraphs(document))

    def test_organisation_case_leaves_individual_section_blank(self):
        document, _ = _fill("ho_tro_mai_tang", org_name="Hội Chữ thập đỏ phường")
        self.assertNotIn("Mối quan hệ với người chết: con", _paragraphs(document))
        self.assertIn("Tên tổ chức: Hội Chữ thập đỏ phường", _paragraphs(document))


class CremationSupportTests(unittest.TestCase):
    def test_ticks_exactly_the_selected_wingdings_box(self):
        document, _ = _fill("ho_tro_hoa_tang")
        xml = document.element.xml
        self.assertEqual(xml.count('w:char="F0FD"'), 1)
        self.assertEqual(xml.count('w:char="F0A8"'), 18)
        near_poor = next(
            element for element in document.element.body.iter(qn("w:p"))
            if Paragraph(element, None).text.startswith("- Hộ cận nghèo")
        )
        self.assertIn('w:char="F0FD"', near_poor.xml)
        self.assertIn("mã số: CN-77", Paragraph(near_poor, None).text)
        self.assertIn("(Giấy chứng tử số: 123/TB ngày 16/08/2026 do Bệnh viện Chợ Rẫy cấp)", _paragraphs(document))

    def test_category_printed_on_two_lines_ticks_the_box_on_the_second_line(self):
        document, _ = _fill("ho_tro_hoa_tang", cremation_support_category="Người hoạt động cách mạng trước ngày 01/01/1945")
        second_line = next(
            element for element in document.element.body.iter(qn("w:p"))
            if "(cán bộ lão thành cách mạng)" in Paragraph(element, None).text
        )
        self.assertIn('w:char="F0FD"', second_line.xml)


class ResolutionSupportTests(unittest.TestCase):
    def test_both_resolution_forms_tick_one_row_and_fill_its_code(self):
        for template_id in ("ho_tro_nq40", "ho_tro_nq32"):
            with self.subTest(template_id=template_id):
                document, _ = _fill(template_id)
                xml = document.element.xml
                self.assertEqual(xml.count('w:char="F053"'), 1)
                self.assertEqual(xml.count('w:char="F0A3"'), 4)
                self.assertIn("mã số: CN-123", _line(document, "Hộ cận nghèo (theo tiêu chí"))
                self.assertIn(".....", _line(document, "Hộ nghèo (theo tiêu chí"))
                self.assertEqual(
                    _line(document, "3. CCCD số:"),
                    "3. CCCD số: 079045001234,Ngày cấp: 10/08/2021; Nơi cấp Bộ Công an",
                )


class DisabilityDeterminationTests(unittest.TestCase):
    def test_ticks_selected_procedure_and_splits_date_of_birth(self):
        document, _ = _fill("xac_dinh_khuyet_tat")
        checked = [line for line in _paragraphs(document) if line.startswith("☒")]
        self.assertEqual(checked, ["☒ Cấp lại Giấy xác nhận khuyết tật"])
        self.assertEqual(_line(document, "- Sinh ngày"), "- Sinh ngày 01\ttháng 02\tnăm 1945\tGiới tính: Nam")
        self.assertIn("Chủ tịch Ủy ban nhân dân phường Minh Phụng", _paragraphs(document))

    def test_representative_section_stays_blank_without_a_representative(self):
        document, _ = fill_document(get_template("xac_dinh_khuyet_tat"), REQUIRED_ONLY)
        lines = _paragraphs(document)
        start = lines.index(next(line for line in lines if line.startswith("II. Thông tin người đại diện")))
        self.assertTrue(lines[start + 1].rstrip().endswith(":"))


class HealthInsuranceTests(unittest.TestCase):
    def test_without_social_insurance_code_fills_section_one(self):
        document, _ = _fill("to_khai_bhyt", social_insurance_number="")
        self.assertIn("NGUYỄN VĂN AN", _line(document, "[01]."))
        self.assertIn("[05]. Dân tộc: Hoa", _line(document, "[05]."))
        self.assertIn("Xã: phường Minh Phụng", _line(document, "[09.1]"))
        self.assertNotIn("NGUYỄN VĂN AN", _line(document, "[14.1]."))

    def test_with_social_insurance_code_fills_section_two_only(self):
        document, _ = _fill("to_khai_bhyt", social_insurance_number="7912345678")
        self.assertIn("7912345678", _line(document, "[13]."))
        self.assertIn("NGUYỄN VĂN AN", _line(document, "[14.1]."))
        self.assertNotIn("NGUYỄN VĂN AN", _line(document, "[01]."))
        self.assertIn("[05]. Dân tộc: Kinh", _line(document, "[05]."))


class ConfirmationLetterTests(unittest.TestCase):
    def test_example_page_with_real_personal_data_is_not_copied(self):
        # Tệp mẫu từng kèm thêm một trang đơn đã điền thông tin thật: đơn tạo ra
        # chỉ có một bản, và mọi số CCCD trong đơn đều là số người dùng nhập.
        document, _ = _fill("xn_khuyet_tat_khai_thay")
        text = "\n".join(_paragraphs(document))
        self.assertEqual(text.count("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM"), 1)
        entered_ids = {value for key, value in SAMPLE.items() if key.endswith("citizen_id")}
        self.assertLessEqual(set(re.findall(r"\d{12}", text)), entered_ids)
        self.assertIn("xác nhận cho mẹ tôi là Nguyễn Văn An", text)

    def test_destination_ward_replaces_leftover_ward_names(self):
        nq35, _ = _fill("xn_nq35")
        self.assertNotIn("Bình Trị Đông", "\n".join(_paragraphs(nq35)))
        self.assertTrue(_line(nq35, "để tôi bổ túc hồ sơ").endswith("xin hưởng chế độ tại phường Gia Định"))
        disability, _ = _fill("xn_khuyet_tat_tu_khai")
        self.assertIn("Kính chuyển đến UBND phường Gia Định", _paragraphs(disability))

    def test_proxy_letter_uses_the_real_relationship_instead_of_printed_father(self):
        document, _ = _fill("xn_mung_tho_khai_thay")
        self.assertIn("xác nhận mẹ tôi từ trước tới nay", _line(document, "Nay tôi làm đơn"))
        self.assertEqual(_line(document, "Mối quan hệ với đối tượng:"), "Mối quan hệ với đối tượng: con")

    def test_self_declared_letter_fills_tab_separated_fields(self):
        document, _ = _fill("xn_huu_tri_tu_khai")
        self.assertEqual(_line(document, "- Họ tên:"), "- Họ tên: Nguyễn Văn An\tSinh ngày: 01/02/1945")
        self.assertEqual(_line(document, "- Số căn cước công dân:"), "- Số căn cước công dân: 079045001234\tcấp ngày: 10/08/2021")


if __name__ == "__main__":
    unittest.main()
