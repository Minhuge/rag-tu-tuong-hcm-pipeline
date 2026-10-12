"""
Chấm một câu tự luận đã nộp (roadmap 3.4): đọc bài → (AI chấm) → lưu điểm. Các quyết định ở ROADMAP_CHI_TIET 3.1.

  grade_essay_answer(submission_id, question_id, llm=…, qdrant=…, embedder=…)
    1. exams.essay_for_grading        câu đã có điểm (người tạo đề chấm tay / đã chấm) → bỏ qua, không gọi AI
    2. bài rỗng / quá ngắn            → 0 điểm, graded_by="auto", KHÔNG gọi Gemini (không tốn quota)
    3. câu không có rubric            → không chấm, chờ người tạo đề chấm tay
    3b. quota (3.9)                   người làm bài đã được AI chấm đủ AI_GRADE_PER_USER_PER_DAY câu hôm nay
                                      → không gọi Gemini, chờ lượt mới (quét định kỳ) hoặc người tạo đề chấm tay
    4. đoạn giáo trình                trang nguồn ± 1; câu không ghi trang → tìm theo nội dung câu hỏi
    5. rag.essay_grading.grade_essay  Gemini chấm từng ý, code cộng điểm, kẹp 0..points, làm tròn 0.25
    6. exams.save_essay_grade         graded_by="ai"; người tạo đề vừa chấm trong lúc chờ Gemini → không ghi đè

Lỗi khi lấy đoạn giáo trình / gọi Gemini → không ghi gì (score vẫn null), trả status "failed"; lần quét bù khi
khởi động server chấm lại. Gemini chạy 5–15 giây → async; database / Qdrant chạy trong thread.

Chạy nền (3.5):
  grade_submission(sid)   mọi câu tự luận chưa có điểm của một bài — router nộp bài gọi qua BackgroundTasks
  sweep_ungraded()        mọi bài "submitted" còn câu chưa chấm; bài đã đủ điểm mà chưa chốt thì chốt luôn.
  start_sweep(rag)        main.py: quét ngay lúc khởi động (BackgroundTasks mất khi server tắt giữa chừng), rồi quét
                          lại mỗi SWEEP_INTERVAL giây — câu hết quota được chấm khi sang ngày mới. Lần quét định kỳ
                          BỎ QUA câu AI vừa lỗi (không thử lại liên tục, quyết định 3.1); câu đó chỉ thử lại khi khởi động.
  Câu cuối cùng của bài có điểm → bài tự chuyển "graded" với tổng điểm (3.6, exams.save_essay_grade).
  Tối đa GRADE_CONCURRENCY lời gọi Gemini cùng lúc (cả server); một câu đang chấm thì lần gọi khác bỏ qua câu đó;
  lỗi bất kỳ ở một câu không làm dừng các câu khác.
Theo dõi trạng thái (3.8): with_grading_status thêm vào kết quả bài làm trạng thái từng câu + cờ still_grading để
giao diện hỏi lại định kỳ (polling) tới khi hết câu "grading".
"""
import asyncio
import os
import weakref
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from google.genai import errors as genai_errors
from qdrant_client import QdrantClient

from app import exams
from rag.essay_grading import EMPTY_FEEDBACK, grade_essay, page_passages, search_passages, too_short

Status = Literal["ai", "auto", "already_graded", "needs_creator", "quota", "failed", "busy"]
NO_RUBRIC = "Câu hỏi không có barem — chờ người tạo đề chấm tay."
AI_GRADE_PER_USER_PER_DAY = int(os.getenv("AI_GRADE_PER_USER_PER_DAY", "20"))
SWEEP_INTERVAL = float(os.getenv("SWEEP_INTERVAL", "1800"))     # giây; 0 = chỉ quét lúc khởi động
GRADE_CONCURRENCY = int(os.getenv("GRADE_CONCURRENCY", "2"))   # bậc miễn phí Gemini chỉ ~5 lời gọi/phút
GRADE_TIMEOUT = float(os.getenv("GRADE_TIMEOUT", "180"))       # một câu (gồm cả chờ 429 trong call_gemini)


# Số câu của mỗi người làm bài đang được AI chấm (đã giữ lượt, chưa lưu điểm). Đếm cả những câu này khi xét quota:
# không có nó, 2 câu chấm song song cùng thấy "còn 1 lượt" và cùng gọi Gemini.
_ai_running: Counter = Counter()


def _quota_message(limit: int) -> str:
    return f"Đã dùng hết {limit} lượt AI chấm tự luận hôm nay — chờ lượt mới (sau 0 giờ) hoặc người tạo đề chấm tay."


@dataclass
class EssayGradeResult:
    status: Status
    score: float | None = None
    feedback: str | None = None
    error: str | None = None


def _passages(qdrant: QdrantClient, embedder, ctx: dict) -> str:
    if ctx["source_page"] is not None:
        return page_passages(qdrant, ctx["source_page"])
    if embedder is not None:
        return search_passages(qdrant, embedder, ctx["question"])
    return ""   # không có trang, không có embedder → chấm theo barem, không đối chiếu giáo trình


