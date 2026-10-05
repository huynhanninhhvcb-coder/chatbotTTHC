"""
Tiện ích xử lý/chuẩn hóa văn bản tiếng Việt, dùng để nhận diện các câu hỏi
đặc biệt cố định (chào hỏi, BHYT, mẫu đơn, khu phố...) bằng từ khóa - KHÔNG
còn dùng để tìm kiếm/dò thủ tục nữa, vì chatbot giờ trả lời bằng kiến thức
chung của AI (xem ai_engine.py) thay vì tra cứu dữ liệu riêng.
"""
import re
import unicodedata


def remove_accents(text):
    """
    Bỏ dấu tiếng Việt. Chữ "đ/Đ" phải đổi thành "d/D" TRƯỚC khi chuẩn hóa NFD,
    vì đây là một ký tự Unicode riêng biệt (không phải "d" + dấu phụ) nên NFD
    không tách được - nếu không xử lý riêng, encode ascii sẽ xóa mất "đ".
    """
    text = text.replace('đ', 'd').replace('Đ', 'D')
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore').decode('utf-8')
    return text.lower()


def chuan_hoa_tim_kiem(text):
    """Chuẩn hóa văn bản tìm kiếm"""
    text = remove_accents(text)
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = ' '.join(text.split())
    return text


# Từ xưng hô/đệm lịch sự, không làm thay đổi ý của câu (đã bỏ dấu).
TU_DEM = [
    'ban', 'a', 'nhe', 'nha', 'nhen', 'oi', 'xin', 'vang', 'da', 'ok', 'oke', 'okay',
    'em', 'anh', 'chi', 'co', 'chu', 'bac', 'ong', 'ba', 'minh', 'toi', 'tro ly',
    'cho', 'hoi', 'voi', 'nhieu', 'rat', 'lam', 'qua',
]


def chi_co_y(cau_hoi_chuan, tu_khoa, tu_phu=()):
    """
    True nếu câu hỏi CHỈ mang đúng một ý cố định: có ít nhất một từ khóa, và
    mọi từ còn lại đều là từ phụ của ý đó hoặc từ đệm lịch sự. Chỉ "có chứa"
    từ khóa là chưa đủ - VD "Hộ thoát nghèo..." không phải lời tạm biệt,
    "Chào bạn, cho hỏi thủ tục khai sinh" không chỉ là lời chào, "cấp lại thẻ
    BHYT" không phải tra cứu hạn thẻ BHYT: các câu đó phải chuyển cho AI.
    So khớp theo từ nguyên vẹn, ưu tiên cụm dài nhất tại mỗi vị trí.
    """
    tu_khoa = set(tu_khoa)
    cum_tu = sorted(tu_khoa | set(tu_phu) | set(TU_DEM), key=lambda c: -len(c.split()))
    words = cau_hoi_chuan.split()
    i = 0
    found = False
    while i < len(words):
        for cum in cum_tu:
            n = len(cum.split())
            if words[i:i + n] == cum.split():
                found = found or cum in tu_khoa
                i += n
                break
        else:
            return False  # có từ nằm ngoài ý cố định -> câu hỏi khác, để AI trả lời
    return found
