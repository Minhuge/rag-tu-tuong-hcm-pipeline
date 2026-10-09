"""
Phase 0 (ROADMAP.md): thêm cột mới vào các bảng ĐÃ CÓ trong database PostgreSQL. Chạy lại nhiều lần cũng không sao.

  conversations.mode               'docs' / 'exam'  — NOT NULL, mặc định 'docs'
                                     → mọi cuộc trò chuyện cũ (tạo trước khi có chế độ bài kiểm tra) thành 'docs'
  exams.status                     'draft' / 'published' — mặc định 'draft'
  exams.origin                     'manual' / 'ai'       — mặc định 'manual'
  exams.max_attempts               số nguyên > 0, NULL = không giới hạn
  questions.explanation            text
  questions.source_page            số trang > 0
  submission_answers.graded_by     'auto' / 'ai' / 'creator', NULL = chưa chấm

Vì sao cần script: Base.metadata.create_all (chạy lúc khởi động API) chỉ TẠO bảng còn thiếu, không bao giờ
sửa bảng đã có. Database tạo trước Phase 0 đã có các bảng này → phải ALTER TABLE.

Tất cả chạy trong MỘT transaction (Postgres cho phép DDL trong transaction): lỗi giữa chừng → không đổi gì.

Chạy:  python -m scripts.migrate_phase0
"""
import sys

from sqlalchemy import inspect, text

from app import db

# (bảng, cột, kiểu + mặc định) — khớp với model trong app/db.py
COLUMNS = [
    ("conversations", "mode", "VARCHAR(16) NOT NULL DEFAULT 'docs'"),
    ("exams", "status", "VARCHAR(16) NOT NULL DEFAULT 'draft'"),
    ("exams", "origin", "VARCHAR(16) NOT NULL DEFAULT 'manual'"),
    ("exams", "max_attempts", "INTEGER"),
    ("questions", "explanation", "TEXT"),
    ("questions", "source_page", "INTEGER"),
    ("submission_answers", "graded_by", "VARCHAR(16)"),
]

# (bảng, tên ràng buộc, điều kiện) — cùng tên với CheckConstraint trong app/db.py
CHECKS = [
    ("conversations", "ck_conversations_mode", db._one_of("mode", db.CONVERSATION_MODES)),
    ("exams", "ck_exams_status", db._one_of("status", db.EXAM_STATUS)),
    ("exams", "ck_exams_origin", db._one_of("origin", db.EXAM_ORIGIN)),
    ("exams", "ck_exams_max_attempts", "max_attempts IS NULL OR max_attempts > 0"),
    ("questions", "ck_questions_source_page", "source_page IS NULL OR source_page > 0"),
    ("submission_answers", "ck_submission_answers_graded_by", db._one_of("graded_by", db.GRADED_BY)),
]


def main():
    if db.engine is None or db.engine.dialect.name != "postgresql":
        sys.exit("Cần DATABASE_URL trỏ tới PostgreSQL trong .env")
    db.Base.metadata.create_all(db.engine)          # bảng còn thiếu (database mới) → tạo luôn, đã đủ cột

    before = {t: {c["name"] for c in inspect(db.engine).get_columns(t)} for t, _, _ in COLUMNS}
    with db.engine.begin() as c:
        for table, column, ddl in COLUMNS:
            # ADD COLUMN ... DEFAULT: Postgres điền giá trị mặc định cho mọi dòng đang có
            c.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
            print(f"  {table}.{column}: {'đã có' if column in before[table] else 'đã thêm'}")
        existing = set(c.execute(text("SELECT conname FROM pg_constraint")).scalars())
        for table, name, condition in CHECKS:
            if name not in existing:                # Postgres không có ADD CONSTRAINT IF NOT EXISTS
                c.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition})"))
                print(f"  ràng buộc {name}: đã thêm")

    with db.engine.connect() as c:
        modes = c.execute(text("SELECT mode, count(*) FROM conversations GROUP BY mode ORDER BY mode")).all()
    print("Cuộc trò chuyện theo mode:", ", ".join(f"{m}={n}" for m, n in modes) or "chưa có")
    db.init_db()                                    # kiểm tra lại: thiếu cột nào sẽ báo lỗi ở đây
    print("Xong — khởi động lại API.")


if __name__ == "__main__":
    main()
