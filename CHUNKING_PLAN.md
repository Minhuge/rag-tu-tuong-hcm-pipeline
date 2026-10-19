# Kế hoạch chunking v2: Hierarchical → Size check → Semantic → Recursive

Tài liệu này mô tả:

1. Cấu trúc pipeline chunking mới, thay cho `RecursiveCharacterTextSplitter(800, 100)` hiện tại.
2. Thí nghiệm chọn `max_size`: thống kê độ dài mục, chọn giá trị, ingest vào các collection riêng, hiệu chỉnh và
   đánh giá để chọn bản tốt nhất.

## Trạng thái hiện tại

| Hạng mục | Trạng thái |
|---|---|
| A. `load_full_text` + `PageMap` (`rag/chunking.py`) | ✅ Xong (PyMuPDF) |
| B. `detect_sections` | ✅ Xong, đã đối chiếu mục lục: **118 mục** |
| Thống kê độ dài mục | ✅ Xong (mục 4.2) |
| Chốt giá trị thí nghiệm | ✅ 800 / 1200 / 1600 + bản đối chứng `1200_nosem` |
| Kiểm tra phần chữ nằm ngoài mục (gap check) | ⏳ Cần chạy (mục 2.B.4) |
| Bước 0.5 + 1–5 (`chunk_section`) | ⏳ Chưa làm |
| Tham số mới cho `scripts/ingest.py` | ⏳ Chưa làm |
| Baseline eval, ingest, calibrate, eval, so sánh | ⏳ Chưa làm |

---

## 0. Tổng quan

```
PDF
 │ A. PyMuPDF theo trang → clean_page (giữ nguyên dòng) → ghép toàn văn + PageMap (offset → số trang)
 │ B. Nhận diện tiêu đề → danh sách Section (Chương > I > 1 > a), mỗi Section là đoạn liên tục giữa 2 tiêu đề
 │
 │   0.5 Gộp lời dẫn ngắn  mục lời dẫn < min_size → gộp vào mục con đầu tiên ngay sau nó
 │
 │ Với mỗi mục:
 │   1. Hierarchical   ranh giới cứng = tiêu đề giáo trình
 │   2. Size check     len ≤ max_size → giữ nguyên 1 chunk        (chỉ ~20–35% mục, xem 4.2)
 │   3. Semantic       mục quá dài → cắt ở chỗ đổi ý               (BỘ CẮT CHÍNH với giáo trình này)
 │   4. Recursive      mảnh nào vẫn > max_size → cắt cứng          (chốt chặn)
 │   5. Gộp mảnh nhỏ   mảnh < min_size → gộp vào mảnh liền trước, CÙNG mục
 │
 │ C. Tính page_start / page_end từ offset, tạo payload + embed_text (có đường dẫn mục ở đầu)
 ▼
Qdrant collection (mỗi cấu hình một collection)
```

Nguyên tắc: **chunk không bao giờ vắt qua hai nhánh mục khác nhau**. Mọi bước cắt chỉ diễn ra bên trong một mục.
Ngoại lệ duy nhất là bước 0.5: nó gộp lời dẫn cha với mục con đầu tiên, nên vẫn cùng một nhánh.

### `max_size` và `min_size` làm gì

Hai giá trị này là trần và sàn cho độ dài mọi chunk, **tính bằng ký tự** (`len(text)`):

```
min_size ≤ độ dài chunk ≤ max_size
```

| | Vai trò | Quá thấp | Quá cao |
|---|---|---|---|
| `max_size` | Chunk không dài hơn mức này; mục dài hơn phải cắt | Chunk vụn, mất ngữ cảnh, một ý bị cắt thành nhiều mảnh | Chunk chứa nhiều ý, vector bị "trung bình hoá", prompt dài |
| `min_size` | Mảnh ngắn hơn mức này được gộp vào mảnh bên cạnh | Còn sót mảnh 1–2 câu nghèo nghĩa | Gộp quá tay |

Mặc định `min_size = max_size // 4`.

**Độ mịn** là lượng nội dung trong mỗi chunk. Ví dụ, mục "b. Những tiền đề tư tưởng – lý luận" dài 7004 ký tự sẽ
cho khoảng 9–10 chunk ở `max_size` 800, nhưng chỉ khoảng 4–5 chunk ở 1600. Chunk mịn khớp chính xác với câu hỏi
hẹp nhưng ít ngữ cảnh; chunk thô thì ngược lại.

