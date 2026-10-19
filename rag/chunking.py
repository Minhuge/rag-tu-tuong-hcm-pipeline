"""
Chunking v2 (xem CHUNKING_PLAN.md) — phần A và B:

  A. load_full_text   PDF → một chuỗi toàn văn (giữ nguyên dòng) + PageMap (offset ký tự → số trang)
  B. detect_sections  nhận diện tiêu đề (Chương > I > 1 > a) → danh sách Section liên tục, không mất chữ

Đọc PDF bằng PyMuPDF thay vì PyPDFLoader: pypdf ở chế độ mặc định chèn \\n giữa từng glyph (không còn biết đâu
là xuống dòng thật), còn chế độ layout thì trả về trang trống ở ~40/119 trang của giáo trình. Nhận diện tiêu đề
cần dòng thật, nên ở đây không dùng clean_text của scripts/ingest.py (hàm đó xoá hết \\n).

Kiểm tra bằng mắt:  python -m rag.chunking /Users/pmhieu7/Documents/GitHub/rag-tu-tuong-hcm-pipeline/data/Giao_trinh_Tu_tuong_HCM.pdf   → in path_str của mọi mục để đối chiếu mục lục.
"""
import bisect
import re
import unicodedata
from dataclasses import dataclass

# Cùng danh sách watermark với scripts/ingest.py, nhưng cắt CHUỖI chứ không bỏ cả dòng: PyMuPDF đôi khi dính
# watermark vào dòng chữ thật ("KẾT LUẬNwww.onthisv.com").
NOISE_RE = re.compile(
    r"www\.onthisv\.com|Downloaded by .*?gmail\.com\)?|Scan to open on Studeersnel|studeersnel"
    r"|lOMoARcPSD\|?\d+",
    re.IGNORECASE,
)
PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")


@dataclass
class PageMap:
    starts: list[int]   # offset bắt đầu của từng trang trong full_text
    pages: list[int]    # số trang tương ứng (1-based, trùng số in trên giáo trình)

    def page_at(self, offset: int) -> int:
        i = bisect.bisect_right(self.starts, offset) - 1
        return self.pages[max(i, 0)]


def clean_page(text: str) -> str:
    """Bỏ watermark và số trang, gộp khoảng trắng trong từng dòng, bỏ dòng trống. Giữ nguyên các dòng."""
    lines = []
    for line in unicodedata.normalize("NFC", text).split("\n"):
        line = re.sub(r"\s+", " ", NOISE_RE.sub("", line)).strip()
        if line and not PAGE_NUMBER_RE.match(line):
            lines.append(line)
    return "\n".join(lines)


def load_full_text(pdf_path: str) -> tuple[str, PageMap]:
    import fitz   # PyMuPDF

    parts, starts, pages, pos = [], [], [], 0
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            text = clean_page(page.get_text(sort=True))
            if not text:
                continue
            text += "\n"
            # Số trang = chỉ số PDF + 1, giống field "page" cũ trong ingest.py → trích dẫn không bị lệch.
            starts.append(pos)
            pages.append(i + 1)
            parts.append(text)
            pos += len(text)
    return "".join(parts), PageMap(starts, pages)


# --- B. Nhận diện tiêu đề -------------------------------------------------------------------------------------

# Cấp 0. Tiêu đề chương luôn VIẾT HOA trên dòng riêng ("CHƯƠNG III"), tên chương ở các dòng viết hoa kế tiếp.
# Phân biệt hoa/thường để câu "như chương II đã trình bày" không bị nhận nhầm.
CHAPTER_RE = re.compile(r"^CH[UƯ]ƠNG\s+(MỞ\s*ĐẦU|[IVX]+)\b\s*[.:–-]?\s*(.*)$")
# Cấp 1: "I. CƠ SỞ…", "I.TƯ TƯỞNG…" (có chỗ thiếu dấu cách sau dấu chấm). "KẾT LUẬN" cuối chương cũng là cấp 1:
# bên dưới nó có các mục "1.", "2." riêng.
ROMAN_RE = re.compile(r"^([IVX]+)\s*\.\s*(\S.*)$")
CONCLUSION_RE = re.compile(r"^KẾT LUẬN(?: CHƯƠNG\b.*)?$")
# Cấp 2: "1. Cơ sở khách quan", "1.Khái niệm…". Tối đa 2 chữ số để dòng "1930. Đây chính là…" không khớp.
NUMBER_RE = re.compile(r"^(\d{1,2})\s*\.\s*(\S.*)$")
# Cấp 3: giáo trình dùng "a." (kế hoạch ghi "a)"), nhận cả hai.
LETTER_RE = re.compile(r"^([a-zđ])\s*[.)]\s*(\S.*)$")
# Thứ tự chữ cái đánh mục tiếng Việt; giáo trình có chỗ nhảy d → e (bỏ đ), nên cho phép bỏ qua một chữ.
LETTERS = "abcdđeghiklmnopqrstuvxy"

