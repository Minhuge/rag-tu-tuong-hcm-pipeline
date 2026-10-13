import {
  ArrowClockwise, ArrowLeft, ArrowRight, Check, CircleNotch, Clock, Play, Warning, WifiSlash,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import SaveStatus from '../components/SaveStatus.jsx'
import {
  getExamForTaker, getSubmissionResult, listMySubmissions, startSubmission, submitSubmission,
} from '../examsApi.js'
import { formatNumber, formatTime } from '../format.js'
import { useAnswerSaver, useCountdown } from '../useAnswerSaver.js'
import { useLoad } from '../useLoad.js'
import { useNumericId } from '../useNumericId.js'
import { NotFoundPage, Page } from './ExamPages.jsx'

/**
 * Trang làm bài (roadmap 4.6).
 *   Chưa có bài dở → trang giới thiệu, bấm "Bắt đầu" mới tạo bài (đồng hồ chạy từ lúc đó).
 *   Có bài dở → vào thẳng, khôi phục câu trả lời đã lưu.
 * Mỗi màn hình một câu (?q=3 trên URL), lưới số câu để nhảy; tự lưu (trắc nghiệm khi chọn, tự luận khi ngừng gõ 1 giây);
 * đồng hồ đếm ngược theo số giây còn lại do máy chủ tính; hết giờ tự nộp. Nộp: hỏi lại, kèm các câu chưa làm.
 */
const TO_EXAMS = { to: '/exams?tab=taken', label: 'Bài tôi đã làm' }
const MAX_ESSAY_CHARS = 20_000

const answered = (q, a) => (q.type === 'mcq' ? a?.selected_option_id != null : !!a?.essay_text?.trim())

export default function TakeExamPage() {
  const id = useNumericId()
  const [params] = useSearchParams()
  const shareCode = params.get('share_code')
  const load = useCallback(async () => {
    const [exam, mine] = await Promise.all([getExamForTaker(id, shareCode), listMySubmissions()])
    return { exam, mine: mine.filter((s) => s.exam_id === id) }
  }, [id, shareCode])
  const [state, reload] = useLoad(id == null ? NEVER : load)
  const [attempt, setAttempt] = useState(null) // { sub, answers } khi đang làm

  if (id == null) return <NotFoundPage />
  if (state.status === 'loading') return <Loading />
  if (state.status === 'error') return <LoadError error={state.error} onRetry={reload} />

  const { exam, mine } = state.data
  const resumable = mine.find((s) => s.status === 'in_progress')
  if (attempt) return <Taking exam={exam} sub={attempt.sub} initialAnswers={attempt.answers} />
  return (
    <Intro exam={exam} resumable={resumable} past={mine.filter((s) => s.status !== 'in_progress')}
      shareCode={shareCode} onStarted={setAttempt} />
  )
}

const NEVER = () => new Promise(() => {})

function Loading() {
  return (
    <Page title="Đang tải đề…" back={TO_EXAMS}>
      <p className="sr-only" role="status">Đang tải đề…</p>
      <div className="card skeleton-card" aria-hidden="true">
        <span className="skeleton skeleton-title" />
        <span className="skeleton skeleton-line" />
        <span className="skeleton skeleton-line short" />
      </div>
    </Page>
  )
}

function LoadError({ error, onRetry }) {
  const missing = error.status === 404
  return (
    <Page title={missing ? 'Không vào được đề' : 'Không tải được đề'} back={TO_EXAMS}>
      <div className="state-box state-error" role="alert">
        <Warning size={22} aria-hidden="true" />
        <div>
          <p className="state-title">{missing ? 'Không tìm thấy đề này' : 'Có lỗi khi tải đề'}</p>
          <p className="muted">
            {missing
              ? 'Link có thể bị thiếu mã chia sẻ (phần ?share_code=…), đề chưa được xuất bản, hoặc đã bị xoá. '
                + 'Hãy hỏi lại người gửi link.'
              : error.message}
          </p>
          {!missing && (
            <button type="button" className="ghost state-action" onClick={onRetry}>
              <ArrowClockwise size={16} aria-hidden="true" /> Thử lại
            </button>
          )}
        </div>
      </div>
    </Page>
  )
}

// ====================== trước khi làm ======================
function Intro({ exam, resumable, past, shareCode, onStarted }) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const total = exam.questions.reduce((s, q) => s + q.points, 0)
  const counts = { mcq: 0, essay: 0 }
  for (const q of exam.questions) counts[q.type] += 1

  const begin = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const sub = await startSubmission(exam.id, shareCode)
      // làm tiếp: lấy lại câu trả lời đã lưu
      const saved = await getSubmissionResult(sub.id)
      const answers = Object.fromEntries(saved.questions.map((q) => [q.id, q.your_answer]))
      onStarted({ sub, answers })
    } catch (e) {
      setBusy(false)
      setError(e)
    }
  }

  const submitSaved = async () => {
    try {
      await submitSubmission(resumable.id)
    } catch (e) {
      if (e.status !== 409) {
        setError(e)
        return
      }
    }
    navigate(`/submissions/${resumable.id}`, { replace: true })
  }

  // bài dở dang: không cần trang giới thiệu, mở luôn
  const auto = useRef(false)
  useEffect(() => {
    if (resumable && !auto.current) {
      auto.current = true
      begin()
    }
  })
  if (resumable && !error) return <Loading />

  const facts = [
    ['Số câu', [counts.mcq && `${counts.mcq} trắc nghiệm`, counts.essay && `${counts.essay} tự luận`].filter(Boolean).join(', ')],
    ['Tổng điểm', formatNumber(total)],
    ['Thời gian', exam.duration_minutes ? `${exam.duration_minutes} phút` : 'Không giới hạn'],
    ['Hạn chót', exam.close_at ? formatTime(exam.close_at) : 'Không có'],
  ]
  return (
    <Page title={exam.title} back={TO_EXAMS}>
      {exam.description && <p className="exam-desc">{exam.description}</p>}
      <dl className="facts">{facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      <ul className="intro-notes">
        <li>Câu trả lời được <strong>tự lưu</strong> ngay khi bạn chọn hoặc ngừng gõ — đóng trang rồi mở lại vẫn làm tiếp được.</li>
        {exam.duration_minutes && (
          <li>Đồng hồ chạy từ lúc bấm <strong>Bắt đầu</strong> và <strong>vẫn chạy khi bạn đóng trang</strong>. Hết giờ, bài
            tự nộp.</li>
        )}
        {counts.essay > 0 && <li>Câu tự luận được AI chấm theo barem sau khi nộp; điểm có sau vài phút.</li>}
      </ul>
      {error && (
        <div className="notice notice-error" role="alert">
          <Warning size={18} aria-hidden="true" />
          <div>
            <p>{error.status === 409 ? `${error.message}.` : `Chưa bắt đầu được: ${error.message}`}
              {exam.open_at && error.status === 409 && !resumable && ` Đề mở lúc ${formatTime(exam.open_at)}.`}</p>
            {resumable && error.status === 409 && (
              // bài dở của đề đã đóng: không làm tiếp được nhưng máy chủ vẫn cho nộp phần đã lưu
              <button type="button" className="ghost notice-action" onClick={submitSaved}>
                <Check size={15} weight="bold" aria-hidden="true" /> Nộp phần đã làm
              </button>
            )}
          </div>
        </div>
      )}
      <div className="form-actions">
        <button type="button" className="primary-btn" onClick={begin} aria-disabled={busy}>
          {busy ? <CircleNotch size={16} className="spin" aria-hidden="true" /> : <Play size={16} weight="fill" aria-hidden="true" />}
          {busy ? 'Đang mở…' : 'Bắt đầu làm bài'}
        </button>
      </div>
      {past.length > 0 && (
        <p className="muted past-attempts">
          Bạn đã làm đề này {past.length} lần —{' '}
          <Link to={`/submissions/${past[0].id}`}>xem kết quả lần gần nhất</Link>.
        </p>
      )}
    </Page>
  )
}

