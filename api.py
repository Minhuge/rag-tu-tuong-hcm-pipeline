"""
REST API cho chatbot giáo trình Tư tưởng Hồ Chí Minh.

  GET  /health   — trạng thái: thiết bị chạy model, Gemini model, số point trong Qdrant
  POST /search   — chỉ retrieve + rerank + retrieval guard (không gọi Gemini, không tốn quota)
  POST /ask      — toàn bộ pipeline có guardrail (pipeline.GuardedRAG)

Model được nạp + warmup MỘT lần lúc khởi động; mọi request dùng chung.

Web chat (React, thư mục web/):
  dev   : cd web && npm run dev       → http://localhost:5173 (Vite proxy các endpoint sang :8000)
  build : cd web && npm run build     → FastAPI tự phục vụ web/dist tại http://localhost:8000

Chạy:  uvicorn api:app --port 8000
Docs:  http://localhost:8000/docs
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from guards import MAX_QUESTION_CHARS, source_label
from pipeline import GEMINI_MODEL, GEMINI_THINKING, GuardedRAG
from search_rerank import CANDIDATES, COLLECTION, TOP_N, chapter_filter, qdrant_search, retrieval_guard

rag: GuardedRAG | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag
    t = time.perf_counter()
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
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
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


# ---------- web chat ----------
# Mount SAU các route API → /ask, /search, /health, /docs vẫn được khớp trước.
WEB_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
