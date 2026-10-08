"""
REST API cho chatbot giáo trình Tư tưởng Hồ Chí Minh.

  GET  /health   — trạng thái: thiết bị chạy model, Gemini model, số point trong Qdrant
  POST /search   — chỉ retrieve + rerank + retrieval guard (không gọi Gemini, không tốn quota)
  POST /ask      — toàn bộ pipeline có guardrail (pipeline.GuardedRAG), không lưu lịch sử

  Đăng nhập (xem auth.py) — access token và refresh token đều nằm trong cookie httpOnly:
  POST   /auth/register       — tạo tài khoản rồi đăng nhập luôn (tắt bằng ALLOW_REGISTRATION=false)
  POST   /auth/login          — email + mật khẩu → đặt 2 cookie; JSON có kèm access_token cho /docs, curl
  POST   /auth/refresh        — access token hết hạn → đổi refresh token lấy cặp cookie mới (JSON không có token)
  POST   /auth/logout         — thu hồi refresh token, xoá 2 cookie
  GET    /auth/me             — thông tin người đang đăng nhập
  POST/DELETE đăng nhập bằng cookie phải có header X-Requested-With (chống CSRF); web tự gửi.

  Mọi endpoint dưới đây và /search, /ask cần đăng nhập: cookie access_token (web) hoặc header
  Authorization: Bearer <access token> (trong /docs: bấm Authorize, dán access_token lấy từ /auth/login).
  Chỉ /health là công khai.

  Lịch sử chat (PostgreSQL, DATABASE_URL trong .env, xem db.py), mỗi người chỉ thấy của mình:
  GET    /conversations       — danh sách cuộc trò chuyện, mới → cũ
  GET    /conversations/{id}  — một cuộc trò chuyện kèm toàn bộ tin nhắn
  DELETE /conversations/{id}  — xoá cuộc trò chuyện và tin nhắn của nó
  POST   /chat                — gửi câu hỏi, nhận về luồng NDJSON (mỗi dòng một sự kiện):
           1. lưu tin user → 2. đọc lịch sử → 3. pipeline + Gemini stream → 4. lưu câu trả lời cuối

Model được nạp + warmup MỘT lần lúc khởi động; mọi request dùng chung.

Web chat (React, thư mục web/):
  dev   : cd web && npm run dev       → http://localhost:5173 (Vite proxy các endpoint sang :8000)
  build : cd web && npm run build     → FastAPI tự phục vụ web/dist tại http://localhost:8000

Chạy:  uvicorn api:app --port 8000
Docs:  http://localhost:8000/docs
"""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

import auth
import db
from guards import MAX_QUESTION_CHARS, source_label
from pipeline import GEMINI_MODEL, GEMINI_THINKING, GuardedRAG
from search_rerank import CANDIDATES, COLLECTION, TOP_N, chapter_filter, qdrant_search, retrieval_guard

rag: GuardedRAG | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag
    t = time.perf_counter()
    auth.check_config()                         # thiếu JWT_SECRET → dừng ngay
    db.init_db()                                # tạo bảng trong Postgres nếu chưa có
    rag = await asyncio.to_thread(GuardedRAG)   # nạp model không chặn event loop
    await asyncio.to_thread(rag.warmup)
    print(f"[api] sẵn sàng sau {time.perf_counter() - t:.1f}s | reranker={rag.retriever.reranker.device} "
          f"guard={rag.guard.device} gemini={GEMINI_MODEL}")
    yield
    rag = None


app = FastAPI(title="Chatbot Tư tưởng Hồ Chí Minh", version="1.0", lifespan=lifespan)

# Chỉ cần khi frontend chạy ở origin khác mà không qua Vite proxy (vd. deploy tách riêng).
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization", auth.CSRF_HEADER],
    allow_credentials=True,     # cho phép gửi cookie đăng nhập
)


