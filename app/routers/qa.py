"""
Hỏi–đáp không lưu lịch sử:
  GET  /health   — trạng thái: thiết bị chạy model, Gemini model, số point trong Qdrant (công khai)
  POST /search   — chỉ retrieve + rerank + retrieval guard (không gọi Gemini, không tốn quota)
  POST /ask      — toàn bộ pipeline có guardrail (rag.pipeline.GuardedRAG)
"""
import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from app import auth
from app.deps import get_rag
from rag.guards import MAX_QUESTION_CHARS, source_label
from rag.pipeline import GEMINI_MODEL, GEMINI_THINKING
from rag.search_rerank import CANDIDATES, COLLECTION, TOP_N, chapter_filter, qdrant_search, retrieval_guard

router = APIRouter(tags=["hỏi–đáp"])


class AskRequest(BaseModel):
    # Không đặt min_length/max_length ở đây: để lớp 1a trả câu từ chối thân thiện thay vì lỗi 422.
    question: str = Field(..., max_length=MAX_QUESTION_CHARS * 5,
                          examples=["Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?"])


class SearchRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    chapter: str | None = Field(None, examples=["Chương Mở đầu"])
    top_n: int = Field(TOP_N, ge=1, le=CANDIDATES)


@router.get("/health")
def health():
    r = get_rag()
    return {
        "status": "ok",
        "collection": COLLECTION,
        "points": r.retriever.client.count(COLLECTION).count,
        "reranker_device": r.retriever.reranker.device,
        "guard_device": r.guard.device,
        "gemini_model": GEMINI_MODEL,
        "gemini_thinking": GEMINI_THINKING,
    }


@router.post("/search")
def search(req: SearchRequest, user: dict = Depends(auth.get_current_user)):
    """Def thường (không async) → FastAPI tự chạy trong threadpool, không chặn event loop."""
    r = get_rag()
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


@router.post("/ask")
async def ask(req: AskRequest, user: dict = Depends(auth.get_current_user)):
    r = get_rag()
    try:
        res = await r.ask(req.question)
    except RuntimeError as e:                       # hết quota ngày (pipeline._call ném ra)
        raise HTTPException(429, str(e)) from e
    except genai_errors.APIError as e:
        raise HTTPException(502, f"Lỗi gọi Gemini: {e.code} {e.message}") from e
    return asdict(res)
