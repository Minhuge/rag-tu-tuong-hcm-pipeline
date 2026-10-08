"""
Đánh giá chất lượng câu trả lời bằng RAGAS (Gemini làm giám khảo).

  1. Chạy pipeline thật (GuardedRAG.ask) cho từng câu trong eval_testset.json
  2. Chấm các câu ĐÃ TRẢ LỜI bằng các chỉ số RAGAS, mỗi chỉ số chỉ ra một bộ phận cần sửa:
       faithfulness        (0–1) câu trả lời có bịa ngoài tài liệu không        → thấp: sửa prompt lớp 3 / giám khảo 4a
       answer_relevancy    (0–1) có trả lời đúng câu được hỏi không             → thấp: lạc đề, trả lời lấp lửng
       context_relevance   (0–1) các chunk tìm được có liên quan câu hỏi không  → thấp: sửa retrieval (chunk, embed, rerank, TOP_N)
     Cần "reference" (đáp án chuẩn do người viết) trong testset:
       context_recall      (0–1) chunk tìm được có đủ ý của đáp án chuẩn không  → thấp: retrieval bỏ sót
       factual_correctness (0–1) câu trả lời khớp đáp án chuẩn tới đâu          → thấp: sai so với sự thật
  3. Kiểm tra từ chối: câu "expect": "answer" bị từ chối (bỏ sót) / câu "expect": "refuse" lại được trả lời (lọt)
  4. Lưu eval_runs/<thời điểm>.json kèm cấu hình → sửa pipeline rồi chạy lại để so sánh

Chạy:  python eval_ragas.py                          # toàn bộ testset (nạp model + gọi Gemini)
       python eval_ragas.py --limit 3                # thử nhanh 3 câu đầu
       python eval_ragas.py --rescore eval_runs/X.json   # chấm lại câu trả lời đã lưu, không chạy pipeline

Chi phí: mỗi câu đã trả lời ≈ 1–3 lần gọi Gemini cho pipeline + ~8 lần cho RAGAS (thêm ~5 nếu có reference).
Kết quả gọi giám khảo được cache trong .ragas_cache/ → chấm lại câu không đổi thì không tốn lượt.
Tuỳ chọn .env:  RAGAS_MODEL=gemini-3.6-flash   (giám khảo khác model trả lời → bớt thiên vị "tự chấm mình")
"""
import sys
import types

# ragas 0.4.3 vẫn import ChatVertexAI, đã bị gỡ khỏi langchain-community 0.4 → tạo module giả để import được.
# Không dùng tới VertexAI; bỏ đoạn này khi ragas sửa (https://github.com/vibrantlabsai/ragas).
_vertex = types.ModuleType("langchain_community.chat_models.vertexai")
_vertex.ChatVertexAI = type("ChatVertexAI", (), {})
sys.modules.setdefault(_vertex.__name__, _vertex)

import argparse
import asyncio
import json
import math
import os
from datetime import datetime

import instructor
from google import genai
from google.genai import errors as genai_errors
from ragas.cache import DiskCacheBackend
from ragas.embeddings.base import BaseRagasEmbedding
from ragas.llms.base import InstructorLLM, InstructorModelArgs
from ragas.metrics.collections import (
    AnswerRelevancy, ContextRecall, ContextRelevance, FactualCorrectness, Faithfulness,
)

import search_rerank
from guards import is_refusal
from pipeline import GEMINI_MODEL, GuardedRAG

TESTSET = "eval_testset.json"
RUNS_DIR = "eval_runs"
JUDGE_MODEL = os.getenv("RAGAS_MODEL", GEMINI_MODEL)
REFUSED = {"input-basic", "input-guard", "retrieval"}

HINTS = {
    "faithfulness": "bịa ngoài tài liệu → siết prompt lớp 3, xem giám khảo 4a",
    "answer_relevancy": "trả lời lệch câu hỏi / lấp lửng",
    "context_relevance": "chunk tìm được lạc đề → retrieval (chunk, embed, rerank, TOP_N)",
    "context_recall": "retrieval bỏ sót ý của đáp án chuẩn",
    "factual_correctness": "sai so với đáp án chuẩn",
}


