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

Không còn tra cứu tài liệu PDF nội bộ - bỏ theo yêu cầu 10/2026
để mọi câu hỏi đi thẳng tới AI + tra cứu web, trả lời nhanh và đơn giản hơn.

Nguyên tắc quan trọng: stream_answer() PHẢI tự bắt lỗi và không trả về gì khi
OpenAI không khả dụng (mất mạng, sai key, hết quota...), để app.py có thể
fallback sang một câu trả lời xin lỗi + hướng dẫn liên hệ trực tiếp. Chatbot
không được phép "sập" chỉ vì AI lỗi.
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone

from openai import OpenAI, Timeout

from config import OPENAI_API_KEY, CHAT_MODEL, FALLBACK_CHAT_MODEL, WARD_OFFICE_ADDRESS

# Mặc định thư viện OpenAI chờ tới 10 phút và tự thử lại 2 lần mỗi lượt gọi: lỗi
# mạng/vượt giới hạn có thể khiến người dùng chờ rất lâu. Thử lại 1 lần rồi
# chuyển sang model dự phòng (xem stream_answer) nhanh hơn nhiều.
# Kết nối tới OpenAI bình thường mất <1s, nên chờ kết nối tối đa 5s; khi stream,
# OpenAI gửi sự kiện cách nhau vài giây (kể cả lúc đang tra web), nên 30s không
# nhận được gì coi như kẹt. Trước đây đặt chung 60s: đo trên Render 10/2026 có
# câu hỏi treo >150s không nhận được chữ nào (60s x 2 lần thử x 2 model).
client = OpenAI(api_key=OPENAI_API_KEY, timeout=Timeout(30, connect=5), max_retries=1) if OPENAI_API_KEY else None
if client:
    # Thư viện openai chỉ nạp module "responses" ở lần dùng đầu tiên. Nhiều luồng
    # cùng nạp lần đầu có thể khóa chờ nhau mãi (gặp trên Render 10/2026, Python
    # 3.12: "cannot import name ... from partially initialized module", sau đó
    # mọi câu hỏi đều treo). Nạp sẵn ở đây - lúc server khởi động, chỉ có 1 luồng.
    client.responses

# ==================== TỐI ƯU TỐC ĐỘ ====================
# Đo thực tế (10/2026, câu "Thủ tục đăng ký khai sinh cần những gì?"): cấu hình
# mặc định (reasoning medium, không giới hạn nguồn) mất ~18,6s vì model tra
# web tới 4 vòng; với 3 thiết lập dưới đây còn ~8,3s, nội dung vẫn đủ 3 mục.
# Lưu ý: effort "minimal" nhanh hơn nữa nhưng OpenAI không cho dùng kèm web_search.
REASONING_EFFORT = "low"
# Chỉ tra cứu trong các trang chính thống (đúng các nguồn system prompt yêu
# cầu) để model không phải lục tìm nhiều vòng trên toàn bộ web. Mỗi tên miền
# tính luôn tên miền con (VD hochiminhcity.gov.vn gồm cả dichvucong.hochiminhcity.gov.vn).
ALLOWED_DOMAINS = ["dichvucong.gov.vn", "thuvienphapluat.vn", "chinhphu.vn", "moj.gov.vn",
                   "hochiminhcity.gov.vn", "baohiemxahoi.gov.vn"]
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


def _cache_key(question):
    # Bỏ khác biệt hoa/thường, khoảng trắng thừa và dấu câu cuối câu để
    # "Thủ tục khai sinh?" và "thủ tục khai sinh" dùng chung một câu trả lời.
    return ' '.join(question.lower().split()).rstrip(' ?.!')


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
# Dấu trích dẫn tệp mà model có thể chèn: kiểu cũ "【4:0†ten_file.pdf】" và kiểu
# mới bọc trong ký tự vùng riêng Unicode U+E200..U+E202 (đã gặp thực tế: lọt ra
# màn hình thành "fileciteturn1file2turn1file5"). Lọc cả trường hợp thiếu ký tự
# đóng và ký tự vùng riêng còn sót lại.
_FILE_CITATION = re.compile(
    r'【[^】]*】'
    r'|\ue200[^\ue201]*\ue201'
    r'|[\ue000-\uf8ff]*(?:file)?cite(?:[\ue000-\uf8ff]*turn\d+[a-z]+\d+)+[\ue000-\uf8ff]*'
    r'|[\ue000-\uf8ff]')


