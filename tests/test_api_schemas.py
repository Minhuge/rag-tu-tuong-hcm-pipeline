"""
Schema trả về của API bài kiểm tra (app/exam_schemas.py) và file openapi.json mà web sinh type từ đó (roadmap 4.2).

- Đi hết một lượt qua HTTP, chạm mọi endpoint: service trả thiếu / thừa khoá so với schema → 500, test hỏng.
- web/openapi.json phải khớp API hiện tại: sửa schema mà quên chạy lại `npm run api:types` → test hỏng.

Chạy:  pytest -q tests/test_api_schemas.py
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import auth, db
from app import main as api

A, B = 1, 2          # A ra đề, B làm bài
OPENAPI_FILE = Path(__file__).resolve().parents[1] / "web" / "openapi.json"


@pytest.fixture(autouse=True)
def users(db_engine):
    with Session(db_engine) as s, s.begin():
        s.add_all([db.User(id=uid, email=f"{n}@example.com", password_hash="x", display_name=n.upper())
                   for uid, n in ((A, "a"), (B, "b"))])


def bearer(user_id):
    return {"Authorization": f"Bearer {auth.create_access_token(user_id)}"}


def call(client, method, path, user, status=200, **kw):
    res = client.request(method, path, headers=bearer(user), **kw)
    assert res.status_code == status, res.text
    return res.json() if res.content else None


def test_every_exam_endpoint_matches_its_schema():
    c = TestClient(api.app)            # không dùng "with" → không nạp model; deps.rag = None → không chấm nền
    assert call(c, "GET", "/chapters", A)[0]["number"] == 0

    exam = call(c, "POST", "/exams", A, 201, json={"title": "Đề thử", "visibility": "link", "duration_minutes": 15})
    eid = exam["id"]
    call(c, "PATCH", f"/exams/{eid}", A, json={"description": "Mô tả"})
    mcq = call(c, "POST", f"/exams/{eid}/questions", A, 201, json={
        "type": "mcq", "content": "Câu một?", "points": 1, "source_page": 20,
        "options": [{"label": "A", "content": "Sai"}, {"label": "B", "content": "Đúng", "is_correct": True}]})
    essay = call(c, "POST", f"/exams/{eid}/questions", A, 201, json={
        "type": "essay", "content": "Trình bày…", "points": 2, "rubric": "- Ý một (2 điểm)"})
    call(c, "PATCH", f"/exams/{eid}/questions/{essay['id']}", A, json={"explanation": "Gợi ý"})
    tmp = call(c, "POST", f"/exams/{eid}/questions", A, 201, json={"type": "essay", "content": "Xoá", "rubric": "x"})
    call(c, "DELETE", f"/exams/{eid}/questions/{tmp['id']}", A, 204)
    assert call(c, "GET", f"/exams/{eid}/full", A)["questions"][0]["options"][1]["is_correct"] is True
    published = call(c, "POST", f"/exams/{eid}/publish", A)
    mine = call(c, "GET", "/exams/mine", A)
    assert (mine[0]["question_count"], mine[0]["submission_count"]) == (2, 0)

    code = published["share_code"]
    taker = call(c, "GET", f"/exams/{eid}?share_code={code}", B)
    assert "share_code" not in taker and "is_correct" not in taker["questions"][0]["options"][0]
    sub = call(c, "POST", f"/exams/{eid}/submissions?share_code={code}", B, 201)
    sid = sub["id"]
    right = next(o["id"] for o in call(c, "GET", f"/exams/{eid}/full", A)["questions"][0]["options"] if o["is_correct"])
    call(c, "PUT", f"/submissions/{sid}/answers/{mcq['id']}", B, json={"selected_option_id": right})
    call(c, "PUT", f"/submissions/{sid}/answers/{essay['id']}", B, json={"essay_text": "Bài viết của B."})

    draft = call(c, "GET", f"/submissions/{sid}", B)
    assert draft["grading"] is None and "correct_option_id" not in draft["questions"][0]   # chưa nộp: chưa lộ đáp án
    early = call(c, "GET", f"/submissions/{sid}/review", A)
    assert "grading_message" not in early["questions"][0]

    result = call(c, "POST", f"/submissions/{sid}/submit", B)
    assert result["grading"] == {"graded": 1, "total": 2, "still_grading": True}
    assert result["questions"][0]["correct_option_id"] == right
    assert call(c, "GET", f"/submissions/{sid}", B)["questions"][1]["grading_status"] == "grading"
    assert call(c, "GET", "/submissions/mine", B)[0]["status"] == "submitted"
    assert call(c, "GET", f"/exams/{eid}/submissions", A)[0]["user"]["email"] == "b@example.com"

    graded = call(c, "PATCH", f"/submissions/{sid}/answers/{essay['id']}/grade", A, json={"score": 1.5})
    assert (graded["status"], graded["score"], graded["grading"]["still_grading"]) == ("graded", 2.5, False)
    call(c, "GET", f"/submissions/{sid}/review", A)


def test_web_openapi_file_is_up_to_date():
    assert OPENAPI_FILE.exists(), "Chưa có web/openapi.json — chạy: cd web && npm run api:types"
    assert json.loads(OPENAPI_FILE.read_text(encoding="utf-8")) == api.app.openapi(), (
        "API đã đổi mà web/openapi.json chưa cập nhật — chạy: cd web && npm run api:types")
