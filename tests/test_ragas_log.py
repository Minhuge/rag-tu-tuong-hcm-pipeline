"""
Test nhật ký Markdown của eval_ragas (scripts/ragas_log.py): dựng từ các file kết quả giả, không gọi Gemini.

Chạy:  pytest -q tests/test_ragas_log.py
"""
import json

from scripts import ragas_log

CONFIG = {"gemini_model": "gemini-3.8-flash", "judge_model": "gemini-3.6-flash", "TOP_N": 4}


def row(question, expect="answer", status="answered", answer="Trả lời [Chương I, trang 1].", **scores):
    return {"question": question, "expect": expect, "status": status, "answer": answer, "blocked_by": None,
            "contexts": ["[Chương I, trang 1] đoạn"] if status == "answered" else [], "scores": scores,
            "score_errors": {}}


def save(tmp_path, name, rows, **config):
    (tmp_path / f"{name}.json").write_text(json.dumps({"config": {**CONFIG, **config}, "rows": rows},
                                                      ensure_ascii=False), encoding="utf-8")


def test_overview_newest_first_with_counts_and_colored_averages(tmp_path):
    save(tmp_path, "20261014-093503", [row("Câu A?", faithfulness=0.5), row("Câu B?", status="refused"),
                                       row("Gián?", expect="refuse")])
    save(tmp_path, "20261015-081549", [row("Câu A?", faithfulness=0.9)])
    (tmp_path / "agent_20261015-090000.json").write_text("{}", encoding="utf-8")       # file eval_agent: bỏ qua
    log = ragas_log.build_log(tmp_path)
    overview = log.split("## Tổng quan")[1].split("## Chi tiết")[0]
    assert overview.index("15/10/2026 08:15") < overview.index("14/10/2026 09:35")
    # 3 câu: 2 trả lời (1 trong đó là câu lạc đề → lọt), 1 từ chối câu giáo trình → bỏ sót
    assert "| chạy đầy đủ | 3 | 2 · 1 · 0 · 0 | 1 | 🚨 1 | 🔴 0.50 |" in overview
    assert "🟢 0.90" in overview and "agent_" not in log


def test_rescore_and_rerun_are_labelled(tmp_path):
    rows = [row("Câu A?", faithfulness=1.0)]
    save(tmp_path, "20261014-093503", rows)
    save(tmp_path, "20261014-101333", rows)                                         # file cũ: câu trả lời y hệt
    save(tmp_path, "20261015-081549", rows, rescored_from="20261014-093503.json")
    save(tmp_path, "20261015-090000", [row("Câu A?", answer="khác")],
         rerun_of={"file": "20261014-093503.json", "metric": "faithfulness", "below": 0.6})
    runs = {r["name"]: ragas_log.kind(r) for r in ragas_log.load_runs(tmp_path)}
    assert runs == {"20261014-093503": "chạy đầy đủ", "20261014-101333": "chấm lại 20261014-093503",
                    "20261015-081549": "chấm lại 20261014-093503",
                    "20261015-090000": "chạy lại câu faithfulness < 0.6 của 20261014-093503"}


def test_detail_marks_problems_and_lists_scoring_errors(tmp_path):
    bad = row("Câu lỗi?", context_recall=1.0)
    bad["score_errors"] = {"faithfulness": "InstructorRetryException: Cannot connect to host"}
    unlabeled = row("Câu cũ?", faithfulness=0.8)
    unlabeled["contexts"] = ["đoạn không nhãn"]
    save(tmp_path, "20261014-093503", [bad, unlabeled, row("Câu bị từ chối?", status="refused")])
    log = ragas_log.build_log(tmp_path)
    assert "| ⚠️ từ chối |" in log and "Câu 1 · `faithfulness`: InstructorRetryException" in log
    assert "| 1 | Câu lỗi? | trả lời | trả lời | lỗi | 🟢 1.00 |" in log
    assert "chưa có nhãn trang" in log                       # có đoạn không nhãn → faithfulness thấp hơn thật


def test_write_log_creates_file(tmp_path):
    save(tmp_path, "20261014-093503", [row("Câu A?", faithfulness=1.0)])
    path = ragas_log.write_log(tmp_path)
    assert path.name == "RAGAS_LOG.md" and path.read_text(encoding="utf-8").startswith("# Nhật ký đánh giá RAGAS")
