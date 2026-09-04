import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { ALL_WIDGETS, type RoleId, type WidgetId } from '../components/dashboard/dashboardData'

export type DashboardDensity = 'comfortable' | 'compact'

interface RoleLayout {
  order: WidgetId[]
  hidden: WidgetId[]
}

interface DashboardState {
  role: RoleId
  project: string
  warehouse: string
  period: string
  density: DashboardDensity
  isEditing: boolean
  layouts: Record<RoleId, RoleLayout>
  setRole: (role: RoleId) => void
  setProject: (project: string) => void
  setWarehouse: (warehouse: string) => void
  setPeriod: (period: string) => void
  setDensity: (density: DashboardDensity) => void
  setEditing: (isEditing: boolean) => void
  toggleWidget: (widget: WidgetId) => void
  moveWidget: (widget: WidgetId, direction: -1 | 1) => void
  resetRole: () => void
}

const templateLayouts: Record<RoleId, RoleLayout> = {
  management: { order: ['kpis', 'trend', 'risks', 'exceptions', 'progress', 'focus'], hidden: ['focus'] },
  procurement: { order: ['kpis', 'exceptions', 'trend', 'progress', 'risks', 'focus'], hidden: [] },
  warehouse: { order: ['kpis', 'exceptions', 'progress', 'risks', 'focus', 'trend'], hidden: ['trend'] },
  field: { order: ['kpis', 'progress', 'exceptions', 'trend', 'focus', 'risks'], hidden: ['risks'] },
  production: { order: ['kpis', 'progress', 'exceptions', 'trend', 'focus', 'risks'], hidden: ['risks'] },
}

function cloneLayout(layout: RoleLayout): RoleLayout {
  return { order: [...layout.order], hidden: [...layout.hidden] }
}

function freshLayouts(): Record<RoleId, RoleLayout> {
  return {
    management: cloneLayout(templateLayouts.management),
    procurement: cloneLayout(templateLayouts.procurement),
    warehouse: cloneLayout(templateLayouts.warehouse),
    field: cloneLayout(templateLayouts.field),
    production: cloneLayout(templateLayouts.production),
  }
}

export const useDashboard = create<DashboardState>()(
  persist(
    (set) => ({
      role: 'management',
      project: 'all',
      warehouse: 'all',
      period: 'month',
      density: 'comfortable',
      isEditing: false,
      layouts: freshLayouts(),
      setRole: (role) => set({ role }),
      setProject: (project) => set({ project }),
      setWarehouse: (warehouse) => set({ warehouse }),
      setPeriod: (period) => set({ period }),
      setDensity: (density) => set({ density }),
      setEditing: (isEditing) => set({ isEditing }),
      toggleWidget: (widget) => set((state) => {
        const current = state.layouts[state.role]
        const isHidden = current.hidden.includes(widget)
        return {
          layouts: {
            ...state.layouts,
            [state.role]: {
              ...current,
              hidden: isHidden ? current.hidden.filter((id) => id !== widget) : [...current.hidden, widget],
            },
          },
        }
      }),
      moveWidget: (widget, direction) => set((state) => {
        const current = state.layouts[state.role]
        const index = current.order.indexOf(widget)
        const nextIndex = index + direction
        if (index < 0 || nextIndex < 0 || nextIndex >= current.order.length) return state
        const order = [...current.order]
        ;[order[index], order[nextIndex]] = [order[nextIndex], order[index]]
        return { layouts: { ...state.layouts, [state.role]: { ...current, order } } }
      }),
      resetRole: () => set((state) => ({
        layouts: { ...state.layouts, [state.role]: cloneLayout(templateLayouts[state.role]) },
        density: 'comfortable',
      })),
    }),
    {
      name: 'delivery-tower-unified-dashboard',
      version: 2,
      migrate: (persistedState) => persistedState as DashboardState,
      partialize: (state) => ({
        role: state.role,
        project: state.project,
        warehouse: state.warehouse,
        period: state.period,
        density: state.density,
        layouts: state.layouts,
      }),
      merge: (persisted, current) => {
        const stored = persisted as Partial<DashboardState>
        const layouts = freshLayouts()
        for (const role of Object.keys(layouts) as RoleId[]) {
          const saved = stored.layouts?.[role]
          if (!saved) continue
          const order = [...saved.order.filter((id) => ALL_WIDGETS.includes(id)), ...ALL_WIDGETS.filter((id) => !saved.order.includes(id))]
          layouts[role] = { order, hidden: saved.hidden.filter((id) => ALL_WIDGETS.includes(id)) }
        }
        return { ...current, ...stored, layouts, isEditing: false }
      },
    },
  ),
)
