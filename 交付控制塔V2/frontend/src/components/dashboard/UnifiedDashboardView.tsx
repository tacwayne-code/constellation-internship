import { useEffect, useMemo, useState } from 'react'
import {
  BadgeDollarSign,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  ClipboardList,
  Factory,
  Gauge,
  Maximize2,
  Minimize2,
  PackageOpen,
  RefreshCw,
  RotateCcw,
  Save,
  Settings2,
  ShoppingCart,
  Truck,
  X,
} from 'lucide-react'
import { toast } from '../../store/uiStore'
import { useDashboard } from '../../store/dashboardStore'
import { useFullscreen } from '../../hooks/useFullscreen'
import {
  DASHBOARD_DATA,
  ROLE_LABELS,
  WIDGET_LABELS,
  type ExceptionRow,
  type Metric,
  type ProgressRow,
  type RiskSlice,
  type RoleId,
  type Tone,
  type TrendPoint,
  type WidgetId,
} from './dashboardData'

const ROLE_IDS = Object.keys(ROLE_LABELS) as RoleId[]

const PROJECTS = [
  { value: 'all', label: '全部项目' },
  { value: '华东区域项目群', label: '华东区域项目群' },
  { value: '华南项目 A', label: '华南项目 A' },
  { value: '西南项目 B', label: '西南项目 B' },
  { value: '华北项目 C', label: '华北项目 C' },
  { value: '海外项目 D', label: '海外项目 D' },
]

const WAREHOUSES = [
  { value: 'all', label: '全部仓库' },
  { value: 'main', label: '总部仓' },
  { value: 'east', label: '华东项目仓' },
  { value: 'south', label: '华南项目仓' },
]

const PERIODS = [
  { value: 'week', label: '近 7 天' },
  { value: 'month', label: '本月' },
  { value: 'quarter', label: '本季度' },
]

const METRIC_ICONS = {
  progress: Gauge,
  delivery: Truck,
  inventory: PackageOpen,
  alert: CircleAlert,
  money: BadgeDollarSign,
  purchase: ShoppingCart,
  factory: Factory,
}

function MetricCard({ metric }: { metric: Metric }) {
  const MetricIcon = METRIC_ICONS[metric.icon]
  const deltaClass = metric.direction === 'down' ? 'negative' : metric.direction === 'up' ? 'positive' : 'neutral'

  return (
    <article className="dash-metric">
      <div className={`dash-metric-icon ${metric.tone}`}><MetricIcon size={21} strokeWidth={1.9} /></div>
      <div className="dash-metric-copy">
        <div className="dash-metric-label">{metric.label}</div>
        <div className="dash-metric-value">{metric.value}<span>{metric.unit}</span></div>
        <div className={`dash-metric-delta ${deltaClass}`}>{metric.delta}</div>
      </div>
    </article>
  )
}

function WidgetFrame({ title, count, className = '', children }: {
  title: string
  count?: number
  className?: string
  children: React.ReactNode
}) {
  return (
    <section className={`dash-panel ${className}`}>
      <div className="dash-panel-head">
        <h2>{title}</h2>
        {count != null ? <span className="dash-panel-count">{count}</span> : null}
      </div>
      {children}
    </section>
  )
}

function makePath(points: TrendPoint[], field: 'primary' | 'secondary' | 'target') {
  if (points.length === 0) return ''
  const left = 42
  const width = 574
  const bottom = 194
  const height = 156
  return points.map((point, index) => {
    const x = left + (index * width) / Math.max(1, points.length - 1)
    const y = bottom - (point[field] / 100) * height
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
  }).join(' ')
}

function TrendChart({ data, legend }: { data: TrendPoint[]; legend: [string, string, string] }) {
  const paths = useMemo(() => ({
    primary: makePath(data, 'primary'),
    secondary: makePath(data, 'secondary'),
    target: makePath(data, 'target'),
  }), [data])

  return (
    <div className="trend-chart">
      <div className="chart-legend" aria-label="图例">
        <span><i className="legend-blue" />{legend[0]}</span>
        <span><i className="legend-orange" />{legend[1]}</span>
        <span><i className="legend-green dashed" />{legend[2]}</span>
      </div>
      <svg viewBox="0 0 640 226" role="img" aria-label={`${legend[0]}、${legend[1]}和${legend[2]}趋势图`}>
        {[0, 25, 50, 75, 100].map((tick) => {
          const y = 194 - (tick / 100) * 156
          return (
            <g key={tick}>
              <line x1="42" x2="616" y1={y} y2={y} className="chart-gridline" />
              <text x="32" y={y + 4} textAnchor="end" className="chart-axis-label">{tick}%</text>
            </g>
          )
        })}
        {data.map((point, index) => {
          const x = 42 + (index * 574) / Math.max(1, data.length - 1)
          return <text key={point.label} x={x} y="216" textAnchor="middle" className="chart-axis-label">{point.label}</text>
        })}
        <path d={paths.target} className="chart-line target" />
        <path d={paths.primary} className="chart-line primary" />
        <path d={paths.secondary} className="chart-line secondary" />
        {data.map((point, index) => {
          const x = 42 + (index * 574) / Math.max(1, data.length - 1)
          const y = 194 - (point.secondary / 100) * 156
          return <circle key={point.label} cx={x} cy={y} r="3.2" className="chart-point" />
        })}
      </svg>
    </div>
  )
}