# ---------- schema ----------
class AskRequest(BaseModel):
    # Không đặt min_length/max_length ở đây: để lớp 1a trả câu từ chối thân thiện thay vì lỗi 422.
    question: str = Field(..., max_length=MAX_QUESTION_CHARS * 5,
                          examples=["Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?"])


class SearchRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    chapter: str | None = Field(None, examples=["Chương Mở đầu"])
    top_n: int = Field(TOP_N, ge=1, le=CANDIDATES)


class RegisterRequest(BaseModel):
    # Không giới hạn chặt ở đây: auth.register trả lỗi tiếng Việt dễ hiểu hơn lỗi 422.
    email: str = Field(..., max_length=255, examples=["ban@example.com"])
    password: str = Field(..., max_length=1024)
    display_name: str | None = Field(None, max_length=100)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., max_length=1024)


def _rag() -> GuardedRAG:
    if rag is None:
        raise HTTPException(503, "Model đang khởi động, thử lại sau.")
    return rag


# ---------- endpoints ----------
@app.get("/health")
def health():
    r = _rag()
    return {
        "status": "ok",
        "collection": COLLECTION,
        "points": r.retriever.client.count(COLLECTION).count,
        "reranker_device": r.retriever.reranker.device,
        "guard_device": r.guard.device,
        "gemini_model": GEMINI_MODEL,
        "gemini_thinking": GEMINI_THINKING,
    }


@app.post("/search")
def search(req: SearchRequest, user: dict = Depends(auth.get_current_user)):
    """Def thường (không async) → FastAPI tự chạy trong threadpool, không chặn event loop."""
    r = _rag()
    t0 = time.perf_counter()
    cands = qdrant_search(r.retriever.client, r.retriever.embedder, req.question,
                          query_filter=chapter_filter(req.chapter) if req.chapter else None)
    t1 = time.perf_counter()
    with r._gpu():
        ranked = r.retriever.reranker.rerank(req.question, cands, top_n=req.top_n)
    t2 = time.perf_counter()
    return {
        "retrieval": retrieval_guard(ranked),
        "results": [
            {
                "chunk_id": it["id"],
                "source": source_label(it["metadata"]),
                "rerank_score": round(it["rerank_score"], 4),
                "cosine": round(it["cosine"], 4),
                "cosine_rank": it["cosine_rank"],
                "rerank_rank": it["rerank_rank"],
                "text": it["metadata"].get("text", ""),
            }
            for it in ranked
        ],
        "timings": {"embed_qdrant_s": round(t1 - t0, 3), "rerank_s": round(t2 - t1, 3)},
    }


@app.post("/ask")
async def ask(req: AskRequest, user: dict = Depends(auth.get_current_user)):
    r = _rag()
    try:
        res = await r.ask(req.question)
    except RuntimeError as e:                       # hết quota ngày (pipeline._call ném ra)
        raise HTTPException(429, str(e)) from e
    except genai_errors.APIError as e:
        raise HTTPException(502, f"Lỗi gọi Gemini: {e.code} {e.message}") from e
    return asdict(res)


# ---------- đăng nhập ----------
def _start_session(response: Response, user: dict) -> dict:
    access = auth.create_access_token(user["id"])
    auth.set_session_cookies(response, access, auth.issue_refresh_token(user["id"]))
    return auth.session_response(user, access, include_token=True)


