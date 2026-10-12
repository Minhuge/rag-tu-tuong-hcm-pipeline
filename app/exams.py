"""
Bài kiểm tra — Phase 1 (ROADMAP.md): tạo đề bằng tay, làm bài, chấm trắc nghiệm tự động.
Module này không gọi AI: đề AI (Phase 2, app/exam_ai.py) được lưu qua create_generated_exam, cùng luật với đề tay.

Mọi hàm nhận user_id (lấy từ JWT ở router, KHÔNG bao giờ từ dữ liệu người dùng gửi lên) và tự kiểm tra quyền,
để Phase 5 dùng lại làm tool cho trợ lý AI mà không phải viết lại luật.

Người tạo đề: create_exam → add_question … → publish_exam;  list_my_exams, get_exam_for_creator, list_submissions
              create_generated_exam: đề nháp AI + mọi câu hỏi trong một transaction
              get_submission_for_creator → grade_answer_by_creator: xem bài làm, chấm / sửa điểm từng câu (3.7)
              ai_exams_today / check_ai_quota: số đề AI đã tạo hôm nay (giờ Việt Nam) — roadmap 2.9
              ai_gradings_today: số câu tự luận AI đã chấm hôm nay cho một người làm bài — roadmap 3.9
Người làm bài: get_exam_for_taker → start_submission → save_answer … → submit → get_my_result;  list_my_submissions
Chấm tự luận (Phase 3, app/essay_ai.py gọi — không kiểm tra quyền, không có endpoint):
              ungraded_essays → essay_for_grading → save_essay_grade (không bao giờ ghi đè câu đã có điểm)
              → câu cuối cùng có điểm thì bài tự chuyển "graded" (_finalize_if_complete); finalize_complete_submissions

Lỗi (router đổi thành mã HTTP):
  NotFound      404  không có, HOẶC không được xem (không để lộ đề riêng tư của người khác có tồn tại)
  QuotaExceeded 429  hết lượt tạo đề AI hôm nay
  InvalidState  409  sai trạng thái: sửa đề đã xuất bản, nộp hai lần, hết giờ…
  ValueError    400  dữ liệu sai: lựa chọn của câu khác, câu trắc nghiệm không có đáp án đúng…

Luật database không tự kiểm tra được (kiểm ở đây):
  - lựa chọn được chọn phải thuộc đúng câu hỏi; câu mcq chỉ chọn, câu essay chỉ viết
  - xuất bản: ≥ 1 câu; mỗi câu mcq ≥ 2 lựa chọn và đúng 1 lựa chọn đúng
  - chỉ làm được đề đã xuất bản; visibility (private / link + share_code / public); khung open_at–close_at;
    giới hạn thời gian started_at + duration_minutes
  - chỉ người tạo sửa đề, xem đáp án và xem bài làm của người khác
  - đáp án đúng, rubric, giải thích chỉ hiện cho người làm SAU khi nộp
"""
import random
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app import db
from app.db import (EXAM_VISIBILITY, QUESTION_TYPES, Exam, Question, QuestionOption, Submission, SubmissionAnswer,
                    User, utcnow)

MIN_MCQ_OPTIONS = 2
MAX_OPTIONS = 10
MAX_ESSAY_CHARS = 20_000
DEADLINE_GRACE = timedelta(seconds=30)     # bù trễ mạng: câu trả lời gửi sát giờ vẫn được nhận
VN_TZ = timezone(timedelta(hours=7))       # Việt Nam không đổi giờ theo mùa → không cần gói tzdata
EXAM_FIELDS = {"title", "description", "visibility", "duration_minutes", "open_at", "close_at",
               "shuffle_questions", "max_attempts"}


class NotFound(LookupError):
    pass


class InvalidState(ValueError):
    pass


class QuotaExceeded(Exception):
    pass


def _now() -> datetime:
    """Một chỗ duy nhất lấy giờ hiện tại → test đổi được giờ (monkeypatch exams._now)."""
    return utcnow()


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite (test) trả datetime không múi giờ; giờ gửi lên không kèm múi giờ cũng coi là UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ms(dt: datetime | None) -> int | None:
    return db._ms(dt) if dt is not None else None


# ---------- chuyển sang dict ----------
def _exam_dict(e: Exam, *, creator: bool) -> dict:
    d = {"id": e.id, "title": e.title, "description": e.description, "visibility": e.visibility,
         "status": e.status, "origin": e.origin, "duration_minutes": e.duration_minutes,
         "shuffle_questions": e.shuffle_questions, "max_attempts": e.max_attempts,
         "open_at": _ms(e.open_at), "close_at": _ms(e.close_at),
         "created_at": _ms(e.created_at), "updated_at": _ms(e.updated_at)}
    if creator:
        d["share_code"] = e.share_code       # chỉ người tạo thấy mã chia sẻ
    return d