---

## 1. Cấu trúc module

Lõi chunking đặt trong `rag/` (không import `app/`), để ingest, thống kê và test dùng chung:

```
rag/chunking.py          # PageMap, load_full_text, detect_sections (xong)
                         # merge_short_intros, chunk_section, build_chunks (chưa làm)
                         # __main__: in path_str từng mục + bảng phân vị + gap check
scripts/ingest.py        # thêm --max-size, --min-size, --strategy, --semantic-pct, --collection
tests/test_chunking.py   # test bất biến
```

Không cần `scripts/chunk_stats.py` riêng: phần thống kê nằm luôn trong `python -m rag.chunking`.

---

## 2. Chi tiết từng bước

### A. Ghép toàn văn và bản đồ trang (đã làm)

- Đọc PDF bằng **PyMuPDF** (`page.get_text(sort=True)`), không dùng PyPDFLoader. Lý do: pypdf ở chế độ mặc
  định chèn `\n` giữa từng glyph, nên không còn biết đâu là xuống dòng thật. Còn chế độ layout của pypdf trả về
  trang trống ở khoảng 40/119 trang. Nhận diện tiêu đề cần dòng thật.
- `clean_page`: chuẩn hoá NFC, cắt **chuỗi** watermark (không bỏ cả dòng, vì PyMuPDF đôi khi dính watermark vào
  dòng chữ thật như `KẾT LUẬNwww.onthisv.com`), bỏ dòng chỉ chứa số trang, gộp khoảng trắng trong dòng, bỏ dòng
  trống. **Giữ nguyên các dòng.** Không dùng `clean_text` của `ingest.py`, vì hàm đó xoá hết `\n`.
- Trang trống sau khi làm sạch thì bỏ qua.
- `PageMap.page_at(offset)` dùng `bisect`. Số trang = chỉ số PDF + 1, **giống field `page` cũ**, nên trích dẫn
  không bị lệch.

### B. Nhận diện tiêu đề (đã làm)

#### B.1 Regex theo cấp

| Cấp | Regex | Ví dụ | Ghi chú |
|---|---|---|---|
| 0: Chương | `^CH[UƯ]ƠNG\s+(MỞ\s*ĐẦU\|[IVX]+)\b…` | `CHƯƠNG III` | **Phân biệt hoa/thường**, để câu "như chương II đã trình bày" không khớp. Tên chương nằm ở các dòng viết hoa kế tiếp |
| 1: Mục lớn | `^([IVX]+)\s*\.\s*(\S.*)$` | `I. CƠ SỞ…`, `I.TƯ TƯỞNG…` | Chấp nhận thiếu dấu cách sau dấu chấm |
| 1: Kết luận | `^KẾT LUẬN(?: CHƯƠNG\b.*)?$` | `KẾT LUẬN` | Cùng cấp với mục La Mã, vì bên dưới có các mục `1.`, `2.` riêng |
| 2: Mục | `^(\d{1,2})\s*\.\s*(\S.*)$` | `1. Cơ sở khách quan` | Tối đa 2 chữ số, để `1930. Đây chính là…` không khớp |
| 3: Mục nhỏ | `^([a-zđ])\s*[.)]\s*(\S.*)$` | `a. Bối cảnh lịch sử` | Giáo trình dùng `a.`, nhưng nhận cả `a)` |

#### B.2 Bộ lọc chống nhận nhầm (`_valid_heading`)

- Dòng ≤ 150 ký tự. Tiêu đề không kết thúc bằng `. ; , :` (bỏ qua dấu ngoặc kép hoặc ngoặc đơn ở cuối).
- Chữ đầu tiên của tiêu đề phải viết hoa.
- **Đánh số liên tiếp:** cấp 1 và 2 phải tăng đúng 1 (`I → II`, `1 → 2`). Cấp 3 tăng 1 hoặc 2, vì giáo trình có
  chỗ nhảy `d → e` (bỏ `đ`). Mục mới ở cấp cao hơn thì reset đánh số cho các cấp con.
