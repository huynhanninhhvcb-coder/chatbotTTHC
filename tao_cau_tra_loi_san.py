"""
Soạn sẵn câu trả lời cho các câu hỏi thường gặp, lưu vào cau_tra_loi_san.json
để chatbot trả lời ngay (<1s) thay vì gọi AI + tra web (~5-10s).

Server trên Render bị xóa ổ đĩa mỗi lần deploy/khởi động lại, nên câu trả lời
không thể lưu lúc chạy mà phải soạn trước rồi commit cùng mã nguồn. Mỗi câu
được hỏi qua đúng đường trả lời thật của chatbot (ai_engine.stream_answer: AI +
tra cứu các trang chính thống), câu nào lỗi thì bỏ qua - chatbot sẽ tự tra
trực tiếp khi có người hỏi câu đó.

Cách dùng (nên chạy lại ít nhất mỗi tháng - quá ai_engine.CAU_TRA_LOI_SAN_HAN_DUNG
ngày thì chatbot không dùng câu trả lời soạn sẵn nữa):
    python tao_cau_tra_loi_san.py
rồi commit + push file cau_tra_loi_san.json.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import ai_engine

# Câu hỏi thường gặp ở cấp phường. NUT_CAU_HOI_MAU câu đầu hiện thành nút bấm
# trên màn hình chào; các câu còn lại được trả lời ngay khi người dân gõ đúng
# câu đó (bỏ qua chữ hoa/thường, khoảng trắng và dấu câu cuối câu).
CAU_HOI = [
    "Thủ tục đăng ký khai sinh cần những gì?",
    "Thủ tục đăng ký kết hôn cần những gì?",
    "Đăng ký tạm trú cần giấy tờ gì?",
    "Đăng ký thường trú cần giấy tờ gì?",
    "Chứng thực bản sao cần mang gì?",
    "Làm lại thẻ căn cước bị mất thế nào?",
    "Xin xác nhận tình trạng hôn nhân thế nào?",
    "Thủ tục đăng ký khai tử cần những gì?",
    "Đăng ký khai sinh quá hạn cần gì?",
    "Làm căn cước cho trẻ em thế nào?",
    "Đăng ký tạm vắng cần gì?",
    "Xác nhận thông tin cư trú thế nào?",
    "Cấp bản sao trích lục khai sinh ở đâu?",
    "Thay đổi, cải chính hộ tịch cần gì?",
    "Đăng ký nhận cha, mẹ, con cần gì?",
    "Đăng ký giám hộ cần gì?",
    "Chứng thực chữ ký cần gì?",
    "Chứng thực hợp đồng, giao dịch cần gì?",
    "Cấp phiếu lý lịch tư pháp thế nào?",
    "Đăng ký hộ kinh doanh cần gì?",
    "Cấp lại bằng tốt nghiệp THPT bị mất thế nào?",
    "Đổi giấy phép lái xe ở đâu?",
    "Trợ cấp xã hội cho người cao tuổi cần gì?",
    "Cấp lại thẻ BHYT bị mất thế nào?",
]
NUT_CAU_HOI_MAU = 3  # 8 nút chiếm quá nhiều chỗ trên màn hình chào (góp ý 10/2026)
SO_LUONG_SONG_SONG = 4

# Soạn sẵn không cần nhanh, nên cho AI suy nghĩ kỹ nhất và đọc nhiều nguồn nhất
# (vẫn cùng model, cùng danh sách trang chính thống). Đo 10/2026: với thiết lập
# nhanh của chatbot (effort "low", search_context_size "low"), 2/24 câu dẫn sai
# căn cứ - Luật Hộ tịch 03/2026/QH16 chưa có hiệu lực (từ 01/3/2027), Thông tư
# 21/2019/TT-BGDĐT đã bị Thông tư 10/2026/TT-BGDĐT thay thế.
ai_engine.REASONING_EFFORT = "high"
ai_engine.WEB_SEARCH_TOOL = {**ai_engine.WEB_SEARCH_TOOL, "search_context_size": "high"}
# Suy nghĩ kỹ có thể im lặng lâu hơn 30s giữa hai sự kiện stream (giới hạn của chatbot).
if ai_engine.client:
    ai_engine.client = ai_engine.client.with_options(timeout=300)


def _hoi(cau_hoi):
    """(câu trả lời, gợi ý) hoặc None nếu AI lỗi/trả lời dở dang."""
    parts = list(ai_engine.stream_answer(cau_hoi, dung_san=False))
    suggestions = []
    if parts and parts[-1].startswith(ai_engine.SUGGESTIONS_SEPARATOR):
        suggestions = json.loads(parts.pop()[1:])
    answer = ''.join(parts).strip()
    if not answer or 'bị gián đoạn' in answer:
        return None
    return answer, suggestions


def main():
    if not ai_engine.client:
        raise SystemExit("Chưa cấu hình OPENAI_API_KEY trong file .env")
    with ThreadPoolExecutor(SO_LUONG_SONG_SONG) as pool:
        ket_qua = list(pool.map(_hoi, CAU_HOI))

    cau_tra_loi, loi = {}, []
    for cau_hoi, kq in zip(CAU_HOI, ket_qua):
        if kq is None:
            loi.append(cau_hoi)
            continue
        answer, suggestions = kq
        cau_tra_loi[ai_engine._cache_key(cau_hoi)] = {
            'cau_hoi': cau_hoi, 'answer': answer, 'suggestions': suggestions}

    data = {
        'ngay_tao': ai_engine._today_vn().isoformat(),
        'nut': [q for q in CAU_HOI[:NUT_CAU_HOI_MAU] if ai_engine._cache_key(q) in cau_tra_loi],
        'cau_tra_loi': cau_tra_loi,
    }
    tmp = ai_engine.CAU_TRA_LOI_SAN_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ai_engine.CAU_TRA_LOI_SAN_FILE)

    print(f"✅ Đã soạn {len(cau_tra_loi)}/{len(CAU_HOI)} câu trả lời -> {ai_engine.CAU_TRA_LOI_SAN_FILE}")
    for cau_hoi in loi:
        print(f"⚠️ Lỗi, bỏ qua (chatbot sẽ tra trực tiếp khi có người hỏi): {cau_hoi}")


if __name__ == '__main__':
    main()
