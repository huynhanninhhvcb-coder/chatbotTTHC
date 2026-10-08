"""
Xây dựng kho tra cứu từ các file tài liệu trong thutuc_data/ để chatbot tự tìm
trích đoạn liên quan tới câu hỏi (xem tailieu.py).

Cách dùng: chép/xóa/sửa file trong thutuc_data/ rồi chạy
    python sync_pdf.py
sau đó commit + push file pdf_index.json (server Render đọc kho từ file này).

Các loại file nhận được:
  - .pdf, .docx, .txt: văn bản pháp luật (luật, nghị định, quyết định công bố thủ
    tục...). Nhiều PDF văn bản chính thức là ảnh scan không đọc được chữ - khi đó
    tải bản Word trên thuvienphapluat.vn / vbpl.vn (file .doc thì mở bằng Word rồi
    "Save As" thành .docx) hoặc chép chữ vào file .txt.
  - .md: thông tin do phường tự soạn (giờ làm việc, liên hệ, lưu ý khi nộp hồ
    sơ...), không phải văn bản pháp luật. Dòng "# ..." đầu tiên là tên tài liệu,
    mỗi mục "## ..." là một đoạn tra cứu; ghi chú <!-- ... --> bị bỏ qua.
  - File có tên bắt đầu bằng "_" (bản nháp chưa điền xong) được bỏ qua.

Với mỗi file văn bản pháp luật mới hoặc đã sửa (so mã băm SHA-256 với lần chạy trước), script:
  1. Đọc chữ trong file. PDF dạng ảnh scan (không có lớp chữ) -> báo lỗi, bỏ qua.
  2. Nhờ AI đọc phần đầu và các câu về hiệu lực để lấy thông tin văn bản: số
     hiệu, tên đầy đủ, ngày có hiệu lực, các văn bản bị nó thay thế. Nhờ đó
     chatbot biết văn bản nào đã hết hiệu lực (VD Nghị quyết 32/2025/NQ-HĐND
     thay thế 40/2024/NQ-HĐND) để không trích nhầm quy định cũ. AI đọc sai thì
     sửa tay các trường này trong pdf_index.json được - lần chạy sau giữ nguyên
     chừng nào file PDF không đổi.
  3. Chia văn bản theo từng Điều (Điều dài thì chia tiếp theo khoản, điểm) và
     tính vector ngữ nghĩa (embedding) cho từng đoạn.
File không đổi được giữ nguyên, không tốn lượt gọi API nào. Không cần khởi động
lại web server: tailieu.py tự đọc lại pdf_index.json khi file này thay đổi.
"""
import base64
import hashlib
import json
import os
import re
import struct
import sys
import zipfile
from datetime import date
from xml.etree import ElementTree

from openai import OpenAI

from config import (OPENAI_API_KEY, CHAT_MODEL, PDF_DIR, PDF_INDEX_FILE,
                    EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)

# So khớp câu hỏi trên đoạn NGẮN (~800 ký tự: một vài khoản) nhưng đưa cho AI
# đọc cả Điều chứa đoạn đó (nếu Điều không quá dài). Đo trên 14 câu hỏi mẫu
# (10/2026): đoạn 800 ký tự tìm đúng đoạn trong top 6 cho 14/14 câu, đoạn 2.000
# ký tự chỉ 12/14 - đoạn dài gồm nhiều ý nên "loãng", câu hỏi về một ý nhỏ
# (VD "bà bầu nhà nghèo được hỗ trợ khám sàng lọc không") không khớp được.
DOAN_MAX_CHARS = 800
DIEU_MAX_CHARS = 2500  # Điều dài hơn thì chỉ đưa các đoạn khớp, không đưa cả Điều
NGU_CANH_MAX_CHARS = 300  # dòng đầu Điều/khoản lặp lại ở đầu mỗi đoạn con
# Mẫu tờ khai/phụ lục chỉ giữ tiêu đề: phần thân toàn ô trống "Họ tên, Ngày
# sinh, Nơi cư trú..." khiến chúng khớp nhầm với mọi câu hỏi về thủ tục.
MAU_MAX_CHARS = 250
EMBEDDING_BATCH = 64