- **Không nhảy cấp** (đang ở cấp 1 thì không xuống thẳng cấp 3), trừ khi đó là mục đầu tiên của danh sách. Ví dụ
  `a.` nằm ngay dưới `KẾT LUẬN` ở chương VI, nơi giáo trình bỏ cấp `1.`.

#### B.3 Tiêu đề nhiều dòng

- Cấp 0–1 (viết hoa): nối các dòng viết hoa kế tiếp không phải tiêu đề, tổng ≤ 150 ký tự. Tên chương có thể vắt
  sang trang sau.
- Cấp 2–3: nếu dòng tiêu đề dài ≥ 70 ký tự (gần hết khổ) và dòng sau bắt đầu bằng chữ thường, thì dòng sau là
  phần còn lại của tiêu đề. Đoạn văn thật sau tiêu đề luôn bắt đầu bằng chữ hoa.

#### B.4 Section, và các ký tự không thuộc mục nào

```python
@dataclass
class Section:
    path: list[str]          # ["Chương II: …", "I. TƯ TƯỞNG …", "1. Vấn đề dân tộc thuộc địa"]
    chapter: str             # "Chương II" / "Chương Mở đầu"; cùng dạng field "chapter" cũ
    chapter_no: int | None   # 0 = Mở đầu, None = trước chương đầu tiên
    start: int               # offset trong full_text (sau dòng tiêu đề)
    end: int
    text: str                # == full_text[start:end]
```

- `text` = `full_text[body_start : start của tiêu đề kế tiếp]`, đã strip khoảng trắng hai đầu.
- Lời dẫn giữa tiêu đề cha và tiêu đề con đầu tiên là một `Section` riêng, với `path` dừng ở cấp cha.
- Mục rỗng (tiêu đề nối tiếp tiêu đề, hoặc chỉ có khoảng trắng) bị bỏ qua.
- Phần chữ trước tiêu đề đầu tiên (nếu có) thành `Section` với `path` rỗng và `chapter = "Chưa xác định"`.

**Vì sao tổng ký tự trong mục (356.008) nhỏ hơn toàn văn (365.457), chênh khoảng 9.450 ký tự:** có ba loại ký tự
trong `full_text` không nằm trong `text` của mục nào.

1. **Các dòng tiêu đề.** Đây là phần lớn. Nội dung tiêu đề không mất, vì nó được đưa vào `path`. Số dòng tiêu đề
   **nhiều hơn 118**, vì tiêu đề của các mục rỗng (ví dụ `CHƯƠNG I` → `I.` → `1.` → `a.` nằm sát nhau) cũng
   nằm ngoài mục.
2. **`\n` và khoảng trắng ở rìa mục**, bị `lstrip` / `rstrip` cắt đi.
3. **Mục rỗng chỉ có khoảng trắng.**

**Gap check (cần chạy):** in đúng những đoạn có chữ nằm ngoài mọi mục. Mọi dòng in ra phải là tiêu đề. Nếu có câu
văn bình thường lọt ra, nghĩa là có dòng văn bị nhận nhầm là tiêu đề, hoặc phần nối tiêu đề nhiều dòng đã nuốt mất
một dòng văn. Khi đó sửa B.2 hoặc B.3.

```python
covered = bytearray(len(full_text))
for s in sections:
    covered[s.start:s.end] = b"\x01" * (s.end - s.start)

gaps, i = [], 0
while i < len(full_text):
    if not covered[i]:
        j = i
        while j < len(full_text) and not covered[j]:
            j += 1
        gaps.append(full_text[i:j]); i = j
    else:
        i += 1

non_ws = [g.strip() for g in gaps if g.strip()]
print(f"Ký tự ngoài mục: {sum(map(len, gaps)):,}  "
      f"(khoảng trắng/\\n: {sum(len(g) - len(g.strip()) for g in gaps):,})")
print(f"Số đoạn có chữ nằm ngoài mục: {len(non_ws)}")
for g in non_ws:
    print("  |", g.replace("\n", " ⏎ ")[:150])
```

### 0.5. Gộp lời dẫn ngắn

Thống kê cho thấy có vài mục lời dẫn rất ngắn, ví dụ `II. PHƯƠNG PHÁP NGHIÊN CỨU` chỉ có 168 ký tự và một mục khác
145 ký tự. Để riêng thì chúng thành chunk vụn, vector nhiễu.

