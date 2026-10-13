import {
  ArrowDown, ArrowUp, Check, CheckCircle, Plus, Trash, Warning, X,
} from '@phosphor-icons/react'
import { useEffect, useRef } from 'react'
import { addQuestion, updateQuestion } from '../examsApi.js'
import { LETTERS, MAX_OPTIONS, TYPE_LABEL, newKey, toPayload, validateDraft } from '../questionDraft.js'
import { formatNumber } from '../format.js'
import { useAutosave } from '../useAutosave.js'
import SaveStatus from '../components/SaveStatus.jsx'

/**
 * Một câu hỏi trong trình sửa đề (4.5): tự lưu khi ngừng gõ, lỗi hiện cạnh ô, nút lên / xuống / xoá.
 * Câu mới thêm (chưa có id) được tạo trên máy chủ ở lần lưu đầu tiên — khi đã nhập đủ nội dung.
 */

function FieldError({ id, children }) {
  if (!children) return null
  return (
    <p id={id} className="field-error">
      <Warning size={15} weight="bold" aria-hidden="true" />
      {children}
    </p>
  )
}

/**
 * item: { key, id (null = chưa lưu lần nào), type, draft }
 * onChange(key, draft) · onSaved(key, question từ máy chủ) · onStatus(key, status) · register(key, { flush, discard } | null)
 */
