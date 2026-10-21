"""
Retrieve (Qdrant) → Rerank (Qwen3-Reranker) → Retrieval guardrail.

  1. Qdrant lấy CANDIDATES chunk (lấy rộng, ưu tiên không bỏ sót). Collection có sparse vector "bm25" (ingest mới)
     → hybrid: top HYBRID_PREFETCH dense + top HYBRID_PREFETCH bm25, gộp RRF, lấy HYBRID_CANDIDATES chunk.
  2. Qwen3-Reranker chấm lại, giữ TOP_N chunk tốt nhất.
  2b. Chunk mở đầu bằng từ trỏ về đoạn trước ("Đây là…", "Do đó…") → ghép thêm chunk liền trước (attach_previous).
  3. Retrieval guardrail dựa trên chunk đứng đầu sau rerank — cần CẢ P(yes) VÀ cosine của nó:
       pass    → đủ bằng chứng, cho LLM trả lời
       partial → chỉ có bằng chứng một phần, trả lời kèm lưu ý
       refuse  → giáo trình không đề cập, không gọi LLM

Cấu hình qua .env (đều có giá trị mặc định):
  QDRANT_URL=http://localhost:6333   # Qdrant chạy bằng Docker
  QDRANT_PATH=./qdrant_data          # HOẶC chế độ nhúng (không cần Docker) — đặt cái này thì bỏ qua URL
  QDRANT_COLLECTION=tu_tuong_hcm
  QDRANT_API_KEY=...                 # chỉ cần với Qdrant Cloud
  HYBRID=0                           # tắt hybrid: chỉ dense, kể cả khi collection có bm25 (để so sánh)

Payload mỗi point cần có key "text" (và nên có "chunk_id", "chapter", "chapter_no", "page").

Chạy:  python -m rag.search_rerank
"""
import os
import re
import time
import unicodedata
from functools import lru_cache

import numpy as np

from dotenv import load_dotenv
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient, models

from rag.bm25 import query_sparse
from rag.reranker import Qwen3Reranker

load_dotenv()

EMBED_MODEL = "qwen3-embedding:4b"   # PHẢI trùng model đã dùng khi ingest
# Câu hỏi chỉ vài chục token: context 1024 thay vì 4096 mặc định giúp Ollama giảm từ 4.8 GB
# xuống 3.3 GB VRAM → reranker + guard vẫn nằm gọn trên GPU 6 GB (vector gần như không đổi).
EMBED_NUM_CTX = int(os.getenv("EMBED_NUM_CTX", "1024"))
# Ollama có lúc treo (VRAM gần cạn) → không đặt giới hạn thì request chờ mãi. Lần đầu nạp model mất ~10 s.
EMBED_TIMEOUT = float(os.getenv("EMBED_TIMEOUT", "30"))
COLLECTION = os.getenv("QDRANT_COLLECTION", "tu_tuong_hcm")

# Cấu hình nhẹ cho Mac mini M1 (xem giải thích trong chat).
CANDIDATES = 10   # số chunk lấy từ Qdrant
TOP_N = 4         # số chunk giữ lại sau rerank, đưa vào prompt cho LLM
# Hybrid search (dense + BM25 gộp RRF), chỉ chạy khi collection có sparse vector "bm25".
HYBRID = os.getenv("HYBRID", "1") == "1"
HYBRID_PREFETCH = 20     # mỗi nhánh (dense, bm25) lấy chừng này chunk trước khi gộp
HYBRID_CANDIDATES = 15   # số chunk sau RRF đưa vào reranker
RERANK_MAX_LEN = 768

# Ngưỡng chọn từ calibrate.py (42 câu × 2 cách gõ): mọi câu giáo trình có P(yes) ≥ 0.97 và cosine ≥ 0.49,
# câu lạc đề có cosine ≤ 0.43. Chỉ dùng P(yes) thì không tách được: reranker cho câu lạc đề
# dùng chung chữ với giáo trình điểm rất cao ("đội bóng đoàn kết" 0.98, "gián có hại" 0.87),
# nhưng cosine của chúng thấp. Thêm câu hỏi thật vào calibrate.py rồi chạy lại trước khi sửa.
MIN_TOP = 0.9         # pass    khi P(yes) ≥ MIN_TOP      VÀ cosine ≥ MIN_COS
MIN_COS = 0.45
PARTIAL_BAND = 0.5    # partial khi P(yes) ≥ PARTIAL_BAND VÀ cosine ≥ PARTIAL_COS (ca sát ngưỡng:
PARTIAL_COS = 0.40    #         Gemini trả lời kèm lưu ý, luôn có giám khảo), còn lại → refuse


