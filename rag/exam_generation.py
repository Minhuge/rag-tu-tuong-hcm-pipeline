"""
Tạo đề từ giáo trình.

Lấy đoạn văn theo chương (roadmap 2.2) — khác với trả lời câu hỏi: không tìm theo độ giống mà lấy các đoạn TRẢI ĐỀU cả chương,
nếu không câu hỏi sẽ dồn vào một chỗ.

  1. scroll Qdrant lọc theo payload "chapter" (đã có payload index, không cần vector search);
  2. sắp theo trang rồi theo chunk_id (= thứ tự trong giáo trình);
  3. chọn đều các đoạn từ đầu tới cuối chương cho tới khi đủ max_chars;
  4. mỗi đoạn kèm nhãn [trang X] để Gemini trích source_page.

Schema output (roadmap 2.3): GeneratedExam truyền cho Gemini qua response_schema → Gemini bắt buộc
trả JSON đúng dạng; parse_exam() đọc kết quả. Schema chỉ ràng buộc HÌNH DẠNG (kiểu, nhãn A–D, đúng
4 lựa chọn): sai hình dạng là cả đề hỏng. Lỗi nội dung từng câu (trùng lựa chọn, trang lạ, điểm ≤ 0)
để bước kiểm tra 2.6 loại riêng câu đó, không bỏ cả đề.

Prompt (roadmap 2.4): quy tắc nằm trong rag/prompts.py; build_exam_prompt() ghép đoạn văn + số câu.

generate_exam() (2.6): tạo → xáo lựa chọn → kiểm tra (rag/exam_validation.py) → thiếu câu thì tạo bù
tối đa 1 lần → vẫn thiếu thì trả ít câu hơn kèm thông báo. Tối đa 4 lần gọi Gemini (2 tạo + 2 giám khảo).

Chạy thử:  python -m rag.exam_generation 3 --max-chars 6000
"""
import argparse
import random
import re
from dataclasses import dataclass
from typing import NamedTuple

from google.genai import types
from pydantic import BaseModel, Field, ValidationError
from qdrant_client import QdrantClient

from rag.exam_validation import Label, Rejection, validate_exam
from rag.pipeline import call_gemini, gemini_config
from rag.prompts import AVOID_NOTE, EXAM_PROMPT, EXAM_SYSTEM, NO_ESSAY_NOTE
from rag.search_rerank import COLLECTION, chapter_filter, get_qdrant

DEFAULT_MAX_CHARS = 20_000
MIN_MAX_CHARS = 1_000       # chunk dài tối đa ~800 ký tự + nhãn → luôn chọn được ít nhất 1 đoạn
MIN_PASSAGE_CHARS = 150     # ngắn hơn thì chỉ là dòng tiêu đề chương, không đủ để ra câu hỏi


class Chapter(NamedTuple):
    number: int      # 0 = Chương Mở đầu
    key: str         # giá trị payload "chapter" trong Qdrant (scripts/ingest.py: extract_chapter)
    title: str


# Tên chương chép từ tiêu đề trong giáo trình (payload Qdrant chỉ lưu "Chương III", không có tên).
# Ingest lại với giáo trình khác thì sửa danh sách này theo.
CHAPTERS: tuple[Chapter, ...] = (
    Chapter(0, "Chương Mở đầu", "Đối tượng, phương pháp nghiên cứu và ý nghĩa học tập môn Tư tưởng Hồ Chí Minh"),
    Chapter(1, "Chương I", "Cơ sở, quá trình hình thành và phát triển tư tưởng Hồ Chí Minh"),
    Chapter(2, "Chương II", "Tư tưởng Hồ Chí Minh về vấn đề dân tộc và cách mạng giải phóng dân tộc"),
    Chapter(3, "Chương III", "Tư tưởng Hồ Chí Minh về chủ nghĩa xã hội và con đường quá độ lên chủ nghĩa xã hội "
                             "ở Việt Nam"),
    Chapter(4, "Chương IV", "Tư tưởng Hồ Chí Minh về Đảng Cộng sản Việt Nam"),
    Chapter(5, "Chương V", "Tư tưởng Hồ Chí Minh về đại đoàn kết dân tộc và đoàn kết quốc tế"),
    Chapter(6, "Chương VI", "Tư tưởng Hồ Chí Minh về xây dựng Nhà nước của dân, do dân, vì dân"),
    Chapter(7, "Chương VII", "Tư tưởng Hồ Chí Minh về văn hoá, đạo đức và xây dựng con người mới"),
)


