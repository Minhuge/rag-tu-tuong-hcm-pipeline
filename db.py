"""
Lưu lịch sử chat vào PostgreSQL bằng SQLAlchemy.

  conversations                     messages
  ─────────────                     ────────────────
  id (PK)          ◀───────┐        id (PK)
  client_id                └─────── conversation_id (FK)
  title                             role ("user" / "assistant")
  created_at                        content
  updated_at                        meta (JSON: nguồn, các lớp kiểm tra, thời gian — chỉ tin assistant)
                                    created_at  

client_id: mã ngẫu nhiên mỗi trình duyệt tự tạo (không phải đăng nhập) → người dùng
chung mạng LAN chỉ thấy cuộc trò chuyện của chính trình duyệt mình.

Session SQLAlchemy ở đây là đồng bộ; api.py gọi các hàm này qua asyncio.to_thread.

.env cần:  DATABASE_URL=postgresql+psycopg://user:mật_khẩu@localhost:5432/hcm_chat
Bảng được tạo lúc khởi động API (init_db). Chuyển dữ liệu cũ từ chat.db: python migrate_sqlite.py
Test chạy trên SQLite tạm (xem conftest.py), không cần Postgres.
"""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, create_engine, select
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
    client_id: Mapped[str] = mapped_column(String(64), index=True)
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


def init_db():
    if engine is None:
        raise RuntimeError("Chưa đặt DATABASE_URL trong .env (vd. postgresql+psycopg://user:pass@localhost:5432/hcm_chat)")
    Base.metadata.create_all(engine)


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


def make_title(text: str) -> str:
    t = " ".join(text.split())
    if not t:
        return "Cuộc trò chuyện mới"
    return t if len(t) <= TITLE_CHARS else t[: TITLE_CHARS - 1].rstrip() + "…"


# ---------- các thao tác ----------
def _owned(s: Session, client_id: str, conversation_id: int) -> Conversation:
    conv = s.get(Conversation, conversation_id)
    if conv is None or conv.client_id != client_id:
        raise LookupError("Không tìm thấy cuộc trò chuyện")
    return conv


def list_conversations(client_id: str, limit: int = 200) -> list[dict]:
    with Session(engine) as s:
        rows = s.scalars(select(Conversation).where(Conversation.client_id == client_id)
                         .order_by(Conversation.updated_at.desc()).limit(limit))
        return [conversation_summary(c) for c in rows]


def get_conversation(client_id: str, conversation_id: int) -> dict:
    with Session(engine) as s:
        conv = _owned(s, client_id, conversation_id)
        return {**conversation_summary(conv), "messages": [message_dict(m) for m in conv.messages]}


def delete_conversation(client_id: str, conversation_id: int) -> None:
    with Session(engine) as s, s.begin():
        s.delete(_owned(s, client_id, conversation_id))


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


def begin_turn(client_id: str, conversation_id: int | None, text: str, retry: bool = False) -> tuple[dict, list[dict]]:
    """
    Bước 1–2 trong sơ đồ: lưu tin của người dùng rồi đọc lịch sử trước đó.
    conversation_id = None → tạo cuộc trò chuyện mới, tiêu đề lấy từ câu hỏi đầu tiên.
    retry = True và tin cuối là chính câu hỏi này (chưa có câu trả lời) → dùng lại, không lưu trùng.
    """
    with Session(engine) as s, s.begin():
        if conversation_id is None:
            conv = Conversation(client_id=client_id, title=make_title(text))
            s.add(conv)
            s.flush()
        else:
            conv = _owned(s, client_id, conversation_id)

        prior = list(conv.messages)
        last = prior[-1] if prior else None
        if retry and last is not None and last.role == "user" and last.content == text:
            prior = prior[:-1]
        else:
            s.add(Message(conversation_id=conv.id, role="user", content=text))
        conv.updated_at = utcnow()
        s.flush()
        return conversation_summary(conv), _history(prior)


def save_answer(client_id: str, conversation_id: int, result: dict) -> int:
    """Bước 4: stream xong → lưu câu trả lời cuối (sau khi đã qua các lớp kiểm tra) kèm meta."""
    with Session(engine) as s, s.begin():
        conv = _owned(s, client_id, conversation_id)
        meta = {k: v for k, v in result.items() if k != "answer"}
        msg = Message(conversation_id=conv.id, role="assistant", content=result["answer"], meta=meta)
        s.add(msg)
        conv.updated_at = utcnow()
        s.flush()
        return msg.id
