import { ArrowLeft } from '@phosphor-icons/react'
import { useEffect, useRef } from 'react'
import { Link } from 'react-router'

/** Khung dùng chung cho các trang bài kiểm tra (Page) và trang "không tìm thấy". Mỗi trang ở file riêng trong pages/. */

/**
 * Khung chung: tiêu đề tab, đưa focus tới tiêu đề khi mở trang (trình đọc màn hình biết đã sang trang mới).
 * Chỉ focus lúc mở, không phải mỗi lần title đổi (vd. tên đề vừa tải xong) → không giật focus khỏi ô đang gõ.
 * className thêm vào <main> (vd. "page-wide"); heading thay nội dung <h1> khi cần (mặc định = title).
 */
export function Page({ title, back, children, className = '', heading }) {
  const headingRef = useRef(null)
  useEffect(() => {
    document.title = `${title} · Trợ lý Tư tưởng HCM`
  }, [title])
  useEffect(() => {
    headingRef.current?.focus()
  }, [])
  return (
    <main className={`page ${className}`} id="main">
      {back && (
        <Link className="back-link" to={back.to}>
          <ArrowLeft size={14} aria-hidden="true" />
          {back.label}
        </Link>
      )}
      <h1 ref={headingRef} tabIndex={-1}>{heading ?? title}</h1>
      {children}
    </main>
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
