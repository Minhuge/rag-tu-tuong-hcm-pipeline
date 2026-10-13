// Dữ liệu một câu hỏi trong trình sửa đề (4.5): đổi qua lại giữa câu của máy chủ và giá trị các ô nhập, kiểm tra.
export const LETTERS = 'ABCDEFGHIJ'
export const MAX_OPTIONS = 4 // A–D, khớp app/exams.py MAX_OPTIONS (máy chủ từ chối lựa chọn thứ 5)
export const TYPE_LABEL = { mcq: 'Trắc nghiệm', essay: 'Tự luận' }

let nextKey = 0
export const newKey = () => `k${++nextKey}`

/** Câu từ máy chủ → dữ liệu ô nhập (chuỗi, để xoá trắng ô số được trong lúc gõ). */
export function toDraft(q) {
  return {
    content: q.content ?? '',
    points: String(q.points ?? (q.type === 'essay' ? 2 : 1)),
    explanation: q.explanation ?? '',
    source_page: q.source_page == null ? '' : String(q.source_page),
    rubric: q.rubric ?? '',
    options: (q.options ?? []).map((o) => ({ key: newKey(), content: o.content, is_correct: o.is_correct })),
  }
}

export const blankDraft = (type) => toDraft({
  type,
  options: type === 'mcq' ? Array.from({ length: 4 }, () => ({ content: '', is_correct: false })) : [],
})

const num = (v) => (String(v).trim() === '' ? NaN : Number(v))

/** Lỗi khiến máy chủ từ chối LƯU — tên trường giống lỗi 422 của máy chủ (options.1.content…). */
export function validateDraft(d, type) {
  const e = {}
  if (!d.content.trim()) e.content = 'Nhập nội dung câu hỏi'
  const points = num(d.points)
  if (!(points >= 0)) e.points = 'Điểm là số ≥ 0'
  if (d.source_page.trim() && !/^[1-9]\d*$/.test(d.source_page.trim())) e.source_page = 'Số trang là số nguyên dương'
  if (type === 'mcq') {
    d.options.forEach((o, i) => {
      if (!o.content.trim()) e[`options.${i}.content`] = 'Nhập nội dung hoặc xoá lựa chọn này'
    })
  }
  return e
}

/** Lỗi chỉ chặn XUẤT BẢN (lưu nháp vẫn được) — khớp kiểm tra của máy chủ ở publish_exam. */
export function publishProblems(d, type) {
  if (type !== 'mcq') return []
  const out = []
  if (d.options.length < 2) out.push('Cần ít nhất 2 lựa chọn')
  if (d.options.filter((o) => o.is_correct).length !== 1) out.push('Chọn một đáp án đúng')
  return out
}

export function toPayload(d, type) {
  const p = {
    content: d.content.trim(),
    points: num(d.points),
    explanation: d.explanation.trim() || null,
    source_page: d.source_page.trim() ? Number(d.source_page) : null,
  }
  if (type === 'essay') p.rubric = d.rubric.trim() || null
  else p.options = d.options.map((o, i) => ({ label: LETTERS[i], content: o.content.trim(), is_correct: o.is_correct }))
  return p
}