async def _save(submission_id: int, question_id: int, status: Status, score: float, feedback: str
                ) -> EssayGradeResult:
    saved = await asyncio.to_thread(exams.save_essay_grade, submission_id, question_id,
                                    score=score, feedback=feedback, graded_by=status)
    return EssayGradeResult(status, score, feedback) if saved else EssayGradeResult("already_graded")


async def grade_essay_answer(submission_id: int, question_id: int, *, llm, qdrant: QdrantClient,
                             embedder=None) -> EssayGradeResult:
    """
    Chấm một câu tự luận của một bài đã nộp. Không ném lỗi khi AI / Qdrant lỗi (trả status "failed") để job chạy nền
    chấm tiếp các câu khác. NotFound / InvalidState (sai id, bài chưa nộp) vẫn ném ra: đó là lỗi của người gọi.
    """
    ctx = await asyncio.to_thread(exams.essay_for_grading, submission_id, question_id)
    if ctx["score"] is not None:
        return EssayGradeResult("already_graded", ctx["score"])
    if too_short(ctx["essay_text"]):
        return await _save(submission_id, question_id, "auto", 0.0, EMPTY_FEEDBACK)
    if not (ctx["rubric"] or "").strip():
        return EssayGradeResult("needs_creator", error=NO_RUBRIC)

    taker, limit = ctx["user_id"], AI_GRADE_PER_USER_PER_DAY
    used = await asyncio.to_thread(exams.ai_gradings_today, taker)
    if used + _ai_running[taker] >= limit:     # xét và giữ lượt liền nhau, không có await xen giữa
        return EssayGradeResult("quota", error=_quota_message(limit))
    _ai_running[taker] += 1
    try:
        return await _grade_with_ai(submission_id, question_id, ctx, llm, qdrant, embedder)
    finally:
        _ai_running[taker] -= 1
        if _ai_running[taker] <= 0:
            del _ai_running[taker]


async def _grade_with_ai(submission_id: int, question_id: int, ctx: dict, llm, qdrant: QdrantClient,
                         embedder) -> EssayGradeResult:
    label = f"[chấm tự luận] bài {submission_id}, câu {question_id}"
    try:
        passages = await asyncio.to_thread(_passages, qdrant, embedder, ctx)
    except Exception as e:      # Qdrant / Ollama tắt: không chấm thiếu giáo trình, để lần sau chấm lại
        print(f"  {label}: không lấy được đoạn giáo trình ({type(e).__name__}: {e})")
        return EssayGradeResult("failed", error=f"Không lấy được đoạn giáo trình: {e}")
    try:
        outcome = await grade_essay(llm, question=ctx["question"], rubric=ctx["rubric"], points=ctx["points"],
                                    essay=ctx["essay_text"], passages_text=passages)
    except (ValueError, RuntimeError, genai_errors.APIError) as e:   # JSON hỏng, bị chặn, hết quota / credit
        print(f"  {label}: AI chưa chấm được ({type(e).__name__}: {e})")
        return EssayGradeResult("failed", error=f"AI chưa chấm được: {e}")
    return await _save(submission_id, question_id, "ai", outcome.score, outcome.feedback)


# ---------- chạy nền (3.5) ----------
_limits: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
_in_flight: set[tuple[int, int]] = set()     # (submission_id, question_id) đang chấm trong process này
# Câu chưa chấm được ở lần gần nhất: "failed" (AI / Qdrant lỗi) hoặc "quota" (người làm bài hết lượt). score vẫn null
# nên database không phân biệt được với "đang chấm". Chỉ trong bộ nhớ: khởi động lại server thì quét bù chấm lại.
_stuck: dict[tuple[int, int], Status] = {}


def _limiter() -> asyncio.Semaphore:
    """Một semaphore cho mỗi event loop (server chỉ có một; test mỗi asyncio.run một loop mới)."""
    loop = asyncio.get_running_loop()
    if loop not in _limits:
        _limits[loop] = asyncio.Semaphore(GRADE_CONCURRENCY)
    return _limits[loop]


async def _grade_one(submission_id: int, question_id: int, **clients) -> EssayGradeResult:
    key = (submission_id, question_id)
    if key in _in_flight:             # job sau khi nộp và lần quét bù cùng gặp một câu → chỉ chấm một lần
        return EssayGradeResult("busy")
    _in_flight.add(key)
    try:
        async with _limiter():
            result = await asyncio.wait_for(grade_essay_answer(submission_id, question_id, **clients), GRADE_TIMEOUT)
    except Exception as e:            # chạy nền: không để một câu lỗi (kể cả bài vừa bị xoá) làm dừng câu khác
        print(f"  [chấm tự luận] bài {submission_id}, câu {question_id}: lỗi {type(e).__name__}: {e}")
        result = EssayGradeResult("failed", error=f"{type(e).__name__}: {e}")
    finally:
        _in_flight.discard(key)
    if result.status in ("failed", "quota"):
        _stuck[key] = result.status
    elif result.status != "busy":
        _stuck.pop(key, None)
    return result


