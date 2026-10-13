"""
Đo tương phản màu (WCAG) của giao diện web: đọc biến màu trong web/src/index.css (chế độ sáng và tối) và kiểm tra
mọi cặp chữ / nền, viền / nền mà các trang đang dùng (roadmap 4.10). Chữ cần ≥ 4.5:1, icon và viền ô nhập ≥ 3:1.

Chạy:  python -m scripts.check_contrast          (in mọi cặp: --all)
tests/test_web_contrast.py chạy cùng kiểm tra này → sửa màu làm tụt tương phản thì test báo.
"""
import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "web" / "src" / "index.css"

# (chữ / viền, nền, ngưỡng, dùng ở đâu)
PAIRS = [
    ("ink", "bg", 4.5, "chữ chính"), ("ink", "surface", 4.5, "chữ trong thẻ"), ("ink", "surface-2", 4.5, "chữ trên nền phụ"),
    ("muted", "bg", 4.5, "chữ phụ trên nền trang"), ("muted", "surface", 4.5, "chữ phụ trong thẻ"),
    ("muted", "surface-2", 4.5, "nhãn số liệu, chip chờ"),
    ("accent", "surface", 4.5, "link, tab đang chọn"), ("accent", "bg", 4.5, "tab đang mở"),
    ("accent", "accent-soft", 4.5, "badge Đang làm, thẻ đang chọn"), ("accent-ink", "accent", 4.5, "nút chính"),
    ("ink", "accent-soft", 4.5, "chữ trong thẻ đang chọn"),
    ("ok", "ok-soft", 4.5, "badge Đã chấm / Đúng"), ("ok", "surface", 4.5, "Đã lưu"), ("ok", "bg", 4.5, "Đã lưu trên thanh"),
    ("warn", "warn-soft", 4.5, "badge Nháp / Đang chấm / Sắp có"), ("warn", "surface", 4.5, "cảnh báo ô nhập"),
    ("warn", "bg", 4.5, "cảnh báo trên thanh"),
    ("block", "block-soft", 4.5, "badge Sai, đồng hồ < 1 phút"), ("block", "surface", 4.5, "lỗi ô nhập"),
    ("block", "bg", 4.5, "lỗi trên thanh"),
    ("ink", "ok-soft", 4.5, "đáp án đúng"), ("ink", "block-soft", 4.5, "lựa chọn sai"), ("ink", "warn-soft", 4.5, "thông báo vàng"),
    ("gold", "surface", 3.0, "icon vàng"), ("gold", "bg", 3.0, "icon vàng"), ("gold", "surface-2", 3.0, "icon vàng trong badge / lịch sử"),
    ("gold", "accent-soft", 3.0, "icon vàng trên thẻ đang chọn"), ("gold", "warn-soft", 3.0, "icon vàng trên thông báo"),
    ("border-strong", "surface", 3.0, "viền ô nhập"), ("border-strong", "bg", 3.0, "viền ô nhập"),
    ("accent", "surface", 3.0, "vòng focus"), ("ok", "surface", 3.0, "viền đáp án đúng"),
]


def themes(css: str) -> dict[str, dict[str, str]]:
    def block(start: str) -> dict[str, str]:
        i = css.index(start)
        return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", css[i:css.index("}", i)]))
    light = block(":root {")
    return {"sáng": light, "tối": {**light, **block(":root:not([data-theme='light'])")}}


def _lum(hex_color: str) -> float:
    c = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted([_lum(a), _lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def check(css: str | None = None) -> list[tuple]:
    """→ [(chế độ, chữ, nền, tỉ lệ, ngưỡng, dùng ở đâu)] của mọi cặp; đạt khi tỉ lệ ≥ ngưỡng."""
    css = css if css is not None else CSS.read_text(encoding="utf-8")
    return [(name, fg, bg, ratio(t[fg], t[bg]), need, use)
            for name, t in themes(css).items() for fg, bg, need, use in PAIRS]


def main() -> int:
    rows = check()
    failed = [r for r in rows if r[3] < r[4]]
    for name, fg, bg, r, need, use in rows:
        if r < need or "--all" in sys.argv:
            print(f"{'OK ' if r >= need else 'LỖI'} {name:4} {r:5.2f} (cần {need}) {fg} / {bg}: {use}")
    print(f"{len(rows) - len(failed)}/{len(rows)} cặp đạt")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
