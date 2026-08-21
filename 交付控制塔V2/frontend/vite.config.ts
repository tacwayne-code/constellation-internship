import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // 保持 false：本机 WorkBuddy 沙箱把 Node 删除 API 拦截成「移入回收站」，回收站不可用会 fail-closed 中断构建。
    // 因此构建前需手动清理 dist（用系统 rm -rf，绕过 Node shim），避免旧 hash 资产积累。
    emptyOutDir: false,
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
