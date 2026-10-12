import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import './index.css'
import Root from './Root.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {/* Router bọc cả trang đăng nhập → URL giữ nguyên khi phải đăng nhập (mở link đề được chia sẻ → đăng nhập → vào đúng đề) */}
    <BrowserRouter>
      <Root />
    </BrowserRouter>
  </StrictMode>,
)
