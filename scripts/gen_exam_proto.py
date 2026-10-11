"""
Prototype tạo đề (roadmap 2.5 + 2.6): lấy đoạn văn → Gemini tạo đề → kiểm tra → in đề dễ đọc → lưu JSON
để so sánh giữa các lần chỉnh prompt.

  1. lấy các đoạn trải đều chương (rag.exam_generation.get_chapter_passages) — cần Qdrant đang chạy
  2. rag.exam_generation.generate_exam: Gemini tạo đề (prompt rag/prompts.py + schema GeneratedExam), xáo lựa chọn,
     kiểm tra cấu trúc + giám khảo (rag/exam_validation.py), thiếu câu thì tạo bù tối đa 1 lần
  3. lưu outputs/gen_chapter<số>_<thời điểm>.json: đề đã lọc + các câu bị loại kèm lý do + các đoạn đã gửi
     + prompt hệ thống đã dùng (Gemini trả sai schema thì vẫn lưu lỗi và nguyên văn để xem)
  4. in đề; tự chấm từng câu theo checklist in ở cuối, sửa rag/prompts.py rồi chạy lại

Chạy:  python -m scripts.gen_exam_proto --chapter 3 --mcq 10 --essay 2
       python -m scripts.gen_exam_proto --chapter 3 --sources        # in kèm đoạn giáo trình của trang được trích
       python -m scripts.gen_exam_proto --chapter 3 --no-judge       # chỉ kiểm tra cấu trúc, bớt lượt gọi
       python -m scripts.gen_exam_proto --show outputs/gen_chapter3_X.json --sources      # xem lại, không gọi Gemini
       python -m scripts.gen_exam_proto --validate outputs/gen_chapter3_X.json            # kiểm tra đề đã lưu (2.6)

Chi phí: tạo đề 2–4 lần gọi Gemini (1 tạo + 1 giám khảo, thêm 2 nếu phải tạo bù); --validate 1 lần; --show 0.
Chưa đi qua quota tạo đề của người dùng (2.9).
"""
import argparse
import asyncio
import json
import random
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from google import genai

from rag.exam_generation import (DEFAULT_MAX_CHARS, GeneratedExam, Passage, generate_exam,
                                 get_chapter_passages)
from rag.exam_validation import essay_issues, mcq_issues, rubric_total, validate_exam
from rag.prompts import EXAM_SYSTEM

OUT_DIR = Path(__file__).resolve().parent.parent / "outputs"

CHECKLIST = """Tự chấm từng câu (roadmap 2.5), mục tiêu ~9/10 câu đạt trên ít nhất 3 chương:
  1. Đáp án đúng có thật sự đúng theo giáo trình không?
  2. Có đáp án sai nào cũng đúng không?
  3. Câu hỏi có rõ nghĩa, tự đủ nghĩa khi không thấy đoạn văn không?
  4. Trang trích dẫn có đúng không? (chạy kèm --sources để đối chiếu)
Câu không đạt → thêm/sửa quy tắc trong rag/prompts.py (ghi lý do) rồi chạy lại cùng chương để so sánh."""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


async def generate(chapter, n_mcq: int, n_essay: int, max_chars: int, llm, qdrant=None,
                   rng: random.Random | None = None, use_judge: bool = True) -> dict:
    """Một lần tạo đề → bản ghi đủ để xem lại và so sánh (kể cả khi Gemini trả sai schema)."""
    src = get_chapter_passages(chapter, max_chars, client=qdrant)
    record = {
        "created_at": now(),
        "chapter": src.chapter._asdict(),
        "request": {"n_mcq": n_mcq, "n_essay": n_essay, "max_chars": max_chars, "judge": use_judge},
        "system_prompt": EXAM_SYSTEM,
        "passages": [{"chunk_id": p.chunk_id, "page": p.page, "text": p.text} for p in src.passages],
    }
    t = time.perf_counter()
    try:
        res = await generate_exam(llm, src, n_mcq, n_essay, rng=rng, use_judge=use_judge)
    except ValueError as e:
        record.update(error=str(e), raw_text=getattr(e, "raw", None), elapsed_s=round(time.perf_counter() - t, 1))
        return record
    record.update(models=res.models, calls=res.calls, elapsed_s=round(time.perf_counter() - t, 1),
                  notice=res.notice, exam=res.exam.model_dump(), rejected=[asdict(r) for r in res.rejected])
    return record


