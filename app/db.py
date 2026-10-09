"""
Lưu lịch sử chat vào PostgreSQL bằng SQLAlchemy.

  users                  conversations                messages
  ─────                  ─────────────                ────────────────
  id (PK)  ◀──────┬───── user_id (FK)                 id (PK)
  email (unique)  │      id (PK)        ◀───────────── conversation_id (FK)
  password_hash   │      title                        role ("user" / "assistant")
  display_name    │      created_at                   content
  is_active       │      updated_at                   meta (JSON: nguồn, các lớp kiểm tra, thời gian)
  is_admin        │                                   created_at
  created_at      │      refresh_tokens
  last_login_at   │      ──────────────
                  └───── user_id (FK)
                         id (PK), token_hash (SHA-256, không lưu token gốc),
                         created_at, expires_at, revoked_at

  Đề thi (người dùng nào cũng tạo được đề và làm đề):

  exams ──1:N── questions ──1:N── question_options
    │               │                   │ (0..1)
   1:N             1:N                 0:N
    │               │                   │
  submissions ──1:N── submission_answers ┘
    │
  users (người làm bài, user_id) — exams.created_by cũng trỏ về users

  exams              : created_by, title, description, visibility ("private"/"link"/"public"),
                       status ("draft"/"published"), origin ("manual"/"ai"), max_attempts,
                       share_code (unique), duration_minutes, shuffle_questions, open_at, close_at
  questions          : exam_id, type ("mcq"/"essay"), order_index, content, rubric (chỉ essay), points,
                       explanation, source_page
  question_options   : question_id, label ("A".."D"), content, is_correct, order_index   (chỉ mcq)
  submissions        : exam_id, user_id, status ("in_progress"/"submitted"/"graded"),
                       score, max_score, started_at, submitted_at, graded_at
  submission_answers : submission_id, question_id, selected_option_id (mcq) / essay_text (essay),
                       score, feedback, graded_by ("auto"/"ai"/"creator"), graded_at
                       — mỗi bài làm trả lời mỗi câu tối đa 1 lần

conversations.mode: "docs" = hỏi giáo trình (RAG), "exam" = trợ lý bài kiểm tra; giữ hai lịch sử tách nhau.
Mỗi người chỉ thấy cuộc trò chuyện của chính mình (user_id). Xoá user → xoá dây chuyền
cuộc trò chuyện, tin nhắn, refresh token, đề thi người đó tạo và bài làm của người đó.
Xoá đề → xoá dây chuyền câu hỏi, lựa chọn, bài làm và câu trả lời. Xoá một lựa chọn → câu trả lời
đã chọn nó còn lại với selected_option_id = NULL (không mất bài làm).
Băm mật khẩu, JWT và đăng nhập nằm ở auth.py; file này chỉ lưu và đọc dữ liệu.

Session SQLAlchemy ở đây là đồng bộ; app/routers/chat.py gọi các hàm này qua asyncio.to_thread.

.env cần:  DATABASE_URL=postgresql+psycopg://user:mật_khẩu@localhost:5432/hcm_chat
Bảng được tạo lúc khởi động API (init_db). Database tạo trước khi có đăng nhập: python -m scripts.migrate_auth
Database tạo trước Phase 0 (ROADMAP.md): python -m scripts.migrate_phase0
Test chạy trên SQLite tạm (xem conftest.py), không cần Postgres.
"""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, create_engine, false, inspect, select, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "2"))   # số lượt hỏi–đáp gần nhất gửi kèm cho Gemini
TITLE_CHARS = 60
# Lượt bị chặn ở đầu vào (prompt injection, jailbreak) không đưa vào lịch sử,
# để nội dung đó không lọt vào prompt của các câu hỏi sau.
BLOCKED_INPUT = {"input-basic", "input-guard"}

# pool_pre_ping: kiểm tra kết nối trước khi dùng → Postgres khởi động lại thì không lỗi ở request đầu.
# Chưa có DATABASE_URL thì vẫn import được (test tự thay engine); init_db() sẽ báo lỗi rõ ràng.
engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None


