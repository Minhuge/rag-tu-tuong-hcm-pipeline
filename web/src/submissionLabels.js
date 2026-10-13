// Nhãn dùng chung cho bài làm (trang bài nộp của người tạo đề, 4.8).
export const SUB_STATUS = {
  in_progress: { text: 'Đang làm', className: 'badge-accent' },
  submitted: { text: 'Đang chấm', className: 'badge-warn' },
  graded: { text: 'Đã chấm', className: 'badge-ok' },
}

/** Tên hiển thị, không có thì phần trước @ của email. */
export const personName = (u) => u.display_name || u.email.split('@')[0]
