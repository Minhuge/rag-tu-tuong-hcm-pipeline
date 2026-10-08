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
src/api.js                  gọi /ask, /health, chuẩn hoá thông báo lỗi
src/trace.js                ChatResult → các bước 1a…4a
src/App.jsx                 khung trang, lịch sử, trạng thái máy chủ
src/components/Message.jsx  tin nhắn, chip trích dẫn, các lớp kiểm tra, nguồn
src/components/Composer.jsx ô nhập (Enter gửi, Shift+Enter xuống dòng, tối đa 1000 ký tự)
src/index.css               giao diện sáng/tối, responsive
```
