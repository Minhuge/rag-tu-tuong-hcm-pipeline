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
        result.append({
            "chunk_id": f"chunk_{i:03d}",
            "source": source_name,
            "chapter": current_chapter,   
            "text": chunk.page_content,
        })
    return result


#if __name__ == "__main__":
#    docs = load_document("Giao_trinh_Tu_tuong_HCM.pdf")
#    chunks = chunk_documents(docs)
#    data = build_chunks_with_metadata(chunks)
#    print(f"Tổng số chunk: {len(data)}")
#    print(data[500])

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


#tạo index + upsert vào Pinecone
import os
import time
from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec

load_dotenv()

INDEX_NAME = "tu-tuong-hcm-index"
VECTOR_DIM = 2560        
METRIC = "cosine"


def get_pinecone_client():
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("Chưa set PINECONE_API_KEY trong file .env")
    return Pinecone(api_key=api_key)


def create_or_get_index(pc: Pinecone, index_name: str = INDEX_NAME):
    existing_indexes = [idx["name"] for idx in pc.list_indexes()]

    if index_name not in existing_indexes:
        pc.create_index(
            name=index_name,
            dimension=VECTOR_DIM,
            metric=METRIC,
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        while not pc.describe_index(index_name).status["ready"]:
            time.sleep(1)
        print(f"Đã tạo index mới: {index_name}")
    else:
        print(f"Index '{index_name}' đã tồn tại, dùng lại.")

    return pc.Index(index_name)


def upsert_chunks(index, chunks_data: list, batch_size: int = 100):
    """
    chunks_data: list dict có 'chunk_id', 'embedding', 'text', 'source', 'chapter'
    (kết quả từ embed_chunks ở Bước 2).
    """
    vectors = []
    for c in chunks_data:
        vectors.append({
            "id": c["chunk_id"],
            "values": c["embedding"],
            "metadata": {
                "source": c["source"],
                "chapter": c["chapter"],
                "text": c["text"],   
            },
        })

    for i in range(0, len(vectors), batch_size):
        batch = vectors[i : i + batch_size]
        index.upsert(vectors=batch)
        print(f"Đã upsert {min(i + batch_size, len(vectors))}/{len(vectors)} vectors")


if __name__ == "__main__":
    docs = load_document("Giao_trinh_Tu_tuong_HCM.pdf")
    chunks = chunk_documents(docs)
    data = build_chunks_with_metadata(chunks)
    print(f"Tổng số chunk: {len(data)}")

    embedder = get_embedding_model("qwen3-embedding:4b")
    check_vector_dimension(embedder)

    data = embed_chunks(embedder, data)

    pc = get_pinecone_client()
    index = create_or_get_index(pc)
    upsert_chunks(index, data)

    print("Hoàn tất ingest pipeline!")


