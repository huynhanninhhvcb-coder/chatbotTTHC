"""
Cấu hình chung của ứng dụng. Đọc các giá trị nhạy cảm (API key...) từ file
.env thay vì hardcode trong code, để không bị lộ khi chia sẻ/commit mã nguồn.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Địa chỉ tiếp nhận hồ sơ - dùng trong system prompt của AI (để luôn hướng
# dẫn đúng nơi liên hệ) và trong câu trả lời dự phòng khi OpenAI không khả dụng.
WARD_OFFICE_NAME = "Trung tâm phục vụ hành chính công phường Minh Phụng"
WARD_OFFICE_ADDRESS = f"{WARD_OFFICE_NAME}, 183A Lý Nam Đế"

# ==================== GOOGLE DRIVE (mẫu đơn) ====================
GOOGLE_DRIVE_API_KEY = os.environ.get('GOOGLE_DRIVE_API_KEY', '')
MAU_DON_FOLDER_ID = "1p2B1TfURTU7iTD_iY9mMk5TWGOoNZ7Xf"
GOOGLE_DRIVE_API_URL = "https://www.googleapis.com/drive/v3/files"

# ==================== OPENAI (chatbot AI trả lời bằng kiến thức chung) ====================
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
# "gpt-6-luna": model rẻ nhất dòng GPT-6 của OpenAI có hỗ trợ công cụ web_search
# (đã xác minh trực tiếp từ tài liệu OpenAI 10/2026, không dùng model cũ "gpt-4o-mini"
# vì dòng model đã thay đổi và gpt-4o-mini không hỗ trợ web_search qua Responses API).
# Giá: ~$0,10/$0,50 mỗi 1 triệu token input/output, cộng $10/1.000 lượt tra cứu web.
CHAT_MODEL = "gpt-6-luna"
# Model dự phòng khi CHAT_MODEL lỗi trước khi kịp trả lời chữ nào - thường gặp
# nhất là vượt giới hạn token/phút của tài khoản (đo 10/2026: gpt-6-luna giới
# hạn 200.000 token/phút, mỗi câu hỏi có tra web tốn 10.000-20.000 token, nên
# chỉ cần ~1 câu hỏi mỗi 5 giây là chạm ngưỡng). Giới hạn tính riêng cho từng
# model nên chuyển sang model khác là trả lời được ngay. gpt-5.6-luna cũng rẻ
# ($0,20/$1,20 mỗi 1 triệu token) và hỗ trợ web_search.
FALLBACK_CHAT_MODEL = "gpt-5.6-luna"

# ==================== TRỢ LÝ ĐIỀN ĐƠN ====================
BASE_DIR = Path(__file__).resolve().parent


def _bounded_env_int(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _find_tesseract():
    configured = os.environ.get('TESSERACT_CMD')
    if configured:
        return configured
    candidates = (
        Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe'),
        Path(r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe'),
        Path('/usr/bin/tesseract'),
        Path('/usr/local/bin/tesseract'),
    )
    return next((str(path) for path in candidates if path.is_file()), None)


class Config:
    """Cấu hình cho ứng dụng con quét và điền biểu mẫu."""

    MAX_UPLOAD_FILES = 5
    MAX_FILE_SIZE = 12 * 1024 * 1024
    MAX_BATCH_TOTAL_SIZE = 60 * 1024 * 1024
    MAX_CONTENT_LENGTH = 65 * 1024 * 1024
    UPLOAD_FOLDER = BASE_DIR / 'uploads'
    OUTPUT_FOLDER = BASE_DIR / 'outputs'
    TEMPLATE_FOLDER = BASE_DIR / 'templates_word'
    ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
    MAX_PDF_PAGES = 8
    OCR_MAX_WORKERS = _bounded_env_int('OCR_MAX_WORKERS', 5, 1, 8)
    OCR_TARGET_WIDTH = _bounded_env_int('OCR_TARGET_WIDTH', 1600, 900, 2200)
    OCR_LANGUAGE = os.environ.get('OCR_LANGUAGE', '').strip() or None
    OCR_FAST_MODE = os.environ.get('OCR_FAST_MODE', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    INTERVIEW_TTL_SECONDS = 30 * 60
    TESSERACT_CMD = _find_tesseract()
    OPENAI_API_KEY = OPENAI_API_KEY
    OPENAI_MODEL = os.environ.get('FORM_VISION_MODEL', 'gpt-4o-mini')
    AI_MAX_WORKERS = _bounded_env_int('AI_MAX_WORKERS', 3, 1, 5)


if not GOOGLE_DRIVE_API_KEY:
    print("⚠️ Cảnh báo: Chưa cấu hình GOOGLE_DRIVE_API_KEY trong file .env")
if not OPENAI_API_KEY:
    print("⚠️ Cảnh báo: Chưa cấu hình OPENAI_API_KEY trong file .env — chatbot chỉ trả lời "
          "các câu cố định (chào hỏi, BHYT, mẫu đơn, khu phố); câu khác nhận lời xin lỗi.")
