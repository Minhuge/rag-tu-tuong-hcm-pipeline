import { ArrowClockwise, ArrowDown, ArrowUp, CaretRight, CircleNotch, Users, Warning } from '@phosphor-icons/react'
import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router'
import CopyLinkButton from '../components/CopyLinkButton.jsx'
import { getExamFull, listExamSubmissions } from '../examsApi.js'
import { formatNumber, formatTime } from '../format.js'
import { SUB_STATUS, personName } from '../submissionLabels.js'
import { useNumericId } from '../useNumericId.js'
import { usePolledLoad } from '../usePolledLoad.js'
import { NotFoundPage, Page } from './ExamPages.jsx'

/**
 * Bài nộp của một đề — người tạo đề xem (roadmap 4.8): thống kê, bảng bài nộp sắp xếp được, mở từng bài để xem và
 * chấm tay (ReviewSubmissionPage). Còn bài AI đang chấm → tự cập nhật.
 */
const NEVER = () => new Promise(() => {})

const COLUMNS = [
  { key: 'name', label: 'Người làm', value: (s) => personName(s.user).toLocaleLowerCase('vi') },
  { key: 'status', label: 'Trạng thái', value: (s) => ['in_progress', 'submitted', 'graded'].indexOf(s.status) },
  { key: 'score', label: 'Điểm', value: (s) => (s.score == null ? -1 : s.score) },
  { key: 'submitted', label: 'Nộp lúc', value: (s) => s.submitted_at ?? s.started_at },
]

