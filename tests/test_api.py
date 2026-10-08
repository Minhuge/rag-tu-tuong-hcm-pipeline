"""
Test các endpoint lịch sử chat (app/routers/chat.py) với pipeline giả → không nạp model, không tốn quota Gemini.

Chạy:  pytest -q tests/test_api.py
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import deps
from app import main as api
from app import auth


class FakeRAG:
    """ask_stream giả: ghi lại lịch sử nhận được, trả về vài đoạn chữ rồi kết quả cuối."""

    def __init__(self, fail=False):
        self.fail, self.histories = fail, []

    async def ask_stream(self, question, history=None):
        self.histories.append(history)
        yield {"type": "status", "step": "generate"}
        for piece in ["Trả lời ", "cho: ", question]:
            yield {"type": "delta", "text": piece}
        if self.fail:
            raise RuntimeError("Đã hết lượt gọi Gemini trong ngày")
        yield {"type": "done", "result": {"answer": f"Trả lời cho: {question}", "sources": [],
                                          "blocked_by": None, "timings": {"total_s": 0.1}}}


@pytest.fixture
def client(db_engine, monkeypatch):
    fake = FakeRAG()
    monkeypatch.setattr(deps, "rag", fake)
    c = TestClient(api.app)        # không dùng "with" → không chạy lifespan (không nạp model thật)
    c.fake = fake
    return c


def bearer(email):
    user = auth.register(email, "matkhau123")
    return {"Authorization": f"Bearer {auth.create_access_token(user['id'])}"}


@pytest.fixture
def head_a(db_engine):
    return bearer("a@example.com")


@pytest.fixture
def head_b(db_engine):
    return bearer("b@example.com")


def chat(client, message, headers, conversation_id=None, retry=False):
    res = client.post("/chat", json={"conversation_id": conversation_id, "message": message, "retry": retry},
                      headers=headers)
    assert res.status_code == 200 and res.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in res.text.splitlines() if line]


def test_chat_streams_and_saves_both_messages(client, head_a):
    events = chat(client, "Câu 1", head_a)
    assert [e["type"] for e in events] == ["conversation", "status", "delta", "delta", "delta", "done"]
    conv = events[0]
    assert conv["title"] == "Câu 1"
    assert events[-1]["message_id"] > 0

    full = client.get(f"/conversations/{conv['id']}", headers=head_a).json()
    assert [(m["role"], m["content"]) for m in full["messages"]] == [
        ("user", "Câu 1"), ("assistant", "Trả lời cho: Câu 1")]
    assert full["messages"][1]["meta"]["timings"] == {"total_s": 0.1}


def test_followup_receives_history(client, head_a):
    conv_id = chat(client, "Câu 1", head_a)[0]["id"]
    chat(client, "nói rõ hơn", head_a, conv_id)
    assert client.fake.histories == [[], [{"role": "user", "content": "Câu 1"},
                                          {"role": "assistant", "content": "Trả lời cho: Câu 1"}]]
    assert [c["id"] for c in client.get("/conversations", headers=head_a).json()] == [conv_id]


def test_error_midstream_keeps_user_message_for_retry(client, head_a):
    client.fake.fail = True
    events = chat(client, "Câu lỗi", head_a)
    assert events[-1] == {"type": "error", "message": "Đã hết lượt gọi Gemini trong ngày"}
    conv_id = events[0]["id"]
    msgs = client.get(f"/conversations/{conv_id}", headers=head_a).json()["messages"]
    assert [m["role"] for m in msgs] == ["user"]          # câu trả lời dở dang không được lưu

    client.fake.fail = False
    chat(client, "Câu lỗi", head_a, conv_id, retry=True)
    msgs = client.get(f"/conversations/{conv_id}", headers=head_a).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]   # không lưu trùng câu hỏi


def test_users_are_isolated(client, head_a, head_b):
    conv_id = chat(client, "Của A", head_a)[0]["id"]
    assert client.get("/conversations", headers=head_b).json() == []
    assert client.get(f"/conversations/{conv_id}", headers=head_b).status_code == 404
    res = client.post("/chat", json={"conversation_id": conv_id, "message": "chen vào"}, headers=head_b)
    assert res.status_code == 404
    assert client.delete(f"/conversations/{conv_id}", headers=head_b).status_code == 404


def test_delete(client, head_a):
    conv_id = chat(client, "Xoá tôi", head_a)[0]["id"]
    assert client.delete(f"/conversations/{conv_id}", headers=head_a).status_code == 204
    assert client.get("/conversations", headers=head_a).json() == []


@pytest.mark.parametrize("method, path", [("get", "/conversations"), ("get", "/conversations/1"),
                                          ("delete", "/conversations/1"), ("post", "/chat"),
                                          ("post", "/ask"), ("post", "/search")])
def test_endpoints_require_login(client, method, path):
    res = getattr(client, method)(path)
    assert res.status_code == 401 and res.headers["www-authenticate"] == "Bearer"