MAX_HEADING_LEN = 150
END_PUNCT = (".", ";", ",", ":")
QUOTES = "\"'“”’»)"
ROMAN = {"I": 1, "V": 5, "X": 10}


def roman_to_int(roman: str) -> int:
    total = 0
    for ch, nxt in zip(roman, roman[1:] + " "):
        value = ROMAN[ch]
        total += -value if nxt in ROMAN and ROMAN[nxt] > value else value
    return total


@dataclass
class Section:
    path: list[str]          # ["Chương II: …", "I. TƯ TƯỞNG …", "1. Vấn đề dân tộc thuộc địa"]
    chapter: str             # "Chương II" / "Chương Mở đầu" — cùng dạng field "chapter" cũ
    chapter_no: int | None   # 0 = Mở đầu, None = trước chương đầu tiên
    start: int               # offset trong full_text (sau dòng tiêu đề)
    end: int
    text: str                # == full_text[start:end]

    @property
    def path_str(self) -> str:
        return " > ".join(self.path)


@dataclass
class _Heading:
    level: int
    label: str        # chuỗi đưa vào path
    key: int | None   # số thứ tự để kiểm tra liên tiếp (I=1, 1, a=0…); None = không kiểm tra
    start: int        # offset đầu dòng tiêu đề
    body_start: int   # offset ngay sau dòng tiêu đề cuối cùng (tiêu đề có thể nhiều dòng)
    chapter: str | None = None
    chapter_no: int | None = None


def _is_upper(s: str) -> bool:
    return any(c.isalpha() for c in s) and s == s.upper()


def _looks_like_heading_start(s: str) -> bool:
    return bool(CHAPTER_RE.match(s) or ROMAN_RE.match(s) or CONCLUSION_RE.match(s)
                or NUMBER_RE.match(s) or LETTER_RE.match(s))


def _match_heading(s: str):
    """Dòng → (cấp, số thứ tự, nhãn, tên chương, số chương) nếu có dạng tiêu đề, ngược lại None."""
    if m := CHAPTER_RE.match(s):
        token = m.group(1)
        if token.startswith("MỞ"):
            return 0, None, m.group(2), "Chương Mở đầu", 0
        return 0, None, m.group(2), "Chương " + token, roman_to_int(token)
    if CONCLUSION_RE.match(s):
        return 1, None, s, None, None
    if m := ROMAN_RE.match(s):
        return 1, roman_to_int(m.group(1)), m.group(2), m.group(1), None
    if m := NUMBER_RE.match(s):
        return 2, int(m.group(1)), m.group(2), m.group(1), None
    if m := LETTER_RE.match(s):
        return 3, LETTERS.index(m.group(1)), m.group(2), m.group(1), None
    return None


def _find_headings(full_text: str) -> list[_Heading]:
    lines, pos = [], 0   # (offset đầu dòng, offset sau \n, nội dung)
    for raw in full_text.splitlines(keepends=True):
        lines.append((pos, pos + len(raw), raw.strip()))
        pos += len(raw)

    headings: list[_Heading] = []
    cur_level = -1        # cấp của tiêu đề gần nhất
    last_key = {1: 0, 2: 0, 3: -1}
    i = 0
    while i < len(lines):
        start, end, s = lines[i]
        matched = _match_heading(s) if len(s) <= MAX_HEADING_LEN else None
        if matched is None:
            i += 1
            continue
        level, key, title, tag, chapter_no = matched

        # Tiêu đề dài bị xuống dòng.
        j = i + 1
        if level <= 1:
            # Cấp 0–1 viết hoa: nối các dòng viết hoa kế tiếp (tên chương có thể vắt sang trang sau).
            while (j < len(lines) and _is_upper(lines[j][2]) and not _looks_like_heading_start(lines[j][2])
                   and len(title) + len(lines[j][2]) <= MAX_HEADING_LEN):
                title = f"{title} {lines[j][2]}".strip()
                j += 1
        elif (j < len(lines) and len(s) >= 70 and lines[j][2][:1].islower()
              and not _looks_like_heading_start(lines[j][2])):
            # Cấp 2–3: dòng tiêu đề gần hết khổ và dòng sau viết thường → phần còn lại của tiêu đề
            # (đoạn văn sau tiêu đề luôn bắt đầu bằng chữ hoa).
            title = f"{title} {lines[j][2]}"
            j += 1

        if level > 0 and not _valid_heading(title, level, key, cur_level, last_key):
            i += 1
            continue

        if level == 0:
            label = f"{tag}: {title}" if title else tag
            chapter, chapter_no_ = tag, chapter_no
            last_key = {1: 0, 2: 0, 3: -1}
        else:
            label = title if key is None else f"{tag}. {title}"
            chapter, chapter_no_ = None, None
            if key is not None:
                last_key[level] = key
            # Mục mới ở cấp này → đánh số lại từ đầu cho các cấp con.
            if level <= 1:
                last_key[2] = 0
            if level <= 2:
                last_key[3] = -1
        headings.append(_Heading(level, label, key, start, lines[j - 1][1], chapter, chapter_no_))
        cur_level = level
        i = j
    return headings


