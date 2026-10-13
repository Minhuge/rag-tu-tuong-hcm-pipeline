"""
Tool của Trợ lý bài kiểm tra (roadmap 5.2). Mỗi tool là lớp vỏ mỏng quanh service của Phase 1 (app/exams.py):

  - user_id KHÔNG BAO GIỜ là tham số model điền: agent/graph.py lấy từ JWT qua context rồi truyền vào run(user_id, …).
    Quyền vẫn do service kiểm (chỉ thấy bài của chính mình) — tool không tự viết lại luật nào.
  - Trả về văn bản ngắn, mỗi dòng một mục, để model đọc dễ và tốn ít token (5.3). Kết quả nằm lại trong hội thoại và
    được gửi lại ở MỌI lần gọi model sau đó trong cùng lượt (tối đa 5) → mỗi tool tối đa MAX_RESULT_CHARS: bài / đề
    dài thì rút gọn rồi dừng, kèm ghi chú cách xem phần còn lại (question_number), không cắt giữa một câu.
  - Lỗi model nên biết (không thấy bài…) → ToolError; lỗi khác agent/graph.py chỉ báo chung.

Tool chỉ đọc: list_my_submissions, get_my_submission (bài mình làm), get_my_exam (đề mình tạo) — đọc database,
nằm sẵn trong DATA_TOOLS; search_textbook — đọc giáo trình, cần GuardedRAG đã nạp model nên tạo lúc chạy.
Tool ghi duy nhất (5.8): create_exam_draft — tạo ĐỀ NHÁP bằng AI qua đúng service của trang Tạo đề (Phase 2), không
bao giờ xuất bản / sửa / xoá. Đủ bộ tool cho agent: build_tools(rag).
"""
from collections.abc import Callable
from datetime import datetime

from agent.graph import AgentTool, ToolError, ToolResult
from app import essay_ai, exam_ai, exams
from rag.exam_generation import CHAPTERS
from rag.guards import source_label
from rag.search_rerank import PARTIAL_BAND, retrieval_guard

MAX_LIST = 30                          # bài gần nhất; cũ hơn thì chỉ báo số lượng
# Đo bằng count_tokens của Gemini: tiếng Việt ~3,5–4 ký tự / token, dòng nhiều id / số ~2,2 → 6000 ký tự ≈ 1,5–2,7
# nghìn token. Bài 100 câu sai hết trước khi có giới hạn này: 114 nghìn ký tự ≈ 31 nghìn token, gửi lại tối đa 5 lần.
MAX_RESULT_CHARS = 6000
_NOTE_ROOM = 400                       # chừa chỗ cho các ghi chú cuối kết quả
MAX_TITLE_CHARS = 100
STATUS_TEXT = {"in_progress": "đang làm, chưa nộp", "submitted": "đã nộp, đang chấm tự luận — chưa có điểm tổng"}


def _num(x: float) -> str:
    """7.0 → "7", 7.5 → "7,5" — giống trang kết quả (dấu phẩy thập phân)."""
    return f"{x:g}".replace(".", ",")


def _time(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, exams.VN_TZ).strftime("%d/%m/%Y %H:%M") if ms is not None else "?"


def _one_line(text: str | None, limit: int = 100) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _size(lines: list[str]) -> int:
    return sum(len(line) + 1 for line in lines)


def _fit(head: list[str], blocks: list[list[str]], total: int, more: Callable[[int, int], str],
         notes: list[str] = ()) -> str:
    """
    head + càng nhiều khối (một câu / một bài = một khối, không cắt giữa khối) càng tốt trong MAX_RESULT_CHARS.
    total: số mục thật (có thể nhiều hơn len(blocks)); còn thiếu → thêm more(số mục bị bỏ, số thứ tự mục đầu bị bỏ).
    """
    out, used, shown = list(head), _size(head), 0
    for block in blocks:
        if used + _size(block) > MAX_RESULT_CHARS - _NOTE_ROOM:
            break
        out += block
        used += _size(block)
        shown += 1
    out += notes
    if shown < total:
        out.append(more(total - shown, shown + 1))
    return "\n".join(out)


