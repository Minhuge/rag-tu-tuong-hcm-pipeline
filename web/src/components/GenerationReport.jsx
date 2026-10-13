import { Sparkle, Warning, X } from '@phosphor-icons/react'

const KIND = { mcq: 'Trắc nghiệm', essay: 'Tự luận' }

/**
 * Báo cáo sau khi AI tạo đề (4.4), hiện ở đầu trình sửa đề: tạo được bao nhiêu câu so với yêu cầu, vì sao thiếu,
 * các câu bị loại kèm lý do, lượt còn lại. Dữ liệu đi theo navigate(…, { state }) từ trang tạo đề.
 */
export default function GenerationReport({ generation, counts, onClose }) {
  const { chapter, requested, notice, rejected, quota } = generation
  const made = [counts.mcq && `${counts.mcq} câu trắc nghiệm`, counts.essay && `${counts.essay} câu tự luận`]
    .filter(Boolean).join(' và ')
  const short = counts.mcq < requested.mcq || counts.essay < requested.essay

  return (
    <section className="gen-report" aria-labelledby="gen-report-title">
      <div className="gen-report-head">
        <Sparkle size={20} weight="fill" className="gold" aria-hidden="true" />
        <div>
          <h2 id="gen-report-title">AI đã soạn {made} từ {chapter.chapter}</h2>
          <p className="muted">Đây là bản nháp: đọc lại từng câu, sửa nếu cần, rồi mới xuất bản.
            Còn {quota.remaining}/{quota.limit} lượt tạo đề bằng AI hôm nay.</p>
        </div>
        <button type="button" className="ghost icon-only gen-report-close" onClick={onClose}
          aria-label="Ẩn báo cáo" title="Ẩn báo cáo">
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      {(notice || short) && (
        <div className="notice notice-warn">
          <Warning size={18} aria-hidden="true" />
          <p>
            {notice || 'AI tạo được ít câu hơn yêu cầu.'} (Yêu cầu {requested.mcq} trắc nghiệm, {requested.essay} tự
            luận.) Bạn có thể tự thêm câu trong trình sửa đề.
          </p>
        </div>
      )}

      {rejected.length > 0 && (
        <details className="gen-rejected">
          <summary>{rejected.length} câu AI soạn nhưng bị loại khi kiểm tra — xem lý do</summary>
          <ul>
            {rejected.map((r, i) => (
              <li key={i}>
                <p className="gen-rejected-q">
                  <span className="badge">{KIND[r.kind]} · {r.stage}</span> {r.question}
                </p>
                <ul className="gen-reasons">{r.reasons.map((why) => <li key={why}>{why}</li>)}</ul>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
