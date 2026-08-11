import { useEffect } from 'react'
import { usePortfolioSummary } from '../../api/modules/useData'
import { getLastSource } from '../../api/client'
import { moduleLabel } from '../../config/nav'
import { useNavigation } from '../../store/navigationStore'

/** 顶部栏：上下文标题 + 操作 */
export function Topbar() {
  const { inPortfolio, projectId, moduleId, goPortfolio } = useNavigation()
  const summary = usePortfolioSummary()

  // 触发 lastSource 同步（保留供 UI 指示器使用）
  useEffect(() => {
    if (summary.data) getLastSource()
  }, [summary.data])

  let title: string
  if (inPortfolio) {
    title = '项目组合驾驶舱'
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
    </header>
  )
}
