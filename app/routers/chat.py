"""
Lịch sử chat (PostgreSQL, xem app/db.py), mỗi người chỉ thấy của mình:
  GET    /conversations       — danh sách cuộc trò chuyện, mới → cũ (kèm mode: docs | exam)
  GET    /conversations/{id}  — một cuộc trò chuyện kèm toàn bộ tin nhắn
  DELETE /conversations/{id}  — xoá cuộc trò chuyện và tin nhắn của nó
  POST   /chat                — gửi câu hỏi, nhận về luồng NDJSON (mỗi dòng một sự kiện):
           1. lưu tin user → 2. đọc lịch sử → 3. trả lời (stream) → 4. lưu câu trả lời cuối
           Bước 3 theo chế độ của cuộc trò chuyện: "docs" → pipeline RAG (GuardedRAG.ask_stream),
           "exam" → Trợ lý bài kiểm tra (app/agent_ai.agent_stream: cùng lớp kiểm tra đầu vào, rồi agent gọi tool).
"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from app import agent_ai, auth, db, exams
from app.db import ConversationMode
from app.deps import get_rag
from rag.guards import MAX_QUESTION_CHARS

router = APIRouter(tags=["lịch sử chat"])


class ChatRequest(BaseModel):
    conversation_id: int | None = Field(None, description="Bỏ trống → tạo cuộc trò chuyện mới")
    message: str = Field(..., max_length=MAX_QUESTION_CHARS * 5,
                         examples=["Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?"])
    retry: bool = Field(False, description="Hỏi lại câu cuối chưa có câu trả lời (không lưu trùng tin user)")
    mode: ConversationMode = Field("docs", description='Chỉ dùng khi tạo cuộc trò chuyện mới: "docs" hỏi giáo trình, '
                                                        '"exam" trợ lý bài kiểm tra. Cuộc trò chuyện cũ giữ chế độ của nó.')


@router.get("/conversations")
def list_conversations(user: dict = Depends(auth.get_current_user)):
    return db.list_conversations(user["id"])


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user: dict = Depends(auth.get_current_user)):
    try:
        return db.get_conversation(user["id"], conversation_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, user: dict = Depends(auth.get_current_user)):
    try:
        db.delete_conversation(user["id"], conversation_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return Response(status_code=204)


def _ndjson(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


@router.post("/chat")
async def chat(req: ChatRequest, user: dict = Depends(auth.get_current_user)):
    """
    Trả về application/x-ndjson, mỗi dòng một sự kiện:
      {"type": "conversation", "id", "title", ...}   luôn là dòng đầu (id mới nếu vừa tạo)
      {"type": "status", "step": "guard|rewrite|retrieve|generate|judge"}   (trợ lý bài kiểm tra: guard|agent)
      {"type": "tool", "name": "..."}                trợ lý bài kiểm tra đang gọi tool (list_my_submissions…)
      {"type": "delta", "text": "..."}               từng đoạn chữ Gemini đang viết
      {"type": "done", "message_id", "result": {...}} câu trả lời cuối đã qua kiểm tra, đã lưu vào DB
      {"type": "error", "message": "..."}            lỗi giữa chừng; tin user vẫn được lưu để hỏi lại
    Trợ lý bài kiểm tra hết lượt hôm nay → 429 ngay, không lưu tin (không tạo cuộc trò chuyện rỗng).
    """
    uid = user["id"]
    try:
        mode = req.mode if req.conversation_id is None else await asyncio.to_thread(
            db.conversation_mode, uid, req.conversation_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    r = get_rag()
    if mode == "exam" and (await asyncio.to_thread(agent_ai.agent_quota, uid))["remaining"] <= 0:
        raise HTTPException(429, agent_ai.quota_message())
    # Bước 1–2: lưu tin user, đọc lịch sử. Lỗi ở đây (không tìm thấy cuộc trò chuyện) → trả 404 bình thường.
    try:
        conversation, history = await asyncio.to_thread(db.begin_turn, uid, req.conversation_id, req.message, req.retry,
                                                         req.mode)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    conv_id = conversation["id"]

    async def events():
        yield _ndjson({"type": "conversation", **conversation})
        try:
            # Bước 3: trả lời theo chế độ của cuộc trò chuyện
            answer = (agent_ai.agent_stream(r, uid, req.message, history) if conversation["mode"] == "exam"
                      else r.ask_stream(req.message, history))
            async for ev in answer:
                if ev["type"] == "done":
                    # Bước 4: stream xong → lưu câu trả lời cuối (đã thay bằng câu an toàn nếu không qua 4c/4a)
                    ev = {**ev, "message_id": await asyncio.to_thread(db.save_answer, uid, conv_id, ev["result"])}
                yield _ndjson(ev)
        except exams.QuotaExceeded as e:                 # trợ lý bài kiểm tra: tab khác vừa dùng nốt lượt
            yield _ndjson({"type": "error", "message": str(e)})
        except RuntimeError as e:                        # hết quota ngày / hết credit (pipeline._call), quá giờ
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
