// Gọi FastAPI (api.py). Dev đi qua Vite proxy, bản build được FastAPI phục vụ cùng origin.
const BASE = import.meta.env.VITE_API_URL || ''

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

// ---------- đăng nhập (xem auth.py) ----------
// Access token và refresh token đều nằm trong cookie httpOnly do máy chủ đặt: JavaScript không đọc được
// (mã độc chèn vào trang không lấy được), trình duyệt tự gửi kèm mỗi request, F5 vẫn còn.
// Vì vậy file này không giữ token nào — chỉ cần gửi kèm cookie (credentials) và header chống CSRF.
let refreshing = null // request làm mới đang chạy, dùng chung cho mọi request cùng gặp 401
let onSessionExpired = () => {}

/** App đăng ký hàm này để quay về màn hình đăng nhập khi phiên hết hạn giữa chừng. */
export const setSessionExpiredHandler = (fn) => {
  onSessionExpired = fn
}

async function rawFetch(path, options = {}) {
  // X-Requested-With: máy chủ bắt buộc với POST/DELETE dùng cookie → trang web khác không giả mạo được
  const headers = { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch', ...options.headers }
  try {
    // include: gửi kèm cookie đăng nhập kể cả khi web và API khác origin (VITE_API_URL)
    return await fetch(BASE + path, { ...options, headers, credentials: 'include' })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError('Không kết nối được máy chủ. Kiểm tra uvicorn api:app đã chạy chưa.', 0)
  }
}

/** Đổi cookie refresh token lấy cặp cookie mới. Trả về user, hoặc null nếu phải đăng nhập lại. */
function refreshSession() {
  refreshing ??= rawFetch('/auth/refresh', { method: 'POST' })
    .then(async (res) => (res.ok ? (await res.json()).user : null))
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

// Access token sống 15 phút → hết hạn thì máy chủ trả 401: làm mới một lần rồi gửi lại.
// Body là chuỗi JSON nên gửi lại được, kể cả /chat (luồng chưa bắt đầu khi bị 401).
async function send(path, options) {
  const res = await rawFetch(path, options)
  if (res.status !== 401) return res
  const user = await refreshSession()
  if (!user) {
    onSessionExpired()
    return res
  }
  return rawFetch(path, options)
}

async function authRequest(path, body) {
  const res = await rawFetch(path, { method: 'POST', body: JSON.stringify(body) })
  if (!res.ok) throw await errorFrom(res)
  return (await res.json()).user // máy chủ đã đặt cookie; bỏ qua access_token trong JSON (dành cho /docs, curl)
}

export const login = (email, password) => authRequest('/auth/login', { email, password })

export const register = (email, password, displayName) =>
  authRequest('/auth/register', { email, password, display_name: displayName || null })

/**
 * Lúc mở trang: hỏi /auth/me bằng cookie. Access cookie đã hết hạn (quá 15 phút) → làm mới một lần.
 * Trả về user (đăng nhập sẵn) hoặc null (cần đăng nhập). Mất kết nối → ApiError.
 */
export async function restoreSession() {
  let res = await rawFetch('/auth/me')
  if (res.status === 401 && (await refreshSession())) res = await rawFetch('/auth/me')
  if (res.status === 401) return null
  if (!res.ok) throw await errorFrom(res)
  return res.json()
}

export const logout = () => rawFetch('/auth/logout', { method: 'POST' }) // máy chủ xoá cả hai cookie

async function errorFrom(res) {
  let body = null
  try {
    body = await res.json()
  } catch {
    // Vite proxy trả HTML/rỗng khi backend chưa bật xong
  }
  const detail = typeof body?.detail === 'string' ? body.detail : null
  const fallback = {
    401: 'Phiên đăng nhập đã hết, hãy đăng nhập lại.',
    404: 'Không tìm thấy cuộc trò chuyện (có thể đã bị xoá).',
    422: 'Dữ liệu gửi lên không hợp lệ.',
    502: 'Máy chủ chưa sẵn sàng hoặc lỗi khi gọi Gemini.',
    503: 'Model đang khởi động, thử lại sau ít giây.',
    500: 'Máy chủ gặp lỗi hoặc chưa khởi động xong.',
  }[res.status]
  return new ApiError(detail || fallback || `Lỗi HTTP ${res.status}`, res.status)
}

async function request(path, options = {}) {
  const res = await send(path, options)
  if (!res.ok) throw await errorFrom(res)
  if (res.status === 204) return null
  return res.json()
}

export const ask = (question, signal) =>
  request('/ask', { method: 'POST', body: JSON.stringify({ question }), signal })

export const health = () => request('/health')

// ---------- lịch sử chat (lưu trên máy chủ) ----------
export const toSummary = (c) => ({ id: c.id, title: c.title, createdAt: c.created_at, updatedAt: c.updated_at })

export const listConversations = async () => (await request('/conversations')).map(toSummary)

export const getConversation = (id) => request(`/conversations/${id}`)

// keepalive: vẫn gửi được khi trang đang đóng (xoá đang chờ hoàn tác lúc người dùng rời trang)
export const deleteConversation = (id, { keepalive = false } = {}) =>
  request(`/conversations/${id}`, { method: 'DELETE', keepalive })

/**
 * POST /chat, đọc luồng NDJSON (mỗi dòng một sự kiện) và gọi onEvent cho từng sự kiện:
 * conversation → status → delta… → done | error. Xem api.py để biết đủ các loại sự kiện.
 */
export async function streamChat({ conversationId, message, retry = false, signal, onEvent }) {
  const res = await send('/chat', {
    method: 'POST',
    body: JSON.stringify({ conversation_id: conversationId ?? null, message, retry }),
    signal,
  })
  if (!res.ok) throw await errorFrom(res)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const flush = (final = false) => {
    let nl
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, nl).trim()
      buffer = buffer.slice(nl + 1)
      if (line) onEvent(JSON.parse(line))
    }
    if (final && buffer.trim()) onEvent(JSON.parse(buffer))
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }) // stream: true → không cắt đôi ký tự tiếng Việt
    flush()
  }
  buffer += decoder.decode()
  flush(true)
}