def _int_arg(value, name: str) -> int:
    """Model đôi khi gửi số dạng 15.0 hoặc "15"."""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise ToolError(f"{name} phải là số nguyên.") from None
    if n != float(value):
        raise ToolError(f"{name} phải là số nguyên.")
    return n


# ====================== 1. list_my_submissions ======================
def list_my_submissions(user_id: int) -> str:
    """Các bài đã làm, mới nhất trước — để agent biết người dùng đang nói tới bài nào (tên đề, ngày nộp, lần làm)."""
    subs = exams.list_my_submissions(user_id)            # service chỉ trả bài của user_id, mới bắt đầu trước
    if not subs:
        return "Bạn chưa làm bài kiểm tra nào."
    # lần làm thứ mấy của cùng một đề: đếm từ lần cũ nhất
    attempt, total = {}, {}
    for s in reversed(subs):
        total[s["exam_id"]] = total.get(s["exam_id"], 0) + 1
        attempt[s["id"]] = total[s["exam_id"]]
    descriptions = exams.taken_exam_descriptions(user_id)

    lines = []
    for s in subs[:MAX_LIST]:
        result = (f"{_num(s['score'])}/{_num(s['max_score'])} điểm" if s["status"] == "graded"
                  else STATUS_TEXT[s["status"]])
        when = f"nộp {_time(s['submitted_at'])}" if s["submitted_at"] else f"bắt đầu {_time(s['started_at'])}"
        parts = [f"submission_id={s['id']}", _one_line(s["exam_title"], MAX_TITLE_CHARS), when, result]
        if total[s["exam_id"]] > 1:
            parts.append(f"lần {attempt[s['id']]}/{total[s['exam_id']]} của đề này")
        if desc := _one_line(descriptions.get(s["exam_id"]), 80):
            parts.append(f"mô tả: {desc}")
        lines.append(" | ".join(parts))
    return _fit([], [[line] for line in lines], len(subs), lambda left, _: f"… và {left} bài cũ hơn (không liệt kê).")


LIST_MY_SUBMISSIONS = AgentTool(
    name="list_my_submissions",
    description=("Danh sách bài kiểm tra người dùng đã làm, mới nhất trước. Mỗi dòng: submission_id, tên đề, thời "
                 "điểm nộp, điểm (hoặc trạng thái nếu chưa chấm xong), lần làm thứ mấy nếu làm một đề nhiều lần, mô "
                 "tả đề. Gọi đầu tiên khi người dùng hỏi về bài / điểm của mình để biết họ nói tới bài nào."),
    parameters={"type": "object", "properties": {}},
    run=list_my_submissions,
)


# ====================== 2. get_my_submission ======================
# Giới hạn độ dài từng phần (5.3); cả kết quả thì theo MAX_RESULT_CHARS (xem DETAIL_CHARS bên dưới).
MAX_QUESTION_CHARS = 300
MAX_ESSAY_CHARS = 600
MAX_FEEDBACK_CHARS = 500
MAX_EXPLANATION_CHARS = 400
GRADED_BY = {"ai": "AI chấm", "creator": "người ra đề chấm", "auto": "chấm tự động"}
# Bài dài rút gọn theo 3 bậc để câu nào cũng còn ít nhất kết quả + trang (đủ cho "tôi yếu phần nào"):
#   chi tiết (câu sai) tới DETAIL_CHARS → một dòng có nội dung câu tới SHORT_CHARS → chỉ "Câu n — sai — trang p".
DETAIL_CHARS = 2500
SHORT_CHARS = 3800


def _option(q: dict, option_id: int | None) -> str:
    o = next((o for o in q["options"] if o["id"] == option_id), None)
    return f"{o['label']}. {_one_line(o['content'], 150)}" if o else "?"


