"""
Fixture database dùng chung cho test_db.py và test_api.py.

Mặc định: file SQLite tạm → chạy được ở mọi máy, không cần Postgres.
Kiểm tra trên Postgres thật: đặt TEST_DATABASE_URL tới một database RIÊNG cho test
(mọi bảng trong đó bị xoá trước mỗi test), vd.

  docker exec postgres createdb -U hcm hcm_chat_test
  TEST_DATABASE_URL=postgresql+psycopg://hcm:mật_khẩu@localhost:5432/hcm_chat_test pytest -q test_db.py test_api.py test_auth.py
"""
import os

import pytest
from sqlalchemy import create_engine, event

import auth
import db


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
