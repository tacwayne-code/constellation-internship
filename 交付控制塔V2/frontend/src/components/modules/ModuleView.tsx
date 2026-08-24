import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../../api/client'
import { useNavigation } from '../../store/navigationStore'
import { getModule } from '../../config/modules'
import { Icon } from '../common/Icon'
import { ProgressBar, StatusDot } from '../common/Status'
import { QueryView } from '../common/QueryView'
import { Drawer } from '../common/Drawer'
import { Pagination } from '../common/Pagination'
import { FilterBar } from '../common/FilterBar'
import { EmptyState } from '../common/EmptyState'
import { SearchInput } from '../common/SearchInput'
import { ModuleShell } from './ModuleShell'
import {
  DeliveryTowerView,
  ProcurementTable,
  LogisticsTable,
  PoDrawer,
} from './DeliveryTowerView'
import type {
  ProcurementOverview,
  PoItem,
  LogisticsOverview,
} from './DeliveryTowerView'
import { ListImportDrawer } from './ListImportDrawer'
import { useGantt, useModuleRows, useModuleConfig } from '../../api/modules/useData'
import type { GanttTask, SRow, Tone } from '../../types/contract'

const PAGE_SIZE = 10

/* ====================================================================
 *  通用：分页表格 + 抽屉
 * ==================================================================== */
