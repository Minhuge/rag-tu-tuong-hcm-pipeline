"""
Test auth.py và các route /auth/* trên database tạm (xem conftest.py) — không nạp model, không gọi Gemini.

Chạy:  pytest -q tests/test_auth.py
"""
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import main as api
from app import auth
from app import db


@pytest.fixture(autouse=True)
def temp_db(db_engine):
    return db_engine


CSRF = {"X-Requested-With": "fetch"}     # web (api.js) luôn gửi header này


def browser(cookies=None):
    """Giống trình duyệt chạy web: tự giữ / gửi cookie, gửi header chống CSRF."""
    return TestClient(api.app, headers=CSRF, cookies=cookies)   # không "with" → không nạp model thật


@pytest.fixture
def client():
    return browser()


def set_cookies(res):
    """{tên cookie: chuỗi Set-Cookie (chữ thường)}"""
    return {h.split("=", 1)[0]: h.lower() for h in res.headers.get_list("set-cookie")}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def active_refresh_tokens(user_id):
    """Số token còn dùng được (chưa bị thay, chưa bị xoá khi đăng xuất)."""
    with Session(db.engine) as s:
        return s.scalar(select(func.count()).select_from(db.RefreshToken)
                        .where(db.RefreshToken.user_id == user_id, db.RefreshToken.revoked_at.is_(None)))


# ---------- tài khoản ----------
def test_register_hashes_password_and_normalizes_email():
    user = auth.register("  Hieu@Example.COM ", "matkhau123", " Hiếu ")
    assert user["email"] == "hieu@example.com" and user["display_name"] == "Hiếu"
    assert user["is_admin"] is False and "password_hash" not in user
    with Session(db.engine) as s:
        stored = s.get(db.User, user["id"]).password_hash
    assert stored.startswith("$argon2id$") and "matkhau123" not in stored


def test_duplicate_email_rejected_case_insensitive():
    auth.register("a@example.com", "matkhau123")
    with pytest.raises(ValueError, match="đã được đăng ký"):
        auth.register("A@Example.com", "khac12345")


@pytest.mark.parametrize("email, password", [("khong-co-a-cong", "matkhau123"), ("a@b", "matkhau123"),
                                             ("a b@example.com", "matkhau123"),
                                             ("a@example.com", "ngan"), ("a@example.com", "x" * 129)])
def test_register_validates_input(email, password):
    with pytest.raises(ValueError):
        auth.register(email, password)


def test_authenticate():
    user = auth.register("a@example.com", "matkhau123")
    assert auth.authenticate("A@example.com", "matkhau123")["id"] == user["id"]
    assert auth.authenticate("a@example.com", "sai-mat-khau") is None
    assert auth.authenticate("khong-ton-tai@example.com", "matkhau123") is None
    with Session(db.engine) as s:
        assert s.get(db.User, user["id"]).last_login_at is not None


def test_inactive_user_cannot_log_in():
    user = auth.register("a@example.com", "matkhau123")
    with Session(db.engine) as s, s.begin():
        s.get(db.User, user["id"]).is_active = False
    assert auth.authenticate("a@example.com", "matkhau123") is None


# ---------- access token ----------
def test_access_token_round_trip():
    assert auth.decode_access_token(auth.create_access_token(42)) == 42


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(auth, "ACCESS_TOKEN_MINUTES", -1)
    with pytest.raises(jwt.ExpiredSignatureError):
        auth.decode_access_token(auth.create_access_token(1))


@pytest.mark.parametrize("token", [
    jwt.encode({"sub": "1", "type": "access", "iat": 0, "exp": 2**31}, "khoa-khac-" + "y" * 40, algorithm="HS256"),
    jwt.encode({"sub": "1", "type": "access", "iat": 0, "exp": 2**31}, None, algorithm="none"),
    "khong-phai-jwt",
])
def test_forged_tokens_rejected(token):
    with pytest.raises(jwt.InvalidTokenError):
        auth.decode_access_token(token)


