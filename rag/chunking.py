"""
Chunking v2 (xem CHUNKING_PLAN.md):

  A. load_full_text      PDF → một chuỗi toàn văn (giữ nguyên dòng) + PageMap (offset ký tự → số trang)
  B. detect_sections     nhận diện tiêu đề (Chương > I > 1 > a) → danh sách Section liên tục, không mất chữ
  0.5 merge_short_intros gộp lời dẫn ngắn vào mục con ngay sau nó
  1–5 chunk_section      size check → semantic → recursive → gộp mảnh nhỏ
  build_chunks           ghép tất cả: hai lượt (tính semantic_thr, rồi cắt) → chunk + payload + embed_text + thống kê

Đọc PDF bằng PyMuPDF thay vì PyPDFLoader: pypdf ở chế độ mặc định chèn \\n giữa từng glyph (không còn biết đâu
là xuống dòng thật), còn chế độ layout thì trả về trang trống ở ~40/119 trang của giáo trình. Nhận diện tiêu đề
cần dòng thật, nên ở đây không dùng clean_text của scripts/ingest.py (hàm đó xoá hết \\n).

Kiểm tra bằng mắt:
  python -m rag.chunking                                   → path_str mọi mục, phân vị độ dài mục, gap check
  python -m rag.chunking --dry-run 800 1200 1600           → cắt thử (cần Ollama), thống kê chunk, KHÔNG ghi Qdrant
  python -m rag.chunking --dry-run 1200 --strategy recursive
"""
import bisect
import json
import os
import re
import unicodedata
from dataclasses import dataclass

import numpy as np

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


def _is_child(path: list[str], parent: list[str]) -> bool:
    return len(path) > len(parent) and path[: len(parent)] == parent


def merge_short_intros(sections: list[Section], full_text: str, min_size: int) -> list[Section]:
    """
    Bước 0.5: lời dẫn < min_size mà Section liền sau là mục con của nó → gộp hai mục, lấy path/chapter của mục con.
    Đoạn gộp chứa luôn dòng tiêu đề của mục con nằm giữa (text = full_text[start:end], giữ đúng offset).
    Gộp lặp lại nếu đoạn gộp vẫn < min_size và mục kế tiếp lại là con của nó (Chương → I → 1 đều có lời dẫn ngắn).
    Lời dẫn ngắn đứng cuối nhánh (mục sau không phải con) thì giữ nguyên.
    Chạy trước bước 1–5; phụ thuộc min_size nên tính lại cho từng cấu hình.
    """
    out: list[Section] = []
    i = 0
    while i < len(sections):
        cur = sections[i]
        # path rỗng = phần chữ trước chương đầu tiên, không phải lời dẫn của mục nào.
        while (cur.path and len(cur.text) < min_size and i + 1 < len(sections)
               and _is_child(sections[i + 1].path, cur.path)):
            nxt = sections[i + 1]
            cur = Section(nxt.path, nxt.chapter, nxt.chapter_no, cur.start, nxt.end,
                          full_text[cur.start:nxt.end])
            i += 1
        out.append(cur)
        i += 1
    return out


# --- 1–5. Cắt một mục ------------------------------------------------------------------------------------------

@dataclass
class Piece:
    text: str    # == full_text[start:end]
    start: int   # offset trong full_text
    end: int
    method: str  # "whole_section" | "semantic" | "recursive"


def _slice(src: str, src_start: int, start: int, end: int, method: str) -> Piece:
    """Mảnh [start, end) (offset full_text) lấy từ src (= full_text[src_start:…]), bỏ khoảng trắng hai đầu."""
    body = src[start - src_start:end - src_start]
    lead = len(body) - len(body.lstrip())
    start, end = start + lead, start + len(body.rstrip())
    return Piece(src[start - src_start:end - src_start], start, end, method)


