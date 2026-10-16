"""
Nhật ký kết quả eval_ragas dạng Markdown: data/eval_runs/RAGAS_LOG.md, dựng lại từ MỌI file data/eval_runs/<ngày-giờ>.json.

  - Bảng tổng quan: mỗi lần chạy một dòng (mới nhất trước) → so trước / sau khi sửa pipeline.
  - Chi tiết từng lần: cấu hình, số câu trả lời / từ chối, câu bỏ sót / lọt, trung bình từng chỉ số, điểm từng câu.

eval_ragas tự gọi sau mỗi lần chạy; dựng lại tay:  python -m scripts.eval_ragas --log
File sinh tự động — sửa tay sẽ bị ghi đè. Không import ragas → nhanh, test được.
"""
import json
import math
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "eval_runs"
LOG_FILE = RUNS_DIR / "RAGAS_LOG.md"

# (tên, nhãn ngắn trong bảng, ý nghĩa) — factual_correctness: chỉ số cũ, đã thay bằng answer_accuracy (14/10/2026)
METRICS = [ 
    ("faithfulness", "Bám tài liệu", "câu trả lời có bịa ngoài các đoạn giáo trình tìm được không"),
    ("response_groundedness", "Có căn cứ", "như Bám tài liệu nhưng 2 giám khảo chấm cả câu trả lời — ổn định hơn, "
     "dùng đối chiếu: chỉ Bám tài liệu thấp thì nhiều khả năng do giám khảo tách ý"),
    ("answer_relevancy", "Đúng câu hỏi", "có trả lời đúng điều được hỏi không"),
    ("context_relevance", "Đoạn liên quan", "các đoạn tìm được có liên quan câu hỏi không"),
    ("context_recall", "Đoạn đủ ý", "các đoạn tìm được có đủ ý của đáp án chuẩn không (cần reference)"),
    ("answer_accuracy", "Đúng đáp án", "câu trả lời có cùng ý với đáp án chuẩn không (cần reference)"),
    ("factual_correctness", "Factual (cũ)", "chỉ số cũ, đã thay bằng answer_accuracy"),
]
STATUS = {"answered": "trả lời", "refused": "từ chối", "fallback": "thay thế (4a)", "error": "lỗi"}


def dot(v: float) -> str:
    return "🟢" if v >= 0.8 else "🟡" if v >= 0.6 else "🔴"