export function GenericTableView({
  rows,
  showProgress = true,
  extra,
}: {
  rows: SRow[]
  showProgress?: boolean
  extra?: (row: SRow) => React.ReactNode
}) {
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState('全部')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<SRow | null>(null)

  // 1. 搜索过滤 → 2. 状态过滤 → 3. 分页
  const searchMatch = (r: SRow, q: string) => {
    if (!q) return true
    const hay = `${r.name} ${(r.cells ?? []).join(' ')} ${(r.fields ?? []).map((f) => f[1]).join(' ')}`.toLowerCase()
    return hay.includes(q.toLowerCase())
  }
  const filtered1 = search ? rows.filter((r) => searchMatch(r, search)) : rows
  const filtered = filter === '全部' ? filtered1 : filtered1.filter((r) => r.status === filter)
  const total = filtered.length
  const start = (page - 1) * PAGE_SIZE
  const pageRows = filtered.slice(start, start + PAGE_SIZE)

  return (
    <div className="panel module-table-panel">
      <div className="table-toolbar">
        <SearchInput value={search} onChange={setSearch} placeholder="搜索名称、编号或其他字段..." />
        <FilterBar rows={rows} active={filter} onChange={(s) => { setFilter(s); setPage(1) }} />
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>编号</th>
            <th>名称</th>
            <th>状态</th>
            {showProgress && <th>进度</th>}
          </tr>
        </thead>
        <tbody>
          {pageRows.map((r) => (
            <tr key={r.id} onClick={() => setSelected(r)}>
              <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{r.id}</td>
              <td>
                <div className="cell-name">{r.name}</div>
                {r.cells && r.cells.length > 2 && (
                  <div className="cell-sub">{r.cells.slice(1, 4).join(' · ')}</div>
                )}
              </td>
              <td>
                <span className="drawer-status">
                  <StatusDot tone={r.tone} /> {r.status ?? '—'}
                </span>
              </td>
              {showProgress && (
                <td style={{ width: 140 }}>
                  {r.progress != null ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <ProgressBar value={r.progress} />
                      <span style={{ fontSize: 12, color: 'var(--muted)' }}>{r.progress}%</span>
                    </div>
                  ) : (
                    '—'
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {total === 0 && <div className="state-block">暂无数据</div>}
      <div style={{ padding: '12px 0 4px' }}>
        <Pagination page={page} total={total} pageSize={PAGE_SIZE} onChange={setPage} />
      </div>
      {selected && (
        <Drawer
          title={selected.name}
          subtitle={selected.id}
          tone={selected.tone}
          status={selected.status}
          fields={selected.fields}
          extra={extra ? extra(selected) : undefined}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}

/* ====================================================================
 *  下钻辅助：抽屉内明细表格 / 面板区块
 * ==================================================================== */

/** 抽屉内嵌的明细列表（子件、订单行等） */
function DrillList({ title, rows }: { title: string; rows: SRow[] }) {
  if (!rows.length) {
    return <div className="subtitle" style={{ padding: '6px 2px' }}>暂无明细数据</div>
  }
  return (
    <div className="drawer-section">
      <h4>{title}（{rows.length}）</h4>
      <div className="drill-list">
        {rows.map((r) => (
          <div className="drill-item" key={r.id}>
            <div className="drill-item-main">
              <div className="cell-name">{r.name}</div>
              {r.cells && r.cells.length > 2 && (
                <div className="cell-sub">{r.cells.slice(2, 6).join(' · ')}</div>
              )}
            </div>
            <span className="drawer-status"><StatusDot tone={r.tone} /> {r.status ?? '—'}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** BOM 子件下钻（设计与图纸） */
function BomDetail({ row }: { row: SRow }) {
  const id = String(row.id).replace('BOM-', '')
  const q = useQuery({
    queryKey: ['bom-lines', id],
    queryFn: () => apiFetch<SRow[]>(`/modules/design/bom/${id}/lines`).then((r) => r.data),
    enabled: !!id && id !== row.id,
    staleTime: 60_000,
  })
  return (
    <QueryView query={q} empty={<div className="subtitle" style={{ padding: '6px 2px' }}>暂无子件数据</div>}>
      {(lines) => <DrillList title="BOM 子件清单" rows={lines} />}
    </QueryView>
  )
}

/* ====================================================================
 *  DesignView：设计与图纸（Odoo BOM + 子件下钻）
 * ==================================================================== */
function DesignView() {
  const rowsQ = useModuleRows('design')
  return (
    <QueryView query={rowsQ} empty={<EmptyState module={getModule('design')} />}>
      {() => <GenericTableView rows={rowsQ.data ?? []} extra={(row) => <BomDetail row={row} />} />}
    </QueryView>
  )
}

/* ====================================================================
 *  InventoryView：现场库存（物料 + 库位分布 + 收发流水）
 * ==================================================================== */
function InventoryView() {
  const configQ = useModuleConfig('inventory')
  const [view, setView] = useState<'overview' | 'products' | 'locations' | 'moves'>('overview')
  // 全量数据懒加载：仅进入对应明细才拉，避免概要层拉全量拖慢
  const rowsQ = useModuleRows('inventory', view === 'products')
  const locQ = useQuery({
    queryKey: ['inv-locations'],
    queryFn: () => apiFetch<SRow[]>('/modules/inventory/locations').then((r) => r.data),
    staleTime: 60_000,
    enabled: view === 'locations',
  })
  const movesQ = useQuery({
    queryKey: ['inv-moves'],
    queryFn: () => apiFetch<SRow[]>('/modules/inventory/moves').then((r) => r.data),
    staleTime: 60_000,
    enabled: view === 'moves',
  })

  const rows = rowsQ.data ?? []
  // 分类卡 count 用 config 轻量统计（不再依赖全量 rows/locations/moves）
  const statMap: Record<string, string> = {}
  for (const [k, v] of (configQ.data?.stats ?? [])) statMap[k] = v
  const productCount = Number(statMap['产品种类']) || rows.length
  const locCount = Number(statMap['库位']) || 0
  const moveCount = Number(statMap['收发流水']) || 0

  return (
    <div className="module-view">
      {view === 'overview' && (
        <>
          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">库存分类 · 点击进入明细</span>
            </div>
            <div className="category-grid">
              {[
                { key: 'products', title: '产品库存', count: productCount, tone: 'blue' as Tone, desc: '物料库存明细' },
                { key: 'locations', title: '库位分布', count: locCount, tone: 'green' as Tone, desc: '各库位库存' },
                { key: 'moves', title: '收发流水', count: moveCount, tone: 'orange' as Tone, desc: '最近出入库' },
              ].map((c) => (
                <div className="category-card" key={c.key} onClick={() => setView(c.key as 'products' | 'locations' | 'moves')}>
                  <div className="category-card-head">
                    <StatusDot tone={c.tone} />
                    <span className="category-card-title">{c.title}</span>
                  </div>
                  <div className="category-card-count">{c.count}</div>
                  <div className="category-card-desc">{c.desc}</div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {view !== 'overview' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <button className="ghost-btn" onClick={() => setView('overview')}>
            <Icon name="arrow" size={13} /> 返回概要
          </button>
        </div>
      )}

      {view === 'products' && (
        <QueryView query={rowsQ} empty={<EmptyState module={getModule('inventory')} />}>
          {() => <GenericTableView rows={rows} />}
        </QueryView>
      )}
      {view === 'locations' && (
        <QueryView query={locQ} empty={<div className="state-block">暂无库位数据</div>}>
          {(r) => <GenericTableView rows={r} />}
        </QueryView>
      )}
      {view === 'moves' && (
        <QueryView query={movesQ} empty={<div className="state-block">暂无收发流水</div>}>
          {(r) => <GenericTableView rows={r} />}
        </QueryView>
      )}
    </div>
  )
}

/* ====================================================================
 *  OverviewView：项目健康度 + 风险/摘要 + 当前项目甘特（项目内）
 * ==================================================================== */
function OverviewView({ projectId }: { projectId: string | null }) {
  const openProject = useNavigation((s) => s.openProject)
  const rowsQ = useModuleRows('overview')
  const ganttQ = useGantt(projectId ?? '')
  const inProject = !!projectId

  return (
    <>
      {/* 1. 风险与项目执行摘要（双栏，置顶） */}
      <div className="portfolio-summary-grid">
        <RiskSummaryCard />
        <BlockerSummaryCard />
      </div>

      {/* 2. 项目健康度 */}
      <QueryView
        query={rowsQ}
        empty={<EmptyState module={{ id: 'overview', title: '项目总览', subtitle: '', icon: 'grid' } as any} />}
      >
        {(rows) => <ProjectHealthList rows={rows} onOpen={openProject} />}
      </QueryView>

      {/* 3. 当前项目甘特图（仅项目内） */}
      {inProject ? (
        <QueryView query={ganttQ} empty={<span>该项目暂无任务</span>}>
          {(tasks) => <GanttPanel tasks={tasks} />}
        </QueryView>
      ) : (
        <div className="panel module-gantt-hint">
          <div className="panel-header">
            <span className="panel-title">
              <Icon name="route" size={16} style={{ color: 'var(--blue)' } as React.CSSProperties} />
              项目里程碑
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '20px 0', color: 'var(--muted)' }}>
            <Icon name="info" size={16} />
            <span>从上方选择一个项目查看其交付里程碑与甘特图</span>
          </div>
        </div>
      )}
    </>
  )
}

function fmtDate(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function GanttPanel({ tasks }: { tasks: GanttTask[] }) {
  const [selected, setSelected] = useState<GanttTask | null>(null)
  const [collapsedStages, setCollapsedStages] = useState<Set<string>>(new Set())
  const [hoveredBar, setHoveredBar] = useState<{
    task: GanttTask; rect: DOMRect; tags?: string[]; hours?: { effective: number; remaining: number; total: number }
    dateBegin: string; dateEnd: string; tone: string
  } | null>(null)
  const [tooltipPos, setTooltipPos] = useState<{ top: number; left: number; placement: 'bottom' | 'top' }>({ top: 0, left: 0, placement: 'bottom' })
  const panelRef = useRef<HTMLDivElement | null>(null)
  const tooltipRef = useRef<HTMLDivElement | null>(null)

  // ---- 日期范围选择（默认显示全部） ----
  const allDates = useMemo(() => {
    const times: number[] = []
    for (const t of tasks) {
      const x = t as any
      const b = x._date_begin; const e = x._date_end
      if (b) times.push(new Date(b).getTime())
      if (e) times.push(new Date(e).getTime())
    }
    return { min: Math.min(...times), max: Math.max(...times) }
  }, [tasks])

  const defaultFrom = allDates.min ? fmtDate(new Date(allDates.min)) : ''
  const defaultTo = allDates.max ? fmtDate(new Date(allDates.max)) : ''
  const [dateFrom, setDateFrom] = useState(defaultFrom)
  const [dateTo, setDateTo] = useState(defaultTo)

  const rangeMinTs = dateFrom ? new Date(dateFrom).getTime() : allDates.min
  const rangeMaxTs = dateTo ? new Date(dateTo).getTime() + 86400000 : allDates.max

  // 动态计算时间轴：基于选定日期范围
  const { datedTasks, axisTicks } = useMemo(() => {
    const dated = tasks.filter((t) => {
      const x = t as any
      // 至少要有一个有效日期（_date_begin 或 _date_end）
      if (!x._date_begin && !x._date_end) return false
      const b = x._date_begin ? new Date(x._date_begin).getTime() : 0
      const e = x._date_end ? new Date(x._date_end).getTime() : b
      // 任务与选定区间有交集
      return e >= rangeMinTs && b <= rangeMaxTs
    })
    if (dated.length === 0 || !rangeMinTs || !rangeMaxTs) {
      return { datedTasks: dated, axisTicks: [] as { x: number; label: string }[] }
    }
    const minTs = rangeMinTs
    const maxTs = rangeMaxTs
    const range = maxTs - minTs

    const ticks: { x: number; label: string }[] = []
    const cur = new Date(minTs)
    cur.setDate(1); cur.setHours(0, 0, 0, 0)
    while (cur.getTime() < maxTs) {
      const x = ((cur.getTime() - minTs) / range) * 100
      ticks.push({ x, label: `${cur.getFullYear()}/${String(cur.getMonth() + 1).padStart(2, '0')}` })
      cur.setMonth(cur.getMonth() + 1)
    }
    return { datedTasks: dated, axisTicks: ticks }
  }, [tasks, rangeMinTs, rangeMaxTs])

  // 重新计算 start/width 百分比（相对于选中区间）
  const tasksWithPosition = useMemo(() => {
    if (!rangeMinTs || !rangeMaxTs) return datedTasks
    const minTs = rangeMinTs
    const maxTs = rangeMaxTs
    const range = maxTs - minTs
    return datedTasks.map((t) => {
      const x = t as any
      const b = x._date_begin ? new Date(x._date_begin).getTime() : minTs
      const e = x._date_end ? new Date(x._date_end).getTime() : b
      const start = Math.max(0, ((b - minTs) / range) * 100)
      const width = Math.max(1, ((Math.min(e, maxTs) - Math.max(b, minTs)) / range) * 100)
      return { ...t, start, width }
    })
  }, [datedTasks, rangeMinTs, rangeMaxTs])

  const tasksByStage = useMemo(() => {
    const map = new Map<string, typeof tasksWithPosition[number][]>()
    for (const t of tasksWithPosition) {
      const s = (t as any).stage ?? '未分类'
      if (!map.has(s)) map.set(s, [])
      map.get(s)!.push(t)
    }
    return Array.from(map.entries())
  }, [tasksWithPosition])

  // 快捷时间段预设
  const quickRanges = useMemo(() => {
    const now = new Date()
    const today = fmtDate(now)
    const m30 = fmtDate(new Date(now.getTime() - 30 * 86400000))
    const m90 = fmtDate(new Date(now.getTime() - 90 * 86400000))
    const next90 = fmtDate(new Date(now.getTime() + 90 * 86400000))
    const allFrom = allDates.min ? fmtDate(new Date(allDates.min)) : '2025-01-01'
    const allTo = allDates.max ? fmtDate(new Date(allDates.max)) : fmtDate(now)
    return [
      { label: '近 30 天', from: m30, to: today },
      { label: '近 90 天', from: m90, to: today },
      { label: '未来 90 天', from: today, to: next90 },
      { label: '全部', from: allFrom, to: allTo },
    ]
  }, [allDates])

  const datedAll = useMemo(() => tasks.filter((t: any) => t._date_begin || t._date_end), [tasks])

  // 是否超出实际数据时间范围（容差 1 天）
  const rangeOverflow = !!allDates.min && !!allDates.max && (
    rangeMinTs < allDates.min - 86400000 || rangeMaxTs > allDates.max + 86400000
  )

  const toggleStage = (stage: string) => {
    setCollapsedStages((prev) => {
      const next = new Set(prev)
      if (next.has(stage)) next.delete(stage)
      else next.add(stage)
      return next
    })
  }
  const allCollapsed = tasksByStage.length > 0 && tasksByStage.every(([s]) => collapsedStages.has(s))
  const toggleAllStages = () => {
    if (allCollapsed) setCollapsedStages(new Set())
    else setCollapsedStages(new Set(tasksByStage.map(([s]) => s)))
  }
  const resetRange = () => { setDateFrom(defaultFrom); setDateTo(defaultTo) }

  // 自定义 tooltip 定位：测量真实尺寸后智能避让
  useLayoutEffect(() => {
    if (!hoveredBar || !panelRef.current || !tooltipRef.current) return
    const panelRect = panelRef.current.getBoundingClientRect()
    const ttRect = tooltipRef.current.getBoundingClientRect()
    const barRect = hoveredBar.rect
    let placement: 'bottom' | 'top' = 'bottom'
    let top = barRect.bottom - panelRect.top + 8
    let left = barRect.left - panelRect.left
    if (top + ttRect.height > panelRect.height - 8) {
      placement = 'top'
      top = barRect.top - panelRect.top - ttRect.height - 8
    }
    if (left + ttRect.width > panelRect.width - 8) {
      left = Math.max(8, barRect.right - panelRect.left - ttRect.width)
    }
    if (left < 8) left = 8
    if (top < 8) top = 8
    setTooltipPos({ top, left, placement })
  }, [hoveredBar])

  return (
    <>
      {datedAll.length > 0 && (
        <div className="panel gantt-shell">
          <div className="gantt-panel" ref={panelRef}>
            {/* 顶部：标题 + 全部折叠/展开 */}
            <div className="panel-header">
              <span className="panel-title">
                <Icon name="route" size={16} style={{ color: 'var(--blue)' } as React.CSSProperties} />
                项目里程碑
              </span>
              {tasksByStage.length > 0 && (
                <button className="gantt-toggle-all-btn" onClick={toggleAllStages}>
                  {allCollapsed ? '全部展开' : '全部折叠'}
                </button>
              )}
            </div>

            {/* 日期范围选择器 */}
            <div className="gantt-date-range">
              <div className="gantt-date-inputs">
                <label>从</label>
                <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
                <label>至</label>
                <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
              </div>
              <div className="gantt-quick-ranges">
                {quickRanges.map((q) => (
                  <button
                    key={q.label}
                    className={dateFrom === q.from && dateTo === q.to ? 'active' : ''}
                    onClick={() => { setDateFrom(q.from); setDateTo(q.to) }}
                  >
                    {q.label}
                  </button>
                ))}
              </div>
              {rangeOverflow && (
                <span className="gantt-overflow-tip" role="alert">
                  <Icon name="alert" size={12} />
                  <span>时间数据超过当前模块数据</span>
                  <button className="gantt-overflow-reset" onClick={resetRange}>回到数据范围</button>
                </span>
              )}
              <span className="subtitle" style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}>
                {tasksWithPosition.length} 个任务 · {tasksByStage.length} 个阶段
              </span>
            </div>

            {/* 时间轴（动态按月/周/日） */}
            <div className="gantt-axis">
              <div />
              <div className="gantt-axis-labels">
                {axisTicks.map((t, i) => (
                  <span key={i}>{t.label}</span>
                ))}
              </div>
            </div>

            {/* 任务按阶段分组（可折叠/展开） */}
            {tasksByStage.map(([stage, items]) => {
              const collapsed = collapsedStages.has(stage)
              return (
                <div key={stage} className="gantt-stage-group">
                  <div
                    className="gantt-stage-label gantt-stage-label-collapsible"
                    onClick={() => toggleStage(stage)}
                    role="button"
                    aria-expanded={!collapsed}
                  >
                    <span className={`gantt-stage-caret${collapsed ? ' is-collapsed' : ''}`}>▾</span>
                    <span>{stage} · {items.length}</span>
                  </div>
                  {!collapsed && items.map((t) => {
                    const tags = (t as any).tags as string[] | undefined
                    const hours = (t as any).hours as { effective: number; remaining: number; total: number } | undefined
                    const dateBegin = (t as any)._date_begin ? new Date((t as any)._date_begin).toLocaleDateString('zh-CN') : '—'
                    const dateEnd = (t as any)._date_end ? new Date((t as any)._date_end).toLocaleDateString('zh-CN') : '—'
                    const originalTask = tasks.find((orig) => orig.id === t.id) as GanttTask
                    return (
                      <div className="gantt-row" key={t.id} onClick={() => setSelected(originalTask ?? (t as any as GanttTask))} style={{ cursor: 'pointer' }}>
                        <div className="gantt-label">
                          <div className="gantt-label-name">{t.name}</div>
                          <div className="gantt-label-owner">
                            {t.owner ?? '—'} · {dateBegin} → {dateEnd}
                            {tags && tags.length > 0 && (
                              <span className="task-tags" style={{ marginLeft: 4 }}>
                                {tags.slice(0, 2).map((tag: string) => (
                                  <span key={tag} className="task-tag">{tag}</span>
                                ))}
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="gantt-track">
                          <div
                            className={`gantt-bar tone-${t.tone} gantt-bar-interactive`}
                            style={{ left: `${t.start}%`, width: `${Math.max(1, t.width)}%` }}
                            onMouseEnter={(e) => {
                              setHoveredBar({
          task: originalTask ?? (t as any),
          rect: e.currentTarget.getBoundingClientRect(),
          tags, hours,
          dateBegin, dateEnd, tone: t.tone,
        })
                            }}
                            onMouseLeave={() => setHoveredBar(null)}
                          >
                            <div className="gantt-bar-progress" style={{ width: `${t.progress}%` }} />
                            {t.width > 6 && <span className="gantt-bar-label">{t.name.slice(0, 15)} · {t.progress}%</span>}
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )
            })}
            {hoveredBar && (
              <div
                ref={tooltipRef}
                className={`gantt-tooltip tone-${hoveredBar.tone} placement-${tooltipPos.placement}`}
                style={{ top: tooltipPos.top, left: tooltipPos.left }}
                role="tooltip"
              >
                <div className="gantt-tooltip-header">
                  <span className={`gantt-tooltip-dot tone-${hoveredBar.tone}`} />
                  <span className="gantt-tooltip-name">{hoveredBar.task.name}</span>
                </div>
                <div className="gantt-tooltip-row gantt-tooltip-date">
                  <Icon name="clock" size={12} />
                  <span>{hoveredBar.dateBegin} → {hoveredBar.dateEnd}</span>
                </div>
                <div className="gantt-tooltip-row">
                  <Icon name="layers" size={12} />
                  <span className="gantt-tooltip-label">阶段</span>
                  <span className="gantt-tooltip-value">{hoveredBar.task.stage ?? '—'}</span>
                </div>
                <div className="gantt-tooltip-row">
                  <Icon name="users" size={12} />
                  <span className="gantt-tooltip-label">负责人</span>
                  <span className="gantt-tooltip-value">{hoveredBar.task.owner ?? '—'}</span>
                </div>
                {hoveredBar.task.status && (
                  <div className="gantt-tooltip-row">
                    <Icon name="check" size={12} />
                    <span className="gantt-tooltip-label">状态</span>
                    <span className={`gantt-tooltip-chip tone-${hoveredBar.tone}`}>{hoveredBar.task.status}</span>
                  </div>
                )}
                <div className="gantt-tooltip-progress">
                  <div className="gantt-tooltip-progress-bar">
                    <div className="gantt-tooltip-progress-fill" style={{ width: `${hoveredBar.task.progress ?? 0}%` }} />
                  </div>
                  <span className="gantt-tooltip-progress-text">进度 {hoveredBar.task.progress ?? 0}%</span>
                </div>
                {hoveredBar.hours && (hoveredBar.hours.effective > 0 || hoveredBar.hours.remaining > 0) && (
                  <div className="gantt-tooltip-row gantt-tooltip-row-meta">
                    <Icon name="bolt" size={12} />
                    <span className="gantt-tooltip-label">工时</span>
                    <span className="gantt-tooltip-value">
                      {hoveredBar.hours.effective.toFixed(1)}h / 剩余 {hoveredBar.hours.remaining.toFixed(1)}h
                    </span>
                  </div>
                )}
                {hoveredBar.tags && hoveredBar.tags.length > 0 && (
                  <div className="gantt-tooltip-tags">
                    {hoveredBar.tags.slice(0, 4).map((tag: string) => (
                      <span key={tag} className="task-tag">{tag}</span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {selected && (
        <Drawer title={selected.name} subtitle={selected.id} tone={selected.tone} status={selected.status} fields={selected.fields} onClose={() => setSelected(null)} />
      )}
    </>
  )
}

function ProjectHealthList({ rows, onOpen }: { rows: SRow[]; onOpen: (id: string) => void }) {
  return (
    <div className="panel module-health-panel">
      <div className="panel-header">
        <span className="panel-title">
          <Icon name="chart" size={16} style={{ color: 'var(--blue)' } as React.CSSProperties} />
          项目健康度
        </span>
        <span className="subtitle" style={{ margin: 0 }}>{rows.length} 个项目 · 点击进入</span>
      </div>
      {rows.map((r) => (
        <div className="risk-item" key={r.id} onClick={() => onOpen(r.id)} style={{ cursor: 'pointer' }}>
          <div className={`risk-icon ${r.tone === 'danger' ? 'red' : r.tone === 'warning' ? 'orange' : 'green'}`}>
            <Icon name="grid" size={15} />
          </div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div className="risk-title">{r.name}</div>
            <div className="risk-meta">
              {r.status}
              {r.fields?.find((f) => f[0] === '截止日期')?.[1] !== '—'
                ? ` · 截止 ${r.fields?.find((f) => f[0] === '截止日期')?.[1]}`
                : ''}
            </div>
          </div>
          {r.progress != null && (
            <div style={{ width: 140, display: 'flex', alignItems: 'center', gap: 8 }}>
              <ProgressBar value={r.progress} />
              <span style={{ fontSize: 12, color: 'var(--muted)', whiteSpace: 'nowrap' }}>{r.progress}%</span>
            </div>
          )}
          <Icon name="chevron" size={14} className="chevron" />
        </div>
      ))}
    </div>
  )
}

function RiskSummaryCard() {
  const risksQ = useModuleRows('field')
  const groups = useMemo(() => {
    const list = risksQ.data ?? []
    const g = { high: 0, medium: 0, low: 0 }
    for (const r of list) {
      const tone = r.tone
      if (tone === 'danger') g.high++
      else if (tone === 'warning') g.medium++
      else g.low++
    }
    return g
  }, [risksQ.data])

  return (
    <div className="panel portfolio-risk-panel">
      <div className="panel-header">
        <span className="panel-title">
          <Icon name="alert" size={16} style={{ color: 'var(--red)' } as React.CSSProperties} />
          风险严重度分布
        </span>
      </div>
      <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        <div className="kpi-card">
          <div className="kpi-icon red"><Icon name="alert" size={16} /></div>
          <div className="kpi-copy">
            <div className="num">{groups.high}</div>
            <div className="label">高危</div>
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-icon orange"><Icon name="alert" size={16} /></div>
          <div className="kpi-copy">
            <div className="num">{groups.medium}</div>
            <div className="label">中等</div>
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-icon green"><Icon name="check" size={16} /></div>
          <div className="kpi-copy">
            <div className="num">{groups.low}</div>
            <div className="label">一般</div>
          </div>
        </div>
      </div>
    </div>
  )
}

function BlockerSummaryCard() {
  const rowsQ = useModuleRows('overview')
  const stats = useMemo(() => {
    const projects = (rowsQ.data ?? []) as Array<SRow & { risks?: number; blockers?: number }>
    let activeTasks = 0
    let overdue = 0
    for (const p of projects) {
      activeTasks += p.risks ?? 0
      overdue += p.blockers ?? 0
    }
    return { activeTasks, overdue }
  }, [rowsQ.data])

  return (
    <div className="panel portfolio-blocker-panel">
      <div className="panel-header">
        <span className="panel-title">
          <Icon name="pin" size={16} style={{ color: 'var(--orange)' } as React.CSSProperties} />
          项目执行摘要
        </span>
      </div>
      <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        <div className="kpi-card">
          <div className="kpi-icon blue"><Icon name="grid" size={16} /></div>
          <div className="kpi-copy">
            <div className="num">{rowsQ.data?.length ?? 0}</div>
            <div className="label">项目</div>
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-icon orange"><Icon name="clock" size={16} /></div>
          <div className="kpi-copy">
            <div className="num">{stats.activeTasks}</div>
            <div className="label">活跃任务</div>
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-icon red"><Icon name="alert" size={16} /></div>
          <div className="kpi-copy">
            <div className="num" style={{ color: 'var(--red)' }}>{stats.overdue}</div>
            <div className="label">逾期任务</div>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ====================================================================
 *  RiskControlView（field）
 * ==================================================================== */
function RiskControlView() {
  const rowsQ = useModuleRows('field')
  const [filter, setFilter] = useState('全部')
  const filtered = filter === '全部' ? (rowsQ.data ?? []) : (rowsQ.data ?? []).filter((r) => r.status === filter)

  return (
    <QueryView query={rowsQ} empty={<EmptyState module={getModule('field')} />}>
      {() => {
        const grouped = {
          high: filtered.filter((r) => r.tone === 'danger'),
          medium: filtered.filter((r) => r.tone === 'warning'),
          low: filtered.filter((r) => r.tone !== 'danger' && r.tone !== 'warning'),
        }
        return (
          <>
            <div className="panel" style={{ paddingBottom: 4 }}>
              <FilterBar rows={rowsQ.data ?? []} active={filter} onChange={setFilter} />
            </div>
            <div className="risk-matrix">
              <div className="risk-matrix-cell high">
                <div className="num">{grouped.high.length}</div>
                <div className="label">高危 · 需立即处理</div>
              </div>
              <div className="risk-matrix-cell medium">
                <div className="num">{grouped.medium.length}</div>
                <div className="label">中等 · 持续关注</div>
              </div>
              <div className="risk-matrix-cell low">
                <div className="num">{grouped.low.length}</div>
                <div className="label">一般 · 观察</div>
              </div>
            </div>
            <RiskGroup title="高危风险" items={grouped.high} />
            <RiskGroup title="中等风险" items={grouped.medium} />
            <RiskGroup title="一般风险" items={grouped.low} />
          </>
        )
      }}
    </QueryView>
  )
}

function RiskGroup({ title, items }: { title: string; items: SRow[] }) {
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<SRow | null>(null)
  const start = (page - 1) * PAGE_SIZE
  const pageRows = items.slice(start, start + PAGE_SIZE)
  if (items.length === 0) return null
  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">{title} <span style={{ fontWeight: 400, color: 'var(--muted)' }}>· {items.length}</span></span>
      </div>
      {pageRows.map((r) => (
        <div className="risk-item" key={r.id} onClick={() => setSelected(r)} style={{ cursor: 'pointer' }}>
          <div className={`risk-icon ${r.tone === 'danger' ? 'red' : r.tone === 'warning' ? 'orange' : 'blue'}`}>
            <Icon name="alert" size={15} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="risk-title">{r.name}</div>
            <div className="risk-meta">
              {r.fields?.find((f) => f[0] === '负责人')?.[1] ?? r.status}
              {r.fields?.find((f) => f[0] === '截止日期')?.[1] && r.fields?.find((f) => f[0] === '截止日期')?.[1] !== '—'
                ? ` · 截止 ${r.fields?.find((f) => f[0] === '截止日期')?.[1]}`
                : ''}
            </div>
          </div>
          <span className="risk-tag">{r.status ?? '—'}</span>
        </div>
      ))}
      <Pagination page={page} total={items.length} pageSize={PAGE_SIZE} onChange={setPage} />
      {selected && (
        <Drawer title={selected.name} subtitle={selected.id} tone={selected.tone} status={selected.status} fields={selected.fields} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}

/* ====================================================================
 *  ProcurementView
 * ==================================================================== */
/** 清单板块（按 origin 聚合的清单导入采购单） */
interface ImportList {
  list_name: string
  business_type: string
  po_count: number
  urgent: number
  done: number
  orders: PoItem[]
}

const IMPORT_BIZ_LABEL: Record<string, string> = { warehouse: '立体仓储', rgv: 'RGV', stacker: '堆垛机', other: '其他' }

function ProcurementView() {
  const queryClient = useQueryClient()
  const overviewQ = useQuery({
    queryKey: ['delivery-tower-procurement'],
    queryFn: () => apiFetch<ProcurementOverview>('/delivery-tower/procurement/overview?limit=500').then((r) => r.data),
    staleTime: 60_000,
  })
  const [listSearch, setListSearch] = useState('')
  const listsQ = useQuery({
    queryKey: ['procurement-lists', listSearch],
    queryFn: () => apiFetch<{ total: number; items: ImportList[] }>(
      `/procurement/list/lists${listSearch ? `?q=${encodeURIComponent(listSearch)}` : ''}`,
    ).then((r) => r.data),
    staleTime: 60_000,
  })
  const [view, setView] = useState<'overview' | 'pending' | 'transit' | 'normal' | 'all' | 'lists'>('overview')
  const [selectedPo, setSelectedPo] = useState<PoItem | null>(null)
  const [listImportOpen, setListImportOpen] = useState(false)
  const [expandedList, setExpandedList] = useState<string | null>(null)
  const [listBiz, setListBiz] = useState<'all' | 'warehouse' | 'rgv' | 'stacker' | 'other'>('all')

  return (
    <>
      <QueryView query={overviewQ} empty={<EmptyState module={getModule('procurement')} />}>
        {(overview) => {
          const pending = overview.urgent_pending ?? overview.by_priority['1'] ?? []
          const transit = overview.urgent_transit ?? []
          const normal = overview.by_priority['0'] ?? []
          const all = overview.items ?? []

          if (view === 'overview') {
            return (
              <div className="module-view">
                <div className="panel">
                  <div className="panel-header">
                    <span className="panel-title">采购分类 · 点击进入明细</span>
                  </div>
                  <div className="category-grid">
                    {[
                      { key: 'pending', title: '紧急 · 待发起', count: pending.length, tone: 'red' as Tone, desc: '需立即下单' },
                      { key: 'transit', title: '紧急 · 在途', count: transit.length, tone: 'orange' as Tone, desc: '已下单待收货' },
                      { key: 'normal', title: '普通采购', count: normal.length, tone: 'neutral' as Tone, desc: '常规采购' },
                      { key: 'all', title: '全部采购单', count: all.length, tone: 'blue' as Tone, desc: '查看全量' },
                      { key: 'lists', title: '清单导入', count: listsQ.data?.total ?? 0, tone: 'green' as Tone, desc: '按清单查看' },
                    ].map((c) => (
                      <div className="category-card" key={c.key} onClick={() => setView(c.key as 'pending' | 'transit' | 'normal' | 'all' | 'lists')}>
                        <div className="category-card-head">
                          <StatusDot tone={c.tone} />
                          <span className="category-card-title">{c.title}</span>
                        </div>
                        <div className="category-card-count" style={{ color: c.tone === 'red' ? 'var(--red)' : 'var(--ink)' }}>{c.count}</div>
                        <div className="category-card-desc">{c.desc}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )
          }

          if (view === 'lists') {
            const lists = (listsQ.data?.items ?? []).filter((l) => listBiz === 'all' || l.business_type === listBiz)
            return (
              <div className="list-page">
                <div className="list-toolbar">
                  <button className="ghost-btn" onClick={() => setView('overview')}>
                    <Icon name="arrow" size={13} /> 返回概要
                  </button>
                  <button className="primary-button" onClick={() => setListImportOpen(true)}>
                    <Icon name="upload" size={14} /> 新建清单导入
                  </button>
                  <h2 className="list-toolbar-title">清单导入</h2>
                  <div className="list-toolbar-right">
                    <select
                      style={{ fontSize: 12, padding: '7px 10px', background: 'var(--surface)', color: 'var(--ink)', border: '1px solid var(--border)', borderRadius: 8 }}
                      value={listBiz}
                      onChange={(e) => setListBiz(e.target.value as 'all' | 'warehouse' | 'rgv' | 'stacker' | 'other')}
                    >
                      <option value="all">全部业务</option>
                      <option value="warehouse">立体仓储</option>
                      <option value="rgv">RGV</option>
                      <option value="stacker">堆垛机</option>
                      <option value="other">其他</option>
                    </select>
                    <input
                      className="locate-input"
                      placeholder="按清单名搜索"
                      value={listSearch}
                      onChange={(e) => setListSearch(e.target.value)}
                    />
                    <span className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>共 {lists.length} 个清单</span>
                  </div>
                </div>

                {lists.length === 0 && <div className="state-block">暂无清单导入记录</div>}

                {lists.map((l) => (
                  <div className="import-list-card" key={l.list_name}>
                    <div
                      className="import-list-head"
                      onClick={() => setExpandedList(expandedList === l.list_name ? null : l.list_name)}
                    >
                      <div className="import-list-icon">
                        <Icon name="file" size={18} />
                      </div>
                      <div className="import-list-info">
                        <div className="import-list-name">{l.list_name}</div>
                        <div className="import-list-stats">
                          <span className="import-list-stat biz">{IMPORT_BIZ_LABEL[l.business_type] ?? '其他'}</span>
                          <span className="import-list-stat">采购单 {l.po_count}</span>
                          {l.urgent > 0 && <span className="import-list-stat urgent">紧急 {l.urgent}</span>}
                          {l.done > 0 && <span className="import-list-stat done">已到货 {l.done}</span>}
                        </div>
                      </div>
                      <Icon
                        name="chevron"
                        size={18}
                        className="import-list-arrow"
                        style={{ transform: expandedList === l.list_name ? 'rotate(90deg)' : 'none' }}
                      />
                    </div>
                    {expandedList === l.list_name && (
                      <div className="import-list-body">
                        <ProcurementTable items={l.orders} onOpenPo={setSelectedPo} title={`${l.list_name} · 采购单`} />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )
          }

          const items = view === 'pending' ? pending : view === 'transit' ? transit : view === 'normal' ? normal : all
          const title = view === 'pending' ? '紧急 · 待发起' : view === 'transit' ? '紧急 · 在途' : view === 'normal' ? '普通采购' : '全部采购单'
          return (
            <div className="module-view">
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                <button className="ghost-btn" onClick={() => setView('overview')}>
                  <Icon name="arrow" size={13} /> 返回概要
                </button>
                <span className="muted" style={{ fontSize: 12 }}>{title} · {items.length} 单</span>
              </div>
              <ProcurementTable items={items} onOpenPo={setSelectedPo} title={`${title} · 采购单`} />
            </div>
          )
        }}
      </QueryView>
      {selectedPo && <PoDrawer po={selectedPo} onClose={() => setSelectedPo(null)} />}
      {listImportOpen && (
        <ListImportDrawer
          onClose={() => setListImportOpen(false)}
          onCreated={() => queryClient.invalidateQueries({ queryKey: ['delivery-tower-procurement'] })}
        />
      )}
    </>
  )
}

/* ====================================================================
 *  PeopleView
 * ==================================================================== */
function PeopleView() {
  const rowsQ = useModuleRows('people')
  const grouped = useMemo(() => {
    const list = rowsQ.data ?? []
    const by: Record<string, SRow[]> = {}
    for (const r of list) {
      const dept = r.cells?.[1] ?? '其他'
      if (!by[dept]) by[dept] = []
      by[dept].push(r)
    }
    return by
  }, [rowsQ.data])

  const deptList = Object.entries(grouped)

  return (
    <QueryView query={rowsQ} empty={<EmptyState module={getModule('people')} />}>
      {() => (
        <div className="dept-grid">
          {deptList.map(([dept, members]) => (
            <DeptCard key={dept} dept={dept} members={members} />
          ))}
        </div>
      )}
    </QueryView>
  )
}

function DeptCard({ dept, members }: { dept: string; members: SRow[] }) {
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<SRow | null>(null)
  const start = (page - 1) * PAGE_SIZE
  const pageRows = members.slice(start, start + PAGE_SIZE)

  return (
    <div className="panel dept-card">
      <div className="panel-header">
        <span className="panel-title">
          <Icon name="users" size={16} style={{ color: 'var(--blue)' } as React.CSSProperties} />
          {dept}
        </span>
        <span className="badge-count">{members.length} 人</span>
      </div>
      <div className="dept-members">
        {pageRows.map((m) => (
          <div className="dept-member" key={m.id} onClick={() => setSelected(m)} style={{ cursor: 'pointer' }}>
            <div className="avatar">{m.name?.slice(0, 1) ?? '?'}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="name">{m.name}</div>
              <div className="job">{m.cells?.[2] && m.cells[2] !== '—' ? m.cells[2] : '—'}</div>
            </div>
            <StatusDot tone={m.tone} />
          </div>
        ))}
      </div>
      <Pagination page={page} total={members.length} pageSize={PAGE_SIZE} onChange={setPage} />
      {selected && (
        <Drawer title={selected.name} subtitle={selected.id} tone={selected.tone} status={selected.status} fields={selected.fields} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}

/* ====================================================================
 *  LogisticsView
 * ==================================================================== */
function LogisticsView() {
  const overviewQ = useQuery({
    queryKey: ['delivery-tower-logistics'],
    queryFn: () => apiFetch<LogisticsOverview>('/delivery-tower/logistics').then((r) => r.data),
    staleTime: 60_000,
  })
  const [view, setView] = useState<'overview' | 'incoming' | 'outgoing' | 'internal'>('overview')

  return (
    <QueryView query={overviewQ} empty={<EmptyState module={getModule('logistics')} />}>
      {(overview) => {
        if (view === 'overview') {
          return (
            <div className="module-view">
              <div className="panel">
                <div className="panel-header">
                  <span className="panel-title">物流分类 · 点击进入明细</span>
                </div>
                <div className="category-grid">
                  {[
                    { key: 'incoming', title: '采购收货', count: overview.incoming.length, tone: 'orange' as Tone, desc: '补货入库' },
                    { key: 'outgoing', title: '销售出货', count: overview.outgoing.length, tone: 'purple' as Tone, desc: '发往客户' },
                    { key: 'internal', title: '内部流转', count: overview.internal.length, tone: 'neutral' as Tone, desc: '厂内调拨' },
                  ].map((c) => (
                    <div className="category-card" key={c.key} onClick={() => setView(c.key as 'incoming' | 'outgoing' | 'internal')}>
                      <div className="category-card-head">
                        <StatusDot tone={c.tone} />
                        <span className="category-card-title">{c.title}</span>
                      </div>
                      <div className="category-card-count">{c.count}</div>
                      <div className="category-card-desc">{c.desc}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )
        }
        const items = view === 'incoming' ? overview.incoming : view === 'outgoing' ? overview.outgoing : overview.internal
        const title = view === 'incoming' ? '采购物流 · 补货入库' : view === 'outgoing' ? '销售物流 · 出货' : '内部流转'
        return (
          <div className="module-view">
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <button className="ghost-btn" onClick={() => setView('overview')}>
                <Icon name="arrow" size={13} /> 返回概要
              </button>
              <span className="muted" style={{ fontSize: 12 }}>{title} · {items.length} 单</span>
            </div>
            <LogisticsTable title={title} items={items} />
          </div>
        )
      }}
    </QueryView>
  )
}

/* ====================================================================
 *  DeliveryView：交付包按状态分组看板
 * ==================================================================== */
function DeliveryView() {
  const rowsQ = useModuleRows('delivery')
  const grouped = useMemo(() => {
    const list = rowsQ.data ?? []
    return {
      done: list.filter((r) => (r.status ?? '').includes('完成')),
      doing: list.filter((r) => !(r.status ?? '').includes('完成') && ((r.status ?? '').includes('进行') || (r.status ?? '').includes('进度'))),
      todo: list.filter((r) => !(r.status ?? '').includes('完成') && !(r.status ?? '').includes('进行') && !(r.status ?? '').includes('进度')),
    }
  }, [rowsQ.data])

  return (
    <QueryView query={rowsQ} empty={<EmptyState module={getModule('delivery')} />}>
      {() => (
        <div className="board-layout">
          <DeliveryCol title="待开始" items={grouped.todo} tone="neutral" />
          <DeliveryCol title="进行中" items={grouped.doing} tone="warning" />
          <DeliveryCol title="已完成" items={grouped.done} tone="success" />
        </div>
      )}
    </QueryView>
  )
}

function DeliveryCol({ title, items, tone }: { title: string; items: SRow[]; tone: 'success' | 'warning' | 'neutral' }) {
  const [selected, setSelected] = useState<SRow | null>(null)
  const [page, setPage] = useState(1)
  const start = (page - 1) * PAGE_SIZE
  const pageItems = items.slice(start, start + PAGE_SIZE)
  return (
    <div className="board-column">
      <div className="board-column-head">
        <StatusDot tone={tone} />
        {title}
        <span className="count">{items.length}</span>
      </div>
      <div className="board-cards">
        {pageItems.map((r) => (
          <div className="board-card" key={r.id} onClick={() => setSelected(r)}>
            <div className="board-card-top">
              <span className="board-card-title">{r.name}</span>
              <StatusDot tone={r.tone} />
            </div>
            <div className="board-card-meta">
              {r.id}
              {r.progress != null ? ` · ${r.progress}%` : ''}
              {r.fields?.find((f) => f[0] === '负责人')?.[1] !== '—' && r.fields?.find((f) => f[0] === '负责人')?.[1]
                ? ` · ${r.fields?.find((f) => f[0] === '负责人')?.[1]}`
                : ''}
            </div>
          </div>
        ))}
      </div>
      <Pagination page={page} total={items.length} pageSize={PAGE_SIZE} onChange={setPage} />
      {selected && (
        <Drawer
          title={selected.name}
          subtitle={selected.id}
          tone={selected.tone}
          status={selected.status}
          fields={selected.fields}
          extra={<VendorOrders partnerId={String(selected.id).replace('VEN-', '')} />}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}

/** 供应商合作订单：该供应商（res.partner）的采购单列表，点击查看详情 */
function VendorOrders({ partnerId }: { partnerId: string }) {
  const q = useQuery({
    queryKey: ['vendor-orders', partnerId],
    queryFn: () => apiFetch<Array<{
      id: number; name: string; partner: string; state: string; is_urgent: boolean
      date_planned: string | null; amount_total: number | null; origin: string | null
      line_count: number; priority: string | number
    }>>(`/modules/vendors/${partnerId}/orders`).then((r) => r.data),
    enabled: !!partnerId && /^\d+$/.test(partnerId),
    staleTime: 60_000,
  })
  const [selected, setSelected] = useState<PoItem | null>(null)
  const PO_STATE_CN: Record<string, string> = {
    draft: '询价中', sent: '已发送', purchase: '已下单', done: '已完成', cancel: '已取消',
  }
  return (
    <div className="drawer-section">
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 8 }}>合作订单 · 点击查看详情</div>
      <QueryView query={q} empty={<div className="muted" style={{ fontSize: 12 }}>暂无合作订单</div>}>
        {(orders) => (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {orders.map((o) => (
              <div
                key={o.id}
                className="chain-item"
                style={{ cursor: 'pointer' }}
                onClick={() => setSelected({
                  id: o.id, name: o.name, partner: o.partner, state: o.state,
                  priority: Number(o.priority) || 0, is_urgent: o.is_urgent,
                  overdue: false, overdue_days: 0,
                  date_planned: o.date_planned, amount_total: o.amount_total,
                  line_count: o.line_count, project: '', user: '',
                })}
              >
                <div className="chain-item-head">
                  <span className="chain-item-name">{o.name}</span>
                  {o.is_urgent && <span className="urgent-badge">紧急</span>}
                  <span className="chain-item-state" style={{ color: 'var(--muted)' }}>{PO_STATE_CN[o.state] ?? o.state}</span>
                </div>
                <div className="chain-item-meta">
                  {o.origin ? `来源 ${o.origin}` : ''}
                  {o.date_planned ? ` · 交期 ${String(o.date_planned).slice(0, 10)}` : ''}
                  {o.amount_total != null ? ` · ¥${Number(o.amount_total).toLocaleString()}` : ''}
                </div>
              </div>
            ))}
          </div>
        )}
      </QueryView>
      {selected && <PoDrawer po={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

/* ====================================================================
 *  VendorView：供应商交付（地址/电话/等级列）
 * ==================================================================== */
function VendorView() {
  const rowsQ = useModuleRows('vendors')
  return (
    <QueryView query={rowsQ} empty={<EmptyState module={getModule('vendors')} />}>
      {() => <VendorTable rows={rowsQ.data ?? []} />}
    </QueryView>
  )
}

function VendorTable({ rows }: { rows: SRow[] }) {
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState('全部')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<SRow | null>(null)

  const searchMatch = (r: SRow, q: string) => {
    if (!q) return true
    const hay = `${r.name} ${(r.cells ?? []).join(' ')} ${(r.fields ?? []).map((f) => f[1]).join(' ')}`.toLowerCase()
    return hay.includes(q.toLowerCase())
  }
  const filtered1 = search ? rows.filter((r) => searchMatch(r, search)) : rows
  const filtered = filter === '全部' ? filtered1 : filtered1.filter((r) => r.status === filter)
  const start = (page - 1) * PAGE_SIZE
  const pageRows = filtered.slice(start, start + PAGE_SIZE)

  return (
    <div className="panel">
      <div className="table-toolbar">
        <SearchInput value={search} onChange={setSearch} placeholder="搜索供应商名称/地址/电话..." />
        <FilterBar rows={rows} active={filter} onChange={(s) => { setFilter(s); setPage(1) }} />
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>编号</th>
            <th>名称</th>
            <th>地址</th>
            <th>电话</th>
            <th>等级</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {pageRows.map((r) => (
            <tr key={r.id} onClick={() => setSelected(r)}>
              <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{r.id}</td>
              <td>
                <div className="cell-name">{r.name}</div>
              </td>
              <td style={{ maxWidth: 240, fontSize: 12 }}>{r.cells?.[1] || '—'}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{r.cells?.[2] || '—'}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{r.cells?.[3] || '—'}</td>
              <td>
                <span className="drawer-status">
                  <StatusDot tone={r.tone} /> {r.status ?? '—'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Pagination page={page} total={filtered.length} pageSize={PAGE_SIZE} onChange={setPage} />
      {selected && (
        <Drawer
          title={selected.name}
          subtitle={selected.id}
          tone={selected.tone}
          status={selected.status}
          fields={selected.fields}
          extra={<VendorOrders partnerId={String(selected.id).replace('VEN-', '')} />}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}

/* ====================================================================
 *  WorkshopView：生产车间（车间信息 + 各车间生产进度）
 * ==================================================================== */
function WorkshopDetail({ row }: { row: SRow }) {
  const id = String(row.id).replace('WC-', '')
  const q = useQuery({
    queryKey: ['workshop-workorders', id],
    queryFn: () => apiFetch<SRow[]>(`/modules/workshop/${id}/workorders`).then((r) => r.data),
    enabled: !!id && id !== row.id,
    staleTime: 60_000,
  })
  return (
    <QueryView query={q} empty={<div className="subtitle" style={{ padding: '6px 2px' }}>暂无工单数据</div>}>
      {(rows) => <DrillList title="车间工单 · 生产进度" rows={rows} />}
    </QueryView>
  )
}

function WorkshopView() {
  const rowsQ = useModuleRows('workshop')
  return (
    <QueryView query={rowsQ} empty={<EmptyState module={getModule('workshop')} />}>
      {() => <GenericTableView rows={rowsQ.data ?? []} extra={(row) => <WorkshopDetail row={row} />} />}
    </QueryView>
  )
}

/* ====================================================================
 *  DefaultView：通用表格（未专门实现的模块兜底）
 * ==================================================================== */
function DefaultView() {
  const { moduleId } = useNavigation()
  const rowsQ = useModuleRows(moduleId)
  return (
    <QueryView query={rowsQ} empty={<EmptyState module={getModule(moduleId)} />}>
      {(rows) => <GenericTableView rows={rows} />}
    </QueryView>
  )
}

/* ====================================================================
 *  ModuleView 路由分发（统一由 ModuleShell 包裹头部）
 * ==================================================================== */
export function ModuleView({ projectId }: { projectId: string | null }) {
  const { moduleId } = useNavigation()

  switch (moduleId) {
    case 'overview':
      return <ModuleShell><OverviewView projectId={projectId} /></ModuleShell>
    case 'delivery':
      return <ModuleShell><DeliveryView /></ModuleShell>
    case 'field':
      return <ModuleShell><RiskControlView /></ModuleShell>
    case 'procurement':
      return <ModuleShell><ProcurementView /></ModuleShell>
    case 'people':
      return <ModuleShell><PeopleView /></ModuleShell>
    case 'logistics':
      return <ModuleShell><LogisticsView /></ModuleShell>
    case 'vendors':
      return <ModuleShell><VendorView /></ModuleShell>
    case 'design':
      return <ModuleShell><DesignView /></ModuleShell>
    case 'inventory':
      return <ModuleShell><InventoryView /></ModuleShell>
    case 'electrical':
    case 'manufacturing':
    case 'workshop':
      return <ModuleShell><WorkshopView /></ModuleShell>
    case 'sales':
    case 'deliveryTower': // 兼容旧 hash 直达（#/deliveryTower）
      return <ModuleShell><DeliveryTowerView /></ModuleShell>
    default:
      return <ModuleShell><DefaultView /></ModuleShell>
  }
}