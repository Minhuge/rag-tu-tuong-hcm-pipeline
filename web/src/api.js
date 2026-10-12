// Gọi FastAPI (api.py). Dev đi qua Vite proxy, bản build được FastAPI phục vụ cùng origin.
const BASE = import.meta.env.VITE_API_URL || ''

/**
 * Lỗi gọi API. Ngoài message (tiếng Việt, đưa thẳng lên giao diện) còn có:
 *   status      mã HTTP; 0 = không tới được máy chủ
 *   fields      lỗi từng trường từ 422, vd. { title: 'Không được để trống', 'options.1.content': '…' }
 *               → giao diện hiện lỗi ngay cạnh ô nhập tương ứng
 *   question    số thứ tự câu bị lỗi (1, 2, …) khi máy chủ báo "Câu 3: …" (publish) → cuộn tới câu đó
 *   retryable   lỗi tạm thời (mất mạng, máy chủ đang khởi động, Gemini lỗi) → hiện nút "Thử lại"
 */
export class ApiError extends Error {
  constructor(message, status, { fields = {}, question = null } = {}) {
    super(message)
    this.status = status
    this.fields = fields
    this.question = question
    this.retryable = status === 0 || status >= 500
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
    if (e.name === 'AbortError') throw e // người dùng huỷ (rời trang, bấm dừng): không phải lỗi
    if (e.name === 'TimeoutError') throw new ApiError('Máy chủ phản hồi quá lâu. Thử lại sau ít giây.', 0)
    throw new ApiError('Không kết nối được máy chủ. Kiểm tra mạng, hoặc uvicorn app.main:app đã chạy chưa.', 0)
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

// Tên trường (theo schema API) → nhãn hiện cho người dùng, dùng trong thông báo lỗi 422
const FIELD_LABELS = {
  email: 'Email', password: 'Mật khẩu', display_name: 'Tên hiển thị', title: 'Tiêu đề', description: 'Mô tả',
  duration_minutes: 'Thời gian làm bài', open_at: 'Giờ mở đề', close_at: 'Giờ đóng đề', content: 'Nội dung',
  points: 'Điểm', rubric: 'Barem', explanation: 'Giải thích', source_page: 'Trang nguồn', options: 'Lựa chọn',
  label: 'Nhãn lựa chọn', score: 'Điểm', feedback: 'Nhận xét', n_mcq: 'Số câu trắc nghiệm', n_essay: 'Số câu tự luận',
  chapter: 'Chương', essay_text: 'Bài làm', message: 'Câu hỏi',
}

// Lỗi kiểm tra dữ liệu của FastAPI / Pydantic (tiếng Anh) → tiếng Việt
function fieldMessage({ type, ctx = {}, msg }) {
  switch (type) {
    case 'missing': return 'Không được để trống'
    case 'string_too_short': return ctx.min_length > 1 ? `Cần ít nhất ${ctx.min_length} ký tự` : 'Không được để trống'
    case 'string_too_long': return `Tối đa ${ctx.max_length} ký tự`
    case 'too_long': return `Tối đa ${ctx.max_length} mục`
    case 'greater_than_equal': return `Phải từ ${ctx.ge} trở lên`
    case 'greater_than': return `Phải lớn hơn ${ctx.gt}`
    case 'less_than_equal': return `Tối đa ${ctx.le}`
    case 'int_parsing': case 'int_type': case 'int_from_float': return 'Phải là số nguyên'
    case 'float_parsing': case 'float_type': return 'Phải là số'
    case 'literal_error': return `Chỉ nhận: ${ctx.expected}`
    case 'value_error': return msg.replace(/^Value error, /, '')
    default: return msg
  }
}

/** detail của 422: [{loc: ['body', 'options', 1, 'content'], type, msg, ctx}] → { 'options.1.content': '…' } */
function fieldErrors(detail) {
  const fields = {}
  for (const err of Array.isArray(detail) ? detail : []) {
    const path = (err.loc ?? []).filter((p, i) => !(i === 0 && ['body', 'query', 'path'].includes(p))).join('.')
    fields[path] ??= fieldMessage(err)
  }
  return fields
}

const FALLBACK = {
  400: 'Dữ liệu chưa hợp lệ.',
  401: 'Phiên đăng nhập đã hết, hãy đăng nhập lại.',
  403: 'Bạn không có quyền làm việc này.',
  404: 'Không tìm thấy (có thể đã bị xoá hoặc bạn không có quyền xem).',
  409: 'Không làm được ở trạng thái hiện tại — tải lại trang để xem dữ liệu mới nhất.',
  422: 'Dữ liệu gửi lên không hợp lệ.',
  429: 'Bạn thao tác quá nhiều — đợi một lát rồi thử lại.',
  500: 'Máy chủ gặp lỗi hoặc chưa khởi động xong. Thử lại sau ít giây.',
  502: 'Máy chủ chưa sẵn sàng hoặc lỗi khi gọi Gemini. Thử lại sau ít giây.',
  503: 'Máy chủ đang bận hoặc đang khởi động. Thử lại sau ít giây.',
  504: 'Máy chủ phản hồi quá lâu. Thử lại sau ít giây.',
}

async function errorFrom(res) {
  let body = null
  try {
    body = await res.json()
  } catch {
    // Vite proxy trả HTML/rỗng khi backend chưa bật xong
  }
  const detail = body?.detail
  if (Array.isArray(detail)) {
    const fields = fieldErrors(detail)
    const [path, text] = Object.entries(fields)[0] ?? []
    const label = FIELD_LABELS[path?.split('.').filter((p) => Number.isNaN(Number(p))).at(-1)]
    return new ApiError(text ? `${label ?? 'Dữ liệu'}: ${text}` : FALLBACK[422], res.status, { fields })
  }
  const message = typeof detail === 'string' ? detail : null
  const question = Number(message?.match(/^Câu (\d+):/)?.[1]) || null // lỗi publish: "Câu 3: cần đúng một đáp án đúng"
  return new ApiError(message || FALLBACK[res.status] || `Lỗi HTTP ${res.status}`, res.status, { question })
}

/** Gọi API (tự làm mới phiên khi gặp 401), trả JSON; lỗi → ApiError. 204 → null. */
export async function request(path, options = {}) {
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
