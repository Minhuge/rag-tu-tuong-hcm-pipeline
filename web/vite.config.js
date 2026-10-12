import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: chuyển các endpoint của FastAPI (uvicorn app.main:app --port 8000) qua cùng origin → không cần CORS.
const API = process.env.API_URL || 'http://localhost:8000'

// /chat, /exams, /submissions vừa là trang web (React Router) vừa là API. Trình duyệt mở trang (gõ URL, F5)
// gửi Accept: text/html → để Vite trả index.html; fetch của web không gửi text/html → chuyển sang FastAPI.
// Máy chủ làm đúng như vậy với bản build (app/main.py: spa_fallback).
const toApi = {
  target: API,
  bypass: (req) => (req.method === 'GET' && req.headers.accept?.includes('text/html') ? '/index.html' : undefined),
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/ask': API,
      '/search': API,
      '/health': API,
      '/chat': toApi, // POST trả về luồng NDJSON, proxy chuyển tiếp từng đoạn
      '/conversations': API,
      '/chapters': API,
      '/exams': toApi,
      '/submissions': toApi,
      '/auth': API, // cookie refresh token có path=/auth
    },
  },
})
