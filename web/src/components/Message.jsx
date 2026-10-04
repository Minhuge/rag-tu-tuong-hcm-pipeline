import { ArrowClockwise, Check, Minus, Star, Warning, WarningCircle, X } from '@phosphor-icons/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { BLOCK_LABEL, buildTrace, formatSeconds, visibleSources } from '../trace.js'

// [Chương II, trang 45] → link nội bộ để render thành chip bấm được.
const CITE_RE = /\[(Chương[^\]]+)\]/g
const CITE_PREFIX = '#cite:'
const LONG_SOURCE = 320 // đoạn nguồn dài hơn → thu gọn, có nút "Xem thêm"

// Trạng thái không chỉ bằng màu: kèm icon (mắt thường) và chữ (trình đọc màn hình).
const STATUS = {
  ok: { Icon: Check, word: 'qua' },
  warn: { Icon: Warning, word: 'cảnh báo' },
  block: { Icon: X, word: 'chặn' },
  skip: { Icon: Minus, word: 'không chạy' },
}

const prefersReducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const scrollBehavior = () => (prefersReducedMotion() ? 'auto' : 'smooth')

function linkCitations(text) {
  return text.replace(CITE_RE, (_, label) => `[${label}](${CITE_PREFIX}${encodeURIComponent(label)})`)
}

// Chip "Chương V, trang 83" khớp nguồn nào? Ưu tiên khớp cả trang, sau đó chỉ chương.
function findSource(label, sources) {
  const exact = sources.findIndex((s) => label.includes(s.source))
  if (exact >= 0) return exact
  return sources.findIndex((s) => s.chapter && label.includes(s.chapter))
}

export function UserMessage({ text }) {
  return (
    <div className="msg msg-user">
      <div className="bubble">
        <span className="sr-only">Bạn hỏi: </span>
        {text}
      </div>
    </div>
  )
}

function Avatar({ error = false }) {
  return (
    <div className={`avatar ${error ? 'avatar-err' : ''}`} aria-hidden="true">
      {error ? <WarningCircle size={18} weight="bold" /> : <Star size={16} weight="fill" />}
    </div>
  )
}

function SourceItem({ source, flash, itemRef }) {
  const [expanded, setExpanded] = useState(false)
  const long = (source.text || '').length > LONG_SOURCE
  return (
    <li ref={itemRef} tabIndex={-1} className={flash ? 'flash' : ''}>
      <div className="src-head">
        <span className="src-label">{source.source}</span>
        <span className="score">
          <span className="score-bar" aria-hidden="true">
            <span style={{ width: `${Math.round(source.rerank_score * 100)}%` }} />
          </span>
          <span className="sr-only">Mức liên quan </span>
          {source.rerank_score.toFixed(2)}
        </span>
      </div>
      {source.text && (
        <p className={`src-text ${long && !expanded ? 'clamped' : ''}`}>{source.text}</p>
      )}
      {long && (
        <button type="button" className="more-btn" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'Thu gọn' : 'Xem thêm'}
        </button>
      )}
    </li>
  )
}

