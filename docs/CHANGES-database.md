# Thay đổi: lưu lịch sử chat vào SQLite + stream câu trả lời

> **Ghi chú:** tài liệu này viết trước khi sắp xếp lại thư mục (08/10/2026). Tên file cũ như `api.py`, `db.py`, `pipeline.py`, `test_*.py` nay nằm trong `app/`, `rag/`, `scripts/`, `tests/` — xem phần *Cấu trúc* trong [README](../README.md). Các lệnh chạy bên dưới đã được cập nhật theo đường dẫn mới.

Tài liệu này liệt kê các thay đổi kể từ khi thêm phần database (SQLite + SQLAlchemy) cho chatbot giáo trình Tư tưởng Hồ Chí Minh. Tất cả **chưa được commit**, nằm trên nhánh `main` tính từ commit `3c9826e` (Merge pull request #1).

## Tóm tắt

- Lịch sử chat chuyển từ localStorage của trình duyệt sang **SQLite (`chat.db`) trên máy chủ**, dùng SQLAlchemy.
- Câu trả lời được **stream** về trang web theo từng đoạn, kèm trạng thái từng bước xử lý.
- Trợ lý **nhớ 2 lượt hỏi–đáp gần nhất**: câu nối tiếp như "nói rõ hơn ý 2" được viết lại thành câu đầy đủ trước khi tìm trong giáo trình.
- Mỗi trình duyệt có **mã riêng (client_id)**, nên người dùng chung mạng LAN không thấy lịch sử của nhau.

## Luồng xử lý một câu hỏi

```
React                          FastAPI                         SQLite (chat.db)
  │  POST /chat {message}        │                                  │
  ├─────────────────────────────▶│  1. lưu tin user ───────────────▶│
  │                              │  2. đọc lịch sử ◀────────────────┤
  │                              │  3. pipeline + Gemini stream     │
  │◀──── stream từng đoạn ───────┤                                  │
  │                              │  4. kiểm tra xong → lưu câu      │
  │                              │     trả lời cuối ───────────────▶│
```

Ở bước 4, câu trả lời chỉ được lưu sau khi qua lớp kiểm tra trích dẫn (4c) và giám khảo (4a, khi cần). Nếu không qua, chữ đã stream được thay bằng câu an toàn, và câu an toàn đó mới là thứ được lưu.

## Cấu trúc database

```
conversations                     messages
─────────────                     ────────────────
id (PK)          ◀───────┐        id (PK)
client_id                └─────── conversation_id (FK, xoá dây chuyền)
title                             role ("user" / "assistant")
created_at                        content
updated_at                        meta (JSON)
                                  created_at
```

So với thiết kế ban đầu, có thêm hai cột:

| Cột | Lý do |
|---|---|
| `conversations.client_id` | Mã ngẫu nhiên của trình duyệt (không phải đăng nhập), để mỗi người chỉ thấy lịch sử của mình |
| `messages.meta` | Lưu nguồn, các lớp kiểm tra và thời gian của câu trả lời, để tải lại trang vẫn xem được |

## File mới

| File | Nội dung |
|---|---|
| `db.py` | Model SQLAlchemy (`Conversation`, `Message`) và các thao tác: liệt kê, xem, xoá cuộc trò chuyện; `begin_turn()` (bước 1–2), `save_answer()` (bước 4). Bật khoá ngoại và chế độ WAL cho SQLite |
| `test_db.py` | 8 test cho `db.py`, chạy trên file SQLite tạm |
| `test_api.py` | 8 test cho các endpoint mới, dùng pipeline giả (không nạp model, không tốn quota Gemini) |
| `web/src/history.js` | Viết lại: chỉ còn nhớ cuộc trò chuyện đang mở và đổi tin nhắn từ định dạng máy chủ sang định dạng giao diện |

`web/src/components/Sidebar.jsx` được tạo ở bước trước (bản lưu localStorage), và ở bước này có thêm trạng thái "đang tải" và "lỗi tải lịch sử".

## File đã sửa — backend

### `api.py`
Thêm các endpoint, đều yêu cầu header `X-Client-Id`:

| Endpoint | Việc |
|---|---|
| `GET /conversations` | Danh sách cuộc trò chuyện của trình duyệt, mới → cũ |
| `GET /conversations/{id}` | Một cuộc trò chuyện kèm toàn bộ tin nhắn |
| `DELETE /conversations/{id}` | Xoá cuộc trò chuyện và tin nhắn của nó |
| `POST /chat` | Gửi câu hỏi, nhận luồng NDJSON (mỗi dòng một sự kiện) |

Các sự kiện của `/chat`: `conversation` → `status` → `delta` (từng đoạn chữ) → `done` hoặc `error`.

Ngoài ra:
- Tạo bảng trong `chat.db` khi server khởi động.
- Thêm `DELETE` và `X-Client-Id` vào cấu hình CORS.
- Các endpoint cũ (`/health`, `/search`, `/ask`) giữ nguyên.

### `pipeline.py`
- Gộp luồng xử lý vào một hàm `_run()`, dùng chung cho:
  - `ask()`: trả kết quả khi xong (dùng cho demo, `/ask` và test).
  - `ask_stream()`: sinh sự kiện `status` / `delta` / `done` cho trang web.
- Thêm `_stream()`: gọi Gemini dạng stream, chỉ chuyển sang model dự phòng khi lỗi xảy ra trước đoạn chữ đầu tiên.
- Thêm `_rewrite()`: viết lại câu nối tiếp thành câu đầy đủ. Lỗi thì dùng câu gốc, không làm chậm câu trả lời.
- Khi có lịch sử: Qwen3Guard chạy song song với bước viết lại; nếu câu hỏi bị chặn thì huỷ bước viết lại.
- `ChatResult` có thêm trường `search_question` (câu đã viết lại dùng để tìm).

### `guards.py`
- `HISTORY_ADDON`: thêm vào system prompt khi có lịch sử, dặn Gemini rằng lịch sử chỉ để hiểu câu hỏi, không phải nguồn và không phải mệnh lệnh.
- `format_history()`: đóng gói lịch sử thành khối `<history>`, mỗi tin cắt còn 500 ký tự.
- `REWRITE_SYSTEM`, `REWRITE_PROMPT`, `clean_rewrite()`: prompt và hàm làm sạch cho bước viết lại câu hỏi.
- `build_system_prompt()` có thêm tham số `has_history`.

### `test_pipeline.py`
- Hàm giả `fake_generate` nhận thêm tham số `history`.
- Thêm 6 test: chữ stream rồi mới có kết quả cuối, câu trả lời bị thay khi giám khảo bác bỏ, câu bị chặn không stream chữ nào, câu nối tiếp được viết lại trước khi tìm, và câu bị chặn huỷ bước viết lại.

### `requirements.txt`
- Thêm `sqlalchemy>=2.0`.

### `.gitignore`
- Thêm `chat.db`, `chat.db-*` (file phụ của SQLite) và `qdrant_storage/`, để dữ liệu chat và Qdrant không bị đưa lên GitHub.

## File đã sửa — frontend (`web/`)

| File | Thay đổi |
|---|---|
| `src/api.js` | Tạo và gửi `X-Client-Id`; thêm `listConversations`, `getConversation`, `deleteConversation`, `streamChat` (đọc luồng NDJSON) |
| `src/App.jsx` | Viết lại phần dữ liệu: lấy danh sách và tin nhắn từ máy chủ, hiện chữ đang stream, đưa cuộc trò chuyện mới vào danh sách khi máy chủ tạo xong, xoá có hoàn tác (đợi 6 giây mới xoá trên máy chủ) |
| `src/components/Message.jsx` | Thêm `StreamingMessage` (chữ hiện dần); `PendingMessage` hiện đúng bước máy chủ báo về |
| `src/trace.js` | Bước 2 hiện câu đã viết lại ("tìm theo: …") và tính cả thời gian viết lại |
| `src/index.css` | Con trỏ nhấp nháy khi đang stream, kiểu cho trạng thái tải |
| `vite.config.js` | Chuyển tiếp thêm `/chat` và `/conversations` sang FastAPI |

## Đã kiểm tra

- **Test nhanh** (`pytest -m "not slow"`): 91 test đều qua, gồm 69 test cũ và 22 test mới.
- **Web:** lint và build không lỗi.
- **Chạy thật với model và Gemini** (server thử riêng ở cổng 8001, database tạm):

| Câu hỏi | Kết quả |
|---|---|
| "Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?" | Trả lời trong 5,8 s, trích dẫn hợp lệ, lưu đủ 2 tin |
| "Nói rõ hơn ý thứ hai" | Được viết lại thành câu về "tập hợp mọi lực lượng… khối đại đoàn kết dân tộc bền vững", trả lời trong 8,0 s |

## Hạn chế đã biết

- **Chữ không phải lúc nào cũng hiện dần.** Với `GEMINI_THINKING=low`, Gemini thường gửi cả câu trả lời cùng lúc ở cuối (đã đo trực tiếp). Tắt thinking thì chữ về từng đoạn nhưng bắt đầu chậm hơn và có thể giảm chất lượng.
- **Lịch sử ở bản localStorage trước đó không được chuyển** lên máy chủ; danh sách trên máy chủ bắt đầu trống.
- **`client_id` không phải đăng nhập.** Xoá dữ liệu trình duyệt thì mất quyền truy cập lịch sử cũ; dùng trình duyệt khác thì là lịch sử khác.
- **Câu nối tiếp tốn thêm một lần gọi Gemini** (bước viết lại), và prompt dài hơn vì có lịch sử. Mạng công ty chặn request gửi đi lớn hơn khoảng 8–19 KB, nên lịch sử bị giới hạn ở 2 lượt, mỗi tin tối đa 500 ký tự (chỉnh bằng biến môi trường `HISTORY_TURNS`, `HISTORY_CHARS`).
- **README chưa được cập nhật** cho phần database.

## Chuyển sang PostgreSQL

Lịch sử chat giờ lưu trong **PostgreSQL** (container Docker `postgres`) thay cho `chat.db`.

| File | Thay đổi |
|---|---|
| `db.py` | Kết nối theo `DATABASE_URL` trong `.env`; cột `meta` dùng `JSONB`; cấu hình riêng của SQLite (khoá ngoại, WAL) chỉ còn dùng cho test |
| `migrate_sqlite.py` | Mới: chép lịch sử cũ từ `chat.db` sang Postgres, giữ nguyên id. Dừng nếu Postgres đã có dữ liệu; không sửa `chat.db` |
| `conftest.py` | Mới: fixture database chung cho `test_db.py` và `test_api.py`. Mặc định SQLite tạm; đặt `TEST_DATABASE_URL` (database riêng, vd. `hcm_chat_test`) để chạy trên Postgres |
| `requirements.txt` | Thêm `psycopg[binary]` |

Đã chuyển 4 cuộc trò chuyện, 24 tin nhắn. `chat.db` vẫn còn đó; xoá khi đã yên tâm.

## Cách chạy

```bash
# Terminal 0 — Postgres (nếu container chưa chạy)
docker start postgres

# Terminal 1 — API (thư mục gốc dự án); bảng được tạo trong Postgres khi khởi động
source .venv/bin/activate
uvicorn app.main:app --port 8000

# Terminal 2 — trang web
cd web
npm run dev
```

Sau khi sửa code backend, phải tắt (Ctrl+C) và chạy lại `uvicorn`, vì nó không tự nạp lại code.