def _sqlite_pragmas(dbapi_conn, _):
    """Chỉ dùng cho SQLite (test và migrate_sqlite.py); Postgres có sẵn khoá ngoại."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")     # SQLite mặc định tắt khoá ngoại → bật để xoá dây chuyền
    cur.execute("PRAGMA journal_mode=WAL")    # đọc và ghi cùng lúc không chặn nhau
    cur.close()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# JSON trên SQLite (test), JSONB trên Postgres (truy vấn được bên trong, có index)
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    """CHECK chỉ nhận các giá trị cho trước. Cột cho phép NULL thì NULL vẫn qua (SQL coi NULL IN (...) là không sai)."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# Hai hệ thống chat chung một cửa sổ: docs = hỏi giáo trình (RAG hiện có), exam = trợ lý bài kiểm tra (Phase 5)
CONVERSATION_MODES = ("docs", "exam")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(_one_of("mode", CONVERSATION_MODES), name="ck_conversations_mode"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="docs", server_default="docs")
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))     # "user" / "assistant"
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)   # luôn lưu chữ thường
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshToken(Base):
    """Mỗi lần đăng nhập / làm mới phiên một dòng. Đổi token → dòng cũ bị đánh dấu revoked_at."""
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------- đề thi ----------
EXAM_VISIBILITY = ("private", "link", "public")   # chỉ người tạo / ai có share_code / mọi người
EXAM_STATUS = ("draft", "published")              # đề AI phải được xem lại trước khi cho người khác làm
EXAM_ORIGIN = ("manual", "ai")                    # tự soạn / AI sinh — cũng dùng để đếm lượt sinh đề AI mỗi ngày
QUESTION_TYPES = ("mcq", "essay")                  # trắc nghiệm / tự luận
SUBMISSION_STATUS = ("in_progress", "submitted", "graded")
GRADED_BY = ("auto", "ai", "creator")             # máy chấm mcq / AI chấm tự luận / người ra đề chấm lại