def _question_dict(q: Question, *, reveal: bool) -> dict:
    """reveal=False: bản cho người làm bài — không có đáp án đúng, rubric, giải thích."""
    d = {"id": q.id, "type": q.type, "order_index": q.order_index, "content": q.content, "points": q.points,
         "options": [{"id": o.id, "label": o.label, "content": o.content} for o in q.options]}
    if reveal:
        d.update(rubric=q.rubric, explanation=q.explanation, source_page=q.source_page)
        for out, o in zip(d["options"], q.options):
            out["is_correct"] = o.is_correct
    return d


def _deadline(sub: Submission, exam: Exam) -> datetime | None:
    """Hạn nộp = mốc sớm hơn giữa (bắt đầu + thời gian làm bài) và giờ đóng đề. None = không giới hạn."""
    ends = []
    if exam.duration_minutes:
        ends.append(_aware(sub.started_at) + timedelta(minutes=exam.duration_minutes))
    if exam.close_at:
        ends.append(_aware(exam.close_at))
    return min(ends) if ends else None


def _submission_summary(sub: Submission, exam: Exam) -> dict:
    return {"id": sub.id, "exam_id": exam.id, "exam_title": exam.title, "status": sub.status,
            "score": sub.score, "max_score": sub.max_score, "started_at": _ms(sub.started_at),
            "deadline": _ms(_deadline(sub, exam)), "submitted_at": _ms(sub.submitted_at),
            "graded_at": _ms(sub.graded_at)}


# ---------- kiểm tra quyền ----------
def _own_exam(s: Session, user_id: int, exam_id: int) -> Exam:
    exam = s.get(Exam, exam_id)
    if exam is None or exam.created_by != user_id:
        raise NotFound("Không tìm thấy đề thi")
    return exam


def _require_draft(exam: Exam) -> None:
    if exam.status != "draft":
        raise InvalidState("Đề đã xuất bản nên không sửa được — hãy tạo đề mới")


def _takeable_exam(s: Session, user_id: int, exam_id: int, share_code: str | None) -> Exam:
    """Đề người này được làm: đã xuất bản VÀ (của chính mình / công khai / có đúng mã chia sẻ)."""
    exam = s.get(Exam, exam_id)
    if exam is not None and exam.status == "published":
        if exam.created_by == user_id or exam.visibility == "public":
            return exam
        if (exam.visibility == "link" and share_code and exam.share_code
                and secrets.compare_digest(share_code, exam.share_code)):
            return exam
    raise NotFound("Không tìm thấy đề thi")


def _own_submission(s: Session, user_id: int, submission_id: int, *, lock: bool = False) -> Submission:
    sub = s.get(Submission, submission_id, with_for_update=lock)
    if sub is None or sub.user_id != user_id:
        raise NotFound("Không tìm thấy bài làm")
    return sub


# ---------- kiểm tra dữ liệu ----------
def _check_exam_fields(fields: dict) -> dict:
    unknown = set(fields) - EXAM_FIELDS
    if unknown:
        raise ValueError(f"Trường không hợp lệ: {', '.join(sorted(unknown))}")
    out = dict(fields)
    if "title" in out:
        out["title"] = (out["title"] or "").strip()
        if not out["title"]:
            raise ValueError("Tiêu đề không được trống")
    if "visibility" in out and out["visibility"] not in EXAM_VISIBILITY:
        raise ValueError(f"visibility phải là một trong: {', '.join(EXAM_VISIBILITY)}")
    for key in ("duration_minutes", "max_attempts"):
        if out.get(key) is not None and out[key] <= 0:
            raise ValueError(f"{key} phải lớn hơn 0")
    for key in ("open_at", "close_at"):
        if key in out:
            out[key] = _aware(out[key])
    return out


def _check_window(exam: Exam) -> None:
    if exam.open_at and exam.close_at and _aware(exam.close_at) <= _aware(exam.open_at):
        raise ValueError("Giờ đóng đề phải sau giờ mở đề")


def _build_options(qtype: str, options: list[dict] | None, rubric: str | None) -> list[QuestionOption]:
    options = options or []
    if qtype == "essay":
        if options:
            raise ValueError("Câu tự luận không có lựa chọn")
        return []
    if rubric:
        raise ValueError("Câu trắc nghiệm không có rubric (rubric chỉ dành cho câu tự luận)")
    if len(options) > MAX_OPTIONS:
        raise ValueError(f"Tối đa {MAX_OPTIONS} lựa chọn")
    labels = [str(o.get("label", "")).strip() for o in options]
    if any(not label for label in labels) or len(set(labels)) != len(labels):
        raise ValueError("Mỗi lựa chọn cần nhãn riêng, không trùng (A, B, C, D…)")
    if any(not str(o.get("content", "")).strip() for o in options):
        raise ValueError("Nội dung lựa chọn không được trống")
    if sum(bool(o.get("is_correct")) for o in options) > 1:
        raise ValueError("Mỗi câu trắc nghiệm chỉ có một đáp án đúng")
    return [QuestionOption(label=label, content=o["content"].strip(), is_correct=bool(o.get("is_correct")),
                           order_index=i) for i, (label, o) in enumerate(zip(labels, options))]


