from flask import Flask, Response, render_template, request
import requests

from config import (
    SECRET_KEY, GOOGLE_DRIVE_API_KEY, MAU_DON_FOLDER_ID, GOOGLE_DRIVE_API_URL,
    WARD_OFFICE_ADDRESS,
)
from search import chuan_hoa_tim_kiem, chi_co_y
import ai_engine

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Số lượt hỏi-đáp gần nhất dùng làm ngữ cảnh cho AI, giúp hiểu được câu hỏi
# nối tiếp (VD: "vậy còn phí thì sao") mà không cần người dùng nhắc lại tên
# thủ tục. Lịch sử do TRÌNH DUYỆT giữ và gửi kèm mỗi câu hỏi (không lưu trong
# session/cookie được nữa vì câu trả lời được stream: cookie đã gửi đi trước
# khi có câu trả lời). Server chỉ nhận đúng định dạng và cắt bớt độ dài.
# Giữ 4 lượt hỏi-đáp, mỗi tin tối đa 2.000 ký tự. Đo 10/2026 (.checks/danh_gia.py):
# câu trả lời dài 200-850 ký tự - cắt ở 600 ký tự như trước làm mất phần cuối
# (thường là mục "Căn cứ pháp lý") của ~1/3 câu trả lời, AI không biết mình vừa
# nói gì khi người dùng hỏi tiếp "văn bản đó ban hành năm nào".
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_CHARS = 2000


def _clean_history(raw):
    if not isinstance(raw, list):
        return []
    history = [{'role': m['role'], 'content': m['content'][:MAX_HISTORY_CHARS]}
               for m in raw
               if isinstance(m, dict) and m.get('role') in ('user', 'assistant')
               and isinstance(m.get('content'), str)]
    return history[-MAX_HISTORY_MESSAGES:]


# ===== Ý CỐ ĐỊNH (trả lời bằng câu soạn sẵn, không gọi AI) =====
# Mỗi ý gồm (từ khóa, từ phụ được phép đi kèm), đã bỏ dấu. Chỉ trả lời soạn sẵn
# khi câu hỏi KHÔNG có từ nào khác ngoài các từ này (xem search.chi_co_y); câu
# hỏi có thêm nội dung khác (VD "cấp lại thẻ BHYT bị mất") được chuyển cho AI.
Y_CHAO = (['xin chao', 'chao', 'hi', 'hello', 'alo'],
          ['buoi sang', 'buoi trua', 'buoi chieu', 'buoi toi', 'moi nguoi'])
Y_CAM_ON = (['cam on', 'thank', 'thanks', 'thank you'],
            ['da', 'ho tro', 'giup', 'giup do', 'huong dan', 'tu van', 'thong tin', 'vi'])
Y_TAM_BIET = (['tam biet', 'chao tam biet', 'bye', 'bye bye', 'goodbye', 'hen gap lai'],
              ['chao'])
Y_BHYT = (['bhyt', 'bao hiem y te'],
          ['tra cuu', 'kiem tra', 'xem', 'the', 'han', 'thoi han', 'han su dung', 'su dung',
           'con han', 'het han chua', 'gia tri', 'ma so', 'ma', 'so', 'online', 'truc tuyen',
           'cach', 'o dau', 'nhu the nao', 'the nao', 'khong', 'chua', 'con', 'cua',
           'nguoi than', 'gia dinh', 'trang', 'web', 'link', 'duong dan'])
Y_MAU_DON = (['mau don', 'bieu mau', 'mau giay', 'mau khai', 'to khai', 'kho mau'],
             ['tai', 'tai ve', 'download', 'lay', 'o dau', 'cac', 'nhung', 'danh sach', 'kho',
              'xem', 'can', 'tat ca', 'file', 'ban', 'hanh chinh', 'mau', 'cua', 'phuong',
              'minh phung', 'khong', 'tim', 'in'])
Y_KHU_PHO = (['khu pho'],
             ['thong tin', 'tra cuu', 'danh sach', 'cac', 'xem', 'ban do', 'so do', 'phuong',
              'minh phung', 'cua', 'trong', 'o dau', 'gom', 'nhung', 'bao nhieu', 'tim',
              'tat ca', 'dia gioi', 'ranh gioi'])


