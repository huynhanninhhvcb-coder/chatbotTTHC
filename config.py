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
# Model dự phòng khi CHAT_MODEL lỗi trước khi kịp trả lời chữ nào - thường gặp
# nhất là vượt giới hạn token/phút của tài khoản (đo 10/2026: gpt-6-luna giới
# hạn 200.000 token/phút, mỗi câu hỏi có tra web tốn 10.000-20.000 token, nên
# chỉ cần ~1 câu hỏi mỗi 5 giây là chạm ngưỡng). Giới hạn tính riêng cho từng
# model nên chuyển sang model khác là trả lời được ngay. gpt-5.6-luna cũng rẻ
# ($0,20/$1,20 mỗi 1 triệu token) và hỗ trợ web_search.
FALLBACK_CHAT_MODEL = "gpt-5.6-luna"

# ==================== TÀI LIỆU PDF (nguồn tra cứu nội bộ) ====================
# Chép file PDF vào thư mục này rồi chạy `python sync_pdf.py` - chatbot sẽ tra
# trích đoạn trong đó trước (ưu tiên hơn tra cứu web).
PDF_DIR = os.path.join(BASE_DIR, 'thutuc_data')
# Kho tra cứu (thông tin văn bản + các đoạn đã chia + vector ngữ nghĩa), do
# sync_pdf.py ghi và tailieu.py đọc.
PDF_INDEX_FILE = os.path.join(BASE_DIR, 'pdf_index.json')
# Model tính vector ngữ nghĩa cho đoạn văn bản và câu hỏi. Rút gọn còn 1024
# chiều (OpenAI hỗ trợ sẵn) để kho nhỏ và so khớp nhanh mà gần như không giảm
# độ chính xác. Đổi model/số chiều thì phải chạy lại sync_pdf.py.
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 1024

if not GOOGLE_DRIVE_API_KEY:
    print("⚠️ Cảnh báo: Chưa cấu hình GOOGLE_DRIVE_API_KEY trong file .env")
if not OPENAI_API_KEY:
    print("⚠️ Cảnh báo: Chưa cấu hình OPENAI_API_KEY trong file .env — "
          "chatbot sẽ chỉ chạy ở chế độ trả lời cơ bản (rule-based), không có AI.")