def _verdict(q: dict) -> str:
    """Giống huy hiệu trên trang kết quả: đúng / sai / bỏ trống / một phần / đang chấm…"""
    if q["type"] == "mcq":
        chosen = q["your_answer"]["selected_option_id"]
        if chosen is None:
            return "bỏ trống"
        return "đúng" if chosen == q["correct_option_id"] else "sai"
    if q["grading_status"] != "graded":
        return q["grading_message"] or "chưa chấm"
    if q["score"] >= q["points"]:
        return "đủ điểm"
    return "0 điểm" if not q["score"] else "được một phần điểm"


def _question_detail(n: int, q: dict) -> list[str]:
    kind = "trắc nghiệm" if q["type"] == "mcq" else "tự luận"
    lines = [f"Câu {n} ({kind}, {_num(q['points'])} điểm) — {_verdict(q)}: {_one_line(q['content'], MAX_QUESTION_CHARS)}"]
    if q["type"] == "mcq":
        chosen = q["your_answer"]["selected_option_id"]
        lines.append(f"  Bạn chọn: {_option(q, chosen)}" if chosen is not None else "  Bạn bỏ trống câu này.")
        lines.append(f"  Đáp án đúng: {_option(q, q['correct_option_id'])}")
    else:
        text = q["your_answer"]["essay_text"]
        lines.append(f"  Bài làm: {_one_line(text, MAX_ESSAY_CHARS)}" if (text or "").strip() else "  Bạn bỏ trống câu này.")
        if q["grading_status"] == "graded":
            by = GRADED_BY.get(q["graded_by"])
            lines.append(f"  Điểm: {_num(q['score'])}/{_num(q['points'])}" + (f" ({by})" if by else ""))
        if q["feedback"]:
            lines.append(f"  Nhận xét: {_one_line(q['feedback'], MAX_FEEDBACK_CHARS)}")
    if q["explanation"]:
        lines.append(f"  Giải thích: {_one_line(q['explanation'], MAX_EXPLANATION_CHARS)}")
    if q["source_page"]:
        lines.append(f"  Trang giáo trình: {q['source_page']}")
    return lines


def _question_short(n: int, q: dict) -> str:
    """Câu làm đúng / đủ điểm khi xem cả bài: một dòng là đủ (đỡ tốn token cho câu không cần giải thích)."""
    page = f" — trang {q['source_page']}" if q["source_page"] else ""
    return f"Câu {n} ({'trắc nghiệm' if q['type'] == 'mcq' else 'tự luận'}) — {_verdict(q)}: {_one_line(q['content'], 80)}{page}"


def _question_compact(n: int, q: dict) -> str:
    page = f" — trang {q['source_page']}" if q["source_page"] else ""
    return f"Câu {n} — {_verdict(q)}{page}"


