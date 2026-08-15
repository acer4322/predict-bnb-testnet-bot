import { useEffect, useMemo } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  InputNumber,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  DollarOutlined,
  LineChartOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  WalletOutlined,
} from '@ant-design/icons'
import { useEchtgeldStore } from './echtgeld-store'
import { usePredictFunStore } from './predict-fun-store'
import { useWalletLabHealthStore } from './wallet-lab-health-store'
import { useWalletShadowStore } from './wallet-shadow-store'
import './target-taker-echtgeld.css'

const { Title, Text } = Typography
const SIDE_ONLY = 'TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY'

type RowObject = Record<string, unknown>

const row = (value: unknown): RowObject => value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
const rows = (value: unknown): RowObject[] => Array.isArray(value)
  ? value.filter((item): item is RowObject => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
  : []
const num = (value: unknown): number | null => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}
const text = (value: unknown, fallback = '—'): string => value === null || value === undefined || value === '' ? fallback : String(value)
const price = (value: unknown): string => num(value) === null ? '—' : Number(value).toFixed(3)
const pct = (value: unknown, digits = 1): string => num(value) === null ? '—' : `${(Number(value) * 100).toFixed(digits)}%`
const money = (value: unknown, digits = 2, sign = true): string => {
  const parsed = num(value)
  return parsed === null ? '—' : `${sign && parsed >= 0 ? '+' : ''}$${parsed.toFixed(digits)}`
}
const when = (value: unknown): string => {
  const parsed = num(value)
  return parsed && parsed > 0 ? new Date(parsed).toLocaleString('zh-TW', { hour12: false }) : '—'
}
const age = (value: unknown): string => {
  const parsed = num(value)
  if (!parsed || parsed <= 0) return '—'
  const delta = Math.max(0, Date.now() - parsed)
  if (delta < 1000) return `${Math.round(delta)}ms 前`
  if (delta < 60_000) return `${(delta / 1000).toFixed(1)}s 前`
  return `${(delta / 60_000).toFixed(1)}m 前`
}

function reasonLabel(reason: unknown): string {
  const key = text(reason, 'WAITING')
  return ({
    PUBLIC_SIDE_EBM_MATCH: '條件成立 · 已產生進場訊號',
    PUBLIC_SIDE_EBM_TOO_WEAK: '等待 EBM 信心 ≥ 60%',
    SIDE_EBM_MODEL_UNAVAILABLE: 'EBM 模型不可用',
    MARKET_MISMATCH: '等待正確市場同步',
    STALE_PUBLIC_SNAPSHOT: '公開市場快照過舊',
    STALE_PREDICT_BOOK: 'Predict.fun 訂單簿過舊',
    TOO_LATE: '本局剩餘時間不足，不再進場',
    ASK_UNEXECUTABLE: '選定方向 Ask 不可成交 / 超過上限',
  } as Record<string, string>)[key] || key
}

function orderColor(value: unknown): string {
  const status = text(value).toUpperCase()
  if (status === 'SUBMITTED') return 'success'
  if (status === 'AMBIGUOUS') return 'error'
  if (status.includes('REJECT') || status.includes('ABORT') || status.includes('IGNORE')) return 'warning'
  if (['ATTEMPTING', 'QUEUED', 'PROCESSING'].includes(status)) return 'processing'
  return 'default'
}

function resultColor(value: unknown): string {
  const status = text(value).toUpperCase()
  if (status === 'WIN') return 'success'
  if (status === 'LOSS') return 'error'
  if (status === 'PENDING') return 'processing'
  return 'default'
}

function levelColor(value: unknown): string {
  const level = text(value).toUpperCase()
  if (level === 'ERROR') return 'error'
  if (level === 'WARN' || level === 'WARNING') return 'warning'
  if (level === 'INFO') return 'processing'
  return 'default'
}

function findActiveOrder(engine: RowObject): RowObject {
  return rows(engine.recentOrders).find((item) => {
    const status = text(item.status).toUpperCase()
    const result = text(item.resultStatus).toUpperCase()
    return ['ATTEMPTING', 'QUEUED', 'PROCESSING'].includes(status) || (status === 'SUBMITTED' && result === 'PENDING')
  }) || {}
}

