import { ArrowClockwise, CircleNotch, PencilSimple, Sparkle, Warning } from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { createExam, generateExam, getAiQuota, listChapters } from '../examsApi.js'
import { useLoad } from '../useLoad.js'
import { Page } from './ExamPages.jsx'

/**
 * Tạo đề mới (roadmap 4.4): AI soạn đề nháp từ một chương, hoặc tự soạn từ đề trống.
 * Cách tạo nằm trên URL (?mode=manual). Xong → trình sửa đề /exams/:id/edit (đề AI kèm báo cáo câu bị loại).
 */
const NONE = []
const DEFAULT_LIMITS = { max_mcq: 20, max_essay: 3 }
const TO_EXAMS = { to: '/exams', label: 'Bài kiểm tra của tôi' }

export default function NewExamPage() {
  const [params, setParams] = useSearchParams()
  const mode = params.get('mode') === 'manual' ? 'manual' : 'ai'
  const setMode = (m) => setParams(m === 'ai' ? {} : { mode: m }, { replace: true })

  return (
    <Page title="Tạo đề mới" back={TO_EXAMS}>
      <fieldset className="choice-group mode-picker">
        <legend className="field-label">Cách tạo đề</legend>
        <ChoiceCard name="mode" checked={mode === 'ai'} onChange={() => setMode('ai')}
          icon={<Sparkle size={20} weight="fill" className="gold" aria-hidden="true" />}
          title="Tạo bằng AI" text="AI soạn đề nháp từ một chương giáo trình. Bạn xem lại, sửa rồi mới xuất bản." />
        <ChoiceCard name="mode" checked={mode === 'manual'} onChange={() => setMode('manual')}
          icon={<PencilSimple size={20} aria-hidden="true" />}
          title="Tự soạn" text="Bắt đầu từ đề trống, tự thêm từng câu hỏi." />
      </fieldset>
      {mode === 'ai' ? <AiForm onManual={() => setMode('manual')} /> : <ManualForm />}
    </Page>
  )
}

/** Ô chọn dạng thẻ: <input type=radio> thật bên trong <label> → bàn phím, trình đọc màn hình dùng như radio. */
function ChoiceCard({ name, checked, onChange, icon, title, text, disabled }) {
  return (
    <label className={`choice-card ${checked ? 'checked' : ''}`}>
      <input type="radio" name={name} checked={checked} onChange={onChange} disabled={disabled} />
      {icon}
      <span>
        <span className="choice-title">{title}</span>
        {text && <span className="choice-text">{text}</span>}
      </span>
    </label>
  )
}

function FieldError({ id, children }) {
  if (!children) return null
  return (
    <p id={id} className="field-error">
      <Warning size={15} weight="bold" aria-hidden="true" />
      {children}
    </p>
  )
}

const toInt = (v) => (/^\d+$/.test(String(v).trim()) ? Number(v) : null)

function validate(form, limits) {
  const e = {}
  if (form.chapter == null) e.chapter = 'Chọn một chương'
  const mcq = toInt(form.nMcq)
  const essay = toInt(form.nEssay)
  if (mcq == null || mcq > limits.max_mcq) e.n_mcq = `Nhập số nguyên từ 0 đến ${limits.max_mcq}`
  if (essay == null || essay > limits.max_essay) e.n_essay = `Nhập số nguyên từ 0 đến ${limits.max_essay}`
  if (!e.n_mcq && !e.n_essay && mcq + essay === 0) e.n_mcq = 'Cần ít nhất một câu hỏi'
  return e
}

// id ô nhập theo tên trường của API → focus đúng ô bị lỗi đầu tiên
const FIELD_IDS = { chapter: 'chapter-0', n_mcq: 'n-mcq', n_essay: 'n-essay', title: 'ai-title' }

