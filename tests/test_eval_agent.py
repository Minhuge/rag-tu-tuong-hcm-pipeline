"""
Test bộ chấm của scripts/eval_agent.py (roadmap 5.7) và tính hợp lệ của data/eval_agent_testset.json — không gọi
Gemini. Chạy đánh giá thật: python -m scripts.eval_agent.

Chạy:  pytest -q tests/test_eval_agent.py
"""
import asyncio

import numpy as np
import pytest

from agent import tools
from scripts import eval_agent as ev


def calls(*items):
    return [{"name": n, "args": a, "ok": True} for n, a in items]


def test_normalize_ignores_markdown_case_and_spaces_around_slash():
    assert ev.normalize("Bạn đạt **4 / 4 điểm** ở  _Kiểm tra_") == "bạn đạt 4/4 điểm ở kiểm tra"


def test_tool_rules():
    case = {"must_call": ["list_my_submissions"],
            "must_call_with": [{"name": "get_my_submission", "args": {"question_number": 2}}],
            "must_not_call": ["get_my_exam"]}
    good = calls(("list_my_submissions", {}), ("get_my_submission", {"submission_id": 1, "question_number": 2}))
    assert ev.check(case, "ok", good) == []
    bad = calls(("get_my_submission", {"submission_id": 1, "question_number": 3}), ("get_my_exam", {}))
    assert ev.check(case, "ok", bad) == [
        "không gọi list_my_submissions", "không gọi get_my_submission({'question_number': 2})",
        "không được gọi get_my_exam"]
    assert ev.check({"no_tools": True}, "ok", calls(("list_my_submissions", {}))) == [
        "không được gọi tool nào (đã gọi list_my_submissions)"]


def test_text_rules_and_alternatives():
    case = {"must_contain": ["4/4", ["chưa", "0 bài"]], "must_not_contain": ["1911"]}
    assert ev.check(case, "Bạn được **4 / 4**, đề chưa có ai nộp.", []) == []
    assert ev.check(case, "Được 3/4 vào năm 1911.", []) == ["thiếu “4/4”", "thiếu “chưa” / “0 bài”", "có “1911”"]


def test_every_answer_must_hide_tool_names_and_ids():
    assert ev.check({}, "Theo get_my_submission (submission_id=5) bạn được 4/4", []) == [
        "có “get_my_submission”", "có “submission_id”"]


def test_testset_is_valid_and_matches_real_tools():
    cases = ev.load_cases()
    assert len(cases) >= 15 and sum(bool(c.get("critical")) for c in cases) >= 5      # roadmap: 15–20 câu, có câu từ chối
    real = {t.name for t in tools.build_tools(rag=None)}
    for c in cases:
        named = {*c.get("must_call", []), *c.get("must_not_call", []), *(w["name"] for w in c.get("must_call_with", []))}
        assert named <= real, c["id"]
        assert ev.contents_of(c)[-1].role == "user", c["id"]


