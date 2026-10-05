"""
Retrieve (Qdrant) → Rerank (Qwen3-Reranker) → Retrieval guardrail.

  1. Qdrant lấy CANDIDATES chunk (lấy rộng, ưu tiên không bỏ sót).
  2. Qwen3-Reranker chấm lại, giữ TOP_N chunk tốt nhất.
  3. Retrieval guardrail dựa trên điểm cao nhất:
       pass    → đủ bằng chứng, cho LLM trả lời
       partial → chỉ có bằng chứng một phần, trả lời kèm lưu ý
       refuse  → giáo trình không đề cập, không gọi LLM

Cấu hình qua .env (đều có giá trị mặc định):
  QDRANT_URL=http://localhost:6333   # Qdrant chạy bằng Docker
  QDRANT_PATH=./qdrant_data          # HOẶC chế độ nhúng (không cần Docker) — đặt cái này thì bỏ qua URL
  QDRANT_COLLECTION=tu_tuong_hcm
  QDRANT_API_KEY=...                 # chỉ cần với Qdrant Cloud

Payload mỗi point cần có key "text" (và nên có "chunk_id", "chapter", "page").

Chạy:  python search_rerank.py
"""
import os
import time
from functools import lru_cache

from dotenv import load_dotenv
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient, models

from reranker import Qwen3Reranker

load_dotenv()

EMBED_MODEL = "qwen3-embedding:4b"   # PHẢI trùng model đã dùng khi ingest
# Câu hỏi chỉ vài chục token: context 1024 thay vì 4096 mặc định giúp Ollama giảm từ 4.8 GB
# xuống 3.3 GB VRAM → reranker + guard vẫn nằm gọn trên GPU 6 GB (vector gần như không đổi).
EMBED_NUM_CTX = int(os.getenv("EMBED_NUM_CTX", "1024"))
COLLECTION = os.getenv("QDRANT_COLLECTION", "tu_tuong_hcm")

# Cấu hình nhẹ cho Mac mini M1 (xem giải thích trong chat).
CANDIDATES = 10   # số chunk lấy từ Qdrant
TOP_N = 4         # số chunk giữ lại sau rerank, đưa vào prompt cho LLM
RERANK_MAX_LEN = 512

# Ngưỡng tạm thời — PHẢI hiệu chỉnh lại bằng dữ liệu thật.
MIN_TOP = 0.5         # top score ≥ MIN_TOP        → pass
PARTIAL_BAND = 0.3    # PARTIAL_BAND ≤ top < MIN_TOP → partial, thấp hơn → refuse


def get_qdrant() -> QdrantClient:
    path = os.getenv("QDRANT_PATH")
    if path:
        return QdrantClient(path=path)   # chế độ nhúng: dữ liệu nằm trong thư mục, không qua mạng
    return QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"),
                        api_key=os.getenv("QDRANT_API_KEY"))


def chapter_filter(chapter: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key="chapter", match=models.MatchValue(value=chapter))])


def qdrant_search(client: QdrantClient, embedder, question: str,
                  limit: int = CANDIDATES, query_filter: models.Filter | None = None) -> list[dict]:
    """Trả về list dict chuẩn hoá {id, score, metadata} — đúng dạng reranker.rerank() cần."""
    vector = embedder.embed_query(question)
    points = client.query_points(
        collection_name=COLLECTION, query=vector, limit=limit,
        query_filter=query_filter, with_payload=True,
    ).points
    return [
        {"id": p.payload.get("chunk_id", str(p.id)), "score": p.score, "metadata": p.payload}
        for p in points
    ]


def retrieval_guard(ranked: list[dict], min_top: float = MIN_TOP, partial: float = PARTIAL_BAND) -> str:
    if not ranked:
        return "refuse"
    top = ranked[0]["rerank_score"]
    if top >= min_top:
        return "pass"
    if top >= partial:
        return "partial"
    return "refuse"


class Retriever:
    """Nạp model 1 lần, dùng lại cho mọi câu hỏi (FastAPI: tạo ở startup)."""

    def __init__(self):
        self.client = get_qdrant()
        self.embedder = OllamaEmbeddings(model=EMBED_MODEL, num_ctx=EMBED_NUM_CTX,
                                         keep_alive=-1)   # giữ model trong RAM
        self.reranker = Qwen3Reranker(max_length=RERANK_MAX_LEN, batch_size=CANDIDATES)

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
