import { DiagnosticsPage } from './pages-v2'
import ServiceControlPanel from './service-control-panel'
import EbmStrategyTestServiceCard from './ebm-strategy-test-service-card'

export default function DiagnosticsServiceControlPage() {
  return (
    <>
      <ServiceControlPanel />
      <EbmStrategyTestServiceCard />
      <DiagnosticsPage />
    </>
  )
}
