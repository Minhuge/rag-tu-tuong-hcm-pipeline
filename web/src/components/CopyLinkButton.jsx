import { Check, Copy } from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'
import { takeExamUrl } from '../examsApi.js'

/** Nút chép link làm bài. Trình duyệt chặn bộ nhớ tạm → hiện ô chứa link (chọn sẵn) để tự chép. */
export default function CopyLinkButton({ exam }) {
  const [status, setStatus] = useState(null) // null | 'copied' | 'failed'
  const fallbackRef = useRef(null)
  const url = takeExamUrl(exam)
  useEffect(() => {
    if (status === 'failed') fallbackRef.current?.focus() // focus → onFocus chọn sẵn link để Ctrl/Cmd+C
  }, [status])
  useEffect(() => {
    if (status !== 'copied') return undefined
    const t = setTimeout(() => setStatus(null), 2500)
    return () => clearTimeout(t)
  }, [status])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url)
      setStatus('copied')
    } catch {
      setStatus('failed') // trình duyệt chặn clipboard (http, quyền) → hiện link để tự chép
    }
  }
  return (
    <>
      <button type="button" className="ghost action" onClick={copy}>
        {status === 'copied' ? <Check size={16} weight="bold" aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
        {status === 'copied' ? 'Đã chép link' : 'Chép link chia sẻ'}
      </button>
      <span className="sr-only" role="status">{status === 'copied' ? 'Đã chép link làm bài vào bộ nhớ tạm' : ''}</span>
      {status === 'failed' && (
        <p className="copy-fallback">
          Không chép tự động được — chọn và chép link này:{' '}
          <input ref={fallbackRef} readOnly value={url} aria-label="Link làm bài" onFocus={(e) => e.target.select()} />
        </p>
      )}
    </>
  )
}
