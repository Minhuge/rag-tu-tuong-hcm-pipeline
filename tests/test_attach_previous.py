"""
Test ghép chunk liền trước (rag/search_rerank.attach_previous): chunk mở đầu bằng "Đây là…", "Do đó…" thì ý được nói tới
nằm cuối chunk trước. Qdrant giả, không nạp model, không gọi Gemini.

Chạy:  pytest -q tests/test_attach_previous.py
"""
import unicodedata

from rag import search_rerank as sr

CHUNKS = {   # rút gọn từ giáo trình thật (chunk_155 / chunk_156, Chương II trang 33)
    "chunk_155": {"chunk_id": "chunk_155", "chapter": "Chương II", "page": 33,
                  "text": ". …cách mạng giải phóng dân tộc ở thuộc địa có thể giành thắng lợi trước cách mạng vô sản ở "
                          "chính quốc"},
    "chunk_156": {"chunk_id": "chunk_156", "chapter": "Chương II", "page": 33,
                  "text": ". Đây là một luận điểm sáng tạo, có giá trị lý luận và thực tiễn to lớn"},
    "chunk_099": {"chunk_id": "chunk_099", "chapter": "Chương I", "page": 20, "text": "Cuối chương I."},
    "chunk_100": {"chunk_id": "chunk_100", "chapter": "Chương II", "page": 21, "text": "Do đó, ..."},
}


class FakeQdrant:
    def __init__(self):
        self.asked = []

    def scroll(self, collection, limit, with_payload, scroll_filter):
        chunk_id = scroll_filter.must[0].match.value
        self.asked.append(chunk_id)
        return ([type("P", (), {"payload": CHUNKS[chunk_id]})()] if chunk_id in CHUNKS else []), None


def hit(chunk_id, score=0.99, cos=0.7):
    return {"id": chunk_id, "metadata": CHUNKS.get(chunk_id) or {"chunk_id": chunk_id, "chapter": "Chương II",
                                                                  "text": "Hồ Chí Minh…"},
            "rerank_score": score, "cosine": cos}


def test_refers_back_handles_pdf_text_and_common_openings():
    decomposed = unicodedata.normalize("NFD", ". Đây là một luận điểm")       # PDF lưu dấu tách rời
    assert sr.refers_back(decomposed)
    for text in ("Do đó, Người…", "  Như vậy, …", "Đó là con đường…", "Chính vì vậy…", "…Vì vậy, …"):
        assert sr.refers_back(text), text
    for text in ("Hồ Chí Minh cho rằng…", "Đối với Người…", "Đâylà", "Theo Hồ Chí Minh, đây là…"):
        assert not sr.refers_back(text), text


def test_previous_chunk_id():
    assert sr.previous_chunk_id("chunk_156") == "chunk_155"
    assert sr.previous_chunk_id("chunk_100") == "chunk_099"           # giữ số chữ số
    assert sr.previous_chunk_id("chunk_001") is None and sr.previous_chunk_id("abc") is None


def test_previous_chunk_is_inserted_before_with_the_same_scores():
    q = FakeQdrant()
    ranked = sr.attach_previous(q, [hit("chunk_156", 0.98, 0.74), hit("chunk_200", 0.95)])
    assert [it["id"] for it in ranked] == ["chunk_155", "chunk_156", "chunk_200"]
    added = ranked[0]
    assert added["context_for"] == "chunk_156" and "giành thắng lợi trước" in added["metadata"]["text"]
    # đứng đầu nhưng mang điểm của chunk cần nó → retrieval guard cho kết quả như khi chưa ghép
    assert (added["rerank_score"], added["cosine"]) == (0.98, 0.74)
    assert sr.retrieval_guard(ranked) == sr.retrieval_guard([hit("chunk_156", 0.98, 0.74)])


def test_no_extra_lookup_when_not_needed_and_no_duplicates_or_cross_chapter():
    q = FakeQdrant()
    plain = [hit("chunk_200"), hit("chunk_201")]
    assert sr.attach_previous(q, plain) == plain and q.asked == []                      # không chunk nào "Đây là…"
    both = [hit("chunk_156"), hit("chunk_155")]                                        # chunk trước đã có sẵn
    assert [it["id"] for it in sr.attach_previous(q, both)] == ["chunk_156", "chunk_155"]
    other_chapter = [hit("chunk_100")]                                                # chunk_099 thuộc Chương I
    assert [it["id"] for it in sr.attach_previous(q, other_chapter)] == ["chunk_100"]