@dataclass
class Passage:
    chunk_id: str
    page: int | None
    text: str

    def labeled(self) -> str:
        return f"[trang {self.page}] {self.text}" if self.page is not None else self.text


@dataclass
class ChapterPassages:
    chapter: Chapter
    passages: list[Passage]   # các đoạn đã chọn, theo thứ tự trong giáo trình
    total: int                # số đoạn dùng được của cả chương (trước khi chọn)

    @property
    def pages(self) -> list[int]:
        """Các trang đã gửi cho Gemini — bước kiểm tra (2.6) dùng để loại source_page lạ."""
        return sorted({p.page for p in self.passages if p.page is not None})

    @property
    def chars(self) -> int:
        return len(self.prompt_text())

    def prompt_text(self) -> str:
        return "\n\n".join(p.labeled() for p in self.passages)


def resolve_chapter(chapter: int | str) -> Chapter:
    """Nhận số chương (3), chuỗi số ("3") hoặc đúng key payload ("Chương III", không phân biệt hoa thường)."""
    if isinstance(chapter, str) and chapter.strip().isdigit():
        chapter = int(chapter)
    for c in CHAPTERS:
        if chapter == c.number or (isinstance(chapter, str) and chapter.strip().casefold() == c.key.casefold()):
            return c
    raise ValueError(f"Không có chương {chapter!r}. Chọn một trong: "
                     + ", ".join(f"{c.number} ({c.key})" for c in CHAPTERS))


def _heading_re(c: Chapter) -> re.Pattern:
    word = r"MỞ\s*ĐẦU" if c.number == 0 else re.escape(c.key.split()[1])
    return re.compile(rf"CH[UƯ]ƠNG\s+{word}\b", re.IGNORECASE)


def _clean(text: str, heading: re.Pattern) -> str:
    # Đoạn chứa tiêu đề chương thường mở đầu bằng vài câu cuối của chương trước → cắt bỏ phần đó.
    m = heading.search(text)
    if m:
        text = text[m.start():]
    return text.lstrip(" .,;:").strip()   # splitter hay để lại ". " ở đầu đoạn


def _scroll_chapter(client: QdrantClient, key: str) -> list[dict]:
    payloads, offset = [], None
    while True:
        points, offset = client.scroll(collection_name=COLLECTION, scroll_filter=chapter_filter(key),
                                       limit=256, offset=offset, with_payload=True, with_vectors=False)
        payloads += [p.payload for p in points]
        if offset is None:
            return payloads