def chunk_section(sec: Section, max_size: int, min_size: int, semantic_thr: float | None,
                  sent_cache: dict | None = None) -> list[Piece]:
    # 2. Size check
    if len(sec.text) <= max_size:
        return [Piece(sec.text, sec.start, sec.end, "whole_section")]

    # 3. Semantic (strategy="recursive" thì semantic_thr=None, bỏ qua bước này)
    pieces = semantic_split(sec, max_size, min_size, semantic_thr, sent_cache) \
        if semantic_thr is not None else [Piece(sec.text, sec.start, sec.end, "recursive")]

    # 4. Recursive: chốt chặn cho mảnh vẫn quá dài
    out = []
    for p in pieces:
        out.extend(recursive_split(p, max_size) if len(p.text) > max_size else [p])

    # 5. Gộp mảnh nhỏ trong cùng mục
    return merge_small(sec, out, min_size, max_size)


# Bước 3: Semantic split

def sentence_spans(sec: Section) -> list[tuple[int, int]]:
    """Câu của mục dạng [start, end) theo offset full_text; các câu liền nhau, phủ kín sec.text (không mất chữ)."""
    from underthesea import sent_tokenize

    flat = sec.text.replace("\n", " ")   # một dòng PDF không phải một câu; cùng độ dài nên offset giữ nguyên
    starts, cursor = [], 0
    for sent in sent_tokenize(flat):
        sent = sent.strip()
        i = flat.find(sent, cursor) if sent else -1
        if i < 0:        # tokenizer đổi chữ (hiếm) → phần này nhập vào câu trước
            continue
        starts.append(i)
        cursor = i + len(sent)
    if not starts:
        return [(sec.start, sec.end)]
    starts[0] = 0
    ends = starts[1:] + [len(flat)]
    return [(sec.start + a, sec.start + b) for a, b in zip(starts, ends)]


def sentence_windows(sec: Section, spans: list[tuple[int, int]], buffer: int = 1) -> list[str]:
    """Mỗi câu ghép với `buffer` câu trước và sau rồi mới embed, để câu quá ngắn bớt nhiễu."""
    sents = [sec.text[a - sec.start:b - sec.start].replace("\n", " ").strip() for a, b in spans]
    return [" ".join(sents[max(0, i - buffer):i + buffer + 1]) for i in range(len(sents))]


def embed_windows(windows: list[str], embed_fn, sent_cache: dict, batch_size: int = 32) -> None:
    """Embed các cửa sổ câu chưa có trong sent_cache (văn bản → vector đã chuẩn hoá L2).
    embed_fn: list[str] → list[vector], ví dụ OllamaEmbeddings(model="qwen3-embedding:4b").embed_documents."""
    todo = list(dict.fromkeys(w for w in windows if w not in sent_cache))
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        vecs = np.asarray(embed_fn(batch), dtype=np.float32)
        vecs /= np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)
        sent_cache.update(zip(batch, vecs))


def sentence_distances(windows: list[str], sent_cache: dict) -> np.ndarray:
    """d_i = 1 - cos(v_i, v_{i+1}); có len(windows) - 1 phần tử."""
    if len(windows) < 2:
        return np.zeros(0, dtype=np.float32)
    v = np.stack([sent_cache[w] for w in windows])
    return 1.0 - np.sum(v[:-1] * v[1:], axis=1)


def semantic_threshold(sections: list[Section], max_size: int, embed_fn, sent_cache: dict,
                       pct: float = 90) -> float:
    """Lượt 1: embed câu của mọi mục dài (> max_size) vào sent_cache, trả về semantic_thr = phân vị pct của mọi d_i.
    Tính một lần cho cả giáo trình: tính riêng từng mục thì mục chỉ có một ý cũng bị ép phải có điểm cắt.
    `sections` phải là danh sách SAU merge_short_intros (cùng danh sách mà lượt 2 sẽ cắt)."""
    dists = []
    for sec in sections:
        if len(sec.text) > max_size:
            windows = sentence_windows(sec, sentence_spans(sec))
            embed_windows(windows, embed_fn, sent_cache)
            dists.append(sentence_distances(windows, sent_cache))
    all_d = np.concatenate(dists) if dists else np.zeros(0)
    return float(np.percentile(all_d, pct)) if all_d.size else float("inf")


