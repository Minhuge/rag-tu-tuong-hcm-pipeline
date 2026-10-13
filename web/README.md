# Web chat (React + Vite)

Giao diện chat cho backend FastAPI (`app/`). Mỗi câu trả lời hiển thị:

- câu trả lời (markdown), trích dẫn `[Chương X, trang Y]` thành chip bấm được → mở và tô sáng đoạn giáo trình tương ứng
  (chip màu vàng = trích dẫn không khớp nguồn nào);
- 6 lớp kiểm tra `1a 1b 2 3 4c 4a` (xanh = qua, vàng = cảnh báo, đỏ = chặn, viền xám = không chạy), bấm để xem chi tiết và thời gian;
- danh sách nguồn kèm điểm reranker P(yes).

Lịch sử chat lưu trong `localStorage` của trình duyệt (nút "Cuộc trò chuyện mới" để xoá). Mỗi câu hỏi được pipeline xử lý độc lập.

## Chạy

```bash
# backend (thư mục gốc)
uvicorn app.main:app --port 8000

# Cách 1 — dev, có hot reload: http://localhost:5173 (Vite proxy /ask, /search, /health → :8000)
cd web
npm install
npm run dev

# Cách 2 — build một lần, FastAPI tự phục vụ tại http://localhost:8000
cd web && npm run build
```

Backend ở địa chỉ khác: `API_URL=http://host:8000 npm run dev` (dev proxy) hoặc
`VITE_API_URL=http://host:8000 npm run build` (khi đó thêm origin của web vào `CORS_ORIGINS` trong `.env`).

## Cấu trúc

```
src/main.jsx                BrowserRouter
src/Root.jsx                đăng nhập / khôi phục phiên
src/App.jsx                 khung sau đăng nhập: thanh Chat | Bài kiểm tra, các route
src/pages/ChatPage.jsx      trang hỏi giáo trình: lịch sử, trạng thái máy chủ
src/pages/ExamPages.jsx     các trang bài kiểm tra
src/api.js                  fetch dùng chung (cookie, tự làm mới phiên khi 401), ApiError, API chat
src/examsApi.js             API bài kiểm tra (có kiểm tra kiểu)
src/api-schema.d.ts         type sinh từ openapi.json — KHÔNG sửa tay
openapi.json                mô tả API, xuất từ FastAPI — KHÔNG sửa tay
src/trace.js                ChatResult → các bước 1a…4a
src/components/Message.jsx  tin nhắn, chip trích dẫn, các lớp kiểm tra, nguồn
src/components/Composer.jsx ô nhập (Enter gửi, Shift+Enter xuống dòng, tối đa 1000 ký tự)
src/index.css               giao diện sáng/tối, responsive
```

## Kiểu dữ liệu API (frontend không lệch backend)

Sửa schema API ở backend (`app/exam_schemas.py`, các model trong `app/routers/`) → chạy lại:

```bash
source ../.venv/bin/activate   # cần Python của backend
npm run api:types              # app.openapi() → openapi.json → src/api-schema.d.ts
npm run typecheck              # kiểm tra các file có // @ts-check (src/examsApi.js…)
```

Quên chạy thì `pytest tests/test_api_schemas.py` báo "web/openapi.json chưa cập nhật". Trong VS Code, gõ sai tên
trường ở file có `// @ts-check` sẽ bị gạch đỏ ngay.

Lỗi API là `ApiError`: `message` tiếng Việt hiện thẳng lên giao diện; `fields` = lỗi từng ô nhập (422) để hiện cạnh ô
đó; `question` = số câu bị lỗi khi publish; `retryable` = lỗi tạm thời, nên có nút "Thử lại". Nộp bài, publish, tạo đề
AI, bắt đầu làm bài: gọi trùng trong lúc lần trước chưa xong thì dùng lại lần đang chạy (không gửi hai lần).

## Kiểm tra

- Tự động: `npx oxlint src`, `npm run build`, `npm run typecheck`; ở thư mục gốc `pytest -q` (gồm cả tương phản màu
  của `src/index.css`: `python -m scripts.check_contrast`).
- Thủ công, hai tài khoản, đi hết luồng tạo đề → làm bài → chấm: [docs/KIEM_TRA_THU_CONG_PHASE4.md](../docs/KIEM_TRA_THU_CONG_PHASE4.md).
