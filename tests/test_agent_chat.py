"""
Test Trợ lý bài kiểm tra qua endpoint /chat (roadmap 5.6): cuộc trò chuyện mode="exam" đi qua lớp kiểm tra đầu vào rồi
agent (tool thật, quota thật, database tạm); Gemini giả theo kịch bản → không nạp model, không tốn quota.

Chạy:  pytest -q tests/test_agent_chat.py
"""
import json

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from agent.graph import build_agent
from agent.tools import DATA_TOOLS
from app import agent_ai, auth, deps
from app import main as api


class FakeRAG:
    """screen giả (lớp 1a + 1b): câu có chữ "độc hại" bị chặn. ask_stream: chế độ hỏi giáo trình."""

    def __init__(self):
        self.screened, self.docs_questions = [], []

    async def screen(self, question):
        self.screened.append(question)
        if "độc hại" in question:
            return {"blocked_by": "input-guard", "answer": "Yêu cầu không được hỗ trợ.",
                    "guard": {"label": "Unsafe", "categories": ["Violent"]}, "input_policy": "block"}
        return {"blocked_by": None, "answer": None, "guard": {"label": "Safe", "categories": []},
                "input_policy": "pass"}

    async def ask_stream(self, question, history=None):
        self.docs_questions.append(question)
        yield {"type": "done", "result": {"answer": "giáo trình", "sources": [], "blocked_by": None}}


class Gemini:
    """Gemini giả: lượt đầu của mỗi câu hỏi gọi list_my_submissions, lượt sau trả lời. Ghi lại contents gửi đi."""

    def __init__(self):
        self.calls = []

    async def __call__(self, llm, contents, config):
        self.calls.append(list(contents))
        last = contents[-1].parts[0]
        part = (types.Part(text="Bạn chưa làm bài nào.") if last.function_response
                else types.Part(function_call=types.FunctionCall(name="list_my_submissions", args={})))
        yield types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[part]))])


@pytest.fixture
def client(db_engine, monkeypatch):
    rag, gemini = FakeRAG(), Gemini()
    graph = build_agent(None, DATA_TOOLS, "system", stream=gemini, charge=agent_ai.charge_agent_call)
    monkeypatch.setattr(deps, "rag", rag)
    monkeypatch.setattr(agent_ai, "get_agent", lambda r: graph)
    c = TestClient(api.app)
    c.rag, c.gemini = rag, gemini
    return c


@pytest.fixture
def head_a(db_engine):
    user = auth.register("a@example.com", "matkhau123")
    return {"Authorization": f"Bearer {auth.create_access_token(user['id'])}"}


def ask(client, headers, message, conversation_id=None, mode="exam"):
    res = client.post("/chat", json={"conversation_id": conversation_id, "message": message, "mode": mode},
                      headers=headers)
    assert res.status_code == 200, res.text
    return [json.loads(line) for line in res.text.splitlines() if line]


def test_exam_conversation_runs_agent_and_saves_tool_trace(client, head_a):
    events = ask(client, head_a, "Bài gần nhất tôi được mấy điểm?")
    assert [e["type"] for e in events] == ["conversation", "status", "status", "tool", "delta", "done"]
    assert events[0]["mode"] == "exam"
    assert [e.get("step") for e in events[1:3]] == ["guard", "agent"]
    assert events[3] == {"type": "tool", "name": "list_my_submissions"}
    done = events[-1]
    assert done["result"]["answer"] == "Bạn chưa làm bài nào." and done["message_id"]
    assert (done["result"]["llm_calls"], done["result"]["remaining"]) == (2, agent_ai.AGENT_CALLS_PER_USER_PER_DAY - 2)
    assert client.rag.docs_questions == []                                     # không đi đường hỏi giáo trình

    conv = client.get(f"/conversations/{events[0]['id']}", headers=head_a).json()
    meta = conv["messages"][1]["meta"]
    assert meta["mode"] == "exam" and [t["name"] for t in meta["tool_calls"]] == ["list_my_submissions"]
    assert "result" not in json.dumps(meta)                                    # không lưu kết quả tool
    assert agent_ai.agent_quota(1)["used"] == 2


def test_follow_up_keeps_exam_mode_and_sends_history(client, head_a):
    conv_id = ask(client, head_a, "Bài gần nhất?")[0]["id"]
    # gửi kèm mode="docs" cũng không đổi được chế độ của cuộc trò chuyện đã có
    events = ask(client, head_a, "Còn bài trước đó?", conversation_id=conv_id, mode="docs")
    assert events[-1]["result"]["mode"] == "exam" and client.rag.docs_questions == []
    sent = client.gemini.calls[2]                                               # lần gọi đầu của câu hỏi thứ hai
    assert [(c.role, c.parts[0].text) for c in sent] == [
        ("user", "Bài gần nhất?"), ("model", "Bạn chưa làm bài nào."), ("user", "Còn bài trước đó?")]


