"""
Chấm câu tự luận bằng Gemini (roadmap 3.2): schema kết quả, prompt, và luật tính điểm bằng code.

  too_short          bài rỗng / dưới MIN_ESSAY_CHARS ký tự → 0 điểm, không gọi Gemini (quyết định 3.1)
  page_passages      đoạn giáo trình của trang nguồn ±1 để đối chiếu (quyết định 3.1)
  search_passages    câu không ghi trang (đề tay): tìm theo nội dung câu hỏi
  parse_rubric       barem "- ý (1 điểm)" → các ý đánh số, kèm điểm từng ý
  build_grade_prompt câu hỏi + barem + đoạn giáo trình + bài làm (trong <bai_lam>) → contents
  grade_essay        một lần gọi Gemini cho MỘT câu → GradeOutcome (điểm cuối do code tính)

AI không quyết định điểm. Với mỗi ý barem, AI trả mức "đủ" / "một phần" / "không có" kèm trích dẫn nguyên văn từ
bài làm; code (final_score):
  - ý có điểm nhưng trích dẫn KHÔNG có trong bài làm → tính "không có" (AI bịa / bị bài làm dắt mũi);
  - barem ghi điểm từng ý → điểm = Σ điểm ý × (1 | 0.5 | 0), quy đổi theo điểm tối đa của câu nếu lệch;
    barem không ghi điểm (đề tay viết tự do) → lấy score của AI, nhưng 0 nếu không ý nào được xác nhận;
  - kẹp về 0..points, làm tròn 0.25.
Bài làm là dữ liệu không tin cậy: thẻ giả (<bai_lam>, <barem>…) trong bài bị xoá trước khi ghép prompt;
lời gọi chấm không có tool, không ghi gì — chỉ trả kết quả để code tính.

Các luật còn lại trước khi gọi AI (câu không có rubric → chờ chấm tay, quota) nằm ở service 3.4.
"""
import re
from dataclasses import dataclass, field
from typing import Literal

from google.genai import types
from pydantic import BaseModel, Field, ValidationError
from qdrant_client import QdrantClient

from rag.exam_validation import _RUBRIC_POINTS, _norm
from rag.pipeline import call_gemini, gemini_config
from rag.prompts import GRADE_PROMPT, GRADE_SYSTEM
from rag.search_rerank import load_pages, qdrant_search

Level = Literal["đủ", "một phần", "không có"]
WEIGHT: dict[str, float] = {"đủ": 1.0, "một phần": 0.5, "không có": 0.0}
SCORE_STEP = 0.25
MIN_ESSAY_CHARS = 20          # ngắn hơn (không tính khoảng trắng) thì không đủ để có ý nào → 0, không gọi Gemini
EMPTY_FEEDBACK = "Không có bài làm hoặc bài quá ngắn để chấm."
PAGE_SPREAD = 1               # đối chiếu trang nguồn ± 1: ý của một câu hay vắt sang trang kế bên
SEARCH_K = 6                  # câu không ghi trang: số đoạn tìm theo nội dung câu hỏi

_BULLET = re.compile(r"^\s*(?:[-*•+]|\d+[.)])\s+")
# Thẻ dùng để phân vùng prompt: bài làm tự viết "</bai_lam>" thì có thể "thoát" ra ngoài vùng dữ liệu → xoá.
_PROMPT_TAGS = re.compile(r"<\s*/?\s*(?:bai_lam|barem|giao_trinh|cau_hoi)\b[^>]*>", re.IGNORECASE)


# ---------- schema output ----------
# Thứ tự field = thứ tự Gemini viết: trích dẫn trước rồi mới xếp mức; chấm từng ý trước rồi mới nhận xét và cộng điểm.
class ItemGrade(BaseModel):
    item: int = Field(description="Số thứ tự ý trong barem (1, 2, …)")
    evidence: str = Field(description="Đoạn ngắn chép NGUYÊN VĂN từ bài làm thể hiện ý này; không có thì rỗng")
    level: Level


class EssayGrade(BaseModel):
    items: list[ItemGrade]
    feedback: str = Field(description="Nhận xét tiếng Việt 2–4 câu: ý đạt, ý thiếu kèm (trang X)")
    score: float = Field(description="Tổng điểm theo barem, không vượt điểm tối đa của câu")


# ---------- barem ----------
@dataclass
class RubricItem:
    text: str
    points: float | None   # None: dòng barem không ghi "(x điểm)"


def parse_rubric(rubric: str) -> list[RubricItem]:
    """Mỗi dòng gạch đầu dòng / đánh số là một ý. Không có dòng nào như vậy → cả barem là một ý."""
    items = []
    for line in rubric.splitlines():
        if not _BULLET.match(line):
            continue                                  # dòng tiêu đề như "Barem:" → bỏ
        text = _BULLET.sub("", line).strip()
        found = _RUBRIC_POINTS.findall(text)
        items.append(RubricItem(text, float(found[-1].replace(",", ".")) if found else None))
    return items or [RubricItem(rubric.strip(), None)]


def rubric_points_total(items: list[RubricItem]) -> float:
    """Tổng điểm các ý; 0 nếu có ý không ghi điểm (khi đó không cộng theo barem được)."""
    return sum(i.points for i in items) if all(i.points is not None for i in items) else 0.0


# ---------- trước khi gọi Gemini ----------
def too_short(essay: str | None) -> bool:
    return len("".join((essay or "").split())) < MIN_ESSAY_CHARS


