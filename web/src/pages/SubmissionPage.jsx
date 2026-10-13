import {
  ArrowClockwise, ArrowCounterClockwise, Check, CircleNotch, Clock, Minus, Robot, User, Warning, X,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { getSubmissionResult } from '../examsApi.js'
import { formatNumber, formatTime } from '../format.js'
import { useNumericId } from '../useNumericId.js'
import { usePolledLoad } from '../usePolledLoad.js'
import { NotFoundPage, Page } from './ExamPages.jsx'

/**
 * Kết quả bài làm của chính mình (roadmap 4.7): tổng điểm, từng câu đúng / sai, đáp án đúng, giải thích, trang giáo
 * trình, nhận xét tự luận. Còn câu tự luận đang chấm (grading.still_grading) → hỏi lại máy chủ vài giây một lần
 * cho tới khi xong. Bài chưa nộp → chuyển sang trang làm bài.
 */
const TO_TAKEN = { to: '/exams?tab=taken', label: 'Bài tôi đã làm' }
const NEVER = () => new Promise(() => {}) // id sai trên URL: không gọi API

export default function SubmissionPage() {
  const id = useNumericId()
  const navigate = useNavigate()
  const { state: navState } = useLocation()
  const load = useCallback((signal) => getSubmissionResult(id, { signal }), [id])
  // còn câu tự luận đang chấm → hỏi lại vài giây một lần (AI chấm một câu ~10–30 giây)
  const [result, reload] = usePolledLoad(id == null ? NEVER : load, { busy: (d) => d.grading?.still_grading })

  const inProgress = result.data?.status === 'in_progress'
  useEffect(() => {
    if (inProgress) navigate(`/exams/${result.data.exam_id}/take`, { replace: true }) // chưa nộp → làm tiếp
  }, [inProgress, result.data, navigate])

  if (id == null) return <NotFoundPage />
  if (result.status === 'loading' || inProgress) {
    return (
      <Page title="Đang tải kết quả…" back={TO_TAKEN}>
        <p className="sr-only" role="status">Đang tải kết quả…</p>
        <div className="card skeleton-card result-skeleton" aria-hidden="true">
          <span className="skeleton skeleton-title" />
          <span className="skeleton skeleton-line" />
        </div>
      </Page>
    )
  }
  if (result.status === 'error') {
    const missing = result.error.status === 404
    return (
      <Page title={missing ? 'Không tìm thấy bài làm' : 'Không tải được kết quả'} back={TO_TAKEN}>
        <div className="state-box state-error" role="alert">
          <Warning size={22} aria-hidden="true" />
          <div>
            <p className="state-title">{missing ? 'Bài làm không tồn tại hoặc không phải của bạn' : 'Có lỗi khi tải kết quả'}</p>
            <p className="muted">{missing ? 'Có thể đề đã bị người tạo xoá.' : result.error.message}</p>
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

  return (
    <Result data={result.data} autoSubmitted={!!navState?.autoSubmitted} staleError={result.error} />
  )
}

// ====================== kết quả ======================
const GRADED_BY = { ai: { Icon: Robot, text: 'AI chấm' }, creator: { Icon: User, text: 'Người ra đề chấm' } }

/** Kết quả một câu: đúng / sai / bỏ trống / đang chấm… — luôn có chữ, không chỉ màu. */
function verdict(q) {
  if (q.type === 'mcq') {
    if (q.your_answer.selected_option_id == null) return { kind: 'skip', text: 'Bỏ trống', Icon: Minus }
    return q.your_answer.selected_option_id === q.correct_option_id
      ? { kind: 'ok', text: 'Đúng', Icon: Check }
      : { kind: 'bad', text: 'Sai', Icon: X }
  }
  switch (q.grading_status) {
    case 'graded': {
      const full = q.score >= q.points
      const zero = !q.score
      return { kind: full ? 'ok' : zero ? 'bad' : 'part', text: `${formatNumber(q.score)}/${formatNumber(q.points)} điểm`, Icon: full ? Check : zero ? X : Minus }
    }
    case 'grading': return { kind: 'wait', text: 'AI đang chấm…', Icon: CircleNotch, spin: true }
    case 'needs_creator': return { kind: 'wait', text: 'Chờ người ra đề chấm', Icon: Clock }
    default: return { kind: 'warn', text: 'Chưa chấm được', Icon: Warning } // failed / quota
  }
}

function Result({ data, autoSubmitted, staleError }) {
  const [onlyWrong, setOnlyWrong] = useState(false)
  const { grading } = data
  const stillGrading = !!grading?.still_grading
  const qs = data.questions
  const mcq = qs.filter((q) => q.type === 'mcq')
  const correct = mcq.filter((q) => verdict(q).kind === 'ok').length
  const pending = qs.filter((q) => q.type === 'essay' && q.grading_status !== 'graded')
  // chưa chấm xong: cộng các câu đã có điểm ("điểm tạm")
  const partial = qs.reduce((s, q) => s + (q.score ?? 0), 0)
  const score = data.score ?? partial
  const max = data.max_score ?? qs.reduce((s, q) => s + q.points, 0)
  const pct = max > 0 ? Math.round((score / max) * 100) : 0
  const minutes = data.submitted_at && data.started_at ? Math.max(1, Math.round((data.submitted_at - data.started_at) / 60000)) : null
  const shown = onlyWrong ? qs.filter((q) => ['bad', 'skip', 'part'].includes(verdict(q).kind)) : qs

  return (
    <Page title={`Kết quả: ${data.exam_title}`} heading={data.exam_title} back={TO_TAKEN}>
      {/* vùng thông báo luôn có mặt: chuyển từ "đang chấm" sang "đã chấm xong" thì trình đọc màn hình đọc điểm */}
      <p className="sr-only" role="status">
        {stillGrading ? 'AI đang chấm tự luận.' : `Đã chấm. Điểm của bạn: ${formatNumber(score)} trên ${formatNumber(max)}.`}
      </p>
      {autoSubmitted && (
        <div className="notice notice-warn">
          <Clock size={18} aria-hidden="true" />
          <p>Hết giờ — bài đã được <strong>nộp tự động</strong> với những câu trả lời đã lưu.</p>
        </div>
      )}
      {staleError && stillGrading && (
        <div className="notice" role="status">
          <Warning size={18} aria-hidden="true" />
          <p>Tạm mất kết nối khi cập nhật điểm — trang sẽ tự thử lại.</p>
        </div>
      )}

      <section className="card score-card" aria-labelledby="score-title">
        <h2 id="score-title" className="sr-only">Điểm của bạn</h2>
        <div className="score-main">
          <p className="score-big">
            <strong>{formatNumber(score)}</strong>
            <span>/ {formatNumber(max)}</span>
          </p>
          <div className="score-bar" role="img" aria-label={`Đạt ${pct}% số điểm`}>
            <span style={{ width: `${pct}%` }} />
          </div>
          {stillGrading
            ? <span className="badge badge-warn"><CircleNotch size={13} className="spin" aria-hidden="true" />Điểm tạm — đang chấm tự luận</span>
            : data.status === 'graded'
              ? <span className="badge badge-ok"><Check size={13} weight="bold" aria-hidden="true" />Đã chấm xong</span>
              : <span className="badge badge-warn"><Clock size={13} aria-hidden="true" />Còn câu chưa chấm</span>}
        </div>
        <dl className="facts score-facts">
          {mcq.length > 0 && <div><dt>Trắc nghiệm đúng</dt><dd>{correct}/{mcq.length} câu</dd></div>}
          <div><dt>Nộp lúc</dt><dd>{formatTime(data.submitted_at)}</dd></div>
          {minutes && <div><dt>Thời gian làm</dt><dd>{minutes} phút</dd></div>}
        </dl>
      </section>

      {stillGrading && (
        <div className="notice grading-notice">
          <CircleNotch size={18} className="spin" aria-hidden="true" />
          <p>
            AI đang chấm {pending.length} câu tự luận (đã chấm {grading.graded}/{grading.total} câu).
            Trang tự cập nhật — bạn có thể rời đi và quay lại sau.
          </p>
        </div>
      )}
      {!stillGrading && pending.length > 0 && (
        <div className="notice notice-warn">
          <Warning size={18} aria-hidden="true" />
          <p>
            {pending.length} câu tự luận chưa có điểm (xem lý do ở từng câu). Điểm tổng sẽ cập nhật khi câu đó được chấm —
            quay lại trang này sau.
          </p>
        </div>
      )}

      <div className="result-toolbar">
        <h2 className="section-title">Chi tiết từng câu</h2>
        <label className="check-row compact">
          <input type="checkbox" checked={onlyWrong} onChange={(e) => setOnlyWrong(e.target.checked)} />
          <span>Chỉ xem câu sai / bỏ trống</span>
        </label>
      </div>
      {shown.length === 0 && (
        <div className="state-box"><p>Không có câu sai nào — làm tốt lắm!</p></div>
      )}
      <ol className="card-list q-list">
        {shown.map((q) => <ResultQuestion key={q.id} q={q} n={qs.indexOf(q) + 1} />)}
      </ol>

      <div className="card-actions result-actions">
        <Link className="ghost action" to="/exams?tab=taken">Bài tôi đã làm</Link>
        <Link className="ghost action" to={`/exams/${data.exam_id}/take`}>
          <ArrowCounterClockwise size={16} aria-hidden="true" /> Làm lại đề này
        </Link>
      </div>
    </Page>
  )
}

function ResultQuestion({ q, n }) {
  const v = verdict(q)
  const by = GRADED_BY[q.graded_by]
  return (
    <li className={`card q-card result-q result-${v.kind}`}>
      <div className="q-head">
        <h3 className="q-title">Câu {n}</h3>
        <span className="badge">{q.type === 'mcq' ? 'Trắc nghiệm' : 'Tự luận'}</span>
        <span className={`verdict verdict-${v.kind}`}>
          <v.Icon size={14} weight="bold" className={v.spin ? 'spin' : undefined} aria-hidden="true" />
          {v.text}
        </span>
        {q.type === 'mcq' && <span className="q-points">{formatNumber(q.score ?? 0)}/{formatNumber(q.points)} điểm</span>}
      </div>
      <p className="q-content">{q.content}</p>

      {q.type === 'mcq'
        ? (
          <ul className="opt-view">
            {q.options.map((o) => {
              const mine = o.id === q.your_answer.selected_option_id
              const right = o.id === q.correct_option_id
              return (
                <li key={o.id} className={right ? 'correct' : mine ? 'wrong' : ''}>
                  <span className="opt-letter">{o.label}.</span> {o.content}
                  <span className="opt-tags">
                    {mine && <span className={`opt-tag ${right ? '' : 'opt-tag-bad'}`}>Bạn chọn</span>}
                    {right && <span className="opt-tag"><Check size={13} weight="bold" aria-hidden="true" />Đáp án đúng</span>}
                  </span>
                </li>
              )
            })}
          </ul>
        )
        : (
          <>
            <p className="answer-label">Bài làm của bạn</p>
            {q.your_answer.essay_text?.trim()
              ? <blockquote className="essay-view">{q.your_answer.essay_text}</blockquote>
              : <p className="muted">Bạn bỏ trống câu này.</p>}
            {q.grading_status !== 'graded' && q.grading_message && (
              <p className={`grading-msg ${v.kind}`}>{q.grading_message}</p>
            )}
            {q.feedback && (
              <div className="feedback-box">
                <p className="feedback-head">
                  {by && <by.Icon size={15} aria-hidden="true" />}
                  Nhận xét{by ? ` · ${by.text}` : ''}
                </p>
                <p className="feedback-text">{q.feedback}</p>
              </div>
            )}
          </>
        )}

      {(q.explanation || q.source_page) && (
        <dl className="q-meta">
          {q.explanation && (<><dt>Giải thích</dt><dd>{q.explanation}</dd></>)}
          {q.source_page && (<><dt>Giáo trình</dt><dd>Trang {q.source_page}</dd></>)}
        </dl>
      )}
    </li>
  )
}
