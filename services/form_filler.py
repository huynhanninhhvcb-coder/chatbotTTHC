"""Điền dữ liệu vào biểu mẫu Word dựa trên nhãn in sẵn trong mẫu.

Các mẫu đơn trong ``templates_word`` là văn bản gốc của phường (dòng chấm,
tab có dấu chấm dẫn, ô vuông để đánh dấu), không chứa ``{{placeholder}}``.
Module này tìm nhãn (vd. "Họ và tên:") rồi thay phần trống ngay sau nhãn bằng
giá trị, nên cán bộ có thể tiếp tục sửa mẫu trong Word mà không phải chèn
biến. Nếu một nhãn không còn trong mẫu, nhãn đó được ghi vào ``missing`` thay
vì làm hỏng cả tệp; bộ test dùng danh sách này để phát hiện mẫu bị đổi chữ.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

from docx.document import Document as DocumentType
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.run import Run

# Ký tự được coi là "phần trống để điền": dấu chấm lửng, dấu chấm, gạch dưới,
# tab (thường kèm chấm dẫn), khoảng trắng và dấu "/" của khung ngày ../../....
BLANK_CHARS = frozenset("…._\t  /")
EMPTY_BOX_CHARS = ("□", "☐")  # □ ☐
CHECKED_BOX_CHAR = "☒"  # ☒
# Ô vuông vẽ bằng font ký hiệu (w:sym): mã ô trống -> mã ô có dấu X cùng font.
SYMBOL_BOXES = {
    ("wingdings", "F0A8"): "F0FD",
    ("wingdings 2", "F0A3"): "F053",
}
_TEXT_TAGS = {qn("w:t"), qn("w:tab"), qn("w:cr"), qn("w:noBreakHyphen"), qn("w:softHyphen"), qn("w:ptab")}
# Dòng ngày ký: "Ngày … tháng … năm 20…", "Ngày      tháng     năm 2026"...
_SIGN_DATE = re.compile(
    r"(ngày)[\s ….]*tháng[\s ….]*năm[\s ]*[….\d]*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Blank:
    """Một chỗ trống cần điền: nhãn đứng trước và giá trị.

    ``overwrite=True`` thay toàn bộ phần sau nhãn (đến nhãn kế tiếp hoặc cuối
    dòng) kể cả chữ, dùng khi mẫu còn sót chữ của một hồ sơ cũ.
    """

    label: str
    value: str
    overwrite: bool = False


def _label_pattern(label: str) -> re.Pattern[str]:
    """Regex tìm nhãn không phân biệt hoa/thường, khoảng trắng thường/không ngắt đều khớp."""
    parts = [re.escape(part) for part in label.split()]
    return re.compile(r"[  ]+".join(parts), re.IGNORECASE)


def _is_texty(child) -> bool:
    if child.tag == qn("w:br"):
        return child.get(qn("w:type")) in (None, "textWrapping")
    return child.tag in _TEXT_TAGS


def _set_run_text(run: Run, text: str) -> None:
    """Đổi chữ của một run mà không làm mất ký hiệu, hình vẽ hay ngắt trang nằm chung run."""
    element = run._r
    children = [child for child in element if child.tag != qn("w:rPr")]
    if all(_is_texty(child) for child in children):
        run.text = text
        return
    texty = [child for child in children if _is_texty(child)]
    insert_at = element.index(texty[0]) if texty else len(element)
    for child in texty:
        element.remove(child)
    holder = OxmlElement("w:r")
    holder.text = text
    for offset, child in enumerate([c for c in holder if c.tag != qn("w:rPr")]):
        element.insert(insert_at + offset, child)


class FormCursor:
    """Duyệt các đoạn văn của mẫu theo đúng thứ tự in, điền dần từ trên xuống.

    Nhiều nhãn lặp lại giữa các mục (vd. "Họ và tên:" của người đề nghị, người
    giám hộ, người nhận tiền), nên mỗi lệnh chỉ tìm từ vị trí con trỏ trở đi.
    """

    def __init__(self, document: DocumentType):
        body = document.element.body
        self._paragraphs = list(body.iter(qn("w:p")))
        self.pos = 0
        self.missing: list[str] = []

    # ---- truy cập văn bản -------------------------------------------------
    def _runs(self, index: int) -> list[Run]:
        paragraph = self._paragraphs[index]
        runs = []
        for element in paragraph.iter(qn("w:r")):
            owner = next(element.iterancestors(qn("w:p")), None)
            if owner is paragraph:
                runs.append(Run(element, None))
        return runs

    def _text(self, index: int) -> str:
        return "".join(run.text for run in self._runs(index))

    def _find(self, label: str, start: int | None = None) -> int | None:
        pattern = _label_pattern(label)
        for index in range(self.pos if start is None else start, len(self._paragraphs)):
            if pattern.search(self._text(index)):
                return index
        return None

    def _replace_span(self, index: int, start: int, end: int, value: str) -> bool:
        """Thay đoạn [start, end) của một đoạn văn, kể cả khi Word chia chữ thành nhiều run."""
        offset = 0
        inserted = False
        for run in self._runs(index):
            text = run.text
            run_start, run_end = offset, offset + len(text)
            offset = run_end
            if not text:
                continue
            if start == end:
                if run_start <= start <= run_end and not inserted:
                    cut = start - run_start
                    _set_run_text(run, text[:cut] + value + text[cut:])
                    return True
                continue
            if run_end <= start or run_start >= end:
                continue
            cut_start = max(start, run_start) - run_start
            cut_end = min(end, run_end) - run_start
            new_text = text[:cut_start] + ("" if inserted else value) + text[cut_end:]
            inserted = True
            if new_text != text:
                _set_run_text(run, new_text)
        return inserted

    # ---- các lệnh điền ----------------------------------------------------
    def seek(self, label: str) -> bool:
        """Di chuyển con trỏ tới đoạn chứa nhãn (vd. tiêu đề mục) mà không điền gì."""
        index = self._find(label)
        if index is None:
            self.missing.append(label)
            return False
        self.pos = index
        return True

    def fill(self, *blanks: Blank | tuple) -> bool:
        """Điền các chỗ trống nằm trên cùng một dòng, theo thứ tự nhãn trong dòng.

        Phần trống của một nhãn kéo dài tới nhãn kế tiếp (hoặc cuối dòng); chỉ
        thay các ký tự trống ở đầu phần đó để giữ lại dấu phẩy, chấm phẩy... của
        mẫu. Giá trị rỗng được bỏ qua để giữ nguyên dòng chấm cho người dân viết tay.
        """
        items = [item if isinstance(item, Blank) else Blank(*item) for item in blanks]
        index = self._find(items[0].label)
        if index is None:
            self.missing.append(items[0].label)
            return False
        text = self._text(index)
        spans: list[tuple[int, int]] = []
        cursor = 0
        for item in items:
            match = _label_pattern(item.label).search(text, cursor)
            if match is None:
                self.missing.append(item.label)
                break
            spans.append((match.start(), match.end()))
            cursor = match.end()

        edits = []
        for position, (item, (_, label_end)) in enumerate(zip(items, spans)):
            if not item.value:
                continue
            has_next = position + 1 < len(spans)
            region_end = spans[position + 1][0] if has_next else len(text)
            blank_end = region_end if item.overwrite else label_end
            while blank_end < region_end and text[blank_end] in BLANK_CHARS:
                blank_end += 1
            blank = text[label_end:blank_end]
            if has_next and blank_end == region_end:
                # Giữ khoảng cách với nhãn kế tiếp; nếu mẫu dùng tab chấm dẫn thì
                # giữ tab để nhãn sau vẫn thẳng cột như bản in gốc.
                suffix = "\t" if "\t" in blank and not item.overwrite else " "
            elif blank_end < len(text) and text[blank_end].isalnum():
                suffix = " "
            else:
                suffix = ""
            edits.append((label_end, blank_end, f" {item.value}{suffix}"))
        for start, end, value in reversed(edits):
            self._replace_span(index, start, end, value)
        self.pos = index + 1
        return True

    @staticmethod
    def _empty_symbol_box(element):
        for sym in element.iter(qn("w:sym")):
            key = ((sym.get(qn("w:font")) or "").casefold(), (sym.get(qn("w:char")) or "").upper())
            if key in SYMBOL_BOXES:
                return sym
        return None

    def _locate_box(self, index: int, option: str):
        """Tìm ô vuông của một lựa chọn, trả về hàm đánh dấu ô đó (hoặc None nếu không có ô).

        Thứ tự tìm: ô □ ngay trước/sau chữ của lựa chọn, ô ký hiệu trong cùng
        dòng, trong cùng hàng bảng, rồi tới dòng tiếp theo nếu lựa chọn in
        tràn sang dòng dưới (vd. "(cán bộ lão thành cách mạng)  ☐").
        """
        text = self._text(index)
        option_pattern = _label_pattern(option).pattern
        box = "([" + "".join(EMPTY_BOX_CHARS) + "])"
        for pattern in (f"{box}[  ]*(?:{option_pattern})", f"(?:{option_pattern})[  ]*{box}"):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                start = match.start(1)
                return lambda: self._replace_span(index, start, start + 1, CHECKED_BOX_CHAR)

        def symbol_marker(sym):
            return lambda: sym.set(qn("w:char"), SYMBOL_BOXES[(sym.get(qn("w:font")).casefold(), sym.get(qn("w:char")).upper())])

        paragraph = self._paragraphs[index]
        row = next(paragraph.iterancestors(qn("w:tr")), None)
        for element in (paragraph, row):
            sym = self._empty_symbol_box(element) if element is not None else None
            if sym is not None:
                return symbol_marker(sym)
        for following in range(index + 1, min(index + 3, len(self._paragraphs))):
            line = self._text(following).strip()
            if not line or line[0] in "-+•" + "".join(EMPTY_BOX_CHARS):
                break
            char_box = re.search(box, line)
            if char_box:
                position = self._text(following).index(char_box.group(1))
                return lambda: self._replace_span(following, position, position + 1, CHECKED_BOX_CHAR)
            sym = self._empty_symbol_box(self._paragraphs[following])
            if sym is not None:
                return symbol_marker(sym)
        return None

    def check(self, option: str, checked: bool = True) -> bool:
        """Đánh dấu X vào ô vuông của một lựa chọn (ô □ trong chữ hoặc ô vẽ bằng font ký hiệu).

        Luôn tìm lựa chọn và ô của nó để phát hiện mẫu bị đổi chữ, nhưng chỉ
        đánh dấu khi ``checked``. Con trỏ dừng ở dòng của lựa chọn để các ô
        cùng dòng (vd. "□ Không   □ Có") vẫn tìm được.
        """
        index = self._find(option)
        if index is None:
            self.missing.append(option)
            return False
        self.pos = index
        mark = self._locate_box(index, option)
        if mark is None:
            self.missing.append(f"ô đánh dấu: {option}")
            return False
        if checked:
            mark()
        return True

    def sign_date(self, signer_label: str, today: date | None = None) -> bool:
        """Ghi ngày hôm nay vào dòng "Ngày … tháng … năm …" phía trên chữ ký người làm đơn.

        Chỉ tìm trong cùng ô bảng với nhãn người ký, để không điền nhầm vào
        phần xác nhận của cơ quan ở ô bên cạnh.
        """
        # Tìm từ đầu tệp: ô chữ ký có thể nằm trước các dòng điền cuối cùng
        # (vd. "Địa chỉ" người khai thay in ngay dưới bảng chữ ký).
        index = self._find(signer_label, start=0)
        if index is None:
            self.missing.append(signer_label)
            return False
        today = today or date.today()
        cell = next(self._paragraphs[index].iterancestors(qn("w:tc")), None)
        for candidate in range(index, max(-1, index - 4), -1):
            if next(self._paragraphs[candidate].iterancestors(qn("w:tc")), None) is not cell:
                break
            text = self._text(candidate)
            match = _SIGN_DATE.search(text)
            if match:
                value = f"{match.group(1)} {today.day:02d} tháng {today.month:02d} năm {today.year}"
                return self._replace_span(candidate, match.start(), match.end(), value)
        self.missing.append(f"ngày ký: {signer_label}")
        return False


def drop_repeated_copy(document: DocumentType, heading: str = "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM") -> bool:
    """Bỏ bản thứ hai (thường là bản mẫu đã điền sẵn) nếu tệp chứa hai lần quốc hiệu.

    Tránh đưa thông tin cá nhân của hồ sơ ví dụ vào đơn của người khác.
    """
    body = document.element.body
    children = [child for child in body if child.tag != qn("w:sectPr")]
    headings = [
        child
        for child in children
        if child.tag == qn("w:p") and "".join(t.text or "" for t in child.iter(qn("w:t"))).strip().upper().startswith(heading)
    ]
    if len(headings) < 2:
        return False
    for child in children[children.index(headings[1]) :]:
        body.remove(child)
    remaining = [child for child in body if child.tag != qn("w:sectPr")]
    if remaining and remaining[-1].tag == qn("w:tbl"):
        # Word yêu cầu thân văn bản kết thúc bằng một đoạn văn, không phải bảng.
        remaining[-1].addnext(OxmlElement("w:p"))
    return True


def date_parts(value: str) -> tuple[str, str, str]:
    """Tách "dd/mm/yyyy" thành (ngày, tháng, năm); trả về chuỗi rỗng nếu không đúng định dạng."""
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", value or "")
    return match.groups() if match else ("", "", "")
