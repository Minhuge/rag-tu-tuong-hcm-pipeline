"""
Test service tạo đề AI (app/exam_ai.py: 2.8 service, 2.9 quota), exams.create_generated_exam
và endpoint POST /exams/generate (2.10).
Database tạm + Qdrant trong bộ nhớ + Gemini giả (conftest.py) → không cần Docker, không tốn quota.

Chạy:  pytest -q tests/test_exam_ai.py
"""
import asyncio
from datetime import datetime, timezone
import json
import random
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, db, deps, exam_ai, exams
from app import main as api
from rag.exam_generation import resolve_chapter

A, B = 1, 2


@pytest.fixture(autouse=True)
def users(db_engine):
    with Session(db_engine) as s, s.begin():
        s.add_all([db.User(id=uid, email=f"{n}@example.com", password_hash="x", display_name=n.upper())
                   for uid, n in ((A, "a"), (B, "b"))])


def mcq(question="Hai chân của nền kinh tế là gì?", page=38, **over):
    return {"source_page": page, "question": question,
            "options": [{"label": l, "content": f"lựa chọn {l}"} for l in "ABCD"],
            "correct_label": "B", "explanation": "Theo giáo trình, công nghiệp và nông nghiệp.", **over}


def essay(question="Trình bày quan điểm về con đường quá độ.", page=40, **over):
    return {"source_page": page, "question": question,
            "rubric": "- ý 1 (1 điểm)\n- ý 2 (1.5 điểm)", "points": 2.5, **over}


def exam_text(mcqs=(), essays=()) -> str:
    return json.dumps({"mcq": list(mcqs), "essay": list(essays)}, ensure_ascii=False)


def generate(fake, qdrant, exam_texts, n_mcq=2, n_essay=1, chapter=3, **kw):
    llm = fake(exam_texts)
    out = asyncio.run(exam_ai.generate_exam_draft(A, chapter, n_mcq, n_essay, llm=llm, qdrant=qdrant,
                                                  rng=random.Random(0), **kw))
    return out, llm


def exam_count() -> int:
    with Session(db.engine) as s:
        return s.scalar(select(func.count()).select_from(db.Exam))


# ---------- đề hợp lệ được lưu thành bản nháp ----------
def test_saves_ai_draft_with_answers_explanations_and_pages(fake_gemini, qdrant):
    out, llm = generate(fake_gemini, qdrant, [exam_text([mcq("Câu 1?"), mcq("Câu 2?", page=41)], [essay()])])
    assert out["origin"] == "ai" and out["status"] == "draft"
    assert out["title"] == "Chương III: " + resolve_chapter(3).title
    assert "Chương III (trang 38–47)" in out["description"]

    m1, m2, e = out["questions"]
    assert [q["type"] for q in out["questions"]] == ["mcq", "mcq", "essay"]       # trắc nghiệm trước
    assert (m1["content"], m1["points"], m1["source_page"]) == ("Câu 1?", 1.0, 38) and m2["source_page"] == 41
    assert m1["explanation"].startswith("Theo giáo trình")
    correct = [o for o in m1["options"] if o["is_correct"]]
    assert len(correct) == 1 and correct[0]["content"] == "lựa chọn B"           # đáp án đúng giữ sau khi xáo
    assert (e["rubric"], e["points"], e["source_page"]) == (essay()["rubric"], 2.5, 40)

    gen = out["generation"]
    assert gen["requested"] == {"mcq": 2, "essay": 1} and gen["notice"] is None and gen["rejected"] == []
    assert gen["calls"] == 2 and gen["models"] == ["fake-model"]                  # 1 tạo + 1 giám khảo
    assert "<giao_trinh chuong=\"Chương III\">" in llm.calls[0][0]


def test_draft_is_a_normal_exam_for_the_creator_only(fake_gemini, qdrant):
    out, _ = generate(fake_gemini, qdrant, [exam_text([mcq("Câu 1?"), mcq("Câu 2?")], [essay()])])
    exam_id = out["id"]
    full = exams.get_exam_for_creator(A, exam_id)
    assert full["origin"] == "ai" and len(full["questions"]) == 3
    assert [e["id"] for e in exams.list_my_exams(A)] == [exam_id]
    with pytest.raises(exams.NotFound):
        exams.get_exam_for_creator(B, exam_id)                                    # người khác không xem được
    with pytest.raises(exams.NotFound):
        exams.get_exam_for_taker(B, exam_id)                                      # bản nháp: chưa ai làm được

    q = full["questions"][0]                                                      # sửa và xuất bản như đề tay
    exams.update_question(A, exam_id, q["id"], content="Câu 1 đã sửa?")
    assert exams.publish_exam(A, exam_id)["status"] == "published"


