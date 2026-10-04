// Gọi FastAPI (api.py). Dev đi qua Vite proxy, bản build được FastAPI phục vụ cùng origin.
const BASE = import.meta.env.VITE_API_URL || ''

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(BASE + path, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError('Không kết nối được máy chủ. Kiểm tra uvicorn api:app đã chạy chưa.', 0)
  }

  let body = null
  try {
    body = await res.json()
  } catch {
    // Vite proxy trả HTML/rỗng khi backend chưa bật xong
  }
  if (!res.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : null
    const fallback = {
      502: 'Máy chủ chưa sẵn sàng hoặc lỗi khi gọi Gemini.',
      503: 'Model đang khởi động, thử lại sau ít giây.',
      500: 'Máy chủ gặp lỗi hoặc chưa khởi động xong.',
    }[res.status]
    throw new ApiError(detail || fallback || `Lỗi HTTP ${res.status}`, res.status)
  }
  return body
}

export const ask = (question, signal) =>
  request('/ask', { method: 'POST', body: JSON.stringify({ question }), signal })

export const health = () => request('/health')
