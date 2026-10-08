"""
Kiểm tra nhanh chất lượng tra cứu tài liệu PDF (tailieu.py) sau khi thêm/xóa
PDF hoặc chỉnh cách chia đoạn:  python .checks/tra_cuu_pdf.py

- Câu hỏi CÓ đáp án trong kho: trích đoạn đưa cho AI phải lấy từ đúng văn bản
  và chứa đúng nội dung cần thiết.
- Câu hỏi KHÔNG có trong kho (thủ tục chung, phải tra web): nên không có hoặc
  chỉ có rất ít trích đoạn.
Chỉ gọi API embeddings (~20 lượt, gần như không tốn tiền). Kết quả đúng với
các PDF hiện có (10/2026) - đổi bộ PDF thì sửa danh sách câu hỏi cho phù hợp.
"""
import os
import re
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')
from openai import OpenAI  # noqa: E402

import tailieu  # noqa: E402
from config import OPENAI_API_KEY  # noqa: E402

# (câu hỏi, số hiệu văn bản phải có trong trích đoạn, nội dung phải có)
CO_TRONG_KHO = [
    ("Xã đạt 100% người cao tuổi có thẻ BHYT thì được khen thưởng gì?", "32/2025", "Chăm sóc sức khỏe người cao tuổi"),
    ("bà bầu nhà nghèo được hỗ trợ tiền khám sàng lọc không", "32/2025", "Tầm soát trước sinh"),
    ("Con của liệt sĩ được hưởng những chế độ ưu đãi gì?", "02/2020", "thân nhân của liệt sỹ"),
    ("sinh viên được miễn học phí khi nào", "238/2025", "Đối tượng được miễn học phí"),
    ("ông bà 80 tuổi không có lương hưu được trợ cấp bao nhiêu", "35/2023|335/2026", "[Nn]gười cao tuổi"),
    ("hồ sơ nộp ở bộ phận một cửa bao lâu thì có kết quả", "118/2025", "[Tt]hời gian giải quyết"),
    ("mức chuẩn trợ giúp xã hội hiện nay là bao nhiêu", "335/2026", "Mức chuẩn trợ giúp xã hội"),
    ("nhà bị cháy được nhà nước hỗ trợ gì", "335/2026", "cháy"),
    ("người nhà mất do tai nạn giao thông có được hỗ trợ mai táng phí không", "335/2026", "mai táng"),
    ("trẻ mồ côi ở TP.HCM được hỗ trợ gì", "35/2023", "mồ côi"),
    ("thương binh được hỗ trợ gì về nhà ở", "02/2020", "nhà ở"),
    ("học sinh hộ cận nghèo được giảm học phí bao nhiêu phần trăm", "238/2025", "giảm 50%|giảm học phí"),
    ("nộp hồ sơ trực tuyến trên Cổng Dịch vụ công quốc gia như thế nào", "118/2025", "trực tuyến"),
    ("Nghị quyết 40/2024/NQ-HĐND còn hiệu lực không?", "32/2025", "ĐÃ HẾT HIỆU LỰC"),
    # Hỏi thẳng theo số hiệu văn bản / số Điều: vector ngữ nghĩa gần như không phân biệt được các con số.
    ("Điều 5 Nghị định 118/2025/NĐ-CP quy định gì?", "118/2025", "Những hành vi không được làm"),
    ("điều 24 nghị định 118 nói về cái gì", "118/2025", "Phương thức nộp phí"),
    ("Nghị định 335/2026 điều 4", "335/2026", "540.000"),
    ("Điều 11 Nghị định 335/2026/NĐ-CP", "335/2026", "Hỗ trợ chi phí mai táng"),
    ("Thủ tục đăng ký khai sinh cần những gì?", "60/2014", "Thủ tục đăng ký khai sinh"),
    ("đăng ký kết hôn cần giấy tờ gì", "60/2014", "Thủ tục đăng ký kết hôn"),
    ("người nhà mất được hỗ trợ tiền hỏa táng không", "14/2015", "hỏa táng"),
    ("đóng bảo hiểm xã hội tự nguyện được hưởng chế độ gì", "41/2024", "tự nguyện"),
    ("Luật Doanh nghiệp 2014 còn hiệu lực không", "59/2020", "68/2014/QH13 hết hiệu lực"),
]
NGOAI_KHO = ["Đăng ký tạm trú cho người thuê trọ cần giấy tờ gì?",
             "Chứng thực bản sao từ bản chính lệ phí bao nhiêu?", "cấp lại thẻ căn cước bị mất làm sao"]


def main():
    client = OpenAI(api_key=OPENAI_API_KEY)
    today = date.today()
    files, doan = tailieu._tai_kho()
    print(f"Kho: {len(files)} văn bản, {len(doan)} đoạn")
    for name, (dung_duoc, mo_ta) in tailieu.tinh_trang(files, today).items():
        print(f"  {'✓' if dung_duoc else '✗'} {files[name].get('so_hieu')}: {mo_ta}")

    dat = 0
    print("\nCâu hỏi có đáp án trong kho:")
    for question, so_hieu, noi_dung in CO_TRONG_KHO:
        t = time.monotonic()
        excerpts, score = tailieu.tim_trich_doan(client, question, today)
        ok = bool(re.search(so_hieu, excerpts) and re.search(noi_dung, excerpts))
        dat += ok
        print(f"  {'✓' if ok else '✗'} khớp {score:.2f} | {len(excerpts):5} ký tự | {time.monotonic() - t:.2f}s | {question}")

    print("\nCâu hỏi không có trong kho (nên không có trích đoạn):")
    for question in NGOAI_KHO:
        excerpts, score = tailieu.tim_trich_doan(client, question, today)
        print(f"  {'✓' if len(excerpts) < 1000 else '!'} khớp {score:.2f} | {len(excerpts):5} ký tự | {question}")

    print(f"\nĐạt {dat}/{len(CO_TRONG_KHO)} câu có đáp án trong kho.")
    return 0 if dat == len(CO_TRONG_KHO) else 1


if __name__ == '__main__':
    sys.exit(main())