// ====================== tạo bằng AI ======================
function AiForm({ onManual }) {
  const navigate = useNavigate()
  const [chapters, retryChapters] = useLoad(listChapters, NONE)
  const [quota, retryQuota] = useLoad(getAiQuota)
  const [form, setForm] = useState({ chapter: null, nMcq: '10', nEssay: '2', title: '' })
  const [errors, setErrors] = useState({})
  const [running, setRunning] = useState(null) // { startedAt, nMcq, nEssay } trong lúc chờ AI
  const [failure, setFailure] = useState(null) // ApiError | { cancelled: true }
  const controller = useRef(null)
  const failureRef = useRef(null)

  const limits = quota.data ?? DEFAULT_LIMITS
  const blocked = quota.status === 'ok' && (quota.data.remaining === 0 || quota.data.busy)
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  useEffect(() => () => controller.current?.abort(), []) // rời trang → thôi chờ (máy chủ vẫn có thể tạo xong)
  useEffect(() => {
    if (failure) failureRef.current?.focus()
  }, [failure])

  const submit = async (ev) => {
    ev?.preventDefault()
    const found = validate(form, limits)
    setErrors(found)
    const first = Object.keys(found)[0]
    if (first) {
      document.getElementById(FIELD_IDS[first])?.focus()
      return
    }
    const nMcq = toInt(form.nMcq)
    const nEssay = toInt(form.nEssay)
    controller.current = new AbortController()
    setFailure(null)
    setRunning({ startedAt: Date.now(), nMcq, nEssay })
    try {
      const exam = await generateExam(
        { chapter: form.chapter, n_mcq: nMcq, n_essay: nEssay, title: form.title.trim() || null },
        { signal: controller.current.signal },
      )
      const counts = { mcq: 0, essay: 0 }
      for (const q of exam.questions) counts[q.type] += 1
      navigate(`/exams/${exam.id}/edit`, { state: { generation: exam.generation, counts } })
    } catch (err) {
      setRunning(null)
      if (err.name === 'AbortError') {
        setFailure({ cancelled: true })
        return
      }
      setErrors(err.fields ?? {})
      setFailure(err)
      if (err.status === 429) retryQuota() // cập nhật "còn bao nhiêu lượt" / "đang tạo đề khác"
    }
  }

  if (running) return <Generating {...running} onCancel={() => controller.current?.abort()} />

  const chapterOptions = chapters.data
  return (
    <form className="exam-form" onSubmit={submit} noValidate>
      <fieldset className="choice-group" aria-describedby={errors.chapter ? 'err-chapter' : undefined}>
        <legend className="field-label">Chương giáo trình</legend>
        {chapters.status === 'loading' && (
          <div className="chapter-grid" aria-hidden="true">
            {[0, 1, 2, 3].map((i) => <span key={i} className="skeleton skeleton-choice" />)}
          </div>
        )}
        {chapters.status === 'error' && (
          <div className="inline-error" role="alert">
            <span>Không tải được danh sách chương: {chapters.error.message}</span>
            <button type="button" className="ghost" onClick={retryChapters}>
              <ArrowClockwise size={15} aria-hidden="true" /> Thử lại
            </button>
          </div>
        )}
        {chapters.status === 'ok' && (
          <div className="chapter-grid">
            {chapterOptions.map((c, i) => (
              <label key={c.number} className={`choice-card compact ${form.chapter === c.number ? 'checked' : ''}`}>
                <input type="radio" name="chapter" id={i === 0 ? 'chapter-0' : undefined} value={c.number}
                  checked={form.chapter === c.number}
                  onChange={() => setForm((f) => ({ ...f, chapter: c.number }))} />
                <span>
                  <span className="choice-title">{c.chapter}</span>
                  <span className="choice-text">{c.title}</span>
                </span>
              </label>
            ))}
          </div>
        )}
        <FieldError id="err-chapter">{errors.chapter}</FieldError>
      </fieldset>

      <div className="field-row">
        <NumberField id="n-mcq" label="Số câu trắc nghiệm" max={limits.max_mcq} value={form.nMcq}
          onChange={set('nMcq')} error={errors.n_mcq} />
        <NumberField id="n-essay" label="Số câu tự luận" max={limits.max_essay} value={form.nEssay}
          onChange={set('nEssay')} error={errors.n_essay} />
      </div>

      <div className="field">
        <label className="field-label" htmlFor="ai-title">Tiêu đề <span className="field-optional">(không bắt buộc)</span></label>
        <input id="ai-title" className="text-input" type="text" maxLength={200} autoComplete="off" value={form.title}
          onChange={set('title')} aria-describedby="ai-title-hint" aria-invalid={!!errors.title} />
        <p id="ai-title-hint" className="field-hint">Bỏ trống thì dùng tên chương. Sửa được sau.</p>
        <FieldError id="err-title">{errors.title}</FieldError>
      </div>

      {failure && <GenerateFailure failure={failure} innerRef={failureRef} onRetry={submit} />}
      <QuotaNotice quota={quota} onManual={onManual} />

      <div className="form-actions">
        <button type="submit" className="primary-btn" disabled={blocked}>
          <Sparkle size={16} weight="fill" aria-hidden="true" />
          Tạo đề bằng AI
        </button>
        <span className="field-hint">Thường mất khoảng 30 giây.</span>
      </div>
    </form>
  )
}

