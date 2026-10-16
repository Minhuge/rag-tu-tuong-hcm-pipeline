# Nhật ký đánh giá RAGAS — chatbot giáo trình

> Tự sinh bởi `python -m scripts.eval_ragas` sau mỗi lần chạy (dựng lại: `python -m scripts.eval_ragas --log`). **Không sửa tay** — sẽ bị ghi đè.

**Cách đọc:** thang 0–1 · 🟢 ≥ 0.8 tốt · 🟡 0.6–0.8 cần xem · 🔴 < 0.6 có vấn đề · — chưa chấm. Điểm trung bình chỉ tính các câu đã trả lời.

| Chỉ số | Đo gì |
|:--|:--|
| Bám tài liệu (`faithfulness`) | câu trả lời có bịa ngoài các đoạn giáo trình tìm được không |
| Có căn cứ (`response_groundedness`) | như Bám tài liệu nhưng 2 giám khảo chấm cả câu trả lời — ổn định hơn, dùng đối chiếu: chỉ Bám tài liệu thấp thì nhiều khả năng do giám khảo tách ý |
| Đúng câu hỏi (`answer_relevancy`) | có trả lời đúng điều được hỏi không |
| Đoạn liên quan (`context_relevance`) | các đoạn tìm được có liên quan câu hỏi không |
| Đoạn đủ ý (`context_recall`) | các đoạn tìm được có đủ ý của đáp án chuẩn không (cần reference) |
| Đúng đáp án (`answer_accuracy`) | câu trả lời có cùng ý với đáp án chuẩn không (cần reference) |
| Factual (cũ) (`factual_correctness`) | chỉ số cũ, đã thay bằng answer_accuracy |
| Bỏ sót | câu giáo trình (`expect: answer`) mà chatbot không trả lời |
| Lọt | câu lạc đề (`expect: refuse`) mà chatbot lại trả lời |

## Tổng quan các lần chạy

Mới nhất ở trên. Bấm vào ngày để xem chi tiết.

