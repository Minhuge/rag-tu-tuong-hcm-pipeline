from langchain_ollama import OllamaEmbeddings

from search_rerank import COLLECTION, chapter_filter, get_qdrant, qdrant_search

MODEL_NAME = "qwen3-embedding:4b"   # PHẢI trùng model đã dùng khi ingest
TOP_K = 3


def get_embedder():
    return OllamaEmbeddings(model=MODEL_NAME)


def search(client, embedder, question: str, top_k: int = TOP_K, chapter: str = None):
    # qdrant_search dùng embed_query (không phải embed_documents); chapter=None → tìm toàn bộ collection
    return qdrant_search(client, embedder, question, limit=top_k,
                         query_filter=chapter_filter(chapter) if chapter else None)


def print_results(question: str, matches: list[dict], chapter: str = None):
    print("=" * 80)
    print(f"CÂU HỎI: {question}")
    if chapter:
        print(f"METADATA FILTER: chapter = {chapter}")
    print("=" * 80)

    for rank, match in enumerate(matches, start=1):
        md = match["metadata"]
        preview = md["text"][:400].replace("\n", " ")
        print(f"\n--- Top {rank} ---")
        print(f"Score      : {match['score']:.4f}")
        print(f"Chunk ID   : {match['id']}")
        print(f"Chapter    : {md.get('chapter', 'N/A')}")
        print(f"Page       : {md.get('page', 'N/A')}")
        print(f"Source     : {md.get('source', 'N/A')}")
        print(f"Nội dung   : {preview}...")
    print("\n")


if __name__ == "__main__":
    embedder = get_embedder()
    client = get_qdrant()
    print(f"Qdrant collection: {COLLECTION}")

    # --- Câu 1: semantic search thuần ---
    q1 = "Nguồn gốc và tiền đề hình thành tư tưởng Hồ Chí Minh là gì?"
    print_results(q1, search(client, embedder, q1))

    # --- Câu 2: semantic search thuần ---
    q2 = "Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?"
    print_results(q2, search(client, embedder, q2))

    # --- Câu 3: có Metadata Filter theo Chương ---
    q3 = "Đối tượng và phương pháp nghiên cứu của môn học là gì?"
    chap = "Chương Mở đầu"
    print_results(q3, search(client, embedder, q3, chapter=chap), chapter=chap)
