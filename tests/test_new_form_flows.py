"""Phỏng vấn và tạo đơn cho bộ mẫu mới (đơn xác nhận, tờ khai BHYT, Mẫu 01/03)."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from docx import Document

from form_app import create_app


class NewFormFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        app = create_app(
            {
                "TESTING": True,
                "UPLOAD_FOLDER": root / "uploads",
                "OUTPUT_FOLDER": root / "outputs",
            }
        )
        self.client = app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_interview(self, template_id: str, answers: dict[str, str], limit: int = 40) -> tuple[dict, list[str]]:
        interview = self.client.post("/api/interview/start", json={"template_id": template_id}).get_json()
        asked = []
        for _ in range(limit):
            if interview["complete"]:
                return interview, asked
            asked.append(interview["field"])
            interview = self.client.post(
                "/api/interview/answer",
                json={"session_id": interview["session_id"], "answer": answers.get(interview["field"], "bỏ qua")},
            ).get_json()
            self.assertIsNone(interview["error"], f"{asked[-1]}: {interview['error']}")
        self.fail(f"Phỏng vấn {template_id} không kết thúc sau {limit} câu.")

    def test_catalog_lists_groups_templates_and_labels(self):
        catalog = self.client.get("/api/templates").get_json()
        self.assertEqual({group["id"] for group in catalog["groups"]}, {"to_khai", "xac_nhan"})
        templates = {item["id"]: item for item in catalog["templates"]}
        self.assertEqual(len(templates), 16)
        proxy = templates["xn_khuyet_tat_khai_thay"]
        self.assertEqual(proxy["family"], "xn_khuyet_tat")
        self.assertEqual(proxy["variant"], "khai_thay")
        labels = {field["name"]: field["label"] for field in proxy["fields"]}
        self.assertEqual(labels["guardian_full_name"], "Họ tên người khai thay")
        self.assertIn("Phụ nữ sinh đủ hai con trước 35 tuổi", catalog["options"]["support_category"])

    def test_proxy_disability_letter_interview_and_download(self):
        answers = {
            "full_name": "Nguyễn An Bình",
            "date_of_birth": "12/03/2018",
            "citizen_id": "079218000111",
            "citizen_id_issue_date": "20/03/2018",
            "residence_address": "12 Lý Nam Đế, phường Minh Phụng",
            "contact_address": "34 Điện Biên Phủ, phường Gia Định",
            "subject_relationship": "Con",
            "guardian_full_name": "Trần Thị Hoa",
            "guardian_relationship": "mẹ",
            "guardian_citizen_id": "không bảy chín một tám tám không không một hai ba bốn",
            "destination_ward": "Gia Định",
        }
        interview, asked = self._run_interview("xn_khuyet_tat_khai_thay", answers)
        fields = interview["fields"]
        self.assertEqual(fields["subject_relationship"], "con")
        self.assertEqual(fields["guardian_citizen_id"], "079188001234")
        self.assertEqual(fields["destination_ward"], "xã/phường Gia Định")
        self.assertNotIn("guardian_address", asked)

        response = self.client.post("/api/generate", json={"template_id": "xn_khuyet_tat_khai_thay", "fields": fields})
        self.assertEqual(response.status_code, 200)
        text = "\n".join(paragraph.text for paragraph in Document(io.BytesIO(response.data)).paragraphs)
        self.assertIn("xác nhận cho con tôi là Nguyễn An Bình", text)
        response.close()

    def test_health_insurance_asks_parent_only_for_young_children(self):
        base = {
            "full_name": "Lê Minh Khang",
            "citizen_id": "079223000999",
            "gender": "Nam",
        }
        _, asked_child = self._run_interview("to_khai_bhyt", {**base, "date_of_birth": "01/01/2024"})
        _, asked_adult = self._run_interview("to_khai_bhyt", {**base, "date_of_birth": "01/01/1990"})
        self.assertIn("guardian_full_name", asked_child)
        self.assertNotIn("guardian_full_name", asked_adult)

        _, asked_with_code = self._run_interview(
            "to_khai_bhyt", {**base, "date_of_birth": "01/01/1990", "social_insurance_number": "7912345678"}
        )
        self.assertNotIn("email", asked_with_code)

    def test_pension_asks_guardian_details_and_wallet_only_when_relevant(self):
        identity = {
            "full_name": "Trịnh Thị Lan",
            "date_of_birth": "30/05/1948",
            "citizen_id": "079148001094",
            "residence_address": "8 Lò Siêu, phường Minh Phụng",
        }
        _, asked_alone = self._run_interview("tro_cap_huu_tri", {**identity, "payment_method": "tiền mặt"})
        self.assertNotIn("guardian_citizen_id", asked_alone)
        self.assertNotIn("benefit_recipient", asked_alone)
        self.assertNotIn("bank_account_number", asked_alone)

        interview, asked = self._run_interview(
            "tro_cap_huu_tri",
            {
                **identity,
                "guardian_full_name": "Nguyễn Văn Tâm",
                "benefit_recipient": "người giám hộ nhận",
                "payment_method": "ví MoMo",
                "ewallet_number": "không chín một hai ba bốn năm sáu bảy tám",
            },
        )
        self.assertIn("guardian_citizen_id", asked)
        self.assertIn("ewallet_number", asked)
        self.assertNotIn("bank_account_number", asked)
        self.assertEqual(interview["fields"]["payment_method"], "Ví điện tử")
        self.assertEqual(interview["fields"]["benefit_recipient"], "Người giám hộ")

    def test_progress_total_grows_only_when_a_branch_opens(self):
        interview = self.client.post("/api/interview/start", json={"template_id": "tro_cap_huu_tri"}).get_json()
        initial_total = interview["progress"]["total"]
        for answer in ("Trịnh Thị Lan", "30/05/1948", "Nữ", "Kinh", "079148001094", "bỏ qua", "bỏ qua", "8 Lò Siêu", "bỏ qua", "bỏ qua", "bỏ qua"):
            interview = self.client.post(
                "/api/interview/answer", json={"session_id": interview["session_id"], "answer": answer}
            ).get_json()
        self.assertEqual(interview["field"], "guardian_full_name")
        self.assertEqual(interview["progress"]["total"], initial_total)
        interview = self.client.post(
            "/api/interview/answer", json={"session_id": interview["session_id"], "answer": "Nguyễn Văn Tâm"}
        ).get_json()
        self.assertEqual(interview["field"], "guardian_date_of_birth")
        self.assertGreater(interview["progress"]["total"], initial_total)
        self.assertEqual(interview["progress"]["current"], 13)


if __name__ == "__main__":
    unittest.main()
