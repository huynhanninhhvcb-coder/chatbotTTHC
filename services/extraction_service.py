"""Trích xuất dữ liệu công dân từ OCR/PDF theo nguồn tin có độ tin cậy cao.

Hồ sơ thực tế thường chứa nhiều biểu mẫu, ô trống và trang định danh. Vì vậy
không được lấy kết quả khớp đầu tiên trong toàn bộ văn bản: các ứng viên được
phân tích theo từng trang và ưu tiên Phiếu thông tin dân cư/CCCD.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from collections.abc import Iterable

from models.database import ExtractionResult
from utils.text_utils import (
    normalize_bank_account_number,
    normalize_citizen_id,
    normalize_date,
    normalize_phone,
    normalize_text,
    strip_accents,
)


FIELD_NAMES = {
    "full_name": "Họ và tên",
    "date_of_birth": "Ngày sinh",
    "gender": "Giới tính",
    "ethnic_group": "Dân tộc",
    "citizen_id": "Số CCCD/định danh",
    "citizen_id_issue_date": "Ngày cấp CCCD",
    "citizen_id_issue_place": "Nơi cấp CCCD",
    "residence_address": "Nơi cư trú",
    "contact_address": "Nơi ở hiện nay",
    "temporary_address": "Nơi tạm trú",
    "phone_number": "Số điện thoại",
    "occupation": "Nghề nghiệp",
    "employer": "Đơn vị công tác",
    "support_category": "Đối tượng nhận hỗ trợ",
    "support_detail": "Mã số/thông tin đối tượng hỗ trợ",
    "request_content": "Nội dung đề nghị",
    "deceased_death_hour": "Giờ mất",
    "deceased_death_minute": "Phút mất",
    "payment_method": "Phương thức nhận hỗ trợ",
    "cremation_support_category": "Đối tượng hỗ trợ hỏa táng",
    "cremation_support_detail": "Mã số/thông tin đối tượng hỏa táng",
    "benefit_receiving_location": "Nơi nhận trợ cấp",
    "bank_account_name": "Tên chủ tài khoản",
    "bank_account_number": "Số tài khoản",
    "bank_name": "Ngân hàng",
    "guardian_full_name": "Họ tên người giám hộ/khai thay",
    "guardian_date_of_birth": "Ngày sinh người giám hộ",
    "guardian_citizen_id": "CCCD người giám hộ/khai thay",
    "guardian_citizen_id_issue_date": "Ngày cấp CCCD người giám hộ/khai thay",
    "guardian_citizen_id_issue_place": "Nơi cấp CCCD người giám hộ/khai thay",
    "guardian_address": "Địa chỉ người giám hộ/khai thay",
    "guardian_phone": "Số điện thoại người giám hộ",
    "guardian_relationship": "Quan hệ với người đề nghị",
    # Các trường của bộ mẫu mới (10/2026): trợ cấp hưu trí Mẫu 01, mai táng
    # Mẫu 03, tờ khai BHYT và các đơn xác nhận chưa hưởng chế độ.
    "health_insurance_number": "Số thẻ BHYT",
    "social_insurance_number": "Mã số BHXH",
    "email": "Email",
    "birth_registration_ward": "Xã/phường đăng ký khai sinh",
    "birth_registration_province": "Tỉnh/thành phố đăng ký khai sinh",
    "primary_care_facility": "Nơi đăng ký khám chữa bệnh ban đầu",
    "benefit_recipient": "Người nhận tiền trợ cấp",
    "ewallet_number": "Số ví điện tử",
    "ewallet_owner": "Tên chủ ví",
    "ewallet_provider": "Loại ví điện tử",
    "burial_support_category": "Người mất thuộc đối tượng",
    "destination_ward": "Xã/phường nơi nộp hồ sơ hưởng chế độ",
    "subject_relationship": "Người được xác nhận là gì của người khai",
    # Thông tin người đã mất, dùng cho tờ khai hỗ trợ hỏa táng/mai táng. Không
    # nằm trên giấy tờ của người khai nên không thể OCR/AI tự nhận diện được
    # (giống các trường guardian_* ở trên) — luôn cần nhập tay.
    "deceased_full_name": "Họ và tên người mất",
    "deceased_date_of_birth": "Ngày sinh người mất",
    "deceased_gender": "Giới tính người mất",
    "deceased_ethnic_group": "Dân tộc người mất",
    "deceased_nationality": "Quốc tịch người mất",
    "deceased_citizen_id": "CCCD/định danh người mất",
    "deceased_residence_address": "Nơi cư trú người mất",
    "deceased_relationship": "Quan hệ với người mất",
    "deceased_death_date": "Ngày mất",
    "deceased_death_place": "Nơi mất",
    "deceased_death_cause": "Nguyên nhân mất",
    "death_certificate_number": "Số giấy chứng tử/báo tử",
    "death_certificate_date": "Ngày cấp giấy chứng tử/báo tử",
    "death_certificate_issuer": "Nơi cấp giấy chứng tử/báo tử",
    "org_name": "Tên tổ chức lo mai táng/đại diện",
    "org_address": "Địa chỉ tổ chức",
    "org_representative_name": "Người đại diện tổ chức",
    "org_representative_title": "Chức vụ người đại diện",
    "org_phone": "Số điện thoại tổ chức",
    # Đơn xác định mức độ khuyết tật: nội dung đề nghị và địa chỉ riêng của
    # người đại diện hợp pháp không nằm trên giấy tờ của người được xác định
    # khuyết tật, nên luôn cần nhập tay giống các trường guardian_* khác.
    "disability_procedure_type": "Loại đề nghị",
    "guardian_residence_address": "Hộ khẩu thường trú người đại diện",
    "guardian_current_address": "Nơi ở hiện nay người đại diện",
}


@dataclass(frozen=True)
class Candidate:
    """Một giá trị tiềm năng cho một trường dữ liệu, kèm điểm tin cậy để so sánh với các ứng viên khác.

    ``review_required`` đánh dấu ứng viên tuy được chọn nhưng nên cảnh báo
    người dùng rà soát lại (vd. đọc từ vùng ảnh CCCD dễ lỗi dấu).
    """

    value: str
    score: int
    review_required: bool = False


def _empty_fields() -> dict[str, str]:
    """Tạo dict kết quả rỗng với đủ mọi trường đã khai báo trong FIELD_NAMES."""
    return {key: "" for key in FIELD_NAMES}


# Trọng số lặp lại theo chuẩn ICAO 9303 để tính checksum từng ký tự MRZ.
_MRZ_WEIGHTS = (7, 3, 1)
# Bảng sửa các cặp ký tự dễ bị OCR nhầm lẫn (chữ cái <-> chữ số nhìn giống
# nhau) trong những vùng MRZ mà tiêu chuẩn quy định bắt buộc là chữ số.
_MRZ_NUMERIC_TRANSLATION = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"})


def _mrz_checksum(value: str) -> int:
    """Tính chữ số kiểm tra ICAO 9303 cho dữ liệu MRZ."""
    total = 0
    for index, character in enumerate(value):
        if character.isdigit():
            numeric_value = int(character)
        elif "A" <= character <= "Z":
            numeric_value = ord(character) - ord("A") + 10
        elif character == "<":
            numeric_value = 0
        else:
            return -1
        total += numeric_value * _MRZ_WEIGHTS[index % len(_MRZ_WEIGHTS)]
    return total % 10


def _mrz_digits(value: str) -> str:
    """Sửa ký tự dễ nhầm chỉ trong vùng MRZ bắt buộc là chữ số."""
    return value.upper().translate(_MRZ_NUMERIC_TRANSLATION)


def _parse_vietnam_cccd_mrz(mrz_text: str) -> dict[str, str] | None:
    """Đọc MRZ TD1 của CCCD Việt Nam sau khi xác thực checksum.

    Mặt sau thẻ CCCD chứa ba dòng dài 30 ký tự. Chỉ trả dữ liệu khi các
    checksum của số tài liệu, ngày sinh, ngày hết hạn, số định danh và trường
    tổng hợp đều hợp lệ; OCR sai một ký tự sẽ không được dùng để điền đơn.
    """
    lines = [re.sub(r"[^A-Z0-9<]", "", strip_accents(line).upper()) for line in mrz_text.splitlines()]
    lines = ["IDVNM" + line[5:] if re.match(r"^[1L]DVNM", line) else line for line in lines if line]
    for index in range(max(0, len(lines) - 2)):
        first, second, third = lines[index : index + 3]
        # Hai dòng đầu mang checksum nên phải đủ 30 ký tự. Dòng tên không có
        # checksum; crop ảnh đôi khi cắt mất vài dấu "<" đệm ở cuối, vẫn có
        # thể dùng phần tên khi hai dòng đầu đã được xác thực.
        if len(first) != 30 or len(second) != 30 or not 4 <= len(third) <= 30:
            continue
        if not first.startswith("IDVNM") or second[15:18] != "VNM":
            continue

        document_tail = _mrz_digits(first[5:14])
        document_check = _mrz_digits(first[14])
        citizen_id = _mrz_digits(first[15:27])
        citizen_id_check = _mrz_digits(first[29])
        birth = _mrz_digits(second[0:6])
        birth_check = _mrz_digits(second[6])
        sex = second[7]
        expiry = _mrz_digits(second[8:14])
        expiry_check = _mrz_digits(second[14])
        optional_data = second[18:29]
        composite_check = _mrz_digits(second[29])

        if not (
            re.fullmatch(r"\d{9}", document_tail)
            and re.fullmatch(r"\d", document_check)
            and re.fullmatch(r"\d{12}", citizen_id)
            and re.fullmatch(r"\d", citizen_id_check)
            and re.fullmatch(r"\d{6}", birth)
            and re.fullmatch(r"\d", birth_check)
            and sex in {"M", "F"}
            and re.fullmatch(r"\d{6}", expiry)
            and re.fullmatch(r"\d", expiry_check)
            and optional_data == "<" * 11
            and re.fullmatch(r"\d", composite_check)
        ):
            continue

        if (
            not citizen_id.endswith(document_tail)
            or _mrz_checksum(document_tail) != int(document_check)
            or _mrz_checksum(citizen_id + "<<") != int(citizen_id_check)
            or _mrz_checksum(birth) != int(birth_check)
            or _mrz_checksum(expiry) != int(expiry_check)
        ):
            continue

        composite = f"{document_tail}{document_check}{citizen_id}<<{citizen_id_check}{birth}{birth_check}{expiry}{expiry_check}{optional_data}"
        if _mrz_checksum(composite) != int(composite_check):
            continue

        gender_century_code = int(citizen_id[3])
        expected_sex = "M" if gender_century_code % 2 == 0 else "F"
        if sex != expected_sex:
            continue
        birth_year = 1900 + (gender_century_code // 2) * 100 + int(birth[:2])
        try:
            birth_date = date(birth_year, int(birth[2:4]), int(birth[4:6]))
            # Chỉ kiểm tra ngày/tháng hết hạn; năm không phải trường cần điền.
            date(2000 + int(expiry[:2]), int(expiry[2:4]), int(expiry[4:6]))
        except ValueError:
            continue

        if not re.fullmatch(r"[A-Z<]+", third):
            continue
        name = _clean_name(third.replace("<", " "))
        if not _is_plausible_name(name):
            continue
        return {
            "citizen_id": citizen_id,
            "date_of_birth": birth_date.strftime("%d/%m/%Y"),
            "gender": "Nam" if sex == "M" else "Nữ",
            "full_name": name,
        }
    return None


def _page_score(page: str) -> int:
    """Chấm điểm một trang theo mức độ giống trang định danh (Phiếu dân cư/CCCD/GPLX).

    Điểm này là base_score dùng chung cho mọi ứng viên trích được từ trang đó,
    để ưu tiên dữ liệu lấy từ trang định danh đáng tin cậy hơn các trang đơn từ
    thông thường (vd. văn bản đề nghị hưởng trợ cấp có thể lặp lại các nhãn
    tương tự nhưng của người khác, như cha/mẹ/người giám hộ trẻ em).
    """
    folded = strip_accents(page).lower()
    score = 0
    if "phieu thong tin dan cu" in folded:
        score += 90
    if "thong tin ca nhan" in folded:
        score += 30
    if "so dinh danh" in folded:
        score += 20
    if "noi o hien tai" in folded:
        score += 15
    if re.search(r"can\s*cuo\W*c", folded) or "citizen identity" in folded or "idvnm" in folded:
        score += 30
    if "giay phep lai xe" in folded or "driver's license" in folded or "drivers license" in folded:
        score += 25
    if "cccd bo cuc" in folded:
        score += 15
    if "cccd chi tiet" in folded:
        score += 60
    if "cccd dia chi" in folded:
        score += 60
    if "to khai tham gia" in folded or "bao hiem xa hoi" in folded:
        score += 5
    # Các mẫu dưới đây không phải trang định danh: trừ điểm để tránh lấy nhầm
    # dữ liệu từ đơn đề nghị (văn bản do người khác điền hộ) hoặc từ mục khai
    # thông tin cha/mẹ/người giám hộ của trẻ em trong cùng hồ sơ.
    if "van ban de nghi huong tro cap" in folded:
        score -= 25
    if "ho ten cha" in folded or "ho ten cha/me" in folded or "giam ho doi voi tre em" in folded:
        score -= 40
    return score


def _matched_groups(page: str, pattern: str, flags: re.RegexFlag = re.IGNORECASE | re.MULTILINE) -> list[tuple[str, ...]]:
    """Chạy pattern không dấu rồi trả về phần khớp nguyên bản (còn dấu)."""
    page = normalize_text(page)
    folded = strip_accents(page)
    results: list[tuple[str, ...]] = []
    for match in re.finditer(pattern, folded, flags):
        results.append(tuple(page[match.start(index) : match.end(index)] for index in range(1, len(match.groups()) + 1)))
    return results


def _clean_name(value: str) -> str:
    """Cắt bỏ phần nhãn trường kế tiếp lỡ dính vào chuỗi họ tên rồi loại ký tự thừa.

    Ví dụ input "Nguyễn Văn A Số định danh: 012..." bị cắt tại "Số định danh"
    để chỉ còn lại tên. Sau đó bỏ ký tự không phải chữ cái và các từ đơn lẻ
    một ký tự ở đầu/cuối (thường là ký tự nhiễu OCR còn sót lại).
    """
    value = re.split(r"\b(?:số|so)\s+(?:định danh|dinh danh|cccd|cmnd)|\b(?:ngày|ngay)\s+sinh|\b(?:giới|gioi)\s+tính", value, flags=re.IGNORECASE)[0]
    value = re.sub(r"[^A-Za-zÀ-ỹĐđ\s]", " ", value)
    words = normalize_text(value).split()
    while words and len(strip_accents(words[-1])) == 1:
        words.pop()
    while words and len(strip_accents(words[0])) == 1:
        words.pop(0)
    return " ".join(words)


def _is_plausible_name(value: str) -> bool:
    """Lọc bớt ứng viên tên vô lý: quá ngắn/dài, chỉ một từ, lặp ký tự liên tục, hoặc chứa từ khoá của nhãn khác (cha/mẹ/giám hộ...)."""
    folded = strip_accents(value).lower()
    letters = re.sub(r"[^a-z]", "", folded)
    words = letters and re.findall(r"[a-z]+", folded)
    if not letters or len(value) < 4 or len(value) > 60 or len(words) < 2:
        return False
    if re.search(r"(.)\1{4,}", letters):
        return False
    forbidden = {"cha", "me", "giam", "ho", "doi", "voi", "tre", "em", "duoi"}
    return not any(word in forbidden for word in words)


def _restore_name_from_compact_cccd(page_texts: Iterable[str], selected_name: str) -> str:
    """Khôi phục dấu từ dòng họ tên liền chữ trên ảnh CCCD nếu có.

    OCR của Phiếu dân cư đôi lúc làm rơi một vài dấu, trong khi ảnh CCCD ở trang
    kế tiếp lại có đúng ký tự nhưng không có khoảng trắng (VD: TRỊNHTHỊVIỆTNAM).
    """
    tokens = selected_name.split()
    target = "".join(strip_accents(token) for token in tokens).lower()
    if len(tokens) < 2 or len(target) < 6:
        return selected_name
    token_lengths = [len(strip_accents(token)) for token in tokens]
    for page in page_texts:
        page = normalize_text(page)
        folded_page = strip_accents(page).lower()
        start = folded_page.find(target)
        while start >= 0:
            raw_fragment = page[start : start + len(target)]
            compact_raw = re.sub(r"[^A-Za-zÀ-ỹĐđ]", "", raw_fragment)
            if len(compact_raw) == len(target) and strip_accents(compact_raw).lower() == target:
                pieces: list[str] = []
                offset = 0
                for length in token_lengths:
                    pieces.append(compact_raw[offset : offset + length])
                    offset += length
                restored = " ".join(pieces)
                if restored != selected_name:
                    return restored
            start = folded_page.find(target, start + 1)
    return selected_name


def _clean_address(value: str) -> str:
    """Làm sạch một đoạn địa chỉ thô đã khớp regex: cắt nhãn/kỹ tự thừa dính kèm và chuẩn hoá cách viết TP.HCM."""
    folded_value = strip_accents(value)
    # Phiếu dân cư có thể đặt nhãn "Nơi đăng ký khai sinh" ngay sau địa chỉ
    # thường trú. Nếu OCR gộp hai dòng, phần nhãn này không được đi vào đơn.
    next_field = re.search(r"\s+noi\s+dang\s+ky\s+khai\s+sinh\s*:", folded_value, re.IGNORECASE)
    if next_field:
        value = value[: next_field.start()]
        folded_value = folded_value[: next_field.start()]
    trailing_document_text = re.search(
        r"\s+(?:[il|]\s+)?tp\.?\s+ho\s+chi\s+minh\s*,?\s*ngay",
        folded_value,
        re.IGNORECASE,
    )
    if trailing_document_text:
        value = value[: trailing_document_text.start()]
    clean_lines: list[str] = []
    for line in value.splitlines():
        line = line.strip()
        line = re.sub(r"^[A-Za-zÀ-ỹĐđ]\s+(?=[A-Za-zÀ-ỹĐđ0-9])", "", line)
        line = re.sub(r"\s+[A-Za-zÀ-ỹĐđ]$", "", line)
        line = re.sub(r"\s+[a-z][A-Z]$", "", line)
        if line:
            clean_lines.append(line)
    value = "\n".join(clean_lines)
    value = re.sub(r"[.·_]{4,}", " ", value)
    value = re.sub(r"^[\s|:;,.—–_-]+", "", value)
    cleaned = normalize_text(value).replace("\n", " ")
    cleaned = re.sub(
        r"\bTP\s*\.?\s*H[oồôóòọõơ]+\s*Ch[iíìịỉĩ]\s*M[iíìịỉĩ]nh\b",
        "TP. Hồ Chí Minh",
        cleaned,
        flags=re.IGNORECASE,
    )
    # OCR vùng CCCD đôi khi chèn "= 43" từ ngày hết hạn giữa hai dòng địa chỉ.
    cleaned = re.sub(r"\s*=\s*(?:\d{1,2}\s+)?(?=[A-Za-zÀ-ỹĐđ])", " ", cleaned)
    return re.sub(r"\s*[:~\-]+$", "", cleaned).strip()


def _is_plausible_address(value: str) -> bool:
    """Loại các chuỗi quá ngắn/dài hoặc gần như không có chữ cái để không nhận nhầm thành địa chỉ."""
    return 8 <= len(value) <= 220 and len(re.sub(r"[^A-Za-zÀ-ỹĐđ]", "", value)) >= 5


def _add(candidates: list[Candidate], value: str, score: int, validator=None, *, review_required: bool = False) -> None:
    """Chuẩn hoá value rồi thêm vào danh sách ứng viên nếu không rỗng và qua được validator (nếu có)."""
    value = normalize_text(value)
    if value and (validator is None or validator(value)):
        candidates.append(Candidate(value=value, score=score, review_required=review_required))


def _pick(candidates: Iterable[Candidate]) -> tuple[str, str]:
    """Chọn ứng viên điểm cao nhất và suy ra mức độ tin cậy (missing/medium/high) để trả về cho client."""
    winner = _best_candidate(candidates)
    if winner is None:
        return "", "missing"
    if winner.score < 0:
        return "", "missing"
    return winner.value, "medium" if winner.review_required or winner.score < 80 else "high"


def _best_candidate(candidates: Iterable[Candidate]) -> Candidate | None:
    """Trả về ứng viên có điểm cao nhất, hoặc None nếu danh sách rỗng."""
    candidate_list = list(candidates)
    return max(candidate_list, key=lambda candidate: candidate.score) if candidate_list else None


def _name_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò họ tên bằng nhiều mẫu khác nhau tuỳ loại giấy tờ (Phiếu dân cư, CCCD song ngữ, GPLX, MRZ mặt sau CCCD)."""
    candidates: list[Candidate] = []
    # Phiếu thông tin dân cư có thể chia họ tên qua hai dòng sau cột số định danh.
    registry_pattern = (
        r"ho\s+(?:va\s+)?ten\s*:\s*([^\n]{2,80}?)\s+so\s+dinh\s+danh\s*:.*?"
        r"\n\s*([a-z\s.]{2,60}?)\s+(?:\d[ .-]?){11}\d"
    )
    for first, second in _matched_groups(page, registry_pattern, re.IGNORECASE | re.MULTILINE | re.DOTALL):
        _add(candidates, _clean_name(f"{first} {second}"), base_score + 100, _is_plausible_name)

    # Khớp nhãn "Họ và tên:" / "Họ, chữ đệm, tên:" trên đơn/tờ khai, có thể có
    # số thứ tự mục phía trước (vd. "1. Họ và tên:" hoặc "[1] Họ và tên:").
    direct_label_pattern = (
        r"(?:^|\n)\s*(?:\[\d+(?:\.\d+)?\]\.?\s*)?(?:\d+\.\s*)?"
        r"ho\s*(?:va|,)?\s*(?:chu\s+dem,?\s*)?ten(?:\s*\([^\n)]*\))?\s*:\s*([^\n]{2,120})"
    )
    for (value,) in _matched_groups(page, direct_label_pattern):
        _add(candidates, _clean_name(value), base_score + 60, _is_plausible_name)

    # CCCD dùng nhãn song ngữ “Họ và tên / Full name:” và in họ tên ở dòng
    # sau. Một số bản OCR có thêm “= >” đầu dòng, vì vậy bỏ phần nhiễu trước
    # ký tự chữ cái đầu tiên rồi mới làm sạch tên.
    next_line_pattern = (
        r"(?:ho\s*(?:va\s*)?ten|full\s+name)[^\n]{0,100}\n"
        r"\s*(?:[^a-z\n]{0,12})?([a-z][a-z .]{3,80})"
    )
    for (value,) in _matched_groups(page, next_line_pattern):
        _add(candidates, _clean_name(value), base_score + 75, _is_plausible_name)

    # GPLX và một số thẻ cũ in giá trị cùng dòng với nhãn song ngữ.
    same_line_pattern = (
        r"(?:^|\n)\s*[^a-z\n]{0,12}(?:ho\s*(?:va\s*)?ten|full\s+name)"
        r"(?:\s*/\s*(?:ho\s*(?:va\s*)?ten|full\s+name))?\s*:\s*([a-z][a-z .]{3,80})"
    )
    for (value,) in _matched_groups(page, same_line_pattern):
        _add(candidates, _clean_name(value), base_score + 70, _is_plausible_name)

    # Mặt sau CCCD chứa MRZ không dấu: NGUYEN<<LE<HOANG<YEN. Trường này chỉ
    # hỗ trợ tên; CCCD/ngày sinh/giới tính từ MRZ phải qua checksum riêng.
    for line in normalize_text(page).splitlines():
        compact = re.sub(r"\s+", "", strip_accents(line).upper())
        if compact.count("<") < 2 or re.search(r"\d", compact) or not re.fullmatch(r"[A-Z<]+", compact):
            continue
        _add(candidates, _clean_name(compact.replace("<", " ")), base_score + 80, _is_plausible_name)
    return candidates