async def validate_saved(record: dict, llm, use_judge: bool = True) -> dict:
    """Chạy lớp kiểm tra 2.6 trên đề đã lưu (vd. file từ 2.5) → thêm khoá "validation", không sửa "exam"."""
    exam = GeneratedExam.model_validate(record["exam"])
    passages = [Passage(p["chunk_id"], p["page"], p["text"]) for p in record["passages"]]
    val = await validate_exam(llm, exam, {p.page for p in passages}, "\n\n".join(p.labeled() for p in passages),
                              use_judge=use_judge)
    record["validation"] = {"created_at": now(), "judge": use_judge, "calls": val.judge_calls,
                            "kept": {"mcq": len(val.mcq), "essay": len(val.essay)},
                            "rejected": [asdict(r) for r in val.rejected]}
    return record


def save(record: dict, out_dir: Path = OUT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(record["created_at"]).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"gen_chapter{record['chapter']['number']}_{stamp}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def rejected_of(record: dict) -> list[dict]:
    """Câu bị loại: lúc tạo (đề mới) hoặc khi --validate (đề đã lưu từ trước)."""
    return record.get("rejected") or (record.get("validation") or {}).get("rejected") or []


def hints(record: dict) -> list[str]:
    """Cảnh báo nhanh trên đề đang in. Đề tạo mới đã qua kiểm tra; chủ yếu có ích với file cũ chưa --validate."""
    exam, req = record["exam"], record["request"]
    sent = {p["page"] for p in record["passages"]}
    out = []
    if len(exam["mcq"]) != req["n_mcq"] or len(exam["essay"]) != req["n_essay"]:
        out.append(f"yêu cầu {req['n_mcq']} trắc nghiệm + {req['n_essay']} tự luận, "
                   f"đề có {len(exam['mcq'])} + {len(exam['essay'])}")
    parsed = GeneratedExam.model_validate(exam)
    for i, q in enumerate(parsed.mcq, 1):
        out += [f"Câu {i}: {x}" for x in mcq_issues(q, sent)]
    for i, q in enumerate(parsed.essay, 1):
        out += [f"Tự luận {i}: {x}" for x in essay_issues(q, sent)]
        total = rubric_total(q.rubric)
        if total and abs(total - q.points) > 1e-6:
            out.append(f"Tự luận {i}: rubric cộng được {total:g} điểm nhưng points = {q.points:g}")
    return out


def format_exam(record: dict, sources: bool = False) -> str:
    ch = record["chapter"]
    sent_pages = sorted({p["page"] for p in record["passages"]})
    models = record.get("models") or [record.get("model")]
    lines = [f"{ch['key']}: {ch['title']}",
             f"model {', '.join(map(str, models))} | {record.get('calls', 1)} lần gọi Gemini | "
             f"{record.get('elapsed_s')} s | {len(record['passages'])} đoạn, trang {sent_pages[0]}–{sent_pages[-1]}", ""]
    if "error" in record:
        return "\n".join(lines + [f"LỖI: {record['error']}", "", "Gemini trả về:", str(record.get("raw_text"))])

    by_page: dict[int, list[str]] = {}
    for p in record["passages"]:
        by_page.setdefault(p["page"], []).append(p["text"])
    dropped = {r["question"] for r in rejected_of(record)}

    def source_block(page):
        return [f"      │ {t}" for t in by_page.get(page, ["(không có đoạn nào của trang này trong số đã gửi)"])]

    def mark(q):
        return "  ✗ BỊ LOẠI" if q["question"] in dropped else ""

    exam = record["exam"]
    for i, q in enumerate(exam["mcq"], 1):
        lines.append(f"Câu {i}  [trang {q['source_page']}]{mark(q)}")
        lines.append(f"  {q['question']}")
        for o in q["options"]:
            lines.append(f"  {'✓' if o['label'] == q['correct_label'] else ' '} {o['label']}. {o['content']}")
        lines.append(f"  Giải thích: {q['explanation']}")
        lines += source_block(q["source_page"]) if sources else []
        lines.append("")
    for i, q in enumerate(exam["essay"], 1):
        lines.append(f"Tự luận {i}  [trang {q['source_page']}]  ({q['points']:g} điểm){mark(q)}")
        lines.append(f"  {q['question']}")
        lines += [f"    {r}" for r in q["rubric"].splitlines() if r.strip()]
        lines += source_block(q["source_page"]) if sources else []
        lines.append("")

    used = sorted({q["source_page"] for q in exam["mcq"] + exam["essay"]})
    correct = Counter(q["correct_label"] for q in exam["mcq"])
    longest = sum(max(q["options"], key=lambda o: len(o["content"]))["label"] == q["correct_label"] for q in exam["mcq"])
    lines.append("Đáp án đúng: " + " · ".join(f"{label} {correct[label]}" for label in "ABCD")
                 + f" | đáp án đúng là lựa chọn dài nhất: {longest}/{len(exam['mcq'])} câu")
    lines.append(f"Trang được dùng: {', '.join(map(str, used))} ({len(used)}/{len(sent_pages)} trang đã gửi)")

    rejected = rejected_of(record)
    if rejected:
        lines += ["", f"Bị loại ({len(rejected)} câu):"]
        for r in rejected:
            kind = "trắc nghiệm" if r["kind"] == "mcq" else "tự luận"
            lines.append(f"  ✗ [{kind}, {r['stage']}] {r['question']}")
            lines += [f"      - {x}" for x in r["reasons"]]
    elif "validation" in record or "rejected" in record:
        lines += ["", "Bị loại: không có"]
    if record.get("notice"):
        lines.append(f"\n⚠ {record['notice']}")
    warn = [] if dropped else hints(record)
    lines += ["", "Cảnh báo:", *[f"  ⚠ {w}" for w in warn]] if warn else []
    return "\n".join(lines + ["", CHECKLIST])


async def main():
    ap = argparse.ArgumentParser(description="Prototype tạo đề từ giáo trình bằng Gemini")
    ap.add_argument("--chapter", help='số chương (0 = Mở đầu) hoặc "Chương III"')
    ap.add_argument("--mcq", type=int, default=10, help="số câu trắc nghiệm")
    ap.add_argument("--essay", type=int, default=2, help="số câu tự luận")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="giới hạn độ dài các đoạn gửi đi")
    ap.add_argument("--no-judge", action="store_true", help="chỉ kiểm tra cấu trúc, không gọi giám khảo")
    ap.add_argument("--sources", action="store_true", help="in kèm đoạn giáo trình của trang được trích")
    ap.add_argument("--show", help="in lại file outputs/*.json đã lưu, không gọi Gemini")
    ap.add_argument("--validate", help="chạy lớp kiểm tra 2.6 trên file outputs/*.json đã lưu, ghi kết quả vào file")
    args = ap.parse_args()

    # giữ client trong biến: client tạm bị dọn giữa chừng → lỗi aiohttp "connector is None"
    llm = None if args.show else genai.Client()
    if args.show:
        record = json.loads(Path(args.show).read_text(encoding="utf-8"))
    elif args.validate:
        path = Path(args.validate)
        record = await validate_saved(json.loads(path.read_text(encoding="utf-8")), llm, not args.no_judge)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        kept = record["validation"]["kept"]
        print(f"Đã ghi kết quả kiểm tra vào {path}: giữ {kept['mcq']} trắc nghiệm + {kept['essay']} tự luận\n")
    else:
        if args.chapter is None:
            ap.error("cần --chapter (hoặc --show / --validate với file đã lưu)")
        print(f"Đang tạo {args.mcq} trắc nghiệm + {args.essay} tự luận cho chương {args.chapter}...")
        record = await generate(args.chapter, args.mcq, args.essay, args.max_chars, llm, use_judge=not args.no_judge)
        print(f"Đã lưu {save(record)}\n")
    print(format_exam(record, args.sources))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # console Windows mặc định không in được ✓ ⚠ và chữ có dấu
    asyncio.run(main())
