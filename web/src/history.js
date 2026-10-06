// Lịch sử chat giờ nằm trên máy chủ (SQLite chat.db, xem db.py). Trình duyệt chỉ nhớ đang mở
// cuộc trò chuyện nào, để F5 mở lại đúng chỗ. Đọc/ghi localStorage lỗi thì bỏ qua.
const ACTIVE_KEY = 'hcm-chat-active-id'

export function loadActiveId() {
  try {
    const id = Number(localStorage.getItem(ACTIVE_KEY))
    return Number.isInteger(id) && id > 0 ? id : null
  } catch {
    return null
  }
}

export function saveActiveId(id) {
  try {
    if (id) localStorage.setItem(ACTIVE_KEY, String(id))
    else localStorage.removeItem(ACTIVE_KEY)
  } catch {
    // bỏ qua
  }
}

// Tin nhắn từ máy chủ {role: user|assistant, content, meta} → định dạng các component đang dùng
export function toUiMessages(messages) {
  return messages.map((m) =>
    m.role === 'user'
      ? { id: m.id, role: 'user', text: m.content }
      : { id: m.id, role: 'ai', result: { ...(m.meta || {}), answer: m.content } },
  )
}

// Câu hỏi cuối chưa có câu trả lời (lỗi, bị dừng, F5 khi đang chờ) → thêm thông báo có nút hỏi lại
export function markUnanswered(messages) {
  const last = messages.at(-1)
  if (last?.role !== 'user') return messages
  return [...messages, { id: `unanswered-${last.id}`, role: 'error', question: last.text,
    message: 'Câu hỏi này chưa có câu trả lời.' }]
}