def test_custom_title_and_blank_title(fake_gemini, qdrant):
    out, _ = generate(fake_gemini, qdrant, [exam_text([mcq()])], 1, 0, title="  Ôn tập chương 3  ")
    assert out["title"] == "Ôn tập chương 3"
    out, _ = generate(fake_gemini, qdrant, [exam_text([mcq()])], 1, 0, title="   ")
    assert out["title"].startswith("Chương III: ")


def test_chapter_by_label(fake_gemini, qdrant):
    out, _ = generate(fake_gemini, qdrant, [exam_text([mcq()])], 1, 0, chapter="Chương III")
    assert out["generation"]["chapter"]["number"] == 3


# ---------- thiếu câu / không câu nào đạt ----------
def test_fewer_questions_are_saved_with_notice(fake_gemini, qdrant):
    first = exam_text([mcq("Giữ?"), mcq("Trang lạ?", page=99)], [essay()])
    out, _ = generate(fake_gemini, qdrant, [first, exam_text([mcq("Vẫn lạ?", page=99)])])
    assert [q["content"] for q in out["questions"]] == ["Giữ?", essay()["question"]]
    gen = out["generation"]
    assert gen["notice"].startswith("Chỉ tạo được 1/2 câu trắc nghiệm")
    assert [r["question"] for r in gen["rejected"]] == ["Trang lạ?", "Vẫn lạ?"]
    assert gen["rejected"][0]["stage"] == "cấu trúc"


@pytest.mark.parametrize("responses", [
    [exam_text([mcq(page=99)]), exam_text([mcq(page=99)])],     # không câu nào đạt, kể cả tạo bù
    ["{hỏng"],                                                    # JSON hỏng ngay lần đầu
])
def test_nothing_saved_when_generation_fails(fake_gemini, qdrant, responses):
    with pytest.raises(exam_ai.GenerationFailed):
        generate(fake_gemini, qdrant, responses, 1, 0)
    assert exam_count() == 0


def test_gemini_api_error_becomes_generation_failed(fake_gemini, qdrant, monkeypatch):
    async def quota_exhausted(*a, **kw):
        raise RuntimeError("Gemini báo hết prepayment credits (402).")
    monkeypatch.setattr(exam_ai, "generate_exam", quota_exhausted)
    with pytest.raises(exam_ai.GenerationFailed, match="402"):
        generate(fake_gemini, qdrant, [], 1, 0)
    assert exam_count() == 0


def test_chapter_without_passages(fake_gemini, qdrant):
    """Chương V trong Qdrant giả chỉ có dòng tiêu đề → không có đoạn nào để ra đề."""
    with pytest.raises(exam_ai.GenerationFailed, match="Chương V"):
        generate(fake_gemini, qdrant, [], 1, 0, chapter=5)


# ---------- input sai: báo 400 TRƯỚC khi gọi Qdrant / Gemini ----------
@pytest.mark.parametrize("n_mcq,n_essay,chapter", [
    (0, 0, 3), (-1, 1, 3), (21, 0, 3), (1, 4, 3),   # số câu ngoài giới hạn
    (1, 0, 9), (1, 0, "Chương XX"),                  # chương không tồn tại
])
def test_bad_input_raises_before_any_call(fake_gemini, n_mcq, n_essay, chapter):
    llm = fake_gemini([])
    with pytest.raises(ValueError):
        asyncio.run(exam_ai.generate_exam_draft(A, chapter, n_mcq, n_essay, llm=llm, qdrant=object()))
    assert llm.calls == [] and exam_count() == 0


# ---------- create_generated_exam: một transaction ----------
def test_create_generated_exam_is_all_or_nothing():
    good = {"type": "mcq", "content": "Câu tốt?", "source_page": 38,
            "options": [{"label": "A", "content": "x", "is_correct": True}, {"label": "B", "content": "y"}]}
    bad = {**good, "content": "Câu sai luật?", "options": [{"label": "A", "content": "x", "is_correct": True},
                                                           {"label": "B", "content": "y", "is_correct": True}]}
    with pytest.raises(ValueError, match="một đáp án đúng"):
        exams.create_generated_exam(A, "Đề AI", [good, bad])
    assert exam_count() == 0                                         # câu 1 không bị lưu lẻ
    with pytest.raises(ValueError, match="ít nhất một câu"):
        exams.create_generated_exam(A, "Đề AI", [])

    out = exams.create_generated_exam(A, "Đề AI", [good, {"type": "essay", "content": "Trình bày…", "points": 2}])
    assert out["origin"] == "ai" and [q["order_index"] for q in out["questions"]] == [0, 1]


