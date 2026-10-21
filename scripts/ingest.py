# Phần 1: Load & Chunking
import re
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.bm25 import SPARSE_VECTOR_PARAMS, avg_doc_len, doc_sparse
from rag.chunking import roman_to_int

NOISE_PATTERNS = [
    r"www\.onthisv\.com",
    r"Downloaded by .*?gmail\.com\)?",
    r"Scan to open on Studeersnel",
    r"studeersnel",
    r"lOMoARcPSD\|?\d+",
    r"^\s*\d{1,4}\s*$",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)


def remove_noise_lines(text: str) -> str:
    """Loại các dòng watermark/số trang trước khi text bị hoà lẫn ở bước sau."""
    lines = text.split("\n")
    cleaned = [ln for ln in lines if not NOISE_RE.search(ln)]
    return "\n".join(cleaned)


def clean_text(text: str) -> str:
    text = remove_noise_lines(text)     # 1. lọc watermark/số trang theo dòng
    text = text.replace("\n", "")       # 2. xoá \n artifact giữa các glyph/dấu
    text = re.sub(r"[ \t]+", " ", text) # 3. gộp nhiều space liên tiếp thành 1
    return text.strip()


def load_document(file_path: str):
    if file_path.endswith(".pdf"):
        loader = PyPDFLoader(file_path)
    else:
        from langchain_community.document_loaders import TextLoader
        loader = TextLoader(file_path, encoding="utf-8")
    docs = loader.load()
    for doc in docs:
        doc.page_content = clean_text(doc.page_content)
    return docs


def chunk_documents(docs, chunk_size=800, chunk_overlap=300):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=[
            "\n\nChương ", "\n\nChương",
            "\n\n",
            "\n",
            ". ", "! ", "? ",
            " ", "",
        ],
    )
    return splitter.split_documents(docs)


# Tiêu đề chương trong giáo trình luôn VIẾT HOA ("CHƯƠNG III TƯ TƯỞNG…") → so khớp phân biệt hoa/thường,
# để câu nhắc lại trong bài ("như chương II đã trình bày") không bị nhận nhầm là bắt đầu chương mới.
CHAPTER_HEADING_RE = re.compile(r"CH[UƯ]ƠNG\s+(MỞ\s*ĐẦU|[IVX]+)\b")


def extract_chapter(text: str) -> tuple[str, int] | None:
    """Đoạn có tiêu đề chương → ("Chương III", 3); Chương Mở đầu → ("Chương Mở đầu", 0); không có → None."""
    match = CHAPTER_HEADING_RE.search(text)
    if match is None:
        return None
    token = match.group(1)
    if token.startswith("MỞ"):
        return "Chương Mở đầu", 0
    return "Chương " + token, roman_to_int(token)


def build_chunks_with_metadata(chunks, source_name="Giao_trinh_Tu_tuong_HCM.pdf"):
    result = []
    current_chapter, current_no = "Chưa xác định", None
    for i, chunk in enumerate(chunks, start=1):
        detected = extract_chapter(chunk.page_content)
        if detected is not None:
            current_chapter, current_no = detected
        page = chunk.metadata.get("page")   # PyPDFLoader đánh số từ 0
        result.append({
            "chunk_id": f"chunk_{i:03d}",
            "source": source_name,
            "chapter": current_chapter,
            "chapter_no": current_no,    # số để lọc: 0 = Mở đầu, 1..7 = Chương I..VII
            "page": page + 1 if page is not None else None,   # trang 1-based để trích dẫn
            "text": chunk.page_content,
        })
    return result


#code embedding
from langchain_ollama import OllamaEmbeddings

def get_embedding_model(model_name: str = "qwen3-embedding:4b"):
    return OllamaEmbeddings(model=model_name)


def check_vector_dimension(embedder) -> int:
    sample_vector = embedder.embed_query("kiểm tra dimension")
    dim = len(sample_vector)
    print(f"Vector dimension thực tế: {dim}")
    return dim


