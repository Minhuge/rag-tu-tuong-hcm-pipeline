"""
Chuyển lịch sử chat cũ từ SQLite (chat.db) sang PostgreSQL (DATABASE_URL trong .env). Chạy một lần.

Giữ nguyên id → trang web đang mở cuộc trò chuyện cũ vẫn mở được. Postgres đã có dữ liệu thì dừng,
không ghi đè. chat.db không bị sửa hay xoá.

Chỉ dùng được với schema TRƯỚC khi có đăng nhập (đã chạy xong) — bảng conversations giờ cần user_id.

Chạy:  python -m scripts.migrate_sqlite              # đọc ./chat.db
       python -m scripts.migrate_sqlite đường/dẫn.db
"""
import os
import sys
from datetime import timezone

from sqlalchemy import create_engine, func, insert, select, text

from app import db


def utc(row: dict) -> dict:
    """SQLite lưu thời điểm UTC không kèm múi giờ → gắn UTC để Postgres không hiểu theo múi giờ của phiên."""
    return {k: v.replace(tzinfo=timezone.utc) if hasattr(v, "tzinfo") and v.tzinfo is None else v
            for k, v in row.items()}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chat.db")
    if not os.path.exists(path):
        sys.exit(f"Không thấy {path}")
    db.init_db()   # tạo bảng trong Postgres nếu chưa có

    convs_t, msgs_t = db.Conversation.__table__, db.Message.__table__
    src = create_engine(f"sqlite:///{path}")
    with src.connect() as s:
        convs = [utc(dict(r)) for r in s.execute(select(convs_t).order_by(convs_t.c.id)).mappings()]
        msgs = [utc(dict(r)) for r in s.execute(select(msgs_t).order_by(msgs_t.c.id)).mappings()]

    with db.engine.begin() as d:
        if d.scalar(select(func.count()).select_from(convs_t)):
            sys.exit("Postgres đã có cuộc trò chuyện → dừng để không ghi đè. Xoá bảng trước nếu muốn chuyển lại.")
        if convs:
            d.execute(insert(convs_t), convs)
        if msgs:
            d.execute(insert(msgs_t), msgs)
        # Đã chèn id có sẵn → đẩy bộ đếm id lên sau id lớn nhất, nếu không lần tạo mới tiếp theo sẽ trùng khoá.
        for table in ("conversations", "messages"):
            d.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                           f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"))

    print(f"Đã chuyển {len(convs)} cuộc trò chuyện, {len(msgs)} tin nhắn từ {path} sang Postgres.")


if __name__ == "__main__":
    main()
