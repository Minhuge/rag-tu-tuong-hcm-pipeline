"""
Đánh giá chất lượng câu trả lời bằng RAGAS (Gemini làm giám khảo).

  1. Chạy pipeline thật (GuardedRAG.ask) cho từng câu trong data/eval_testset.json
  2. Chấm các câu ĐÃ TRẢ LỜI bằng các chỉ số RAGAS, mỗi chỉ số chỉ ra một bộ phận cần sửa:
       faithfulness        (0–1) câu trả lời có bịa ngoài tài liệu không        → thấp: sửa prompt lớp 3 / giám khảo 4a
       answer_relevancy    (0–1) có trả lời đúng câu được hỏi không             → thấp: lạc đề, trả lời lấp lửng
       context_relevance   (0–1) các chunk tìm được có liên quan câu hỏi không  → thấp: sửa retrieval (chunk, embed, rerank, TOP_N)
     Cần "reference" (đáp án chuẩn do người viết) trong testset:
       context_recall      (0–1) chunk tìm được có đủ ý của đáp án chuẩn không  → thấp: retrieval bỏ sót
       answer_accuracy     (0–1) câu trả lời có cùng ý với đáp án chuẩn không   → thấp: trả lời sai
                           (2 giám khảo chấm rồi lấy trung bình; thay factual_correctness — chấm theo từng ý của câu
                           trả lời nên đáp án chuẩn 1 câu (đáp án trắc nghiệm) gần như luôn ra 0 dù trả lời đúng)
  3. Kiểm tra từ chối: câu "expect": "answer" bị từ chối (bỏ sót) / câu "expect": "refuse" lại được trả lời (lọt)
  4. Lưu data/eval_runs/<thời điểm>.json kèm cấu hình → sửa pipeline rồi chạy lại để so sánh

Chạy:  python -m scripts.eval_ragas                          # toàn bộ testset (nạp model + gọi Gemini)
       python -m scripts.eval_ragas --limit 3                # thử nhanh 3 câu đầu
       python -m scripts.eval_ragas --rescore data/eval_runs/<ngày-giờ>.json   # vd. 20261014-093503.json: chấm lại
                                                      #   câu trả lời đã lưu, không chạy pipeline
       python -m scripts.eval_ragas --rerun data/eval_runs/<ngày-giờ>.json --metric faithfulness --below 0.6
                                                      # sửa xong → chạy LẠI pipeline chỉ cho các câu có
                                                      #   faithfulness < 0.6 ở lần chạy đó (lấy câu trong testset hiện tại)

Chi phí: mỗi câu đã trả lời ≈ 1–3 lần gọi Gemini cho pipeline + ~8 lần cho RAGAS (thêm ~3 nếu có reference).
Kết quả gọi giám khảo được cache trong .ragas_cache/ → chấm lại câu không đổi thì không tốn lượt.
Mỗi đoạn giáo trình đưa giám khảo kèm nhãn [Chương X, trang Y] (labeled) → trích dẫn trong câu trả lời kiểm được.
Câu mở đầu cố định do pipeline thêm ("Giáo trình chỉ đề cập một phần…") bị bỏ trước khi chấm: không phải ý về nội dung,
giám khảo hay tính là "ý không có trong tài liệu" → faithfulness thấp oan. File kết quả vẫn lưu câu trả lời gốc.
Tuỳ chọn .env:  RAGAS_MODEL=gemini-3.6-flash   (giám khảo khác model trả lời → bớt thiên vị "tự chấm mình")
"""
from scripts.ragas_judge import JUDGE_MODEL, judge_llm  # noqa: I001 — trước mọi import ragas (vá lỗi import)

import argparse
import asyncio
import json
import math
import os
from datetime import datetime

from google.genai import errors as genai_errors
from ragas.embeddings.base import BaseRagasEmbedding
from ragas.metrics.collections import (
    AnswerAccuracy, AnswerRelevancy, ContextRecall, ContextRelevance, Faithfulness,
)

from rag import search_rerank
from rag.guards import PARTIAL_TEXT, is_refusal
from rag.pipeline import GEMINI_MODEL, GuardedRAG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTSET = os.path.join(ROOT, "data", "eval_testset.json")
RUNS_DIR = os.path.join(ROOT, "data", "eval_runs")
REFUSED = {"input-basic", "input-guard", "retrieval"}

