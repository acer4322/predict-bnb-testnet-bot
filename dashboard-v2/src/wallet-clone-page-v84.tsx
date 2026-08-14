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
  Row,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined, SafetyCertificateOutlined, SwapOutlined } from '@ant-design/icons'
import { type CloneAsset, useWalletCloneStore } from './wallet-clone-store'

const { Title, Text } = Typography

type RowObject = Record<string, unknown>

function row(value: unknown): RowObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
}

function num(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function fixed(value: unknown, digits = 4): string {
  const parsed = num(value)
  return parsed === null ? '—' : parsed.toFixed(digits)
}

function money(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `$${parsed.toFixed(4)}`
}

function pct(value: unknown): string {
  const parsed = num(value)
  if (parsed === null) return '—'
  const normalized = parsed > 1 ? parsed : parsed * 100
  return `${normalized.toFixed(1)}%`
}

function directPct(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${parsed.toFixed(2)}%`
}

function clock(value: unknown): string {
  const parsed = num(value)
  if (parsed === null || parsed <= 0) return '—'
  return new Date(parsed).toLocaleTimeString('zh-TW', { hour12: false })
}

function statusColor(value: unknown): string {
  const status = text(value, '').toUpperCase()
  if (['FILLED', 'BOTH_FILLED', 'RESTING', 'BOTH_RESTING', 'PAIR_ACTIVE', 'PAIRED_REPLENISH_ACTIVE'].includes(status)) return 'success'
  if (['PARTIAL_FILL', 'ONE_FILLED', 'PARTIAL_SUBMISSION', 'CANCELING', 'CANCEL_PENDING', 'PLACING_BOTH', 'HARD_T30_CANCEL_PENDING'].includes(status)) return 'processing'
  if (['AMBIGUOUS', 'REJECTED', 'FAILED', 'BLOCKED_NORMAL_LIVE', 'MAXIMUM_LOSS_STOPPED'].includes(status)) return 'error'
  if (['CANCELED', 'ROLLED', 'PAUSED', 'SAFE_PAUSED', 'NO_NEW_ENTRY_AFTER_T60', 'MAX_ENTRY_COUNT_REACHED', 'HARD_T30_CUTOFF'].includes(status)) return 'default'
  return 'warning'
}

function settingsFrom(snapshot: RowObject | null) {
  const settings = row(snapshot?.settings)
  return {
    targetPotentialProfitUsdt: num(settings.targetPotentialProfitUsdt) ?? 1,
    maximumEntryCount: num(settings.maximumEntryCount) ?? 3,
    maximumLossUsdt: num(settings.maximumLossUsdt) ?? 5,
    maximumCombinedMakerPrice: num(settings.maximumCombinedMakerPrice) ?? num(settings.maximumCombinedPrice) ?? 0.98,
    bidOffsetTicks: num(settings.bidOffsetTicks) ?? 0,
    minimumOrderUsdt: num(settings.minimumOrderUsdt) ?? 1,
    maximumOrderUsdt: num(settings.maximumOrderUsdt) ?? 25,
    minimumRemainingSeconds: num(settings.minimumRemainingSeconds) ?? 30,
  }
}

function OrderCard({ side, order, book }: { side: 'UP' | 'DOWN'; order: RowObject; book: RowObject }) {
  const state = text(order.state, 'NOT PLACED')
  return (
    <Card
      size="small"
      title={<Space><Tag color={side === 'UP' ? 'success' : 'error'}>{side}</Tag><span>Passive Maker</span></Space>}
      extra={<Tag color={statusColor(state)}>{state}</Tag>}
    >
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Generation">{text(order.generation)}</Descriptions.Item>
        <Descriptions.Item label="Book Bid / Ask">{fixed(book.bestBid)} / {fixed(book.bestAsk)}</Descriptions.Item>
        <Descriptions.Item label="掛單價">{fixed(order.target_price)}</Descriptions.Item>
        <Descriptions.Item label="原策略潛在獲利目標">{money(order.target_profit_usdt)}</Descriptions.Item>
        <Descriptions.Item label="Planned cost / shares">{money(order.planned_cost_usdt)} / {fixed(order.planned_shares)}</Descriptions.Item>
        <Descriptions.Item label="Filled cost / shares">{money(order.filled_usdt_amount)} / {fixed(order.filled_share_qty)}</Descriptions.Item>
        <Descriptions.Item label="Fill">{pct(order.fill_percentage)}</Descriptions.Item>
        <Descriptions.Item label="Exchange status">{text(order.order_status)}</Descriptions.Item>
        <Descriptions.Item label="Order ID"><Text copyable={Boolean(order.order_id)}>{text(order.order_id)}</Text></Descriptions.Item>
        <Descriptions.Item label="Placed / reconciled">{clock(order.place_completed_at_ms)} / {clock(order.last_reconciled_at_ms)}</Descriptions.Item>
      </Descriptions>
      {order.error_message ? <Alert type="error" showIcon message={text(order.error_kind, 'ORDER ERROR')} description={text(order.error_message)} /> : null}
    </Card>
  )
}

function CloneAssetPanel({ asset }: { asset: CloneAsset }) {
  const state = useWalletCloneStore((store) => store.assets[asset])
  const updateSettings = useWalletCloneStore((store) => store.updateSettings)
  const refresh = useWalletCloneStore((store) => store.refresh)
  const [form] = Form.useForm()
  const snapshot = state.snapshot
  const settings = row(snapshot?.settings)
  const rules = row(snapshot?.rules)
  const market = row(snapshot?.market)
  const pair = row(snapshot?.currentPair)
  const orders = row(snapshot?.orders)
  const books = row(snapshot?.books)
  const upOrder = row(orders.UP)
  const downOrder = row(orders.DOWN)
  const upBook = row(books.UP)
  const downBook = row(books.DOWN)
  const interlock = row(snapshot?.normalLiveInterlock)
  const exposure = row(snapshot?.currentMarketExposure)
  const spreadPreview = row(snapshot?.pairMakerSpreadPreview)
  const events = Array.isArray(snapshot?.recentEvents) ? snapshot?.recentEvents as RowObject[] : []

  const runtimeEnabled = settings.runtimeEnabled === true
  const masterEnabled = snapshot?.masterEnabled === true
  const riskStopLatched = settings.riskStopLatched === true
  const entryCount = num(snapshot?.currentMarketEntryCount) ?? 0
  const maximumEntryCount = num(settings.maximumEntryCount) ?? 3
  const maximumLossUsdt = num(settings.maximumLossUsdt) ?? 5
  const maximumCombinedMakerPrice = num(settings.maximumCombinedMakerPrice) ?? 0.98
  const noNewEntrySeconds = num(rules.noNewEntrySecondsBeforeEndV8) ?? 60
  const hardCancelSeconds = num(rules.hardCancelSecondsBeforeEndV8) ?? 30

  useEffect(() => {
    if (snapshot && !form.isFieldsTouched()) form.setFieldsValue(settingsFrom(snapshot))
  }, [snapshot, form])

  const save = async () => {
    try {
      const values = await form.validateFields()
      await updateSettings(asset, {
        targetPotentialProfitUsdt: Number(values.targetPotentialProfitUsdt),
        maximumEntryCount: Number(values.maximumEntryCount),
        maximumLossUsdt: Number(values.maximumLossUsdt),
        maximumCombinedMakerPrice: Number(values.maximumCombinedMakerPrice),
        bidOffsetTicks: Number(values.bidOffsetTicks),
        minimumOrderUsdt: Number(values.minimumOrderUsdt),
        maximumOrderUsdt: Number(values.maximumOrderUsdt),
        minimumRemainingSeconds: Number(values.minimumRemainingSeconds),
      })
      form.setFieldsValue(settingsFrom(useWalletCloneStore.getState().assets[asset].snapshot))
      message.success(`${asset} V8.4 參數已儲存`)
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const toggle = async () => {
    try {
      await updateSettings(asset, { runtimeEnabled: !runtimeEnabled })
      message.success(`${asset} Clone ${runtimeEnabled ? '已暫停並要求撤單' : '已啟用'}`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const resetRiskStop = async () => {
    try {
      await updateSettings(asset, { resetRiskStop: true })
      message.success(`${asset} Risk Stop 已重設，仍維持 Pause`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const eventColumns = [
    { title: '時間', dataIndex: 'at_ms', width: 88, render: clock },
    { title: '事件', dataIndex: 'event_type', width: 220, render: (value: unknown) => <Tag>{text(value)}</Tag> },
    { title: '內容', dataIndex: 'message', render: (value: unknown) => text(value) },
  ]

  return (
    <Card
      title={<Space><SwapOutlined /><strong>{asset} 5M Maker Clone</strong></Space>}
      extra={<Button size="small" icon={<ReloadOutlined />} onClick={() => void refresh()}>更新</Button>}
    >
      <Space wrap style={{ marginBottom: 12 }}>
        <Tag color={state.service.ok ? 'success' : 'error'}>{state.service.ok ? `API ${Math.round(state.service.latencyMs ?? 0)}ms` : 'OFFLINE'}</Tag>
        <Tag color={runtimeEnabled ? 'error' : 'default'}>{runtimeEnabled ? 'REAL-MONEY ON' : 'SAFE PAUSED'}</Tag>
        <Tag color="blue">{text(snapshot?.version, 'VERSION UNKNOWN')}</Tag>
        <Tag color={statusColor(snapshot?.status)}>{text(snapshot?.status)}</Tag>
      </Space>

      {!masterEnabled ? <Alert type="warning" showIcon message="Clone master switch 關閉" /> : null}
      {interlock.blocked === true ? <Alert type="error" showIcon message="Normal Live interlock" description={`${asset} 其他 Echtgeld engine 仍在運行或管理部位。`} style={{ marginTop: 8 }} /> : null}
      {riskStopLatched ? <Alert type="error" showIcon message={`最大虧損保護已觸發（$${maximumLossUsdt.toFixed(2)}）`} description={<Button danger size="small" onClick={() => void resetRiskStop()}>重設 Risk Stop（保持 Pause）</Button>} style={{ marginTop: 8 }} /> : null}
      {state.saveError ? <Alert type="error" showIcon message="設定寫入失敗" description={state.saveError} style={{ marginTop: 8 }} /> : null}

      <Alert
        type="info"
        showIcon
        style={{ marginTop: 12 }}
        message="V8.4 保留原本 Maker 掛單邏輯，不要求兩腿同時成交，也不改成等量 shares"
        description={`第一輪與後續輪都直接使用各自 current passive best bid（再套 bid offset）；只有 UP 掛單價 + DOWN 掛單價 > ${maximumCombinedMakerPrice.toFixed(4)} 時，才暫時不開這一對。這只是掛單品質 / spread gate，不是 locked-arbitrage 條件。`}
      />

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} md={12}>
          <Card size="small" title="Market / Risk">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Market">#{text(market.market_id)}</Descriptions.Item>
              <Descriptions.Item label="Remaining">{num(market.secondsLeft) === null ? '—' : `${Number(market.secondsLeft).toFixed(1)}s`}</Descriptions.Item>
              <Descriptions.Item label="Pair state"><Tag color={statusColor(pair.state)}>{text(pair.state)}</Tag></Descriptions.Item>
              <Descriptions.Item label="入場輪數">{entryCount} / {maximumEntryCount}</Descriptions.Item>
              <Descriptions.Item label="最差結算 PnL">{money(exposure.worstCasePnlUsdt)}</Descriptions.Item>
              <Descriptions.Item label="目前 worst-case loss">{money(exposure.worstCaseLossUsdt)} / ${maximumLossUsdt.toFixed(2)}</Descriptions.Item>
              <Descriptions.Item label="T-60 / T-30">新輪截止 {noNewEntrySeconds.toFixed(0)}s / 撤單 {hardCancelSeconds.toFixed(0)}s</Descriptions.Item>
              <Descriptions.Item label="Execution path">{text(snapshot?.executionPath)}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card size="small" title="Passive Maker Spread Gate">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="UP planned maker price">{fixed(spreadPreview.upPrice)}</Descriptions.Item>
              <Descriptions.Item label="DOWN planned maker price">{fixed(spreadPreview.downPrice)}</Descriptions.Item>
              <Descriptions.Item label="合計掛單價">{fixed(spreadPreview.combinedMakerPrice)} / max {maximumCombinedMakerPrice.toFixed(4)}</Descriptions.Item>
              <Descriptions.Item label="Spread cushion">{directPct(spreadPreview.makerSpreadCushionPct)}</Descriptions.Item>
              <Descriptions.Item label="Gate">{spreadPreview.passes === true ? <Tag color="success">PASS</Tag> : <Tag color="warning">BLOCK / WAIT</Tag>} {text(spreadPreview.reason, '')}</Descriptions.Item>
              <Descriptions.Item label="Shares sizing">各側維持原本 payoff-normalized sizing</Descriptions.Item>
              <Descriptions.Item label="是否保證套利">否；單側可獨立成交</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}><OrderCard side="UP" order={upOrder} book={upBook} /></Col>
        <Col xs={24} xl={12}><OrderCard side="DOWN" order={downOrder} book={downBook} /></Col>
      </Row>

      <Card size="small" title="Clone V8.4 參數" style={{ marginTop: 12 }}>
        <Form form={form} layout="vertical" initialValues={settingsFrom(snapshot)}>
          <Row gutter={[12, 0]}>
            <Col xs={12} md={6}><Form.Item name="targetPotentialProfitUsdt" label="每側目標潛在獲利 $"><InputNumber min={0.1} max={100} step={0.1} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="maximumCombinedMakerPrice" label="UP+DOWN 掛單價上限"><InputNumber min={0.01} max={1} step={0.01} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="maximumEntryCount" label="每市場最大雙邊輪數"><InputNumber min={1} max={50} step={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="maximumLossUsdt" label="最大虧損上限 $"><InputNumber min={0.01} max={10000} step={0.5} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Row gutter={[12, 0]}>
            <Col xs={12} md={6}><Form.Item name="minimumOrderUsdt" label="每側最低成本 $"><InputNumber min={0.01} max={1000} step={0.1} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="maximumOrderUsdt" label="每側最高成本 $"><InputNumber min={0.1} max={10000} step={1} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="bidOffsetTicks" label="Best Bid 下移 ticks"><InputNumber min={0} max={20} step={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="minimumRemainingSeconds" label="額外最少剩餘秒數"><InputNumber min={5} max={299} step={5} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Space wrap>
            <Button type="primary" loading={state.saving} onClick={() => void save()}>儲存 {asset} V8.4 參數</Button>
            <Popconfirm
              title={runtimeEnabled ? `暫停 ${asset} Clone 並撤 resting orders？` : `啟用 ${asset} Echtgeld Maker Clone？`}
              description={runtimeEnabled ? '已成交 shares 不會賣出。' : `UP+DOWN 掛單價合計必須 ≤ ${maximumCombinedMakerPrice.toFixed(2)}；不要求兩腿同時成交。`}
              onConfirm={() => void toggle()}
              okText="確認"
              cancelText="取消"
            >
              <Button danger={!runtimeEnabled} disabled={!state.service.ok || interlock.blocked === true || (!runtimeEnabled && riskStopLatched)}>
                {runtimeEnabled ? 'Pause + Cancel Resting' : 'Resume Passive Maker Echtgeld'}
              </Button>
            </Popconfirm>
          </Space>
        </Form>
      </Card>

      <Card size="small" title="最近 Order Lifecycle" style={{ marginTop: 12 }}>
        <Table<RowObject> size="small" rowKey={(event) => String(event.id ?? `${event.at_ms}-${event.event_type}`)} dataSource={events.slice(0, 12)} columns={eventColumns} pagination={false} scroll={{ x: 720 }} />
      </Card>
    </Card>
  )
}

export default function WalletClonePageV84() {
  const refresh = useWalletCloneStore((store) => store.refresh)

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      if (!cancelled && document.visibilityState === 'visible') void refresh()
    }
    tick()
    const timer = window.setInterval(tick, 1000)
    const visibility = () => tick()
    document.addEventListener('visibilitychange', visibility)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', visibility)
    }
  }, [refresh])

  return (
    <>
      <div className="page-heading">
        <div>
          <Title level={3}>Wallet Maker Clone V8.4</Title>
          <Text type="secondary">Passive best-bid quoting · 原 sizing 保留 · pair spread gate · T-60 / T-30 / max-loss safety</Text>
        </div>
        <Tag color="warning"><SafetyCertificateOutlined /> LOCALHOST WRITE ONLY</Tag>
      </div>
      <Alert
        type="warning"
        showIcon
        message="V8.4 不是雙腿鎖利套利"
        description="UP / DOWN 是兩張獨立 Maker 掛單；策略價值來自被動掛單與後續成交，而不是等待一組可立即鎖利的互補價格。合計價 gate 只用來避免 0.50/0.50 這類沒有 spread cushion 的報價。"
        style={{ marginBottom: 12 }}
      />
      <Row gutter={[12, 12]}>
        <Col xs={24} xxl={12}><CloneAssetPanel asset="ETH" /></Col>
        <Col xs={24} xxl={12}><CloneAssetPanel asset="BNB" /></Col>
      </Row>
    </>
  )
}
