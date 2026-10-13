import {
  ArrowClockwise, ArrowRight, Check, Eye, Globe, LinkSimple, Lock, PencilSimple, Play, Plus, Sparkle, Users,
  Warning,
} from '@phosphor-icons/react'
import { useRef } from 'react'
import { Link, useSearchParams } from 'react-router'
import CopyLinkButton from '../components/CopyLinkButton.jsx'
import { listMyExams, listMySubmissions } from '../examsApi.js'
import { formatNumber, formatTime } from '../format.js'
import { useLoad } from '../useLoad.js'
import { Page } from './ExamPages.jsx'

/**
 * "Bài kiểm tra của tôi" (roadmap 4.3): hai tab — đề tôi tạo / bài tôi đã làm.
 * Tab đang mở nằm trên URL (?tab=taken) → F5, nút Back, gửi link đều mở đúng tab.
 */
const TABS = [
  { id: 'created', label: 'Đề tôi tạo' },
  { id: 'taken', label: 'Bài tôi đã làm' },
]

const NONE = [] // giá trị ban đầu cố định (useLoad so sánh theo tham chiếu)

export default function MyExamsPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'taken' ? 'taken' : 'created'
  const [created, reloadCreated] = useLoad(listMyExams, NONE)
  const [taken, reloadTaken] = useLoad(listMySubmissions, NONE)
  const lists = { created, taken }
  const tabRefs = useRef({})

  const select = (id, focus = false) => {
    setParams(id === 'created' ? {} : { tab: id }, { replace: true })
    if (focus) tabRefs.current[id]?.focus()
  }

  // Phím mũi tên / Home / End chuyển tab (mẫu tablist của WAI-ARIA)
  const onTabKey = (e) => {
    const i = TABS.findIndex((t) => t.id === tab)
    const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 }[e.key]
    if (next === undefined) return
    e.preventDefault()
    select(TABS[(next + TABS.length) % TABS.length].id, true)
  }

  return (
    <Page title="Bài kiểm tra của tôi">
      <div className="page-actions">
        <Link className="primary-btn" to="/exams/new">
          <Plus size={16} weight="bold" aria-hidden="true" />
          Tạo đề mới
        </Link>
      </div>

      <div className="tabs" role="tablist" aria-label="Danh sách" onKeyDown={onTabKey}>
        {TABS.map(({ id, label }) => {
          const list = lists[id]
          return (
            <button key={id} type="button" role="tab" id={`tab-${id}`} aria-controls={`panel-${id}`}
              aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} className="tab"
              ref={(el) => { tabRefs.current[id] = el }} onClick={() => select(id)}>
              {label}
              {list.status === 'ok' && <span className="tab-count">{list.data.length}</span>}
            </button>
          )
        })}
      </div>

      <section role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}
        aria-busy={lists[tab].status === 'loading'}>
        {tab === 'created'
          ? <ListState state={created} onRetry={reloadCreated} empty={<NoExams />}>
            <ul className="card-list">{created.data.map((e) => <ExamCard key={e.id} exam={e} />)}</ul>
          </ListState>
          : <ListState state={taken} onRetry={reloadTaken} empty={<NoSubmissions />}>
            <ul className="card-list">{taken.data.map((s) => <SubmissionCard key={s.id} sub={s} />)}</ul>
          </ListState>}
      </section>
    </Page>
  )
}

/** Đang tải → khung xương; lỗi → thông báo + "Thử lại"; rỗng → empty; có dữ liệu → children. */
function ListState({ state, onRetry, empty, children }) {
  if (state.status === 'loading') {
    return (
      <>
        <p className="sr-only" role="status">Đang tải…</p>
        <ul className="card-list" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <li key={i} className="card skeleton-card">
              <span className="skeleton skeleton-title" />
              <span className="skeleton skeleton-line" />
              <span className="skeleton skeleton-line short" />
            </li>
          ))}
        </ul>
      </>
    )
  }
  if (state.status === 'error') {
    return (
      <div className="state-box state-error" role="alert">
        <Warning size={22} aria-hidden="true" />
        <div>
          <p className="state-title">Không tải được danh sách</p>
          <p className="muted">{state.error.message}</p>
          <button type="button" className="ghost state-action" onClick={onRetry}>
            <ArrowClockwise size={16} aria-hidden="true" />
            Thử lại
          </button>
        </div>
      </div>
    )
  }
  return state.data.length ? children : empty
}

function NoExams() {
  return (
    <div className="state-box">
      <div>
        <p className="state-title">Bạn chưa tạo đề nào</p>
        <p className="muted">Chọn một chương giáo trình để AI soạn đề nháp trong khoảng 30 giây, hoặc tự soạn từng câu.
          Đề chỉ đến tay người khác sau khi bạn xuất bản.</p>
        <Link className="primary-btn state-action" to="/exams/new">
          <Plus size={16} weight="bold" aria-hidden="true" />
          Tạo đề đầu tiên
        </Link>
      </div>
    </div>
  )
}

