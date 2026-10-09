"""
Test bài kiểm tra Phase 1 (app/exams.py + app/routers/exams.py) trên database tạm — không nạp model, không gọi Gemini.

Mỗi luật trong ROADMAP.md (Phase 1) có ít nhất một test. Luật về thời gian dùng đồng hồ giả (exams._now).

Chạy:  pytest -q tests/test_exams.py
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, db, exams
from app import main as api

A, B, C = 1, 2, 3          # A tạo đề, B và C làm bài
T0 = db.utcnow().replace(microsecond=0)


@pytest.fixture(autouse=True)
def users(db_engine):
    with Session(db_engine) as s, s.begin():
        s.add_all([db.User(id=uid, email=f"{name}@example.com", password_hash="x", display_name=name.upper())
                   for uid, name in ((A, "a"), (B, "b"), (C, "c"))])


@pytest.fixture
def clock(monkeypatch):
    """Đồng hồ giả: clock.now = …; clock.advance(minutes=…)."""
    class Clock:
        now = T0

        def advance(self, **kw):
            self.now = self.now + timedelta(**kw)
    c = Clock()
    monkeypatch.setattr(exams, "_now", lambda: c.now)
    return c


def mcq(content="Câu trắc nghiệm?", correct="B", labels="ABCD", **kw):
    return {"type": "mcq", "content": content, "explanation": f"Đáp án {correct}", "source_page": 12,
            "options": [{"label": l, "content": f"Lựa chọn {l}", "is_correct": l == correct} for l in labels], **kw}


def essay(content="Trình bày…", **kw):
    return {"type": "essay", "content": content, "rubric": "Nêu đủ 3 ý", "points": 2, **kw}


def make_exam(owner=A, questions=(), publish=True, **fields):
    """Tạo đề có các câu cho trước; trả về (exam_id, [question dict có id lựa chọn])."""
    fields.setdefault("visibility", "public")
    exam = exams.create_exam(owner, fields.pop("title", "Kiểm tra chương 1"), **fields)
    qs = [exams.add_question(owner, exam["id"], **q) for q in questions]
    if publish:
        exams.publish_exam(owner, exam["id"])
    return exam["id"], qs


def option_id(q, label):
    return next(o["id"] for o in q["options"] if o["label"] == label)


def keys_anywhere(obj):
    """Mọi khoá xuất hiện ở bất kỳ tầng nào của dict/list — để chắc đáp án không lọt."""
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in keys_anywhere(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in keys_anywhere(v)}
    return set()


SECRET_KEYS = {"is_correct", "rubric", "explanation", "correct_option_id"}


# ====================== người tạo đề ======================
def test_create_exam_is_manual_draft():
    exam = exams.create_exam(A, "  Đề 1  ", duration_minutes=30)
    assert (exam["title"], exam["status"], exam["origin"], exam["duration_minutes"]) == ("Đề 1", "draft", "manual", 30)


@pytest.mark.parametrize("fields", [
    {"title": "   "},
    {"title": "x", "visibility": "secret"},
    {"title": "x", "duration_minutes": 0},
    {"title": "x", "open_at": T0, "close_at": T0 - timedelta(hours=1)},
])
def test_create_exam_validates(fields):
    with pytest.raises(ValueError):
        exams.create_exam(A, **fields)


@pytest.mark.parametrize("call", [
    lambda e, q: exams.update_exam(B, e, title="Của B"),
    lambda e, q: exams.delete_exam(B, e),
    lambda e, q: exams.add_question(B, e, **mcq()),
    lambda e, q: exams.update_question(B, e, q, content="sửa"),
    lambda e, q: exams.delete_question(B, e, q),
    lambda e, q: exams.publish_exam(B, e),
    lambda e, q: exams.get_exam_for_creator(B, e),
    lambda e, q: exams.list_submissions(B, e),
])
def test_only_creator_can_manage_exam(call):
    exam_id, qs = make_exam(questions=[mcq()], publish=False)
    with pytest.raises(exams.NotFound):                       # 404, không để lộ đề có tồn tại
        call(exam_id, qs[0]["id"])


@pytest.mark.parametrize("call", [
    lambda e, q: exams.update_exam(A, e, title="mới"),
    lambda e, q: exams.add_question(A, e, **mcq()),
    lambda e, q: exams.update_question(A, e, q, content="mới"),
    lambda e, q: exams.delete_question(A, e, q),
    lambda e, q: exams.publish_exam(A, e),
])
def test_published_exam_is_locked(call):
    exam_id, qs = make_exam(questions=[mcq()])
    with pytest.raises(exams.InvalidState):
        call(exam_id, qs[0]["id"])


@pytest.mark.parametrize("question", [
    mcq(correct=None) | {"options": [{"label": "A", "content": "x", "is_correct": True},
                                     {"label": "B", "content": "y", "is_correct": True}]},   # hai đáp án đúng
    mcq(labels="AA"),                                                                      # trùng nhãn
    mcq(rubric="không dành cho mcq"),
    essay(options=[{"label": "A", "content": "x"}]),
    mcq(content="   "),
    mcq(points=-1),
    mcq(source_page=0),
])
def test_add_question_validates(question):
    exam_id, _ = make_exam(publish=False)
    with pytest.raises(ValueError):
        exams.add_question(A, exam_id, **question)


def test_question_order_and_update_replaces_options():
    exam_id, qs = make_exam(questions=[mcq("Câu 1"), essay("Câu 2")], publish=False)
    assert [q["order_index"] for q in qs] == [0, 1]
    updated = exams.update_question(A, exam_id, qs[0]["id"], content="Câu 1 (sửa)",
                                    options=[{"label": "X", "content": "x", "is_correct": True},
                                             {"label": "Y", "content": "y"}])
    assert updated["content"] == "Câu 1 (sửa)"
    assert [(o["label"], o["is_correct"]) for o in updated["options"]] == [("X", True), ("Y", False)]
    with Session(db.engine) as s:
        assert len(s.scalars(select(db.QuestionOption)).all()) == 2           # lựa chọn cũ đã bị xoá


@pytest.mark.parametrize("questions, message", [
    ([], "ít nhất một câu"),
    ([mcq(labels="A", correct="A")], "ít nhất 2 lựa chọn"),
    ([mcq(correct="Z")], "đúng một đáp án đúng"),                               # không có đáp án đúng
])
def test_publish_validates(questions, message):
    exam_id, _ = make_exam(questions=questions, publish=False)
    with pytest.raises(ValueError, match=message):
        exams.publish_exam(A, exam_id)


def test_publish_rejects_past_close_time(clock):
    exam_id, _ = make_exam(questions=[mcq()], publish=False, close_at=T0 + timedelta(hours=1))
    clock.advance(hours=2)
    with pytest.raises(ValueError, match="Giờ đóng đề đã qua"):
        exams.publish_exam(A, exam_id)


def test_publish_link_exam_creates_share_code():
    exam_id, _ = make_exam(questions=[mcq()], publish=False, visibility="link")
    published = exams.publish_exam(A, exam_id)
    assert published["status"] == "published" and len(published["share_code"]) >= 8


def test_list_my_exams_counts():
    exam_id, _ = make_exam(questions=[mcq(), essay()])
    make_exam(owner=B, questions=[mcq()])
    exams.start_submission(B, exam_id)
    mine = exams.list_my_exams(A)
    assert [(e["id"], e["question_count"], e["submission_count"]) for e in mine] == [(exam_id, 2, 1)]


# ====================== ai được làm đề nào ======================
def test_draft_cannot_be_taken():
    exam_id, _ = make_exam(questions=[mcq()], publish=False)
    for user in (A, B):
        with pytest.raises(exams.NotFound):
            exams.start_submission(user, exam_id)


def test_visibility_rules():
    private, _ = make_exam(questions=[mcq()], visibility="private")
    link, _ = make_exam(questions=[mcq()], visibility="link")
    public, _ = make_exam(questions=[mcq()], visibility="public")
    code = exams.get_exam_for_creator(A, link)["share_code"]

    assert exams.get_exam_for_taker(A, private)["id"] == private            # người tạo làm thử đề riêng tư
    for exam_id, share_code in ((private, None), (link, None), (link, "sai-ma")):
        with pytest.raises(exams.NotFound):
            exams.get_exam_for_taker(B, exam_id, share_code)
    assert exams.get_exam_for_taker(B, link, code)["id"] == link
    assert exams.start_submission(B, link, code)["status"] == "in_progress"
    assert exams.get_exam_for_taker(B, public)["id"] == public


def test_taker_view_hides_answers():
    exam_id, _ = make_exam(questions=[mcq(), essay()])
    view = exams.get_exam_for_taker(B, exam_id)
    assert not keys_anywhere(view) & SECRET_KEYS
    assert view["total_points"] == 3 and "share_code" not in view


def test_shuffle_is_stable_per_user():
    exam_id, _ = make_exam(questions=[mcq(f"Câu {i}") for i in range(8)], shuffle_questions=True)
    order = lambda user: [q["content"] for q in exams.get_exam_for_taker(user, exam_id)["questions"]]
    assert order(B) == order(B)                                              # tải lại trang không đổi thứ tự
    assert sorted(order(B)) == sorted(order(C)) and len(order(B)) == 8


# ====================== thời gian ======================
def test_open_and_close_window(clock):
    exam_id, _ = make_exam(questions=[mcq()], open_at=T0 + timedelta(hours=1), close_at=T0 + timedelta(hours=2))
    with pytest.raises(exams.InvalidState, match="chưa đến giờ mở"):
        exams.start_submission(B, exam_id)
    clock.advance(hours=1)
    assert exams.start_submission(B, exam_id)["status"] == "in_progress"
    clock.advance(hours=1)
    with pytest.raises(exams.InvalidState, match="đã đóng"):
        exams.start_submission(C, exam_id)


def test_time_limit_blocks_saving_but_not_submitting(clock):
    exam_id, qs = make_exam(questions=[mcq()], duration_minutes=10)
    sub = exams.start_submission(B, exam_id)
    q = qs[0]
    clock.advance(minutes=10, seconds=20)                                    # quá hạn nhưng còn trong 30 s ân hạn
    exams.save_answer(B, sub["id"], q["id"], selected_option_id=option_id(q, "B"))
    clock.advance(minutes=1)
    with pytest.raises(exams.InvalidState, match="hết giờ"):
        exams.save_answer(B, sub["id"], q["id"], selected_option_id=option_id(q, "A"))
    result = exams.submit(B, sub["id"])                                      # vẫn nộp được, tính câu đã lưu kịp
    assert result["score"] == 1


def test_deadline_is_earlier_of_limit_and_close(clock):
    exam_id, _ = make_exam(questions=[mcq()], duration_minutes=60, close_at=T0 + timedelta(minutes=5))
    sub = exams.start_submission(B, exam_id)
    assert sub["deadline"] == db._ms(T0 + timedelta(minutes=5))              # đề đóng trước khi hết 60 phút


# ====================== làm bài ======================
def test_start_resumes_open_attempt():
    exam_id, _ = make_exam(questions=[mcq()])
    first = exams.start_submission(B, exam_id)
    assert exams.start_submission(B, exam_id)["id"] == first["id"]


def test_save_answer_rules():
    exam_id, qs = make_exam(questions=[mcq(), mcq("Câu 2"), essay()])
    other_exam, other_qs = make_exam(questions=[mcq()])
    sub = exams.start_submission(B, exam_id)["id"]
    q1, q2, q3 = qs
    cases = [
        dict(question_id=q1["id"], selected_option_id=option_id(q2, "A")),      # lựa chọn của câu khác
        dict(question_id=q1["id"], essay_text="viết vào câu trắc nghiệm"),
        dict(question_id=q3["id"], selected_option_id=option_id(q1, "A")),      # chọn đáp án ở câu tự luận
        dict(question_id=other_qs[0]["id"], selected_option_id=option_id(other_qs[0], "B")),   # câu của đề khác
    ]
    for case in cases:
        with pytest.raises(ValueError):
            exams.save_answer(B, sub, case.pop("question_id"), **case)


def test_save_answer_overwrites_and_clears():
    exam_id, qs = make_exam(questions=[mcq(), essay()])
    sub = exams.start_submission(B, exam_id)["id"]
    q1, q2 = qs
    exams.save_answer(B, sub, q1["id"], selected_option_id=option_id(q1, "A"))
    exams.save_answer(B, sub, q1["id"], selected_option_id=option_id(q1, "C"))   # đổi ý
    exams.save_answer(B, sub, q2["id"], essay_text="bài viết")
    exams.save_answer(B, sub, q2["id"])                                          # xoá câu trả lời
    with Session(db.engine) as s:
        rows = s.scalars(select(db.SubmissionAnswer)).all()
    assert [(r.question_id, r.selected_option_id) for r in rows] == [(q1["id"], option_id(q1, "C"))]


def test_other_user_cannot_touch_submission():
    exam_id, qs = make_exam(questions=[mcq()])
    sub = exams.start_submission(B, exam_id)["id"]
    for call in (lambda: exams.save_answer(C, sub, qs[0]["id"], selected_option_id=option_id(qs[0], "B")),
                 lambda: exams.submit(C, sub),
                 lambda: exams.get_my_result(C, sub),
                 lambda: exams.get_my_result(A, sub)):                         # người tạo đề cũng không mở bài người khác
        with pytest.raises(exams.NotFound):
            call()


# ====================== nộp bài + chấm trắc nghiệm ======================
def test_mcq_only_exam_is_graded_on_submit():
    exam_id, qs = make_exam(questions=[mcq("Câu 1"), mcq("Câu 2", points=2), mcq("Câu 3")])
    sub = exams.start_submission(B, exam_id)["id"]
    q1, q2, q3 = qs
    exams.save_answer(B, sub, q1["id"], selected_option_id=option_id(q1, "B"))    # đúng: 1 điểm
    exams.save_answer(B, sub, q2["id"], selected_option_id=option_id(q2, "A"))    # sai: 0 / 2
    # câu 3 bỏ trống: 0
    result = exams.submit(B, sub)
    assert (result["status"], result["score"], result["max_score"]) == ("graded", 1, 4)
    assert [(q["score"], q["graded_by"]) for q in result["questions"]] == [(1, "auto"), (0, "auto"), (0, "auto")]
    with Session(db.engine) as s:                                            # câu bỏ trống vẫn có dòng 0 điểm
        assert len(s.scalars(select(db.SubmissionAnswer)).all()) == 3


def test_exam_with_essay_waits_for_grading():
    exam_id, qs = make_exam(questions=[mcq(), essay()])
    sub = exams.start_submission(B, exam_id)["id"]
    exams.save_answer(B, sub, qs[0]["id"], selected_option_id=option_id(qs[0], "B"))
    exams.save_answer(B, sub, qs[1]["id"], essay_text="bài viết")
    result = exams.submit(B, sub)
    assert (result["status"], result["score"], result["max_score"]) == ("submitted", None, 3)
    mcq_item, essay_item = result["questions"]
    assert (mcq_item["score"], mcq_item["graded_by"]) == (1, "auto")
    assert (essay_item["score"], essay_item["graded_by"]) == (None, None)          # chờ Phase 3


def test_cannot_submit_or_answer_twice():
    exam_id, qs = make_exam(questions=[mcq()])
    sub = exams.start_submission(B, exam_id)["id"]
    exams.submit(B, sub)
    with pytest.raises(exams.InvalidState):
        exams.submit(B, sub)
    with pytest.raises(exams.InvalidState):
        exams.save_answer(B, sub, qs[0]["id"], selected_option_id=option_id(qs[0], "A"))
    assert exams.start_submission(B, exam_id)["id"] != sub                     # nộp xong → lượt mới


def test_result_reveals_answers_only_after_submit():
    exam_id, qs = make_exam(questions=[mcq()])
    sub = exams.start_submission(B, exam_id)["id"]
    exams.save_answer(B, sub, qs[0]["id"], selected_option_id=option_id(qs[0], "A"))
    before = exams.get_my_result(B, sub)
    assert not keys_anywhere(before) & SECRET_KEYS
    assert before["questions"][0]["your_answer"]["selected_option_id"] == option_id(qs[0], "A")
    exams.submit(B, sub)
    after = exams.get_my_result(B, sub)["questions"][0]
    assert after["correct_option_id"] == option_id(qs[0], "B") and after["explanation"] == "Đáp án B"
    assert after["source_page"] == 12


def test_creator_sees_submissions_takers_see_their_own():
    exam_id, _ = make_exam(questions=[mcq()])
    sub_b = exams.start_submission(B, exam_id)["id"]
    exams.submit(B, sub_b)
    exams.start_submission(C, exam_id)
    listing = exams.list_submissions(A, exam_id)
    assert {(x["user"]["email"], x["status"]) for x in listing} == {("b@example.com", "graded"),
                                                                    ("c@example.com", "in_progress")}
    assert [x["id"] for x in exams.list_my_submissions(B)] == [sub_b]


# ====================== qua HTTP (router) ======================
def bearer(user_id):
    return {"Authorization": f"Bearer {auth.create_access_token(user_id)}"}


@pytest.fixture
def client():
    return TestClient(api.app)        # không dùng "with" → không nạp model


def test_done_when_scenario_over_http(client):
    """ROADMAP Phase 1 — Done when: A tạo đề bằng tay, B làm bài, điểm trắc nghiệm đúng."""
    ha, hb = bearer(A), bearer(B)
    exam = client.post("/exams", json={"title": "Kiểm tra chương 1", "visibility": "public"}, headers=ha).json()
    q1 = client.post(f"/exams/{exam['id']}/questions", json=mcq("Câu 1"), headers=ha).json()
    q2 = client.post(f"/exams/{exam['id']}/questions", json=mcq("Câu 2"), headers=ha).json()
    assert client.post(f"/exams/{exam['id']}/publish", headers=ha).json()["status"] == "published"

    view = client.get(f"/exams/{exam['id']}", headers=hb)
    assert view.status_code == 200 and not keys_anywhere(view.json()) & SECRET_KEYS
    assert client.get(f"/exams/{exam['id']}/full", headers=hb).status_code == 404   # B không xem được đáp án

    sub = client.post(f"/exams/{exam['id']}/submissions", headers=hb)
    assert sub.status_code == 201
    sid = sub.json()["id"]
    for q, label in ((q1, "B"), (q2, "A")):                                     # 1 đúng, 1 sai
        res = client.put(f"/submissions/{sid}/answers/{q['id']}",
                         json={"selected_option_id": option_id(q, label)}, headers=hb)
        assert res.status_code == 200
    result = client.post(f"/submissions/{sid}/submit", headers=hb).json()
    assert (result["status"], result["score"], result["max_score"]) == ("graded", 1, 2)

    listing = client.get(f"/exams/{exam['id']}/submissions", headers=ha).json()
    assert [(x["user"]["email"], x["score"]) for x in listing] == [("b@example.com", 1)]


def test_http_status_codes(client):
    ha, hb = bearer(A), bearer(B)
    exam_id, qs = make_exam(questions=[mcq()])
    assert client.patch(f"/exams/{exam_id}", json={"title": "x"}, headers=ha).status_code == 409   # đã xuất bản
    assert client.patch(f"/exams/{exam_id}", json={"title": "x"}, headers=hb).status_code == 404   # không phải của B
    sid = client.post(f"/exams/{exam_id}/submissions", headers=hb).json()["id"]
    bad = client.put(f"/submissions/{sid}/answers/{qs[0]['id']}", json={"essay_text": "x"}, headers=hb)
    assert bad.status_code == 400
    assert client.post("/exams", json={"title": "x", "visibility": "secret"}, headers=ha).status_code == 422
    assert client.get("/exams/mine").status_code == 401                                          # chưa đăng nhập
    assert client.get("/exams/mine", headers=hb).json() == []


def test_cookie_requests_need_csrf_header(client):
    """Đăng nhập bằng cookie (web) mà thiếu X-Requested-With → 403, như mọi endpoint khác."""
    exam_id, _ = make_exam(questions=[mcq()])
    cookies = {"access_token": auth.create_access_token(B)}
    no_header = TestClient(api.app, cookies=cookies)
    assert no_header.get(f"/exams/{exam_id}").status_code == 200
    assert no_header.post(f"/exams/{exam_id}/submissions").status_code == 403
    with_header = TestClient(api.app, cookies=cookies, headers={"X-Requested-With": "fetch"})
    assert with_header.post(f"/exams/{exam_id}/submissions").status_code == 201