**Luật:** nếu một Section ngắn hơn `min_size`, **và** Section liền sau là mục con của nó (`next.path` bắt đầu bằng
`sec.path` và dài hơn), thì gộp hai mục lại:

- `start = sec.start`, `end = next.end`, `text = full_text[start:end]`. Đoạn gộp chứa luôn dòng tiêu đề của mục
  con nằm giữa, điều này tự nhiên và giữ đúng offset.
- `path = next.path` (đường dẫn của mục con), `chapter` và `chapter_no` lấy theo mục con.
- Nếu Section liền sau không phải mục con (lời dẫn ngắn đứng cuối nhánh), thì giữ nguyên.

Luật này chạy **trước** bước 1–5 và phụ thuộc `min_size`, nên được tính lại cho từng cấu hình.

### 1–5. Cắt một mục

```python
@dataclass
class Piece:
    text: str
    start: int   # offset trong full_text
    end: int
    method: str  # "whole_section" | "semantic" | "recursive"

def chunk_section(sec: Section, max_size: int, min_size: int, semantic_thr: float | None,
                  sent_cache=None) -> list[Piece]:
    # 2. Size check
    if len(sec.text) <= max_size:
        return [Piece(sec.text, sec.start, sec.end, "whole_section")]

    # 3. Semantic (strategy="recursive" thì semantic_thr=None, bỏ qua bước này)
    pieces = semantic_split(sec, max_size, min_size, semantic_thr, sent_cache) \
             if semantic_thr is not None else [Piece(sec.text, sec.start, sec.end, "recursive")]

    # 4. Recursive: chốt chặn cho mảnh vẫn quá dài
    out = []
    for p in pieces:
        out.extend(recursive_split(p, max_size) if len(p.text) > max_size else [p])

    # 5. Gộp mảnh nhỏ trong cùng mục
    return merge_small(out, min_size, max_size)
```

#### Bước 3: Semantic split

Với giáo trình này, 72–80% số mục phải đi qua bước này (xem 4.2), nên đây là bộ cắt chính.

1. **Tách câu** bằng `underthesea.sent_tokenize`. Giữ offset từng câu, tìm bằng `sec.text.find(sent, cursor)`
   rồi tiến `cursor` dần. Vì `text` giữ nguyên dòng, nên trước khi tách câu cần thay `\n` giữa câu bằng dấu cách
   (một dòng PDF không phải một câu). Lưu ý phép thay này không đổi độ dài chuỗi, nhờ vậy offset vẫn đúng.
2. **Cửa sổ câu:** ghép mỗi câu với 1 câu trước và 1 câu sau (buffer = 1) rồi mới embed.
3. **Embed** bằng `qwen3-embedding:4b` qua Ollama (batch 32), chuẩn hoá L2.
4. **Khoảng cách** giữa câu i và i+1: `d_i = 1 - cos(v_i, v_{i+1})`.
5. **Ngưỡng toàn cục:** `semantic_thr = percentile(tất cả d_i của mọi mục dài, semantic_pct)`, mặc định 90.
   Tính một lần cho cả giáo trình, không tính riêng từng mục.
6. **Cắt có ràng buộc:** đóng mảnh khi `d_i ≥ semantic_thr` **và** mảnh ≥ `min_size`, hoặc khi thêm câu kế tiếp
   sẽ làm mảnh vượt `max_size`.

```python
def split_by_distance(sents, offs, dist, thr, min_size, max_size):
    pieces, cur, cur_start = [], [], offs[0]
    for i, s in enumerate(sents):
        cur.append(s)
        size = offs[i] + len(s) - cur_start
        last = i == len(sents) - 1
        if last or (dist[i] >= thr and size >= min_size) or offs[i + 1] + len(sents[i + 1]) - cur_start > max_size:
            pieces.append(Piece(..., cur_start, offs[i] + len(s), "semantic"))   # text = full_text[start:end]
            if not last:
                cur, cur_start = [], offs[i + 1]
    return pieces
```

Lấy `text` của mảnh bằng `full_text[start:end]` thay vì `" ".join(cur)`, để payload giữ đúng văn bản gốc và
`test_coverage` kiểm được.