def page_passages(client: QdrantClient, page: int | None, spread: int = PAGE_SPREAD) -> str:
    """Đoạn giáo trình của trang page ± spread, gắn nhãn [trang X] như lúc tạo đề. page None → rỗng
    (câu đề tay không ghi trang: 3.4 tìm theo nội dung câu hỏi)."""
    if page is None:
        return ""
    return _labeled(load_pages(client, max(1, page - spread), page + spread))


def _labeled(rows: list[dict]) -> str:
    return "\n\n".join(f"[trang {r.get('page')}] {r.get('text', '').strip()}" for r in rows)


def search_passages(client: QdrantClient, embedder, question: str, k: int = SEARCH_K) -> str:
    """
    Đoạn giáo trình gần nội dung câu hỏi nhất (câu đề tay không ghi trang). Chỉ embedding (Ollama) + Qdrant, không
    reranker: job chấm chạy nền, không tranh GPU với chatbot. Xếp lại theo thứ tự trong sách cho dễ đọc.
    """
    hits = qdrant_search(client, embedder, question, limit=k)
    rows = sorted((h["metadata"] for h in hits), key=lambda r: (r.get("page") or 0, r.get("chunk_id", "")))
    return _labeled(rows)


# ---------- prompt ----------
def clean_essay(essay: str) -> str:
    return _PROMPT_TAGS.sub(" ", essay).strip()


def build_grade_prompt(question: str, rubric: str, points: float, essay: str, passages_text: str) -> str:
    items = parse_rubric(rubric)
    return GRADE_PROMPT.format(question=question.strip(), points=f"{points:g}",
                               rubric="\n".join(f"{n}. {it.text}" for n, it in enumerate(items, 1)),
                               passages=passages_text.strip() or "(không có đoạn giáo trình)",
                               essay=clean_essay(essay), n_items=len(items))


def grade_config() -> types.GenerateContentConfig:
    return gemini_config(GRADE_SYSTEM, schema=EssayGrade)


class GradeParseError(ValueError):
    """Gemini trả kết quả chấm không đọc được; raw = nguyên văn để xem lỗi."""

    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message)
        self.raw = raw


def parse_grade(resp) -> EssayGrade:
    if isinstance(resp.parsed, EssayGrade):
        return resp.parsed
    if not resp.text:   # bị chặn bởi safety filter hoặc hết max_output_tokens
        reason = resp.candidates[0].finish_reason if resp.candidates else "không có candidate"
        raise GradeParseError(f"Gemini không trả về kết quả chấm ({reason}).")
    try:
        return EssayGrade.model_validate_json(resp.text)
    except ValidationError as e:
        raise GradeParseError(f"Gemini trả kết quả chấm không đúng schema: {e.errors()[0]['loc']} "
                              f"{e.errors()[0]['msg']}", raw=resp.text) from e


# ---------- điểm cuối do code tính ----------
@dataclass
class GradeOutcome:
    score: float                       # điểm lưu vào submission_answers.score
    feedback: str
    matched_points: list[str]          # các ý barem bài làm đạt (đủ hoặc một phần), sau khi kiểm trích dẫn
    levels: dict[int, str]             # mức cuối của từng ý (ý AI bỏ sót → "không có")
    unsupported: list[int] = field(default_factory=list)   # ý AI cho điểm mà trích dẫn không có trong bài làm
    from_rubric: bool = True           # True: cộng theo điểm từng ý; False: barem không ghi điểm → dùng score của AI
    ai_score: float | None = None      # điểm AI tự cộng (để so sánh trong prototype 3.3)
    model: str | None = None


def round_score(score: float, points: float) -> float:
    return min(max(round(score / SCORE_STEP) * SCORE_STEP, 0.0), points)


def final_score(grade: EssayGrade, items: list[RubricItem], points: float, essay: str) -> GradeOutcome:
    essay_norm = _norm(essay)
    levels = {n: "không có" for n in range(1, len(items) + 1)}
    unsupported = []
    seen = set()
    for g in grade.items:
        if g.item not in levels or g.item in seen:      # số ý lạ / trả trùng → bỏ qua
            continue
        seen.add(g.item)
        quote = _norm(g.evidence)
        if WEIGHT[g.level] > 0 and (not quote or quote not in essay_norm):
            unsupported.append(g.item)
            continue
        levels[g.item] = g.level

    matched = [n for n, lv in levels.items() if WEIGHT[lv] > 0]
    total = rubric_points_total(items)
    if total > 0:
        raw = sum(items[n - 1].points * WEIGHT[lv] for n, lv in levels.items()) * points / total
    else:
        raw = grade.score if matched else 0.0
    return GradeOutcome(score=round_score(raw, points), feedback=grade.feedback.strip(),
                        matched_points=[items[n - 1].text for n in matched], levels=levels,
                        unsupported=unsupported, from_rubric=total > 0, ai_score=grade.score)


async def grade_essay(llm, *, question: str, rubric: str, points: float, essay: str,
                      passages_text: str) -> GradeOutcome:
    """Một lần gọi Gemini. Lỗi: GradeParseError (ValueError) hoặc lỗi của call_gemini — người gọi để score = null."""
    resp = await call_gemini(llm, build_grade_prompt(question, rubric, points, essay, passages_text), grade_config())
    outcome = final_score(parse_grade(resp), parse_rubric(rubric), points, essay)
    outcome.model = resp.model_version
    return outcome
