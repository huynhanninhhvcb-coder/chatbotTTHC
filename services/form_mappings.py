"""Cách điền từng mẫu đơn trong templates_word: nhãn in sẵn nào nhận trường dữ liệu nào.

Nhãn được chép nguyên văn từ tệp Word. Khi cán bộ sửa chữ của một nhãn trong
mẫu, cần sửa lại nhãn tương ứng ở đây; bộ test ``tests/test_docx_mapping.py``
sẽ báo nhãn nào không còn tìm thấy.
"""

from __future__ import annotations

from datetime import date
from collections.abc import Callable

from services.form_filler import Blank, FormCursor, date_parts
from services.form_options import (
    BURIAL_SUPPORT_CATEGORIES,
    CREMATION_CATEGORIES,
    DISABILITY_PROCEDURES,
    PAYMENT_BANK,
    PAYMENT_CASH,
    PAYMENT_EWALLET,
    RECIPIENT_GUARDIAN,
    SUPPORT_CATEGORIES,
)

WARD_NAME = "Minh Phụng"
CITY_NAME = "Hồ Chí Minh"
DEFAULT_BURIAL_REQUEST = "Đề nghị hỗ trợ chi phí mai táng"

Fields = dict[str, str]


def age_on(date_of_birth: str, today: date | None = None) -> int | None:
    """Tuổi tròn tính đến hôm nay từ ngày sinh dd/mm/yyyy; None nếu ngày sinh không hợp lệ."""
    day, month, year = date_parts(date_of_birth)
    if not year:
        return None
    try:
        born = date(int(year), int(month), int(day))
    except ValueError:
        return None
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# ---- Tờ khai đề nghị hưởng chế độ --------------------------------------------


def _fill_mau01_person(c: FormCursor, person: Fields, name_label: str, residence_label: str, current_label: str) -> None:
    """Khối thông tin cá nhân lặp lại 3 lần trong Mẫu số 01 (người hưởng, người giám hộ, người nhận tiền)."""
    c.fill((name_label, person.get("full_name", "")))
    c.fill(("Số căn cước/Số định danh cá nhân:", person.get("citizen_id", "")), ("ngày cấp", person.get("citizen_id_issue_date", "")))
    c.fill(("Nơi cấp:", person.get("citizen_id_issue_place", "")))
    c.fill(
        ("Ngày sinh:", person.get("date_of_birth", "")),
        ("Giới tính:", person.get("gender", "")),
        ("Dân tộc:", person.get("ethnic_group", "")),
    )
    c.fill((residence_label, person.get("residence_address", "")))
    c.fill((current_label, person.get("contact_address", "")))


def _guardian_as_person(f: Fields) -> Fields:
    return {
        "full_name": f["guardian_full_name"],
        "citizen_id": f["guardian_citizen_id"],
        "citizen_id_issue_date": f["guardian_citizen_id_issue_date"],
        "citizen_id_issue_place": f["guardian_citizen_id_issue_place"],
        "date_of_birth": f["guardian_date_of_birth"],
        "residence_address": f["guardian_residence_address"],
        "contact_address": f["guardian_current_address"],
    }


