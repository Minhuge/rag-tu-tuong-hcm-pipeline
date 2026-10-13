import { ArrowClockwise, Warning } from '@phosphor-icons/react'
import { Component } from 'react'

/**
 * Bắt lỗi khi vẽ trang (roadmap 4.10): một component lỗi không làm trắng cả ứng dụng — chỉ phần trang được thay bằng
 * thông báo, thanh điều hướng vẫn dùng được. App.jsx đặt key theo đường dẫn → sang trang khác là thử lại từ đầu.
 * Lỗi gọi API không tới đây (các trang tự hiện lỗi + "Thử lại"); đây là lưới an toàn cho lỗi lập trình.
 */
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Lỗi khi hiển thị trang:', error, info.componentStack) // để người phát triển xem trong DevTools
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="page" id="main">
        <div className="state-box state-error" role="alert">
          <Warning size={22} aria-hidden="true" />
          <div>
            <p className="state-title">Trang này gặp lỗi khi hiển thị</p>
            <p className="muted">Dữ liệu của bạn không bị mất — câu trả lời và đề đã lưu vẫn còn trên máy chủ. Tải lại trang
              thường sẽ hết lỗi; nếu vẫn bị, hãy chuyển sang trang khác bằng thanh phía trên.</p>
            <button type="button" className="ghost state-action" onClick={() => window.location.reload()}>
              <ArrowClockwise size={16} aria-hidden="true" /> Tải lại trang
            </button>
          </div>
        </div>
      </main>
    )
  }
}