export function AssistantMessage({ result }) {
  const sources = useMemo(() => visibleSources(result), [result])
  const [showSources, setShowSources] = useState(false)
  const [showTrace, setShowTrace] = useState(false)
  const [highlight, setHighlight] = useState(null)
  const sourceRefs = useRef([])
  const trace = useMemo(() => buildTrace(result), [result])
  const blocked = result.blocked_by

  const traceSummary = 'Các lớp kiểm tra: ' + trace.map((s) => `${s.id} ${STATUS[s.status].word}`).join(', ')

  const openSource = (label) => {
    const i = findSource(label, sources)
    setShowSources(true)
    setHighlight(i)
    // đợi panel mở rồi mới cuộn; chuyển focus để người dùng bàn phím đọc tiếp được đoạn nguồn
    requestAnimationFrame(() => {
      const el = sourceRefs.current[i]
      el?.focus({ preventScroll: true })
      el?.scrollIntoView({ behavior: scrollBehavior(), block: 'nearest' })
    })
  }

  useEffect(() => {
    if (highlight == null) return
    const id = setTimeout(() => setHighlight(null), 2200)
    return () => clearTimeout(id)
  }, [highlight])

  const components = {
    a({ href, children }) {
      if (href?.startsWith(CITE_PREFIX)) {
        const label = decodeURIComponent(href.slice(CITE_PREFIX.length))
        if (findSource(label, sources) < 0) {
          // Không khớp nguồn nào → không làm nút (bấm cũng không có gì), hiện rõ bằng icon + chữ.
          return (
            <span className="cite cite-unknown" title="Không tìm thấy đoạn tương ứng trong nguồn">
              <Warning size={12} weight="bold" aria-hidden="true" />
              {children}
              <span className="sr-only"> (không khớp nguồn nào)</span>
            </span>
          )
        }
        return (
          <button type="button" className="cite" onClick={() => openSource(label)}
            aria-label={`${label}, xem đoạn giáo trình`}>
            {children}
          </button>
        )
      }
      return <a href={href} target="_blank" rel="noreferrer">{children}</a>
    },
  }

  return (
    <div className="msg msg-ai">
      <Avatar />
      <div className="card">
        <span className="sr-only">Trợ lý trả lời: </span>
        {blocked && (
          <div className={`banner banner-${blocked === 'retrieval' ? 'info' : 'warn'}`}>
            <Warning size={14} weight="bold" aria-hidden="true" />
            {BLOCK_LABEL[blocked] || blocked}
          </div>
        )}
        {result.input_policy === 'strict' && !blocked && (
          <div className="banner banner-info">
            <Warning size={14} weight="bold" aria-hidden="true" />
            Chế độ nghiêm: chỉ diễn đạt lại đúng nội dung giáo trình
          </div>
        )}

        <div className="answer">
          <Markdown components={components}>{linkCitations(result.answer || '')}</Markdown>
        </div>

        <div className="trace-row">
          <button type="button" className="trace-dots" onClick={() => setShowTrace((v) => !v)}
            aria-expanded={showTrace} aria-label={`${traceSummary}. ${showTrace ? 'Ẩn' : 'Xem'} chi tiết`}>
            {trace.map((s) => {
              const { Icon } = STATUS[s.status]
              return (
                <span key={s.id} className={`dot dot-${s.status}`} aria-hidden="true">
                  <Icon size={10} weight="bold" />
                  {s.id}
                </span>
              )
            })}
            <span className="chev" aria-hidden="true">{showTrace ? 'Ẩn các lớp' : 'Các lớp kiểm tra'}</span>
          </button>
          {result.timings?.total_s != null && (
            <span className="meta">
              <span className="sr-only">Thời gian </span>
              {formatSeconds(result.timings.total_s)}
            </span>
          )}
          {sources.length > 0 && (
            <button type="button" className="link-btn" onClick={() => setShowSources((v) => !v)} aria-expanded={showSources}>
              {showSources ? 'Ẩn nguồn' : `Nguồn (${sources.length})`}
            </button>
          )}
        </div>

        {showTrace && (
          <ol className="trace" aria-label="Chi tiết các lớp kiểm tra">
            {trace.map((s) => {
              const { Icon, word } = STATUS[s.status]
              return (
                <li key={s.id} className={`step step-${s.status}`}>
                  <span className="step-id">
                    <Icon size={12} weight="bold" aria-hidden="true" />
                    {s.id}
                  </span>
                  <span className="step-name">
                    {s.name}
                    <span className="sr-only">: {word}.</span>
                  </span>
                  <span className="step-detail">{s.detail}</span>
                  {s.time && <span className="step-time">{s.time}</span>}
                </li>
              )
            })}
          </ol>
        )}

        {showSources && (
          <ul className="sources" aria-label="Đoạn giáo trình được dùng làm nguồn">
            {sources.map((s, i) => (
              <SourceItem key={s.chunk_id} source={s} flash={highlight === i}
                itemRef={(el) => (sourceRefs.current[i] = el)} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

const HINTS = [
  'Đang kiểm tra an toàn câu hỏi…',
  'Đang tìm đoạn liên quan trong giáo trình…',
  'Đang chấm lại mức liên quan…',
  'Gemini đang soạn câu trả lời…',
  'Đang kiểm tra trích dẫn…',
]

export function PendingMessage({ startedAt }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(id)
  }, [])
  const elapsed = (now - startedAt) / 1000
  const hint = HINTS[Math.min(HINTS.length - 1, Math.floor(elapsed / 1.6))]
  return (
    <div className="msg msg-ai">
      <Avatar />
      <div className="card pending">
        {/* Gợi ý + đồng hồ đổi liên tục → ẩn với trình đọc màn hình, chỉ báo 1 câu cố định */}
        <span className="sr-only">Đang soạn câu trả lời, nhấn Esc để dừng.</span>
        <span className="typing" aria-hidden="true"><i /><i /><i /></span>
        <span className="pending-hint" aria-hidden="true">{hint}</span>
        <span className="meta" aria-hidden="true">{elapsed.toFixed(1)} s</span>
      </div>
    </div>
  )
}

export function ErrorMessage({ message, onRetry }) {
  return (
    <div className="msg msg-ai">
      <Avatar error />
      <div className="card card-err">
        <p><span className="sr-only">Lỗi: </span>{message}</p>
        {onRetry && (
          <button type="button" className="retry-btn" onClick={onRetry}>
            <ArrowClockwise size={14} weight="bold" aria-hidden="true" />
            Thử lại
          </button>
        )}
      </div>
    </div>
  )
}