def _date_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò ngày sinh: ưu tiên ngày đi kèm nhãn "ngày sinh/date of birth", còn ngày dd/mm/yyyy trôi nổi chỉ nhận điểm thấp làm phương án dự phòng."""
    candidates: list[Candidate] = []
    labelled = r"(?:ngay\s*,?\s*thang\s*,?\s*nam\s*sinh|ngay\s+sinh|sinh\s+ngay|date\s+of\s+birth)\s*[:\-–]?\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})"
    for (value,) in _matched_groups(page, labelled):
        _add(candidates, normalize_date(value), base_score + 45)
    for (value,) in _matched_groups(page, r"(?<!\d)(\d{1,2}[/.\-]\d{1,2}[/.\-](?:19|20)\d{2})(?!\d)"):
        _add(candidates, normalize_date(value), base_score + 10)
    return candidates


def _citizen_id_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò dãy 12 chữ số (cho phép có khoảng trắng/dấu ngăn cách) và chỉ nhận khi trang có ngữ cảnh CCCD/định danh."""
    page_normalized = normalize_text(page)
    folded = strip_accents(page_normalized).lower()
    candidates: list[Candidate] = []
    for match in re.finditer(r"(?<!\d)(?:\d[ .-]?){11}\d(?!\d)", folded):
        value = normalize_citizen_id(page_normalized[match.start() : match.end()])
        context = folded[max(0, match.start() - 55) : match.start()]
        label_bonus = 30 if any(label in context for label in ("so dinh danh", "so cccd", "can cuoc", "cccd", "cmnd")) else 0
        page_is_identity_card = bool(
            re.search(r"can\s*cuo\W*c", folded, re.IGNORECASE)
            or "citizen identity" in folded.lower()
            or "idvnm" in folded.lower()
        )
        # A disability-card serial or driving-licence number can also contain
        # twelve digits.  Never put it into the CCCD field without an identity
        # label/card context.
        if not label_bonus and not page_is_identity_card:
            continue
        _add(candidates, value, base_score + 35 + label_bonus, lambda item: bool(re.fullmatch(r"\d{12}", item)))
    return candidates