class OllamaEmbedding(BaseRagasEmbedding):
    """Dùng lại embedder Ollama của pipeline (qwen3-embedding, chạy local) → answer_relevancy không tốn quota."""

    def __init__(self, lc_embedder):
        super().__init__()
        self.lc = lc_embedder

    def embed_text(self, text: str, **kwargs) -> list[float]:
        return self.lc.embed_query(text)

    async def aembed_text(self, text: str, **kwargs) -> list[float]:
        return await self.lc.aembed_query(text)


def make_metrics(embedder) -> tuple[dict, dict]:
    # ragas tự bọc genai.Client ở chế độ sync nhưng metric lại gọi async → tự bọc bản async.
    # max_tokens cao vì token "thinking" của Gemini cũng tính vào giới hạn này (1024 mặc định → JSON bị cắt).
    llm = InstructorLLM(
        client=instructor.from_genai(genai.Client(), mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS, use_async=True),
        model=JUDGE_MODEL, provider="google", model_args=InstructorModelArgs(max_tokens=8192),
        cache=DiskCacheBackend(".ragas_cache"))
    relevancy = AnswerRelevancy(llm=llm, embeddings=OllamaEmbedding(embedder))
    # Prompt gốc toàn tiếng Anh → model hay sinh câu hỏi tiếng Anh, so cosine với câu tiếng Việt bị thấp oan.
    relevancy.prompt.instruction += "\nWrite the question in the same language as the answer."
    no_ref = {"faithfulness": Faithfulness(llm=llm), "answer_relevancy": relevancy,
              "context_relevance": ContextRelevance(llm=llm)}
    with_ref = {"context_recall": ContextRecall(llm=llm), "factual_correctness": FactualCorrectness(llm=llm)}
    return no_ref, with_ref


# ---------- bước 1: chạy pipeline ----------
async def run_pipeline(rag: GuardedRAG, items: list[dict]) -> list[dict]:
    rows = []
    for i, it in enumerate(items, 1):
        row = {**it, "status": "error", "answer": "", "contexts": []}
        try:
            r = await rag.ask(it["question"])
            if r.blocked_by in REFUSED or is_refusal(r.answer):
                status = "refused"
            elif r.blocked_by == "output":
                status = "fallback"     # giám khảo 4a bác bỏ → câu trả lời gốc đã bị thay
            else:
                status = "answered"
            row.update(status=status, answer=r.answer, contexts=[s["text"] for s in r.sources],
                       blocked_by=r.blocked_by, retrieval=r.retrieval, retrieval_top=r.retrieval_top,
                       retrieval_top_cos=r.retrieval_top_cos)
        except (RuntimeError, genai_errors.APIError) as e:
            row["error"] = f"{type(e).__name__}: {e}"
        print(f"[{i}/{len(items)}] {row['status']:<9} {it['question'][:70]}")
        rows.append(row)
    return rows


# ---------- bước 2: chấm ----------
async def _one(name, metric, **inputs):
    try:
        return name, float((await metric.ascore(**inputs)).value), None
    except Exception as e:      # 1 chỉ số lỗi (hết quota, JSON hỏng) không làm hỏng cả lượt chấm
        return name, None, f"{type(e).__name__}: {str(e)[:200]}"


async def score(rows: list[dict], no_ref: dict, with_ref: dict):
    todo = [r for r in rows if r["status"] == "answered"]
    for i, r in enumerate(todo, 1):
        q, a, ctx, ref = r["question"], r["answer"], r["contexts"], r.get("reference") or ""
        jobs = [
            _one("faithfulness", no_ref["faithfulness"], user_input=q, response=a, retrieved_contexts=ctx),
            _one("answer_relevancy", no_ref["answer_relevancy"], user_input=q, response=a),
            _one("context_relevance", no_ref["context_relevance"], user_input=q, retrieved_contexts=ctx),
        ]
        if ref:
            jobs += [
                _one("context_recall", with_ref["context_recall"], user_input=q, retrieved_contexts=ctx, reference=ref),
                _one("factual_correctness", with_ref["factual_correctness"], response=a, reference=ref),
            ]
        r["scores"], r["score_errors"] = {}, {}
        for name, value, err in await asyncio.gather(*jobs):
            r["scores"][name] = value
            if err:
                r["score_errors"][name] = err
        shown = "  ".join(f"{k}={v:.2f}" if v is not None else f"{k}=lỗi" for k, v in r["scores"].items())
        print(f"[{i}/{len(todo)}] {shown}  | {q[:50]}")


