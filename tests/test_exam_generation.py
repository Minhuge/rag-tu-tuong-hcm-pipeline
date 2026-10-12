"""
Test rag/exam_generation.py (lấy đoạn văn theo chương, schema output) và GET /chapters.
Qdrant chạy trong bộ nhớ (fixture qdrant trong conftest.py) → không cần Docker, không cần Ollama.
Response Gemini là object giả → không gọi Gemini thật, không tốn quota.

Chạy:  pytest -q tests/test_exam_generation.py
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth
from app import main as api
from rag import exam_generation as eg


def test_whole_chapter_when_it_fits(qdrant):
    res = eg.get_chapter_passages(3, max_chars=100_000, client=qdrant)
    assert res.chapter.key == "Chương III" and res.total == 20 and len(res.passages) == 20
    assert [p.chunk_id for p in res.passages] == [f"chunk_{100 + i:03d}" for i in range(20)]
    assert res.pages == list(range(38, 48))


def test_only_requested_chapter(qdrant):
    res = eg.get_chapter_passages("Chương IV", client=qdrant)
    assert [p.chunk_id for p in res.passages] == ["chunk_200"]


def test_labels_and_cleanup(qdrant):
    res = eg.get_chapter_passages(3, max_chars=100_000, client=qdrant)
    first, second = res.passages[0], res.passages[1]
    assert first.text.startswith("CHƯƠNG III")             # bỏ phần đuôi của chương trước
    assert second.text.startswith("Nội dung đoạn 01")      # bỏ ". " thừa ở đầu đoạn
    assert first.labeled().startswith("[trang 38] CHƯƠNG III")
    assert res.prompt_text().split("\n\n")[1].startswith("[trang 38] Nội dung đoạn 01")


def test_spread_evenly_within_budget(qdrant):
    res = eg.get_chapter_passages(3, max_chars=2_000, client=qdrant)
    assert res.chars <= 2_000
    assert 1 < len(res.passages) < 20
    ids = [p.chunk_id for p in res.passages]
    assert ids[0] == "chunk_100" and ids[-1] == "chunk_119"   # có cả đầu lẫn cuối chương
    nums = [int(i[-3:]) for i in ids]
    gaps = [b - a for a, b in zip(nums, nums[1:])]
    assert max(gaps) - min(gaps) <= 1                         # khoảng cách giữa các đoạn gần như đều


def test_heading_only_chunks_are_skipped(qdrant):
    with pytest.raises(LookupError):
        eg.get_chapter_passages(5, client=qdrant)


def test_resolve_chapter():
    assert eg.resolve_chapter(0).key == "Chương Mở đầu"
    assert eg.resolve_chapter("3").key == eg.resolve_chapter("chương iii").key == "Chương III"
    for bad in (8, "Chương IX", "ba"):
        with pytest.raises(ValueError):
            eg.resolve_chapter(bad)


def test_rejects_tiny_budget(qdrant):
    with pytest.raises(ValueError):
        eg.get_chapter_passages(3, max_chars=100, client=qdrant)


def test_spread_includes_both_ends():
    assert eg._spread(10, 2) == [0, 9]
    assert eg._spread(10, 4) == [0, 3, 6, 9]
    assert eg._spread(10, 1) == [5]
    assert eg._spread(3, 5) == [0, 1, 2]


def test_chapters_endpoint(db_engine):
    c = TestClient(api.app)        # không dùng "with" → không chạy lifespan (không nạp model)
    assert c.get("/chapters").status_code == 401
    user = auth.register("a@example.com", "matkhau123")
    res = c.get("/chapters", headers={"Authorization": f"Bearer {auth.create_access_token(user['id'])}"})
    assert res.status_code == 200
    data = res.json()
    assert [d["number"] for d in data] == list(range(8))
    assert data[3]["chapter"] == "Chương III" and data[3]["title"].startswith("Tư tưởng Hồ Chí Minh về chủ nghĩa xã hội")


# ---------- schema output (2.3) ----------
def mcq(**over):
    q = {"source_page": 43, "question": "Hai chân của nền kinh tế là gì?",
         "options": [{"label": l, "content": f"lựa chọn {l}"} for l in "ABCD"],
         "correct_label": "B", "explanation": "Theo đoạn trang 43."}
    return {**q, **over}


def exam_json(**over) -> str:
    data = {"mcq": [mcq()], "essay": [{"source_page": 41, "question": "Trình bày…",
                                       "rubric": "- ý 1 (1 điểm)\n- ý 2 (1 điểm)", "points": 2}]}
    return json.dumps({**data, **over}, ensure_ascii=False)


def fake_resp(text, parsed=None, finish_reason="STOP"):
    return SimpleNamespace(text=text, parsed=parsed, candidates=[SimpleNamespace(finish_reason=finish_reason)])


def test_parse_valid_exam():
    exam = eg.parse_exam(fake_resp(exam_json()))
    assert exam.mcq[0].correct_label == "B" and [o.label for o in exam.mcq[0].options] == list("ABCD")
    assert exam.essay[0].points == 2.0


def test_parse_prefers_sdk_parsed_object():
    parsed = eg.GeneratedExam(mcq=[], essay=[])
    assert eg.parse_exam(fake_resp("không phải JSON", parsed=parsed)) is parsed


# id viết không dấu: pytest đổi ký tự có dấu trong tên test thành \uXXXX
@pytest.mark.parametrize("text", [
    pytest.param("{hỏng", id="json-hong"),
    pytest.param(exam_json(mcq=[mcq(options=[{"label": l, "content": "x"} for l in "ABC"])]), id="3-lua-chon"),
    pytest.param(exam_json(mcq=[mcq(correct_label="E")]), id="nhan-ngoai-A-D"),
    pytest.param(exam_json(mcq=[mcq(source_page="trang 43")]), id="trang-khong-phai-so"),
    pytest.param(json.dumps({"mcq": []}), id="thieu-essay"),
])
def test_parse_rejects_malformed(text):
    with pytest.raises(ValueError, match="không đúng schema"):
        eg.parse_exam(fake_resp(text))


def test_parse_empty_response():
    with pytest.raises(ValueError, match="SAFETY"):
        eg.parse_exam(fake_resp(None, finish_reason="SAFETY"))


def test_per_question_issues_are_left_to_validation():
    """Trùng lựa chọn / điểm ≤ 0 vẫn parse được → bước 2.6 loại riêng câu đó, không bỏ cả đề."""
    same = [{"label": l, "content": "giống nhau"} for l in "ABCD"]
    exam = eg.parse_exam(fake_resp(exam_json(mcq=[mcq(options=same)],
                                             essay=[{"source_page": 1, "question": "?", "rubric": "", "points": 0}])))
    assert len(exam.mcq) == 1 and exam.essay[0].points == 0


def test_exam_config_sends_schema():
    cfg = eg.exam_config()
    assert cfg.response_mime_type == "application/json" and cfg.response_schema is eg.GeneratedExam
    assert cfg.system_instruction == eg.EXAM_SYSTEM


# ---------- prompt (2.4) ----------
def test_build_prompt_contains_passages_and_counts(qdrant):
    src = eg.get_chapter_passages(3, max_chars=2_000, client=qdrant)
    prompt = eg.build_exam_prompt(src, n_mcq=10, n_essay=2)
    assert '<giao_trinh chuong="Chương III">' in prompt and src.prompt_text() in prompt
    assert prompt.rstrip().endswith("tạo 10 câu trắc nghiệm và 2 câu tự luận.")
    assert "[trang 38]" in prompt


def test_build_prompt_without_essay(qdrant):
    src = eg.get_chapter_passages(4, client=qdrant)
    prompt = eg.build_exam_prompt(src, n_mcq=5, n_essay=0)
    assert "0 câu tự luận" in prompt and "essay rỗng" in prompt


@pytest.mark.parametrize("n_mcq,n_essay", [(0, 0), (-1, 2), (3, -1)])
def test_build_prompt_rejects_bad_counts(qdrant, n_mcq, n_essay):
    src = eg.get_chapter_passages(4, client=qdrant)
    with pytest.raises(ValueError):
        eg.build_exam_prompt(src, n_mcq, n_essay)


# ---------- script prototype (2.5, 2.6) ----------
from scripts import gen_exam_proto as proto


def proto_exam_json(mcq_page=38, essay_page=39, points=2):
    return exam_json(mcq=[mcq(source_page=mcq_page)],
                     essay=[{"source_page": essay_page, "question": "Trình bày…",
                             "rubric": "- ý 1 (1 điểm)\n- ý 2 (1 điểm)", "points": points}])


def test_proto_generate_save_and_show(qdrant, tmp_path, fake_gemini):
    llm = fake_gemini([proto_exam_json()])
    record = asyncio.run(proto.generate(3, 1, 1, 100_000, llm, qdrant=qdrant))
    contents, config = llm.calls[0]
    assert "tạo 1 câu trắc nghiệm và 1 câu tự luận" in contents and config.response_schema is eg.GeneratedExam
    assert record["models"] == ["fake-model"] and record["calls"] == 2 and len(record["passages"]) == 20
    assert record["rejected"] == [] and record["notice"] is None
    q = record["exam"]["mcq"][0]
    correct = next(o["content"] for o in q["options"] if o["label"] == q["correct_label"])
    assert correct == "lựa chọn B"   # lựa chọn đã bị xáo, đáp án vẫn đúng nội dung

    path = proto.save(record, tmp_path)
    assert path.name.startswith("gen_chapter3_") and json.loads(path.read_text(encoding="utf-8")) == record
    text = proto.format_exam(record, sources=True)
    assert f"✓ {q['correct_label']}. lựa chọn B" in text and "│ CHƯƠNG III" in text and "Bị loại: không có" in text


def test_proto_shows_rejected_and_notice(qdrant, fake_gemini):
    # trang 99 không được gửi → loại ở tầng cấu trúc; tạo bù cũng hỏng trang → thiếu câu, có thông báo
    llm = fake_gemini([proto_exam_json(mcq_page=99), exam_json(mcq=[mcq(source_page=99)], essay=[])])
    record = asyncio.run(proto.generate(3, 1, 1, 100_000, llm, qdrant=qdrant))
    assert record["exam"]["mcq"] == [] and len(record["rejected"]) == 2
    text = proto.format_exam(record)
    assert "Bị loại (2 câu)" in text and "trang 99 không nằm trong các trang đã gửi" in text
    assert "Chỉ tạo được 0/1 câu trắc nghiệm" in text


def test_proto_validate_saved_file(qdrant, fake_gemini):
    """--validate trên file kiểu 2.5 (chưa qua kiểm tra): chỉ thêm khoá validation, đánh dấu câu bị loại."""
    saved = asyncio.run(proto.generate(3, 1, 1, 100_000, fake_gemini([proto_exam_json()]), qdrant=qdrant,
                                       use_judge=False))
    del saved["rejected"]
    def reject_essay(contents):   # câu trắc nghiệm đạt (tự giải đúng), câu tự luận trùng câu 1
        result = json.loads(fake_gemini.all_pass(contents))
        result["verdicts"][1].update(reason="hỏi lại câu 1", duplicate_of=1)
        return json.dumps(result)
    record = asyncio.run(proto.validate_saved(saved, fake_gemini([], judge=reject_essay)))
    v = record["validation"]
    assert v["kept"] == {"mcq": 1, "essay": 0} and v["calls"] == 1 and len(record["exam"]["essay"]) == 1
    assert v["rejected"][0]["reasons"][0] == "trùng nội dung câu 1"
    assert "✗ BỊ LOẠI" in proto.format_exam(record)


def test_proto_hints_for_old_files(qdrant, fake_gemini):
    record = asyncio.run(proto.generate(3, 1, 1, 100_000, fake_gemini([proto_exam_json()]), qdrant=qdrant,
                                        use_judge=False))
    # giả lập file cũ (2.5) chưa qua kiểm tra: sửa thẳng đề đã lưu
    del record["rejected"]
    record["request"]["n_mcq"] = 2
    record["exam"]["mcq"][0]["source_page"] = 99
    record["exam"]["essay"][0]["points"] = 3
    warn = proto.hints(record)
    assert any("yêu cầu 2 trắc nghiệm" in w for w in warn)
    assert any("Câu 1: trang 99" in w for w in warn)
    assert any("rubric cộng được 2 điểm nhưng points = 3" in w for w in warn)


def test_proto_keeps_raw_text_when_schema_fails(qdrant, fake_gemini):
    record = asyncio.run(proto.generate(3, 1, 0, 100_000, fake_gemini(["{hỏng"]), qdrant=qdrant))
    assert "exam" not in record and record["raw_text"] == "{hỏng" and "không đúng schema" in record["error"]
    assert "LỖI:" in proto.format_exam(record)


def test_shuffle_options_keeps_correct_answer():
    import random
    q = eg.GeneratedMCQ.model_validate(mcq())
    seen = set()
    for seed in range(40):
        s = eg.shuffle_options(q, random.Random(seed))
        assert [o.label for o in s.options] == list("ABCD")
        assert sorted(o.content for o in s.options) == sorted(o.content for o in q.options)
        assert next(o.content for o in s.options if o.label == s.correct_label) == "lựa chọn B"
        seen.add(s.correct_label)
    assert seen == set("ABCD")                  # đáp án đúng rơi vào đủ cả 4 vị trí
    assert q.correct_label == "B"               # không sửa câu gốc


def test_shuffle_leaves_broken_labels_for_validation():
    import random
    q = eg.GeneratedMCQ.model_validate(mcq(options=[{"label": l, "content": l} for l in "AABC"]))
    assert eg.shuffle_options(q, random.Random(0)) is q