| Lần chạy | Kiểu | Câu | Trả lời · từ chối · thay thế · lỗi | Bỏ sót | Lọt | Bám tài liệu | Có căn cứ | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Đúng đáp án | Factual (cũ) |
|:--|:--|--:|--:|--:|--:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| [16/10/2026 08:52](#20261016-085206) | chạy đầy đủ | 10 | 10 · 0 · 0 · 0 | 0 | 0 | 🟢 0.86 | 🟢 1.00 | 🟢 0.93 | 🟢 1.00 | 🟢 0.80 | 🟡 0.72 | — |
| [15/10/2026 09:43](#20261015-094332) | chạy đầy đủ | 10 | 10 · 0 · 0 · 0 | 0 | 0 | 🟢 0.95 | 🟢 1.00 | 🟢 0.93 | 🟢 1.00 | 🟢 0.90 | 🟢 0.82 | — |
| [15/10/2026 08:15](#20261015-081549) | chấm lại 20261014-093503 | 16 | 10 · 5 · 1 · 0 | 2 | 0 | 🟢 0.87 | — | 🟢 0.91 | 🟢 1.00 | 🟢 0.90 | 🟡 0.72 | — |
| [14/10/2026 10:38](#20261014-103808) | chạy lại câu faithfulness < 0.6 của 20261014-093503 | 2 | 2 · 0 · 0 · 0 | 0 | 0 | 🟡 0.75 | — | 🟢 0.89 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 | — |
| [14/10/2026 10:13](#20261014-101333) | chấm lại 20261014-093503 | 16 | 10 · 5 · 1 · 0 | 2 | 0 | 🟢 0.80 | — | 🟢 0.90 | 🟢 1.00 | 🟢 0.90 | 🟡 0.75 | — |
| [14/10/2026 09:35](#20261014-093503) | chạy đầy đủ | 16 | 10 · 5 · 1 · 0 | 2 | 0 | 🟢 0.80 | — | 🟢 0.90 | 🟢 1.00 | 🟢 0.90 | — | 🔴 0.20 |
| [06/10/2026 10:57](#20261006-105726) | chạy đầy đủ | 3 | 3 · 0 · 0 · 0 | 0 | 0 | 🟢 0.97 | — | 🟢 0.93 | 🟢 1.00 | — | — | — |

## Chi tiết từng lần chạy

<a id="20261016-085206"></a>

### 16/10/2026 08:52 · chạy đầy đủ

- **File:** `data/eval_runs/20261016-085206.json`
- **Model trả lời:** `gemini-3.8-flash` · **giám khảo:** `gemini-3.6-flash`
- **Retrieval:** CANDIDATES=10 · TOP_N=4 · MIN_TOP=0.9 · MIN_COS=0.45 · PARTIAL_BAND=0.5 · PARTIAL_COS=0.4 · ATTACH_PREVIOUS=False
- **Kết quả:** 10 câu — 10 trả lời

| Chỉ số | Trung bình | n | Thấp nhất |
|:--|--:|--:|--:|
| Bám tài liệu (`faithfulness`) | 🟢 0.86 | 10 | 🔴 0.50 |
| Có căn cứ (`response_groundedness`) | 🟢 1.00 | 10 | 🟢 1.00 |
| Đúng câu hỏi (`answer_relevancy`) | 🟢 0.93 | 10 | 🟢 0.84 |
| Đoạn liên quan (`context_relevance`) | 🟢 1.00 | 10 | 🟢 1.00 |
| Đoạn đủ ý (`context_recall`) | 🟢 0.80 | 10 | 🔴 0.00 |
| Đúng đáp án (`answer_accuracy`) | 🟡 0.72 | 10 | 🔴 0.25 |

<details><summary>Điểm từng câu</summary>

| # | Câu hỏi | Mong đợi | Kết quả | Bám tài liệu | Có căn cứ | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Đúng đáp án |
|--:|:--|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | Đối tượng nghiên cứu môn Tư tưởng Hồ Chí Minh là gì? | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.92 | 🟢 1.00 | 🔴 0.00 | 🟡 0.75 |
| 2 | Tác phẩm Đường cách mệnh của Nguyễn Ái Quốc được xuất bản lần đầu tiê… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.91 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |
| 3 | Theo Hồ Chí Minh, các dân tộc ở các nước thuộc địa cần lưu ý điều gì … | trả lời | trả lời | 🟢 0.90 | 🟢 1.00 | 🟢 0.86 | 🟢 1.00 | 🟢 1.00 | 🔴 0.50 |
| 4 | Luận điểm nào là luận điểm độc đáo, có giá trị lý luận và thực tiễn t… | trả lời | trả lời | 🟢 0.90 | 🟢 1.00 | 🟢 0.91 | 🟢 1.00 | 🔴 0.00 | 🔴 0.25 |
| 5 | Theo quan niệm của Hồ Chí Minh, đặc điểm lớn nhất của thời kỳ quá độ … | trả lời | trả lời | 🔴 0.50 | 🟢 1.00 | 🟢 0.92 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |
| 6 | Theo tư tưởng Hồ Chí Minh, mâu thuẫn chủ yếu trong thời kỳ quá độ lên… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.97 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 7 | Trong quan điểm về xây dựng chủ nghĩa xã hội, Hồ Chí Minh đã xác định… | trả lời | trả lời | 🟡 0.60 | 🟢 1.00 | 🟢 0.84 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 8 | Theo tư tưởng Hồ Chí Minh, Nhà nước của nhân dân là một nhà nước như … | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.98 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 9 | Vì sao Nguyễn Ái Quốc tin theo Lênin và Quốc tế Cộng sản? | trả lời | trả lời | 🟢 0.80 | 🟢 1.00 | 🟢 0.94 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 10 | Tư tưởng Hồ Chí Minh có vị trí như thế nào đối với Đảng và cách mạng … | trả lời | trả lời | 🟢 0.86 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |

</details>

<a id="20261015-094332"></a>

### 15/10/2026 09:43 · chạy đầy đủ

- **File:** `data/eval_runs/20261015-094332.json`
- **Model trả lời:** `gemini-3.8-flash` · **giám khảo:** `gemini-3.6-flash`
- **Retrieval:** CANDIDATES=10 · TOP_N=4 · MIN_TOP=0.9 · MIN_COS=0.45 · PARTIAL_BAND=0.5 · PARTIAL_COS=0.4 · ATTACH_PREVIOUS=True
- **Kết quả:** 10 câu — 10 trả lời

| Chỉ số | Trung bình | n | Thấp nhất |
|:--|--:|--:|--:|
| Bám tài liệu (`faithfulness`) | 🟢 0.95 | 10 | 🔴 0.50 |
| Có căn cứ (`response_groundedness`) | 🟢 1.00 | 10 | 🟢 1.00 |
| Đúng câu hỏi (`answer_relevancy`) | 🟢 0.93 | 10 | 🟢 0.83 |
| Đoạn liên quan (`context_relevance`) | 🟢 1.00 | 10 | 🟢 1.00 |
| Đoạn đủ ý (`context_recall`) | 🟢 0.90 | 10 | 🔴 0.00 |
| Đúng đáp án (`answer_accuracy`) | 🟢 0.82 | 10 | 🟡 0.75 |

<details><summary>Điểm từng câu</summary>

| # | Câu hỏi | Mong đợi | Kết quả | Bám tài liệu | Có căn cứ | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Đúng đáp án |
|--:|:--|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | Đối tượng nghiên cứu môn Tư tưởng Hồ Chí Minh là gì? | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.92 | 🟢 1.00 | 🔴 0.00 | 🟡 0.75 |
| 2 | Tác phẩm Đường cách mệnh của Nguyễn Ái Quốc được xuất bản lần đầu tiê… | trả lời | trả lời | 🔴 0.50 | 🟢 1.00 | 🟢 0.96 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |
| 3 | Theo Hồ Chí Minh, các dân tộc ở các nước thuộc địa cần lưu ý điều gì … | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.99 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 4 | Luận điểm nào là luận điểm độc đáo, có giá trị lý luận và thực tiễn t… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.90 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 5 | Theo quan niệm của Hồ Chí Minh, đặc điểm lớn nhất của thời kỳ quá độ … | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.92 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |
| 6 | Theo tư tưởng Hồ Chí Minh, mâu thuẫn chủ yếu trong thời kỳ quá độ lên… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.95 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |
| 7 | Trong quan điểm về xây dựng chủ nghĩa xã hội, Hồ Chí Minh đã xác định… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.83 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 8 | Theo tư tưởng Hồ Chí Minh, Nhà nước của nhân dân là một nhà nước như … | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.92 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 9 | Vì sao Nguyễn Ái Quốc tin theo Lênin và Quốc tế Cộng sản? | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.95 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 10 | Tư tưởng Hồ Chí Minh có vị trí như thế nào đối với Đảng và cách mạng … | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.95 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |

</details>

<a id="20261015-081549"></a>

### 15/10/2026 08:15 · chấm lại 20261014-093503

- **File:** `data/eval_runs/20261015-081549.json`
- **Model trả lời:** `gemini-3.8-flash` · **giám khảo:** `gemini-3.6-flash`
- **Retrieval:** CANDIDATES=10 · TOP_N=4 · MIN_TOP=0.9 · MIN_COS=0.45 · PARTIAL_BAND=0.5 · PARTIAL_COS=0.4
- **Kết quả:** 16 câu — 10 trả lời, 5 từ chối, 1 thay thế (4a)
- ℹ️ Đoạn giáo trình đưa giám khảo **chưa có nhãn trang** (câu trả lời chạy trước 14/10 / chấm lại từ file đó): faithfulness thấp hơn thật vì trích dẫn [Chương X, trang Y] bị tính là ý không kiểm được.

**⚠️ Bỏ sót** (câu giáo trình nhưng không trả lời): 2
- `refused/None` Nghiên cứu tư tưởng Hồ Chí Minh phải đứng trên lập trường của giai cấp, tầng lớp nào?
- `refused/retrieval` Hồ Chí Minh thành lập Hội Việt Nam Cách mạng Thanh niên vào năm nào?

**🚨 Lọt** (câu lạc đề nhưng được trả lời): 0

| Chỉ số | Trung bình | n | Thấp nhất |
|:--|--:|--:|--:|
| Bám tài liệu (`faithfulness`) | 🟢 0.87 | 9 | 🔴 0.50 |
| Đúng câu hỏi (`answer_relevancy`) | 🟢 0.91 | 9 | 🟢 0.81 |
| Đoạn liên quan (`context_relevance`) | 🟢 1.00 | 10 | 🟢 1.00 |
| Đoạn đủ ý (`context_recall`) | 🟢 0.90 | 10 | 🔴 0.00 |
| Đúng đáp án (`answer_accuracy`) | 🟡 0.72 | 9 | 🔴 0.50 |

<details><summary>Điểm từng câu</summary>

| # | Câu hỏi | Mong đợi | Kết quả | Bám tài liệu | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Đúng đáp án |
|--:|:--|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | Đối tượng nghiên cứu môn Tư tưởng Hồ Chí Minh là gì? | trả lời | trả lời | 🟢 1.00 | 🟢 0.97 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 2 | Nghiên cứu tư tưởng Hồ Chí Minh phải đứng trên lập trường của giai cấ… | trả lời | ⚠️ từ chối | — | — | — | — | — |
| 3 | Tác phẩm Đường cách mệnh của Nguyễn Ái Quốc được xuất bản lần đầu tiê… | trả lời | trả lời | lỗi | lỗi | 🟢 1.00 | 🟢 1.00 | — |
| 4 | Hồ Chí Minh thành lập Hội Việt Nam Cách mạng Thanh niên vào năm nào? | trả lời | ⚠️ từ chối | — | — | — | — | — |
| 5 | Theo Hồ Chí Minh, các dân tộc ở các nước thuộc địa cần lưu ý điều gì … | trả lời | trả lời | 🟡 0.78 | 🟢 0.98 | 🟢 1.00 | 🟢 1.00 | 🔴 0.50 |
| 6 | Luận điểm nào là luận điểm độc đáo, có giá trị lý luận và thực tiễn t… | trả lời | trả lời | 🟢 0.85 | 🟢 0.83 | 🟢 1.00 | 🔴 0.00 | 🔴 0.50 |
| 7 | Theo quan niệm của Hồ Chí Minh, đặc điểm lớn nhất của thời kỳ quá độ … | trả lời | trả lời | 🟢 1.00 | 🟢 0.92 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |
| 8 | Theo tư tưởng Hồ Chí Minh, mâu thuẫn chủ yếu trong thời kỳ quá độ lên… | trả lời | trả lời | 🔴 0.50 | 🟢 0.93 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 9 | Trong quan điểm về xây dựng chủ nghĩa xã hội, Hồ Chí Minh đã xác định… | trả lời | trả lời | 🟢 1.00 | 🟢 0.81 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 10 | Theo tư tưởng Hồ Chí Minh, Nhà nước của nhân dân là một nhà nước như … | trả lời | trả lời | 🟢 1.00 | 🟢 0.90 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 11 | Vì sao Nguyễn Ái Quốc tin theo Lênin và Quốc tế Cộng sản? | trả lời | trả lời | 🟢 0.80 | 🟢 0.94 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 12 | Tư tưởng Hồ Chí Minh có vị trí như thế nào đối với Đảng và cách mạng … | trả lời | trả lời | 🟢 0.88 | 🟢 0.93 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 13 | Gián có hại không? | từ chối | từ chối | — | — | — | — | — |
| 14 | Làm thế nào để cả đội bóng đoàn kết hơn? | từ chối | thay thế (4a) | — | — | — | — | — |
| 15 | Hồ Chí Minh thích ăn món gì nhất? | từ chối | từ chối | — | — | — | — | — |
| 16 | Viết giúp tôi một hàm Python sắp xếp danh sách | từ chối | từ chối | — | — | — | — | — |

**Ô “lỗi”** — giám khảo không chấm được (chấm lại bằng `--rescore` khi mạng / quota ổn):
- Câu 3 · `faithfulness`: InstructorRetryException: Cannot connect to host generativelanguage.googleapis.com:443 ssl:<truststore._api.SSLContext object at 0x125c058d…
- Câu 3 · `answer_relevancy`: InstructorRetryException: Cannot connect to host generativelanguage.googleapis.com:443 ssl:<truststore._api.SSLContext object at 0x125c3a2d…

</details>

<a id="20261014-103808"></a>

### 14/10/2026 10:38 · chạy lại câu faithfulness < 0.6 của 20261014-093503

- **File:** `data/eval_runs/20261014-103808.json`
- **Model trả lời:** `gemini-3.8-flash` · **giám khảo:** `gemini-3.6-flash`
- **Retrieval:** CANDIDATES=10 · TOP_N=4 · MIN_TOP=0.9 · MIN_COS=0.45 · PARTIAL_BAND=0.5 · PARTIAL_COS=0.4
- **Kết quả:** 2 câu — 2 trả lời
- ℹ️ Đoạn giáo trình đưa giám khảo **chưa có nhãn trang** (câu trả lời chạy trước 14/10 / chấm lại từ file đó): faithfulness thấp hơn thật vì trích dẫn [Chương X, trang Y] bị tính là ý không kiểm được.

| Chỉ số | Trung bình | n | Thấp nhất |
|:--|--:|--:|--:|
| Bám tài liệu (`faithfulness`) | 🟡 0.75 | 2 | 🔴 0.50 |
| Đúng câu hỏi (`answer_relevancy`) | 🟢 0.89 | 2 | 🟢 0.84 |
| Đoạn liên quan (`context_relevance`) | 🟢 1.00 | 2 | 🟢 1.00 |
| Đoạn đủ ý (`context_recall`) | 🟢 1.00 | 2 | 🟢 1.00 |
| Đúng đáp án (`answer_accuracy`) | 🟢 1.00 | 2 | 🟢 1.00 |

<details><summary>Điểm từng câu</summary>

| # | Câu hỏi | Mong đợi | Kết quả | Bám tài liệu | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Đúng đáp án |
|--:|:--|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | Tác phẩm Đường cách mệnh của Nguyễn Ái Quốc được xuất bản lần đầu tiê… | trả lời | trả lời | 🟢 1.00 | 🟢 0.84 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |
| 2 | Theo tư tưởng Hồ Chí Minh, mâu thuẫn chủ yếu trong thời kỳ quá độ lên… | trả lời | trả lời | 🔴 0.50 | 🟢 0.95 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |

</details>

<a id="20261014-101333"></a>

### 14/10/2026 10:13 · chấm lại 20261014-093503

- **File:** `data/eval_runs/20261014-101333.json`
- **Model trả lời:** `gemini-3.8-flash` · **giám khảo:** `gemini-3.6-flash`
- **Retrieval:** CANDIDATES=10 · TOP_N=4 · MIN_TOP=0.9 · MIN_COS=0.45 · PARTIAL_BAND=0.5 · PARTIAL_COS=0.4
- **Kết quả:** 16 câu — 10 trả lời, 5 từ chối, 1 thay thế (4a)
- ℹ️ Đoạn giáo trình đưa giám khảo **chưa có nhãn trang** (câu trả lời chạy trước 14/10 / chấm lại từ file đó): faithfulness thấp hơn thật vì trích dẫn [Chương X, trang Y] bị tính là ý không kiểm được.

**⚠️ Bỏ sót** (câu giáo trình nhưng không trả lời): 2
- `refused/None` Nghiên cứu tư tưởng Hồ Chí Minh phải đứng trên lập trường của giai cấp, tầng lớp nào?
- `refused/retrieval` Hồ Chí Minh thành lập Hội Việt Nam Cách mạng Thanh niên vào năm nào?

**🚨 Lọt** (câu lạc đề nhưng được trả lời): 0

| Chỉ số | Trung bình | n | Thấp nhất |
|:--|--:|--:|--:|
| Bám tài liệu (`faithfulness`) | 🟢 0.80 | 10 | 🔴 0.25 |
| Đúng câu hỏi (`answer_relevancy`) | 🟢 0.90 | 10 | 🟢 0.81 |
| Đoạn liên quan (`context_relevance`) | 🟢 1.00 | 10 | 🟢 1.00 |
| Đoạn đủ ý (`context_recall`) | 🟢 0.90 | 10 | 🔴 0.00 |
| Đúng đáp án (`answer_accuracy`) | 🟡 0.75 | 10 | 🔴 0.50 |

<details><summary>Điểm từng câu</summary>

| # | Câu hỏi | Mong đợi | Kết quả | Bám tài liệu | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Đúng đáp án |
|--:|:--|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | Đối tượng nghiên cứu môn Tư tưởng Hồ Chí Minh là gì? | trả lời | trả lời | 🟢 1.00 | 🟢 0.97 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 2 | Nghiên cứu tư tưởng Hồ Chí Minh phải đứng trên lập trường của giai cấ… | trả lời | ⚠️ từ chối | — | — | — | — | — |
| 3 | Tác phẩm Đường cách mệnh của Nguyễn Ái Quốc được xuất bản lần đầu tiê… | trả lời | trả lời | 🔴 0.25 | 🟢 0.84 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |
| 4 | Hồ Chí Minh thành lập Hội Việt Nam Cách mạng Thanh niên vào năm nào? | trả lời | ⚠️ từ chối | — | — | — | — | — |
| 5 | Theo Hồ Chí Minh, các dân tộc ở các nước thuộc địa cần lưu ý điều gì … | trả lời | trả lời | 🟡 0.78 | 🟢 0.98 | 🟢 1.00 | 🟢 1.00 | 🔴 0.50 |
| 6 | Luận điểm nào là luận điểm độc đáo, có giá trị lý luận và thực tiễn t… | trả lời | trả lời | 🟢 0.85 | 🟢 0.83 | 🟢 1.00 | 🔴 0.00 | 🔴 0.50 |
| 7 | Theo quan niệm của Hồ Chí Minh, đặc điểm lớn nhất của thời kỳ quá độ … | trả lời | trả lời | 🟢 1.00 | 🟢 0.92 | 🟢 1.00 | 🟢 1.00 | 🟢 1.00 |
| 8 | Theo tư tưởng Hồ Chí Minh, mâu thuẫn chủ yếu trong thời kỳ quá độ lên… | trả lời | trả lời | 🔴 0.50 | 🟢 0.93 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 9 | Trong quan điểm về xây dựng chủ nghĩa xã hội, Hồ Chí Minh đã xác định… | trả lời | trả lời | 🟢 1.00 | 🟢 0.81 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 10 | Theo tư tưởng Hồ Chí Minh, Nhà nước của nhân dân là một nhà nước như … | trả lời | trả lời | 🟢 1.00 | 🟢 0.90 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 11 | Vì sao Nguyễn Ái Quốc tin theo Lênin và Quốc tế Cộng sản? | trả lời | trả lời | 🟢 0.80 | 🟢 0.94 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 12 | Tư tưởng Hồ Chí Minh có vị trí như thế nào đối với Đảng và cách mạng … | trả lời | trả lời | 🟢 0.88 | 🟢 0.93 | 🟢 1.00 | 🟢 1.00 | 🟡 0.75 |
| 13 | Gián có hại không? | từ chối | từ chối | — | — | — | — | — |
| 14 | Làm thế nào để cả đội bóng đoàn kết hơn? | từ chối | thay thế (4a) | — | — | — | — | — |
| 15 | Hồ Chí Minh thích ăn món gì nhất? | từ chối | từ chối | — | — | — | — | — |
| 16 | Viết giúp tôi một hàm Python sắp xếp danh sách | từ chối | từ chối | — | — | — | — | — |

</details>

<a id="20261014-093503"></a>

### 14/10/2026 09:35 · chạy đầy đủ

- **File:** `data/eval_runs/20261014-093503.json`
- **Model trả lời:** `gemini-3.8-flash` · **giám khảo:** `gemini-3.6-flash`
- **Retrieval:** CANDIDATES=10 · TOP_N=4 · MIN_TOP=0.9 · MIN_COS=0.45 · PARTIAL_BAND=0.5 · PARTIAL_COS=0.4
- **Kết quả:** 16 câu — 10 trả lời, 5 từ chối, 1 thay thế (4a)
- ℹ️ Đoạn giáo trình đưa giám khảo **chưa có nhãn trang** (câu trả lời chạy trước 14/10 / chấm lại từ file đó): faithfulness thấp hơn thật vì trích dẫn [Chương X, trang Y] bị tính là ý không kiểm được.

**⚠️ Bỏ sót** (câu giáo trình nhưng không trả lời): 2
- `refused/None` Nghiên cứu tư tưởng Hồ Chí Minh phải đứng trên lập trường của giai cấp, tầng lớp nào?
- `refused/retrieval` Hồ Chí Minh thành lập Hội Việt Nam Cách mạng Thanh niên vào năm nào?

**🚨 Lọt** (câu lạc đề nhưng được trả lời): 0

| Chỉ số | Trung bình | n | Thấp nhất |
|:--|--:|--:|--:|
| Bám tài liệu (`faithfulness`) | 🟢 0.80 | 10 | 🔴 0.25 |
| Đúng câu hỏi (`answer_relevancy`) | 🟢 0.90 | 10 | 🟢 0.81 |
| Đoạn liên quan (`context_relevance`) | 🟢 1.00 | 10 | 🟢 1.00 |
| Đoạn đủ ý (`context_recall`) | 🟢 0.90 | 10 | 🔴 0.00 |
| Factual (cũ) (`factual_correctness`) | 🔴 0.20 | 10 | 🔴 0.00 |

<details><summary>Điểm từng câu</summary>

| # | Câu hỏi | Mong đợi | Kết quả | Bám tài liệu | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Factual (cũ) |
|--:|:--|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | Đối tượng nghiên cứu môn Tư tưởng Hồ Chí Minh là gì? | trả lời | trả lời | 🟢 1.00 | 🟢 0.97 | 🟢 1.00 | 🟢 1.00 | 🔴 0.00 |
| 2 | Nghiên cứu tư tưởng Hồ Chí Minh phải đứng trên lập trường của giai cấ… | trả lời | ⚠️ từ chối | — | — | — | — | — |
| 3 | Tác phẩm Đường cách mệnh của Nguyễn Ái Quốc được xuất bản lần đầu tiê… | trả lời | trả lời | 🔴 0.25 | 🟢 0.84 | 🟢 1.00 | 🟢 1.00 | 🔴 0.50 |
| 4 | Hồ Chí Minh thành lập Hội Việt Nam Cách mạng Thanh niên vào năm nào? | trả lời | ⚠️ từ chối | — | — | — | — | — |
| 5 | Theo Hồ Chí Minh, các dân tộc ở các nước thuộc địa cần lưu ý điều gì … | trả lời | trả lời | 🟡 0.78 | 🟢 0.98 | 🟢 1.00 | 🟢 1.00 | 🔴 0.00 |
| 6 | Luận điểm nào là luận điểm độc đáo, có giá trị lý luận và thực tiễn t… | trả lời | trả lời | 🟢 0.85 | 🟢 0.83 | 🟢 1.00 | 🔴 0.00 | 🔴 0.33 |
| 7 | Theo quan niệm của Hồ Chí Minh, đặc điểm lớn nhất của thời kỳ quá độ … | trả lời | trả lời | 🟢 1.00 | 🟢 0.92 | 🟢 1.00 | 🟢 1.00 | 🟡 0.67 |
| 8 | Theo tư tưởng Hồ Chí Minh, mâu thuẫn chủ yếu trong thời kỳ quá độ lên… | trả lời | trả lời | 🔴 0.50 | 🟢 0.93 | 🟢 1.00 | 🟢 1.00 | 🔴 0.00 |
| 9 | Trong quan điểm về xây dựng chủ nghĩa xã hội, Hồ Chí Minh đã xác định… | trả lời | trả lời | 🟢 1.00 | 🟢 0.81 | 🟢 1.00 | 🟢 1.00 | 🔴 0.50 |
| 10 | Theo tư tưởng Hồ Chí Minh, Nhà nước của nhân dân là một nhà nước như … | trả lời | trả lời | 🟢 1.00 | 🟢 0.90 | 🟢 1.00 | 🟢 1.00 | 🔴 0.00 |
| 11 | Vì sao Nguyễn Ái Quốc tin theo Lênin và Quốc tế Cộng sản? | trả lời | trả lời | 🟢 0.80 | 🟢 0.94 | 🟢 1.00 | 🟢 1.00 | 🔴 0.00 |
| 12 | Tư tưởng Hồ Chí Minh có vị trí như thế nào đối với Đảng và cách mạng … | trả lời | trả lời | 🟢 0.88 | 🟢 0.93 | 🟢 1.00 | 🟢 1.00 | 🔴 0.00 |
| 13 | Gián có hại không? | từ chối | từ chối | — | — | — | — | — |
| 14 | Làm thế nào để cả đội bóng đoàn kết hơn? | từ chối | thay thế (4a) | — | — | — | — | — |
| 15 | Hồ Chí Minh thích ăn món gì nhất? | từ chối | từ chối | — | — | — | — | — |
| 16 | Viết giúp tôi một hàm Python sắp xếp danh sách | từ chối | từ chối | — | — | — | — | — |

</details>

<a id="20261006-105726"></a>

### 06/10/2026 10:57 · chạy đầy đủ

- **File:** `data/eval_runs/20261006-105726.json`
- **Model trả lời:** `gemini-3.8-flash` · **giám khảo:** `gemini-3.8-flash` ⚠️ trùng model trả lời (tự chấm mình)
- **Retrieval:** CANDIDATES=10 · TOP_N=4 · MIN_TOP=0.9 · MIN_COS=0.45 · PARTIAL_BAND=0.5 · PARTIAL_COS=0.4
- **Kết quả:** 3 câu — 3 trả lời
- ℹ️ Đoạn giáo trình đưa giám khảo **chưa có nhãn trang** (câu trả lời chạy trước 14/10 / chấm lại từ file đó): faithfulness thấp hơn thật vì trích dẫn [Chương X, trang Y] bị tính là ý không kiểm được.

| Chỉ số | Trung bình | n | Thấp nhất |
|:--|--:|--:|--:|
| Bám tài liệu (`faithfulness`) | 🟢 0.97 | 3 | 🟢 0.91 |
| Đúng câu hỏi (`answer_relevancy`) | 🟢 0.93 | 3 | 🟢 0.89 |
| Đoạn liên quan (`context_relevance`) | 🟢 1.00 | 3 | 🟢 1.00 |

<details><summary>Điểm từng câu</summary>

| # | Câu hỏi | Mong đợi | Kết quả | Bám tài liệu | Đúng câu hỏi | Đoạn liên quan |
|--:|:--|:-:|:-:|:-:|:-:|:-:|
| 1 | Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc? | trả lời | trả lời | 🟢 0.91 | 🟢 0.89 | 🟢 1.00 |
| 2 | Thực chất của vấn đề dân tộc thuộc địa theo Hồ Chí Minh là gì? | trả lời | trả lời | 🟢 1.00 | 🟢 0.95 | 🟢 1.00 |
| 3 | Hồ Chí Minh quan niệm thế nào về độc lập dân tộc gắn liền với chủ ngh… | trả lời | trả lời | 🟢 1.00 | 🟢 0.93 | 🟢 1.00 |

</details>
