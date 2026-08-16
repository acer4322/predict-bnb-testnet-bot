import { DiagnosticsPage } from './pages-v2'
import ServiceControlPanel from './service-control-panel'
import EbmStrategyTestServiceCard from './ebm-strategy-test-service-card'
import PolyStrategyHealthCard from './poly-strategy-health-card'

export default function DiagnosticsServiceControlPage() {
  return (
    <>
      <ServiceControlPanel />
      <PolyStrategyHealthCard />
      <EbmStrategyTestServiceCard />
      <DiagnosticsPage />
    </>
  )
}
