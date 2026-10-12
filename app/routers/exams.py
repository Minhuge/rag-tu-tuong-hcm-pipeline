"""
Bài kiểm tra — Phase 1 (logic và luật ở app/exams.py; file này chỉ nhận request, gọi service, đổi lỗi thành mã HTTP).

Người tạo đề:
  POST   /exams                              tạo đề (luôn là bản nháp)
  GET    /exams/mine                         đề của tôi
  GET    /exams/{id}/full                    đề đầy đủ có đáp án — chỉ người tạo
  PATCH  /exams/{id}     DELETE /exams/{id}  sửa (chỉ bản nháp) / xoá
  POST   /exams/{id}/questions               thêm câu hỏi
  PATCH  /exams/{id}/questions/{qid}         sửa câu hỏi     DELETE … xoá câu hỏi
  POST   /exams/{id}/publish                 xuất bản (kiểm tra đề hợp lệ)
  GET    /exams/{id}/submissions             bài làm của mọi người cho đề này
  GET    /submissions/{sid}/review           xem một bài làm: câu trả lời, đáp án, rubric, điểm + nhận xét từng câu
  PATCH  /submissions/{sid}/answers/{qid}/grade   chấm / sửa điểm một câu (ghi đè điểm AI, tính lại tổng)
Người làm bài:
  GET    /exams/{id}?share_code=…            đề để làm — KHÔNG có đáp án
  POST   /exams/{id}/submissions             bắt đầu (hoặc làm tiếp) bài làm
  PUT    /submissions/{sid}/answers/{qid}    lưu câu trả lời một câu
  POST   /submissions/{sid}/submit           nộp bài + chấm trắc nghiệm; có tự luận → AI chấm nền (Phase 3)
  GET    /submissions/{sid}                  kết quả bài của tôi (+ trạng thái chấm từng câu: hỏi lại tới khi
                                             grading.still_grading = false)
  GET    /submissions/mine                   các bài tôi đã làm
Tạo đề bằng AI (Phase 2, logic ở app/exam_ai.py):
  GET    /chapters                           danh sách chương (số + tên) cho dropdown
  POST   /exams/generate                     AI tạo đề NHÁP từ một chương (chờ 10–30 giây) → sửa, publish như đề tay

Thời gian (open_at, close_at) gửi theo ISO 8601, nên kèm múi giờ: "2026-10-10T08:00:00+07:00"
(không kèm thì hiểu là UTC). Thời gian trả về là mili giây (epoch), giống các API khác.
"""
from contextlib import contextmanager
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app import auth, deps, essay_ai, exam_ai, exams
from app import exam_schemas as out
from app.db import ExamVisibility, QuestionType
from app.deps import get_rag
from rag.exam_generation import CHAPTERS

router = APIRouter(tags=["bài kiểm tra"])


@contextmanager
def _http_errors():
    try:
        yield
    except exams.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except exams.QuotaExceeded as e:
        raise HTTPException(429, str(e)) from e
    except exam_ai.GenerationFailed as e:    # Qdrant / Gemini lỗi: không phải lỗi người gọi, thử lại sau
        raise HTTPException(503, str(e)) from e
    except exams.InvalidState as e:          # lớp con của ValueError → phải bắt trước
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ---------- schema gửi lên ----------
class ExamCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, examples=["Kiểm tra chương 1"])
    description: str | None = Field(None, max_length=5000)
    visibility: ExamVisibility = "private"
    duration_minutes: int | None = Field(None, ge=1, description="Bỏ trống = không giới hạn")
    open_at: datetime | None = Field(None, description="Bỏ trống = mở ngay khi xuất bản")
    close_at: datetime | None = Field(None, description="Bỏ trống = không đóng")
    shuffle_questions: bool = False
    max_attempts: int | None = Field(None, ge=1, description="Lưu lại, chưa áp dụng (Phase 6)")


class ExamUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    visibility: ExamVisibility | None = None
    duration_minutes: int | None = Field(None, ge=1)
    open_at: datetime | None = None
    close_at: datetime | None = None
    shuffle_questions: bool | None = None
    max_attempts: int | None = Field(None, ge=1)


class OptionIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=8, examples=["A"])
    content: str = Field(..., min_length=1, max_length=2000)
    is_correct: bool = False


