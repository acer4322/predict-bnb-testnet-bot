import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Alert, Button, Drawer, Grid, Layout, Menu, Space, Tag, type MenuProps } from 'antd'
import {
  AimOutlined,
  ApiOutlined,
  BarChartOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DollarOutlined,
  ExperimentOutlined,
  MenuOutlined,
  SafetyCertificateOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import { type ServiceSnapshot, useDashboardStore } from './store'
import { useStrategyStore } from './strategy-store'
import { usePredictFunStore } from './predict-fun-store'
import { useWalletShadowStore } from './wallet-shadow-store'
import { useWalletLabHealthStore } from './wallet-lab-health-store'
import { useEchtgeldStore } from './echtgeld-store'
import CrossOracleStorageCard from './cross-oracle-storage-card'
import LiveMarketsPage from './live-markets-page'
import PinnedDivergencePage from './pinned-divergence-page'
import WalletClonePage from './wallet-clone-page-v84'
import WalletShadowPage from './wallet-shadow-page'
import EbmStrategyTestReentryPage from './ebm-strategy-test-reentry-page'
import TargetTakerEchtgeldPage from './target-taker-echtgeld-page'
import DiagnosticsServiceControlPage from './diagnostics-service-control-page'
import {
  OverviewPage,
  PolyGapPage,
  StrategiesPage,
  TradesPage,
} from './pages-v2'

const { Header, Sider, Content } = Layout

function ServiceTag({ label, service }: { label: string; service: ServiceSnapshot }) {
  return (
    <Tag color={service.ok ? 'success' : service.loading ? 'processing' : 'error'}>
      {label} · {service.ok ? `${Math.round(service.latencyMs ?? 0)}ms` : service.loading ? '連線中' : '離線'}
    </Tag>
  )
}

const menuItems: MenuProps['items'] = [
  { key: '/', icon: <DashboardOutlined />, label: '總覽' },
  { key: '/live-markets', icon: <DollarOutlined />, label: 'Live Markets' },
  { key: '/wallet-shadow', icon: <ExperimentOutlined />, label: 'Target Wallet Research' },
  { key: '/ebm-strategy-test', icon: <ExperimentOutlined />, label: 'EBM 策略測試' },
  { key: '/wallet-clone', icon: <SwapOutlined />, label: 'Wallet Maker Clone' },
  { key: '/pinned-divergence', icon: <AimOutlined />, label: 'Pinned Divergence' },
  { key: '/live', icon: <SafetyCertificateOutlined />, label: 'Echtgeld Engine' },
  { key: '/poly-gap', icon: <SwapOutlined />, label: 'Poly Gap' },
  { key: '/strategies', icon: <BarChartOutlined />, label: 'Strategies' },
  { key: '/trades', icon: <DatabaseOutlined />, label: 'Trades' },
  { key: '/diagnostics', icon: <ApiOutlined />, label: 'Diagnostics / Services' },
]

function Shell() {
  const navigate = useNavigate()
  const location = useLocation()
  const screens = Grid.useBreakpoint()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const refreshFast = useDashboardStore((state) => state.refresh)
  const services = useDashboardStore((state) => state.services)
  const refreshStrategies = useStrategyStore((state) => state.refresh)
  const strategyService = useStrategyStore((state) => state.service)
  const refreshPredictFun = usePredictFunStore((state) => state.refresh)
  const predictFunService = usePredictFunStore((state) => state.service)
  const refreshWalletShadow = useWalletShadowStore((state) => state.refresh)
  const ebmProducer = useWalletShadowStore((state) => state.producer8782)
  const refreshWalletLabHealth = useWalletLabHealthStore((state) => state.refresh)
  const walletShadowHealth = useWalletLabHealthStore((state) => state.observer8776)
  const echtgeldService = useEchtgeldStore((state) => state.service)
  const mobile = !screens.lg

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      if (!cancelled && document.visibilityState === 'visible') {
        void refreshFast()
        void refreshPredictFun()
      }
    }
    tick()
    const timer = window.setInterval(tick, 1000)
    const onVisibility = () => tick()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [refreshFast, refreshPredictFun])

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      if (!cancelled && document.visibilityState === 'visible') void refreshWalletLabHealth()
    }
    tick()
    const timer = window.setInterval(tick, 1000)
    const onVisibility = () => tick()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [refreshWalletLabHealth])

  useEffect(() => {
    const walletResearch = location.pathname === '/wallet-shadow'
    const echtgeld = location.pathname === '/live'
    if (!walletResearch && !echtgeld) return
    let cancelled = false
    const tick = () => {
      if (!cancelled && document.visibilityState === 'visible') void refreshWalletShadow()
    }
    tick()
    // Echtgeld consumes the live 8782 producer state, so refresh it at the same
    // one-second cadence as the rest of the live console. Research can stay at
    // three seconds because 8776 includes heavier historical aggregates.
    const timer = window.setInterval(tick, echtgeld ? 1000 : 3000)
    const onVisibility = () => tick()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [location.pathname, refreshWalletShadow])

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      if (!cancelled && document.visibilityState === 'visible') void refreshStrategies()
    }
    tick()
    const timer = window.setInterval(tick, 2000)
    const onVisibility = () => tick()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [refreshStrategies])

  const menu = (
    <Menu
      theme="dark"
      mode="inline"
      selectedKeys={[location.pathname]}
      items={menuItems}
      onClick={({ key }) => {
        navigate(key)
        setDrawerOpen(false)
      }}
    />
  )

  return (
    <Layout className="app-shell">
      {!mobile ? (
        <Sider width={216} className="app-sider">
          <div className="brand">
            <strong>BTC 5M Lab</strong>
            <span>Dashboard V2</span>
          </div>
          {menu}
          <div className="sider-note">PolyHermes-inspired UI · LAN read-only / localhost Echtgeld control</div>
        </Sider>
      ) : null}
      <Layout className="main-layout">
        <Header className="app-header">
          <Space>
            {mobile ? <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)} /> : null}
            <div>
              <strong>Prediction Trading Console</strong>
              <div className="header-subtitle">BTC / ETH / BNB 5M · LAN read-only · Echtgeld writes localhost-only</div>
            </div>
          </Space>
          <Space size={4} wrap>
            <ServiceTag label="8766" service={services.realtime} />
            <ServiceTag label="8767" service={services.crossOracle} />
            <ServiceTag label="8768" service={strategyService} />
            <ServiceTag label="8769" service={services.polyGap} />
            <ServiceTag label="8770" service={services.multiMarket} />
            <ServiceTag label="8771" service={predictFunService} />
            <ServiceTag label="8776 OFFICIAL" service={walletShadowHealth} />
            {location.pathname === '/live' || ebmProducer.updatedAt !== null ? <ServiceTag label="8782 EBM" service={ebmProducer} /> : null}
            {location.pathname === '/live' || echtgeldService.updatedAt !== null ? <ServiceTag label="8781" service={echtgeldService} /> : null}
          </Space>
        </Header>
        <Content className="app-content">
          <Routes>
            <Route path="/" element={<><OverviewPage /><CrossOracleStorageCard /></>} />
            <Route path="/live-markets" element={<LiveMarketsPage />} />
            <Route path="/wallet-shadow" element={<WalletShadowPage />} />
            <Route path="/ebm-strategy-test" element={<EbmStrategyTestReentryPage />} />
            <Route path="/wallet-clone" element={<WalletClonePage />} />
            <Route path="/pinned-divergence" element={<PinnedDivergencePage />} />
            <Route path="/live" element={(
              <>
                <Alert
                  type={ebmProducer.ok ? 'success' : 'error'}
                  showIcon
                  style={{ marginBottom: 12 }}
                  message={ebmProducer.ok ? 'Echtgeld 策略來源：8782 frozen EBM producer' : '8782 EBM producer 離線；不可把 8776 視為策略來源'}
                  description="TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY 的 decision 與 BASE Entry #1 handoff 由 8782 提供；8776 只提供 Official market identity / settlement truth，8781 仍獨立負責 PAUSE/ARM、durable dedupe 與實際送單。"
                />
                <TargetTakerEchtgeldPage />
              </>
            )} />
            <Route path="/poly-gap" element={<PolyGapPage />} />
            <Route path="/strategies" element={<StrategiesPage />} />
            <Route path="/trades" element={<TradesPage />} />
            <Route path="/diagnostics" element={<DiagnosticsServiceControlPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Content>
      </Layout>
      <Drawer
        title="BTC 5M Lab"
        placement="left"
        width={260}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        styles={{ body: { padding: 0, background: '#001529' } }}
      >
        {menu}
      </Drawer>
    </Layout>
  )
}

export default function AppV2() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  )
}
