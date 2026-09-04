import { useNavigation } from './store/navigationStore'
import { AppShell } from './components/layout/AppShell'
import { UnifiedDashboardView } from './components/dashboard/UnifiedDashboardView'
import { ModuleView } from './components/modules/ModuleView'

function App() {
  const { inPortfolio, projectId } = useNavigation()

  return (
    <AppShell>
      {inPortfolio ? <UnifiedDashboardView /> : <ModuleView projectId={projectId} />}
    </AppShell>
  )
}

export default App