function donutGradient(slices: RiskSlice[]) {
  const total = slices.reduce((sum, slice) => sum + slice.value, 0)
  let current = 0
  const stops = slices.map((slice) => {
    const start = current
    current += (slice.value / total) * 100
    return `${slice.color} ${start.toFixed(1)}% ${current.toFixed(1)}%`
  })
  return `conic-gradient(${stops.join(', ')})`
}

function RiskChart({ slices }: { slices: RiskSlice[] }) {
  const total = slices.reduce((sum, slice) => sum + slice.value, 0)
  return (
    <div className="risk-chart">
      <div className="risk-donut" style={{ background: donutGradient(slices) }}>
        <div className="risk-donut-center"><span>总计</span><strong>{total}</strong></div>
      </div>
      <div className="risk-legend">
        {slices.map((slice) => (
          <div className="risk-legend-row" key={slice.label}>
            <i style={{ background: slice.color }} />
            <span>{slice.label}</span>
            <strong>{slice.value}</strong>
            <em>{Math.round((slice.value / total) * 100)}%</em>
          </div>
        ))}
      </div>
    </div>
  )
}

function StatusTag({ status }: { status: ExceptionRow['status'] }) {
  const tone = status === '处理中' ? 'orange' : status === '待验证' ? 'blue' : 'red'
  return <span className={`dash-status ${tone}`}>{status}</span>
}

function ExceptionTable({ rows }: { rows: ExceptionRow[] }) {
  return (
    <div className="dash-table-wrap">
      <table className="dash-table">
        <thead>
          <tr><th>异常事项</th><th>项目</th><th>责任部门</th><th>级别</th><th>状态</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td><strong>{row.type} · {row.object}</strong><span>{row.id} · {row.time}</span></td>
              <td>{row.project}</td>
              <td>{row.owner}</td>
              <td><span className={`dash-level ${row.level === '严重' ? 'red' : row.level === '高' ? 'orange' : 'yellow'}`}>{row.level}</span></td>
              <td><StatusTag status={row.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 ? <div className="dash-empty">当前筛选范围内暂无异常</div> : null}
    </div>
  )
}

function ProgressCell({ value, tone }: { value: number; tone: Tone }) {
  return (
    <div className="progress-cell">
      <div className="progress-track"><span className={tone} style={{ width: `${value}%` }} /></div>
      <span>{value}%</span>
    </div>
  )
}

