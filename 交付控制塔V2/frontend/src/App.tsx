import { useNavigation } from './store/navigationStore'
import { AppShell } from './components/layout/AppShell'
import { PortfolioView } from './components/portfolio/PortfolioView'
import { ModuleView } from './components/modules/ModuleView'

function App() {
  const { inPortfolio, projectId } = useNavigation()

  return (
    <AppShell>
      {inPortfolio ? <PortfolioView /> : <ModuleView projectId={projectId} />}
    </AppShell>
  )
}

export default App