def _check_question_fields(qtype: str, content: str, points: float, source_page: int | None) -> None:
    if qtype not in QUESTION_TYPES:
        raise ValueError(f"type phải là một trong: {', '.join(QUESTION_TYPES)}")
    if not (content or "").strip():
        raise ValueError("Nội dung câu hỏi không được trống")
    if points is None or points < 0:
        raise ValueError("Điểm phải ≥ 0")
    if source_page is not None and source_page <= 0:
        raise ValueError("Số trang phải lớn hơn 0")


# ---------- quota tạo đề AI (2.9) ----------
def _start_of_today_vn() -> datetime:
    """0 giờ hôm nay theo giờ Việt Nam, đổi ra UTC để so với created_at."""
    today = _now().astimezone(VN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return today.astimezone(timezone.utc)


def _count_ai_exams_today(s: Session, user_id: int) -> int:
    """Đếm đề đang có → xoá đề AI thì được trả lại lượt; tạo hỏng (không lưu gì) thì không mất lượt."""
    return s.scalar(select(func.count()).select_from(Exam).where(
        Exam.created_by == user_id, Exam.origin == "ai", Exam.created_at >= _start_of_today_vn()))


def _quota_message(limit: int) -> str:
    return f"Bạn đã tạo đủ {limit} đề bằng AI hôm nay. Lượt mới mở lại lúc 0 giờ (giờ Việt Nam)."


def ai_exams_today(user_id: int) -> int:
    with Session(db.engine) as s:
        return _count_ai_exams_today(s, user_id)


def ai_gradings_today(user_id: int) -> int:
    """
    Số câu AI đã chấm (graded_by="ai") hôm nay trong các bài của người LÀM bài (quota chấm tính cho người làm, 3.1).
    Câu bỏ trống (graded_by="auto") không tính; người tạo đề chấm đè (graded_by="creator") → lượt đó được trả lại.
    """
    with Session(db.engine) as s:
        return s.scalar(select(func.count()).select_from(SubmissionAnswer)
                        .join(Submission, Submission.id == SubmissionAnswer.submission_id)
                        .where(Submission.user_id == user_id, SubmissionAnswer.graded_by == "ai",
                               SubmissionAnswer.graded_at >= _start_of_today_vn()))


def check_ai_quota(user_id: int, limit: int) -> int:
    """Số lượt còn lại hôm nay; hết lượt → QuotaExceeded."""
    used = ai_exams_today(user_id)
    if used >= limit:
        raise QuotaExceeded(_quota_message(limit))
    return limit - used


# ---------- ghi vào session (đề tay và đề AI dùng chung → cùng một bộ luật) ----------
def _new_exam(s: Session, user_id: int, origin: str, fields: dict) -> Exam:
    """fields đã qua _check_exam_fields. Luôn là bản nháp."""
    exam = Exam(created_by=user_id, status="draft", origin=origin, **fields)
    _check_window(exam)
    s.add(exam)
    s.flush()
    return exam


def _new_question(s: Session, exam: Exam, *, type: str, content: str, points: float = 1.0,
                  rubric: str | None = None, explanation: str | None = None, source_page: int | None = None,
                  options: list[dict] | None = None) -> Question:
    """Thêm câu vào cuối đề nháp. Kiểm tra quyền và trạng thái đề là việc của hàm gọi."""
    _check_question_fields(type, content, points, source_page)
    built = _build_options(type, options, rubric)
    last = s.scalar(select(func.max(Question.order_index)).where(Question.exam_id == exam.id))
    q = Question(exam_id=exam.id, type=type, order_index=(last + 1) if last is not None else 0,
                 content=content.strip(), points=points, rubric=rubric, explanation=explanation,
                 source_page=source_page, options=built)
    s.add(q)
    exam.updated_at = _now()
    s.flush()
    return q


# ====================== NGƯỜI TẠO ĐỀ ======================
def create_exam(user_id: int, title: str, **fields) -> dict:
    """Luôn tạo bản nháp (status="draft"), nguồn "manual"."""
    fields = _check_exam_fields({"title": title, **fields})
    with Session(db.engine) as s, s.begin():
        return _exam_dict(_new_exam(s, user_id, "manual", fields), creator=True)


def create_generated_exam(user_id: int, title: str, questions: list[dict], *, daily_limit: int | None = None,
                          **fields) -> dict:
    """
    Đề nháp do AI tạo (origin="ai") cùng mọi câu hỏi, trong MỘT transaction: một câu sai luật → ValueError,
    không lưu gì (không để lại đề dở dang). Mỗi câu là dict cùng tham số với add_question.
    daily_limit: đếm lại quota ngay lúc lưu, giữ khoá dòng users (Postgres) → hai request song song
    không cùng lọt qua lượt cuối. Trả về giống get_exam_for_creator.
    """
    if not questions:
        raise ValueError("Đề cần ít nhất một câu hỏi")
    fields = _check_exam_fields({"title": title, **fields})
    with Session(db.engine) as s, s.begin():
        if daily_limit is not None:
            s.get(User, user_id, with_for_update=True)
            if _count_ai_exams_today(s, user_id) >= daily_limit:
                raise QuotaExceeded(_quota_message(daily_limit))
        exam = _new_exam(s, user_id, "ai", fields)
        saved = [_new_question(s, exam, **q) for q in questions]
        return {**_exam_dict(exam, creator=True), "questions": [_question_dict(q, reveal=True) for q in saved]}


def update_exam(user_id: int, exam_id: int, **fields) -> dict:
    fields = _check_exam_fields(fields)
    with Session(db.engine) as s, s.begin():
        exam = _own_exam(s, user_id, exam_id)
        _require_draft(exam)
        for key, value in fields.items():
            setattr(exam, key, value)
        _check_window(exam)
        s.flush()
        return _exam_dict(exam, creator=True)


def delete_exam(user_id: int, exam_id: int) -> None:
    """Xoá cả câu hỏi, bài làm (kể cả đề đã xuất bản — người tạo toàn quyền với đề của mình)."""
    with Session(db.engine) as s, s.begin():
        s.delete(_own_exam(s, user_id, exam_id))


def add_question(user_id: int, exam_id: int, *, type: str, content: str, points: float = 1.0,
                 rubric: str | None = None, explanation: str | None = None, source_page: int | None = None,
                 options: list[dict] | None = None) -> dict:
    _check_question_fields(type, content, points, source_page)   # dữ liệu sai → 400 trước khi đụng database
    _build_options(type, options, rubric)
    with Session(db.engine) as s, s.begin():
        exam = _own_exam(s, user_id, exam_id)
        _require_draft(exam)
        q = _new_question(s, exam, type=type, content=content, points=points, rubric=rubric,
                          explanation=explanation, source_page=source_page, options=options)
        return _question_dict(q, reveal=True)


def update_question(user_id: int, exam_id: int, question_id: int, **fields) -> dict:
    """Chỉ đổi các trường được gửi. Gửi options → thay toàn bộ danh sách lựa chọn."""
    allowed = {"content", "points", "rubric", "explanation", "source_page", "options"}
    if set(fields) - allowed:
        raise ValueError(f"Trường không hợp lệ: {', '.join(sorted(set(fields) - allowed))}")
    with Session(db.engine) as s, s.begin():
        exam = _own_exam(s, user_id, exam_id)
        _require_draft(exam)
        q = s.get(Question, question_id)
        if q is None or q.exam_id != exam.id:
            raise NotFound("Không tìm thấy câu hỏi")
        merged = {"content": q.content, "points": q.points, "rubric": q.rubric,
                  "explanation": q.explanation, "source_page": q.source_page, **fields}
        _check_question_fields(q.type, merged["content"], merged["points"], merged["source_page"])
        if "options" in fields or "rubric" in fields:
            current = [{"label": o.label, "content": o.content, "is_correct": o.is_correct} for o in q.options]
            new_options = _build_options(q.type, fields.get("options", current), merged["rubric"])
            if "options" in fields:
                q.options = []
                s.flush()                    # xoá lựa chọn cũ trước khi thêm mới (nhãn không trùng)
                q.options = new_options
        q.content, q.points = merged["content"].strip(), merged["points"]
        q.rubric, q.explanation, q.source_page = merged["rubric"], merged["explanation"], merged["source_page"]
        exam.updated_at = _now()
        s.flush()
        return _question_dict(q, reveal=True)


def delete_question(user_id: int, exam_id: int, question_id: int) -> None:
    with Session(db.engine) as s, s.begin():
        exam = _own_exam(s, user_id, exam_id)
        _require_draft(exam)
        q = s.get(Question, question_id)
        if q is None or q.exam_id != exam.id:
            raise NotFound("Không tìm thấy câu hỏi")
        s.delete(q)
        exam.updated_at = _now()


def publish_exam(user_id: int, exam_id: int) -> dict:
    with Session(db.engine) as s, s.begin():
        exam = _own_exam(s, user_id, exam_id)
        _require_draft(exam)
        if not exam.questions:
            raise ValueError("Đề cần ít nhất một câu hỏi")
        for n, q in enumerate(exam.questions, start=1):
            if q.type == "mcq":
                if len(q.options) < MIN_MCQ_OPTIONS:
                    raise ValueError(f"Câu {n}: cần ít nhất {MIN_MCQ_OPTIONS} lựa chọn")
                if sum(o.is_correct for o in q.options) != 1:
                    raise ValueError(f"Câu {n}: cần đúng một đáp án đúng")
        if exam.close_at and _aware(exam.close_at) <= _now():
            raise ValueError("Giờ đóng đề đã qua")
        if exam.visibility == "link" and not exam.share_code:
            exam.share_code = secrets.token_urlsafe(8)
        exam.status = "published"
        exam.updated_at = _now()
        s.flush()
        return _exam_dict(exam, creator=True)


def list_my_exams(user_id: int) -> list[dict]:
    q_count = (select(func.count()).select_from(Question).where(Question.exam_id == Exam.id)
               .correlate(Exam).scalar_subquery())
    s_count = (select(func.count()).select_from(Submission).where(Submission.exam_id == Exam.id)
               .correlate(Exam).scalar_subquery())
    with Session(db.engine) as s:
        rows = s.execute(select(Exam, q_count, s_count).where(Exam.created_by == user_id)
                         .order_by(Exam.updated_at.desc()))
        return [{**_exam_dict(e, creator=True), "question_count": nq, "submission_count": ns} for e, nq, ns in rows]


def get_exam_for_creator(user_id: int, exam_id: int) -> dict:
    """Bản đầy đủ có đáp án đúng, rubric, giải thích — chỉ người tạo."""
    with Session(db.engine) as s:
        exam = _own_exam(s, user_id, exam_id)
        return {**_exam_dict(exam, creator=True),
                "questions": [_question_dict(q, reveal=True) for q in exam.questions]}


def list_submissions(user_id: int, exam_id: int) -> list[dict]:
    """Mọi bài làm của đề này (người tạo xem), mới nhất trước."""
    with Session(db.engine) as s:
        exam = _own_exam(s, user_id, exam_id)
        rows = s.execute(select(Submission, User).join(User, User.id == Submission.user_id)
                         .where(Submission.exam_id == exam.id).order_by(Submission.started_at.desc()))
        return [{**_submission_summary(sub, exam),
                 "user": {"id": u.id, "email": u.email, "display_name": u.display_name}} for sub, u in rows]


def _grading_status(q: Question, a: SubmissionAnswer | None) -> str:
    """Trạng thái chấm một câu đã nộp, suy ra từ database (3.8). "failed" (AI vừa lỗi) chỉ có trong bộ nhớ:
    app/essay_ai.with_grading_status thêm vào."""
    if a is not None and a.score is not None:
        return "graded"
    if q.type == "essay" and not (q.rubric or "").strip():
        return "needs_creator"           # AI không chấm câu không có barem (quyết định 3.1)
    return "grading"


def _creator_submission(s: Session, user_id: int, submission_id: int, *, lock: bool = False) -> Submission:
    """Bài làm thuộc đề của người này (người tạo đề). Không phải → NotFound, như mọi chỗ khác."""
    sub = s.get(Submission, submission_id, with_for_update=lock)
    if sub is None or sub.exam.created_by != user_id:
        raise NotFound("Không tìm thấy bài làm")
    return sub


def _review_dict(s: Session, sub: Submission) -> dict:
    exam = sub.exam
    answers = {a.question_id: a for a in sub.answers}
    taker = s.get(User, sub.user_id)
    items = []
    for q in exam.questions:
        a = answers.get(q.id)
        items.append({**_question_dict(q, reveal=True),
                      "answer": {"selected_option_id": a.selected_option_id if a else None,
                                 "essay_text": a.essay_text if a else None},
                      "score": a.score if a else None, "feedback": a.feedback if a else None,
                      "graded_by": a.graded_by if a else None, "graded_at": _ms(a.graded_at) if a else None,
                      "grading_status": _grading_status(q, a) if sub.status != "in_progress" else None})
    return {**_submission_summary(sub, exam),
            "user": {"id": taker.id, "email": taker.email, "display_name": taker.display_name},
            "questions": items}


def get_submission_for_creator(user_id: int, submission_id: int) -> dict:
    """Bài làm của một người cho đề của mình: câu trả lời, đáp án, rubric, điểm + nhận xét từng câu (AI / tự động / tay)."""
    with Session(db.engine) as s:
        return _review_dict(s, _creator_submission(s, user_id, submission_id))


def grade_answer_by_creator(user_id: int, submission_id: int, question_id: int, *, score: float,
                            feedback: str | None = None) -> dict:
    """
    Người tạo đề chấm / sửa điểm một câu (3.7): ghi đè cả điểm AI hay chấm tự động, graded_by="creator" (AI không bao
    giờ ghi đè lại). Gọi được cả khi AI chưa chấm xong. feedback None → giữ nhận xét cũ; "" → xoá.
    Tính lại tổng điểm bài (đủ câu → "graded"). Trả về bài làm sau khi chấm (giống get_submission_for_creator).
    """
    with Session(db.engine) as s, s.begin():
        sub = _creator_submission(s, user_id, submission_id, lock=True)
        if sub.status == "in_progress":
            raise InvalidState("Bài chưa nộp — chưa chấm được")
        q = s.get(Question, question_id)
        if q is None or q.exam_id != sub.exam_id:
            raise NotFound("Không tìm thấy câu hỏi")
        if score is None or not 0 <= score <= q.points:
            raise ValueError(f"Điểm phải từ 0 đến {q.points:g}")
        answer = s.scalar(select(SubmissionAnswer).where(SubmissionAnswer.submission_id == sub.id,
                                                         SubmissionAnswer.question_id == q.id))
        if answer is None:                 # câu tự luận bỏ trống chưa có dòng nào
            answer = SubmissionAnswer(submission_id=sub.id, question_id=q.id)
            s.add(answer)
        answer.score, answer.graded_by, answer.graded_at = score, "creator", _now()
        if feedback is not None:
            answer.feedback = feedback.strip() or None
        _finalize_if_complete(s, sub)
        return _review_dict(s, sub)


# ====================== NGƯỜI LÀM BÀI ======================
def get_exam_for_taker(user_id: int, exam_id: int, share_code: str | None = None) -> dict:
    """Không có đáp án đúng, rubric, giải thích. shuffle_questions → trộn câu, cố định cho từng người."""
    with Session(db.engine) as s:
        exam = _takeable_exam(s, user_id, exam_id, share_code)
        questions = list(exam.questions)
        if exam.shuffle_questions:
            random.Random(f"{exam.id}:{user_id}").shuffle(questions)
        return {**_exam_dict(exam, creator=False),
                "total_points": sum(q.points for q in questions),
                "questions": [_question_dict(q, reveal=False) for q in questions]}


def start_submission(user_id: int, exam_id: int, share_code: str | None = None) -> dict:
    """Đang có bài chưa nộp → trả lại bài đó (làm tiếp), không tạo bài thứ hai."""
    with Session(db.engine) as s, s.begin():
        exam = _takeable_exam(s, user_id, exam_id, share_code)
        now = _now()
        if exam.open_at and now < _aware(exam.open_at):
            raise InvalidState("Đề chưa đến giờ mở")
        if exam.close_at and now >= _aware(exam.close_at):
            raise InvalidState("Đề đã đóng")
        sub = s.scalar(select(Submission).where(Submission.exam_id == exam.id, Submission.user_id == user_id,
                                                Submission.status == "in_progress"))
        if sub is None:
            sub = Submission(exam_id=exam.id, user_id=user_id, status="in_progress", started_at=now)
            s.add(sub)
            s.flush()
        return _submission_summary(sub, exam)


def save_answer(user_id: int, submission_id: int, question_id: int, *,
                selected_option_id: int | None = None, essay_text: str | None = None) -> dict:
    """Ghi (hoặc ghi đè) câu trả lời một câu. Gửi cả hai trường rỗng → xoá câu trả lời."""
    with Session(db.engine) as s, s.begin():
        sub = _own_submission(s, user_id, submission_id)
        if sub.status != "in_progress":
            raise InvalidState("Bài đã nộp, không sửa được")
        deadline = _deadline(sub, sub.exam)
        if deadline and _now() > deadline + DEADLINE_GRACE:
            raise InvalidState("Đã hết giờ làm bài — hãy nộp bài")
        q = s.get(Question, question_id)
        if q is None or q.exam_id != sub.exam_id:
            raise ValueError("Câu hỏi không thuộc đề này")
        if q.type == "mcq":
            if essay_text is not None:
                raise ValueError("Câu trắc nghiệm chỉ chọn đáp án, không viết bài")
            if selected_option_id is not None and selected_option_id not in {o.id for o in q.options}:
                raise ValueError("Lựa chọn không thuộc câu hỏi này")
        else:
            if selected_option_id is not None:
                raise ValueError("Câu tự luận chỉ viết bài, không chọn đáp án")
            if essay_text is not None and len(essay_text) > MAX_ESSAY_CHARS:
                raise ValueError(f"Bài viết tối đa {MAX_ESSAY_CHARS} ký tự")
        answer = s.scalar(select(SubmissionAnswer).where(SubmissionAnswer.submission_id == sub.id,
                                                         SubmissionAnswer.question_id == q.id))
        if selected_option_id is None and not (essay_text or "").strip():
            if answer is not None:
                s.delete(answer)
            return {"question_id": q.id, "selected_option_id": None, "essay_text": None}
        if answer is None:
            answer = SubmissionAnswer(submission_id=sub.id, question_id=q.id)
            s.add(answer)
        answer.selected_option_id, answer.essay_text = selected_option_id, essay_text
        return {"question_id": q.id, "selected_option_id": selected_option_id, "essay_text": essay_text}


def submit(user_id: int, submission_id: int) -> dict:
    """
    Nộp bài + chấm trắc nghiệm: đúng → đủ điểm câu đó, sai / bỏ trống → 0, graded_by="auto".
    Không có câu tự luận → cộng điểm, status="graded". Có → status="submitted" (chờ chấm tự luận, Phase 3).
    Nộp sau hạn vẫn được (chỉ tính câu trả lời đã lưu trước hạn — save_answer đã chặn sau hạn).
    """
    with Session(db.engine) as s, s.begin():
        sub = _own_submission(s, user_id, submission_id, lock=True)   # khoá dòng: hai lần nộp cùng lúc → chỉ một
        if sub.status != "in_progress":
            raise InvalidState("Bài đã được nộp rồi")
        now = _now()
        answers = {a.question_id: a for a in sub.answers}
        questions = sub.exam.questions
        mcq_total = 0.0
        for q in questions:
            if q.type != "mcq":
                continue
            answer = answers.get(q.id)
            if answer is None:                # bỏ trống → vẫn ghi một dòng 0 điểm (thống kê câu khó, Phase 6)
                answer = SubmissionAnswer(submission_id=sub.id, question_id=q.id)
                sub.answers.append(answer)
            correct = next((o.id for o in q.options if o.is_correct), None)
            answer.score = q.points if answer.selected_option_id is not None and answer.selected_option_id == correct else 0.0
            answer.graded_by, answer.graded_at = "auto", now
            mcq_total += answer.score
        sub.max_score = sum(q.points for q in questions)
        sub.submitted_at = now
        if any(q.type == "essay" for q in questions):
            sub.status = "submitted"
        else:
            sub.status, sub.score, sub.graded_at = "graded", mcq_total, now
        s.flush()
    return get_my_result(user_id, submission_id)


def get_my_result(user_id: int, submission_id: int) -> dict:
    """Bài của chính mình. Chưa nộp: chỉ thấy câu trả lời đã lưu. Đã nộp: thêm đáp án đúng, giải thích, điểm từng câu."""
    with Session(db.engine) as s:
        sub = _own_submission(s, user_id, submission_id)
        exam = sub.exam
        done = sub.status != "in_progress"
        answers = {a.question_id: a for a in sub.answers}
        items = []
        for q in exam.questions:
            a = answers.get(q.id)
            item = {**_question_dict(q, reveal=False),
                    "your_answer": {"selected_option_id": a.selected_option_id if a else None,
                                    "essay_text": a.essay_text if a else None}}
            if done:
                item.update(correct_option_id=next((o.id for o in q.options if o.is_correct), None),
                            explanation=q.explanation, source_page=q.source_page,
                            score=a.score if a else None, feedback=a.feedback if a else None,
                            graded_by=a.graded_by if a else None, grading_status=_grading_status(q, a))
            items.append(item)
        return {**_submission_summary(sub, exam), "questions": items}


def list_my_submissions(user_id: int) -> list[dict]:
    with Session(db.engine) as s:
        rows = s.execute(select(Submission, Exam).join(Exam, Exam.id == Submission.exam_id)
                         .where(Submission.user_id == user_id).order_by(Submission.started_at.desc()))
        return [_submission_summary(sub, exam) for sub, exam in rows]


# ====================== CHẤM TỰ LUẬN (Phase 3) ======================
# Job chấm gọi, không phải người dùng → không nhận user_id, không kiểm tra quyền. Không có router nào gọi thẳng.
AUTO_GRADERS = ("ai", "auto")      # "creator" ghi điểm qua đường khác (3.7), được phép ghi đè


def ungraded_essays(submission_id: int | None = None) -> dict[int, list[int]]:
    """
    {submission_id: [question_id, …]} các câu tự luận chưa có điểm của bài đã nộp (status "submitted"), bài nộp trước
    đứng trước, câu theo thứ tự trong đề. Câu bỏ trống (chưa có dòng submission_answers) cũng tính.
    submission_id: chỉ xét một bài (job sau khi nộp); None: mọi bài (quét bù khi khởi động).
    """
    query = (select(Submission.id, Question.id)
             .join(Question, Question.exam_id == Submission.exam_id)
             .outerjoin(SubmissionAnswer, and_(SubmissionAnswer.submission_id == Submission.id,
                                               SubmissionAnswer.question_id == Question.id))
             .where(Submission.status == "submitted", Question.type == "essay", SubmissionAnswer.score.is_(None))
             .order_by(Submission.submitted_at, Submission.id, Question.order_index))
    if submission_id is not None:
        query = query.where(Submission.id == submission_id)
    out: dict[int, list[int]] = {}
    with Session(db.engine) as s:
        for sid, qid in s.execute(query):
            out.setdefault(sid, []).append(qid)
    return out


def _essay(s: Session, submission_id: int, question_id: int, *, lock: bool = False
           ) -> tuple[Submission, Question, SubmissionAnswer | None]:
    sub = s.get(Submission, submission_id, with_for_update=lock)
    q = s.get(Question, question_id)
    if sub is None or q is None or q.exam_id != sub.exam_id or q.type != "essay":
        raise NotFound("Không tìm thấy câu tự luận trong bài làm")
    if sub.status == "in_progress":
        raise InvalidState("Bài chưa nộp — chưa chấm")
    answer = s.scalar(select(SubmissionAnswer).where(SubmissionAnswer.submission_id == sub.id,
                                                     SubmissionAnswer.question_id == q.id))
    return sub, q, answer


def _finalize_if_complete(s: Session, sub: Submission) -> bool:
    """
    Mọi câu đều có điểm → submissions.score = tổng, status = "graded", graded_at = bây giờ (3.6).
    Tổng luôn đọc lại từ database, không cộng dồn: hai job chấm hai câu của cùng một bài xong cùng lúc vẫn ra đúng.
    Người gọi phải đang khoá dòng submissions (with_for_update) để các lần chốt chạy lần lượt.
    """
    s.flush()
    scores = dict(s.execute(select(SubmissionAnswer.question_id, SubmissionAnswer.score)
                            .where(SubmissionAnswer.submission_id == sub.id)).all())
    questions = sub.exam.questions
    if any(scores.get(q.id) is None for q in questions):
        return False
    sub.score = sum(scores[q.id] for q in questions)
    sub.max_score = sum(q.points for q in questions)
    sub.status, sub.graded_at = "graded", _now()
    return True


def finalize_complete_submissions() -> list[int]:
    """Bài "submitted" mà mọi câu đã có điểm nhưng chưa chốt (vd. chấm xong trước khi có 3.6) → chốt. → các id đã chốt."""
    done = []
    with Session(db.engine) as s:
        ids = s.scalars(select(Submission.id).where(Submission.status == "submitted")).all()
    for sid in ids:
        with Session(db.engine) as s, s.begin():
            sub = s.get(Submission, sid, with_for_update=True)
            if sub is not None and sub.status == "submitted" and _finalize_if_complete(s, sub):
                done.append(sid)
    return done


def essay_for_grading(submission_id: int, question_id: int) -> dict:
    """Những gì cần để chấm một câu tự luận đã nộp. score khác None → câu đã có điểm, không chấm lại."""
    with Session(db.engine) as s:
        sub, q, a = _essay(s, submission_id, question_id)
        return {"submission_id": sub.id, "question_id": q.id, "user_id": sub.user_id,
                "question": q.content, "rubric": q.rubric, "points": q.points, "source_page": q.source_page,
                "essay_text": a.essay_text if a else None,
                "score": a.score if a else None, "graded_by": a.graded_by if a else None}


def save_essay_grade(submission_id: int, question_id: int, *, score: float, feedback: str, graded_by: str) -> bool:
    """
    Ghi điểm câu tự luận NẾU câu đó chưa có điểm. Đã có (người tạo đề chấm tay trong lúc AI đang chấm, hoặc một
    job khác chấm trước) → False, không ghi gì. Khoá dòng submissions → các job chấm cùng một bài ghi lần lượt.
    Câu bỏ trống chưa có dòng submission_answers (submit chỉ tạo cho trắc nghiệm) → tạo dòng mới.
    Câu cuối cùng của bài có điểm → chốt tổng điểm, bài chuyển "graded" ngay trong transaction này.
    """
    if graded_by not in AUTO_GRADERS:
        raise ValueError(f"graded_by phải là một trong: {', '.join(AUTO_GRADERS)}")
    with Session(db.engine) as s, s.begin():
        sub, q, answer = _essay(s, submission_id, question_id, lock=True)
        if not 0 <= score <= q.points:
            raise ValueError(f"Điểm {score:g} ngoài khoảng 0–{q.points:g}")
        if answer is not None and answer.score is not None:
            return False
        if answer is None:
            answer = SubmissionAnswer(submission_id=sub.id, question_id=q.id)
            s.add(answer)
        answer.score, answer.feedback, answer.graded_by, answer.graded_at = score, feedback, graded_by, _now()
        _finalize_if_complete(s, sub)
        return True
