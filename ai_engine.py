"""
Lớp tích hợp AI (OpenAI): chatbot trả lời bằng cách TRA CỨU WEB THẬT (công cụ
web_search tích hợp sẵn của OpenAI, qua Responses API) thay vì chỉ dựa vào
kiến thức huấn luyện sẵn có của model - để tìm đúng văn bản pháp lý/thủ tục
hành chính hiện hành. Việc tra cứu chỉ dùng để ĐẢM BẢO ĐỘ CHÍNH XÁC nội bộ;
câu trả lời cuối cùng CHỈ gồm đúng nội dung trọng tâm (trình tự thực hiện,
thành phần hồ sơ, căn cứ pháp lý) - không hiển thị đường link nguồn ra cho
người dùng (theo yêu cầu).

Đã xác minh trực tiếp từ tài liệu OpenAI (10/2026, không suy đoán từ trí nhớ
vì dòng model đã đổi nhiều lần từ đầu năm): model dùng là "gpt-6-luna" (rẻ
nhất dòng GPT-6, hỗ trợ web_search), gọi qua client.responses.create(...) với
tools=[{"type": "web_search"}]. Giá: ~$0.10/$0.50 mỗi 1 triệu token input/
output, cộng $10/1.000 lượt tra cứu (~0,01 USD/lần tra cứu).

Đánh đổi vẫn giữ nguyên như trước: không còn dữ liệu riêng (file .txt) của
phường, nên với chi tiết đặc thù của phường Minh Phụng (giờ làm việc, mẫu đơn
cụ thể...) mà tra cứu web cũng không chắc chắn, AI vẫn phải khuyên người dân
xác minh trực tiếp tại nơi tiếp nhận thay vì khẳng định bừa.

Nguồn tra cứu thứ hai: tài liệu PDF do phường tự tải lên (thư mục
thutuc_data/, đồng bộ bằng sync_pdf.py lên vector store của OpenAI), tra cứu
bằng công cụ file_search. Khi đã có tài liệu, AI ưu tiên PDF trước, chỉ tra
cứu web để bổ sung phần PDF không có; mâu thuẫn thì theo PDF.

Nguyên tắc quan trọng: stream_answer() PHẢI tự bắt lỗi và không trả về gì khi
OpenAI không khả dụng (mất mạng, sai key, hết quota...), để app.py có thể
fallback sang một câu trả lời xin lỗi + hướng dẫn liên hệ trực tiếp. Chatbot
không được phép "sập" chỉ vì AI lỗi.
"""
import re
import time

from openai import OpenAI

from config import OPENAI_API_KEY, CHAT_MODEL, WARD_OFFICE_ADDRESS
from sync_pdf import load_index

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ==================== TỐI ƯU TỐC ĐỘ ====================
# Đo thực tế (10/2026, câu "Thủ tục đăng ký khai sinh cần những gì?"): cấu hình
# mặc định (reasoning medium, không giới hạn nguồn) mất ~18,6s vì model tra
# web tới 4 vòng; với 3 thiết lập dưới đây còn ~8,3s, nội dung vẫn đủ 3 mục.
# Lưu ý: effort "minimal" nhanh hơn nữa nhưng OpenAI không cho dùng kèm web_search.
REASONING_EFFORT = "low"
# Chỉ tra cứu trong các trang chính thống (đúng các nguồn system prompt yêu
# cầu) để model không phải lục tìm nhiều vòng trên toàn bộ web.
ALLOWED_DOMAINS = ["dichvucong.gov.vn", "thuvienphapluat.vn", "chinhphu.vn", "moj.gov.vn"]
WEB_SEARCH_TOOL = {
    "type": "web_search",
    "search_context_size": "low",
    "filters": {"allowed_domains": ALLOWED_DOMAINS},
    "user_location": {"type": "approximate", "country": "VN"},
}

# Bộ nhớ đệm câu trả lời: người dân thường hỏi lặp lại cùng một thủ tục (khai
# sinh, tạm trú...), nên câu hỏi giống hệt sẽ trả lời ngay thay vì gọi lại AI.
# Chỉ áp dụng cho câu hỏi ĐẦU TIÊN (không có lịch sử), vì câu hỏi nối tiếp
# kiểu "vậy còn phí thì sao" phụ thuộc ngữ cảnh. Lưu trong RAM của tiến
# trình server (gunicorn chạy 1 worker nên mọi người dùng chung một bộ đệm),
# mất khi server khởi động lại - chấp nhận được.
CACHE_TTL_SECONDS = 24 * 3600
CACHE_MAX_ITEMS = 500
_answer_cache = {}


def _cache_key(question, vs_id):
    # Gắn kèm vs_id để khi đồng bộ tài liệu PDF mới thì không dùng lại câu trả lời cũ.
    # Bỏ khác biệt hoa/thường, khoảng trắng thừa và dấu câu cuối câu để
    # "Thủ tục khai sinh?" và "thủ tục khai sinh" dùng chung một câu trả lời.
    return (vs_id, ' '.join(question.lower().split()).rstrip(' ?.!'))


