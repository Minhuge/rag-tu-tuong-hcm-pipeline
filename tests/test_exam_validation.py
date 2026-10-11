"""
Test lớp kiểm tra đề (rag/exam_validation.py) và luồng tạo đề có kiểm tra + tạo bù (exam_generation.generate_exam).
Gemini giả (fixture fake_gemini trong conftest.py) → không gọi Gemini thật, không tốn quota.

Chạy:  pytest -q tests/test_exam_validation.py
"""
import asyncio
import json
import random

import pytest

from rag import exam_generation as eg
from rag import exam_validation as ev

PAGES = {38, 39}
SRC = eg.ChapterPassages(eg.CHAPTERS[3], [eg.Passage("chunk_1", 38, "đoạn trang 38 " + "x" * 200),
                                          eg.Passage("chunk_2", 39, "đoạn trang 39 " + "y" * 200)], total=2)


def mcq(question="Hai chân của nền kinh tế là gì?", **over):
    data = {"source_page": 38, "question": question,
            "options": [{"label": l, "content": f"lựa chọn {l}"} for l in "ABCD"],
            "correct_label": "B", "explanation": "Theo giáo trình, công nghiệp và nông nghiệp."}
    return {**data, **over}


def essay(question="Trình bày quan điểm…", **over):
    return {"source_page": 39, "question": question, "rubric": "- ý 1 (1 điểm)\n- ý 2 (1 điểm)", "points": 2, **over}


def exam_text(mcqs=(), essays=()) -> str:
    return json.dumps({"mcq": list(mcqs), "essay": list(essays)}, ensure_ascii=False)


def verdicts(*items) -> callable:
    """judge giả: items = (chosen_label, supported, other_correct_option, duplicate_of) theo thứ tự câu."""
    def judge(contents):
        return json.dumps({"verdicts": [{"index": i, "reason": f"lý do {i}", "chosen_label": c, "supported": s,
                                         "other_correct_option": o, "duplicate_of": d}
                                        for i, (c, s, o, d) in enumerate(items, 1)]})
    return judge


# ---------- tầng 1: cấu trúc ----------
def M(**over):
    return eg.GeneratedMCQ.model_validate(mcq(**over))


def E(**over):
    return eg.GeneratedEssay.model_validate(essay(**over))


def test_good_questions_have_no_issues():
    assert ev.mcq_issues(M(), PAGES) == [] and ev.essay_issues(E(), PAGES) == []


@pytest.mark.parametrize("over,expected", [
    pytest.param({"options": [{"label": l, "content": "giống nhau"} for l in "ABCD"]}, "trùng nội dung", id="trung"),
    pytest.param({"options": [{"label": l, "content": "Giống, nhau." if l == "A" else f"x{l}"} for l in "ABCD"]},
                 None, id="khac-nhau-ok"),
    pytest.param({"options": [{"label": l, "content": " " if l == "C" else l} for l in "ABCD"]}, "lựa chọn rỗng",
                 id="rong"),
    pytest.param({"options": [{"label": l, "content": l} for l in "AABC"], "correct_label": "D"},
                 "không có trong các lựa chọn", id="dap-an-ngoai-nhan"),
    pytest.param({"explanation": "Đáp án B đúng vì…"}, "nhắc chữ cái", id="nhac-chu-cai"),
    pytest.param({"explanation": " "}, "thiếu giải thích", id="thieu-giai-thich"),
    pytest.param({"source_page": 99}, "trang 99 không nằm", id="trang-la"),
    pytest.param({"question": ""}, "câu hỏi rỗng", id="cau-hoi-rong"),
])
def test_mcq_issues(over, expected):
    issues = ev.mcq_issues(M(**over), PAGES)
    assert (issues == []) if expected is None else any(expected in x for x in issues)


def test_options_differing_only_by_case_and_punctuation_are_duplicates():
    opts = [{"label": "A", "content": "Độc lập dân tộc."}, {"label": "B", "content": "độc lập  dân tộc"},
            {"label": "C", "content": "x"}, {"label": "D", "content": "y"}]
    assert ev.mcq_issues(M(options=opts), PAGES) == ["có lựa chọn trùng nội dung"]


@pytest.mark.parametrize("over,expected", [
    pytest.param({"rubric": " "}, "rubric rỗng", id="rubric-rong"),
    pytest.param({"rubric": "nêu đủ ý", "points": 0}, "points = 0", id="diem-0"),
    pytest.param({"source_page": 1}, "trang 1 không nằm", id="trang-la"),
])
def test_essay_issues(over, expected):
    assert any(expected in x for x in ev.essay_issues(E(**over), PAGES))


