/**
 * 前端数据契约（对应 dist 反推的 S() 行生成器与各实体结构）
 */

export type Tone = 'success' | 'warning' | 'danger' | 'neutral' | 'blue' | 'purple' | 'orange' | 'green' | 'red'

export type DataSource = 'live' | 'mock'

/** 通用行记录（S() 契约） */
export interface SRow {
  id: string
  name: string
  cells?: string[]
  status?: string
  tone: Tone
  fields?: [string, string][]
  progress?: number | null
}

/** 项目组合（驾驶舱项目卡） */
export interface Project {
  id: string
  name: string
  short: string
  type: string
  owner: string
  status: string
  tone: Tone
  progress: number
  risks: number
  blockers: number
  due: string
  phase: string
  start?: string
  stage?: string
  fields?: [string, string][]
}

/** 甘特任务 */
export interface GanttTask extends SRow {
  start: number
  width: number
  owner?: string
  stage?: string
  tags?: string[]
  hours?: { effective: number; remaining: number; total: number }
  _date_begin?: string
  _date_end?: string
}

/** 风险/问题 */
export interface RiskItem extends SRow {
  category?: string
  level?: string
  tag?: string
  title?: string
  meta?: string
  icon?: string
}

/** 驾驶舱 KPI 汇总 */
export interface PortfolioSummary {
  projects_total: number
  projects_active: number
  progress_avg: number
  risks_total: number
  blockers_total: number
  by_tone: { green: number; amber: number; red: number }
  projects: Project[]
}

/** 模块配置（前端静态 metadata，stats 可被后端 config 覆盖） */
export interface ModuleConfig {
  id: string
  title: string
  subtitle: string
  icon: string
  stats?: [string, string][]
  focus?: string
  workflow?: string[]
  board?: boolean
}