def _cache_get(key):
    item = _answer_cache.get(key)
    if item and time.time() - item[0] < CACHE_TTL_SECONDS:
        return item[1]
    return None


def _cache_set(key, answer):
    if len(_answer_cache) >= CACHE_MAX_ITEMS:
        _answer_cache.pop(next(iter(_answer_cache)), None)  # bỏ mục cũ nhất
    _answer_cache[key] = (time.time(), answer)

# Khi dùng công cụ web_search, model có xu hướng TỰ ĐỘNG chèn trích dẫn dạng
# "([domain.vn](https://...))" ngay sau câu - đây là hành vi mặc định của
# chính model khi tra cứu web, lời nhắc trong prompt (dù đã yêu cầu rõ) không
# chặn được 100%. Nên phải lọc bỏ bằng code để đảm bảo chắc chắn không hiển
# thị link cho người dùng, đúng yêu cầu.
_MARKDOWN_LINK_IN_PARENS = re.compile(r'\s*\(\[[^\]]+\]\(https?://[^)\s]+\)\)')
_MARKDOWN_LINK = re.compile(r'\s*\[[^\]]+\]\(https?://[^)\s]+\)')
_BARE_URL = re.compile(r'\s*https?://\S+')
_EMPTY_PARENS = re.compile(r'\(\s*\)')
# Dấu trích dẫn kiểu "【4:0†ten_file.pdf】" mà model đôi khi chèn khi dùng file_search.
_FILE_CITATION = re.compile(r'【[^】]*】')


def _strip_links_line(line):
    # Lọc theo TỪNG DÒNG để dùng được khi stream: link/trích dẫn không bao giờ
    # nằm vắt qua 2 dòng, nên lọc xong dòng nào gửi ngay dòng đó cho người dùng.
    line = _FILE_CITATION.sub('', line)
    line = _MARKDOWN_LINK_IN_PARENS.sub('', line)
    line = _MARKDOWN_LINK.sub('', line)
    line = _BARE_URL.sub('', line)
    line = _EMPTY_PARENS.sub('', line)
    line = re.sub(r'[ \t]{2,}', ' ', line)
    return line.rstrip()


def _strip_links(text):
    return '\n'.join(_strip_links_line(line) for line in text.split('\n')).strip()

_WEB_SOURCE_RULE = """ƯU TIÊN TUYỆT ĐỐI tra cứu tại https://dichvucong.gov.vn/ (Cổng Dịch vụ công Quốc gia) trước
   tiên - đây là nguồn chính thống, có cấu trúc đúng 3 mục trên cho từng thủ tục. Chỉ tra cứu thêm
   nguồn khác (thuvienphapluat.vn, chinhphu.vn, congbao.chinhphu.vn) khi dichvucong.gov.vn không
   có đủ thông tin."""

# Chỉ dùng khi đã đồng bộ ít nhất 1 tài liệu PDF (xem sync_pdf.py).
_PDF_SOURCE_RULE = """LUÔN tra cứu TÀI LIỆU NỘI BỘ của phường (công cụ file_search) TRƯỚC TIÊN - đây là văn
   bản chính thức do phường cung cấp, là nguồn ưu tiên cao nhất. Chỉ dùng web_search để bổ sung
   phần tài liệu nội bộ không có; khi tra web thì ưu tiên https://dichvucong.gov.vn/ trước, sau đó
   mới tới thuvienphapluat.vn, chinhphu.vn, congbao.chinhphu.vn.
   Nếu tài liệu nội bộ và kết quả web MÂU THUẪN nhau, trả lời theo tài liệu nội bộ.
   Không nhắc tên file PDF hay cụm từ "tài liệu nội bộ" trong câu trả lời - chỉ nêu tên/số hiệu
   văn bản khi cần."""

