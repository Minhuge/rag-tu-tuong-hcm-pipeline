"""
Test an toàn của Trợ lý bài kiểm tra (roadmap 5.9), bổ sung cho các test từng tool:

  1. Quét HẾT: gọi mọi tool đọc dữ liệu với mọi id có trong database (và id không tồn tại), mọi số câu, dưới danh
     nghĩa từng người dùng → mỗi người chỉ thấy đúng phần được thấy. Dữ liệu cài các "chuỗi bí mật" để dò:
       - đáp án / câu hỏi của bài ĐANG LÀM (chưa nộp) → không ai lộ ra qua trợ lý, kể cả chính người làm
       - đáp án đề NHÁP của người tạo → chỉ người tạo thấy
       - bài làm của người khác → không ai thấy qua trợ lý (người tạo đề xem ở trang Bài nộp, không qua trợ lý)
     Mỗi test có đối chứng: phần ĐƯỢC thấy phải thấy được → chắc phép quét không rỗng một cách vô nghĩa.
  2. Model "xấu" qua vòng lặp agent: Gemini giả gọi mọi tool với mọi id (và cố truyền user_id) trong một lượt.
  3. Chốt chặn cuối của vòng lặp: giới hạn mềm hỏng → recursion_limit vẫn dừng, báo lỗi dễ hiểu.
  4. Quota dưới tải song song — chỉ chạy trên Postgres thật (SQLite không khoá dòng).

Chạy:  pytest -q tests/test_agent_security.py
       TEST_DATABASE_URL=… pytest -q tests/test_agent_security.py     # thêm test quota song song
"""
import asyncio
import os
import threading

import pytest
from google.genai import types
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent import graph as ag
from agent import tools
from agent.graph import ToolError, build_agent, run_agent, user_turn
from app import agent_ai, db, exams

A, B, C = 1, 2, 3            # A ra đề; B làm bài; C người lạ (không tạo, không làm gì)

SECRET_IN_PROGRESS = ("CAU_HOI_DANG_THI", "DAP_AN_DANG_THI")      # bài B đang làm, chưa nộp
SECRET_DRAFT = "DAP_AN_DE_NHAP_CUA_A"                             # đề nháp của A
B_ESSAY = "BAI_LAM_TU_LUAN_CUA_B"                                 # bài tự luận B đã nộp
SUBMITTED_ANSWER = "DAP_AN_DE_DA_NOP"                             # đáp án đề B đã nộp: B được thấy (đối chứng)


@pytest.fixture(autouse=True)
def world(db_engine):
    with Session(db_engine) as s, s.begin():
        s.add_all([db.User(id=u, email=f"{n}@example.com", password_hash="x") for u, n in ((A, "a"), (B, "b"), (C, "c"))])

    def mcq(content, correct_text, **kw):
        return {"type": "mcq", "content": content, "explanation": "Giải thích.", "source_page": 12, **kw,
                "options": [{"label": "A", "content": "sai", "is_correct": False},
                            {"label": "B", "content": correct_text, "is_correct": True}]}

    def exam(title, questions, publish=True):
        e = exams.create_exam(A, title, visibility="public")["id"]
        qs = [exams.add_question(A, e, **q) for q in questions]
        if publish:
            exams.publish_exam(A, e)
        return e, qs

    done_id, done_q = exam("Đề đã nộp", [mcq("Câu đã làm?", SUBMITTED_ANSWER),
                                         {"type": "essay", "content": "Tự luận?", "rubric": "ý 1", "points": 2}])
    sub = exams.start_submission(B, done_id)["id"]
    exams.save_answer(B, sub, done_q[0]["id"], selected_option_id=done_q[0]["options"][0]["id"])
    exams.save_answer(B, sub, done_q[1]["id"], essay_text=B_ESSAY + " " + "viết thêm cho đủ dài " * 3)
    exams.submit(B, sub)

    live_id, _ = exam("Đề đang thi", [mcq(SECRET_IN_PROGRESS[0], SECRET_IN_PROGRESS[1])])
    exams.start_submission(B, live_id)
    exam("Đề nháp của A", [mcq("Câu nháp?", SECRET_DRAFT)], publish=False)


def all_ids():
    with Session(db.engine) as s:
        subs = list(s.scalars(select(db.Submission.id)))
        exam_ids = list(s.scalars(select(db.Exam.id)))
    return subs + [999], exam_ids + [999]


def sweep(user_id: int) -> str:
    """Mọi kết quả (cả thông báo lỗi) khi `user_id` gọi mọi tool đọc dữ liệu với mọi tham số có thể."""
    subs, exam_ids = all_ids()
    calls = [lambda: tools.list_my_submissions(user_id), lambda: tools.get_my_exam(user_id)]
    for sid in subs:
        for n in (None, 1, 2, 3):
            calls.append(lambda sid=sid, n=n: tools.get_my_submission(user_id, sid, n))
    for eid in exam_ids:
        for n in (None, 1, 2, 3):
            calls.append(lambda eid=eid, n=n: tools.get_my_exam(user_id, eid, n))
    out = []
    for call in calls:
        try:
            out.append(call())
        except ToolError as e:
            out.append(str(e))
    return "\n".join(out)


