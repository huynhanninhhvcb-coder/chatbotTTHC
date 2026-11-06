"""Trợ lý phỏng vấn cục bộ để điền đơn khi người dân không có tệp nguồn.

Đây là hội thoại có cấu trúc: câu hỏi được xác định từ trường của biểu mẫu và
câu trả lời được kiểm tra ngay. Dữ liệu chỉ giữ trong RAM tối đa 30 phút, không
gửi tới nhà cung cấp AI bên ngoài và không ghi vào cơ sở dữ liệu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import secrets
import threading
import time
from collections.abc import Callable

from services.extraction_service import FIELD_NAMES
from services.form_mappings import age_on
from services.form_options import (
    FIELD_OPTIONS,
    PAYMENT_BANK,
    PAYMENT_CASH,
    PAYMENT_EWALLET,
    RECIPIENT_GUARDIAN,
    RECIPIENT_SELF,
    SUPPORT_CATEGORIES,
)
from services.template_service import FormTemplate
from services.validation_service import validate_form_data
from utils.text_utils import (
    normalize_citizen_id,
    normalize_date,
    normalize_phone,
    normalize_text,
    strip_accents,
)


@dataclass(frozen=True)
class InterviewStep:
    """Một câu hỏi trong kịch bản phỏng vấn.

    ``when`` (nếu có) nhận các câu trả lời đã có và quyết định có hỏi câu này
    không, vd. chỉ hỏi số tài khoản khi người dân chọn nhận qua ngân hàng.
    """

    field: str
    question: str
    required: bool = False
    when: Callable[[dict[str, str]], bool] | None = None


def _has(field_name: str) -> Callable[[dict[str, str]], bool]:
    return lambda fields: bool(fields.get(field_name))


def _equals(field_name: str, value: str) -> Callable[[dict[str, str]], bool]:
    return lambda fields: fields.get(field_name) == value


def _identity_steps(
    subject: str = "bạn",
    *,
    gender: bool = False,
    ethnic: bool = False,
    issue_date: bool = False,
    issue_place: bool = False,
    issue_required: bool = False,
    current_address: bool = False,
    phone: bool = False,
    residence_name: str = "Nơi cư trú (hộ khẩu thường trú)",
) -> tuple[InterviewStep, ...]:
    """Các câu hỏi nhân thân dùng chung; ``subject`` là người được khai (bạn / người được xác nhận...)."""
    steps = [
        InterviewStep("full_name", f"Vui lòng cho biết họ và tên đầy đủ của {subject}, đúng như trên CCCD.", True),
        InterviewStep("date_of_birth", f"Ngày, tháng, năm sinh của {subject} là gì? Ví dụ: 30/05/1951.", True),
    ]
    if gender:
        steps.append(InterviewStep("gender", f"Giới tính của {subject} là Nam hay Nữ?"))
    if ethnic:
        steps.append(InterviewStep("ethnic_group", f"Dân tộc của {subject} là gì? Ví dụ: Kinh."))
    steps.append(
        InterviewStep("citizen_id", f"Vui lòng đọc từng chữ số CCCD hoặc số định danh cá nhân của {subject}, gồm 12 số.", True)
    )
    if issue_date:
        steps.append(InterviewStep("citizen_id_issue_date", f"CCCD của {subject} được cấp ngày, tháng, năm nào?", issue_required))
    if issue_place:
        steps.append(
            InterviewStep(
                "citizen_id_issue_place",
                f"CCCD của {subject} do cơ quan nào cấp? Ví dụ: Cục Cảnh sát quản lý hành chính về trật tự xã hội.",
                issue_required,
            )
        )
    steps.append(
        InterviewStep(
            "residence_address",
            f"{residence_name} của {subject} ở đâu? Vui lòng nêu đầy đủ số nhà, đường, phường/xã, tỉnh/thành phố.",
            True,
        )
    )
    if current_address:
        steps.append(
            InterviewStep(
                "contact_address",
                f"Nơi ở hiện nay của {subject} có giống nơi cư trú không? Nếu giống, trả lời “giống nơi cư trú”; nếu khác, hãy nêu địa chỉ.",
            )
        )
    if phone:
        steps.append(InterviewStep("phone_number", f"Số điện thoại liên hệ của {subject} là gì? Hãy đọc từng chữ số."))
    return tuple(steps)


def _payment_steps(purpose: str, required: bool = False) -> tuple[InterviewStep, ...]:
    """Hỏi hình thức nhận tiền, rồi chỉ hỏi chi tiết tài khoản/ví tương ứng với lựa chọn."""
    bank = _equals("payment_method", PAYMENT_BANK)
    wallet = _equals("payment_method", PAYMENT_EWALLET)
    return (
        InterviewStep("payment_method", f"Bạn muốn nhận {purpose} bằng tiền mặt, tài khoản ngân hàng hay ví điện tử?", required),
        InterviewStep("bank_account_number", "Số tài khoản ngân hàng là gì? Hãy đọc từng chữ số.", True, bank),
        InterviewStep("bank_account_name", "Tên chủ tài khoản là gì?", True, bank),
        InterviewStep("bank_name", "Tài khoản mở tại ngân hàng nào?", True, bank),
        InterviewStep("ewallet_number", "Số ví điện tử là gì?", True, wallet),
        InterviewStep("ewallet_owner", "Tên chủ ví là gì?", False, wallet),
        InterviewStep("ewallet_provider", "Đó là loại ví điện tử nào? Ví dụ: MoMo, ZaloPay, VNPT Money.", False, wallet),
    )


def _proxy_steps(*, name: bool = True, address: bool = False) -> tuple[InterviewStep, ...]:
    """Thông tin người đứng ra khai thay trong các đơn xác nhận."""
    steps = []
    if name:
        steps.append(InterviewStep("guardian_full_name", "Họ và tên đầy đủ của bạn (người khai thay) là gì?", True))
    steps += [
        InterviewStep("guardian_relationship", "Bạn là gì của người được xác nhận? Ví dụ: con, cháu, vợ, chồng.", True),
        InterviewStep("guardian_citizen_id", "Vui lòng đọc từng chữ số CCCD của bạn, gồm 12 số."),
        InterviewStep("guardian_citizen_id_issue_date", "CCCD của bạn được cấp ngày, tháng, năm nào?"),
        InterviewStep("guardian_citizen_id_issue_place", "CCCD của bạn do cơ quan nào cấp?"),
    ]
    if address:
        steps.append(InterviewStep("guardian_address", "Địa chỉ hiện nay của bạn là gì?"))
    return tuple(steps)


_SUBJECT = "người được xác nhận"
_SUBJECT_RELATIONSHIP = InterviewStep(
    "subject_relationship", "Người được xác nhận là gì của bạn? Ví dụ: cha, mẹ, ông, bà, con.", True
)
_DESTINATION = InterviewStep(
    "destination_ward", "Hồ sơ hưởng chế độ sẽ nộp tại xã/phường nào? Ví dụ: phường Gia Định.", True
)
_has_guardian = _has("guardian_full_name")

# Mẫu số 01 – trợ cấp hưu trí xã hội: người đề nghị, người giám hộ (nếu có),
# rồi người nhận tiền và hình thức nhận.
_TRO_CAP_HUU_TRI_STEPS: tuple[InterviewStep, ...] = (
    *_identity_steps(gender=True, ethnic=True, issue_date=True, issue_place=True, current_address=True, phone=True),
    InterviewStep("health_insurance_number", "Nếu đã có thẻ BHYT, vui lòng đọc số thẻ. Nếu chưa có, trả lời “bỏ qua”."),
    InterviewStep(
        "guardian_full_name",
        "Nếu bạn có người giám hộ, hãy cho biết họ tên người giám hộ. Nếu không có, trả lời “bỏ qua”.",
    ),
    InterviewStep("guardian_date_of_birth", "Ngày sinh của người giám hộ là gì?", False, _has_guardian),
    InterviewStep("guardian_citizen_id", "Số CCCD của người giám hộ là gì? Hãy đọc từng chữ số.", False, _has_guardian),
    InterviewStep("guardian_citizen_id_issue_date", "CCCD của người giám hộ được cấp ngày nào?", False, _has_guardian),
    InterviewStep("guardian_citizen_id_issue_place", "CCCD của người giám hộ do cơ quan nào cấp?", False, _has_guardian),
    InterviewStep("guardian_residence_address", "Nơi cư trú của người giám hộ ở đâu?", False, _has_guardian),
    InterviewStep(
        "guardian_current_address",
        "Nơi ở hiện nay của người giám hộ có giống nơi cư trú không? Nếu giống, trả lời “giống nơi cư trú”.",
        False,
        _has_guardian,
    ),
    InterviewStep("guardian_phone", "Số điện thoại của người giám hộ là gì?", False, _has_guardian),
    InterviewStep("guardian_relationship", "Người giám hộ có quan hệ gì với bạn?", False, _has_guardian),
    InterviewStep(
        "benefit_recipient", "Ai sẽ đứng tên nhận tiền trợ cấp: chính bạn hay người giám hộ?", False, _has_guardian
    ),
    *_payment_steps("trợ cấp"),
)

# Mẫu số 03 – hỗ trợ chi phí mai táng.
_HO_TRO_MAI_TANG_STEPS: tuple[InterviewStep, ...] = (
    *_identity_steps(gender=True, issue_date=True, issue_place=True, phone=True),
    InterviewStep("deceased_relationship", "Bạn có quan hệ gì với người đã mất? Ví dụ: con, vợ, chồng."),
    InterviewStep("deceased_full_name", "Họ và tên đầy đủ của người đã mất là gì?", True),
    InterviewStep("deceased_date_of_birth", "Ngày, tháng, năm sinh của người đã mất là gì?"),
    InterviewStep("deceased_gender", "Giới tính của người đã mất là Nam hay Nữ?"),
    InterviewStep("deceased_ethnic_group", "Dân tộc của người đã mất là gì?"),
    InterviewStep("deceased_nationality", "Quốc tịch của người đã mất là gì? Nếu là Việt Nam, trả lời “Việt Nam”."),
    InterviewStep("deceased_residence_address", "Nơi cư trú của người đã mất ở đâu?"),
    InterviewStep("deceased_citizen_id", "Số CCCD hoặc số định danh cá nhân của người đã mất là gì? Vui lòng đọc từng chữ số."),
    InterviewStep("deceased_death_date", "Người đã mất qua đời vào ngày, tháng, năm nào? Ví dụ: 15/08/2026.", True),
    InterviewStep("deceased_death_hour", "Người đó mất vào lúc mấy giờ? Nếu không rõ, hãy nói bỏ qua."),
    InterviewStep("deceased_death_minute", "Người đó mất vào phút thứ bao nhiêu? Nếu không rõ, hãy nói bỏ qua."),
    InterviewStep("deceased_death_place", "Người đó qua đời ở đâu?"),
    InterviewStep("deceased_death_cause", "Nguyên nhân qua đời là gì, nếu bạn biết?"),
    InterviewStep("death_certificate_number", "Số giấy báo tử hoặc giấy tờ thay thế là bao nhiêu, nếu bạn có?"),
    InterviewStep("death_certificate_issuer", "Giấy báo tử do cơ quan nào cấp?"),
    InterviewStep("death_certificate_date", "Giấy báo tử được cấp vào ngày, tháng, năm nào?"),
    InterviewStep(
        "burial_support_category",
        "Người đã mất thuộc đối tượng nào: (1) đang hưởng trợ cấp xã hội, trợ cấp hưu trí xã hội; (2) con của người đơn thân nghèo đang hưởng trợ cấp; "
        "(3) người từ đủ 75 tuổi đang hưởng trợ cấp tuất BHXH; (4) người từ đủ 70 đến dưới 75 tuổi thuộc hộ nghèo, cận nghèo đang hưởng trợ cấp tuất; "
        "hay (5) người chết trong trường hợp khẩn cấp như thiên tai, tai nạn?",
        True,
    ),
    InterviewStep(
        "org_name",
        "Nếu có cơ quan, tổ chức đứng ra lo mai táng (không phải cá nhân), hãy cho biết tên tổ chức. Nếu không, trả lời “bỏ qua”.",
    ),
    InterviewStep("org_address", "Địa chỉ của tổ chức đó là gì?", False, _has("org_name")),
    InterviewStep("org_representative_name", "Người đại diện theo pháp luật của tổ chức là ai?", False, _has("org_name")),
    InterviewStep("org_representative_title", "Chức vụ của người đại diện đó là gì?", False, _has("org_name")),
    InterviewStep("org_phone", "Số điện thoại của tổ chức là gì?", False, _has("org_name")),
    *_payment_steps("tiền hỗ trợ", required=True),
)

# Tờ khai hỗ trợ khuyến khích hỏa táng.
_HO_TRO_HOA_TANG_STEPS: tuple[InterviewStep, ...] = (
    *_identity_steps(phone=True),
    InterviewStep("deceased_relationship", "Bạn có quan hệ gì với người đã mất?"),
    InterviewStep("deceased_full_name", "Họ và tên đầy đủ của người đã mất là gì?", True),
    InterviewStep("deceased_death_date", "Người đã mất qua đời vào ngày, tháng, năm nào? Ví dụ: 15/08/2026.", True),
    InterviewStep("death_certificate_number", "Số giấy chứng tử là bao nhiêu, nếu bạn có?"),
    InterviewStep("death_certificate_date", "Giấy chứng tử được cấp vào ngày, tháng, năm nào?"),
    InterviewStep("death_certificate_issuer", "Giấy chứng tử do cơ quan nào cấp?"),
    InterviewStep(
        "cremation_support_category",
        "Người đã mất thuộc đối tượng hỗ trợ hỏa táng nào? Ví dụ: Bà mẹ Việt Nam anh hùng, người khuyết tật, người cao tuổi, hộ nghèo, hộ cận nghèo, đối tượng hưu trí, hoặc người dân có hộ khẩu Thành phố Hồ Chí Minh.",
        True,
    ),
    InterviewStep(
        "cremation_support_detail",
        "Vui lòng cho biết mã số hộ nghèo/cận nghèo.",
        False,
        lambda fields: fields.get("cremation_support_category") in {"Hộ nghèo", "Hộ cận nghèo"},
    ),
    InterviewStep(
        "org_name",
        "Nếu bạn đại diện cho một tổ chức đứng ra lo việc hỏa táng, hãy cho biết tên tổ chức. Nếu không, trả lời “bỏ qua”.",
    ),
    InterviewStep(
        "bank_account_number",
        "Nếu muốn nhận tiền qua tài khoản ngân hàng, hãy đọc số tài khoản. Nếu nhận tiền mặt, trả lời “bỏ qua”.",
    ),
    InterviewStep("bank_name", "Tài khoản mở tại ngân hàng nào?", True, _has("bank_account_number")),
)

# Tờ khai hỗ trợ theo Nghị quyết 40 và Nghị quyết 32 (cùng bố cục).
_HO_TRO_NGHI_QUYET_STEPS: tuple[InterviewStep, ...] = (
    *_identity_steps(issue_date=True, issue_place=True, issue_required=True),
    InterviewStep("temporary_address", "Nơi tạm trú hiện nay của bạn ở đâu? Nếu không có, hãy trả lời bỏ qua."),
    InterviewStep("phone_number", "Số điện thoại liên hệ của bạn là gì? Hãy đọc từng chữ số."),
    InterviewStep("occupation", "Nghề nghiệp hiện nay của bạn là gì?"),
    InterviewStep("employer", "Đơn vị công tác của bạn là gì? Nếu không có, hãy trả lời bỏ qua."),
    InterviewStep(
        "support_category",
        "Bạn thuộc nhóm hỗ trợ nào? Hãy trả lời một trong năm nhóm: phụ nữ sinh đủ hai con trước 35 tuổi; hộ nghèo; hộ cận nghèo; đối tượng bảo trợ xã hội; hoặc đối tượng sống tại xã đảo.",
        True,
    ),
    InterviewStep(
        "support_detail",
        "Vui lòng cho biết mã số hộ nghèo/cận nghèo, hoặc thông tin cụ thể về diện bảo trợ xã hội/xã đảo.",
        False,
        lambda fields: bool(fields.get("support_category")) and fields.get("support_category") != SUPPORT_CATEGORIES[0][0],
    ),
)

# Đơn đề nghị xác định mức độ khuyết tật (Mẫu 01, TT 01/2019).
_XAC_DINH_KHUYET_TAT_STEPS: tuple[InterviewStep, ...] = (
    *_identity_steps("người được xác định khuyết tật", gender=True, current_address=True, residence_name="Hộ khẩu thường trú"),
    InterviewStep(
        "disability_procedure_type",
        "Bạn đề nghị nội dung nào: xác định mức độ khuyết tật, xác định lại mức độ khuyết tật, cấp lại Giấy xác nhận khuyết tật, hay cấp đổi Giấy xác nhận khuyết tật?",
        True,
    ),
    InterviewStep(
        "guardian_full_name",
        "Nếu có người đại diện hợp pháp (cha, mẹ, người giám hộ...), hãy cho biết họ và tên người đó. Nếu không có, trả lời “bỏ qua”.",
    ),
    InterviewStep(
        "guardian_relationship", "Người đại diện có quan hệ gì với người được xác định khuyết tật?", False, _has_guardian
    ),
    InterviewStep("guardian_citizen_id", "Số CCCD của người đại diện là gì? Vui lòng đọc từng chữ số.", False, _has_guardian),
    InterviewStep("guardian_residence_address", "Hộ khẩu thường trú của người đại diện ở đâu?", False, _has_guardian),
    InterviewStep(
        "guardian_current_address",
        "Nơi ở hiện nay của người đại diện có giống hộ khẩu thường trú không? Nếu giống, trả lời “giống nơi cư trú”.",
        False,
        _has_guardian,
    ),
    InterviewStep("guardian_phone", "Số điện thoại của người đại diện là gì?", False, _has_guardian),
)


def _without_code(fields: dict[str, str]) -> bool:
    return not fields.get("social_insurance_number")


def _child_under_six(fields: dict[str, str]) -> bool:
    age = age_on(fields.get("date_of_birth", ""))
    return _without_code(fields) and age is not None and age < 6


# Tờ khai BHYT (TK1-TS): người chưa có mã số BHXH khai mục I, đã có thì khai mục II.
_TO_KHAI_BHYT_STEPS: tuple[InterviewStep, ...] = (
    InterviewStep(
        "social_insurance_number",
        "Người tham gia đã có mã số BHXH chưa? Nếu có, hãy đọc 10 chữ số; nếu chưa có, trả lời “chưa có”.",
    ),
    InterviewStep("full_name", "Họ và tên đầy đủ của người tham gia BHYT là gì?", True),
    InterviewStep("date_of_birth", "Ngày, tháng, năm sinh của người tham gia là gì? Ví dụ: 30/05/2020.", True),
    InterviewStep("gender", "Giới tính của người tham gia là Nam hay Nữ?"),
    InterviewStep("citizen_id", "Vui lòng đọc từng chữ số CCCD hoặc số định danh cá nhân của người tham gia, gồm 12 số.", True),
    InterviewStep("ethnic_group", "Dân tộc của người tham gia là gì? Nếu là dân tộc Kinh, trả lời “Kinh”.", False, _without_code),
    InterviewStep("phone_number", "Số điện thoại liên hệ là gì? Hãy đọc từng chữ số.", False, _without_code),
    InterviewStep("email", "Email liên hệ là gì? Nếu không có, trả lời “bỏ qua”.", False, _without_code),
    InterviewStep("birth_registration_ward", "Người tham gia đăng ký khai sinh tại xã/phường nào?"),
    InterviewStep("birth_registration_province", "Nơi đăng ký khai sinh thuộc tỉnh/thành phố nào?"),
    InterviewStep("guardian_full_name", "Họ tên cha, mẹ hoặc người giám hộ của bé là gì?", False, _child_under_six),
    InterviewStep(
        "primary_care_facility", "Bạn muốn đăng ký khám chữa bệnh ban đầu ở đâu? Nếu chưa xác định, trả lời “bỏ qua”."
    ),
    InterviewStep(
        "request_content", "Bạn cần điều chỉnh thông tin gì hoặc có yêu cầu khác không? Nếu không, trả lời “bỏ qua”."
    ),
)


def _xn_steps(*, phone: bool = False, destination: bool = False, current: bool = True) -> tuple[InterviewStep, ...]:
    """Đơn xác nhận tự khai: thông tin người làm đơn và nơi sẽ nộp hồ sơ."""
    steps = _identity_steps(issue_date=True, current_address=current, phone=phone, residence_name="Hộ khẩu thường trú")
    return (*steps, _DESTINATION) if destination else steps


def _xn_proxy_steps(*, name: bool, address: bool, relationship: bool, destination: bool) -> tuple[InterviewStep, ...]:
    """Đơn xác nhận khai thay: thông tin người được xác nhận, rồi người khai thay."""
    steps = (
        *_identity_steps(_SUBJECT, issue_date=True, current_address=True, residence_name="Hộ khẩu thường trú"),
        *((_SUBJECT_RELATIONSHIP,) if relationship else ()),
        *_proxy_steps(name=name, address=address),
    )
    return (*steps, _DESTINATION) if destination else steps


# Ánh xạ từ id mẫu (FormTemplate.id) sang kịch bản câu hỏi tương ứng.
TEMPLATE_STEPS: dict[str, tuple[InterviewStep, ...]] = {
    "tro_cap_huu_tri": _TRO_CAP_HUU_TRI_STEPS,
    "ho_tro_mai_tang": _HO_TRO_MAI_TANG_STEPS,
    "ho_tro_hoa_tang": _HO_TRO_HOA_TANG_STEPS,
    "ho_tro_nq40": _HO_TRO_NGHI_QUYET_STEPS,
    "ho_tro_nq32": _HO_TRO_NGHI_QUYET_STEPS,
    "xac_dinh_khuyet_tat": _XAC_DINH_KHUYET_TAT_STEPS,
    "to_khai_bhyt": _TO_KHAI_BHYT_STEPS,
    "xn_huu_tri_tu_khai": _xn_steps(),
    "xn_huu_tri_khai_thay": _xn_proxy_steps(name=True, address=True, relationship=False, destination=False),
    "xn_mung_tho_tu_khai": _xn_steps(phone=True, destination=True),
    "xn_mung_tho_khai_thay": _xn_proxy_steps(name=False, address=True, relationship=True, destination=True),
    "xn_khuyet_tat_tu_khai": _xn_steps(destination=True),
    "xn_khuyet_tat_khai_thay": _xn_proxy_steps(name=True, address=False, relationship=True, destination=True),
    "xn_nq35": _xn_steps(destination=True),
    "xn_nq40": _xn_steps(phone=True),
    "xn_nq32": _xn_steps(phone=True),
}

# Các câu trả lời (đã bỏ dấu, viết thường) được hiểu là "bỏ qua trường này".
SKIP_ANSWERS = {"bo qua", "khong co", "chua co", "khong nho", "khong biet", "khong ap dung", "skip"}
# Cách đọc chữ số tiếng Việt sang ký tự số, dùng khi người dùng đọc từng chữ số
# (CCCD, số điện thoại...) thay vì gõ trực tiếp. Gồm cả biến thể vùng miền:
# "tư" cho 4, "lăm" cho 5. "linh"/"không" đều là số 0 khi đọc từng số riêng lẻ.
SPOKEN_DIGITS = {
    "khong": "0",
    "linh": "0",
    "mot": "1",
    "hai": "2",
    "ba": "3",
    "bon": "4",
    "tu": "4",
    "nam": "5",
    "lam": "5",
    "sau": "6",
    "bay": "7",
    "tam": "8",
    "chin": "9",
}
# Dùng riêng cho việc đọc số đếm (vd. "hai mươi ba"), nên bỏ "linh" (chỉ có
# nghĩa số 0 khi đọc rời từng chữ số, không dùng làm số hạng trong số đếm).
CARDINAL_DIGITS = {key: int(value) for key, value in SPOKEN_DIGITS.items() if key != "linh"}


def _spoken_digits(value: str) -> str:
    """Chuyển cách đọc từng số tiếng Việt từ SpeechRecognition thành chữ số."""
    tokens = re.findall(r"\d+|[a-z]+", strip_accents(value).lower())
    digits: list[str] = []
    for token in tokens:
        if token.isdigit():
            digits.append(token)
        elif token in SPOKEN_DIGITS:
            digits.append(SPOKEN_DIGITS[token])
    return "".join(digits)


def _spoken_number(value: str, *, year: bool = False) -> int | None:
    """Đọc một số tiếng Việt cơ bản, đủ cho ngày/tháng/năm trong phỏng vấn."""
    tokens = re.findall(r"\d+|[a-z]+", strip_accents(value).lower())
    if not tokens:
        return None
    if all(token.isdigit() for token in tokens):
        return int("".join(tokens))
    if year and len(tokens) >= 3 and all(token in CARDINAL_DIGITS for token in tokens):
        return int("".join(str(CARDINAL_DIGITS[token]) for token in tokens))

    def under_one_hundred(items: list[str]) -> int | None:
        if not items:
            return 0
        if "muoi" in items:
            index = items.index("muoi")
            tens = CARDINAL_DIGITS.get(items[index - 1], 1) if index else 1
            if index and items[index - 1] not in CARDINAL_DIGITS:
                return None
            result = tens * 10
            if index + 1 < len(items) and items[index + 1] in CARDINAL_DIGITS:
                result += CARDINAL_DIGITS[items[index + 1]]
            return result
        if len(items) == 1 and items[0] in CARDINAL_DIGITS:
            return CARDINAL_DIGITS[items[0]]
        return None

    def under_one_thousand(items: list[str]) -> int | None:
        if "tram" not in items:
            return under_one_hundred(items)
        index = items.index("tram")
        if index == 0 or items[index - 1] not in CARDINAL_DIGITS:
            return None
        tail = [item for item in items[index + 1 :] if item not in {"le", "linh", "va"}]
        remainder = under_one_hundred(tail)
        return CARDINAL_DIGITS[items[index - 1]] * 100 + (remainder if remainder is not None else 0)

    if "nghin" in tokens:
        index = tokens.index("nghin")
        thousands = under_one_thousand(tokens[:index])
        remainder = under_one_thousand(tokens[index + 1 :])
        if thousands is None or remainder is None:
            return None
        return thousands * 1000 + remainder
    return under_one_thousand(tokens)


def _normalise_spoken_date(value: str) -> str:
    """Chuyển câu trả lời dạng "ngày ... tháng ... năm ..." (số hoặc đọc chữ) thành dd/mm/yyyy.

    Thử chuẩn hoá trực tiếp trước (normalize_date xử lý dạng số như 30/05/1990);
    chỉ khi không khớp mới tách theo mẫu "ngày X tháng Y năm Z" và đọc số bằng
    chữ cho từng thành phần.
    """
    direct = normalize_date(value)
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", direct):
        return direct
    folded = strip_accents(value).lower()
    match = re.search(r"(?:ngay\s+)?(.+?)\s+thang\s+(.+?)\s+nam\s+(.+)$", folded)
    if not match:
        return direct
    day = _spoken_number(match.group(1))
    month = _spoken_number(match.group(2))
    year = _spoken_number(match.group(3), year=True)
    if day is None or month is None or year is None:
        return direct
    return f"{day:02d}/{month:02d}/{year:04d}"


class InterviewNotFoundError(ValueError):
    """Phiên phỏng vấn không tồn tại hoặc đã hết hạn (quá TTL hoặc đã bị discard)."""

    pass


@dataclass
class InterviewSession:
    """Trạng thái một phiên phỏng vấn: mẫu đơn, câu trả lời đã thu thập và vị trí câu hỏi hiện tại."""

    template: FormTemplate
    fields: dict[str, str] = field(default_factory=lambda: {key: "" for key in FIELD_NAMES})
    step_index: int = 0
    expires_at: float = 0.0
    steps: tuple[InterviewStep, ...] = field(default_factory=tuple)
    answered: int = 0

    def __post_init__(self) -> None:
        # Chọn kịch bản câu hỏi theo loại mẫu đơn; nếu không có mẫu tương ứng
        # thì dùng tạm kịch bản trợ cấp hưu trí làm mặc định.
        if not self.steps:
            self.steps = TEMPLATE_STEPS.get(self.template.id, _TRO_CAP_HUU_TRI_STEPS)

    def applies(self, step: InterviewStep) -> bool:
        return step.when is None or step.when(self.fields)

    def skip_inapplicable(self) -> None:
        """Bỏ qua các câu hỏi không áp dụng với câu trả lời hiện có (vd. chi tiết người giám hộ khi không có)."""
        while self.step_index < len(self.steps) and not self.applies(self.steps[self.step_index]):
            self.step_index += 1

    def label(self, field_name: str) -> str:
        return self.template.labels.get(field_name) or FIELD_NAMES.get(field_name, field_name)


class InterviewService:
    """Quản lý các phiên phỏng vấn hỏi-đáp đang diễn ra, lưu tạm trong RAM có TTL."""

    def __init__(self, ttl_seconds: int = 30 * 60):
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, InterviewSession] = {}
        self._lock = threading.Lock()

    def _purge_expired(self) -> None:
        """Dọn các phiên đã quá hạn TTL để tránh rò rỉ bộ nhớ."""
        now = time.monotonic()
        expired = [key for key, session in self._sessions.items() if session.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)

    def _session(self, session_id: str) -> InterviewSession:
        """Lấy phiên theo id, gia hạn TTL thêm một lượt; báo lỗi nếu phiên không còn."""
        self._purge_expired()
        session = self._sessions.get(session_id)
        if session is None:
            raise InterviewNotFoundError("Phiên phỏng vấn không còn hiệu lực. Vui lòng bắt đầu lại.")
        session.expires_at = time.monotonic() + self.ttl_seconds
        return session

    @staticmethod
    def _is_skip(answer: str) -> bool:
        """Kiểm tra câu trả lời có nghĩa "bỏ qua trường này" hay không (vd. "không có", "bỏ qua")."""
        folded = "".join(character for character in strip_accents(answer).lower() if character.isalnum() or character.isspace())
        return normalize_text(folded) in SKIP_ANSWERS

    @staticmethod
    def _normalise_answer(field: str, answer: str, known_fields: dict[str, str]) -> tuple[str, str | None]:
        """Chuẩn hoá và kiểm tra câu trả lời cho một trường cụ thể.

        Mỗi nhánh xử lý theo kiểu dữ liệu của trường (số đọc từng chữ số, ngày
        đọc bằng lời, lựa chọn trong danh sách cố định...). Trả về (giá trị đã
        chuẩn hoá, None) nếu hợp lệ, hoặc ("", thông báo lỗi) để hỏi lại.
        """
        value = normalize_text(answer)
        folded = strip_accents(value).lower()
        if field in {"citizen_id", "guardian_citizen_id", "deceased_citizen_id"}:
            # Người dùng thường đọc từng chữ số CCCD; ưu tiên diễn giải theo
            # cách đọc số trước, nếu không ra chữ số nào mới dùng nguyên văn.
            value = normalize_citizen_id(_spoken_digits(value) or value)
            if not re.fullmatch(r"\d{12}", value):
                return "", "Số CCCD/số định danh phải gồm đúng 12 chữ số. Vui lòng đọc lại từng số."
        elif field in {
            "date_of_birth",
            "citizen_id_issue_date",
            "guardian_date_of_birth",
            "guardian_citizen_id_issue_date",
            "deceased_date_of_birth",
            "deceased_death_date",
            "death_certificate_date",
        }:
            value = _normalise_spoken_date(value)
            validation = validate_form_data({field: value}, ())
            if field in validation.errors:
                return "", validation.errors[field]
        elif field in {"phone_number", "guardian_phone", "org_phone"}:
            value = normalize_phone(_spoken_digits(value) or value)
            if value and not re.fullmatch(r"0\d{9}", value):
                return "", "Số điện thoại cần gồm 10 chữ số và bắt đầu bằng số 0."
        elif field in {"bank_account_number", "ewallet_number"}:
            value = _spoken_digits(value) or value
        elif field == "social_insurance_number":
            value = _spoken_digits(value) or value
            if not re.fullmatch(r"\d{10}", value):
                return "", "Mã số BHXH gồm 10 chữ số. Vui lòng đọc lại, hoặc trả lời “chưa có”."
        elif field == "health_insurance_number":
            value = re.sub(r"\s+", "", value).upper()
        elif field == "email":
            value = re.sub(r"\s*(a còng|a móc|a cong|a moc)\s*", "@", value, flags=re.IGNORECASE)
            value = re.sub(r"\s*(chấm|cham)\s*", ".", value, flags=re.IGNORECASE)
            value = re.sub(r"\s+", "", value).lower()
            if not re.fullmatch(r"[^@]+@[^@]+\.[^@]+", value):
                return "", "Email chưa đúng định dạng, ví dụ: ten@gmail.com. Nếu không có email, trả lời “bỏ qua”."
        elif field == "destination_ward":
            # Mẫu in "tại xã/phường ...", nên giữ tiền tố phường/xã để câu đọc đúng.
            if not re.match(r"(phuong|xa|thi tran|dac khu)\b", folded):
                value = f"xã/phường {value}"
        elif field in {"subject_relationship", "guardian_relationship", "deceased_relationship"}:
            # Quan hệ được chèn giữa câu ("xác nhận cho mẹ tôi"), nên viết thường.
            value = value[:1].lower() + value[1:]
        elif field == "benefit_recipient":
            if "giam ho" in folded:
                value = RECIPIENT_GUARDIAN
            elif re.search(r"chinh|toi|ban than|nguoi huong|minh", folded):
                value = RECIPIENT_SELF
            else:
                return "", "Vui lòng trả lời: chính bạn nhận, hay người giám hộ nhận."
        elif field == "burial_support_category":
            # Thứ tự quan trọng: nhóm "70 đến dưới 75 tuổi" cũng chứa số 75.
            categories = (
                (r"khan cap|thien tai|hoa hoan|tai nan|\(?5\)?$|thu nam", 4),
                (r"don than", 1),
                (r"\b70\b|bay muoi(?! lam)|\(?4\)?$|thu tu", 3),
                (r"\b75\b|bay muoi lam|bay lam|\(?3\)?$|thu ba", 2),
                (r"tro cap xa hoi|huu tri xa hoi|\(?1\)?$|thu nhat", 0),
                (r"\(?2\)?$|thu hai", 1),
            )
            index = next((position for pattern, position in categories if re.search(pattern, folded)), None)
            if index is None:
                return "", "Vui lòng chọn một trong năm đối tượng vừa nêu, có thể trả lời bằng số thứ tự từ 1 đến 5."
            value = FIELD_OPTIONS["burial_support_category"][index]
        elif field in {"gender", "deceased_gender"}:
            if re.search(r"\bnu\b", folded):
                value = "Nữ"
            elif re.search(r"\bnam\b", folded):
                value = "Nam"
            elif "khac" in folded:
                value = "Khác"
            else:
                return "", "Vui lòng trả lời Nam, Nữ hoặc Khác."
        elif field == "support_category":
            # Dò các từ khoá đặc trưng của từng nhóm hỗ trợ theo Nghị quyết
            # trong câu trả lời tự do, để chọn đúng nhãn cố định ghi vào đơn.
            categories = (
                (r"(?:hai|2)\s*con|truoc\s*(?:ba\s*muoi\s*lam|35)", "Phụ nữ sinh đủ hai con trước 35 tuổi"),
                (r"can\s*ngheo", "Hộ cận nghèo"),
                (r"ho\s*ngheo|ngheo", "Hộ nghèo"),
                (r"bao\s*tro\s*xa\s*hoi", "Đối tượng bảo trợ xã hội"),
                (r"xa\s*dao|song\s*tai\s*dao", "Đối tượng sống tại xã đảo"),
            )
            value = next((label for pattern, label in categories if re.search(pattern, folded)), "")
            if not value:
                return "", "Vui lòng chọn một trong năm nhóm hỗ trợ vừa được nêu trong câu hỏi."
        elif field == "payment_method":
            # Ví được kiểm tra trước vì người dân hay nói "tài khoản ví MoMo".
            if re.search(r"\bvi\b|momo|zalo|viettel money|vnpt money|shopee ?pay", folded):
                value = PAYMENT_EWALLET
            elif "tien mat" in folded:
                value = PAYMENT_CASH
            elif re.search(r"tai khoan|ngan hang|chuyen khoan", folded):
                value = PAYMENT_BANK
            else:
                return "", "Vui lòng trả lời: tiền mặt, tài khoản ngân hàng hoặc ví điện tử."
        elif field in {"deceased_death_hour", "deceased_death_minute"}:
            value = _spoken_digits(value) or value
            match = re.search(r"\d{1,2}", value)
            value = match.group(0) if match else ""
            maximum = 23 if field.endswith("hour") else 59
            if not value or int(value) > maximum:
                return "", f"Vui lòng đọc một số từ 0 đến {maximum}."
        elif field == "cremation_support_category":
            # Tương tự support_category: dò từ khoá để chọn đúng 1 trong 19
            # nhóm đối tượng hỗ trợ hỏa táng (khớp thứ tự với docx_service).
            categories = (
                (r"ba me viet nam", "Bà mẹ Việt Nam anh hùng"),
                (r"anh hung luc luong|anh hung lao dong", "Anh hùng lực lượng vũ trang nhân dân, anh hùng lao động"),
                (r"huy hieu 40|dang vien", "Đảng viên có Huy hiệu 40 tuổi Đảng trở lên"),
                (r"truoc.*1945|lao thanh", "Người hoạt động cách mạng trước ngày 01/01/1945"),
                (r"tien khoi nghia|19.?8.?1945", "Người hoạt động cách mạng từ 01/01/1945 đến trước 19/8/1945"),
                (r"thuong binh", "Thương binh từ 81% trở lên"),
                (r"benh binh", "Bệnh binh từ 81% trở lên"),
                (r"chat doc hoa hoc", "Người hoạt động kháng chiến bị nhiễm chất độc hóa học từ 81% trở lên"),
                (r"than nhan liet si", "Thân nhân liệt sĩ, người có công đang hưởng trợ cấp nuôi dưỡng"),
                (r"thi nghe|trung tam duong lao", "Đối tượng chính sách tại Trung tâm dưỡng lão Thị Nghè"),
                (r"can ngheo", "Hộ cận nghèo"),
                (r"ho ngheo|ngheo", "Hộ nghèo"),
                (r"khuyet tat", "Người khuyết tật"),
                (r"cao tuoi", "Người cao tuổi"),
                (r"bao tro xa hoi", "Đối tượng bảo trợ xã hội khác"),
                (r"huu tri", "Đối tượng hưu trí"),
                (r"tre.*tam tru|kt3", "Trẻ từ 6 tuổi trở xuống có tạm trú KT3 tại TP.HCM"),
                (r"tre.*ho khau", "Trẻ từ 6 tuổi trở xuống có hộ khẩu tại TP.HCM"),
                (r"ho khau.*thanh pho|nguoi dan", "Người dân có hộ khẩu tại TP.HCM"),
            )
            value = next((label for pattern, label in categories if re.search(pattern, folded)), "")
            if not value:
                return "", "Tôi chưa xác định được nhóm đối tượng. Vui lòng nói lại tên nhóm như trên giấy tờ xác nhận."
        elif field in {"contact_address", "guardian_current_address"} and re.search(
            r"giong (noi cu tru|ho khau|thuong tru|tren|dia chi tren)|nhu tren", folded
        ):
            # Người trả lời có thể nói "giống nơi cư trú" thay vì đọc lại địa
            # chỉ; khi đó sao chép từ trường thường trú tương ứng đã hỏi trước đó.
            source_field = "guardian_residence_address" if field == "guardian_current_address" else "residence_address"
            value = known_fields.get(source_field, "")
            if not value:
                return "", "Tôi chưa có địa chỉ thường trú tương ứng. Vui lòng cung cấp địa chỉ cụ thể."
        elif field == "disability_procedure_type":
            # Thứ tự kiểm tra quan trọng: "cấp đổi"/"cấp lại" phải được thử
            # trước "xác định lại"/"xác định" vì cùng chứa các âm tiết gần giống.
            categories = (
                (r"cap\s*doi", "Cấp đổi Giấy xác nhận khuyết tật"),
                (r"cap\s*lai", "Cấp lại Giấy xác nhận khuyết tật"),
                (r"xac\s*dinh\s*lai", "Xác định lại mức độ khuyết tật và cấp Giấy xác nhận khuyết tật"),
                (r"xac\s*dinh", "Xác định mức độ khuyết tật và cấp Giấy xác nhận khuyết tật"),
            )
            value = next((label for pattern, label in categories if re.search(pattern, folded)), "")
            if not value:
                return "", "Vui lòng chọn một trong bốn nội dung: xác định, xác định lại, cấp lại hoặc cấp đổi Giấy xác nhận khuyết tật."
        elif field in {"full_name", "deceased_full_name"} and len(re.sub(r"[^A-Za-zÀ-ỹĐđ]", "", value)) < 4:
            # Yêu cầu tối thiểu 4 ký tự chữ cái để tránh nhận nhầm câu trả lời
            # cụt/lỗi ghi âm thành họ tên hợp lệ.
            return "", "Họ và tên có vẻ chưa đầy đủ. Vui lòng cho biết đầy đủ họ, chữ đệm và tên."
        return value, None

    @staticmethod
    def _response(session_id: str, session: InterviewSession, message: str, error: str | None = None) -> dict:
        """Đóng gói trạng thái phiên hiện tại thành dict trả về cho client (câu hỏi kế tiếp, tiến độ, lỗi nếu có)."""
        complete = session.step_index >= len(session.steps)
        step = None if complete else session.steps[session.step_index]
        # Tổng số câu = số câu đã trả lời + số câu còn lại áp dụng với câu trả lời hiện có.
        remaining = sum(1 for item in session.steps[session.step_index :] if session.applies(item))
        total = session.answered + remaining
        return {
            "session_id": session_id,
            "complete": complete,
            "field": step.field if step else None,
            "question": step.question if step else None,
            "message": message,
            "error": error,
            "fields": session.fields.copy(),
            "progress": {"current": min(session.answered + 1, max(total, 1)), "total": total},
        }

    def start(self, template: FormTemplate, prefilled_fields: dict | None = None) -> dict:
        """Tạo một phiên phỏng vấn mới; nếu có prefilled_fields thì điền sẵn và bỏ qua bước đã có dữ liệu."""
        with self._lock:
            self._purge_expired()
            session_id = secrets.token_urlsafe(24)
            session = InterviewSession(template=template, expires_at=time.monotonic() + self.ttl_seconds)
            if prefilled_fields:
                for field_name, value in prefilled_fields.items():
                    if field_name in session.fields and str(value or "").strip():
                        session.fields[field_name] = str(value).strip()
                # Lọc chỉ hỏi những bước mà trường tương ứng vẫn còn trống.
                session.steps = tuple(
                    step for step in session.steps
                    if not str(session.fields.get(step.field) or "").strip()
                )
            session.skip_inapplicable()
            self._sessions[session_id] = session
            pending = sum(1 for step in session.steps if session.applies(step))
            if prefilled_fields and session.step_index >= len(session.steps):
                message = "Đã có đủ thông tin từ tài liệu. Bạn có thể kiểm tra và tạo đơn."
            elif prefilled_fields:
                message = f"Tôi sẽ hỏi thêm {pending} thông tin còn thiếu. Bạn có thể trả lời “bỏ qua” cho trường không bắt buộc."
            else:
                message = "Xin chào. Tôi sẽ hỏi lần lượt để hỗ trợ điền đơn. Bạn có thể trả lời “bỏ qua” cho trường không bắt buộc."
            return self._response(session_id, session, message)

    def answer(self, session_id: str, answer: str) -> dict:
        """Xử lý một câu trả lời: kiểm tra hợp lệ, lưu vào phiên rồi chuyển sang câu hỏi kế tiếp.

        Đây là bước chuyển trạng thái chính của cuộc phỏng vấn: mỗi lần gọi
        chỉ tiến thêm đúng một bước (step_index += 1) khi câu trả lời hợp lệ;
        nếu có lỗi thì giữ nguyên bước hiện tại để hỏi lại.
        """
        with self._lock:
            session = self._session(session_id)
            if session.step_index >= len(session.steps):
                return self._response(session_id, session, "Cuộc phỏng vấn đã hoàn tất. Hãy kiểm tra lại thông tin trước khi tạo đơn.")
            step = session.steps[session.step_index]
            answer = normalize_text(answer)
            if not answer:
                return self._response(session_id, session, "Tôi chưa nhận được câu trả lời. Vui lòng trả lời câu hỏi này.", "Câu trả lời không được để trống.")
            if self._is_skip(answer):
                if step.required:
                    return self._response(session_id, session, "Trường này bắt buộc để tạo đơn. Vui lòng cung cấp thông tin.", "Không thể bỏ qua trường bắt buộc.")
                value = ""
            else:
                value, error = self._normalise_answer(step.field, answer, session.fields)
                if error:
                    return self._response(session_id, session, error, error)

            session.fields[step.field] = value
            session.step_index += 1
            session.answered += 1
            session.skip_inapplicable()
            if session.step_index >= len(session.steps):
                # Hết danh sách câu hỏi: kiểm tra lại toàn bộ dữ liệu theo yêu
                # cầu bắt buộc của mẫu đơn (không chỉ theo required của từng bước).
                validation = validate_form_data(session.fields, session.template.required_fields)
                if not validation.valid:
                    return self._response(session_id, session, "Đã thu thập xong. Một vài trường bắt buộc cần được rà soát ở bước tiếp theo.")
                return self._response(session_id, session, "Đã thu thập đủ thông tin. Tôi đã điền dữ liệu vào biểu mẫu để bạn kiểm tra lần cuối.")
            next_step = session.steps[session.step_index]
            value_message = "Đã bỏ qua trường không bắt buộc." if not value else f"Đã ghi nhận {session.label(step.field)}."
            return self._response(session_id, session, f"{value_message} {next_step.question}")

    def discard(self, session_id: str) -> None:
        """Huỷ bỏ một phiên phỏng vấn (người dùng thoát giữa chừng)."""
        with self._lock:
            self._sessions.pop(session_id, None)