def _gender_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò giới tính sau nhãn "Giới tính/Sex", loại trừ nhầm lẫn với chữ "Nam" trong "Việt Nam" của trường Quốc tịch."""
    candidates: list[Candidate] = []
    folded = strip_accents(normalize_text(page))
    pattern = r"(?:g(?:ioi|idi)\s*tinh|sex)(?:\s*/\s*(?:g(?:ioi|idi)\s*tinh|sex))?\s*[:\-]?\s*([^\n]{0,28})"
    for match in re.finditer(pattern, folded, re.IGNORECASE):
        # Không để “Việt Nam” của trường Quốc tịch bị hiểu nhầm là giới tính Nam.
        nearby = re.split(r"(?:quoc\s*tich|nationality)", match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
        value_match = re.search(r"\b(nam|nu|ni|ny)\b", nearby, re.IGNORECASE)
        if not value_match:
            continue
        token = value_match.group(1).lower()
        value = "Nam" if token == "nam" else "Nữ"
        # “Ni/Ny” thường là lỗi OCR của “Nữ”, cần cho người dân rà soát thêm.
        score = base_score + (50 if token in {"nam", "nu"} else 38)
        _add(candidates, value, score)
    return candidates


def _ethnic_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò dân tộc sau nhãn "Dân tộc:", cắt bỏ phần nhãn trường kế tiếp lỡ dính vào cùng dòng."""
    candidates: list[Candidate] = []
    for (value,) in _matched_groups(page, r"dan\s+toc\s*:\s*([^\n]{2,60})"):
        # Cắt tại từ khoá của nhãn kế tiếp (số/ngày/giới/quốc...) nếu OCR gộp chung một dòng.
        value = re.split(r"\b(?:so|ngay|gioi|quoc)\b", strip_accents(value), maxsplit=1, flags=re.IGNORECASE)[0]
        _add(candidates, normalize_text(value).title(), base_score + 30, lambda item: 2 <= len(item) <= 35)
    return candidates


