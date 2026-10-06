import { Plus, Trash, X } from '@phosphor-icons/react'
import { useEffect, useRef } from 'react'

const DAY = 86400000

// Nhóm theo thời điểm dùng gần nhất, giống các app chat quen thuộc
function bucket(ts, now = new Date()) {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (ts >= startOfToday) return 'Hôm nay'
  if (ts >= startOfToday - DAY) return 'Hôm qua'
  if (ts >= startOfToday - 7 * DAY) return '7 ngày qua'
  if (ts >= startOfToday - 30 * DAY) return '30 ngày qua'
  return 'Cũ hơn'
}

function groupByDay(conversations) {
  const groups = []
  for (const c of conversations) {
    const label = bucket(c.updatedAt)
    if (groups.at(-1)?.label !== label) groups.push({ label, items: [] })
    groups.at(-1).items.push(c)
  }
  return groups
}

/**
 * Cột lịch sử bên trái. Màn hình hẹp (≤ 900px): thành ngăn kéo trượt ra từ nút menu,
 * khi đóng thì `inert` để Tab không đi vào phần đang bị ẩn.
 */
export default function Sidebar({
  conversations, activeId, pendingId, listState, onReload, onSelect, onNew, onDelete, narrow, open, onClose,
}) {
  const newRef = useRef(null)
  const hidden = narrow && !open

  useEffect(() => {
    if (narrow && open) newRef.current?.focus()
  }, [narrow, open])

  return (
    <>
      {narrow && open && <div className="scrim" onClick={onClose} aria-hidden="true" />}
      <aside
        id="history"
        className={`sidebar ${open ? 'open' : ''}`}
        aria-label="Lịch sử trò chuyện"
        inert={hidden}
        onKeyDown={(e) => {
          // Không để Esc lan lên window: Composer dùng Esc để dừng câu hỏi đang chờ
          if (e.key === 'Escape' && narrow) {
            e.stopPropagation()
            onClose()
          }
        }}
      >
        <div className="sidebar-head">
          <button ref={newRef} type="button" className="new-chat" onClick={onNew}>
            <Plus size={15} weight="bold" aria-hidden="true" />
            Cuộc trò chuyện mới
          </button>
          {narrow && (
            <button type="button" className="ghost icon-only" onClick={onClose} aria-label="Đóng lịch sử">
              <X size={16} aria-hidden="true" />
            </button>
          )}
        </div>

        <nav className="history" aria-label="Các cuộc trò chuyện">
          {listState === 'error' && (
            <div className="history-empty" role="alert">
              Không tải được lịch sử từ máy chủ.{' '}
              <button type="button" className="link-btn" onClick={onReload}>Thử lại</button>
            </div>
          )}
          {listState === 'loading' && conversations.length === 0 ? (
            <p className="history-empty" role="status">Đang tải lịch sử…</p>
          ) : listState === 'ok' && conversations.length === 0 ? (
            <p className="history-empty">Chưa có cuộc trò chuyện nào. Câu hỏi đầu tiên sẽ xuất hiện ở đây.</p>
          ) : (
            groupByDay(conversations).map((g) => (
              <section key={g.label}>
                <h2>{g.label}</h2>
                <ul>
                  {g.items.map((c) => {
                    const current = c.id === activeId
                    return (
                      <li key={c.id} className={`history-item ${current ? 'active' : ''}`}>
                        <button
                          type="button"
                          className="history-open"
                          onClick={() => onSelect(c.id)}
                          aria-current={current ? 'page' : undefined}
                          title={c.title}
                        >
                          <span className="history-title">{c.title}</span>
                          {c.id === pendingId && <span className="history-busy">Đang trả lời…</span>}
                        </button>
                        <button
                          type="button"
                          className="history-del"
                          onClick={() => onDelete(c.id)}
                          aria-label={`Xoá cuộc trò chuyện: ${c.title}`}
                        >
                          <Trash size={15} aria-hidden="true" />
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </section>
            ))
          )}
        </nav>

        <p className="history-note">Lịch sử lưu trên máy chủ, gắn với trình duyệt này.</p>
      </aside>
    </>
  )
}