def test_stranger_sees_nothing_from_anyone():
    seen = sweep(C)
    for secret in (*SECRET_IN_PROGRESS, SECRET_DRAFT, B_ESSAY, SUBMITTED_ANSWER):
        assert secret not in seen
    for title in ("Đề đã nộp", "Đề đang thi", "Đề nháp của A"):        # không biết cả tên đề của người khác
        assert title not in seen
    assert "Bạn chưa làm bài kiểm tra nào." in seen and "Bạn chưa tạo đề nào." in seen


def test_taker_never_sees_the_test_in_progress_or_others_drafts():
    seen = sweep(B)
    for secret in (*SECRET_IN_PROGRESS, SECRET_DRAFT):
        assert secret not in seen
    # đối chứng: bài đã nộp thì B được xem đáp án và bài làm của chính mình; bài đang làm vẫn hiện tiến độ
    assert SUBMITTED_ANSWER in seen and B_ESSAY in seen and "ĐANG LÀM, chưa nộp" in seen


def test_creator_sees_own_answer_keys_but_not_takers_work():
    seen = sweep(A)
    assert B_ESSAY not in seen                                   # bài nộp của B: chỉ ở trang Bài nộp, không qua trợ lý
    # đối chứng: đáp án các đề do A tạo (kể cả nháp và đề B đang thi) thì A được xem — A là người ra đề
    assert SECRET_DRAFT in seen and SUBMITTED_ANSWER in seen and SECRET_IN_PROGRESS[1] in seen


def test_malicious_model_cannot_reach_other_users_data_through_the_loop():
    """Gemini giả gọi MỌI tool với MỌI id trong một lượt, kèm cả user_id của người khác → chỉ thấy phần của C."""
    subs, exam_ids = all_ids()
    calls = [types.Part(function_call=types.FunctionCall(name="list_my_submissions", args={"user_id": B}))]
    calls += [types.Part(function_call=types.FunctionCall(name="get_my_submission", args={"submission_id": s}))
              for s in subs]
    calls += [types.Part(function_call=types.FunctionCall(name="get_my_exam", args={"exam_id": e, "question_number": 1}))
              for e in exam_ids]
    seen = []

    async def evil(llm, contents, config):
        seen.append(list(contents))
        part = types.Part(text="xong") if len(seen) > 1 else None
        yield types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
            role="model", parts=[part] if part else calls))])

    async def go():
        g = build_agent(None, tools.DATA_TOOLS, "system", stream=evil)
        return [ev async for ev in run_agent(g, [user_turn("?")], user_id=C)]
    asyncio.run(go())

    responses = [p.function_response.response for p in seen[1][-1].parts]
    text = str(responses)
    for secret in (*SECRET_IN_PROGRESS, SECRET_DRAFT, B_ESSAY, SUBMITTED_ANSWER, "Đề đã nộp", "Đề đang thi"):
        assert secret not in text
    assert responses[0] == {"error": "Tham số không hợp lệ: user_id."}                # cố truyền user_id → bị chặn
    assert len(responses) == len(calls)


def test_recursion_limit_is_the_last_line_of_defence(monkeypatch):
    """Giới hạn mềm (MAX_TOOL_ROUNDS) coi như hỏng → recursion_limit vẫn dừng vòng lặp, báo lỗi dễ hiểu."""
    monkeypatch.setattr(ag, "MAX_TOOL_ROUNDS", 10_000)
    monkeypatch.setattr(ag, "RECURSION_LIMIT", 7)
    count = []

    async def endless(llm, contents, config):
        count.append(1)
        yield types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(name="list_my_submissions", args={}))]))])

    async def go():
        g = build_agent(None, tools.DATA_TOOLS, "system", stream=endless)
        return [ev async for ev in run_agent(g, [user_turn("?")], user_id=B)]
    with pytest.raises(RuntimeError, match="quá nhiều bước"):
        asyncio.run(go())
    assert len(count) <= 4                                        # 7 bước graph = tối đa 4 lần gọi model


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="cần Postgres thật (SQLite không khoá dòng)")
def test_parallel_requests_cannot_exceed_daily_quota(monkeypatch):
    """20 luồng cùng giành 5 lượt cuối: đúng 5 lượt được cấp (khoá dòng users trong charge_agent_call)."""
    monkeypatch.setattr(agent_ai, "AGENT_CALLS_PER_USER_PER_DAY", 5)
    granted, refused, start = [], [], threading.Barrier(20)

    def worker():
        start.wait()
        try:
            granted.append(agent_ai.charge_agent_call(B))
        except exams.QuotaExceeded:
            refused.append(1)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(granted) == [0, 1, 2, 3, 4] and len(refused) == 15
    assert agent_ai.agent_quota(B)["used"] == 5
