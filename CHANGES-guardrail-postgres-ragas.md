# Thay đổi: log guardrail, retrieval guard mới, PostgreSQL, đánh giá RAGAS

Tài liệu này liệt kê các thay đổi làm ngày 06/10/2026, tiếp nối phần database trong [CHANGES-database.md](CHANGES-database.md). Tất cả **chưa được commit**, nằm trên nhánh `main` tính từ commit `3c9826e`.

## Tóm tắt

| # | Thay đổi | Vì sao |
|---|---|---|
| 1 | In log những gì lớp 1b và lớp 2 nhìn thấy (`DEBUG_GUARDS=1`) | Câu "gián có hại không" được lớp 2 cho qua với điểm 0.867, cần xem chunk nào gây ra |
| 2 | Script hiệu chỉnh ngưỡng lớp 2 (`calibrate.py`) | Ngưỡng cũ là tạm thời, chưa từng đo bằng dữ liệu |
| 3 | Lớp 2 dùng **cả P(yes) của reranker và cosine** | Chỉ dùng P(yes) thì không tách được câu giáo trình và câu lạc đề |
| 4 | Lịch sử chat chuyển từ SQLite (`chat.db`) sang **PostgreSQL** | Theo yêu cầu; dữ liệu cũ đã được chuyển sang |
| 5 | Đánh giá chất lượng câu trả lời bằng **RAGAS** (`eval_ragas.py`) | Đo câu trả lời có bám tài liệu, đúng câu hỏi, đúng sự thật không, để cải thiện có số liệu |

---

## 1. Log lớp 1b và lớp 2

Đặt `DEBUG_GUARDS=1` (trong `.env` hoặc khi chạy `uvicorn`) thì terminal in ra:

```
[1b] input : 'gián có hại không'
[1b] raw   : 'Safety: Safe\nCategories: None' → label=Safe cats=[]
[2] search_q: 'Gián có hại không?' (10 chunk từ Qdrant → giữ 4)
  #1 rerank=0.867 cos=0.201 | Chương VII, trang 114
     '. Có thể là lợi ích lâu dài, ...'
[2] decision=pass top=0.867 cos=0.201
```

Lưu ý: **lớp 1b không nhìn thấy tài liệu nào**, nó chỉ nhận câu hỏi gốc. Chỉ lớp 2 làm việc với các chunk.

File sửa: `pipeline.py` (`_guard_job`, `_retrieval_job`, sau `retrieval_guard()` trong `_run`). Mặc định tắt.

## 2. Vì sao lớp 2 cho qua câu lạc đề

Từ log và từ endpoint `/search`, tìm ra hai nguyên nhân:

- **Điểm thay đổi theo cách gõ.** "Gián có hại không?" (câu Gemini viết lại khi có lịch sử) được 0.867. "gián có hại không" (câu gõ thẳng) chỉ được 0.448. Với câu lạc đề, không chunk nào thực sự liên quan, nên điểm reranker gần như ngẫu nhiên và dao động mạnh khi đổi chữ hoa hay dấu "?".
- **Reranker bám vào chữ trùng.** Chunk được 0.867 nói về "việc gì **lợi** cho dân… việc gì **hại** cho dân". Reranker 0.6B hiểu câu hỏi là "X có hại không?" và thấy chunk này khớp. P(yes) chỉ so "yes" với "no", không phải độ tin cậy thật.

Trong khi đó **cosine của mọi chunk đều thấp** (0.17–0.31), nhưng lớp 2 lúc đó bỏ qua cosine.

## 3. Hiệu chỉnh ngưỡng: `calibrate.py`

Script mới. Chạy 22 câu giáo trình và 20 câu lạc đề qua `/search` của API đang chạy, mỗi câu thêm một bản viết thường không dấu "?", tổng 84 lượt. Script in điểm từng câu và ngưỡng tách hai nhóm tốt nhất.

```bash
python calibrate.py --variants        # cần API đang chạy ở cổng 8000
```

Kết quả với ngưỡng cũ (`MIN_TOP = 0.5`):

| Chỉ số | Câu giáo trình thấp nhất | Câu lạc đề cao nhất | Tách được? |
|---|---|---|---|
| P(yes) reranker | 0.972 | **0.982** ("đội bóng đoàn kết") | Không |
| Cosine của chunk đầu | 0.488 | 0.432 | Có, nhưng khoảng trống hẹp |

Ngưỡng cũ cho lọt **7/84** câu lạc đề, ví dụ "gián có hại không", "ăn nhiều đường có lợi hay hại", "con mèo có mấy cái chân".

## 4. Lớp 2 mới: P(yes) VÀ cosine

File sửa: `search_rerank.py`