def _spread(n: int, k: int) -> list[int]:
    """k chỉ số trải đều trên 0..n-1, luôn gồm đoạn đầu và đoạn cuối (k ≥ 2)."""
    if k >= n:
        return list(range(n))
    if k == 1:
        return [n // 2]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def _select_evenly(passages: list[Passage], max_chars: int) -> list[Passage]:
    sizes = [len(p.labeled()) + 2 for p in passages]   # +2: "\n\n" nối giữa các đoạn
    if sum(sizes) - 2 <= max_chars:
        return passages
    for k in range(len(passages) - 1, 0, -1):          # nhiều đoạn nhất mà vẫn vừa max_chars
        idx = _spread(len(passages), k)
        if sum(sizes[i] for i in idx) - 2 <= max_chars:
            return [passages[i] for i in idx]
    return passages[:1]   # không xảy ra khi max_chars ≥ MIN_MAX_CHARS


def get_chapter_passages(chapter: int | str, max_chars: int = DEFAULT_MAX_CHARS,
                         client: QdrantClient | None = None) -> ChapterPassages:
    """
    Các đoạn trải đều cả chương, tổng độ dài (kể cả nhãn [trang X]) ≤ max_chars.
    ValueError: chương không tồn tại hoặc max_chars quá nhỏ. LookupError: Qdrant không có đoạn nào của chương.
    """
    if max_chars < MIN_MAX_CHARS:
        raise ValueError(f"max_chars phải ≥ {MIN_MAX_CHARS}")
    c = resolve_chapter(chapter)
    rows = _scroll_chapter(client or get_qdrant(), c.key)
    rows.sort(key=lambda r: (r.get("page") or 0, str(r.get("chunk_id", ""))))
    heading = _heading_re(c)
    passages = [Passage(str(r.get("chunk_id", "")), r.get("page"), _clean(r.get("text", ""), heading))
                for r in rows]
    passages = [p for p in passages if len(p.text) >= MIN_PASSAGE_CHARS]
    if not passages:
        raise LookupError(f"Qdrant chưa có đoạn nào của {c.key} (collection {COLLECTION}).")
    return ChapterPassages(c, _select_evenly(passages, max_chars), total=len(passages))



# ---------- schema output cho Gemini (2.3) ----------
# description của từng field được gửi kèm schema → Gemini đọc như chỉ dẫn ngắn; quy tắc đầy đủ nằm trong prompt (2.4).
# Thứ tự field = thứ tự Gemini viết: source_page đứng đầu → chọn đoạn nguồn trước rồi mới đặt câu hỏi từ đoạn đó.
class GeneratedOption(BaseModel):
    label: Label
    content: str = Field(description="Nội dung lựa chọn, không kèm chữ cái đầu")


class GeneratedMCQ(BaseModel):
    source_page: int = Field(description="Số trang lấy từ nhãn [trang X] của đoạn dùng để ra câu hỏi")
    question: str
    options: list[GeneratedOption] = Field(min_length=4, max_length=4, description="Đúng 4 lựa chọn A, B, C, D")
    correct_label: Label
    explanation: str = Field(description="Vì sao đáp án đúng, dựa vào đoạn giáo trình")


class GeneratedEssay(BaseModel):
    source_page: int = Field(description="Số trang lấy từ nhãn [trang X] của đoạn dùng để ra câu hỏi")
    question: str
    rubric: str = Field(description="Các ý cần có trong bài làm, mỗi ý ghi số điểm")
    points: float = Field(description="Tổng điểm của câu, bằng tổng điểm các ý trong rubric")


class GeneratedExam(BaseModel):
    mcq: list[GeneratedMCQ]
    essay: list[GeneratedEssay]


def shuffle_options(q: GeneratedMCQ, rng: random.Random) -> GeneratedMCQ:
    """
    Xáo thứ tự 4 lựa chọn rồi gán lại nhãn A–D. Gemini không tự rải đáp án đúng dù prompt có dặn
    (đo 30 câu ở 3 chương: B 13 lần, D 2 lần) → làm bằng code. Nhãn hỏng (trùng, thiếu) thì giữ nguyên để 2.6 loại.
    """
    labels = [o.label for o in q.options]
    if sorted(labels) != ["A", "B", "C", "D"]:
        return q
    order = list(range(4))
    rng.shuffle(order)
    options = [GeneratedOption(label="ABCD"[i], content=q.options[j].content) for i, j in enumerate(order)]
    return q.model_copy(update={"options": options,
                                "correct_label": "ABCD"[order.index(labels.index(q.correct_label))]})


def exam_config(system: str = EXAM_SYSTEM) -> types.GenerateContentConfig:
    return gemini_config(system, schema=GeneratedExam)


def build_exam_prompt(src: ChapterPassages, n_mcq: int, n_essay: int, avoid: list[str] | None = None) -> str:
    """Phần contents của lần gọi tạo đề; quy tắc cố định nằm trong EXAM_SYSTEM (exam_config).
    avoid: câu hỏi đã có (lần tạo bù) → Gemini không hỏi lại."""
    if n_mcq < 0 or n_essay < 0 or n_mcq + n_essay == 0:
        raise ValueError("Cần ít nhất 1 câu; số câu không được âm.")
    prompt = EXAM_PROMPT.format(chapter=src.chapter.key, passages=src.prompt_text(), n_mcq=n_mcq,
                                n_essay=n_essay, essay_note=NO_ESSAY_NOTE if n_essay == 0 else "")
    if avoid:
        prompt += AVOID_NOTE.format(questions="\n".join(f"- {q}" for q in avoid))
    return prompt


class ExamParseError(ValueError):
    """Gemini trả đề không đọc được; raw = nguyên văn để xem lỗi (script prototype lưu lại)."""

    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message)
        self.raw = raw


