"""
Test giới hạn lượt Trợ lý bài kiểm tra (roadmap 5.5, app/agent_ai.py) trên database tạm, đồng hồ giả.

Chạy:  pytest -q tests/test_agent_quota.py
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from google.genai import types
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.graph import AgentTool, build_agent, run_agent, user_turn
from app import agent_ai, db, exams

A, B = 1, 2
# 23:58 ngày 13/10 giờ Việt Nam
T0 = datetime(2026, 10, 13, 23, 58, tzinfo=exams.VN_TZ).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def users(db_engine):
    with Session(db_engine) as s, s.begin():
        s.add_all([db.User(id=u, email=f"{n}@example.com", password_hash="x") for u, n in ((A, "a"), (B, "b"))])


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = T0

        def advance(self, **kw):
            self.now = self.now + timedelta(**kw)
    c = Clock()
    monkeypatch.setattr(exams, "_now", lambda: c.now)
    return c


@pytest.fixture
def limit3(monkeypatch):
    monkeypatch.setattr(agent_ai, "AGENT_CALLS_PER_USER_PER_DAY", 3)


def test_charge_counts_down_then_refuses(clock, limit3):
    assert [agent_ai.charge_agent_call(A) for _ in range(3)] == [2, 1, 0]
    with pytest.raises(exams.QuotaExceeded, match="hết 3 lượt Trợ lý bài kiểm tra hôm nay"):
        agent_ai.charge_agent_call(A)
    assert agent_ai.agent_quota(A) == {"limit": 3, "used": 3, "remaining": 0}
    assert agent_ai.agent_quota(B) == {"limit": 3, "used": 0, "remaining": 3}     # mỗi người một quota
    assert agent_ai.charge_agent_call(B) == 2


def test_quota_resets_at_midnight_vietnam_time(clock, limit3):
    for _ in range(3):
        agent_ai.charge_agent_call(A)
    clock.advance(minutes=1)                       # 23:59 — vẫn là hôm nay
    with pytest.raises(exams.QuotaExceeded):
        agent_ai.charge_agent_call(A)
    clock.advance(minutes=2)                       # 00:01 ngày 14/10
    assert agent_ai.charge_agent_call(A) == 2


def test_refused_call_is_not_recorded(clock, limit3):
    for _ in range(3):
        agent_ai.charge_agent_call(A)
    for _ in range(5):
        with pytest.raises(exams.QuotaExceeded):
            agent_ai.charge_agent_call(A)
    with Session(db.engine) as s:
        assert len(s.scalars(select(db.AiCall)).all()) == 3


def test_agent_stops_gracefully_when_quota_runs_out_mid_question(clock, limit3):
    """Còn 3 lượt, câu hỏi muốn gọi tool mãi: lần 3 là lần cuối (tắt tool) → vẫn có câu trả lời; câu sau → 429."""
    seen = []

    async def fake(llm, contents, config):
        seen.append(config)
        last = config.tool_config is not None
        yield types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[
            types.Part(text="Trả lời bằng dữ liệu đã có.") if last else
            types.Part(function_call=types.FunctionCall(name="noop", args={}))]))])

    noop = AgentTool("noop", "Không làm gì.", {"type": "object", "properties": {}}, lambda user_id: "ok")
    graph = build_agent(None, [noop], "system", stream=fake, charge=agent_ai.charge_agent_call)

    async def ask():
        return [ev async for ev in run_agent(graph, [user_turn("?")], A)]
    done = asyncio.run(ask())[-1]
    assert (done["answer"], done["llm_calls"], done["remaining"], done["rounds"]) == ("Trả lời bằng dữ liệu đã có.", 3, 0, 2)
    with pytest.raises(exams.QuotaExceeded):
        asyncio.run(ask())
    assert len(seen) == 3
