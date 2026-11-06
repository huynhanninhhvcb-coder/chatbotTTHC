"""Kiểm tra dữ liệu trước khi chèn vào biểu mẫu."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from services.form_options import FIELD_OPTIONS, PAYMENT_BANK
from utils.text_utils import normalize_bank_account_number, normalize_citizen_id, normalize_date, normalize_phone, normalize_text


@dataclass
class ValidationResult:
    """Kết quả kiểm tra dữ liệu form: lỗi chặn submit (errors) và cảnh báo không chặn (warnings)."""

    errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """Form được coi là hợp lệ khi không còn lỗi nào (warnings không ảnh hưởng đến việc xuất biểu mẫu)."""
        return not self.errors


def validate_form_data(data: dict, required_fields: tuple[str, ...] | list[str]) -> ValidationResult:
    """Kiểm tra dữ liệu người dùng nhập/OCR trước khi điền vào biểu mẫu Word, trả về lỗi bắt buộc sửa và cảnh báo nên xem lại."""
    errors: dict[str, str] = {}
    warnings: list[str] = []
    # Chuẩn hóa khoảng trắng/định dạng văn bản cho toàn bộ giá trị trước khi
    # kiểm tra, tránh báo thiếu trường chỉ vì dữ liệu có khoảng trắng thừa.
    clean_data = {key: normalize_text(str(value)) for key, value in (data or {}).items()}

    for key in required_fields:
        if not clean_data.get(key):
            errors[key] = "Trường này là bắt buộc."

    # CCCD/số định danh cá nhân (người đề nghị, người khai thay/giám hộ, người
    # đã mất, nếu có) phải đúng 12 chữ số theo quy định hiện hành.
    for citizen_id_key in ("citizen_id", "guardian_citizen_id", "deceased_citizen_id"):
        citizen_id = normalize_citizen_id(clean_data.get(citizen_id_key))
        if citizen_id and not re.fullmatch(r"\d{12}", citizen_id):
            errors[citizen_id_key] = "CCCD/số định danh phải gồm đúng 12 chữ số."

    # Kiểm tra định dạng và tính hợp lý cho mọi trường ngày tháng có thể xuất
    # hiện tùy loại biểu mẫu: không được là ngày trong tương lai và phải theo
    # đúng định dạng dd/mm/yyyy sau khi chuẩn hóa.
    for date_key in (
        "date_of_birth",
        "citizen_id_issue_date",
        "guardian_date_of_birth",
        "guardian_citizen_id_issue_date",
        "deceased_date_of_birth",
        "deceased_death_date",
        "death_certificate_date",
    ):
        value = clean_data.get(date_key, "")
        if value:
            normalized = normalize_date(value)
            try:
                parsed = datetime.strptime(normalized, "%d/%m/%Y")
                if parsed.date() > datetime.now().date():
                    errors[date_key] = "Ngày không được ở tương lai."
            except ValueError:
                errors[date_key] = "Ngày phải theo định dạng dd/mm/yyyy."

    # Số điện thoại chỉ tạo cảnh báo (không chặn submit) vì không phải mẫu
    # nào cũng bắt buộc phải có, nhưng vẫn nên nhắc người dùng bổ sung để cơ
    # quan tiện liên hệ khi cần.
    phone = normalize_phone(clean_data.get("phone_number"))
    if phone and not re.fullmatch(r"0\d{9}", phone):
        warnings.append("Số điện thoại nên gồm 10 chữ số và bắt đầu bằng 0.")
    if not clean_data.get("phone_number"):
        warnings.append("Chưa có số điện thoại để cơ quan liên hệ khi cần.")

    # Thông tin ngân hàng: khi có bất kỳ trường nào (hoặc chọn nhận qua tài
    # khoản), số tài khoản và tên ngân hàng là bắt buộc. Tên chủ tài khoản
    # không bắt buộc vì tờ khai hỏa táng không có ô này.
    bank_fields = {
        "bank_account_name": clean_data.get("bank_account_name", ""),
        "bank_account_number": normalize_bank_account_number(clean_data.get("bank_account_number")),
        "bank_name": clean_data.get("bank_name", ""),
    }
    if any(bank_fields.values()) or clean_data.get("payment_method") == PAYMENT_BANK:
        if not bank_fields["bank_account_number"]:
            errors["bank_account_number"] = "Vui lòng nhập số tài khoản."
        if not bank_fields["bank_name"]:
            errors["bank_name"] = "Vui lòng nhập tên ngân hàng."

    # Số tài khoản ngân hàng Việt Nam thường dài 6-19 chữ số tùy ngân hàng.
    account_number = bank_fields["bank_account_number"]
    if account_number and not re.fullmatch(r"\d{6,19}", account_number):
        errors["bank_account_number"] = "Số tài khoản phải gồm từ 6 đến 19 chữ số."

    social_insurance_number = re.sub(r"\D", "", clean_data.get("social_insurance_number", ""))
    if clean_data.get("social_insurance_number") and len(social_insurance_number) != 10:
        errors["social_insurance_number"] = "Mã số BHXH phải gồm 10 chữ số."

    # Các trường lựa chọn phải khớp đúng chữ in trên mẫu (Nghị quyết 40/32,
    # hình thức nhận tiền, đối tượng mai táng/hỏa táng, loại thủ tục khuyết tật...)
    # để bước điền Word đánh dấu đúng ô.
    option_errors = {
        "support_category": "Vui lòng chọn đúng một đối tượng nhận hỗ trợ trong danh sách.",
        "payment_method": "Vui lòng chọn nhận qua tiền mặt, tài khoản ngân hàng hoặc ví điện tử.",
        "benefit_recipient": "Vui lòng chọn người nhận tiền: người hưởng trợ cấp hoặc người giám hộ.",
        "burial_support_category": "Vui lòng chọn đúng một đối tượng của người đã mất trong danh sách.",
        "cremation_support_category": "Vui lòng chọn đúng một đối tượng hỗ trợ hỏa táng trong danh sách.",
        "disability_procedure_type": "Vui lòng chọn đúng một nội dung đề nghị trong danh sách.",
    }
    for field_name, message in option_errors.items():
        value = clean_data.get(field_name, "")
        if value and value not in FIELD_OPTIONS[field_name]:
            errors[field_name] = message

    # Giờ/phút khai tử (nếu có) phải là số nguyên hợp lệ trong ngày:
    # 0-23 cho giờ và 0-59 cho phút.
    for time_field, maximum in (("deceased_death_hour", 23), ("deceased_death_minute", 59)):
        value = clean_data.get(time_field, "")
        if value and (not value.isdigit() or not 0 <= int(value) <= maximum):
            errors[time_field] = f"Giá trị phải là số từ 0 đến {maximum}."

    return ValidationResult(errors=errors, warnings=warnings)