def embed_chunks(embedder, chunks_data: list, batch_size: int = 32):
    texts = [c["text"] for c in chunks_data]
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_embeddings = embedder.embed_documents(batch)  # gọi Ollama 1 lần cho cả batch
        all_embeddings.extend(batch_embeddings)
        print(f"Đã embed {min(i + batch_size, len(texts))}/{len(texts)} chunks")

    for chunk, emb in zip(chunks_data, all_embeddings):
        chunk["embedding"] = emb

    return chunks_data


#tạo collection + upsert vào Qdrant
import json
import os
from datetime import datetime

import numpy as np
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

load_dotenv()

COLLECTION = os.getenv("QDRANT_COLLECTION", "tu_tuong_hcm")
DISTANCE = models.Distance.COSINE


def get_qdrant_client() -> QdrantClient:
    path = os.getenv("QDRANT_PATH")
    if path:
        return QdrantClient(path=path)   # chế độ nhúng, không cần Docker
    return QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"),
                        api_key=os.getenv("QDRANT_API_KEY"))


def create_or_recreate_collection(client: QdrantClient, dim: int, collection: str = COLLECTION,
                                  sparse: bool = False):
    """
    Ingest lại từ đầu mỗi lần chạy: số chunk/ID có thể đổi khi sửa clean/chunk,
    giữ collection cũ sẽ để lại point "mồ côi" lẫn vào kết quả search.
    sparse=True: thêm sparse vector "bm25" (modifier IDF, xem rag/bm25.py) cho hybrid search. Vector dense vẫn KHÔNG
    đặt tên: search dense hiện tại (query_points không truyền using) chạy được trên cả collection cũ lẫn mới.
    """
    if client.collection_exists(collection):
        client.delete_collection(collection)
        print(f"Đã xoá collection cũ: {collection}")
    client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=dim, distance=DISTANCE),
        sparse_vectors_config={"bm25": SPARSE_VECTOR_PARAMS} if sparse else None,
    )
    # Index payload để lọc theo chương nhanh (tương đương metadata filter của Pinecone).
    client.create_payload_index(collection, field_name="chapter",
                                field_schema=models.PayloadSchemaType.KEYWORD)
    client.create_payload_index(collection, field_name="chapter_no",
                                field_schema=models.PayloadSchemaType.INTEGER)
    client.create_payload_index(collection, field_name="section",
                                field_schema=models.PayloadSchemaType.KEYWORD)
    print(f"Đã tạo collection: {collection} (dim={dim}, distance={DISTANCE.value}{', + bm25' if sparse else ''})")


def upsert_chunks(client: QdrantClient, chunks_data: list, batch_size: int = 100,
                  collection: str = COLLECTION):
    """
    chunks_data: list dict có 'embedding' + các field payload ('chunk_id', 'text', 'source', 'chapter', 'chapter_no',
    'page'; chunking v2 thêm 'section', 'page_start', 'page_end', 'chunk_method'), tuỳ chọn 'bm25' (sparse vector,
    add_bm25). Mọi key trừ 'embedding' / 'bm25' vào payload.
    Qdrant chỉ nhận ID dạng số nguyên hoặc UUID → dùng số thứ tự, chunk_id để trong payload.
    """
    points = [
        models.PointStruct(
            id=i,
            # "" = vector dense không tên (cùng tên mặc định với collection cũ)
            vector={"": c["embedding"], "bm25": c["bm25"]} if "bm25" in c else c["embedding"],
            payload={k: v for k, v in c.items() if k not in ("embedding", "bm25")},
        )
        for i, c in enumerate(chunks_data, start=1)
    ]

    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=collection, points=points[i : i + batch_size], wait=True)
        print(f"Đã upsert {min(i + batch_size, len(points))}/{len(points)} points")


