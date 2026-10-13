import { useEffect, useRef, useState } from 'react'

/**
 * Tải dữ liệu rồi HỎI LẠI định kỳ khi busy(data) còn đúng (vd. AI đang chấm tự luận) — trang kết quả (4.7) và
 * trang bài nộp của người tạo đề (4.8).
 *   load(signal)  → Promise; phải ổn định giữa các lần render (useCallback)
 *   busy(data)    → true thì hỏi lại sau `interval` ms; quá `slowAfter` ms vẫn bận → hỏi thưa hơn (`slowInterval`)
 * Tab bị ẩn → tạm dừng (không tốn request khi không ai xem). Lỗi khi đang hỏi lại → giữ dữ liệu cũ, kèm error.
 * Trả về [{ status: loading | ok | error, data, error }, reload, setData] — setData: thay dữ liệu bằng phản hồi của
 * một thao tác (vd. sau khi chấm tay), không cần tải lại.
 */
export function usePolledLoad(load, { busy, interval = 4000, slowInterval = 15000, slowAfter = 180_000 }) {
  const [state, setState] = useState({ status: 'loading', data: null, error: null })
  const [tick, setTick] = useState(0)
  const since = useRef(null)

  useEffect(() => {
    const ctrl = new AbortController()
    load(ctrl.signal)
      .then((data) => setState({ status: 'ok', data, error: null }))
      .catch((error) => {
        if (error.name === 'AbortError') return
        setState((s) => (s.data ? { ...s, error } : { status: 'error', data: null, error }))
      })
    return () => ctrl.abort()
  }, [load, tick])

  const isBusy = state.data != null && !!busy(state.data)
  useEffect(() => {
    if (!isBusy) {
      since.current = null
      return undefined
    }
    since.current ??= Date.now()
    const delay = Date.now() - since.current > slowAfter ? slowInterval : interval
    let t = null
    const schedule = () => {
      clearTimeout(t)
      if (document.visibilityState === 'visible') t = setTimeout(() => setTick((n) => n + 1), delay)
    }
    schedule()
    document.addEventListener('visibilitychange', schedule)
    return () => {
      clearTimeout(t)
      document.removeEventListener('visibilitychange', schedule)
    }
  }, [isBusy, state.data, interval, slowInterval, slowAfter])

  const reload = () => setTick((n) => n + 1)
  const setData = (data) => {
    setState({ status: 'ok', data, error: null })
    setTick((n) => n + 1) // huỷ lượt hỏi lại đang chạy (có thể mang dữ liệu cũ hơn) và lấy bản mới nhất
  }
  return [state, reload, setData]
}
