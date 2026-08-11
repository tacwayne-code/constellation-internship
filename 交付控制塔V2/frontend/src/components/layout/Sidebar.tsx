import { NAV_SECTIONS } from '../../config/nav'
import { useNavigation } from '../../store/navigationStore'
import { Icon } from '../common/Icon'
import { usePortfolioSummary } from '../../api/modules/useData'

/** 左侧导航栏 */
export function Sidebar() {
  const { inPortfolio, moduleId, goPortfolio, openModule } = useNavigation()
  const summary = usePortfolioSummary()
  const projectCount = summary.data?.projects_total ?? '-'

  return (
    <aside className="sidebar">
      <div className="brand" onClick={goPortfolio} style={{ cursor: 'pointer' }}>
        <div className="brand-mark">塔</div>
        <div>
          <div className="brand-name">交付控制塔</div>
          <div className="brand-sub">{projectCount} 个项目</div>
        </div>
      </div>

      <nav className="sidebar-nav">
        <div className="nav-section">
          <button
            className={`nav-item nav-portfolio ${inPortfolio ? 'active' : ''}`}
            onClick={goPortfolio}
          >
            <Icon name="grid" size={16} />
            <span className="nav-label">项目组合</span>
          </button>
        </div>

        {NAV_SECTIONS.map((section) => (
          <div className="nav-section" key={section.id}>
            <div className="nav-heading">{section.label}</div>
            {section.items.map((item) => {
              const active = !inPortfolio && moduleId === item.id
              return (
                <button
                  key={item.id}
                  className={`nav-item ${active ? 'active' : ''}`}
                  onClick={() => openModule(item.id)}
                >
                  <span className="nav-marker" />
                  <Icon name={item.icon} size={16} />
                  <span className="nav-label">{item.label}</span>
                </button>
              )
            })}
          </div>
        ))}
      </nav>
    </aside>
  )
}