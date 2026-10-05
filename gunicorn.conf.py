"""
Cấu hình gunicorn khi chạy trên server (Render). Gunicorn tự đọc file này
khi khởi động từ thư mục gốc dự án; tham số ghi trực tiếp trong Start Command
(--workers, --timeout) sẽ được ưu tiên hơn giá trị ở đây.

Mỗi câu hỏi AI chủ yếu là CHỜ OpenAI trả lời (~6s), gần như không tốn CPU, nên
dùng nhiều luồng (threads) để nhiều người hỏi cùng lúc không phải xếp hàng chờ
nhau. Chạy 1 worker (tiến trình) để mọi người dùng chung MỘT bộ nhớ đệm câu
trả lời trong RAM (xem ai_engine.py) - nhiều worker vẫn chạy đúng, chỉ là mỗi
worker giữ bộ đệm riêng nên câu hỏi lặp lại ít được trả lời ngay hơn.
"""
workers = 1
threads = 16
timeout = 120
