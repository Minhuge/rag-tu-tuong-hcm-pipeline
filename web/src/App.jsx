import { List, Plus, Star } from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { deleteConversation, getConversation, health, listConversations, streamChat, toSummary } from './api.js'
import Composer from './components/Composer.jsx'
import { AssistantMessage, ErrorMessage, StreamingMessage, UserMessage } from './components/Message.jsx'
import Sidebar from './components/Sidebar.jsx'
import { loadActiveId, markUnanswered, saveActiveId, toUiMessages } from './history.js'

const SUGGESTIONS = [
  'Quan điểm của Hồ Chí Minh về đại đoàn kết toàn dân tộc?',
  'Nguồn gốc và tiền đề hình thành tư tưởng Hồ Chí Minh là gì?',
  'Hồ Chí Minh quan niệm thế nào về vai trò lãnh đạo của Đảng Cộng sản?',
  'Đối tượng và phương pháp nghiên cứu của môn học là gì?',
]

const UNDO_MS = 6000
const NO_MESSAGES = [] // hằng số → tham chiếu không đổi giữa các lần render
// Khoá tạm cho "Cuộc trò chuyện mới": máy chủ chỉ tạo id khi nhận câu hỏi đầu tiên (sự kiện conversation)
const DRAFT = 'draft'

let nextId = Date.now()
const uid = () => ++nextId

const byNewest = (a, b) => b.updatedAt - a.updatedAt

// Đưa cuộc trò chuyện vừa có tin mới lên đầu danh sách
function bump(conversations, id) {
  const c = conversations.find((x) => x.id === id)
  return c ? [{ ...c, updatedAt: Date.now() }, ...conversations.filter((x) => x.id !== id)] : conversations
}

