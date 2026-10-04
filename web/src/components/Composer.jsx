import { PaperPlaneRight, Stop } from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'

const MAX_CHARS = 1000 // trùng MAX_QUESTION_CHARS trong guards.py

export default function Composer({ onSend, disabled, onStop }) {
  const [text, setText] = useState('')
  const ref = useRef(null)

  // textarea tự giãn theo nội dung, tối đa ~6 dòng
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }, [text])

  useEffect(() => {
    if (!disabled) ref.current?.focus()
  }, [disabled])

  // Esc ở bất kỳ đâu trên trang → dừng câu hỏi đang chờ
  useEffect(() => {
    if (!disabled) return
    const onKey = (e) => e.key === 'Escape' && onStop()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [disabled, onStop])

  const submit = () => {
    const q = text.trim()
    if (!q || disabled || q.length > MAX_CHARS) return
    onSend(q)
    setText('')
  }

  const over = text.length > MAX_CHARS

  return (
    <form className="composer-wrap" onSubmit={(e) => { e.preventDefault(); submit() }}>
      <div className={`composer ${over ? 'invalid' : ''}`}>
        <textarea
          ref={ref}
          rows={1}
          value={text}
          placeholder="Hỏi về nội dung giáo trình Tư tưởng Hồ Chí Minh…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              submit()
            }
          }}
          aria-label="Câu hỏi"
          aria-describedby="composer-help composer-count"
          aria-invalid={over}
        />
        <div className="composer-side">
          <span id="composer-count" className={`counter ${over ? 'over' : ''}`}>
            {text.length}/{MAX_CHARS}<span className="sr-only"> ký tự</span>
          </span>
          {disabled ? (
            <button type="button" className="send stop" onClick={onStop} aria-label="Dừng câu hỏi đang chờ (Esc)">
              <Stop size={16} weight="fill" aria-hidden="true" />
            </button>
          ) : (
            <button type="submit" className="send" disabled={!text.trim() || over} aria-label="Gửi câu hỏi">
              <PaperPlaneRight size={18} weight="fill" aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
      {over ? (
        <p id="composer-help" className="composer-help error" role="alert">
          Câu hỏi dài {text.length} ký tự, vượt giới hạn {MAX_CHARS}. Hãy rút gọn trước khi gửi.
        </p>
      ) : (
        <p id="composer-help" className="composer-help">
          <kbd>Enter</kbd> để gửi · <kbd>Shift</kbd>+<kbd>Enter</kbd> xuống dòng
          {disabled && <> · <kbd>Esc</kbd> để dừng</>}
        </p>
      )}
    </form>
  )
}
