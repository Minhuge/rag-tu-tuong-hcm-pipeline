# Kiểm tra thủ công giao diện bài kiểm tra (Phase 4)

Đi hết luồng **tạo đề AI → sửa → xuất bản → người khác làm → chấm → xem kết quả** trên giao diện, với hai tài khoản.
Giống bài test bằng Swagger trước đây, nhưng làm hoàn toàn trên web. Đánh dấu `[x]` từng dòng khi đạt; dòng nào không
đạt thì ghi lại bước, kết quả thấy được và ảnh chụp.

**Phase 4 xong khi:** một người mới dùng được cả luồng trên mà không cần ai hướng dẫn (roadmap, Phase 4).

Kiểm tra tự động đã có (không cần làm tay): `pytest -q` (API, quyền, chấm điểm, tương phản màu), `cd web && npx oxlint src`,
`npm run build`, `npm run typecheck`.

---

## 0. Chuẩn bị

- [ ] Docker đang chạy (Postgres + Qdrant), giáo trình đã nạp vào Qdrant.
- [ ] Backend: `uvicorn app.main:app --port 8000` — đợi log báo đã nạp model.
- [ ] Web: `cd web && npm run dev` → mở http://localhost:5173.
- [ ] Hai trình duyệt (hoặc một cửa sổ thường + một cửa sổ ẩn danh):
  - **A — người ra đề** (vd. `giangvien@example.com`)
  - **B — người làm bài** (vd. `sinhvien@example.com`)
- [ ] Đăng ký / đăng nhập cả hai. Góc phải thanh trên hiện đúng tên từng người.

## 1. Điều hướng và chế độ trò chuyện (4.1, 4.9)

- [ ] Thanh trên có **Trò chuyện | Bài kiểm tra**; tab đang mở được tô (nền + màu chữ + icon đậm).
- [ ] Mở thẳng một đường dẫn (gõ `/exams` rồi Enter, hoặc F5 ở `/exams/new`) → ra đúng trang, không phải lỗi JSON.
- [ ] Đường dẫn lạ (`/abcxyz`) → trang "Không tìm thấy trang" có link quay về.
- [ ] Trò chuyện → dòng dưới tiêu đề hiện chế độ ("Hỏi giáo trình") và link **Đổi chế độ: cuộc trò chuyện mới**.
- [ ] Cuộc trò chuyện mới: chọn **Trợ lý bài kiểm tra** → màn hình riêng (4 câu mẫu, hai lưu ý giới hạn), ô nhập
  dùng được. Thử trợ lý: mục 10.
- [ ] Chọn lại **Hỏi giáo trình**, hỏi một câu → có câu trả lời kèm trích dẫn; cuộc trò chuyện hiện trong lịch sử với icon
  bong bóng chat.
- [ ] Nhấn Tab từ đầu trang: phần tử đầu tiên là "Bỏ qua thanh điều hướng"; mọi nút đều có viền focus nhìn thấy được.

## 2. A tạo đề bằng AI (4.4)

- [ ] Bài kiểm tra → **Tạo đề mới**. Thấy "Còn N/5 lượt tạo đề bằng AI hôm nay".
- [ ] Bấm **Tạo đề bằng AI** khi chưa chọn chương → lỗi "Chọn một chương" ngay dưới danh sách chương, focus nhảy tới đó.
- [ ] Nhập số câu trắc nghiệm `25` → lỗi "Nhập số nguyên từ 0 đến 20" dưới ô; `0` và `0` → "Cần ít nhất một câu hỏi".
- [ ] Chọn một chương, 5 trắc nghiệm + 1 tự luận → khung chờ: vòng quay, "Đã chờ N giây", các bước sáng dần.
- [ ] (Tuỳ chọn) bấm **Thôi chờ** → thông báo đề có thể vẫn được tạo, có link Bài kiểm tra của tôi. Đợi 1 phút, mở danh
  sách: đề vừa tạo vẫn xuất hiện → **không** tạo lại để khỏi tốn lượt.
- [ ] Tạo xong → tự sang trình sửa đề, đầu trang có báo cáo "AI đã soạn … từ Chương …", số lượt còn lại; nếu có câu bị loại
  thì mở "… câu bị loại — xem lý do" xem được lý do.