def fill_tro_cap_huu_tri(c: FormCursor, f: Fields) -> None:
    """Mẫu số 01: chatbot chỉ hỗ trợ trường hợp người cao tuổi đề nghị trợ cấp hưu trí xã hội (mục I, II.2, III)."""
    c.check("Người cao tuổi đề nghị hưởng trợ cấp hưu trí xã hội khai mục I")
    c.seek("1. Đối tượng hưởng trợ cấp xã hội, trợ cấp hưu trí xã hội")
    _fill_mau01_person(c, f, "1.1. Họ và tên:", "1.2. Nơi cư trú:", "1.3. Nơi ở hiện nay (Ghi cụ thể):")
    c.fill(("1.4. Số điện thoại:", f["phone_number"]))
    c.seek("1.5. Thẻ BHYT")
    c.check("Có", bool(f["health_insurance_number"]))
    c.fill(("Số thẻ BHYT:", f["health_insurance_number"]))

    guardian = _guardian_as_person(f)
    c.seek("2. Thông tin người giám hộ")
    _fill_mau01_person(c, guardian, "2.1. Họ và tên:", "2.2. Nơi cư trú:", "2.3. Nơi ở hiện nay (Ghi cụ thể):")
    c.fill(("2.4. Số điện thoại:", f["guardian_phone"]))
    c.fill(("2.5. Mối quan hệ với người đề nghị hưởng chính sách:", f["guardian_relationship"]))

    # Mục II.2: chọn ô theo tuổi người đề nghị (đủ 75 tuổi, hoặc 70-dưới 75 tuổi thuộc hộ nghèo/cận nghèo).
    age = age_on(f["date_of_birth"])
    c.seek("2. Thông tin đề nghị hưởng trợ cấp hưu trí xã hội")
    c.check("Người từ đủ 75 tuổi trở lên", age is not None and age >= 75)
    c.check("Người từ đủ 70 tuổi đến dưới 75 tuổi", age is not None and 70 <= age < 75)

    c.seek("III. Phương thức nhận kinh phí trợ cấp")
    recipient = guardian if f["benefit_recipient"] == RECIPIENT_GUARDIAN and f["guardian_full_name"] else f
    _fill_mau01_person(c, recipient, "Họ và tên:", "- Nơi cư trú:", "- Nơi ở hiện nay (Ghi cụ thể):")
    method = f["payment_method"]
    c.check("Tiền mặt", method == PAYMENT_CASH)
    c.check("Tài khoản ngân hàng", method == PAYMENT_BANK)
    c.fill(("Số tài khoản ngân hàng:", f["bank_account_number"]))
    c.fill(("Tên chủ tài khoản:", f["bank_account_name"]))
    c.fill(("Tại ngân hàng:", f["bank_name"]))
    c.check("Ví điện tử", method == PAYMENT_EWALLET)
    c.fill(("Số ví:", f["ewallet_number"]))
    c.fill(("Tên chủ ví:", f["ewallet_owner"]))
    c.fill(("Tên loại ví điện tử:", f["ewallet_provider"]))
    c.sign_date("NGƯỜI KHAI")


def fill_ho_tro_mai_tang(c: FormCursor, f: Fields) -> None:
    """Mẫu số 03: người đề nghị, người đã mất, người/tổ chức lo mai táng và hình thức nhận tiền."""
    c.seek("1. Thông tin người")
    c.fill(("Họ và tên :", f["full_name"]))
    c.fill(("Số căn cước hoặc số định danh cá nhân:", f["citizen_id"]), ("ngày cấp", f["citizen_id_issue_date"]))
    c.fill(("Nơi cấp:", f["citizen_id_issue_place"]))
    c.fill(("Nơi cư trú:", f["residence_address"]))
    c.fill(("Nội dung đề nghị:", f["request_content"] or DEFAULT_BURIAL_REQUEST))

    c.seek("2. Thông tin người chết được tổ chức mai táng")
    c.fill(("Họ và tên:", f["deceased_full_name"]))
    c.fill(("Ngày sinh:", f["deceased_date_of_birth"]))
    c.fill(
        ("Giới tính:", f["deceased_gender"]),
        ("Dân tộc:", f["deceased_ethnic_group"]),
        ("Quốc tịch:", f["deceased_nationality"]),
    )
    c.fill(("Nơi cư trú:", f["deceased_residence_address"]))
    c.fill(("Số căn cước hoặc số định danh cá nhân:", f["deceased_citizen_id"]))
    day, month, year = date_parts(f["deceased_death_date"])
    c.fill(
        ("Đã chết vào lúc:", f["deceased_death_hour"]),
        ("giờ", f["deceased_death_minute"]),
        ("phút, ngày", day),
        ("tháng", month),
        ("năm", year),
    )
    c.fill(("Nơi chết:", f["deceased_death_place"]))
    c.fill(("Nguyên nhân chết:", f["deceased_death_cause"]))
    c.fill(("Giấy tờ thay thế Giấy báo tử:", f["death_certificate_number"]), ("do:", f["death_certificate_issuer"]))
    day, month, year = date_parts(f["death_certificate_date"])
    c.fill(("cấp ngày", day), ("tháng", month), ("năm", year))
    c.seek("Thuộc đối tượng:")
    for value, printed in BURIAL_SUPPORT_CATEGORIES:
        c.check(printed, f["burial_support_category"] == value)

    # 3.1 là chính người đề nghị (cá nhân/thân nhân); khi có tổ chức đứng ra
    # lo mai táng thì để trống 3.1 và điền 3.2.
    person = {} if f["org_name"] else f
    c.seek("3.1. Trường hợp cá nhân")
    c.fill(("Họ và tên:", person.get("full_name", "")))
    c.fill(("Ngày sinh:", person.get("date_of_birth", "")), ("Nam/Nữ:", person.get("gender", "")))
    c.fill(
        ("Số căn cước hoặc số định danh cá nhân:", person.get("citizen_id", "")),
        ("ngày cấp", person.get("citizen_id_issue_date", "")),
    )
    c.fill(("Nơi cấp:", person.get("citizen_id_issue_place", "")))
    c.fill(("Địa chỉ nơi cư trú:", person.get("residence_address", "")))
    c.fill(("Mối quan hệ với người chết:", person.get("deceased_relationship", "")))
    c.fill(("Số điện thoại:", person.get("phone_number", "")))
    c.seek("3.2. Trường hợp cơ quan, tổ chức")
    c.fill(("Tên tổ chức:", f["org_name"]))
    c.fill(("Địa chỉ:", f["org_address"]))
    c.fill(("Người đại diện theo pháp luật:", f["org_representative_name"]), ("Chức vụ:", f["org_representative_title"]))
    c.fill(("Số điện thoại:", f["org_phone"]))

    method = f["payment_method"]
    c.seek("4. Hình thức nhận chi phí hỗ trợ mai táng")
    c.check("Tài khoản ngân hàng", method == PAYMENT_BANK)
    c.fill(("Tên chủ tài khoản:", f["bank_account_name"]))
    c.fill(("Số tài khoản:", f["bank_account_number"]))
    c.fill(("Ngân hàng:", f["bank_name"]))
    c.check("Ví điện tử", method == PAYMENT_EWALLET)
    c.fill(("Số ví:", f["ewallet_number"]))
    c.fill(("Tên chủ ví:", f["ewallet_owner"]))
    c.fill(("Tên loại ví điện tử:", f["ewallet_provider"]))
    c.check("Tiền mặt", method == PAYMENT_CASH)
    c.sign_date("NGƯỜI KHAI")