# ==================== ĐỌC & CHIA ĐOẠN ====================
# Dòng mở đầu một ý mới trong văn bản pháp luật. pypdf trả về chữ đã bị ngắt
# dòng theo khổ giấy, nên các dòng còn lại được nối vào dòng trước.
_BAT_DAU_Y = re.compile(
    r'(Điều \d+[a-z]?\.|Chương [IVXLCDM]+\b|CHƯƠNG [IVXLCDM]+\b|Mục \d+\b|MỤC \d+\b'
    r'|Phụ lục|PHỤ LỤC|Mẫu số|Nơi nhận:|\d+(?:\.\d+)*\.\s|[a-zđ]\)\s|[-–+•]\s)')
_DIEU = re.compile(r'Điều \d+[a-z]?\.')
_KHOAN = re.compile(r'\d+\.\s')
_DIEM = re.compile(r'[a-zđ]\)\s')
_PHU_LUC = re.compile(r'(Phụ lục|PHỤ LỤC|Mẫu số)\b')


DUOI_FILE = ('.pdf', '.docx', '.txt', '.md')
_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def doc_chu_pdf(path):
    from pypdf import PdfReader  # chỉ cần khi đồng bộ, server không dùng tới
    return '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)


def doc_chu_docx(path):
    # File .docx là gói zip, chữ nằm trong word/document.xml: mỗi <w:p> là một
    # đoạn (kể cả đoạn trong ô bảng), chữ nằm trong các <w:t>. Đọc thẳng để khỏi
    # thêm thư viện. Lưu ý: số thứ tự khoản do Word TỰ ĐÁNH (định dạng danh sách)
    # không nằm trong chữ nên bị mất - văn bản tải từ thuvienphapluat.vn gõ số tay
    # nên không bị ảnh hưởng.
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read('word/document.xml'))
    return '\n'.join(''.join(t.text or '' for t in p.iter(f'{_W}t')) for p in root.iter(f'{_W}p'))


