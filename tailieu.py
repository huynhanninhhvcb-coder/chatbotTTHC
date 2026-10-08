"""
Tra cứu tài liệu PDF nội bộ của phường: kho do sync_pdf.py xây từ thư mục
thutuc_data/ (lưu trong pdf_index.json), gồm thông tin từng văn bản và các đoạn
đã chia theo Điều kèm vector ngữ nghĩa.

Tự so khớp ngay trong code thay vì gọi vector_stores.search của OpenAI như
trước: đo thực tế 10/2026, vector_stores.search mất ~1,5-2,3s mỗi câu hỏi (phần
lớn do OpenAI xếp hạng lại kết quả), còn cách này chỉ cần 1 lần gọi embeddings
cho câu hỏi (~0,3s); so khớp với vài trăm đoạn chạy trong RAM chỉ mất vài ms.

Mỗi trích đoạn ghi kèm tên đầy đủ và tình trạng hiệu lực của văn bản. Văn bản
đã bị một văn bản khác trong kho thay thế (VD Nghị quyết 40/2024/NQ-HĐND bị
32/2025/NQ-HĐND thay thế từ 01/9/2025) không được đưa vào trích đoạn nữa: khi
chỉ thấy tên file, AI từng trích nhầm quy định cũ ("Giấy khen của Chủ tịch UBND
cấp huyện") của văn bản đã hết hiệu lực.
"""
import base64
import math
import operator
import os
import re
import struct
import threading
from array import array

from config import PDF_INDEX_FILE, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS
from search import remove_accents
from sync_pdf import load_index

# math.sumprod (Python 3.12+) tính tích vô hướng bằng C, nhanh hơn nhiều lần vòng lặp Python.
_dot = getattr(math, 'sumprod', None) or (lambda a, b: sum(map(operator.mul, a, b)))

# Xét tối đa 8 đoạn khớp nhất; mỗi đoạn được thay bằng cả Điều chứa nó (nếu
# Điều không quá dài - xem sync_pdf.DIEU_MAX_CHARS), tổng không quá ~9.000 ký
# tự (~2.700 token) để prompt gọn, AI đọc nhanh.
MAX_KHOP = 8
MAX_TRICH_DOAN_CHARS = 9000
# Độ giống nhau (cosine) tối thiểu để coi một đoạn là liên quan. Đo 10/2026 trên
# 14 câu hỏi có đáp án trong kho: đoạn đúng đạt 0,51-0,70; 5 câu hỏi không có
# trong kho (khai sinh, tạm trú, chứng thực...) cao nhất 0,50. Đặt hơi thấp hơn
# để không bỏ sót - prompt đã dặn AI bỏ qua trích đoạn không liên quan.
MIN_SCORE = 0.48
EMBEDDING_TIMEOUT = 3  # giây

_lock = threading.Lock()
_kho = {'mtime': None, 'files': {}, 'doan': []}


def _giai_ma(vector_b64):
    raw = base64.b64decode(vector_b64)
    vec = struct.unpack(f'<{len(raw) // 2}e', raw)
    norm = math.sqrt(_dot(vec, vec)) or 1.0
    return array('d', (x / norm for x in vec))


def _tai_kho():
    """Kho đang dùng; tự đọc lại khi pdf_index.json đổi (vừa chạy sync_pdf.py hoặc deploy bản mới)."""
    try:
        mtime = os.path.getmtime(PDF_INDEX_FILE)
    except OSError:
        mtime = None
    with _lock:
        if mtime != _kho['mtime']:
            index = load_index() if mtime else {}
            if (index.get('embedding_model'), index.get('dimensions')) != (EMBEDDING_MODEL, EMBEDDING_DIMENSIONS):
                index = {}  # kho cũ/khác model: vector không so được với câu hỏi
            files = index.get('files', {})
            # Mỗi đoạn: (tên file, vị trí trong văn bản, chữ đưa cho AI đọc, vector). Chữ
            # đưa cho AI là cả Điều nếu sync_pdf.py có lưu, không thì chính đoạn đó.
            _kho['doan'] = [(name, (i, j), dieu.get('text') or d['text'], _giai_ma(d['vector']))
                            for name, f in files.items()
                            for i, dieu in enumerate(f.get('dieu', []))
                            for j, d in enumerate(dieu['doan'])]
            _kho['files'] = {name: {k: v for k, v in f.items() if k != 'dieu'} for name, f in files.items()}
            _kho['mtime'] = mtime
        return _kho['files'], _kho['doan']


