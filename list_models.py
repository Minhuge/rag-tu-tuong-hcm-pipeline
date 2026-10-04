"""
Liệt kê các model Gemini mà API key của bạn gọi được (lấy đúng tên để điền GEMINI_MODEL).
Lệnh này KHÔNG tốn lượt generate_content.

Chạy:  python list_models.py
"""
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client()   # đọc GEMINI_API_KEY từ .env

print(f"{'GEMINI_MODEL=':<40} {'Tên hiển thị':<32} {'thinking':<9} input_tokens")
print("-" * 100)
for m in client.models.list():
    actions = m.supported_actions or []
    if "generateContent" not in actions:
        continue
    model_id = (m.name or "").removeprefix("models/")
    if not any(k in model_id for k in ("gemini", "gemma")):
        continue
    print(f"{model_id:<40} {(m.display_name or '')[:31]:<32} {str(bool(m.thinking)):<9} {m.input_token_limit}")