- [ ] Bấm X ẩn báo cáo, F5 → báo cáo không hiện lại.
- [ ] Mở hai tab cùng tạo đề AI cùng lúc → tab thứ hai báo đang có một đề khác đang tạo (không tốn thêm lượt).

## 3. A sửa đề (4.5)

- [ ] Sửa tiêu đề → thanh trên hiện "Chưa lưu…" → "Đang lưu…" → "Đã lưu" sau khoảng 1 giây ngừng gõ.
- [ ] Xoá trắng tiêu đề → "Nhập tiêu đề" dưới ô, trạng thái "Chưa lưu — sửa lỗi bên dưới"; gõ lại → lưu bình thường.
- [ ] Chọn **Ai có link**, đặt thời gian làm bài **2 phút** (để thử hết giờ ở mục 5).
- [ ] Câu trắc nghiệm: đổi đáp án đúng bằng vòng tròn → chỉ một đáp án có nhãn "Đúng".
- [ ] Đủ 4 lựa chọn → nút "Thêm lựa chọn" ẩn, thay bằng dòng "Đã đủ 4 lựa chọn (A, B, C, D)". Xoá một → nút hiện lại,
  nhãn A–D tự đánh lại.
- [ ] Đưa một câu lên / xuống → thứ tự đổi, F5 vẫn giữ thứ tự mới.
- [ ] **Thêm câu tự luận**, để trống barem → cảnh báo "AI không chấm câu này". Viết nội dung câu hỏi, không viết barem
  (để thử chấm tay ở mục 7).
- [ ] Thêm một câu trắc nghiệm, không chọn đáp án đúng → bấm **Xuất bản** → câu đó viền đỏ "Chưa xuất bản được: Chọn một
  đáp án đúng", trang cuộn tới câu đó. Chọn đáp án → lỗi tự biến mất.
- [ ] Xoá một câu → hộp xác nhận → câu biến mất, F5 không quay lại.
- [ ] Đang có ô chưa lưu, đóng tab → trình duyệt hỏi có chắc rời trang.
- [ ] **Xuất bản** → xác nhận → trang chuyển sang chế độ chỉ xem: có khung **Chia sẻ** với link (kèm mã), nút
  **Chép link chia sẻ** → "Đã chép link". Không còn ô sửa nào.

## 4. Quyền và chia sẻ

- [ ] B dán link **không có** phần `?share_code=…` → "Không tìm thấy đề này" kèm giải thích thiếu mã.
- [ ] B dán link đầy đủ → trang giới thiệu đề (số câu, thời gian, hạn).
- [ ] (Tuỳ chọn) A tạo một đề **Riêng tư** đã xuất bản; B mở `/exams/<id>/take` → không vào được.
- [ ] B mở `/exams/<id>/edit` hay `/exams/<id>/results` của đề A → "không tìm thấy" (không lộ đáp án).

## 5. B làm bài (4.6)

- [ ] Trang giới thiệu: đồng hồ **chưa** chạy. Bấm **Bắt đầu làm bài** → thanh trên có tiến độ "0/N câu" và đồng hồ.
- [ ] Chọn đáp án → "Đã lưu" dưới câu; ô số câu trong lưới chuyển xanh. **Bỏ chọn** → trở lại "Chưa trả lời".
- [ ] Gõ tự luận → "Đang lưu…" rồi "Đã lưu" sau khoảng 1 giây ngừng gõ; có đếm số từ.
- [ ] Bấm số câu trong lưới để nhảy câu; URL đổi `?q=3`; nút Back của trình duyệt quay về trang trước, không lùi từng câu.
- [ ] F5 giữa chừng → vào thẳng bài đang làm, các câu đã trả lời còn nguyên, đồng hồ tiếp tục (không reset).
- [ ] Tắt Wi-Fi, chọn một đáp án → báo "Mất kết nối mạng", câu đó "Chưa lưu được". Bật lại → tự lưu, báo lỗi biến mất.
- [ ] Bấm **Nộp bài** khi còn câu trống → hộp xác nhận ghi "còn N câu chưa làm" + số các câu; bấm một số → về câu đó.
  Nhấn Enter ngay khi hộp mở → **không** nộp (focus ở "Làm tiếp").
- [ ] **Thử hết giờ:** chờ đồng hồ về 0 (đổi màu vàng lúc còn 5 phút / đỏ lúc còn 1 phút nếu đề dài hơn) → "Đã hết giờ —
  đang nộp bài" → tự sang trang kết quả với dòng "nộp tự động".
