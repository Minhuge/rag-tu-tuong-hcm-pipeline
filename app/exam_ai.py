"""
Tạo đề bằng AI (roadmap 2.8): lấy đoạn giáo trình → Gemini tạo đề + kiểm tra → lưu đề NHÁP.

  generate_exam_draft(user_id, chapter, n_mcq, n_essay, llm=…)
    1. kiểm tra input (số câu, chương) — trước mọi lần gọi Qdrant / Gemini
    2. quota (2.9)               mỗi người một đề đang tạo, tối đa AI_GEN_PER_USER_PER_DAY đề AI mỗi ngày
                                 (giờ Việt Nam) — hết lượt thì dừng ở đây, không tốn lượt Gemini
       get_chapter_passages      các đoạn trải đều chương (rag/exam_generation.py)
    3. generate_exam             Gemini tạo đề, xáo lựa chọn, kiểm tra cấu trúc + giám khảo + loại câu trùng,
                                 thiếu câu thì tạo bù 1 lần
    4. exams.create_generated_exam   lưu qua đúng luật tạo đề của Phase 1, origin="ai", status="draft",
                                     một transaction (không ghi thẳng vào database); đếm lại quota lúc lưu
  Quota đếm số đề AI đang có: xoá đề thì được trả lượt, tạo hỏng (không lưu gì) thì không mất lượt.
  Mỗi lần gọi Gemini vẫn đi qua call_gemini (rag/pipeline.py): chờ khi 429 theo phút, đổi model khi hết lượt ngày.

Không bao giờ publish: người tạo xem lại, sửa (PATCH câu hỏi) rồi tự publish như đề tay.
Gemini 10–30 giây → hàm async; Qdrant và database là code đồng bộ nên chạy trong thread, không chặn event loop.

Lỗi (router 2.10 đổi thành mã HTTP):
  ValueError        400  số câu ngoài giới hạn, chương không tồn tại
  QuotaExceeded     429  hết lượt hôm nay, hoặc đang có một đề khác đang tạo
  GenerationFailed  503  Qdrant / Gemini lỗi, hoặc không câu nào đạt kiểm tra — không lưu gì, thử lại sau
"""
import asyncio
import os
import random
from dataclasses import asdict

from google.genai import errors as genai_errors
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app import exams
from rag.exam_generation import (Chapter, ChapterPassages, GeneratedExam, generate_exam, get_chapter_passages,
                                 resolve_chapter)

MAX_MCQ = 20
MAX_ESSAY = 3
MCQ_POINTS = 1.0
AI_GEN_PER_USER_PER_DAY = int(os.getenv("AI_GEN_PER_USER_PER_DAY", "5"))

# user đang có đề đang tạo (trong process này). Không có nó, 5 request gửi cùng lúc đều qua bước đếm
# (chưa đề nào được lưu) và cùng tốn lượt Gemini. Chạy nhiều worker thì mỗi worker một tập riêng —
# lúc đó vẫn còn bước đếm lại khi lưu (create_generated_exam) giữ đúng giới hạn số đề.
_generating: set[int] = set()


class GenerationFailed(RuntimeError):
    """Phía AI không tạo được đề (không phải lỗi của người gọi). Không có gì được lưu."""


def check_counts(n_mcq: int, n_essay: int) -> None:
    if not 0 <= n_mcq <= MAX_MCQ:
        raise ValueError(f"Số câu trắc nghiệm phải từ 0 đến {MAX_MCQ}")
    if not 0 <= n_essay <= MAX_ESSAY:
        raise ValueError(f"Số câu tự luận phải từ 0 đến {MAX_ESSAY}")
    if n_mcq + n_essay == 0:
        raise ValueError("Cần ít nhất một câu hỏi")


def ai_quota(user_id: int) -> dict:
    """Lượt tạo đề AI hôm nay + giới hạn số câu — trang tạo đề hiện TRƯỚC khi người dùng bấm (4.4).
    busy = người này đang có một đề đang tạo (vd. F5 giữa chừng) → tạo thêm sẽ bị 429."""
    limit = AI_GEN_PER_USER_PER_DAY
    return {"limit": limit, "remaining": max(limit - exams.ai_exams_today(user_id), 0),
            "busy": user_id in _generating, "max_mcq": MAX_MCQ, "max_essay": MAX_ESSAY}


