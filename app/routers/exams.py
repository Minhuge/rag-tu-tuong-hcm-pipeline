"""
Đề thi:
  GET /chapters   — danh sách chương (số + tên) cho dropdown tạo đề bằng AI
"""
from fastapi import APIRouter, Depends

from app import auth
from rag.exam_generation import CHAPTERS

router = APIRouter(tags=["đề thi"])


@router.get("/chapters")
def list_chapters(user: dict = Depends(auth.get_current_user)):
    """`chapter` là giá trị truyền lại khi tạo đề (khớp payload "chapter" trong Qdrant)."""
    return [{"number": c.number, "chapter": c.key, "title": c.title} for c in CHAPTERS]
