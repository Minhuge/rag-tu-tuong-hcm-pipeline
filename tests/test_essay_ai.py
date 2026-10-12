"""
Test chấm tự luận đã nộp (app/essay_ai.py: 3.4 chấm một câu, 3.5 chạy nền + quét bù) và các hàm
ghi điểm trong app/exams.py.
Database tạm + Qdrant trong bộ nhớ (Chương III trang 38–47) + Gemini giả → không cần Docker, không tốn quota.

Chạy:  pytest -q tests/test_essay_ai.py
"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, db, deps, essay_ai, exams
from app import main as api
from rag import essay_grading as eg

A, B = 1, 2          # A ra đề, B làm bài
RUBRIC = "- Nêu ý thứ nhất về độc lập dân tộc (1 điểm)\n- Nêu ý thứ hai về chủ nghĩa xã hội (1 điểm)"
ESSAY = "Độc lập dân tộc là mục tiêu trước hết. Chủ nghĩa xã hội là điều kiện giữ vững độc lập."


@pytest.fixture(autouse=True)
def fresh_job_state():
    """Bộ nhớ của job (câu đang chấm / vừa lỗi) theo id; database tạm mỗi test dùng lại id 1, 2… → xoá trước mỗi test."""
    essay_ai._in_flight.clear()
    essay_ai._stuck.clear()
    essay_ai._ai_running.clear()


@pytest.fixture(autouse=True)
def users(db_engine):
    with Session(db_engine) as s, s.begin():
        s.add_all([db.User(id=uid, email=f"{n}@example.com", password_hash="x", display_name=n.upper())
                   for uid, n in ((A, "a"), (B, "b"))])


def essay_q(content="Trình bày mối quan hệ giữa độc lập dân tộc và chủ nghĩa xã hội.", **kw):
    return {"type": "essay", "content": content, "rubric": RUBRIC, "points": 2, "source_page": 40, **kw}


def submitted(*questions, answers=None, submit=True):
    """A tạo + xuất bản đề công khai, B làm bài. answers: {thứ tự câu: bài viết}. → (submission_id, [question_id])."""
    exam = exams.create_exam(A, "Đề tự luận", visibility="public")
    qids = [exams.add_question(A, exam["id"], **q)["id"] for q in questions]
    exams.publish_exam(A, exam["id"])
    sid = exams.start_submission(B, exam["id"])["id"]
    for i, text in (answers or {}).items():
        exams.save_answer(B, sid, qids[i], essay_text=text)
    if submit:
        exams.submit(B, sid)
    return sid, qids


def ai_grade(*items, feedback="Bạn đã nêu ý 1.", score=1.0) -> str:
    return eg.EssayGrade(items=[eg.ItemGrade(item=n, evidence=ev, level=lv) for n, ev, lv in items],
                         feedback=feedback, score=score).model_dump_json()


def grade(llm, sid, qid, qdrant, **kw):
    return asyncio.run(essay_ai.grade_essay_answer(sid, qid, llm=llm, qdrant=qdrant, **kw))


def answer_row(sid, qid):
    with Session(db.engine) as s:
        a = s.scalar(select(db.SubmissionAnswer).where(db.SubmissionAnswer.submission_id == sid,
                                                       db.SubmissionAnswer.question_id == qid))
        return None if a is None else {"score": a.score, "feedback": a.feedback, "graded_by": a.graded_by,
                                       "graded_at": a.graded_at}


# ---------- AI chấm và lưu ----------
def test_ai_grade_is_saved(fake_gemini, qdrant):
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY})
    llm = fake_gemini([ai_grade((1, "Độc lập dân tộc là mục tiêu trước hết", "đủ"),
                                (2, "Chủ nghĩa xã hội là điều kiện", "một phần"), feedback="  Khá tốt. ")])
    res = grade(llm, sid, qid, qdrant)
    assert (res.status, res.score, res.feedback) == ("ai", 1.5, "Khá tốt.")
    row = answer_row(sid, qid)
    assert (row["score"], row["graded_by"], row["feedback"]) == (1.5, "ai", "Khá tốt.") and row["graded_at"]

    prompt = llm.calls[0][0]
    assert "[trang 40]" in prompt and "[trang 39]" in prompt and "[trang 41]" in prompt   # trang nguồn ± 1
    assert "[trang 43]" not in prompt and ESSAY in prompt and 'diem_toi_da="2"' in prompt

    result = exams.get_my_result(B, sid)["questions"][0]                     # B thấy điểm + nhận xét
    assert (result["score"], result["feedback"], result["graded_by"]) == (1.5, "Khá tốt.", "ai")


def test_score_is_clamped_to_question_points(fake_gemini, qdrant):
    """Roadmap 3.10: AI cho 15 điểm câu 10 điểm → lưu 10."""
    sid, (qid,) = submitted(essay_q(rubric="Nêu đủ các ý chính", points=10), answers={0: ESSAY})
    res = grade(fake_gemini([ai_grade((1, "Độc lập dân tộc là mục tiêu trước hết", "đủ"), score=15)]),
                sid, qid, qdrant)
    assert res.score == 10 and answer_row(sid, qid)["score"] == 10


def test_grading_twice_does_not_call_ai_again(fake_gemini, qdrant):
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY})
    llm = fake_gemini([ai_grade((1, "Độc lập dân tộc là mục tiêu trước hết", "đủ"))])
    grade(llm, sid, qid, qdrant)
    again = grade(llm, sid, qid, qdrant)
    assert (again.status, again.score) == ("already_graded", 1.0) and len(llm.calls) == 1


# ---------- không gọi Gemini ----------
@pytest.mark.parametrize("answers", [{}, {0: "   "}, {0: "không biết ạ"}])     # bỏ trống / khoảng trắng / quá ngắn
def test_blank_or_short_essay_gets_zero_without_ai(fake_gemini, qdrant, answers):
    sid, (qid,) = submitted(essay_q(), answers=answers)
    llm = fake_gemini([])
    res = grade(llm, sid, qid, qdrant)
    assert (res.status, res.score) == ("auto", 0.0) and llm.calls == []
    row = answer_row(sid, qid)                                                 # câu bỏ trống: tạo dòng mới
    assert (row["score"], row["graded_by"], row["feedback"]) == (0.0, "auto", eg.EMPTY_FEEDBACK)


def test_no_rubric_waits_for_creator(fake_gemini, qdrant):
    sid, (qid,) = submitted(essay_q(rubric=None), answers={0: ESSAY})
    llm = fake_gemini([])
    res = grade(llm, sid, qid, qdrant)
    assert res.status == "needs_creator" and llm.calls == [] and answer_row(sid, qid)["score"] is None


# ---------- lỗi → không ghi gì, chấm lại sau ----------
def test_ai_failure_leaves_score_null(fake_gemini, qdrant):
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY})
    res = grade(fake_gemini(["{hỏng"]), sid, qid, qdrant)
    assert res.status == "failed" and "AI chưa chấm được" in res.error
    assert answer_row(sid, qid)["score"] is None
    res = grade(fake_gemini([ai_grade((1, "Độc lập dân tộc là mục tiêu trước hết", "đủ"))]), sid, qid, qdrant)
    assert res.status == "ai"                                                   # lần sau chấm được


def test_qdrant_failure_does_not_grade_without_textbook(fake_gemini):
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY})
    llm = fake_gemini([])
    res = grade(llm, sid, qid, qdrant=object())                               # client hỏng
    assert res.status == "failed" and "giáo trình" in res.error and llm.calls == []
    assert answer_row(sid, qid)["score"] is None


# ---------- người tạo đề chấm trước → AI không ghi đè ----------
def test_creator_grade_during_ai_call_is_not_overwritten(fake_gemini, qdrant):
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY})

    class CreatorGradesMeanwhile(fake_gemini):
        async def generate_content(self, model, contents, config):
            with Session(db.engine) as s, s.begin():                           # người tạo đề chấm tay lúc AI đang chạy
                a = s.scalar(select(db.SubmissionAnswer).where(db.SubmissionAnswer.submission_id == sid))
                a.score, a.feedback, a.graded_by = 2.0, "Chấm tay.", "creator"
            return await super().generate_content(model, contents, config)

    llm = CreatorGradesMeanwhile([ai_grade((1, "Độc lập dân tộc là mục tiêu trước hết", "đủ"))])
    res = grade(llm, sid, qid, qdrant)
    assert res.status == "already_graded"
    assert (answer_row(sid, qid)["score"], answer_row(sid, qid)["graded_by"]) == (2.0, "creator")


def test_already_creator_graded_skips_ai(fake_gemini, qdrant):
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY})
    with Session(db.engine) as s, s.begin():
        a = s.scalar(select(db.SubmissionAnswer).where(db.SubmissionAnswer.submission_id == sid))
        a.score, a.graded_by = 0.5, "creator"
    llm = fake_gemini([])
    assert grade(llm, sid, qid, qdrant).status == "already_graded" and llm.calls == []


# ---------- câu không ghi trang ----------
class FakeEmbedder:
    def embed_query(self, text):
        return [1.0, 0.0]


def test_question_without_page_searches_by_content(fake_gemini, qdrant):
    sid, (qid,) = submitted(essay_q(source_page=None), answers={0: ESSAY})
    llm = fake_gemini([ai_grade((1, "Độc lập dân tộc là mục tiêu trước hết", "đủ"))])
    grade(llm, sid, qid, qdrant, embedder=FakeEmbedder())
    assert llm.calls[0][0].count("[trang ") == eg.SEARCH_K


def test_question_without_page_and_no_embedder_grades_on_rubric(fake_gemini, qdrant):
    sid, (qid,) = submitted(essay_q(source_page=None), answers={0: ESSAY})
    llm = fake_gemini([ai_grade((1, "Độc lập dân tộc là mục tiêu trước hết", "đủ"))])
    assert grade(llm, sid, qid, qdrant).status == "ai"
    assert "(không có đoạn giáo trình)" in llm.calls[0][0]


# ---------- sai id / sai trạng thái ----------
def test_wrong_ids_and_unsubmitted(fake_gemini, qdrant):
    mcq = {"type": "mcq", "content": "Trắc nghiệm?",
           "options": [{"label": "A", "content": "x", "is_correct": True}, {"label": "B", "content": "y"}]}
    sid, (eq, mq) = submitted(essay_q(), mcq, answers={0: ESSAY})
    llm = fake_gemini([])
    for bad_sid, bad_qid in ((sid, mq), (sid, 9999), (9999, eq)):              # câu trắc nghiệm / không tồn tại
        with pytest.raises(exams.NotFound):
            grade(llm, bad_sid, bad_qid, qdrant)
    open_sid, (oq,) = submitted(essay_q(), answers={0: ESSAY}, submit=False)
    with pytest.raises(exams.InvalidState):
        grade(llm, open_sid, oq, qdrant)
    assert llm.calls == []


def test_save_essay_grade_guards():
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY})
    with pytest.raises(ValueError, match="graded_by"):
        exams.save_essay_grade(sid, qid, score=1, feedback="x", graded_by="creator")
    with pytest.raises(ValueError, match="ngoài khoảng"):
        exams.save_essay_grade(sid, qid, score=2.5, feedback="x", graded_by="ai")
    assert exams.save_essay_grade(sid, qid, score=1, feedback="x", graded_by="ai") is True
    assert exams.save_essay_grade(sid, qid, score=2, feedback="y", graded_by="ai") is False   # không ghi đè
    assert answer_row(sid, qid)["score"] == 1


# ====================== chạy nền (3.5) ======================
GOOD = ai_grade((1, "Độc lập dân tộc là mục tiêu trước hết", "đủ"))


def clients(llm, qdrant):
    return {"llm": llm, "qdrant": qdrant}


def test_ungraded_essays_lists_only_submitted_unscored_essays():
    mcq = {"type": "mcq", "content": "Trắc nghiệm?",
           "options": [{"label": "A", "content": "x", "is_correct": True}, {"label": "B", "content": "y"}]}
    s1, (e1, m1, e2) = submitted(essay_q("Câu 1?"), mcq, essay_q("Câu 2?"), answers={0: ESSAY})   # câu 2 bỏ trống
    s2, (e3,) = submitted(essay_q("Câu 3?"), answers={0: ESSAY})
    open_sid, _ = submitted(essay_q("Câu 4?"), answers={0: ESSAY}, submit=False)                  # chưa nộp
    exams.save_essay_grade(s2, e3, score=1, feedback="x", graded_by="ai")                         # đã chấm
    assert exams.ungraded_essays() == {s1: [e1, e2]}
    assert exams.ungraded_essays(s1) == {s1: [e1, e2]} and exams.ungraded_essays(s2) == {}


def test_grade_submission_grades_every_essay(fake_gemini, qdrant):
    sid, (q1, q2, q3) = submitted(essay_q("Câu 1?"), essay_q("Câu 2?"), essay_q("Câu 3?", rubric=None),
                                  answers={0: ESSAY, 2: ESSAY})                                 # câu 2 bỏ trống
    llm = fake_gemini([GOOD])
    out = asyncio.run(essay_ai.grade_submission(sid, **clients(llm, qdrant)))
    assert {q: r.status for q, r in out.items()} == {q1: "ai", q2: "auto", q3: "needs_creator"}
    assert len(llm.calls) == 1                                       # chỉ câu 1 cần Gemini
    assert exams.ungraded_essays(sid) == {sid: [q3]}                 # còn câu chờ người tạo đề
    assert asyncio.run(essay_ai.grade_submission(sid, **clients(llm, qdrant)))[q3].status == "needs_creator"


def test_one_failing_question_does_not_stop_the_others(fake_gemini, qdrant, monkeypatch):
    sid, (q1, q2) = submitted(essay_q("Câu 1?"), essay_q("Câu 2?"), answers={0: ESSAY, 1: ESSAY})
    real = essay_ai.grade_essay_answer

    async def flaky(submission_id, question_id, **kw):
        if question_id == q1:
            raise ConnectionError("Postgres rớt mạng")               # lỗi bất ngờ, không phải lỗi AI
        return await real(submission_id, question_id, **kw)

    monkeypatch.setattr(essay_ai, "grade_essay_answer", flaky)
    out = asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini([GOOD]), qdrant)))
    assert out[q1].status == "failed" and "ConnectionError" in out[q1].error
    assert out[q2].status == "ai" and essay_ai._in_flight == set()


def test_gemini_calls_are_limited(fake_gemini, qdrant, monkeypatch):
    monkeypatch.setattr(essay_ai, "GRADE_CONCURRENCY", 2)
    sid, qids = submitted(*(essay_q(f"Câu {i}?") for i in range(5)), answers={i: ESSAY for i in range(5)})

    class Slow(fake_gemini):
        active = peak = 0

        async def generate_content(self, model, contents, config):
            Slow.active += 1
            Slow.peak = max(Slow.peak, Slow.active)
            await asyncio.sleep(0.02)
            Slow.active -= 1
            return await super().generate_content(model, contents, config)

    out = asyncio.run(essay_ai.grade_submission(sid, **clients(Slow([GOOD] * 5), qdrant)))
    assert all(r.status == "ai" for r in out.values()) and Slow.peak == 2


def test_same_question_is_not_graded_twice_at_once(fake_gemini, qdrant):
    """Job sau khi nộp và lần quét bù chạy cùng lúc → mỗi câu chỉ gọi Gemini một lần."""
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY})

    class Slow(fake_gemini):
        async def generate_content(self, model, contents, config):
            await asyncio.sleep(0.02)
            return await super().generate_content(model, contents, config)

    llm = Slow([GOOD])

    async def both():
        return await asyncio.gather(essay_ai.grade_submission(sid, **clients(llm, qdrant)),
                                    essay_ai.sweep_ungraded(**clients(llm, qdrant)))
    first, _ = asyncio.run(both())
    assert first[qid].status == "ai" and len(llm.calls) == 1 and answer_row(sid, qid)["score"] == 1.0


def test_sweep_grades_leftovers_from_previous_run(fake_gemini, qdrant):
    s1, _ = submitted(essay_q("Câu 1?"), answers={0: ESSAY})
    s2, _ = submitted(essay_q("Câu 2?"), answers={0: ESSAY})
    submitted(essay_q("Câu 3?"), answers={0: ESSAY}, submit=False)    # chưa nộp → không chấm
    llm = fake_gemini([GOOD, GOOD])
    assert asyncio.run(essay_ai.sweep_ungraded(**clients(llm, qdrant))) == 2
    assert len(llm.calls) == 2 and exams.ungraded_essays() == {}
    assert asyncio.run(essay_ai.sweep_ungraded(**clients(llm, qdrant))) == 0


def test_start_sweep_runs_in_background(fake_gemini, qdrant):
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY})
    rag = SimpleNamespace(llm=fake_gemini([GOOD]), retriever=SimpleNamespace(client=qdrant, embedder=None))

    async def startup():
        task = essay_ai.start_sweep(rag, interval=0)                   # 0 → chỉ quét một lần
        assert not task.done()                                         # không chặn khởi động
        return await task
    assert asyncio.run(startup()) == 1 and answer_row(sid, qid)["graded_by"] == "ai"


# ---------- qua HTTP: nộp bài → chấm nền ----------
def bearer(user_id):
    return {"Authorization": f"Bearer {auth.create_access_token(user_id)}"}


def test_submit_over_http_grades_essays_in_background(fake_gemini, qdrant, monkeypatch):
    llm = fake_gemini([GOOD])
    monkeypatch.setattr(deps, "rag", SimpleNamespace(llm=llm, retriever=SimpleNamespace(client=qdrant, embedder=None)))
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY}, submit=False)
    res = TestClient(api.app).post(f"/submissions/{sid}/submit", headers=bearer(B))
    assert res.status_code == 200 and res.json()["status"] == "submitted"   # trả về trước khi AI chấm
    assert len(llm.calls) == 1 and answer_row(sid, qid)["graded_by"] == "ai"  # TestClient chạy job nền sau response


def test_submit_without_essays_or_without_models_starts_no_job(fake_gemini, qdrant, monkeypatch):
    llm = fake_gemini([])
    monkeypatch.setattr(deps, "rag", SimpleNamespace(llm=llm, retriever=SimpleNamespace(client=qdrant, embedder=None)))
    mcq = {"type": "mcq", "content": "Trắc nghiệm?",
           "options": [{"label": "A", "content": "x", "is_correct": True}, {"label": "B", "content": "y"}]}
    sid, _ = submitted(mcq, submit=False)
    assert TestClient(api.app).post(f"/submissions/{sid}/submit", headers=bearer(B)).json()["status"] == "graded"

    monkeypatch.setattr(deps, "rag", None)                                    # model đang nạp
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY}, submit=False)
    assert TestClient(api.app).post(f"/submissions/{sid}/submit", headers=bearer(B)).status_code == 200
    assert llm.calls == [] and exams.ungraded_essays(sid) == {sid: [qid]}     # để lần quét bù chấm


# ====================== chốt điểm bài làm (3.6) ======================
MCQ = {"type": "mcq", "content": "Trắc nghiệm?", "points": 1,
       "options": [{"label": "A", "content": "đúng", "is_correct": True}, {"label": "B", "content": "sai"}]}


def submission(sid):
    with Session(db.engine) as s:
        sub = s.get(db.Submission, sid)
        return {"status": sub.status, "score": sub.score, "max_score": sub.max_score, "graded_at": sub.graded_at}


def submitted_with_mcq(*essays, essay_answers, mcq_right=True):
    """Đề: 1 câu trắc nghiệm (1 điểm) + các câu tự luận (2 điểm). B làm đúng / sai câu trắc nghiệm rồi nộp."""
    sid, qids = submitted(MCQ, *essays, answers=None, submit=False)
    full = exams.get_exam_for_creator(A, exams.get_my_result(B, sid)["exam_id"])
    right = next(o["id"] for o in full["questions"][0]["options"] if o["is_correct"] is mcq_right)
    exams.save_answer(B, sid, qids[0], selected_option_id=right)
    for i, text in essay_answers.items():
        exams.save_answer(B, sid, qids[i], essay_text=text)
    exams.submit(B, sid)
    return sid, qids


def test_last_essay_graded_finalizes_the_submission(fake_gemini, qdrant):
    sid, (mq, e1, e2) = submitted_with_mcq(essay_q("Câu 1?"), essay_q("Câu 2?"), essay_answers={1: ESSAY, 2: ESSAY})
    assert submission(sid)["status"] == "submitted" and submission(sid)["score"] is None

    exams.save_essay_grade(sid, e1, score=1.5, feedback="x", graded_by="ai")
    assert submission(sid)["status"] == "submitted" and submission(sid)["score"] is None   # còn câu 2

    exams.save_essay_grade(sid, e2, score=0.5, feedback="y", graded_by="ai")
    sub = submission(sid)
    assert (sub["status"], sub["score"], sub["max_score"]) == ("graded", 3.0, 5.0) and sub["graded_at"]  # 1 + 1.5 + 0.5

    result = exams.get_my_result(B, sid)                                     # B thấy tổng điểm
    assert (result["status"], result["score"]) == ("graded", 3.0)
    assert [x["score"] for x in exams.list_my_submissions(B)] == [3.0]


def test_blank_essay_and_ai_essay_finalize_together(fake_gemini, qdrant):
    sid, _ = submitted_with_mcq(essay_q("Câu 1?"), essay_q("Câu 2?"), essay_answers={1: ESSAY}, mcq_right=False)
    asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini([GOOD]), qdrant)))
    assert (submission(sid)["status"], submission(sid)["score"]) == ("graded", 1.0)      # 0 + 1 (AI) + 0 (bỏ trống)


def test_waiting_for_creator_keeps_submission_open(fake_gemini, qdrant):
    sid, _ = submitted_with_mcq(essay_q("Câu 1?"), essay_q("Câu 2?", rubric=None), essay_answers={1: ESSAY, 2: ESSAY})
    asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini([GOOD]), qdrant)))
    assert submission(sid)["status"] == "submitted" and submission(sid)["score"] is None


def test_parallel_grading_gives_the_right_total(fake_gemini, qdrant):
    """Nhiều câu chấm song song → tổng đọc lại từ database, không bị hai job ghi đè lẫn nhau."""
    sid, _ = submitted_with_mcq(*(essay_q(f"Câu {i}?") for i in range(4)),
                                essay_answers={i: ESSAY for i in range(1, 5)})

    class Slow(fake_gemini):
        async def generate_content(self, model, contents, config):
            await asyncio.sleep(0.01)
            return await super().generate_content(model, contents, config)

    asyncio.run(essay_ai.grade_submission(sid, **clients(Slow([GOOD] * 4), qdrant)))
    assert (submission(sid)["status"], submission(sid)["score"]) == ("graded", 5.0)      # 1 + 4 × 1


def test_sweep_finalizes_submissions_graded_before_3_6(fake_gemini, qdrant):
    sid, (mq, eq) = submitted_with_mcq(essay_q(), essay_answers={1: ESSAY})
    with Session(db.engine) as s, s.begin():                                 # chấm xong mà chưa chốt (dữ liệu cũ)
        a = s.scalar(select(db.SubmissionAnswer).where(db.SubmissionAnswer.submission_id == sid,
                                                       db.SubmissionAnswer.question_id == eq))
        a.score, a.graded_by = 2.0, "ai"
    assert submission(sid)["status"] == "submitted" and exams.ungraded_essays() == {}
    llm = fake_gemini([])
    asyncio.run(essay_ai.sweep_ungraded(**clients(llm, qdrant)))
    assert (submission(sid)["status"], submission(sid)["score"]) == ("graded", 3.0) and llm.calls == []
    assert exams.finalize_complete_submissions() == []                       # lần sau không còn gì


def test_done_when_phase3_over_http(fake_gemini, qdrant, monkeypatch):
    """Phase 3 (phần AI): nộp bài có tự luận → trả "submitted" ngay → chấm nền → GET thấy "graded" + nhận xét."""
    llm = fake_gemini([ai_grade((1, "Độc lập dân tộc là mục tiêu trước hết", "đủ"),
                                (2, "Chủ nghĩa xã hội là điều kiện giữ vững độc lập", "đủ"), feedback="Bài tốt.")])
    monkeypatch.setattr(deps, "rag", SimpleNamespace(llm=llm, retriever=SimpleNamespace(client=qdrant, embedder=None)))
    sid, (qid,) = submitted(essay_q(), answers={0: ESSAY}, submit=False)
    client = TestClient(api.app)
    assert client.post(f"/submissions/{sid}/submit", headers=bearer(B)).json()["status"] == "submitted"
    result = client.get(f"/submissions/{sid}", headers=bearer(B)).json()
    assert (result["status"], result["score"], result["max_score"]) == ("graded", 2.0, 2.0)
    assert (result["questions"][0]["feedback"], result["questions"][0]["graded_by"]) == ("Bài tốt.", "ai")


# ====================== người tạo đề chấm / sửa điểm (3.7) ======================
C = 3   # người ngoài


@pytest.fixture
def outsider():
    with Session(db.engine) as s, s.begin():
        s.add(db.User(id=C, email="c@example.com", password_hash="x", display_name="C"))


def test_creator_grades_essay_waiting_for_creator(fake_gemini, qdrant):
    """Câu không có barem: AI không chấm → bài kẹt "submitted" → người tạo đề chấm tay → "graded"."""
    sid, (mq, eq) = submitted_with_mcq(essay_q(rubric=None), essay_answers={1: ESSAY})
    asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini([]), qdrant)))
    assert submission(sid)["status"] == "submitted"

    review = exams.grade_answer_by_creator(A, sid, eq, score=1.5, feedback="  Thiếu ví dụ. ")
    essay = review["questions"][1]
    assert (essay["score"], essay["feedback"], essay["graded_by"]) == (1.5, "Thiếu ví dụ.", "creator")
    assert (review["status"], review["score"]) == ("graded", 2.5) == (submission(sid)["status"], submission(sid)["score"])


def test_creator_overrides_ai_score_and_total_is_recomputed(fake_gemini, qdrant):
    sid, (mq, eq) = submitted_with_mcq(essay_q(), essay_answers={1: ESSAY})
    asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini([GOOD]), qdrant)))
    assert submission(sid)["score"] == 2.0                                    # 1 (trắc nghiệm) + 1 (AI)

    review = exams.grade_answer_by_creator(A, sid, eq, score=2)               # feedback None → giữ nhận xét AI
    assert (review["questions"][1]["graded_by"], review["questions"][1]["feedback"]) == ("creator", "Bạn đã nêu ý 1.")
    assert (submission(sid)["status"], submission(sid)["score"]) == ("graded", 3.0)
    exams.grade_answer_by_creator(A, sid, eq, score=0, feedback="")           # "" → xoá nhận xét
    assert (submission(sid)["score"], answer_row(sid, eq)["feedback"]) == (1.0, None)


def test_creator_can_override_auto_mcq_and_blank_essay(fake_gemini, qdrant):
    sid, (mq, eq) = submitted_with_mcq(essay_q(), essay_answers={}, mcq_right=False)   # sai trắc nghiệm, bỏ trống tự luận
    exams.grade_answer_by_creator(A, sid, mq, score=1, feedback="Đáp án đề bị sai, cho điểm.")
    exams.grade_answer_by_creator(A, sid, eq, score=0.5)                     # câu bỏ trống: tạo dòng mới
    assert (submission(sid)["status"], submission(sid)["score"]) == ("graded", 1.5)


def test_creator_grade_before_ai_means_ai_skips_it(fake_gemini, qdrant):
    sid, (mq, eq) = submitted_with_mcq(essay_q(), essay_answers={1: ESSAY})
    exams.grade_answer_by_creator(A, sid, eq, score=2)                        # chấm tay trước khi job chạy
    llm = fake_gemini([])
    asyncio.run(essay_ai.grade_submission(sid, **clients(llm, qdrant)))
    assert llm.calls == [] and answer_row(sid, eq)["graded_by"] == "creator"


def test_only_the_creator_can_review_or_grade(outsider):
    sid, (mq, eq) = submitted_with_mcq(essay_q(), essay_answers={1: ESSAY})
    for user in (B, C):                                                       # người làm bài, người ngoài
        with pytest.raises(exams.NotFound):
            exams.get_submission_for_creator(user, sid)
        with pytest.raises(exams.NotFound):
            exams.grade_answer_by_creator(user, sid, eq, score=2)
    assert answer_row(sid, eq)["score"] is None


def test_grade_validation():
    sid, (mq, eq) = submitted_with_mcq(essay_q(), essay_answers={1: ESSAY})
    for bad in (-1, 2.5):
        with pytest.raises(ValueError, match="từ 0 đến 2"):
            exams.grade_answer_by_creator(A, sid, eq, score=bad)
    other_sid, (other_q,) = submitted(essay_q("Đề khác?"), answers={0: ESSAY})
    with pytest.raises(exams.NotFound):
        exams.grade_answer_by_creator(A, sid, other_q, score=1)               # câu của đề khác
    open_sid, (oq,) = submitted(essay_q(), answers={0: ESSAY}, submit=False)
    with pytest.raises(exams.InvalidState):
        exams.grade_answer_by_creator(A, open_sid, oq, score=1)               # chưa nộp


def test_review_shows_answers_rubric_and_grades(fake_gemini, qdrant):
    sid, (mq, eq) = submitted_with_mcq(essay_q(), essay_answers={1: ESSAY})
    asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini([GOOD]), qdrant)))
    review = exams.get_submission_for_creator(A, sid)
    assert review["user"]["email"] == "b@example.com"
    m, e = review["questions"]
    assert m["answer"]["selected_option_id"] and any(o["is_correct"] for o in m["options"]) and m["graded_by"] == "auto"
    assert (e["answer"]["essay_text"], e["rubric"], e["score"], e["graded_by"]) == (ESSAY, RUBRIC, 1.0, "ai")


def test_grade_over_http(fake_gemini, qdrant, outsider):
    sid, (mq, eq) = submitted_with_mcq(essay_q(rubric=None), essay_answers={1: ESSAY})
    client = TestClient(api.app)
    url = f"/submissions/{sid}/answers/{eq}/grade"
    assert client.get(f"/submissions/{sid}/review", headers=bearer(A)).status_code == 200
    assert client.get(f"/submissions/{sid}/review", headers=bearer(B)).status_code == 404
    assert client.patch(url, json={"score": 1}, headers=bearer(C)).status_code == 404
    assert client.patch(url, json={"score": 3}, headers=bearer(A)).status_code == 400          # > 2 điểm
    assert client.patch(url, json={"score": -1}, headers=bearer(A)).status_code == 422         # schema chặn
    res = client.patch(url, json={"score": 2, "feedback": "Tốt."}, headers=bearer(A))
    assert res.status_code == 200 and (res.json()["status"], res.json()["score"]) == ("graded", 3.0)
    mine = client.get(f"/submissions/{sid}", headers=bearer(B)).json()                       # B thấy điểm tay
    assert (mine["score"], mine["questions"][1]["feedback"], mine["questions"][1]["graded_by"]) == (3.0, "Tốt.", "creator")



# ====================== theo dõi trạng thái (3.8) ======================
def status_of(user, sid):
    return essay_ai.with_grading_status(exams.get_my_result(user, sid))


def test_status_while_grading_then_graded(fake_gemini, qdrant):
    sid, (mq, eq) = submitted_with_mcq(essay_q(), essay_answers={1: ESSAY})
    view = status_of(B, sid)
    assert [q["grading_status"] for q in view["questions"]] == ["graded", "grading"]      # trắc nghiệm chấm ngay
    assert view["questions"][1]["grading_message"] == "AI đang chấm…"
    assert view["grading"] == {"graded": 1, "total": 2, "still_grading": True}            # → giao diện hỏi lại

    asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini([GOOD]), qdrant)))
    view = status_of(B, sid)
    assert view["status"] == "graded" and view["grading"] == {"graded": 2, "total": 2, "still_grading": False}
    assert view["questions"][1]["grading_message"] is None


def test_status_is_grading_during_the_ai_call(fake_gemini, qdrant):
    sid, (eq,) = submitted(essay_q(), answers={0: ESSAY})
    seen = []

    class Peek(fake_gemini):
        async def generate_content(self, model, contents, config):
            seen.append(status_of(B, sid)["questions"][0]["grading_status"])          # giữa lúc Gemini đang chạy
            return await super().generate_content(model, contents, config)

    asyncio.run(essay_ai.grade_submission(sid, **clients(Peek([GOOD]), qdrant)))
    assert seen == ["grading"] and status_of(B, sid)["questions"][0]["grading_status"] == "graded"


def test_failed_ai_stops_polling_until_retry_succeeds(fake_gemini, qdrant):
    sid, (eq,) = submitted(essay_q(), answers={0: ESSAY})
    asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini(["{hỏng"]), qdrant)))
    view = status_of(B, sid)
    assert view["questions"][0]["grading_status"] == "failed" and view["grading"]["still_grading"] is False
    assert "người tạo đề chấm tay" in view["questions"][0]["grading_message"]
    assert "hỏng" not in str(view)                                                        # không lộ lỗi kỹ thuật

    asyncio.run(essay_ai.sweep_ungraded(**clients(fake_gemini([GOOD]), qdrant)))         # lần quét bù chấm được
    assert status_of(B, sid)["questions"][0]["grading_status"] == "graded" and essay_ai._stuck == {}


def test_failed_then_creator_grades_shows_graded(fake_gemini, qdrant):
    sid, (eq,) = submitted(essay_q(), answers={0: ESSAY})
    asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini(["{hỏng"]), qdrant)))
    exams.grade_answer_by_creator(A, sid, eq, score=1)
    assert status_of(B, sid)["questions"][0]["grading_status"] == "graded"


def test_needs_creator_does_not_keep_polling(fake_gemini, qdrant):
    sid, _ = submitted(essay_q(rubric=None), answers={0: ESSAY})
    view = status_of(B, sid)
    assert view["status"] == "submitted" and view["questions"][0]["grading_status"] == "needs_creator"
    assert view["grading"]["still_grading"] is False


def test_unsubmitted_has_no_grading_status():
    sid, _ = submitted(essay_q(), answers={0: ESSAY}, submit=False)
    view = status_of(B, sid)
    assert view["grading"] is None and "grading_status" not in view["questions"][0]


def test_polling_over_http(fake_gemini, qdrant, monkeypatch):
    llm = fake_gemini([GOOD])
    sid, (eq,) = submitted(essay_q(), answers={0: ESSAY}, submit=False)
    client = TestClient(api.app)
    monkeypatch.setattr(deps, "rag", None)                                  # chưa nạp model → chưa chấm ngay
    first = client.post(f"/submissions/{sid}/submit", headers=bearer(B)).json()
    assert first["grading"]["still_grading"] is True and first["questions"][0]["grading_status"] == "grading"
    assert client.get(f"/submissions/{sid}", headers=bearer(B)).json()["grading"]["still_grading"] is True
    asyncio.run(essay_ai.sweep_ungraded(**clients(llm, qdrant)))           # khởi động xong → quét bù chấm
    after = client.get(f"/submissions/{sid}", headers=bearer(B)).json()
    assert after["grading"] == {"graded": 1, "total": 1, "still_grading": False} and after["status"] == "graded"
    review = client.get(f"/submissions/{sid}/review", headers=bearer(A)).json()           # người tạo đề cũng thấy
    assert review["questions"][0]["grading_status"] == "graded" and review["grading"]["graded"] == 1


# ====================== quota chấm (3.9) ======================
@pytest.fixture
def limit(monkeypatch):
    monkeypatch.setattr(essay_ai, "AI_GRADE_PER_USER_PER_DAY", 2)
    return 2


def test_quota_stops_ai_and_question_waits(fake_gemini, qdrant, limit):
    sid, qids = submitted(*(essay_q(f"Câu {i}?") for i in range(3)), answers={i: ESSAY for i in range(3)})
    llm = fake_gemini([GOOD, GOOD])
    out = asyncio.run(essay_ai.grade_submission(sid, **clients(llm, qdrant)))
    assert sorted(r.status for r in out.values()) == ["ai", "ai", "quota"] and len(llm.calls) == 2
    assert exams.ai_gradings_today(B) == 2

    view = status_of(B, sid)
    blocked = [q for q in view["questions"] if q["grading_status"] == "quota"]
    assert len(blocked) == 1 and "hết 2 lượt" in blocked[0]["grading_message"]
    assert view["status"] == "submitted" and view["grading"]["still_grading"] is False   # dừng hỏi lại


def test_parallel_grading_cannot_exceed_quota(fake_gemini, qdrant, monkeypatch):
    """Chấm song song: 2 câu cùng xét quota khi còn đúng 1 lượt → chỉ một câu được gọi Gemini."""
    monkeypatch.setattr(essay_ai, "AI_GRADE_PER_USER_PER_DAY", 1)
    sid, _ = submitted(essay_q("Câu 1?"), essay_q("Câu 2?"), answers={0: ESSAY, 1: ESSAY})

    class Slow(fake_gemini):
        async def generate_content(self, model, contents, config):
            await asyncio.sleep(0.02)
            return await super().generate_content(model, contents, config)

    llm = Slow([GOOD, GOOD])
    out = asyncio.run(essay_ai.grade_submission(sid, **clients(llm, qdrant)))
    assert sorted(r.status for r in out.values()) == ["ai", "quota"] and len(llm.calls) == 1
    assert essay_ai._ai_running == {}                                         # đã trả chỗ giữ lượt


def test_quota_counts_only_this_takers_ai_gradings_today(monkeypatch):
    sid, (q1, q2, q3) = submitted(essay_q("Câu 1?"), essay_q("Câu 2?"), essay_q("Câu 3?"),
                                  answers={0: ESSAY, 1: ESSAY})
    exams.save_essay_grade(sid, q1, score=1, feedback="x", graded_by="ai")
    exams.save_essay_grade(sid, q3, score=0, feedback="x", graded_by="auto")         # bỏ trống: không tính
    assert exams.ai_gradings_today(B) == 1 and exams.ai_gradings_today(A) == 0       # tính cho người làm bài
    exams.grade_answer_by_creator(A, sid, q1, score=2)                              # người tạo đề chấm đè → trả lượt
    assert exams.ai_gradings_today(B) == 0
    exams.save_essay_grade(sid, q2, score=1, feedback="x", graded_by="ai")
    tomorrow = exams._now() + timedelta(days=1)
    monkeypatch.setattr(exams, "_now", lambda: tomorrow)
    assert exams.ai_gradings_today(B) == 0                                           # sang ngày mới


def test_blocked_question_is_graded_on_a_new_day(fake_gemini, qdrant, monkeypatch):
    monkeypatch.setattr(essay_ai, "AI_GRADE_PER_USER_PER_DAY", 1)
    sid, _ = submitted(essay_q("Câu 1?"), essay_q("Câu 2?"), answers={0: ESSAY, 1: ESSAY})
    asyncio.run(essay_ai.grade_submission(sid, **clients(fake_gemini([GOOD]), qdrant)))
    assert submission(sid)["status"] == "submitted"

    tomorrow = exams._now() + timedelta(days=1)
    monkeypatch.setattr(exams, "_now", lambda: tomorrow)
    llm = fake_gemini([GOOD])
    asyncio.run(essay_ai.sweep_ungraded(**clients(llm, qdrant), skip_failed=True))  # lần quét định kỳ
    assert len(llm.calls) == 1 and submission(sid)["status"] == "graded"


def test_periodic_sweep_skips_failed_but_retries_quota(fake_gemini, qdrant, monkeypatch):
    s1, (failed_q,) = submitted(essay_q("Câu lỗi?"), answers={0: ESSAY})
    s2, (quota_q,) = submitted(essay_q("Câu hết lượt?"), answers={0: ESSAY})
    essay_ai._stuck.update({(s1, failed_q): "failed", (s2, quota_q): "quota"})
    llm = fake_gemini([GOOD])
    asyncio.run(essay_ai.sweep_ungraded(**clients(llm, qdrant), skip_failed=True))
    assert len(llm.calls) == 1 and "Câu hết lượt?" in llm.calls[0][0]
    assert answer_row(s1, failed_q)["score"] is None and (s1, failed_q) in essay_ai._stuck
    asyncio.run(essay_ai.sweep_ungraded(**clients(fake_gemini([GOOD]), qdrant)))   # lúc khởi động: thử lại cả câu lỗi
    assert answer_row(s1, failed_q)["graded_by"] == "ai"


def test_sweep_loop_keeps_running_after_an_error(monkeypatch):
    calls = []

    async def flaky_sweep(**kw):
        calls.append(kw.get("skip_failed", False))
        if len(calls) == 1:
            raise ConnectionError("Postgres tạm mất kết nối")
        return 0

    monkeypatch.setattr(essay_ai, "sweep_ungraded", flaky_sweep)

    async def run_a_while():
        task = asyncio.create_task(essay_ai.sweep_forever(interval=0.01, llm=None, qdrant=None))
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(run_a_while())
    assert len(calls) >= 3 and calls[0] is False and all(calls[1:])          # lần đầu thử cả câu lỗi, sau đó bỏ qua