def doc_chu(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return doc_chu_pdf(path)
    if ext == '.docx':
        return doc_chu_docx(path)
    with open(path, encoding='utf-8-sig') as f:
        return f.read()


_QUOC_HIEU = re.compile(r'CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\s*Độc lập\s*-\s*Tự do\s*-\s*Hạnh phúc\s*')


def _ghep_dong(text):
    lines = []
    for raw in _QUOC_HIEU.sub('', text).splitlines():
        line = re.sub(r'[.…]{3,}', '…', raw).strip()  # dòng chấm để điền trong mẫu đơn
        if not line or re.fullmatch(r'[-_=…]{3,}', line):
            continue
        if lines and not _BAT_DAU_Y.match(line) and not re.search(r'[.:;!?]$', lines[-1]):
            # "NĐ-" + "CP" -> "NĐ-CP" (số hiệu văn bản bị ngắt dòng ngay sau dấu gạch)
            joiner = '' if re.search(r'\S-$', lines[-1]) else ' '
            lines[-1] += joiner + line
        else:
            lines.append(line)
    return lines


def _doan(context, lines):
    return '\n'.join(context + lines)


def _ngan_sach(context):
    return max(DOAN_MAX_CHARS - len(_doan(context, [])) - 1, 500)


def _chia_nhom(group, context, levels):
    """
    Chia một nhóm dòng quá dài (dòng đầu là tên Điều/khoản/điểm) thành nhiều
    đoạn; dòng đầu được lặp lại ở đầu mỗi đoạn con để đoạn nào cũng rõ ngữ cảnh.
    """
    if len(group) == 1:  # một câu quá dài, không còn cấu trúc để chia: cắt cứng
        text, budget = group[0], _ngan_sach(context)
        return [_doan(context, [text[i:i + budget]]) for i in range(0, len(text), budget)]
    first, chunks = group[0], []
    if len(first) > NGU_CANH_MAX_CHARS:
        chunks = _chia_nhom([first], context, levels)
        first = first[:NGU_CANH_MAX_CHARS] + '…'
    return chunks + _dong_goi(group[1:], context + [first], levels)


def _dong_goi(lines, context, levels):
    """
    Gom các khoản (không có khoản thì các điểm, rồi tới từng dòng) liền nhau
    thành đoạn <= DOAN_MAX_CHARS, mỗi đoạn mở đầu bằng các dòng ngữ cảnh.
    """
    budget = _ngan_sach(context)
    groups, rest = None, levels
    while rest and groups is None:
        pattern, rest = rest[0], rest[1:]
        starts = [i for i, line in enumerate(lines) if pattern.match(line)]
        if starts:
            groups = ([lines[:starts[0]]] if starts[0] else []) + \
                     [lines[a:b] for a, b in zip(starts, starts[1:] + [len(lines)])]
    if groups is None:
        groups = [[line] for line in lines]

    chunks, current = [], []
    for group in groups:
        if len('\n'.join(group)) > budget:
            if current:
                chunks.append(_doan(context, current))
                current = []
            chunks.extend(_chia_nhom(group, context, rest))
            continue
        if current and len('\n'.join(current + group)) > budget:
            chunks.append(_doan(context, current))
            current = []
        current += group
    if current:
        chunks.append(_doan(context, current))
    return chunks


def chia_doan(text):
    """
    Chia văn bản thành các khối (phần mở đầu, từng Điều, từng phụ lục/mẫu),
    mỗi khối chia tiếp thành các đoạn ngắn để so khớp với câu hỏi.
    Trả về [(chữ của cả khối, [đoạn, ...]), ...].
    """
    blocks, current, skipping = [], [], False
    for line in _ghep_dong(text):
        if line.startswith('Nơi nhận:'):
            skipping = True  # danh sách nơi nhận + chữ ký: không có nội dung để tra
            continue
        starts_block = _DIEU.match(line) or _PHU_LUC.match(line)
        if skipping and not _PHU_LUC.match(line):
            continue
        skipping = False
        if starts_block and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)

    result, seen = [], set()
    for block in blocks:
        text = '\n'.join(block)
        if _PHU_LUC.match(text):
            text = ' '.join(text.split())[:MAU_MAX_CHARS]
        if text in seen:  # mẫu/phụ lục lặp lại cùng tiêu đề
            continue
        seen.add(text)
        chunks = [text] if len(text) <= DOAN_MAX_CHARS else _chia_nhom(block, [], [_KHOAN, _DIEM])
        result.append((text, chunks))
    return result


# ==================== THÔNG TIN VĂN BẢN (AI đọc) ====================
_META_SCHEMA = {
    "type": "object",
    "properties": {
        "so_hieu": {"type": "string",
                    "description": "Số hiệu văn bản, đúng như ghi trên văn bản. VD: 32/2025/NQ-HĐND"},
        "ten": {"type": "string",
                "description": "Tên đầy đủ để trích dẫn: loại văn bản + số hiệu + ngày ban hành + cơ quan ban "
                               "hành + trích yếu. VD: Nghị quyết số 32/2025/NQ-HĐND ngày 28/8/2025 của HĐND "
                               "TP. Hồ Chí Minh quy định về chính sách khen thưởng, hỗ trợ ..."},
        "ngay_ban_hanh": {"type": ["string", "null"], "description": "Ngày ban hành, dạng YYYY-MM-DD"},
        "hieu_luc_tu": {"type": ["string", "null"],
                        "description": "Ngày văn bản bắt đầu có hiệu lực, dạng YYYY-MM-DD"},
        "thay_the": {"type": "array", "items": {"type": "string"},
                     "description": "Số hiệu các văn bản bị văn bản này thay thế hoặc tuyên bố hết hiệu lực "
                                    "TOÀN BỘ. Không ghi văn bản chỉ bị sửa đổi, bổ sung hoặc bãi bỏ một phần."},
    },
    "required": ["so_hieu", "ten", "ngay_ban_hanh", "hieu_luc_tu", "thay_the"],
    "additionalProperties": False,
}
_CAU_HIEU_LUC = re.compile(r'hiệu lực|thay thế|bãi bỏ', re.IGNORECASE)
# Câu nói về hiệu lực của CHÍNH văn bản ("Luật này có hiệu lực thi hành từ...",
# "Nghị định này thay thế...", "... hết hiệu lực kể từ ngày Luật này..."). Bộ luật
# dài có hàng trăm câu kiểu "nghị quyết có hiệu lực kể từ ngày thông qua" và các
# quy định chuyển tiếp - lấy theo thứ tự xuất hiện thì 4.000 ký tự không chứa nổi
# điều khoản hiệu lực (gặp 10/2026 với Bộ luật Dân sự, Luật Doanh nghiệp, Luật
# Kinh doanh bất động sản), nên đưa các câu này lên trước.
_CAU_HIEU_LUC_CHINH = re.compile(
    r'này có hiệu lực (thi hành )?(từ|kể từ|sau)|này thay thế|Hiệu lực thi hành|hết hiệu lực (thi hành )?kể từ')


