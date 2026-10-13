import {
  ArrowClockwise, Check, CircleNotch, Globe, LinkSimple, Lock, Play, Plus, RocketLaunch, Trash, Users, Warning,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import CopyLinkButton from '../components/CopyLinkButton.jsx'
import GenerationReport from '../components/GenerationReport.jsx'
import SaveStatus from '../components/SaveStatus.jsx'
import {
  deleteExam, deleteQuestion, getExamFull, publishExam, reorderQuestions, takeExamUrl, updateExam,
} from '../examsApi.js'
import { formatNumber, formatTime } from '../format.js'
import { useAutosave, worstStatus } from '../useAutosave.js'
import { useLoad } from '../useLoad.js'
import { useNumericId } from '../useNumericId.js'
import { NotFoundPage, Page } from './ExamPages.jsx'
import { TYPE_LABEL, blankDraft, newKey, publishProblems, toDraft } from '../questionDraft.js'
import { QuestionEditor, QuestionView } from './QuestionEditor.jsx'

/**
 * Trình sửa đề (roadmap 4.5).
 *   Bản nháp: thông tin đề + từng câu TỰ LƯU khi ngừng gõ; thêm / xoá / đổi thứ tự câu; Xuất bản (kiểm tra mọi câu
 *             trước, lỗi hiện tại đúng câu; lỗi máy chủ "Câu 3: …" cũng chỉ về câu 3).
 *   Đã xuất bản: chỉ xem; link / mã chia sẻ; xem bài nộp, làm thử.
 */
const TO_EXAMS = { to: '/exams', label: 'Bài kiểm tra của tôi' }

export default function EditExamPage() {
  const id = useNumericId()
  const load = useCallback(() => getExamFull(id), [id])
  const [exam, reload] = useLoad(id == null ? NEVER : load)

  if (id == null) return <NotFoundPage />
  if (exam.status === 'loading') {
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
  if (exam.status === 'error') {
    const missing = exam.error.status === 404
    return (
      <Page title={missing ? 'Không tìm thấy đề' : 'Không tải được đề'} back={TO_EXAMS}>
        <div className="state-box state-error" role="alert">
          <Warning size={22} aria-hidden="true" />
          <div>
            <p className="state-title">{missing ? 'Đề không tồn tại hoặc không phải đề của bạn' : 'Có lỗi khi tải đề'}</p>
            <p className="muted">{missing ? 'Có thể đề đã bị xoá.' : exam.error.message}</p>
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
  return exam.data.status === 'published'
    ? <PublishedExam exam={exam.data} />
    : <DraftEditor key={exam.data.id} exam={exam.data} onPublished={reload} />
}

const NEVER = () => new Promise(() => {}) // id sai trên URL: không gọi API (trang hiện "không tìm thấy")

async function confirmDeleteExam(exam, navigate, setNotice) {
  const extra = exam.status === 'published' ? ' cùng MỌI bài làm của người khác' : ''
  if (!window.confirm(`Xoá đề "${exam.title}"${extra}? Không hoàn tác được.`)) return
  try {
    await deleteExam(exam.id)
    navigate('/exams', { replace: true })
  } catch (e) {
    setNotice(e.message)
  }
}

// ====================== bản nháp ======================
function DraftEditor({ exam, onPublished }) {
  const navigate = useNavigate()
  const { state } = useLocation()
  const [title, setTitle] = useState(exam.title)
  const [items, setItems] = useState(() =>
    exam.questions.map((q) => ({ key: newKey(), id: q.id, type: q.type, draft: toDraft(q) })))
  const [statuses, setStatuses] = useState({})
  const [attempted, setAttempted] = useState(false) // đã bấm Xuất bản → hiện lỗi chặn xuất bản ngay khi sửa
  const [serverProblem, setServerProblem] = useState(null) // { key, message } từ máy chủ khi publish
  const [notice, setNotice] = useState(null)
  const [publishing, setPublishing] = useState(false)
  const editors = useRef(new Map()) // key → { flush, discard } của từng khối (thông tin đề + từng câu)
  const itemsRef = useRef(items)
  const statusesRef = useRef(statuses)
  const reorderChain = useRef(Promise.resolve())
  useEffect(() => {
    itemsRef.current = items
    statusesRef.current = statuses
  })

  const onStatus = useCallback((key, s) => setStatuses((prev) => (prev[key] === s ? prev : { ...prev, [key]: s })), [])
  const register = useCallback((key, api) => {
    if (api) editors.current.set(key, api)
    else editors.current.delete(key)
  }, [])
  const onChange = useCallback((key, draft) => {
    setItems((list) => list.map((it) => (it.key === key ? { ...it, draft } : it)))
    setServerProblem((p) => (p?.key === key ? null : p))
  }, [])
  const onSaved = useCallback((key, q) => {
    setItems((list) => list.map((it) => (it.key === key && it.id !== q.id ? { ...it, id: q.id } : it)))
  }, [])

  const overall = worstStatus(Object.entries(statuses)
    .filter(([key]) => key === 'settings' || items.some((it) => it.key === key)).map(([, s]) => s))
  useEffect(() => {
    if (overall === 'saved') return undefined
    const warn = (e) => e.preventDefault() // còn thay đổi chưa lưu → trình duyệt hỏi trước khi đóng tab / F5
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [overall])

  const problems = useMemo(() => {
    const out = {}
    for (const it of items) {
      const list = attempted ? publishProblems(it.draft, it.type) : []
      if (serverProblem?.key === it.key) list.push(serverProblem.message)
      if (list.length) out[it.key] = list
    }
    return out
  }, [items, attempted, serverProblem])

  const focusCard = (key) => requestAnimationFrame(() => {
    const el = document.getElementById(`q-${key}`)
    el?.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
    el?.focus({ preventScroll: true })
  })

  const flushAll = async () => (await Promise.all([...editors.current.values()].map((e) => e.flush()))).every(Boolean)

  const add = (type) => {
    const key = newKey()
    setItems((list) => [...list, { key, id: null, type, draft: blankDraft(type) }])
    requestAnimationFrame(() => {
      document.getElementById(`${key}-content`)?.focus()
      document.getElementById(`q-${key}`)?.scrollIntoView({ block: 'center' })
    })
  }

  const move = async (key, dir) => {
    setNotice(null)
    if (itemsRef.current.some((it) => it.id == null)) {
      await flushAll()
      if (itemsRef.current.some((it) => it.id == null)) {
        setNotice('Câu mới thêm chưa lưu được (còn ô trống) — nhập nội dung trước khi đổi thứ tự.')
        return
      }
    }
    const before = itemsRef.current
    const i = before.findIndex((it) => it.key === key)
    const j = i + dir
    if (i < 0 || j < 0 || j >= before.length) return
    const after = [...before]
    ;[after[i], after[j]] = [after[j], after[i]]
    setItems(after)
    // focus đi theo câu vừa chuyển; tới đầu / cuối thì nút cùng chiều bị khoá → sang nút còn lại
    requestAnimationFrame(() => {
      const same = document.getElementById(`${key}-${dir < 0 ? 'up' : 'down'}`)
      ;(same && !same.disabled ? same : document.getElementById(`${key}-${dir < 0 ? 'down' : 'up'}`))?.focus()
    })
    reorderChain.current = reorderChain.current.then(() => reorderQuestions(exam.id, after.map((it) => it.id)))
      .catch((e) => {
        setItems(before)
        setNotice(`Chưa đổi được thứ tự: ${e.message}`)
      })
  }

  const remove = async (key) => {
    const list = itemsRef.current
    const i = list.findIndex((it) => it.key === key)
    const it = list[i]
    const blank = !it.draft.content.trim()
    if (!(blank && it.id == null) && !window.confirm(`Xoá câu ${i + 1}? Không hoàn tác được.`)) return
    setNotice(null)
    if (it.id != null) {
      try {
        await deleteQuestion(exam.id, it.id)
      } catch (e) {
        if (e.status !== 404) {
          setNotice(`Chưa xoá được câu ${i + 1}: ${e.message}`)
          return
        }
      }
    }
    editors.current.get(key)?.discard() // câu đã xoá: không "lưu nốt" khi thẻ biến mất (sẽ tạo lại câu ma)
    setItems((l) => l.filter((x) => x.key !== key))
    const next = list[i + 1] ?? list[i - 1]
    requestAnimationFrame(() => (next ? document.getElementById(`q-${next.key}`) : document.getElementById('add-mcq'))?.focus())
  }

  const publish = async () => {
    if (publishing) return
    setNotice(null)
    setServerProblem(null)
    setAttempted(true)
    const list = itemsRef.current
    if (!list.length) {
      setNotice('Đề cần ít nhất một câu hỏi.')
      return
    }
    setPublishing(true)
    try {
      if (!(await flushAll())) {
        setNotice('Còn chỗ chưa lưu được — sửa các ô báo lỗi rồi xuất bản lại.')
        const bad = itemsRef.current.find((x) => ['invalid', 'error'].includes(statusesRef.current[x.key]))
        if (bad) focusCard(bad.key)
        else document.getElementById('settings')?.focus()
        return
      }
      const firstProblem = list.find((x) => publishProblems(x.draft, x.type).length)
      if (firstProblem) {
        setNotice('Một số câu chưa đủ điều kiện xuất bản — xem các câu được đánh dấu.')
        focusCard(firstProblem.key)
        return
      }
      if (!window.confirm('Xuất bản đề? Sau khi xuất bản sẽ không sửa được nữa, và người khác bắt đầu làm được '
        + 'theo chế độ chia sẻ bạn đã chọn.')) return
      await publishExam(exam.id)
      onPublished()
    } catch (e) {
      const target = e.question ? itemsRef.current[e.question - 1] : null
      if (target) {
        setServerProblem({ key: target.key, message: e.message.replace(/^Câu \d+:\s*/, '') })
        focusCard(target.key)
      } else {
        setNotice(e.message)
      }
    } finally {
      setPublishing(false)
    }
  }
  const totalPoints = items.reduce((sum, it) => sum + (Number(it.draft.points) || 0), 0)
  return (
    <Page title={`Sửa đề: ${title}`} heading={title} back={TO_EXAMS}>
      <div className="editor-bar">
        <div className="editor-bar-info">
          <span className="badge badge-warn">Nháp</span>
          <SaveStatus status={overall} />
          <span className="muted editor-count">{items.length} câu · {formatNumber(totalPoints)} điểm</span>
        </div>
        <button type="button" className="primary-btn" onClick={publish} aria-disabled={publishing}>
          {publishing ? <CircleNotch size={16} className="spin" aria-hidden="true" />
            : <RocketLaunch size={16} aria-hidden="true" />}
          {publishing ? 'Đang kiểm tra…' : 'Xuất bản'}
        </button>
      </div>
      {/* trình đọc màn hình chỉ được báo khi có lỗi — "đang lưu / đã lưu" đổi liên tục khi gõ, đọc sẽ rất phiền */}
      <p className="sr-only" role="status">
        {overall === 'error' ? 'Có thay đổi chưa lưu được.' : overall === 'invalid' ? 'Có ô nhập bị lỗi, chưa lưu.' : ''}
      </p>

      {state?.generation && (
        // vừa tạo bằng AI (trang tạo đề gửi kèm). Ẩn → xoá khỏi history để F5 / Back không hiện lại
        <GenerationReport generation={state.generation} counts={state.counts}
          onClose={() => navigate('.', { replace: true, state: null })} />
      )}
      {notice && (
        <div className="notice notice-error" role="alert">
          <Warning size={18} aria-hidden="true" />
          <p>{notice}</p>
        </div>
      )}

      <ExamSettings exam={exam} onTitle={setTitle} onStatus={onStatus} register={register}
        onDelete={() => confirmDeleteExam(exam, navigate, setNotice)} />

      <section className="editor-section" aria-labelledby="questions-title">
        <h2 id="questions-title" className="section-title">Câu hỏi</h2>
        {items.length === 0 && (
          <div className="state-box">
            <p>Đề chưa có câu nào. Thêm câu trắc nghiệm hoặc tự luận bằng các nút bên dưới.</p>
          </div>
        )}
        <ol className="card-list q-list">
          {items.map((it, i) => (
            <QuestionEditor key={it.key} examId={exam.id} item={it} index={i} total={items.length}
              problems={problems[it.key] ?? []} onChange={onChange} onSaved={onSaved} onStatus={onStatus}
              register={register} onMove={move} onDelete={remove} />
          ))}
        </ol>
        <div className="add-bar">
          <button type="button" id="add-mcq" className="ghost add-btn" onClick={() => add('mcq')}>
            <Plus size={16} aria-hidden="true" /> Thêm câu {TYPE_LABEL.mcq.toLowerCase()}
          </button>
          <button type="button" className="ghost add-btn" onClick={() => add('essay')}>
            <Plus size={16} aria-hidden="true" /> Thêm câu {TYPE_LABEL.essay.toLowerCase()}
          </button>
        </div>
      </section>
    </Page>
  )
}

// ---------- thông tin đề ----------
const VISIBILITY = [
  { value: 'private', Icon: Lock, title: 'Riêng tư', text: 'Chỉ bạn xem và làm thử.' },
  { value: 'link', Icon: LinkSimple, title: 'Ai có link', text: 'Người nhận link (kèm mã bí mật) mới làm được.' },
  { value: 'public', Icon: Globe, title: 'Công khai', text: 'Ai đã đăng nhập và mở link đề đều làm được.' },
]

/** ms → giá trị cho <input type="datetime-local"> theo giờ máy người dùng. */
const toLocalInput = (ms) => (ms == null ? '' : new Date(ms - new Date(ms).getTimezoneOffset() * 60000).toISOString().slice(0, 16))
const fromLocalInput = (v) => (v ? new Date(v).toISOString() : null)

function settingsDraft(e) {
  return {
    title: e.title, description: e.description ?? '', visibility: e.visibility,
    duration: e.duration_minutes == null ? '' : String(e.duration_minutes),
    open_at: toLocalInput(e.open_at), close_at: toLocalInput(e.close_at), shuffle_questions: e.shuffle_questions,
  }
}

function validateSettings(d) {
  const e = {}
  if (!d.title.trim()) e.title = 'Nhập tiêu đề'
  if (d.duration.trim() && !/^[1-9]\d*$/.test(d.duration.trim())) e.duration_minutes = 'Số phút là số nguyên ≥ 1'
  if (d.open_at && d.close_at && new Date(d.close_at) <= new Date(d.open_at)) e.close_at = 'Giờ đóng phải sau giờ mở'
  return e
}

function ExamSettings({ exam, onTitle, onStatus, register, onDelete }) {
  const [draft, setDraft] = useState(() => settingsDraft(exam))
  const { status, errors, error, flush } = useAutosave(draft, {
    validate: validateSettings,
    save: async (d) => {
      const saved = await updateExam(exam.id, {
        title: d.title.trim(), description: d.description.trim() || null, visibility: d.visibility,
        duration_minutes: d.duration.trim() ? Number(d.duration) : null,
        open_at: fromLocalInput(d.open_at), close_at: fromLocalInput(d.close_at), shuffle_questions: d.shuffle_questions,
      })
      onTitle(saved.title)
    },
  })
  useEffect(() => onStatus('settings', status), [status, onStatus])
  useEffect(() => {
    register('settings', { flush, discard: () => {} })
    return () => register('settings', null)
  }, [flush, register])

  const set = (field) => (e) => setDraft((d) => ({ ...d, [field]: e.target.value }))
  const err = (field) => errors[field] && (
    <p id={`set-${field}-err`} className="field-error"><Warning size={15} weight="bold" aria-hidden="true" />{errors[field]}</p>
  )
  const described = (field, hint) => [hint && `set-${field}-hint`, errors[field] && `set-${field}-err`]
    .filter(Boolean).join(' ') || undefined

  return (
    <section className="card editor-section settings" id="settings" tabIndex={-1} aria-labelledby="settings-title">
      <div className="q-head">
        <h2 id="settings-title" className="section-title">Thông tin đề</h2>
        <SaveStatus status={status} compact onRetry={flush} />
      </div>
      {status === 'error' && !Object.keys(errors).length && (
        <div className="notice notice-error" role="alert">
          <Warning size={18} aria-hidden="true" /><p>Chưa lưu được: {error?.message}</p>
        </div>
      )}

      <div className="field">
        <label className="field-label" htmlFor="set-title">Tiêu đề</label>
        <input id="set-title" className="text-input" type="text" maxLength={200} autoComplete="off" value={draft.title}
          onChange={set('title')} aria-invalid={!!errors.title} aria-describedby={described('title')} />
        {err('title')}
      </div>
      <div className="field">
        <label className="field-label" htmlFor="set-description">
          Mô tả <span className="field-optional">(không bắt buộc)</span>
        </label>
        <textarea id="set-description" className="text-input text-area" rows={2} maxLength={5000} value={draft.description}
          onChange={set('description')} aria-describedby={described('description', true)} />
        <p id="set-description-hint" className="field-hint">Hiện ở đầu bài làm: phạm vi kiến thức, lưu ý…</p>
        {err('description')}
      </div>

      <fieldset className="choice-group">
        <legend className="field-label">Ai được làm bài</legend>
        <div className="visibility-grid">
          {VISIBILITY.map(({ value, Icon, title, text }) => (
            <label key={value} className={`choice-card compact ${draft.visibility === value ? 'checked' : ''}`}>
              <input type="radio" name="visibility" value={value} checked={draft.visibility === value}
                onChange={() => setDraft((d) => ({ ...d, visibility: value }))} />
              <Icon size={18} aria-hidden="true" />
              <span><span className="choice-title">{title}</span><span className="choice-text">{text}</span></span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="field-row">
        <div className="field">
          <label className="field-label" htmlFor="set-duration">Thời gian làm bài (phút)</label>
          <input id="set-duration" className="text-input number-input" type="number" inputMode="numeric" min={1}
            step={1} value={draft.duration} onChange={set('duration')} aria-invalid={!!errors.duration_minutes}
            aria-describedby={described('duration_minutes', true)} />
          <p id="set-duration_minutes-hint" className="field-hint">Bỏ trống = không giới hạn</p>
          {err('duration_minutes')}
        </div>
        <div className="field">
          <label className="field-label" htmlFor="set-open">Mở đề lúc</label>
          <input id="set-open" className="text-input date-input" type="datetime-local" value={draft.open_at}
            onChange={set('open_at')} aria-invalid={!!errors.open_at} aria-describedby={described('open_at', true)} />
          <p id="set-open_at-hint" className="field-hint">Bỏ trống = mở ngay khi xuất bản</p>
          {err('open_at')}
        </div>
        <div className="field">
          <label className="field-label" htmlFor="set-close">Đóng đề lúc</label>
          <input id="set-close" className="text-input date-input" type="datetime-local" value={draft.close_at}
            onChange={set('close_at')} aria-invalid={!!errors.close_at} aria-describedby={described('close_at', true)} />
          <p id="set-close_at-hint" className="field-hint">Bỏ trống = không đóng</p>
          {err('close_at')}
        </div>
      </div>

      <label className="check-row">
        <input type="checkbox" checked={draft.shuffle_questions}
          onChange={(e) => setDraft((d) => ({ ...d, shuffle_questions: e.target.checked }))} />
        <span>
          <span className="choice-title">Trộn thứ tự câu</span>
          <span className="choice-text">Mỗi người làm bài thấy các câu theo một thứ tự khác nhau.</span>
        </span>
      </label>

      <div className="danger-zone">
        <button type="button" className="ghost danger-btn" onClick={onDelete}>
          <Trash size={16} aria-hidden="true" /> Xoá đề này
        </button>
      </div>
    </section>
  )
}

// ====================== đã xuất bản ======================
const VIS_LABEL = { private: 'Riêng tư', link: 'Ai có link', public: 'Công khai' }

function PublishedExam({ exam }) {
  const navigate = useNavigate()
  const [notice, setNotice] = useState(null)
  const total = exam.questions.reduce((s, q) => s + q.points, 0)
  const facts = [
    ['Thời gian làm bài', exam.duration_minutes ? `${exam.duration_minutes} phút` : 'Không giới hạn'],
    ['Mở đề', exam.open_at ? formatTime(exam.open_at) : 'Ngay khi xuất bản'],
    ['Đóng đề', exam.close_at ? formatTime(exam.close_at) : 'Không đóng'],
    ['Trộn thứ tự câu', exam.shuffle_questions ? 'Có' : 'Không'],
  ]
  return (
    <Page title={exam.title} back={TO_EXAMS}>
      <div className="badges published-badges">
        <span className="badge badge-ok"><Check size={13} weight="bold" aria-hidden="true" />Đã xuất bản</span>
        <span className="badge">{VIS_LABEL[exam.visibility]}</span>
        <span className="badge">{exam.questions.length} câu · {formatNumber(total)} điểm</span>
      </div>
      {exam.description && <p className="exam-desc">{exam.description}</p>}
      {notice && (
        <div className="notice notice-error" role="alert"><Warning size={18} aria-hidden="true" /><p>{notice}</p></div>
      )}

      <section className="card share-box" aria-labelledby="share-title">
        <h2 id="share-title" className="section-title">Chia sẻ</h2>
        {exam.visibility === 'private'
          ? <p className="muted">Đề riêng tư: chỉ bạn làm thử được. Đề đã xuất bản nên không đổi được chế độ chia sẻ —
            muốn cho người khác làm, hãy tạo đề mới.</p>
          : (
            <>
              <p className="muted">
                {exam.visibility === 'link'
                  ? 'Gửi link này cho người làm bài — link có kèm mã bí mật, ai không có link thì không vào được.'
                  : 'Đề công khai: ai đã đăng nhập và mở link này đều làm được.'}
              </p>
              <div className="share-row">
                <input className="text-input share-url" readOnly value={takeExamUrl(exam)} aria-label="Link làm bài"
                  onFocus={(e) => e.target.select()} />
                <CopyLinkButton exam={exam} />
              </div>
              {exam.visibility === 'link' && (
                <p className="field-hint">Mã chia sẻ: <code className="share-code">{exam.share_code}</code></p>
              )}
            </>
          )}
        <div className="card-actions">
          <Link className="ghost action" to={`/exams/${exam.id}/results`}><Users size={16} aria-hidden="true" />Bài nộp</Link>
          <Link className="ghost action" to={`/exams/${exam.id}/take`}><Play size={16} aria-hidden="true" />Làm thử</Link>
          <button type="button" className="ghost action danger-btn"
            onClick={() => confirmDeleteExam(exam, navigate, setNotice)}>
            <Trash size={16} aria-hidden="true" />Xoá đề
          </button>
        </div>
      </section>

      <dl className="facts">
        {facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
      <p className="muted locked-note">Đề đã xuất bản nên không sửa được nữa — người làm bài luôn thấy đúng đề đã giao.</p>

      <h2 className="section-title">Câu hỏi</h2>
      <ol className="card-list q-list">
        {exam.questions.map((q, i) => <QuestionView key={q.id} q={q} index={i} />)}
      </ol>
    </Page>
  )
}