- [ ] Bài tôi đã làm (tab thứ hai ở Bài kiểm tra) → bài vừa nộp có trạng thái "Đang chấm" hoặc "Đã chấm".

## 6. B xem kết quả (4.7)

- [ ] Điểm lớn ở đầu trang, thanh phần trăm, số câu trắc nghiệm đúng, thời gian làm.
- [ ] Câu tự luận **có barem**: hiện "AI đang chấm…", trang tự cập nhật trong vòng ~1 phút thành điểm + nhận xét, không cần F5.
- [ ] Câu tự luận **không barem**: "Chờ người ra đề chấm", kèm thông báo vàng phía trên.
- [ ] Trắc nghiệm: đáp án đúng màu xanh "Đáp án đúng", lựa chọn sai màu đỏ "Bạn chọn"; mỗi câu có chữ Đúng / Sai / Bỏ trống.
- [ ] Tích **Chỉ xem câu sai / bỏ trống** → chỉ còn các câu đó.
- [ ] **Làm lại đề này** → về trang giới thiệu, bắt đầu được lần làm mới.

## 7. A xem bài nộp và chấm tay (4.8)

- [ ] Bài kiểm tra → đề vừa giao → **Bài nộp**: số liệu (người làm, bài đã nộp, điểm trung bình, cao / thấp nhất) đúng.
- [ ] Bấm tiêu đề cột **Điểm** / **Nộp lúc** → bảng sắp xếp lại, có mũi tên chiều sắp xếp.
- [ ] Mở bài của B → banner "1 câu cần bạn chấm tay"; tích **Chỉ xem câu cần chấm tay**.
- [ ] Barem tự mở với câu cần chấm. Nhập điểm lớn hơn tối đa → lỗi "Điểm phải từ 0 đến …".
- [ ] Bấm **Tối đa**, viết nhận xét, **Lưu điểm** → "Đã lưu", tổng điểm ở đầu trang cập nhật, câu ghi "Bạn đã chấm".
- [ ] Câu trắc nghiệm → **Sửa điểm câu này** → đổi điểm → lưu được (ghi đè chấm tự động).
- [ ] Bên B, F5 trang kết quả → thấy điểm và nhận xét của A, trạng thái "Đã chấm xong".

## 8. Danh sách "Bài kiểm tra của tôi" (4.3)

- [ ] Tab **Đề tôi tạo**: nhãn Nháp / Đã xuất bản, AI tạo, chế độ chia sẻ; số câu, số bài nộp đúng.
- [ ] Đề nháp chỉ có **Sửa đề**; đề đã xuất bản có Xem đề / Bài nộp / Chép link (khi không riêng tư) / Làm thử.
- [ ] Tab **Bài tôi đã làm** (B): điểm "7,5 / 10", trạng thái; bài đang làm dở có **Làm tiếp**.
- [ ] Đổi tab → URL có `?tab=taken`; F5 vẫn ở đúng tab. Phím mũi tên trái / phải đổi tab khi đang focus ở tab.
- [ ] Tài khoản mới chưa có gì → cả hai tab có lời hướng dẫn (không phải trang trắng); tab Đề tôi tạo có nút "Tạo đề đầu tiên".

## 9. Hoàn thiện (4.10)

**Điện thoại** — DevTools → Toggle device toolbar → iPhone SE (375 px), đi lại mục 2, 3, 5, 6, 7:
- [ ] Không trang nào phải cuộn ngang.
- [ ] Chữ nội dung đủ to để đọc (16 px), gõ vào ô nhập không làm trang tự phóng to.
- [ ] Nút bấm đủ lớn (không bấm nhầm nút bên cạnh); nút Nộp bài / Xuất bản trải hết chiều ngang.
- [ ] Lưới số câu nằm dưới câu hỏi; bảng bài nộp hiện thành từng thẻ.
- [ ] Trang chat: dòng chế độ vẫn hiện dưới tiêu đề.

**Chế độ tối** — macOS: System Settings → Appearance → Dark (hoặc DevTools → Rendering → prefers-color-scheme: dark):
- [ ] Mọi trang đọc rõ; nhãn Đúng / Sai / Nháp / Đang chấm phân biệt được; viền ô nhập nhìn thấy.