@app.post("/auth/register", status_code=201)
def register(req: RegisterRequest, response: Response):
    if not auth.ALLOW_REGISTRATION:
        raise HTTPException(403, "Đăng ký đang tắt, liên hệ quản trị viên để được cấp tài khoản.")
    try:
        user = auth.register(req.email, req.password, req.display_name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _start_session(response, user)


@app.post("/auth/login")
def login(req: LoginRequest, response: Response):
    user = auth.authenticate(req.email, req.password)
    if user is None:     # không nói rõ sai email hay sai mật khẩu → không dò được email nào đã đăng ký
        raise HTTPException(401, "Email hoặc mật khẩu không đúng", headers={"WWW-Authenticate": "Bearer"})
    return _start_session(response, user)


@app.post("/auth/refresh", dependencies=[Depends(auth.check_csrf)])
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


@app.post("/auth/logout", status_code=204, dependencies=[Depends(auth.check_csrf)])
def logout(refresh_token: str | None = Cookie(None)):
    # Không cần access token → access token đã hết hạn vẫn đăng xuất được.
    if refresh_token:
        auth.revoke_refresh_token(refresh_token)
    res = Response(status_code=204)
    auth.clear_session_cookies(res)
    return res


@app.get("/auth/me")
def me(user: dict = Depends(auth.get_current_user)):
    return user


# ---------- lịch sử chat ----------
class ChatRequest(BaseModel):
    conversation_id: int | None = Field(None, description="Bỏ trống → tạo cuộc trò chuyện mới")
    message: str = Field(..., max_length=MAX_QUESTION_CHARS * 5,
                         examples=["Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?"])
    retry: bool = Field(False, description="Hỏi lại câu cuối chưa có câu trả lời (không lưu trùng tin user)")


@app.get("/conversations")
def list_conversations(user: dict = Depends(auth.get_current_user)):
    return db.list_conversations(user["id"])


@app.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user: dict = Depends(auth.get_current_user)):
    try:
        return db.get_conversation(user["id"], conversation_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, user: dict = Depends(auth.get_current_user)):
    try:
        db.delete_conversation(user["id"], conversation_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return Response(status_code=204)


def _ndjson(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


@app.post("/chat")
async def chat(req: ChatRequest, user: dict = Depends(auth.get_current_user)):
    """
    Trả về application/x-ndjson, mỗi dòng một sự kiện:
      {"type": "conversation", "id", "title", ...}   luôn là dòng đầu (id mới nếu vừa tạo)
      {"type": "status", "step": "guard|rewrite|retrieve|generate|judge"}
      {"type": "delta", "text": "..."}               từng đoạn chữ Gemini đang viết
      {"type": "done", "message_id", "result": {...}} câu trả lời cuối đã qua kiểm tra, đã lưu vào DB
      {"type": "error", "message": "..."}            lỗi giữa chừng; tin user vẫn được lưu để hỏi lại
    """
    r = _rag()
    uid = user["id"]
    # Bước 1–2: lưu tin user, đọc lịch sử. Lỗi ở đây (không tìm thấy cuộc trò chuyện) → trả 404 bình thường.
    try:
        conversation, history = await asyncio.to_thread(db.begin_turn, uid, req.conversation_id, req.message, req.retry)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    conv_id = conversation["id"]

    async def events():
        yield _ndjson({"type": "conversation", **conversation})
        try:
            # Bước 3: pipeline + Gemini stream
            async for ev in r.ask_stream(req.message, history):
                if ev["type"] == "done":
                    # Bước 4: stream xong → lưu câu trả lời cuối (đã thay bằng câu an toàn nếu không qua 4c/4a)
                    ev = {**ev, "message_id": await asyncio.to_thread(db.save_answer, uid, conv_id, ev["result"])}
                yield _ndjson(ev)
        except RuntimeError as e:                        # hết quota ngày / hết credit (pipeline._call)
            yield _ndjson({"type": "error", "message": str(e)})
        except genai_errors.APIError as e:
            yield _ndjson({"type": "error", "message": f"Lỗi gọi Gemini: {e.code} {e.message}"})
        except Exception as e:                            # Qdrant / Ollama tắt giữa chừng...
            print(f"[api] /chat lỗi: {type(e).__name__}: {e}")
            yield _ndjson({"type": "error", "message": f"Máy chủ gặp lỗi: {type(e).__name__}"})
        # Trình duyệt ngắt kết nối (bấm Dừng / đóng trang) → Starlette huỷ generator này:
        # câu trả lời dở dang không được lưu, tin user còn đó để hỏi lại.

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- web chat ----------
# Mount SAU các route API → /auth, /ask, /search, /health, /docs vẫn được khớp trước.
WEB_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