def test_fix_essay_points_uses_rubric_total():
    assert ev.fix_essay_points(E(points=3)).points == 2                       # lệch tổng → lấy theo rubric
    assert ev.fix_essay_points(E(rubric="- nêu đủ ý", points=3)).points == 3  # rubric không ghi điểm → giữ
    assert ev.rubric_total("- a (1 điểm)\n- b (0,5 điểm)\n- c ( 0.5 điểm )") == 2.0


# ---------- tầng 2 + gộp ----------
def run_validate(exam: dict, judge=None, fake=None, use_judge=True):
    llm = fake([], judge=judge)
    res = asyncio.run(ev.validate_exam(llm, eg.GeneratedExam.model_validate(exam), PAGES, SRC.prompt_text(),
                                       use_judge=use_judge))
    return res, llm


def test_judge_combines_blind_solving_and_other_correct_check(fake_gemini):
    """Đề đánh dấu B cho mọi câu trắc nghiệm (validate_exam không xáo)."""
    questions = ["Câu đúng?", "Giải ra khác?", "Không lựa chọn nào đúng?", "Không chắc?", "Hai đáp án?"]
    exam = {"mcq": [mcq(q) for q in questions], "essay": [essay("Hỏi lại câu 1?"), essay("Rubric sai?")]}
    res, llm = run_validate(exam, verdicts(
        ("B", True, False, None),     # tự giải ra đúng B, chỉ một đáp án → giữ
        ("C", True, False, None),     # tự giải ra C ≠ B → loại
        (None, False, False, None),   # không thấy lựa chọn nào đúng → loại
        ("B", False, False, None),    # chọn trùng B nhưng giáo trình không khẳng định rõ → loại
        ("B", True, True, None),      # chọn trùng B nhưng lựa chọn khác cũng đúng → loại (tự giải không bắt được)
        (None, True, False, 1),       # tự luận trùng chủ đề câu 1 → loại
        (None, False, False, None),   # tự luận: rubric không đúng giáo trình → loại
    ), fake_gemini)
    assert [q.question for q in res.mcq] == ["Câu đúng?"] and res.essay == [] and res.judge_calls == 1
    reasons = {r.question: r.reasons for r in res.rejected}
    assert reasons["Giải ra khác?"][0] == "giám khảo tự giải ra C, đề đánh dấu B"
    assert reasons["Không lựa chọn nào đúng?"][0] == "giám khảo không thấy lựa chọn nào đúng theo giáo trình"
    assert reasons["Không chắc?"][0] == "giáo trình không khẳng định rõ đáp án"
    assert reasons["Hai đáp án?"][0] == "có lựa chọn khác cũng đúng"
    assert reasons["Hỏi lại câu 1?"][0] == "trùng nội dung câu 1"
    assert reasons["Rubric sai?"][0] == "giáo trình không xác nhận câu hỏi / rubric"
    assert reasons["Giải ra khác?"][-1] == "giám khảo: lý do 2"
    assert len(llm.calls) == 1                                    # cả đề chỉ một lần gọi giám khảo


def test_judge_prompt_hides_marked_answer(fake_gemini):
    _, llm = run_validate({"mcq": [mcq()], "essay": []}, None, fake_gemini)
    prompt = llm.calls[0][0]
    assert "A. lựa chọn A" in prompt and "đánh dấu" not in prompt.split("<cac_cau_hoi>")[-1]


def test_structural_failures_are_not_sent_to_judge(fake_gemini):
    exam = {"mcq": [mcq("Trang lạ?", source_page=99), mcq("Câu tốt?")], "essay": [essay()]}
    res, llm = run_validate(exam, None, fake_gemini)
    prompt = llm.calls[0][0]
    assert "Trang lạ?" not in prompt and "Câu 1 (trắc nghiệm, trang 38)\nHỏi: Câu tốt?" in prompt
    assert "Câu 2 (tự luận, trang 39, 2 điểm)" in prompt          # tự luận đánh số tiếp sau trắc nghiệm
    assert "Theo giáo trình, công nghiệp" not in prompt            # không gửi explanation cho giám khảo
    assert [r.stage for r in res.rejected] == ["cấu trúc"] and len(res.mcq) == 1 and len(res.essay) == 1