def get_my_submission(user_id: int, submission_id, question_number=None) -> str:
    """
    Một bài của chính mình: điểm, rồi từng câu đánh số Câu 1, Câu 2… theo đúng thứ tự trang kết quả.
    Bài chưa nộp: chỉ tiến độ — không đáp án (service không trả), cũng không nội dung câu hỏi (không để agent giúp
    làm bài đang thi).
    """
    sid = _int_arg(submission_id, "submission_id")
    try:
        r = essay_ai.with_grading_status(exams.get_my_result(user_id, sid))
    except exams.NotFound:
        # bài không tồn tại và bài của người khác: cùng một câu trả lời → không lộ bài nào có thật
        raise ToolError(f"Không có bài làm submission_id={sid} của bạn. Gọi list_my_submissions để xem id đúng.") from None
    qs = r["questions"]

    if r["status"] == "in_progress":
        answered = sum(bool(q["your_answer"]["selected_option_id"] is not None
                            or (q["your_answer"]["essay_text"] or "").strip()) for q in qs)
        left = (f", còn khoảng {max(1, round(r['remaining_seconds'] / 60))} phút"
                if r["remaining_seconds"] is not None else "")
        return (f"{_one_line(r['exam_title'], MAX_TITLE_CHARS)} (submission_id={sid}) — ĐANG LÀM, chưa nộp{left}. Đã trả lời {answered}/{len(qs)} câu.\n"
                "Chưa có điểm và đáp án; không cung cấp nội dung câu hỏi khi bài đang làm.")

    if question_number is not None:
        n = _int_arg(question_number, "question_number")
        if not 1 <= n <= len(qs):
            raise ToolError(f"Bài này chỉ có {len(qs)} câu (Câu 1 đến Câu {len(qs)}).")
        return "\n".join([_header(r, sid), *_question_detail(n, qs[n - 1])])

    # 3 bậc (xem DETAIL_CHARS); bậc chỉ đi xuống. Vẫn quá MAX_RESULT_CHARS thì _fit dừng kèm ghi chú.
    head = [_header(r, sid)]
    blocks, used, short_from, compact_from = [], _size(head), None, None
    for n, q in enumerate(qs, 1):
        block = None
        if short_from is None and _verdict(q) not in ("đúng", "đủ điểm"):
            detail = _question_detail(n, q)
            if used + _size(detail) <= DETAIL_CHARS:
                block = detail
            else:
                short_from = n
        if block is None and compact_from is None:
            short = [_question_short(n, q)]
            if used + _size(short) <= SHORT_CHARS:
                block = short
            else:
                compact_from = n
        block = block or [_question_compact(n, q)]
        blocks.append(block)
        used += _size(block)
    notes = []
    if short_from or compact_from:
        cut = f"từ Câu {short_from or compact_from} câu sai không kèm đáp án"
        if compact_from:
            cut += f", từ Câu {compact_from} chỉ còn kết quả và trang"
        notes.append(f"(Bài dài nên rút gọn: {cut}. Gọi lại với question_number để xem đủ một câu.)")
    return _fit(head, blocks, len(qs), lambda left, first: (
        f"… còn {left} câu (Câu {first}–{len(qs)}) chưa liệt kê vì bài dài — xem từng câu bằng question_number."), notes)


def _header(r: dict, sid: int) -> str:
    qs = r["questions"]
    mcq = [q for q in qs if q["type"] == "mcq"]
    correct = sum(_verdict(q) == "đúng" for q in mcq)
    if r["status"] == "graded":
        score = f"{_num(r['score'])}/{_num(r['max_score'])} điểm"
    else:   # còn câu tự luận chưa có điểm: cộng các câu đã có (như "điểm tạm" trên trang kết quả)
        partial = sum(q["score"] or 0 for q in qs)
        pending = sum(q["grading_status"] != "graded" for q in qs)
        score = f"điểm tạm {_num(partial)}/{_num(r['max_score'])} — còn {pending} câu tự luận chưa có điểm"
    mcq_part = f" | trắc nghiệm đúng {correct}/{len(mcq)} câu" if mcq else ""
    return (f"{_one_line(r['exam_title'], MAX_TITLE_CHARS)} (submission_id={sid}) | nộp {_time(r['submitted_at'])} | {score}{mcq_part} | "
            f"{len(qs)} câu")


GET_MY_SUBMISSION = AgentTool(
    name="get_my_submission",
    description=("Chi tiết một bài người dùng đã làm (submission_id lấy từ list_my_submissions): tổng điểm, rồi từng "
                 "câu đánh số Câu 1, Câu 2… giống trang kết quả. Câu sai / bỏ trống / thiếu điểm có đủ: lựa chọn của "
                 "người dùng, đáp án đúng, bài tự luận, nhận xét, giải thích, trang giáo trình; câu làm đúng chỉ một "
                 "dòng. Truyền question_number để xem đầy đủ riêng một câu (kể cả câu làm đúng). Bài đang làm dở: chỉ "
                 "có tiến độ, không có đáp án."),
    parameters={"type": "object", "properties": {
        "submission_id": {"type": "integer", "description": "submission_id trong list_my_submissions"},
        "question_number": {"type": "integer", "description": "Số thứ tự câu như trên trang kết quả (Câu 1 = 1). "
                                                              "Bỏ trống để xem cả bài."},
    }, "required": ["submission_id"]},
    run=get_my_submission,
)


