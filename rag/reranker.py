"""
Qwen3-Reranker — chấm lại mức liên quan giữa câu hỏi và từng chunk.

Cơ chế (cross-encoder dạng LLM):
  1. Ghép (Instruct, Query, Document) vào CHUNG một prompt.
  2. Cho qua model 1 lần, lấy logit của token "yes" và "no" tại vị trí tiếp theo.
  3. score = softmax([logit_no, logit_yes])[1] = P(yes) ∈ [0, 1]
     → "xác suất chunk này trả lời được câu hỏi".

Cách format prompt lấy theo model card Qwen/Qwen3-Reranker-0.6B.
Lệch 1 token trong PREFIX/SUFFIX là điểm sẽ sai → không sửa 2 hằng số này.
"""
import os

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

DEFAULT_MODEL = os.getenv("RERANK_MODEL", "Qwen/Qwen3-Reranker-0.6B")

# Instruction viết bằng tiếng Anh (Qwen khuyến nghị) nhưng query/document vẫn là tiếng Việt.
# Câu cuối biến reranker thành "topic guard": câu hỏi ngoài môn học → P(yes) thấp ở mọi chunk.
DEFAULT_INSTRUCT = (
    "Given a Vietnamese question about Ho Chi Minh Thought, judge whether the passage "
    "from the official textbook contains information that directly answers the question. "
    "Answer 'no' if the question is unrelated to the course."
)

PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the "
    "Query and the Instruct provided. Note that the answer can only be \"yes\" or \"no\"."
    "<|im_end|>\n<|im_start|>user\n"
)
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def pick_device(device: str | None = None) -> str:
    """Ưu tiên biến môi trường RERANK_DEVICE (cuda / cpu / mps), không có thì tự chọn."""
    device = device or os.getenv("RERANK_DEVICE")
    if device:
        return device
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class Qwen3Reranker:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str | None = None,
        max_length: int = 1024,     # chunk 800 ký tự ≈ 300-400 token, 1024 là dư
        batch_size: int = 8,        # giảm xuống 4 nếu GPU báo hết VRAM
        instruct: str = DEFAULT_INSTRUCT,
        model=None,                 # truyền sẵn model/tokenizer (dùng khi test)
        tokenizer=None,
    ):
        self.device = pick_device(device)
        self.max_length = max_length
        self.batch_size = batch_size
        self.instruct = instruct

        # padding_side="left": token cuối của MỌI dòng trong batch đều là token thật,
        # nên logits[:, -1, :] luôn là vị trí model sắp sinh "yes"/"no".
        self.tokenizer = tokenizer or AutoTokenizer.from_pretrained(model_name, padding_side="left")
        self.tokenizer.padding_side = "left"

        if model is None:
            dtype = torch.float16 if self.device in ("cuda", "mps") else torch.float32
            model = AutoModelForCausalLM.from_pretrained(model_name, dtype=dtype)
        self.model = model.to(self.device).eval()

        self.yes_id = self.tokenizer.convert_tokens_to_ids("yes")
        self.no_id = self.tokenizer.convert_tokens_to_ids("no")
        self.prefix_ids = self.tokenizer.encode(PREFIX, add_special_tokens=False)
        self.suffix_ids = self.tokenizer.encode(SUFFIX, add_special_tokens=False)

    # ---------- Bước 1: ghép câu hỏi + chunk thành 1 chuỗi ----------
    @staticmethod
    def format_pair(query: str, doc: str, instruct: str) -> str:
        return f"<Instruct>: {instruct}\n<Query>: {query}\n<Document>: {doc}"

    def _encode(self, pairs: list[str]) -> dict:
        # Chỉ cắt phần giữa (instruct+query+doc) để PREFIX/SUFFIX luôn nguyên vẹn.
        body_max = self.max_length - len(self.prefix_ids) - len(self.suffix_ids)
        if body_max <= 0:
            raise ValueError(f"max_length={self.max_length} quá nhỏ, không đủ chỗ cho prompt")
        enc = self.tokenizer(
            pairs, padding=False, truncation="longest_first",
            return_attention_mask=False, max_length=body_max,
        )
        enc["input_ids"] = [self.prefix_ids + ids + self.suffix_ids for ids in enc["input_ids"]]
        batch = self.tokenizer.pad(enc, padding=True, return_tensors="pt")
        return {k: v.to(self.device) for k, v in batch.items()}

    # ---------- Bước 2-3: forward 1 lần, lấy P(yes) ----------
    @torch.no_grad()
    def score(self, query: str, docs: list[str], instruct: str | None = None) -> list[float]:
        instruct = instruct or self.instruct
        scores: list[float] = []
        for i in range(0, len(docs), self.batch_size):
            pairs = [self.format_pair(query, d, instruct) for d in docs[i : i + self.batch_size]]
            inputs = self._encode(pairs)
            # logits_to_keep=1: chỉ tính logit cho token cuối (tiết kiệm VRAM rất nhiều,
            # vì vocab của Qwen3 ~151k token).
            logits = self.model(**inputs, logits_to_keep=1).logits[:, -1, :]
            yes_no = torch.stack([logits[:, self.no_id], logits[:, self.yes_id]], dim=1).float()
            p_yes = torch.log_softmax(yes_no, dim=1)[:, 1].exp()
            scores.extend(p_yes.tolist())
        return scores

    # ---------- Bước 4: sắp xếp lại kết quả Pinecone ----------
    def rerank(self, query: str, matches, top_n: int | None = 4, instruct: str | None = None) -> list[dict]:
        """
        matches: results["matches"] từ Pinecone (mỗi match có 'id', 'score', 'metadata'['text']).
        Trả về list dict đã sắp theo rerank_score giảm dần, giữ lại cả thứ hạng cosine cũ
        để so sánh trước/sau.
        """
        if not matches:
            return []
        texts = [m["metadata"]["text"] for m in matches]
        scores = self.score(query, texts, instruct)

        items = [
            {
                "id": m["id"],
                "cosine": float(m["score"]),
                "cosine_rank": rank,
                "rerank_score": s,
                "metadata": m["metadata"],
            }
            for rank, (m, s) in enumerate(zip(matches, scores), start=1)
        ]
        items.sort(key=lambda x: x["rerank_score"], reverse=True)
        for rank, it in enumerate(items, start=1):
            it["rerank_rank"] = rank
        return items[:top_n] if top_n else items