def doc_thong_tin(client, text):
    lines = _ghep_dong(text)
    dau = '\n'.join(lines)[:2500]
    cau = [line for line in lines if _CAU_HIEU_LUC.search(line)]
    hieu_luc = '\n'.join([c for c in cau if _CAU_HIEU_LUC_CHINH.search(c)] +
                         [c for c in cau if not _CAU_HIEU_LUC_CHINH.search(c)])[:4000]
    resp = client.responses.create(
        model=CHAT_MODEL,
        reasoning={"effort": "low"},
        input=[
            {"role": "system", "content": "Trích xuất thông tin của văn bản quy phạm pháp luật Việt Nam từ "
                                          "phần đầu văn bản và các câu nói về hiệu lực. Chỉ dựa vào nội dung "
                                          "được cung cấp, không suy đoán."},
            {"role": "user", "content": f"PHẦN ĐẦU VĂN BẢN:\n{dau}\n\nCÁC CÂU VỀ HIỆU LỰC:\n{hieu_luc}"},
        ],
        text={"format": {"type": "json_schema", "name": "thong_tin_van_ban",
                         "schema": _META_SCHEMA, "strict": True}},
    )
    meta = json.loads(resp.output_text)
    for key in ('ngay_ban_hanh', 'hieu_luc_tu'):
        try:
            meta[key] = date.fromisoformat(meta[key]).isoformat()
        except (TypeError, ValueError):
            meta[key] = None
    return meta


# ==================== VECTOR NGỮ NGHĨA ====================
def ma_hoa_vector(vector):
    """Lưu gọn vector dạng float16 + base64 (~2,7KB/đoạn thay vì ~20KB nếu ghi số thập phân)."""
    return base64.b64encode(struct.pack(f'<{len(vector)}e', *vector)).decode('ascii')


def tinh_vector(client, texts):
    vectors = []
    for i in range(0, len(texts), EMBEDDING_BATCH):
        resp = client.embeddings.create(model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS,
                                        input=texts[i:i + EMBEDDING_BATCH])
        vectors.extend(item.embedding for item in resp.data)
    return vectors


# ==================== ĐỒNG BỘ ====================
def load_index():
    """Đọc kho đã xây. Trả về dict rỗng nếu chưa đồng bộ lần nào."""
    try:
        with open(PDF_INDEX_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_index(index):
    # Ghi ra file tạm rồi đổi tên: server đang chạy không bao giờ đọc phải file ghi dở.
    tmp = PDF_INDEX_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PDF_INDEX_FILE)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def chia_muc(text):
    """
    Chia file .md tự soạn theo các mục "## ...", mỗi mục chia tiếp thành đoạn
    ngắn (tiêu đề mục lặp lại ở đầu mỗi đoạn). Trả về (tên tài liệu, khối) với
    khối cùng dạng chia_doan.
    """
    lines = [line.strip() for line in re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL).splitlines()]
    lines = [line for line in lines if line]
    ten = next((line[2:].strip() for line in lines if line.startswith('# ')), None)
    blocks, current = [], []
    for line in lines:
        if line.startswith('# '):
            continue
        if line.startswith('##') and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    return ten, [('\n'.join(block), ['\n'.join(block)] if len('\n'.join(block)) <= DOAN_MAX_CHARS
                  else _chia_nhom(block, [], [])) for block in blocks]