def _address_candidates(page: str, base_score: int, labels: str, label_bonus: int) -> list[Candidate]:
    """Dò địa chỉ nhiều dòng sau một nhãn cho trước, dừng lại khi gặp nhãn của trường kế tiếp."""
    candidates: list[Candidate] = []
    # Cho phép địa chỉ xuống dòng; dừng khi gặp nhãn của trường kế tiếp.
    stop = r"(?:dia\s+chi|noi\s+o|noi\s+cu\s+tru|thong\s+tin|quan\s+he|ho\s+va\s+ten|ngay\s+sinh|ngay\s*,?\s*thang\s*,?\s*nam\s*/?\s*date|dac\s+diem\s+nhan\s+dang|cuc\s+truong|idvnm|gioi\s*tinh|dan\s+toc|quoc\s+tich|nationality|que\s+quan|place\s+of\s+origin|dang\s+khuyet\s+tat|muc\s+do\s+khuyet\s+tat|co\s+gia\s+tri\s+den|date\s+of\s+expiry|hang\s*/?\s*class|tp\.?\s*ho\s+chi\s+minh\s*,?\s*ngay|tuq\.?\s*giam|so\s+(?:dinh\s+danh|cccd|dien\s+thoai))"
    # Nhãn CCCD thường là “Nơi thường trú / Place of residence:”.
    pattern = rf"(?:{labels})(?:\s*/\s*[^\n:]+)?\s*:\s*(.+?)(?=\n\s*{stop}\b|\Z)"
    review_required = any(
        marker in strip_accents(page).lower()
        for marker in ("cccd dia chi", "cccd chi tiet")
    )
    for (value,) in _matched_groups(page, pattern, re.IGNORECASE | re.MULTILINE | re.DOTALL):
        _add(
            candidates,
            _clean_address(value),
            base_score + label_bonus,
            _is_plausible_address,
            review_required=review_required,
        )
    return candidates


