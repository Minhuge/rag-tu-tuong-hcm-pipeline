"""
Đăng nhập (logic ở app/auth.py) — access token và refresh token đều nằm trong cookie httpOnly:
  POST /auth/register  — tạo tài khoản rồi đăng nhập luôn (tắt bằng ALLOW_REGISTRATION=false)
  POST /auth/login     — email + mật khẩu → đặt 2 cookie; JSON có kèm access_token cho /docs, curl
  POST /auth/refresh   — access token hết hạn → đổi refresh token lấy cặp cookie mới (JSON không có token)
  POST /auth/logout    — thu hồi refresh token, xoá 2 cookie
  GET  /auth/me        — thông tin người đang đăng nhập
POST/DELETE đăng nhập bằng cookie phải có header X-Requested-With (chống CSRF); web tự gửi.
"""
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import auth

router = APIRouter(prefix="/auth", tags=["đăng nhập"])


class RegisterRequest(BaseModel):
    # Không giới hạn chặt ở đây: auth.register trả lỗi tiếng Việt dễ hiểu hơn lỗi 422.
    email: str = Field(..., max_length=255, examples=["ban@example.com"])
    password: str = Field(..., max_length=1024)
    display_name: str | None = Field(None, max_length=100)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., max_length=1024)


def _start_session(response: Response, user: dict) -> dict:
    access = auth.create_access_token(user["id"])
    auth.set_session_cookies(response, access, auth.issue_refresh_token(user["id"]))
    return auth.session_response(user, access, include_token=True)


@router.post("/register", status_code=201)
def register(req: RegisterRequest, response: Response):
    if not auth.ALLOW_REGISTRATION:
        raise HTTPException(403, "Đăng ký đang tắt, liên hệ quản trị viên để được cấp tài khoản.")
    try:
        user = auth.register(req.email, req.password, req.display_name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _start_session(response, user)


@router.post("/login")
def login(req: LoginRequest, response: Response):
    user = auth.authenticate(req.email, req.password)
    if user is None:     # không nói rõ sai email hay sai mật khẩu → không dò được email nào đã đăng ký
        raise HTTPException(401, "Email hoặc mật khẩu không đúng", headers={"WWW-Authenticate": "Bearer"})
    return _start_session(response, user)


@router.post("/refresh", dependencies=[Depends(auth.check_csrf)])
def refresh(response: Response, refresh_token: str | None = Cookie(None)):
    rotated = auth.rotate_refresh_token(refresh_token) if refresh_token else None
    if rotated is None:
        # Không raise HTTPException: như vậy sẽ mất lệnh xoá cookie hỏng trên trình duyệt.
        res = JSONResponse({"detail": "Phiên đăng nhập đã hết, hãy đăng nhập lại"}, status_code=401)
        auth.clear_session_cookies(res)
        return res
    user, new_refresh = rotated
    access = auth.create_access_token(user["id"])
    auth.set_session_cookies(response, access, new_refresh)
    return auth.session_response(user, access, include_token=False)


@router.post("/logout", status_code=204, dependencies=[Depends(auth.check_csrf)])
def logout(refresh_token: str | None = Cookie(None)):
    # Không cần access token → access token đã hết hạn vẫn đăng xuất được.
    if refresh_token:
        auth.revoke_refresh_token(refresh_token)
    res = Response(status_code=204)
    auth.clear_session_cookies(res)
    return res


@router.get("/me")
def me(user: dict = Depends(auth.get_current_user)):
    return user