def cell(v) -> str:
    """Ô điểm: 🟢 ≥ 0.8 · 🟡 0.6–0.8 · 🔴 < 0.6 · — chưa chấm."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{dot(v)} {v:.2f}"


def short(text: str, n: int = 70) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= n else text[: n - 1] + "…"


def when(stem: str) -> str:
    try:
        return datetime.strptime(stem, "%Y%m%d-%H%M%S").strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return stem


def load_runs(runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Các lần chạy eval_ragas, mới nhất trước (bỏ qua agent_*.json của eval_agent)."""
    runs = []
    for f in sorted(runs_dir.glob("[0-9]*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        runs.append({"name": f.stem, "config": data.get("config", {}), "rows": data.get("rows", [])})
    by_answers = {}
    for run in runs:                         # file cũ (trước 15/10) chưa ghi rescored_from: nhận ra qua câu trả lời y hệt
        key = tuple((r["question"], r.get("answer")) for r in run["rows"])
        run["same_answers_as"] = by_answers.get(key)
        by_answers.setdefault(key, run["name"])
    return runs[::-1]


def kind(run: dict) -> str:
    c = run["config"]
    if c.get("rerun_of"):
        r = c["rerun_of"]
        return f"chạy lại câu {r['metric']} < {r['below']} của {Path(r['file']).stem}"
    source = c.get("rescored_from") or run["same_answers_as"]
    return f"chấm lại {Path(source).stem}" if source else "chạy đầy đủ"


def averages(rows: list[dict]) -> dict:
    out = {}
    for name, *_ in METRICS:
        vals = [r["scores"][name] for r in rows if r.get("status") == "answered"
                and (r.get("scores") or {}).get(name) is not None and not math.isnan(r["scores"][name])]
        if vals:
            out[name] = (sum(vals) / len(vals), len(vals), min(vals))
    return out


def counts(rows: list[dict]) -> tuple[dict, list, list]:
    n = {s: sum(r.get("status") == s for r in rows) for s in STATUS}
    missed = [r for r in rows if r.get("expect") == "answer" and r.get("status") != "answered"]
    leaked = [r for r in rows if r.get("expect") == "refuse" and r.get("status") == "answered"]
    return n, missed, leaked


def used_metrics(runs: list[dict]) -> list[tuple]:
    seen = {k for run in runs for r in run["rows"] for k in (r.get("scores") or {})}
    return [m for m in METRICS if m[0] in seen]


def overview(runs: list[dict]) -> list[str]:
    metrics = used_metrics(runs)
    head = ["Lần chạy", "Kiểu", "Câu", "Trả lời · từ chối · thay thế · lỗi", "Bỏ sót", "Lọt", *(m[1] for m in metrics)]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join([":--"] * 2 + ["--:"] * 4 + [":-:"] * len(metrics)) + "|"]
    for run in runs:
        n, missed, leaked = counts(run["rows"])
        avg = averages(run["rows"])
        lines.append("| " + " | ".join([
            f"[{when(run['name'])}](#{run['name']})", kind(run), str(len(run["rows"])),
            " · ".join(str(n[s]) for s in STATUS), str(len(missed)) if missed else "0",
            f"🚨 {len(leaked)}" if leaked else "0",
            *(cell(avg[m[0]][0]) if m[0] in avg else "—" for m in metrics)]) + " |")
    return lines


def detail(run: dict) -> list[str]:
    c, rows = run["config"], run["rows"]
    n, missed, leaked = counts(rows)
    unlabeled = any(not ctx.startswith("[") for r in rows for ctx in r.get("contexts", []))
    out = [f'<a id="{run["name"]}"></a>', "", f"### {when(run['name'])} · {kind(run)}", "",
           f"- **File:** `data/eval_runs/{run['name']}.json`",
           f"- **Model trả lời:** `{c.get('gemini_model', '?')}` · **giám khảo:** `{c.get('judge_model', '?')}`"
           + ("" if c.get("judge_model") != c.get("gemini_model") else " ⚠️ trùng model trả lời (tự chấm mình)"),
           "- **Retrieval:** " + " · ".join(f"{k}={c[k]}" for k in ("CANDIDATES", "TOP_N", "MIN_TOP", "MIN_COS",
                                                                     "PARTIAL_BAND", "PARTIAL_COS", "ATTACH_PREVIOUS") if k in c),
           f"- **Kết quả:** {len(rows)} câu — " + ", ".join(f"{n[s]} {STATUS[s]}" for s in STATUS if n[s])]
    if unlabeled:
        out.append("- ℹ️ Đoạn giáo trình đưa giám khảo **chưa có nhãn trang** (câu trả lời chạy trước 14/10 / chấm lại từ file đó): faithfulness thấp hơn thật "
                   "vì trích dẫn [Chương X, trang Y] bị tính là ý không kiểm được.")
    out.append("")
    if missed or leaked:
        out += [f"**⚠️ Bỏ sót** (câu giáo trình nhưng không trả lời): {len(missed)}"]
        out += [f"- `{r.get('status')}/{r.get('blocked_by')}` {short(r['question'], 100)}" for r in missed]
        out += ["", f"**🚨 Lọt** (câu lạc đề nhưng được trả lời): {len(leaked)}"]
        out += [f"- {short(r['question'], 100)}" for r in leaked]
        out.append("")

    avg = averages(rows)
    if avg:
        out += ["| Chỉ số | Trung bình | n | Thấp nhất |", "|:--|--:|--:|--:|"]
        for name, label, _ in METRICS:
            if name in avg:
                mean, count, low = avg[name]
                out.append(f"| {label} (`{name}`) | {cell(mean)} | {count} | {cell(low)} |")
        out.append("")

    metrics = [m for m in METRICS if any(m[0] in (r.get("scores") or {}) for r in rows)]
    out += ["<details><summary>Điểm từng câu</summary>", "",
            "| # | Câu hỏi | Mong đợi | Kết quả | " + " | ".join(m[1] for m in metrics) + " |",
            "|--:|:--|:-:|:-:|" + "|".join([":-:"] * len(metrics)) + "|"]
    for i, r in enumerate(rows, 1):
        status = STATUS.get(r.get("status"), r.get("status"))
        if r.get("expect") == "answer" and r.get("status") != "answered":
            status = f"⚠️ {status}"
        elif r.get("expect") == "refuse" and r.get("status") == "answered":
            status = f"🚨 {status}"
        scores = r.get("scores") or {}
        errors = r.get("score_errors") or {}
        out.append(f"| {i} | {short(r['question'])} | {'trả lời' if r.get('expect') == 'answer' else 'từ chối'} | "
                   f"{status} | " + " | ".join("lỗi" if m[0] in errors else cell(scores.get(m[0])) for m in metrics)
                   + " |")
    failed = [(i, name, err) for i, r in enumerate(rows, 1) for name, err in (r.get("score_errors") or {}).items()]
    if failed:
        out += ["", "**Ô “lỗi”** — giám khảo không chấm được (chấm lại bằng `--rescore` khi mạng / quota ổn):"]
        out += [f"- Câu {i} · `{name}`: {short(err, 140)}" for i, name, err in failed]
    out += ["", "</details>", ""]
    return out


def build_log(runs_dir: Path = RUNS_DIR) -> str:
    runs = load_runs(runs_dir)
    lines = [
        "# Nhật ký đánh giá RAGAS — chatbot giáo trình", "",
        "> Tự sinh bởi `python -m scripts.eval_ragas` sau mỗi lần chạy (dựng lại: `python -m scripts.eval_ragas --log`)."
        " **Không sửa tay** — sẽ bị ghi đè.", "",
        "**Cách đọc:** thang 0–1 · 🟢 ≥ 0.8 tốt · 🟡 0.6–0.8 cần xem · 🔴 < 0.6 có vấn đề · — chưa chấm. "
        "Điểm trung bình chỉ tính các câu đã trả lời.", "",
        "| Chỉ số | Đo gì |", "|:--|:--|",
        *(f"| {label} (`{name}`) | {meaning} |" for name, label, meaning in used_metrics(runs)),
        "| Bỏ sót | câu giáo trình (`expect: answer`) mà chatbot không trả lời |",
        "| Lọt | câu lạc đề (`expect: refuse`) mà chatbot lại trả lời |", "",
        "## Tổng quan các lần chạy", "", "Mới nhất ở trên. Bấm vào ngày để xem chi tiết.", "",
        *overview(runs), "",
        "## Chi tiết từng lần chạy", "",
    ]
    for run in runs:
        lines += detail(run)
    return "\n".join(lines).rstrip() + "\n"


def write_log(runs_dir: Path = RUNS_DIR, path: Path | None = None) -> Path:
    path = path or runs_dir / LOG_FILE.name
    path.write_text(build_log(runs_dir), encoding="utf-8")
    return path