**Hai lượt:** lượt 1 embed câu của mọi mục dài, gom khoảng cách và tính `semantic_thr`; lượt 2 cắt. Embedding câu
lưu trong `sent_cache` (theo mục). Câu không phụ thuộc `max_size`, nên có thể ghi cache ra đĩa
(`data/chunk_runs/sent_cache.npz`), và 3 collection semantic chỉ phải embed câu một lần.

Không dùng overlap ở bước này.

#### Bước 4: Recursive (chốt chặn)

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

def recursive_split(p: Piece, max_size: int) -> list[Piece]:
    sp = RecursiveCharacterTextSplitter(chunk_size=max_size, chunk_overlap=max_size // 8,
                                        separators=["\n\n", "\n", ". ", " ", ""])
    out, cursor = [], 0
    for t in sp.split_text(p.text):
        i = p.text.find(t, cursor); i = cursor if i < 0 else i
        out.append(Piece(t, p.start + i, p.start + i + len(t), "recursive"))
        cursor = i + 1
    return out
```

Overlap khoảng 1/8 `max_size` chỉ áp dụng ở bước này. Ở bản đối chứng `nosem`, đây là bộ cắt duy nhất cho mục dài.

#### Bước 5: Gộp mảnh nhỏ

Mảnh `< min_size` gộp vào mảnh **liền trước trong cùng mục**, nếu tổng không vượt `max_size`. Mảnh đầu mục thì gộp
vào mảnh sau. Mục ngắn hơn `min_size` mà không gộp được ở bước 0.5 thì giữ nguyên làm một chunk.

### C. Payload và embed_text

```python
{
    "chunk_id": f"{collection}-{n:04d}",
    "source": "Giao_trinh_Tu_tuong_HCM.pdf",
    "chapter": sec.chapter,
    "chapter_no": sec.chapter_no,
    "section": sec.path_str,                     # mới
    "page": page_map.page_at(piece.start),       # giữ để code cũ không vỡ
    "page_start": page_map.page_at(piece.start), # mới
    "page_end": page_map.page_at(piece.end - 1), # mới
    "chunk_method": piece.method,                # mới: whole_section | semantic | recursive
    "text": piece.text,                          # hiển thị + đưa vào prompt
}
```

```python
embed_text = f"[{sec.path_str}]\n{piece.text}"   # chỉ để embed; payload "text" giữ nguyên văn gốc
```

`path_str` có thể dài 150–300 ký tự, vì tên chương viết hoa rất dài. Nếu cần, rút gọn phần chương thành
`"Chương II"` trong `embed_text`.

Payload index: thêm `section` bên cạnh `chapter` và `chapter_no`.

---

## 3. Giới hạn cứng cần kiểm tra

| Giới hạn | Kiểm tra | Nếu vượt |
|---|---|---|
| `EMBED_NUM_CTX` (Ollama) | Token của `embed_text` dài nhất (chunk + đường dẫn mục) | Ollama cắt ngầm phần cuối |
| `max_length` của reranker | Token của câu hỏi + chunk + template | Reranker chấm trên chunk bị cắt |

```python
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("Qwen/Qwen3-Embedding-4B")
n_tokens = len(tok(embed_text)["input_ids"])
```

Tham khảo: 1600 ký tự tiếng Việt vào khoảng 450–550 token. `ingest.py` in token lớn nhất và cảnh báo nếu vượt.

---

## 4. Thí nghiệm chọn max_size

### 4.1 Baseline

```bash
QDRANT_COLLECTION=tu_tuong_hcm python -m scripts.eval_ragas      # lưu vào data/eval_runs/
```

Chạy **trước** khi sửa bất kỳ phần nào của pipeline truy vấn.

### 4.2 Thống kê độ dài mục (đã chạy)

`python -m rag.chunking data/Giao_trinh_Tu_tuong_HCM.pdf`

**Kết quả:** 118 mục, tổng 356.008 ký tự trong mục, toàn văn 365.457 ký tự.

| Phân vị | Độ dài mục (ký tự) |
|---|---|
| p10 | 709 |
| p25 | 1.111 |
| p50 | 2.621 |
| p75 | 4.067 |
| p90 | 6.420 |
| p95 | 7.275 |
| max | 11.240 ("…một số nguyên tắc cơ bản về xây dựng và hoạt động của mặt trận dân tộc thống nhất") |

| `max_size` | % mục giữ nguyên | Số mục phải cắt |
|---|---|---|
| 600 | 8% | 108 |
| 800 | 13% | 103 |
| 1000 | 20% | 94 |
| 1200 | 28% | 85 |
| 1500 | 32% | 80 |
| 1800 | 36% | 75 |
| 2000 | 41% | 70 |

"Số mục phải cắt" là số mục dài hơn `max_size`, phải đi tiếp xuống bước semantic hoặc recursive. Hai cột cộng lại
luôn bằng 118.

**Nhận xét:**

1. **Mục của giáo trình dài hơn dự kiến nhiều** (median 2.621). Ngay cả ở 2.000 cũng chỉ 41% mục giữ nguyên.
   Muốn khoảng 70% mục giữ nguyên thì `max_size` phải hơn 4.000, quá thô cho retrieval: một vector gánh nhiều ý,
   và top 4 vào khoảng 16.000 ký tự trong prompt.
2. Vì vậy **bỏ quy tắc "chọn `max_size` để 60–80% mục giữ nguyên"** của bản kế hoạch trước. `max_size` được chọn
   theo **độ mịn mong muốn của chunk**. Hierarchical chỉ cung cấp ranh giới cứng và `section_path`; semantic là
   bộ cắt chính.
3. Semantic chắc chắn chạy trên phần lớn mục, nên **bản đối chứng `nosem` là bắt buộc**. Đó là cách duy nhất để
   biết semantic có tốt hơn recursive bên trong mục hay không.
4. Mục quá ngắn hiếm (p10 = 709), nhưng vẫn có vài lời dẫn 145–242 ký tự, nên cần bước 0.5.

### 4.3 Giá trị đã chốt

| Collection | `max_size` | `min_size` | Strategy | Số chunk ước tính* | Mục đích |
|---|---|---|---|---|---|
| `tu_tuong_hcm_ms800` | 800 | 200 | hier_semantic | ~550–650 | Cùng cỡ với baseline, chỉ khác **cách cắt** |
| `tu_tuong_hcm_ms1200` | 1200 | 300 | hier_semantic | ~380–450 | Ứng viên chính |
| `tu_tuong_hcm_ms1600` | 1600 | 400 | hier_semantic | ~280–340 | Chunk to hơn có còn giúp ích không |
| `tu_tuong_hcm_ms1200_nosem` | 1200 | 300 | recursive | ~380–450 | Đo phần đóng góp riêng của semantic |

\*Ước lượng thô theo 356.000 ký tự, giả định chunk trung bình bằng 65–75% `max_size`. Số thật lấy từ
`data/chunk_runs/<collection>.json`.

**Vì sao là 800 / 1200 / 1600.** Ba số này không tính ra từ công thức. Đây là 3 điểm lấy mẫu trong một khoảng hợp
lý; eval mới là thứ quyết định.

- **800 (đáy):** bằng baseline 800/100, nên so sánh được công bằng. Độ dài chunk tương đương, chỉ khác cách cắt.
- **1600 (trần):** khoảng 450–550 token, top 4 khoảng 6.400 ký tự context. Vẫn trong khoảng thường dùng cho RAG
  văn bản, và xa giới hạn cứng ở mục 3.
- **1200 (giữa):** chia đều khoảng 800–1600. Bước nhảy 400 đủ thưa để khác biệt không lẫn vào nhiễu eval, đủ dày
  để thấy được xu hướng.

**Mở rộng sau khi có kết quả:**

- Recall vẫn tăng mạnh từ 1200 lên 1600: thử thêm 2000.
- 800 đã tốt nhất: thử thêm 600.
- Đỉnh ở 1200: tinh chỉnh bằng 1000 và 1400.

### 4.4 Ingest

```bash
python -m scripts.ingest --max-size 800  --collection tu_tuong_hcm_ms800
python -m scripts.ingest --max-size 1200 --collection tu_tuong_hcm_ms1200
python -m scripts.ingest --max-size 1600 --collection tu_tuong_hcm_ms1600
python -m scripts.ingest --max-size 1200 --strategy recursive --collection tu_tuong_hcm_ms1200_nosem
```

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--max-size` | 1200 | Độ dài tối đa của chunk (ký tự) |
| `--min-size` | `max_size // 4` | Ngưỡng gộp mảnh nhỏ và gộp lời dẫn (bước 0.5) |
| `--strategy` | `hier_semantic` | `hier_semantic` (bước 0.5 + 1–5) hoặc `recursive` (bỏ bước 3) |
| `--semantic-pct` | 90 | Phân vị khoảng cách để tính `semantic_thr` |
| `--collection` | `QDRANT_COLLECTION` | Tên collection đích |

