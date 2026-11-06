"""Câu hỏi kèm nội dung giấy tờ của người dân không được lưu đệm hay tra cứu web."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ai_engine


def _failing_client(calls):
    # Ghi lại tham số gọi OpenAI rồi báo lỗi: test không gọi mạng thật.
    def create(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("không gọi mạng trong test")

    return SimpleNamespace(responses=SimpleNamespace(create=create))


class DocumentQuestionPrivacyTests(unittest.TestCase):
    def test_document_question_skips_cache_and_web_search(self):
        calls = []
        question = "<tai_lieu>NGUYỄN VĂN AN, CCCD 079150001234</tai_lieu> Giấy này là gì?"
        with patch.object(ai_engine, "client", _failing_client(calls)), \
                patch.object(ai_engine, "_cache_get") as cache_get, \
                patch.object(ai_engine, "_cache_set") as cache_set:
            self.assertEqual(list(ai_engine.stream_answer(question, tai_lieu=True)), [])
        self.assertTrue(calls)
        self.assertTrue(all(call["tools"] == [] for call in calls))
        cache_get.assert_not_called()
        cache_set.assert_not_called()

    def test_normal_question_keeps_cache_and_web_search(self):
        calls = []
        with patch.object(ai_engine, "client", _failing_client(calls)), \
                patch.object(ai_engine, "_cache_get", return_value=None) as cache_get:
            list(ai_engine.stream_answer("Giờ làm việc của phường?", dung_san=False))
        cache_get.assert_called_once()
        self.assertTrue(all(call["tools"] == [ai_engine.WEB_SEARCH_TOOL] for call in calls))


if __name__ == "__main__":
    unittest.main()
