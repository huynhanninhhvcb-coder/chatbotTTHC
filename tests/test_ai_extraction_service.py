from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openai import OpenAIError

from services.ai_extraction_service import AIExtractionError, extract_missing_fields


class AIExtractionServiceTests(unittest.TestCase):
    @patch("services.ai_extraction_service._render_images", return_value=["encoded-image"])
    @patch("services.ai_extraction_service.OpenAI")
    def test_uses_strict_json_and_validates_returned_fields(self, openai_class, _render):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "full_name": "NGUYỄN VĂN THỬ",
                                "date_of_birth": "1/2/1980",
                                "citizen_id": "079 000 000 001",
                            }
                        )
                    )
                )
            ]
        )
        openai_class.return_value = client

        result = extract_missing_fields(
            "synthetic.png",
            "png",
            ["full_name", "date_of_birth", "citizen_id"],
            api_key="test-key",
            model="gpt-4o-mini",
            max_pages=1,
        )

        self.assertEqual(result["full_name"], "NGUYỄN VĂN THỬ")
        self.assertEqual(result["date_of_birth"], "01/02/1980")
        self.assertEqual(result["citizen_id"], "079000000001")
        request = client.chat.completions.create.call_args.kwargs
        self.assertFalse(request["store"])
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertEqual(request["messages"][0]["content"][1]["image_url"]["detail"], "high")

    @patch("services.ai_extraction_service._render_images", return_value=["encoded-image"])
    @patch("services.ai_extraction_service.OpenAI")
    def test_returns_safe_message_for_exhausted_credit(self, openai_class, _render):
        error = OpenAIError("provider details must not be shown")
        error.code = "credit_balance_exhausted"
        client = MagicMock()
        client.chat.completions.create.side_effect = error
        openai_class.return_value = client

        with self.assertRaisesRegex(AIExtractionError, "hết tín dụng"):
            extract_missing_fields(
                "synthetic.png",
                "png",
                ["full_name"],
                api_key="test-key",
                model="gpt-4o-mini",
                max_pages=1,
            )


if __name__ == "__main__":
    unittest.main()
