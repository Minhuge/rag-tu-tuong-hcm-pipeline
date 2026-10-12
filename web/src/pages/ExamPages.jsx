import { ArrowLeft, Plus } from '@phosphor-icons/react'
import { useEffect, useRef } from 'react'
import { Link, useParams } from 'react-router'

/**
 * Khung các trang bài kiểm tra (roadmap 4.1). Nội dung thật làm ở 4.3–4.8; tạm thời mỗi trang nói rõ sẽ có gì
 * và nối sẵn các liên kết để đi thử mọi route.
 */

/** Khung chung: tiêu đề tab, đưa focus tới tiêu đề khi đổi trang (trình đọc màn hình biết đã sang trang mới). */
export function Page({ title, back, children }) {
  const headingRef = useRef(null)
  useEffect(() => {
    document.title = `${title} · Trợ lý Tư tưởng HCM`
    headingRef.current?.focus()
  }, [title])
  return (
    <main className="page" id="main">
      {back && (
        <Link className="back-link" to={back.to}>
          <ArrowLeft size={14} aria-hidden="true" />
          {back.label}
        </Link>
      )}
      <h1 ref={headingRef} tabIndex={-1}>{title}</h1>
      {children}
    </main>
  )
}

function ComingSoon({ step, children }) {
  return (
    <section className="coming-soon">
      <span className="coming-badge">Sắp có · bước {step}</span>
      <p>{children}</p>
    </section>
  )
}

/** id trên URL phải là số nguyên dương; sai → null (trang hiện "không tìm thấy" thay vì gọi API với id rác). */
function useNumericId() {
  const { id } = useParams()
  return /^[1-9]\d*$/.test(id ?? '') ? Number(id) : null
}

const TO_EXAMS = { to: '/exams', label: 'Bài kiểm tra của tôi' }

export function MyExamsPage() {
  return (
    <Page title="Bài kiểm tra của tôi">
      <div className="page-actions">
        <Link className="primary-btn" to="/exams/new">
          <Plus size={15} weight="bold" aria-hidden="true" />
          Tạo đề mới
        </Link>
      </div>
      <ComingSoon step="4.3">
        Hai danh sách: đề bạn đã tạo (nháp, đã xuất bản — sửa, xem bài nộp, chia sẻ link) và các bài bạn đã làm
        (điểm, trạng thái chấm).
      </ComingSoon>
    </Page>
  )
}

export function NewExamPage() {
  return (
    <Page title="Tạo đề mới" back={TO_EXAMS}>
      <ComingSoon step="4.4">
        Chọn chương, số câu trắc nghiệm và tự luận → AI tạo đề nháp từ giáo trình (10–30 giây) → mở trình sửa đề.
        Hoặc tạo đề trống để tự soạn.
      </ComingSoon>
    </Page>
  )
}

export function EditExamPage() {
  const id = useNumericId()
  if (id == null) return <NotFoundPage />
  return (
    <Page title={`Sửa đề #${id}`} back={TO_EXAMS}>
      <ComingSoon step="4.5">
        Sửa câu hỏi, lựa chọn, đáp án, rubric; đặt chế độ chia sẻ, thời gian làm bài; xuất bản.
      </ComingSoon>
      <p className="page-links">
        <Link to={`/exams/${id}/take`}>Làm thử đề này</Link> · <Link to={`/exams/${id}/results`}>Xem bài nộp</Link>
      </p>
    </Page>
  )
}

export function TakeExamPage() {
  const id = useNumericId()
  if (id == null) return <NotFoundPage />
  return (
    <Page title={`Làm bài #${id}`} back={TO_EXAMS}>
      <ComingSoon step="4.6">
        Từng câu một, có đồng hồ đếm ngược; câu trả lời được lưu ngay khi chọn / gõ; nộp bài.
      </ComingSoon>
    </Page>
  )
}

export function SubmissionPage() {
  const id = useNumericId()
  if (id == null) return <NotFoundPage />
  return (
    <Page title={`Kết quả bài làm #${id}`} back={TO_EXAMS}>
      <ComingSoon step="4.7">
        Điểm, đáp án đúng, giải thích, nhận xét của AI cho câu tự luận, trích trang giáo trình. Hiện "AI đang chấm…"
        và tự cập nhật trong lúc bài còn đang chấm.
      </ComingSoon>
    </Page>
  )
}

export function ExamResultsPage() {
  const id = useNumericId()
  if (id == null) return <NotFoundPage />
  return (
    <Page title={`Bài nộp của đề #${id}`} back={{ to: `/exams/${id}/edit`, label: `Đề #${id}` }}>
      <ComingSoon step="4.8">
        Danh sách người đã làm và điểm; mở từng bài để xem câu trả lời, nhận xét của AI và sửa điểm.
      </ComingSoon>
    </Page>
  )
}

export function NotFoundPage() {
  return (
    <Page title="Không tìm thấy trang">
      <p className="muted">Đường dẫn không đúng, hoặc trang này không còn nữa.</p>
      <p className="page-links">
        <Link to="/chat">Về trang hỏi giáo trình</Link> · <Link to="/exams">Bài kiểm tra của tôi</Link>
      </p>
    </Page>
  )
}
