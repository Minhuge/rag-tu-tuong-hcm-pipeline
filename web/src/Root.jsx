import { Star } from '@phosphor-icons/react'
import { useEffect, useState } from 'react'
import { logout, restoreSession, setSessionExpiredHandler } from './api.js'
import App from './App.jsx'
import AuthPage from './components/AuthPage.jsx'

/**
 * Cổng đăng nhập: mở trang → hỏi /auth/me bằng cookie (hết hạn thì /auth/refresh).
 * Có phiên → vào App; không → màn hình đăng nhập. Phiên hết hạn giữa chừng → quay lại đây.
 */
export default function Root() {
  const [session, setSession] = useState({ status: 'checking' }) // checking | out | in

  useEffect(() => {
    let alive = true
    setSessionExpiredHandler(() =>
      setSession({ status: 'out', notice: 'Phiên đăng nhập đã hết, hãy đăng nhập lại.' }))
    restoreSession()
      .then((user) => alive && setSession(user ? { status: 'in', user } : { status: 'out' }))
      .catch((e) => alive && setSession({ status: 'out', notice: e.message }))
    return () => {
      alive = false
    }
  }, [])

  if (session.status === 'checking')
    return (
      <div className="auth-splash" role="status">
        <span className="logo" aria-hidden="true"><Star size={20} weight="fill" /></span>
        <span>Đang tải…</span>
      </div>
    )

  if (session.status === 'out')
    return <AuthPage notice={session.notice} onAuthed={(user) => setSession({ status: 'in', user })} />

  const signOut = async () => {
    try {
      await logout()
    } finally {
      setSession({ status: 'out' })
    }
  }

  // key: đổi người dùng → App dựng lại từ đầu, không giữ lịch sử của người trước
  return <App key={session.user.id} user={session.user} onLogout={signOut} />
}