def test_manual_exams_stay_manual():
    assert exams.create_exam(A, "Đề tay")["origin"] == "manual"


# ---------- quota (2.9) ----------
def one_mcq():
    return [exam_text([mcq()])]


def add_exam(owner=A, origin="ai", created_at=None):
    with Session(db.engine) as s, s.begin():
        s.add(db.Exam(created_by=owner, title="Đề cũ", origin=origin,
                      **({"created_at": created_at} if created_at else {})))


@pytest.fixture
def limit(monkeypatch):
    monkeypatch.setattr(exam_ai, "AI_GEN_PER_USER_PER_DAY", 2)
    return 2


def test_quota_blocks_before_calling_gemini(fake_gemini, qdrant, limit):
    out, _ = generate(fake_gemini, qdrant, one_mcq(), 1, 0)
    assert out["generation"]["quota"] == {"limit": 2, "remaining": 1}
    out, _ = generate(fake_gemini, qdrant, one_mcq(), 1, 0)
    assert out["generation"]["quota"] == {"limit": 2, "remaining": 0}

    llm = fake_gemini(one_mcq())
    with pytest.raises(exams.QuotaExceeded, match="đủ 2 đề bằng AI hôm nay"):
        asyncio.run(exam_ai.generate_exam_draft(A, 3, 1, 0, llm=llm, qdrant=qdrant))
    assert llm.calls == [] and exam_count() == 2                   # hết lượt → không tốn lượt Gemini


def test_deleting_ai_exam_gives_the_slot_back(fake_gemini, qdrant, limit):
    first, _ = generate(fake_gemini, qdrant, one_mcq(), 1, 0)
    generate(fake_gemini, qdrant, one_mcq(), 1, 0)
    exams.delete_exam(A, first["id"])
    out, _ = generate(fake_gemini, qdrant, one_mcq(), 1, 0)
    assert out["generation"]["quota"]["remaining"] == 0


def test_failed_generation_does_not_use_a_slot(fake_gemini, qdrant, limit):
    with pytest.raises(exam_ai.GenerationFailed):
        generate(fake_gemini, qdrant, ["{hỏng"], 1, 0)
    assert exams.ai_exams_today(A) == 0


def test_only_own_ai_exams_count(fake_gemini, qdrant, limit):
    add_exam(origin="manual")
    add_exam(origin="manual")
    add_exam(owner=B)
    add_exam(owner=B)
    assert exams.ai_exams_today(A) == 0 and exams.ai_exams_today(B) == 2
    generate(fake_gemini, qdrant, one_mcq(), 1, 0)                 # B hết lượt không ảnh hưởng A


def test_day_starts_at_midnight_vietnam_time(monkeypatch):
    # 23:30 giờ VN ngày 12/10 = 16:30 UTC ngày 12/10 → "hôm nay" bắt đầu từ 17:00 UTC ngày 11/10
    now = datetime(2026, 10, 12, 16, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(exams, "_now", lambda: now)
    add_exam(created_at=datetime(2026, 10, 11, 16, 59, tzinfo=timezone.utc))   # 23:59 VN hôm qua → không tính
    add_exam(created_at=datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc))    # 00:00 VN hôm nay → tính
    add_exam(created_at=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc))    # 17:00 VN hôm nay → tính
    assert exams.ai_exams_today(A) == 2
    with pytest.raises(exams.QuotaExceeded):
        exams.check_ai_quota(A, 2)
    assert exams.check_ai_quota(A, 3) == 1


def test_one_generation_at_a_time_per_user(fake_gemini, qdrant):
    async def both():
        llm = fake_gemini(one_mcq())
        return await asyncio.gather(
            exam_ai.generate_exam_draft(A, 3, 1, 0, llm=llm, qdrant=qdrant),
            exam_ai.generate_exam_draft(A, 3, 1, 0, llm=llm, qdrant=qdrant),
            return_exceptions=True)
    first, second = asyncio.run(both())
    assert first["origin"] == "ai"
    assert isinstance(second, exams.QuotaExceeded) and "đang tạo một đề khác" in str(second)
    assert exam_ai._generating == set()                             # xong (kể cả lỗi) thì mở khoá


def test_lock_released_after_failure(fake_gemini, qdrant):
    with pytest.raises(exam_ai.GenerationFailed):
        generate(fake_gemini, qdrant, ["{hỏng"], 1, 0)
    assert exam_ai._generating == set()
    generate(fake_gemini, qdrant, one_mcq(), 1, 0)


