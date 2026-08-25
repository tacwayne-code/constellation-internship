/**
 * 左侧导航 schema（3 段分组，对齐 dist 反推）
 */
export interface NavItem {
  id: string
  label: string
  icon: string
}

export interface NavSection {
  id: string
  label: string
  items: NavItem[]
}

export const NAV_SECTIONS: NavSection[] = [
  {
    id: 'collab',
    label: '项目协作',
    items: [
      { id: 'overview', label: '项目总览', icon: 'grid' },
      { id: 'delivery', label: '交付包', icon: 'layers' },
      { id: 'design', label: '设计与图纸', icon: 'file' },
      { id: 'procurement', label: '采购与交期', icon: 'truck' },
    ],
  },
  {
    id: 'field',
    label: '现场交付',
    items: [
      { id: 'sales', label: '订单管理', icon: 'handshake' },
      { id: 'logistics', label: '物流管理', icon: 'route' },
      { id: 'inventory', label: '现场库存', icon: 'box' },
      { id: 'products', label: '产品主数据', icon: 'box' },
      { id: 'people', label: '人员管理', icon: 'users' },
      { id: 'vendors', label: '供应商交付', icon: 'handshake' },
      { id: 'workshop', label: '生产车间', icon: 'factory' },
      { id: 'field', label: '任务状态', icon: 'grid' },
    ],
  },
  {
    id: 'systems',
    label: '系统实施',
    items: [
      { id: 'mes', label: 'MES / WCS 实施', icon: 'code' },
      { id: 'commissioning', label: '调试与验收', icon: 'check' },
    ],
  },
]

export const ALL_MODULE_IDS = NAV_SECTIONS.flatMap((s) => s.items.map((i) => i.id))

/** 由 moduleId 查找标签 */
export function moduleLabel(id: string): string {
  for (const section of NAV_SECTIONS) {
    const found = section.items.find((i) => i.id === id)
    if (found) return found.label
  }
  return id
}
