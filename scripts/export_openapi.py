"""
Ghi mô tả API (OpenAPI) ra file để web sinh type TypeScript từ đó (roadmap 4.2). Không cần bật server,
không nạp model, không kết nối database — chỉ đọc các route và schema của FastAPI.

Thường không chạy trực tiếp mà qua:  cd web && npm run api:types
Chạy riêng:                         python -m scripts.export_openapi web/openapi.json

tests/test_api_schemas.py báo lỗi khi file này cũ hơn API → sửa schema xong nhớ chạy lại.
"""
import json
import sys
from pathlib import Path

from app.main import app

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Đã ghi {out}")


if __name__ == "__main__":
    main()
