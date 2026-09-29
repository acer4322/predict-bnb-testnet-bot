import { useEffect, useMemo, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Grid,
  Layout,
  Menu,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  ApiOutlined,
  BarChartOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  MenuOutlined,
  RadarChartOutlined,
  SafetyCertificateOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import type { MenuProps, TableColumnsType } from 'antd'
import { asNumber, asText, getPath, type ServiceSnapshot, useDashboardStore } from './store'
import MultiMarketOverview from './multi-market-overview'

const { Header, Sider, Content } = Layout
const { Title, Text } = Typography

function fmtPrice(value: unknown, digits = 3) {
  const n = asNumber(value)
  return n === null ? '—' : n.toFixed(digits)
}

function fmtMs(value: unknown) {
  const n = asNumber(value)
  return n === null ? '—' : `${Math.round(n)} ms`
}

function fmtMoney(value: unknown) {
  const n = asNumber(value)
  if (n === null) return '—'
  return `${n >= 0 ? '+' : '-'}$${Math.abs(n).toFixed(3)}`
}

function nestedState(input: unknown) {
  return getPath(input, 'state') ?? input
}

function statusColor(value: unknown) {
  const text = asText(value, '').toUpperCase()
  if (!text) return 'default'
  if (text.includes('ACTIVE') || text.includes('READY') || text.includes('ALLOW') || text.includes('LEADING')) return 'success'
  if (text.includes('WAIT') || text.includes('CONFIRM') || text.includes('BUILD')) return 'processing'
  if (text.includes('BLOCK') || text.includes('STALE') || text.includes('ERROR') || text.includes('DEGRADED')) return 'error'
  if (text.includes('WATCH') || text.includes('PAUSE') || text.includes('UNKNOWN')) return 'warning'
  return 'default'
}

function ServiceTag({ label, service }: { label: string; service: ServiceSnapshot }) {
  return (
    <Tag color={service.ok ? 'success' : service.loading ? 'processing' : 'error'}>
      {label} · {service.ok ? `${Math.round(service.latencyMs ?? 0)}ms` : service.loading ? '連線中' : '離線'}
    </Tag>
  )
}

function PageHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="page-heading">
      <div>
        <Title level={3}>{title}</Title>
        <Text type="secondary">{subtitle}</Text>
      </div>
      <Tag color="blue">READ ONLY</Tag>
    </div>
  )
}

function MetricCard({ title, value, suffix, detail }: { title: string; value: string; suffix?: string; detail?: string }) {
  return (
    <Card className="metric-card" size="small">
      <Statistic title={title} value={value} suffix={suffix} />
      {detail ? <Text type="secondary" className="metric-detail">{detail}</Text> : null}
    </Card>
  )
}

