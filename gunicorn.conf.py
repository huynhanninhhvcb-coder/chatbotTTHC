"""
Cấu hình gunicorn khi chạy trên server (Render). Gunicorn tự đọc file này
khi khởi động từ thư mục gốc dự án; tham số ghi trực tiếp trong Start Command
(--workers, --timeout) sẽ được ưu tiên hơn giá trị ở đây.

Mỗi câu hỏi AI chủ yếu là CHỜ OpenAI trả lời (~8s), gần như không tốn CPU, nên
dùng nhiều luồng (threads) để nhiều người hỏi cùng lúc không phải xếp hàng chờ
nhau - với 2 worker x 8 luồng, server phục vụ được 16 câu hỏi đồng thời.
"""
workers = 2
threads = 8
timeout = 120
