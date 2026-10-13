import { useEffect, useState } from 'react'

/**
 * Gọi API lúc mở trang: [{ status: loading | ok | error, data, error }, retry].
 * load phải ổn định giữa các lần render (hàm import từ examsApi.js, không phải arrow tạo mới mỗi lần).
 * Rời trang trước khi API trả về → bỏ kết quả.
 */
export function useLoad(load, initial = null) {
  const [state, setState] = useState({ status: 'loading', data: initial, error: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let alive = true
    load()
      .then((data) => alive && setState({ status: 'ok', data, error: null }))
      .catch((error) => alive && setState({ status: 'error', data: initial, error }))
    return () => {
      alive = false
    }
  }, [load, attempt, initial])
  const retry = () => {
    setState({ status: 'loading', data: initial, error: null })
    setAttempt((n) => n + 1)
  }
  return [state, retry]
}
