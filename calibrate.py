"""
Hiệu chỉnh ngưỡng retrieval guard (lớp 2): MIN_TOP / PARTIAL_BAND trong search_rerank.py.

Chạy 2 bộ câu hỏi qua /search của API đang chạy (không nạp model lần 2):
  IN_COURSE  — câu giáo trình CÓ trả lời → mong muốn pass
  OFF_TOPIC  — câu ngoài môn học         → mong muốn refuse
rồi in điểm từng câu và ngưỡng tách 2 nhóm tốt nhất cho:
  rerank    — P(yes) của chunk đứng đầu sau rerank (cái lớp 2 đang dùng)
  cos_top   — cosine của chính chunk đó
  cos_max   — cosine cao nhất trong 10 chunk Qdrant trả về
  rerank + cos_top — luật kết hợp: rerank ≥ a VÀ cos_top ≥ b

Chạy:  uvicorn api:app   (terminal khác)
       python calibrate.py                 # câu hỏi như đã viết
       python calibrate.py --variants      # thêm bản viết thường, bỏ dấu "?" để xem điểm dao động
       python calibrate.py --csv out.csv   # lưu điểm thô
"""
import argparse
import csv
import json
import urllib.request

API = "http://localhost:8000"

# Câu có trong giáo trình (chương II–VII). Thêm câu thật người dùng hỏi vào đây càng nhiều càng tốt.
IN_COURSE = [
    "Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?",
    "Vai trò của đại đoàn kết dân tộc trong sự nghiệp cách mạng là gì?",
    "Thực chất của vấn đề dân tộc thuộc địa theo Hồ Chí Minh là gì?",
    "Hồ Chí Minh quan niệm thế nào về độc lập dân tộc gắn liền với chủ nghĩa xã hội?",
    "Theo Hồ Chí Minh, đặc trưng bản chất của chủ nghĩa xã hội là gì?",
    "Con đường cách mạng vô sản theo tư tưởng Hồ Chí Minh?",
    "Hồ Chí Minh nói gì về vai trò lãnh đạo của Đảng Cộng sản Việt Nam?",
    "Đảng phải trong sạch, vững mạnh theo Hồ Chí Minh nghĩa là gì?",
    "Cán bộ, đảng viên là đầy tớ trung thành của nhân dân nghĩa là gì?",
    "Nhà nước của dân, do dân, vì dân là gì?",
    "Nhà nước vì dân theo tư tưởng Hồ Chí Minh?",
    "Hồ Chí Minh đề ra biện pháp gì để chống tham ô, lãng phí, quan liêu?",
    "Xây dựng nhà nước trong sạch, vững mạnh cần tẩy trừ những gì?",
    "Mặt trận dân tộc thống nhất có vai trò gì?",
    "Quan điểm của Hồ Chí Minh về vai trò và sức mạnh của đạo đức?",
    "Cần, kiệm, liêm, chính, chí công vô tư là gì?",
    "Chủ nghĩa cá nhân là gì theo Hồ Chí Minh?",
    "Chiến lược trồng người của Hồ Chí Minh?",
    "Hồ Chí Minh quan niệm văn hóa là gì?",
    "Học để làm gì, học để phục vụ ai theo Hồ Chí Minh?",
    "Tinh thần quốc tế trong sáng là gì?",
    "Nguồn gốc hình thành tư tưởng Hồ Chí Minh?",
]

# Câu ngoài môn học. Cố tình có câu dùng chung chữ với giáo trình (hại, lợi, đoàn kết, nhà nước...)
# vì đó là loại dễ lọt qua reranker nhất (như "gián có hại không").
OFF_TOPIC = [
    "Gián có hại không?",
    "Thuốc lá có hại cho sức khỏe không?",
    "Ăn nhiều đường có lợi hay hại?",
    "Muỗi truyền bệnh gì?",
    "Làm sao để diệt chuột trong nhà?",
    "Viết giúp tôi một hàm Python sắp xếp danh sách",
    "Công thức nấu phở bò?",
    "Đội bóng nào vô địch World Cup 2022?",
    "Làm thế nào để cả đội bóng đoàn kết hơn?",
    "Thời tiết Hà Nội hôm nay thế nào?",
    "Cách học tiếng Anh nhanh nhất?",
    "Giá vàng hôm nay bao nhiêu?",
    "Nhà nước Mỹ có bao nhiêu bang?",
    "Nên đầu tư chứng khoán hay bất động sản?",
    "Đạo đức kinh doanh trong doanh nghiệp là gì?",
    "Lợi ích của việc tập thể dục buổi sáng?",
    "Hồ Chí Minh thích ăn món gì nhất?",
    "Cách sửa lỗi màn hình xanh trên Windows?",
    "Con mèo có mấy cái chân?",
    "Phân biệt virus và vi khuẩn?",
]