def semantic_split(sec: Section, max_size: int, min_size: int, semantic_thr: float,
                   sent_cache: dict) -> list[Piece]:
    """Lượt 2: cắt ở chỗ đổi ý. Embedding câu phải có sẵn trong sent_cache (semantic_threshold)."""
    spans = sentence_spans(sec)
    dist = sentence_distances(sentence_windows(sec, spans), sent_cache)
    return split_by_distance(sec, spans, dist, semantic_thr, min_size, max_size)


def split_by_distance(sec: Section, spans: list[tuple[int, int]], dist: np.ndarray, thr: float,
                      min_size: int, max_size: int) -> list[Piece]:
    """Đóng mảnh khi d_i ≥ thr và mảnh ≥ min_size, hoặc khi thêm câu kế tiếp sẽ làm mảnh vượt max_size.
    Không cắt semantic khi đang ở giữa một trích dẫn “…” (trích lời Hồ Chí Minh nhiều câu); giới hạn max_size
    vẫn áp dụng. Câu đơn dài hơn max_size thành một mảnh riêng, bước 4 cắt tiếp."""
    pieces, cur_start = [], spans[0][0]
    for i, (_, end) in enumerate(spans):
        last = i == len(spans) - 1
        text_so_far = sec.text[cur_start - sec.start:end - sec.start]
        in_quote = text_so_far.count("“") > text_so_far.count("”")
        semantic_cut = not last and dist[i] >= thr and end - cur_start >= min_size and not in_quote
        if last or semantic_cut or spans[i + 1][1] - cur_start > max_size:
            pieces.append(_slice(sec.text, sec.start, cur_start, end, "semantic"))
            if not last:
                cur_start = spans[i + 1][0]
    return pieces


# Bước 4: Recursive (chốt chặn)

