from __future__ import annotations

import unittest

from models.database import ExtractionResult
from services.batch_extraction_service import merge_extraction_results


class BatchExtractionMergeTests(unittest.TestCase):
    def test_selects_high_confidence_value_and_reports_vietnamese_conflict(self):
        merged = merge_extraction_results(
            [
                ExtractionResult(
                    fields={"full_name": "Nguyễn Vãn An", "citizen_id": "079 150 001 234"},
                    confidence={"full_name": "medium", "citizen_id": "high"},
                ),
                ExtractionResult(
                    fields={"full_name": "Nguyễn Văn An", "citizen_id": "079150001234"},
                    confidence={"full_name": "high", "citizen_id": "medium"},
                ),
            ]
        )

        self.assertEqual(merged.result.fields["full_name"], "Nguyễn Văn An")
        self.assertEqual(merged.result.confidence["full_name"], "high")
        self.assertEqual(merged.result.fields["citizen_id"], "079 150 001 234")
        self.assertEqual(len(merged.conflicts), 1)
        self.assertEqual(merged.conflicts[0].label, "Họ và tên")
        self.assertIn("Họ và tên", merged.result.warnings[-1])

    def test_majority_breaks_same_confidence_tie(self):
        merged = merge_extraction_results(
            [
                ExtractionResult(
                    fields={"residence_address": "Phường 1, Quận 3"},
                    confidence={"residence_address": "medium"},
                ),
                ExtractionResult(
                    fields={"residence_address": "Phường 1, Quận 3"},
                    confidence={"residence_address": "medium"},
                ),
                ExtractionResult(
                    fields={"residence_address": "Phường 2, Quận 3"},
                    confidence={"residence_address": "medium"},
                ),
            ]
        )

        self.assertEqual(merged.result.fields["residence_address"], "Phường 1, Quận 3")
        self.assertEqual(merged.conflicts[0].alternative_values, ("Phường 2, Quận 3",))
        self.assertEqual(merged.conflicts[0].source_indexes, (0, 1, 2))

    def test_treats_mrz_name_without_accents_as_same_name_and_keeps_accented_value(self):
        merged = merge_extraction_results(
            [
                ExtractionResult(fields={"full_name": "NGUYEN LE THI"}, confidence={"full_name": "high"}),
                ExtractionResult(fields={"full_name": "NGUYÊN LÊ THỊ"}, confidence={"full_name": "high"}),
            ]
        )

        self.assertEqual(merged.result.fields["full_name"], "NGUYÊN LÊ THỊ")
        self.assertEqual(merged.conflicts, [])

    def test_blank_result_list_has_reviewable_empty_result(self):
        merged = merge_extraction_results([])

        self.assertEqual(merged.result.fields["full_name"], "")
        self.assertEqual(merged.result.confidence["full_name"], "missing")
        self.assertEqual(merged.conflicts, [])
        self.assertTrue(any("Chưa có tệp nào" in warning for warning in merged.result.warnings))

    def test_regenerates_missing_field_warnings_from_the_merged_profile(self):
        merged = merge_extraction_results(
            [
                ExtractionResult(
                    fields={"full_name": "Nguyễn Văn An"},
                    confidence={"full_name": "high"},
                    warnings=[
                        "Dữ liệu được suy luận từ OCR/PDF. Hãy kiểm tra kỹ mọi trường trước khi tải đơn.",
                        "Không nhận diện được số CCCD/định danh 12 chữ số.",
                    ],
                ),
                ExtractionResult(
                    fields={"citizen_id": "079150001234"},
                    confidence={"citizen_id": "high"},
                    warnings=["Dữ liệu được suy luận từ OCR/PDF. Hãy kiểm tra kỹ mọi trường trước khi tải đơn."],
                ),
            ]
        )

        self.assertEqual(
            merged.result.warnings.count(
                "Dữ liệu được suy luận từ OCR/PDF. Hãy kiểm tra kỹ mọi trường trước khi tải đơn."
            ),
            1,
        )
        self.assertFalse(any("Không nhận diện được số CCCD" in warning for warning in merged.result.warnings))


if __name__ == "__main__":
    unittest.main()
