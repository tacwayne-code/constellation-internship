import { useEffect } from 'react'
import { Maximize2, Minimize2 } from 'lucide-react'
import { usePortfolioSummary } from '../../api/modules/useData'
import { getLastSource } from '../../api/client'
import { moduleLabel } from '../../config/nav'
import { useNavigation } from '../../store/navigationStore'
import { useFullscreen } from '../../hooks/useFullscreen'
import { toast } from '../../store/uiStore'

/** 顶部栏：上下文标题 + 操作 */
export function Topbar() {
  const { inPortfolio, projectId, moduleId, goPortfolio } = useNavigation()
  const summary = usePortfolioSummary(!inPortfolio)
  const { isFullscreen, toggleFullscreen } = useFullscreen()

  // 触发 lastSource 同步（保留供 UI 指示器使用）
  useEffect(() => {
    if (summary.data) getLastSource()
  }, [summary.data])

  let title: string
  if (inPortfolio) {
    title = '统一运营看板'
  } else {
    const project = summary.data?.projects.find((p) => p.id === projectId)
    title = `${project?.short ?? '项目'} / ${moduleLabel(moduleId)}`
  }

  return (
    <header className="topbar">
      <div className="topbar-context">
        {!inPortfolio && (
          <span className="crumb" onClick={goPortfolio} style={{ cursor: 'pointer' }}>
            ← 项目组合
          </span>
        )}
        {title}
      </div>
      <div className="topbar-actions">
        <button
          className={`topbar-fullscreen ${isFullscreen ? 'active' : ''}`}
          onClick={() => {
            void toggleFullscreen().catch(() => toast('当前浏览器不支持全屏模式，请使用浏览器的全屏功能', 'warning'))
          }}
          aria-label={isFullscreen ? '退出全屏' : '进入全屏'}
        >
          {isFullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          {isFullscreen ? '退出全屏' : '全屏展示'}
        </button>
      </div>
    </header>
  )
}
