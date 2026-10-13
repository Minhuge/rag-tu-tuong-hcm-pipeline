"""
Schema TRẢ VỀ của API bài kiểm tra (app/routers/exams.py) — roadmap 4.2.

Hai việc:
  1. FastAPI kiểm tra mọi response theo schema: service đổi dict mà quên sửa ở đây → lỗi ngay trong test,
     không âm thầm làm hỏng giao diện. Trường thừa bị bỏ → đáp án không lọt sang bản của người làm bài.
  2. /openapi.json mô tả đủ dữ liệu trả về → web/src/api/schema.d.ts sinh từ đó (npm run api:types),
     frontend gõ sai tên trường là trình soạn thảo báo ngay.

Thời gian là mili giây (epoch), giống các API khác. Trường "chỉ có khi …" khai báo có mặc định và endpoint dùng
response_model_exclude_unset → JSON giữ đúng như service trả (không tự thêm khoá null).
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.db import ExamOrigin, ExamStatus, ExamVisibility, GradedBy, QuestionType, SubmissionStatus

# graded: đã có điểm · grading: AI đang chấm · failed: AI lỗi, sẽ thử lại · quota: hết lượt AI chấm hôm nay ·
# needs_creator: câu tự luận không có barem, chờ người tạo đề chấm tay (app/essay_ai.with_grading_status)
GradingStatus = Literal["graded", "grading", "failed", "quota", "needs_creator"]


class Out(BaseModel):
    """Service trả thêm khoá chưa khai báo ở đây → lỗi (test bắt được), thay vì FastAPI lặng lẽ bỏ đi."""
    model_config = ConfigDict(extra="forbid")


# ---------- câu hỏi ----------
class OptionOut(Out):
    id: int
    label: str
    content: str


class OptionFull(OptionOut):
    is_correct: bool


class QuestionOut(Out):
    """Bản cho người làm bài: không có đáp án đúng, rubric, giải thích."""
    id: int
    type: QuestionType
    order_index: int
    content: str
    points: float
    options: list[OptionOut]


class QuestionFull(QuestionOut):
    """Bản cho người tạo đề."""
    options: list[OptionFull]
    rubric: str | None
    explanation: str | None
    source_page: int | None


# ---------- đề ----------
class ExamOut(Out):
    """Đề (không kèm câu hỏi) — người tạo đề xem."""
    id: int
    title: str
    description: str | None
    visibility: ExamVisibility
    status: ExamStatus
    origin: ExamOrigin
    duration_minutes: int | None
    shuffle_questions: bool
    max_attempts: int | None
    open_at: int | None
    close_at: int | None
    created_at: int
    updated_at: int
    share_code: str | None


class ExamListItem(ExamOut):
    question_count: int
    submission_count: int


class ExamFull(ExamOut):
    questions: list[QuestionFull]


class TakerExam(Out):
    """Đề để làm. Cố tình KHÔNG có is_correct / rubric / explanation / share_code. Riêng schema này bỏ qua khoá
    thừa (service trả cả visibility, status…): dù service lỡ trả thêm, đáp án cũng không lọt ra ngoài."""
    model_config = ConfigDict(extra="ignore")
    id: int
    title: str
    description: str | None
    duration_minutes: int | None
    open_at: int | None
    close_at: int | None
    total_points: float
    questions: list[QuestionOut]


# ---------- tạo đề bằng AI ----------
class ChapterOut(Out):
    number: int                    # 0 = Mở đầu
    chapter: str                   # giá trị gửi lại khi tạo đề
    title: str


class RejectedQuestion(Out):
    kind: QuestionType
    question: str
    stage: str                     # "cấu trúc" | "giám khảo" | "trùng lặp"
    reasons: list[str]


class RequestedCounts(Out):
    mcq: int
    essay: int


class Quota(Out):
    limit: int
    remaining: int                 # lượt còn lại hôm nay, sau đề này


class AiQuota(Out):
    limit: int                     # số đề AI tối đa mỗi ngày (giờ Việt Nam)
    remaining: int                 # còn lại hôm nay
    busy: bool                     # đang có một đề khác đang tạo
    max_mcq: int
    max_essay: int


class GenerationInfo(Out):
    chapter: ChapterOut
    requested: RequestedCounts
    notice: str | None             # tạo được ít câu hơn yêu cầu
    rejected: list[RejectedQuestion]
    models: list[str]
    calls: int
    quota: Quota


class ExamGenerated(ExamFull):
    generation: GenerationInfo


# ---------- bài làm ----------
class UserBrief(Out):
    id: int
    email: str
    display_name: str | None


class SubmissionOut(Out):
    id: int
    exam_id: int
    exam_title: str
    status: SubmissionStatus
    score: float | None
    max_score: float | None
    started_at: int
    deadline: int | None           # hạn nộp (thời gian làm bài / giờ đóng đề); None = không giới hạn
    remaining_seconds: float | None  # máy chủ tính, chỉ khi đang làm và có hạn → đồng hồ đếm ngược
    submitted_at: int | None
    graded_at: int | None


class SubmissionWithUser(SubmissionOut):
    user: UserBrief


class AnswerOut(Out):
    selected_option_id: int | None
    essay_text: str | None


class SavedAnswer(AnswerOut):
    question_id: int


class GradingProgress(Out):
    graded: int
    total: int
    still_grading: bool            # True → hỏi lại sau vài giây


class ResultQuestion(QuestionOut):
    your_answer: AnswerOut
    # chỉ có sau khi nộp
    correct_option_id: int | None = None
    explanation: str | None = None
    source_page: int | None = None
    score: float | None = None
    feedback: str | None = None
    graded_by: GradedBy | None = None
    grading_status: GradingStatus | None = None
    grading_message: str | None = None


class SubmissionResult(SubmissionOut):
    """Bài của chính mình. grading = None khi chưa nộp."""
    questions: list[ResultQuestion]
    grading: GradingProgress | None


class ReviewQuestion(QuestionFull):
    answer: AnswerOut
    score: float | None
    feedback: str | None
    graded_by: GradedBy | None
    graded_at: int | None
    grading_status: GradingStatus | None       # None khi bài chưa nộp
    grading_message: str | None = None         # chỉ có khi bài đã nộp


class SubmissionReview(SubmissionWithUser):
    """Người tạo đề xem một bài làm."""
    questions: list[ReviewQuestion]
    grading: GradingProgress | None