class QuestionCreate(BaseModel):
    type: QuestionType
    content: str = Field(..., min_length=1, max_length=5000)
    points: float = Field(1.0, ge=0)
    rubric: str | None = Field(None, max_length=5000, description="Chỉ câu tự luận")
    explanation: str | None = Field(None, max_length=5000)
    source_page: int | None = Field(None, ge=1)
    options: list[OptionIn] = Field(default_factory=list, max_length=10, description="Chỉ câu trắc nghiệm")

    model_config = {"json_schema_extra": {"examples": [{
        "type": "mcq", "content": "Tư tưởng Hồ Chí Minh hình thành từ những tiền đề nào?", "points": 1,
        "explanation": "Giáo trình nêu ba tiền đề…", "source_page": 25,
        "options": [{"label": "A", "content": "Chỉ chủ nghĩa Mác – Lênin", "is_correct": False},
                    {"label": "B", "content": "Truyền thống dân tộc, tinh hoa văn hoá nhân loại, chủ nghĩa Mác – Lênin",
                     "is_correct": True}]}]}}


class QuestionUpdate(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=5000)
    points: float | None = Field(None, ge=0)
    rubric: str | None = Field(None, max_length=5000)
    explanation: str | None = Field(None, max_length=5000)
    source_page: int | None = Field(None, ge=1)
    options: list[OptionIn] | None = Field(None, max_length=10, description="Gửi → thay toàn bộ lựa chọn")


class ExamGenerate(BaseModel):
    chapter: int | str = Field(..., examples=[3], description='Số chương (0 = Mở đầu) hoặc "Chương III" — xem GET /chapters')
    n_mcq: int = Field(10, ge=0, le=exam_ai.MAX_MCQ, description="Số câu trắc nghiệm")
    n_essay: int = Field(2, ge=0, le=exam_ai.MAX_ESSAY, description="Số câu tự luận")
    title: str | None = Field(None, max_length=200, description="Bỏ trống = tên chương")


class GradeIn(BaseModel):
    score: float = Field(..., ge=0, examples=[1.5], description="Từ 0 đến điểm tối đa của câu")
    feedback: str | None = Field(None, max_length=5000, description='Bỏ trống = giữ nhận xét cũ; "" = xoá')


class AnswerIn(BaseModel):
    selected_option_id: int | None = Field(None, description="Câu trắc nghiệm")
    essay_text: str | None = Field(None, max_length=exams.MAX_ESSAY_CHARS, description="Câu tự luận")


