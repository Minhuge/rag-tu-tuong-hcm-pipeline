"""
Đăng nhập: băm mật khẩu, JWT, refresh token và dependency get_current_user cho các router trong app/routers/.

  access token  : JWT ký HS256 bằng JWT_SECRET, sống ACCESS_TOKEN_MINUTES phút, nằm trong cookie httpOnly
                  "access_token" (Path=/) → trình duyệt tự gửi kèm mọi request, JavaScript không đọc được.
                  Máy chủ không lưu → chỉ kiểm tra chữ ký + hạn, rồi đọc user để chắc tài khoản còn hoạt động.
                  /docs, curl, app khác: gửi header  Authorization: Bearer <token>  (lấy từ /auth/login).
  refresh token : chuỗi ngẫu nhiên, sống REFRESH_TOKEN_DAYS ngày, cookie httpOnly "refresh_token" chỉ gửi tới /auth
                  (JavaScript không đọc được → lỗi XSS không lấy được). Database chỉ lưu SHA-256 của nó.
                  Mỗi lần /auth/refresh: token cũ bị thu hồi, cấp token mới (rotation). Token đã thu hồi
                  mà bị dùng lại → có thể đã bị lộ → thu hồi mọi phiên của người đó, bắt đăng nhập lại.
                  Ngoại lệ: dùng lại trong REUSE_GRACE_SECONDS giây sau khi đổi (hai tab cùng làm mới
                  một lúc, mạng chập chờn mất phản hồi) → vẫn cấp token mới, không coi là bị lộ.
                  Vì vậy revoked_at chỉ dùng cho token bị thay khi làm mới; đăng xuất / thu hồi hết thì
                  XOÁ dòng → token đó hết hiệu lực ngay, không có thời gian ân hạn.
  chống CSRF    : cookie được trình duyệt tự gửi, kể cả khi trang web khác gây ra request. Hai lớp chặn:
                  1. SameSite=Strict → request xuất phát từ trang khác không mang cookie;
                  2. POST/DELETE đăng nhập bằng cookie phải có header X-Requested-With — trang khác không tự
                     thêm header lạ được (trình duyệt hỏi CORS trước, mà CORS chỉ cho phép web của mình).

.env:  JWT_SECRET=...            bắt buộc, ≥ 32 ký tự:  python -c "import secrets; print(secrets.token_urlsafe(64))"
       ACCESS_TOKEN_MINUTES=15
       REFRESH_TOKEN_DAYS=30
       ALLOW_REGISTRATION=true   false → chỉ tạo tài khoản bằng create_user.py
       COOKIE_SECURE=false       true khi chạy sau HTTPS (cookie chỉ gửi qua https)
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import db
from app.db import RefreshToken, User, normalize_email, user_dict, utcnow

load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "15"))
REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "30"))
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "true").lower() == "true"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_HEADER = "X-Requested-With"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
REUSE_GRACE_SECONDS = 30
MIN_PASSWORD_CHARS = 8
MAX_PASSWORD_CHARS = 128   # Argon2 nhận chuỗi dài bao nhiêu cũng được → giới hạn để không bị gửi mật khẩu 1 MB

# Argon2id — băm chậm có chủ đích, mỗi mật khẩu một salt riêng → lộ database cũng khó dò ngược.
password_hasher = PasswordHash.recommended()
# Email không tồn tại vẫn kiểm tra với một hash giả → thời gian phản hồi như nhau, không dò được email nào đã đăng ký.
_DUMMY_HASH = password_hasher.hash("dummy-password")


def check_config():
    """Gọi lúc khởi động API → thiếu JWT_SECRET thì dừng ngay, không đợi tới lần đăng nhập đầu."""
    if not JWT_SECRET or len(JWT_SECRET) < 32:
        raise RuntimeError('Đặt JWT_SECRET (≥ 32 ký tự) trong .env: '
                           'python -c "import secrets; print(secrets.token_urlsafe(64))"')


def _aware(dt: datetime) -> datetime:
    """Postgres trả datetime có múi giờ; SQLite (test) thì không — mọi thời điểm đều được ghi theo UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- tài khoản ----------
def register(email: str, password: str, display_name: str | None = None, is_admin: bool = False) -> dict:
    """Tạo tài khoản. Email sai dạng, mật khẩu không hợp lệ hoặc email đã có → ValueError."""
    email = normalize_email(email)
    local, _, domain = email.partition("@")
    if not local or "." not in domain or " " in email:
        raise ValueError("Email không hợp lệ")
    if not MIN_PASSWORD_CHARS <= len(password) <= MAX_PASSWORD_CHARS:
        raise ValueError(f"Mật khẩu cần từ {MIN_PASSWORD_CHARS} đến {MAX_PASSWORD_CHARS} ký tự")
    name = (display_name or "").strip() or None
    try:
        with Session(db.engine) as s, s.begin():
            user = User(email=email, password_hash=password_hasher.hash(password), display_name=name,
                        is_admin=is_admin)
            s.add(user)
            s.flush()
            return user_dict(user)
    except IntegrityError:      # email unique → hai người đăng ký trùng cùng lúc cũng chỉ một người thành công
        raise ValueError("Email đã được đăng ký") from None


def authenticate(email: str, password: str) -> dict | None:
    """Đúng email + mật khẩu và tài khoản còn hoạt động → thông tin user, sai → None."""
    with Session(db.engine) as s, s.begin():
        user = s.scalar(select(User).where(User.email == normalize_email(email)))
        valid, new_hash = password_hasher.verify_and_update(password, user.password_hash if user else _DUMMY_HASH)
        if user is None or not valid or not user.is_active:
            return None
        if new_hash:            # thông số băm đã được nâng → lưu lại hash mới
            user.password_hash = new_hash
        user.last_login_at = utcnow()
        return user_dict(user)


