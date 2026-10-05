from flask import Flask, render_template, request, jsonify, session
import requests

from config import (
    SECRET_KEY, GOOGLE_DRIVE_API_KEY, MAU_DON_FOLDER_ID, GOOGLE_DRIVE_API_URL,
    WARD_OFFICE_ADDRESS,
)
from search import chuan_hoa_tim_kiem, co_tu_khoa
import ai_engine

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Số lượt hỏi-đáp gần nhất giữ lại trong session làm ngữ cảnh cho AI, giúp
# hiểu được câu hỏi nối tiếp (VD: "vậy còn phí thì sao") mà không cần người
# dùng nhắc lại tên thủ tục. Giới hạn độ dài để session (cookie) không phình to.
MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CHARS = 600


def _push_history(cau_hoi, reply):
    history = session.get('history', [])
    history.append({'role': 'user', 'content': cau_hoi[:MAX_HISTORY_CHARS]})
    history.append({'role': 'assistant', 'content': reply[:MAX_HISTORY_CHARS]})
    del history[:-MAX_HISTORY_MESSAGES]
    session['history'] = history


def _reset_context():
    session.pop('history', None)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json(silent=True) or {}
    cau_hoi = str(data.get('message', '')).strip()
    if not cau_hoi:
        return jsonify({'reply': 'Xin lỗi, tôi không nghe rõ. Bạn vui lòng nói lại nhé! 🎤'})
    cau_hoi_chuan = chuan_hoa_tim_kiem(cau_hoi)

    # ===== TỪ KHÓA ĐẶC BIỆT =====
    # Các nhánh này xử lý trực tiếp bằng quy tắc (không gọi AI) để đảm bảo
    # luôn đúng 100% và không tốn chi phí cho những câu hỏi đơn giản, cố định.
    if co_tu_khoa(cau_hoi_chuan, ['chao', 'hi', 'hello']):
        _reset_context()
        return jsonify({'reply': 'Xin chào! Tôi có thể giúp gì cho bạn hôm nay? Hãy hỏi tôi về các thủ tục hành chính công nhé! 🎤'})
    if co_tu_khoa(cau_hoi_chuan, ['cam on', 'thank']):
        return jsonify({'reply': 'Dạ không có gì ạ! Rất vui được hỗ trợ bạn.'})
    if co_tu_khoa(cau_hoi_chuan, ['tam biet', 'bye', 'thoat']):
        _reset_context()
        return jsonify({'reply': 'Tạm biệt! Chúc bạn một ngày tốt lành! 👋'})

    # BHYT
    if co_tu_khoa(cau_hoi_chuan, ['bhyt', 'bao hiem y te', 'tra cuu bhyt']):
        reply = """🏥 **HƯỚNG DẪN TRA CỨU BHYT**

Bạn vui lòng truy cập trực tiếp vào trang web của Bảo hiểm xã hội Việt Nam theo đường dẫn dưới đây:

🔗 **Link tra cứu:** https://baohiemxahoi.gov.vn/tracuu/Pages/tra-cuu-thoi-han-su-dung-the-bhyt.aspx

📝 **Cách thực hiện:**
1. Nhập mã số thẻ BHYT vào ô tìm kiếm
2. Nhập mã xác nhận
3. Bấm "Tra cứu" để xem kết quả

💡 *Lưu ý: Trang web này do Bảo hiểm xã hội Việt Nam quản lý.*"""
        return jsonify({'reply': reply})

    # Mẫu đơn (giữ từ khóa cụ thể để không nhầm với câu hỏi "cần giấy tờ gì" của một thủ tục)
    if co_tu_khoa(cau_hoi_chuan, ['mau don', 'bieu mau', 'mau giay', 'mau khai', 'to khai', 'kho mau']):
        reply = """📄 **KHO MẪU ĐƠN, TỜ KHAI**

Tôi đã chuẩn bị sẵn một kho lưu trữ các mẫu đơn, tờ khai hành chính cho bạn. Hãy bấm nút "Mẫu đơn, tờ khai" trên thanh công cụ để xem danh sách chi tiết.

🔗 **Hoặc truy cập trực tiếp:** https://drive.google.com/drive/folders/1p2B1TfURTU7iTD_iY9mMk5TWGOoNZ7Xf

💡 *Lưu ý: Bạn cần đăng nhập Google để xem và tải file.*"""
        return jsonify({'reply': reply})

    # Khu phố
    if co_tu_khoa(cau_hoi_chuan, ['khu pho', 'thong tin khu pho', 'tra cuu khu pho']):
        reply = """🗺️ **THÔNG TIN KHU PHỐ**

Bạn có thể tra cứu thông tin chi tiết về các khu phố tại địa chỉ:

🔗 **Đường dẫn tra cứu:**
https://sites.google.com/view/phuongminhphung/trang-ch%E1%BB%A7

📝 **Cách thực hiện:**
1. Truy cập đường dẫn trên
2. Tìm kiếm thông tin về khu phố bạn quan tâm
3. Xem chi tiết thông tin hành chính

💡 *Trang thông tin này cung cấp dữ liệu chính thống về các khu phố của phường Minh Phụng.*"""
        return jsonify({'reply': reply})

    # ===== CHATBOT AI: trả lời bằng kiến thức chung (không còn dữ liệu riêng) =====
    history = session.get('history', [])
    ai_reply = ai_engine.generate_answer(cau_hoi, history)
    if ai_reply:
        _push_history(cau_hoi, ai_reply)
        return jsonify({'reply': ai_reply})

    # ===== DỰ PHÒNG: chỉ chạy khi OpenAI không khả dụng (mất mạng, hết
    # quota, chưa cấu hình key...) để chatbot vẫn phản hồi được =====
    reply = (f'❌ Xin lỗi, hệ thống AI đang tạm thời không khả dụng nên tôi chưa thể trả lời câu '
             f'hỏi này.\n\n💡 Bạn vui lòng thử lại sau ít phút, hoặc liên hệ trực tiếp tại '
             f'{WARD_OFFICE_ADDRESS} để được hỗ trợ ngay.')
    return jsonify({'reply': reply})


@app.route('/clear-context', methods=['POST'])
def clear_context():
    """Xóa lịch sử hội thoại khi người dùng bấm 'Xóa hội thoại'"""
    _reset_context()
    return jsonify({'ok': True})


@app.route('/mau-don')
def mau_don():
    """Hiển thị danh sách các folder con trong thư mục Google Drive (dạng nút bấm)"""
    folders = []
    error = None
    try:
        params = {
            'q': f"'{MAU_DON_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            'key': GOOGLE_DRIVE_API_KEY,
            'fields': 'files(id, name)'
        }
        resp = requests.get(GOOGLE_DRIVE_API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        folders = data.get('files', [])
    except Exception as e:
        error = f"Không thể tải danh sách mẫu đơn: {str(e)}"
    return render_template('mau_don.html', folders=folders, error=error)
if __name__ == '__main__':
    app.run(debug=True)
