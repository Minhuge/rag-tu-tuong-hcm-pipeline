import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: chuyển các endpoint của FastAPI (uvicorn api:app --port 8000) qua cùng origin → không cần CORS.
const API = process.env.API_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/ask': API,
      '/search': API,
      '/health': API,
    },
  },
})