// ====================== đang làm ======================
function Taking({ exam, sub, initialAnswers }) {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const questions = exam.questions
  const n = questions.length
  const index = Math.min(Math.max((Number(params.get('q')) || 1) - 1, 0), n - 1)
  const q = questions[index]
  const [answers, setAnswers] = useState(initialAnswers)
  const [serverTimeUp, setServerTimeUp] = useState(false) // máy chủ báo hết giờ / đã nộp (409 khi lưu)
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState(null) // { message, retry: 'save' | 'submit' }
  const [online, setOnline] = useState(navigator.onLine)
  const dialogRef = useRef(null)
  const questionRef = useRef(null)
  const left = useCountdown(sub.remaining_seconds)
  const timeUp = serverTimeUp || left === 0

  const saver = useAnswerSaver(sub.id, {
    onError: (e) => {
      if (e.status === 409) setServerTimeUp(true) // máy chủ báo hết giờ / đã nộp → dừng sửa, nộp
    },
  })

  const go = (i) => {
    saver.flushTimers() // rời câu tự luận đang gõ → lưu ngay, không chờ hết 1 giây
    const next = new URLSearchParams(params)
    next.set('q', String(i + 1))
    setParams(next, { replace: true }) // không chất từng câu vào lịch sử: Back vẫn về trang trước
    requestAnimationFrame(() => questionRef.current?.focus())
  }

  const setAnswer = (qid, answer, { debounce = false } = {}) => {
    setAnswers((a) => ({ ...a, [qid]: answer }))
    if (debounce) saver.saveLater(qid, answer, 1000)
    else saver.save(qid, answer)
  }

  const doneCount = questions.filter((x) => answered(x, answers[x.id])).length
  const unanswered = questions.map((x, i) => (answered(x, answers[x.id]) ? null : i)).filter((i) => i !== null)
  const errors = Object.values(saver.status).filter((s) => s === 'error').length
  const saving = Object.values(saver.status).some((s) => s === 'saving')
  const saveStatus = errors ? 'error' : saving ? 'saving' : 'saved'

  const finish = useCallback(async ({ auto = false } = {}) => {
    if (submitting) return
    setSubmitting(true)
    setNotice(null)
    dialogRef.current?.close()
    const allSaved = await saver.drain()
    if (!allSaved && !auto) {
      setSubmitting(false)
      setNotice({ message: 'Còn câu trả lời chưa lưu được lên máy chủ. Kiểm tra mạng rồi bấm "Thử lại" trước khi nộp.',
        retry: 'save' })
      return
    }
    try {
      await submitSubmission(sub.id)
    } catch (e) {
      if (e.status !== 409) { // 409 = đã nộp rồi (vd. ở tab khác) → vẫn sang trang kết quả
        setSubmitting(false)
        setNotice({ message: `Chưa nộp được: ${e.message}`, retry: 'submit' })
        return
      }
    }
    navigate(`/submissions/${sub.id}`, { replace: true, state: { autoSubmitted: auto } })
  }, [submitting, saver, sub.id, navigate])

  // hết giờ → tự nộp (câu đã lưu kịp trong 30 giây ân hạn của máy chủ vẫn được tính). finishRef: chạy đúng một lần
  // khi timeUp chuyển sang true, không chạy lại mỗi khi finish được tạo mới
  const finishRef = useRef(finish)
  useEffect(() => {
    finishRef.current = finish
  })
  useEffect(() => {
    if (!timeUp) return undefined
    const t = setTimeout(() => finishRef.current({ auto: true }), 0)
    return () => clearTimeout(t)
  }, [timeUp])

  // mất mạng: báo, có mạng lại thì gửi lại các câu lỗi
  const { retry, hasPending } = saver
  useEffect(() => {
    const up = () => {
      setOnline(true)
      retry()
    }
    const down = () => setOnline(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    return () => {
      window.removeEventListener('online', up)
      window.removeEventListener('offline', down)
    }
  }, [retry])

  // đóng tab / F5 khi đang làm: còn câu chưa lưu, hoặc đồng hồ vẫn chạy → trình duyệt hỏi lại
  useEffect(() => {
    const warn = (e) => {
      if (hasPending() || sub.remaining_seconds != null) e.preventDefault()
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [hasPending, sub.remaining_seconds])

  const openReview = () => {
    saver.flushTimers()
    dialogRef.current?.showModal()
  }

  return (
    <Page title={`Làm bài: ${exam.title}`} heading={exam.title} className="page-wide">
      <div className="take-bar">
        <span className="take-progress" aria-label={`Đã làm ${doneCount} trên ${n} câu`}>
          <Check size={15} weight="bold" aria-hidden="true" /> {doneCount}/{n} câu
        </span>
        {left != null && <Countdown left={left} />}
        <SaveStatus status={saveStatus} onRetry={saver.retry} />
        <button type="button" className="primary-btn take-submit" onClick={openReview} aria-disabled={submitting}>
          {submitting ? <CircleNotch size={16} className="spin" aria-hidden="true" /> : <Check size={16} weight="bold" aria-hidden="true" />}
          {submitting ? 'Đang nộp…' : 'Nộp bài'}
        </button>
      </div>

      {!online && (
        <div className="notice notice-warn" role="status">
          <WifiSlash size={18} aria-hidden="true" />
          <p>Mất kết nối mạng. Cứ làm tiếp — câu trả lời sẽ được gửi khi có mạng lại. Đừng đóng trang lúc này.</p>
        </div>
      )}
      {timeUp && (
        <div className="notice notice-warn" role="alert">
          <Clock size={18} aria-hidden="true" />
          <p>Đã hết giờ — đang nộp bài của bạn…</p>
        </div>
      )}
      {notice && (
        <div className="notice notice-error" role="alert">
          <Warning size={18} aria-hidden="true" />
          <div>
            <p>{notice.message}</p>
            <button type="button" className="ghost notice-action"
              onClick={notice.retry === 'submit' ? () => finish({ auto: timeUp }) : saver.retry}>
              <ArrowClockwise size={15} aria-hidden="true" /> Thử lại
            </button>
          </div>
        </div>
      )}

      <div className="take-layout">
        <section className="card take-question" aria-labelledby="q-heading" ref={questionRef} tabIndex={-1}>
          <div className="q-head">
            <h2 id="q-heading" className="q-title">Câu {index + 1}<span className="muted">/{n}</span></h2>
            <span className="badge">{q.type === 'mcq' ? 'Trắc nghiệm' : 'Tự luận'}</span>
            <span className="q-points">{formatNumber(q.points)} điểm</span>
          </div>
          <p className="q-content">{q.content}</p>
          {q.type === 'mcq'
            ? <McqAnswer key={q.id} q={q} value={answers[q.id]} disabled={timeUp || submitting}
                status={saver.status[q.id]} onChange={(a) => setAnswer(q.id, a)} />
            : <EssayAnswer key={q.id} q={q} value={answers[q.id]} disabled={timeUp || submitting}
                status={saver.status[q.id]} onChange={(a) => setAnswer(q.id, a, { debounce: true })} />}

          <div className="take-nav">
            <button type="button" className="ghost nav-btn" onClick={() => go(index - 1)} disabled={index === 0}>
              <ArrowLeft size={16} aria-hidden="true" /> Câu trước
            </button>
            {index < n - 1
              ? <button type="button" className="ghost nav-btn" onClick={() => go(index + 1)}>
                Câu sau <ArrowRight size={16} aria-hidden="true" />
              </button>
              : <button type="button" className="primary-btn nav-btn" onClick={openReview}>
                Xem lại &amp; nộp <ArrowRight size={16} aria-hidden="true" />
              </button>}
          </div>
        </section>

        <nav className="card q-grid-box" aria-label="Danh sách câu hỏi">
          <p className="q-grid-title">Câu hỏi</p>
          <ol className="q-grid">
            {questions.map((x, i) => {
              const done = answered(x, answers[x.id])
              const err = saver.status[x.id] === 'error'
              return (
                <li key={x.id}>
                  <button type="button" className={`q-dot ${done ? 'done' : ''} ${i === index ? 'current' : ''} ${err ? 'err' : ''}`}
                    onClick={() => go(i)} aria-current={i === index ? 'step' : undefined}
                    aria-label={`Câu ${i + 1}, ${err ? 'chưa lưu được' : done ? 'đã trả lời' : 'chưa trả lời'}`}>
                    {i + 1}
                  </button>
                </li>
              )
            })}
          </ol>
          <p className="q-grid-legend">
            <span><i className="q-dot-mini done" aria-hidden="true" />Đã làm</span>
            <span><i className="q-dot-mini" aria-hidden="true" />Chưa làm</span>
          </p>
        </nav>
      </div>

      <dialog ref={dialogRef} className="confirm-dialog" aria-labelledby="confirm-title">
        <h2 id="confirm-title">Nộp bài?</h2>
        {unanswered.length
          ? (
            <>
              <p>Bạn còn <strong>{unanswered.length} câu chưa làm</strong>. Câu bỏ trống được 0 điểm.</p>
              <ul className="q-grid small">
                {unanswered.map((i) => (
                  <li key={i}>
                    <button type="button" className="q-dot" onClick={() => {
                      dialogRef.current?.close()
                      go(i)
                    }} aria-label={`Đến câu ${i + 1}`}>{i + 1}</button>
                  </li>
                ))}
              </ul>
            </>
          )
          : <p>Bạn đã trả lời đủ {n} câu.</p>}
        <p className="muted">Sau khi nộp sẽ không sửa được nữa.</p>
        <div className="dialog-actions">
          {/* focus mặc định ở "Làm tiếp": lỡ nhấn Enter không nộp nhầm */}
          <button type="button" className="ghost nav-btn" onClick={() => dialogRef.current?.close()} autoFocus>
            Làm tiếp
          </button>
          <button type="button" className="primary-btn" onClick={() => finish()}>Nộp bài</button>
        </div>
      </dialog>
    </Page>
  )
}

function Countdown({ left }) {
  const h = Math.floor(left / 3600)
  const m = Math.floor((left % 3600) / 60)
  const s = left % 60
  const text = `${h ? `${h}:` : ''}${String(m).padStart(h ? 2 : 1, '0')}:${String(s).padStart(2, '0')}`
  const level = left <= 60 ? 'bad' : left <= 300 ? 'warn' : ''
  // trình đọc màn hình chỉ được báo ở các mốc, không đọc từng giây
  const milestone = left === 300 ? 'Còn 5 phút.' : left === 60 ? 'Còn 1 phút.' : ''
  return (
    <span className={`countdown ${level}`} title="Thời gian còn lại">
      <Clock size={16} weight="bold" aria-hidden="true" />
      <span className="sr-only">Thời gian còn lại: </span>
      <span className="countdown-time">{text}</span>
      <span className="sr-only" role="status">{milestone}</span>
    </span>
  )
}

function McqAnswer({ q, value, disabled, status, onChange }) {
  const selected = value?.selected_option_id ?? null
  return (
    <fieldset className="choice-group answer-options" disabled={disabled}>
      <legend className="sr-only">Chọn một đáp án cho câu này</legend>
      {q.options.map((o) => (
        <label key={o.id} className={`choice-card answer-option ${selected === o.id ? 'checked' : ''}`}>
          <input type="radio" name={`q-${q.id}`} checked={selected === o.id}
            onChange={() => onChange({ selected_option_id: o.id, essay_text: null })} />
          <span className="answer-letter" aria-hidden="true">{o.label}</span>
          <span className="answer-text">{o.content}</span>
        </label>
      ))}
      <div className="answer-foot">
        <AnswerStatus status={status} saved={selected != null} />
        {selected != null && !disabled && (
          <button type="button" className="link-btn" onClick={() => onChange({ selected_option_id: null, essay_text: null })}>
            Bỏ chọn
          </button>
        )}
      </div>
    </fieldset>
  )
}

function EssayAnswer({ q, value, disabled, status, onChange }) {
  const text = value?.essay_text ?? ''
  const words = useMemo(() => (text.trim() ? text.trim().split(/\s+/).length : 0), [text])
  return (
    <div className="field">
      <label className="field-label" htmlFor={`essay-${q.id}`}>Bài làm</label>
      <textarea id={`essay-${q.id}`} className="text-input text-area essay-input" rows={10} maxLength={MAX_ESSAY_CHARS}
        value={text} disabled={disabled} aria-describedby={`essay-${q.id}-hint`}
        onChange={(e) => onChange({ selected_option_id: null, essay_text: e.target.value })} />
      <div className="answer-foot" id={`essay-${q.id}-hint`}>
        <AnswerStatus status={status} saved={!!text.trim()} />
        <span className="muted">{words} từ</span>
      </div>
    </div>
  )
}

function AnswerStatus({ status, saved }) {
  if (status === 'saving') return <span className="save-status"><CircleNotch size={14} className="spin" aria-hidden="true" />Đang lưu…</span>
  if (status === 'error') return <span className="save-status bad"><Warning size={14} weight="bold" aria-hidden="true" />Chưa lưu được</span>
  if (saved) return <span className="save-status ok"><Check size={14} weight="bold" aria-hidden="true" />Đã lưu</span>
  return <span className="save-status">Chưa trả lời</span>
}
