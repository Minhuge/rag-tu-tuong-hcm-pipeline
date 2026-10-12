// @ts-check
/**
 * Gọi API bài kiểm tra (app/routers/exams.py) — roadmap 4.2.
 *
 * Kiểu dữ liệu lấy từ api-schema.d.ts, sinh tự động từ API thật:  npm run api:types
 * (sửa schema ở backend mà quên chạy lại → tests/test_api_schemas.py báo). File này có @ts-check:
 * gõ sai tên trường / quên tham số → VS Code gạch đỏ, `npm run typecheck` báo lỗi.
 *
 * Mọi hàm đi qua request() của api.js: hết phiên (401) tự làm mới rồi gửi lại; lỗi → ApiError có message tiếng Việt,
 * .fields (lỗi từng ô nhập), .question (câu bị lỗi khi publish), .retryable (nên hiện nút "Thử lại").
 */
import { ApiError, request } from './api.js'

/** @typedef {import('./api-schema').components['schemas']} Schemas */
/** @typedef {Schemas['ExamOut']} ExamOut */
/** @typedef {Schemas['ExamListItem']} ExamListItem */
/** @typedef {Schemas['ExamFull']} ExamFull */
/** @typedef {Schemas['ExamCreate']} ExamCreate */
/** @typedef {Schemas['ExamUpdate']} ExamUpdate */
/** @typedef {Schemas['QuestionFull']} QuestionFull */
/** @typedef {Schemas['QuestionCreate']} QuestionCreate */
/** @typedef {Schemas['QuestionUpdate']} QuestionUpdate */
/** @typedef {Schemas['ChapterOut']} ChapterOut */
/** @typedef {Schemas['ExamGenerate']} ExamGenerate */
/** @typedef {Schemas['ExamGenerated']} ExamGenerated */
/** @typedef {Schemas['TakerExam']} TakerExam */
/** @typedef {Schemas['SubmissionOut']} SubmissionOut */
/** @typedef {Schemas['SubmissionWithUser']} SubmissionWithUser */
/** @typedef {Schemas['SubmissionResult']} SubmissionResult */
/** @typedef {Schemas['SubmissionReview']} SubmissionReview */
/** @typedef {Schemas['AnswerIn']} AnswerIn */
/** @typedef {Schemas['SavedAnswer']} SavedAnswer */
/** @typedef {Schemas['GradeIn']} GradeIn */

const json = (/** @type {unknown} */ body) => JSON.stringify(body)

/** ?share_code=… khi có (đề chia sẻ bằng link), rỗng khi không. */
const withShareCode = (/** @type {string | null | undefined} */ shareCode) =>
  shareCode ? `?${new URLSearchParams({ share_code: shareCode })}` : ''

// Thao tác không nên chạy hai lần (nộp bài, publish, tạo đề AI): đang chạy mà bị gọi lại (bấm đúp, Enter + click)
// → trả về đúng lời gọi đang chạy thay vì gửi request thứ hai. Giao diện vẫn nên khoá nút trong lúc chờ.
/** @type {Map<string, Promise<any>>} */
const running = new Map()
/**
 * @template T
 * @param {string} key
 * @param {() => Promise<T>} fn
 * @returns {Promise<T>}
 */
function once(key, fn) {
  let p = running.get(key)
  if (!p) {
    p = fn().finally(() => running.delete(key))
    running.set(key, p)
  }
  return p
}

// ====================== người tạo đề ======================
/** @returns {Promise<ExamListItem[]>} đề tôi tạo, mới sửa gần nhất trước */
export const listMyExams = () => request('/exams/mine')

/** Tạo đề trống (luôn là bản nháp). @param {ExamCreate} fields @returns {Promise<ExamOut>} */
export const createExam = (fields) => request('/exams', { method: 'POST', body: json(fields) })

/** Đề đầy đủ có đáp án, rubric — chỉ người tạo. @param {number} id @returns {Promise<ExamFull>} */
export const getExamFull = (id) => request(`/exams/${id}/full`)

/** Chỉ gửi trường cần đổi. Chỉ sửa được bản nháp (409 nếu đã publish). @param {number} id @param {ExamUpdate} fields @returns {Promise<ExamOut>} */
export const updateExam = (id, fields) => request(`/exams/${id}`, { method: 'PATCH', body: json(fields) })

/** @param {number} id @returns {Promise<null>} */
export const deleteExam = (id) => request(`/exams/${id}`, { method: 'DELETE' })

/** @param {number} examId @param {QuestionCreate} question @returns {Promise<QuestionFull>} */
export const addQuestion = (examId, question) =>
  request(`/exams/${examId}/questions`, { method: 'POST', body: json(question) })

/** Gửi options → thay toàn bộ lựa chọn. @param {number} examId @param {number} questionId @param {QuestionUpdate} fields @returns {Promise<QuestionFull>} */
export const updateQuestion = (examId, questionId, fields) =>
  request(`/exams/${examId}/questions/${questionId}`, { method: 'PATCH', body: json(fields) })

/** @param {number} examId @param {number} questionId @returns {Promise<null>} */
export const deleteQuestion = (examId, questionId) =>
  request(`/exams/${examId}/questions/${questionId}`, { method: 'DELETE' })

/**
 * Xuất bản. Đề chưa hợp lệ → ApiError 400, .question = số thứ tự câu bị lỗi (nếu lỗi nằm ở một câu).
 * @param {number} id @returns {Promise<ExamOut>}
 */