def _clean_bank_line(value: str) -> str:
    """Nối các dòng của một giá trị nhiều dòng (vd. tên ngân hàng bị ngắt dòng) thành một chuỗi duy nhất."""
    return " ".join(part.strip() for part in value.splitlines() if part.strip())


def _is_plausible_bank_name(value: str) -> bool:
    """Kiểm tra sơ bộ độ dài và có ít nhất một chữ cái để loại chuỗi rác."""
    return 3 <= len(value) <= 150 and bool(re.search(r"[A-Za-zÀ-ỹĐđ]", value))


def _bank_account_name_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò tên chủ tài khoản ngân hàng sau nhãn "(Tên) chủ tài khoản"."""
    candidates: list[Candidate] = []
    pattern = r"(?:ten\s+)?chu\s+tai\s+khoan\s*:?\s*([^\n]{2,80})"
    for (value,) in _matched_groups(page, pattern):
        _add(candidates, _clean_name(value), base_score + 55, _is_plausible_name)
    return candidates


def _bank_account_number_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò số tài khoản ngân hàng (6-19 chữ số, có thể xen khoảng trắng/dấu ngăn cách) sau nhãn "Số tài khoản"."""
    candidates: list[Candidate] = []
    pattern = r"so\s+tai\s+khoan(?:\s+ngan\s+hang)?\s*:?\s*([\d](?:[\d .-]{4,24})?[\d])"
    for (value,) in _matched_groups(page, pattern):
        _add(
            candidates,
            normalize_bank_account_number(value),
            base_score + 50,
            lambda item: bool(re.fullmatch(r"\d{6,19}", item)),
        )
    return candidates


