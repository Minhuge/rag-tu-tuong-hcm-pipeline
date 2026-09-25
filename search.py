import os
from dotenv import load_dotenv
from pinecone import Pinecone
from langchain_ollama import OllamaEmbeddings

load_dotenv()

INDEX_NAME = "tu-tuong-hcm-index"
MODEL_NAME = "qwen3-embedding:4b"   # PHẢI trùng model đã dùng khi ingest
TOP_K = 3


def get_embedder():
    return OllamaEmbeddings(model=MODEL_NAME)


def get_index():
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("Chưa set PINECONE_API_KEY trong file .env")
    pc = Pinecone(api_key=api_key)
    return pc.Index(INDEX_NAME)


def search(index, embedder, question: str, top_k: int = TOP_K, metadata_filter: dict = None):
    query_vector = embedder.embed_query(question)   # dùng embed_query, không phải embed_documents

    results = index.query(
        vector=query_vector,
        top_k=top_k,
        include_metadata=True,
        filter=metadata_filter,   # None = không lọc, tìm toàn bộ index
    )
    return results


def print_results(question: str, results, metadata_filter: dict = None):
    print("=" * 80)
    print(f"CÂU HỎI: {question}")
    if metadata_filter:
        print(f"METADATA FILTER: {metadata_filter}")
    print("=" * 80)

    for rank, match in enumerate(results["matches"], start=1):
        md = match["metadata"]
        preview = md["text"][:400].replace("\n", " ")
        print(f"\n--- Top {rank} ---")
        print(f"Score      : {match['score']:.4f}")
        print(f"Chunk ID   : {match['id']}")
        print(f"Chapter    : {md.get('chapter', 'N/A')}")
        print(f"Source     : {md.get('source', 'N/A')}")
        print(f"Nội dung   : {preview}...")
    print("\n")


if __name__ == "__main__":
    embedder = get_embedder()
    index = get_index()

    # --- Câu 1: semantic search thuần ---
    q1 = "Nguồn gốc và tiền đề hình thành tư tưởng Hồ Chí Minh là gì?"
    print_results(q1, search(index, embedder, q1))

    # --- Câu 2: semantic search thuần ---
    q2 = "Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?"
    print_results(q2, search(index, embedder, q2))

    # --- Câu 3: có Metadata Filter theo Chương ---
    q3 = "Đối tượng và phương pháp nghiên cứu của môn học là gì?"
    filt = {"chapter": {"$eq": "Chương Mở đầu"}}
    print_results(q3, search(index, embedder, q3, metadata_filter=filt), metadata_filter=filt)