def test_missing_verdict_counts_as_failure(fake_gemini):
    res, _ = run_validate({"mcq": [mcq("Một?"), mcq("Hai?")], "essay": []},
                          verdicts(("B", True, False, None)), fake_gemini)
    assert [q.question for q in res.mcq] == ["Một?"]
    assert res.rejected[0].reasons == ["giám khảo không trả kết quả cho câu này"]


def test_without_judge_or_when_nothing_passes_structure(fake_gemini):
    res, llm = run_validate({"mcq": [mcq()], "essay": []}, None, fake_gemini, use_judge=False)
    assert len(res.mcq) == 1 and res.judge_calls == 0 and llm.calls == []
    res, llm = run_validate({"mcq": [mcq(source_page=99)], "essay": []}, None, fake_gemini)
    assert res.mcq == [] and llm.calls == []                       # không còn câu nào → không gọi giám khảo


def test_broken_judge_output_raises(fake_gemini):
    with pytest.raises(ValueError, match="Giám khảo"):
        run_validate({"mcq": [mcq()], "essay": []}, lambda c: "{hỏng", fake_gemini)


# ---------- generate_exam: tạo → kiểm tra → tạo bù ----------
def gen(fake, exam_texts, n_mcq, n_essay, judge=None):
    llm = fake(exam_texts, judge=judge)
    return asyncio.run(eg.generate_exam(llm, SRC, n_mcq, n_essay, rng=random.Random(0))), llm


def test_generate_all_pass_in_one_round(fake_gemini):
    res, llm = gen(fake_gemini, [exam_text([mcq("Một?"), mcq("Hai?")], [essay()])], 2, 1)
    assert len(res.exam.mcq) == 2 and len(res.exam.essay) == 1 and res.notice is None
    assert res.calls == 2 and not res.topped_up and len(llm.calls) == 2


def test_generate_tops_up_once_with_avoid_list(fake_gemini):
    first = exam_text([mcq("Giữ?"), mcq("Trang lạ?", source_page=99)], [essay("Tự luận giữ?")])
    second = exam_text([mcq("Câu bù?")], [])
    res, llm = gen(fake_gemini, [first, second], 2, 1)
    assert [q.question for q in res.exam.mcq] == ["Giữ?", "Câu bù?"] and len(res.exam.essay) == 1
    assert res.calls == 4 and res.topped_up and res.notice is None
    topup_prompt = llm.calls[2][0]
    assert "tạo 1 câu trắc nghiệm và 0 câu tự luận" in topup_prompt
    assert "- Giữ?" in topup_prompt and "- Tự luận giữ?" in topup_prompt   # báo câu đã có để không hỏi lại


def test_generate_returns_fewer_with_notice(fake_gemini):
    res, _ = gen(fake_gemini, [exam_text([mcq(source_page=99)], [essay()]), exam_text([mcq(source_page=99)])], 1, 1)
    assert res.exam.mcq == [] and len(res.exam.essay) == 1 and len(res.rejected) == 2
    assert res.notice.startswith("Chỉ tạo được 0/1 câu trắc nghiệm và 1/1 câu tự luận")


def test_generate_trims_extra_questions(fake_gemini):
    res, _ = gen(fake_gemini, [exam_text([mcq("1?"), mcq("2?"), mcq("3?")], [])], 2, 0)
    assert [q.question for q in res.exam.mcq] == ["1?", "2?"]


def test_generate_first_round_broken_json_raises(fake_gemini):
    with pytest.raises(eg.ExamParseError) as e:
        gen(fake_gemini, ["{hỏng"], 1, 0)
    assert e.value.raw == "{hỏng"


def test_generate_topup_broken_keeps_what_passed(fake_gemini):
    res, _ = gen(fake_gemini, [exam_text([mcq("Giữ?"), mcq(source_page=99)]), "{hỏng"], 2, 0)
    assert [q.question for q in res.exam.mcq] == ["Giữ?"]
    assert "Chỉ tạo được 1/2" in res.notice and "Tạo bù thất bại" in res.notice


def test_generate_options_are_shuffled(fake_gemini):
    texts = [exam_text([mcq(f"{i}?") for i in range(12)])]
    res, _ = gen(fake_gemini, texts, 12, 0)
    assert len({q.correct_label for q in res.exam.mcq}) > 1        # không còn dồn vào B như Gemini trả về
    for q in res.exam.mcq:
        assert next(o.content for o in q.options if o.label == q.correct_label) == "lựa chọn B"