def _strip_links_line(line):
    # Lọc theo TỪNG DÒNG để dùng được khi stream: link/trích dẫn không bao giờ
    # nằm vắt qua 2 dòng, nên lọc xong dòng nào gửi ngay dòng đó cho người dùng.
    line = _FILE_CITATION.sub('', line)
    line = _MARKDOWN_LINK_IN_PARENS.sub('', line)
    line = _MARKDOWN_LINK.sub('', line)
    line = _BARE_URL.sub('', line)
    line = _EMPTY_PARENS.sub('', line)
    line = re.sub(r'[ \t]{2,}', ' ', line)
    # Gạch đầu dòng chỉ chứa link (VD "- https://dichvucong.gov.vn") lọc xong còn
    # trơ dấu "-" -> bỏ luôn dấu đó (đã gặp ở cuối câu trả lời, 10/2026).
    return '' if re.fullmatch(r'\s*[-*•+]\s*', line) else line.rstrip()


# ==================== GỢI Ý CÂU HỎI TIẾP THEO ====================
# Model viết thêm một dòng cuối "[GỢI Ý] câu 1 | câu 2 | câu 3" (quy tắc 11 của
# system prompt). Dòng này KHÔNG hiển thị như văn bản mà được tách riêng, gửi
# cho trình duyệt sau ký tự SUGGESTIONS_SEPARATOR (ASCII "record separator",
# không bao giờ có trong văn bản thường) dưới dạng danh sách JSON để hiển thị
# thành nút bấm. Model không viết dòng này thì đơn giản là không có nút gợi ý.
SUGGESTIONS_SEPARATOR = '\x1e'
# Nhận cả "[GỢI Ý]" (đúng định dạng) lẫn dòng mở đầu bằng "Gợi ý:" có dấu "|"
# (model đôi khi quên ngoặc vuông); câu văn thường kiểu "Gợi ý: bạn nên mang
# bản chính..." không có "|" nên vẫn hiển thị bình thường.
_SUGGESTION_MARKER = re.compile(
    r'\[\s*g[ợo]i\s*[ýy]\s*\]\s*:?|^[\s*_]*g[ợo]i\s*[ýy]\s*[*_]*\s*:(?=.*\|)', re.IGNORECASE)
MAX_SUGGESTIONS = 3


def _parse_suggestions(text):
    items = []
    for part in re.split(r'[|\n]', text):
        part = re.sub(r'^\s*(?:[-*•]+|\d+[.)])\s*', '', part)  # gạch đầu dòng / số thứ tự
        part = _strip_links_line(part).strip(' *_"“”')
        if part and len(part) <= 80:
            items.append(part)
    return items[:MAX_SUGGESTIONS]


