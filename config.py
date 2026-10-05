"""
Cấu hình chung của ứng dụng. Đọc các giá trị nhạy cảm (API key...) từ file
.env thay vì hardcode trong code, để không bị lộ khi chia sẻ/commit mã nguồn.
"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Địa chỉ tiếp nhận hồ sơ - dùng trong system prompt của AI (để luôn hướng
# dẫn đúng nơi liên hệ) và trong câu trả lời dự phòng khi OpenAI không khả dụng.
WARD_OFFICE_NAME = "Trung tâm phục vụ hành chính công phường Minh Phụng"
WARD_OFFICE_ADDRESS = f"{WARD_OFFICE_NAME}, 183A Lý Nam Đế"

# Khóa dùng để mã hóa session (lưu ngữ cảnh hội thoại).
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-doi-khi-trien-khai')

# ==================== GOOGLE DRIVE (mẫu đơn) ====================
GOOGLE_DRIVE_API_KEY = os.environ.get('GOOGLE_DRIVE_API_KEY', '')
MAU_DON_FOLDER_ID = "1p2B1TfURTU7iTD_iY9mMk5TWGOoNZ7Xf"
GOOGLE_DRIVE_API_URL = "https://www.googleapis.com/drive/v3/files"

# ==================== OPENAI (chatbot AI trả lời bằng kiến thức chung) ====================
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
# "gpt-6-luna": model rẻ nhất dòng GPT-6 của OpenAI có hỗ trợ công cụ web_search
# (đã xác minh trực tiếp từ tài liệu OpenAI 10/2026, không dùng model cũ "gpt-4o-mini"
# vì dòng model đã thay đổi và gpt-4o-mini không hỗ trợ web_search qua Responses API).
CHAT_MODEL = "gpt-6-luna"

# ==================== TÀI LIỆU PDF (nguồn tra cứu nội bộ) ====================
# Chép file PDF vào thư mục này rồi chạy `python sync_pdf.py` để đẩy lên
# vector store của OpenAI - chatbot sẽ tra cứu trong đó bằng công cụ
# file_search (ưu tiên hơn tra cứu web).
PDF_DIR = os.path.join(BASE_DIR, 'thutuc_data')
# File lưu trạng thái đồng bộ (ID vector store + ID/mã băm từng file đã đẩy
# lên), do sync_pdf.py ghi và ai_engine.py đọc. Không sửa tay.
PDF_INDEX_FILE = os.path.join(BASE_DIR, 'pdf_index.json')

if not GOOGLE_DRIVE_API_KEY:
    print("⚠️ Cảnh báo: Chưa cấu hình GOOGLE_DRIVE_API_KEY trong file .env")
if not OPENAI_API_KEY:
    print("⚠️ Cảnh báo: Chưa cấu hình OPENAI_API_KEY trong file .env — "
          "chatbot sẽ chỉ chạy ở chế độ trả lời cơ bản (rule-based), không có AI.")
