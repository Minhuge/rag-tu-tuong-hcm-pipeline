"""
Trang web và API dùng chung đường dẫn (/chat, /exams/…, /submissions/…) — app/main.py: spa_fallback (roadmap 4.1).
Trình duyệt mở trang (Accept: text/html) → index.html; fetch / Swagger → API. Không nạp model, không cần web/dist thật.

Chạy:  pytest -q tests/test_web_routes.py
"""
import pytest
from fastapi.testclient import TestClient

from app import main as api

PAGE = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}   # trình duyệt mở trang
INDEX = "<!doctype html><title>web</title><div id=root></div>"


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    monkeypatch.setattr(api, "WEB_DIST", tmp_path)
    return TestClient(api.app)           # không dùng "with" → không chạy lifespan (không nạp model)


@pytest.mark.parametrize("path", ["/chat", "/exams", "/exams/new", "/exams/5/edit", "/exams/5/take",
                                  "/exams/5/results", "/submissions/3", "/exams/5/take?share_code=abc",
                                  "/khong-co-trang-nay"])
def test_browser_navigation_gets_the_web_app(client, path):
    res = client.get(path, headers=PAGE)
    assert res.status_code == 200 and res.text == INDEX


@pytest.mark.parametrize("method,path,status", [
    ("get", "/submissions/3", 401),       # fetch của web: API, cần đăng nhập
    ("get", "/exams/mine", 401),
    ("post", "/chat", 401),               # POST không bao giờ bị thay bằng trang
    ("get", "/chapters", 401),
])
def test_fetch_still_reaches_the_api(client, method, path, status):
    res = getattr(client, method)(path, headers={"Accept": "*/*"})
    assert res.status_code == status and res.headers["content-type"].startswith("application/json")


def test_post_with_html_accept_is_not_a_page(client):
    assert client.post("/chat", headers=PAGE).status_code == 401


def test_fastapi_own_pages_and_static_files_are_untouched(client):
    assert "swagger" in client.get("/docs", headers=PAGE).text.lower()
    assert client.get("/openapi.json", headers=PAGE).json()["info"]["title"]
    assert client.get("/assets/index-abc.js", headers=PAGE).text != INDEX      # file tĩnh: không đổi thành trang


def test_without_a_web_build_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "WEB_DIST", tmp_path / "chua-build")
    res = TestClient(api.app).get("/chat", headers=PAGE)
    assert res.status_code in (404, 405) and res.text != INDEX                # như trước: không có trang GET /chat