function ProgressTable({ rows, columns }: { rows: ProgressRow[]; columns: [string, string, string] }) {
  return (
    <div className="dash-table-wrap progress-table-wrap">
      <table className="dash-table progress-table">
        <thead><tr><th>名称</th><th>总进度</th><th>{columns[0]}</th><th>{columns[1]}</th><th>{columns[2]}</th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name}>
              <td><strong>{row.name}</strong></td>
              <td>{row.overall}%</td>
              <td><ProgressCell value={row.first} tone="blue" /></td>
              <td><ProgressCell value={row.second} tone="green" /></td>
              <td><ProgressCell value={row.third} tone="orange" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FocusList({ items }: { items: { time: string; title: string; meta: string; tone: Tone }[] }) {
  return (
    <div className="focus-list">
      {items.map((item) => (
        <div className="focus-row" key={`${item.time}-${item.title}`}>
          <span className="focus-time">{item.time}</span>
          <i className={`focus-dot ${item.tone}`} />
          <div><strong>{item.title}</strong><span>{item.meta}</span></div>
        </div>
      ))}
    </div>
  )
}

function CustomizationDrawer() {
  const role = useDashboard((state) => state.role)
  const layout = useDashboard((state) => state.layouts[state.role])
  const density = useDashboard((state) => state.density)
  const setDensity = useDashboard((state) => state.setDensity)
  const setEditing = useDashboard((state) => state.setEditing)
  const toggleWidget = useDashboard((state) => state.toggleWidget)
  const moveWidget = useDashboard((state) => state.moveWidget)
  const resetRole = useDashboard((state) => state.resetRole)

  const save = () => {
    setEditing(false)
    toast(`${ROLE_LABELS[role]}视图已保存到本机`, 'success')
  }

  const reset = () => {
    resetRole()
    toast(`已恢复${ROLE_LABELS[role]}部门默认布局`, 'info')
  }

  return (
    <aside className="customize-drawer" aria-label="编辑布局">
      <div className="customize-head">
        <div><h2>编辑布局</h2><p>正在调整：{ROLE_LABELS[role]}视图</p></div>
        <button className="dash-icon-button" onClick={() => setEditing(false)} aria-label="关闭编辑布局"><X size={18} /></button>
      </div>

      <div className="customize-section">
        <h3>显示与顺序</h3>
        <p>选择需要关注的模块，并调整它们的展示顺序。</p>
        <div className="widget-editor-list">
          {layout.order.map((widget, index) => {
            const visible = !layout.hidden.includes(widget)
            return (
              <div className={`widget-editor-row ${visible ? '' : 'disabled'}`} key={widget}>
                <button className={`switch ${visible ? 'on' : ''}`} onClick={() => toggleWidget(widget)} aria-label={`${visible ? '隐藏' : '显示'}${WIDGET_LABELS[widget]}`}><span /></button>
                <span className="widget-editor-name">{WIDGET_LABELS[widget]}</span>
                <div className="widget-move-actions">
                  <button onClick={() => moveWidget(widget, -1)} disabled={index === 0} aria-label="上移"><ChevronUp size={15} /></button>
                  <button onClick={() => moveWidget(widget, 1)} disabled={index === layout.order.length - 1} aria-label="下移"><ChevronDown size={15} /></button>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <div className="customize-section">
        <h3>布局密度</h3>
        <div className="density-control">
          <button className={density === 'compact' ? 'active' : ''} onClick={() => setDensity('compact')}>紧凑</button>
          <button className={density === 'comfortable' ? 'active' : ''} onClick={() => setDensity('comfortable')}>舒适</button>
        </div>
      </div>

      <div className="customize-note">
        <CheckCircle2 size={17} />
        <span>只改变你的展示方式，不会修改指标口径和数据权限。</span>
      </div>

      <div className="customize-actions">
        <button className="dash-secondary-button" onClick={reset}><RotateCcw size={16} />恢复部门默认</button>
        <button className="dash-primary-button" onClick={save}><Save size={16} />保存我的视图</button>
      </div>
    </aside>
  )
}

function DashboardWidget({ id, data, exceptions, progress }: {
  id: WidgetId
  data: (typeof DASHBOARD_DATA)[RoleId]
  exceptions: ExceptionRow[]
  progress: ProgressRow[]
}) {
  if (id === 'kpis') {
    return <div className="dash-kpi-grid widget-span-full">{data.metrics.map((metric) => <MetricCard key={metric.label} metric={metric} />)}</div>
  }
  if (id === 'trend') {
    return <WidgetFrame title={data.trendTitle} className="widget-span-7"><TrendChart data={data.trend} legend={data.trendLegend} /></WidgetFrame>
  }
  if (id === 'risks') {
    return <WidgetFrame title="风险分布" className="widget-span-5"><RiskChart slices={data.risks} /></WidgetFrame>
  }
  if (id === 'exceptions') {
    return <WidgetFrame title="运营异常" count={exceptions.length} className="widget-span-7"><ExceptionTable rows={exceptions} /></WidgetFrame>
  }
  if (id === 'progress') {
    return <WidgetFrame title={data.progressTitle} className="widget-span-5"><ProgressTable rows={progress} columns={data.progressColumns} /></WidgetFrame>
  }
  return <WidgetFrame title="今日重点" count={data.focus.length} className="widget-span-5"><FocusList items={data.focus} /></WidgetFrame>
}

export function UnifiedDashboardView() {
  const role = useDashboard((state) => state.role)
  const project = useDashboard((state) => state.project)
  const warehouse = useDashboard((state) => state.warehouse)
  const period = useDashboard((state) => state.period)
  const density = useDashboard((state) => state.density)
  const isEditing = useDashboard((state) => state.isEditing)
  const layout = useDashboard((state) => state.layouts[state.role])
  const setRole = useDashboard((state) => state.setRole)
  const setProject = useDashboard((state) => state.setProject)
  const setWarehouse = useDashboard((state) => state.setWarehouse)
  const setPeriod = useDashboard((state) => state.setPeriod)
  const setEditing = useDashboard((state) => state.setEditing)
  const [lastUpdated, setLastUpdated] = useState(() => new Date())
  const { isFullscreen, toggleFullscreen } = useFullscreen()
  const data = DASHBOARD_DATA[role]
  const visibleWidgets = layout.order.filter((widget) => !layout.hidden.includes(widget))

  const periodData = useMemo(() => {
    if (period === 'week') return { ...data, trend: data.trend.slice(-5) }
    if (period === 'quarter') {
      return {
        ...data,
        trend: data.trend.map((point, index) => ({
          ...point,
          label: `第 ${index + 1} 月`,
          primary: Math.min(100, point.primary + 2),
          secondary: Math.min(100, point.secondary + 1),
        })),
      }
    }
    return data
  }, [data, period])

  const filteredExceptions = useMemo(() => {
    if (project === 'all') return data.exceptions
    return data.exceptions.filter((row) => row.project === project)
  }, [data.exceptions, project])

  const filteredProgress = useMemo(() => {
    if (project === 'all') return data.progress
    const matching = data.progress.filter((row) => row.name === project)
    return matching.length > 0 ? matching : data.progress
  }, [data.progress, project])

  const refresh = () => {
    setLastUpdated(new Date())
    toast('演示数据已刷新；连接 Odoo 后这里会触发实时同步', 'success')
  }

  const scopeLabel = PROJECTS.find((item) => item.value === project)?.label ?? '全部项目'

  useEffect(() => {
    const isWorkshopFullscreen = role === 'production' && isFullscreen
    document.body.dataset.workshopFullscreen = String(isWorkshopFullscreen)
    return () => { delete document.body.dataset.workshopFullscreen }
  }, [isFullscreen, role])

  const changeRole = (nextRole: RoleId) => {
    setRole(nextRole)
    if (isFullscreen && nextRole !== 'production') void document.exitFullscreen()
  }

  const toggleWorkshopFullscreen = async () => {
    try {
      setEditing(false)
      await toggleFullscreen()
    } catch {
      toast('当前浏览器不支持全屏模式，请使用浏览器的全屏功能', 'warning')
    }
  }

  return (
    <div className={`unified-dashboard density-${density} role-${role} ${isEditing ? 'is-editing' : ''} ${isFullscreen ? 'is-fullscreen' : ''}`}>
      <div className="dashboard-intro">
        <div>
          <h1>统一运营看板</h1>
          <p>{data.subtitle}</p>
        </div>
        <div className="dashboard-source">
          <span className="source-dot" />
          演示数据 · 待连接 Odoo · 更新于 {lastUpdated.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
        </div>
      </div>

      <div className="dashboard-toolbar">
        <div className="role-selector" aria-label="岗位模板">
          <span className="toolbar-label">岗位模板</span>
          <div className="role-tabs">
            {ROLE_IDS.map((id) => <button key={id} className={role === id ? 'active' : ''} onClick={() => changeRole(id)}>{ROLE_LABELS[id]}</button>)}
          </div>
        </div>
        <label className="filter-select">
          <span>项目</span>
          <select value={project} onChange={(event) => setProject(event.target.value)}>{PROJECTS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
        </label>
        <label className="filter-select">
          <span>仓库</span>
          <select value={warehouse} onChange={(event) => setWarehouse(event.target.value)}>{WAREHOUSES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
        </label>
        <label className="filter-select period-select">
          <CalendarDays size={15} />
          <select value={period} onChange={(event) => setPeriod(event.target.value)}>{PERIODS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
        </label>
        <div className="toolbar-actions">
          {role === 'production' ? (
            <button className={`dash-secondary-button fullscreen-button ${isFullscreen ? 'active' : ''}`} onClick={() => void toggleWorkshopFullscreen()}>
              {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              {isFullscreen ? '退出全屏' : '车间大屏'}
            </button>
          ) : null}
          <button className="dash-secondary-button edit-button" onClick={() => setEditing(true)}><Settings2 size={16} />编辑布局</button>
          <button className="dash-secondary-button" onClick={refresh}><RefreshCw size={16} />刷新数据</button>
        </div>
      </div>

      <div className="dashboard-scope-line">
        <ClipboardList size={14} />当前范围：{scopeLabel} · {WAREHOUSES.find((item) => item.value === warehouse)?.label} · {PERIODS.find((item) => item.value === period)?.label}
      </div>

      <div className="dashboard-widget-grid">
        {visibleWidgets.map((widget) => <DashboardWidget key={widget} id={widget} data={periodData} exceptions={filteredExceptions} progress={filteredProgress} />)}
      </div>

      {isEditing ? <CustomizationDrawer /> : null}
    </div>
  )
}
