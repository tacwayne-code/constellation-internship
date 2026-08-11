import type { ReactNode } from 'react'
import { useNavigation } from '../../store/navigationStore'
import { getModule } from '../../config/modules'
import { Icon } from '../common/Icon'

interface ModuleShellProps {
  children: ReactNode
}

/** 模块视图统一外壳：英雄区 + 内容区
 *  顶部 stats 不再由 Shell 渲染（避免与各 View 内部 KPI 重复）
 */
export function ModuleShell({ children }: ModuleShellProps) {
  const { moduleId } = useNavigation()
  const cfg = getModule(moduleId)

  return (
    <div className="module-view">
      <div className="module-hero">
        <div className="module-title-row">
          <div className="module-title-icon">
            <Icon name={cfg.icon} size={20} />
          </div>
          <div>
            <h1>{cfg.title}</h1>
            <div className="subtitle">{cfg.subtitle}</div>
          </div>
        </div>
      </div>

      {children}
    </div>
  )
}