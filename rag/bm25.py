"""
BM25 dạng sparse vector cho Qdrant (nhánh bm25 của hybrid search: dense + bm25 → RRF).

  Ingest:   doc_sparse(text, avgdl)  → phần TF của BM25 cho từng chunk (avgdl = avg_doc_len của cả collection)
  Truy vấn: query_sparse(question)  → mỗi token của câu hỏi trọng số 1

Phần IDF KHÔNG tính ở đây: collection phải khai báo sparse vector với modifier IDF (SPARSE_VECTOR_PARAMS), Qdrant tự
nhân IDF lúc truy vấn → điểm = Σ IDF(t) · tf_bm25(t, d). Nhờ vậy thêm / xoá chunk không phải tính lại trọng số cũ.

Token = âm tiết (tiếng Việt viết tách âm tiết) + cặp âm tiết liền nhau ("hồ_chí", "chí_minh") để bắt cụm từ ghép
mà không cần bộ tách từ.
"""
import re
import unicodedata
import zlib
from collections import Counter

from qdrant_client import models

K1, B = 1.2, 0.75
_TOKEN = re.compile(r"\w+", re.UNICODE)

# Cấu hình sparse vector khi tạo collection: client.create_collection(..., sparse_vectors_config={"bm25": ...}).
SPARSE_VECTOR_PARAMS = models.SparseVectorParams(modifier=models.Modifier.IDF)


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower())
    syl = _TOKEN.findall(text)
    return syl + [f"{a}_{b}" for a, b in zip(syl, syl[1:])]


def _tid(tok: str) -> int:
    # KHÔNG dùng hash() của Python: bị salt, mỗi lần chạy ra số khác
    return zlib.crc32(tok.encode("utf-8")) & 0x7FFFFFFF


def _to_sparse(weights: dict[int, float]) -> models.SparseVector:
    return models.SparseVector(indices=list(weights), values=list(weights.values()))


def avg_doc_len(texts: list[str]) -> float:
    """avgdl của BM25: số token trung bình mỗi chunk (đếm cả cặp âm tiết, giống doc_sparse)."""
    return sum(len(tokenize(t)) for t in texts) / max(len(texts), 1)


def doc_sparse(text: str, avgdl: float) -> models.SparseVector:
    toks = tokenize(text)
    dl, w = len(toks), {}
    for t, f in Counter(toks).items():
        i = _tid(t)  # gộp nếu hash trùng — Qdrant không nhận index lặp
        w[i] = w.get(i, 0.0) + f * (K1 + 1) / (f + K1 * (1 - B + B * dl / avgdl))
    return _to_sparse(w)


def query_sparse(text: str) -> models.SparseVector:
    return _to_sparse({_tid(t): 1.0 for t in set(tokenize(text))})
