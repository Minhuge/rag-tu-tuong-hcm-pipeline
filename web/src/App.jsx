import { ChatCircleText, Exam, SignOut } from '@phosphor-icons/react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router'
import ChatPage from './pages/ChatPage.jsx'
import {
  EditExamPage, ExamResultsPage, MyExamsPage, NewExamPage, NotFoundPage, SubmissionPage, TakeExamPage,
} from './pages/ExamPages.jsx'

/**
 * Khung sau khi đăng nhập (roadmap 4.1): thanh điều hướng Chat | Bài kiểm tra + tài khoản, bên dưới là trang.
 *
 *   /chat                hỏi giáo trình                  /exams/:id/take      làm bài
 *   /exams               bài kiểm tra của tôi             /submissions/:id     kết quả bài làm
 *   /exams/new           tạo đề                           /exams/:id/results   người tạo đề xem bài nộp
 *   /exams/:id/edit      sửa đề
 *
 * Các URL này trùng tiền tố với API (/chat, /exams, /submissions): trình duyệt mở trang thì gửi
 * Accept: text/html → máy chủ / Vite trả index.html; fetch của web thì đi tới API (app/main.py, vite.config.js).
 */
const TABS = [
  { to: '/chat', label: 'Hỏi giáo trình', short: 'Chat', icon: ChatCircleText, match: ['/chat'] },
  { to: '/exams', label: 'Bài kiểm tra', short: 'Kiểm tra', icon: Exam, match: ['/exams', '/submissions'] },
]

function NavTabs() {
  const { pathname } = useLocation()
  return (
    <nav className="nav-tabs" aria-label="Chức năng chính">
      {TABS.map(({ to, label, short, icon: Icon, match }) => {
        const active = match.some((p) => pathname === p || pathname.startsWith(`${p}/`))
        return (
          // aria-current cả khi đang ở trang con (/exams/5/take, /submissions/3) — NavLink chỉ tự đặt khi khớp to
          <Link key={to} to={to} className={`nav-tab ${active ? 'active' : ''}`}
            aria-current={active ? 'page' : undefined}>
            <Icon size={17} weight={active ? 'fill' : 'regular'} aria-hidden="true" />
            <span className="label-long">{label}</span>
            <span className="label-short">{short}</span>
          </Link>
        )
      })}
    </nav>
  )
}

function Account({ user, onLogout }) {
  const name = user.display_name || user.email.split('@')[0]
  return (
    <div className="nav-account">
      <span className="account-avatar" aria-hidden="true">
        {(user.display_name || user.email).trim().charAt(0).toUpperCase()}
      </span>
      <span className="nav-account-name" title={user.email}>{name}</span>
      <button type="button" className="ghost icon-only account-out" onClick={onLogout} aria-label="Đăng xuất"
        title="Đăng xuất">
        <SignOut size={17} aria-hidden="true" />
      </button>
    </div>
  )
}

export default function App({ user, onLogout }) {
  return (
    <div className="shell">
      <a className="skip-link" href="#main">Bỏ qua thanh điều hướng</a>
      <header className="global-nav">
        <NavTabs />
        <Account user={user} onLogout={onLogout} />
      </header>
      <div className="shell-body">
        <Routes>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatPage user={user} />} />
          <Route path="exams" element={<MyExamsPage />} />
          <Route path="exams/new" element={<NewExamPage />} />
          <Route path="exams/:id/edit" element={<EditExamPage />} />
          <Route path="exams/:id/take" element={<TakeExamPage />} />
          <Route path="exams/:id/results" element={<ExamResultsPage />} />
          <Route path="submissions/:id" element={<SubmissionPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </div>
    </div>
  )
}