def _bank_name_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò tên ngân hàng, hỗ trợ cả dạng có nhãn "Ngân hàng:" lẫn dạng in trực tiếp tên đầy đủ không nhãn trên sao kê/thẻ."""
    candidates: list[Candidate] = []
    stop = (
        r"(?:ten\s+chu\s+tai\s+khoan|chu\s+tai\s+khoan|so\s+tai\s+khoan|"
        r"ho\s+va\s+ten|dia\s+chi|so\s+dien\s+thoai|ngay\s+sinh)"
    )
    # Nhãn tách rời giá trị bằng dấu hai chấm, ví dụ "Ngân hàng: Vietcombank".
    labelled = rf"ngan\s+hang\s*:\s*(.+?)(?=\n\s*{stop}\b|\n\s*\n|\Z)"
    for (value,) in _matched_groups(page, labelled, re.IGNORECASE | re.MULTILINE | re.DOTALL):
        _add(candidates, _clean_bank_line(value), base_score + 55, _is_plausible_bank_name)
    # Sao kê/thẻ ngân hàng thường in thẳng tên đầy đủ, không có dấu hai chấm,
    # đôi khi xuống dòng: "NGÂN HÀNG NÔNG NGHIỆP VÀ PHÁT TRIỂN\nNÔNG THÔN...".
    standalone = rf"(?:^|\n)\s*(ngan\s+hang\b.*?)(?=\n\s*{stop}\b|\n\s*\n|\Z)"
    for (value,) in _matched_groups(page, standalone, re.IGNORECASE | re.MULTILINE | re.DOTALL):
        _add(candidates, _clean_bank_line(value), base_score + 40, _is_plausible_bank_name)
    return candidates


_DEATH_EXTRACT_BASE_SCORE = 80


def _is_death_extract_page(page: str) -> bool:
    """Nhận diện trang Trích lục khai tử để đọc thông tin người mất riêng.

    Một trang như vậy mô tả người đã mất, không phải người đề nghị. Nếu vẫn
    chạy các hàm nhận diện họ tên/CCCD/ngày sinh/giới tính/dân tộc của người
    đề nghị trên trang này, dữ liệu người mất sẽ bị lẫn vào hồ sơ người đề
    nghị. Vì vậy trang này được xử lý bằng bộ hàm _deceased_* riêng, tách
    khỏi bộ hàm nhận diện danh tính người đề nghị ở trên.
    """
    folded = strip_accents(page).lower()
    return "trich luc khai tu" in folded or "da chet vao" in folded


def _deceased_name_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò họ tên người đã mất trên trang Trích lục khai tử, theo nhãn "Họ, chữ đệm, tên:"."""
    candidates: list[Candidate] = []
    pattern = r"ho\s*,?\s*chu\s*dem\s*,?\s*ten\s*:\s*([^\n]{2,80})"
    for (value,) in _matched_groups(page, pattern):
        _add(candidates, _clean_name(value), base_score + 60, _is_plausible_name)
    return candidates


def _deceased_date_of_birth_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò ngày sinh người đã mất trên trang Trích lục khai tử."""
    candidates: list[Candidate] = []
    pattern = r"ngay\s*,?\s*thang\s*,?\s*nam\s*sinh\s*:\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})"
    for (value,) in _matched_groups(page, pattern):
        _add(candidates, normalize_date(value), base_score + 60)
    return candidates


def _deceased_gender_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò giới tính người đã mất trên trang Trích lục khai tử."""
    candidates: list[Candidate] = []
    folded = strip_accents(normalize_text(page))
    for match in re.finditer(r"gioi\s*tinh\s*:\s*(nam|nu)\b", folded, re.IGNORECASE):
        token = match.group(1).lower()
        _add(candidates, "Nam" if token == "nam" else "Nữ", base_score + 60)
    return candidates


def _deceased_ethnic_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò dân tộc người đã mất, cắt bỏ phần nhãn trường kế tiếp nếu OCR gộp chung dòng."""
    candidates: list[Candidate] = []
    for (value,) in _matched_groups(page, r"dan\s*toc\s*:\s*([^\n]{2,40})"):
        value = re.split(r"\b(?:quoc|so|ngay)\b", strip_accents(value), maxsplit=1, flags=re.IGNORECASE)[0]
        _add(candidates, normalize_text(value).title(), base_score + 40, lambda item: 2 <= len(item) <= 35)
    return candidates


def _deceased_nationality_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò quốc tịch người đã mất trên trang Trích lục khai tử."""
    candidates: list[Candidate] = []
    for (value,) in _matched_groups(page, r"quoc\s*tich\s*:\s*([^\n]{2,40})"):
        _add(candidates, normalize_text(value), base_score + 40, lambda item: 2 <= len(item) <= 40)
    return candidates


def _deceased_citizen_id_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò số định danh cá nhân của người đã mất trên trang Trích lục khai tử."""
    candidates: list[Candidate] = []
    pattern = r"so\s*dinh\s*danh\s*ca\s*nhan\s*:\s*((?:\d[ .-]?){11}\d)"
    for (value,) in _matched_groups(page, pattern):
        _add(candidates, normalize_citizen_id(value), base_score + 60, lambda item: bool(re.fullmatch(r"\d{12}", item)))
    return candidates


def _deceased_death_date_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò ngày mất theo cụm "đã chết vào lúc ... ngày dd/mm/yyyy" trên Trích lục khai tử."""
    candidates: list[Candidate] = []
    pattern = r"da\s*chet\s*vao\s*luc[^\n]{0,60}?ngay\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})"
    for (value,) in _matched_groups(page, pattern, re.IGNORECASE | re.MULTILINE | re.DOTALL):
        _add(candidates, normalize_date(value), base_score + 60)
    return candidates


def _deceased_death_place_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò nơi mất theo nhãn "Nơi chết:" trên Trích lục khai tử."""
    candidates: list[Candidate] = []
    for (value,) in _matched_groups(page, r"noi\s*chet\s*:\s*([^\n]{5,150})"):
        _add(candidates, normalize_text(value), base_score + 40, _is_plausible_address)
    return candidates


def _death_certificate_issuer_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò nơi đăng ký khai tử theo cụm "đã được đăng ký khai tử tại: ..." trước dòng "Số:"."""
    candidates: list[Candidate] = []
    pattern = r"da\s*duoc\s*dang\s*ky\s*khai\s*tu\s*tai\s*:\s*(.+?)(?=\n\s*so\s*:|\Z)"
    for (value,) in _matched_groups(page, pattern, re.IGNORECASE | re.MULTILINE | re.DOTALL):
        value = normalize_text(value).replace("\n", " ")
        _add(candidates, value, base_score + 40, lambda item: 5 <= len(item) <= 200)
    return candidates


def _death_certificate_number_and_date_candidates(
    page: str, base_score: int
) -> tuple[list[Candidate], list[Candidate]]:
    """Đọc dòng "Số: .../... ngày .. tháng .. năm ...." đăng ký khai tử.

    Dòng "Số:" ở đầu trích lục (mã bản trích lục, kiểu .../TLKT-BS) không có
    "ngày ... tháng ... năm ..." theo ngay sau nên không khớp pattern này;
    chỉ dòng đăng ký khai tử thật mới có đủ cả hai phần.
    """
    number_candidates: list[Candidate] = []
    date_candidates: list[Candidate] = []
    pattern = r"so\s*:\s*(\d+\s*/\s*\d+)\s*ngay\s*(\d{1,2})\s*thang\s*(\d{1,2})\s*nam\s*(\d{4})"
    for number, day, month, year in _matched_groups(page, pattern, re.IGNORECASE | re.MULTILINE):
        _add(
            number_candidates,
            re.sub(r"\s+", "", number),
            base_score + 60,
            lambda item: bool(re.fullmatch(r"\d+/\d+", item)),
        )
        _add(date_candidates, f"{int(day):02d}/{int(month):02d}/{year}", base_score + 60)
    return number_candidates, date_candidates


