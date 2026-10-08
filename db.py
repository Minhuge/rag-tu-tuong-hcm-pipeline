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

Mỗi người chỉ thấy cuộc trò chuyện của chính mình (user_id). Xoá user → xoá dây chuyền
cuộc trò chuyện, tin nhắn và refresh token của người đó.
Băm mật khẩu, JWT và đăng nhập nằm ở auth.py; file này chỉ lưu và đọc dữ liệu.

Session SQLAlchemy ở đây là đồng bộ; api.py gọi các hàm này qua asyncio.to_thread.

.env cần:  DATABASE_URL=postgresql+psycopg://user:mật_khẩu@localhost:5432/hcm_chat
Bảng được tạo lúc khởi động API (init_db). Database tạo trước khi có đăng nhập: python migrate_auth.py
Test chạy trên SQLite tạm (xem conftest.py), không cần Postgres.
"""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, create_engine, false, inspect, select
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


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    meta: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
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


# Cột được thêm sau khi bảng đã có dữ liệu → create_all không tự thêm, phải chạy migrate_auth.py.
_MIGRATED_COLUMNS = {"conversations": "user_id", "users": "is_admin"}


def init_db():
    if engine is None:
        raise RuntimeError("Chưa đặt DATABASE_URL trong .env (vd. postgresql+psycopg://user:pass@localhost:5432/hcm_chat)")
    Base.metadata.create_all(engine)
    cols = inspect(engine)
    for table, column in _MIGRATED_COLUMNS.items():
        if column not in {c["name"] for c in cols.get_columns(table)}:
            raise RuntimeError(f"Bảng {table} chưa có cột {column} → chạy: python migrate_auth.py")


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