Yêu cầu với `ingest.py`:

- **Không xoá collection gốc `tu_tuong_hcm`.** Chỉ tạo lại collection chỉ định bằng `--collection`.
- Dùng lại `sent_cache` cho 3 bản semantic, không phải embed câu lại.
- Ghi `data/chunk_runs/<collection>.json` gồm: tham số, `semantic_thr`, số chunk, phân vị độ dài chunk, số chunk
  theo `chunk_method`, số lời dẫn đã gộp, token lớn nhất.
- Cảnh báo nếu có `embed_text` vượt `EMBED_NUM_CTX`.

### 4.5 Hiệu chỉnh và đánh giá

Với **mỗi** collection, chạy hiệu chỉnh rồi mới đánh giá. Ngưỡng `retrieval_guard` (0.9/0.45, 0.5/0.40) phụ thuộc
vào phân bố cosine và P(yes), mà phân bố này đổi theo độ dài chunk.

```bash
for c in tu_tuong_hcm_ms800 tu_tuong_hcm_ms1200 tu_tuong_hcm_ms1600 tu_tuong_hcm_ms1200_nosem; do
  QDRANT_COLLECTION=$c python -m scripts.calibrate
  # đặt ngưỡng mới cho collection này (env hoặc file theo collection), rồi:
  QDRANT_COLLECTION=$c python -m scripts.eval_ragas
done
```