def xu_ly_file(client, path):
    """Đọc, lấy thông tin, chia đoạn, tính vector cho một file tài liệu. Lỗi -> ValueError."""
    text = doc_chu(path)
    if path.lower().endswith('.md'):
        ten, blocks = chia_muc(text)
        if not ten or not blocks:
            raise ValueError('file .md cần một dòng tiêu đề "# ..." và ít nhất một mục nội dung')
        # Thông tin tự soạn: không có số hiệu/hiệu lực, luôn được dùng (xem tailieu.tinh_trang).
        meta = {'loai': 'thong_tin', 'so_hieu': None, 'ten': ten, 'ngay_ban_hanh': None,
                'hieu_luc_tu': None, 'thay_the': []}
    else:
        if len(text.strip()) < 200:
            raise ValueError("file không có chữ (PDF dạng ảnh scan?) - cần bản có chữ: PDF đã OCR, .docx hoặc .txt")
        meta = doc_thong_tin(client, text)
        blocks = chia_doan(text)
    # Gắn tên văn bản vào trước mỗi đoạn khi tính vector, để câu hỏi nhắc tới chủ
    # đề/số hiệu văn bản (VD "chính sách dân số", "Nghị quyết 32") vẫn tìm đúng đoạn.
    vectors = iter(tinh_vector(client, [f"{meta['ten']}\n{chunk}" for _, chunks in blocks for chunk in chunks]))
    dieu = []
    for block_text, chunks in blocks:
        if len(block_text) <= DIEU_MAX_CHARS:  # AI đọc cả khối -> không cần lưu chữ từng đoạn
            dieu.append({'text': block_text, 'doan': [{'vector': ma_hoa_vector(next(vectors))} for _ in chunks]})
        else:
            dieu.append({'doan': [{'text': chunk, 'vector': ma_hoa_vector(next(vectors))} for chunk in chunks]})
    return {**meta, 'dieu': dieu}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if not OPENAI_API_KEY:
        print("❌ Chưa cấu hình OPENAI_API_KEY trong file .env")
        return 1

    os.makedirs(PDF_DIR, exist_ok=True)
    client = OpenAI(api_key=OPENAI_API_KEY)
    old = load_index()
    # Đổi model/số chiều embedding thì vector cũ không so được với vector mới -> làm lại hết.
    same_model = (old.get('embedding_model'), old.get('dimensions')) == (EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)
    old_files = old.get('files', {}) if same_model else {}
    index = {'embedding_model': EMBEDDING_MODEL, 'dimensions': EMBEDDING_DIMENSIONS, 'files': {}}

    local = {name: _sha256(os.path.join(PDF_DIR, name))
             for name in sorted(os.listdir(PDF_DIR))
             if name.lower().endswith(DUOI_FILE) and not name.startswith('_')
             and os.path.isfile(os.path.join(PDF_DIR, name))}
    removed = [n for n in old_files if n not in local]
    added = changed = unchanged = 0
    failed = []
    for name, sha in local.items():
        entry = old_files.get(name)
        if entry and entry.get('sha256') == sha and entry.get('dieu'):
            index['files'][name] = entry
            unchanged += 1
            continue
        print(f"⏳ Đang xử lý: {name} ...", flush=True)
        try:
            data = xu_ly_file(client, os.path.join(PDF_DIR, name))
        except Exception as e:
            failed.append(name)
            print(f"❌ Lỗi xử lý: {name} - {e}")
            continue
        index['files'][name] = {'sha256': sha, **data}
        if entry:
            changed += 1
        else:
            added += 1
        loai = 'thông tin tự soạn' if data.get('loai') == 'thong_tin' else f"hiệu lực từ {data['hieu_luc_tu'] or '?'}"
        print(f"✅ {'Đã cập nhật' if entry else 'Đã thêm'}: {name} - {data['ten'][:90]} "
              f"({loai}, {sum(len(d['doan']) for d in data['dieu'])} đoạn)")
    for name in removed:
        print(f"🗑️  Đã xóa: {name}")
    _save_index(index)

    print(f"\nHoàn tất: {added} mới, {changed} cập nhật, {len(removed)} xóa, {unchanged} không đổi, "
          f"{len(failed)} lỗi. Tổng số tài liệu: {len(index['files'])}.")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
