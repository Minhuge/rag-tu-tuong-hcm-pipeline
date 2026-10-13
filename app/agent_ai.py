"""
Trợ lý bài kiểm tra phía ứng dụng (Phase 5): giới hạn lượt gọi Gemini (5.5) và nối vào endpoint chat (5.6).

  agent_stream()  một lượt hỏi của cuộc trò chuyện mode="exam", cùng dạng sự kiện với GuardedRAG.ask_stream:
                  status guard → (bị chặn: done) → status agent → tool / delta … → done {"result": …}
    1. lớp 1a + 1b như chế độ hỏi giáo trình (GuardedRAG.screen) — TRƯỚC khi agent thấy câu hỏi
    2. lịch sử (HISTORY_TURNS lượt gần nhất, db._history) → các lượt user / model của Gemini, mỗi tin tối đa
       AGENT_HISTORY_CHARS ký tự. Chỉ có chữ, không có kết quả tool cũ → câu nối tiếp thì agent gọi lại tool.
    3. agent/graph.py với 4 tool, prompt có ngày hôm nay, quota mỗi lần gọi Gemini
    result (lưu vào messages.meta, trừ answer): mode, tool_calls, rounds, llm_calls, remaining, guard, input_policy,
    timings — không lưu kết quả tool (có thể dài, và đã có sẵn trong database).

Giới hạn mỗi tin nhắn (agent/graph.py, 5.1): tối đa AGENT_MAX_TOOL_ROUNDS lượt gọi tool, cả lượt ≤ AGENT_TIMEOUT giây.
Giới hạn mỗi ngày (file này): mỗi lần agent gọi Gemini trừ 1 lượt — một câu hỏi thường tốn 2–3 lượt (đọc danh sách,
mở bài, trả lời). Đếm bằng bảng ai_calls theo ngày giờ Việt Nam, như quota tạo đề (2.9) và chấm tự luận (3.9).

  charge_agent_call()  giữ lượt TRƯỚC khi gọi: hết lượt → QuotaExceeded (429). Trả số lượt còn lại sau lần này;
                       về 0 giữa chừng → agent/graph.py cho lần gọi đó là lần cuối (tắt tool, trả lời bằng dữ liệu đã
                       có) thay vì báo lỗi khi đã trả lời được nửa chừng.
  agent_quota()        cho UI: còn bao nhiêu lượt hôm nay.
Thử lại do quá tải trong stream_gemini (đổi model dự phòng) không trừ thêm: một lần gọi của agent = một lượt.
"""
import os
import time
import weakref

from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent.graph import build_agent, run_agent, user_turn
from agent.prompts import system_prompt
from agent.tools import build_tools
from app import db, exams
from app.db import AiCall, User

AGENT_CALLS_PER_USER_PER_DAY = int(os.getenv("AGENT_CALLS_PER_USER_PER_DAY", "100"))
# Tin cũ dài hơn thì cắt: đủ giữ câu hỏi lại của trợ lý ("bạn hỏi đề nào? 1) … 2) …") để hiểu "cái thứ hai".
AGENT_HISTORY_CHARS = int(os.getenv("AGENT_HISTORY_CHARS", "1000"))


def quota_message(limit: int | None = None) -> str:
    limit = AGENT_CALLS_PER_USER_PER_DAY if limit is None else limit
    return (f"Bạn đã dùng hết {limit} lượt Trợ lý bài kiểm tra hôm nay. Lượt mới mở lại lúc 0 giờ (giờ Việt Nam); "
            "trong lúc chờ vẫn xem được kết quả ở trang Bài kiểm tra.")


def _used_today(s: Session, user_id: int) -> int:
    return s.scalar(select(func.count()).select_from(AiCall).where(
        AiCall.user_id == user_id, AiCall.created_at >= exams._start_of_today_vn()))


def agent_quota(user_id: int) -> dict:
    limit = AGENT_CALLS_PER_USER_PER_DAY
    with Session(db.engine) as s:
        used = _used_today(s, user_id)
    return {"limit": limit, "used": used, "remaining": max(limit - used, 0)}


def charge_agent_call(user_id: int) -> int:
    """
    Trừ một lượt rồi trả số lượt còn lại. Đếm và ghi trong CÙNG transaction, khoá dòng users của người này
    (Postgres) → hai tab gửi cùng lúc không cùng thấy "còn 1 lượt" rồi cùng gọi Gemini.
    """
    limit = AGENT_CALLS_PER_USER_PER_DAY
    with Session(db.engine) as s, s.begin():
        s.get(User, user_id, with_for_update=True)
        used = _used_today(s, user_id)
        if used >= limit:
            raise exams.QuotaExceeded(quota_message(limit))
        s.add(AiCall(user_id=user_id, created_at=exams._now()))
    return limit - used - 1


# ====================== nối vào chat (5.6) ======================
_graphs: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()   # GuardedRAG → graph (dựng một lần)


def get_agent(rag):
    """Graph dùng chung cho mọi người (user_id đi theo từng lượt chạy). search_textbook cần rag đã nạp model."""
    graph = _graphs.get(rag)
    if graph is None:
        graph = _graphs[rag] = build_agent(rag.llm, build_tools(rag), system_prompt, charge=charge_agent_call)
    return graph


def history_contents(history: list[dict]) -> list[types.Content]:
    """[{role: user | assistant, content}] (db._history) → các lượt hội thoại của Gemini."""
    out = []
    for m in history:
        text = " ".join(str(m["content"]).split())
        if len(text) > AGENT_HISTORY_CHARS:
            text = text[:AGENT_HISTORY_CHARS - 1] + "…"
        out.append(types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part(text=text)]))
    return out


async def agent_stream(rag, user_id: int, question: str, history: list[dict], *, graph=None):
    """Một lượt hỏi của Trợ lý bài kiểm tra; graph: test truyền graph dùng Gemini giả."""
    t0 = time.perf_counter()
    yield {"type": "status", "step": "guard"}
    screen = await rag.screen(question)
    guard_s = round(time.perf_counter() - t0, 3)
    if screen["blocked_by"]:
        yield {"type": "done", "result": {**screen, "mode": "exam",
                                          "timings": {"guard_s": guard_s, "total_s": guard_s}}}
        return

    yield {"type": "status", "step": "agent"}
    contents = [*history_contents(history), user_turn(question)]
    async for ev in run_agent(graph or get_agent(rag), contents, user_id):
        if ev["type"] != "done":
            yield ev
            continue
        yield {"type": "done", "result": {
            "answer": ev["answer"], "mode": "exam", "blocked_by": None,
            "guard": screen["guard"], "input_policy": screen["input_policy"],
            "tool_calls": ev["tool_calls"], "rounds": ev["rounds"], "llm_calls": ev["llm_calls"],
            "remaining": ev["remaining"],
            "timings": {"guard_s": guard_s, "total_s": round(time.perf_counter() - t0, 3)},
        }}