HINTS = {
    "faithfulness": "bịa ngoài tài liệu → siết prompt lớp 3, xem giám khảo 4a",
    "answer_relevancy": "trả lời lệch câu hỏi / lấp lửng",
    "context_relevance": "chunk tìm được lạc đề → retrieval (chunk, embed, rerank, TOP_N)",
    "context_recall": "retrieval bỏ sót ý của đáp án chuẩn",
    "answer_accuracy": "trả lời sai so với đáp án chuẩn",
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
    llm = judge_llm()
    relevancy = AnswerRelevancy(llm=llm, embeddings=OllamaEmbedding(embedder))
    # Prompt gốc toàn tiếng Anh → model hay sinh câu hỏi tiếng Anh, so cosine với câu tiếng Việt bị thấp oan.
    relevancy.prompt.instruction += "\nWrite the question in the same language as the answer."
    no_ref = {"faithfulness": Faithfulness(llm=llm), "answer_relevancy": relevancy,
              "context_relevance": ContextRelevance(llm=llm)}
    with_ref = {"context_recall": ContextRecall(llm=llm), "answer_accuracy": AnswerAccuracy(llm=llm)}
    return no_ref, with_ref


# ---------- bước 1: chạy pipeline ----------
def labeled(source: dict) -> str:
    """Đoạn giáo trình kèm nhãn "[Chương III, trang 46]" như Gemini thấy (source="…" trong <context>) → giám khảo kiểm
    được trích dẫn trong câu trả lời. Không có nhãn thì trích dẫn bị tính là ý "không có trong tài liệu" → faithfulness
    thấp oan (câu ngắn 1 ý + 1 trích dẫn chỉ được 0.5); trích sai trang thì vẫn bị trừ điểm."""
    return f"[{source['source']}] {source['text']}"


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
            row.update(status=status, answer=r.answer, contexts=[labeled(s) for s in r.sources],
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


def scored_answer(answer: str) -> str:
    """Câu trả lời đưa cho giám khảo: bỏ câu mở đầu cố định của pipeline (lưu ý "chỉ đề cập một phần")."""
    return answer.replace(PARTIAL_TEXT, "").strip()


async def score(rows: list[dict], no_ref: dict, with_ref: dict):
    todo = [r for r in rows if r["status"] == "answered"]
    for i, r in enumerate(todo, 1):
        q, a, ctx, ref = r["question"], scored_answer(r["answer"]), r["contexts"], r.get("reference") or ""
        jobs = [
            _one("faithfulness", no_ref["faithfulness"], user_input=q, response=a, retrieved_contexts=ctx),
            _one("answer_relevancy", no_ref["answer_relevancy"], user_input=q, response=a),
            _one("context_relevance", no_ref["context_relevance"], user_input=q, retrieved_contexts=ctx),
        ]
        if ref:
            jobs += [
                _one("context_recall", with_ref["context_recall"], user_input=q, retrieved_contexts=ctx, reference=ref),
                _one("answer_accuracy", with_ref["answer_accuracy"], user_input=q, response=a, reference=ref),
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
            print(f"{name:<20} {'—':>5} {0:>3}   (chưa có dữ liệu{' — cần reference' if name in ('context_recall', 'answer_accuracy') else ''})")
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


def pick_low(run_file: str, metric: str, below: float, testset: list[dict]) -> list[dict]:
    """Câu có `metric` < below trong lần chạy cũ. Lấy bản trong testset hiện tại (reference đã sửa thì dùng bản mới);
    câu đã bị sửa / xoá khỏi testset thì dùng lại câu, expect, reference đã lưu trong file kết quả."""
    with open(run_file, encoding="utf-8") as f:
        rows = json.load(f)["rows"]
    current = {it["question"]: it for it in testset}
    picked = []
    for r in rows:
        v = (r.get("scores") or {}).get(metric)
        if v is None or math.isnan(v) or v >= below:
            continue
        it = current.get(r["question"])
        print(f"  {metric}={v:.2f}  {r['question'][:70]}" + ("" if it else "  (không còn trong testset → dùng bản đã lưu)"))
        picked.append(it or {k: r[k] for k in ("question", "expect", "reference") if k in r})
    print(f"Chạy lại {len(picked)} câu {metric} < {below} trong {os.path.basename(run_file)}")
    return picked


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="chỉ chạy N câu đầu")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--rescore", help="chấm lại file data/eval_runs/*.json đã lưu, không chạy pipeline")
    mode.add_argument("--rerun", help="chạy lại pipeline chỉ cho các câu điểm thấp trong file data/eval_runs/*.json")
    ap.add_argument("--metric", default="faithfulness", choices=list(HINTS), help="chỉ số để lọc câu với --rerun")
    ap.add_argument("--below", type=float, default=0.7, help="--rerun: lấy các câu có chỉ số < ngưỡng này")
    args = ap.parse_args()

    if args.rescore:
        with open(args.rescore, encoding="utf-8") as f:
            saved = json.load(f)
        rows, config = saved["rows"], saved["config"]
        rag = None
        embedder = search_rerank.OllamaEmbeddings(model=search_rerank.EMBED_MODEL, num_ctx=search_rerank.EMBED_NUM_CTX)
    else:
        with open(TESTSET, encoding="utf-8") as f:
            items = json.load(f)
        if args.rerun:
            items = pick_low(args.rerun, args.metric, args.below, items)
            if not items:
                print(f"Không có câu nào {args.metric} < {args.below} trong {args.rerun}.")
                return
        items = items[: args.limit]
        print(f"Nạp model... (giám khảo: {JUDGE_MODEL})")
        rag = GuardedRAG()
        await asyncio.to_thread(rag.warmup)
        rows, config = await run_pipeline(rag, items), config_snapshot()
        if args.rerun:
            config["rerun_of"] = {"file": os.path.basename(args.rerun), "metric": args.metric, "below": args.below}
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
