"""
Giám khảo RAGAS dùng chung cho scripts/eval_ragas.py và scripts/eval_agent.py: Gemini hoặc DeepSeek qua instructor
(structured output), kết quả cache trong .ragas_cache/<model>/ → chấm lại câu không đổi thì không tốn lượt.

Import module này TRƯỚC mọi import ragas (nó vá lỗi import của ragas 0.4.3 bên dưới).
Tuỳ chọn .env:  RAGAS_MODEL=gemini-3.6-flash   (giám khảo khác model trả lời → bớt thiên vị "tự chấm mình")
                RAGAS_MODEL=deepseek-v4-pro    + DEEPSEEK_API_KEY=...   (DEEPSEEK_BASE_URL mặc định https://api.deepseek.com)
"""
import os
import sys
import types

# ragas 0.4.3 vẫn import ChatVertexAI, đã bị gỡ khỏi langchain-community 0.4 → tạo module giả để import được.
# Không dùng tới VertexAI; bỏ đoạn này khi ragas sửa (https://github.com/vibrantlabsai/ragas).
_vertex = types.ModuleType("langchain_community.chat_models.vertexai")
_vertex.ChatVertexAI = type("ChatVertexAI", (), {})
sys.modules.setdefault(_vertex.__name__, _vertex)

import instructor  # noqa: E402
from google import genai  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from ragas.cache import DiskCacheBackend  # noqa: E402
from ragas.llms.base import InstructorLLM, InstructorModelArgs  # noqa: E402

from rag.pipeline import GEMINI_MODEL  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JUDGE_MODEL = os.getenv("RAGAS_MODEL", GEMINI_MODEL)


def judge_llm() -> InstructorLLM:
    # ragas tự bọc client ở chế độ sync nhưng metric lại gọi async → tự bọc bản async.
    # max_tokens cao vì token "thinking" cũng tính vào giới hạn này (1024 mặc định → JSON bị cắt).
    if JUDGE_MODEL.startswith("deepseek"):
        # API DeepSeek tương thích OpenAI; Mode.JSON = response_format json_object + schema JSON trong prompt
        client = instructor.from_openai(AsyncOpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com")), mode=instructor.Mode.JSON)
        provider = "openai"
    else:
        client = instructor.from_genai(genai.Client(), mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS, use_async=True)
        provider = "google"
    # khoá cache không chứa tên model → mỗi giám khảo một thư mục, đổi model không dùng nhầm điểm của model cũ
    return InstructorLLM(client=client, model=JUDGE_MODEL, provider=provider,
                         model_args=InstructorModelArgs(max_tokens=8192),
                         cache=DiskCacheBackend(os.path.join(ROOT, ".ragas_cache", JUDGE_MODEL)))