# ---------- theo dõi trạng thái (3.8) ----------
GRADING_MESSAGES = {
    "grading": "AI đang chấm…",
    "failed": "AI chưa chấm được câu này — sẽ thử lại sau, hoặc người tạo đề chấm tay.",
    "needs_creator": "Câu này chờ người tạo đề chấm tay.",
}


def with_grading_status(result: dict) -> dict:
    """
    Thêm vào kết quả bài làm (exams.get_my_result / get_submission_for_creator):
      - từng câu: grading_status "graded" | "grading" | "failed" | "quota" | "needs_creator" + grading_message;
      - cả bài: grading = {graded, total, still_grading}. still_grading = còn câu "grading" → hỏi lại sau vài giây;
        False → dừng hỏi (đã chấm xong, hoặc chỉ còn câu chờ người tạo đề / AI lỗi / hết lượt).
    Bài chưa nộp: không có trạng thái chấm (grading = None).
    """
    questions = result["questions"]
    if result["status"] == "in_progress":
        return {**result, "grading": None}
    for q in questions:
        key = (result["id"], q["id"])
        if q["grading_status"] == "grading" and key in _stuck and key not in _in_flight:
            q["grading_status"] = _stuck[key]
        q["grading_message"] = (_quota_message(AI_GRADE_PER_USER_PER_DAY) if q["grading_status"] == "quota"
                                else GRADING_MESSAGES.get(q["grading_status"]))
    graded = sum(q["grading_status"] == "graded" for q in questions)
    return {**result, "grading": {"graded": graded, "total": len(questions),
                                  "still_grading": any(q["grading_status"] == "grading" for q in questions)}}


async def grade_submission(submission_id: int, *, llm, qdrant: QdrantClient, embedder=None,
                           question_ids: list[int] | None = None) -> dict[int, EssayGradeResult]:
    """Chấm (song song, có giới hạn) mọi câu tự luận chưa có điểm của một bài. → {question_id: kết quả}."""
    if question_ids is None:
        question_ids = (await asyncio.to_thread(exams.ungraded_essays, submission_id)).get(submission_id, [])
    if not question_ids:
        return {}
    results = await asyncio.gather(*(_grade_one(submission_id, qid, llm=llm, qdrant=qdrant, embedder=embedder)
                                     for qid in question_ids))
    out = dict(zip(question_ids, results))
    counts = {st: sum(r.status == st for r in results) for st in ("ai", "auto", "needs_creator", "quota", "failed")}
    print(f"  [chấm tự luận] bài {submission_id}: " + ", ".join(f"{n} {st}" for st, n in counts.items() if n))
    return out


async def sweep_ungraded(*, llm, qdrant: QdrantClient, embedder=None, skip_failed: bool = False) -> int:
    """
    Quét bù: chốt các bài đã đủ điểm, rồi chấm mọi bài đã nộp còn câu tự luận chưa có điểm. → số bài đã chấm.
    skip_failed: bỏ qua câu AI vừa lỗi (lần quét định kỳ); câu hết quota vẫn xét lại (rẻ: chỉ đếm, không gọi Gemini).
    """
    finalized = await asyncio.to_thread(exams.finalize_complete_submissions)
    if finalized:
        print(f"[chấm tự luận] quét bù: chốt điểm {len(finalized)} bài đã chấm đủ")
    pending = await asyncio.to_thread(exams.ungraded_essays)
    if skip_failed:
        pending = {sid: kept for sid, qids in pending.items()
                   if (kept := [q for q in qids if _stuck.get((sid, q)) != "failed"])}
    if pending:
        print(f"[chấm tự luận] quét bù: {len(pending)} bài, {sum(map(len, pending.values()))} câu chưa chấm")
    for sid, qids in pending.items():      # lần lượt từng bài; các câu trong bài vẫn song song (có giới hạn)
        await grade_submission(sid, llm=llm, qdrant=qdrant, embedder=embedder, question_ids=qids)
    return len(pending)


async def _safe_sweep(**kwargs) -> int:
    try:
        return await sweep_ungraded(**kwargs)
    except Exception as e:      # database tạm mất kết nối… → không làm chết vòng quét định kỳ
        print(f"[chấm tự luận] quét bù lỗi: {type(e).__name__}: {e}")
        return 0


async def sweep_forever(*, interval: float, **clients) -> int:
    """Quét ngay (thử lại cả câu AI lỗi), rồi cứ interval giây quét lại (bỏ qua câu AI lỗi). interval 0 → quét một lần."""
    count = await _safe_sweep(**clients)
    while interval > 0:
        await asyncio.sleep(interval)
        await _safe_sweep(**clients, skip_failed=True)
    return count


def start_sweep(rag, interval: float | None = None) -> asyncio.Task:
    """Chạy quét bù nền lúc khởi động — không bắt server chờ chấm xong mới nhận request."""
    return asyncio.create_task(sweep_forever(
        interval=SWEEP_INTERVAL if interval is None else interval,
        llm=rag.llm, qdrant=rag.retriever.client, embedder=rag.retriever.embedder))