| Quyết định | Điều kiện (chunk đứng đầu sau rerank) |
|---|---|
| **pass** | P(yes) ≥ 0.9 **và** cosine ≥ 0.45 |
| **partial** | P(yes) ≥ 0.5 **và** cosine ≥ 0.40 (Gemini trả lời kèm lưu ý, luôn có giám khảo 4a) |
| **refuse** | còn lại |

Kết quả: **0/84** câu sai. Ba câu lạc đề rơi vào partial: "đội bóng đoàn kết" (cả 2 cách gõ) và "Hồ Chí Minh thích ăn món gì nhất". Chúng vẫn đi qua Gemini và giám khảo, như trước đây. Câu "gián có hại không" giờ bị chặn ngay ở lớp 2, không tốn lượt Gemini.

Các file liên quan:

| File | Thay đổi |
|---|---|
| `pipeline.py` | `ChatResult` thêm `retrieval_top_cos` (cosine của chunk đầu) |
| `web/src/trace.js` | Dòng lớp 2 hiện thêm cosine, vd. "refuse · P(yes) cao nhất 0.982 · cosine 0.416". Câu trả lời cũ trong lịch sử không có trường này |
| `guards.py` | Sửa chú thích mô tả lớp 2 |
| `test_guards.py` | Test lớp 2 theo cả hai chỉ số, gồm ca "gián có hại" và "đội bóng đoàn kết" |
| `test_pipeline.py` | Test partial dùng P(yes) 0.7 (0.4 giờ là refuse) |

**Lưu ý:** ngưỡng được chọn từ câu hỏi viết theo văn giáo trình. Câu hỏi thật của sinh viên thường ngắn hơn, mơ hồ hơn, nên có thể có điểm thấp hơn. Nên thêm câu hỏi thật vào `calibrate.py` rồi chạy lại.

## 5. Lịch sử chat chuyển sang PostgreSQL

Database chạy trong container Docker `postgres` (PostgreSQL 18), database `hcm_chat`. Kết nối qua `DATABASE_URL` trong `.env`.

| File | Thay đổi |
|---|---|
| `db.py` | Kết nối theo `DATABASE_URL`; thiếu thì báo lỗi rõ ràng lúc khởi động (không âm thầm dùng SQLite). Cột `meta` dùng `JSONB`. Cấu hình riêng của SQLite (khoá ngoại, WAL) chỉ còn dùng cho test |
| `migrate_sqlite.py` | **Mới.** Chép lịch sử từ `chat.db` sang Postgres, giữ nguyên id. Dừng nếu Postgres đã có dữ liệu; không sửa `chat.db` |
| `conftest.py` | **Mới.** Fixture database chung cho `test_db.py` và `test_api.py`. Mặc định SQLite tạm; đặt `TEST_DATABASE_URL` để chạy trên Postgres. Từ chối chạy nếu trùng database thật |
| `test_db.py`, `test_api.py` | Dùng fixture chung thay cho đoạn tạo SQLite riêng |
| `api.py` | Sửa chú thích (SQLite → PostgreSQL) |
| `requirements.txt` | Thêm `psycopg[binary]` |
| `CHANGES-database.md` | Thêm mục "Chuyển sang PostgreSQL" |

Đã làm:

- Chuyển **4 cuộc trò chuyện, 24 tin nhắn** từ `chat.db`; thời gian khớp, bộ đếm id được đẩy lên đúng (cuộc trò chuyện mới nhận id 5).
- Tạo database `hcm_chat_test`, chỉ dùng cho test.
- `chat.db` vẫn còn nguyên, xoá khi đã yên tâm.

Xem dữ liệu:

```bash
docker exec -it postgres psql -U hcm -d hcm_chat
```

```sql
-- tin nhắn mới nhất, tự làm mới mỗi 2 giây
SELECT id, conversation_id, role, left(content, 70) FROM messages ORDER BY id DESC LIMIT 6 \watch 2
```

## 6. Đánh giá bằng RAGAS

| File | Nội dung |
|---|---|
| `eval_ragas.py` | **Mới.** Chạy pipeline thật cho từng câu trong testset, chấm bằng RAGAS (Gemini làm giám khảo), in báo cáo và lưu kết quả |
| `eval_testset.json` | **Mới.** 12 câu giáo trình (`"expect": "answer"`) và 4 câu lạc đề (`"expect": "refuse"`) |
| `eval_runs/` | Kết quả mỗi lần chạy, kèm cấu hình (model, ngưỡng lớp 2, `TOP_N`) để so sánh trước/sau khi sửa |
| `requirements.txt` | Thêm `ragas==0.4.3`, `jsonref` |
| `.gitignore` | Thêm `.ragas_cache/` (cache kết quả giám khảo) |

Các chỉ số:

