"""
Nâng database PostgreSQL tạo trước khi có đăng nhập. Chạy lại nhiều lần cũng không sao.

  1. tạo bảng refresh_tokens, thêm cột users.is_admin và conversations.user_id (khoá ngoại + index)
  2. gán mọi cuộc trò chuyện chưa có chủ cho tài khoản quản trị
  3. đặt conversations.user_id NOT NULL. Cột client_id cũ (mã trình duyệt) không còn dùng:
     bỏ NOT NULL, giữ nguyên dữ liệu. Muốn xoá hẳn: ALTER TABLE conversations DROP COLUMN client_id;

Lần đầu:
  python -m scripts.migrate_auth                            # bước 1, báo chưa có quản trị viên
  python -m scripts.create_user admin@example.com --admin
  python -m scripts.migrate_auth                            # bước 2–3
Có nhiều quản trị viên → chọn người nhận:  python -m scripts.migrate_auth --owner admin@example.com
"""
import argparse
import sys

from sqlalchemy import inspect, text

from app import db


def find_owner(conn, email: str | None) -> tuple[int, str] | None:
    if email:
        row = conn.execute(text("SELECT id, email FROM users WHERE email = :e"),
                           {"e": db.normalize_email(email)}).first()
        if row is None:
            sys.exit(f"Không có tài khoản {email}")
        return tuple(row)
    admins = conn.execute(text("SELECT id, email FROM users WHERE is_admin ORDER BY id")).all()
    if len(admins) > 1:
        sys.exit("Có nhiều quản trị viên → chọn một: python -m scripts.migrate_auth --owner <email>\n  "
                 + "\n  ".join(email for _, email in admins))
    return tuple(admins[0]) if admins else None


def main():
    ap = argparse.ArgumentParser(description="Thêm đăng nhập vào database cũ")
    ap.add_argument("--owner", help="email nhận các cuộc trò chuyện cũ (mặc định: quản trị viên duy nhất)")
    args = ap.parse_args()
    if db.engine is None or db.engine.dialect.name != "postgresql":
        sys.exit("Cần DATABASE_URL trỏ tới PostgreSQL trong .env")

    # Bước 1 — commit riêng, để create_user.py chạy được ngay cả khi chưa có quản trị viên.
    db.Base.metadata.create_all(db.engine)     # bảng refresh_tokens (bảng đã có thì bỏ qua)
    conv_cols = {c["name"] for c in inspect(db.engine).get_columns("conversations")}
    with db.engine.begin() as c:
        c.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false"))
        c.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
                       "user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_user_id ON conversations (user_id)"))
        if "client_id" in conv_cols:
            c.execute(text("ALTER TABLE conversations ALTER COLUMN client_id DROP NOT NULL"))
    print("Bước 1: đã có refresh_tokens, users.is_admin, conversations.user_id")

    # Bước 2–3
    with db.engine.begin() as c:
        orphans = c.scalar(text("SELECT count(*) FROM conversations WHERE user_id IS NULL"))
        if orphans:
            owner = find_owner(c, args.owner)
            if owner is None:
                sys.exit(f"Còn {orphans} cuộc trò chuyện chưa có chủ nhưng chưa có quản trị viên. Chạy:\n"
                         "  python -m scripts.create_user <email> --admin\n  python -m scripts.migrate_auth")
            c.execute(text("UPDATE conversations SET user_id = :u WHERE user_id IS NULL"), {"u": owner[0]})
            print(f"Bước 2: đã gán {orphans} cuộc trò chuyện cũ cho #{owner[0]} {owner[1]}")
        else:
            print("Bước 2: không còn cuộc trò chuyện nào chưa có chủ")
        c.execute(text("ALTER TABLE conversations ALTER COLUMN user_id SET NOT NULL"))
    print("Bước 3: conversations.user_id NOT NULL — xong, khởi động lại API.")


if __name__ == "__main__":
    main()