def fill_ho_tro_hoa_tang(c: FormCursor, f: Fields) -> None:
    """Tờ khai nhận chi phí hỗ trợ khuyến khích hỏa táng (phần xác nhận của UBND để trống)."""
    c.fill(("1. Tôi tên là:", f["full_name"]))
    c.fill(("2. Ngày, tháng, năm sinh:", f["date_of_birth"]), ("3. CCCD số:", f["citizen_id"]))
    c.fill(("4. Hộ khẩu thường trú:", f["residence_address"]))
    c.fill(("Số Tài khoản:", f["bank_account_number"]), ("Ngân hàng:", f["bank_name"]))
    c.fill(("Số điện thoại:", f["phone_number"]))
    c.fill(("5. Quan hệ với người mất:", f["deceased_relationship"]))
    c.fill(("Hoặc đại diện cho tổ chức (nếu có):", f["org_name"]))
    c.fill(("6. Họ và tên người mất:", f["deceased_full_name"]))
    c.fill(("7. Đã từ trần ngày:", f["deceased_death_date"]))
    c.fill(
        ("Giấy chứng tử số:", f["death_certificate_number"]),
        ("ngày", f["death_certificate_date"]),
        ("do", f["death_certificate_issuer"]),
    )
    c.seek("8. Thuộc đối tượng")
    category = f["cremation_support_category"]
    for value, printed in CREMATION_CATEGORIES:
        if value.startswith("Trẻ từ 6 tuổi") and printed.startswith("Có hộ khẩu"):
            # "Có hộ khẩu tại Thành phố..." cũng là đuôi của dòng "Người dân có
            # hộ khẩu..." ngay phía trên, nên nhảy tới mục trẻ em trước khi tìm.
            c.seek("Trẻ từ 6 tuổi trở xuống")
        c.check(printed, category == value)
        if value in {"Hộ nghèo", "Hộ cận nghèo"}:
            c.fill((f"{printed}, mã số:", f["cremation_support_detail"] if category == value else ""))
    c.sign_date("Người khai")


