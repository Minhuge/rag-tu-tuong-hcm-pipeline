// Gọi FastAPI (api.py). Dev đi qua Vite proxy, bản build được FastAPI phục vụ cùng origin.
const BASE = import.meta.env.VITE_API_URL || ''

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

// Mã ngẫu nhiên của trình duyệt này (không phải đăng nhập): máy chủ chỉ trả về lịch sử của mã này,
// nên người dùng chung mạng LAN không thấy cuộc trò chuyện của nhau. Xoá dữ liệu trình duyệt = mất lịch sử.
const CLIENT_KEY = 'hcm-client-id'
const CLIENT_RE = /^[A-Za-z0-9_-]{8,64}$/
// Không dùng crypto.randomUUID: hàm này chỉ có trên https/localhost, mở qua IP LAN (http) sẽ lỗi.
const randomId = () => 'b' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12)
let memoryClientId = null

function clientId() {
  try {
    let id = localStorage.getItem(CLIENT_KEY)
    if (!id || !CLIENT_RE.test(id)) {
      id = randomId()
      localStorage.setItem(CLIENT_KEY, id)
    }
    return id
  } catch {
    // chế độ ẩn danh chặn localStorage → dùng mã tạm cho phiên này
    memoryClientId ??= randomId()
    return memoryClientId
  }
}

const headers = (extra) => ({ 'Content-Type': 'application/json', 'X-Client-Id': clientId(), ...extra })

async function send(path, options) {
  try {
    return await fetch(BASE + path, { ...options, headers: headers(options.headers) })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError('Không kết nối được máy chủ. Kiểm tra uvicorn api:app đã chạy chưa.', 0)
  }
}

async function errorFrom(res) {
  let body = null
  try {
    body = await res.json()
  } catch {
    // Vite proxy trả HTML/rỗng khi backend chưa bật xong
  }
  const detail = typeof body?.detail === 'string' ? body.detail : null
  const fallback = {
    404: 'Không tìm thấy cuộc trò chuyện (có thể đã bị xoá).',
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
