import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Tự lưu một khối dữ liệu (thông tin đề / một câu hỏi) sau khi ngừng gõ `delay` ms — trình sửa đề (4.5).
 *
 *   value      dữ liệu đang sửa; mỗi lần sửa là một object MỚI (so sánh theo tham chiếu)
 *   save(v)    gửi lên máy chủ → Promise. Các lần lưu chạy nối tiếp, không bao giờ chồng nhau
 *   validate(v) → { trường: lỗi }: có lỗi thì KHÔNG gửi (máy chủ sẽ từ chối), hiện lỗi cạnh ô
 *
 * Trả về { status, errors, error, flush, discard }:
 *   status  saved | dirty (đang chờ ngừng gõ) | saving | invalid (lỗi nhập) | error (máy chủ / mạng lỗi)
 *   flush() lưu ngay, chờ xong → true nếu đã lưu hết (dùng trước khi xuất bản)
 *   discard() bỏ thay đổi chưa lưu (khối sắp bị xoá) → không lưu nốt lúc rời trang
 * Rời trang khi còn thay đổi hợp lệ chưa lưu → gửi luôn, không chờ.
 */
export function useAutosave(value, { save, validate, delay = 800 }) {
  const [saved, setSaved] = useState(value)
  const [phase, setPhase] = useState({ name: 'idle', errors: {}, error: null })
  const valueRef = useRef(value)
  const savedRef = useRef(value)
  const fns = useRef({ save, validate })
  const chain = useRef(Promise.resolve(true))

  useEffect(() => {
    fns.current = { save, validate }
    valueRef.current = value
  })

  const attempt = useCallback(async () => {
    const v = valueRef.current
    if (v === savedRef.current) return true
    const errors = fns.current.validate?.(v) ?? {}
    if (Object.keys(errors).length) {
      setPhase({ name: 'invalid', errors, error: null })
      return false
    }
    setPhase({ name: 'saving', errors: {}, error: null })
    try {
      await fns.current.save(v)
      savedRef.current = v
      setSaved(v)
      setPhase({ name: 'idle', errors: {}, error: null })
      return true
    } catch (error) {
      setPhase({ name: 'error', errors: error.fields ?? {}, error })
      return false
    }
  }, [])

  const flush = useCallback(() => {
    const next = chain.current.then(attempt)
    chain.current = next.catch(() => false)
    return next
  }, [attempt])

  const discard = useCallback(() => {
    savedRef.current = valueRef.current
  }, [])

  useEffect(() => {
    if (value === savedRef.current) return undefined
    const t = setTimeout(flush, delay)
    return () => clearTimeout(t)
  }, [value, delay, flush])

  useEffect(() => () => {
    // rời trang (đổi route) khi còn thay đổi hợp lệ chưa lưu → gửi ngay
    const v = valueRef.current
    if (v !== savedRef.current && !Object.keys(fns.current.validate?.(v) ?? {}).length) {
      fns.current.save(v).catch(() => {})
    }
  }, [])

  const status = phase.name === 'saving' ? 'saving'
    : phase.name === 'invalid' || phase.name === 'error' ? phase.name
      : value !== saved ? 'dirty' : 'saved'
  return { status, errors: phase.errors, error: phase.error, flush, discard }
}

/** Trạng thái chung của nhiều khối: khối tệ nhất quyết định. */
const RANK = { saved: 0, dirty: 1, saving: 2, invalid: 3, error: 4 }
export const worstStatus = (statuses) =>
  statuses.reduce((worst, s) => (RANK[s] > RANK[worst] ? s : worst), 'saved')
