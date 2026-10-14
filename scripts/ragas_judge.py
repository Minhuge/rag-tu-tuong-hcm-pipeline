"""
Giám khảo RAGAS dùng chung cho scripts/eval_ragas.py và scripts/eval_agent.py: Gemini qua instructor (structured
output), kết quả cache trong .ragas_cache/ → chấm lại câu không đổi thì không tốn lượt.

Import module này TRƯỚC mọi import ragas (nó vá lỗi import của ragas 0.4.3 bên dưới).
Tuỳ chọn .env:  RAGAS_MODEL=gemini-3.6-flash   (giám khảo khác model trả lời → bớt thiên vị "tự chấm mình")
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
from ragas.cache import DiskCacheBackend  # noqa: E402
from ragas.llms.base import InstructorLLM, InstructorModelArgs  # noqa: E402

from rag.pipeline import GEMINI_MODEL  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JUDGE_MODEL = os.getenv("RAGAS_MODEL", GEMINI_MODEL)


def judge_llm() -> InstructorLLM:
    # ragas tự bọc genai.Client ở chế độ sync nhưng metric lại gọi async → tự bọc bản async.
    # max_tokens cao vì token "thinking" của Gemini cũng tính vào giới hạn này (1024 mặc định → JSON bị cắt).
    return InstructorLLM(
        client=instructor.from_genai(genai.Client(), mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS, use_async=True),
        model=JUDGE_MODEL, provider="google", model_args=InstructorModelArgs(max_tokens=8192),
        cache=DiskCacheBackend(os.path.join(ROOT, ".ragas_cache")))