def fill_ho_tro_nghi_quyet(c: FormCursor, f: Fields) -> None:
    """Tờ khai nhận hỗ trợ theo Nghị quyết 40 hoặc 32 (hai mẫu có cùng bố cục)."""
    c.fill(("1. Họ và tên:", f["full_name"]))
    c.fill(("2. Ngày, tháng, năm sinh:", f["date_of_birth"]))
    c.fill(
        ("3. CCCD số:", f["citizen_id"]),
        ("Ngày cấp:", f["citizen_id_issue_date"]),
        ("Nơi cấp", f["citizen_id_issue_place"]),
    )
    c.fill(("4. Hộ khẩu thường trú:", f["residence_address"]))
    c.fill(("5. Nơi tạm trú (nếu có):", f["temporary_address"]))
    c.fill(("6. Số điện thoại:", f["phone_number"]))
    c.fill(("7. Nghề nghiệp:", f["occupation"]))
    c.fill(("8. Đơn vị công tác:", f["employer"]))
    category = f["support_category"]
    for index, (value, printed) in enumerate(SUPPORT_CATEGORIES):
        selected = category == value
        c.check(printed, selected)
        if index:  # Các nhóm sau nhóm đầu đều có chỗ ghi mã số/thông tin cụ thể.
            label = printed if printed.endswith(":") else f"{printed}, mã số:"
            c.fill((label, f["support_detail"] if selected else ""))
    c.sign_date("Người khai")


def fill_xac_dinh_khuyet_tat(c: FormCursor, f: Fields) -> None:
    """Mẫu số 01 (TT 01/2019): phần I, II; bảng dạng tật và mức độ để người dân tự đánh dấu."""
    c.fill(("Chủ tịch Ủy ban nhân dân phường", WARD_NAME))
    c.fill(("Tỉnh, thành phố", CITY_NAME))
    c.seek("tôi đề nghị:")
    for procedure in DISABILITY_PROCEDURES:
        c.check(procedure, f["disability_procedure_type"] == procedure)
    c.seek("I. Thông tin người được xác định mức độ khuyết tật")
    c.fill(("- Họ và tên:", f["full_name"]))
    day, month, year = date_parts(f["date_of_birth"])
    c.fill(("- Sinh ngày", day), ("tháng", month), ("năm", year), ("Giới tính:", f["gender"]))
    c.fill(("- Số CMND hoặc căn cước công dân:", f["citizen_id"]))
    c.fill(("- Hộ khẩu thường trú:", f["residence_address"]))
    c.fill(("- Nơi ở hiện nay:", f["contact_address"]))
    c.seek("II. Thông tin người đại diện hợp pháp")
    c.fill(("- Họ và tên:", f["guardian_full_name"]))
    c.fill(("- Mối quan hệ với người được xác định khuyết tật:", f["guardian_relationship"]))
    c.fill(("- Số CMND hoặc căn cước công dân:", f["guardian_citizen_id"]))
    c.fill(("- Hộ khẩu thường trú:", f["guardian_residence_address"]))
    c.fill(("- Nơi ở hiện nay:", f["guardian_current_address"]))
    c.fill(("- Số điện thoại:", f["guardian_phone"]))
    c.sign_date("Người viết đơn")


def fill_to_khai_bhyt(c: FormCursor, f: Fields) -> None:
    """Tờ khai TK1-TS: mục I khi chưa có mã số BHXH, mục II khi đã có mã số."""
    has_code = bool(f["social_insurance_number"])

    def part1(value: str) -> str:
        return "" if has_code else value

    def part2(value: str) -> str:
        return value if has_code else ""

    name = f["full_name"].upper()
    c.fill(("[01]. Họ và tên (viết chữ in hoa):", part1(name)), ("[02]. Giới tính:", part1(f["gender"])))
    c.fill(("[03]. Ngày, tháng, năm sinh:", part1(f["date_of_birth"])), ("[04]. Quốc tịch:", ""))
    # Mẫu in sẵn "Dân tộc: Kinh"; chỉ thay khi người khai cho biết dân tộc khác.
    c.fill(Blank("[05]. Dân tộc:", part1(f["ethnic_group"]), overwrite=True), ("[06]. Số CCCD/ĐDCN/Hộ chiếu:", part1(f["citizen_id"])))
    c.fill(("[07]. Điện thoại:", part1(f["phone_number"])), ("[08]. Email (nếu có):", part1(f["email"])))
    c.fill(Blank("[09.1]. Xã:", part1(f["birth_registration_ward"]), overwrite=True), ("[09.2]. Huyện:", ""))
    c.fill(("[09.3]. Tỉnh:", part1(f["birth_registration_province"])))
    c.fill(("[10]. Họ tên cha/mẹ/giám hộ (đối với trẻ em dưới 6 tuổi):", part1(f["guardian_full_name"])))

    c.seek("II. Áp dụng đối với người tham gia đã có mã số BHXH")
    c.fill(("[13]. Mã số", f["social_insurance_number"]), ("[14]. Điều chỉnh thông tin cá nhân:", ""))
    c.fill(("[14.1]. Họ và tên (viết chữ in hoa):", part2(name)), ("[14.2]. Giới tính:", part2(f["gender"])))
    birth_place = ", ".join(value for value in (f["birth_registration_ward"], f["birth_registration_province"]) if value)
    c.fill(("[14.3]. Ngày, tháng, năm sinh:", part2(f["date_of_birth"])), ("[14.4]. Nơi đăng ký khai sinh:", part2(birth_place)))
    c.fill(("[14.5]. Số CCCD/ĐDCN:", part2(f["citizen_id"])))
    c.fill(("[17]. Nơi đăng ký khám, chữa bệnh ban đầu:", f["primary_care_facility"]))
    c.fill(("[18]. Nội dung thay đổi, yêu cầu khác:", f["request_content"]))
    c.sign_date("Người kê khai")


