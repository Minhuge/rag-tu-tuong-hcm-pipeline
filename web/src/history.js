// Lịch sử chat nằm trên máy chủ (PostgreSQL, xem db.py), theo tài khoản. Trình duyệt chỉ nhớ đang mở
// cuộc trò chuyện nào, để F5 mở lại đúng chỗ — nhớ riêng cho từng tài khoản. Đọc/ghi localStorage lỗi thì bỏ qua.
const activeKey = (userId) => `hcm-chat-active-id:${userId}`

export function loadActiveId(userId) {
  try {
    const id = Number(localStorage.getItem(activeKey(userId)))
    return Number.isInteger(id) && id > 0 ? id : null
  } catch {
    return null
  }
}

export function saveActiveId(userId, id) {
  try {
    if (id) localStorage.setItem(activeKey(userId), String(id))
    else localStorage.removeItem(activeKey(userId))
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