def test_quota_rechecked_when_saving():
    """Request khác (worker khác) lưu trước trong lúc chờ Gemini → lúc lưu đếm lại và chặn."""
    q = {"type": "essay", "content": "Trình bày…", "points": 1}
    exams.create_generated_exam(A, "Đề 1", [q], daily_limit=1)
    with pytest.raises(exams.QuotaExceeded):
        exams.create_generated_exam(A, "Đề 2", [q], daily_limit=1)
    assert exam_count() == 1


# ---------- POST /exams/generate (2.10) ----------
def bearer(user_id):
    return {"Authorization": f"Bearer {auth.create_access_token(user_id)}"}


@pytest.fixture
def api_with(monkeypatch, qdrant, fake_gemini):
    """api_with([câu trả lời Gemini…]) → (TestClient, llm giả). Thay pipeline đã nạp model bằng client giả."""
    def make(exam_texts):
        llm = fake_gemini(exam_texts)
        monkeypatch.setattr(deps, "rag", SimpleNamespace(llm=llm, retriever=SimpleNamespace(client=qdrant)))
        return TestClient(api.app), llm      # không dùng "with" → không chạy lifespan (không nạp model)
    return make


def test_generate_over_http_then_review_and_publish(api_with):
    """Phase 2 xong khi: tạo đề AI qua API, xem lại, sửa, publish được; người khác không thấy bản nháp."""
    client, llm = api_with([exam_text([mcq("Câu 1?"), mcq("Câu 2?")], [essay()])])
    res = client.post("/exams/generate", json={"chapter": 3, "n_mcq": 2, "n_essay": 1}, headers=bearer(A))
    assert res.status_code == 201
    body = res.json()
    assert (body["origin"], body["status"], len(body["questions"])) == ("ai", "draft", 3)
    assert body["generation"]["quota"]["remaining"] == exam_ai.AI_GEN_PER_USER_PER_DAY - 1
    assert len(llm.calls) == 2

    exam_id = body["id"]
    assert client.get(f"/exams/{exam_id}/full", headers=bearer(B)).status_code == 404
    q = client.get(f"/exams/{exam_id}/full", headers=bearer(A)).json()["questions"][0]
    assert client.patch(f"/exams/{exam_id}/questions/{q['id']}", json={"content": "Câu 1 (đã sửa)?"},
                        headers=bearer(A)).status_code == 200
    assert client.post(f"/exams/{exam_id}/publish", headers=bearer(A)).json()["status"] == "published"


def test_generate_accepts_chapter_label_and_title(api_with):
    client, _ = api_with([exam_text([mcq()])])
    res = client.post("/exams/generate", json={"chapter": "Chương III", "n_mcq": 1, "n_essay": 0,
                                               "title": "Ôn tập"}, headers=bearer(A))
    assert res.status_code == 201 and res.json()["title"] == "Ôn tập"


@pytest.mark.parametrize("payload,status", [
    ({"chapter": 3, "n_mcq": 21}, 422),                 # ngoài giới hạn → FastAPI chặn
    ({"chapter": 3, "n_essay": 4}, 422),
    ({"n_mcq": 1}, 422),                                # thiếu chương
    ({"chapter": 3, "n_mcq": 0, "n_essay": 0}, 400),    # không có câu nào
    ({"chapter": 9, "n_mcq": 1}, 400),                  # chương không tồn tại
])
def test_generate_bad_input(api_with, payload, status):
    client, llm = api_with([])
    assert client.post("/exams/generate", json=payload, headers=bearer(A)).status_code == status
    assert llm.calls == []


def test_generate_status_codes(api_with, monkeypatch):
    client, _ = api_with([])
    assert client.post("/exams/generate", json={"chapter": 3}).status_code == 401           # chưa đăng nhập

    client, _ = api_with(["{hỏng"])
    res = client.post("/exams/generate", json={"chapter": 3, "n_mcq": 1, "n_essay": 0}, headers=bearer(A))
    assert res.status_code == 503 and "AI chưa tạo được đề" in res.json()["detail"]
    assert exam_count() == 0

    monkeypatch.setattr(exam_ai, "AI_GEN_PER_USER_PER_DAY", 0)
    client, llm = api_with([exam_text([mcq()])])
    res = client.post("/exams/generate", json={"chapter": 3, "n_mcq": 1, "n_essay": 0}, headers=bearer(A))
    assert res.status_code == 429 and "hôm nay" in res.json()["detail"] and llm.calls == []

    monkeypatch.setattr(deps, "rag", None)                                                  # model đang khởi động
    res = TestClient(api.app).post("/exams/generate", json={"chapter": 3, "n_mcq": 1}, headers=bearer(A))
    assert res.status_code == 503
