"""
Đồng bộ thư mục thutuc_data/ lên vector store của OpenAI để chatbot tra cứu
nội dung PDF bằng công cụ file_search (xem ai_engine.py).

Cách dùng: chép/xóa/sửa file PDF trong thutuc_data/ rồi chạy
    python sync_pdf.py

Script so sánh mã băm (SHA-256) từng file với lần đồng bộ trước (lưu trong
pdf_index.json) nên chỉ xử lý phần thay đổi:
  - File mới         -> tải lên
  - File đã sửa      -> xóa bản cũ trên OpenAI, tải bản mới lên
  - File đã bị xóa   -> xóa khỏi OpenAI
  - File không đổi   -> bỏ qua
Không cần khởi động lại web server sau khi đồng bộ: ai_engine.py đọc lại
pdf_index.json ở mỗi câu hỏi.
"""
import hashlib
import json
import os
import sys

from openai import OpenAI, NotFoundError

from config import OPENAI_API_KEY, PDF_DIR, PDF_INDEX_FILE

VECTOR_STORE_NAME = "chatbot-hanhchinh-minhphung-tailieu"


def load_index():
    """Đọc trạng thái đồng bộ. Trả về dict rỗng nếu chưa đồng bộ lần nào."""
    try:
        with open(PDF_INDEX_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_index(index):
    with open(PDF_INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _ensure_vector_store(client, index):
    """Dùng lại vector store cũ nếu còn tồn tại, nếu không thì tạo mới."""
    vs_id = index.get('vector_store_id')
    if vs_id:
        try:
            client.vector_stores.retrieve(vs_id)
            return vs_id
        except NotFoundError:
            print(f"⚠️ Vector store {vs_id} không còn trên OpenAI, tạo mới và tải lại toàn bộ.")
    vs = client.vector_stores.create(name=VECTOR_STORE_NAME)
    index.clear()
    index['vector_store_id'] = vs.id
    index['files'] = {}
    _save_index(index)
    print(f"✅ Đã tạo vector store mới: {vs.id}")
    return vs.id


def _remove_remote(client, vs_id, file_id):
    # Gỡ khỏi vector store rồi xóa luôn file gốc, tránh tốn dung lượng lưu trữ.
    for delete in (lambda: client.vector_stores.files.delete(file_id, vector_store_id=vs_id),
                   lambda: client.files.delete(file_id)):
        try:
            delete()
        except NotFoundError:
            pass


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if not OPENAI_API_KEY:
        print("❌ Chưa cấu hình OPENAI_API_KEY trong file .env")
        return 1

    os.makedirs(PDF_DIR, exist_ok=True)
    client = OpenAI(api_key=OPENAI_API_KEY)
    index = load_index()
    vs_id = _ensure_vector_store(client, index)
    synced = index.setdefault('files', {})

    local = {name: _sha256(os.path.join(PDF_DIR, name))
             for name in sorted(os.listdir(PDF_DIR))
             if name.lower().endswith('.pdf') and os.path.isfile(os.path.join(PDF_DIR, name))}

    removed = [n for n in synced if n not in local]
    changed = [n for n in local if n in synced and synced[n]['sha256'] != local[n]]
    added = [n for n in local if n not in synced]
    unchanged = len(local) - len(added) - len(changed)

    for name in removed:
        _remove_remote(client, vs_id, synced.pop(name)['file_id'])
        _save_index(index)
        print(f"🗑️  Đã xóa: {name}")

    failed = []
    for name in changed + added:
        if name in synced:
            _remove_remote(client, vs_id, synced.pop(name)['file_id'])
            _save_index(index)
        print(f"⏳ Đang tải lên: {name} ...", flush=True)
        with open(os.path.join(PDF_DIR, name), 'rb') as f:
            vs_file = client.vector_stores.files.upload_and_poll(
                vector_store_id=vs_id, file=f, attributes={'ten_file': name[:512]})
        if vs_file.status != 'completed':
            # VD: PDF dạng ảnh scan không có lớp chữ -> OpenAI không đọc được nội dung.
            reason = vs_file.last_error.message if vs_file.last_error else vs_file.status
            _remove_remote(client, vs_id, vs_file.id)
            failed.append((name, reason))
            print(f"❌ Lỗi xử lý: {name} - {reason}")
            continue
        synced[name] = {'file_id': vs_file.id, 'sha256': local[name]}
        _save_index(index)
        print(f"✅ {'Đã cập nhật' if name in changed else 'Đã thêm'}: {name}")

    print(f"\nHoàn tất: {len(added)} mới, {len(changed)} cập nhật, {len(removed)} xóa, "
          f"{unchanged} không đổi, {len(failed)} lỗi. Tổng số tài liệu: {len(synced)}.")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