def add_bm25(chunks_data: list) -> float:
    """Thêm sparse vector BM25 (key 'bm25') tính trên payload 'text' của từng chunk; trả về avgdl.
    Dùng 'text' chứ không dùng embed_text: tiền tố đường dẫn mục lặp lại ở mọi chunk cùng mục, làm lệch tần suất từ."""
    avgdl = avg_doc_len([c["text"] for c in chunks_data])
    for c in chunks_data:
        c["bm25"] = doc_sparse(c["text"], avgdl)
    print(f"Đã tính BM25 cho {len(chunks_data)} chunks (avgdl={avgdl:.1f} token)")
    return avgdl


# ---------- chunking v2 (CHUNKING_PLAN.md mục 4.4) ----------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_PATH = os.path.join(ROOT, "data", "Giao_trinh_Tu_tuong_HCM.pdf")
CHUNK_RUNS_DIR = os.path.join(ROOT, "data", "chunk_runs")
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding:4b")
EMBED_NUM_CTX = int(os.getenv("EMBED_NUM_CTX", "1024"))
# Collection baseline (Recursive 800/300) là mốc so sánh của thí nghiệm → chunking v2 không được ghi đè lên nó.
PROTECTED_COLLECTIONS = {"tu_tuong_hcm"}


def count_tokens(texts: list[str]) -> list[int]:
    """Số token thật theo tokenizer Qwen3 (cùng bộ từ vựng với qwen3-embedding; lệch Ollama ~1 token đặc biệt).
    Dùng tokenizer của reranker vì nó đã có sẵn trong cache Hugging Face."""
    from transformers import AutoTokenizer

    from rag.reranker import DEFAULT_MODEL
    tok = AutoTokenizer.from_pretrained(DEFAULT_MODEL)
    return [len(ids) for ids in tok(texts, add_special_tokens=False)["input_ids"]]


