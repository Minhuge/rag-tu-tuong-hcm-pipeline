import { Plus, Star } from '@phosphor-icons/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ask, health } from './api.js'
import Composer from './components/Composer.jsx'
import { AssistantMessage, ErrorMessage, PendingMessage, UserMessage } from './components/Message.jsx'

const STORAGE_KEY = 'hcm-chat-history-v1'

const SUGGESTIONS = [
  'Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?',
  'Nguồn gốc và tiền đề hình thành tư tưởng Hồ Chí Minh là gì?',
  'Hồ Chí Minh quan niệm thế nào về vai trò lãnh đạo của Đảng Cộng sản?',
  'Đối tượng và phương pháp nghiên cứu của môn học là gì?',
]

// Lịch sử chat lưu trong localStorage → F5 không mất. Chỉ là tiện ích trên trình duyệt này:
// mỗi origin (localhost:5173 khi dev, localhost:8000 bản build) có lịch sử riêng; đọc/ghi lỗi thì bỏ qua.
function loadHistory() {
  let messages = []
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    if (Array.isArray(parsed)) messages = parsed
  } catch {
    return []
  }
  // F5 khi đang chờ trả lời: câu hỏi đã lưu nhưng câu trả lời bị mất → cho phép hỏi lại.
  const last = messages.at(-1)
  if (last?.role === 'user') {
    messages.push({ id: Date.now(), role: 'error', question: last.text,
      message: 'Câu hỏi này chưa có câu trả lời vì trang đã được tải lại.' })
  }
  return messages
}

function saveHistory(messages) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-60)))
  } catch {
    // hết quota / chế độ ẩn danh
  }
}

const UNDO_MS = 6000

let nextId = Date.now()
const uid = () => ++nextId

function HealthPill() {
  const [state, setState] = useState({ status: 'loading' })

  useEffect(() => {
    let alive = true
    const check = () =>
      health()
        .then((h) => alive && setState({ status: 'ok', ...h }))
        .catch((e) => alive && setState({ status: 'down', message: e.message }))
    check()
    const id = setInterval(check, 20000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  // role=status: đổi trạng thái (mất kết nối / kết nối lại) được đọc lên mà không cướp focus
  if (state.status === 'loading')
    return <span className="pill pill-wait" role="status">Đang kết nối…</span>
  if (state.status === 'down')
    return <span className="pill pill-down" role="status" title={state.message}>Máy chủ chưa sẵn sàng</span>
  return (
    <span className="pill pill-ok" role="status"
      title={`Qdrant: ${state.collection} · reranker: ${state.reranker_device} · guard: ${state.guard_device}`}>
      <span className="sr-only">Máy chủ sẵn sàng: </span>
      {state.points} đoạn · {state.gemini_model}
    </span>
  )
}

export default function App() {
  const [messages, setMessages] = useState(loadHistory)
  const [pending, setPending] = useState(null) // { startedAt, controller }
  const [undo, setUndo] = useState(null) // tin nhắn vừa xoá, giữ tạm để hoàn tác
  const bottomRef = useRef(null)

  useEffect(() => saveHistory(messages), [messages])

  useEffect(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    bottomRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'end' })
  }, [messages, pending])

  useEffect(() => {
    if (!undo) return
    const id = setTimeout(() => setUndo(null), UNDO_MS)
    return () => clearTimeout(id)
  }, [undo])

  const send = useCallback(async (question, { retry = false } = {}) => {
    const controller = new AbortController()
    setUndo(null)
    setMessages((m) => {
      // Thử lại: bỏ thông báo lỗi cuối, giữ câu hỏi cũ
      const base = retry && m.at(-1)?.role === 'error' ? m.slice(0, -1) : m
      return retry ? base : [...base, { id: uid(), role: 'user', text: question }]
    })
    setPending({ startedAt: Date.now(), controller })
    try {
      const result = await ask(question, controller.signal)
      setMessages((m) => [...m, { id: uid(), role: 'ai', result }])
    } catch (e) {
      const message = e.name === 'AbortError' ? 'Đã dừng câu hỏi này.' : e.message
      setMessages((m) => [...m, { id: uid(), role: 'error', message, question }])
    } finally {
      setPending(null)
    }
  }, [])

  // Xoá cả cuộc trò chuyện là thao tác phá huỷ → cho hoàn tác trong vài giây thay vì mất luôn.
  const clear = () => {
    pending?.controller.abort()
    setUndo(messages)
    setMessages([])
  }

  const restore = () => {
    setMessages(undo)
    setUndo(null)
  }

  const empty = messages.length === 0 && !pending

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true"><Star size={20} weight="fill" /></span>
          <div>
            <h1>Trợ lý Tư tưởng Hồ Chí Minh</h1>
            <p>Trả lời dựa trên giáo trình, có trích dẫn chương và trang</p>
          </div>
        </div>
        <div className="top-actions">
          <HealthPill />
          {messages.length > 0 && (
            <button type="button" className="ghost" onClick={clear} aria-label="Cuộc trò chuyện mới">
              <Plus size={14} weight="bold" aria-hidden="true" />
              <span className="label-long" aria-hidden="true">Cuộc trò chuyện mới</span>
              <span className="label-short" aria-hidden="true">Mới</span>
            </button>
          )}
        </div>
      </header>

      <main className="chat" id="main">
        {empty ? (
          <section className="welcome">
            <h2>Bạn muốn ôn phần nào?</h2>
            <p>
              Mỗi câu hỏi đi qua 6 lớp kiểm tra: lọc đầu vào, Qwen3Guard, kiểm tra tài liệu liên quan,
              Gemini trả lời chỉ từ giáo trình, kiểm tra trích dẫn và giám khảo khi cần.
            </p>
            <div className="suggestions">
              {SUGGESTIONS.map((q) => (
                <button key={q} type="button" onClick={() => send(q)}>{q}</button>
              ))}
            </div>
          </section>
        ) : (
          // role=log: tin nhắn mới được trình đọc màn hình đọc lần lượt, không cần di chuyển focus
          <div className="thread" role="log" aria-live="polite" aria-relevant="additions" aria-label="Cuộc trò chuyện">
            {messages.map((m, i) => {
              if (m.role === 'user') return <UserMessage key={m.id} text={m.text} />
              if (m.role === 'ai') return <AssistantMessage key={m.id} result={m.result} />
              const isLast = i === messages.length - 1
              return (
                <ErrorMessage key={m.id} message={m.message}
                  onRetry={isLast && !pending ? () => send(m.question, { retry: true }) : null} />
              )
            })}
            {pending && <PendingMessage startedAt={pending.startedAt} />}
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      <footer className="dock">
        {undo && (
          <div className="toast" role="status">
            <span>Đã xoá cuộc trò chuyện.</span>
            <button type="button" onClick={restore}>Hoàn tác</button>
          </div>
        )}
        <Composer onSend={send} disabled={!!pending} onStop={() => pending?.controller.abort()} />
        <p className="disclaimer">Mỗi câu hỏi được trả lời độc lập, chỉ dựa trên giáo trình. Hãy đối chiếu với phần nguồn.</p>
      </footer>
    </div>
  )
}