def get_qdrant() -> QdrantClient:
    path = os.getenv("QDRANT_PATH")
    if path:
        return QdrantClient(path=path)   # chế độ nhúng: dữ liệu nằm trong thư mục, không qua mạng
    return QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"),
                        api_key=os.getenv("QDRANT_API_KEY"))


def chapter_filter(chapter: str | int) -> models.Filter:
    """Lọc theo chương: số (3, 0 = Mở đầu) → chapter_no; chữ ("Chương III") → chapter."""
    key = "chapter_no" if isinstance(chapter, int) else "chapter"
    return models.Filter(must=[models.FieldCondition(key=key, match=models.MatchValue(value=chapter))])


def load_chapter(client: QdrantClient, chapter_no: int) -> list[dict]:
    """
    Toàn bộ đoạn của một chương, theo thứ tự trong giáo trình (để sinh đề — không phải tìm kiếm ngữ nghĩa).
    Chỉ đọc payload qua scroll: không embedding, không cần Ollama/GPU.
    """
    chunks, offset = [], None
    while True:
        points, offset = client.scroll(COLLECTION, scroll_filter=chapter_filter(chapter_no), limit=256,
                                       offset=offset, with_payload=True, with_vectors=False)
        chunks += [p.payload for p in points]
        if offset is None:
            break
    return sorted(chunks, key=lambda c: (c.get("page") or 0, c.get("chunk_id", "")))


def load_pages(client: QdrantClient, first: int, last: int) -> list[dict]:
    """Các đoạn có page trong [first, last], theo thứ tự trong giáo trình (chấm tự luận: trang nguồn ±1).
    Lọc payload, không embedding. 585 đoạn → quét không cần index cho page."""
    flt = models.Filter(must=[models.FieldCondition(key="page", range=models.Range(gte=first, lte=last))])
    chunks, offset = [], None
    while True:
        points, offset = client.scroll(COLLECTION, scroll_filter=flt, limit=256, offset=offset,
                                       with_payload=True, with_vectors=False)
        chunks += [p.payload for p in points]
        if offset is None:
            break
    return sorted(chunks, key=lambda c: (c.get("page") or 0, c.get("chunk_id", "")))


@lru_cache(maxsize=None)
def has_bm25(client: QdrantClient, collection: str) -> bool:
    """Collection có sparse vector "bm25" (scripts/ingest.py mới) không. Hỏi Qdrant một lần mỗi collection."""
    return "bm25" in (client.get_collection(collection).config.params.sparse_vectors or {})


def retrieval_mode(client: QdrantClient, collection: str | None = None) -> str:
    return "hybrid" if HYBRID and has_bm25(client, collection or COLLECTION) else "dense"


