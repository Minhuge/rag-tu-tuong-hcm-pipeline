"""
Test vòng lặp agent (roadmap 5.1, agent/graph.py) với Gemini giả: kịch bản trả lời định sẵn từng lượt →
không gọi mạng, không tốn quota. Tool thật của 5.2 có test riêng.

Chạy:  pytest -q tests/test_agent_graph.py
"""
import asyncio

import pytest
from google.genai import types

from agent import graph as ag
from agent.graph import AgentTool, ToolError, build_agent, run_agent, user_turn


def call(name, **args):
    return types.Part(function_call=types.FunctionCall(id=f"c-{name}", name=name, args=args))


def chunk(*parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=list(parts)))])


class FakeGemini:
    """Mỗi lần gọi lấy một lượt trong script (list các chunk); hết script → lặp lại lượt cuối. Ghi lại config."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.configs, self.contents = [], []

    async def __call__(self, llm, contents, config):
        self.configs.append(config)
        self.contents.append(list(contents))
        turn = self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        for c in turn:
            await asyncio.sleep(0)
            yield c


class Recorder:
    """Tool giả ghi lại user_id và tham số nó nhận được."""

    def __init__(self, result="3 bài", error=None):
        self.calls, self.result, self.error = [], result, error

    def __call__(self, user_id, **args):
        self.calls.append((user_id, args))
        if self.error:
            raise self.error
        return self.result


def tool(run, name="list_my_submissions", params=None):
    return AgentTool(name=name, description="Danh sách bài đã làm.", run=run,
                     parameters=params or {"type": "object", "properties": {}})


def run(fake, tools, question="Tôi được mấy điểm?", user_id=7):
    async def go():
        g = build_agent(None, tools, "system", stream=fake)
        return [ev async for ev in run_agent(g, [user_turn(question)], user_id)]
    return asyncio.run(go())


def done(events):
    assert events[-1]["type"] == "done"
    return events[-1]


def test_tool_gets_user_id_from_context_and_answer_streams():
    rec = Recorder("Bài 12: Chương 3, 7/10 điểm")
    fake = FakeGemini([chunk(call("list_my_submissions"))],
                      [chunk(types.Part(text="Bạn được ")), chunk(types.Part(text="7/10 điểm."))])
    events = run(fake, [tool(rec)], user_id=42)

    assert rec.calls == [(42, {})]
    assert [e for e in events if e["type"] == "tool"] == [{"type": "tool", "name": "list_my_submissions"}]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Bạn được 7/10 điểm."
    d = done(events)
    assert d["answer"] == "Bạn được 7/10 điểm." and d["rounds"] == 1
    assert [(t["name"], t["ok"]) for t in d["tool_calls"]] == [("list_my_submissions", True)]
    # lượt 2 gửi lại nguyên lượt gọi tool của model + kết quả tool (giữ thought signature của Gemini 3)
    sent = fake.contents[1]
    assert sent[1].parts[0].function_call.name == "list_my_submissions"
    resp = sent[2].parts[0].function_response
    assert resp.id == "c-list_my_submissions" and resp.response == {"result": "Bài 12: Chương 3, 7/10 điểm"}


def test_thought_signature_part_is_sent_back_unchanged():
    sig = b"chu-ky"
    fake = FakeGemini([chunk(types.Part(function_call=types.FunctionCall(name="list_my_submissions", args={}),
                                        thought_signature=sig))],
                      [chunk(types.Part(text="Xong."))])
    run(fake, [tool(Recorder())])
    assert fake.contents[1][1].parts[0].thought_signature == sig


def test_model_cannot_pass_user_id():
    rec = Recorder()
    fake = FakeGemini([chunk(call("list_my_submissions", user_id=1))], [chunk(types.Part(text="Không xem được."))])
    d = done(run(fake, [tool(rec)], user_id=7))

    assert rec.calls == []   # tool không chạy
    assert fake.contents[1][2].parts[0].function_response.response == {"error": "Tham số không hợp lệ: user_id."}
    assert d["tool_calls"][0]["ok"] is False


def test_unknown_tool_missing_arg_and_errors_go_back_to_model():
    get = AgentTool(name="get_my_submission", description="Một bài.", run=Recorder(error=ToolError("Không có bài 99.")),
                    parameters={"type": "object", "properties": {"submission_id": {"type": "integer"}},
                                "required": ["submission_id"]})
    boom = tool(Recorder(error=KeyError("db")), name="broken")
    fake = FakeGemini([chunk(call("nope"), call("get_my_submission"), call("get_my_submission", submission_id=99),
                             call("broken"))],
                      [chunk(types.Part(text="Không tìm thấy bài đó."))])
    d = done(run(fake, [get, boom]))

    errors = [p.function_response.response["error"] for p in fake.contents[1][2].parts]
    assert errors == ["Không có tool tên nope.", "Thiếu tham số: submission_id.", "Không có bài 99.", ag._TOOL_FAILED]
    assert d["answer"] == "Không tìm thấy bài đó." and d["rounds"] == 1


def test_endless_tool_calls_stop_and_force_an_answer(monkeypatch):
    monkeypatch.setattr(ag, "MAX_TOOL_ROUNDS", 3)
    rec = Recorder()
    # model cứ đòi gọi tool; chỉ khi bị cấm gọi (lượt cuối) mới trả lời
    class Stubborn(FakeGemini):
        async def __call__(self, llm, contents, config):
            self.configs.append(config)
            mode = config.tool_config and config.tool_config.function_calling_config.mode
            yield chunk(types.Part(text="Theo dữ liệu đã có…") if mode == types.FunctionCallingConfigMode.NONE
                        else call("list_my_submissions"))

    fake = Stubborn([])
    d = done(run(fake, [tool(rec)]))
    assert len(rec.calls) == 3 and d["rounds"] == 3
    assert len(fake.configs) == 4 and d["answer"] == "Theo dữ liệu đã có…"


def test_text_before_tool_call_stays_in_answer():
    fake = FakeGemini([chunk(types.Part(text="Để mình xem."), call("list_my_submissions"))],
                      [chunk(types.Part(text="Bạn được 7 điểm."))])
    events = run(fake, [tool(Recorder())])
    shown = "".join(e["text"] for e in events if e["type"] == "delta")
    assert shown == done(events)["answer"] == "Để mình xem.\n\nBạn được 7 điểm."


def test_empty_answer_and_timeout(monkeypatch):
    assert done(run(FakeGemini([chunk(types.Part(text=""))]), []))["answer"] == ag.EMPTY_ANSWER

    async def slow(llm, contents, config):
        await asyncio.sleep(1)
        yield chunk(types.Part(text="muộn"))

    async def go():
        g = build_agent(None, [], "system", stream=slow)
        return [ev async for ev in run_agent(g, [user_turn("?")], 1, timeout=0.05)]
    with pytest.raises(RuntimeError, match="quá"):
        asyncio.run(go())


def test_declarations_never_mention_user_id():
    t = tool(Recorder(), params={"type": "object", "properties": {"submission_id": {"type": "integer"}}})
    fake = FakeGemini([chunk(types.Part(text="ok"))])
    run(fake, [t])
    decl = fake.configs[0].tools[0].function_declarations[0]
    assert decl.name == "list_my_submissions" and "user_id" not in str(decl.parameters_json_schema)


def test_oversized_tool_result_is_cut_before_it_reaches_the_model():
    fake = FakeGemini([chunk(call("list_my_submissions"))], [chunk(types.Part(text="ok"))])
    run(fake, [tool(Recorder("x" * 50_000))])
    sent = fake.contents[1][2].parts[0].function_response.response["result"]
    assert sent == "x" * ag.MAX_TOOL_RESULT_CHARS + ag.TRUNCATED_NOTE


def test_system_prompt_can_be_rebuilt_on_every_call():
    prompts = iter(["ngày 1", "ngày 2"])
    fake = FakeGemini([chunk(call("list_my_submissions"))], [chunk(types.Part(text="ok"))])

    async def go():
        g = build_agent(None, [tool(Recorder())], lambda: next(prompts), stream=fake)
        return [ev async for ev in run_agent(g, [user_turn("?")], 1)]
    asyncio.run(go())
    assert [c.system_instruction for c in fake.configs] == ["ngày 1", "ngày 2"]


# ---------- quota (5.5) ----------
class OutOfQuota(Exception):
    pass


class Meter:
    """charge giả: còn `left` lượt; mỗi lần gọi trừ 1 và trả số còn lại; hết thì ném OutOfQuota."""

    def __init__(self, left):
        self.left, self.users = left, []

    def __call__(self, user_id):
        self.users.append(user_id)
        if self.left <= 0:
            raise OutOfQuota("hết lượt")
        self.left -= 1
        return self.left


def run_charged(fake, meter, tools_=None, user_id=7):
    async def go():
        g = build_agent(None, tools_ or [tool(Recorder())], "system", stream=fake, charge=meter)
        return [ev async for ev in run_agent(g, [user_turn("?")], user_id)]
    return asyncio.run(go())


def test_every_model_call_is_charged_to_the_logged_in_user():
    meter = Meter(10)
    fake = FakeGemini([chunk(call("list_my_submissions"))], [chunk(types.Part(text="ok"))])
    d = done(run_charged(fake, meter, user_id=42))
    assert meter.users == [42, 42] and (d["llm_calls"], d["remaining"]) == (2, 8)


def test_last_remaining_call_answers_without_tools():
    """Còn đúng 2 lượt mà model muốn gọi tool mãi → lần 2 tắt tool, kèm lời nhắc trả lời bằng dữ liệu đã có."""
    meter, rec = Meter(2), Recorder()

    class Stubborn(FakeGemini):
        async def __call__(self, llm, contents, config):
            self.configs.append(config)
            mode = config.tool_config and config.tool_config.function_calling_config.mode
            yield chunk(types.Part(text="Mới xem được danh sách.") if mode == types.FunctionCallingConfigMode.NONE
                        else call("list_my_submissions"))

    fake = Stubborn([])
    d = done(run_charged(fake, meter, [tool(rec)]))
    assert len(fake.configs) == 2 and len(rec.calls) == 1
    assert fake.configs[1].system_instruction == "system" + ag.LAST_CALL_NOTE
    assert ag.LAST_CALL_NOTE not in fake.configs[0].system_instruction
    assert (d["answer"], d["remaining"], d["llm_calls"]) == ("Mới xem được danh sách.", 0, 2)


def test_no_quota_left_fails_before_calling_gemini():
    fake = FakeGemini([chunk(types.Part(text="không được gọi"))])
    with pytest.raises(OutOfQuota):
        run_charged(fake, Meter(0))
    assert fake.configs == []


def test_round_limit_also_adds_last_call_note(monkeypatch):
    monkeypatch.setattr(ag, "MAX_TOOL_ROUNDS", 1)
    fake = FakeGemini([chunk(call("list_my_submissions"))], [chunk(types.Part(text="ok"))])
    d = done(run(fake, [tool(Recorder())]))
    assert fake.configs[1].system_instruction.endswith(ag.LAST_CALL_NOTE) and d["remaining"] is None


# ---------- tool async, ToolResult, once_per_turn (5.8) ----------
def test_async_tool_is_awaited_and_its_data_is_logged():
    seen = []

    async def make(user_id, chapter):
        await asyncio.sleep(0)
        seen.append((user_id, chapter))
        return ag.ToolResult("Đã tạo đề nháp.", {"exam_id": 9, "link": "/exams/9/edit"})

    t = AgentTool("create_exam_draft", "Tạo đề.", {"type": "object", "properties": {"chapter": {"type": "integer"}}},
                  make, once_per_turn=True)
    fake = FakeGemini([chunk(call("create_exam_draft", chapter=3))], [chunk(types.Part(text="Xong."))])
    d = done(run(fake, [t], user_id=5))
    assert seen == [(5, 3)]
    assert d["tool_calls"][0]["data"] == {"exam_id": 9, "link": "/exams/9/edit"}
    assert fake.contents[1][2].parts[0].function_response.response == {"result": "Đã tạo đề nháp."}


def test_once_per_turn_tool_runs_only_once_per_message():
    rec = Recorder("Đã tạo.")
    t = AgentTool("create_exam_draft", "Tạo đề.", {"type": "object", "properties": {"chapter": {"type": "integer"}}},
                  rec, once_per_turn=True)
    # cùng một lượt gọi 2 lần, rồi lượt sau gọi thêm lần nữa → chỉ lần đầu chạy
    fake = FakeGemini([chunk(call("create_exam_draft", chapter=1), call("create_exam_draft", chapter=2))],
                      [chunk(call("create_exam_draft", chapter=3))], [chunk(types.Part(text="Chỉ tạo một đề."))])
    d = done(run(fake, [t]))
    assert [c[1] for c in rec.calls] == [{"chapter": 1}]
    assert [c["ok"] for c in d["tool_calls"]] == [True, False, False]
    assert "một lần" in fake.contents[1][2].parts[1].function_response.response["error"]


def test_once_per_turn_allows_retry_after_a_failed_call():
    rec = Recorder(error=ToolError("Hết lượt tạo đề."))
    t = AgentTool("create_exam_draft", "Tạo đề.", {"type": "object", "properties": {}}, rec, once_per_turn=True)
    fake = FakeGemini([chunk(call("create_exam_draft"))], [chunk(call("create_exam_draft"))],
                      [chunk(types.Part(text="Hết lượt."))])
    run(fake, [t])
    assert len(rec.calls) == 2                       # lần trước lỗi (không tạo gì) → được thử lại