# ====================== 3. get_my_exam ======================
# Đề do chính người dùng TẠO (khác bài mình làm ở tool 2). Roadmap gốc là get_question(question_id) cho cả người làm
# bài, nhưng người làm bài đã có đủ đáp án qua get_my_submission → tool này chỉ phục vụ người tạo đề, đánh số câu như
# trang soạn đề, không dùng id nội bộ của câu.
EXAM_STATUS = {"draft": "bản nháp", "published": "đã xuất bản"}
VISIBILITY = {"private": "riêng tư", "link": "chia sẻ qua link", "public": "công khai"}
MAX_RUBRIC_CHARS = 600


def _exam_line(e: dict, n_questions: int, total_points: float | None = None) -> str:
    parts = [f"exam_id={e['id']}", _one_line(e["title"], MAX_TITLE_CHARS), f"{EXAM_STATUS[e['status']]}, {VISIBILITY[e['visibility']]}"]
    if e["origin"] == "ai":
        parts.append("AI tạo")
    parts.append(f"{n_questions} câu" + (f", {_num(total_points)} điểm" if total_points is not None else ""))
    if e["duration_minutes"]:
        parts.append(f"{e['duration_minutes']} phút")
    return " | ".join(parts)


def _correct(q: dict) -> str:
    o = next((o for o in q["options"] if o["is_correct"]), None)
    return f"{o['label']}. {_one_line(o['content'], 150)}" if o else "chưa chọn đáp án đúng"


def _exam_question_short(n: int, q: dict) -> str:
    kind = "trắc nghiệm" if q["type"] == "mcq" else "tự luận"
    key = (f"đáp án đúng: {_correct(q)}" if q["type"] == "mcq"
           else f"barem: {_one_line(q['rubric'], 150) or 'chưa có (AI không chấm, người tạo đề chấm tay)'}")
    page = f" — trang {q['source_page']}" if q["source_page"] else ""
    return f"Câu {n} ({kind}, {_num(q['points'])} điểm): {_one_line(q['content'], 150)} — {key}{page}"


def _exam_question_full(n: int, q: dict) -> list[str]:
    kind = "trắc nghiệm" if q["type"] == "mcq" else "tự luận"
    lines = [f"Câu {n} ({kind}, {_num(q['points'])} điểm): {_one_line(q['content'], MAX_QUESTION_CHARS)}"]
    if q["type"] == "mcq":
        lines += [f"  {o['label']}. {_one_line(o['content'], 150)}" + (" (đáp án đúng)" if o["is_correct"] else "")
                  for o in q["options"]]
    else:
        lines.append(f"  Barem: {_one_line(q['rubric'], MAX_RUBRIC_CHARS) or 'chưa có (người tạo đề chấm tay)'}")
    if q["explanation"]:
        lines.append(f"  Giải thích: {_one_line(q['explanation'], MAX_EXPLANATION_CHARS)}")
    if q["source_page"]:
        lines.append(f"  Trang giáo trình: {q['source_page']}")
    return lines


