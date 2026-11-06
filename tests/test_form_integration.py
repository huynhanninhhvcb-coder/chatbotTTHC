"""Kiểm tra ứng dụng điền đơn được gắn đúng vào chatbot chính."""

import unittest

from app import app


class FormIntegrationTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_chatbot_embeds_form_input_and_form_filling_button(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="formFileInput"', response.data)
        self.assertIn(b'id="attachBtn"', response.data)
        # Bỏ chụp ảnh giấy tờ (10/2026): máy chủ Render không đọc được ảnh chụp,
        # chỉ giữ nút đính kèm tệp.
        self.assertNotIn(b'id="cameraBtn"', response.data)
        self.assertNotIn(b'id="cameraInput"', response.data)
        # Nút "Điền đơn tự động" thay cho "Tra cứu hồ sơ" (yêu cầu 10/2026).
        self.assertIn(b'id="dienDonBtn"', response.data)
        self.assertNotIn(b'id="hoSoBtn"', response.data)

    def test_mounted_form_page_and_static_asset_are_available(self):
        page = self.client.get("/dien-don/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"/dien-don/form_static/js/app.js", page.data)

        with self.client.get("/dien-don/form_static/js/app.js") as script:
            self.assertEqual(script.status_code, 200)
            self.assertIn(b"apiUrl", script.data)

    def test_mounted_interview_api_starts_a_session(self):
        response = self.client.post(
            "/dien-don/api/interview/start",
            json={"template_id": "tro_cap_huu_tri"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get("session_id"))


if __name__ == "__main__":
    unittest.main()