> Quota Gemini: mỗi lượt eval tốn nhiều lời gọi. Cache tại `.ragas_cache/` không giúp nhiều, vì câu trả lời khác
> nhau giữa các collection. Chạy cùng một bộ câu hỏi; có thể chia mỗi ngày một collection.

### 4.6 So sánh và chọn

| Chỉ số | baseline 800/100 | ms800 | ms1200 | ms1600 | ms1200_nosem |
|---|---|---|---|---|---|
| Số chunk | 585 | | | | |
| Độ dài chunk p50 / p95 | | | | | |
| % chunk semantic / recursive / whole | — | | | | |
| `context_recall` | | | | | |
| `context_relevance` | | | | | |
| `faithfulness` | | | | | |
| `answer_relevancy` | | | | | |
| `answer_accuracy` | | | | | |
| Từ chối đúng (%) | | | | | |
| Từ chối lọt (%) | | | | | |
| Trích dẫn sai (4c) | | | | | |
| Thời gian local / câu | | | | | |

Quy tắc chọn:

1. **Loại** bản nào làm *từ chối lọt* tăng so với baseline.
2. Chọn **`max_size` nhỏ nhất mà `context_recall` không còn tăng đáng kể** khi tăng tiếp (chênh dưới khoảng 0.02).
3. Ngang nhau thì chọn bản có `context_relevance` cao hơn.
4. So `ms1200` với `ms1200_nosem`: nếu ngang nhau thì bỏ semantic, vì pipeline đơn giản hơn và ingest nhanh hơn.
5. Đọc tay khoảng 20 chunk ngẫu nhiên của bản thắng, ưu tiên chunk `semantic`, để xem điểm cắt có hợp lý không.

Chỉ số "trích dẫn sai" trong lúc thí nghiệm có thể bị thổi phồng, vì 4c chưa hỗ trợ khoảng trang (mục 5).

### 4.7 Chuyển sang bản thắng

- Đặt `QDRANT_COLLECTION` sang bản thắng, cùng với ngưỡng đã hiệu chỉnh.
- Cập nhật README (lý do chọn `max_size`, lý do dùng PyMuPDF) và tài liệu kiến trúc (mục 3: luồng ingest).
- Giữ collection cũ thêm một thời gian để có thể quay lại.

---

## 5. Code phải sửa theo (do chunk có khoảng trang)

