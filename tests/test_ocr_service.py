"""Kiểm thử đơn vị cho các nhánh OCR chuyên biệt của CCCD."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from services.ocr_service import (
    _detect_document_crop,
    _looks_like_cccd,
    _prepare_image,
    _split_stacked_cccd,
    _split_stacked_cccd_on_separator,
    ocr_pil_image,
)


class CccdDetectionTests(unittest.TestCase):
    def test_preparation_never_upscales_a_small_image(self):
        image = Image.new("RGB", (800, 500), "white")

        prepared = _prepare_image(image, target_width=1200)

        self.assertEqual(prepared.size, image.size)

    def test_preparation_downscales_a_large_image_to_configured_width(self):
        image = Image.new("RGB", (2400, 1500), "white")

        prepared = _prepare_image(image, target_width=1200)

        self.assertEqual(prepared.size, (1200, 750))

    def test_uses_configured_single_language_for_primary_ocr(self):
        image = Image.new("RGB", (800, 500), "white")
        with patch(
            "services.ocr_service.pytesseract.image_to_string", return_value=""
        ) as image_to_string:
            ocr_pil_image(
                image,
                languages=["vie", "eng"],
                preferred_language="vie",
            )

        self.assertEqual(image_to_string.call_args.kwargs["lang"], "vie")

    def test_does_not_treat_every_horizontal_official_document_as_cccd(self):
        image = Image.new("RGB", (1200, 800), "white")
        self.assertFalse(_looks_like_cccd("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", image))

    def test_fast_mode_skips_mrz_pass_for_a_front_only_card(self):
        image = Image.new("RGB", (1600, 1000), "white")
        front_text = "CĂN CƯỚC CÔNG DÂN\nSố: 000000000000\nHọ và tên: NGUYỄN VĂN THỬ"

        with (
            patch(
                "services.ocr_service.pytesseract.image_to_string",
                return_value=front_text,
            ) as image_to_string,
            patch("services.ocr_service._ocr_cccd_mrz", return_value="") as mrz,
        ):
            ocr_pil_image(
                image,
                languages=["vie", "eng"],
                max_workers=1,
                fast_mode=True,
            )

        self.assertEqual(image_to_string.call_count, 2)  # toàn thẻ + bố cục
        mrz.assert_not_called()

    def test_fast_mode_skips_layout_pass_for_a_back_only_card(self):
        image = Image.new("RGB", (1600, 1000), "white")
        back_text = "IDVNM000000000000<<<<<<<<<<<<"

        with (
            patch(
                "services.ocr_service.pytesseract.image_to_string",
                return_value=back_text,
            ) as image_to_string,
            patch(
                "services.ocr_service._ocr_cccd_mrz",
                return_value=back_text,
            ) as mrz,
        ):
            ocr_pil_image(
                image,
                languages=["vie", "eng"],
                max_workers=1,
                fast_mode=True,
            )

        self.assertEqual(image_to_string.call_count, 1)  # chỉ lượt toàn thẻ
        mrz.assert_called_once()

    def test_crops_warm_card_from_large_neutral_phone_photo(self):
        image = Image.new("RGB", (900, 1600), (130, 145, 125))
        card = Image.new("RGB", (800, 500), (245, 225, 180))
        image.paste(card, (50, 650))

        cropped = _detect_document_crop(image)

        self.assertLess(cropped.height, image.height)
        self.assertGreater(cropped.width / cropped.height, 1.3)


class StackedCccdSplitTests(unittest.TestCase):
    def test_detects_dark_separator_without_running_ocr_first(self):
        composite = Image.new("RGB", (1000, 1360), "white")
        for y in range(670, 690):
            for x in range(composite.width):
                composite.putpixel((x, y), (20, 20, 20))

        halves = _split_stacked_cccd_on_separator(composite)

        self.assertIsNotNone(halves)
        front, back = halves
        self.assertLess(front.height, composite.height)
        self.assertLess(back.height, composite.height)

    def test_fast_mode_reads_separator_composite_in_two_passes(self):
        composite = Image.new("RGB", (1000, 1360), "white")
        for y in range(670, 690):
            for x in range(composite.width):
                composite.putpixel((x, y), (20, 20, 20))
        front_text = "CĂN CƯỚC CÔNG DÂN\nHọ và tên: NGUYỄN VĂN THỬ"

        with (
            patch("services.ocr_service._detect_document_crop", return_value=composite),
            patch(
                "services.ocr_service.pytesseract.image_to_string",
                return_value=front_text,
            ) as image_to_string,
            patch("services.ocr_service._ocr_cccd_mrz", return_value="IDVNM") as mrz,
        ):
            ocr_pil_image(
                composite,
                languages=["vie", "eng"],
                max_workers=1,
                fast_mode=True,
            )

        # Một lượt mặt trước + một lượt MRZ (mock riêng), không còn lượt bố cục.
        image_to_string.assert_called_once()
        mrz.assert_called_once()
        self.assertLess(image_to_string.call_args.args[0].height, composite.height)

    def test_splits_a_front_and_back_composite_into_two_card_shaped_halves(self):
        # Người dân thường ghép mặt trước (trên) và mặt sau (dưới) CCCD vào
        # chung một tệp ảnh; tỉ lệ khung ảnh tổng thể khi đó xấp xỉ 0.65-0.95.
        composite = Image.new("RGB", (1012, 1391), "white")

        halves = _split_stacked_cccd(composite)

        self.assertIsNotNone(halves)
        front, back = halves
        self.assertEqual(front.width, composite.width)
        self.assertEqual(back.width, composite.width)
        # Mỗi nửa phải có tỉ lệ của một mặt thẻ nằm ngang để các hàm đọc theo
        # vùng (vốn tính tọa độ theo tỉ lệ một mặt thẻ) chạy đúng.
        self.assertGreater(front.width / front.height, 1.3)
        self.assertGreater(back.width / back.height, 1.3)
        # Hai nửa phải chờm nhẹ lên nhau quanh điểm giữa để không hụt mép thẻ.
        self.assertGreater(front.height + back.height, composite.height)

    def test_does_not_split_a_single_horizontal_card(self):
        horizontal_card = Image.new("RGB", (1600, 1000), "white")

        self.assertIsNone(_split_stacked_cccd(horizontal_card))

    def test_ocr_pipeline_feeds_the_split_back_half_to_mrz(self):
        # Trước khi có _split_stacked_cccd, MRZ luôn nhận nguyên ảnh ghép,
        # khiến vùng MRZ (52%-97% chiều cao ảnh) trật hoàn toàn khỏi vị trí
        # thật. Bài test này khóa lại hành vi: mặt sau phải được tách ra
        # trước khi tới hàm đọc MRZ.
        composite = Image.new("RGB", (1000, 1360), "white")
        whole_card_text = "CĂN CƯỚC CÔNG DÂN\nIDVNM123456789\n"

        with (
            patch("services.ocr_service.pytesseract.get_languages", return_value=["vie", "eng"]),
            patch("services.ocr_service.pytesseract.image_to_string", return_value=whole_card_text),
            patch("services.ocr_service._ocr_cccd_mrz", return_value="") as mrz,
        ):
            ocr_pil_image(composite)

        mrz.assert_called_once()

        back_image = mrz.call_args.args[0]
        self.assertEqual(back_image.width, composite.width)
        self.assertLess(back_image.height, composite.height)


if __name__ == "__main__":
    unittest.main()