def test_token_without_access_type_rejected():
    token = jwt.encode({"sub": "1", "iat": 0, "exp": 2**31}, auth.JWT_SECRET, algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        auth.decode_access_token(token)


def test_check_config(monkeypatch):
    auth.check_config()
    monkeypatch.setattr(auth, "JWT_SECRET", "ngan-qua")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.check_config()


# ---------- route /auth ----------
def test_register_logs_in_and_sets_httponly_cookies(client):
    res = client.post("/auth/register", json={"email": "a@example.com", "password": "matkhau123",
                                              "display_name": "A"})
    assert res.status_code == 201
    body = res.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == auth.ACCESS_TOKEN_MINUTES * 60
    cookies = set_cookies(res)
    for name in ("access_token", "refresh_token"):
        assert "httponly" in cookies[name] and "samesite=strict" in cookies[name]
    assert "path=/;" in cookies["access_token"] and f"max-age={auth.ACCESS_TOKEN_MINUTES * 60}" in cookies["access_token"]
    assert "path=/auth" in cookies["refresh_token"]
    me = client.get("/auth/me")                                    # chỉ cookie, không header Authorization
    assert me.status_code == 200 and me.json()["email"] == "a@example.com"
    # token trong JSON (cho /docs, curl) dùng được bằng header Bearer
    assert TestClient(api.app).get("/auth/me", headers=bearer(body["access_token"])).status_code == 200


def test_register_errors(client, monkeypatch):
    client.post("/auth/register", json={"email": "a@example.com", "password": "matkhau123"})
    res = client.post("/auth/register", json={"email": "a@example.com", "password": "matkhau123"})
    assert res.status_code == 400 and "đã được đăng ký" in res.json()["detail"]
    monkeypatch.setattr(auth, "ALLOW_REGISTRATION", False)
    assert client.post("/auth/register", json={"email": "b@example.com", "password": "matkhau123"}).status_code == 403


def test_login(client):
    auth.register("a@example.com", "matkhau123")
    ok = client.post("/auth/login", json={"email": "a@example.com", "password": "matkhau123"})
    assert ok.status_code == 200 and client.get("/auth/me").status_code == 200
    wrong = client.post("/auth/login", json={"email": "a@example.com", "password": "sai-mat-khau"})
    unknown = client.post("/auth/login", json={"email": "x@example.com", "password": "matkhau123"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()          # không lộ email nào đã đăng ký


def test_refresh_rotates_token(client):
    auth.register("a@example.com", "matkhau123")
    client.post("/auth/login", json={"email": "a@example.com", "password": "matkhau123"})
    old = client.cookies["refresh_token"]
    res = client.post("/auth/refresh")
    assert res.status_code == 200 and res.json()["user"]["email"] == "a@example.com"
    assert "access_token" not in res.json()                       # mã độc gọi /auth/refresh cũng không đọc được token
    assert set(set_cookies(res)) == {"access_token", "refresh_token"}
    assert client.cookies["refresh_token"] != old
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/refresh").status_code == 200       # token mới dùng tiếp được


def test_reused_refresh_token_revokes_all_sessions(client, monkeypatch):
    monkeypatch.setattr(auth, "REUSE_GRACE_SECONDS", 0)
    user = auth.register("a@example.com", "matkhau123")
    client.post("/auth/login", json={"email": "a@example.com", "password": "matkhau123"})
    stolen = client.cookies["refresh_token"]
    client.post("/auth/refresh")                                   # chủ tài khoản đổi token
    assert active_refresh_tokens(user["id"]) == 1

    attacker = browser({"refresh_token": stolen})
    res = attacker.post("/auth/refresh")
    assert res.status_code == 401
    assert active_refresh_tokens(user["id"]) == 0                  # phiên của chủ tài khoản cũng bị thu hồi
    assert client.post("/auth/refresh").status_code == 401


def test_concurrent_refresh_within_grace_is_allowed(client):
    """Hai tab cùng làm mới bằng một token → cả hai được token mới, không ai bị đăng xuất."""
    user = auth.register("a@example.com", "matkhau123")
    client.post("/auth/login", json={"email": "a@example.com", "password": "matkhau123"})
    shared = client.cookies["refresh_token"]
    tab_a = browser({"refresh_token": shared})
    tab_b = browser({"refresh_token": shared})
    assert tab_a.post("/auth/refresh").status_code == 200
    assert tab_b.post("/auth/refresh").status_code == 200
    assert active_refresh_tokens(user["id"]) == 2


def test_refresh_without_or_with_bad_cookie(client):
    assert client.post("/auth/refresh").status_code == 401
    bad = browser({"refresh_token": "token-bia-ra"})
    res = bad.post("/auth/refresh")
    assert res.status_code == 401 and set(set_cookies(res)) == {"access_token", "refresh_token"}   # xoá cookie hỏng


def test_expired_refresh_token_rejected(client, monkeypatch):
    auth.register("a@example.com", "matkhau123")
    client.post("/auth/login", json={"email": "a@example.com", "password": "matkhau123"})
    with Session(db.engine) as s, s.begin():
        row = s.scalar(select(db.RefreshToken))
        row.expires_at = db.utcnow() - timedelta(seconds=1)
    assert client.post("/auth/refresh").status_code == 401


def test_logout_revokes_refresh_token(client):
    user = auth.register("a@example.com", "matkhau123")
    client.post("/auth/login", json={"email": "a@example.com", "password": "matkhau123"})
    token = client.cookies["refresh_token"]
    res = client.post("/auth/logout")
    assert res.status_code == 204 and set(set_cookies(res)) == {"access_token", "refresh_token"}   # xoá cả hai
    assert client.get("/auth/me").status_code == 401
    assert active_refresh_tokens(user["id"]) == 0
    assert browser({"refresh_token": token}).post("/auth/refresh").status_code == 401


def test_protected_route_rejects_bad_tokens(client, monkeypatch):
    user = auth.register("a@example.com", "matkhau123")
    assert client.get("/auth/me").json()["detail"] == "Chưa đăng nhập"
    assert client.get("/auth/me", headers=bearer("rac")).json()["detail"] == "Token không hợp lệ"

    monkeypatch.setattr(auth, "ACCESS_TOKEN_MINUTES", -1)
    expired = auth.create_access_token(user["id"])
    assert client.get("/auth/me", headers=bearer(expired)).json()["detail"] == "Phiên đăng nhập đã hết hạn"


def test_deactivated_user_token_stops_working(client):
    user = auth.register("a@example.com", "matkhau123")
    token = auth.create_access_token(user["id"])
    assert client.get("/conversations", headers=bearer(token)).status_code == 200
    with Session(db.engine) as s, s.begin():
        s.get(db.User, user["id"]).is_active = False
    assert client.get("/conversations", headers=bearer(token)).status_code == 401


def test_expired_access_cookie_rejected(monkeypatch):
    user = auth.register("a@example.com", "matkhau123")
    monkeypatch.setattr(auth, "ACCESS_TOKEN_MINUTES", -1)
    res = browser({"access_token": auth.create_access_token(user["id"])}).get("/auth/me")
    assert res.status_code == 401 and res.json()["detail"] == "Phiên đăng nhập đã hết hạn"


# ---------- chống CSRF ----------
def test_cookie_post_without_csrf_header_rejected(client):
    """Trang web khác gây ra request (form, fetch không header) → có cookie nhưng thiếu header → 403."""
    auth.register("a@example.com", "matkhau123")
    client.post("/auth/login", json={"email": "a@example.com", "password": "matkhau123"})
    no_header = TestClient(api.app, cookies=dict(client.cookies))
    assert no_header.get("/conversations").status_code == 200                # GET chỉ đọc → không cần header
    assert no_header.delete("/conversations/999").status_code == 403
    assert no_header.post("/auth/refresh").status_code == 403
    assert no_header.post("/auth/logout").status_code == 403
    assert client.delete("/conversations/999").status_code == 404           # có header → qua, chỉ là không có mục 999


def test_bearer_header_needs_no_csrf_header():
    """Header Authorization không bị trình duyệt tự gửi → không cần header chống CSRF (/docs, curl)."""
    user = auth.register("a@example.com", "matkhau123")
    res = TestClient(api.app).delete("/conversations/999", headers=bearer(auth.create_access_token(user["id"])))
    assert res.status_code == 404