function useIsNarrow(query = '(max-width: 900px)') {
  const [narrow, setNarrow] = useState(() => window.matchMedia?.(query).matches ?? false)
  useEffect(() => {
    const mq = window.matchMedia?.(query)
    if (!mq) return
    const onChange = (e) => setNarrow(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return narrow
}

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
  const [conversations, setConversations] = useState([]) // tóm tắt từ máy chủ: { id, title, createdAt, updatedAt }
  const [listState, setListState] = useState('loading') // loading | ok | error
  // null = "Cuộc trò chuyện mới" chưa có câu hỏi nào; F5 mở lại cuộc đang xem (nhớ trong localStorage)
  const [activeId, setActiveId] = useState(loadActiveId)
  const [threads, setThreads] = useState({}) // id (hoặc DRAFT) → tin nhắn đã tải, định dạng giao diện
  const [threadError, setThreadError] = useState(null)
  const [pending, setPending] = useState(null) // { key, startedAt, controller, step, text }
  const [undo, setUndo] = useState(null) // { conversation, wasActive }: đã ẩn, chờ hết giờ mới xoá trên máy chủ
  const [drawerOpen, setDrawerOpen] = useState(false)
  const narrow = useIsNarrow()
  const bottomRef = useRef(null)
  const menuRef = useRef(null)
  const undoRef = useRef(null)

  const key = activeId ?? DRAFT
  const messages = threads[key] ?? NO_MESSAGES
  // Câu hỏi đang chờ có thể thuộc cuộc trò chuyện khác (đã bấm sang mục khác trong lúc chờ)
  const pendingHere = !!pending && pending.key === key
  const loadingThread = activeId != null && threads[activeId] === undefined && !threadError
  const display = useMemo(() => (pendingHere ? messages : markUnanswered(messages)), [messages, pendingHere])

  // ---------- tải từ máy chủ ----------
  const fetchList = useCallback(
    () =>
      listConversations()
        .then((cs) => {
          setConversations(cs)
          setListState('ok')
        })
        .catch(() => setListState('error')),
    [],
  )

  useEffect(() => {
    fetchList()
  }, [fetchList])

  const reloadList = () => {
    setListState('loading')
    fetchList()
  }

  useEffect(() => {
    if (activeId == null || threads[activeId] !== undefined || threadError) return
    let alive = true
    getConversation(activeId)
      .then((c) => alive && setThreads((t) => ({ ...t, [activeId]: toUiMessages(c.messages) })))
      .catch((e) => {
        if (!alive) return
        if (e.status === 404) setActiveId(null) // đã bị xoá / mã cũ → về màn hình mới
        else setThreadError(e.message)
      })
    return () => {
      alive = false
    }
  }, [activeId, threads, threadError])

  useEffect(() => saveActiveId(activeId), [activeId])

  useEffect(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    // Đang hiện chữ dần → cuộn tức thì, tránh hiệu ứng cuộn chồng lên nhau
    const behavior = reduce || pending?.text ? 'auto' : 'smooth'
    bottomRef.current?.scrollIntoView({ behavior, block: 'end' })
  }, [display, pendingHere, pending?.text])

  // ---------- xoá có hoàn tác ----------
  // Ẩn ngay khỏi danh sách, đợi UNDO_MS mới xoá trên máy chủ. Xoá lỗi → hiện lại.
  const commitDelete = useCallback((u, options) => {
    deleteConversation(u.conversation.id, options).catch(() =>
      setConversations((cs) => [...cs, u.conversation].sort(byNewest)),
    )
  }, [])

  useEffect(() => {
    undoRef.current = undo
    if (!undo) return
    const id = setTimeout(() => {
      commitDelete(undo)
      setUndo(null)
    }, UNDO_MS)
    return () => clearTimeout(id)
  }, [undo, commitDelete])

  // Đóng / tải lại trang khi còn đang chờ hoàn tác → vẫn xoá (keepalive gửi được cả khi trang đang đóng)
  useEffect(() => {
    const onHide = () => undoRef.current && commitDelete(undoRef.current, { keepalive: true })
    window.addEventListener('pagehide', onHide)
    return () => window.removeEventListener('pagehide', onHide)
  }, [commitDelete])

  const remove = (id) => {
    const conversation = conversations.find((c) => c.id === id)
    if (!conversation) return
    if (pending?.key === id) pending.controller.abort()
    if (undo) commitDelete(undo) // đang chờ hoàn tác mục khác → xoá luôn mục đó
    setUndo({ conversation, wasActive: id === activeId })
    setConversations((cs) => cs.filter((c) => c.id !== id))
    if (id === activeId) setActiveId(null)
  }

  const restore = () => {
    setConversations((cs) => [...cs, undo.conversation].sort(byNewest))
    if (undo.wasActive) setActiveId(undo.conversation.id)
    setUndo(null)
  }

  // ---------- gửi câu hỏi: 1 lưu tin user → 2 đọc lịch sử → 3 stream → 4 lưu câu trả lời (ở máy chủ) ----------
  const send = useCallback(async (question, { retry = false } = {}) => {
    const controller = new AbortController()
    let k = activeId ?? DRAFT // đổi sang id thật khi máy chủ tạo xong cuộc trò chuyện mới
    let finished = false
    const append = (msg) => setThreads((t) => ({ ...t, [k]: [...(t[k] ?? []), msg] }))

    setThreads((t) => {
      const base = t[k] ?? []
      // Thử lại: bỏ thông báo lỗi cuối, giữ câu hỏi cũ (máy chủ cũng không lưu trùng)
      const trimmed = retry && base.at(-1)?.role === 'error' ? base.slice(0, -1) : base
      return { ...t, [k]: retry ? trimmed : [...trimmed, { id: uid(), role: 'user', text: question }] }
    })
    setPending({ key: k, startedAt: Date.now(), controller, step: 'guard', text: '' })

    const onEvent = (ev) => {
      if (ev.type === 'conversation') {
        const summary = toSummary(ev)
        if (k === DRAFT) {
          const id = summary.id
          setThreads(({ [DRAFT]: draft = [], ...rest }) => ({ ...rest, [id]: draft }))
          setActiveId((a) => (a === null ? id : a)) // chỉ chuyển nếu người dùng vẫn đang ở màn hình mới
          setPending((p) => p && { ...p, key: id })
          k = id
        }
        setConversations((cs) => [summary, ...cs.filter((c) => c.id !== summary.id)])
      } else if (ev.type === 'status') {
        setPending((p) => p && { ...p, step: ev.step })
      } else if (ev.type === 'delta') {
        setPending((p) => p && { ...p, text: p.text + ev.text })
      } else if (ev.type === 'done') {
        finished = true
        append({ id: ev.message_id ?? uid(), role: 'ai', result: ev.result })
        setConversations((cs) => bump(cs, k))
      } else if (ev.type === 'error') {
        finished = true
        append({ id: uid(), role: 'error', message: ev.message, question })
      }
    }

    try {
      await streamChat({ conversationId: activeId, message: question, retry, signal: controller.signal, onEvent })
      if (!finished) append({ id: uid(), role: 'error', message: 'Mất kết nối với máy chủ giữa chừng.', question })
    } catch (e) {
      const message = e.name === 'AbortError' ? 'Đã dừng câu hỏi này.' : e.message
      append({ id: uid(), role: 'error', message, question })
    } finally {
      setPending(null)
    }
  }, [activeId])

  // ---------- điều hướng ----------
  const closeDrawer = () => {
    setDrawerOpen(false)
    menuRef.current?.focus()
  }

  const startNew = () => {
    // Bản nháp cũ (gửi lỗi trước khi máy chủ tạo được cuộc trò chuyện) → bỏ, trừ khi đang chờ trả lời
    if (pending?.key !== DRAFT) {
      setThreads((t) => {
        const next = { ...t }
        delete next[DRAFT]
        return next
      })
    }
    setActiveId(null)
    setThreadError(null)
    setDrawerOpen(false)
  }

  const select = (id) => {
    setActiveId(id)
    setThreadError(null)
    setDrawerOpen(false)
  }

  const empty = display.length === 0 && !pendingHere && !loadingThread && !threadError

  return (
    <div className="layout">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        pendingId={pending?.key}
        listState={listState}
        onReload={reloadList}
        onSelect={select}
        onNew={startNew}
        onDelete={remove}
        narrow={narrow}
        open={narrow && drawerOpen}
        onClose={closeDrawer}
      />
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <button ref={menuRef} type="button" className="ghost icon-only only-narrow" onClick={() => setDrawerOpen(true)}
              aria-label="Mở lịch sử trò chuyện" aria-expanded={narrow && drawerOpen} aria-controls="history">
              <List size={18} aria-hidden="true" />
            </button>
            <span className="logo" aria-hidden="true"><Star size={20} weight="fill" /></span>
            <div>
              <h1>Trợ lý Tư tưởng Hồ Chí Minh</h1>
              <p>Trả lời dựa trên giáo trình, có trích dẫn chương và trang</p>
            </div>
          </div>
          <div className="top-actions">
            <HealthPill />
            {display.length > 0 && (
              <button type="button" className="ghost only-narrow" onClick={startNew} aria-label="Cuộc trò chuyện mới">
                <Plus size={14} weight="bold" aria-hidden="true" />
                <span className="label-long" aria-hidden="true">Cuộc trò chuyện mới</span>
                <span className="label-short" aria-hidden="true">Mới</span>
              </button>
            )}
          </div>
        </header>

        <main className="chat" id="main">
          {loadingThread && <p className="thread-status" role="status">Đang tải cuộc trò chuyện…</p>}
          {threadError && (
            <ErrorMessage message={`Không tải được cuộc trò chuyện: ${threadError}`}
              onRetry={() => setThreadError(null)} />
          )}
          {empty ? (
            <section className="welcome">
              <h2>Bạn muốn ôn phần nào?</h2>
              <p>
                Mỗi câu hỏi đi qua 6 lớp kiểm tra: lọc đầu vào, Qwen3Guard, kiểm tra tài liệu liên quan,
                Gemini trả lời chỉ từ giáo trình, kiểm tra trích dẫn và giám khảo khi cần.
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((q) => (
                  <button key={q} type="button" onClick={() => send(q)} disabled={!!pending}>{q}</button>
                ))}
              </div>
            </section>
          ) : (
            // role=log: tin nhắn mới được trình đọc màn hình đọc lần lượt, không cần di chuyển focus
            <div className="thread" role="log" aria-live="polite" aria-relevant="additions" aria-label="Cuộc trò chuyện">
              {display.map((m, i) => {
                if (m.role === 'user') return <UserMessage key={m.id} text={m.text} />
                if (m.role === 'ai') return <AssistantMessage key={m.id} result={m.result} />
                const isLast = i === display.length - 1
                return (
                  <ErrorMessage key={m.id} message={m.message}
                    onRetry={isLast && !pending ? () => send(m.question, { retry: true }) : null} />
                )
              })}
              {pendingHere && <StreamingMessage startedAt={pending.startedAt} step={pending.step} text={pending.text} />}
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
          {/* Máy chủ trả lời từng câu một → đang chờ ở cuộc trò chuyện khác thì ô nhập cũng phải đợi */}
          <Composer onSend={send} disabled={!!pending} onStop={() => pending?.controller.abort()} />
          <p className="disclaimer">
            {pending && !pendingHere
              ? 'Đang chờ trả lời ở một cuộc trò chuyện khác — mở lại mục đó để xem.'
              : 'Trợ lý nhớ 2 lượt hỏi–đáp gần nhất để hiểu câu hỏi nối tiếp. Mọi ý trả lời chỉ lấy từ giáo trình — hãy đối chiếu với phần nguồn.'}
          </p>
        </footer>
      </div>
    </div>
  )
}
