/**
 * 导航状态（zustand）——等价 dist 的 A()/Oe()/ke()/ye()
 */
import { create } from 'zustand'

export type ViewMode = 'overview' | 'board' | 'table'

interface NavigationState {
  inPortfolio: boolean
  projectId: string | null
  moduleId: string
  viewMode: ViewMode
  // 动作
  goPortfolio: () => void
  openProject: (projectId: string) => void
  openModule: (moduleId: string) => void
  setViewMode: (mode: ViewMode) => void
}

export const useNavigation = create<NavigationState>((set) => ({
  ...initFromHash(),
  viewMode: 'overview',

  goPortfolio: () => {
    window.location.hash = '#/portfolio'
    set({ inPortfolio: true, projectId: null })
  },
  openProject: (projectId) => {
    window.location.hash = `#/project/${projectId}`
    set({ inPortfolio: false, projectId, moduleId: 'overview' })
  },
  openModule: (moduleId) => {
    window.location.hash = `#/${moduleId}`
    set({ moduleId, inPortfolio: false })
  },
  setViewMode: (viewMode) => set({ viewMode }),
}))

/**
 * 从 URL hash 恢复导航状态（如 #/deliveryTower 直达交付塔）
 * 支持： #/portfolio           → 项目组合
 *       #/project/{id}        → 项目工作台
 *       #/{moduleId}          → 模块页（overview/delivery/procurement/deliveryTower...）
 */
function initFromHash(): Pick<NavigationState, 'inPortfolio' | 'projectId' | 'moduleId'> {
  const h = window.location.hash.replace(/^#\/?/, '')
  if (h && h !== 'portfolio') {
    if (h.startsWith('project/')) {
      return { inPortfolio: false, projectId: h.slice('project/'.length), moduleId: 'overview' }
    }
    return { inPortfolio: false, projectId: null, moduleId: h }
  }
  return { inPortfolio: true, projectId: null, moduleId: 'overview' }
}