def qdrant_search(client: QdrantClient, embedder, question: str,
                  limit: int | None = None, query_filter: models.Filter | None = None) -> list[dict]:
    """Trả về list dict chuẩn hoá {id, score, metadata} — đúng dạng reranker.rerank() cần.
    "score" LUÔN là cosine của vector dense (reranker ghi nó vào "cosine", retrieval_guard dùng), kể cả khi hybrid.
    limit=None → HYBRID_CANDIDATES (hybrid) / CANDIDATES (dense)."""
    vector = embedder.embed_query(question)
    if retrieval_mode(client) == "hybrid":
        points = client.query_points(
            collection_name=COLLECTION,
            prefetch=[
                models.Prefetch(query=vector, limit=HYBRID_PREFETCH, filter=query_filter),   # dense: vector không tên
                models.Prefetch(query=query_sparse(question), using="bm25", limit=HYBRID_PREFETCH, filter=query_filter),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit or HYBRID_CANDIDATES, with_payload=True, with_vectors=[""],
        ).points
        # p.score lúc này là điểm RRF, không phải cosine → tính lại. Distance Cosine: Qdrant lưu vector đã chuẩn hoá,
        # nên dot với câu hỏi đã chuẩn hoá = cosine (chunk chỉ nhánh bm25 tìm thấy cũng có cosine thật).
        qn = np.asarray(vector) / np.linalg.norm(vector)
        return [
            {"id": p.payload.get("chunk_id", str(p.id)), "score": float(np.dot(p.vector, qn)), "rrf": p.score,
             "metadata": p.payload}
            for p in points
        ]
    points = client.query_points(
        collection_name=COLLECTION, query=vector, limit=limit or CANDIDATES,
        query_filter=query_filter, with_payload=True,
    ).points
    return [
        {"id": p.payload.get("chunk_id", str(p.id)), "score": p.score, "metadata": p.payload}
        for p in points
    ]


# ---------- ghép chunk liền trước ----------
# Chunk mở đầu bằng từ trỏ về đoạn trước thì ý được nói tới nằm ở cuối chunk liền trước, mà chunk_overlap (100 ký tự)
# không mang sang được khi câu đó dài. Vd. chunk_156 ". Đây là một luận điểm sáng tạo, có giá trị lý luận và thực tiễn
# to lớn…" khớp câu hỏi nên được tìm thấy, nhưng "đây" là câu cuối chunk_155 "…cách mạng giải phóng dân tộc ở thuộc địa
# có thể giành thắng lợi trước cách mạng vô sản ở chính quốc" → không ghép thì Gemini không biết luận điểm là gì.
ATTACH_PREVIOUS = os.getenv("ATTACH_PREVIOUS", "0") == "1"     # tạm tắt (16/10): thử chunk_overlap 300 thay thế
BACKREF_RE = re.compile(r"(Đây|Đó là|Điều này|Điều đó|Như vậy|Do đó|Do vậy|Vì vậy|Vì thế|Bởi vậy|Chính vì)\b")


def refers_back(text: str) -> bool:
    """Đoạn mở đầu bằng từ trỏ về đoạn trước. Chuẩn hoá NFC trước: text từ PDF có chỗ lưu dấu tách rời (chữ + dấu
    kết hợp) → so thẳng với "Đây" không khớp."""
    head = unicodedata.normalize("NFC", text[:40]).lstrip(" .…\n\t")
    return bool(BACKREF_RE.match(head))


def previous_chunk_id(chunk_id: str) -> str | None:
    """"chunk_156" → "chunk_155" (giữ số chữ số); chunk đầu tiên / id lạ → None."""
    m = re.fullmatch(r"(.*?)(\d+)", chunk_id)
    if not m or int(m.group(2)) <= 1:
        return None
    return f"{m.group(1)}{int(m.group(2)) - 1:0{len(m.group(2))}d}"


def fetch_chunk(client: QdrantClient, chunk_id: str) -> dict | None:
    points, _ = client.scroll(COLLECTION, limit=1, with_payload=True, scroll_filter=models.Filter(
        must=[models.FieldCondition(key="chunk_id", match=models.MatchValue(value=chunk_id))]))
    return points[0].payload if points else None


def attach_previous(client: QdrantClient, ranked: list[dict]) -> list[dict]:
    """Chèn chunk liền trước (cùng chương, chưa có trong danh sách) ngay TRƯỚC mỗi chunk trỏ về đoạn trước. Chunk chèn
    thêm mang điểm của chunk cần nó → retrieval guard và các chỗ lọc theo điểm cho kết quả như cũ; "context_for" = id
    chunk cần nó. Chỉ hỏi Qdrant khi có chunk trỏ về đoạn trước."""
    have = {str(it["id"]) for it in ranked}
    out = []
    for it in ranked:
        prev_id = previous_chunk_id(str(it["id"])) if refers_back(it["metadata"].get("text", "")) else None
        if prev_id and prev_id not in have:
            prev = fetch_chunk(client, prev_id)
            if prev and prev.get("chapter") == it["metadata"].get("chapter"):
                out.append({**it, "id": prev_id, "metadata": prev, "context_for": it["id"]})
                have.add(prev_id)
        out.append(it)
    return out


def retrieval_guard(ranked: list[dict], min_top: float = MIN_TOP, min_cos: float = MIN_COS,
                    partial: float = PARTIAL_BAND, partial_cos: float = PARTIAL_COS) -> str:
    if not ranked:
        return "refuse"
    top, cos = ranked[0]["rerank_score"], ranked[0]["cosine"]
    if top >= min_top and cos >= min_cos:
        return "pass"
    if top >= partial and cos >= partial_cos:
        return "partial"
    return "refuse"


class Retriever:
    """Nạp model 1 lần, dùng lại cho mọi câu hỏi (FastAPI: tạo ở startup)."""

    def __init__(self):
        self.client = get_qdrant()
        self.embedder = OllamaEmbeddings(model=EMBED_MODEL, num_ctx=EMBED_NUM_CTX,
                                         keep_alive=-1,   # giữ model trong RAM
                                         client_kwargs={"timeout": EMBED_TIMEOUT})
        self.reranker = Qwen3Reranker(max_length=RERANK_MAX_LEN, batch_size=max(CANDIDATES, HYBRID_CANDIDATES))

    def warmup(self):
        """Lần chạy đầu trên GPU Apple (MPS) rất chậm vì biên dịch kernel — làm trước khi nhận request."""
        self.embedder.embed_query("khởi động")
        self.reranker.score("khởi động", ["đoạn văn mẫu"])

    def run(self, question: str, query_filter: models.Filter | None = None):
        t0 = time.perf_counter()
        candidates = qdrant_search(self.client, self.embedder, question, query_filter=query_filter)
        t1 = time.perf_counter()
        ranked = self.reranker.rerank(question, candidates, top_n=TOP_N)
        t2 = time.perf_counter()
        return ranked, {"retrieve_s": t1 - t0, "rerank_s": t2 - t1, "n": len(candidates)}

    @lru_cache(maxsize=256)
    def run_cached(self, question: str):
        """Cache theo câu hỏi (không filter) — hỏi lại cùng câu gần như tức thì."""
        return self.run(question)


def print_comparison(question: str, ranked: list[dict], decision: str, timing: dict, note: str = ""):
    print("=" * 100)
    print(f"CÂU HỎI : {question}")
    if note:
        print(f"FILTER  : {note}")
    print(f"Thời gian: retrieve {timing['retrieve_s']:.2f}s | rerank {timing['rerank_s']:.2f}s "
          f"({timing['n']} chunk)")
    print("=" * 100)
    print(f"{'Mới':>3} {'Cũ':>4} {'Δ':>4} {'P(yes)':>7} {'Cosine':>7}  {'Chunk':<10} {'Chương':<14} Nội dung")
    print("-" * 100)
    for it in ranked:
        delta = it["cosine_rank"] - it["rerank_rank"]
        arrow = f"↑{delta}" if delta > 0 else (f"↓{-delta}" if delta < 0 else "=")
        preview = it["metadata"].get("text", "")[:70].replace("\n", " ")
        print(f"{it['rerank_rank']:>3} {it['cosine_rank']:>4} {arrow:>4} "
              f"{it['rerank_score']:>7.3f} {it['cosine']:>7.3f}  {str(it['id']):<10} "
              f"{str(it['metadata'].get('chapter', 'N/A')):<14} {preview}...")

    label = {
        "pass": "PASS    → đủ bằng chứng, gửi top chunk cho LLM",
        "partial": "PARTIAL → trả lời kèm lưu ý 'giáo trình chỉ đề cập một phần'",
        "refuse": "REFUSE  → 'Giáo trình không đề cập nội dung này.' (không gọi LLM)",
    }[decision]
    print(f"\nRetrieval guardrail: {label}\n")


if __name__ == "__main__":
    print("Đang tải model (lần đầu tải Qwen3-Reranker ~1.2 GB)...")
    r = Retriever()
    print(f"Reranker chạy trên: {r.reranker.device} | Qdrant collection: {COLLECTION}")
    t = time.perf_counter()
    r.warmup()
    print(f"Warmup: {time.perf_counter() - t:.1f}s\n")

    questions = [
        ("Nguồn gốc và tiền đề hình thành tư tưởng Hồ Chí Minh là gì?", None),
        ("Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?", None),
        ("Đối tượng và phương pháp nghiên cứu của môn học là gì?", "Chương Mở đầu"),
        ("Hồ Chí Minh quan niệm thế nào về vai trò của Đảng Cộng sản Việt Nam?", None),
        ("Giá vàng hôm nay bao nhiêu?", None),   # ngoài phạm vi: kỳ vọng REFUSE
    ]
    for q, chap in questions:
        ranked, timing = r.run(q, query_filter=chapter_filter(chap) if chap else None)
        print_comparison(q, ranked, retrieval_guard(ranked), timing, note=f"chapter = {chap}" if chap else "")
