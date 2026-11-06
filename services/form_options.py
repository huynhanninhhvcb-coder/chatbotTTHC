"""Các lựa chọn cố định in trên mẫu đơn (ô đánh dấu), dùng chung cho kiểm tra dữ liệu,
phỏng vấn và bước điền Word để ba nơi luôn khớp nhau."""

from __future__ import annotations

PAYMENT_BANK = "Tài khoản ngân hàng"
PAYMENT_CASH = "Tiền mặt"
PAYMENT_EWALLET = "Ví điện tử"
PAYMENT_METHODS = (PAYMENT_BANK, PAYMENT_CASH, PAYMENT_EWALLET)

RECIPIENT_SELF = "Người hưởng trợ cấp"
RECIPIENT_GUARDIAN = "Người giám hộ"
BENEFIT_RECIPIENTS = (RECIPIENT_SELF, RECIPIENT_GUARDIAN)

# Nghị quyết 40/32: (giá trị lưu, chữ in trên mẫu cạnh ô đánh dấu).
SUPPORT_CATEGORIES = (
    ("Phụ nữ sinh đủ hai con trước 35 tuổi", "Phụ nữ sinh đủ hai con trước 35 tuổi"),
    ("Hộ nghèo", "Hộ nghèo (theo tiêu chí của Thành phố)"),
    ("Hộ cận nghèo", "Hộ cận nghèo (theo tiêu chí của Thành phố)"),
    ("Đối tượng bảo trợ xã hội", "Đối tượng bảo trợ xã hội, cụ thể:"),
    ("Đối tượng sống tại xã đảo", "Đối tượng sống tại xã đảo:"),
)

# Mẫu số 03 (mai táng), mục "Thuộc đối tượng".
BURIAL_SUPPORT_CATEGORIES = (
    ("Đang hưởng trợ cấp xã hội, trợ cấp hưu trí xã hội", "Đang hưởng trợ cấp xã hội, trợ cấp hưu trí xã hội hoặc đối tượng"),
    ("Con của người đơn thân nghèo đang hưởng trợ cấp hằng tháng", "Con của người đơn thân nghèo"),
    ("Người từ đủ 75 tuổi đang hưởng trợ cấp tuất BHXH hoặc trợ cấp hằng tháng khác", "Người từ đủ 75 tuổi đang hưởng trợ cấp tuất"),
    ("Người từ đủ 70 đến dưới 75 tuổi thuộc hộ nghèo, cận nghèo đang hưởng trợ cấp tuất BHXH", "Người từ đủ 70 đến dưới 75 tuổi"),
    ("Người chết trong trường hợp khẩn cấp (thiên tai, hỏa hoạn, tai nạn giao thông...)", "Người chết trong trường hợp khẩn cấp"),
)

# Tờ khai hỏa táng, mục 8: (giá trị lưu, chữ in trên mẫu). Thứ tự giống bản in.
CREMATION_CATEGORIES = (
    ("Bà mẹ Việt Nam anh hùng", "Bà mẹ Việt Nam anh hùng"),
    ("Anh hùng lực lượng vũ trang nhân dân, anh hùng lao động", "Anh hùng lực lượng vũ trang nhân dân, anh hùng lao động"),
    ("Đảng viên có Huy hiệu 40 tuổi Đảng trở lên", "Đảng viên có Huy hiệu 40 tuổi Đảng trở lên"),
    ("Người hoạt động cách mạng trước ngày 01/01/1945", "Người hoạt động cách mạng trước ngày 01/01/1945"),
    ("Người hoạt động cách mạng từ 01/01/1945 đến trước 19/8/1945", "Người hoạt động cách mạng từ ngày 01/01/1945"),
    ("Thương binh từ 81% trở lên", "Thương binh, người hưởng chính sách như thương binh"),
    ("Bệnh binh từ 81% trở lên", "Bệnh binh có tỷ lệ suy giảm khả năng lao động"),
    ("Người hoạt động kháng chiến bị nhiễm chất độc hóa học từ 81% trở lên", "Người hoạt động kháng chiến bị nhiễm chất độc hóa học"),
    ("Thân nhân liệt sĩ, người có công đang hưởng trợ cấp nuôi dưỡng", "Thân nhân liệt sĩ và người có công giúp đỡ cách mạng"),
    ("Đối tượng chính sách tại Trung tâm dưỡng lão Thị Nghè", "Các đối tượng chính sách đang được nuôi dưỡng"),
    ("Hộ nghèo", "Hộ nghèo (theo tiêu chí của Thành phố)"),
    ("Người khuyết tật", "Người khuyết tật (theo Nghị định số 28/2012/NĐ-CP)"),
    ("Người cao tuổi", "Người cao tuổi (theo Nghị định số 20/2021/NĐ-CP)"),
    ("Đối tượng bảo trợ xã hội khác", "Đối tượng bảo trợ xã hội khác"),
    ("Đối tượng hưu trí", "Đối tượng hưu trí"),
    ("Hộ cận nghèo", "Hộ cận nghèo (theo tiêu chí của Thành phố)"),
    ("Người dân có hộ khẩu tại TP.HCM", "Người dân có hộ khẩu tại Thành phố Hồ Chí Minh"),
    ("Trẻ từ 6 tuổi trở xuống có hộ khẩu tại TP.HCM", "Có hộ khẩu tại Thành phố Hồ Chí Minh"),
    ("Trẻ từ 6 tuổi trở xuống có tạm trú KT3 tại TP.HCM", "Có tạm trú (KT3) tại Thành phố Hồ Chí Minh"),
)

DISABILITY_PROCEDURES = (
    "Xác định mức độ khuyết tật và cấp Giấy xác nhận khuyết tật",
    "Xác định lại mức độ khuyết tật và cấp Giấy xác nhận khuyết tật",
    "Cấp lại Giấy xác nhận khuyết tật",
    "Cấp đổi Giấy xác nhận khuyết tật",
)

# Trường chỉ nhận một trong các giá trị cố định -> danh sách giá trị hợp lệ.
FIELD_OPTIONS: dict[str, tuple[str, ...]] = {
    "payment_method": PAYMENT_METHODS,
    "benefit_recipient": BENEFIT_RECIPIENTS,
    "support_category": tuple(value for value, _ in SUPPORT_CATEGORIES),
    "burial_support_category": tuple(value for value, _ in BURIAL_SUPPORT_CATEGORIES),
    "cremation_support_category": tuple(value for value, _ in CREMATION_CATEGORIES),
    "disability_procedure_type": DISABILITY_PROCEDURES,
    "gender": ("Nam", "Nữ", "Khác"),
    "deceased_gender": ("Nam", "Nữ"),
}
