"""
REST API cho chatbot giáo trình Tư tưởng Hồ Chí Minh. File này chỉ dựng app; endpoint nằm trong app/routers/:

  routers/qa.py    GET /health, POST /search, POST /ask        hỏi–đáp không lưu lịch sử
  routers/auth.py  /auth/register, /login, /refresh, /logout, /me
  routers/chat.py  /conversations, POST /chat (NDJSON stream)   lịch sử chat theo tài khoản
  routers/exams.py /exams, /submissions, GET /chapters          bài kiểm tra: tạo đề, làm bài, chấm (AI chấm tự luận nền)

Mọi endpoint trừ /health cần đăng nhập: cookie access_token (web) hoặc header
Authorization: Bearer <access token> (trong /docs: bấm Authorize, dán access_token lấy từ /auth/login).

Model được nạp + warmup MỘT lần lúc khởi động (gán vào app.deps.rag); mọi request dùng chung.
Sau đó quét bù chạy nền: chấm các câu tự luận còn dở từ lần chạy trước (app/essay_ai.py: start_sweep).

Web (React, thư mục web/):
  dev   : cd web && npm run dev       → http://localhost:5173 (Vite proxy các endpoint sang :8000)
  build : cd web && npm run build     → FastAPI tự phục vụ web/dist tại http://localhost:8000
  Trang web và API dùng chung đường dẫn (/chat, /exams/5/take, /submissions/3…): trình duyệt mở trang
  (Accept: text/html) → index.html, React Router hiện đúng trang; fetch / Swagger → API (spa_fallback).

Chạy (ở thư mục gốc dự án):  uvicorn app.main:app --port 8000
Docs:  http://localhost:8000/docs
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import auth, db, deps, essay_ai
from app.routers import auth as auth_routes
from app.routers import chat, exams, qa
from rag.pipeline import GEMINI_MODEL, GuardedRAG

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = time.perf_counter()
    auth.check_config()                              # thiếu JWT_SECRET → dừng ngay
    db.init_db()                                     # tạo bảng trong Postgres nếu chưa có
    rag = await asyncio.to_thread(GuardedRAG)        # nạp model không chặn event loop
    await asyncio.to_thread(rag.warmup)
    deps.rag = rag
    print(f"[api] sẵn sàng sau {time.perf_counter() - t:.1f}s | reranker={rag.retriever.reranker.device} "
          f"guard={rag.guard.device} gemini={GEMINI_MODEL}")
    sweep = essay_ai.start_sweep(rag)
    yield
    sweep.cancel()       # tắt server giữa chừng: câu chưa chấm vẫn null → lần khởi động sau quét tiếp
    deps.rag = None


app = FastAPI(title="Chatbot Tư tưởng Hồ Chí Minh", version="1.0", lifespan=lifespan)

# Chỉ cần khi frontend chạy ở origin khác mà không qua Vite proxy (vd. deploy tách riêng).
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", auth.CSRF_HEADER],
    allow_credentials=True,     # cho phép gửi cookie đăng nhập
)

app.include_router(qa.router)
app.include_router(auth_routes.router)
app.include_router(chat.router)
app.include_router(exams.router)

# ---------- web ----------
# Mount SAU các router → /auth, /exams, /chapters, /ask, /search, /health, /docs vẫn được khớp trước.
WEB_DIST = ROOT / "web" / "dist"
API_PAGES = ("/docs", "/redoc", "/openapi.json")   # trang HTML của chính FastAPI, không thay bằng web


def wants_web_page(request: Request) -> bool:
    """Trình duyệt mở một trang của web (gõ URL, F5, link chia sẻ đề) chứ không phải fetch API / tải file tĩnh."""
    path = request.url.path
    return (request.method == "GET" and "text/html" in request.headers.get("accept", "")
            and not path.startswith(API_PAGES) and "." not in path.rsplit("/", 1)[-1])


@app.middleware("http")
async def spa_fallback(request: Request, call_next):
    """/chat, /exams/5/take, /submissions/3… vừa là trang web vừa là API: trang → index.html (React Router lo phần
    còn lại). Thiếu cái này, F5 ở /submissions/3 trả JSON của API, ở /chat trả 405 (chỉ có POST /chat)."""
    index = WEB_DIST / "index.html"
    if wants_web_page(request) and index.is_file():
        return FileResponse(index)
    return await call_next(request)


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
