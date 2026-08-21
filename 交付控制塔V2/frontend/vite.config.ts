import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    emptyOutDir: false, // 关闭 safe-delete：本机 genie-trash 不可用会 fail-closed 中断构建
  },
  server: {
    port: 5173,
    proxy: {
      // 开发模式：前端 /api → 后端 FastAPI (localhost:8000)
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