function NoSubmissions() {
  return (
    <div className="state-box">
      <div>
        <p className="state-title">Bạn chưa làm bài nào</p>
        <p className="muted">Mở đường link đề mà giảng viên hoặc bạn bè gửi để bắt đầu. Các bài bạn làm — kể cả đang làm dở
          — sẽ hiện ở đây kèm điểm.</p>
      </div>
    </div>
  )
}

// ---------- đề tôi tạo ----------
const VISIBILITY = {
  private: { Icon: Lock, label: 'Riêng tư' },
  link: { Icon: LinkSimple, label: 'Ai có link' },
  public: { Icon: Globe, label: 'Công khai' },
}

function ExamCard({ exam }) {
  const published = exam.status === 'published'
  const { Icon: VisIcon, label: visLabel } = VISIBILITY[exam.visibility]
  const shareable = published && exam.visibility !== 'private'
  return (
    <li className="card">
      <div className="card-head">
        <h2 className="card-title">
          <Link to={`/exams/${exam.id}/edit`}>{exam.title}</Link>
        </h2>
      </div>
      <div className="badges">
        {published
          ? <span className="badge badge-ok"><Check size={13} weight="bold" aria-hidden="true" />Đã xuất bản</span>
          : <span className="badge badge-warn"><PencilSimple size={13} aria-hidden="true" />Nháp</span>}
        {exam.origin === 'ai' && (
          <span className="badge"><Sparkle size={13} weight="fill" className="gold" aria-hidden="true" />AI tạo</span>
        )}
        <span className="badge"><VisIcon size={13} aria-hidden="true" />{visLabel}</span>
      </div>
      <p className="card-meta">
        {exam.question_count} câu
        {published && ` · ${exam.submission_count} bài nộp`}
        {` · sửa lúc ${formatTime(exam.updated_at)}`}
      </p>
      <div className="card-actions">
        <Link className="ghost action" to={`/exams/${exam.id}/edit`}>
          {published ? <Eye size={16} aria-hidden="true" /> : <PencilSimple size={16} aria-hidden="true" />}
          {published ? 'Xem đề' : 'Sửa đề'}
        </Link>
        {published && (
          <>
            <Link className="ghost action" to={`/exams/${exam.id}/results`}>
              <Users size={16} aria-hidden="true" />
              Bài nộp
            </Link>
            {shareable && <CopyLinkButton exam={exam} />}
            <Link className="ghost action" to={`/exams/${exam.id}/take`}>
              <Play size={16} aria-hidden="true" />
              Làm thử
            </Link>
          </>
        )}
      </div>
    </li>
  )
}

// ---------- bài tôi đã làm ----------
const SUB_STATUS = {
  in_progress: { label: 'Đang làm', className: 'badge-accent', Icon: PencilSimple },
  submitted: { label: 'Đang chấm', className: 'badge-warn', Icon: ArrowClockwise },
  graded: { label: 'Đã chấm', className: 'badge-ok', Icon: Check },
}

function SubmissionCard({ sub }) {
  const { label, className, Icon } = SUB_STATUS[sub.status]
  const doing = sub.status === 'in_progress'
  const to = doing ? `/exams/${sub.exam_id}/take` : `/submissions/${sub.id}`
  const when = doing
    ? `bắt đầu lúc ${formatTime(sub.started_at)}${sub.deadline ? ` · hạn nộp ${formatTime(sub.deadline)}` : ''}`
    : `nộp lúc ${formatTime(sub.submitted_at)}`
  return (
    <li className="card">
      <div className="card-head">
        <h2 className="card-title"><Link to={to}>{sub.exam_title}</Link></h2>
        {sub.status === 'graded' && sub.score != null && (
          <p className="score" aria-label={`Điểm ${formatNumber(sub.score)} trên ${formatNumber(sub.max_score)}`}>
            <strong>{formatNumber(sub.score)}</strong>
            <span>/ {formatNumber(sub.max_score)}</span>
          </p>
        )}
      </div>
      <div className="badges">
        <span className={`badge ${className}`}><Icon size={13} weight="bold" aria-hidden="true" />{label}</span>
      </div>
      <p className="card-meta">{when}</p>
      <div className="card-actions">
        <Link className="ghost action" to={to}>
          {doing ? 'Làm tiếp' : 'Xem kết quả'}
          <ArrowRight size={16} aria-hidden="true" />
        </Link>
      </div>
    </li>
  )
}
