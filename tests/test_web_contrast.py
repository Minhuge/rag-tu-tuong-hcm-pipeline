"""
Tương phản màu của giao diện web (scripts/check_contrast.py, roadmap 4.10): mọi cặp chữ / nền đạt WCAG ở cả chế độ
sáng và tối. Sửa biến màu trong web/src/index.css làm tụt tương phản → test hỏng, kèm cặp nào và dùng ở đâu.

Chạy:  pytest -q tests/test_web_contrast.py
"""
from scripts.check_contrast import check, ratio


def test_every_color_pair_meets_wcag():
    failed = [f"{name}: {fg}/{bg} = {r:.2f} < {need} ({use})" for name, fg, bg, r, need, use in check() if r < need]
    assert not failed, "\n".join(failed)


def test_ratio_matches_known_values():
    assert round(ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(ratio("#777777", "#ffffff"), 2) == 4.48         # xám #777 trên trắng: vừa dưới 4.5 → không đạt