def test_blocked_input_never_reaches_agent_or_quota(client, head_a):
    events = ask(client, head_a, "câu hỏi độc hại")
    assert events[-1]["result"]["blocked_by"] == "input-guard"
    assert "agent" not in [e.get("step") for e in events]
    assert client.gemini.calls == [] and agent_ai.agent_quota(1)["used"] == 0


def test_no_quota_left_is_429_and_saves_nothing(client, head_a, monkeypatch):
    conv_id = ask(client, head_a, "Bài gần nhất?")[0]["id"]
    monkeypatch.setattr(agent_ai, "AGENT_CALLS_PER_USER_PER_DAY", 2)          # đã dùng 2
    for body in ({"message": "Câu mới", "mode": "exam"}, {"conversation_id": conv_id, "message": "Câu tiếp"}):
        res = client.post("/chat", json=body, headers=head_a)
        assert res.status_code == 429 and "hết 2 lượt" in res.json()["detail"]
    assert len(client.get("/conversations", headers=head_a).json()) == 1
    assert len(client.get(f"/conversations/{conv_id}", headers=head_a).json()["messages"]) == 2
    # chế độ hỏi giáo trình không bị giới hạn này
    assert ask(client, head_a, "Câu giáo trình", mode="docs")[-1]["result"]["answer"] == "giáo trình"


def test_quota_used_up_by_another_tab_mid_request_is_an_error_event(client, head_a, monkeypatch):
    """Kiểm tra trước khi lưu còn lượt, nhưng tới lúc gọi Gemini thì tab khác đã dùng hết → sự kiện error."""
    monkeypatch.setattr(agent_ai, "agent_quota", lambda uid: {"limit": 1, "used": 0, "remaining": 1})
    monkeypatch.setattr(agent_ai, "AGENT_CALLS_PER_USER_PER_DAY", 0)
    events = ask(client, head_a, "Bài gần nhất?")
    assert events[-1]["type"] == "error" and "hết 0 lượt" in events[-1]["message"]


def test_other_users_conversation_is_404(client, head_a):
    conv_id = ask(client, head_a, "Bài gần nhất?")[0]["id"]
    other = auth.register("b@example.com", "matkhau123")
    head_b = {"Authorization": f"Bearer {auth.create_access_token(other['id'])}"}
    assert client.post("/chat", json={"conversation_id": conv_id, "message": "x"}, headers=head_b).status_code == 404


def test_history_is_cut_per_message(monkeypatch):
    monkeypatch.setattr(agent_ai, "AGENT_HISTORY_CHARS", 10)
    out = agent_ai.history_contents([{"role": "user", "content": "a  b\n c"},
                                     {"role": "assistant", "content": "x" * 50}])
    assert [(c.role, c.parts[0].text) for c in out] == [("user", "a b c"), ("model", "x" * 9 + "…")]


def test_created_draft_data_is_saved_for_the_draft_button(db_engine, monkeypatch, head_a):
    """5.8: dữ liệu đề nháp (tool_calls[].data) nằm trong messages.meta → F5 vẫn hiện nút "Xem lại & xuất bản"."""
    from agent.graph import AgentTool, ToolResult

    tool = AgentTool("create_exam_draft", "Tạo đề.", {"type": "object", "properties": {"chapter": {"type": "integer"}}},
                     lambda user_id, chapter: ToolResult("Đã tạo đề nháp.", {"exam_id": 7, "title": "Chương III",
                                                                            "questions": 5, "link": "/exams/7/edit"}),
                     once_per_turn=True)

    class Creates(Gemini):
        async def __call__(self, llm, contents, config):
            self.calls.append(list(contents))
            last = contents[-1].parts[0]
            part = (types.Part(text="Đã tạo [Mở đề nháp](/exams/7/edit).") if last.function_response
                    else types.Part(function_call=types.FunctionCall(name="create_exam_draft", args={"chapter": 3})))
            yield types.GenerateContentResponse(candidates=[types.Candidate(
                content=types.Content(role="model", parts=[part]))])

    graph = build_agent(None, [tool], "system", stream=Creates(), charge=agent_ai.charge_agent_call)
    monkeypatch.setattr(deps, "rag", FakeRAG())
    monkeypatch.setattr(agent_ai, "get_agent", lambda r: graph)
    client = TestClient(api.app)
    conv_id = ask(client, head_a, "Tạo đề chương 3")[0]["id"]
    meta = client.get(f"/conversations/{conv_id}", headers=head_a).json()["messages"][1]["meta"]
    assert meta["tool_calls"][0]["data"]["link"] == "/exams/7/edit"