function NumberField({ id, label, max, value, onChange, error }) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>{label}</label>
      <input id={id} className="text-input number-input" type="number" inputMode="numeric" min={0} max={max} step={1}
        value={value} onChange={onChange} aria-invalid={!!error}
        aria-describedby={`${id}-hint${error ? ` err-${id}` : ''}`} />
      <p id={`${id}-hint`} className="field-hint">Từ 0 đến {max}</p>
      <FieldError id={`err-${id}`}>{error}</FieldError>
    </div>
  )
}

function QuotaNotice({ quota, onManual }) {
  if (quota.status !== 'ok') return null // lỗi tải lượt: không chặn — máy chủ vẫn kiểm tra khi tạo
  const { remaining, limit, busy } = quota.data
  if (busy) {
    return (
      <div className="notice notice-warn" role="status">
        <CircleNotch size={18} className="spin" aria-hidden="true" />
        <p>Bạn đang có một đề AI đang tạo (có thể ở tab khác). Đợi đề đó xong rồi tạo tiếp — đề sẽ hiện trong{' '}
          <Link to="/exams">Bài kiểm tra của tôi</Link>.</p>
      </div>
    )
  }
  if (remaining === 0) {
    return (
      <div className="notice notice-warn" role="status">
        <Warning size={18} aria-hidden="true" />
        <p>Bạn đã dùng hết {limit} lượt tạo đề bằng AI hôm nay. Lượt mới mở lại lúc 0 giờ (giờ Việt Nam).{' '}
          <button type="button" className="link-btn" onClick={onManual}>Tự soạn đề</button> thì không giới hạn.</p>
      </div>
    )
  }
  return (
    <p className="field-hint quota-line">
      Còn <strong>{remaining}/{limit}</strong> lượt tạo đề bằng AI hôm nay. Xoá đề AI thì được trả lại lượt.
    </p>
  )
}

function GenerateFailure({ failure, innerRef, onRetry }) {
  if (failure.cancelled) {
    return (
      <div className="notice" role="status" tabIndex={-1} ref={innerRef}>
        <p>Đã thôi chờ. Máy chủ có thể vẫn tạo xong đề này — xem <Link to="/exams">Bài kiểm tra của tôi</Link> sau
          ít phút trước khi tạo lại.</p>
      </div>
    )
  }
  return (
    <div className={`notice ${failure.status === 429 ? 'notice-warn' : 'notice-error'}`} role="alert" tabIndex={-1}
      ref={innerRef}>
      <Warning size={18} aria-hidden="true" />
      <div>
        <p>{failure.message}</p>
        {failure.retryable && (
          <button type="button" className="ghost notice-action" onClick={onRetry}>
            <ArrowClockwise size={15} aria-hidden="true" /> Thử lại
          </button>
        )}
      </div>
    </div>
  )
}

