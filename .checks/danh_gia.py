"""
Chấm điểm TOÀN BỘ chatbot (tra PDF + tra web + AI soạn câu trả lời) trên bộ câu
hỏi mẫu, để biết một thay đổi (thêm tài liệu, sửa prompt, sửa code) làm chatbot
tốt lên hay kém đi:
    python .checks/danh_gia.py              # chạy hết
    python .checks/danh_gia.py khai_sinh    # chỉ chạy các câu có mã chứa "khai_sinh"

Gọi AI thật đúng như khi người dân hỏi (~20 câu, tốn dưới 0,5 USD mỗi lần chạy).
Câu trả lời đầy đủ được ghi vào .checks/ket_qua_danh_gia.md để đọc lại và so
sánh giữa các lần chạy. Điểm chỉ chấm những điều kiểm tra được bằng máy (có
đúng số hiệu văn bản, đúng con số, đúng định dạng...) - nên đọc thêm câu trả lời
để đánh giá phần còn lại. Đáp án dựa trên các văn bản có trong kho (10/2026);
đổi bộ tài liệu thì sửa danh sách câu hỏi cho phù hợp.
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')
import ai_engine  # noqa: E402
from app import _clean_history  # noqa: E402

KET_QUA_FILE = os.path.join(ROOT, '.checks', 'ket_qua_danh_gia.md')

# loai: "thu_tuc" = hỏi cách làm thủ tục -> phải có các mục 🔰/📋/⚖️
#       "chi_tiet" = hỏi một chi tiết (lệ phí, mức hưởng...) -> phải có dòng "Căn cứ"
#       "hoi_lai" = câu hỏi chưa rõ -> phải hỏi lại, không đoán thủ tục
#       "tu_choi" = ngoài phạm vi -> từ chối ngắn, không gợi ý
#       None = không chấm định dạng
# hoi: nhiều câu = hội thoại nối tiếp, chỉ chấm câu trả lời cuối cùng.
CAU_HOI = [
    # --- Thủ tục chung (tra web) ---
    dict(ma='khai_sinh', loai='thu_tuc', hoi=["Thủ tục đăng ký khai sinh cần những gì?"],
         phai_co=[r'[Gg]iấy chứng sinh', r'Luật Hộ tịch']),
    dict(ma='tam_tru_thue_tro', loai='thu_tuc', hoi=["Đăng ký tạm trú cho người thuê trọ cần giấy tờ gì?"],
         phai_co=[r'Cư trú', r'hợp đồng thuê|chỗ ở hợp pháp']),
    dict(ma='chung_thuc_le_phi', loai='chi_tiet', hoi=["Chứng thực bản sao từ bản chính lệ phí bao nhiêu?"],
         phai_co=[r'\d[\d.]*\s*(đồng|đ)\b']),
    dict(ma='ket_hon', loai='thu_tuc', hoi=["Đăng ký kết hôn cần giấy tờ gì?"],
         phai_co=[r'[Tt]ờ khai đăng ký kết hôn']),
    dict(ma='can_cuoc_mat', loai=None, hoi=["Cấp lại thẻ căn cước bị mất làm sao?"],
         phai_co=[r'[Cc]ăn cước']),
    dict(ma='khai_tu', loai='thu_tuc', hoi=["Người nhà mất thì làm giấy khai tử thế nào?"],
         phai_co=[r'khai tử', r'[Gg]iấy báo tử']),
    # --- Có đáp án trong kho tài liệu ---
    dict(ma='muc_chuan_tgxh', loai='chi_tiet', hoi=["Mức chuẩn trợ giúp xã hội hiện nay là bao nhiêu?"],
         phai_co=[r'540\.000', r'335/2026']),
    dict(ma='mai_tang', loai=None,
         hoi=["Người nhà mất do tai nạn giao thông có được hỗ trợ mai táng phí không?"],
         phai_co=[r'335/2026', r'mai táng']),
    dict(ma='mien_hoc_phi', loai=None, hoi=["Sinh viên được miễn học phí khi nào?"], phai_co=[r'238/2025']),
    dict(ma='con_liet_si', loai=None, hoi=["Con của liệt sĩ được hưởng những chế độ ưu đãi gì?"],
         phai_co=[r'02/2020']),
    dict(ma='het_hieu_luc', loai=None, hoi=["Nghị quyết 40/2024/NQ-HĐND còn hiệu lực không?"],
         phai_co=[r'hết hiệu lực', r'32/2025']),
    dict(ma='dieu_11_nd335', loai=None, hoi=["Điều 11 Nghị định 335/2026/NĐ-CP quy định gì?"],
         phai_co=[r'mai táng']),
    dict(ma='dieu_5_nd118', loai=None, hoi=["Điều 5 Nghị định 118/2025/NĐ-CP quy định gì?"],
         phai_co=[r'[Cc]ửa quyền|sách nhiễu']),  # ý đầu tiên (điểm a khoản 1) của Điều 5
    dict(ma='hoa_tang', loai=None, hoi=["Người nhà mất được hỗ trợ tiền hỏa táng không?"],
         phai_co=[r'14/2015', r'hỏa táng']),
    dict(ma='khai_sinh_nuoc_ngoai', loai=None,
         hoi=["Con có cha là người nước ngoài thì đăng ký khai sinh ở đâu?"], phai_co=[r'khai sinh']),
    # --- Thông tin của phường ---
    dict(ma='dia_chi', loai=None, hoi=["Trung tâm hành chính công phường ở đâu?"], phai_co=[r'183A Lý Nam Đế']),
    dict(ma='gio_lam_viec', loai=None, hoi=["Giờ làm việc của phường thế nào?"], phai_co=[]),
    # --- Hỏi lại / từ chối ---
    dict(ma='mo_ho', loai='hoi_lai', hoi=["Tôi muốn làm giấy tờ cho con"]),
    dict(ma='ngoai_pham_vi', loai='tu_choi', hoi=["Hôm nay thời tiết TP.HCM thế nào?"]),
    # --- Hội thoại nối tiếp ---
    dict(ma='noi_tiep_le_phi', loai='chi_tiet',
         hoi=["Thủ tục đăng ký khai sinh cần những gì?", "Vậy lệ phí bao nhiêu?"],
         phai_co=[r'khai sinh', r'miễn|không thu|đồng']),
    dict(ma='noi_tiep_3_luot', loai=None,
         hoi=["Thủ tục đăng ký khai sinh cần những gì?", "Lệ phí bao nhiêu?", "Nộp online được không?"],
         phai_co=[r'khai sinh|Hộ tịch|123/2015', r'trực tuyến|[Dd]ịch vụ công']),
    dict(ma='tra_loi_hoi_lai', loai=None, hoi=["Đăng ký tạm trú cần giấy tờ gì?", "Cho người thuê trọ"],
         phai_co=[r'hợp đồng thuê|chủ nhà|chủ sở hữu|chỗ ở hợp pháp']),
]

# Áp dụng cho MỌI câu trả lời.
CAM_CHUNG = [
    (r'https?://|www\.', "có đường link"),
    (r'【||filecite', "lọt dấu trích dẫn tệp"),
    (r'(nộp|đến|tại|liên hệ)[^.\n]{0,25}(UBND|Ủy ban nhân dân) (quận|huyện|cấp huyện)',
     "hướng dẫn đến UBND quận/huyện"),  # Luật Hộ tịch 2014 bản gốc vẫn ghi "UBND cấp huyện"
    (r'(?m)^\s*[-*•]\s*$', "có gạch đầu dòng trống"),
]


def hoi(cau_hoi, history):
    """(câu trả lời, gợi ý, số giây) - giống trình duyệt nhận được."""
    started = time.monotonic()
    parts = list(ai_engine.stream_answer(cau_hoi, _clean_history(history)))
    seconds = time.monotonic() - started
    goi_y = []
    if parts and parts[-1].startswith(ai_engine.SUGGESTIONS_SEPARATOR):
        goi_y = json.loads(parts.pop()[1:])
    return ''.join(parts).strip(), goi_y, seconds


def cham(case, tra_loi, goi_y):
    """Danh sách lỗi (rỗng = đạt)."""
    loi = []
    if not tra_loi:
        return ["không có câu trả lời (OpenAI lỗi?)"]
    for pattern, mo_ta in CAM_CHUNG:
        if re.search(pattern, tra_loi):
            loi.append(mo_ta)
    for pattern in case.get('phai_co', []):
        if not re.search(pattern, tra_loi):
            loi.append(f"thiếu /{pattern}/")
    loai = case['loai']
    if loai == 'thu_tuc':
        if not re.search(r'Trình tự thực hiện|Thành phần hồ sơ', tra_loi):
            loi.append("không theo mẫu 3 mục")
    elif loai == 'chi_tiet':
        if not re.search(r'Căn cứ', tra_loi):
            loi.append("thiếu dòng Căn cứ")
    elif loai == 'hoi_lai':
        if '?' not in tra_loi[-300:]:
            loi.append("không hỏi lại")
        if re.search(r'Thành phần hồ sơ', tra_loi):
            loi.append("đoán thủ tục thay vì hỏi lại")
    elif loai == 'tu_choi':
        if goi_y:
            loi.append("có gợi ý dù từ chối")
        if len(tra_loi) > 400:
            loi.append("từ chối quá dài")
    if loai in ('thu_tuc', 'chi_tiet', 'hoi_lai') and not goi_y:
        loi.append("không có gợi ý câu hỏi tiếp")
    return loi


def main():
    loc = sys.argv[1] if len(sys.argv) > 1 else ''
    cases = [c for c in CAU_HOI if loc in c['ma']]
    if not ai_engine.client:
        print("❌ Chưa cấu hình OPENAI_API_KEY trong file .env")
        return 1

    dat, tong_giay, bao_cao = 0, 0.0, []
    for case in cases:
        history, tra_loi, goi_y, seconds = [], '', [], 0.0
        for cau in case['hoi']:
            tra_loi, goi_y, seconds = hoi(cau, history)
            # Trình duyệt chỉ lưu lượt hỏi-đáp vào lịch sử, server tự cắt bớt (_clean_history).
            history += [{'role': 'user', 'content': cau}, {'role': 'assistant', 'content': tra_loi}]
        loi = cham(case, tra_loi, goi_y)
        dat += not loi
        tong_giay += seconds
        print(f"{'✓' if not loi else '✗'} {seconds:5.1f}s  {case['ma']:<18} {'; '.join(loi)}", flush=True)
        bao_cao.append(f"## {'✓' if not loi else '✗'} {case['ma']} ({seconds:.1f}s)\n\n"
                       + ''.join(f"> **Hỏi:** {cau}\n>\n" for cau in case['hoi'])
                       + (f"\n**Lỗi:** {'; '.join(loi)}\n" if loi else '')
                       + f"\n{tra_loi}\n\n*Gợi ý:* {' | '.join(goi_y) or '(không có)'}\n")

    tom_tat = (f"Đạt {dat}/{len(cases)} câu - trung bình {tong_giay / max(len(cases), 1):.1f}s "
               f"cho câu trả lời cuối (chạy lúc {time.strftime('%d/%m/%Y %H:%M')})")
    print(f"\n{tom_tat}\nCâu trả lời đầy đủ: {os.path.relpath(KET_QUA_FILE, ROOT)}")
    with open(KET_QUA_FILE, 'w', encoding='utf-8') as f:
        f.write(f"# Kết quả đánh giá chatbot\n\n{tom_tat}\n\n" + '\n'.join(bao_cao))
    return 0 if dat == len(cases) else 1


if __name__ == '__main__':
    sys.exit(main())