| Chỉ số | Đo gì | Thấp thì sửa |
|---|---|---|
| faithfulness | Câu trả lời có bịa ngoài tài liệu không | Prompt lớp 3, giám khảo 4a |
| answer_relevancy | Có trả lời đúng câu được hỏi không | Câu trả lời lệch đề, lấp lửng |
| context_relevance | Chunk tìm được có liên quan câu hỏi không | Retrieval: chunk, embedding, rerank, `TOP_N` |
| context_recall¹ | Chunk tìm được có đủ ý của đáp án chuẩn không | Retrieval bỏ sót |
| factual_correctness¹ | Câu trả lời khớp đáp án chuẩn tới đâu | Sai so với sự thật |

¹ Chỉ chạy khi câu hỏi có `"reference"` (đáp án chuẩn do người viết).

Báo cáo còn đếm câu giáo trình bị từ chối (**bỏ sót**) và câu lạc đề được trả lời (**lọt**).

```bash
python eval_ragas.py                              # toàn bộ testset
python eval_ragas.py --limit 3                    # thử nhanh
python eval_ragas.py --rescore eval_runs/X.json   # chấm lại câu trả lời đã lưu, không chạy pipeline
```

Kết quả thử 3 câu đầu (`eval_runs/20261006-105726.json`): faithfulness 0.97, answer_relevancy 0.93, context_relevance 1.00 (trung bình).

Một số điểm kỹ thuật trong `eval_ragas.py`:

- **ragas 0.4.3 không chạy thẳng với project.** Thư viện vẫn import module VertexAI đã bị gỡ khỏi `langchain-community` 0.4, nên script tạo module giả trước khi import ragas.
- **Client Gemini bản async được tạo tay**, vì ragas tự bọc client ở chế độ sync nhưng metric lại gọi async.
- **Giới hạn đầu ra của giám khảo là 8192 token**, vì token "thinking" của Gemini cũng tính vào giới hạn này.
- **answer_relevancy dùng embedder Ollama local** (qwen3-embedding) nên không tốn quota. Prompt được thêm yêu cầu sinh câu hỏi cùng ngôn ngữ với câu trả lời.

## Đã kiểm tra

- **Test:** 96 test đều qua trên SQLite tạm. 16 test của `test_db.py` và `test_api.py` cũng qua trên Postgres thật (`hcm_chat_test`).
- **Web:** build không lỗi.
- **API với Postgres:** chạy thử ở cổng 8001. Đọc được 4 cuộc trò chuyện cũ; ghi, đọc và xoá một cuộc trò chuyện thử (xoá dây chuyền cả tin nhắn).
- **Lớp 2:** `calibrate.py` chạy trên API thật, 0/84 câu sai.
- **RAGAS:** chạy thật 3 câu; cả 5 chỉ số đều chấm được; chấm lại dùng cache (8 giây, không gọi Gemini).

## Hạn chế đã biết

- **Ngưỡng lớp 2 chưa đo trên câu hỏi thật.** Khoảng cách cosine giữa hai nhóm hẹp (0.432 → 0.488).
- **Testset RAGAS chưa có đáp án chuẩn**, nên chưa đo được context_recall và factual_correctness. Đáp án chuẩn cần do người viết từ giáo trình, không lấy từ chunk mà pipeline tìm được (nếu không thì context_recall luôn gần 1).
- **Giám khảo RAGAS mặc định là chính model trả lời** (`gemini-3.8-flash`) nên có xu hướng chấm dễ. Đặt `RAGAS_MODEL` trong `.env` để dùng model khác.
- **Chi phí RAGAS:** khoảng 8 lần gọi Gemini cho mỗi câu đã trả lời, thêm khoảng 5 nếu có đáp án chuẩn. Toàn bộ testset khoảng 150 lần gọi.
- **16 câu là mẫu nhỏ:** chênh lệch trung bình 0.05 giữa hai lần chạy phần lớn là nhiễu.
- **Module giả cho VertexAI** trong `eval_ragas.py` cần bỏ khi ragas sửa lỗi import.
- **README chưa được cập nhật** cho các thay đổi này.

## Cách chạy

```bash
# Terminal 0 — Postgres và Qdrant (nếu container chưa chạy)
docker start postgres qdrant

# Terminal 1 — API (thư mục gốc dự án)
source .venv/bin/activate
DEBUG_GUARDS=1 uvicorn api:app --reload      # bỏ DEBUG_GUARDS=1 nếu không cần log

# Terminal 2 — trang web
cd web && npm run dev

# Khi cần đo lại
python calibrate.py --variants               # ngưỡng lớp 2 (cần API đang chạy)
python eval_ragas.py                         # chất lượng câu trả lời (tự nạp model)
```