# ---------- access token (JWT) ----------
def create_access_token(user_id: int) -> str:
    now = utcnow()
    payload = {"sub": str(user_id), "type": "access", "iat": now,
               "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> int:
    """Trả user_id. Sai chữ ký / hết hạn / sai loại → jwt.InvalidTokenError (ExpiredSignatureError là lớp con)."""
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM],   # cố định thuật toán → chặn "alg": "none"
                         options={"require": ["exp", "iat", "sub"]})
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Không phải access token")
    try:
        return int(payload["sub"])
    except ValueError:
        raise jwt.InvalidTokenError("sub không hợp lệ") from None


def session_response(user: dict, access_token: str, include_token: bool) -> dict:
    """include_token: chỉ /auth/login, /auth/register (cần mật khẩu) trả token trong JSON cho /docs, curl.
    /auth/refresh không trả → mã độc chèn vào trang gọi /auth/refresh cũng không đọc được token."""
    body = {"user": user, "expires_in": ACCESS_TOKEN_MINUTES * 60}
    if include_token:
        body.update(access_token=access_token, token_type="bearer")
    return body


# ---------- refresh token (lưu trong database) ----------
def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _add_refresh_token(s: Session, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    s.add(RefreshToken(user_id=user_id, token_hash=_sha256(token),
                       expires_at=utcnow() + timedelta(days=REFRESH_TOKEN_DAYS)))
    return token


def issue_refresh_token(user_id: int) -> str:
    """Sau khi đăng nhập / đăng ký. Dọn luôn các token đã hết hạn của người này."""
    with Session(db.engine) as s, s.begin():
        s.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.expires_at < utcnow()))
        return _add_refresh_token(s, user_id)


def rotate_refresh_token(token: str) -> tuple[dict, str] | None:
    """Đổi refresh token cũ lấy token mới. Không hợp lệ / hết hạn / đã thu hồi → None."""
    with Session(db.engine) as s, s.begin():
        # FOR UPDATE: hai request làm mới cùng lúc bằng một token → chỉ request đầu đổi được
        row = s.scalar(select(RefreshToken).where(RefreshToken.token_hash == _sha256(token)).with_for_update())
        if row is None:
            return None
        now = utcnow()
        if row.revoked_at is not None and now - _aware(row.revoked_at) > timedelta(seconds=REUSE_GRACE_SECONDS):
            revoke_all(row.user_id, s)       # dùng lại token đã đổi từ lâu → có thể bị lộ
            return None
        user = s.get(User, row.user_id)
        if _aware(row.expires_at) <= now or user is None or not user.is_active:
            return None
        row.revoked_at = row.revoked_at or now
        return user_dict(user), _add_refresh_token(s, user.id)


def revoke_refresh_token(token: str) -> None:
    """Đăng xuất phiên này."""
    with Session(db.engine) as s, s.begin():
        s.execute(delete(RefreshToken).where(RefreshToken.token_hash == _sha256(token)))


def revoke_all(user_id: int, s: Session | None = None) -> None:
    """Đăng xuất mọi thiết bị của một người."""
    stmt = delete(RefreshToken).where(RefreshToken.user_id == user_id)
    if s is not None:
        s.execute(stmt)
        return
    with Session(db.engine) as s, s.begin():
        s.execute(stmt)


# ---------- cookie ----------
_COOKIE_FLAGS = {"httponly": True, "secure": COOKIE_SECURE, "samesite": "strict"}


def set_session_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    # max_age của access cookie = hạn của JWT → trình duyệt tự bỏ cookie khi token hết hạn
    response.set_cookie(ACCESS_COOKIE, access_token, max_age=ACCESS_TOKEN_MINUTES * 60, path="/", **_COOKIE_FLAGS)
    response.set_cookie(REFRESH_COOKIE, refresh_token, max_age=REFRESH_TOKEN_DAYS * 86400, path="/auth",
                        **_COOKIE_FLAGS)


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", **_COOKIE_FLAGS)
    response.delete_cookie(REFRESH_COOKIE, path="/auth", **_COOKIE_FLAGS)


def check_csrf(request: Request) -> None:
    """Dependency: request dùng cookie mà thay đổi dữ liệu (POST/DELETE) → bắt buộc có header X-Requested-With."""
    if request.method not in SAFE_METHODS and not request.headers.get(CSRF_HEADER):
        raise HTTPException(403, f"Thiếu header {CSRF_HEADER} (chống CSRF)")


# ---------- dependency cho các route cần đăng nhập ----------
_bearer = HTTPBearer(auto_error=False)    # tự trả 401 có thông báo tiếng Việt thay vì 403 mặc định


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    """Header Bearer (/docs, curl) được ưu tiên; không có thì lấy cookie access_token (web)."""
    if cred is not None:
        token = cred.credentials       # header không bị trình duyệt tự gửi → không cần chống CSRF
    else:
        token = request.cookies.get(ACCESS_COOKIE)
        if token is None:
            raise _unauthorized("Chưa đăng nhập")
        check_csrf(request)
    try:
        user_id = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Phiên đăng nhập đã hết hạn") from None
    except jwt.InvalidTokenError:
        raise _unauthorized("Token không hợp lệ") from None
    user = db.get_user(user_id)     # tài khoản bị khoá / xoá → token còn hạn cũng không dùng được
    if user is None:
        raise _unauthorized("Tài khoản không tồn tại hoặc đã bị khoá")
    return user
