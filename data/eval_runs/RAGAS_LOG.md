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
| Bỏ sót | câu giáo trình (`expect: answer`) mà chatbot không trả lời |
| Lọt | câu lạc đề (`expect: refuse`) mà chatbot lại trả lời |

## Tổng quan các lần chạy

Mới nhất ở trên. Bấm vào ngày để xem chi tiết.

| Lần chạy | Kiểu | Câu | Trả lời · từ chối · thay thế · lỗi | Bỏ sót | Lọt | Bám tài liệu | Có căn cứ | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Đúng đáp án |
|:--|:--|--:|--:|--:|--:|:-:|:-:|:-:|:-:|:-:|:-:|
| [21/10/2026 08:25](#20261021-082504) | chạy đầy đủ | 1 | 1 · 0 · 0 · 0 | 0 | 0 | — | 🟢 1.00 | 🟢 0.90 | 🟢 1.00 | — | 🟡 0.75 |
| [19/10/2026 11:25](#20261019-112548) | chạy đầy đủ | 20 | 14 · 6 · 0 · 0 | 3 | 0 | 🟢 0.93 | 🟢 0.98 | 🟢 0.87 | 🟢 1.00 | — | 🟢 0.88 |

## Chi tiết từng lần chạy

<a id="20261021-082504"></a>

### 21/10/2026 08:25 · chạy đầy đủ

- **File:** `data/eval_runs/20261021-082504.json`
- **Model trả lời:** `gemini-3.6-flash` · **giám khảo:** `deepseek-v4-pro`
- **Retrieval:** CANDIDATES=10 · TOP_N=4 · MIN_TOP=0.9 · MIN_COS=0.45 · PARTIAL_BAND=0.5 · PARTIAL_COS=0.4 · ATTACH_PREVIOUS=False
- **Kết quả:** 1 câu — 1 trả lời

| Chỉ số | Trung bình | n | Thấp nhất |
|:--|--:|--:|--:|
| Có căn cứ (`response_groundedness`) | 🟢 1.00 | 1 | 🟢 1.00 |
| Đúng câu hỏi (`answer_relevancy`) | 🟢 0.90 | 1 | 🟢 0.90 |
| Đoạn liên quan (`context_relevance`) | 🟢 1.00 | 1 | 🟢 1.00 |
| Đúng đáp án (`answer_accuracy`) | 🟡 0.75 | 1 | 🟡 0.75 |

<details><summary>Điểm từng câu</summary>

| # | Câu hỏi | Mong đợi | Kết quả | Bám tài liệu | Có căn cứ | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Đúng đáp án |
|--:|:--|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | Hồ Chí Minh sinh ngày, tháng, năm nào và trong gia đình như thế nào? | trả lời | trả lời | lỗi | 🟢 1.00 | 🟢 0.90 | 🟢 1.00 | lỗi | 🟡 0.75 |

**Ô “lỗi”** — giám khảo không chấm được (chấm lại bằng `--rescore` khi mạng / quota ổn):
- Câu 1 · `faithfulness`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 1 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…

</details>

<a id="20261019-112548"></a>

### 19/10/2026 11:25 · chạy đầy đủ

- **File:** `data/eval_runs/20261019-112548.json`
- **Model trả lời:** `gemini-3.6-flash` · **giám khảo:** `deepseek-v4-pro`
- **Retrieval:** CANDIDATES=10 · TOP_N=4 · MIN_TOP=0.9 · MIN_COS=0.45 · PARTIAL_BAND=0.5 · PARTIAL_COS=0.4 · ATTACH_PREVIOUS=False
- **Kết quả:** 20 câu — 14 trả lời, 6 từ chối

**⚠️ Bỏ sót** (câu giáo trình nhưng không trả lời): 3
- `refused/input-guard` Văn kiện nào của Lênin mà Nguyễn Ái Quốc đọc năm 1920 đã giúp Người tìm thấy con đường giải phóng d…
- `refused/input-guard` Hội nghị Ban Chấp hành Trung ương lâm thời tháng 10-1930 đã phê phán Chánh cương vắn tắt, Sách lược…
- `refused/None` So với Lênin, Hồ Chí Minh bổ sung thêm yếu tố nào vào sự ra đời của Đảng Cộng sản Việt Nam, và yếu …

**🚨 Lọt** (câu lạc đề nhưng được trả lời): 0

| Chỉ số | Trung bình | n | Thấp nhất |
|:--|--:|--:|--:|
| Bám tài liệu (`faithfulness`) | 🟢 0.93 | 10 | 🔴 0.27 |
| Có căn cứ (`response_groundedness`) | 🟢 0.98 | 14 | 🟡 0.75 |
| Đúng câu hỏi (`answer_relevancy`) | 🟢 0.87 | 14 | 🔴 0.54 |
| Đoạn liên quan (`context_relevance`) | 🟢 1.00 | 14 | 🟢 1.00 |
| Đúng đáp án (`answer_accuracy`) | 🟢 0.88 | 14 | 🔴 0.50 |

<details><summary>Điểm từng câu</summary>

| # | Câu hỏi | Mong đợi | Kết quả | Bám tài liệu | Có căn cứ | Đúng câu hỏi | Đoạn liên quan | Đoạn đủ ý | Đúng đáp án |
|--:|:--|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | Hồ Chí Minh sinh ngày, tháng, năm nào và trong gia đình như thế nào? | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.81 | 🟢 1.00 | lỗi | 🟡 0.75 |
| 2 | Năm nào Nguyễn Ái Quốc gửi Bản yêu sách của nhân dân An Nam tới hội n… | trả lời | trả lời | lỗi | 🟢 1.00 | 🟢 0.97 | 🟢 1.00 | lỗi | 🟡 0.75 |
| 3 | Theo Hiến pháp năm 1946, những việc quan hệ đến vận mệnh quốc gia có … | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.87 | 🟢 1.00 | lỗi | 🟢 1.00 |
| 4 | Có phải Nguyễn Tất Thành từ chối con đường Đông du vì đã hiểu rõ bản … | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.98 | 🟢 1.00 | lỗi | 🟢 1.00 |
| 5 | Cho tôi công thức nấu phở bò ngon chuẩn vị Hà Nội. | từ chối | từ chối | — | — | — | — | — | — |
| 6 | Năm 1911, Nguyễn Tất Thành rời Bến Nhà Rồng trên con tàu tên là gì? | từ chối | từ chối | — | — | — | — | — | — |
| 7 | Trong tác phẩm mà Nguyễn Ái Quốc khẳng định 'cách mệnh trước hết phải… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🔴 0.54 | 🟢 1.00 | lỗi | 🟢 1.00 |
| 8 | Nguyễn Ái Quốc trở về Tổ quốc vào ngày nào sau bao nhiêu năm hoạt độn… | trả lời | trả lời | 🟢 1.00 | 🟡 0.75 | 🟢 0.84 | 🟢 1.00 | lỗi | 🟢 1.00 |
| 9 | Có đúng là trong kháng chiến, Hồ Chí Minh chủ trương 'vừa đánh vừa đà… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.92 | 🟢 1.00 | lỗi | 🟢 1.00 |
| 10 | Tóm tắt Năm điểm lớn mà Hồ Chí Minh đưa ra để định hướng xây dựng nền… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.94 | 🟢 1.00 | lỗi | 🟢 1.00 |
| 11 | Tóm tắt nội dung các chuẩn mực Cần, Kiệm, Liêm, Chính theo tư tưởng H… | trả lời | trả lời | lỗi | 🟢 1.00 | 🟢 0.98 | 🟢 1.00 | lỗi | 🔴 0.50 |
| 12 | Theo Hồ Chí Minh, bản chất giai cấp công nhân của Đảng có phải chỉ do… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.88 | 🟢 1.00 | lỗi | 🟢 1.00 |
| 13 | Theo Hồ Chí Minh, nhân dân ta đã đoàn kết trong những mặt trận nào và… | trả lời | trả lời | 🔴 0.27 | 🟢 1.00 | 🟢 0.93 | 🟢 1.00 | lỗi | 🟢 1.00 |
| 14 | Văn kiện nào của Lênin mà Nguyễn Ái Quốc đọc năm 1920 đã giúp Người t… | trả lời | ⚠️ từ chối | — | — | — | — | — | — |
| 15 | Hội nghị Ban Chấp hành Trung ương lâm thời tháng 10-1930 đã phê phán … | trả lời | ⚠️ từ chối | — | — | — | — | — | — |
| 16 | Tại Đại hội nào Đảng ra hoạt động công khai với tên Đảng Lao động Việ… | trả lời | trả lời | 🟢 1.00 | 🟢 1.00 | 🟢 0.84 | 🟢 1.00 | lỗi | 🟡 0.75 |
| 17 | So với Lênin, Hồ Chí Minh bổ sung thêm yếu tố nào vào sự ra đời của Đ… | trả lời | ⚠️ từ chối | — | — | — | — | — | — |
| 18 | Tổng hợp quan điểm của Hồ Chí Minh về mối quan hệ giữa đức và tài, gi… | trả lời | trả lời | lỗi | 🟢 1.00 | 🟢 0.93 | 🟢 1.00 | lỗi | 🟢 1.00 |
| 19 | Tóm tắt đặc điểm lớn nhất, nhiệm vụ lịch sử và lý do tính chất khó kh… | trả lời | trả lời | lỗi | 🟢 1.00 | 🟢 0.83 | 🟢 1.00 | lỗi | 🔴 0.50 |
| 20 | Theo giáo trình, Hồ Chí Minh đã đưa ra những chỉ dẫn cụ thể nào về ph… | từ chối | từ chối | — | — | — | — | — | — |

**Ô “lỗi”** — giám khảo không chấm được (chấm lại bằng `--rescore` khi mạng / quota ổn):
- Câu 1 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 2 · `faithfulness`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 2 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 3 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 4 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 7 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 8 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 9 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 10 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 11 · `faithfulness`: InstructorRetryException: Connection error.
- Câu 11 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 12 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 13 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 16 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 18 · `faithfulness`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 18 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 19 · `faithfulness`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…
- Câu 19 · `context_recall`: InstructorRetryException: <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transition…

</details>