def _valid_heading(title: str, level: int, key: int | None, cur_level: int, last_key: dict) -> bool:
    """Bộ lọc chống nhận nhầm danh sách đánh số trong đoạn văn."""
    if len(title) > MAX_HEADING_LEN or title.rstrip(QUOTES).endswith(END_PUNCT):
        return False
    if not title[0].isupper():
        return False
    # Không nhảy cấp (đang ở cấp 1 thì không xuống thẳng cấp 3), trừ khi đó là mục đầu tiên của danh sách
    # ("a." ngay dưới "KẾT LUẬN" ở chương VI — giáo trình bỏ cấp "1."); các mục sau đi theo thứ tự như thường.
    if level > cur_level + 1 and key != (0 if level == 3 else 1):
        return False
    if key is None:
        return True
    if level == 3:                     # a → b, hoặc bỏ qua một chữ (d → e)
        return key in (last_key[3] + 1, last_key[3] + 2)
    return key == last_key[level] + 1  # I → II, 1 → 2: phải liên tiếp


def detect_sections(full_text: str) -> list[Section]:
    """
    Mỗi Section là đoạn văn liên tục giữa hai tiêu đề liên tiếp; lời dẫn giữa tiêu đề cha và tiêu đề con đầu
    tiên là một Section riêng với path dừng ở cấp cha. Mục rỗng (tiêu đề nối tiếp tiêu đề) bị bỏ qua.
    Phần chữ trước tiêu đề đầu tiên (nếu có) thành Section có path rỗng.
    """
    headings = _find_headings(full_text)
    sections: list[Section] = []

    def add(path, chapter, chapter_no, start, end):
        body = full_text[start:end]
        lead = len(body) - len(body.lstrip())
        start, end = start + lead, start + len(body.rstrip())
        if start < end:
            sections.append(Section(list(path), chapter, chapter_no, start, end, full_text[start:end]))

    first = headings[0].start if headings else len(full_text)
    add([], "Chưa xác định", None, 0, first)

    stack: list[str] = []   # nhãn theo cấp, stack[k] = tiêu đề cấp k
    chapter, chapter_no = "Chưa xác định", None
    for h, nxt in zip(headings, headings[1:] + [None]):
        if h.level == 0:
            chapter, chapter_no = h.chapter, h.chapter_no
        del stack[h.level:]
        stack.extend([""] * (h.level - len(stack)))   # cấp bị bỏ qua (xem _valid_heading)
        stack.append(h.label)
        add([t for t in stack if t], chapter, chapter_no, h.body_start, nxt.start if nxt else len(full_text))
    return sections


if __name__ == "__main__":
    import os
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pdf = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "data", "Giao_trinh_Tu_tuong_HCM.pdf")
    full_text, page_map = load_full_text(pdf)
    sections = detect_sections(full_text)
    for sec in sections:
        print(f"{len(sec.text):>6}  p{page_map.page_at(sec.start):>3}  {sec.path_str}")
    print(f"\nSố mục: {len(sections)}   tổng ký tự trong mục: {sum(len(s.text) for s in sections):,}"
          f"   toàn văn: {len(full_text):,}")

    import numpy as np
    lens = np.array([len(s.text) for s in sections])
    print("\nPhân vị độ dài mục:")
    for p in [10, 25, 50, 75, 90, 95]:
        print(f"  p{p:<3}{int(np.percentile(lens, p)):>7}")
    print(f"  max {lens.max():>7}  ({sections[lens.argmax()].path_str[-80:]})")
    print("\nmax_size | % mục giữ nguyên | số mục phải cắt")
    for m in [600, 800, 1000, 1200, 1500, 1800, 2000]:
        print(f"{m:>8} | {(lens <= m).mean():>15.0%} | {(lens > m).sum():>14}")


        covered = bytearray(len(full_text))
    for s in sections:
        covered[s.start:s.end] = b"\x01" * (s.end - s.start)

    gaps, i = [], 0
    while i < len(full_text):
        if not covered[i]:
            j = i
            while j < len(full_text) and not covered[j]:
                j += 1
            gaps.append(full_text[i:j])
            i = j
        else:
            i += 1

    non_ws = [g.strip() for g in gaps if g.strip()]
    print(f"\nKý tự ngoài mục: {sum(map(len, gaps)):,}  "
          f"(trong đó khoảng trắng/\\n: {sum(len(g) - len(g.strip()) for g in gaps):,})")
    print(f"Số đoạn có chữ nằm ngoài mục: {len(non_ws)}")
    for g in non_ws:
        print("  |", g.replace("\n", " ⏎ ")[:150])    