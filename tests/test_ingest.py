"""
Metadata chương/trang khi ingest và lọc theo chương (Phase 2.1). Qdrant chạy trong bộ nhớ, không cần Docker.

Chạy:  pytest tests/test_ingest.py -v
"""
import pytest
from langchain_core.documents import Document
from qdrant_client import QdrantClient, models

from rag import search_rerank
from scripts.ingest import build_chunks_with_metadata, extract_chapter, roman_to_int


@pytest.mark.parametrize("roman, n", [("I", 1), ("II", 2), ("III", 3), ("IV", 4), ("V", 5),
                                      ("VI", 6), ("VII", 7), ("IX", 9), ("XII", 12)])
def test_roman_to_int(roman, n):
    assert roman_to_int(roman) == n


@pytest.mark.parametrize("text, expected", [
    ("CHƯƠNG MỞ ĐẦU ĐỐI TƯỢNG, PHƯƠNG PHÁP NGHIÊN CỨU", ("Chương Mở đầu", 0)),
    ("…kết thúc chương trước. CHƯƠNG III TƯ TƯỞNG HỒ CHÍ MINH VỀ CHỦ NGHĨA XÃ HỘI", ("Chương III", 3)),
    ("CHƯƠNG IV TƯ TƯỞNG HỒ CHÍ MINH", ("Chương IV", 4)),
    ("CHƯƠNG VII TƯ TƯỞNG HỒ CHÍ MINH VỀ VĂN HOÁ", ("Chương VII", 7)),
])
def test_extract_chapter_heading(text, expected):
    assert extract_chapter(text) == expected


@pytest.mark.parametrize("text", [
    "Như chương II đã trình bày, độc lập dân tộc gắn liền với chủ nghĩa xã hội.",   # nhắc lại trong bài
    "Chương II, mục 3 phân tích kỹ hơn.",                                              # không viết hoa
    "chương trình hành động của Đảng",                                                 # không phải số chương
    "Đoạn văn bình thường không có tiêu đề.",
])
def test_extract_chapter_ignores_non_headings(text):
    assert extract_chapter(text) is None


def test_chapter_carries_forward_until_next_heading():
    docs = [Document(page_content=t, metadata={"page": p}) for t, p in [
        ("Lời nói đầu", 0),
        ("CHƯƠNG MỞ ĐẦU ĐỐI TƯỢNG NGHIÊN CỨU", 0),
        ("Như chương II đã nói…", 3),                      # không đổi chương
        ("CHƯƠNG I CƠ SỞ HÌNH THÀNH", 6),
        ("tiếp nội dung chương I", 7),
    ]]
    out = build_chunks_with_metadata(docs)
    assert [(c["chapter"], c["chapter_no"], c["page"]) for c in out] == [
        ("Chưa xác định", None, 1),
        ("Chương Mở đầu", 0, 1),
        ("Chương Mở đầu", 0, 4),
        ("Chương I", 1, 7),
        ("Chương I", 1, 8),                                # trang 1-based
    ]
    assert [c["chunk_id"] for c in out] == ["chunk_001", "chunk_002", "chunk_003", "chunk_004", "chunk_005"]


@pytest.fixture
def qdrant(monkeypatch):
    client = QdrantClient(":memory:")
    monkeypatch.setattr(search_rerank, "COLLECTION", "test_chapters")
    client.create_collection("test_chapters", vectors_config=models.VectorParams(size=2, distance="Cosine"))
    rows = [  # cố tình xếp lộn xộn: load_chapter phải trả theo thứ tự trong sách
        (1, 3, "Chương III", 40, "chunk_201"), (2, 2, "Chương II", 30, "chunk_150"),
        (3, 3, "Chương III", 38, "chunk_180"), (4, 0, "Chương Mở đầu", 2, "chunk_003"),
        (5, 3, "Chương III", 38, "chunk_181"), (6, 4, "Chương IV", 52, "chunk_252"),
    ]
    client.upsert("test_chapters", points=[
        models.PointStruct(id=i, vector=[1.0, 0.0], payload={
            "chunk_id": cid, "chapter": ch, "chapter_no": no, "page": page, "text": f"đoạn {cid}"})
        for i, no, ch, page, cid in rows])
    return client


def test_load_chapter_returns_only_that_chapter_in_book_order(qdrant):
    chunks = search_rerank.load_chapter(qdrant, 3)
    assert [c["chunk_id"] for c in chunks] == ["chunk_180", "chunk_181", "chunk_201"]
    assert all(c["chapter"] == "Chương III" and c["page"] for c in chunks)


def test_load_chapter_zero_is_mo_dau_and_unknown_is_empty(qdrant):
    assert [c["chapter"] for c in search_rerank.load_chapter(qdrant, 0)] == ["Chương Mở đầu"]
    assert search_rerank.load_chapter(qdrant, 9) == []


def test_chapter_filter_accepts_number_or_label(qdrant):
    by_no = qdrant.scroll("test_chapters", scroll_filter=search_rerank.chapter_filter(3))[0]
    by_label = qdrant.scroll("test_chapters", scroll_filter=search_rerank.chapter_filter("Chương III"))[0]
    assert {p.id for p in by_no} == {p.id for p in by_label} == {1, 3, 5}
