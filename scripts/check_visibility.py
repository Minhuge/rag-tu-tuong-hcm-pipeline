"""
Kiểm tra quyền xem đề trên server ĐANG CHẠY (khác pytest: đi qua uvicorn + Postgres thật).

  A tạo 4 đề bằng tay (không gọi Gemini, không tốn lượt), B thử truy cập:
    private đã xuất bản   → B không xem / làm / sửa được (404), A vẫn xem được
    public nhưng còn nháp → B không xem được (404)
    public đã xuất bản    → B xem được (200) nhưng KHÔNG thấy đáp án, rubric, giải thích
    link đã xuất bản      → B cần đúng share_code
  Xong thì xoá các đề vừa tạo. Hai tài khoản thử (vischeck-a/b@example.com) được giữ lại để lần sau dùng lại.

Chạy (server đang bật ở cổng 8000):  python -m scripts.check_visibility
                                    python -m scripts.check_visibility --base http://localhost:8001
"""
import argparse
import sys

import httpx

PASSWORD = "kiem-tra-quyen-123"
SECRET_KEYS = {"is_correct", "rubric", "explanation"}

MCQ = {"type": "mcq", "content": "Câu hỏi thử quyền xem?", "explanation": "Giải thích bí mật", "source_page": 1,
       "options": [{"label": "A", "content": "Sai", "is_correct": False},
                   {"label": "B", "content": "Đúng", "is_correct": True}]}


class Checker:
    def __init__(self):
        self.passed = self.failed = 0

    def expect(self, label: str, res: httpx.Response, status: int, extra: bool = True, note: str = ""):
        ok = res.status_code == status and extra
        self.passed += ok
        self.failed += not ok
        detail = "" if ok else f"  ← nhận {res.status_code} {res.text[:150]}"
        print(f"  {'✓' if ok else '✗'} {label}: mong đợi {status}{note}{detail}")


def keys_anywhere(obj) -> set:
    if isinstance(obj, dict):
        return set(obj) | set().union(*(keys_anywhere(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(keys_anywhere(v) for v in obj)) if obj else set()
    return set()


def login(base: str, email: str) -> httpx.Client:
    """Đăng nhập; chưa có tài khoản thì đăng ký. Client dùng header Bearer (không dùng cookie)."""
    with httpx.Client(base_url=base, timeout=30) as c:
        res = c.post("/auth/login", json={"email": email, "password": PASSWORD})
        if res.status_code == 401:
            res = c.post("/auth/register", json={"email": email, "password": PASSWORD})
            if res.status_code == 403:
                sys.exit(f"Server đang tắt đăng ký. Tạo tài khoản {email} (mật khẩu {PASSWORD}) bằng "
                         "python -m scripts.create_user rồi chạy lại.")
        res.raise_for_status()
    token = res.json()["access_token"]
    return httpx.Client(base_url=base, timeout=30, headers={"Authorization": f"Bearer {token}"})


def make_exam(a: httpx.Client, title: str, visibility: str, publish: bool) -> dict:
    exam = a.post("/exams", json={"title": title, "visibility": visibility})
    exam.raise_for_status()
    exam = exam.json()
    a.post(f"/exams/{exam['id']}/questions", json=MCQ).raise_for_status()
    if publish:
        exam = a.post(f"/exams/{exam['id']}/publish")
        exam.raise_for_status()
        exam = exam.json()
    return exam


def main():
    ap = argparse.ArgumentParser(description="Kiểm tra quyền xem đề (private / public / link / nháp)")
    ap.add_argument("--base", default="http://localhost:8000")
    base = ap.parse_args().base
    try:
        httpx.get(f"{base}/health", timeout=5)
    except httpx.HTTPError:
        sys.exit(f"Không kết nối được {base} — bật server trước: uvicorn app.main:app --port 8000")

    a, b = login(base, "vischeck-a@example.com"), login(base, "vischeck-b@example.com")
    print(f"A = {a.get('/auth/me').json()['email']}, B = {b.get('/auth/me').json()['email']}\n")
    t, created = Checker(), []
    try:
        private = make_exam(a, "[kiểm tra] private", "private", publish=True)
        draft = make_exam(a, "[kiểm tra] public nhưng còn nháp", "public", publish=False)
        public = make_exam(a, "[kiểm tra] public", "public", publish=True)
        link = make_exam(a, "[kiểm tra] link", "link", publish=True)
        created = [private, draft, public, link]

        pid = private["id"]
        print(f"1. Đề private đã xuất bản (id {pid})")
        t.expect("B xem đề để làm      GET  /exams/{id}", b.get(f"/exams/{pid}"), 404)
        t.expect("B xem đáp án         GET  /exams/{id}/full", b.get(f"/exams/{pid}/full"), 404)
        t.expect("B bắt đầu làm bài    POST /exams/{id}/submissions", b.post(f"/exams/{pid}/submissions"), 404)
        t.expect("B sửa đề             PATCH /exams/{id}", b.patch(f"/exams/{pid}", json={"title": "x"}), 404)
        t.expect("B xoá đề             DELETE /exams/{id}", b.delete(f"/exams/{pid}"), 404)
        t.expect("B xem bài làm của đề GET  /exams/{id}/submissions", b.get(f"/exams/{pid}/submissions"), 404)
        mine = b.get("/exams/mine")
        t.expect("B: đề không nằm trong /exams/mine của B", mine, 200,
                 all(e["id"] != pid for e in mine.json()))
        t.expect("A (người tạo) vẫn xem được", a.get(f"/exams/{pid}"), 200)
        t.expect("Chưa đăng nhập", httpx.get(f"{base}/exams/{pid}"), 401)

        print(f"\n2. Đề public nhưng còn nháp (id {draft['id']})")
        t.expect("B xem đề nháp", b.get(f"/exams/{draft['id']}"), 404)

        print(f"\n3. Đề public đã xuất bản (id {public['id']})")
        res = b.get(f"/exams/{public['id']}")
        leaked = keys_anywhere(res.json()) & SECRET_KEYS if res.status_code == 200 else set()
        t.expect("B xem đề để làm", res, 200, not leaked,
                 note=", không lộ đáp án/rubric/giải thích" + (f" (LỘ: {leaked})" if leaked else ""))
        t.expect("B xem đáp án /full", b.get(f"/exams/{public['id']}/full"), 404)

        code = link.get("share_code")
        print(f"\n4. Đề link đã xuất bản (id {link['id']}, share_code {code})")
        t.expect("B không có mã", b.get(f"/exams/{link['id']}"), 404)
        t.expect("B sai mã", b.get(f"/exams/{link['id']}", params={"share_code": "sai-ma"}), 404)
        t.expect("B đúng mã", b.get(f"/exams/{link['id']}", params={"share_code": code}), 200)
    finally:
        for e in created:
            a.delete(f"/exams/{e['id']}")
        if created:
            print(f"\nĐã xoá {len(created)} đề thử.")

    print(f"\nKết quả: {t.passed} đạt, {t.failed} không đạt")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
