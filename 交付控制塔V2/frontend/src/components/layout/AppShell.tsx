import type { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { ToastStack } from '../common/Toast'

interface AppShellProps {
  children: ReactNode
}

/** 顶层布局：侧边栏 + 主区域（topbar + 内容） */
export function AppShell({ children }: AppShellProps) {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main">
        <Topbar />
        <div className="page-content">{children}</div>
      </main>
      <ToastStack />
    </div>
  )
}