def recursive_split(p: Piece, max_size: int) -> list[Piece]:
    """Cắt cứng mảnh > max_size, overlap ~1/8 max_size. Cắt trên bản phẳng (\\n → dấu cách, cùng độ dài): \\n chỉ là
    xuống dòng của PDF, ưu tiên cắt ở cuối câu rồi mới tới dấu phẩy, dấu cách."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    overlap = max_size // 8
    sp = RecursiveCharacterTextSplitter(chunk_size=max_size, chunk_overlap=overlap, keep_separator="end",
                                        separators=[". ", "? ", "! ", "; ", ", ", " ", ""])
    flat = p.text.replace("\n", " ")
    out, cursor = [], 0
    for t in sp.split_text(flat):
        i = flat.find(t, cursor)
        if i < 0:
            print(f"[recursive_split] cảnh báo: không tìm thấy mảnh trong văn bản gốc, offset có thể sai "
                  f"(start={p.start}, mảnh={t[:40]!r})")
            i = cursor
        out.append(_slice(p.text, p.start, p.start + i, p.start + i + len(t), "recursive"))
        # Mảnh sau chỉ lùi lại tối đa `overlap` ký tự so với cuối mảnh này. Tìm từ i + 1 thì với đoạn văn lặp
        # lại (trích dẫn, câu khẩu hiệu nhắc nhiều lần) find có thể khớp nhầm vào chỗ sớm hơn và bỏ sót chữ.
        cursor = max(i + 1, i + len(t) - overlap)
    return out


# Bước 5: Gộp mảnh nhỏ

def merge_small(sec: Section, pieces: list[Piece], min_size: int, max_size: int) -> list[Piece]:
    """Mảnh < min_size gộp vào mảnh liền trước nếu tổng không vượt max_size; mảnh đầu mục (hoặc mảnh không gộp
    lùi được) thì mảnh sau gộp vào nó. Chỉ gộp trong cùng mục. Mảnh gộp mang method của phần dài hơn."""
    out: list[Piece] = []
    for p in pieces:
        prev = out[-1] if out else None
        if prev and min(len(prev.text), len(p.text)) < min_size and p.end - prev.start <= max_size:
            method = max(prev, p, key=lambda x: len(x.text)).method
            out[-1] = _slice(sec.text, sec.start, prev.start, p.end, method)
        else:
            out.append(p)
    return out


# --- Ghép tất cả -----------------------------------------------------------------------------------------------

STRATEGIES = ("hier_semantic", "recursive")


@dataclass
class Chunk:
    text: str          # == full_text[start:end], đưa vào payload "text" (hiển thị + prompt + reranker)
    embed_text: str    # "[path]\ntext", chỉ dùng để embed
    start: int
    end: int
    payload: dict


def build_chunks(full_text: str, page_map: PageMap, sections: list[Section], max_size: int,
                 min_size: int | None = None, strategy: str = "hier_semantic", semantic_pct: float = 90,
                 embed_fn=None, sent_cache: dict | None = None,
                 source: str = "Giao_trinh_Tu_tuong_HCM.pdf", id_prefix: str = "chunk") -> tuple[list[Chunk], dict]:
    """
    sections: kết quả detect_sections (chưa gộp lời dẫn).
    strategy: "hier_semantic" (bước 0.5 + 1–5, cần embed_fn) hoặc "recursive" (bỏ bước 3, không embed gì).
    sent_cache: dict văn bản cửa sổ → vector; truyền cùng một dict (hoặc nạp từ load_sent_cache) cho nhiều cấu hình
                để không phải embed câu lại.
    id_prefix: chunk_id = "<id_prefix>-0001", "-0002"… (ingest truyền tên collection). Phải là chuỗi đánh số từ 1:
               search_rerank.previous_chunk_id / fetch_chunk tìm chunk liền trước bằng chuỗi "…-0011" → "…-0010".
    Trả về (chunks, stats).
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy phải là một trong {STRATEGIES}, nhận {strategy!r}")
    min_size = max_size // 4 if min_size is None else min_size

    # 0.5 — làm TRƯỚC khi tính ngưỡng: lượt 1 và lượt 2 phải nhìn cùng một danh sách mục.
    secs = merge_short_intros(sections, full_text, min_size)

    semantic_thr = None
    if strategy == "hier_semantic":
        if embed_fn is None:
            raise ValueError("strategy='hier_semantic' cần embed_fn")
        sent_cache = {} if sent_cache is None else sent_cache
        semantic_thr = semantic_threshold(secs, max_size, embed_fn, sent_cache, semantic_pct)   # lượt 1

    chunks: list[Chunk] = []
    for sec in secs:                                                                             # lượt 2
        for p in chunk_section(sec, max_size, min_size, semantic_thr, sent_cache):
            payload = {
                "chunk_id": f"{id_prefix}-{len(chunks) + 1:04d}",
                "source": source,
                "chapter": sec.chapter,
                "chapter_no": sec.chapter_no,
                "section": sec.path_str,
                "page": page_map.page_at(p.start),          # giữ để code cũ không vỡ
                "page_start": page_map.page_at(p.start),
                "page_end": page_map.page_at(p.end - 1),
                "chunk_method": p.method,
                "text": p.text,
            }
            embed_text = f"[{sec.path_str}]\n{p.text}" if sec.path else p.text
            chunks.append(Chunk(p.text, embed_text, p.start, p.end, payload))

    stats = chunk_stats(chunks, max_size, min_size)
    stats.update({
        "strategy": strategy,
        "max_size": max_size,
        "min_size": min_size,
        "semantic_pct": semantic_pct if strategy == "hier_semantic" else None,
        "semantic_thr": None if semantic_thr is None or semantic_thr == float("inf") else round(semantic_thr, 4),
        "n_sections": len(sections),
        "n_sections_after_intro_merge": len(secs),
        "n_intros_merged": len(sections) - len(secs),
        "n_sections_split": sum(len(s.text) > max_size for s in secs),
    })
    return chunks, stats


