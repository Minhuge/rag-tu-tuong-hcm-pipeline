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
Người làm bài:
  GET    /exams/{id}?share_code=…            đề để làm — KHÔNG có đáp án
  POST   /exams/{id}/submissions             bắt đầu (hoặc làm tiếp) bài làm
  PUT    /submissions/{sid}/answers/{qid}    lưu câu trả lời một câu
  POST   /submissions/{sid}/submit           nộp bài + chấm trắc nghiệm
  GET    /submissions/{sid}                  kết quả bài của tôi
  GET    /submissions/mine                   các bài tôi đã làm

Thời gian (open_at, close_at) gửi theo ISO 8601, nên kèm múi giờ: "2026-10-10T08:00:00+07:00"
(không kèm thì hiểu là UTC). Thời gian trả về là mili giây (epoch), giống các API khác.
"""
from contextlib import contextmanager
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app import auth, exams
from app.db import EXAM_VISIBILITY

router = APIRouter(tags=["bài kiểm tra"])
Visibility = Literal[EXAM_VISIBILITY]


@contextmanager
def _http_errors():
    try:
        yield
    except exams.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except exams.InvalidState as e:          # lớp con của ValueError → phải bắt trước
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ---------- schema gửi lên ----------
class ExamCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, examples=["Kiểm tra chương 1"])
    description: str | None = Field(None, max_length=5000)
    visibility: Visibility = "private"
    duration_minutes: int | None = Field(None, ge=1, description="Bỏ trống = không giới hạn")
    open_at: datetime | None = Field(None, description="Bỏ trống = mở ngay khi xuất bản")
    close_at: datetime | None = Field(None, description="Bỏ trống = không đóng")
    shuffle_questions: bool = False
    max_attempts: int | None = Field(None, ge=1, description="Lưu lại, chưa áp dụng (Phase 6)")


class ExamUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    visibility: Visibility | None = None
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
    type: Literal["mcq", "essay"]
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


class AnswerIn(BaseModel):
    selected_option_id: int | None = Field(None, description="Câu trắc nghiệm")
    essay_text: str | None = Field(None, max_length=exams.MAX_ESSAY_CHARS, description="Câu tự luận")


# ---------- schema trả về cho người làm bài ----------
# Cố tình KHÔNG có is_correct / rubric / explanation: FastAPI bỏ mọi trường thừa theo response_model,
# nên dù service lỡ trả thừa thì đáp án cũng không lọt ra ngoài.
class TakerOption(BaseModel):
    id: int
    label: str
    content: str


class TakerQuestion(BaseModel):
    id: int
    type: str
    order_index: int
    content: str
    points: float
    options: list[TakerOption]


class TakerExam(BaseModel):
    id: int
    title: str
    description: str | None
    duration_minutes: int | None
    open_at: int | None
    close_at: int | None
    total_points: float
    questions: list[TakerQuestion]


# ====================== người tạo đề ======================
@router.post("/exams", status_code=201)
def create_exam(req: ExamCreate, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.create_exam(user["id"], **req.model_dump())


@router.get("/exams/mine")
def list_my_exams(user: dict = Depends(auth.get_current_user)):
    return exams.list_my_exams(user["id"])


@router.get("/exams/{exam_id}/full")
def get_exam_full(exam_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.get_exam_for_creator(user["id"], exam_id)


@router.patch("/exams/{exam_id}")
def update_exam(exam_id: int, req: ExamUpdate, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.update_exam(user["id"], exam_id, **req.model_dump(exclude_unset=True))


@router.delete("/exams/{exam_id}", status_code=204)
def delete_exam(exam_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        exams.delete_exam(user["id"], exam_id)
    return Response(status_code=204)


@router.post("/exams/{exam_id}/questions", status_code=201)
def add_question(exam_id: int, req: QuestionCreate, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.add_question(user["id"], exam_id, **req.model_dump())


@router.patch("/exams/{exam_id}/questions/{question_id}")
def update_question(exam_id: int, question_id: int, req: QuestionUpdate,
                    user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.update_question(user["id"], exam_id, question_id, **req.model_dump(exclude_unset=True))


@router.delete("/exams/{exam_id}/questions/{question_id}", status_code=204)
def delete_question(exam_id: int, question_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        exams.delete_question(user["id"], exam_id, question_id)
    return Response(status_code=204)


@router.post("/exams/{exam_id}/publish")
def publish_exam(exam_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.publish_exam(user["id"], exam_id)


@router.get("/exams/{exam_id}/submissions")
def list_submissions(exam_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.list_submissions(user["id"], exam_id)


# ====================== người làm bài ======================
@router.get("/exams/{exam_id}", response_model=TakerExam)
def get_exam(exam_id: int, share_code: str | None = None, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.get_exam_for_taker(user["id"], exam_id, share_code)


@router.post("/exams/{exam_id}/submissions", status_code=201)
def start_submission(exam_id: int, share_code: str | None = None, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.start_submission(user["id"], exam_id, share_code)


@router.get("/submissions/mine")
def list_my_submissions(user: dict = Depends(auth.get_current_user)):
    return exams.list_my_submissions(user["id"])


@router.put("/submissions/{submission_id}/answers/{question_id}")
def save_answer(submission_id: int, question_id: int, req: AnswerIn, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.save_answer(user["id"], submission_id, question_id, **req.model_dump())


@router.post("/submissions/{submission_id}/submit")
def submit(submission_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.submit(user["id"], submission_id)


@router.get("/submissions/{submission_id}")
def get_result(submission_id: int, user: dict = Depends(auth.get_current_user)):
    with _http_errors():
        return exams.get_my_result(user["id"], submission_id)
