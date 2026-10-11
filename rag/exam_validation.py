"""
Kiểm tra đề do Gemini tạo (roadmap 2.6). Câu không đạt thì bỏ RIÊNG câu đó, không bỏ cả đề.

  Tầng 1 — cấu trúc, bằng code (không tốn quota):
    trắc nghiệm: câu hỏi / giải thích không rỗng; đúng 4 lựa chọn A–D, không rỗng, không trùng nội dung;
                 correct_label nằm trong các nhãn; giải thích không nhắc chữ cái (lựa chọn đã bị xáo)
    tự luận    : câu hỏi, rubric không rỗng; points > 0 (rubric ghi điểm từng ý → points lấy theo tổng đó)
    mọi câu    : source_page thuộc các trang đã gửi cho Gemini
  Tầng 2 — nội dung, Gemini làm giám khảo, MỘT lần gọi cho mọi câu qua được tầng 1.
    Trắc nghiệm kết hợp hai cách:
      tự giải   — giám khảo KHÔNG thấy đáp án đã đánh dấu, tự chọn chosen_label theo giáo trình
                  → khác correct_label thì loại (không bị đáp án có sẵn dẫn dắt)
      thẩm định — other_correct_option: ngoài lựa chọn đó còn lựa chọn nào cũng đúng không → có thì loại
                  (tự giải không bắt được câu có 2 đáp án nếu giám khảo tình cờ chọn trùng)
    supported: giáo trình khẳng định rõ lựa chọn đã chọn (trắc nghiệm) / câu hỏi + rubric (tự luận)
    duplicate_of: cùng chủ đề chính với một câu đứng trước, kể cả khác dạng

Thử trên đề đã lưu:  python -m scripts.gen_exam_proto --validate outputs/gen_chapter3_X.json
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, ValidationError

from rag.pipeline import call_gemini, gemini_config
from rag.prompts import EXAM_JUDGE_PROMPT, EXAM_JUDGE_SYSTEM

if TYPE_CHECKING:   # tránh import vòng: exam_generation import module này
    from rag.exam_generation import GeneratedEssay, GeneratedExam, GeneratedMCQ

Label = Literal["A", "B", "C", "D"]   # dùng chung với schema đề (exam_generation import từ đây)

_LETTER_REF = re.compile(r"\b(đáp án|phương án|lựa chọn|câu)\s+[A-D]\b", re.IGNORECASE)
_RUBRIC_POINTS = re.compile(r"\(\s*(\d+(?:[.,]\d+)?)\s*điểm\s*\)", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def rubric_total(rubric: str) -> float:
    """Cộng các "(x điểm)" trong rubric; 0 nếu rubric không ghi điểm theo dạng đó."""
    return sum(float(x.replace(",", ".")) for x in _RUBRIC_POINTS.findall(rubric))


# ---------- tầng 1: cấu trúc ----------
def mcq_issues(q: GeneratedMCQ, pages: set[int]) -> list[str]:
    issues = []
    if not q.question.strip():
        issues.append("câu hỏi rỗng")
    labels = [o.label for o in q.options]
    if sorted(labels) != ["A", "B", "C", "D"]:
        issues.append(f"nhãn lựa chọn phải là A, B, C, D (nhận {', '.join(labels)})")
    if q.correct_label not in labels:
        issues.append(f"đáp án {q.correct_label} không có trong các lựa chọn")
    contents = [_norm(o.content) for o in q.options]
    if any(not c for c in contents):
        issues.append("có lựa chọn rỗng")
    elif len(set(contents)) < len(contents):
        issues.append("có lựa chọn trùng nội dung")
    if not q.explanation.strip():
        issues.append("thiếu giải thích")
    elif _LETTER_REF.search(q.explanation):
        issues.append("giải thích nhắc chữ cái của lựa chọn (sai sau khi xáo)")
    if q.source_page not in pages:
        issues.append(f"trang {q.source_page} không nằm trong các trang đã gửi")
    return issues


def fix_essay_points(q: GeneratedEssay) -> GeneratedEssay:
    """Rubric ghi điểm từng ý mà points lệch tổng → lấy theo rubric (phép cộng thì code làm, không bỏ câu)."""
    total = rubric_total(q.rubric)
    return q.model_copy(update={"points": total}) if total > 0 and abs(total - q.points) > 1e-6 else q


def essay_issues(q: GeneratedEssay, pages: set[int]) -> list[str]:
    issues = []
    if not q.question.strip():
        issues.append("câu hỏi rỗng")
    if not q.rubric.strip():
        issues.append("rubric rỗng")
    if q.points <= 0:
        issues.append(f"points = {q.points:g}, phải > 0")
    if q.source_page not in pages:
        issues.append(f"trang {q.source_page} không nằm trong các trang đã gửi")
    return issues


# ---------- tầng 2: giám khảo ----------
# reason đứng trước các kết luận: Gemini viết theo thứ tự field → nêu căn cứ trước rồi mới quyết định.
class JudgeVerdict(BaseModel):
    index: int
    reason: str
    chosen_label: Label | None = Field(None, description="Trắc nghiệm: lựa chọn đúng theo giáo trình; "
                                                         "không có lựa chọn nào đúng hoặc câu tự luận thì null")
    supported: bool
    other_correct_option: bool
    duplicate_of: int | None = Field(None, description="Số thứ tự câu đứng trước hỏi cùng nội dung, không có thì null")


class JudgeResult(BaseModel):
    verdicts: list[JudgeVerdict]


def _format_mcq(n: int, q: GeneratedMCQ) -> str:
    # cố ý không ghi đáp án: giám khảo tự giải rồi code so với correct_label
    opts = "\n".join(f"{o.label}. {o.content}" for o in q.options)
    return f"Câu {n} (trắc nghiệm, trang {q.source_page})\nHỏi: {q.question}\n{opts}"


def _format_essay(n: int, q: GeneratedEssay) -> str:
    return (f"Câu {n} (tự luận, trang {q.source_page}, {q.points:g} điểm)\nHỏi: {q.question}\n"
            f"Rubric:\n{q.rubric}")


def build_judge_prompt(passages_text: str, mcq: list[GeneratedMCQ], essay: list[GeneratedEssay]) -> str:
    items = [_format_mcq(i, q) for i, q in enumerate(mcq, 1)]
    items += [_format_essay(i, q) for i, q in enumerate(essay, len(mcq) + 1)]
    return EXAM_JUDGE_PROMPT.format(passages=passages_text, questions="\n\n".join(items))


async def judge(llm, passages_text: str, mcq: list[GeneratedMCQ],
                essay: list[GeneratedEssay]) -> dict[int, JudgeVerdict]:
    """Một lần gọi Gemini cho cả đề → {số thứ tự câu: kết luận}. Câu đánh số: trắc nghiệm trước, tự luận sau."""
    resp = await call_gemini(llm, build_judge_prompt(passages_text, mcq, essay),
                             gemini_config(EXAM_JUDGE_SYSTEM, schema=JudgeResult))
    if isinstance(resp.parsed, JudgeResult):
        result = resp.parsed
    else:
        try:
            result = JudgeResult.model_validate_json(resp.text or "")
        except ValidationError as e:
            raise ValueError(f"Giám khảo trả về kết quả không đúng schema: {e.errors()[0]['msg']}") from e
    return {v.index: v for v in result.verdicts}


# ---------- gộp hai tầng ----------
@dataclass
class Rejection:
    kind: str            # "mcq" | "essay"
    question: str
    stage: str           # "cấu trúc" | "giám khảo"
    reasons: list[str]


@dataclass
class ValidationResult:
    mcq: list = field(default_factory=list)       # các câu đạt, giữ thứ tự
    essay: list = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    judge_calls: int = 0


def _judge_failures(v: JudgeVerdict | None, q: GeneratedMCQ | GeneratedEssay, kind: str) -> list[str]:
    if v is None:
        return ["giám khảo không trả kết quả cho câu này"]
    out = []
    if kind == "mcq":
        if v.chosen_label is None:
            out.append("giám khảo không thấy lựa chọn nào đúng theo giáo trình")
        elif v.chosen_label != q.correct_label:
            out.append(f"giám khảo tự giải ra {v.chosen_label}, đề đánh dấu {q.correct_label}")
        elif not v.supported:
            out.append("giáo trình không khẳng định rõ đáp án")
        if v.other_correct_option:
            out.append("có lựa chọn khác cũng đúng")
    elif not v.supported:
        out.append("giáo trình không xác nhận câu hỏi / rubric")
    if v.duplicate_of is not None:
        out.append(f"trùng nội dung câu {v.duplicate_of}")
    return out + ([f"giám khảo: {v.reason}"] if out else [])


async def validate_exam(llm, exam: GeneratedExam, pages: set[int], passages_text: str,
                        use_judge: bool = True) -> ValidationResult:
    """Tầng 1 cho mọi câu, rồi tầng 2 (một lần gọi) cho các câu qua tầng 1. use_judge=False → chỉ tầng 1."""
    res = ValidationResult()
    mcq_ok, essay_ok = [], []
    for kind, items, check, ok in (("mcq", exam.mcq, mcq_issues, mcq_ok),
                                   ("essay", [fix_essay_points(q) for q in exam.essay], essay_issues, essay_ok)):
        for q in items:
            issues = check(q, pages)
            if issues:
                res.rejected.append(Rejection(kind, q.question, "cấu trúc", issues))
            else:
                ok.append(q)

    if not use_judge or not (mcq_ok or essay_ok):
        res.mcq, res.essay = mcq_ok, essay_ok
        return res
    verdicts = await judge(llm, passages_text, mcq_ok, essay_ok)
    res.judge_calls = 1
    for kind, items, offset in (("mcq", mcq_ok, 0), ("essay", essay_ok, len(mcq_ok))):
        for i, q in enumerate(items, offset + 1):
            failures = _judge_failures(verdicts.get(i), q, kind)
            if failures:
                res.rejected.append(Rejection(kind, q.question, "giám khảo", failures))
            else:
                (res.mcq if kind == "mcq" else res.essay).append(q)
    return res