def phien_ban():
    """Đổi mỗi khi kho đổi - dùng làm một phần khóa bộ nhớ đệm câu trả lời."""
    _tai_kho()
    return _kho['mtime']


def _chuan_so_hieu(so_hieu):
    # "40/2024/NQ-HĐND", "40/2024/NQ-HDND", "40/2024/nq-hđnd" -> "40/2024/NQHDND"
    return re.sub(r'[^A-Z0-9/]', '', remove_accents(so_hieu or '').upper())


def _ngay(iso):
    y, m, d = iso.split('-')
    return f"{d}/{m}/{y}"


def tinh_trang(files, today):
    """{tên file: (còn áp dụng?, mô tả hiệu lực)} tại ngày today (date)."""
    today = today.isoformat()
    bi_thay = {}  # số hiệu (đã chuẩn hóa) -> văn bản thay thế nó
    for f in files.values():
        if f.get('hieu_luc_tu') and f['hieu_luc_tu'] > today:
            continue  # văn bản mới chưa có hiệu lực thì văn bản cũ vẫn đang áp dụng
        for so_hieu in f.get('thay_the', []):
            if _chuan_so_hieu(so_hieu) != _chuan_so_hieu(f.get('so_hieu')):
                bi_thay[_chuan_so_hieu(so_hieu)] = f
    result = {}
    for name, f in files.items():
        moi = bi_thay.get(_chuan_so_hieu(f.get('so_hieu')))
        if f.get('loai') == 'thong_tin':  # file .md phường tự soạn (xem sync_pdf.chia_muc)
            result[name] = (True, "thông tin do phường cung cấp")
        elif moi:
            tu = f" từ {_ngay(moi['hieu_luc_tu'])}" if moi.get('hieu_luc_tu') else ''
            result[name] = (False, f"đã hết hiệu lực, bị thay thế bởi {moi.get('so_hieu')}{tu}")
        elif f.get('hieu_luc_tu') and f['hieu_luc_tu'] > today:
            result[name] = (True, f"chưa có hiệu lực, áp dụng từ {_ngay(f['hieu_luc_tu'])}")
        else:
            tu = f" từ {_ngay(f['hieu_luc_tu'])}" if f.get('hieu_luc_tu') else ''
            result[name] = (True, f"đang có hiệu lực{tu}")
    return result


# Câu hỏi nêu thẳng số hiệu văn bản / số Điều ("Điều 11 Nghị định 335/2026/NĐ-CP",
# "điều 24 nghị định 118"): vector ngữ nghĩa gần như không phân biệt được các con
# số - đo 10/2026 vẫn tìm đúng văn bản nhưng Điều được hỏi xếp tận thứ 8, thứ 34
# nên có khi không lọt vào trích đoạn. Vì vậy cộng điểm cho các đoạn của văn bản
# được nêu số hiệu, và đưa Điều được hỏi của văn bản đó lên đầu. (Chạy trên câu
# hỏi đã bỏ dấu: "nghị định 118/2025/nđ-cp" -> "nghi dinh 118/2025/nd-cp".)
_VAN_BAN_HOI = re.compile(
    r'\b(\d+)/(\d{4})\b'
    r'|\b(?:nghi dinh|nghi quyet|thong tu|phap lenh|quyet dinh|luat|nd|nq|tt|qd)\s+(?:so\s+)?(\d+)\b(?!/)')
_DIEU_HOI = re.compile(r'\bdieu\s+(\d+[a-z]?)\b')
DIEM_VAN_BAN_DUOC_HOI = 0.05
DIEM_DIEU_DUOC_HOI = 1.0