def _reply(text, context=None):
    """
    Trả câu trả lời dạng văn bản thuần. Header X-Context báo trình duyệt xử lý
    lịch sử hội thoại: "reset" = xóa ngữ cảnh, "append" = lưu lượt hỏi-đáp này.
    """
    resp = Response(text, mimetype='text/plain')
    if context:
        resp.headers['X-Context'] = context
    return resp


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json(silent=True) or {}
    cau_hoi = str(data.get('message', '')).strip()
    if not cau_hoi:
        return _reply('Xin lỗi, tôi không nghe rõ. Bạn vui lòng nói lại nhé! 🎤')
    cau_hoi_chuan = chuan_hoa_tim_kiem(cau_hoi)

    # ===== TỪ KHÓA ĐẶC BIỆT =====
    # Các nhánh này xử lý trực tiếp bằng quy tắc (không gọi AI) để đảm bảo
    # luôn đúng 100% và không tốn chi phí cho những câu hỏi đơn giản, cố định.
    # Chỉ áp dụng khi câu hỏi CHỈ có đúng ý đó (xem các hằng Y_... ở trên).
    if chi_co_y(cau_hoi_chuan, *Y_CHAO):
        return _reply('Xin chào! Tôi có thể giúp gì cho bạn hôm nay? Hãy hỏi tôi về các thủ tục hành chính công nhé! 🎤', 'reset')
    if chi_co_y(cau_hoi_chuan, *Y_CAM_ON):
        return _reply('Dạ không có gì ạ! Rất vui được hỗ trợ bạn.')
    if chi_co_y(cau_hoi_chuan, *Y_TAM_BIET):
        return _reply('Tạm biệt! Chúc bạn một ngày tốt lành! 👋', 'reset')

    # BHYT
    if chi_co_y(cau_hoi_chuan, *Y_BHYT):
        reply = """🏥 **HƯỚNG DẪN TRA CỨU BHYT**

Bạn vui lòng truy cập trực tiếp vào trang web của Bảo hiểm xã hội Việt Nam theo đường dẫn dưới đây:

🔗 **Link tra cứu:** https://baohiemxahoi.gov.vn/tracuu/Pages/tra-cuu-thoi-han-su-dung-the-bhyt.aspx

📝 **Cách thực hiện:**
1. Nhập mã số thẻ BHYT vào ô tìm kiếm
2. Nhập mã xác nhận
3. Bấm "Tra cứu" để xem kết quả

💡 *Lưu ý: Trang web này do Bảo hiểm xã hội Việt Nam quản lý.*"""
        return _reply(reply)

    # Mẫu đơn (giữ từ khóa cụ thể để không nhầm với câu hỏi "cần giấy tờ gì" của một thủ tục)
    if chi_co_y(cau_hoi_chuan, *Y_MAU_DON):
        reply = """📄 **KHO MẪU ĐƠN, TỜ KHAI**

Tôi đã chuẩn bị sẵn một kho lưu trữ các mẫu đơn, tờ khai hành chính cho bạn. Hãy bấm nút "Mẫu đơn, tờ khai" trên thanh công cụ để xem danh sách chi tiết.

🔗 **Hoặc truy cập trực tiếp:** https://drive.google.com/drive/folders/1p2B1TfURTU7iTD_iY9mMk5TWGOoNZ7Xf

💡 *Lưu ý: Bạn cần đăng nhập Google để xem và tải file.*"""
        return _reply(reply)

    # Khu phố
    if chi_co_y(cau_hoi_chuan, *Y_KHU_PHO):
        reply = """🗺️ **THÔNG TIN KHU PHỐ**

Bạn có thể tra cứu thông tin chi tiết về các khu phố tại địa chỉ:

🔗 **Đường dẫn tra cứu:**
https://sites.google.com/view/phuongminhphung/trang-ch%E1%BB%A7

📝 **Cách thực hiện:**
1. Truy cập đường dẫn trên
2. Tìm kiếm thông tin về khu phố bạn quan tâm
3. Xem chi tiết thông tin hành chính

💡 *Trang thông tin này cung cấp dữ liệu chính thống về các khu phố của phường Minh Phụng.*"""
        return _reply(reply)

    # ===== CHATBOT AI: trả lời bằng kiến thức chung (không còn dữ liệu riêng) =====
    # Chờ dòng ĐẦU TIÊN rồi mới trả response: lúc đó mới biết AI có hoạt động
    # không, để chọn giữa stream câu trả lời hay câu trả lời dự phòng. Các dòng
    # sau được stream tới trình duyệt ngay khi AI viết xong từng dòng.
    answer = ai_engine.stream_answer(cau_hoi, _clean_history(data.get('history')))
    first = next(answer, None)
    if first is not None:
        def body():
            yield first
            yield from answer
        resp = Response(body(), mimetype='text/plain')
        resp.headers['X-Context'] = 'append'
        # Báo proxy (Render/Nginx) không gom cả câu trả lời rồi mới gửi.
        resp.headers['X-Accel-Buffering'] = 'no'
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    # ===== DỰ PHÒNG: chỉ chạy khi OpenAI không khả dụng (mất mạng, hết
    # quota, chưa cấu hình key...) để chatbot vẫn phản hồi được =====
    return _reply(f'❌ Xin lỗi, hệ thống AI đang tạm thời không khả dụng nên tôi chưa thể trả lời câu '
                  f'hỏi này.\n\n💡 Bạn vui lòng thử lại sau ít phút, hoặc liên hệ trực tiếp tại '
                  f'{WARD_OFFICE_ADDRESS} để được hỗ trợ ngay.')


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
        resp = requests.get(GOOGLE_DRIVE_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        folders = data.get('files', [])
    except Exception as e:
        # Chỉ ghi chi tiết vào log: thông báo lỗi của requests có cả URL kèm API key.
        print(f"⚠️ Không thể tải danh sách mẫu đơn: {e}")
        error = True
    return render_template('mau_don.html', folders=folders, error=error)
if __name__ == '__main__':
    app.run(debug=True)