def get_my_exam(user_id: int, exam_id=None, question_number=None) -> str:
    """
    Không có exam_id → danh sách đề mình đã tạo. Có exam_id → cả đề kèm đáp án (mỗi câu một dòng).
    Thêm question_number → riêng câu đó, đủ mọi lựa chọn, giải thích, barem.
    Chỉ người tạo đề: đề của người khác và đề không tồn tại trả cùng một thông báo.
    """
    if exam_id is None:
        if question_number is not None:
            raise ToolError("Cần exam_id để xem một câu. Gọi get_my_exam không tham số để xem danh sách đề.")
        mine = exams.list_my_exams(user_id)        # mới sửa gần nhất trước
        if not mine:
            return "Bạn chưa tạo đề nào."
        lines = [_exam_line(e, e["question_count"]) + f" | {e['submission_count']} bài nộp" for e in mine[:MAX_LIST]]
        return _fit([], [[line] for line in lines], len(mine),
                    lambda left, _: f"… và {left} đề cũ hơn (không liệt kê).")

    eid = _int_arg(exam_id, "exam_id")
    try:
        e = exams.get_exam_for_creator(user_id, eid)
    except exams.NotFound:
        raise ToolError(f"Không có đề exam_id={eid} do bạn tạo. Gọi get_my_exam không tham số để xem id đúng.") from None
    qs = e["questions"]
    head = _exam_line(e, len(qs), sum(q["points"] for q in qs))
    if question_number is not None:
        n = _int_arg(question_number, "question_number")
        if not 1 <= n <= len(qs):
            raise ToolError(f"Đề này có {len(qs)} câu (Câu 1 đến Câu {len(qs)})." if qs else "Đề này chưa có câu nào.")
        return "\n".join([head, *_exam_question_full(n, qs[n - 1])])
    if not qs:
        return "\n".join([head, "Đề chưa có câu nào."])
    return _fit([head], [[_exam_question_short(n, q)] for n, q in enumerate(qs, 1)], len(qs), lambda left, first: (
        f"… còn {left} câu (Câu {first}–{len(qs)}) chưa liệt kê vì đề dài — xem từng câu bằng question_number."))


GET_MY_EXAM = AgentTool(
    name="get_my_exam",
    description=("Đề kiểm tra do CHÍNH người dùng tạo (không phải bài họ làm — bài đã làm thì dùng "
                 "get_my_submission). Không truyền gì: danh sách đề đã tạo (exam_id, tên, trạng thái, số câu, số bài "
                 "nộp). Truyền exam_id: cả đề, mỗi câu một dòng kèm đáp án đúng / barem và trang giáo trình. Thêm "
                 "question_number: riêng câu đó với đủ các lựa chọn, giải thích, barem."),
    parameters={"type": "object", "properties": {
        "exam_id": {"type": "integer", "description": "exam_id trong danh sách đề đã tạo. Bỏ trống để xem danh sách."},
        "question_number": {"type": "integer", "description": "Số thứ tự câu trong đề (Câu 1 = 1). Cần exam_id."},
    }},
    run=get_my_exam,
)


# ====================== 4. search_textbook ======================
# Khác 3 tool trên: đọc giáo trình (Qdrant + reranker trên GPU) chứ không đọc database, không cần user_id. Dùng chung
# đường tìm của chế độ hỏi giáo trình (GuardedRAG.search: cùng embedding, reranker, khoá GPU) và cùng retrieval guard:
# không có đoạn đủ liên quan thì nói thẳng là không có, để agent không giải thích bằng kiến thức ngoài giáo trình.
MAX_QUERY_CHARS = 300
MAX_PASSAGE_CHARS = 900
CHAPTER_NUMBERS = {c.number: c for c in CHAPTERS}


