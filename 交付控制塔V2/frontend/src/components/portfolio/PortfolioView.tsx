import { usePortfolioSummary, useRisks, useBlockers } from '../../api/modules/useData'
import { NAV_SECTIONS } from '../../config/nav'
import { getModule } from '../../config/modules'
import { useNavigation } from '../../store/navigationStore'
import { Icon } from '../common/Icon'
import { ProgressBar, StatusDot } from '../common/Status'
import { QueryView } from '../common/QueryView'
import { Drawer } from '../common/Drawer'
import type { Project, RiskItem } from '../../types/contract'
import { useState } from 'react'

const MARK_COLORS: Record<string, string> = {
  warning: 'var(--orange)',
  success: 'var(--green)',
  danger: 'var(--red)',
}

function ProjectCard({ project, onOpen }: { project: Project; onOpen: (id: string) => void }) {
  const [drawer, setDrawer] = useState(false)
  return (
    <>
      <div className="project-card" onClick={() => setDrawer(true)}>
        <div className="project-card-top">
          <div className="project-mark" style={{ background: MARK_COLORS[project.tone] ?? 'var(--blue)' }}>
            {project.short.slice(0, 1)}
          </div>
          <div style={{ textAlign: 'right' }}>
            <StatusDot tone={project.tone} />
            {project.status}
          </div>
        </div>
        <div className="project-card-title">{project.name}</div>
        <div className="project-card-type">{project.type}</div>
        <ProgressBar value={project.progress} />
        <div className="project-card-meta">
          <span>进度 <b>{project.progress}%</b></span>
          <span>风险 <b style={{ color: 'var(--red)' }}>{project.risks}</b></span>
          <span>阻塞 <b style={{ color: project.blockers > 0 ? 'var(--orange)' : 'var(--green)' }}>{project.blockers}</b></span>
        </div>
        <div className="project-card-foot">
          <span className="phase-text">{project.phase}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            截止 {project.due}
            <Icon name="chevron" size={14} className="chevron" />
          </span>
        </div>
      </div>
      {drawer && (
        <Drawer
          title={project.name}
          subtitle={project.short}
          tone={project.tone}
          status={project.status}
          fields={project.fields}
          onClose={() => setDrawer(false)}
          extra={
            <button
              className="primary-button"
              style={{ marginTop: 16, width: '100%', justifyContent: 'center' }}
              onClick={() => { setDrawer(false); onOpen(project.id) }}
            >
              进入项目
            </button>
          }
        />
      )}
    </>
  )
}

function RiskList({ risks }: { risks: RiskItem[] }) {
  return (
    <div className="panel portfolio-risk-panel">
      <div className="panel-header">
        <span className="panel-title">
          <Icon name="shield" size={16} style={{ color: 'var(--red)' } as React.CSSProperties} />
          任务风险与活动
        </span>
        <span className="subtitle" style={{ margin: 0 }}>{risks.length} 项</span>
      </div>
      <div className="risk-list-scroll">
        {risks.map((r, i) => (
          <div className="risk-item" key={r.id ?? i}>
            <div className={`risk-icon ${r.tone === 'danger' ? 'red' : r.tone === 'warning' ? 'orange' : r.tone === 'success' ? 'green' : 'blue'}`}>
              <Icon name={r.icon ?? 'alert'} size={15} />
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="risk-title" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {r.title ?? r.name}
              </div>
              <div className="risk-meta">
                {r.fields?.find((f) => f[0] === '所属项目')?.[1] && (
                  <span style={{ color: 'var(--blue)' }}>{r.fields?.find((f) => f[0] === '所属项目')?.[1]}</span>
                )}
                {r.fields?.find((f) => f[0] === '任务阶段')?.[1] && (
                  <span> · {r.fields?.find((f) => f[0] === '任务阶段')?.[1]}</span>
                )}
                {r.meta ? ` · ${r.meta}` : (r.status ? ` · ${r.status}` : '')}
              </div>
            </div>
            <span className="risk-tag">{r.tag ?? r.category ?? ''}</span>
          </div>
        ))}
      </div>
      {risks.length === 0 && <div className="state-block" style={{ padding: 24 }}>暂无活跃风险</div>}
    </div>
  )
}

