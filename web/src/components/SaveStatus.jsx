import { ArrowClockwise, Check, CircleNotch, PencilSimple, Warning } from '@phosphor-icons/react'

/** Trạng thái tự lưu (useAutosave): chữ + icon, không chỉ dựa vào màu. compact = dùng trong thẻ câu hỏi. */
const VIEW = {
  saved: { Icon: Check, text: 'Đã lưu', className: 'ok' },
  dirty: { Icon: PencilSimple, text: 'Chưa lưu…', className: '' },
  saving: { Icon: CircleNotch, text: 'Đang lưu…', className: '', spin: true },
  invalid: { Icon: Warning, text: 'Chưa lưu — sửa lỗi bên dưới', short: 'Có lỗi', className: 'warn' },
  error: { Icon: Warning, text: 'Lưu lỗi', className: 'bad' },
}

export default function SaveStatus({ status, compact = false, onRetry }) {
  const { Icon, text, short, className, spin } = VIEW[status]
  return (
    <span className={`save-status ${className}`}>
      <Icon size={15} weight="bold" className={spin ? 'spin' : undefined} aria-hidden="true" />
      {compact && short ? short : text}
      {status === 'error' && onRetry && (
        <button type="button" className="link-btn" onClick={onRetry}>
          <ArrowClockwise size={14} aria-hidden="true" /> Thử lại
        </button>
      )}
    </span>
  )
}
