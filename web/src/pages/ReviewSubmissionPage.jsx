import {
  ArrowClockwise, Check, CircleNotch, Clock, Minus, PencilSimple, Robot, User, Warning, X,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { getSubmissionReview, gradeAnswer } from '../examsApi.js'
import { formatNumber, formatTime } from '../format.js'
import { SUB_STATUS, personName } from '../submissionLabels.js'
import { usePolledLoad } from '../usePolledLoad.js'
import { NotFoundPage, Page } from './ExamPages.jsx'

/**
 * Người tạo đề xem một bài làm và chấm tay (roadmap 4.8, API ở 3.7): câu trả lời, đáp án, barem, nhận xét AI;
 * chấm / sửa điểm từng câu (ghi đè điểm AI, tổng điểm tính lại trên máy chủ). AI còn đang chấm → tự cập nhật,
 * nhưng không đè lên ô điểm bạn đang sửa dở.
 */
const NEVER = () => new Promise(() => {})
const NEEDS_HAND = ['needs_creator', 'failed', 'quota'] // câu AI không chấm / chưa chấm được → chờ người tạo đề
const BY = { ai: { Icon: Robot, text: 'AI chấm' }, creator: { Icon: User, text: 'Bạn đã chấm' }, auto: null }

const isId = (v) => /^[1-9]\d*$/.test(v ?? '')

export default function ReviewSubmissionPage() {
  const { id, sid } = useParams()
  const ok = isId(id) && isId(sid)
  const load = useCallback(() => getSubmissionReview(Number(sid)), [sid])
  const [state, reload, setData] = usePolledLoad(ok ? load : NEVER, { busy: (d) => d.grading?.still_grading })

  if (!ok) return <NotFoundPage />
  const back = { to: `/exams/${id}/results`, label: 'Danh sách bài nộp' }
  if (state.status === 'loading') {
    return (
      <Page title="Đang tải bài làm…" back={back}>
        <p className="sr-only" role="status">Đang tải…</p>
        <div className="card skeleton-card result-skeleton" aria-hidden="true">
          <span className="skeleton skeleton-title" /><span className="skeleton skeleton-line" />
        </div>
      </Page>
    )
  }
  if (state.status === 'error') {
    const missing = state.error.status === 404
    return (
      <Page title={missing ? 'Không tìm thấy bài làm' : 'Không tải được bài làm'} back={back}>
        <div className="state-box state-error" role="alert">
          <Warning size={22} aria-hidden="true" />
          <div>
            <p className="state-title">{missing ? 'Bài làm không tồn tại hoặc không thuộc đề của bạn' : 'Có lỗi khi tải'}</p>
            {!missing && <p className="muted">{state.error.message}</p>}
            {!missing && (
              <button type="button" className="ghost state-action" onClick={reload}>
                <ArrowClockwise size={16} aria-hidden="true" /> Thử lại
              </button>
            )}
          </div>
        </div>
      </Page>
    )
  }
  return <Review data={state.data} back={back} onSaved={setData} />
}

/** Kết quả một câu — luôn có chữ, không chỉ màu. */
function verdict(q, submitted) {
  if (!submitted) return null
  if (q.grading_status !== 'graded') {
    if (q.grading_status === 'grading') return { kind: 'wait', text: 'AI đang chấm…', Icon: CircleNotch, spin: true }
    if (q.grading_status === 'needs_creator') return { kind: 'warn', text: 'Cần bạn chấm', Icon: PencilSimple }
    return { kind: 'warn', text: 'AI chưa chấm được', Icon: Warning }
  }
  if (q.type === 'mcq' && q.answer.selected_option_id == null) return { kind: 'skip', text: 'Bỏ trống · 0 điểm', Icon: Minus }
  const full = q.score >= q.points
  const zero = !q.score
  return {
    kind: full ? 'ok' : zero ? 'bad' : 'part',
    text: `${formatNumber(q.score)}/${formatNumber(q.points)} điểm`,
    Icon: full ? Check : zero ? X : Minus,
  }
}

function Review({ data, back, onSaved }) {
  const [onlyHand, setOnlyHand] = useState(false)
  const [saved, setSaved] = useState('') // thông báo cho trình đọc màn hình sau khi lưu điểm
  const submitted = data.status !== 'in_progress'
  const stillGrading = !!data.grading?.still_grading
  const qs = data.questions
  const hand = qs.filter((q) => NEEDS_HAND.includes(q.grading_status))
  const score = data.score ?? qs.reduce((t, q) => t + (q.score ?? 0), 0)
  const max = data.max_score ?? qs.reduce((t, q) => t + q.points, 0)
  const minutes = data.submitted_at ? Math.max(1, Math.round((data.submitted_at - data.started_at) / 60000)) : null
  const st = SUB_STATUS[data.status]
  const shown = onlyHand ? hand : qs

  const handleSaved = (review, n) => {
    onSaved(review)
    setSaved(`Đã lưu điểm câu ${n}. Tổng điểm: ${formatNumber(review.score ?? 0)} trên ${formatNumber(review.max_score ?? max)}.`)
  }

  return (
    <Page title={`Bài làm của ${personName(data.user)}`} back={back}>
      <p className="sr-only" role="status">{saved}</p>
      <section className="card score-card" aria-label="Tổng quan bài làm">
        <div className="score-main">
          <p className="score-big">
            <strong>{submitted ? formatNumber(score) : '—'}</strong>
            <span>/ {formatNumber(max)}</span>
          </p>
          <span className={`badge ${st.className}`}>
            {stillGrading && <CircleNotch size={13} className="spin" aria-hidden="true" />}
            {stillGrading ? 'Điểm tạm — AI đang chấm' : st.text}
          </span>
        </div>
        <dl className="facts score-facts">
          <div><dt>Người làm</dt><dd>{personName(data.user)}</dd></div>
          <div><dt>Email</dt><dd className="break">{data.user.email}</dd></div>
          <div><dt>{submitted ? 'Nộp lúc' : 'Bắt đầu lúc'}</dt><dd>{formatTime(submitted ? data.submitted_at : data.started_at)}</dd></div>
          {minutes && <div><dt>Thời gian làm</dt><dd>{minutes} phút</dd></div>}
        </dl>
      </section>

      {!submitted && (
        <div className="notice notice-warn">
          <Clock size={18} aria-hidden="true" />
          <p>Bài đang làm dở — chưa nộp nên chưa chấm được. Bên dưới là các câu đã lưu tới lúc này.</p>
        </div>
      )}
      {stillGrading && (
        <div className="notice" role="status">
          <CircleNotch size={18} className="spin" aria-hidden="true" />
          <p>AI đang chấm tự luận ({data.grading.graded}/{data.grading.total} câu đã có điểm) — trang tự cập nhật. Bạn vẫn
            chấm tay được ngay; điểm của bạn được giữ, AI không ghi đè.</p>
        </div>
      )}
      {submitted && hand.length > 0 && (
        <div className="notice notice-warn">
          <PencilSimple size={18} aria-hidden="true" />
          <p><strong>{hand.length} câu cần bạn chấm tay</strong> (không có barem, hoặc AI chưa chấm được). Tổng điểm chỉ
            chốt khi mọi câu đã có điểm.</p>
        </div>
      )}

      <div className="result-toolbar">
        <h2 className="section-title">Từng câu</h2>
        {hand.length > 0 && (
          <label className="check-row compact">
            <input type="checkbox" checked={onlyHand} onChange={(e) => setOnlyHand(e.target.checked)} />
            <span>Chỉ xem câu cần chấm tay</span>
          </label>
        )}
      </div>
      <ol className="card-list q-list">
        {shown.map((q) => (
          <ReviewQuestion key={q.id} q={q} n={qs.indexOf(q) + 1} sid={data.id} submitted={submitted}
            onSaved={handleSaved} />
        ))}
      </ol>
    </Page>
  )
}

function ReviewQuestion({ q, n, sid, submitted, onSaved }) {
  const v = verdict(q, submitted)
  const by = BY[q.graded_by]
  const [editing, setEditing] = useState(q.type === 'essay') // trắc nghiệm đã chấm tự động: mở form khi bấm "Sửa điểm"
  return (
    <li className={`card q-card result-q ${v ? `result-${v.kind}` : ''}`}>
      <div className="q-head">
        <h3 className="q-title">Câu {n}</h3>
        <span className="badge">{q.type === 'mcq' ? 'Trắc nghiệm' : 'Tự luận'}</span>
        {v && (
          <span className={`verdict verdict-${v.kind}`}>
            <v.Icon size={14} weight="bold" className={v.spin ? 'spin' : undefined} aria-hidden="true" />{v.text}
          </span>
        )}
        {by && submitted && <span className="graded-by"><by.Icon size={14} aria-hidden="true" />{by.text}</span>}
      </div>
      <p className="q-content">{q.content}</p>

      {q.type === 'mcq'
        ? (
          <ul className="opt-view">
            {q.options.map((o) => {
              const mine = o.id === q.answer.selected_option_id
              return (
                <li key={o.id} className={o.is_correct ? 'correct' : mine ? 'wrong' : ''}>
                  <span className="opt-letter">{o.label}.</span> {o.content}
                  <span className="opt-tags">
                    {mine && <span className={`opt-tag ${o.is_correct ? '' : 'opt-tag-bad'}`}>Đã chọn</span>}
                    {o.is_correct && <span className="opt-tag"><Check size={13} weight="bold" aria-hidden="true" />Đáp án đúng</span>}
                  </span>
                </li>
              )
            })}
          </ul>
        )
        : (
          <>
            <details className="rubric-box" open={NEEDS_HAND.includes(q.grading_status)}>
              <summary>Barem{q.rubric ? '' : ' — không có'}</summary>
              <p className="pre">{q.rubric || 'Câu này không có barem nên AI không chấm — hãy chấm tay.'}</p>
            </details>
            <p className="answer-label">Bài làm</p>
            {q.answer.essay_text?.trim()
              ? <blockquote className="essay-view">{q.answer.essay_text}</blockquote>
              : <p className="muted">Bỏ trống.</p>}
            {q.grading_status !== 'graded' && q.grading_message && <p className="grading-msg warn">{q.grading_message}</p>}
            {q.feedback && (
              <div className="feedback-box">
                <p className="feedback-head">{by && <by.Icon size={15} aria-hidden="true" />}Nhận xét{by ? ` · ${by.text}` : ''}</p>
                <p className="feedback-text">{q.feedback}</p>
              </div>
            )}
          </>
        )}

      {submitted && (editing
        ? <GradeForm q={q} n={n} sid={sid} onSaved={onSaved} onCancel={q.type === 'mcq' ? () => setEditing(false) : null} />
        : (
          <button type="button" className="ghost grade-toggle" onClick={() => setEditing(true)}>
            <PencilSimple size={15} aria-hidden="true" /> Sửa điểm câu này
          </button>
        ))}
    </li>
  )
}

/**
 * Ô chấm một câu. Chưa sửa gì → hiện điểm hiện tại từ máy chủ (cập nhật theo khi AI chấm xong);
 * đã sửa → giữ bản đang sửa cho tới khi lưu / huỷ (lượt tự cập nhật không đè lên).
 */
function GradeForm({ q, n, sid, onSaved, onCancel }) {
  const [draft, setDraft] = useState(null) // null = chưa sửa
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const value = draft ?? { score: q.score == null ? '' : String(q.score), feedback: q.feedback ?? '' }
  const ids = { score: `grade-${q.id}-score`, fb: `grade-${q.id}-fb`, err: `grade-${q.id}-err`, hint: `grade-${q.id}-hint` }

  useEffect(() => {
    if (!done) return undefined
    const t = setTimeout(() => setDone(false), 2500)
    return () => clearTimeout(t)
  }, [done])

  const edit = (patch) => {
    setDraft({ ...value, ...patch })
    setDone(false)
  }

  const save = async (e) => {
    e.preventDefault()
    if (busy) return
    const score = String(value.score).trim() === '' ? NaN : Number(value.score)
    if (!(score >= 0 && score <= q.points)) {
      setError(`Điểm phải từ 0 đến ${formatNumber(q.points)}`)
      document.getElementById(ids.score)?.focus()
      return
    }
    setBusy(true)
    setError(null)
    try {
      const review = await gradeAnswer(sid, q.id, { score, feedback: value.feedback.trim() })
      setDraft(null)
      setDone(true)
      onSaved(review, n)
    } catch (err) {
      setError(err.fields?.score ?? err.message)
    } finally {
      setBusy(false)
    }
  }

  const quick = [['0', 0], ['Nửa', q.points / 2], ['Tối đa', q.points]]
  return (
    <form className="grade-form" onSubmit={save} noValidate>
      <p className="grade-title">{q.graded_by === 'ai' ? 'Sửa điểm AI' : q.score == null ? 'Chấm câu này' : 'Sửa điểm'}</p>
      <div className="grade-row">
        <div className="field">
          <label className="field-label" htmlFor={ids.score}>Điểm</label>
          <div className="score-input">
            <input id={ids.score} className="text-input number-input" type="number" inputMode="decimal" min={0}
              max={q.points} step={0.25} value={value.score} onChange={(e) => edit({ score: e.target.value })}
              aria-invalid={!!error} aria-describedby={`${ids.hint}${error ? ` ${ids.err}` : ''}`} />
            <span className="muted">/ {formatNumber(q.points)}</span>
          </div>
          <p id={ids.hint} className="field-hint">Từ 0 đến {formatNumber(q.points)}, bước 0,25</p>
        </div>
        <div className="quick-scores" role="group" aria-label="Chọn nhanh điểm">
          {quick.map(([label, val]) => (
            <button key={label} type="button" className="ghost chip" onClick={() => edit({ score: String(val) })}
              aria-pressed={Number(value.score) === val && String(value.score) !== ''}>
              {label}{label !== '0' && ` (${formatNumber(val)})`}
            </button>
          ))}
        </div>
      </div>
      <div className="field">
        <label className="field-label" htmlFor={ids.fb}>Nhận xét <span className="field-optional">(người làm bài thấy)</span></label>
        <textarea id={ids.fb} className="text-input text-area" rows={2} maxLength={5000} value={value.feedback}
          onChange={(e) => edit({ feedback: e.target.value })} />
      </div>
      {error && <p id={ids.err} className="field-error"><Warning size={15} weight="bold" aria-hidden="true" />{error}</p>}
      <div className="form-actions">
        <button type="submit" className="primary-btn" aria-disabled={busy}>
          {busy ? <CircleNotch size={16} className="spin" aria-hidden="true" /> : <Check size={16} weight="bold" aria-hidden="true" />}
          {busy ? 'Đang lưu…' : 'Lưu điểm'}
        </button>
        {draft && !busy && (
          <button type="button" className="ghost nav-btn" onClick={() => {
            setDraft(null)
            setError(null)
          }}>Huỷ thay đổi</button>
        )}
        {!draft && onCancel && <button type="button" className="ghost nav-btn" onClick={onCancel}>Đóng</button>}
        {done && <span className="save-status ok"><Check size={14} weight="bold" aria-hidden="true" />Đã lưu</span>}
      </div>
    </form>
  )
}