function BlockersTable({ blockers }: { blockers: RiskItem[] }) {
  return (
    <div className="panel portfolio-blocker-panel">
      <div className="panel-header">
        <span className="panel-title">
          <Icon name="pin" size={16} style={{ color: 'var(--orange)' } as React.CSSProperties} />
          跨项目任务
        </span>
        <span className="subtitle" style={{ margin: 0 }}>{blockers.length} 项</span>
      </div>
      <div className="blocker-table-scroll">
        <table className="blocker-table">
          <thead>
            <tr>
              <th>事项</th>
              <th>状态</th>
              <th>下一步</th>
            </tr>
          </thead>
          <tbody>
            {blockers.map((b, i) => (
              <tr key={b.id ?? i}>
                <td>
                  <div className="blocker-desc">{b.title ?? b.name}</div>
                  <div className="blocker-meta">
                    {b.id}
                    {b.fields?.find((f) => f[0] === '所属项目')?.[1] && ` · ${b.fields?.find((f) => f[0] === '所属项目')?.[1]}`}
                    {b.fields?.find((f) => f[0] === '任务阶段')?.[1] && ` · ${b.fields?.find((f) => f[0] === '任务阶段')?.[1]}`}
                    {b.status ? ` · ${b.status}` : ''}
                  </div>
                </td>
                <td>
                  <span className={`blocker-status ${b.tone === 'danger' ? 'danger' : b.tone === 'warning' ? 'warning' : 'success'}`}>
                    {b.status ?? '跟进中'}
                  </span>
                </td>
                <td className="blocker-action">{b.fields?.find((f) => f[0] === '下一步动作')?.[1] ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {blockers.length === 0 && <div className="state-block" style={{ padding: 24 }}>暂无阻塞事项</div>}
    </div>
  )
}

function ModuleMap() {
  const openModule = useNavigation((s) => s.openModule)
  return (
    <div className="panel">
      <div className="panel-header">
        <span className="panel-title">
          <Icon name="grid" size={16} style={{ color: 'var(--blue)' } as React.CSSProperties} />
          项目工作台
        </span>
      </div>
      <div className="module-map">
        {NAV_SECTIONS.flatMap((s) => s.items).map((item) => {
          const cfg = getModule(item.id)
          return (
            <div key={item.id} className="module-map-card" onClick={() => openModule(item.id)}>
              <div className="module-map-icon" style={{ background: 'var(--navy)', color: '#fff' }}>
                <Icon name={item.icon} size={17} />
              </div>
              <div className="module-map-title">{item.label}</div>
              <div className="module-map-copy">{cfg.subtitle}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** 驾驶舱（Portfolio View） */
export function PortfolioView() {
  const summary = usePortfolioSummary()
  const risks = useRisks()
  const blockers = useBlockers()
  const openProject = useNavigation((s) => s.openProject)
  const projectCount = summary.data?.projects_total

  return (
    <div className="portfolio-view">
      <div className="portfolio-head">
        <div>
          <h1>项目组合驾驶舱</h1>
          <div className="subtitle">
            {projectCount != null ? `${projectCount} 个并行交付项目的组合健康度与跨项目协同` : '项目组合健康度与跨项目协同'}
          </div>
        </div>
      </div>

      <QueryView
        query={summary}
        empty={<span>暂无项目数据（等待 Odoo 凭据或部署模块）</span>}
      >
        {(s) => (
          <>
            {/* KPI */}
            <div className="kpi-grid">
              <div className="kpi-card">
                <div className="kpi-icon navy"><Icon name="grid" size={18} /></div>
                <div className="kpi-copy">
                  <div className="num">{s.projects_total}</div>
                  <div className="label">在管项目</div>
                </div>
              </div>
              <div className="kpi-card">
                <div className="kpi-icon blue"><Icon name="chart" size={18} /></div>
                <div className="kpi-copy">
                  <div className="num">{s.progress_avg}%</div>
                  <div className="label">平均进度</div>
                </div>
              </div>
              <div className="kpi-card">
                <div className="kpi-icon red"><Icon name="alert" size={18} /></div>
                <div className="kpi-copy">
                  <div className="num">{s.risks_total}</div>
                  <div className="label">活跃风险</div>
                </div>
              </div>
              <div className="kpi-card">
                <div className="kpi-icon orange"><Icon name="pin" size={18} /></div>
                <div className="kpi-copy">
                  <div className="num">{s.blockers_total}</div>
                  <div className="label">阻塞事项</div>
                </div>
              </div>
              <div className="kpi-card">
                <div className="kpi-icon green"><Icon name="check" size={18} /></div>
                <div className="kpi-copy">
                  <div className="num">{s.by_tone.green}/{s.by_tone.amber}/{s.by_tone.red}</div>
                  <div className="label">绿/黄/红</div>
                </div>
              </div>
            </div>

            {/* 项目卡 */}
            <div className="portfolio-grid">
              {s.projects.map((p) => (
                <ProjectCard key={p.id} project={p} onOpen={openProject} />
              ))}
            </div>

            {/* 风险 + 阻塞 */}
            <div className="portfolio-summary-grid">
              <RiskList risks={risks.data ?? []} />
              <BlockersTable blockers={blockers.data ?? []} />
            </div>

            {/* 模块地图 */}
            <ModuleMap />
          </>
        )}
      </QueryView>
    </div>
  )
}
