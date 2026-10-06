"""
REST API cho chatbot giáo trình Tư tưởng Hồ Chí Minh.

  GET  /health   — trạng thái: thiết bị chạy model, Gemini model, số point trong Qdrant
  POST /search   — chỉ retrieve + rerank + retrieval guard (không gọi Gemini, không tốn quota)
  POST /ask      — toàn bộ pipeline có guardrail (pipeline.GuardedRAG), không lưu lịch sử

  Lịch sử chat (PostgreSQL, DATABASE_URL trong .env, xem db.py). Mọi request gửi header X-Client-Id (mã ngẫu nhiên của trình duyệt):
  GET    /conversations       — danh sách cuộc trò chuyện của trình duyệt này, mới → cũ
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
import re
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

import db
from guards import MAX_QUESTION_CHARS, source_label
from pipeline import GEMINI_MODEL, GEMINI_THINKING, GuardedRAG
from search_rerank import CANDIDATES, COLLECTION, TOP_N, chapter_filter, qdrant_search, retrieval_guard

rag: GuardedRAG | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag
    t = time.perf_counter()
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
    allow_headers=["Content-Type", "X-Client-Id"],
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
def search(req: SearchRequest):
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
async def ask(req: AskRequest):
    r = _rag()
    try:
        res = await r.ask(req.question)
    except RuntimeError as e:                       # hết quota ngày (pipeline._call ném ra)
        raise HTTPException(429, str(e)) from e
    except genai_errors.APIError as e:
        raise HTTPException(502, f"Lỗi gọi Gemini: {e.code} {e.message}") from e
    return asdict(res)


# ---------- lịch sử chat (SQLite) ----------
_CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def client_id(x_client_id: str = Header(..., description="Mã ngẫu nhiên của trình duyệt, tự tạo ở web/src/api.js")) -> str:
    # Không phải đăng nhập: chỉ để người dùng chung mạng LAN không thấy lịch sử của nhau.
    if not _CLIENT_RE.match(x_client_id):
        raise HTTPException(400, "X-Client-Id không hợp lệ")
    return x_client_id


class ChatRequest(BaseModel):
    conversation_id: int | None = Field(None, description="Bỏ trống → tạo cuộc trò chuyện mới")
    message: str = Field(..., max_length=MAX_QUESTION_CHARS * 5,
                         examples=["Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?"])
    retry: bool = Field(False, description="Hỏi lại câu cuối chưa có câu trả lời (không lưu trùng tin user)")


@app.get("/conversations")
def list_conversations(cid: str = Depends(client_id)):
    return db.list_conversations(cid)


@app.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, cid: str = Depends(client_id)):
    try:
        return db.get_conversation(cid, conversation_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, cid: str = Depends(client_id)):
    try:
        db.delete_conversation(cid, conversation_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return Response(status_code=204)


def _ndjson(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


@app.post("/chat")
async def chat(req: ChatRequest, cid: str = Depends(client_id)):
    """
    Trả về application/x-ndjson, mỗi dòng một sự kiện:
      {"type": "conversation", "id", "title", ...}   luôn là dòng đầu (id mới nếu vừa tạo)
      {"type": "status", "step": "guard|rewrite|retrieve|generate|judge"}
      {"type": "delta", "text": "..."}               từng đoạn chữ Gemini đang viết
      {"type": "done", "message_id", "result": {...}} câu trả lời cuối đã qua kiểm tra, đã lưu vào DB
      {"type": "error", "message": "..."}            lỗi giữa chừng; tin user vẫn được lưu để hỏi lại
    """
    r = _rag()
    # Bước 1–2: lưu tin user, đọc lịch sử. Lỗi ở đây (không tìm thấy cuộc trò chuyện) → trả 404 bình thường.
    try:
        conversation, history = await asyncio.to_thread(db.begin_turn, cid, req.conversation_id, req.message, req.retry)
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
                    ev = {**ev, "message_id": await asyncio.to_thread(db.save_answer, cid, conv_id, ev["result"])}
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
# Mount SAU các route API → /ask, /search, /health, /docs vẫn được khớp trước.
WEB_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