def search_textbook(rag, query, chapter=None) -> str:
    q = " ".join(str(query or "").split())[:MAX_QUERY_CHARS]
    if not q:
        raise ToolError("query không được để trống.")
    ch = None
    if chapter is not None:
        ch = _int_arg(chapter, "chapter")
        if ch not in CHAPTER_NUMBERS:
            raise ToolError("chapter phải từ 0 (Chương Mở đầu) đến 7 (Chương VII).")
    try:
        ranked = rag.search(q, ch)
    except RuntimeError as e:                  # Ollama không phản hồi khi tạo embedding
        raise ToolError(f"Chưa tìm được trong giáo trình: {e}") from None

    where = f" trong {CHAPTER_NUMBERS[ch].key}" if ch is not None else ""
    decision = retrieval_guard(ranked)
    if decision == "refuse":
        return (f"Không tìm thấy đoạn giáo trình nào đủ liên quan tới \"{q}\"{where}. "
                "Không giải thích phần này bằng kiến thức ngoài giáo trình.")
    lines = [f"Các đoạn giáo trình liên quan tới \"{q}\"{where}:"]
    if decision == "partial":
        lines.append("Lưu ý: các đoạn chỉ liên quan một phần — chỉ dùng ý thật sự khớp, nói rõ nếu giáo trình không đề cập.")
    # đoạn đứng sau mà reranker chấm thấp: bỏ, đỡ tốn token và tránh trích nhầm đoạn lạc đề (đoạn đầu luôn ≥ PARTIAL_BAND)
    kept = [it for it in ranked if it["rerank_score"] >= PARTIAL_BAND]
    for i, it in enumerate(kept, 1):
        md = it["metadata"]
        text = " ".join((md.get("text") or "").split()).lstrip(" .,;:")   # chunk hay bắt đầu bằng ". " (chỗ cắt câu)
        lines.append(f"[{i}] {source_label(md)}: {_one_line(text, MAX_PASSAGE_CHARS)}")
    return "\n".join(lines)


def search_textbook_tool(rag) -> AgentTool:
    chapters = ", ".join(f"{c.number} = {c.key}" for c in CHAPTERS)
    return AgentTool(
        name="search_textbook",
        description=("Tìm trong giáo trình Tư tưởng Hồ Chí Minh các đoạn liên quan tới một nội dung, kèm chương và "
                     "trang — dùng để giải thích vì sao đáp án đúng / sai, hoặc ôn lại phần người dùng làm sai. Trả "
                     "tối đa 4 đoạn; không có đoạn đủ liên quan thì báo không có."),
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "Nội dung cần tìm, viết cụ thể bằng tiếng Việt (vd. nội dung "
                                                       "câu hỏi kèm đáp án đúng) — không viết kiểu \"câu 2\"."},
            "chapter": {"type": "integer", "description": f"Chỉ tìm trong một chương: {chapters}. Bỏ trống để tìm "
                                                          "cả giáo trình."},
        }, "required": ["query"]},
        run=lambda _user_id, query, chapter=None: search_textbook(rag, query, chapter),
    )


# ====================== 5. create_exam_draft (tool ghi duy nhất, 5.8) ======================
# Gọi exam_ai.generate_exam_draft — cùng service, cùng luật với trang Tạo đề (4.4): kiểm số câu / chương, quota tạo đề
# AI mỗi ngày (AI_GEN_PER_USER_PER_DAY), mỗi người một đề đang tạo, chỉ lưu đề NHÁP, lỗi thì không lưu gì.
# once_per_turn: mỗi tin nhắn tạo tối đa một đề. Lượt gọi Gemini bên trong việc tạo đề tính vào quota tạo đề, không
# trừ quota trợ lý (quota trợ lý chỉ đếm lần agent gọi Gemini để suy nghĩ / trả lời).
DEFAULT_MCQ, DEFAULT_ESSAY = 10, 2      # giống mặc định của POST /exams/generate


def draft_link(exam_id: int) -> str:
    return f"/exams/{exam_id}/edit"