def ingest_v2(args) -> None:
    from rag.chunking import build_chunks, detect_sections, load_full_text, load_sent_cache, save_sent_cache

    full_text, page_map = load_full_text(PDF_PATH)
    sections = detect_sections(full_text)
    embedder = OllamaEmbeddings(model=EMBED_MODEL, num_ctx=EMBED_NUM_CTX)

    sent_cache, cache_path = None, None
    if args.strategy == "hier_semantic":
        model_tag = re.sub(r"[^A-Za-z0-9._-]+", "-", EMBED_MODEL)
        cache_path = os.path.join(CHUNK_RUNS_DIR, f"sent_cache_{model_tag}.npz")
        sent_cache = load_sent_cache(cache_path)
        print(f"Cache embedding câu: {len(sent_cache):,} cửa sổ có sẵn")

    chunks, stats = build_chunks(full_text, page_map, sections, args.max_size, args.min_size, args.strategy,
                                 args.semantic_pct, embedder.embed_documents, sent_cache, id_prefix=args.collection)
    if sent_cache is not None:
        save_sent_cache(cache_path, sent_cache)
    print(f"Tổng số chunk: {len(chunks)}  ({stats['by_method']}, semantic_thr={stats['semantic_thr']})")

    # Giới hạn cứng (mục 3): Ollama với truncate mặc định sẽ CẮT NGẦM embed_text dài hơn num_ctx.
    embed_tokens = count_tokens([c.embed_text for c in chunks])
    text_tokens = count_tokens([c.text for c in chunks])
    over = [c.payload["chunk_id"] for c, n in zip(chunks, embed_tokens) if n > EMBED_NUM_CTX]
    if over:
        print(f"⚠️  {len(over)} embed_text vượt EMBED_NUM_CTX={EMBED_NUM_CTX} token, Ollama sẽ cắt phần cuối: {over[:5]}")
    print(f"Token lớn nhất: embed_text {max(embed_tokens)}, text {max(text_tokens)} (EMBED_NUM_CTX={EMBED_NUM_CTX})")

    # Embed embed_text ("[đường dẫn mục]\ntext"); payload "text" giữ nguyên văn gốc.
    data = [{**c.payload, "embed_text": c.embed_text} for c in chunks]
    vectors = []
    for i in range(0, len(data), 32):
        vectors.extend(embedder.embed_documents([d["embed_text"] for d in data[i:i + 32]]))
        print(f"Đã embed {min(i + 32, len(data))}/{len(data)} chunks")
    for d, v in zip(data, vectors):
        del d["embed_text"]
        d["embedding"] = v

    avgdl = add_bm25(data)

    client = get_qdrant_client()
    create_or_recreate_collection(client, len(vectors[0]), args.collection, sparse=True)
    upsert_chunks(client, data, collection=args.collection)
    n_points = client.count(args.collection).count
    print(f"Collection '{args.collection}' hiện có {n_points} points")

    summary = {
        "collection": args.collection,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "embed_model": EMBED_MODEL,
        "embed_num_ctx": EMBED_NUM_CTX,
        "n_points": n_points,
        **stats,
        "embed_text_max_tokens": max(embed_tokens),
        "embed_text_tokens_p95": int(np.percentile(embed_tokens, 95)),
        "text_max_tokens": max(text_tokens),
        "n_embed_text_over_ctx": len(over),
        "bm25_avgdl": round(avgdl, 2),
    }
    os.makedirs(CHUNK_RUNS_DIR, exist_ok=True)
    out = os.path.join(CHUNK_RUNS_DIR, f"{args.collection}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"Đã ghi {out}")


def ingest_legacy(collection: str) -> None:
    """Pipeline cũ: PyPDFLoader + clean_text + Recursive 800/300 (collection baseline)."""
    docs = load_document(PDF_PATH)
    chunks = chunk_documents(docs)
    data = build_chunks_with_metadata(chunks)
    print(f"Tổng số chunk: {len(data)}")

    embedder = get_embedding_model(EMBED_MODEL)
    dim = check_vector_dimension(embedder)   # đo thật thay vì hardcode 2560

    data = embed_chunks(embedder, data)
    add_bm25(data)

    client = get_qdrant_client()
    create_or_recreate_collection(client, dim, collection, sparse=True)
    upsert_chunks(client, data, collection=collection)
    print(f"Collection '{collection}' hiện có {client.count(collection).count} points")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Ingest giáo trình vào Qdrant (xoá và tạo lại collection đích).",
                                 epilog="Ví dụ: python -m scripts.ingest --max-size 1200 --collection tu_tuong_hcm_ms1200")
    ap.add_argument("--strategy", choices=("hier_semantic", "recursive", "legacy"), default="hier_semantic",
                    help="hier_semantic: chunking v2 bước 0.5 + 1–5 | recursive: v2 bỏ bước semantic | "
                         "legacy: pipeline cũ Recursive 800/300")
    ap.add_argument("--max-size", type=int, default=1200, help="độ dài tối đa của chunk (ký tự)")
    ap.add_argument("--min-size", type=int, default=None, help="mặc định max_size // 4")
    ap.add_argument("--semantic-pct", type=float, default=90, help="phân vị khoảng cách để tính semantic_thr")
    ap.add_argument("--collection", default=None,
                    help="collection đích (bắt buộc với chunking v2; legacy mặc định QDRANT_COLLECTION)")
    args = ap.parse_args()

    if args.strategy == "legacy":
        ingest_legacy(args.collection or COLLECTION)
    else:
        if not args.collection:
            ap.error("chunking v2 cần --collection, ví dụ --collection tu_tuong_hcm_ms1200")
        if args.collection in PROTECTED_COLLECTIONS:
            ap.error(f"'{args.collection}' là collection baseline, không ghi đè; chọn tên khác")
        ingest_v2(args)
    print("Hoàn tất ingest pipeline!")


if __name__ == "__main__":
    main()