def test_load_cases_rejects_bad_files(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text('{"cases": [{"id": "a", "question": "?", "must_cal": []}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="khoá lạ"):
        ev.load_cases(bad)
    bad.write_text('{"cases": [{"id": "a", "question": "?"}, {"id": "a", "question": "?"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="trùng"):
        ev.load_cases(bad)


def test_score_marks_errors_and_counts_runs():
    cases = [{"id": "x", "question": "Điểm?", "must_contain": ["4/4"], "critical": True}]
    records = [{"id": "x", "run": 1, "answer": "4/4", "tool_calls": [], "error": None},
               {"id": "x", "run": 2, "answer": "", "tool_calls": [], "error": "RuntimeError: quá giờ"}]
    summary = ev.score(cases, records)
    assert summary["x"]["passed"] == 1 and summary["x"]["runs"] == 2
    assert records[1]["problems"] == ["lỗi: RuntimeError: quá giờ"]
    assert ev.report(cases, records, summary, min_pass=0.5) is False                 # critical trượt một lần


def test_write_tool_only_when_asked_and_at_most_once():
    create = ("create_exam_draft", {"chapter": 3})
    assert ev.check({}, "ok", calls(create)) == ["tạo đề khi không được yêu cầu"]
    failed = [{"name": "create_exam_draft", "args": {}, "ok": False}]
    assert ev.check({}, "ok", failed) == ["tạo đề khi không được yêu cầu"]            # gọi lỗi cũng là gọi
    asked = {"must_call": ["create_exam_draft"]}
    assert ev.check(asked, "ok", calls(create)) == []
    assert ev.check(asked, "ok", calls(create, ("create_exam_draft", {"chapter": 1}))) == ["tạo nhiều hơn một đề"]


# ---------- chỉ số RAGAS (tool_call_accuracy / tool_call_f1 không gọi Gemini; InScope chỉ thử phần tính điểm) ----------
def ragas_of(case, tool_calls):
    record = {"id": case["id"], "run": 1, "answer": "ok", "tool_calls": tool_calls, "error": None}
    asyncio.run(ev.ragas_scores([case], [record], use_llm=False, concurrency=1))
    return record["ragas"]


def test_tool_call_metrics_reward_the_reference_and_punish_deviations():
    case = {"id": "x", "question": "?", "reference_tool_calls": [
        {"name": "list_my_submissions"}, {"name": "get_my_submission", "args": {"submission_id": 1, "question_number": 2}}]}
    right = calls(("list_my_submissions", {}), ("get_my_submission", {"submission_id": 1, "question_number": 2}))
    assert ragas_of(case, right) == {"tool_call_accuracy": 1.0, "tool_call_f1": 1.0}
    wrong_id = calls(("list_my_submissions", {}), ("get_my_submission", {"submission_id": 4, "question_number": 2}))
    assert ragas_of(case, wrong_id) == {"tool_call_accuracy": 0.75, "tool_call_f1": 0.5}
    swapped = list(reversed(right))                         # đúng tool, sai thứ tự: accuracy 0, f1 (theo tập) vẫn 1
    assert ragas_of(case, swapped) == {"tool_call_accuracy": 0.0, "tool_call_f1": 1.0}
    extra = right + calls(("get_my_exam", {}))
    assert ragas_of(case, extra)["tool_call_f1"] < 1


def test_defaults_are_filled_and_free_text_args_ignored():
    draft = {"id": "d", "question": "?", "reference_tool_calls": [{"name": "create_exam_draft", "args": {"chapter": 1}}]}
    assert ragas_of(draft, calls(("create_exam_draft", {"chapter": 1, "n_mcq": 10, "n_essay": 2})))["tool_call_f1"] == 1
    assert ragas_of(draft, calls(("create_exam_draft", {"chapter": 1, "n_mcq": 5})))["tool_call_f1"] == 0
    search = {"id": "s", "question": "?", "reference_tool_calls": [{"name": "search_textbook"}]}
    assert ragas_of(search, calls(("search_textbook", {"query": "bất kỳ"})))["tool_call_accuracy"] == 1


def test_no_tool_expected_and_none_called_is_perfect():
    case = {"id": "n", "question": "?", "reference_tool_calls": []}
    assert ragas_of(case, []) == {"tool_call_accuracy": 1.0, "tool_call_f1": 1.0}
    assert ragas_of(case, calls(("list_my_submissions", {}))) == {"tool_call_accuracy": 0.0, "tool_call_f1": 0.0}
    assert ragas_of({"id": "free", "question": "?"}, calls(("list_my_submissions", {}))) == {}   # không có mẫu: bỏ qua


def test_topic_adherence_refusing_everything_is_in_scope():
    metric = object.__new__(ev.InScope)                    # chỉ thử phần tính điểm, không cần giám khảo
    metric.mode = "precision"
    assert metric._compute_score(np.array([False]), np.array([False])) == 1.0        # từ chối câu thời tiết
    assert metric._compute_score(np.array([True, True]), np.array([True, False])) == pytest.approx(0.5)


def test_ragas_messages_keep_turns_tool_calls_and_answer():
    case = {"conversation": [["user", "Câu 2?"], ["model", "Đề nào?"], ["user", "ôn tập"]]}
    record = {"answer": "Đáp án B", "tool_calls": [{"name": "list_my_submissions", "args": {}, "ok": False}]}
    msgs = ev.ragas_messages(case, record, [ev.ToolCall(name="list_my_submissions", args={})])
    assert [type(m).__name__ for m in msgs] == ["HumanMessage", "AIMessage", "HumanMessage", "AIMessage",
                                               "ToolMessage", "AIMessage"]
    assert msgs[4].content == "(tool báo lỗi)" and msgs[-1].content == "Đáp án B"


def test_testset_references_use_real_tools_and_every_case_has_a_goal():
    real = {t.name for t in tools.build_tools(rag=None)}
    for c in ev.load_cases():
        assert {r["name"] for r in c.get("reference_tool_calls", [])} <= real, c["id"]
        assert c.get("expected_outcome"), c["id"]          # câu từ chối mà không có mẫu → giám khảo chấm 0 oan