function RawJson({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>
}

function useModel() {
  const services = useDashboardStore((state) => state.services)
  const realtime = services.realtime.data
  const polyGap = nestedState(services.polyGap.data)
  const crossOracle = services.crossOracle.data
  const multiMarket = services.multiMarket.data
  const echtgeld = nestedState(services.echtgeld.data)

  const marketId = getPath(
    polyGap,
    'marketId',
    'market.marketId',
    'currentMarket.marketId',
  ) ?? getPath(realtime, 'marketId', 'market.market_id', 'market.marketId', 'observation.market_id', 'currentObservation.market_id')

  const secondsLeft = getPath(polyGap, 'secondsLeft', 'market.secondsLeft')
    ?? getPath(realtime, 'seconds_left', 'observation.seconds_left', 'currentObservation.seconds_left')

  const polyUp = getPath(
    crossOracle,
    'polymarket.up.mid',
    'polymarket.upMid',
    'poly.upMid',
    'upMid',
  ) ?? getPath(polyGap, 'poly.upMid', 'polymarket.upMid', 'signal.polyUpMid')

  const polyDown = getPath(
    crossOracle,
    'polymarket.down.mid',
    'polymarket.downMid',
    'poly.downMid',
    'downMid',
  ) ?? getPath(polyGap, 'poly.downMid', 'polymarket.downMid', 'signal.polyDownMid')

  const binanceUpAsk = getPath(
    polyGap,
    'binanceBook.up.ask',
    'book.up.ask',
    'upAsk',
  ) ?? getPath(realtime, 'up_ask', 'observation.up_ask', 'currentObservation.up_ask')

  const binanceDownAsk = getPath(
    polyGap,
    'binanceBook.down.ask',
    'book.down.ask',
    'downAsk',
  ) ?? getPath(realtime, 'down_ask', 'observation.down_ask', 'currentObservation.down_ask')

  const sourceAge = getPath(
    polyGap,
    'sourceFreshnessV41.current.sourceAgeMs',
    'sourceFreshnessV41.current.quoteSourceAgeMs',
    'sourceFreshnessV41.sourceAgeMs',
    'quoteTimestampRaceFixV43.sourceAgeMs',
    'polySourceAgeMs',
  )

  const receiptAge = getPath(
    polyGap,
    'sourceFreshnessV41.current.quoteReceiptAgeMs',
    'sourceFreshnessV41.current.receiptAgeMs',
    'polyReceiptAgeMs',
  )

  const direction = getPath(polyGap, 'direction', 'signal.direction', 'candidate.direction', 'selectedSide')
  const edge = getPath(polyGap, 'edge', 'signal.edge', 'currentEdge', 'entryEdge')
  const status = getPath(polyGap, 'status', 'runtimeStatus')
  const version = getPath(polyGap, 'version')
  const positionSide = getPath(polyGap, 'position.side', 'currentPosition.side', 'round.side', 'openRound.side')
  const shares = getPath(polyGap, 'position.shares', 'currentPosition.shares', 'round.shares', 'openRound.shares')
  const entry = getPath(polyGap, 'position.entryPrice', 'currentPosition.entryPrice', 'round.entry_quote_average', 'openRound.entry_quote_average')
  const pnl = getPath(polyGap, 'position.pnlUsdt', 'currentPosition.pnlUsdt', 'round.pnl_usdt', 'openRound.pnl_usdt')

  return {
    services,
    realtime,
    polyGap,
    crossOracle,
    multiMarket,
    echtgeld,
    marketId,
    secondsLeft,
    polyUp,
    polyDown,
    binanceUpAsk,
    binanceDownAsk,
    sourceAge,
    receiptAge,
    direction,
    edge,
    status,
    version,
    positionSide,
    shares,
    entry,
    pnl,
  }
}

function OverviewPage() {
  const m = useModel()
  const seconds = asNumber(m.secondsLeft)
  const sourceAge = asNumber(m.sourceAge)
  const sourceFresh = sourceAge !== null && sourceAge <= 750

  return (
    <>
      <PageHeading title="總覽" subtitle="第一屏保留 BTC Echtgeld hot path；下方獨立比較 BTC / ETH / BNB 的 Poly ↔ Binance 5m 市場軌跡。" />
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={16}>
          <Card title={<Space><RadarChartOutlined /> BTC 5M Market</Space>} extra={<Tag color="geekblue">#{asText(m.marketId)}</Tag>}>
            <Row gutter={[12, 12]}>
              <Col xs={12} md={6}><MetricCard title="剩餘" value={seconds === null ? '—' : `${Math.max(0, Math.round(seconds))}`} suffix="s" /></Col>
              <Col xs={12} md={6}><MetricCard title="方向" value={asText(m.direction)} detail="Poly signal" /></Col>
              <Col xs={12} md={6}><MetricCard title="Edge" value={fmtPrice(m.edge)} /></Col>
              <Col xs={12} md={6}><MetricCard title="Source age" value={fmtMs(m.sourceAge)} detail={sourceAge === null ? '等待 V43 telemetry' : sourceFresh ? '新鮮' : '超過 750ms'} /></Col>
            </Row>
            <div className="market-grid">
              <div className="venue-panel">
                <Text type="secondary">POLYMARKET</Text>
                <div className="quote-row"><span>UP</span><strong>{fmtPrice(m.polyUp)}</strong></div>
                <div className="quote-row"><span>DOWN</span><strong>{fmtPrice(m.polyDown)}</strong></div>
                <div className="quote-foot">Receipt age {fmtMs(m.receiptAge)}</div>
              </div>
              <div className="venue-panel">
                <Text type="secondary">BINANCE PREDICTION</Text>
                <div className="quote-row"><span>UP Ask</span><strong>{fmtPrice(m.binanceUpAsk)}</strong></div>
                <div className="quote-row"><span>DOWN Ask</span><strong>{fmtPrice(m.binanceDownAsk)}</strong></div>
                <div className="quote-foot">Execution venue</div>
              </div>
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Echtgeld Position" className="position-card">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Side"><Tag color={m.positionSide ? 'processing' : 'default'}>{asText(m.positionSide, 'FLAT')}</Tag></Descriptions.Item>
              <Descriptions.Item label="Shares">{fmtPrice(m.shares, 4)}</Descriptions.Item>
              <Descriptions.Item label="Entry">{fmtPrice(m.entry)}</Descriptions.Item>
              <Descriptions.Item label="PnL"><strong>{fmtMoney(m.pnl)}</strong></Descriptions.Item>
            </Descriptions>
          </Card>
          <Card title="Execution" className="stack-card">
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Tag color={statusColor(m.status)}>{asText(m.status, 'WAITING')}</Tag>
              <Text type="secondary">{asText(m.version, 'V44 state 尚未回傳')}</Text>
              <div>
                <Text type="secondary">Source freshness</Text>
                <Progress percent={sourceAge === null ? 0 : Math.max(0, Math.min(100, 100 - (sourceAge / 750) * 100))} showInfo={false} status={sourceAge !== null && !sourceFresh ? 'exception' : 'normal'} />
              </div>
            </Space>
          </Card>
        </Col>
      </Row>
      <MultiMarketOverview service={m.services.multiMarket} />
    </>
  )
}

function LivePage() {
  const m = useModel()
  const refresh = useDashboardStore((state) => state.refresh)
  const [controlToken, setControlToken] = useState<string | null>(null)
  const [selectedSource, setSelectedSource] = useState('CAP100_8787')
  const [busy, setBusy] = useState(false)
  const [actionMessage, setActionMessage] = useState('')

  const armed = Boolean(getPath(m.echtgeld, 'armed'))
  const runtimeStatus = asText(getPath(m.echtgeld, 'runtimeStatus'), armed ? 'LIVE ARMED' : 'PAUSED')
  const gateSource = asText(getPath(m.echtgeld, 'entrySourceGate.selectedSourceId'), '')
  const execution = getPath(m.echtgeld, 'strategyExecution', 'cap100Execution')
  const executionSource = asText(getPath(execution, 'sourceId'), gateSource || '未選擇來源')
  const executionName = asText(getPath(execution, 'displayName'), executionSource === 'R3S_R31_8790' ? 'R3-S + R3.1 V1.1.3 WTP1 Continuous Gate Fix' : executionSource === 'R2_R21_8789' ? 'R2 + R2.1 V3.5.2 Autonomous Reassess + Audit Fix' : executionSource === 'CAP100_8787' ? 'CAP100 Frozen Controller' : executionSource)
  const heartbeatAge = asNumber(getPath(execution, 'heartbeatAgeMs'))
  const activeOrdersRaw = getPath(execution, 'activeOrders')
  const activeOrders = Array.isArray(activeOrdersRaw) ? activeOrdersRaw.length : 0
  const unknownFrozen = Boolean(getPath(execution, 'entryWriteFrozen'))
  const latestExam = getPath(m.echtgeld, 'cap100PreflightStressExam.latest.report', 'cap100PreflightStressExam.latest', 'cap100PreLiveStressExam.latest.report', 'cap100PreLiveStressExam.latest')
  const examStatus = asText(getPath(latestExam, 'status'), '尚未執行')
  const examGrade = asText(getPath(latestExam, 'safetyGrade', 'grade'), '—')
  const examViolations = asNumber(getPath(latestExam, 'safety.violationCount', 'safetyViolations', 'violations'))
  const examRecommendation = asText(getPath(latestExam, 'admissionRecommendation', 'recommendation'), '—')
  const nextMarketGate = getPath(m.echtgeld, 'nextMarketArmGate')
  const waitingNextMarket = Boolean(getPath(nextMarketGate, 'waitingNextMarket'))
  const activationMarketId = getPath(nextMarketGate, 'activationMarketId')
  const liveMarketId = getPath(nextMarketGate, 'liveMarketId')

  useEffect(() => {
    let cancelled = false
    fetch('/control/session', { cache: 'no-store' })
      .then(async (res) => {
        const body = await res.json()
        if (!res.ok || !body.token) throw new Error(body.error || 'control unavailable')
        if (!cancelled) setControlToken(String(body.token))
      })
      .catch(() => { if (!cancelled) setControlToken(null) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (gateSource) setSelectedSource(gateSource)
  }, [gateSource])

  async function controlPost(path: string, payload: Record<string, unknown>) {
    if (!controlToken) throw new Error('只有 localhost 控制端可以修改 Echtgeld')
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-BTC-LAB-CONTROL': controlToken },
      body: JSON.stringify(payload),
    })
    const body = await res.json()
    if (!res.ok || body.ok === false) throw new Error(body.error || `HTTP ${res.status}`)
    return body
  }

  async function chooseSource(sourceId: string | null) {
    setBusy(true)
    setActionMessage('套用中…')
    try {
      await controlPost('/control/echtgeld/entry-source', { sourceId })
      await refresh()
      const current = asText(getPath(useDashboardStore.getState().services.echtgeld.data, 'entrySourceGate.selectedSourceId'), '')
      if ((sourceId || '') !== current) throw new Error(`8781 回讀不一致：預期 ${sourceId || 'BLOCK ALL'}，實際 ${current || 'BLOCK ALL'}`)
      setActionMessage(sourceId ? `8781 已鎖定 ${sourceId}` : '8781 已 BLOCK ALL SOURCES')
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  async function setArmed(next: boolean) {
    const label = next ? 'RESUME Echtgeld' : 'PAUSE Echtgeld'
    if (!window.confirm(`${label}？\n目前來源：${gateSource || 'BLOCK ALL'}\nActive ${executionName} orders：${activeOrders}`)) return
    setBusy(true)
    setActionMessage(`${label} 中…`)
    try {
      await controlPost(next ? '/control/echtgeld/resume' : '/control/echtgeld/pause', next ? {} : { reason: 'dashboard-v2 operator' })
      await refresh()
      const currentGate = getPath(useDashboardStore.getState().services.echtgeld.data, 'nextMarketArmGate')
      const waiting = Boolean(getPath(currentGate, 'waitingNextMarket'))
      setActionMessage(next ? (waiting ? `8781 已 ARMED，等待下一輪市場；本輪 #${asText(getPath(currentGate, 'activationMarketId'))} 禁止開單` : '8781 已 LIVE ARMED') : '8781 已 PAUSED')
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeading title="Echtgeld" subtitle="UI BUILD ENTRY-SOURCE-V1 · 8781 實單引擎控制中心；來源選擇採 exclusive fail-closed，切換後會從 8781 回讀確認。" />

      <Card
        title={<Space><SafetyCertificateOutlined /> 8781 LIVE ENTRY SOURCE CONTROL</Space>}
        extra={<Space><Tag color={armed ? 'error' : 'success'}>{runtimeStatus}</Tag><Tag color={gateSource ? 'processing' : 'error'}>{gateSource || 'BLOCK ALL SOURCES'}</Tag></Space>}
        style={{ marginBottom: 12 }}
      >
        <Alert
          type={['CAP100_8787','R2_R21_8789','R3S_R31_8790'].includes(gateSource) ? 'success' : gateSource ? 'warning' : 'error'}
          showIcon
          message={gateSource ? `目前只有 ${gateSource} 可以建立新的 Echtgeld position` : '目前所有新 Echtgeld entry 都被封鎖'}
          description={waitingNextMarket ? `已按 RESUME，但本輪市場 #${asText(activationMarketId)} 仍禁止任何新 entry；8781 只會在下一輪 marketId 出現後才正式開放。` : '來源只能在 PAUSED 時切換；選定 controller 按 RESUME 後也不會立即開單，必須等下一輪市場。排隊後在真正 venue write 前 8781 仍會再次檢查。'}
          style={{ marginBottom: 14 }}
        />
        <Row gutter={[12, 12]} align="bottom">
          <Col xs={24} lg={10}>
            <Text type="secondary">唯一 Entry Source · 8781</Text>
            <Select
              style={{ width: '100%', marginTop: 6 }}
              value={selectedSource}
              disabled={!controlToken || armed || busy}
              onChange={setSelectedSource}
              options={[
                { value: 'CAP100_8787', label: '8787 · CAP100 Frozen Controller' },
                { value: 'R2_R21_8789', label: '8789 · R2 + R2.1 V3.5.2 Autonomous Reassess + Audit Fix' },
                { value: 'R3S_R31_8790', label: '8790 · R3-S + R3.1 V1.1.3 WTP1 Continuous Gate Fix · AQ2/SA2/SE1/PA2/ER2/TC1/WT1/MBF1/CGF1' },
                { value: 'TARGET_TAKER_FORWARD', label: 'Target Taker Forward' },
                { value: 'POLY_GAP_LIVE', label: 'Poly Gap Live' },
                { value: 'POLY_PINNED_LIVE', label: 'Poly Pinned Live' },
              ]}
            />
          </Col>
          <Col xs={24} lg={14}>
            <Space wrap>
              <Button type="primary" disabled={!controlToken || armed || busy} loading={busy} onClick={() => void chooseSource(selectedSource)}>只允許此來源</Button>
              <Button danger disabled={!controlToken || armed || busy} onClick={() => void chooseSource(null)}>BLOCK ALL SOURCES</Button>
              <Button danger={armed} type={armed ? 'default' : 'primary'} disabled={!controlToken || busy || (!armed && !gateSource)} onClick={() => void setArmed(!armed)}>{armed ? 'PAUSE NEW ENTRY' : 'RESUME ECHTGELD'}</Button>
            </Space>
          </Col>
        </Row>
        <div style={{ marginTop: 10 }}><Text type={actionMessage.includes('不一致') || actionMessage.includes('Error') ? 'danger' : 'secondary'}>{controlToken ? actionMessage || 'LOCAL CONTROL READY' : 'VIEW ONLY：需從 localhost 開啟 Dashboard 才能操作'}</Text></div>
      </Card>

      <Row gutter={[12, 12]} className="section-row">
        <Col xs={24} md={6}><MetricCard title="8781 Engine" value={runtimeStatus} detail={waitingNextMarket ? `等待下一輪 · anchor #${asText(activationMarketId)}` : asText(getPath(m.echtgeld, 'version'))} /></Col>
        <Col xs={24} md={6}><MetricCard title={`${executionName} Heartbeat`} value={heartbeatAge === null ? '—' : `${Math.round(heartbeatAge)} ms`} detail={`${executionSource} · ${heartbeatAge !== null && heartbeatAge <= 3500 ? 'fresh' : 'STALE / unavailable'}`} /></Col>
        <Col xs={24} md={6}><MetricCard title={`${executionName} Active Orders`} value={String(activeOrders)} detail={unknownFrozen ? 'ENTRY FROZEN · unknown write' : 'no unresolved venue-write freeze'} /></Col>
        <Col xs={24} md={6}><MetricCard title="Pre-Live Exam" value={`${examStatus} · ${examGrade}`} detail={`${examViolations ?? '—'} violations · ${examRecommendation}`} /></Col>
      </Row>

      {waitingNextMarket ? <Alert type="info" showIcon message="ARMED · WAIT NEXT MARKET" description={`目前市場 #${asText(activationMarketId)} 不會送任何新單；下一輪首次 entry request 才會解除 gate。Live market：${asText(liveMarketId, '尚未啟用')}`} style={{ marginBottom: 12 }} /> : null}

      {unknownFrozen ? <Alert type="error" showIcon message={`${executionName} ENTRY WRITE FROZEN`} description="8781 有尚未確認的 venue-write 狀態；不得重新送單，必須先完成 reconciliation / manual review。" style={{ marginBottom: 12 }} /> : null}

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={12}><Card title={`${executionName} Execution`}><RawJson value={execution ?? { status: 'not exposed' }} /></Card></Col>
        <Col xs={24} xl={12}><Card title="Entry Source Gate"><RawJson value={getPath(m.echtgeld, 'entrySourceGate') ?? { status: 'not exposed' }} /></Card></Col>
      </Row>
    </>
  )
}

function PolyGapPage() {
  const m = useModel()
  const leader = getPath(m.polyGap, 'leaderGuardV38', 'leaderGuard')
  const freshness = getPath(m.polyGap, 'sourceFreshnessV41', 'quoteTimestampRaceFixV43')
  const reentry = getPath(m.polyGap, 'reversalReentryV40')
  const tp = getPath(m.polyGap, 'takeProfitMarketLockV42')
  return (
    <>
      <PageHeading title="Poly Gap" subtitle="把 feed freshness、re-entry、leader guard、TP lock 分開，不再全部壓成 WAITING_BINANCE_BOOK。" />
      <Row gutter={[12, 12]}>
        <Col xs={24} md={12} xl={6}><Card title="Source freshness"><RawJson value={freshness ?? { status: 'not exposed' }} /></Card></Col>
        <Col xs={24} md={12} xl={6}><Card title="Reversal re-entry"><RawJson value={reentry ?? { status: 'not exposed' }} /></Card></Col>
        <Col xs={24} md={12} xl={6}><Card title="Leader guard"><RawJson value={leader ?? { status: 'not exposed' }} /></Card></Col>
        <Col xs={24} md={12} xl={6}><Card title="TP same-market lock"><RawJson value={tp ?? { status: 'not exposed' }} /></Card></Col>
      </Row>
    </>
  )
}

type GenericRow = Record<string, unknown>

function StrategiesPage() {
  const m = useModel()
  const lifecycle = (getPath(m.realtime, 'strategyLifecycle', 'state.strategyLifecycle') ?? {}) as GenericRow
  const rows = Array.isArray(lifecycle.strategies) ? lifecycle.strategies.filter((row): row is GenericRow => !!row && typeof row === 'object') : []
  const columns: TableColumnsType<GenericRow> = [
    { title: '策略', dataIndex: 'strategy', key: 'strategy', render: (v) => <strong>{asText(v)}</strong> },
    { title: '來源', dataIndex: 'dataSource', key: 'dataSource', render: (v) => <Tag>{asText(v)}</Tag> },
    { title: 'Lifecycle', dataIndex: 'status', key: 'status', render: (v) => <Tag color={statusColor(v)}>{asText(v)}</Tag> },
    { title: '樣本', dataIndex: 'settledMarkets', key: 'settledMarkets', render: (v) => asText(v, '0') },
    { title: 'Last20', key: 'last20', render: (_, row) => fmtMoney(getPath(row, 'last20.pnlUsdt')) },
    { title: 'Last50', key: 'last50', render: (_, row) => fmtMoney(getPath(row, 'last50.pnlUsdt')) },
    { title: 'DD', key: 'dd', render: (_, row) => fmtMoney(getPath(row, 'drawdown.currentDrawdownUsdt')) },
  ]
  return (
    <>
      <PageHeading title="Strategies" subtitle="先沿用現有 Lifecycle payload，不讓每個策略 panel 各自 polling。" />
      <Card>
        {rows.length ? (
          <Table rowKey={(row) => asText(row.strategy)} dataSource={rows} columns={columns} pagination={{ pageSize: 12 }} size="middle" scroll={{ x: 760 }} />
        ) : (
          <Empty description="8766 realtime 尚未暴露 strategyLifecycle，或目前服務離線。" />
        )}
      </Card>
    </>
  )
}

function findArray(input: unknown, paths: string[]): GenericRow[] {
  for (const path of paths) {
    const value = getPath(input, path)
    if (Array.isArray(value)) return value.filter((row): row is GenericRow => !!row && typeof row === 'object')
  }
  return []
}

function TradesPage() {
  const m = useModel()
  const rows = useMemo(() => findArray(m.realtime, ['recentOrders', 'liveOrders', 'orders', 'trades', 'ledger.orders', 'live.orders']).slice(0, 100), [m.realtime])
  const columns: TableColumnsType<GenericRow> = [
    { title: 'Market', key: 'market', render: (_, row) => asText(getPath(row, 'market_id', 'marketId')) },
    { title: 'Strategy', key: 'strategy', render: (_, row) => asText(getPath(row, 'strategy')) },
    { title: 'Side', key: 'side', render: (_, row) => <Tag>{asText(getPath(row, 'side'))}</Tag> },
    { title: 'Status', key: 'status', render: (_, row) => <Tag color={statusColor(getPath(row, 'status', 'state'))}>{asText(getPath(row, 'status', 'state'))}</Tag> },
    { title: 'Entry', key: 'entry', render: (_, row) => fmtPrice(getPath(row, 'entry_price', 'entryPrice', 'quote_average_price')) },
    { title: 'PnL', key: 'pnl', render: (_, row) => fmtMoney(getPath(row, 'pnl', 'pnl_usdt', 'realized_pnl', 'settlement_pnl_usdt')) },
    { title: 'Updated', key: 'updated', render: (_, row) => asText(getPath(row, 'updated_at', 'updatedAt', 'settled_at')) },
  ]
  return (
    <>
      <PageHeading title="Trades" subtitle="從既有 8766 snapshot 找最近訂單/交易；之後再接專用 pagination endpoint。" />
      <Card>
        {rows.length ? (
          <Table rowKey={(row, index) => asText(getPath(row, 'id', 'order_id'), String(index))} dataSource={rows} columns={columns} pagination={{ pageSize: 15 }} scroll={{ x: 850 }} />
        ) : (
          <Empty description="目前 snapshot 找不到 recentOrders/liveOrders/orders/trades 陣列。" />
        )}
      </Card>
    </>
  )
}

function DiagnosticsPage() {
  const m = useModel()
  const entries: Array<[string, ServiceSnapshot, unknown]> = [
    ['8766 · realtime', m.services.realtime, m.realtime],
    ['8767 · cross-oracle', m.services.crossOracle, m.crossOracle],
    ['8769 · V44 live', m.services.polyGap, m.polyGap],
    ['8770 · multi-market', m.services.multiMarket, m.multiMarket],
    ['8781 · Echtgeld', m.services.echtgeld, m.echtgeld],
  ]
  return (
    <>
      <PageHeading title="Diagnostics" subtitle="服務健康與原始 snapshot 集中在這裡，不再污染主交易畫面。" />
      <Row gutter={[12, 12]}>
        {entries.map(([label, service]) => (
          <Col xs={24} md={12} xl={6} key={label}>
            <Card title={label}>
              <Space direction="vertical">
                <Badge status={service.ok ? 'success' : 'error'} text={service.ok ? 'ONLINE' : 'OFFLINE'} />
                <Text>HTTP {service.latencyMs === null ? '—' : `${Math.round(service.latencyMs)} ms`}</Text>
                <Text type="secondary">{service.error ?? 'No error'}</Text>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
      <Row gutter={[12, 12]} className="section-row">
        {entries.map(([label, , data]) => (
          <Col xs={24} xl={12} key={`${label}-json`}>
            <Card title={`${label} raw`}><RawJson value={data ?? { status: 'no snapshot' }} /></Card>
          </Col>
        ))}
      </Row>
    </>
  )
}

const menuItems: MenuProps['items'] = [
  { key: '/', icon: <DashboardOutlined />, label: '總覽' },
  { key: '/live', icon: <SafetyCertificateOutlined />, label: 'Echtgeld' },
  { key: '/poly-gap', icon: <SwapOutlined />, label: 'Poly Gap' },
  { key: '/strategies', icon: <BarChartOutlined />, label: 'Strategies' },
  { key: '/trades', icon: <DatabaseOutlined />, label: 'Trades' },
  { key: '/diagnostics', icon: <ApiOutlined />, label: 'Diagnostics' },
]

function Shell() {
  const navigate = useNavigate()
  const location = useLocation()
  const screens = Grid.useBreakpoint()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const refresh = useDashboardStore((state) => state.refresh)
  const services = useDashboardStore((state) => state.services)
  const mobile = !screens.lg

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      if (!cancelled && document.visibilityState === 'visible') void refresh()
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
  }, [refresh])

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
          <div className="sider-note">PolyHermes-inspired UI · read-only migration</div>
        </Sider>
      ) : null}
      <Layout className="main-layout">
        <Header className="app-header">
          <Space>
            {mobile ? <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)} /> : null}
            <div>
              <strong>Prediction Trading Console</strong>
              <div className="header-subtitle">舊 Dashboard 與 Echtgeld hot path 保持不變</div>
            </div>
          </Space>
          <Space size={4} wrap>
            <ServiceTag label="8766" service={services.realtime} />
            <ServiceTag label="8767" service={services.crossOracle} />
            <ServiceTag label="8769" service={services.polyGap} />
            <ServiceTag label="8770" service={services.multiMarket} />
            <ServiceTag label="8781" service={services.echtgeld} />
          </Space>
        </Header>
        <Content className="app-content">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/live" element={<LivePage />} />
            <Route path="/poly-gap" element={<PolyGapPage />} />
            <Route path="/strategies" element={<StrategiesPage />} />
            <Route path="/trades" element={<TradesPage />} />
            <Route path="/diagnostics" element={<DiagnosticsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Content>
      </Layout>
      <Drawer title="BTC 5M Lab" placement="left" width={260} open={drawerOpen} onClose={() => setDrawerOpen(false)} styles={{ body: { padding: 0, background: '#001529' } }}>
        {menu}
      </Drawer>
    </Layout>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  )
}
