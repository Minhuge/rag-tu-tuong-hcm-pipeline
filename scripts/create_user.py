"""
Tạo tài khoản từ dòng lệnh, vd. tài khoản quản trị đầu tiên hoặc khi đã tắt đăng ký (ALLOW_REGISTRATION=false).
Mật khẩu nhập ẩn hai lần → không nằm trong lịch sử shell.

Chạy:  python -m scripts.create_user admin@example.com --admin --name "Quản trị"
       python -m scripts.create_user ban@example.com
"""
import argparse
import getpass
import sys

from app import auth
from app import db


def main():
    ap = argparse.ArgumentParser(description="Tạo tài khoản đăng nhập")
    ap.add_argument("email")
    ap.add_argument("--name", help="tên hiển thị")
    ap.add_argument("--admin", action="store_true", help="tài khoản quản trị")
    args = ap.parse_args()

    try:
        db.init_db()
    except RuntimeError as e:      # thiếu DATABASE_URL / chưa chạy migrate_auth.py
        sys.exit(str(e))
    password = getpass.getpass("Mật khẩu: ")
    if password != getpass.getpass("Nhập lại mật khẩu: "):
        sys.exit("Hai lần nhập không khớp")
    try:
        user = auth.register(args.email, password, args.name, is_admin=args.admin)
    except ValueError as e:
        sys.exit(str(e))
    print(f"Đã tạo {'quản trị viên' if user['is_admin'] else 'người dùng'} #{user['id']} {user['email']}")


if __name__ == "__main__":
    main()