def chunk_stats(chunks: list[Chunk], max_size: int, min_size: int) -> dict:
    lens = np.array([len(c.text) for c in chunks]) if chunks else np.zeros(1)
    methods: dict[str, int] = {}
    for c in chunks:
        methods[c.payload["chunk_method"]] = methods.get(c.payload["chunk_method"], 0) + 1
    return {
        "n_chunks": len(chunks),
        "len_percentiles": {f"p{p}": int(np.percentile(lens, p)) for p in (5, 25, 50, 75, 95)},
        "len_max": int(lens.max()),
        "len_mean": round(float(lens.mean()), 1),
        "by_method": methods,
        "n_over_max": int((lens > max_size).sum()),        # phải = 0
        "n_under_min": int((lens < min_size).sum()),       # nhỏ; xem merge_small để biết vì sao còn
        "n_cross_page": sum(c.payload["page_start"] != c.payload["page_end"] for c in chunks),
        # Số chunk lệch “ ”: chủ yếu do cắt giữa trích dẫn, nhưng ĐẾM THỪA vì PDF dùng lẫn kiểu ngoặc (có chỗ dùng ”
        # để mở, ví dụ 'chiến lược” trồng người”'; toàn văn có 500 “ / 511 ”) → vài chunk whole_section cũng bị đếm.
        "n_unbalanced_quotes": sum(c.text.count("“") != c.text.count("”") for c in chunks),
        "n_lowercase_start": sum(c.text[:1].islower() for c in chunks),                     # bắt đầu giữa câu
        "embed_text_max_chars": max((len(c.embed_text) for c in chunks), default=0),
    }


# --- Cache embedding câu ---------------------------------------------------------------------------------------

def load_sent_cache(path: str) -> dict:
    """Nạp cache embedding câu từ .npz (keys: texts, vecs). File không tồn tại → dict rỗng.
    Cache gắn với một model embedding cụ thể: đổi model thì dùng file khác."""
    if not os.path.exists(path):
        return {}
    data = np.load(path, allow_pickle=False)
    return dict(zip(data["texts"].tolist(), data["vecs"]))


def save_sent_cache(path: str, sent_cache: dict) -> None:
    if not sent_cache:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    texts = list(sent_cache)
    np.savez_compressed(path, texts=np.array(texts), vecs=np.stack([sent_cache[t] for t in texts]))


# --- CLI -------------------------------------------------------------------------------------------------------

def _print_section_report(full_text: str, page_map: PageMap, sections: list[Section]) -> None:
    for sec in sections:
        print(f"{len(sec.text):>6}  p{page_map.page_at(sec.start):>3}  {sec.path_str}")
    print(f"\nSố mục: {len(sections)}   tổng ký tự trong mục: {sum(len(s.text) for s in sections):,}"
          f"   toàn văn: {len(full_text):,}")

    lens = np.array([len(s.text) for s in sections])
    print("\nPhân vị độ dài mục:")
    for p in [10, 25, 50, 75, 90, 95]:
        print(f"  p{p:<3}{int(np.percentile(lens, p)):>7}")
    print(f"  max {lens.max():>7}  ({sections[lens.argmax()].path_str[-80:]})")
    print("\nmax_size | % mục giữ nguyên | số mục phải cắt")
    for m in [600, 800, 1000, 1200, 1500, 1800, 2000]:
        print(f"{m:>8} | {(lens <= m).mean():>15.0%} | {(lens > m).sum():>14}")

    # Gap check: mọi đoạn có chữ nằm ngoài các mục phải là dòng tiêu đề.
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


