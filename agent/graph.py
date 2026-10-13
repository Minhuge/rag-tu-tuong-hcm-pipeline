"""
Vòng lặp agent của "Trợ lý bài kiểm tra" (roadmap 5.1): LangGraph điều khiển vòng lặp, Gemini gọi qua google-genai
như mọi chỗ khác trong dự án (call_gemini / stream_gemini của rag/pipeline.py).

  START → model ──(có function_call)──→ tools ──→ model ──→ … ──(chỉ có chữ)──→ END
            └── đã gọi tool MAX_TOOL_ROUNDS lượt → lần gọi model cuối TẮT tool → buộc phải trả lời bằng dữ liệu đã có

Vì sao chọn cách này (xem ROADMAP_CHI_TIET.md 5.1):
  - LangGraph: giới hạn bước (recursion_limit làm chốt chặn cuối), stream từng bước để xem agent đã gọi tool gì,
    context theo từng lượt chạy để truyền user_id lấy từ JWT — model không bao giờ thấy hay điền user_id.
  - google-genai trực tiếp (không qua langchain-google-genai): dùng lại việc đổi model dự phòng / chờ 429 / báo 402,
    thinking_level, các lỗi genai_errors mà router đã bắt; nội dung model trả về được gửi lại nguyên vẹn nên giữ
    đúng thought signature mà Gemini 3 bắt buộc khi gọi tool nhiều lượt.

Tool (5.2) là AgentTool: run(user_id, **args) → str (hoặc ToolResult: chữ cho model + dữ liệu cho giao diện).
Hàm đồng bộ (service của Phase 1) chạy trong thread; hàm async (tạo đề bằng AI, 5.8) được await trực tiếp.
once_per_turn: tool chỉ chạy thành công một lần mỗi tin nhắn (tool ghi — không để model lỡ tay / bị dắt tạo nhiều đề).
JSON schema của tool chỉ mô tả tham số model được điền; tham số lạ (kể cả user_id) bị từ chối, không chạy tool.
Lỗi model nên biết (không thấy bài, chưa nộp…) → tool ném ToolError; lỗi khác chỉ báo chung, không lộ chi tiết.

Sự kiện của run_agent giống ask_stream của chế độ hỏi giáo trình, để 5.6 nối vào cùng endpoint chat:
  {"type": "tool", "name"}  → đang gọi tool (UI hiện "đang xem bài làm…")
  {"type": "delta", "text"} → từng đoạn chữ của câu trả lời
  {"type": "done", "answer", "tool_calls", "rounds", "llm_calls", "remaining"} → kết quả cuối; tool_calls ghi vào
     messages.meta để debug; remaining = số lượt còn lại hôm nay (None nếu không có charge)

Quota (5.5): build_agent(charge=…) — charge(user_id) chạy TRƯỚC mỗi lần gọi Gemini, trả số lượt còn lại hoặc ném lỗi
hết lượt (lỗi này đi thẳng ra ngoài run_agent). Còn 0 lượt sau lần này → lần này là lần cuối, tắt tool như khi chạm
MAX_TOOL_ROUNDS, nên người dùng vẫn nhận được câu trả lời thay vì lỗi giữa chừng.
"""
import asyncio
import inspect
import operator
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from google.genai import types
from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from rag.pipeline import gemini_config, stream_gemini

# Một tin nhắn gọi tool tối đa ngần này lượt (một lượt có thể gồm vài tool gọi song song trong cùng câu trả lời).
MAX_TOOL_ROUNDS = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "5"))
# Mỗi lượt = 2 bước của graph (model + tools), cộng lượt trả lời cuối. Chỉ chạm tới khi giới hạn mềm ở trên hỏng.
RECURSION_LIMIT = 2 * MAX_TOOL_ROUNDS + 3
# Cả lượt, gồm mọi lần gọi Gemini và tool. create_exam_draft (5.8) tạo đề bằng AI mất 15–60 s, có khi hơn.
AGENT_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "180"))

# Chốt chặn cuối cho mọi tool (tool trong agent/tools.py tự giữ dưới MAX_RESULT_CHARS = 6000): kết quả nằm lại trong
# hội thoại và bị gửi lại ở mọi lần gọi model sau đó, nên một tool lỗi trả quá dài sẽ nhân lên nhiều lần.
MAX_TOOL_RESULT_CHARS = 8000
TRUNCATED_NOTE = "\n[… kết quả quá dài, đã cắt bớt]"