class Exam(Base):
    __tablename__ = "exams"
    __table_args__ = (
        CheckConstraint(_one_of("visibility", EXAM_VISIBILITY), name="ck_exams_visibility"),
        CheckConstraint("duration_minutes IS NULL OR duration_minutes > 0", name="ck_exams_duration"),
        CheckConstraint("open_at IS NULL OR close_at IS NULL OR close_at > open_at", name="ck_exams_window"),
        CheckConstraint(_one_of("status", EXAM_STATUS), name="ck_exams_status"),
        CheckConstraint(_one_of("origin", EXAM_ORIGIN), name="ck_exams_origin"),
        CheckConstraint("max_attempts IS NULL OR max_attempts > 0", name="ck_exams_max_attempts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), default="private", server_default="private")
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    origin: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    max_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)         # NULL = làm lại không giới hạn
    # Mã chia sẻ cho visibility="link"; NULL được lặp lại, giá trị thật thì không trùng
    share_code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)     # NULL = không giới hạn
    shuffle_questions: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)    # NULL = mở ngay
    close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # NULL = không đóng
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", order_by="Question.order_index")
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", passive_deletes=True)


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(_one_of("type", QUESTION_TYPES), name="ck_questions_type"),
        CheckConstraint("points >= 0", name="ck_questions_points"),
        CheckConstraint("source_page IS NULL OR source_page > 0", name="ck_questions_source_page"),
        Index("ix_questions_exam_order", "exam_id", "order_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(16))
    order_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    rubric: Mapped[str | None] = mapped_column(Text, nullable=True)                  # chỉ essay: hướng dẫn chấm
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)             # vì sao đáp án đúng (trang kết quả)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)          # trang giáo trình, như trích dẫn chatbot
    points: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")

    exam: Mapped[Exam] = relationship(back_populates="questions")
    options: Mapped[list["QuestionOption"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", order_by="QuestionOption.order_index")


class QuestionOption(Base):
    """Một lựa chọn của câu trắc nghiệm. Mỗi câu có tối đa một lựa chọn đúng (chọn một đáp án)."""
    __tablename__ = "question_options"
    __table_args__ = (
        UniqueConstraint("question_id", "label", name="uq_question_options_label"),
        Index("ix_question_options_question_order", "question_id", "order_index"),
        # chỉ một dòng is_correct = true cho mỗi câu — partial unique index (Postgres và SQLite đều hỗ trợ)
        Index("ux_question_options_one_correct", "question_id", unique=True,
              postgresql_where=text("is_correct"), sqlite_where=text("is_correct")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(8))                                    # "A", "B", "C", "D"
    content: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    order_index: Mapped[int] = mapped_column(Integer)

    question: Mapped[Question] = relationship(back_populates="options")


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint(_one_of("status", SUBMISSION_STATUS), name="ck_submissions_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="in_progress", server_default="in_progress")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    exam: Mapped[Exam] = relationship(back_populates="submissions")
    answers: Mapped[list["SubmissionAnswer"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", passive_deletes=True)


class SubmissionAnswer(Base):
    """Câu trả lời cho một câu hỏi trong một bài làm: chọn một lựa chọn (mcq) hoặc viết bài (essay)."""
    __tablename__ = "submission_answers"
    __table_args__ = (
        UniqueConstraint("submission_id", "question_id", name="uq_submission_answers_question"),
        CheckConstraint("selected_option_id IS NULL OR essay_text IS NULL", name="ck_submission_answers_one_kind"),
        CheckConstraint("score IS NULL OR score >= 0", name="ck_submission_answers_score"),
        CheckConstraint(_one_of("graded_by", GRADED_BY), name="ck_submission_answers_graded_by"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    # Xoá lựa chọn → giữ câu trả lời, chỉ bỏ liên kết. Lựa chọn phải thuộc đúng question_id: kiểm tra ở code ghi bài.
    selected_option_id: Mapped[int | None] = mapped_column(
        ForeignKey("question_options.id", ondelete="SET NULL"), nullable=True, index=True)
    essay_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    graded_by: Mapped[str | None] = mapped_column(String(16), nullable=True)         # NULL = chưa chấm
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    submission: Mapped[Submission] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship()
    selected_option: Mapped[QuestionOption | None] = relationship()


# Cột được thêm vào bảng ĐÃ CÓ trong database → create_all không tự thêm (nó chỉ tạo bảng còn thiếu),
# phải chạy script migration tương ứng. init_db kiểm tra để báo rõ thay vì lỗi SQL khó hiểu lúc chạy.
_MIGRATED_COLUMNS = {
    ("conversations", "user_id"): "scripts.migrate_auth",
    ("users", "is_admin"): "scripts.migrate_auth",
    ("conversations", "mode"): "scripts.migrate_phase0",
    ("exams", "status"): "scripts.migrate_phase0",
    ("exams", "origin"): "scripts.migrate_phase0",
    ("exams", "max_attempts"): "scripts.migrate_phase0",
    ("questions", "explanation"): "scripts.migrate_phase0",
    ("questions", "source_page"): "scripts.migrate_phase0",
    ("submission_answers", "graded_by"): "scripts.migrate_phase0",
}


def init_db():
    if engine is None:
        raise RuntimeError("Chưa đặt DATABASE_URL trong .env (vd. postgresql+psycopg://user:pass@localhost:5432/hcm_chat)")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    columns = {}
    for (table, column), script in _MIGRATED_COLUMNS.items():
        columns.setdefault(table, {c["name"] for c in inspector.get_columns(table)})
        if column not in columns[table]:
            raise RuntimeError(f"Bảng {table} chưa có cột {column} → chạy: python -m {script}")


# ---------- chuyển sang dict để trả về JSON ----------
def _ms(dt: datetime) -> int:
    """Postgres trả datetime có múi giờ; SQLite (test) thì không — mọi thời điểm đều được ghi theo UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def conversation_summary(c: Conversation) -> dict:
    return {"id": c.id, "title": c.title, "created_at": _ms(c.created_at), "updated_at": _ms(c.updated_at)}


def message_dict(m: Message) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content, "meta": m.meta, "created_at": _ms(m.created_at)}


def user_dict(u: User) -> dict:
    """Thông tin trả cho trình duyệt — không bao giờ kèm password_hash."""
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "is_admin": u.is_admin,
            "created_at": _ms(u.created_at)}


def make_title(text: str) -> str:
    t = " ".join(text.split())
    if not t:
        return "Cuộc trò chuyện mới"
    return t if len(t) <= TITLE_CHARS else t[: TITLE_CHARS - 1].rstrip() + "…"


# ---------- các thao tác ----------
def _owned(s: Session, user_id: int, conversation_id: int) -> Conversation:
    conv = s.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user_id:
        raise LookupError("Không tìm thấy cuộc trò chuyện")
    return conv


def list_conversations(user_id: int, limit: int = 200) -> list[dict]:
    with Session(engine) as s:
        rows = s.scalars(select(Conversation).where(Conversation.user_id == user_id)
                         .order_by(Conversation.updated_at.desc()).limit(limit))
        return [conversation_summary(c) for c in rows]


def get_conversation(user_id: int, conversation_id: int) -> dict:
    with Session(engine) as s:
        conv = _owned(s, user_id, conversation_id)
        return {**conversation_summary(conv), "messages": [message_dict(m) for m in conv.messages]}


def delete_conversation(user_id: int, conversation_id: int) -> None:
    with Session(engine) as s, s.begin():
        s.delete(_owned(s, user_id, conversation_id))


def _history(prior: list[Message]) -> list[dict]:
    """Ghép các tin trước thành lượt hỏi–đáp, bỏ lượt bị chặn ở đầu vào, giữ HISTORY_TURNS lượt cuối."""
    turns, i = [], 0
    while i < len(prior):
        m = prior[i]
        nxt = prior[i + 1] if i + 1 < len(prior) else None
        if m.role == "user" and nxt is not None and nxt.role == "assistant":
            if (nxt.meta or {}).get("blocked_by") not in BLOCKED_INPUT:
                turns.append([{"role": "user", "content": m.content}, {"role": "assistant", "content": nxt.content}])
            i += 2
        else:   # câu hỏi chưa có câu trả lời (lỗi / bị dừng) → bỏ qua
            i += 1
    return [msg for turn in turns[-HISTORY_TURNS:] for msg in turn] if HISTORY_TURNS > 0 else []


def begin_turn(user_id: int, conversation_id: int | None, text: str, retry: bool = False) -> tuple[dict, list[dict]]:
    """
    Bước 1–2 trong sơ đồ: lưu tin của người dùng rồi đọc lịch sử trước đó.
    conversation_id = None → tạo cuộc trò chuyện mới, tiêu đề lấy từ câu hỏi đầu tiên.
    retry = True và tin cuối là chính câu hỏi này (chưa có câu trả lời) → dùng lại, không lưu trùng.
    """
    with Session(engine) as s, s.begin():
        if conversation_id is None:
            conv = Conversation(user_id=user_id, title=make_title(text))
            s.add(conv)
            s.flush()
        else:
            conv = _owned(s, user_id, conversation_id)

        prior = list(conv.messages)
        last = prior[-1] if prior else None
        if retry and last is not None and last.role == "user" and last.content == text:
            prior = prior[:-1]
        else:
            s.add(Message(conversation_id=conv.id, role="user", content=text))
        conv.updated_at = utcnow()
        s.flush()
        return conversation_summary(conv), _history(prior)


def save_answer(user_id: int, conversation_id: int, result: dict) -> int:
    """Bước 4: stream xong → lưu câu trả lời cuối (sau khi đã qua các lớp kiểm tra) kèm meta."""
    with Session(engine) as s, s.begin():
        conv = _owned(s, user_id, conversation_id)
        meta = {k: v for k, v in result.items() if k != "answer"}
        msg = Message(conversation_id=conv.id, role="assistant", content=result["answer"], meta=meta)
        s.add(msg)
        conv.updated_at = utcnow()
        s.flush()
        return msg.id


# ---------- người dùng (logic đăng nhập ở auth.py) ----------
def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user(user_id: int) -> dict | None:
    with Session(engine) as s:
        user = s.get(User, user_id)
        return user_dict(user) if user and user.is_active else None