export function QuestionEditor({
  examId, item, index, total, problems, onChange, onSaved, onStatus, register, onMove, onDelete,
}) {
  const { key, type, draft } = item
  const idRef = useRef(item.id)
  const { status, errors, error, flush, discard } = useAutosave(draft, {
    validate: (d) => validateDraft(d, type),
    save: async (d) => {
      const payload = toPayload(d, type)
      const saved = idRef.current == null
        ? await addQuestion(examId, { type, ...payload })
        : await updateQuestion(examId, idRef.current, payload)
      idRef.current = saved.id
      onSaved(key, saved)
    },
  })

  useEffect(() => onStatus(key, status), [key, status, onStatus])
  useEffect(() => {
    register(key, { flush, discard })
    return () => register(key, null)
  }, [key, flush, discard, register])

  const set = (field) => (e) => onChange(key, { ...draft, [field]: e.target.value })
  const setOptions = (options) => onChange(key, { ...draft, options })
  const id = (field) => `${key}-${field}`
  const describe = (field, hint) => [hint && id(`${field}-hint`), errors[field] && id(`${field}-err`)]
    .filter(Boolean).join(' ') || undefined

  const n = index + 1
  return (
    <li className={`card q-card ${problems.length ? 'has-problem' : ''}`} id={`q-${key}`} tabIndex={-1}
      aria-labelledby={id('title')}>
      <div className="q-head">
        <h3 id={id('title')} className="q-title">Câu {n}</h3>
        <span className="badge">{TYPE_LABEL[type]}</span>
        <SaveStatus status={status} compact onRetry={flush} />
        <div className="q-tools">
          <button type="button" className="ghost icon-only tool" id={id('up')} disabled={index === 0}
            onClick={() => onMove(key, -1)} aria-label={`Đưa câu ${n} lên trên`} title="Đưa lên">
            <ArrowUp size={17} aria-hidden="true" />
          </button>
          <button type="button" className="ghost icon-only tool" id={id('down')} disabled={index === total - 1}
            onClick={() => onMove(key, 1)} aria-label={`Đưa câu ${n} xuống dưới`} title="Đưa xuống">
            <ArrowDown size={17} aria-hidden="true" />
          </button>
          <button type="button" className="ghost icon-only tool tool-danger" onClick={() => onDelete(key)}
            aria-label={`Xoá câu ${n}`} title="Xoá câu">
            <Trash size={17} aria-hidden="true" />
          </button>
        </div>
      </div>

      {problems.length > 0 && (
        <div className="notice notice-error q-problems" role="alert">
          <Warning size={18} aria-hidden="true" />
          <p>Chưa xuất bản được: {problems.join('; ')}.</p>
        </div>
      )}
      {status === 'error' && !Object.keys(errors).length && (
        <div className="notice notice-error" role="alert">
          <Warning size={18} aria-hidden="true" />
          <p>Chưa lưu được câu này: {error?.message}</p>
        </div>
      )}

      <div className="field">
        <label className="field-label" htmlFor={id('content')}>Nội dung câu hỏi</label>
        <textarea id={id('content')} className="text-input text-area" rows={2} value={draft.content}
          onChange={set('content')} aria-invalid={!!errors.content} aria-describedby={describe('content')} />
        <FieldError id={id('content-err')}>{errors.content}</FieldError>
      </div>

      {type === 'mcq'
        ? <OptionsEditor qKey={key} n={n} options={draft.options} errors={errors} onChange={setOptions} />
        : (
          <div className="field">
            <label className="field-label" htmlFor={id('rubric')}>Barem chấm</label>
            <textarea id={id('rubric')} className="text-input text-area" rows={3} value={draft.rubric}
              onChange={set('rubric')} aria-describedby={describe('rubric', true)}
              placeholder={'- Nêu được ý thứ nhất (1 điểm)\n- Nêu được ý thứ hai (1 điểm)'} />
            <p id={id('rubric-hint')} className="field-hint">
              Mỗi ý một dòng bắt đầu bằng gạch đầu dòng, điểm ghi cuối dòng. AI chấm theo barem này.
            </p>
            {!draft.rubric.trim() && (
              <p className="field-warn">
                <Warning size={15} aria-hidden="true" />
                Không có barem thì AI không chấm câu này — bạn phải chấm tay từng bài.
              </p>
            )}
            <FieldError id={id('rubric-err')}>{errors.rubric}</FieldError>
          </div>
        )}

      <div className="field-row">
        <div className="field">
          <label className="field-label" htmlFor={id('points')}>Điểm</label>
          <input id={id('points')} className="text-input number-input" type="number" inputMode="decimal" min={0}
            step={0.25} value={draft.points} onChange={set('points')} aria-invalid={!!errors.points}
            aria-describedby={describe('points')} />
          <FieldError id={id('points-err')}>{errors.points}</FieldError>
        </div>
        <div className="field">
          <label className="field-label" htmlFor={id('source_page')}>
            Trang giáo trình <span className="field-optional">(không bắt buộc)</span>
          </label>
          <input id={id('source_page')} className="text-input number-input" type="number" inputMode="numeric" min={1}
            step={1} value={draft.source_page} onChange={set('source_page')} aria-invalid={!!errors.source_page}
            aria-describedby={describe('source_page')} />
          <FieldError id={id('source_page-err')}>{errors.source_page}</FieldError>
        </div>
      </div>

      <div className="field">
        <label className="field-label" htmlFor={id('explanation')}>
          Giải thích <span className="field-optional">(không bắt buộc)</span>
        </label>
        <textarea id={id('explanation')} className="text-input text-area" rows={2} value={draft.explanation}
          onChange={set('explanation')} aria-describedby={describe('explanation', true)} />
        <p id={id('explanation-hint')} className="field-hint">Người làm bài thấy sau khi nộp.</p>
        <FieldError id={id('explanation-err')}>{errors.explanation}</FieldError>
      </div>
    </li>
  )
}