# Thêm vào prompt ở lần gọi cuối (chạm giới hạn lượt tool hoặc hết quota): trả lời thật thà với dữ liệu đang có.
LAST_CALL_NOTE = ("\n\nĐây là lần trả lời cuối cho câu hỏi này, không gọi thêm tool được nữa: trả lời bằng dữ liệu "
                  "đã có; nếu chưa đủ để trả lời thì nói rõ phần nào chưa xem được.")

EMPTY_ANSWER = "Mình chưa trả lời được câu này. Bạn thử hỏi lại cụ thể hơn nhé."
_TOOL_FAILED = "Tool gặp lỗi nội bộ, chưa lấy được dữ liệu."


class ToolError(Exception):
    """Lỗi tool muốn model đọc được (vd. exams.NotFound đổi thành "Không có bài 12 của bạn.")."""


@dataclass(frozen=True)
class ToolResult:
    """Kết quả tool kèm dữ liệu cho giao diện (vd. đề nháp vừa tạo → nút mở đề), lưu vào tool_calls[].data."""
    text: str
    data: dict


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict             # JSON schema các tham số model điền — không bao giờ có user_id
    run: Callable[..., Any]      # run(user_id, **args) → str | ToolResult; hàm thường hoặc async
    once_per_turn: bool = False  # tool ghi: chỉ thành công một lần mỗi tin nhắn

    def declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(name=self.name, description=self.description,
                                         parameters_json_schema=self.parameters)


@dataclass(frozen=True)
class AgentContext:
    """Dữ liệu của lượt chạy do code điền (từ JWT), không nằm trong state nên model không đọc / sửa được."""
    user_id: int


class AgentState(TypedDict):
    contents: Annotated[list[types.Content], operator.add]   # hội thoại gửi Gemini, model + tool nối thêm vào
    rounds: int                                              # số lượt đã gọi tool
    tool_log: Annotated[list[dict], operator.add]            # tool đã gọi: tên, tham số, ok, thời gian
    answer: str                                              # chữ người dùng đã thấy (mọi lượt model)
    llm_calls: int                                           # số lần đã gọi Gemini trong lượt này
    remaining: int | None                                    # lượt còn lại hôm nay sau lần gọi gần nhất
    final: bool                                              # lần gọi model vừa rồi đã tắt tool (lần cuối)