def search(question: str) -> dict:
    req = urllib.request.Request(
        f"{API}/search", method="POST", headers={"Content-Type": "application/json"},
        data=json.dumps({"question": question, "top_n": 10}).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def variant(q: str) -> str:
    """Kiểu người dùng hay gõ: viết thường, không dấu '?'."""
    return q.rstrip(" ?").lower()


def best_threshold(rows: list[dict], key: str) -> tuple[float, int, list[dict]]:
    """Ngưỡng t (score ≥ t → pass) ít lỗi nhất; hoà thì lấy t ở giữa khoảng trống."""
    vals = sorted({r[key] for r in rows})
    cands = [vals[0]] + [(a + b) / 2 for a, b in zip(vals, vals[1:])] + [vals[-1] + 1e-6]
    best = None
    for t in cands:
        wrong = [r for r in rows if (r[key] >= t) != r["in_course"]]
        if best is None or len(wrong) < len(best[2]):
            best = (t, len(wrong), wrong)
    return best


def best_pair(rows: list[dict]) -> tuple[float, float, list[dict]]:
    rr = sorted({r["rerank"] for r in rows})
    cs = sorted({r["cos_top"] for r in rows})
    best = None
    for a in rr:
        for b in cs:
            wrong = [r for r in rows if (r["rerank"] >= a and r["cos_top"] >= b) != r["in_course"]]
            if best is None or len(wrong) < len(best[2]):
                best = (a, b, wrong)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", action="store_true", help="thêm bản viết thường, bỏ '?'")
    ap.add_argument("--csv", help="lưu điểm thô ra file CSV")
    args = ap.parse_args()

    jobs = [(q, True) for q in IN_COURSE] + [(q, False) for q in OFF_TOPIC]
    if args.variants:
        jobs += [(variant(q), ic) for q, ic in jobs]

    rows = []
    print(f"{'nhóm':<4} {'rerank':>6} {'cos_top':>7} {'cos_max':>7} {'lớp 2':<8} câu hỏi → chunk đầu")
    for i, (q, in_course) in enumerate(jobs, 1):
        d = search(q)
        top = d["results"][0] if d["results"] else None
        row = {
            "question": q, "in_course": in_course, "decision": d["retrieval"],
            "rerank": top["rerank_score"] if top else 0.0,
            "cos_top": top["cosine"] if top else 0.0,
            "cos_max": max((it["cosine"] for it in d["results"]), default=0.0),
            "top_source": top["source"] if top else "",
        }
        rows.append(row)
        # Đánh dấu ✗ khi lớp 2 hiện tại quyết định sai (câu giáo trình bị refuse / câu lạc đề được pass)
        wrong = (row["decision"] == "refuse") if in_course else (row["decision"] == "pass")
        print(f"{'IN' if in_course else 'OFF':<4} {row['rerank']:>6.3f} {row['cos_top']:>7.3f} {row['cos_max']:>7.3f} "
              f"{row['decision']:<8}{'✗' if wrong else ' '} {q[:55]} → {row['top_source']}")

    ins = [r for r in rows if r["in_course"]]
    offs = [r for r in rows if not r["in_course"]]
    print("\n" + "=" * 90)
    print(f"{'chỉ số':<8} {'IN thấp nhất':>12} {'OFF cao nhất':>12}   tách được?   ngưỡng tốt nhất")
    for key in ("rerank", "cos_top", "cos_max"):
        lo, hi = min(r[key] for r in ins), max(r[key] for r in offs)
        t, n, wrong = best_threshold(rows, key)
        print(f"{key:<8} {lo:>12.3f} {hi:>12.3f}   {'CÓ' if lo > hi else 'không':<11}  ≥ {t:.3f} → sai {n}/{len(rows)}")
        for r in wrong:
            print(f"{'':<12}sai: [{'IN' if r['in_course'] else 'OFF'}] {key}={r[key]:.3f}  {r['question'][:60]}")

    a, b, wrong = best_pair(rows)
    print(f"rerank ≥ {a:.3f} VÀ cos_top ≥ {b:.3f} → sai {len(wrong)}/{len(rows)}")
    for r in wrong:
        print(f"{'':<12}sai: [{'IN' if r['in_course'] else 'OFF'}] rerank={r['rerank']:.3f} "
              f"cos_top={r['cos_top']:.3f}  {r['question'][:60]}")

    cur_wrong = sum((r["decision"] == "refuse") if r["in_course"] else (r["decision"] == "pass") for r in rows)
    print(f"\nHiện tại (MIN_TOP/PARTIAL_BAND trong search_rerank.py): sai {cur_wrong}/{len(rows)} "
          f"(câu giáo trình bị refuse + câu lạc đề được pass; partial không tính là sai)")
    print("Lưu ý: ngưỡng chọn từ ít câu sẽ khớp quá mức với bộ câu này — để lại khoảng an toàn, "
          "và thêm câu thật từ chat.db trước khi sửa search_rerank.py.")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"Đã lưu {args.csv}")


if __name__ == "__main__":
    main()