def to_questions(exam: GeneratedExam) -> list[dict]:
    """Đề Gemini (đã kiểm tra) → tham số của exams.add_question. Trắc nghiệm trước, tự luận sau."""
    mcq = [{"type": "mcq", "content": q.question, "points": MCQ_POINTS, "explanation": q.explanation,
            "source_page": q.source_page,
            "options": [{"label": o.label, "content": o.content, "is_correct": o.label == q.correct_label}
                        for o in q.options]}
           for q in exam.mcq]
    essay = [{"type": "essay", "content": q.question, "points": q.points, "rubric": q.rubric,
              "source_page": q.source_page}
             for q in exam.essay]
    return mcq + essay


def _description(src: ChapterPassages) -> str:
    pages = src.pages
    span = f"trang {pages[0]}–{pages[-1]}" if pages else "không rõ trang"
    return (f"Đề nháp do AI tạo từ giáo trình, {src.chapter.key} ({span}). "
            "Hãy xem lại từng câu trước khi xuất bản.")


async def generate_exam_draft(user_id: int, chapter: int | str, n_mcq: int, n_essay: int, *, llm,
                              title: str | None = None, qdrant: QdrantClient | None = None,
                              rng: random.Random | None = None) -> dict:
    """
    Đề nháp đã lưu (giống exams.get_exam_for_creator) + khoá "generation": yêu cầu ban đầu, notice khi thiếu câu,
    các câu bị loại kèm lý do, model đã dùng, số lần gọi Gemini, lượt còn lại hôm nay.
    llm: genai.Client; qdrant: None → get_qdrant() theo .env.
    """
    check_counts(n_mcq, n_essay)
    c = resolve_chapter(chapter)
    title = (title or "").strip() or f"{c.key}: {c.title}"   # để trống → tên chương

    if user_id in _generating:   # kiểm tra và thêm liền nhau, không có await xen giữa → không bị chen ngang
        raise exams.QuotaExceeded("Bạn đang tạo một đề khác. Đợi đề đó xong rồi thử lại.")
    _generating.add(user_id)
    try:
        return await _generate(user_id, c, n_mcq, n_essay, llm, title, qdrant, rng)
    finally:
        _generating.discard(user_id)


async def _generate(user_id: int, c: Chapter, n_mcq: int, n_essay: int, llm, title: str,
                    qdrant: QdrantClient | None, rng: random.Random | None) -> dict:
    limit = AI_GEN_PER_USER_PER_DAY
    remaining = await asyncio.to_thread(exams.check_ai_quota, user_id, limit)

    try:
        src = await asyncio.to_thread(get_chapter_passages, c.number, client=qdrant)
    except LookupError as e:                                    # chương chưa có đoạn nào trong Qdrant
        raise GenerationFailed(str(e)) from e
    except (ResponseHandlingException, UnexpectedResponse) as e:
        raise GenerationFailed("Không kết nối được kho giáo trình (Qdrant). Thử lại sau.") from e

    try:
        res = await generate_exam(llm, src, n_mcq, n_essay, rng=rng)
    except (ValueError, RuntimeError, genai_errors.APIError) as e:   # JSON hỏng, giám khảo lỗi, hết quota / credit
        raise GenerationFailed(f"AI chưa tạo được đề: {e}") from e
    if not res.exam.mcq and not res.exam.essay:
        raise GenerationFailed("AI chưa tạo được câu nào đạt kiểm tra. Thử lại hoặc giảm số câu.")

    try:
        saved = await asyncio.to_thread(exams.create_generated_exam, user_id, title, to_questions(res.exam),
                                        daily_limit=limit, description=_description(src))
    except ValueError as e:   # câu đã qua kiểm tra mà vẫn sai luật Phase 1 → lỗi phía AI, không phải người gọi
        raise GenerationFailed(f"Đề AI tạo ra không hợp lệ: {e}") from e

    return {**saved, "generation": {
        "chapter": {"number": c.number, "chapter": c.key, "title": c.title},
        "requested": {"mcq": n_mcq, "essay": n_essay},
        "notice": res.notice,
        "rejected": [asdict(r) for r in res.rejected],
        "models": res.models,
        "calls": res.calls,
        "quota": {"limit": limit, "remaining": remaining - 1},   # lượt còn lại hôm nay, sau đề này
    }}
