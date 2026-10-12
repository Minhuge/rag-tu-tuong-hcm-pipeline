"""
Fixture dùng chung: database (test_db.py, test_api.py, …), Gemini giả cho phần tạo đề (fake_gemini)
và Qdrant trong bộ nhớ có sẵn vài chương giáo trình (qdrant).

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
from qdrant_client import QdrantClient, models
from sqlalchemy import create_engine, event

from app import auth
from app import db
from rag.search_rerank import COLLECTION


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


def body(i: int, n: int = 300) -> str:
    return f"Nội dung đoạn {i:02d} " + "x" * n


@pytest.fixture
def qdrant():
    """Chương III: 20 đoạn trang 38–47 (point chèn lộn xộn); Chương IV: 1 đoạn; Chương V: chỉ dòng tiêu đề."""
    client = QdrantClient(":memory:")
    client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    rows = [{"chunk_id": f"chunk_{100 + i:03d}", "chapter": "Chương III", "page": 38 + i // 2, "text": body(i)}
            for i in range(20)]
    rows[0]["text"] = "phần cuối chương trước. CHƯƠNG III TƯ TƯỞNG HỒ CHÍ MINH VỀ CHỦ NGHĨA XÃ HỘI " + "y" * 200
    rows[1]["text"] = ". " + body(1)
    rows += [{"chunk_id": "chunk_200", "chapter": "Chương IV", "page": 52, "text": body(0)},
             {"chunk_id": "chunk_300", "chapter": "Chương V", "page": 67,
              "text": "hết chương IV. CHƯƠNG V TƯ TƯỞNG HỒ CHÍ MINH VỀ ĐẠI ĐOÀN KẾT"}]
    order = list(range(len(rows)))[::-1]   # chèn ngược → kết quả phải tự sắp lại theo trang
    client.upsert(COLLECTION, [models.PointStruct(id=j, vector=[1.0, 0.0], payload=rows[i])
                               for j, i in enumerate(order)])
    return client