SYSTEM_PROMPT = f"""Bạn là trợ lý ảo AI của Trung tâm phục vụ hành chính công phường Minh Phụng, TP. Hồ Chí Minh.

Bạn CÓ công cụ tra cứu web (web_search). Khi người dùng hỏi CÁCH LÀM một thủ tục hành chính (làm thế nào, cần
giấy tờ gì), hãy tra cứu rồi trả lời NGẮN GỌN, CHỈ gồm đúng 3 mục sau (mục nào không có thông tin
thì bỏ hẳn, không viết kiểu "chưa xác định"; không thêm mục nào khác):

🔰 Trình tự thực hiện
📋 Thành phần hồ sơ
⚖️ Căn cứ pháp lý

BỐI CẢNH CẦN NHỚ:
- Từ 01/7/2025 chính quyền địa phương chỉ còn 2 cấp: TP. Hồ Chí Minh (cấp tỉnh) và phường/xã
  (cấp xã), KHÔNG còn cấp quận/huyện. Nhiều trang web viết trước thời điểm này vẫn ghi "UBND
  quận/huyện": đó là thông tin cũ - không hướng dẫn người dân đến UBND quận/huyện, phải xác định
  cơ quan có thẩm quyền theo quy định hiện hành.
- Chỉ áp dụng văn bản còn hiệu lực vào ngày hôm nay (ghi ở cuối hướng dẫn này); văn bản đã hết
  hiệu lực hoặc đã bị thay thế thì nói rõ và dùng văn bản mới.
- Trang chatbot có 3 nút trên thanh công cụ: "Tra cứu BHYT" (tra cứu hạn thẻ BHYT trên trang Bảo
  hiểm xã hội Việt Nam), "Mẫu đơn" (kho mẫu đơn, tờ khai của phường), "Khu phố" (thông tin các khu
  phố của phường). Khi câu hỏi liên quan, nhắc người dùng bấm đúng nút đó (chỉ nêu tên nút, không
  ghi link).

QUY TẮC BẮT BUỘC:
1. ƯU TIÊN TUYỆT ĐỐI tra cứu tại https://dichvucong.gov.vn/ (Cổng Dịch vụ công Quốc gia) trước
   tiên - đây là nguồn chính thống, có cấu trúc đúng 3 mục trên cho từng thủ tục. Chỉ tra cứu thêm
   nguồn khác (thuvienphapluat.vn, chinhphu.vn, congbao.chinhphu.vn) khi dichvucong.gov.vn không
   có đủ thông tin. Quy định, mức thu riêng của TP. Hồ Chí Minh (VD: lệ phí do HĐND Thành phố quyết
   định) tra tại hochiminhcity.gov.vn; câu hỏi về BHYT, BHXH tra thêm tại baohiemxahoi.gov.vn.
2. ĐI THẲNG VÀO NỘI DUNG: không chào hỏi lại, không lặp lại câu hỏi, không mở đầu dài dòng kiểu
   "Nếu bạn hỏi về...", không thêm lời khuyên/diễn giải ngoài 3 mục trên (trừ câu hỏi lại ở quy
   tắc 9 và lời nhắc bấm nút trên thanh công cụ). Mỗi mục trình bày bằng gạch đầu dòng thật ngắn,
   không viết thành đoạn văn dài.
3. Mục "Căn cứ pháp lý" phải nêu rõ tên và số hiệu văn bản (luật/nghị định/thông tư/nghị quyết).
   KHÔNG chèn đường link/URL nào vào câu trả lời (kể cả dạng markdown link) - chỉ nêu tên văn bản.
4. Nếu người dùng hỏi một chi tiết KHÁC ngoài 3 mục trên (lệ phí, thời gian giải quyết, đối tượng,
   điều kiện, chế độ/chính sách được hưởng, mức hỗ trợ, nơi nộp...), trả lời thẳng, ngắn gọn vào
   đúng câu hỏi đó bằng gạch đầu dòng - KHÔNG dùng 3 mục mặc định; dòng cuối ghi "Căn cứ: <tên,
   số hiệu văn bản đã dùng để trả lời>".
5. Với chi tiết CÓ THỂ khác nhau theo từng phường mà tra cứu cũng không ra kết quả chắc chắn riêng
   cho phường Minh Phụng (VD: giờ làm việc, mẫu đơn riêng của phường), nói rõ đây là quy định
   chung và khuyên xác minh tại {WARD_OFFICE_ADDRESS}. KHÔNG tự bịa số liệu/quy định của phường.
6. Nếu người dùng hỏi nơi nộp hồ sơ/liên hệ trực tiếp, cung cấp địa chỉ: {WARD_OFFICE_ADDRESS}.
7. Câu hỏi không liên quan thủ tục hành chính: từ chối lịch sự bằng 1 câu, không cần tra cứu.
   Câu hỏi về chính trợ lý (bạn là ai, giúp được gì): giới thiệu ngắn gọn trong 1-2 câu.
8. Dùng lịch sử hội thoại để hiểu câu hỏi nối tiếp (ví dụ "vậy còn phí thì sao").
9. HỎI LẠI KHI THIẾU THÔNG TIN: nếu thủ tục có các trường hợp khác nhau đáng kể (VD: đăng ký khai
   sinh đúng hạn hay quá hạn, có yếu tố nước ngoài hay không; đăng ký tạm trú cho bản thân hay cho
   người thuê trọ) mà câu hỏi chưa cho biết, hãy trả lời cho trường hợp phổ biến nhất rồi kết thúc
   bằng ĐÚNG MỘT câu hỏi ngắn để xác định trường hợp của người dùng. Nếu câu hỏi quá chung chung,
   chưa biết là thủ tục nào (VD: "làm giấy tờ cho con"), chỉ hỏi lại ngắn gọn, không đoán.
10. Xưng "tôi", gọi người dùng là "bạn".
11. DÒNG GỢI Ý: dòng CUỐI CÙNG của câu trả lời luôn có dạng
    [GỢI Ý] <câu 1> | <câu 2> | <câu 3>
    gồm 2-3 câu ngắn (mỗi câu không quá 10 từ) để người dùng bấm hỏi tiếp, viết như lời người hỏi
    (VD: "Lệ phí bao nhiêu?", "Nộp trực tuyến được không?"), không gợi ý điều vừa trả lời. Nếu vừa
    hỏi lại người dùng (quy tắc 9), dòng gợi ý là các phương án trả lời cho câu hỏi đó (VD: "Cho
    bản thân | Cho người thuê trọ"). Không có dòng gợi ý khi từ chối câu hỏi không liên quan.
"""


def _today_vn():
    # Giờ Việt Nam (UTC+7, không có giờ mùa hè) - server Render chạy theo giờ UTC.
    return datetime.now(timezone(timedelta(hours=7))).date()


def _build_messages(question, history, today):
    # Ngày hiện tại đặt CUỐI system prompt để phần đầu (giống hệt nhau ở mọi lần
    # gọi) vẫn được OpenAI cache lại, giúp phản hồi nhanh và rẻ hơn.
    system_content = f"{SYSTEM_PROMPT}\nHôm nay là ngày {today.strftime('%d/%m/%Y')}."
    return [{"role": "system", "content": system_content}, *(history or []),
            {"role": "user", "content": question}]


