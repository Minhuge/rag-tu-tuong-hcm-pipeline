"""
Test prompt hệ thống của Trợ lý bài kiểm tra (roadmap 5.4, agent/prompts.py). Hành vi thật của model với prompt này:
python -m scripts.agent_proto (gọi Gemini thật).

Chạy:  pytest -q tests/test_agent_prompts.py
"""
import re
from datetime import datetime, timezone

from agent import tools
from agent.prompts import system_prompt
from app.exams import VN_TZ


def test_today_and_yesterday_in_vietnam_time():
    p = system_prompt(datetime(2026, 10, 13, 9, 0, tzinfo=VN_TZ))
    assert "Hôm nay là Thứ Ba, 13/10/2026 (giờ Việt Nam). Hôm qua là 12/10/2026." in p
    # 20:00 UTC ngày 12 = 03:00 sáng ngày 13 ở Việt Nam
    assert "Hôm nay là Thứ Ba, 13/10/2026" in system_prompt(datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc))
    assert "Chủ nhật, 01/01/2023" in system_prompt(datetime(2023, 1, 1, 8, 0, tzinfo=VN_TZ))
    assert "Hôm qua là 31/12/2022" in system_prompt(datetime(2023, 1, 1, 8, 0, tzinfo=VN_TZ))


def test_prompt_names_only_tools_that_exist():
    named = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", system_prompt()))
    real = {t.name for t in tools.build_tools(rag=None)}
    assert named - {"submission_id", "exam_id", "question_number"} == real


def test_prompt_is_small():
    """Prompt gửi lại ở mọi lần gọi model trong lượt (tối đa 6): giữ gọn. 5.8 thêm nhóm TẠO ĐỀ → ~5,2 KB."""
    assert len(system_prompt().encode()) < 5600
