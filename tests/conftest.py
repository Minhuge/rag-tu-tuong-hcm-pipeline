"""
Fixture dùng chung: database (test_db.py, test_api.py, …) và Gemini giả cho phần tạo đề (fake_gemini).

Mặc định: file SQLite tạm → chạy được ở mọi máy, không cần Postgres.
Kiểm tra trên Postgres thật: đặt TEST_DATABASE_URL tới một database RIÊNG cho test
(mọi bảng trong đó bị xoá trước mỗi test), vd.

  docker exec postgres createdb -U hcm hcm_chat_test
  TEST_DATABASE_URL=postgresql+psycopg://hcm:mật_khẩu@localhost:5432/hcm_chat_test pytest -q tests/test_db.py tests/test_api.py tests/test_auth.py
"""
import json
import os
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event

from app import auth
from app import db


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        if url == db.DATABASE_URL:
            pytest.exit("TEST_DATABASE_URL trùng DATABASE_URL — test sẽ xoá dữ liệu thật", returncode=1)
        engine = create_engine(url)
        db.Base.metadata.drop_all(engine)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'chat.db'}", connect_args={"check_same_thread": False})
        event.listen(engine, "connect", db._sqlite_pragmas)
    monkeypatch.setattr(db, "engine", engine)
    db.init_db()
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch):
    """Test không đọc JWT_SECRET thật trong .env."""
    monkeypatch.setattr(auth, "JWT_SECRET", "test-secret-" + "x" * 40)


class FakeGemini:
    """
    llm.aio.models.generate_content giả cho phần tạo đề (không gọi Gemini thật, không tốn quota):
      lời gọi tạo đề   → lần lượt lấy từ exam_texts
      lời gọi giám khảo → judge(contents) trả JSON; mặc định mọi câu đều đạt
    calls ghi lại (contents, config) của từng lần gọi.
    """
    CORRECT = "lựa chọn B"   # quy ước dữ liệu test: đáp án đúng có nội dung này (vị trí đổi sau khi xáo)

    def __init__(self, exam_texts, judge=None):
        self.exam_texts, self.judge, self.calls = list(exam_texts), judge, []
        self.aio = self.models = self

    @classmethod
    def all_pass(cls, contents: str) -> str:
        """Giám khảo giả "tự giải": chọn lựa chọn có nội dung CORRECT (prompt không ghi đáp án)."""
        blocks = re.split(r"^(?=Câu \d+ \()", contents.split("<cac_cau_hoi>")[-1], flags=re.MULTILINE)[1:]
        verdicts = []
        for i, block in enumerate(blocks, 1):
            m = re.search(rf"^([A-D])\. {cls.CORRECT}$", block, re.MULTILINE)
            verdicts.append({"index": i, "reason": "ok", "chosen_label": m.group(1) if m else None,
                             "supported": True, "other_correct_option": False, "duplicate_of": None})
        return json.dumps({"verdicts": verdicts})

    async def generate_content(self, model, contents, config):
        self.calls.append((contents, config))
        if getattr(config.response_schema, "__name__", "") == "JudgeResult":
            text = (self.judge or self.all_pass)(contents)
        else:
            text = self.exam_texts.pop(0)
        return SimpleNamespace(text=text, parsed=None, model_version="fake-model",
                               candidates=[SimpleNamespace(finish_reason="STOP")])


@pytest.fixture
def fake_gemini():
    return FakeGemini
