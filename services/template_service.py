"""Danh mục các mẫu đơn Word trong thư mục templates_word mà chatbot hỗ trợ điền."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unicodedata

from config import BASE_DIR

TEMPLATE_DIR = BASE_DIR / "templates_word"

# Nhóm hiển thị trong chatbot: người dân chọn nhóm trước rồi mới chọn mẫu,
# để không phải đọc một danh sách quá dài.
TEMPLATE_GROUPS = {
    "to_khai": "Tờ khai đề nghị hưởng chế độ",
    "xac_nhan": "Đơn xác nhận chưa hưởng chế độ",
}

# Nhãn riêng cho các mẫu khai thay: trường "full_name"... là của người được
# xác nhận, còn các trường guardian_* là của người đứng ra khai thay.
_PROXY_LABELS = {
    "full_name": "Họ tên người được xác nhận",
    "date_of_birth": "Ngày sinh người được xác nhận",
    "citizen_id": "Số CCCD người được xác nhận",
    "citizen_id_issue_date": "Ngày cấp CCCD người được xác nhận",
    "residence_address": "Hộ khẩu thường trú người được xác nhận",
    "contact_address": "Nơi ở hiện nay người được xác nhận",
    "guardian_full_name": "Họ tên người khai thay",
    "guardian_citizen_id": "Số CCCD người khai thay",
    "guardian_citizen_id_issue_date": "Ngày cấp CCCD người khai thay",
    "guardian_citizen_id_issue_place": "Nơi cấp CCCD người khai thay",
    "guardian_relationship": "Người khai là gì của người được xác nhận",
    "guardian_address": "Địa chỉ người khai thay",
    "subject_relationship": "Người được xác nhận là gì của người khai",
}


@dataclass(frozen=True)
class FormTemplate:
    """Một mẫu đơn: tệp Word nguồn, tên hiển thị và các trường bắt buộc trước khi xuất."""

    id: str
    title: str
    short_title: str
    group: str
    file_name: str
    download_name: str
    required_fields: tuple[str, ...]
    # Từ khoá (không dấu) để nhận ra mẫu khi người dân gõ/nói tự do.
    keywords: tuple[str, ...] = ()
    # Các mẫu cùng "family" là hai phiên bản tự khai/khai thay của một đơn.
    family: str = ""
    variant: str = ""
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        """Đường dẫn tệp Word; so khớp tên theo Unicode NFC để không lệch giữa Windows và Linux."""
        direct = TEMPLATE_DIR / self.file_name
        if direct.is_file():
            return direct
        wanted = unicodedata.normalize("NFC", self.file_name).casefold()
        if TEMPLATE_DIR.is_dir():
            for candidate in TEMPLATE_DIR.iterdir():
                if unicodedata.normalize("NFC", candidate.name).casefold() == wanted:
                    return candidate
        return direct

    @property
    def family_id(self) -> str:
        return self.family or self.id


_IDENTITY = ("full_name", "date_of_birth", "citizen_id", "residence_address")
_DECEASED = ("deceased_full_name", "deceased_death_date")

_TEMPLATE_LIST = (
    # ---- Tờ khai đề nghị hưởng chế độ ---------------------------------------
    FormTemplate(
        id="tro_cap_huu_tri",
        title="Tờ khai đề nghị trợ cấp hưu trí xã hội (Mẫu số 01)",
        short_title="Trợ cấp hưu trí xã hội",
        group="to_khai",
        file_name="Mẫu số 01_Trợ cấp huu tri xa hoi.docx",
        download_name="to-khai-de-nghi-tro-cap-huu-tri-xa-hoi",
        required_fields=_IDENTITY,
        keywords=("huu tri",),
        labels={
            "guardian_full_name": "Họ tên người giám hộ",
            "guardian_residence_address": "Nơi cư trú người giám hộ",
            "guardian_current_address": "Nơi ở hiện nay người giám hộ",
        },
    ),
    FormTemplate(
        id="ho_tro_mai_tang",
        title="Tờ khai đề nghị hỗ trợ chi phí mai táng (Mẫu số 03)",
        short_title="Hỗ trợ chi phí mai táng",
        group="to_khai",
        file_name="Mẫu số 03_Hỗ trợ mai táng.docx",
        download_name="to-khai-de-nghi-ho-tro-chi-phi-mai-tang",
        required_fields=(*_IDENTITY, *_DECEASED, "burial_support_category", "payment_method"),
        keywords=("mai tang",),
    ),
    FormTemplate(
        id="ho_tro_hoa_tang",
        title="Tờ khai nhận chi phí hỗ trợ khuyến khích hỏa táng",
        short_title="Hỗ trợ chi phí hỏa táng",
        group="to_khai",
        file_name="tờ khai Hoả táng.docx",
        download_name="to-khai-ho-tro-khuyen-khich-hoa-tang",
        required_fields=(*_IDENTITY, *_DECEASED, "cremation_support_category"),
        keywords=("hoa tang",),
    ),
    FormTemplate(
        id="ho_tro_nq40",
        title="Tờ khai nhận hỗ trợ theo Nghị quyết 40/NQ-HĐND (trước tháng 9)",
        short_title="Hỗ trợ theo Nghị quyết 40",
        group="to_khai",
        file_name="tờ khai đề nghị hỗ trợ NQ40 (trước tháng 9).docx",
        download_name="to-khai-ho-tro-nghi-quyet-40",
        required_fields=(*_IDENTITY, "citizen_id_issue_date", "citizen_id_issue_place", "support_category"),
        keywords=("nghi quyet 40", "nghi quyet so 40", "nq40", "nq 40"),
    ),
    FormTemplate(
        id="ho_tro_nq32",
        title="Tờ khai nhận hỗ trợ theo Nghị quyết 32/2025/NQ-HĐND (sau tháng 9)",
        short_title="Hỗ trợ theo Nghị quyết 32",
        group="to_khai",
        file_name="tờ khai đề nghị hỗ trợ NQ32 (sau tháng 9).docx",
        download_name="to-khai-ho-tro-nghi-quyet-32",
        required_fields=(*_IDENTITY, "citizen_id_issue_date", "citizen_id_issue_place", "support_category"),
        keywords=("nghi quyet 32", "nghi quyet so 32", "nq32", "nq 32"),
    ),
    FormTemplate(
        id="xac_dinh_khuyet_tat",
        title="Đơn đề nghị xác định, xác định lại mức độ khuyết tật và cấp, cấp đổi, cấp lại Giấy xác nhận khuyết tật",
        short_title="Xác định mức độ khuyết tật",
        group="to_khai",
        file_name="Đơn xác định mức độ KT  (mẫu 01).docx",
        download_name="don-xac-dinh-muc-do-khuyet-tat",
        required_fields=(*_IDENTITY, "disability_procedure_type"),
        keywords=("khuyet tat",),
        labels={
            "full_name": "Họ tên người được xác định khuyết tật",
            "date_of_birth": "Ngày sinh người được xác định khuyết tật",
            "guardian_full_name": "Họ tên người đại diện hợp pháp",
            "guardian_citizen_id": "CCCD người đại diện",
            "guardian_phone": "Số điện thoại người đại diện",
            "guardian_relationship": "Quan hệ với người được xác định khuyết tật",
            "guardian_residence_address": "Hộ khẩu thường trú người đại diện",
            "guardian_current_address": "Nơi ở hiện nay người đại diện",
        },
    ),
    FormTemplate(
        id="to_khai_bhyt",
        title="Tờ khai tham gia, điều chỉnh thông tin bảo hiểm xã hội, bảo hiểm y tế",
        short_title="Tờ khai BHYT",
        group="to_khai",
        file_name="Tờ khai BHYT.docx",
        download_name="to-khai-tham-gia-dieu-chinh-bhxh-bhyt",
        required_fields=("full_name", "date_of_birth", "citizen_id"),
        keywords=("bhyt", "bao hiem y te", "bhxh", "bao hiem xa hoi", "the bao hiem"),
        labels={"guardian_full_name": "Họ tên cha/mẹ/giám hộ (trẻ dưới 6 tuổi)"},
    ),
    # ---- Đơn xác nhận chưa hưởng chế độ (dùng khi chuyển nơi ở) -------------
    FormTemplate(
        id="xn_huu_tri_tu_khai",
        title="Đơn xác nhận chưa làm hồ sơ hưởng trợ cấp hưu trí xã hội (tự khai)",
        short_title="Chưa hưởng trợ cấp hưu trí xã hội",
        group="xac_nhan",
        file_name="ĐƠN XN chưa hưởng trợ cấp hưu trí (tự khai).docx",
        download_name="don-xac-nhan-chua-huong-tro-cap-huu-tri",
        required_fields=_IDENTITY,
        keywords=("huu tri",),
        family="xn_huu_tri",
        variant="tu_khai",
    ),
    FormTemplate(
        id="xn_huu_tri_khai_thay",
        title="Đơn xác nhận chưa làm hồ sơ hưởng trợ cấp hưu trí xã hội (khai thay)",
        short_title="Chưa hưởng trợ cấp hưu trí xã hội",
        group="xac_nhan",
        file_name="ĐƠN XN chưa hưởng trợ cấp hưu trí (khai thay).docx",
        download_name="don-xac-nhan-chua-huong-tro-cap-huu-tri-khai-thay",
        required_fields=(*_IDENTITY, "guardian_full_name", "guardian_relationship"),
        keywords=("huu tri",),
        family="xn_huu_tri",
        variant="khai_thay",
        labels=_PROXY_LABELS,
    ),
    FormTemplate(
        id="xn_mung_tho_tu_khai",
        title="Đơn xác nhận chưa làm hồ sơ hưởng chế độ mừng thọ (tự khai)",
        short_title="Chưa hưởng chế độ mừng thọ",
        group="xac_nhan",
        file_name="Đơn XN chưa hưởng mừng thọ.docx",
        download_name="don-xac-nhan-chua-huong-mung-tho",
        required_fields=(*_IDENTITY, "destination_ward"),
        keywords=("mung tho", "chuc tho"),
        family="xn_mung_tho",
        variant="tu_khai",
    ),
    FormTemplate(
        id="xn_mung_tho_khai_thay",
        title="Đơn xác nhận chưa làm hồ sơ hưởng chế độ mừng thọ (khai thay)",
        short_title="Chưa hưởng chế độ mừng thọ",
        group="xac_nhan",
        file_name="Đơn XN chưa hưởng mừng thọ (khai thay).docx",
        download_name="don-xac-nhan-chua-huong-mung-tho-khai-thay",
        required_fields=(*_IDENTITY, "destination_ward", "subject_relationship", "guardian_relationship"),
        keywords=("mung tho", "chuc tho"),
        family="xn_mung_tho",
        variant="khai_thay",
        labels=_PROXY_LABELS,
    ),
    FormTemplate(
        id="xn_khuyet_tat_tu_khai",
        title="Đơn xác nhận chưa làm hồ sơ xác định mức độ khuyết tật (tự khai)",
        short_title="Chưa làm hồ sơ khuyết tật",
        group="xac_nhan",
        file_name="ĐƠN XN chưa làm hồ sơ khuyết tật.docx",
        download_name="don-xac-nhan-chua-lam-ho-so-khuyet-tat",
        required_fields=(*_IDENTITY, "destination_ward"),
        keywords=("khuyet tat",),
        family="xn_khuyet_tat",
        variant="tu_khai",
    ),
    FormTemplate(
        id="xn_khuyet_tat_khai_thay",
        title="Đơn xác nhận chưa làm hồ sơ xác định mức độ khuyết tật (khai thay)",
        short_title="Chưa làm hồ sơ khuyết tật",
        group="xac_nhan",
        file_name="ĐƠN XN chưa làm hồ sơ khuyết  tật (khai thay).docx",
        download_name="don-xac-nhan-chua-lam-ho-so-khuyet-tat-khai-thay",
        required_fields=(
            *_IDENTITY,
            "destination_ward",
            "subject_relationship",
            "guardian_full_name",
            "guardian_relationship",
        ),
        keywords=("khuyet tat",),
        family="xn_khuyet_tat",
        variant="khai_thay",
        labels=_PROXY_LABELS,
    ),
    FormTemplate(
        id="xn_nq35",
        title="Đơn xác nhận chưa làm hồ sơ hưởng chế độ theo Nghị quyết 35/2023/NQ-HĐND",
        short_title="Chưa hưởng chế độ theo Nghị quyết 35",
        group="xac_nhan",
        file_name="Đơn XN chưa làm hồ sơ NQ 35.docx",
        download_name="don-xac-nhan-chua-huong-nghi-quyet-35",
        required_fields=(*_IDENTITY, "destination_ward"),
        keywords=("nghi quyet 35", "nghi quyet so 35", "nq35", "nq 35"),
    ),
    FormTemplate(
        id="xn_nq40",
        title="Đơn xác nhận chưa nhận hỗ trợ theo Nghị quyết 40/NQ-HĐND",
        short_title="Chưa nhận hỗ trợ theo Nghị quyết 40",
        group="xac_nhan",
        file_name="ĐON XN chưa hưởng NQ40.docx",
        download_name="don-xac-nhan-chua-nhan-ho-tro-nghi-quyet-40",
        required_fields=_IDENTITY,
        keywords=("nghi quyet 40", "nghi quyet so 40", "nq40", "nq 40"),
    ),
    FormTemplate(
        id="xn_nq32",
        title="Đơn xác nhận chưa nhận hỗ trợ theo Nghị quyết 32/2025/NQ-HĐND",
        short_title="Chưa nhận hỗ trợ theo Nghị quyết 32",
        group="xac_nhan",
        file_name="ĐON XN chưa hưởng NQ32.docx",
        download_name="don-xac-nhan-chua-nhan-ho-tro-nghi-quyet-32",
        required_fields=_IDENTITY,
        keywords=("nghi quyet 32", "nghi quyet so 32", "nq32", "nq 32"),
    ),
)

TEMPLATES: dict[str, FormTemplate] = {template.id: template for template in _TEMPLATE_LIST}


def get_template(template_id: str) -> FormTemplate | None:
    """Tra cứu một template theo id; trả về None nếu không tồn tại."""
    return TEMPLATES.get(template_id)


def list_templates() -> list[dict[str, str]]:
    """Danh sách rút gọn (id, tiêu đề) của mọi template, dùng cho ô chọn mẫu."""
    return [{"id": item.id, "title": item.title} for item in TEMPLATES.values()]