def _phone_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò số điện thoại Việt Nam (bắt đầu bằng 0 hoặc +84, đủ 10 số) xuất hiện trên trang, không cần nhãn đi kèm."""
    candidates: list[Candidate] = []
    folded = strip_accents(normalize_text(page))
    for match in re.finditer(r"(?<!\d)(?:\+84|0)(?:[ .-]?\d){9}(?![\d .-])", folded):
        value = normalize_phone(match.group(0))
        _add(candidates, value, base_score + 20, lambda item: bool(re.fullmatch(r"0\d{9}", item)))
    return candidates


def _single_line_labeled_candidates(
    page: str,
    base_score: int,
    label_pattern: str,
    *,
    maximum_length: int = 160,
) -> list[Candidate]:
    """Đọc một giá trị trên cùng dòng sau nhãn, dùng cho trường mẫu NQ32/NQ40."""
    candidates: list[Candidate] = []
    for (value,) in _matched_groups(page, rf"{label_pattern}\s*:\s*([^\n]{{1,{maximum_length}}})"):
        value = re.split(r"\.{3,}", value, maxsplit=1)[0]
        _add(
            candidates,
            normalize_text(value),
            base_score + 35,
            lambda item: 1 <= len(item) <= maximum_length,
        )
    return candidates


def _citizen_issue_date_candidates(page: str, base_score: int) -> list[Candidate]:
    """Dò ngày cấp CCCD, theo nhãn "Ngày cấp:" hoặc nhãn song ngữ "Ngày, tháng, năm/Date...:"."""
    candidates: list[Candidate] = []
    patterns = (
        r"ngay\s*cap\s*:\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
        r"ngay\s*,?\s*thang\s*,?\s*nam(?:\s*/[^\n:]{0,45})?\s*:\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
    )
    for pattern in patterns:
        for (value,) in _matched_groups(page, pattern):
            _add(candidates, normalize_date(value), base_score + 40)
    return candidates


def _split_pages(raw_text: str, page_texts: list[str] | None) -> list[str]:
    """Tách văn bản OCR thành danh sách từng trang, ưu tiên dùng page_texts có sẵn nếu được truyền vào."""
    if page_texts:
        return [normalize_text(page) for page in page_texts if normalize_text(page)]
    # Không có page_texts (vd. văn bản gộp từ nhiều nguồn): tách theo dấu mốc
    # "--- TRANG N ---" do bước OCR/PDF chèn vào giữa các trang.
    marked_pages = re.split(r"(?:^|\n)---\s*TRANG\s+\d+\s*---\s*\n", raw_text, flags=re.IGNORECASE)
    return [normalize_text(page) for page in marked_pages if normalize_text(page)]


def extract_personal_information(
    raw_text: str,
    page_texts: list[str] | None = None,
    cccd_mrz_texts: list[str] | None = None,
) -> ExtractionResult:
    """Hàm chính: trích xuất toàn bộ thông tin cá nhân từ văn bản OCR/PDF của hồ sơ.

    Xử lý theo từng trang riêng biệt để tính base_score (mức tin cậy của trang)
    rồi thu thập ứng viên cho từng trường bằng các hàm ``_*_candidates`` ở trên.
    Trang Trích lục khai tử được xử lý bằng bộ hàm ``_deceased_*`` riêng để
    không lẫn thông tin người mất vào thông tin người đề nghị. Dữ liệu MRZ mặt
    sau CCCD (đã qua checksum) được cộng thêm với điểm rất cao vì đáng tin cậy
    hơn OCR toàn ảnh. Cuối cùng, với mỗi trường chọn ứng viên điểm cao nhất
    (``_pick``) và áp dụng vài quy tắc suy luận địa chỉ khi hồ sơ chỉ có một
    trong hai loại địa chỉ (cư trú/liên lạc).
    """
    pages = _split_pages(raw_text, page_texts)
    fields = _empty_fields()
    confidence = {key: "missing" for key in FIELD_NAMES}
    field_candidates: dict[str, list[Candidate]] = {key: [] for key in FIELD_NAMES}
    residence_candidates: list[Candidate] = []
    contact_candidates: list[Candidate] = []

    for page in pages:
        base_score = _page_score(page)
        if _is_death_extract_page(page):
            # Trang này mô tả người đã mất, không phải người đề nghị: đọc
            # bằng bộ hàm _deceased_* riêng thay vì hàm nhận diện danh tính
            # người đề nghị ở nhánh else, để không lẫn hai người vào nhau.
            field_candidates["deceased_full_name"].extend(_deceased_name_candidates(page, _DEATH_EXTRACT_BASE_SCORE))
            field_candidates["deceased_date_of_birth"].extend(
                _deceased_date_of_birth_candidates(page, _DEATH_EXTRACT_BASE_SCORE)
            )
            field_candidates["deceased_gender"].extend(_deceased_gender_candidates(page, _DEATH_EXTRACT_BASE_SCORE))
            field_candidates["deceased_ethnic_group"].extend(
                _deceased_ethnic_candidates(page, _DEATH_EXTRACT_BASE_SCORE)
            )
            field_candidates["deceased_nationality"].extend(
                _deceased_nationality_candidates(page, _DEATH_EXTRACT_BASE_SCORE)
            )
            field_candidates["deceased_citizen_id"].extend(
                _deceased_citizen_id_candidates(page, _DEATH_EXTRACT_BASE_SCORE)
            )
            field_candidates["deceased_death_date"].extend(
                _deceased_death_date_candidates(page, _DEATH_EXTRACT_BASE_SCORE)
            )
            field_candidates["deceased_death_place"].extend(
                _deceased_death_place_candidates(page, _DEATH_EXTRACT_BASE_SCORE)
            )
            field_candidates["death_certificate_issuer"].extend(
                _death_certificate_issuer_candidates(page, _DEATH_EXTRACT_BASE_SCORE)
            )
            number_candidates, date_candidates = _death_certificate_number_and_date_candidates(
                page, _DEATH_EXTRACT_BASE_SCORE
            )
            field_candidates["death_certificate_number"].extend(number_candidates)
            field_candidates["death_certificate_date"].extend(date_candidates)
            continue
        field_candidates["full_name"].extend(_name_candidates(page, base_score))
        field_candidates["date_of_birth"].extend(_date_candidates(page, base_score))
        field_candidates["citizen_id"].extend(_citizen_id_candidates(page, base_score))
        field_candidates["citizen_id_issue_date"].extend(_citizen_issue_date_candidates(page, base_score))
        field_candidates["citizen_id_issue_place"].extend(
            _single_line_labeled_candidates(page, base_score, r"noi\s*cap")
        )
        field_candidates["gender"].extend(_gender_candidates(page, base_score))
        field_candidates["ethnic_group"].extend(_ethnic_candidates(page, base_score))
        # "Nơi ở hiện tại/hiện nay" là chỗ đang sinh sống, gần nghĩa "Địa chỉ
        # liên lạc" trên đơn hơn là "Nơi cư trú" (nơi thường trú/hộ khẩu chính
        # thức) — đưa hai nhãn này vào contact_candidates thay vì residence.
        contact_candidates.extend(_address_candidates(page, base_score, r"noi\s+o\s+hien\s+tai", 45))
        contact_candidates.extend(_address_candidates(page, base_score, r"noi\s+o\s+hien\s+nay", 45))
        residence_candidates.extend(_address_candidates(page, base_score, r"dia\s+chi\s+thuong\s+tru", 40))
        residence_candidates.extend(_address_candidates(page, base_score, r"noi\s+thuong\s+tru", 55))
        residence_candidates.extend(_address_candidates(page, base_score, r"place\s+of\s+residence", 45))
        residence_candidates.extend(_address_candidates(page, base_score, r"no[i1]?\s+cu\s+tru", 25))
        contact_candidates.extend(_address_candidates(page, base_score, r"dia\s+chi\s+lien\s+lac", 25))
        field_candidates["temporary_address"].extend(
            _single_line_labeled_candidates(page, base_score, r"noi\s*tam\s*tru(?:\s*\([^\n)]*\))?")
        )
        field_candidates["phone_number"].extend(_phone_candidates(page, base_score))
        field_candidates["occupation"].extend(
            _single_line_labeled_candidates(page, base_score, r"nghe\s*nghiep")
        )
        field_candidates["employer"].extend(
            _single_line_labeled_candidates(page, base_score, r"don\s*vi\s*cong\s*tac")
        )
        field_candidates["bank_account_name"].extend(_bank_account_name_candidates(page, base_score))
        field_candidates["bank_account_number"].extend(_bank_account_number_candidates(page, base_score))
        field_candidates["bank_name"].extend(_bank_name_candidates(page, base_score))

    # Mặt sau CCCD dùng MRZ TD1. Kết quả chỉ được thêm khi cả chuỗi vượt qua
    # checksum ICAO, nên đáng tin cậy hơn số/chữ bị dính trong OCR toàn ảnh.
    for mrz_text in cccd_mrz_texts or []:
        parsed_mrz = _parse_vietnam_cccd_mrz(mrz_text)
        if not parsed_mrz:
            continue
        _add(field_candidates["citizen_id"], parsed_mrz["citizen_id"], 160, lambda item: bool(re.fullmatch(r"\d{12}", item)))
        _add(field_candidates["date_of_birth"], parsed_mrz["date_of_birth"], 150)
        _add(field_candidates["gender"], parsed_mrz["gender"], 150)
        # Tên MRZ không có dấu; ưu tiên tên có dấu đọc đúng từ mặt trước nếu có.
        _add(field_candidates["full_name"], parsed_mrz["full_name"], 100, _is_plausible_name)

    for key in (
        "full_name",
        "date_of_birth",
        "citizen_id",
        "citizen_id_issue_date",
        "citizen_id_issue_place",
        "gender",
        "ethnic_group",
        "temporary_address",
        "phone_number",
        "occupation",
        "employer",
        "support_category",
        "support_detail",
        "bank_account_name",
        "bank_account_number",
        "bank_name",
        "deceased_full_name",
        "deceased_date_of_birth",
        "deceased_gender",
        "deceased_ethnic_group",
        "deceased_nationality",
        "deceased_citizen_id",
        "deceased_death_date",
        "deceased_death_place",
        "death_certificate_number",
        "death_certificate_date",
        "death_certificate_issuer",
    ):
        fields[key], confidence[key] = _pick(field_candidates[key])
    if fields["full_name"]:
        fields["full_name"] = _restore_name_from_compact_cccd(pages, fields["full_name"])
    residence_winner = _best_candidate(residence_candidates)
    contact_winner = _best_candidate(contact_candidates)
    fields["residence_address"], confidence["residence_address"] = _pick(residence_candidates)
    fields["contact_address"], confidence["contact_address"] = _pick(contact_candidates)
    # Một số giấy tờ chỉ có duy nhất địa chỉ cư trú (vd. CCCD chỉ ghi "Nơi
    # thường trú", không có "Nơi ở hiện tại"/"Địa chỉ liên lạc"); khi đó dùng
    # luôn địa chỉ cư trú cho ô liên lạc thay vì để trống.
    if (
        not fields["contact_address"]
        or (residence_winner and contact_winner and contact_winner.score < residence_winner.score)
    ) and fields["residence_address"]:
        fields["contact_address"] = fields["residence_address"]
        confidence["contact_address"] = confidence["residence_address"]
    # Ngược lại, một số giấy tờ (giấy xác nhận khuyết tật, phiếu dân cư cũ...)
    # chỉ ghi "Nơi ở hiện tại" chứ không có địa chỉ thường trú riêng; dùng luôn
    # địa chỉ đó cho ô cư trú thay vì để trống. Chỉ áp dụng khi ô cư trú còn
    # trống hẳn (không ghi đè một địa chỉ thường trú đã nhận diện được).
    if not fields["residence_address"] and fields["contact_address"]:
        fields["residence_address"] = fields["contact_address"]
        confidence["residence_address"] = confidence["contact_address"]

    warnings = ["Dữ liệu được suy luận từ OCR/PDF. Hãy kiểm tra kỹ mọi trường trước khi tải đơn."]
    if (
        (residence_winner and residence_winner.review_required)
        or (contact_winner and contact_winner.review_required)
    ):
        warnings.append(
            "Địa chỉ đọc từ ảnh CCCD có thể thiếu hoặc sai dấu tiếng Việt. "
            "Hãy đối chiếu thẻ và sửa cả Nơi cư trú, Địa chỉ liên lạc trước khi tạo đơn."
        )
    if not fields["citizen_id"]:
        warnings.append("Không nhận diện được số CCCD/định danh 12 chữ số.")
    if not fields["full_name"]:
        warnings.append("Không nhận diện được họ và tên đáng tin cậy từ hồ sơ.")
    if fields["deceased_full_name"] or fields["deceased_death_date"]:
        warnings.append(
            "Thông tin người mất được đọc từ Trích lục khai tử; hãy đối chiếu bản gốc trước khi tạo đơn."
        )
    return ExtractionResult(fields=fields, confidence=confidence, warnings=warnings)