# ---- Đơn xác nhận chưa hưởng chế độ -------------------------------------------


def _fill_xn_identity(c: FormCursor, f: Fields, name_label: str, current_label: str) -> None:
    """Bốn dòng đầu giống nhau ở mọi đơn xác nhận: họ tên + ngày sinh, CCCD + ngày cấp, thường trú, nơi ở."""
    c.fill((name_label, f["full_name"]), ("Sinh ngày:", f["date_of_birth"]))
    c.fill(("- Số căn cước công dân:", f["citizen_id"]), ("cấp ngày:", f["citizen_id_issue_date"]))
    c.fill(("- Hộ khẩu thường trú:", f["residence_address"]))
    c.fill((current_label, f["contact_address"]))


def _destination(f: Fields) -> str:
    return f["destination_ward"]


def fill_xn_huu_tri_tu_khai(c: FormCursor, f: Fields) -> None:
    _fill_xn_identity(c, f, "- Họ tên:", "- Hiện cư ngụ tại số:")
    c.sign_date("Người làm đơn")


def fill_xn_huu_tri_khai_thay(c: FormCursor, f: Fields) -> None:
    _fill_xn_identity(c, f, "- Họ tên người xác nhận:", "- Hiện cư ngụ tại số:")
    c.fill(("xác nhận cho ông/bà", f["full_name"]))
    c.seek("Thông tin người khai thay")
    c.fill(("Họ tên:", f["guardian_full_name"]))
    c.fill(("Giấy CMND hoặc Căn cước công dân số:", f["guardian_citizen_id"]))
    c.fill(("Ngày cấp:", f["guardian_citizen_id_issue_date"]))
    c.fill(("Nơi cấp:", f["guardian_citizen_id_issue_place"]))
    c.fill(("Mối quan hệ với đối tượng:", f["guardian_relationship"]))
    c.fill(("Địa chỉ:", f["guardian_address"]))
    c.sign_date("Người làm đơn")


def fill_xn_mung_tho_tu_khai(c: FormCursor, f: Fields) -> None:
    _fill_xn_identity(c, f, "- Họ tên:", "- Hiện cư ngụ tại số:")
    c.fill(("- Số điện thoại:", f["phone_number"]))
    c.fill(Blank("bổ sung hồ sơ hưởng chế độ chúc thọ, mừng thọ tại", _destination(f), overwrite=True))
    c.fill(Blank("xin hưởng chế độ chúc thọ, mừng thọ tại", _destination(f), overwrite=True))
    c.sign_date("Người làm đơn")


def fill_xn_mung_tho_khai_thay(c: FormCursor, f: Fields) -> None:
    _fill_xn_identity(c, f, "- Họ tên người đề nghị xác nhận:", "- Hiện cư ngụ tại số:")
    c.fill(Blank("bổ sung hồ sơ hưởng chế độ chúc thọ, mừng thọ tại", _destination(f), overwrite=True))
    # Mẫu in sẵn "xác nhận cha tôi"; thay "cha" bằng quan hệ thực tế (mẹ, ông, bà...).
    c.fill(
        Blank("Minh Phụng xác nhận", f["subject_relationship"], overwrite=True),
        Blank("tôi từ trước tới nay", ""),
        Blank("xin hưởng chế độ chúc thọ, mừng thọ tại", _destination(f), overwrite=True),
    )
    c.seek("Thông tin người khai thay")
    c.fill(("Giấy CMND hoặc Căn cước công dân số:", f["guardian_citizen_id"]))
    c.fill(("Ngày cấp:", f["guardian_citizen_id_issue_date"]))
    c.fill(("Nơi cấp:", f["guardian_citizen_id_issue_place"]))
    c.fill(("Mối quan hệ với đối tượng:", f["guardian_relationship"]))
    c.fill(("Địa chỉ:", f["guardian_address"]))
    c.sign_date("Người làm đơn")