async def create_exam_draft(user_id: int, chapter, n_mcq=DEFAULT_MCQ, n_essay=DEFAULT_ESSAY, *, llm, qdrant,
                            generate=exam_ai.generate_exam_draft) -> ToolResult:
    ch = _int_arg(chapter, "chapter")
    if ch not in CHAPTER_NUMBERS:
        raise ToolError("chapter phải từ 0 (Chương Mở đầu) đến 7 (Chương VII).")
    n_mcq, n_essay = _int_arg(n_mcq, "n_mcq"), _int_arg(n_essay, "n_essay")
    try:
        exam = await generate(user_id, ch, n_mcq, n_essay, llm=llm, qdrant=qdrant)
    except (ValueError, exams.QuotaExceeded) as e:        # số câu sai, hết lượt tạo đề hôm nay, đang tạo đề khác
        raise ToolError(f"Chưa tạo đề: {e}") from None
    except exam_ai.GenerationFailed as e:                 # Qdrant / Gemini lỗi: không có gì được lưu
        raise ToolError(f"Chưa tạo được đề: {e} Không có gì được lưu, có thể thử lại sau.") from None

    g, qs = exam["generation"], exam["questions"]
    mcq = sum(q["type"] == "mcq" for q in qs)
    link = draft_link(exam["id"])
    lines = [f"Đã tạo ĐỀ NHÁP \"{_one_line(exam['title'], MAX_TITLE_CHARS)}\": {len(qs)} câu ({mcq} trắc nghiệm, "
             f"{len(qs) - mcq} tự luận), từ {g['chapter']['chapter']}. Chưa xuất bản — chỉ người dùng thấy."]
    if g.get("notice"):
        lines.append(f"Lưu ý: {g['notice']}")
    lines += [f"Link xem lại, sửa và xuất bản: {link}",
              f"Còn {g['quota']['remaining']}/{g['quota']['limit']} lượt tạo đề bằng AI hôm nay."]
    return ToolResult("\n".join(lines), {"exam_id": exam["id"], "title": exam["title"], "questions": len(qs),
                                         "link": link})


def create_exam_draft_tool(llm, qdrant, generate=exam_ai.generate_exam_draft) -> AgentTool:
    chapters = ", ".join(f"{c.number} = {c.key}" for c in CHAPTERS)

    async def run(user_id, chapter, n_mcq=DEFAULT_MCQ, n_essay=DEFAULT_ESSAY):
        return await create_exam_draft(user_id, chapter, n_mcq, n_essay, llm=llm, qdrant=qdrant, generate=generate)

    return AgentTool(
        name="create_exam_draft",
        description=(f"Tạo một ĐỀ NHÁP bằng AI từ một chương giáo trình (mất 15–60 giây). Chỉ gọi khi người dùng yêu cầu "
                     f"tạo đề rõ ràng trong tin nhắn hiện tại. Đề chưa được xuất bản: trả về link để người dùng xem "
                     f"lại, sửa rồi tự xuất bản. Tối đa {exam_ai.MAX_MCQ} câu trắc nghiệm, {exam_ai.MAX_ESSAY} câu tự "
                     f"luận; có giới hạn số đề AI mỗi ngày."),
        parameters={"type": "object", "properties": {
            "chapter": {"type": "integer", "description": f"Chương: {chapters}."},
            "n_mcq": {"type": "integer", "description": f"Số câu trắc nghiệm (0–{exam_ai.MAX_MCQ}), mặc định "
                                                        f"{DEFAULT_MCQ}."},
            "n_essay": {"type": "integer", "description": f"Số câu tự luận (0–{exam_ai.MAX_ESSAY}), mặc định "
                                                          f"{DEFAULT_ESSAY}."},
        }, "required": ["chapter"]},
        run=run,
        once_per_turn=True,
    )


DATA_TOOLS = [LIST_MY_SUBMISSIONS, GET_MY_SUBMISSION, GET_MY_EXAM]


def build_tools(rag, generate=exam_ai.generate_exam_draft) -> list[AgentTool]:
    """
    Đủ bộ tool: 4 tool chỉ đọc + create_exam_draft. rag: GuardedRAG đã nạp model (app/deps.get_rag()).
    generate: hàm tạo đề (script đánh giá / test truyền bản giả không gọi Gemini).
    """
    llm = getattr(rag, "llm", None)
    qdrant = getattr(getattr(rag, "retriever", None), "client", None)
    return [*DATA_TOOLS, search_textbook_tool(rag), create_exam_draft_tool(llm, qdrant, generate)]
