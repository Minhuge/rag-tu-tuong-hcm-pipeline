"""
Đối tượng dùng chung giữa các router: pipeline RAG đã nạp model.

main.py nạp GuardedRAG một lần lúc khởi động rồi gán vào `rag`; router lấy ra qua get_rag()
(đọc lúc gọi, không import thẳng biến → test thay được bằng pipeline giả: monkeypatch.setattr(deps, "rag", ...)).
"""
from fastapi import HTTPException

from rag.pipeline import GuardedRAG

rag: GuardedRAG | None = None


def get_rag() -> GuardedRAG:
    if rag is None:
        raise HTTPException(503, "Model đang khởi động, thử lại sau.")
    return rag
