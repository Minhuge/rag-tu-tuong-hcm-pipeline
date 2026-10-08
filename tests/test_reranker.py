"""
Test cho reranker.py.

  - Unit test: tokenizer thật + model GIẢ (logit yes/no do ta quyết định)
    → kiểm tra format prompt, cắt/pad, công thức P(yes), sắp xếp, batch.
  - Test tích hợp: model Qwen3-Reranker thật → đoạn liên quan phải điểm cao hơn đoạn lạc đề.

Chạy:  pytest -q tests/test_reranker.py
       pytest -q tests/test_reranker.py -m "not slow"     # bỏ test nạp model thật
"""
import math

import pytest
import torch
from transformers import AutoTokenizer

from rag.reranker import DEFAULT_MODEL, PREFIX, SUFFIX, Qwen3Reranker, pick_device

# Token đặc biệt luôn được tokenizer giữ nguyên thành 1 token → dùng làm "dấu hiệu liên quan".
MARKER = "<|object_ref_start|>"


@pytest.fixture(scope="module")
def tokenizer():
    try:
        return AutoTokenizer.from_pretrained(DEFAULT_MODEL, padding_side="left")
    except OSError as e:   # chưa tải được tokenizer (offline lần đầu)
        pytest.skip(f"Không tải được tokenizer {DEFAULT_MODEL}: {e}")


class FakeLM(torch.nn.Module):
    """
    Logit "yes" = 2 × (số MARKER trong dòng), logit "no" = 1, các token khác = 0.
    → P(yes) = sigmoid(2k − 1): 0 marker ≈ 0.269, 1 marker ≈ 0.731, 2 marker ≈ 0.953.
    Ghi lại input để test kiểm tra cách encode.
    """

    def __init__(self, vocab: int, yes_id: int, no_id: int, marker_id: int):
        super().__init__()
        self.vocab, self.yes_id, self.no_id, self.marker_id = vocab, yes_id, no_id, marker_id
        self.calls = []

    def forward(self, input_ids, attention_mask=None, logits_to_keep=0, **_):
        self.calls.append({"input_ids": input_ids, "attention_mask": attention_mask,
                           "logits_to_keep": logits_to_keep})
        # Chỉ dựng logit cho vị trí cuối (vocab ~151k, dựng đủ n vị trí rất tốn RAM).
        logits = torch.zeros(input_ids.shape[0], 1, self.vocab)
        k = (input_ids == self.marker_id).sum(dim=1).float()
        logits[:, -1, self.yes_id] = 2 * k
        logits[:, -1, self.no_id] = 1.0

        class Out:
            pass

        out = Out()
        out.logits = logits
        return out


def expected(k: int) -> float:
    return 1 / (1 + math.exp(-(2 * k - 1)))


@pytest.fixture
def rr(tokenizer):
    yes_id = tokenizer.convert_tokens_to_ids("yes")
    no_id = tokenizer.convert_tokens_to_ids("no")
    marker_id = tokenizer.convert_tokens_to_ids(MARKER)
    model = FakeLM(len(tokenizer), yes_id, no_id, marker_id)
    return Qwen3Reranker(device="cpu", max_length=128, batch_size=2, model=model, tokenizer=tokenizer)


# ---------------------------------------------------------------------
# pick_device
# ---------------------------------------------------------------------
def test_pick_device_explicit_wins(monkeypatch):
    monkeypatch.setenv("RERANK_DEVICE", "cuda")
    assert pick_device("cpu") == "cpu"


def test_pick_device_env(monkeypatch):
    monkeypatch.setenv("RERANK_DEVICE", "cpu")
    assert pick_device() == "cpu"


# ---------------------------------------------------------------------
# format + encode
# ---------------------------------------------------------------------
def test_format_pair():
    s = Qwen3Reranker.format_pair("câu hỏi", "đoạn văn", "hướng dẫn")
    assert s == "<Instruct>: hướng dẫn\n<Query>: câu hỏi\n<Document>: đoạn văn"


def test_yes_no_are_single_tokens(rr, tokenizer):
    assert rr.yes_id != tokenizer.unk_token_id and rr.no_id != tokenizer.unk_token_id
    assert tokenizer.decode([rr.yes_id]) == "yes" and tokenizer.decode([rr.no_id]) == "no"


def test_encode_keeps_prefix_suffix_and_truncates(rr):
    long_doc = "Hồ Chí Minh " * 500
    batch = rr._encode([rr.format_pair("q", long_doc, "i"), rr.format_pair("q", "ngắn", "i")])
    ids, mask = batch["input_ids"], batch["attention_mask"]
    assert ids.shape[1] <= rr.max_length

    n_pre, n_suf = len(rr.prefix_ids), len(rr.suffix_ids)
    for row, m in zip(ids.tolist(), mask.tolist()):
        real = [t for t, keep in zip(row, m) if keep]
        assert real[:n_pre] == rr.prefix_ids          # PREFIX nguyên vẹn
        assert real[-n_suf:] == rr.suffix_ids         # SUFFIX nguyên vẹn dù doc bị cắt