def user_turn(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def _visible_text(part: types.Part) -> str:
    return "" if part.thought or not part.text else part.text


def _bad_args(tool: AgentTool, args: dict) -> str | None:
    """Tham số model điền có đúng tên trong schema không. Chỉ kiểm tên; kiểu / giá trị do tool kiểm."""
    allowed = set(tool.parameters.get("properties", {}))
    unknown = sorted(set(args) - allowed)
    if unknown:
        return f"Tham số không hợp lệ: {', '.join(unknown)}."
    missing = sorted(set(tool.parameters.get("required", [])) - set(args))
    if missing:
        return f"Thiếu tham số: {', '.join(missing)}."
    return None


async def _run_tool(tool: AgentTool | None, name: str, args: dict, user_id: int) -> tuple[bool, str, dict | None]:
    """(ok, chữ cho model, dữ liệu cho giao diện | None)."""
    if tool is None:
        return False, f"Không có tool tên {name}.", None
    if problem := _bad_args(tool, args):
        return False, problem, None
    try:
        if inspect.iscoroutinefunction(tool.run):
            result = await tool.run(user_id, **args)
        else:
            result = await asyncio.to_thread(tool.run, user_id, **args)
        text, data = (result.text, result.data) if isinstance(result, ToolResult) else (result, None)
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + TRUNCATED_NOTE
        return True, text, data
    except ToolError as e:
        return False, str(e), None
    except Exception as e:                                     # lỗi lập trình / database: không lộ chi tiết cho model
        print(f"  [agent] tool {name} lỗi: {type(e).__name__}: {e}")
        return False, _TOOL_FAILED, None


def build_agent(llm, tools: list[AgentTool], system: str | Callable[[], str], *, stream=stream_gemini,
                charge: Callable[[int], int] | None = None):
    """
    llm: genai.Client. system: prompt, hoặc hàm tạo prompt (gọi lại mỗi lần gọi model — prompt có ngày hôm nay).
    stream(llm, contents, config) → các chunk của Gemini; test truyền bản giả.
    charge(user_id) → số lượt còn lại sau lần gọi này (đồng bộ, chạy trong thread); hết lượt thì ném lỗi. None = không
    giới hạn (prototype, test). Trả về graph đã compile; chạy bằng run_agent.
    """
    by_name = {t.name: t for t in tools}
    declarations = types.Tool(function_declarations=[t.declaration() for t in tools])
    no_tools = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
        mode=types.FunctionCallingConfigMode.NONE))

    def config(last: bool) -> types.GenerateContentConfig:
        prompt = system() if callable(system) else system
        if not last:
            return gemini_config(prompt).model_copy(update={"tools": [declarations]})
        # Lượt cuối vẫn khai báo tool (lịch sử có function_call) nhưng cấm gọi → model phải trả lời bằng chữ.
        return gemini_config(prompt + LAST_CALL_NOTE).model_copy(update={"tools": [declarations],
                                                                         "tool_config": no_tools})

    async def model(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        write = get_stream_writer()
        remaining = await asyncio.to_thread(charge, runtime.context.user_id) if charge else None
        last = state["rounds"] >= MAX_TOOL_ROUNDS or remaining == 0
        parts: list[types.Part] = []
        sep = "\n\n" if state["answer"] else ""   # chữ của lượt trước đã hiện → xuống đoạn
        text = ""
        async for chunk in stream(llm, state["contents"], config(last)):
            for part in chunk.candidates[0].content.parts:
                parts.append(part)   # giữ mọi part (cả part rỗng chỉ mang thought signature) để gửi lại nguyên vẹn
                if piece := _visible_text(part):
                    write({"type": "delta", "text": sep + piece if not text else piece})
                    text += piece
        update: dict[str, Any] = {"contents": [types.Content(role="model", parts=parts)], "final": last,
                                  "llm_calls": state["llm_calls"] + 1, "remaining": remaining}
        if text.strip():
            update["answer"] = state["answer"] + sep + text
        return update

    def after_model(state: AgentState) -> str:
        calls = any(p.function_call for p in state["contents"][-1].parts or [])
        return "tools" if calls and not state["final"] else END

    async def call_tools(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        write = get_stream_writer()
        responses, log = [], []
        for fc in (p.function_call for p in state["contents"][-1].parts if p.function_call):
            args = dict(fc.args or {})
            tool = by_name.get(fc.name)
            done_before = any(c["name"] == fc.name and c["ok"] for c in [*state["tool_log"], *log])
            t = time.perf_counter()
            if tool is not None and tool.once_per_turn and done_before:
                ok, result, data = False, "Mỗi tin nhắn chỉ làm việc này một lần — đã làm ở trên rồi.", None
            else:
                write({"type": "tool", "name": fc.name})
                ok, result, data = await _run_tool(tool, fc.name, args, runtime.context.user_id)
            entry = {"name": fc.name, "args": args, "ok": ok, "ms": round((time.perf_counter() - t) * 1000)}
            if data is not None:
                entry["data"] = data
            log.append(entry)
            responses.append(types.Part(function_response=types.FunctionResponse(
                id=fc.id, name=fc.name, response={"result": result} if ok else {"error": result})))
        return {"contents": [types.Content(role="user", parts=responses)], "rounds": state["rounds"] + 1,
                "tool_log": log}

    g = StateGraph(AgentState, context_schema=AgentContext)
    g.add_node("model", model)
    g.add_node("tools", call_tools)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", after_model, ["tools", END])
    g.add_edge("tools", "model")
    return g.compile()


async def run_agent(graph, contents: list[types.Content], user_id: int, *, timeout: float = AGENT_TIMEOUT):
    """
    Một lượt hỏi: contents = lịch sử + câu hỏi (user_turn). Yield các sự kiện tool / delta rồi done (xem đầu file).
    Quá giờ / vượt số bước → RuntimeError có thông báo dễ hiểu (router chat đã bắt RuntimeError).
    """
    state: dict = {}
    start = {"contents": contents, "rounds": 0, "tool_log": [], "answer": "", "llm_calls": 0, "remaining": None,
             "final": False}
    try:
        async with asyncio.timeout(timeout):
            async for mode, chunk in graph.astream(start, context=AgentContext(user_id=user_id),
                                                   config={"recursion_limit": RECURSION_LIMIT},
                                                   stream_mode=["custom", "values"]):
                if mode == "custom":
                    yield chunk
                else:
                    state = chunk
    except TimeoutError:
        raise RuntimeError(f"Trợ lý trả lời quá {timeout:.0f} s. Thử lại sau ít giây.") from None
    except GraphRecursionError:
        raise RuntimeError("Trợ lý gọi tool quá nhiều bước. Thử hỏi cụ thể hơn.") from None
    yield {"type": "done", "answer": state.get("answer", "").strip() or EMPTY_ANSWER,
           "tool_calls": state.get("tool_log", []), "rounds": state.get("rounds", 0),
           "llm_calls": state.get("llm_calls", 0), "remaining": state.get("remaining")}