| Chỗ | Việc cần sửa |
|---|---|
| `rag/pipeline.py`: `build_context` | Ghi nguồn cho LLM kèm `section` và khoảng trang |
| Lớp 4c `check_citations` | Chấp nhận `[Chương X, trang Y]` nếu `page_start ≤ Y ≤ page_end` của một chunk đã truy xuất |
| `get_chapter_passages` (tạo đề) | Vẫn scroll theo `chapter`; `source_page` của đề AI phải nằm trong khoảng trang |
| `rag/exam_validation.py` tầng 1 | Kiểm tra "trang nguồn hợp lệ" theo khoảng trang |
| `web/src/components/Message.jsx` | Chip trích dẫn hiển thị khoảng trang và tên mục |

Những chỗ này chỉ cần sửa trước khi chuyển hẳn sang bản mới. Trong lúc thí nghiệm, field `page` (= `page_start`)
giữ cho code cũ chạy được.

---

## 6. Test (`tests/test_chunking.py`)

Test chạy trên văn bản giả nhỏ, có tiêu đề và vắt trang. Embedding câu được thay bằng hàm giả, nên không cần
Ollama.

| Test | Bất biến kiểm tra |
|---|---|
| `test_detect_sections_levels` | Nhận đúng các cấp; `KẾT LUẬN` là cấp 1; "như chương II đã trình bày" và "1930. Đây…" không bị nhận là tiêu đề |
| `test_numbering_consecutive` | `1.`, `3.` (thiếu `2.`) thì `3.` không được nhận; `d → e` được nhận |
| `test_multiline_heading` | Tên chương viết hoa vắt 2 dòng được nối; dòng văn viết hoa đầu câu không bị nuốt |
| `test_intro_text_kept` | Lời dẫn giữa tiêu đề cha và tiêu đề con đầu tiên thành một `Section` riêng |
| `test_gap_only_headings` | Mọi đoạn có chữ nằm ngoài các Section đều là dòng tiêu đề (gap check) |
| `test_intro_merge` | Lời dẫn < `min_size` được gộp vào mục con ngay sau, lấy `path` của mục con; lời dẫn đứng cuối nhánh thì giữ nguyên |
| `test_no_chunk_exceeds_max` | Mọi chunk có `len(text) <= max_size` |
| `test_no_cross_section` | Mỗi chunk nằm gọn trong `[sec.start, sec.end)` của đúng một mục (sau bước 0.5) |
| `test_coverage` | Mọi ký tự không phải khoảng trắng của mỗi mục có trong ít nhất một chunk |
| `test_text_matches_offsets` | `chunk.text == full_text[chunk.start:chunk.end]` |
| `test_short_section_whole` | Mục ≤ `max_size` cho ra đúng 1 chunk `whole_section` |
| `test_min_size_merge` | Không còn chunk `< min_size`, trừ khi cả mục ngắn hơn `min_size` |
| `test_page_range` | Chunk vắt 2 trang có `page_start < page_end`, đúng số trang |
| `test_semantic_cut_at_topic_shift` | Embedding giả cho hai cụm vector khác hẳn; điểm cắt rơi đúng ranh giới hai cụm |
| `test_nosem_strategy` | `strategy="recursive"` không gọi embedding câu, mọi chunk của mục dài là `recursive` |
| `test_embed_text_has_path` | `embed_text` bắt đầu bằng `[path_str]`; payload `text` không chứa tiền tố |
| `test_citation_page_range` (khi sửa 4c) | `[Chương X, trang Y]` hợp lệ khi Y nằm trong khoảng trang của chunk |

---

## 7. Thứ tự làm

1. ✅ `load_full_text`, `detect_sections`, đối chiếu mục lục.
2. ✅ Thống kê phân vị, chốt 800 / 1200 / 1600 + `1200_nosem`.
3. ⏳ Chạy gap check (2.B.4). Nếu có câu văn lọt ra thì sửa regex hoặc bộ lọc. Viết các test của phần B.
4. ⏳ Chạy baseline eval (4.1).
5. ⏳ `merge_short_intros` (0.5), `chunk_section` (1–5), `build_chunks` hai lượt kèm `sent_cache`, các test còn lại.
6. ⏳ Thêm tham số cho `scripts/ingest.py`, ingest 4 collection.
7. ⏳ `calibrate` + `eval_ragas` cho từng collection, điền bảng 4.6, chọn bản thắng.
8. ⏳ Sửa code ở mục 5, chuyển sang bản thắng, cập nhật README và docs.