def fill_xn_khuyet_tat_tu_khai(c: FormCursor, f: Fields) -> None:
    _fill_xn_identity(c, f, "- Tôi tên:", "- Địa chỉ cư trú:")
    c.fill(Blank("tại nơi ở hiện tại là", _destination(f), overwrite=True))
    # Phần xác nhận của phường in sẵn nơi chuyển hồ sơ của một trường hợp cũ;
    # thay bằng xã/phường người dân sẽ nộp hồ sơ.
    c.fill(Blank("Kính chuyển đến", f"UBND {_destination(f)}" if _destination(f) else "", overwrite=True))
    c.sign_date("Người làm đơn")


def fill_xn_khuyet_tat_khai_thay(c: FormCursor, f: Fields) -> None:
    _fill_xn_identity(c, f, "- Họ tên người xác nhận:", "- Địa chỉ cư trú:")
    c.fill(
        ("xác nhận cho", f["subject_relationship"]),
        ("tôi là", f["full_name"]),
        ("số định danh cá nhân:", f["citizen_id"]),
        ("sinh ngày", f["date_of_birth"]),
        ("thường trú tại:", f["residence_address"]),
    )
    c.fill(("hiện cư trú tại:", f["contact_address"]))
    c.fill(Blank("tại nơi ở hiện tại là", _destination(f), overwrite=True))
    c.seek("Thông tin người khai")
    c.fill(("Họ tên:", f["guardian_full_name"]))
    c.fill(("Căn cước công dân số:", f["guardian_citizen_id"]))
    c.fill(("Ngày cấp:", f["guardian_citizen_id_issue_date"]))
    c.fill(("Nơi cấp:", f["guardian_citizen_id_issue_place"]))
    c.fill(("Mối quan hệ với đối tượng:", f["guardian_relationship"]))
    c.sign_date("Người làm đơn")


def fill_xn_nq35(c: FormCursor, f: Fields) -> None:
    _fill_xn_identity(c, f, "- Tôi tên:", "- Hiện cư ngụ tại số:")
    c.fill(Blank("năm 2023 tại", _destination(f), overwrite=True))
    # Mẫu in sẵn "tại phường Bình Trị Đông" của một hồ sơ cũ.
    c.fill(Blank("xin hưởng chế độ tại", _destination(f), overwrite=True))
    c.sign_date("Người làm đơn")


def fill_xn_nghi_quyet(c: FormCursor, f: Fields) -> None:
    """Đơn xác nhận chưa nhận hỗ trợ theo Nghị quyết 40 hoặc 32 (cùng bố cục)."""
    _fill_xn_identity(c, f, "- Tôi tên:", "- Hiện cư ngụ tại số:")
    c.fill(("- Số điện thoại:", f["phone_number"]))
    c.sign_date("Người làm đơn")


FILLERS: dict[str, Callable[[FormCursor, Fields], None]] = {
    "tro_cap_huu_tri": fill_tro_cap_huu_tri,
    "ho_tro_mai_tang": fill_ho_tro_mai_tang,
    "ho_tro_hoa_tang": fill_ho_tro_hoa_tang,
    "ho_tro_nq40": fill_ho_tro_nghi_quyet,
    "ho_tro_nq32": fill_ho_tro_nghi_quyet,
    "xac_dinh_khuyet_tat": fill_xac_dinh_khuyet_tat,
    "to_khai_bhyt": fill_to_khai_bhyt,
    "xn_huu_tri_tu_khai": fill_xn_huu_tri_tu_khai,
    "xn_huu_tri_khai_thay": fill_xn_huu_tri_khai_thay,
    "xn_mung_tho_tu_khai": fill_xn_mung_tho_tu_khai,
    "xn_mung_tho_khai_thay": fill_xn_mung_tho_khai_thay,
    "xn_khuyet_tat_tu_khai": fill_xn_khuyet_tat_tu_khai,
    "xn_khuyet_tat_khai_thay": fill_xn_khuyet_tat_khai_thay,
    "xn_nq35": fill_xn_nq35,
    "xn_nq40": fill_xn_nghi_quyet,
    "xn_nq32": fill_xn_nghi_quyet,
}
