import { useCallback, useEffect, useRef, useState } from 'react'
import { saveAnswer } from './examsApi.js'

/**
 * Tự lưu câu trả lời của trang làm bài (4.6).
 *   save(qid, answer)            gửi ngay (trắc nghiệm: lưu khi chọn)
 *   saveLater(qid, answer, ms)   gửi sau khi ngừng gõ (tự luận)
 *   drain()                      gửi hết phần còn chờ, chờ xong → true nếu không còn câu lỗi (trước khi nộp)
 *   retry()                      gửi lại các câu lỗi (nút "Thử lại", khi có mạng lại)
 * Mỗi câu chỉ có MỘT request đang chạy; sửa trong lúc đang gửi → gửi tiếp bản mới nhất khi xong (không gửi chồng,
 * không để bản cũ về sau ghi đè bản mới).
 * status: { [qid]: 'saving' | 'error' } — không có = đã lưu. onError(e) nhận lỗi (vd. 409 hết giờ).
 */
export function useAnswerSaver(submissionId, { onError } = {}) {
  const [status, setStatus] = useState({})
  const jobs = useRef(new Map()) // qid → { latest, running: Promise | null, failed }
  const timers = useRef(new Map()) // qid → { t, answer } chờ ngừng gõ
  const onErrorRef = useRef(onError)
  useEffect(() => {
    onErrorRef.current = onError
  })

  const mark = (qid, s) => setStatus((prev) => {
    if ((prev[qid] ?? null) === s) return prev
    const next = { ...prev }
    if (s) next[qid] = s
    else delete next[qid]
    return next
  })

  const pump = useCallback((qid) => {
    const run = (id) => {
      const job = jobs.current.get(id)
      if (!job || job.running) return job?.running ?? Promise.resolve()
      const value = job.latest
      mark(id, 'saving')
      job.running = saveAnswer(submissionId, id, value)
        .then(() => {
          job.running = null
          job.failed = false
          if (job.latest !== value) return run(id) // đã sửa tiếp trong lúc gửi → gửi bản mới nhất
          jobs.current.delete(id)
          mark(id, null)
          return undefined
        })
        .catch((e) => {
          job.running = null
          job.failed = true
          mark(id, 'error')
          onErrorRef.current?.(e)
        })
      return job.running
    }
    return run(qid)
  }, [submissionId])

  const save = useCallback((qid, answer) => {
    const pending = timers.current.get(qid)
    if (pending) clearTimeout(pending.t)
    timers.current.delete(qid)
    const job = jobs.current.get(qid) ?? { latest: null, running: null, failed: false }
    job.latest = answer
    jobs.current.set(qid, job)
    return pump(qid)
  }, [pump])

  const saveLater = useCallback((qid, answer, ms = 1000) => {
    const pending = timers.current.get(qid)
    if (pending) clearTimeout(pending.t)
    mark(qid, 'saving') // hiện "Đang lưu…" ngay khi gõ: người làm bài biết thay đổi sẽ được lưu
    timers.current.set(qid, { answer, t: setTimeout(() => save(qid, answer), ms) })
  }, [save])

  const flushTimers = useCallback(() => {
    for (const [qid, { answer }] of [...timers.current]) save(qid, answer)
  }, [save])

  const drain = useCallback(async () => {
    flushTimers()
    for (let i = 0; i < 5; i += 1) { // vòng lặp: trong lúc chờ có thể có bản mới hơn được gửi tiếp
      const running = [...jobs.current.values()].map((j) => j.running).filter(Boolean)
      if (!running.length) break
      await Promise.all(running)
    }
    return ![...jobs.current.values()].some((j) => j.failed)
  }, [flushTimers])

  const retry = useCallback(() => {
    for (const [qid, job] of jobs.current) if (job.failed) pump(qid)
  }, [pump])

  // còn câu chưa gửi lúc rời trang (đổi route) → gửi luôn
  useEffect(() => () => flushTimers(), [flushTimers])

  const hasPending = useCallback(() => timers.current.size > 0 || jobs.current.size > 0, [])
  return { status, save, saveLater, drain, retry, flushTimers, hasPending }
}

/**
 * Đồng hồ đếm ngược từ số giây CÒN LẠI do máy chủ tính (remaining_seconds) — đếm bằng performance.now(),
 * không dùng giờ hệ thống của máy người dùng (có thể lệch). null = không giới hạn thời gian.
 */
export function useCountdown(remainingSeconds) {
  const [end] = useState(() => (remainingSeconds == null ? null : performance.now() + remainingSeconds * 1000))
  const [left, setLeft] = useState(remainingSeconds == null ? null : Math.max(0, Math.ceil(remainingSeconds)))
  useEffect(() => {
    if (end == null) return undefined
    const tick = () => setLeft(Math.max(0, Math.ceil((end - performance.now()) / 1000)))
    const t = setInterval(tick, 250) // 4 lần / giây: số giây đổi đúng lúc, không bị "nhảy cóc"
    return () => clearInterval(t)
  }, [end])
  return left
}