def _van_ban_duoc_hoi(query, files):
    """Tên các file có số hiệu được nhắc tới trong câu hỏi ("118/2025", "nghị định 118")."""
    names = set()
    for m in _VAN_BAN_HOI.finditer(remove_accents(query)):
        so, nam = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), None)
        for name, f in files.items():
            parts = _chuan_so_hieu(f.get('so_hieu')).split('/')  # ["118", "2025", "NDCP"]
            if parts[0].lstrip('0') == so.lstrip('0') and (nam is None or parts[1:2] == [nam]):
                names.add(name)
    return names


def xep_hang(client, query, doan, files=None):
    """[(điểm, tên file, vị trí, chữ đưa cho AI), ...] từ khớp nhất. Lỗi API -> ném lỗi."""
    # Bình thường chỉ ~0,2s. Mạng chập chờn thì bỏ qua PDF sau ~3s (chatbot vẫn trả
    # lời bằng tra cứu web) thay vì để người dùng chờ theo thời gian chờ mặc định 10 phút.
    q = client.with_options(timeout=EMBEDDING_TIMEOUT, max_retries=1).embeddings.create(
        model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS, input=query).data[0].embedding
    norm = math.sqrt(_dot(q, q)) or 1.0
    q = [x / norm for x in q]
    van_ban = _van_ban_duoc_hoi(query, files or {})
    so_dieu = _DIEU_HOI.findall(remove_accents(query))
    dieu = re.compile(r'Điều (?:%s)\.' % '|'.join(so_dieu)) if van_ban and so_dieu else None

    def diem(name, text, vec):
        score = _dot(q, vec)
        if name in van_ban:
            score += DIEM_VAN_BAN_DUOC_HOI
            if dieu and dieu.match(text):
                score += DIEM_DIEU_DUOC_HOI
        return score

    return sorted(((diem(name, text, vec), name, pos, text) for name, pos, text, vec in doan), reverse=True)


def tim_trich_doan(client, query, today):
    """
    (trích đoạn, điểm khớp cao nhất): trích đoạn liên quan tới câu hỏi đã ghi tên
    + hiệu lực văn bản, sẵn sàng đưa vào prompt. ('', 0) nếu kho trống, không có
    đoạn nào đủ liên quan hoặc gọi API lỗi (chatbot vẫn trả lời bằng tra cứu web).
    """
    files, doan = _tai_kho()
    if not doan:
        return '', 0
    try:
        ranked = xep_hang(client, query, doan, files)
    except Exception as e:
        print(f"⚠️ Lỗi tra cứu tài liệu PDF: {e}")
        return '', 0
    status = tinh_trang(files, today)

    chosen, het_hieu_luc, seen, total, best, xet = [], {}, set(), 0, 0, 0
    for score, name, pos, text in ranked:
        if score < MIN_SCORE or xet >= MAX_KHOP:
            break
        if not status[name][0]:
            # Không trích quy định cũ, chỉ báo cho AI biết văn bản đó đã hết hiệu
            # lực (người dân có thể đang hỏi đúng về văn bản này). Đoạn của văn bản
            # cũ không tính vào MAX_KHOP: hỏi đúng số hiệu văn bản cũ thì các đoạn
            # của nó được cộng điểm, chiếm hết chỗ của văn bản mới thay thế nó.
            het_hieu_luc[name] = status[name][1]
            continue
        xet += 1
        best = max(best, score)
        if text in seen or total + len(text) > MAX_TRICH_DOAN_CHARS:
            continue  # nhiều đoạn cùng một Điều -> chỉ đưa Điều đó một lần
        seen.add(text)
        total += len(text)
        chosen.append((name, pos, text))
    if not chosen and not het_hieu_luc:
        return '', 0

    by_file = {}  # văn bản có đoạn khớp nhất lên trước, trong mỗi văn bản giữ đúng thứ tự các Điều
    for name, pos, text in chosen:
        by_file.setdefault(name, []).append((pos, text))
    parts = [f"[{files[name].get('ten') or name} - {status[name][1]}]\n" +
             '\n\n'.join(text for _, text in sorted(items))
             for name, items in by_file.items()]
    if het_hieu_luc:
        parts.append("Văn bản trong kho ĐÃ HẾT HIỆU LỰC, không áp dụng: " + '; '.join(
            f"{files[name].get('ten') or name} ({mo_ta})" for name, mo_ta in het_hieu_luc.items()))
    return '\n\n'.join(parts), best