# ====================== người tạo đề ======================
@router.post("/exams", status_code=201, response_model=out.ExamOut)
def create_exam(req: ExamCreate, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.create_exam(user["id"], **req.model_dump())


@router.get("/exams/mine", response_model=list[out.ExamListItem])
def list_my_exams(user: dict = Depends(auth.get_current_user)):
    return exams.list_my_exams(user["id"])


@router.get("/exams/{exam_id}/full", response_model=out.ExamFull)
def get_exam_full(exam_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.get_exam_for_creator(user["id"], exam_id)


@router.patch("/exams/{exam_id}", response_model=out.ExamOut)
def update_exam(exam_id: int, req: ExamUpdate, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.update_exam(user["id"], exam_id, **req.model_dump(exclude_unset=True))


@router.delete("/exams/{exam_id}", status_code=204)
def delete_exam(exam_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        exams.delete_exam(user["id"], exam_id)
    return Response(status_code=204)


@router.post("/exams/{exam_id}/questions", status_code=201, response_model=out.QuestionFull)
def add_question(exam_id: int, req: QuestionCreate, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.add_question(user["id"], exam_id, **req.model_dump())


@router.patch("/exams/{exam_id}/questions/{question_id}", response_model=out.QuestionFull)
def update_question(exam_id: int, question_id: int, req: QuestionUpdate,
                    user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.update_question(user["id"], exam_id, question_id, **req.model_dump(exclude_unset=True))


@router.delete("/exams/{exam_id}/questions/{question_id}", status_code=204)
def delete_question(exam_id: int, question_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        exams.delete_question(user["id"], exam_id, question_id)
    return Response(status_code=204)


@router.post("/exams/{exam_id}/publish", response_model=out.ExamOut)
def publish_exam(exam_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.publish_exam(user["id"], exam_id)


@router.get("/exams/{exam_id}/submissions", response_model=list[out.SubmissionWithUser])
def list_submissions(exam_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.list_submissions(user["id"], exam_id)


@router.get("/submissions/{submission_id}/review", response_model=out.SubmissionReview,
            response_model_exclude_unset=True)
def review_submission(submission_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return essay_ai.with_grading_status(exams.get_submission_for_creator(user["id"], submission_id))


@router.patch("/submissions/{submission_id}/answers/{question_id}/grade", response_model=out.SubmissionReview,
              response_model_exclude_unset=True)
def grade_answer(submission_id: int, question_id: int, req: GradeIn, user: dict = Depends(auth.get_current_user)):
    """Người tạo đề chấm tay / sửa điểm một câu. Chỉ người tạo đề; người khác 404. Điểm vượt tối đa → 400."""
    with _http_errors():
        return essay_ai.with_grading_status(exams.grade_answer_by_creator(
            user["id"], submission_id, question_id, score=req.score, feedback=req.feedback))


# ====================== người làm bài ======================
@router.get("/exams/{exam_id}", response_model=out.TakerExam)
def get_exam(exam_id: int, share_code: str | None = None, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.get_exam_for_taker(user["id"], exam_id, share_code)


@router.post("/exams/{exam_id}/submissions", status_code=201, response_model=out.SubmissionOut)
def start_submission(exam_id: int, share_code: str | None = None, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.start_submission(user["id"], exam_id, share_code)


@router.get("/submissions/mine", response_model=list[out.SubmissionOut])
def list_my_submissions(user: dict = Depends(auth.get_current_user)):
    return exams.list_my_submissions(user["id"])


@router.put("/submissions/{submission_id}/answers/{question_id}", response_model=out.SavedAnswer)
def save_answer(submission_id: int, question_id: int, req: AnswerIn, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.save_answer(user["id"], submission_id, question_id, **req.model_dump())


@router.post("/submissions/{submission_id}/submit", response_model=out.SubmissionResult,
             response_model_exclude_unset=True)
def submit(submission_id: int, background: BackgroundTasks, user: dict = Depends(auth.get_current_user)):
    """Chấm trắc nghiệm ngay. Có câu tự luận → status "submitted", AI chấm nền sau khi trả kết quả:
    gọi lại GET /submissions/{id} tới khi status = "graded"."""
    with _http_errors():
        result = essay_ai.with_grading_status(exams.submit(user["id"], submission_id))
    rag = deps.rag      # chưa nạp xong model → không chấm ngay; lần quét bù khi khởi động sẽ chấm
    if result["status"] == "submitted" and rag is not None:
        background.add_task(essay_ai.grade_submission, submission_id, llm=rag.llm,
                            qdrant=rag.retriever.client, embedder=rag.retriever.embedder)
    return result


@router.get("/submissions/{submission_id}", response_model=out.SubmissionResult,
            response_model_exclude_unset=True)
def get_result(submission_id: int, user: dict = Depends(auth.get_current_user)):
    """Có `grading.still_grading = true` → AI đang chấm tự luận: gọi lại sau vài giây để lấy điểm."""
    with _http_errors():
        return essay_ai.with_grading_status(exams.get_my_result(user["id"], submission_id))


# ---------- tạo đề bằng AI (Phase 2) ----------
@router.get("/chapters", response_model=list[out.ChapterOut])
def list_chapters(user: dict = Depends(auth.get_current_user)):
    """`chapter` là giá trị truyền lại khi tạo đề (khớp payload "chapter" trong Qdrant)."""
    return [{"number": c.number, "chapter": c.key, "title": c.title} for c in CHAPTERS]


@router.post("/exams/generate", status_code=201, response_model=out.ExamGenerated)
async def generate_exam(req: ExamGenerate, user: dict = Depends(auth.get_current_user)):
    """
    AI tạo đề nháp từ một chương giáo trình. Trả về đề đã lưu (giống GET /exams/{id}/full) + `generation`:
    câu bị loại kèm lý do, `notice` khi tạo được ít câu hơn yêu cầu, lượt còn lại hôm nay.
    400 số câu / chương sai · 429 hết lượt hôm nay hoặc đang tạo đề khác · 503 AI / kho giáo trình lỗi (không lưu gì).
    """
    r = get_rag()   # dùng lại Gemini client + Qdrant client đã tạo lúc khởi động; đang khởi động → 503
    with _http_errors():
        return await exam_ai.generate_exam_draft(user["id"], req.chapter, req.n_mcq, req.n_essay,
                                                 llm=r.llm, qdrant=r.retriever.client, title=req.title)