_SYSTEM_PROMPT_TEMPLATE = f"""Bạn là trợ lý ảo AI của Trung tâm phục vụ hành chính công phường Minh Phụng.

Bạn CÓ {{tools_desc}}. Khi người dùng hỏi về một thủ tục hành chính nói chung,
hãy tra cứu rồi trả lời NGẮN GỌN, CHỈ gồm đúng 3 mục sau (bỏ mục nào không tìm được, không thêm
mục nào khác):

🔰 Trình tự thực hiện
📋 Thành phần hồ sơ
⚖️ Căn cứ pháp lý

QUY TẮC BẮT BUỘC:
1. {{source_rule}}
2. ĐI THẲNG VÀO NỘI DUNG: không chào hỏi lại, không lặp lại câu hỏi, không mở đầu dài dòng kiểu
   "Nếu bạn hỏi về...", không thêm lời khuyên/diễn giải ngoài 3 mục trên. Mỗi mục trình bày bằng
   gạch đầu dòng thật ngắn, không viết thành đoạn văn dài.
3. Mục "Căn cứ pháp lý" phải nêu rõ tên và số hiệu văn bản (luật/nghị định/thông tư/nghị quyết).
   KHÔNG chèn đường link/URL nào vào câu trả lời (kể cả dạng markdown link) - chỉ nêu tên văn bản.
4. Nếu người dùng hỏi một chi tiết KHÁC ngoài 3 mục trên (lệ phí, thời gian giải quyết, đối tượng
   áp dụng, nơi nộp...), vẫn tra cứu và trả lời thẳng, ngắn gọn vào đúng câu hỏi đó - không cần
   nhắc lại 3 mục mặc định.
5. Với chi tiết CÓ THỂ khác nhau theo từng phường mà tra cứu cũng không ra kết quả chắc chắn riêng
   cho phường Minh Phụng (VD: giờ làm việc, mẫu đơn riêng của phường), nói rõ đây là quy định
   chung và khuyên xác minh tại {WARD_OFFICE_ADDRESS}. KHÔNG tự bịa số liệu/quy định của phường.
6. Nếu người dùng hỏi nơi nộp hồ sơ/liên hệ trực tiếp, cung cấp địa chỉ: {WARD_OFFICE_ADDRESS}.
7. Câu hỏi không liên quan thủ tục hành chính: từ chối lịch sự bằng 1 câu, không cần tra cứu.
8. Dùng lịch sử hội thoại để hiểu câu hỏi nối tiếp (ví dụ "vậy còn phí thì sao").
9. Xưng "tôi", gọi người dùng là "bạn".
"""

SYSTEM_PROMPT = _SYSTEM_PROMPT_TEMPLATE.format(
    tools_desc="công cụ tra cứu web (web_search)", source_rule=_WEB_SOURCE_RULE)
SYSTEM_PROMPT_WITH_PDF = _SYSTEM_PROMPT_TEMPLATE.format(
    tools_desc="2 công cụ tra cứu: tài liệu nội bộ của phường (file_search) và web (web_search)",
    source_rule=_PDF_SOURCE_RULE)


def _pdf_vector_store_id():
    """
    ID vector store chứa tài liệu PDF, hoặc None nếu chưa đồng bộ tài liệu nào.
    Đọc lại pdf_index.json mỗi lần gọi (file rất nhỏ) để tài liệu mới đồng bộ
    có hiệu lực ngay, không cần khởi động lại server.
    """
    index = load_index()
    return index.get('vector_store_id') if index.get('files') else None


def stream_answer(question, history=None):
    """
    Gọi GPT (kèm công cụ tra cứu web) để trả lời câu hỏi, ưu tiên căn cứ vào
    nguồn thật tìm được trên web thay vì chỉ dựa vào kiến thức sẵn có của
    model. Là generator: trả dần câu trả lời theo TỪNG DÒNG ngay khi model
    viết xong dòng đó, để người dùng thấy chữ sớm hơn ~2-3s so với chờ trọn
    câu trả lời. Không yield gì nếu OpenAI không khả dụng/lỗi ngay từ đầu, để
    app.py tự fallback sang câu trả lời xin lỗi + hướng dẫn liên hệ trực tiếp.
    """
    if not client:
        return

    tools = [WEB_SEARCH_TOOL]
    system_prompt = SYSTEM_PROMPT
    vs_id = _pdf_vector_store_id()
    if vs_id:
        tools.insert(0, {"type": "file_search", "vector_store_ids": [vs_id], "max_num_results": 8})
        system_prompt = SYSTEM_PROMPT_WITH_PDF

    cache_key = None if history else _cache_key(question, vs_id)
    if cache_key:
        cached = _cache_get(cache_key)
        if cached:
            yield cached
            return

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": question})

    raw = ''
    pending = ''  # phần dòng đang viết dở, chưa lọc link được
    completed = False
    try:
        stream = client.responses.create(
            model=CHAT_MODEL,
            reasoning={"effort": REASONING_EFFORT},
            tools=tools,
            input=messages,
            stream=True,
        )
        for event in stream:
            if event.type == "response.output_text.delta":
                raw += event.delta
                pending += event.delta
                *lines, pending = pending.split('\n')
                for line in lines:
                    yield _strip_links_line(line) + '\n'
            elif event.type == "response.completed":
                completed = True
        if pending:
            yield _strip_links_line(pending)
    except Exception as e:
        print(f"⚠️ Lỗi gọi OpenAI Responses API: {e}")
        if '\n' in raw:  # đã gửi ít nhất 1 dòng câu trả lời cho người dùng
            yield '\n\n⚠️ Câu trả lời bị gián đoạn, bạn vui lòng hỏi lại.'
        return

    answer = _strip_links(raw)
    if cache_key and answer and completed:
        _cache_set(cache_key, answer)