# ---------- báo cáo ----------
def report(rows: list[dict]):
    print("\n" + "=" * 90)
    answered = [r for r in rows if r["status"] == "answered"]
    print(f"{len(rows)} câu: " + ", ".join(f"{s}={sum(r['status'] == s for r in rows)}"
                                            for s in ("answered", "refused", "fallback", "error")))

    missed = [r for r in rows if r["expect"] == "answer" and r["status"] != "answered"]
    leaked = [r for r in rows if r["expect"] == "refuse" and r["status"] == "answered"]
    print(f"\nBỏ sót (câu giáo trình nhưng không trả lời): {len(missed)}")
    for r in missed:
        print(f"   [{r['status']}/{r.get('blocked_by')}] {r['question'][:70]}")
    print(f"Lọt (câu lạc đề nhưng được trả lời): {len(leaked)}")
    for r in leaked:
        print(f"   {r['question'][:70]}")

    print(f"\n{'chỉ số':<20} {'TB':>5} {'n':>3}   câu thấp nhất")
    for name, hint in HINTS.items():
        vals = [(r["scores"][name], r) for r in answered
                if r.get("scores", {}).get(name) is not None and not math.isnan(r["scores"][name])]
        if not vals:
            print(f"{name:<20} {'—':>5} {0:>3}   (chưa có dữ liệu{' — cần reference' if name in ('context_recall', 'factual_correctness') else ''})")
            continue
        avg = sum(v for v, _ in vals) / len(vals)
        worst_v, worst = min(vals, key=lambda x: x[0])
        print(f"{name:<20} {avg:>5.2f} {len(vals):>3}   {worst_v:.2f} {worst['question'][:45]}")
        if worst_v < 0.7:
            print(f"{'':<31}↳ {hint}")
    errs = sum(len(r.get("score_errors") or {}) for r in rows)
    if errs:
        print(f"\n{errs} lần chấm lỗi — xem 'score_errors' trong file kết quả.")


def config_snapshot() -> dict:
    return {"gemini_model": GEMINI_MODEL, "judge_model": JUDGE_MODEL,
            **{k: getattr(search_rerank, k) for k in ("CANDIDATES", "TOP_N", "MIN_TOP", "MIN_COS",
                                                       "PARTIAL_BAND", "PARTIAL_COS")}}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="chỉ chạy N câu đầu")
    ap.add_argument("--rescore", help="chấm lại file eval_runs/*.json đã lưu, không chạy pipeline")
    args = ap.parse_args()

    if args.rescore:
        with open(args.rescore, encoding="utf-8") as f:
            saved = json.load(f)
        rows, config = saved["rows"], saved["config"]
        rag = None
        embedder = search_rerank.OllamaEmbeddings(model=search_rerank.EMBED_MODEL, num_ctx=search_rerank.EMBED_NUM_CTX)
    else:
        with open(TESTSET, encoding="utf-8") as f:
            items = json.load(f)[: args.limit]
        print(f"Nạp model... (giám khảo: {JUDGE_MODEL})")
        rag = GuardedRAG()
        await asyncio.to_thread(rag.warmup)
        rows, config = await run_pipeline(rag, items), config_snapshot()
        embedder = rag.retriever.embedder

    no_ref, with_ref = make_metrics(embedder)
    print("\nChấm bằng RAGAS...")
    await score(rows, no_ref, with_ref)
    report(rows)

    os.makedirs(RUNS_DIR, exist_ok=True)
    out = os.path.join(RUNS_DIR, datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"config": config, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\nĐã lưu {out}")


if __name__ == "__main__":
    asyncio.run(main())