function MarketTrajectory({ asset, activeOrder }: { asset: RowObject; activeOrder: RowObject }) {
  const trajectory = useMemo(() => rows(asset.trajectory).slice(-300), [asset.trajectory])
  const market = row(asset.market)
  const up = row(asset.up)
  const down = row(asset.down)
  const currentMarketId = num(market.id ?? market.marketId)
  const orderMarketId = num(activeOrder.market_id ?? activeOrder.marketId)
  const plot = trajectory.map((point, index) => ({
    x: trajectory.length <= 1 ? 0 : index / (trajectory.length - 1) * 1000,
    up: num(point.up),
    down: num(point.down),
    at: num(point.sampledAtMs),
  }))
  const y = (value: number) => 170 - Math.max(0, Math.min(1, value)) * 160
  const upPoints = plot.filter((p) => p.up !== null).map((p) => `${p.x.toFixed(1)},${y(p.up as number).toFixed(1)}`).join(' ')
  const downPoints = plot.filter((p) => p.down !== null).map((p) => `${p.x.toFixed(1)},${y(p.down as number).toFixed(1)}`).join(' ')

  const attemptedAt = num(activeOrder.attempted_at_ms ?? activeOrder.attemptedAtMs)
  let entryX: number | null = null
  if (attemptedAt && currentMarketId && currentMarketId === orderMarketId && plot.length > 1) {
    const first = plot[0].at
    const last = plot[plot.length - 1].at
    if (first && last && last > first && attemptedAt >= first && attemptedAt <= last) {
      entryX = (attemptedAt - first) / (last - first) * 1000
    }
  }

  return (
    <Card
      className="trajectory-card"
      title={<Space><LineChartOutlined /> BTC 5M 市場軌跡</Space>}
      extra={<Space size={6} wrap><Tag color={text(asset.status) === 'LIVE' ? 'success' : 'warning'}>{text(asset.status)}</Tag><Tag>#{text(market.id ?? market.marketId)}</Tag></Space>}
    >
      <div className="trajectory-status-row">
        <Text strong>UP</Text><Text>{price(up.bid)} / {price(up.ask)}</Text>
        <Text strong>DOWN</Text><Text>{price(down.bid)} / {price(down.ask)}</Text>
        <Text type="secondary">剩餘 {num(asset.secondsLeft)?.toFixed(1) ?? '—'}s</Text>
        <Text type="secondary">receipt {num(asset.receiptAgeMs)?.toFixed(0) ?? '—'}ms</Text>
      </div>
      {plot.length > 1 ? (
        <>
          <div className="trajectory-chart-wrap echtgeld-trajectory-wrap">
            <span className="trajectory-axis-label trajectory-axis-top">1.00</span>
            <span className="trajectory-axis-label trajectory-axis-mid">0.50</span>
            <span className="trajectory-axis-label trajectory-axis-bottom">0.00</span>
            <svg className="trajectory-chart" viewBox="0 0 1000 180" preserveAspectRatio="none" role="img" aria-label="Predict.fun BTC 5 minute UP and DOWN mid price trajectory">
              <line className="trajectory-gridline" x1="0" x2="1000" y1="10" y2="10" />
              <line className="trajectory-midline" x1="0" x2="1000" y1="90" y2="90" />
              <line className="trajectory-gridline" x1="0" x2="1000" y1="170" y2="170" />
              {upPoints ? <polyline className="trajectory-poly-line" fill="none" points={upPoints} /> : null}
              {downPoints ? <polyline className="echtgeld-down-line" fill="none" points={downPoints} /> : null}
              {entryX !== null ? <line className="echtgeld-entry-line" x1={entryX} x2={entryX} y1="8" y2="172" /> : null}
            </svg>
          </div>
          <div className="trajectory-legend">
            <span><i className="legend-line poly" /> UP mid</span>
            <span><i className="legend-line echtgeld-down-legend" /> DOWN mid</span>
            {entryX !== null ? <span><i className="legend-line echtgeld-entry-legend" /> Echtgeld entry</span> : null}
            <span className="trajectory-samples">{plot.length} samples</span>
          </div>
        </>
      ) : <div className="trajectory-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待 8771 累積市場軌跡" /></div>}
      <div className="trajectory-meta">
        <Tag>{text(market.title, 'BTC Predict.fun 5M')}</Tag>
        <Text type="secondary">資料源：8771 WebSocket observer</Text>
        <Text type="secondary">不依賴 8776 大型 research ledger；沒有持倉時也會持續跑。</Text>
      </div>
    </Card>
  )
}

function PositionMonitor({ engine, asset }: { engine: RowObject; asset: RowObject }) {
  const active = findActiveOrder(engine)
  const market = row(asset.market)
  const currentMarketId = num(market.id ?? market.marketId)
  const activeMarketId = num(active.market_id ?? active.marketId)
  const side = text(active.side).toUpperCase()
  const sameMarket = Boolean(currentMarketId && currentMarketId === activeMarketId)
  const sideBook = side === 'UP' ? row(asset.up) : side === 'DOWN' ? row(asset.down) : {}
  const markBid = sameMarket ? num(sideBook.bid) : null
  const shares = num(active.shares)
  const cost = num(active.submitted_usdt ?? active.submittedUsdt)
  const markPnl = markBid !== null && shares !== null && cost !== null ? markBid * shares - cost : null
  const hasActive = Object.keys(active).length > 0

  return (
    <Card className="position-card echtgeld-position-card" title={<Space><ThunderboltOutlined /> 實單持倉監控</Space>} extra={<Tag color={hasActive ? 'processing' : 'default'}>{hasActive ? 'OPEN / PENDING' : 'FLAT'}</Tag>}>
      {!hasActive ? (
        <div className="echtgeld-flat-state">
          <SafetyCertificateOutlined />
          <div>
            <Title level={4}>FLAT · 目前沒有未結算 Echtgeld 持倉</Title>
            <Text type="secondary">監控仍在運作；EBM 符合條件並由 8781 成功送單後，這裡會立即切成持倉畫面。</Text>
          </div>
        </div>
      ) : (
        <>
          <div className="echtgeld-position-hero">
            <div><Text type="secondary">Market</Text><Title level={3}>#{text(active.market_id ?? active.marketId)} · {side}</Title></div>
            <div><Text type="secondary">成交 / Shares</Text><Title level={3}>{price(active.execution_price ?? active.executionPrice)} · {shares?.toFixed(4) ?? '—'}</Title></div>
            <div><Text type="secondary">參考 Mark PnL</Text><Title level={3} className={markPnl !== null && markPnl < 0 ? 'echtgeld-negative' : markPnl !== null && markPnl > 0 ? 'echtgeld-positive' : ''}>{money(markPnl, 4)}</Title></div>
          </div>
          <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
            <Descriptions.Item label="Signal Ask">{price(active.signal_ask ?? active.signalAsk)}</Descriptions.Item>
            <Descriptions.Item label="Submitted">{money(cost, 4, false)}</Descriptions.Item>
            <Descriptions.Item label="Current same-side Bid">{markBid === null ? '—' : price(markBid)}</Descriptions.Item>
            <Descriptions.Item label="Order"><Tag color={orderColor(active.status)}>{text(active.status)}</Tag></Descriptions.Item>
            <Descriptions.Item label="Settlement"><Tag color={resultColor(active.resultStatus)}>{text(active.resultStatus, 'PENDING')}</Tag></Descriptions.Item>
            <Descriptions.Item label="Entered">{when(active.attempted_at_ms ?? active.attemptedAtMs)}</Descriptions.Item>
            <Descriptions.Item label="Venue">{text(active.venue)}</Descriptions.Item>
            <Descriptions.Item label="Vendor order">{text(active.vendor_order_id ?? active.vendorOrderId)}</Descriptions.Item>
          </Descriptions>
          <Alert type="info" showIcon message="Mark PnL 只是持倉監控值" description="以目前同側 Bid 估算；正式 PnL 仍只採 8781 durable order + 官方 winner 結算，不會把這個估值寫入績效帳本。" />
        </>
      )}
    </Card>
  )
}

export default function TargetTakerEchtgeldPage() {
  const engineService = useEchtgeldStore((state) => state.service)
  const saving = useEchtgeldStore((state) => state.saving)
  const saveError = useEchtgeldStore((state) => state.saveError)
  const refreshEngine = useEchtgeldStore((state) => state.refresh)
  const pauseEngine = useEchtgeldStore((state) => state.pause)
  const resumeEngine = useEchtgeldStore((state) => state.resume)
  const updateSettings = useEchtgeldStore((state) => state.updateSettings)
  const predictService = usePredictFunStore((state) => state.service)
  const walletService = useWalletShadowStore((state) => state.service)
  const refreshWalletShadow = useWalletShadowStore((state) => state.refresh)
  const observerHealth = useWalletLabHealthStore((state) => state.observer8776)
  const [form] = Form.useForm()

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      if (!cancelled && document.visibilityState === 'visible') void refreshEngine()
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
  }, [refreshEngine])

  const engine = row(engineService.data)
  const config = row(engine.config)
  const performance = row(engine.performance)
  const balance = row(engine.balance)
  const settlementSync = row(performance.settlementSync)
  const engineSettlementSync = row(engine.settlementSync)
  const predict = row(predictService.data)
  const btc = row(row(predict.assets).BTC)
  const wallet = row(walletService.data)
  const lab = row(wallet.targetTakerPublicSideV1Lab)
  const sideOnly = row(row(lab.cohorts)[SIDE_ONLY])
  const decision = row(sideOnly.lastDecision)
  const signal = row(decision.signal)
  const currentEvent = row(sideOnly.currentEvent)
  const producer = row(wallet.targetTakerEchtgeldProducerV1)
  const producerLast = row(producer.last)
  const orders = rows(engine.recentOrders)
  const events = rows(engine.recentEvents)
  const activeOrder = findActiveOrder(engine)
  const armed = engine.armed === true
  const selectedProbability = num(signal.selectedProbability)
  const threshold = num(signal.threshold) ?? 0.60
  const selectedAsk = num(decision.ask)
  const secondsLeft = num(decision.secondsLeft ?? btc.secondsLeft)
  const sampleAge = num(decision.sampleAgeMs)
  const receiptAge = num(decision.predictReceiptAgeMs)
  const notional = num(config.notionalUsdt)
  const liveSafetyReady = text(config.cohort) === SIDE_ONLY && notional !== null && notional > 0 && notional <= 1
  const strategyAlive = observerHealth.ok
  const predictAlive = predictService.ok && text(btc.status) === 'LIVE'

  useEffect(() => {
    if (!Object.keys(config).length || form.isFieldsTouched()) return
    form.setFieldsValue({
      venue: text(config.venue, 'predictfun'),
      notionalUsdt: num(config.notionalUsdt) ?? 1,
      maxPriceDrift: num(config.maxPriceDrift) ?? 0.02,
    })
  }, [config, form])

  const statusTitle = !engineService.ok
    ? 'Echtgeld Engine 離線'
    : !strategyAlive
      ? 'EBM Observer 離線'
      : !predictAlive
        ? '市場資料不是 LIVE'
        : !armed
          ? 'PAUSED · EBM 持續觀測，但不會送เงินจริง'
          : decision.decision === 'TRADE'
            ? 'LIVE ARMED · 本局已出現 EBM 進場訊號'
            : `LIVE ARMED · ${reasonLabel(decision.reason)}`
  const statusColor = !engineService.ok || !strategyAlive ? 'error' : armed ? 'success' : 'warning'

  const conditions = [
    ['8771 市場 WebSocket', predictAlive, `${text(btc.status)} · receipt ${num(btc.receiptAgeMs)?.toFixed(0) ?? '—'}ms`],
    ['8776 EBM Observer', strategyAlive, observerHealth.ok ? `ONLINE · ${Math.round(observerHealth.latencyMs ?? 0)}ms` : 'OFFLINE'],
    ['Public snapshot freshness', sampleAge !== null && sampleAge <= 2000, sampleAge === null ? '—' : `${sampleAge.toFixed(0)}ms / ≤ 2000ms`],
    ['Predict receipt freshness', receiptAge !== null && receiptAge <= 2500, receiptAge === null ? '—' : `${receiptAge.toFixed(0)}ms / ≤ 2500ms`],
    ['EBM selected probability', selectedProbability !== null && selectedProbability >= threshold, `${pct(selectedProbability)} / ≥ ${pct(threshold)}`],
    ['Selected Ask', selectedAsk !== null && selectedAsk > 0 && selectedAsk <= 0.95, `${price(selectedAsk)} / ≤ 0.950`],
    ['Entry time window', secondsLeft !== null && secondsLeft > 10, `${secondsLeft?.toFixed(1) ?? '—'}s / > 10s`],
    ['8781 Echtgeld arming', armed && liveSafetyReady, armed ? 'LIVE ARMED' : 'PAUSED'],
  ] as Array<[string, boolean, string]>

  const applySettings = async () => {
    try {
      const values = await form.validateFields()
      await updateSettings({
        venue: values.venue,
        cohort: SIDE_ONLY,
        notionalUsdt: Number(values.notionalUsdt),
        maxPriceDrift: Number(values.maxPriceDrift),
      })
      form.resetFields()
      message.success('Echtgeld 設定已寫入 8781 durable runtime config')
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const doPause = async () => {
    try {
      await pauseEngine('dashboard-v2 operator pause')
      message.success('Echtgeld 已 PAUSED；新 signal 只記錄、不送單')
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const doResume = async () => {
    if (!liveSafetyReady) {
      message.error('Live Canary 只允許 SIDE_ONLY 且每市場 notional ≤ 1 USDT。')
      return
    }
    try {
      await resumeEngine()
      message.warning('Echtgeld 已 LIVE ARMED；之後符合條件的新 intent 可能送出 Echtgeld')
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const eventColumns = [
    { title: '時間', key: 'time', width: 170, render: (_: unknown, item: RowObject) => when(item.occurred_at_ms) },
    { title: 'Level', dataIndex: 'level', key: 'level', width: 90, render: (value: unknown) => <Tag color={levelColor(value)}>{text(value)}</Tag> },
    { title: '事件 / Phase', key: 'event', width: 220, render: (_: unknown, item: RowObject) => <Space direction="vertical" size={0}><Text strong>{text(item.event_type)}</Text><Text type="secondary">{text(item.phase)}</Text></Space> },
    { title: 'Market', dataIndex: 'market_id', key: 'market', width: 100, render: (value: unknown) => value ? `#${text(value)}` : '—' },
    { title: 'Side', dataIndex: 'side', key: 'side', width: 80, render: (value: unknown) => <Tag>{text(value)}</Tag> },
    { title: '永久訊息', dataIndex: 'message', key: 'message', render: (value: unknown, item: RowObject) => <Space direction="vertical" size={0}><Text>{text(value)}</Text>{item.error_class ? <Text type="danger">{text(item.error_class)}</Text> : null}</Space> },
  ]

  const orderColumns = [
    { title: '時間', key: 'time', width: 170, render: (_: unknown, item: RowObject) => when(item.attempted_at_ms) },
    { title: 'Market', dataIndex: 'market_id', key: 'market', width: 95, render: (value: unknown) => `#${text(value)}` },
    { title: 'Venue', dataIndex: 'venue', key: 'venue', width: 100 },
    { title: 'Side', dataIndex: 'side', key: 'side', width: 75, render: (value: unknown) => <Tag>{text(value)}</Tag> },
    { title: 'Signal → Exec', key: 'price', width: 150, render: (_: unknown, item: RowObject) => `${price(item.signal_ask)} → ${price(item.execution_price)}` },
    { title: 'Fill', key: 'fill', width: 155, render: (_: unknown, item: RowObject) => `${money(item.submitted_usdt, 4, false)} / ${num(item.shares)?.toFixed(4) ?? '—'} sh` },
    { title: 'Order', dataIndex: 'status', key: 'status', width: 110, render: (value: unknown) => <Tag color={orderColor(value)}>{text(value)}</Tag> },
    { title: 'Result', dataIndex: 'resultStatus', key: 'result', width: 105, render: (value: unknown) => <Tag color={resultColor(value)}>{text(value)}</Tag> },
    { title: 'PnL / ROI', key: 'pnl', width: 130, render: (_: unknown, item: RowObject) => <Space direction="vertical" size={0}><Text>{money(item.netPnlUsdt, 4)}</Text><Text type="secondary">{pct(item.netRoi)}</Text></Space> },
    { title: 'Order ID / Error', key: 'detail', render: (_: unknown, item: RowObject) => <Space direction="vertical" size={0}><Text copyable={Boolean(item.vendor_order_id)}>{text(item.vendor_order_id)}</Text>{item.error_message ? <Text type="danger">{text(item.error_message)}</Text> : null}</Space> },
  ]

  return (
    <div>
      <div className="page-heading">
        <div>
          <Title level={3}>EBM Echtgeld Control</Title>
          <Text type="secondary">TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY · 8771 市場 → 8776 EBM → 8781 Echtgeld Engine</Text>
        </div>
        <Space wrap>
          <Tag color={predictService.ok ? 'success' : 'error'}>8771 MARKET · {predictService.ok ? `${Math.round(predictService.latencyMs ?? 0)}ms` : 'OFFLINE'}</Tag>
          <Tag color={observerHealth.ok ? 'success' : 'error'}>8776 EBM · {observerHealth.ok ? `${Math.round(observerHealth.latencyMs ?? 0)}ms` : 'OFFLINE'}</Tag>
          <Tag color={engineService.ok ? 'success' : 'error'}>8781 ECHTGELD · {engineService.ok ? `${Math.round(engineService.latencyMs ?? 0)}ms` : 'OFFLINE'}</Tag>
          <Button icon={<ReloadOutlined />} onClick={() => { void refreshEngine(); void refreshWalletShadow() }}>刷新完整狀態</Button>
        </Space>
      </div>

      {!engineService.ok ? <Alert className="echtgeld-alert" type="error" showIcon message="8781 Echtgeld Engine 無法連線" description={`${engineService.error || '請先啟動 start-echtgeld-engine-v1.ps1'}；舊資料若存在只保留顯示，不會被當成在線。`} /> : null}
      {saveError ? <Alert className="echtgeld-alert" type="error" showIcon message="Echtgeld 控制失敗" description={saveError} /> : null}
      {!liveSafetyReady && Object.keys(config).length ? <Alert className="echtgeld-alert" type="warning" showIcon message="Live Canary 安全設定不符合目前規格" description={`必須鎖定 SIDE_ONLY 且 notional ≤ 1 USDT。目前 cohort=${text(config.cohort)} / notional=${money(config.notionalUsdt, 2, false)}。Dashboard 會阻擋 Resume。`} /> : null}

      <Card className="echtgeld-status-hero" bordered={false}>
        <Row gutter={[20, 18]} align="middle">
          <Col xs={24} lg={15}>
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap><Tag color={statusColor}>{armed ? 'LIVE ARMED' : 'PAUSED'}</Tag><Tag>{text(decision.decision, 'WAITING')}</Tag><Tag>{text(decision.side)}</Tag></Space>
              <Title level={2}>{statusTitle}</Title>
              <Text className="echtgeld-status-reason">{reasonLabel(decision.reason)}</Text>
              <Text type="secondary">最後 EBM decision：{when(decision.sampledAtMs)}（{age(decision.sampledAtMs)}） · 8776 完整 state 可能因研究 ledger 較大而較慢；8771 行情與 8781 Echtgeld 心跳獨立監控。</Text>
            </Space>
          </Col>
          <Col xs={24} lg={9}>
            <div className="echtgeld-confidence">
              <Text type="secondary">EBM selected probability</Text>
              <Title level={2}>{pct(selectedProbability)}</Title>
              <Progress percent={selectedProbability === null ? 0 : Math.round(selectedProbability * 1000) / 10} status={selectedProbability !== null && selectedProbability >= threshold ? 'success' : 'normal'} />
              <Space wrap><Tag>門檻 {pct(threshold)}</Tag><Tag>Ask {price(selectedAsk)}</Tag><Tag>{secondsLeft?.toFixed(1) ?? '—'}s left</Tag></Space>
            </div>
          </Col>
        </Row>
      </Card>

      <Row gutter={[12, 12]} className="section-row">
        <Col xs={24} xxl={14}><MarketTrajectory asset={btc} activeOrder={activeOrder} /></Col>
        <Col xs={24} xxl={10}><PositionMonitor engine={engine} asset={btc} /></Col>
      </Row>

      <Row gutter={[12, 12]} className="section-row">
        <Col xs={24} xl={12}>
          <Card title={<Space><SafetyCertificateOutlined /> 當前策略條件</Space>} extra={<Tag color={text(sideOnly.status) === 'ACTIVE' ? 'success' : 'default'}>{text(sideOnly.status)}</Tag>}>
            <div className="echtgeld-condition-grid">
              {conditions.map(([label, ok, value]) => <div className="echtgeld-condition" key={label}><Tag color={ok ? 'success' : 'default'}>{ok ? 'PASS' : 'WAIT'}</Tag><div><Text strong>{label}</Text><br /><Text type="secondary">{value}</Text></div></div>)}
            </div>
            <Descriptions className="echtgeld-intent-summary" size="small" column={{ xs: 1, md: 2 }}>
              <Descriptions.Item label="本市場 paper event">{Object.keys(currentEvent).length ? `${text(currentEvent.side)} @ ${price(currentEvent.observedAsk ?? currentEvent.ask)}` : '尚未觸發'}</Descriptions.Item>
              <Descriptions.Item label="Producer handoff">{Object.keys(producerLast).length ? `${text(producerLast.status)} · #${text(producerLast.marketId)}` : '尚無 Echtgeld intent'}</Descriptions.Item>
              <Descriptions.Item label="Producer sent / errors">{text(producer.sent, '0')} / {text(producer.errors, '0')}</Descriptions.Item>
              <Descriptions.Item label="Target wallet 驅動">禁止 · public-state only</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        <Col xs={24} xl={12}>
          <Card title={<Space><WalletOutlined /> Echtgeld 帳戶 / PnL</Space>}>
            <Row gutter={[10, 10]}>
              <Col xs={12} md={6}><Statistic title="Available" value={num(balance.availableUsdt) ?? 0} precision={2} prefix="$" /></Col>
              <Col xs={12} md={6}><Statistic title="Net PnL" value={num(performance.netPnlUsdt) ?? 0} precision={2} prefix="$" valueStyle={{ color: (num(performance.netPnlUsdt) ?? 0) < 0 ? '#cf1322' : undefined }} /></Col>
              <Col xs={12} md={6}><Statistic title="W / L" value={`${text(performance.wins, '0')} / ${text(performance.losses, '0')}`} /></Col>
              <Col xs={12} md={6}><Statistic title="Win rate" value={num(performance.winRate) === null ? 0 : Number(performance.winRate) * 100} precision={1} suffix="%" /></Col>
            </Row>
            <Descriptions size="small" column={{ xs: 1, md: 2 }} className="echtgeld-accounting-details">
              <Descriptions.Item label="Venue">{text(config.venue)}</Descriptions.Item>
              <Descriptions.Item label="Balance status">{text(balance.status)}</Descriptions.Item>
              <Descriptions.Item label="Submitted / Attempts">{text(performance.submitted, '0')} / {text(performance.attempts, '0')}</Descriptions.Item>
              <Descriptions.Item label="Max Drawdown">{money(performance.maxDrawdownUsdt, 2, false)}</Descriptions.Item>
              <Descriptions.Item label="Net ROI">{pct(performance.netRoi)}</Descriptions.Item>
              <Descriptions.Item label="Settlement sync">{text(settlementSync.status ?? engineSettlementSync.status)}</Descriptions.Item>
            </Descriptions>
            <Text type="secondary">{text(performance.accountingBasis, 'PnL 以 8781 durable order 與官方 market settlement 為準。')}</Text>
          </Card>
        </Col>
      </Row>

      <Card className="stack-card" title={<Space><DollarOutlined /> Echtgeld 設定與控制</Space>} extra={<Space><Tag color={armed ? 'success' : 'default'}>{text(engine.runtimeStatus)}</Tag><Text type="secondary">Engine restart 永遠 PAUSED</Text></Space>}>
        <Alert type="info" showIcon message="Live Canary 固定 SIDE_ONLY" description="HAZARD_SIDE 仍只保留 paper。這個 Dashboard 不提供把 HAZARD_SIDE 切成 Echtgeld 的控制；單市場 notional 也限制在 1 USDT 以內。" />
        <Form form={form} layout="vertical" className="echtgeld-control-form">
          <Row gutter={[12, 0]}>
            <Col xs={24} md={8}><Form.Item name="venue" label="Venue" rules={[{ required: true }]}><Select disabled={armed} options={[{ value: 'predictfun', label: 'Predict.fun' }, { value: 'binance', label: 'Binance Prediction' }]} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="notionalUsdt" label="每市場 Echtgeld Notional (USDT)" rules={[{ required: true }]}><InputNumber disabled={armed} min={0.01} max={1} step={0.01} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="maxPriceDrift" label="Max price drift" rules={[{ required: true }]}><InputNumber disabled={armed} min={0} max={0.10} step={0.001} precision={3} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
        </Form>
        <Space wrap>
          <Button disabled={armed || !engineService.ok} loading={saving} onClick={() => void applySettings()}>套用設定</Button>
          {armed ? (
            <Popconfirm title="PAUSE Echtgeld 新進場？" description="已送出的持倉不會被刪除；之後新 intent 只會被記錄為 paused。" okText="Pause" cancelText="取消" onConfirm={() => void doPause()}><Button danger icon={<PauseCircleOutlined />} loading={saving}>PAUSE NEW ENTRY</Button></Popconfirm>
          ) : (
            <Popconfirm title="RESUME Echtgeld？" description={`確認以 ${text(config.venue)} / ${money(config.notionalUsdt, 2, false)} / SIDE_ONLY 讓未來符合條件的新 intent 可以送 Echtgeld。`} okText="LIVE ARMED" cancelText="取消" onConfirm={() => void doResume()}><Button type="primary" icon={<PlayCircleOutlined />} disabled={!engineService.ok || !liveSafetyReady} loading={saving}>RESUME ECHTGELD</Button></Popconfirm>
          )}
          <Text type="secondary">目前 config：{text(config.cohort)} · {money(config.notionalUsdt, 2, false)} · drift {price(config.maxPriceDrift)}</Text>
        </Space>
      </Card>

      <Card className="stack-card" title="永久實單訊息" extra={<Text type="secondary">8781 SQLite engine_events · restart 後仍保留</Text>}>
        {engine.latestError ? <Alert className="echtgeld-alert" type="error" showIcon message="最近 Echtgeld ERROR" description={text(row(engine.latestError).message)} /> : null}
        {engine.latestWarning ? <Alert className="echtgeld-alert" type="warning" showIcon message="最近 Echtgeld WARNING" description={text(row(engine.latestWarning).message)} /> : null}
        <Table<RowObject> size="small" rowKey={(item) => text(item.id, `${text(item.occurred_at_ms)}:${text(item.event_type)}`)} dataSource={events} columns={eventColumns} pagination={{ pageSize: 20, hideOnSinglePage: true }} scroll={{ x: 1050 }} locale={{ emptyText: '尚無 Echtgeld event；Engine 啟動、Pause/Resume、intent、送單結果都會永久記錄在這裡。' }} />
      </Card>

      <Card className="stack-card" title="Durable Echtgeld Order Ledger" extra={<Text type="secondary">實際 fill + 官方 winner → tracked PnL</Text>}>
        <Table<RowObject> size="small" rowKey={(item) => text(item.id, `${text(item.market_id)}:${text(item.intent_id)}`)} dataSource={orders} columns={orderColumns} pagination={{ pageSize: 20, hideOnSinglePage: true }} scroll={{ x: 1350 }} locale={{ emptyText: '還沒有 Echtgeld order attempt。' }} />
      </Card>
    </div>
  )
}