// Các bước AI làm (rag/exam_generation.py). Máy chủ không báo tiến độ → đánh dấu theo thời gian thường gặp.
const STEPS = [
  { from: 0, label: 'Lấy các đoạn giáo trình trải đều chương' },
  { from: 4, label: 'Soạn câu hỏi, đáp án và barem' },
  { from: 16, label: 'Giám khảo AI tự giải lại để kiểm tra đáp án' },
  { from: 28, label: 'Loại câu sai / trùng, lưu đề nháp' },
]

function Generating({ startedAt, nMcq, nEssay, onCancel }) {
  const [now, setNow] = useState(startedAt)
  const headingRef = useRef(null)
  useEffect(() => {
    headingRef.current?.focus()
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const seconds = Math.floor((now - startedAt) / 1000)
  const current = STEPS.findLastIndex((s) => seconds >= s.from)
  const parts = [nMcq && `${nMcq} câu trắc nghiệm`, nEssay && `${nEssay} câu tự luận`].filter(Boolean).join(', ')

  return (
    <section className="generating" aria-labelledby="gen-title">
      <div className="generating-head">
        <CircleNotch size={28} className="spin" aria-hidden="true" />
        <div>
          <h2 id="gen-title" ref={headingRef} tabIndex={-1}>AI đang soạn đề…</h2>
          <p className="muted">{parts} · thường mất khoảng 30 giây</p>
        </div>
      </div>
      <ol className="steps">
        {STEPS.map((s, i) => (
          <li key={s.label} className={i < current ? 'done' : i === current ? 'current' : ''}
            aria-current={i === current ? 'step' : undefined}>
            {s.label}
          </li>
        ))}
      </ol>
      {/* không aria-live: số giây đổi mỗi giây; trình đọc màn hình chỉ được báo một lần khi quá 60 giây (dưới) */}
      <p className="muted">
        Đã chờ {seconds} giây{seconds >= 60 && ' — lâu hơn bình thường: Gemini có thể đang quá tải, AI vẫn đang thử lại.'}
      </p>
      <p className="sr-only" role="status">{seconds >= 60 ? 'Lâu hơn bình thường, AI vẫn đang thử lại.' : ''}</p>
      <button type="button" className="ghost generating-cancel" onClick={onCancel}>Thôi chờ</button>
    </section>
  )
}

// ====================== tự soạn ======================
function ManualForm() {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  const submit = async (ev) => {
    ev.preventDefault()
    if (saving) return // nút vẫn bấm được (aria-disabled) để trình đọc màn hình còn đọc nhãn → chặn ở đây
    if (!title.trim()) {
      setError('Nhập tiêu đề đề thi')
      document.getElementById('manual-title')?.focus()
      return
    }
    setSaving(true)
    setError(null)
    try {
      const exam = await createExam({ title: title.trim() })
      navigate(`/exams/${exam.id}/edit`)
    } catch (err) {
      setSaving(false)
      setError(err.fields?.title ?? err.message)
    }
  }

  return (
    <form className="exam-form" onSubmit={submit} noValidate>
      <div className="field">
        <label className="field-label" htmlFor="manual-title">Tiêu đề</label>
        <input id="manual-title" className="text-input" type="text" maxLength={200} autoComplete="off" value={title}
          onChange={(e) => setTitle(e.target.value)} aria-invalid={!!error}
          aria-describedby={`manual-title-hint${error ? ' err-manual-title' : ''}`} />
        <p id="manual-title-hint" className="field-hint">
          Ví dụ: Kiểm tra giữa kỳ — Chương II. Câu hỏi, thời gian làm bài, chế độ chia sẻ đặt ở bước sau.
        </p>
        <FieldError id="err-manual-title">{error}</FieldError>
      </div>
      <div className="form-actions">
        <button type="submit" className="primary-btn" aria-disabled={saving}>
          {saving ? <CircleNotch size={16} className="spin" aria-hidden="true" /> : <PencilSimple size={16} aria-hidden="true" />}
          {saving ? 'Đang tạo…' : 'Tạo đề trống'}
        </button>
      </div>
    </form>
  )
}