def parse_exam(resp) -> GeneratedExam:
    """Kết quả Gemini → GeneratedExam. JSON hỏng / sai schema / rỗng → ExamParseError (không lưu gì)."""
    if isinstance(resp.parsed, GeneratedExam):
        return resp.parsed
    if not resp.text:   # bị chặn bởi safety filter hoặc hết max_output_tokens
        reason = resp.candidates[0].finish_reason if resp.candidates else "không có candidate"
        raise ExamParseError(f"Gemini không trả về nội dung ({reason}).")
    try:
        return GeneratedExam.model_validate_json(resp.text)
    except ValidationError as e:
        raise ExamParseError(f"Gemini trả về đề không đúng schema: {e.error_count()} lỗi, "
                             f"đầu tiên: {e.errors()[0]['loc']} {e.errors()[0]['msg']}", raw=resp.text) from e



# ---------- tạo đề có kiểm tra (2.6) ----------
@dataclass
class GenerationResult:
    exam: GeneratedExam
    rejected: list[Rejection]
    calls: int                 # số lần gọi Gemini (tạo + giám khảo)
    models: list[str]          # model đã trả lời từng lần tạo (có thể là model dự phòng)
    notice: str | None = None  # vẫn thiếu câu sau khi tạo bù

    @property
    def topped_up(self) -> bool:
        return len(self.models) > 1


async def _generate_round(llm, src: ChapterPassages, n_mcq: int, n_essay: int, rng: random.Random,
                          avoid: list[str], use_judge: bool):
    resp = await call_gemini(llm, build_exam_prompt(src, n_mcq, n_essay, avoid), exam_config())
    exam = parse_exam(resp)
    exam.mcq = [shuffle_options(q, rng) for q in exam.mcq]
    return await validate_exam(llm, exam, set(src.pages), src.prompt_text(), use_judge, existing=avoid), \
        resp.model_version


async def generate_exam(llm, src: ChapterPassages, n_mcq: int, n_essay: int,
                        rng: random.Random | None = None, use_judge: bool = True) -> GenerationResult:
    """
    Đề đã qua kiểm tra. Lần tạo đầu lỗi (JSON hỏng, giám khảo lỗi) → ValueError, không có gì để lưu.
    Lần tạo bù lỗi → giữ các câu đã đạt và ghi vào notice.
    """
    rng = rng or random.Random()
    val, model = await _generate_round(llm, src, n_mcq, n_essay, rng, [], use_judge)
    mcq, essay = val.mcq[:n_mcq], val.essay[:n_essay]
    res = GenerationResult(GeneratedExam(mcq=mcq, essay=essay), val.rejected, 1 + val.judge_calls, [model])

    missing_mcq, missing_essay = n_mcq - len(mcq), n_essay - len(essay)
    if missing_mcq or missing_essay:
        try:
            val2, model2 = await _generate_round(llm, src, missing_mcq, missing_essay, rng,
                                                 [q.question for q in mcq + essay], use_judge)
        except ValueError as e:
            res.calls += 1
            res.notice = f"Tạo bù thất bại ({e})."
        else:
            mcq += val2.mcq[:missing_mcq]
            essay += val2.essay[:missing_essay]
            res.rejected += val2.rejected
            res.calls += 1 + val2.judge_calls
            res.models.append(model2)
    if len(mcq) < n_mcq or len(essay) < n_essay:
        short = f"Chỉ tạo được {len(mcq)}/{n_mcq} câu trắc nghiệm và {len(essay)}/{n_essay} câu tự luận đạt kiểm tra."
        res.notice = f"{short} {res.notice}" if res.notice else short
    res.exam = GeneratedExam(mcq=mcq, essay=essay)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="In các đoạn giáo trình trải đều một chương")
    ap.add_argument("chapter", help='số chương (0 = Mở đầu) hoặc "Chương III"')
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    args = ap.parse_args()
    res = get_chapter_passages(args.chapter, args.max_chars)
    print(f"{res.chapter.key}: {res.chapter.title}")
    print(f"Chọn {len(res.passages)}/{res.total} đoạn, {res.chars} ký tự, trang {res.pages[0]}–{res.pages[-1]}\n")
    for p in res.passages:
        print(f"{p.chunk_id}  {p.labeled()[:160]}…")
