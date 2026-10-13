"""
Test tool của Trợ lý bài kiểm tra (roadmap 5.2, agent/tools.py) trên database tạm — gọi thẳng hàm tool, không gọi
Gemini. Trọng tâm: mỗi người chỉ thấy dữ liệu của chính mình, văn bản trả về đủ để agent nhận ra bài.

Chạy:  pytest -q tests/test_agent_tools.py
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from google.genai import types
from sqlalchemy.orm import Session

from agent import tools
from agent.graph import build_agent, run_agent, user_turn
from app import db, exam_ai, exams

A, B, C = 1, 2, 3          # A tạo đề, B và C làm bài
T0 = datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc)      # 20:00 giờ Việt Nam


@pytest.fixture(autouse=True)
def users(db_engine):
    with Session(db_engine) as s, s.begin():
        s.add_all([db.User(id=uid, email=f"{name}@example.com", password_hash="x", display_name=name.upper())
                   for uid, name in ((A, "a"), (B, "b"), (C, "c"))])


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = T0

        def advance(self, **kw):
            self.now = self.now + timedelta(**kw)
    c = Clock()
    monkeypatch.setattr(exams, "_now", lambda: c.now)
    return c


def make_exam(title, questions, **fields):
    exam = exams.create_exam(A, title, visibility="public", **fields)
    qs = [exams.add_question(A, exam["id"], **q) for q in questions]
    exams.publish_exam(A, exam["id"])
    return exam["id"], qs


def mcq(content="Câu trắc nghiệm?", correct="B", **kw):
    return {"type": "mcq", "content": content, "explanation": f"Đáp án {correct}", "source_page": 12,
            "options": [{"label": l, "content": f"Lựa chọn {l}", "is_correct": l == correct} for l in "ABCD"], **kw}


def essay(content="Trình bày…"):
    return {"type": "essay", "content": content, "rubric": "Nêu đủ 3 ý", "points": 2}


def take(user, exam_id, qs, label=None, clock=None, minutes=10):
    """Làm và nộp bài: chọn `label` cho mọi câu trắc nghiệm (None = bỏ trống)."""
    sub = exams.start_submission(user, exam_id)["id"]
    if clock:
        clock.advance(minutes=minutes)
    for q in qs:
        if label and q["type"] == "mcq":
            oid = next(o["id"] for o in q["options"] if o["label"] == label)
            exams.save_answer(user, sub, q["id"], selected_option_id=oid)
    exams.submit(user, sub)
    return sub


# ====================== 1. list_my_submissions ======================
def test_list_empty():
    assert tools.list_my_submissions(B) == "Bạn chưa làm bài kiểm tra nào."


def test_list_newest_first_with_attempts_status_and_description(clock):
    ch3, ch3_qs = make_exam("Kiểm tra chương 3", [mcq(), mcq(points=0.5)])
    mid, mid_qs = make_exam("Đề ôn giữa kỳ", [mcq(), essay()],
                            description="Đề nháp do AI tạo từ giáo trình, Chương III (trang 40–60).")
    ch1, _ = make_exam("Kiểm tra chương 1", [mcq()])

    first = take(B, ch3, ch3_qs, label="A", clock=clock)            # 12/10 20:10 — sai cả hai: 0/1,5
    clock.advance(days=1)
    second = take(B, ch3, ch3_qs, label="B", clock=clock)           # 13/10 20:20 — đúng cả hai: 1,5/1,5
    clock.advance(hours=1)
    graded_later = take(B, mid, mid_qs, label="B", clock=clock)     # có tự luận → chờ chấm
    clock.advance(hours=1)
    open_sub = exams.start_submission(B, ch1)["id"]                  # 13/10 22:30 — chưa nộp
    take(C, ch3, ch3_qs, label="B")                                  # bài của người khác

    lines = tools.list_my_submissions(B).splitlines()
    assert lines == [
        f"submission_id={open_sub} | Kiểm tra chương 1 | bắt đầu 13/10/2026 22:30 | đang làm, chưa nộp",
        f"submission_id={graded_later} | Đề ôn giữa kỳ | nộp 13/10/2026 21:30 | "
        "đã nộp, đang chấm tự luận — chưa có điểm tổng | "
        "mô tả: Đề nháp do AI tạo từ giáo trình, Chương III (trang 40–60).",
        f"submission_id={second} | Kiểm tra chương 3 | nộp 13/10/2026 20:20 | 1,5/1,5 điểm | lần 2/2 của đề này",
        f"submission_id={first} | Kiểm tra chương 3 | nộp 12/10/2026 20:10 | 0/1,5 điểm | lần 1/2 của đề này",
    ]
    # C chỉ thấy bài của C
    assert tools.list_my_submissions(C).count("submission_id=") == 1


def test_list_caps_long_history(clock, monkeypatch):
    monkeypatch.setattr(tools, "MAX_LIST", 2)
    exam_id, qs = make_exam("Đề luyện", [mcq()])
    for _ in range(3):
        take(B, exam_id, qs, label="B", clock=clock)
    lines = tools.list_my_submissions(B).splitlines()
    assert len(lines) == 3 and "lần 3/3" in lines[0] and lines[-1] == "… và 1 bài cũ hơn (không liệt kê)."


def test_long_description_is_one_short_line():
    exam_id, qs = make_exam("Đề dài", [mcq()], description="Dòng một\n\n" + "rất dài " * 40)
    take(B, exam_id, qs)
    desc = tools.list_my_submissions(B).split("mô tả: ")[1]
    assert "\n" not in desc and len(desc) == 80 and desc.endswith("…")


@pytest.mark.parametrize("x, text", [(7.0, "7"), (7.5, "7,5"), (0.25, "0,25"), (10, "10")])
def test_number_format_matches_result_page(x, text):
    assert tools._num(x) == text


def test_agent_passes_logged_in_user_to_tool():
    """Qua vòng lặp agent: tool nhận user_id từ context (người đăng nhập), model không chọn được người khác."""
    exam_id, qs = make_exam("Kiểm tra chương 3", [mcq()])
    take(C, exam_id, qs, label="B")
    seen = []

    async def fake(llm, contents, config):
        seen.append(contents)
        part = (types.Part(text="Bạn chưa làm bài nào.") if len(seen) > 1 else
                types.Part(function_call=types.FunctionCall(name="list_my_submissions", args={})))
        yield types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))])

    async def go():
        g = build_agent(None, tools.DATA_TOOLS, "system", stream=fake)
        return [ev async for ev in run_agent(g, [user_turn("Tôi được mấy điểm?")], user_id=B)]
    asyncio.run(go())
    assert seen[1][-1].parts[0].function_response.response == {"result": "Bạn chưa làm bài kiểm tra nào."}


# ====================== 2. get_my_submission ======================
def answer(user, sub, q, label=None, text=None):
    oid = next(o["id"] for o in q["options"] if o["label"] == label) if label else None
    exams.save_answer(user, sub, q["id"], selected_option_id=oid, essay_text=text)


def mixed_submission(clock=None):
    """B: Câu 1 đúng, Câu 2 sai, Câu 3 bỏ trống, Câu 4 tự luận chờ AI chấm."""
    exam_id, qs = make_exam("Kiểm tra chương 3", [
        mcq("Đặc điểm lớn nhất của thời kỳ quá độ?"),
        mcq("Nhà nước của dân là gì?", correct="C", explanation="Mọi quyền lực thuộc về nhân dân.", source_page=52),
        mcq("Câu bỏ trống?"),
        essay("Trình bày hai nhiệm vụ của thời kỳ quá độ."),
    ])
    sub = exams.start_submission(B, exam_id)["id"]
    answer(B, sub, qs[0], "B")
    answer(B, sub, qs[1], "A")
    answer(B, sub, qs[3], text="Nhiệm vụ một là xây dựng nền tảng vật chất.")
    exams.submit(B, sub)
    return sub, qs


def test_submission_numbers_questions_like_result_page_and_details_only_mistakes(clock):
    sub, qs = mixed_submission()
    out = tools.get_my_submission(B, sub).splitlines()
    assert out[0] == (f"Kiểm tra chương 3 (submission_id={sub}) | nộp 12/10/2026 20:00 | "
                      "điểm tạm 1/5 — còn 1 câu tự luận chưa có điểm | trắc nghiệm đúng 1/3 câu | 4 câu")
    assert out[1] == "Câu 1 (trắc nghiệm) — đúng: Đặc điểm lớn nhất của thời kỳ quá độ? — trang 12"   # đúng: 1 dòng
    assert out[2:7] == ["Câu 2 (trắc nghiệm, 1 điểm) — sai: Nhà nước của dân là gì?",
                        "  Bạn chọn: A. Lựa chọn A",
                        "  Đáp án đúng: C. Lựa chọn C",
                        "  Giải thích: Mọi quyền lực thuộc về nhân dân.",
                        "  Trang giáo trình: 52"]
    assert out[7:9] == ["Câu 3 (trắc nghiệm, 1 điểm) — bỏ trống: Câu bỏ trống?", "  Bạn bỏ trống câu này."]
    assert out[-2:] == ["Câu 4 (tự luận, 2 điểm) — AI đang chấm…: Trình bày hai nhiệm vụ của thời kỳ quá độ.",
                        "  Bài làm: Nhiệm vụ một là xây dựng nền tảng vật chất."]

    exams.save_essay_grade(sub, qs[3]["id"], score=1, feedback="Thiếu nhiệm vụ thứ hai.", graded_by="ai")
    out = tools.get_my_submission(B, sub).splitlines()
    assert "| 2/5 điểm |" in out[0]
    assert out[-4:] == ["Câu 4 (tự luận, 2 điểm) — được một phần điểm: Trình bày hai nhiệm vụ của thời kỳ quá độ.",
                        "  Bài làm: Nhiệm vụ một là xây dựng nền tảng vật chất.",
                        "  Điểm: 1/2 (AI chấm)",
                        "  Nhận xét: Thiếu nhiệm vụ thứ hai."]


def test_question_number_shows_one_question_in_full():
    sub, _ = mixed_submission()
    out = tools.get_my_submission(B, sub, question_number=1).splitlines()
    assert out[1:] == ["Câu 1 (trắc nghiệm, 1 điểm) — đúng: Đặc điểm lớn nhất của thời kỳ quá độ?",
                       "  Bạn chọn: B. Lựa chọn B", "  Đáp án đúng: B. Lựa chọn B",
                       "  Giải thích: Đáp án B", "  Trang giáo trình: 12"]
    assert tools.get_my_submission(B, sub, question_number="2").splitlines()[1].startswith("Câu 2 ")
    for bad in (0, 5):
        with pytest.raises(tools.ToolError, match="chỉ có 4 câu"):
            tools.get_my_submission(B, sub, question_number=bad)


def test_other_users_and_missing_submissions_look_the_same():
    sub, _ = mixed_submission()
    for user, sid in ((C, sub), (B, 999)):
        with pytest.raises(tools.ToolError) as e:
            tools.get_my_submission(user, sid)
        assert str(e.value) == f"Không có bài làm submission_id={sid} của bạn. Gọi list_my_submissions để xem id đúng."
    for bad in ("abc", 1.5, None):
        with pytest.raises(tools.ToolError, match="số nguyên"):
            tools.get_my_submission(B, bad)


def test_in_progress_gives_progress_only(clock):
    exam_id, qs = make_exam("Đề đang thi", [mcq("Câu hỏi bí mật 1?"), mcq("Câu hỏi bí mật 2?")], duration_minutes=30)
    sub = exams.start_submission(B, exam_id)["id"]
    answer(B, sub, qs[0], "A")
    clock.advance(minutes=10)
    out = tools.get_my_submission(B, sub)
    assert out == (f"Đề đang thi (submission_id={sub}) — ĐANG LÀM, chưa nộp, còn khoảng 20 phút. Đã trả lời 1/2 câu.\n"
                   "Chưa có điểm và đáp án; không cung cấp nội dung câu hỏi khi bài đang làm.")
    assert "bí mật" not in tools.get_my_submission(B, sub, question_number=1)    # kể cả hỏi riêng một câu


def test_long_essay_and_feedback_are_cut():
    exam_id, qs = make_exam("Đề tự luận", [essay()])
    sub = exams.start_submission(B, exam_id)["id"]
    answer(B, sub, qs[0], text="viết rất dài " * 200)
    exams.submit(B, sub)
    exams.save_essay_grade(sub, qs[0]["id"], score=0, feedback="nhận xét dài " * 100, graded_by="ai")
    out = tools.get_my_submission(B, sub)
    essay_line = next(l for l in out.splitlines() if l.startswith("  Bài làm: "))
    feedback_line = next(l for l in out.splitlines() if l.startswith("  Nhận xét: "))
    assert len(essay_line) == len("  Bài làm: ") + tools.MAX_ESSAY_CHARS and essay_line.endswith("…")
    assert len(feedback_line) == len("  Nhận xét: ") + tools.MAX_FEEDBACK_CHARS
    assert "— 0 điểm:" in out


# ====================== 3. get_my_exam ======================
def test_exam_list_is_only_my_created_exams():
    assert tools.get_my_exam(A) == "Bạn chưa tạo đề nào."
    sub, _ = mixed_submission()                                       # A tạo, B làm
    draft = exams.create_exam(A, "Đề nháp", duration_minutes=15)["id"]
    lines = tools.get_my_exam(A).splitlines()
    assert lines == [f"exam_id={draft} | Đề nháp | bản nháp, riêng tư | 0 câu | 15 phút | 0 bài nộp",
                     "exam_id=1 | Kiểm tra chương 3 | đã xuất bản, công khai | 4 câu | 1 bài nộp"]
    assert tools.get_my_exam(B) == "Bạn chưa tạo đề nào."              # B làm bài nhưng không tạo đề


def test_exam_shows_answers_one_line_per_question():
    mixed_submission()
    assert tools.get_my_exam(A, exam_id=1).splitlines() == [
        "exam_id=1 | Kiểm tra chương 3 | đã xuất bản, công khai | 4 câu, 5 điểm",
        "Câu 1 (trắc nghiệm, 1 điểm): Đặc điểm lớn nhất của thời kỳ quá độ? — đáp án đúng: B. Lựa chọn B — trang 12",
        "Câu 2 (trắc nghiệm, 1 điểm): Nhà nước của dân là gì? — đáp án đúng: C. Lựa chọn C — trang 52",
        "Câu 3 (trắc nghiệm, 1 điểm): Câu bỏ trống? — đáp án đúng: B. Lựa chọn B — trang 12",
        "Câu 4 (tự luận, 2 điểm): Trình bày hai nhiệm vụ của thời kỳ quá độ. — barem: Nêu đủ 3 ý",
    ]


def test_exam_question_number_shows_every_option():
    mixed_submission()
    assert tools.get_my_exam(A, exam_id=1, question_number=2).splitlines()[1:] == [
        "Câu 2 (trắc nghiệm, 1 điểm): Nhà nước của dân là gì?",
        "  A. Lựa chọn A", "  B. Lựa chọn B", "  C. Lựa chọn C (đáp án đúng)", "  D. Lựa chọn D",
        "  Giải thích: Mọi quyền lực thuộc về nhân dân.", "  Trang giáo trình: 52"]
    assert tools.get_my_exam(A, exam_id=1, question_number=4).splitlines()[1:] == [
        "Câu 4 (tự luận, 2 điểm): Trình bày hai nhiệm vụ của thời kỳ quá độ.", "  Barem: Nêu đủ 3 ý"]
    with pytest.raises(tools.ToolError, match="có 4 câu"):
        tools.get_my_exam(A, exam_id=1, question_number=9)
    with pytest.raises(tools.ToolError, match="Cần exam_id"):
        tools.get_my_exam(A, question_number=1)


def test_taker_cannot_read_answers_of_exam_they_did_not_create():
    """B làm đề của A (kể cả đã nộp) vẫn không xem được đề qua tool này; giống hệt đề không tồn tại."""
    mixed_submission()
    for user, eid in ((B, 1), (A, 999)):
        with pytest.raises(tools.ToolError) as e:
            tools.get_my_exam(user, exam_id=eid)
        assert str(e.value) == f"Không có đề exam_id={eid} do bạn tạo. Gọi get_my_exam không tham số để xem id đúng."


def test_draft_without_answers_or_questions():
    eid = exams.create_exam(A, "Đề nháp")["id"]
    assert tools.get_my_exam(A, exam_id=eid).splitlines()[1] == "Đề chưa có câu nào."
    exams.add_question(A, eid, type="essay", content="Tự luận không barem?", rubric=None)
    exams.add_question(A, eid, type="mcq", content="Chưa chọn đáp án?",      # bản nháp được phép, xuất bản thì không
                       options=[{"label": l, "content": f"Lựa chọn {l}", "is_correct": False} for l in "AB"])
    out = tools.get_my_exam(A, exam_id=eid)
    assert "barem: chưa có (AI không chấm, người tạo đề chấm tay)" in out
    assert "đáp án đúng: chưa chọn đáp án đúng" in out


# ====================== 4. search_textbook ======================
def passage(score, page, text, chapter="Chương III", cos=0.6):
    return {"id": f"c{page}", "rerank_score": score, "cosine": cos,
            "metadata": {"chapter": chapter, "page": page, "text": text}}


class FakeRag:
    """GuardedRAG.search giả: ghi lại (câu tìm, chương), trả các đoạn cho trước."""

    def __init__(self, ranked=(), error=None):
        self.ranked, self.error, self.calls = list(ranked), error, []

    def search(self, question, chapter=None):
        self.calls.append((question, chapter))
        if self.error:
            raise self.error
        return self.ranked


def test_search_returns_relevant_passages_with_pages():
    rag = FakeRag([passage(0.95, 46, ". Đặc điểm lớn nhất…\n  của thời kỳ quá độ"), passage(0.7, 47, "Nhiệm vụ…"),
                   passage(0.2, 90, "đoạn lạc đề")])
    out = tools.search_textbook(rag, "  đặc điểm   thời kỳ quá độ ", chapter=3)
    assert rag.calls == [("đặc điểm thời kỳ quá độ", 3)]
    assert out.splitlines() == ['Các đoạn giáo trình liên quan tới "đặc điểm thời kỳ quá độ" trong Chương III:',
                                "[1] Chương III, trang 46: Đặc điểm lớn nhất… của thời kỳ quá độ",
                                "[2] Chương III, trang 47: Nhiệm vụ…"]       # đoạn chấm 0,2 bị bỏ


def test_search_partial_warns_and_refuse_says_nothing_found():
    partial = tools.search_textbook(FakeRag([passage(0.6, 46, "gần đúng")]), "câu hỏi")
    assert partial.splitlines()[1].startswith("Lưu ý: các đoạn chỉ liên quan một phần")
    refuse = tools.search_textbook(FakeRag([passage(0.1, 46, "lạc đề")]), "bóng đá", chapter=0)
    assert refuse == ('Không tìm thấy đoạn giáo trình nào đủ liên quan tới "bóng đá" trong Chương Mở đầu. '
                      "Không giải thích phần này bằng kiến thức ngoài giáo trình.")
    assert "lạc đề" not in refuse
    assert tools.search_textbook(FakeRag([]), "gì đó").startswith("Không tìm thấy")


def test_search_validates_input():
    rag = FakeRag([passage(0.95, 46, "x")])
    for bad in ("", "   ", None):
        with pytest.raises(tools.ToolError, match="trống"):
            tools.search_textbook(rag, bad)
    for bad in (8, -1, "ba"):
        with pytest.raises(tools.ToolError):
            tools.search_textbook(rag, "x", chapter=bad)
    tools.search_textbook(rag, "y" * 1000, chapter="7")
    assert rag.calls == [("y" * tools.MAX_QUERY_CHARS, 7)]


def test_search_reports_embedding_outage():
    rag = FakeRag(error=RuntimeError("Ollama không phản hồi"))
    with pytest.raises(tools.ToolError, match="Chưa tìm được trong giáo trình: Ollama không phản hồi"):
        tools.search_textbook(rag, "x")


def test_build_tools_has_all_tools_and_search_ignores_user():
    rag = FakeRag([passage(0.95, 46, "x")])
    built = tools.build_tools(rag)
    assert [t.name for t in built] == ["list_my_submissions", "get_my_submission", "get_my_exam", "search_textbook",
                                       "create_exam_draft"]
    assert [t.once_per_turn for t in built] == [False] * 4 + [True]                 # tool ghi duy nhất
    assert built[3].run(B, query="x", chapter=3).startswith("Các đoạn")
    assert rag.calls == [("x", 3)]


def test_guarded_rag_search_filters_by_chapter(monkeypatch):
    """GuardedRAG.search dùng chung đường tìm của chế độ hỏi giáo trình, thêm bộ lọc chương."""
    from rag import pipeline
    seen = []
    monkeypatch.setattr(pipeline, "qdrant_search",
                        lambda client, emb, q, query_filter=None: seen.append(query_filter) or [{"id": "x"}])
    rag = object.__new__(pipeline.GuardedRAG)
    rag._gpu_lock = None
    rag.retriever = type("R", (), {"client": None, "embedder": None, "reranker": type("Rr", (), {
        "rerank": staticmethod(lambda q, cands, top_n: [passage(0.9, 46, "x")])})()})()
    assert rag.search("x", 3)[0]["metadata"]["page"] == 46
    rag.search("x")
    cond = seen[0].must[0]
    assert (cond.key, cond.match.value) == ("chapter_no", 3) and seen[1] is None


# ====================== 5.3: độ dài kết quả ======================
@pytest.fixture
def small_budget(monkeypatch):
    """Ngân sách nhỏ để đề 20 câu đã phải rút gọn (đề thật cần ~100 câu dài mới chạm 6000 ký tự)."""
    monkeypatch.setattr(tools, "MAX_RESULT_CHARS", 1900)
    monkeypatch.setattr(tools, "DETAIL_CHARS", 500)
    monkeypatch.setattr(tools, "SHORT_CHARS", 900)


def long_wrong_submission(n=20):
    exam_id, qs = make_exam("Đề dài", [mcq(f"Câu hỏi số {i} " + "nội dung " * 10, source_page=10 + i)
                                       for i in range(1, n + 1)])
    return take(B, exam_id, qs, label="A"), exam_id          # chọn A cho mọi câu: sai hết


def test_long_submission_degrades_in_three_steps_and_stays_in_budget(small_budget):
    sub, _ = long_wrong_submission()
    out = tools.get_my_submission(B, sub)
    lines = out.splitlines()
    assert len(out) <= tools.MAX_RESULT_CHARS
    assert lines[1].startswith("Câu 1 (trắc nghiệm, 1 điểm) — sai: Câu hỏi số 1")    # bậc 1: đủ chi tiết
    assert "  Đáp án đúng: B. Lựa chọn B" in lines
    assert any(l.startswith("Câu ") and l.endswith(" — trang 13") and "Câu hỏi số 3" in l for l in lines)   # bậc 2
    assert "Câu 20 — sai — trang 30" in lines                                       # bậc 3: vẫn còn câu cuối
    note = next(l for l in lines if l.startswith("(Bài dài nên rút gọn"))
    assert "chỉ còn kết quả và trang" in note and "question_number" in note
    # xem riêng một câu vẫn đủ chi tiết
    assert "  Đáp án đúng: B. Lựa chọn B" in tools.get_my_submission(B, sub, question_number=20)


def test_huge_submission_stops_with_note(small_budget, monkeypatch):
    monkeypatch.setattr(tools, "MAX_RESULT_CHARS", 900)
    sub, _ = long_wrong_submission(40)
    out = tools.get_my_submission(B, sub)
    assert len(out) <= 900 and out.splitlines()[-1].startswith("… còn ")
    assert "chưa liệt kê vì bài dài — xem từng câu bằng question_number." in out


def test_long_exam_and_lists_stay_in_budget(small_budget, clock):
    _, exam_id = long_wrong_submission()
    out = tools.get_my_exam(A, exam_id=exam_id)
    assert len(out) <= tools.MAX_RESULT_CHARS and out.splitlines()[-1].startswith("… còn ")
    for _ in range(15):
        clock.advance(minutes=1)
        make_exam("Đề " + "rất dài " * 24, [mcq()])                 # 195 ký tự (tối đa 200)
    out = tools.get_my_exam(A)
    assert len(out) <= tools.MAX_RESULT_CHARS and out.splitlines()[-1].startswith("… và ")
    assert all(len(l.split(" | ")[1]) <= tools.MAX_TITLE_CHARS for l in out.splitlines()[:-1])


# ====================== 5. create_exam_draft (5.8) ======================
def gen_mcq(i):
    return {"source_page": 38 + i, "question": f"Câu hỏi AI số {i}?", "correct_label": "B",
            "options": [{"label": l, "content": f"lựa chọn {l}"} for l in "ABCD"],   # giám khảo giả nhận "lựa chọn B"
            "explanation": "Theo giáo trình."}


def test_create_draft_through_real_generator(fake_gemini, qdrant):
    """Đi qua đúng exam_ai.generate_exam_draft (Gemini giả, Qdrant trong bộ nhớ): đề nháp của người hỏi, chưa xuất bản."""
    import json
    llm = fake_gemini([json.dumps({"mcq": [gen_mcq(1), gen_mcq(2)], "essay": []}, ensure_ascii=False)])
    out = asyncio.run(tools.create_exam_draft(B, chapter=3, n_mcq=2, n_essay=0, llm=llm, qdrant=qdrant))

    eid = out.data["exam_id"]
    exam = exams.get_exam_for_creator(B, eid)                 # của B (user_id từ context, không do model chọn)
    assert (exam["status"], exam["origin"], len(exam["questions"])) == ("draft", "ai", 2)
    assert out.data == {"exam_id": eid, "title": exam["title"], "questions": 2, "link": f"/exams/{eid}/edit"}
    lines = out.text.splitlines()
    assert lines[0].startswith('Đã tạo ĐỀ NHÁP "Chương III: ') and "2 câu (2 trắc nghiệm, 0 tự luận)" in lines[0]
    assert f"/exams/{eid}/edit" in out.text and "Còn 4/5 lượt tạo đề bằng AI hôm nay." in out.text


def stub(result=None, error=None):
    calls = []

    async def generate(user_id, chapter, n_mcq, n_essay, *, llm, qdrant):
        calls.append((user_id, chapter, n_mcq, n_essay))
        if error:
            raise error
        return result
    generate.calls = calls
    return generate


def test_create_draft_defaults_and_validation():
    gen = stub(error=exams.QuotaExceeded("x"))
    tool = tools.create_exam_draft_tool(None, None, generate=gen)
    with pytest.raises(tools.ToolError):
        asyncio.run(tool.run(B, chapter="3"))
    assert gen.calls == [(B, 3, tools.DEFAULT_MCQ, tools.DEFAULT_ESSAY)]          # mặc định 10 + 2 như trang Tạo đề
    for bad in ({"chapter": 8}, {"chapter": "ba"}, {"chapter": 3, "n_mcq": 2.5}):
        with pytest.raises(tools.ToolError):
            asyncio.run(tool.run(B, **bad))
    assert len(gen.calls) == 1                                                     # sai tham số → không gọi tạo đề


@pytest.mark.parametrize("error, message", [
    (ValueError("Số câu trắc nghiệm phải từ 0 đến 20"), "Chưa tạo đề: Số câu trắc nghiệm phải từ 0 đến 20"),
    (exams.QuotaExceeded("Bạn đã tạo đủ 5 đề bằng AI hôm nay."), "Chưa tạo đề: Bạn đã tạo đủ 5 đề bằng AI hôm nay."),
    (exam_ai.GenerationFailed("AI chưa tạo được đề: 503"), "Chưa tạo được đề: AI chưa tạo được đề: 503 Không có gì "
                                                           "được lưu, có thể thử lại sau."),
])
def test_create_draft_errors_reach_the_model(error, message):
    with pytest.raises(tools.ToolError) as e:
        asyncio.run(tools.create_exam_draft(B, 3, 30, 0, llm=None, qdrant=None, generate=stub(error=error)))
    assert str(e.value) == message
