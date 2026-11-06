"""Lớp OCR độc lập để có thể thay Tesseract bằng một nhà cung cấp khác sau này."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from collections import deque
from functools import lru_cache
from pathlib import Path
import re

import pytesseract
from PIL import Image, ImageFilter, ImageOps
from pytesseract import TesseractNotFoundError

from utils.text_utils import strip_accents


class OCRUnavailableError(RuntimeError):
    """Tesseract hoặc dữ liệu ngôn ngữ cần thiết chưa sẵn sàng."""


@dataclass
class OCRResult:
    text: str
    warnings: list[str] = field(default_factory=list)
    source: str = "ocr"
    # PDF cần giữ ranh giới trang để lớp trích xuất ưu tiên đúng tài liệu nguồn.
    pages: list[str] = field(default_factory=list)
    # Vùng MRZ mặt sau CCCD được OCR riêng bằng whitelist Latin/chữ số.
    # Không ghép vào raw text để lớp trích xuất còn kiểm tra checksum riêng.
    mrz_texts: list[str] = field(default_factory=list)


def _configure_tesseract(tesseract_cmd: str | None) -> None:
    """Trỏ pytesseract tới đường dẫn thực thi Tesseract tùy chỉnh, nếu có."""
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd


def _available_languages() -> list[str]:
    """Liệt kê các gói ngôn ngữ Tesseract đã cài trên máy; báo lỗi rõ ràng nếu chưa cài Tesseract."""
    try:
        return pytesseract.get_languages(config="")
    except TesseractNotFoundError as error:
        raise OCRUnavailableError(
            "Chưa tìm thấy Tesseract. Cài Tesseract OCR và đặt TESSERACT_CMD nếu nó không nằm trong PATH."
        ) from error


@lru_cache(maxsize=4)
def _cached_languages(tesseract_cmd: str | None) -> tuple[str, ...]:
    """Bộ nhớ đệm danh sách ngôn ngữ theo từng đường dẫn tesseract_cmd (xem lý do ở detect_languages)."""
    _configure_tesseract(tesseract_cmd)
    return tuple(_available_languages())


def detect_languages(tesseract_cmd: str | None = None) -> list[str]:
    """Cấu hình Tesseract và liệt kê gói ngôn ngữ đã cài, một lần duy nhất.

    ``tesseract --list-langs`` tốn 150 ms-1 s mỗi lần gọi (đo thực tế trên
    Windows). Nơi xử lý nhiều trang/tệp trong cùng một yêu cầu (``ocr_pdf``)
    nên gọi hàm này một lần rồi truyền kết quả cho mỗi lần OCR, thay vì để
    từng trang tự tra lại.
    """
    return list(_cached_languages(tesseract_cmd))


def _prepare_image(image: Image.Image, target_width: int = 1600) -> Image.Image:
    """Tiền xử lý ảnh trước khi OCR: xoay đúng chiều theo EXIF, chuyển xám, tăng tương phản và thu nhỏ nếu quá lớn."""
    image = ImageOps.exif_transpose(image)
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    # Đưa ảnh về một chiều rộng vừa đủ cho Tesseract (~1600px) thay vì luôn
    # nhân đôi ảnh nhỏ hoặc giữ nguyên ảnh chụp điện thoại độ phân giải cao
    # (3000-4000px): OCR ảnh lớn không cần thiết là phần tốn CPU nhất trên
    # máy chủ cấu hình thấp (vd. Render free 0.1 CPU).
    # Không phóng to ảnh nhỏ: nội suy không tạo thêm chi tiết chữ nhưng làm
    # Tesseract phải xử lý nhiều điểm ảnh hơn. Chỉ thu nhỏ ảnh chụp quá lớn.
    if image.width > target_width > 0:
        scale = target_width / image.width
        image = image.resize(
            (target_width, max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


# Thuật toán Otsu: tự động tìm ngưỡng sáng/tối tối ưu để tách nền và tài liệu
# trong _detect_document_crop, không cần đặt ngưỡng cố định thủ công.
def _otsu_threshold(histogram: list[int], total: int) -> int:
    """Return an automatic foreground threshold for an 8-bit image."""
    weighted_sum = sum(level * count for level, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0
    best_variance = -1.0
    best_level = 127
    for level, count in enumerate(histogram):
        background_weight += count
        if not background_weight:
            continue
        foreground_weight = total - background_weight
        if not foreground_weight:
            break
        background_sum += level * count
        background_mean = background_sum / background_weight
        foreground_mean = (weighted_sum - background_sum) / foreground_weight
        variance = background_weight * foreground_weight * (background_mean - foreground_mean) ** 2
        if variance > best_variance:
            best_variance = variance
            best_level = level
    return best_level


def _detect_document_crop(image: Image.Image) -> Image.Image:
    """Crop a photographed card/document surrounded by a large background.

    Phone photos often contain much more table/wall than the document itself.
    Tesseract then treats the textured background as characters.  Work on a
    small blurred grayscale copy, find the largest coherent bright rectangle,
    and map its bounds back to the original image.  Conservative acceptance
    rules keep ordinary scans and full-frame cards unchanged.
    """
    source = ImageOps.exif_transpose(image)
    if source.width < 500 or source.height < 500:
        return source

    scale = min(1.0, 360 / max(source.width, source.height))
    sample = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.BILINEAR,
    )
    gray = ImageOps.grayscale(sample).filter(ImageFilter.GaussianBlur(radius=2.2))
    threshold = _otsu_threshold(gray.histogram(), gray.width * gray.height)
    pixels = gray.load()
    width, height = gray.size
    # Old Vietnamese driving licences have a warm cream background.  Its
    # colour separates reliably from a grey/green table even when luminance
    # alone considers the upper half of the table foreground.
    colour_sample = sample.convert("RGB").filter(ImageFilter.GaussianBlur(radius=2.2))
    colour_pixels = colour_sample.load()
    warm = bytearray(
        1
        if colour_pixels[x, y][0] - colour_pixels[x, y][2] > 22 and pixels[x, y] > 105
        else 0
        for y in range(height)
        for x in range(width)
    )
    warm_ratio = sum(warm) / (width * height)
    bright = warm if 0.04 <= warm_ratio <= 0.65 else bytearray(
        1 if pixels[x, y] > threshold else 0 for y in range(height) for x in range(width)
    )
    visited = bytearray(width * height)
    best: tuple[int, int, int, int, int] | None = None

    # Duyệt qua từng điểm ảnh sáng chưa thăm, loang (BFS) để tìm vùng liên
    # thông sáng lớn nhất — đó là ứng viên cho vùng chứa tài liệu/thẻ.
    for start in range(width * height):
        if not bright[start] or visited[start]:
            continue
        queue = deque([start])
        visited[start] = 1
        count = 0
        min_x = max_x = start % width
        min_y = max_y = start // width
        while queue:
            index = queue.popleft()
            x, y = index % width, index // width
            count += 1
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            for neighbour in (index - 1, index + 1, index - width, index + width):
                if neighbour < 0 or neighbour >= width * height or visited[neighbour] or not bright[neighbour]:
                    continue
                nx, ny = neighbour % width, neighbour // width
                if abs(nx - x) + abs(ny - y) != 1:
                    continue
                visited[neighbour] = 1
                queue.append(neighbour)
        bounds_area = (max_x - min_x + 1) * (max_y - min_y + 1)
        fill_ratio = count / bounds_area
        candidate_width = max_x - min_x + 1
        candidate_height = max_y - min_y + 1
        candidate_aspect = candidate_width / max(1, candidate_height)
        candidate_area_ratio = bounds_area / (width * height)
        candidate = (count, min_x, min_y, max_x + 1, max_y + 1)
        # Chỉ chấp nhận vùng có tỉ lệ khung ảnh, diện tích và độ đặc (fill_ratio)
        # giống một tấm thẻ/giấy tờ điển hình, để không nhận nhầm mảng sáng lớn
        # (tường, mặt bàn...) làm tài liệu.
        if (
            1.15 <= candidate_aspect <= 2.2
            and 0.07 <= candidate_area_ratio <= 0.88
            and fill_ratio >= 0.34
            and (best is None or count > best[0])
        ):
            best = candidate

    if best is None:
        return source
    _, left, top, right, bottom = best
    crop_width, crop_height = right - left, bottom - top
    aspect = crop_width / max(1, crop_height)
    area_ratio = crop_width * crop_height / (width * height)
    width_ratio = crop_width / width
    # Xác nhận lại lần cuối trước khi cắt thật: vùng liên thông lớn nhất vẫn
    # phải đủ giống tài liệu (tỉ lệ, diện tích) và chiếm phần lớn chiều ngang
    # ảnh, nếu không thà giữ nguyên ảnh gốc còn hơn cắt sai.
    if not 1.15 <= aspect <= 2.2 or not 0.07 <= area_ratio <= 0.88 or width_ratio < 0.65:
        return source

    # Nới thêm biên nhỏ quanh vùng phát hiện để không cắt hụt mép chữ, sau đó
    # quy tọa độ từ ảnh mẫu đã thu nhỏ (scale) về đúng kích thước ảnh gốc.
    margin = max(2, round(min(crop_width, crop_height) * 0.025))
    left, top = max(0, left - margin), max(0, top - margin)
    right, bottom = min(width, right + margin), min(height, bottom + margin)
    return source.crop(
        (
            round(left / scale),
            round(top / scale),
            round(right / scale),
            round(bottom / scale),
        )
    )


def _looks_like_cccd(text: str, image: Image.Image) -> bool:
    """Recognise a single horizontal CCCD or a vertical front/back composite."""
    if image.height == 0:
        return False
    ratio = image.width / image.height
    folded = strip_accents(text).lower()
    has_mrz_marker = bool(re.search(r"\b[1il]dvnm", folded))
    identity_markers = bool(
        re.search(r"can\s*cuo\W*c", folded)
        or "citizen identity" in folded
        or has_mrz_marker
    )
    # Thẻ CCCD chụp một mặt nằm ngang có tỉ lệ khung ảnh đặc trưng; ảnh ghép
    # dọc cả hai mặt thì gần vuông hơn nhưng vẫn phải có dấu hiệu MRZ.
    horizontal_card = 1.35 <= ratio <= 2.15
    stacked_sides = 0.65 <= ratio <= 0.95 and has_mrz_marker and identity_markers
    return identity_markers and (horizontal_card or stacked_sides)


def _split_stacked_cccd(image: Image.Image) -> tuple[Image.Image, Image.Image] | None:
    """Tách ảnh ghép dọc mặt trước/sau CCCD thành hai nửa dạng một mặt thẻ.

    Người dân thường chụp/ghép mặt trước và mặt sau CCCD vào chung một tệp
    ảnh (mặt trước ở trên, mặt sau ở dưới). Các hàm đọc theo vùng cố định bên
    dưới tính tọa độ theo tỉ lệ của MỘT mặt thẻ nằm ngang; áp thẳng lên toàn
    bộ ảnh ghép sẽ trật hoàn toàn, ví dụ vùng MRZ vốn lấy 52%-97% chiều cao
    ảnh sẽ rơi vào giữa hai mặt thay vì nửa dưới của mặt sau. Cắt đôi ảnh
    (chờm nhẹ để không hụt mép) rồi trả về (mặt trước, mặt sau) để các hàm đó
    chạy lại đúng như trên một ảnh một mặt thẻ độc lập.
    """
    if image.height == 0:
        return None
    ratio = image.width / image.height
    if not 0.6 <= ratio <= 1.0:
        return None
    overlap = round(image.height * 0.03)
    midpoint = image.height // 2
    top = image.crop((0, 0, image.width, min(image.height, midpoint + overlap)))
    bottom = image.crop((0, max(0, midpoint - overlap), image.width, image.height))
    return top, bottom


def _split_stacked_cccd_on_separator(image: Image.Image) -> tuple[Image.Image, Image.Image] | None:
    """Nhận biết ảnh ghép hai mặt qua dải phân cách tối gần giữa ảnh.

    Nhiều ảnh CCCD do điện thoại/ứng dụng ghép tạo một dải đen ngang giữa hai
    mặt. Tín hiệu hình học này cho phép tách trước lượt OCR đầu tiên, tránh bắt
    Tesseract đọc cả hai mặt trong một ảnh dọc rất lớn. Văn bản dọc thông thường
    không thể bị nhầm vì phải có ít nhất 70% chiều ngang gần như đen.
    """
    halves = _split_stacked_cccd(image)
    if halves is None:
        return None

    sample_width = min(400, image.width)
    scale = sample_width / image.width
    sample = ImageOps.grayscale(
        image.resize(
            (sample_width, max(1, round(image.height * scale))),
            Image.Resampling.BILINEAR,
        )
    )
    width, height = sample.size
    # Dải phân cách thường nằm gần chính giữa ảnh ghép; chỉ quét quanh vùng
    # 42%-58% chiều cao để tránh nhận nhầm chữ đậm/viền thẻ ở xa giữa.
    start = round(height * 0.42)
    end = round(height * 0.58)
    separator_rows: list[int] = []
    for y in range(start, end):
        histogram = sample.crop((0, y, width, y + 1)).histogram()
        dark_ratio = sum(histogram[:70]) / width
        # Một hàng được coi là thuộc dải phân cách nếu ít nhất 70% chiều
        # ngang là điểm ảnh tối (giá trị xám dưới 70).
        if dark_ratio >= 0.70:
            separator_rows.append(y)
    if len(separator_rows) < 2:
        return None

    split_y = round((separator_rows[0] + separator_rows[-1]) / 2 / scale)
    # Preserve the text line immediately above/below the separator.  Some
    # photographed cards place the residence line very close to the divider.
    overlap = round(image.height * 0.035)
    front = image.crop((0, 0, image.width, min(image.height, split_y + overlap)))
    back = image.crop((0, max(0, split_y - overlap), image.width, image.height))
    if front.height == 0 or back.height == 0:
        return None
    if image.width / front.height < 1.25 or image.width / back.height < 1.25:
        return None
    return front, back


def _ocr_cccd_mrz(
    image: Image.Image,
    languages: list[str],
    fallback_language: str,
    max_width: int = 2200,
) -> str:
    """Đọc riêng ba dòng MRZ ở nửa dưới CCCD bằng bộ ký tự hạn chế.

    Cách cắt theo tỷ lệ giữ ứng dụng không phụ thuộc OpenCV và hoạt động với
    ảnh chụp ngang phổ biến. Dữ liệu trả về vẫn phải vượt checksum ở tầng
    trích xuất trước khi được điền vào biểu mẫu.
    """
    image = ImageOps.exif_transpose(image)
    # Vùng MRZ luôn nằm ở dải ngang gần đáy mặt sau CCCD (khoảng 52%-97%
    # chiều cao); lấy theo tỷ lệ để không phụ thuộc kích thước ảnh gốc.
    left, top = int(image.width * 0.04), int(image.height * 0.52)
    right, bottom = int(image.width * 0.96), int(image.height * 0.97)
    if right <= left or bottom <= top:
        return ""

    crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop((left, top, right, bottom))))
    # Phóng to vùng MRZ nếu còn nhỏ hơn max_width để các ký tự đủ lớn cho
    # Tesseract đọc rõ, nhưng giới hạn tối đa 2 lần để tránh tốn CPU vô ích.
    if crop.width:
        scale = min(2.0, max_width / crop.width)
        if abs(scale - 1.0) > 0.01:
            crop = crop.resize(
                (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                Image.Resampling.LANCZOS,
            )

    # MRZ chỉ gồm chữ Latin và số nên ưu tiên gói "eng"; không cần gói "vie".
    language = "eng" if "eng" in languages else fallback_language
    try:
        text = pytesseract.image_to_string(
            crop,
            lang=language,
            config="--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<",
        )
    except pytesseract.TesseractError:
        return ""

    # Chỉ giữ ký tự thuộc whitelist rồi sửa lỗi OCR thường gặp: Tesseract hay
    # đọc nhầm chữ "I" đầu dòng "IDVNM" (mã quốc gia) thành "1" hoặc "l".
    lines = [re.sub(r"[^A-Z0-9<]", "", line.upper()) for line in text.splitlines()]
    lines = ["IDVNM" + line[5:] if re.match(r"^[1L]DVNM", line) else line for line in lines if line]
    # Chỉ coi là đọc được MRZ hợp lệ khi có dòng bắt đầu bằng IDVNM và có ký
    # tự đệm "<" đặc trưng của định dạng MRZ; nếu không, trả về rỗng để lớp
    # trích xuất không nhận nhầm rác OCR.
    if any(line.startswith("IDVNM") for line in lines) and any("<" in line for line in lines):
        return "\n".join(lines)
    return ""


def ocr_pil_image(
    image: Image.Image,
    tesseract_cmd: str | None = None,
    languages: list[str] | None = None,
    max_workers: int = 5,
    target_width: int = 1600,
    preferred_language: str | None = None,
    fast_mode: bool = False,
) -> OCRResult:
    """OCR một ảnh đã mở.

    ``languages`` cho phép nơi gọi (vd. OCR nhiều trang PDF) tra cứu gói ngôn
    ngữ một lần rồi truyền vào, thay vì để mỗi lần gọi tự tốn thêm một lượt
    gọi tiến trình ``tesseract --list-langs``.
    """
    if languages is None:
        _configure_tesseract(tesseract_cmd)
        languages = _available_languages()
    if not languages:
        raise OCRUnavailableError("Tesseract không có gói ngôn ngữ nào để nhận dạng văn bản.")

    warnings: list[str] = []
    # Ưu tiên ngôn ngữ người dùng chỉ định nếu Tesseract có đủ gói tương ứng;
    # nếu không, tự chọn theo gói đã cài (vie+eng tốt nhất, rồi tới vie/eng).
    requested_languages = [part.strip() for part in (preferred_language or "").split("+") if part.strip()]
    if requested_languages and all(part in languages for part in requested_languages):
        language = "+".join(requested_languages)
    else:
        language = "vie+eng" if "vie" in languages and "eng" in languages else "vie" if "vie" in languages else "eng"
    if "vie" not in languages:
        warnings.append("Máy chủ chưa cài dữ liệu tiếng Việt cho Tesseract; kết quả OCR có thể sai dấu.")
    try:
        image = _detect_document_crop(image)
        separator_halves = _split_stacked_cccd_on_separator(image) if fast_mode else None
        # Ảnh ghép có dải phân cách được tách ngay: lượt chính chỉ đọc mặt
        # trước. Mặt sau sẽ đi thẳng vào OCR MRZ chuyên biệt bên dưới.
        primary_image = separator_halves[0] if separator_halves is not None else image
        prepared = _prepare_image(primary_image, target_width=target_width)
        text = pytesseract.image_to_string(prepared, lang=language, config="--oem 3 --psm 6")
    except TesseractNotFoundError as error:
        raise OCRUnavailableError("Không thể khởi chạy Tesseract OCR.") from error
    except pytesseract.TesseractError as error:
        raise OCRUnavailableError(f"Tesseract không thể nhận dạng tệp: {error}") from error

    primary_text = text.strip()
    page_texts = [primary_text] if primary_text else []
    mrz_texts: list[str] = []
    if separator_halves is not None or _looks_like_cccd(text, prepared):
        # Một ảnh đơn thường chỉ là mặt trước HOẶC mặt sau. Ở chế độ nhanh,
        # mặt trước chỉ cần OCR bố cục, mặt sau chỉ cần OCR MRZ; trước đây cả
        # hai lượt đều chạy cho mọi mặt thẻ dù một lượt chắc chắn vô ích.
        folded_text = strip_accents(text).lower()
        has_mrz_marker = bool(re.search(r"\b[1il]dvnm", folded_text))
        stacked_halves = separator_halves or _split_stacked_cccd(image)
        # Quyết định lượt OCR nào thực sự cần chạy thêm, tùy theo đã tách được
        # composite hai mặt hay chưa và ảnh đang xét có dấu hiệu MRZ hay
        # không, để tránh chạy các lượt Tesseract thừa trên CPU máy chủ yếu.
        if fast_mode and separator_halves is not None:
            # PSM 6 vừa đọc riêng mặt trước nên không lặp thêm PSM 11.
            run_layout, run_mrz = False, True
        else:
            run_layout = not fast_mode or not has_mrz_marker or stacked_halves is not None
            run_mrz = not fast_mode or has_mrz_marker or stacked_halves is not None

        # Nếu ảnh là composite hai mặt: OCR bố cục dùng riêng mặt trước, OCR
        # MRZ dùng riêng mặt sau đã tách; nếu chỉ là một mặt thẻ thì cả hai
        # lượt cùng dùng chung ảnh gốc.
        layout_image = prepared
        if stacked_halves is not None:
            front_region, back_region = stacked_halves
            layout_image = _prepare_image(front_region, target_width=target_width)
        else:
            back_region = image

        def _layout_call() -> str:
            # PSM 11 nhận các khối chữ rời của mặt trước tốt hơn PSM 6, trong
            # khi PSM 6 vẫn hữu ích cho bố cục thông thường. Giữ cả hai để
            # extractor chọn ứng viên có nhãn/độ tin cậy tốt hơn.
            try:
                layout_language = "vie" if "vie" in languages else language
                return pytesseract.image_to_string(
                    layout_image, lang=layout_language, config="--oem 3 --psm 11"
                ).strip()
            except pytesseract.TesseractError:
                return ""

        # Ảnh ghép dọc mặt trước/sau (một tệp chứa cả hai mặt) có tỉ lệ khung
        # ảnh của cả composite, không phải của một mặt thẻ nằm ngang. Tách đôi
        # trước khi đọc MRZ, nếu không toạ độ tương đối của vùng MRZ (52%-97%
        # chiều cao ảnh) sẽ trật khỏi mặt sau.
        # CPU máy chủ triển khai (vd. Render free 0.1 CPU) quá yếu để chạy nổi
        # các lượt OCR vùng chuyên biệt (tên/ngày sinh/giới tính/địa chỉ) từng
        # có ở đây: mỗi lượt là một tiến trình Tesseract riêng, tốn vài giây
        # bất kể ảnh to hay nhỏ, và một ảnh CCCD có thể kéo theo hơn chục lượt
        # như vậy. Giờ chỉ giữ lượt đọc bố cục (bổ sung cho lượt đọc chính ở
        # trên) và MRZ. Đổi lại, một số CCCD chụp lóa sáng/mờ/kiểu cũ có thể
        # thiếu tên, ngày sinh hoặc địa chỉ và cần người dùng tự nhập bù.
        # Chỉ tạo đúng số luồng bằng số lượt cần chạy (0, 1 hoặc 2) và chạy
        # song song lượt bố cục với lượt MRZ để giảm thời gian chờ tổng.
        task_count = int(run_layout) + int(run_mrz)
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, task_count))) as executor:
            layout_future = executor.submit(_layout_call) if run_layout else None
            mrz_future = (
                executor.submit(
                    _ocr_cccd_mrz,
                    back_region,
                    languages,
                    language,
                    1400 if fast_mode else 2200,
                )
                if run_mrz
                else None
            )

            if layout_future is not None:
                layout_text = layout_future.result()
                # Chỉ thêm văn bản bố cục nếu có nội dung mới, tránh lặp lại
                # những gì lượt OCR chính (PSM 6) đã đọc được.
                if layout_text and layout_text not in primary_text:
                    page_texts.append(f"--- CCCD BỐ CỤC ---\n{layout_text}")

            if mrz_future is not None:
                mrz_text = mrz_future.result()
                if mrz_text:
                    mrz_texts.append(mrz_text)

    cleaned_text = "\n\n".join(page_texts).strip()
    return OCRResult(
        text=cleaned_text,
        warnings=warnings,
        pages=page_texts,
        mrz_texts=mrz_texts,
    )


def ocr_image(
    image_path: str | Path,
    tesseract_cmd: str | None = None,
    languages: list[str] | None = None,
    max_workers: int = 5,
    target_width: int = 1600,
    preferred_language: str | None = None,
    fast_mode: bool = False,
) -> OCRResult:
    """Mở ảnh từ đường dẫn tệp rồi OCR; bọc lỗi đọc/giải mã ảnh thành ValueError dễ hiểu cho tầng gọi."""
    try:
        if languages is None:
            languages = detect_languages(tesseract_cmd)
        with Image.open(image_path) as image:
            return ocr_pil_image(
                image,
                tesseract_cmd=tesseract_cmd,
                languages=languages,
                max_workers=max_workers,
                target_width=target_width,
                preferred_language=preferred_language,
                fast_mode=fast_mode,
            )
    except (OSError, ValueError) as error:
        raise ValueError("Tệp ảnh không hợp lệ hoặc bị hỏng.") from error
