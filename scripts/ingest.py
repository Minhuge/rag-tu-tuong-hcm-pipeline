# Phần 1: Load & Chunking
import re
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

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


def chunk_documents(docs, chunk_size=800, chunk_overlap=100):
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


def extract_chapter(text: str):
    if re.search(r"CH[UƯ]ƠNG\s+MỞ\s*ĐẦU", text, re.IGNORECASE):
        return "Chương Mở đầu"
    match = re.search(r"CH[UƯ]ƠNG\s+([IVXLCDM]+)\b", text, re.IGNORECASE)
    if match:
        return "Chương " + match.group(1).upper()

    return None


def build_chunks_with_metadata(chunks, source_name="Giao_trinh_Tu_tuong_HCM.pdf"):
    result = []
    current_chapter = "Chưa xác định"
    for i, chunk in enumerate(chunks, start=1):
        detected_chapter = extract_chapter(chunk.page_content)
        if detected_chapter is not None:
            current_chapter = detected_chapter
        page = chunk.metadata.get("page")   # PyPDFLoader đánh số từ 0
        result.append({
            "chunk_id": f"chunk_{i:03d}",
            "source": source_name,
            "chapter": current_chapter,
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
import os
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


def create_or_recreate_collection(client: QdrantClient, dim: int, collection: str = COLLECTION):
    """
    Ingest lại từ đầu mỗi lần chạy: số chunk/ID có thể đổi khi sửa clean/chunk,
    giữ collection cũ sẽ để lại point "mồ côi" lẫn vào kết quả search.
    """
    if client.collection_exists(collection):
        client.delete_collection(collection)
        print(f"Đã xoá collection cũ: {collection}")
    client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=dim, distance=DISTANCE),
    )
    # Index payload để lọc theo chương nhanh (tương đương metadata filter của Pinecone).
    client.create_payload_index(collection, field_name="chapter",
                                field_schema=models.PayloadSchemaType.KEYWORD)
    print(f"Đã tạo collection: {collection} (dim={dim}, distance={DISTANCE.value})")


def upsert_chunks(client: QdrantClient, chunks_data: list, batch_size: int = 100,
                  collection: str = COLLECTION):
    """
    chunks_data: list dict có 'chunk_id', 'embedding', 'text', 'source', 'chapter', 'page'
    (kết quả từ embed_chunks ở Bước 2).
    Qdrant chỉ nhận ID dạng số nguyên hoặc UUID → dùng số thứ tự, chunk_id để trong payload.
    """
    points = [
        models.PointStruct(
            id=i,
            vector=c["embedding"],
            payload={k: c[k] for k in ("chunk_id", "source", "chapter", "page", "text")},
        )
        for i, c in enumerate(chunks_data, start=1)
    ]

    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=collection, points=points[i : i + batch_size], wait=True)
        print(f"Đã upsert {min(i + batch_size, len(points))}/{len(points)} points")


if __name__ == "__main__":
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs = load_document(os.path.join(ROOT, "data", "Giao_trinh_Tu_tuong_HCM.pdf"))
    chunks = chunk_documents(docs)
    data = build_chunks_with_metadata(chunks)
    print(f"Tổng số chunk: {len(data)}")

    embedder = get_embedding_model("qwen3-embedding:4b")
    dim = check_vector_dimension(embedder)   # đo thật thay vì hardcode 2560

    data = embed_chunks(embedder, data)

    client = get_qdrant_client()
    create_or_recreate_collection(client, dim)
    upsert_chunks(client, data)
    print(f"Collection '{COLLECTION}' hiện có {client.count(COLLECTION).count} points")

    print("Hoàn tất ingest pipeline!")
