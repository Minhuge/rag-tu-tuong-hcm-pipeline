"""
Test các endpoint lịch sử chat của api.py với pipeline giả → không nạp model, không tốn quota Gemini.

Chạy:  pytest -q test_api.py
"""
import json

import pytest
from fastapi.testclient import TestClient

import api

HEAD_A = {"X-Client-Id": "client-aaaa"}
HEAD_B = {"X-Client-Id": "client-bbbb"}


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
    monkeypatch.setattr(api, "rag", fake)
    c = TestClient(api.app)        # không dùng "with" → không chạy lifespan (không nạp model thật)
    c.fake = fake
    return c


def chat(client, message, conversation_id=None, headers=HEAD_A, retry=False):
    res = client.post("/chat", json={"conversation_id": conversation_id, "message": message, "retry": retry},
                      headers=headers)
    assert res.status_code == 200 and res.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in res.text.splitlines() if line]


def test_chat_streams_and_saves_both_messages(client):
    events = chat(client, "Câu 1")
    assert [e["type"] for e in events] == ["conversation", "status", "delta", "delta", "delta", "done"]
    conv = events[0]
    assert conv["title"] == "Câu 1"
    assert events[-1]["message_id"] > 0

    full = client.get(f"/conversations/{conv['id']}", headers=HEAD_A).json()
    assert [(m["role"], m["content"]) for m in full["messages"]] == [
        ("user", "Câu 1"), ("assistant", "Trả lời cho: Câu 1")]
    assert full["messages"][1]["meta"]["timings"] == {"total_s": 0.1}


def test_followup_receives_history(client):
    conv_id = chat(client, "Câu 1")[0]["id"]
    chat(client, "nói rõ hơn", conv_id)
    assert client.fake.histories == [[], [{"role": "user", "content": "Câu 1"},
                                          {"role": "assistant", "content": "Trả lời cho: Câu 1"}]]
    assert [c["id"] for c in client.get("/conversations", headers=HEAD_A).json()] == [conv_id]


def test_error_midstream_keeps_user_message_for_retry(client):
    client.fake.fail = True
    events = chat(client, "Câu lỗi")
    assert events[-1] == {"type": "error", "message": "Đã hết lượt gọi Gemini trong ngày"}
    conv_id = events[0]["id"]
    msgs = client.get(f"/conversations/{conv_id}", headers=HEAD_A).json()["messages"]
    assert [m["role"] for m in msgs] == ["user"]          # câu trả lời dở dang không được lưu

    client.fake.fail = False
    chat(client, "Câu lỗi", conv_id, retry=True)
    msgs = client.get(f"/conversations/{conv_id}", headers=HEAD_A).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]   # không lưu trùng câu hỏi


def test_clients_are_isolated(client):
    conv_id = chat(client, "Của A")[0]["id"]
    assert client.get("/conversations", headers=HEAD_B).json() == []
    assert client.get(f"/conversations/{conv_id}", headers=HEAD_B).status_code == 404
    res = client.post("/chat", json={"conversation_id": conv_id, "message": "chen vào"}, headers=HEAD_B)
    assert res.status_code == 404
    assert client.delete(f"/conversations/{conv_id}", headers=HEAD_B).status_code == 404


def test_delete(client):
    conv_id = chat(client, "Xoá tôi")[0]["id"]
    assert client.delete(f"/conversations/{conv_id}", headers=HEAD_A).status_code == 204
    assert client.get("/conversations", headers=HEAD_A).json() == []


@pytest.mark.parametrize("headers", [{}, {"X-Client-Id": "short"}, {"X-Client-Id": "has spaces!!"}])
def test_client_id_required(client, headers):
    assert client.get("/conversations", headers=headers).status_code in (400, 422)