def test_encode_pads_left(rr):
    batch = rr._encode([rr.format_pair("q", "dài " * 30, "i"), rr.format_pair("q", "x", "i")])
    mask = batch["attention_mask"]
    assert mask[:, -1].all()          # token cuối mọi dòng là token thật
    assert mask[1, 0] == 0            # dòng ngắn được pad ở bên TRÁI


def test_prefix_suffix_constants_untouched():
    assert PREFIX.endswith("<|im_start|>user\n")
    assert SUFFIX == "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def test_encode_rejects_tiny_max_length(tokenizer):
    r = Qwen3Reranker(device="cpu", max_length=10, model=torch.nn.Linear(1, 1), tokenizer=tokenizer)
    with pytest.raises(ValueError):
        r._encode(["abc"])


# ---------------------------------------------------------------------
# score
# ---------------------------------------------------------------------
def test_score_is_p_yes(rr):
    docs = ["không có gì", f"có {MARKER}", f"{MARKER} hai {MARKER}"]
    scores = rr.score("câu hỏi", docs)
    assert scores == pytest.approx([expected(0), expected(1), expected(2)], abs=1e-5)
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_score_uses_logits_to_keep_and_batches(rr):
    rr.score("q", ["a", "b", "c", "d", "e"])
    assert [c["input_ids"].shape[0] for c in rr.model.calls] == [2, 2, 1]   # batch_size=2
    assert all(c["logits_to_keep"] == 1 for c in rr.model.calls)


def test_score_independent_of_batch_size(rr):
    docs = [f"đoạn {i} " * (i + 1) + (MARKER if i % 2 else "") for i in range(5)]
    s_batch = rr.score("q", docs)
    rr.batch_size = 1
    assert rr.score("q", docs) == pytest.approx(s_batch, abs=1e-5)


def test_score_empty(rr):
    assert rr.score("q", []) == []


# ---------------------------------------------------------------------
# rerank
# ---------------------------------------------------------------------
def _matches(texts):
    # cosine giảm dần như Qdrant trả về
    return [{"id": f"chunk_{i}", "score": 0.9 - 0.1 * i, "metadata": {"text": t}} for i, t in enumerate(texts)]


def test_rerank_sorts_and_tracks_ranks(rr):
    ranked = rr.rerank("q", _matches(["lạc đề", "cũng lạc đề", f"đúng {MARKER}"]), top_n=None)
    assert [it["id"] for it in ranked] == ["chunk_2", "chunk_0", "chunk_1"]
    top = ranked[0]
    assert top["cosine_rank"] == 3 and top["rerank_rank"] == 1
    assert top["rerank_score"] == pytest.approx(expected(1), abs=1e-5)
    assert top["cosine"] == pytest.approx(0.7)
    assert [it["rerank_rank"] for it in ranked] == [1, 2, 3]


def test_rerank_top_n(rr):
    ranked = rr.rerank("q", _matches(["a", f"b {MARKER}", "c", f"d {MARKER}{MARKER}"]), top_n=2)
    assert [it["id"] for it in ranked] == ["chunk_3", "chunk_1"]


def test_rerank_empty(rr):
    assert rr.rerank("q", []) == []


# ---------------------------------------------------------------------
# Tích hợp: model thật
# ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def real_rr():
    try:
        return Qwen3Reranker(max_length=512, batch_size=4)
    except OSError as e:
        pytest.skip(f"Không tải được {DEFAULT_MODEL}: {e}")


RELEVANT = ("Hồ Chí Minh khẳng định đại đoàn kết toàn dân tộc là vấn đề có ý nghĩa chiến lược, "
            "quyết định thành công của cách mạng. Đoàn kết, đoàn kết, đại đoàn kết; "
            "thành công, thành công, đại thành công.")
IRRELEVANT = "Để nấu phở bò ngon cần ninh xương trong nhiều giờ và thêm quế, hồi, gừng nướng."


@pytest.mark.slow
def test_real_model_prefers_relevant(real_rr):
    q = "Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?"
    s_rel, s_irr = real_rr.score(q, [RELEVANT, IRRELEVANT])
    assert s_rel > 0.5 > s_irr
    ranked = real_rr.rerank(q, _matches([IRRELEVANT, RELEVANT]), top_n=None)
    assert ranked[0]["metadata"]["text"] == RELEVANT


@pytest.mark.slow
def test_real_model_off_topic_question_scores_low(real_rr):
    s = real_rr.score("Giá vàng hôm nay bao nhiêu?", [RELEVANT])[0]
    assert s < 0.3