def stream_answer(question, history=None):
    """
    Gọi GPT (kèm công cụ tra cứu web) để trả lời câu hỏi, ưu tiên căn cứ vào
    nguồn thật tìm được trên web thay vì chỉ dựa vào kiến thức sẵn có của
    model. Là generator: trả dần câu trả lời theo TỪNG DÒNG ngay khi model
    viết xong dòng đó, để người dùng thấy chữ sớm hơn ~2-3s so với chờ trọn
    câu trả lời. Phần tử cuối (nếu có) bắt đầu bằng SUGGESTIONS_SEPARATOR, theo
    sau là danh sách câu hỏi gợi ý dạng JSON. Không yield gì nếu OpenAI không
    khả dụng/lỗi ngay từ đầu, để app.py tự fallback sang câu trả lời xin lỗi +
    hướng dẫn liên hệ trực tiếp.
    """
    if not client:
        return

    cache_key = None if history else _cache_key(question)
    if cache_key:
        cached = _cache_get(cache_key)
        if cached:
            answer, suggestions = cached
            yield answer
            if suggestions:
                yield SUGGESTIONS_SEPARATOR + json.dumps(suggestions, ensure_ascii=False)
            return

    started = time.monotonic()
    messages = _build_messages(question, history, _today_vn())

    shown = []               # các dòng đã gửi cho người dùng
    suggestion_lines = None  # các dòng từ dấu [GỢI Ý] trở đi (không hiển thị)

    def take_line(line):
        """Dòng cần hiển thị (đã lọc link), hoặc None nếu dòng thuộc phần gợi ý."""
        nonlocal suggestion_lines
        if suggestion_lines is not None:
            suggestion_lines.append(line)
            return None
        marker = _SUGGESTION_MARKER.search(line)
        if marker:
            suggestion_lines = [line[marker.end():]]
            line = line[:marker.start()]
            if not line.strip():
                return None
        return _strip_links_line(line)

    completed = False
    t_first = None
    n_web = 0
    # Model chính lỗi trước khi kịp gửi dòng nào (thường do vượt giới hạn token/
    # phút) -> hỏi lại bằng model dự phòng; đã gửi dở thì không hỏi lại được nữa.
    for model in (CHAT_MODEL, FALLBACK_CHAT_MODEL):
        pending = ''  # phần dòng đang viết dở, chưa lọc link được
        suggestion_lines = None
        try:
            stream = client.responses.create(
                model=model,
                reasoning={"effort": REASONING_EFFORT},
                tools=[WEB_SEARCH_TOOL],
                input=messages,
                stream=True,
            )
            for event in stream:
                if event.type == "response.output_text.delta":
                    pending += event.delta
                    *lines, pending = pending.split('\n')
                    for line in lines:
                        text = take_line(line)
                        if text is not None:
                            if t_first is None:
                                t_first = time.monotonic() - started
                            shown.append(text)
                            yield text + '\n'
                elif event.type == "response.completed":
                    completed = True
                    n_web = sum(1 for item in event.response.output if item.type == "web_search_call")
                elif event.type == "response.failed":
                    error = event.response.error
                    raise RuntimeError(error.message if error else "response.failed")
                elif event.type == "error":
                    raise RuntimeError(event.message)
            text = take_line(pending) if pending else None
            if text is not None:
                if t_first is None:
                    t_first = time.monotonic() - started
                shown.append(text)
                yield text
            break
        except Exception as e:
            print(f"⚠️ Lỗi gọi OpenAI Responses API ({model}): {e}")
            if shown:  # đã gửi ít nhất 1 dòng câu trả lời cho người dùng
                yield '\n\n⚠️ Câu trả lời bị gián đoạn, bạn vui lòng hỏi lại.'
                return
    else:
        return  # cả 2 model đều lỗi -> app.py trả câu xin lỗi + hướng dẫn liên hệ

    # Một dòng log cho mỗi câu trả lời (xem trong mục Logs của Render) để theo dõi tốc độ thật.
    first = f"{t_first:.2f}s" if t_first is not None else "-"
    print(f"⏱️ dòng đầu {first} | "
          f"xong {time.monotonic() - started:.2f}s | tra web {n_web} lần | {model}", flush=True)

    suggestions = _parse_suggestions('\n'.join(suggestion_lines or []))
    if suggestions:
        yield SUGGESTIONS_SEPARATOR + json.dumps(suggestions, ensure_ascii=False)
    answer = '\n'.join(shown).strip()
    if cache_key and answer and completed:
        _cache_set(cache_key, (answer, suggestions))