/** Lựa chọn A, B, C…: đáp án đúng chọn bằng radio → giao diện bảo đảm chỉ một đáp án. */
function OptionsEditor({ qKey, n, options, errors, onChange }) {
  const listRef = useRef(null)
  const update = (i, patch) => onChange(options.map((o, j) => (j === i ? { ...o, ...patch } : o)))
  const markCorrect = (i) => onChange(options.map((o, j) => ({ ...o, is_correct: j === i })))
  const remove = (i) => {
    onChange(options.filter((_, j) => j !== i))
    // focus ô liền trước (hoặc nút thêm) thay vì để focus rơi về đầu trang
    requestAnimationFrame(() => {
      const inputs = listRef.current?.querySelectorAll('.opt-input')
      ;(inputs?.[Math.max(0, i - 1)] ?? listRef.current?.querySelector('.opt-add'))?.focus()
    })
  }
  const add = () => {
    onChange([...options, { key: newKey(), content: '', is_correct: false }])
    requestAnimationFrame(() => [...(listRef.current?.querySelectorAll('.opt-input') ?? [])].at(-1)?.focus())
  }
  const hasCorrect = options.some((o) => o.is_correct)

  return (
    <fieldset className="choice-group options" ref={listRef}>
      <legend className="field-label">Lựa chọn — bấm vòng tròn để chọn đáp án đúng</legend>
      <ul className="opt-list">
        {options.map((o, i) => {
          const label = LETTERS[i]
          const err = errors[`options.${i}.content`]
          const errId = `${qKey}-opt-${i}-err`
          return (
            <li key={o.key} className={`opt-row ${o.is_correct ? 'correct' : ''}`}>
              <label className="opt-correct" title="Đáp án đúng">
                <input type="radio" name={`${qKey}-correct`} checked={o.is_correct} onChange={() => markCorrect(i)}
                  aria-label={`Câu ${n}: ${label} là đáp án đúng`} />
                <span className="opt-mark" aria-hidden="true">
                  {o.is_correct ? <CheckCircle size={22} weight="fill" /> : <span className="opt-circle" />}
                </span>
              </label>
              <span className="opt-letter" aria-hidden="true">{label}.</span>
              <div className="opt-field">
                <input className="text-input opt-input" type="text" value={o.content}
                  onChange={(e) => update(i, { content: e.target.value })} aria-label={`Câu ${n}, lựa chọn ${label}`}
                  aria-invalid={!!err} aria-describedby={err ? errId : undefined} />
                {o.is_correct && <span className="opt-tag"><Check size={13} weight="bold" aria-hidden="true" />Đúng</span>}
                <FieldError id={errId}>{err}</FieldError>
              </div>
              <button type="button" className="ghost icon-only tool" onClick={() => remove(i)}
                aria-label={`Xoá lựa chọn ${label}`} title="Xoá lựa chọn">
                <X size={16} aria-hidden="true" />
              </button>
            </li>
          )
        })}
      </ul>
      {!hasCorrect && options.length > 0 && (
        <p className="field-warn"><Warning size={15} aria-hidden="true" />Chưa chọn đáp án đúng.</p>
      )}
      {options.length < MAX_OPTIONS
        ? (
          <button type="button" className="ghost opt-add" onClick={add}>
            <Plus size={15} aria-hidden="true" /> Thêm lựa chọn
          </button>
        )
        : <p className="field-hint">Đã đủ {MAX_OPTIONS} lựa chọn ({LETTERS.slice(0, MAX_OPTIONS).split('').join(', ')}) — mỗi câu tối đa {MAX_OPTIONS}.</p>}
    </fieldset>
  )
}

/** Câu hỏi chỉ đọc — đề đã xuất bản. */
export function QuestionView({ q, index }) {
  return (
    <li className="card q-card">
      <div className="q-head">
        <h3 className="q-title">Câu {index + 1}</h3>
        <span className="badge">{TYPE_LABEL[q.type]}</span>
        <span className="q-points">{formatNumber(q.points)} điểm</span>
      </div>
      <p className="q-content">{q.content}</p>
      {q.type === 'mcq' && (
        <ul className="opt-view">
          {q.options.map((o) => (
            <li key={o.id} className={o.is_correct ? 'correct' : ''}>
              <span className="opt-letter">{o.label}.</span> {o.content}
              {o.is_correct && <span className="opt-tag"><Check size={13} weight="bold" aria-hidden="true" />Đáp án đúng</span>}
            </li>
          ))}
        </ul>
      )}
      <dl className="q-meta">
        {q.type === 'essay' && (<><dt>Barem</dt><dd className="pre">{q.rubric || 'Không có — chấm tay'}</dd></>)}
        {q.explanation && (<><dt>Giải thích</dt><dd>{q.explanation}</dd></>)}
        {q.source_page && (<><dt>Trang giáo trình</dt><dd>{q.source_page}</dd></>)}
      </dl>
    </li>
  )
}
