// Định dạng hiển thị dùng chung cho các trang bài kiểm tra. Thời gian từ API là mili giây (epoch).
const timeFmt = new Intl.DateTimeFormat('vi-VN', { hour: '2-digit', minute: '2-digit' })
const dateFmt = new Intl.DateTimeFormat('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' })
const numberFmt = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 2 })

/** "14:30 hôm nay" · "09:05 hôm qua" · "14:30 12/10/2026". Không có → "". */
export function formatTime(ms) {
  if (ms == null) return ''
  const d = new Date(ms)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const day = new Date(d)
  day.setHours(0, 0, 0, 0)
  const days = Math.round((today - day) / 86_400_000)
  const when = days === 0 ? 'hôm nay' : days === 1 ? 'hôm qua' : dateFmt.format(d)
  return `${timeFmt.format(d)} ${when}`
}

/** 7.5 → "7,5" (dấu phẩy thập phân kiểu Việt Nam, tối đa 2 chữ số lẻ). */
export const formatNumber = (n) => numberFmt.format(n)
