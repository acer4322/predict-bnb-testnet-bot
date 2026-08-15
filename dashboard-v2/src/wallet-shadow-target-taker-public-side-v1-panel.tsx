import { useEffect } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
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
import type { TableColumnsType } from 'antd'
import { DollarOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { useWalletShadowStore } from './wallet-shadow-store'

const { Text, Title } = Typography

type RowObject = Record<string, unknown>

function row(value: unknown): RowObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
}

function rows(value: unknown): RowObject[] {
  return Array.isArray(value)
    ? value.filter((item): item is RowObject => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    : []
}

function num(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function pct(value: unknown, digits = 1): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(digits)}%`
}

function money(value: unknown): string {
  const parsed = num(value)
  if (parsed === null) return '—'
  return `${parsed > 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

function balanceMoney(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `$${parsed.toFixed(2)}`
}

function fixed(value: unknown, digits = 3): string {
  const parsed = num(value)
  return parsed === null ? '—' : parsed.toFixed(digits)
}

function time(value: unknown): string {
  const parsed = num(value)
  if (parsed === null || parsed <= 0) return '—'
  const date = new Date(parsed)
  return date.toLocaleTimeString('zh-TW', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function sideTag(value: unknown) {
  const side = text(value).toUpperCase()
  if (side === 'UP') return <Tag color="success">UP</Tag>
  if (side === 'DOWN') return <Tag color="error">DOWN</Tag>
  return <Tag>{side}</Tag>
}

function resultTag(value: unknown) {
  const status = text(value).toUpperCase()
  if (status === 'WIN') return <Tag color="success">WIN</Tag>
  if (status === 'LOSS') return <Tag color="error">LOSS</Tag>
  if (status === 'FLAT') return <Tag color="warning">FLAT</Tag>
  if (status === 'OPEN' || status === 'PENDING') return <Tag color="processing">{status}</Tag>
  if (status === 'NO_TRADE') return <Tag>NO TRADE</Tag>
  return <Tag>{status}</Tag>
}

function decisionTag(value: unknown) {
  const decision = text(value).toUpperCase()
  if (decision === 'TRADE') return <Tag color="success">TRADE</Tag>
  if (decision === 'SKIP') return <Tag>SKIP</Tag>
  return <Tag>{decision}</Tag>
}

function orderStatusTag(value: unknown) {
  const status = text(value).toUpperCase()
  if (status === 'SUBMITTED') return <Tag color="success">SUBMITTED</Tag>
  if (status === 'ATTEMPTING') return <Tag color="processing">ATTEMPTING</Tag>
  if (status === 'AMBIGUOUS') return <Tag color="warning">AMBIGUOUS</Tag>
  if (status === 'REJECTED') return <Tag color="error">REJECTED</Tag>
  return <Tag>{status}</Tag>
}

const recentColumns: TableColumnsType<RowObject> = [
  { title: 'Market', dataIndex: 'market_id', width: 88, render: (value) => `#${text(value)}` },
  { title: 'Winner', dataIndex: 'winner', width: 78, render: sideTag },
  { title: 'Result', dataIndex: 'status', width: 92, render: resultTag },
  { title: 'Side', dataIndex: 'side', width: 76, render: sideTag },
  { title: 'Entry ask', dataIndex: 'observed_ask', width: 92, render: (value) => fixed(value) },
  { title: 'Stake', dataIndex: 'stake_usdt', width: 82, render: (value) => `$${fixed(value, 2)}` },
  { title: 'PnL', dataIndex: 'net_pnl_usdt', width: 92, render: (value) => <strong>{money(value)}</strong> },
  { title: 'ROI', dataIndex: 'net_roi', width: 82, render: (value) => pct(value) },
  { title: 'Resolved', dataIndex: 'resolved_at_ms', width: 98, render: time },
]

const liveOrderColumns: TableColumnsType<RowObject> = [
  { title: 'Market', dataIndex: 'market_id', width: 86, render: (value) => `#${text(value)}` },
  { title: 'Venue', dataIndex: 'venue', width: 92, render: (value) => <Tag>{text(value).toUpperCase()}</Tag> },
  { title: 'Side', dataIndex: 'side', width: 72, render: sideTag },
  { title: 'Order', dataIndex: 'status', width: 112, render: orderStatusTag },
  { title: 'Result', dataIndex: 'resultStatus', width: 90, render: resultTag },
  { title: 'Signal ask', dataIndex: 'signal_ask', width: 94, render: (value) => fixed(value) },
  { title: 'Exec', dataIndex: 'execution_price', width: 82, render: (value) => fixed(value) },
  { title: 'Submitted', dataIndex: 'submitted_usdt', width: 96, render: (value) => balanceMoney(value) },
  { title: 'PnL', dataIndex: 'netPnlUsdt', width: 88, render: (value) => <strong>{money(value)}</strong> },
  { title: 'ROI', dataIndex: 'netRoi', width: 78, render: (value) => pct(value) },
  { title: 'At', dataIndex: 'attempted_at_ms', width: 90, render: time },
  { title: 'Order ID', dataIndex: 'vendor_order_id', width: 180, render: (value) => <Text code>{text(value)}</Text> },
  { title: 'Error', dataIndex: 'error_message', width: 220, render: (value) => text(value) },
]

const bankrollColumns: TableColumnsType<RowObject> = [
  { title: 'Market', dataIndex: 'market_id', width: 88, render: (value) => `#${text(value)}` },
  { title: 'Side', dataIndex: 'side', width: 72, render: sideTag },
  { title: 'Result', dataIndex: 'status', width: 90, render: resultTag },
  { title: 'Confidence', dataIndex: 'signal_confidence', width: 96, render: (value) => pct(value, 1) },
  { title: 'Maturity', dataIndex: 'maturity_state', width: 102, render: (value) => <Tag>{text(value)}</Tag> },
  { title: 'Risk', dataIndex: 'final_risk_fraction', width: 74, render: (value) => pct(value, 2) },
  { title: 'Stake', dataIndex: 'stake_usdt', width: 82, render: (value) => balanceMoney(value) },
  { title: 'PnL', dataIndex: 'net_pnl_usdt', width: 88, render: (value) => <strong>{money(value)}</strong> },
  { title: 'Equity after', dataIndex: 'equity_after_usdt', width: 102, render: (value) => balanceMoney(value) },
  { title: 'Surprise', dataIndex: 'surprise_loss', width: 82, render: (value) => value ? <Tag color="warning">YES</Tag> : <Tag>NO</Tag> },
  { title: 'Sizing reason', dataIndex: 'sizing_reason', width: 360, render: (value) => <Text>{text(value)}</Text> },
]

function CohortCard({ cohort, title, color, hazard }: { cohort: RowObject; title: string; color: string; hazard: boolean }) {
  const decision = row(cohort.lastDecision)
  const signal = row(decision.signal)
  const hazardDecision = row(decision.hazard)
  const event = row(cohort.currentEvent)
  const eligibility = row(cohort.eligibility)
  const performance = row(cohort.performance)

  const selectedProbability = num(signal.selectedProbability)
  const probabilityUp = num(signal.probabilityUp)
  const threshold = num(signal.threshold) ?? 0.60
  const score = num(signal.score)
  const eligibilityScore = num(hazardDecision.eligibilityScore)
  const eligibilityThreshold = num(hazardDecision.threshold) ?? 0.55
  const expiresAt = num(eligibility.expiresAtMs)
  const remainingMs = expiresAt === null ? null : Math.max(0, expiresAt - Date.now())
  const confidencePercent = selectedProbability === null ? 0 : Math.max(0, Math.min(100, selectedProbability * 100))

  return (
    <Card size="small" title={<><Tag color={color}>{title}</Tag> {text(cohort.status)}</>} style={{ height: '100%' }}>
      <Row gutter={[10, 10]}>
        <Col xs={12} md={8}><Statistic title="Traded / Settled" value={`${text(performance.tradedMarkets, '0')} / ${text(performance.settledMarkets, '0')}`} /></Col>
        <Col xs={12} md={8}><Statistic title="Win rate" value={pct(performance.winRate)} /></Col>
        <Col xs={12} md={8}><Statistic title="Trade rate" value={pct(performance.tradeRate)} /></Col>
        <Col xs={12} md={8}><Statistic title="Net PnL" value={money(performance.netPnlUsdt)} /></Col>
        <Col xs={12} md={8}><Statistic title="Net ROI" value={pct(performance.netRoi)} /></Col>
        <Col xs={12} md={8}><Statistic title="Max drawdown" value={money(performance.maxDrawdownUsdt)} /></Col>
      </Row>

      <Descriptions size="small" column={2} style={{ marginTop: 10 }}>
        <Descriptions.Item label="目前決策">{decisionTag(decision.decision)} {text(decision.reason)}</Descriptions.Item>
        <Descriptions.Item label="方向">{sideTag(decision.side)}</Descriptions.Item>
        <Descriptions.Item label="EBM selected probability">{pct(selectedProbability, 2)}</Descriptions.Item>
        <Descriptions.Item label="P(UP)">{pct(probabilityUp, 2)}</Descriptions.Item>
        <Descriptions.Item label="Signed score">{score === null ? '—' : score.toFixed(4)}</Descriptions.Item>
        <Descriptions.Item label="EBM threshold">{pct(threshold, 1)}</Descriptions.Item>
        <Descriptions.Item label="Ask">{fixed(decision.ask)}</Descriptions.Item>
        <Descriptions.Item label="Seconds left">{fixed(decision.secondsLeft, 1)}s</Descriptions.Item>
        <Descriptions.Item label="Sample age">{fixed(decision.sampleAgeMs, 0)} ms</Descriptions.Item>
        <Descriptions.Item label="Predict receipt age">{fixed(decision.predictReceiptAgeMs, 0)} ms</Descriptions.Item>
        <Descriptions.Item label="目前已進場">{Object.keys(event).length > 0 ? <Tag color="purple">YES</Tag> : <Tag>NO</Tag>}</Descriptions.Item>
        <Descriptions.Item label="目前 entry">{Object.keys(event).length > 0 ? <>{sideTag(event.side)} @ {fixed(event.ask)}</> : '—'}</Descriptions.Item>
        <Descriptions.Item label="Pending markets">{text(performance.pendingMarkets, '0')}</Descriptions.Item>
        <Descriptions.Item label="Longest loss streak">{text(performance.longestLossStreak, '0')}</Descriptions.Item>
        {hazard ? (
          <>
            <Descriptions.Item label="Hazard score">{eligibilityScore === null ? '—' : `${pct(eligibilityScore, 1)} / ${pct(eligibilityThreshold, 1)}`}</Descriptions.Item>
            <Descriptions.Item label="Hazard reason">{text(hazardDecision.reason)}</Descriptions.Item>
            <Descriptions.Item label="Maker anchor">{sideTag(hazardDecision.makerAnchorSide ?? eligibility.makerAnchorSide)}</Descriptions.Item>
            <Descriptions.Item label="Maker-side mid">{fixed(hazardDecision.makerSidePredictMid)}</Descriptions.Item>
            <Descriptions.Item label="Time regime">{text(hazardDecision.timeRegime)}</Descriptions.Item>
            <Descriptions.Item label="Price regime">{text(hazardDecision.priceRegime)}</Descriptions.Item>
            <Descriptions.Item label="5s window">{remainingMs === null ? '等待 own paper Maker fill' : `${(remainingMs / 1000).toFixed(1)}s remaining`}</Descriptions.Item>
          </>
        ) : null}
      </Descriptions>

      <div style={{ marginTop: 8 }}>
        <Text type="secondary">EBM confidence · gate {pct(threshold, 1)}</Text>
        <Progress percent={confidencePercent} status={selectedProbability !== null && selectedProbability >= threshold ? 'success' : 'normal'} showInfo={false} size="small" />
      </div>

      <Table
        style={{ marginTop: 10 }}
        rowKey={(item) => text(item.market_id)}
        dataSource={rows(performance.recentMarkets).slice(0, 10)}
        columns={recentColumns}
        size="small"
        pagination={false}
        scroll={{ x: 880 }}
        locale={{ emptyText: '等待 forward paper 市場結算' }}
      />
    </Card>
  )
}

function LiveControl({ live, market, sideOnly }: { live: RowObject; market: RowObject; sideOnly: RowObject }) {
  const update = useWalletShadowStore((state) => state.updateTargetTakerSettings)
  const refresh = useWalletShadowStore((state) => state.refresh)
  const saving = useWalletShadowStore((state) => state.targetTakerSaving)
  const saveError = useWalletShadowStore((state) => state.targetTakerSaveError)
  const [form] = Form.useForm()
  const performance = row(live.performance)
  const balance = row(live.balance)
  const decision = row(sideOnly.lastDecision)
  const runtimeEnabled = live.runtimeEnabled === true

  const settings = {
    venue: text(live.venue, 'predictfun'),
    notionalUsdt: num(live.notionalUsdt) ?? 1,
    maxPriceDrift: num(live.maxPriceDrift) ?? 0.02,
    cohort: text(live.cohort, 'TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY'),
  }

  useEffect(() => {
    if (!form.isFieldsTouched()) form.setFieldsValue(settings)
  }, [live.venue, live.notionalUsdt, live.maxPriceDrift, live.cohort, form])

  const saveSettings = async () => {
    try {
      const values = await form.validateFields()
      await update({
        venue: values.venue,
        notionalUsdt: Number(values.notionalUsdt),
        maxPriceDrift: Number(values.maxPriceDrift),
        cohort: values.cohort,
      })
      form.setFieldsValue(values)
      message.success('Target Taker runtime 設定已套用到下一次新進場')
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const toggleRuntime = async () => {
    try {
      await update({ runtimeEnabled: !runtimeEnabled })
      message.success(runtimeEnabled ? 'Target Taker Echtgeld已暫停新進場' : 'Target Taker Echtgeld已恢復新進場')
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <>
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={9}>
          <Card title={<Space><SafetyCertificateOutlined /> Echtgeld狀態</Space>} style={{ height: '100%' }}>
            <Space wrap style={{ marginBottom: 10 }}>
              <Tag color={runtimeEnabled ? 'success' : 'default'}>{runtimeEnabled ? 'LIVE · NEW ENTRY ON' : 'PAUSED · PAPER CONTINUES'}</Tag>
              <Tag color={text(live.venue) === 'binance' ? 'gold' : 'blue'}>{text(live.venue).toUpperCase()}</Tag>
              <Tag>{text(live.executorVersion)}</Tag>
            </Space>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Current market">#{text(market.id ?? market.marketId ?? market.market_id ?? balance.marketId)}</Descriptions.Item>
              <Descriptions.Item label="EBM decision">{decisionTag(decision.decision)} {sideTag(decision.side)} @ {fixed(decision.ask)}</Descriptions.Item>
              <Descriptions.Item label="Live cohort">{text(live.cohort)}</Descriptions.Item>
              <Descriptions.Item label="Notional">{balanceMoney(live.notionalUsdt)}</Descriptions.Item>
              <Descriptions.Item label="Max price drift">{fixed(live.maxPriceDrift, 3)}</Descriptions.Item>
              <Descriptions.Item label="Last execution">{orderStatusTag(row(live.lastExecution).status)} · {time(row(live.lastExecution).completedAtMs)}</Descriptions.Item>
              <Descriptions.Item label="Runtime settings">重啟後回到 launcher 設定</Descriptions.Item>
            </Descriptions>
            <Space wrap style={{ marginTop: 10 }}>
              <Popconfirm
                title={runtimeEnabled ? '暫停 Target Taker Echtgeld新進場？' : '恢復 Target Taker Echtgeld新進場？'}
                description={runtimeEnabled ? 'Paper cohort 與 Auto Bankroll 仍會繼續跑。' : `確認以 ${text(live.venue).toUpperCase()}、${balanceMoney(live.notionalUsdt)} 開始之後的新 Echtgeld BUY。`}
                okText="確認"
                cancelText="取消"
                onConfirm={() => void toggleRuntime()}
              >
                <Button danger={runtimeEnabled} loading={saving}>{runtimeEnabled ? 'Pause New Entry' : 'Resume Echtgeld'}</Button>
              </Popconfirm>
              <Button icon={<ReloadOutlined />} onClick={() => void refresh()}>重新讀取狀態</Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} xl={7}>
          <Card title={<Space><DollarOutlined /> 選定 Venue 可用餘額</Space>} style={{ height: '100%' }}>
            <Statistic title={`${text(balance.venue, text(live.venue)).toUpperCase()} available USDT`} value={balanceMoney(balance.availableUsdt)} />
            <Descriptions column={1} size="small" style={{ marginTop: 10 }}>
              <Descriptions.Item label="Status">{text(balance.status)}</Descriptions.Item>
              <Descriptions.Item label="Market round">#{text(balance.marketId)}</Descriptions.Item>
              <Descriptions.Item label="Last read">{time(balance.asOfMs)}</Descriptions.Item>
              <Descriptions.Item label="Source">{text(balance.source)}</Descriptions.Item>
              <Descriptions.Item label="Refresh">每輪一次；切 venue 立即重讀</Descriptions.Item>
            </Descriptions>
            {balance.error ? <Alert type="warning" showIcon message="餘額讀取失敗" description={text(balance.error)} /> : null}
          </Card>
        </Col>

        <Col xs={24} xl={8}>
          <Card title="Echtgeld Runtime Settings" style={{ height: '100%' }}>
            {saveError ? <Alert type="error" showIcon message="設定寫入失敗" description={saveError} style={{ marginBottom: 10 }} /> : null}
            <Form form={form} layout="vertical" initialValues={settings}>
              <Row gutter={[10, 0]}>
                <Col span={12}>
                  <Form.Item name="venue" label="Venue" rules={[{ required: true }]}>
                    <Select options={[
                      { value: 'predictfun', label: 'Predict.fun' },
                      { value: 'binance', label: 'Binance Prediction' },
                    ]} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="notionalUsdt" label="每筆 USDT" rules={[{ required: true }]}>
                    <InputNumber min={0.01} max={100} step={0.25} precision={2} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={[10, 0]}>
                <Col span={12}>
                  <Form.Item name="maxPriceDrift" label="Max price drift" rules={[{ required: true }]}>
                    <InputNumber min={0} max={0.10} step={0.005} precision={3} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="cohort" label="Live cohort" rules={[{ required: true }]}>
                    <Select options={[
                      { value: 'TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY', label: 'SIDE ONLY' },
                      { value: 'TARGET_TAKER_PUBLIC_SIDE_V1_HAZARD_SIDE', label: 'HAZARD + SIDE' },
                    ]} />
                  </Form.Item>
                </Col>
              </Row>
              <Popconfirm
                title="套用 Echtgeld runtime 設定？"
                description="只影響之後的新進場；正在送出的 FOK 不會中途切換。重啟 8776 後回到 launcher/env 設定。"
                okText="套用"
                cancelText="取消"
                onConfirm={() => void saveSettings()}
              >
                <Button type="primary" loading={saving}>套用設定</Button>
              </Popconfirm>
            </Form>
          </Card>
        </Col>
      </Row>

      <Card title="Echtgeld 勝敗 / PnL / ROI" style={{ marginTop: 12 }}>
        <Row gutter={[10, 10]}>
          <Col xs={12} md={6} xl={3}><Statistic title="Attempts" value={text(performance.attempts, '0')} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Submitted" value={text(performance.submitted, '0')} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="W / L" value={`${text(performance.wins, '0')} / ${text(performance.losses, '0')}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Win rate" value={pct(performance.winRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Net PnL" value={money(performance.netPnlUsdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Net ROI" value={pct(performance.netRoi)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Rejected / Ambiguous" value={`${text(performance.rejected, '0')} / ${text(performance.ambiguous, '0')}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Max drawdown" value={money(performance.maxDrawdownUsdt)} /></Col>
        </Row>
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 10 }}
          message="Live PnL 是 tracked FOK accounting，不冒充交易所 statement"
          description={text(performance.accountingBasis)}
        />
        <Table
          style={{ marginTop: 10 }}
          rowKey={(item) => `${text(item.cohort)}:${text(item.market_id)}`}
          dataSource={rows(performance.recentOrders)}
          columns={liveOrderColumns}
          size="small"
          pagination={false}
          scroll={{ x: 1450 }}
          locale={{ emptyText: '尚無 Echtgeld Target Taker orders' }}
        />
      </Card>
    </>
  )
}

function AutoBankrollCard({ bankroll }: { bankroll: RowObject }) {
  const performance = row(bankroll.performance)
  const sizing = row(bankroll.lastSizing)
  return (
    <Card
      title={<Space><Tag color="cyan">PAPER</Tag> Target Taker Auto Bankroll V1</Space>}
      style={{ marginTop: 12 }}
    >
      <Alert
        type="info"
        showIcon
        message="同一 SIDE_ONLY EBM trade stream，只改下注比例"
        description="初始模擬資金 $100；只用已結算 equity 複利。這個錢包不會改 Echtgeld notional，也不會使用 Target wallet 即時成交去決定 size。"
        style={{ marginBottom: 12 }}
      />
      <Row gutter={[10, 10]}>
        <Col xs={12} md={6} xl={3}><Statistic title="Equity" value={balanceMoney(bankroll.equityUsdt)} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="High water" value={balanceMoney(bankroll.highWaterUsdt)} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Maturity" value={text(bankroll.currentMaturityState)} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Risk ceiling" value={pct(bankroll.currentRiskCeilingFraction, 2)} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="W / L" value={`${text(performance.wins, '0')} / ${text(performance.losses, '0')}`} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Win rate" value={pct(performance.winRate)} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Net PnL" value={money(performance.netPnlUsdt)} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Net ROI" value={pct(performance.netRoi)} /></Col>
      </Row>

      <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }} style={{ marginTop: 12 }}>
        <Descriptions.Item label="Last equity before">{balanceMoney(sizing.equityBeforeUsdt)}</Descriptions.Item>
        <Descriptions.Item label="Last stake">{balanceMoney(sizing.stakeUsdt)}</Descriptions.Item>
        <Descriptions.Item label="Final risk">{pct(sizing.finalRiskFraction, 2)}</Descriptions.Item>
        <Descriptions.Item label="Signal multiplier">×{fixed(sizing.signalMultiplier, 2)}</Descriptions.Item>
        <Descriptions.Item label="Surprise-loss multiplier">×{fixed(sizing.shortTermMultiplier, 2)}</Descriptions.Item>
        <Descriptions.Item label="Drawdown multiplier">×{fixed(sizing.drawdownMultiplier, 2)}</Descriptions.Item>
        <Descriptions.Item label="Current DD">{pct(performance.currentDrawdownFraction, 2)}</Descriptions.Item>
        <Descriptions.Item label="Recent 5 surprise losses">{text(performance.recent5SurpriseLosses, '0')}</Descriptions.Item>
        <Descriptions.Item label="Sizing reason" span={4}>{text(sizing.sizingReason)}</Descriptions.Item>
      </Descriptions>

      <Table
        style={{ marginTop: 10 }}
        rowKey={(item) => text(item.market_id)}
        dataSource={rows(bankroll.recentTrades)}
        columns={bankrollColumns}
        size="small"
        pagination={false}
        scroll={{ x: 1350 }}
        locale={{ emptyText: '等待 SIDE_ONLY 產生第一筆 Auto Bankroll paper trade' }}
      />
    </Card>
  )
}

export default function WalletShadowTargetTakerPublicSideV1Panel() {
  const service = useWalletShadowStore((state) => state.service)
  const snapshot = row(service.data)
  const lab = row(snapshot.targetTakerPublicSideV1Lab)
  const live = row(snapshot.targetTakerLiveV1)
  const bankroll = row(snapshot.targetTakerAutoBankrollV1)
  const market = row(snapshot.market)
  const model = row(lab.sideModel)
  const policy = row(lab.policy)
  const cohorts = row(lab.cohorts)
  const sideOnly = row(cohorts.TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY)
  const hazardSide = row(cohorts.TARGET_TAKER_PUBLIC_SIDE_V1_HAZARD_SIDE)

  if (!Object.keys(lab).length) {
    return (
      <Alert
        type="warning"
        showIcon
        message="Target Taker V1 尚未出現在 8776 state"
        description="確認 8776 使用最新 predict_wallet_shadow_observer_v4_21，並以 start-target-taker-public-side-v1.ps1 啟動。"
      />
    )
  }

  return (
    <>
      <div style={{ marginBottom: 12 }}>
        <Title level={2} style={{ marginBottom: 2 }}>Target Taker V1</Title>
        <Text type="secondary">Frozen Side EBM · Echtgeld control · Auto Bankroll paper wallet · fixed-$1 forward controls</Text>
      </div>

      {Object.keys(live).length ? (
        <LiveControl live={live} market={market} sideOnly={sideOnly} />
      ) : (
        <Alert type="warning" showIcon message="targetTakerLiveV1 尚未載入" description="需要最新 8776 observer 才有 Echtgeld status/settings/balance panel。" />
      )}

      {Object.keys(bankroll).length ? (
        <AutoBankrollCard bankroll={bankroll} />
      ) : (
        <Alert type="warning" showIcon style={{ marginTop: 12 }} message="targetTakerAutoBankrollV1 尚未載入" />
      )}

      <Card title="Fixed-$1 Frozen EBM Forward Controls" style={{ marginTop: 12 }}>
        <Alert
          type={model.loaded === true ? 'success' : 'warning'}
          showIcon
          message={model.loaded === true ? 'compact_side EBM 已載入，固定 $1 paper controls 正在 forward 測試' : 'Side EBM 尚未載入，paper cohorts 會 fail closed'}
          description={model.loaded === true
            ? 'SIDE_ONLY 是 Echtgeld預設 signal source；HAZARD_SIDE 額外要求 Lifecycle V3 自己的 paper Maker fill 後 5 秒 time+price gate。Target 即時成交不能觸發策略。'
            : text(model.error, `Expected model: ${text(model.path)}`)}
          style={{ marginBottom: 12 }}
        />

        <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
          <Col xs={12} md={6}><Statistic title="Version" value={text(lab.version)} /></Col>
          <Col xs={12} md={6}><Statistic title="Model" value={model.loaded === true ? 'LOADED' : 'OFFLINE'} /></Col>
          <Col xs={12} md={6}><Statistic title="Fixed stake" value={`$${fixed(policy.fixedStakeUsdt, 2)}`} /></Col>
          <Col xs={12} md={6}><Statistic title="Side threshold" value={pct(policy.sideProbabilityThreshold, 1)} /></Col>
        </Row>

        <Row gutter={[12, 12]}>
          <Col xs={24} xl={12}><CohortCard cohort={sideOnly} title="SIDE ONLY" color="blue" hazard={false} /></Col>
          <Col xs={24} xl={12}><CohortCard cohort={hazardSide} title="HAZARD + SIDE" color="purple" hazard /></Col>
        </Row>

        <Text type="secondary" style={{ display: 'block', marginTop: 10 }}>
          EBM probability 是 class-weighted model score，只拿來做門檻、排序與 Auto Bankroll operational high-confidence label，不宣稱是校準後真實勝率。
        </Text>
      </Card>
    </>
  )
}
