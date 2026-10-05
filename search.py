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


def co_tu_khoa(cau_hoi_chuan, tu_khoa_list):
    """
    Kiểm tra câu hỏi có chứa một trong các từ khóa hay không.
    Từ khóa 1 từ được so khớp theo TỪ NGUYÊN VẸN (tránh việc từ ngắn như
    "hi" khớp nhầm vào bên trong từ khác, ví dụ "phí"/"chi" sau khi bỏ dấu
    đều chứa chuỗi con "hi"). Cụm nhiều từ vẫn so khớp theo chuỗi con.
    """
    words = cau_hoi_chuan.split()
    for tu in tu_khoa_list:
        if ' ' in tu:
            if tu in cau_hoi_chuan:
                return True
        elif tu in words:
            return True
    return False