export const publishExam = (id) => once(`publish:${id}`, () => request(`/exams/${id}/publish`, { method: 'POST' }))

/** @param {number} examId @returns {Promise<SubmissionWithUser[]>} mọi bài nộp cho đề này, mới nhất trước */
export const listExamSubmissions = (examId) => request(`/exams/${examId}/submissions`)

/** Một bài làm: câu trả lời, đáp án, rubric, điểm + nhận xét. @param {number} submissionId @returns {Promise<SubmissionReview>} */
export const getSubmissionReview = (submissionId) => request(`/submissions/${submissionId}/review`)

/**
 * Chấm tay / sửa điểm một câu (ghi đè điểm AI, tính lại tổng). feedback bỏ trống = giữ nhận xét cũ, "" = xoá.
 * @param {number} submissionId @param {number} questionId @param {GradeIn} grade @returns {Promise<SubmissionReview>}
 */
export const gradeAnswer = (submissionId, questionId, grade) =>
  request(`/submissions/${submissionId}/answers/${questionId}/grade`, { method: 'PATCH', body: json(grade) })

// ---------- tạo đề bằng AI ----------
/** @returns {Promise<ChapterOut[]>} danh sách chương cho dropdown (number 0 = Mở đầu) */
export const listChapters = () => request('/chapters')

// AI thường mất 10–30 giây; tạo bù + Gemini quá tải có thể lâu hơn nhiều. Quá mốc này thì thôi chờ.
export const GENERATE_TIMEOUT_MS = 180_000

/**
 * AI tạo đề nháp từ một chương. 429 = hết lượt hôm nay / đang tạo đề khác · 503 = AI lỗi, thử lại sau.
 * Huỷ (signal) hay quá giờ chỉ dừng CHỜ ở trình duyệt: máy chủ vẫn có thể tạo xong và lưu đề.
 * @param {ExamGenerate} params
 * @param {{ signal?: AbortSignal }} [opts]
 * @returns {Promise<ExamGenerated>}
 */
export function generateExam(params, { signal } = {}) {
  return once('generate', async () => {
    const timeout = AbortSignal.timeout(GENERATE_TIMEOUT_MS)
    try {
      return await request('/exams/generate', {
        method: 'POST', body: json(params), signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      })
    } catch (e) {
      if (!timeout.aborted) throw e
      throw new ApiError('AI tạo đề lâu hơn bình thường. Đề có thể vẫn đang được tạo — xem lại "Bài kiểm tra của tôi" '
        + 'sau ít phút trước khi tạo lại (mỗi lần tạo tốn một lượt).', 0)
    }
  })
}

// ====================== người làm bài ======================
/**
 * Đề để làm — không có đáp án. Đề chia sẻ bằng link cần shareCode.
 * @param {number} id @param {string | null} [shareCode] @returns {Promise<TakerExam>}
 */
export const getExamForTaker = (id, shareCode) => request(`/exams/${id}${withShareCode(shareCode)}`)

/**
 * Bắt đầu làm bài — đang có bài chưa nộp thì trả lại bài đó (làm tiếp). 409: đề chưa mở / đã đóng.
 * @param {number} examId @param {string | null} [shareCode] @returns {Promise<SubmissionOut>}
 */
export const startSubmission = (examId, shareCode) =>
  once(`start:${examId}`, () => request(`/exams/${examId}/submissions${withShareCode(shareCode)}`, { method: 'POST' }))

/**
 * Lưu câu trả lời một câu (tự lưu). keepalive: vẫn gửi được khi trang đang đóng (giới hạn ~64 KB).
 * @param {number} submissionId @param {number} questionId @param {AnswerIn} answer
 * @param {{ signal?: AbortSignal, keepalive?: boolean }} [opts]
 * @returns {Promise<SavedAnswer>}
 */
export const saveAnswer = (submissionId, questionId, answer, { signal, keepalive = false } = {}) =>
  request(`/submissions/${submissionId}/answers/${questionId}`, { method: 'PUT', body: json(answer), signal, keepalive })

/**
 * Nộp bài: trắc nghiệm chấm ngay; có tự luận → grading.still_grading = true, gọi lại getSubmissionResult sau vài giây.
 * @param {number} submissionId @returns {Promise<SubmissionResult>}
 */
export const submitSubmission = (submissionId) =>
  once(`submit:${submissionId}`, () => request(`/submissions/${submissionId}/submit`, { method: 'POST' }))

/** Bài của tôi (chưa nộp: chỉ câu trả lời đã lưu). @param {number} submissionId @param {{ signal?: AbortSignal }} [opts] @returns {Promise<SubmissionResult>} */
export const getSubmissionResult = (submissionId, { signal } = {}) => request(`/submissions/${submissionId}`, { signal })

/** @returns {Promise<SubmissionOut[]>} các bài tôi đã làm, mới nhất trước */
export const listMySubmissions = () => request('/submissions/mine')

// ---------- đường dẫn trang ----------
/** Link mở trang làm bài, để sao chép / chia sẻ. @param {Pick<ExamOut, 'id' | 'share_code' | 'visibility'>} exam */
export const takeExamUrl = (exam) =>
  `${window.location.origin}/exams/${exam.id}/take${exam.visibility === 'link' ? withShareCode(exam.share_code) : ''}`