def _print_dry_run(full_text: str, chunks: list[Chunk], stats: dict, n_samples: int, seed: int) -> None:
    print(f"\n=== {stats['strategy']}  max_size={stats['max_size']}  min_size={stats['min_size']} ===")
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    # Kiểm tra nhanh các bất biến (bản đầy đủ nằm trong tests/test_chunking.py).
    bad_offset = sum(full_text[c.start:c.end] != c.text for c in chunks)
    print(f"Bất biến: text khớp offset sai {bad_offset} | vượt max_size {stats['n_over_max']}")

    rng = np.random.default_rng(seed)
    semantic = [c for c in chunks if c.payload["chunk_method"] == "semantic"]
    pool = semantic if semantic else chunks
    for c in rng.choice(pool, size=min(n_samples, len(pool)), replace=False):
        p = c.payload
        print(f"\n--- #{p['chunk_id']}  {p['chunk_method']}  {len(c.text)} ký tự  "
              f"tr.{p['page_start']}–{p['page_end']}  {p['section'][-70:]}")
        body = c.text.replace("\n", " ")
        print(body if len(body) <= 400 else f"{body[:200]} … {body[-200:]}")


def main() -> None:
    import argparse

    from dotenv import load_dotenv

    load_dotenv()   # EMBED_MODEL / EMBED_NUM_CTX trong .env làm giá trị mặc định cho tham số bên dưới
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="Chunking v2: kiểm tra mục, hoặc cắt thử (dry run, không ghi Qdrant).")
    ap.add_argument("pdf", nargs="?", default=os.path.join(root, "data", "Giao_trinh_Tu_tuong_HCM.pdf"))
    ap.add_argument("--dry-run", nargs="+", type=int, metavar="MAX_SIZE",
                    help="cắt thử với các max_size này, ví dụ --dry-run 800 1200 1600")
    ap.add_argument("--strategy", choices=STRATEGIES, default="hier_semantic")
    ap.add_argument("--min-size", type=int, default=None, help="mặc định max_size // 4")
    ap.add_argument("--semantic-pct", type=float, default=90)
    ap.add_argument("--embed-model", default=os.getenv("EMBED_MODEL", "qwen3-embedding:4b"))
    ap.add_argument("--embed-num-ctx", type=int, default=int(os.getenv("EMBED_NUM_CTX", "1024")))
    ap.add_argument("--cache", default=None,
                    help="mặc định data/chunk_runs/sent_cache_<embed-model>.npz (mỗi model một file)")
    ap.add_argument("--samples", type=int, default=5, help="số chunk mẫu in ra (ưu tiên chunk semantic)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None, help="thư mục ghi <tên>.json thống kê, ví dụ data/chunk_runs")
    a = ap.parse_args()

    full_text, page_map = load_full_text(a.pdf)
    sections = detect_sections(full_text)

    if not a.dry_run:
        _print_section_report(full_text, page_map, sections)
        return
    if a.cache is None:
        model_tag = re.sub(r"[^A-Za-z0-9._-]+", "-", a.embed_model)   # "qwen3-embedding:4b" → "qwen3-embedding-4b"
        a.cache = os.path.join(root, "data", "chunk_runs", f"sent_cache_{model_tag}.npz")

    embed_fn, sent_cache = None, None
    if a.strategy == "hier_semantic":
        from langchain_ollama import OllamaEmbeddings
        embed_fn = OllamaEmbeddings(model=a.embed_model, num_ctx=a.embed_num_ctx).embed_documents
        sent_cache = load_sent_cache(a.cache)
        print(f"Cache embedding câu: {len(sent_cache):,} cửa sổ có sẵn ({a.cache})")

    for max_size in a.dry_run:
        chunks, stats = build_chunks(full_text, page_map, sections, max_size, a.min_size, a.strategy,
                                     a.semantic_pct, embed_fn, sent_cache)
        if sent_cache is not None:
            save_sent_cache(a.cache, sent_cache)   # lưu sau mỗi cấu hình: dừng giữa chừng vẫn giữ được phần đã embed
        _print_dry_run(full_text, chunks, stats, a.samples, a.seed)
        if a.out:
            name = f"ms{max_size}" + ("_nosem" if a.strategy == "recursive" else "")
            os.makedirs(a.out, exist_ok=True)
            with open(os.path.join(a.out, f"dryrun_{name}.json"), "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