export default function ExamResultsPage() {
  const id = useNumericId()
  const load = useCallback(async () => {
    const [exam, subs] = await Promise.all([getExamFull(id), listExamSubmissions(id)])
    return { exam, subs }
  }, [id])
  // còn bài đang chấm → cập nhật (thưa hơn trang kết quả: đây là danh sách, không phải một bài)
  const [state, reload] = usePolledLoad(id == null ? NEVER : load, {
    busy: (d) => d.subs.some((s) => s.status === 'submitted'), interval: 8000, slowInterval: 30000,
  })

  if (id == null) return <NotFoundPage />
  if (state.status === 'loading') {
    return (
      <Page title="Đang tải bài nộp…" back={{ to: '/exams', label: 'Bài kiểm tra của tôi' }}>
        <p className="sr-only" role="status">Đang tải…</p>
        <div className="card skeleton-card" aria-hidden="true">
          <span className="skeleton skeleton-title" /><span className="skeleton skeleton-line" />
          <span className="skeleton skeleton-line" /><span className="skeleton skeleton-line short" />
        </div>
      </Page>
    )
  }
  if (state.status === 'error') {
    const missing = state.error.status === 404
    return (
      <Page title={missing ? 'Không tìm thấy đề' : 'Không tải được bài nộp'} back={{ to: '/exams', label: 'Bài kiểm tra của tôi' }}>
        <div className="state-box state-error" role="alert">
          <Warning size={22} aria-hidden="true" />
          <div>
            <p className="state-title">{missing ? 'Đề không tồn tại hoặc không phải đề của bạn' : 'Có lỗi khi tải'}</p>
            <p className="muted">{missing ? 'Chỉ người tạo đề mới xem được bài nộp.' : state.error.message}</p>
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
  return <Results exam={state.data.exam} subs={state.data.subs} />
}

function Results({ exam, subs }) {
  const [sort, setSort] = useState({ key: 'submitted', dir: 'desc' })
  const rows = useMemo(() => {
    const col = COLUMNS.find((c) => c.key === sort.key)
    const sign = sort.dir === 'asc' ? 1 : -1
    return [...subs].sort((a, b) => {
      const x = col.value(a)
      const y = col.value(b)
      return (x < y ? -1 : x > y ? 1 : 0) * sign
    })
  }, [subs, sort])

  const done = subs.filter((s) => s.status !== 'in_progress')
  const graded = subs.filter((s) => s.status === 'graded' && s.score != null)
  const grading = subs.filter((s) => s.status === 'submitted').length
  const max = exam.questions.reduce((t, q) => t + q.points, 0)
  const scores = graded.map((s) => s.score)
  const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null
  const people = new Set(subs.map((s) => s.user.id)).size

  const toggle = (key) => setSort((s) => (s.key === key
    ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: key === 'name' ? 'asc' : 'desc' }))

  return (
    <Page title={`Bài nộp: ${exam.title}`} heading={exam.title} className="page-wide"
      back={{ to: `/exams/${exam.id}/edit`, label: 'Về đề thi' }}>
      <p className="muted page-sub">Bài nộp · tổng {formatNumber(max)} điểm · {exam.questions.length} câu</p>

      {subs.length === 0
        ? <NoSubmissions exam={exam} />
        : (
          <>
            <dl className="facts stats">
              <div><dt>Người làm</dt><dd>{people}</dd></div>
              <div><dt>Bài đã nộp</dt><dd>{done.length}{subs.length > done.length && <span className="muted"> · {subs.length - done.length} đang làm</span>}</dd></div>
              <div><dt>Điểm trung bình</dt><dd>{avg == null ? '—' : `${formatNumber(Math.round(avg * 100) / 100)}/${formatNumber(max)}`}</dd></div>
              <div><dt>Cao nhất · thấp nhất</dt><dd>{scores.length ? `${formatNumber(Math.max(...scores))} · ${formatNumber(Math.min(...scores))}` : '—'}</dd></div>
            </dl>
            {grading > 0 && (
              <div className="notice" role="status">
                <CircleNotch size={18} className="spin" aria-hidden="true" />
                <p>AI đang chấm tự luận của {grading} bài — bảng tự cập nhật.</p>
              </div>
            )}

            <div className="table-wrap">
              <table className="results-table">
                <caption className="sr-only">Bài nộp, bấm tiêu đề cột để sắp xếp</caption>
                <thead>
                  <tr>
                    {COLUMNS.map((c) => (
                      <th key={c.key} scope="col"
                        aria-sort={sort.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                        <button type="button" className="sort-btn" onClick={() => toggle(c.key)}>
                          {c.label}
                          {sort.key === c.key && (sort.dir === 'asc'
                            ? <ArrowUp size={13} weight="bold" aria-hidden="true" />
                            : <ArrowDown size={13} weight="bold" aria-hidden="true" />)}
                        </button>
                      </th>
                    ))}
                    <th scope="col"><span className="sr-only">Mở bài</span></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => {
                    const st = SUB_STATUS[s.status]
                    const to = `/exams/${exam.id}/results/${s.id}`
                    return (
                      <tr key={s.id}>
                        <td data-label="Người làm">
                          <Link className="person" to={to}>{personName(s.user)}</Link>
                          <span className="muted person-email">{s.user.email}</span>
                        </td>
                        <td data-label="Trạng thái"><span className={`badge ${st.className}`}>{st.text}</span></td>
                        <td data-label="Điểm" className="num">
                          {s.score == null ? <span className="muted">—</span>
                            : <><strong>{formatNumber(s.score)}</strong><span className="muted">/{formatNumber(s.max_score ?? max)}</span></>}
                        </td>
                        <td data-label="Nộp lúc">
                          {s.submitted_at ? formatTime(s.submitted_at) : <span className="muted">bắt đầu {formatTime(s.started_at)}</span>}
                        </td>
                        <td className="row-action">
                          <Link className="ghost action" to={to}
                            aria-label={`${s.status === 'submitted' || s.status === 'graded' ? 'Xem và chấm' : 'Xem'} bài của ${personName(s.user)}`}>
                            {s.status === 'in_progress' ? 'Xem' : 'Xem & chấm'}
                            <CaretRight size={15} aria-hidden="true" />
                          </Link>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
    </Page>
  )
}

function NoSubmissions({ exam }) {
  const published = exam.status === 'published'
  return (
    <div className="state-box">
      <Users size={22} aria-hidden="true" />
      <div>
        <p className="state-title">Chưa có ai làm đề này</p>
        {!published && (
          <p className="muted">Đề còn là bản nháp — <Link to={`/exams/${exam.id}/edit`}>xuất bản đề</Link> thì người khác mới làm được.</p>
        )}
        {published && exam.visibility === 'private' && (
          <p className="muted">Đề ở chế độ riêng tư nên chỉ bạn làm thử được.</p>
        )}
        {published && exam.visibility !== 'private' && (
          <>
            <p className="muted">Gửi link làm bài cho người học. Bài nộp sẽ hiện ở đây, câu tự luận được AI chấm sẵn.</p>
            <div className="state-action"><CopyLinkButton exam={exam} /></div>
          </>
        )}
      </div>
    </div>
  )
}