**Giảm chuyển động** — DevTools → Rendering → prefers-reduced-motion: reduce:
- [ ] Khung xương lúc tải không nhấp nháy, vòng quay đứng yên, thanh điểm không chạy hiệu ứng.

**Lỗi và mất kết nối:**
- [ ] Tắt uvicorn rồi mở Bài kiểm tra → "Không tải được danh sách" + nút **Thử lại**; bật lại uvicorn, bấm Thử lại → hiện dữ liệu.
- [ ] Đang ở trình sửa đề, tắt uvicorn, sửa một câu → "Lưu lỗi" + Thử lại; bật lại → Thử lại lưu được.
- [ ] Phiên đăng nhập hết hạn (đăng xuất ở tab khác) → thao tác tiếp theo đưa về màn hình đăng nhập, không kẹt.

## 10. Trợ lý bài kiểm tra (Phase 5.6)

Làm sau mục 6–7 (B đã có bài nộp, A đã có đề). Đăng nhập **B**, Trò chuyện → cuộc trò chuyện mới → **Trợ lý bài kiểm tra**.
- [ ] Bấm câu mẫu "Bài gần nhất tôi được mấy điểm?" → trong lúc chờ thấy lần lượt "Đang kiểm tra an toàn câu hỏi…",
  "Đang xem danh sách bài bạn đã làm…"; câu trả lời nêu đúng tên đề, ngày nộp, điểm như trang kết quả.
- [ ] Bấm **Các bước** dưới câu trả lời → 1a, 1b, T1 "Xem danh sách bài đã làm", AI "N lần gọi Gemini".
- [ ] Hỏi "vì sao tôi sai câu 2 bài <tên đề>?" → đúng lựa chọn của B, đáp án đúng, giải thích; nguồn hiện thành chip
  "trang 12" (không bấm được, không có cảnh báo vàng).
- [ ] Hỏi tiếp "còn câu 3 thì sao?" (cùng cuộc trò chuyện) → trợ lý hiểu vẫn là bài đó.
- [ ] Nếu B làm hai đề cùng chương: hỏi "vì sao tôi sai câu 1 đề chương X?" → trợ lý hỏi lại đề nào, không đoán.
- [ ] B bắt đầu làm một đề nhưng chưa nộp, hỏi "đáp án câu 1 bài tôi đang làm?" → từ chối, nói tiến độ, mời nộp bài.
- [ ] Hỏi "xem điểm của <email A>" → từ chối, không nêu điểm nào của A.
- [ ] F5 → mở lại đúng cuộc trò chuyện; lịch sử có icon Trợ lý bài kiểm tra; câu trả lời cũ vẫn có **Các bước**.
- [ ] Đăng nhập **A**, hỏi "đề tôi tạo có bao nhiêu bài nộp?" → đúng số bài nộp; hỏi "đáp án câu 1 đề <tên>?" → đúng.
- [ ] Hết lượt: đặt `AGENT_CALLS_PER_USER_PER_DAY=3` trong `.env`, khởi động lại uvicorn, hỏi 2 câu → câu sau báo
  "Bạn đã dùng hết 3 lượt…" (429), không tạo cuộc trò chuyện rỗng; dưới câu trả lời trước có "Còn N lượt trợ lý hôm nay".
  Xong thì xoá dòng đó khỏi `.env`.
- [ ] Hỏi giáo trình (chế độ kia) vẫn chạy bình thường, không bị giới hạn lượt của trợ lý.
- [ ] (5.8) Nhắn "Tạo giúp tôi đề 5 câu trắc nghiệm chương 3" → lúc chờ "Đang tạo đề nháp bằng AI — thường 15–60
  giây…"; xong có thẻ **Bản nháp** + nút **Xem lại & xuất bản** → mở đúng trang soạn đề, cùng tab; đề là bản nháp.
- [ ] Nhắn "Tạo cho tôi một đề kiểm tra" (không nói chương) → trợ lý hỏi chương, không tạo đề.
- [ ] Nhắn "Tạo 2 đề chương 1 và chương 2" → chỉ một đề được tạo, trợ lý nói rõ.
- [ ] Trang **Bài kiểm tra → Tạo đề mới**: số lượt tạo đề AI còn lại đã giảm theo các đề trợ lý vừa tạo.

---

### Ghi lỗi

| Mục | Bước | Thấy gì | Mong đợi | Ảnh |
|-----|------|---------|----------|-----|
|     |      |         |          |     |
