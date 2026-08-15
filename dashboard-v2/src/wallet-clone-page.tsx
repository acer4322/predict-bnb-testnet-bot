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

function price(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : parsed.toFixed(4)
}

function amount(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : parsed.toFixed(4)
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
  if (['FILLED', 'BOTH_FILLED', 'RESTING', 'BOTH_RESTING', 'PAIR_ACTIVE'].includes(status)) return 'success'
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
    maximumCombinedPrice: num(settings.maximumCombinedPrice) ?? 0.98,
    minimumLockedReturnPct: num(settings.minimumLockedReturnPct) ?? 1,
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
      title={<Space><Tag color={side === 'UP' ? 'success' : 'error'}>{side}</Tag><span>Passive LIMIT/GTC</span></Space>}
      extra={<Tag color={statusColor(state)}>{state}</Tag>}
    >
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Generation">{text(order.generation)}</Descriptions.Item>
        <Descriptions.Item label="Best Bid / Ask">{price(book.bestBid)} / {price(book.bestAsk)}</Descriptions.Item>
        <Descriptions.Item label="Target price">{price(order.target_price)}</Descriptions.Item>
        <Descriptions.Item label="Potential-profit target">${amount(order.target_profit_usdt)}</Descriptions.Item>
        <Descriptions.Item label="Planned cost / shares">${amount(order.planned_cost_usdt)} / {amount(order.planned_shares)}</Descriptions.Item>
        <Descriptions.Item label="Filled cost / shares">${amount(order.filled_usdt_amount)} / {amount(order.filled_share_qty)}</Descriptions.Item>
        <Descriptions.Item label="Fill">{pct(order.fill_percentage)}</Descriptions.Item>
        <Descriptions.Item label="Exchange status">{text(order.order_status)}</Descriptions.Item>
        <Descriptions.Item label="Order ID"><Text copyable={Boolean(order.order_id)}>{text(order.order_id)}</Text></Descriptions.Item>
        <Descriptions.Item label="Quote RTT / Place RTT">{amount(order.quote_rtt_ms)} / {amount(order.place_rtt_ms)} ms</Descriptions.Item>
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
  const summary = row(snapshot?.summary)
  const exposure = row(snapshot?.currentMarketExposure)
  const pairEdge = row(snapshot?.pairLockedEdgePreview)
  const events = Array.isArray(snapshot?.recentEvents) ? snapshot?.recentEvents as RowObject[] : []
  const runtimeEnabled = settings.runtimeEnabled === true
  const masterEnabled = snapshot?.masterEnabled === true
  const riskStopLatched = settings.riskStopLatched === true
  const entryCount = num(snapshot?.currentMarketEntryCount) ?? 0
  const maximumEntryCount = num(settings.maximumEntryCount) ?? 3
  const maximumLossUsdt = num(settings.maximumLossUsdt) ?? 5
  const maximumCombinedPrice = num(settings.maximumCombinedPrice) ?? 0.98
  const minimumLockedReturnPct = num(settings.minimumLockedReturnPct) ?? 1
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
        maximumCombinedPrice: Number(values.maximumCombinedPrice),
        minimumLockedReturnPct: Number(values.minimumLockedReturnPct),
        bidOffsetTicks: Number(values.bidOffsetTicks),
        minimumOrderUsdt: Number(values.minimumOrderUsdt),
        maximumOrderUsdt: Number(values.maximumOrderUsdt),
        minimumRemainingSeconds: Number(values.minimumRemainingSeconds),
      })
      form.setFieldsValue(settingsFrom(useWalletCloneStore.getState().assets[asset].snapshot))
      message.success(`${asset} Clone V8.3 參數已寫入獨立 SQLite`)
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const toggle = async () => {
    try {
      await updateSettings(asset, { runtimeEnabled: !runtimeEnabled })
      message.success(`${asset} Clone ${runtimeEnabled ? '已暫停並要求撤銷 resting orders' : '已啟用雙邊實單'}`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const resetRiskStop = async () => {
    try {
      await updateSettings(asset, { resetRiskStop: true })
      message.success(`${asset} 最大虧損鎖已重設；仍維持 Pause，請確認後再 Resume`)
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
      title={<Space><SwapOutlined /><strong>{asset} 5M Wallet Maker Clone</strong></Space>}
      extra={<Button size="small" icon={<ReloadOutlined />} onClick={() => void refresh()}>更新</Button>}
    >
      <Space wrap style={{ marginBottom: 12 }}>
        <Tag color={state.service.ok ? 'success' : 'error'}>{state.service.ok ? `API ${Math.round(state.service.latencyMs ?? 0)}ms` : 'OFFLINE'}</Tag>
        <Tag color={runtimeEnabled ? 'error' : 'default'}>{runtimeEnabled ? 'REAL-MONEY CLONE ON' : 'SAFE PAUSED'}</Tag>
        <Tag color="blue">{text(snapshot?.version, 'VERSION UNKNOWN')}</Tag>
        <Tag color={statusColor(snapshot?.status)}>{text(snapshot?.status)}</Tag>
        <Tag color={statusColor(pair.state)}>{text(pair.state, 'NO PAIR')}</Tag>
      </Space>

      {!masterEnabled ? <Alert type="warning" showIcon message="Clone master switch 關閉" description={`PREDICT_${asset}_WALLET_MAKER_CLONE_ENABLED=false；服務可觀測但不能 Resume Echtgeld。`} /> : null}
      {interlock.blocked === true ? (
        <Alert
          type="error"
          showIcon
          message="Normal Live interlock"
          description={`${asset} 原本 live engine 仍為 ON 或正在管理持倉，Clone 不允許同時啟用。`}
          style={{ marginTop: 8 }}
        />
      ) : null}
      {riskStopLatched ? (
        <Alert
          type="error"
          showIcon
          message={`V8 最大虧損保護已觸發（上限 $${maximumLossUsdt.toFixed(2)}）`}
          description={<Space direction="vertical"><span>{text(snapshot?.riskStopReason, '已停止新單並要求撤單')}</span><Button danger size="small" onClick={() => void resetRiskStop()}>重設 Risk Stop（保持 Pause）</Button></Space>}
          style={{ marginTop: 8 }}
        />
      ) : null}
      {state.saveError ? <Alert type="error" showIcon message="Clone 設定寫入失敗" description={state.saveError} style={{ marginTop: 8 }} /> : null}

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} md={12}>
          <Card size="small" title="Pair / Market">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Market">#{text(market.market_id)}</Descriptions.Item>
              <Descriptions.Item label="Remaining">{num(market.secondsLeft) === null ? '—' : `${Number(market.secondsLeft).toFixed(1)}s`}</Descriptions.Item>
              <Descriptions.Item label="Pair state"><Tag color={statusColor(pair.state)}>{text(pair.state)}</Tag></Descriptions.Item>
              <Descriptions.Item label="入場輪數">{entryCount} / {maximumEntryCount}</Descriptions.Item>
              <Descriptions.Item label="UP↔DOWN place skew">{amount(pair.pair_place_skew_ms)} ms</Descriptions.Item>
              <Descriptions.Item label="Filled cost">${amount(summary.filledCostUsdt)}</Descriptions.Item>
              <Descriptions.Item label="Inventory">UP {amount(summary.upFilledShares)} / DOWN {amount(summary.downFilledShares)}</Descriptions.Item>
              <Descriptions.Item label="Execution path">{text(snapshot?.executionPath)}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card size="small" title="V8.3 Safety / Pair Edge">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="最大目前虧損">${amount(exposure.worstCaseLossUsdt)} / ${maximumLossUsdt.toFixed(2)}</Descriptions.Item>
              <Descriptions.Item label="最差結算 PnL">${amount(exposure.worstCasePnlUsdt)}</Descriptions.Item>
              <Descriptions.Item label="目前 Pair 合計價">{price(pairEdge.combinedPrice)} / max {maximumCombinedPrice.toFixed(4)}</Descriptions.Item>
              <Descriptions.Item label="預估鎖定收益率">{directPct(pairEdge.lockedReturnPct)} / min {minimumLockedReturnPct.toFixed(2)}%</Descriptions.Item>
              <Descriptions.Item label="等量 shares">{amount(pairEdge.equalShares)}</Descriptions.Item>
              <Descriptions.Item label="Pair gate">{pairEdge.passes === true ? <Tag color="success">PASS</Tag> : <Tag color="warning">BLOCK / WAIT</Tag>} {text(pairEdge.reason, '')}</Descriptions.Item>
              <Descriptions.Item label="T-60 新單截止">剩餘 ≤ {noNewEntrySeconds.toFixed(0)}s 絕不建立下一輪</Descriptions.Item>
              <Descriptions.Item label="T-30 強制撤單">剩餘 ≤ {hardCancelSeconds.toFixed(0)}s 撤銷所有未完成 Clone orders</Descriptions.Item>
              <Descriptions.Item label="下一輪條件">上一輪 UP + DOWN 都確認 FILLED 才能送下一對</Descriptions.Item>
              <Descriptions.Item label="Risk stop">{riskStopLatched ? 'LATCHED · 已停止' : 'ARMED'}</Descriptions.Item>
              <Descriptions.Item label="Normal live">{interlock.blocked === true ? 'BLOCKED' : text(interlock.reason, 'WAITING CHECK')}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Alert
        type="info"
        showIcon
        style={{ marginTop: 12 }}
        message={`V8.3：只有等量 UP/DOWN shares 且合計價 ≤ ${maximumCombinedPrice.toFixed(4)}、鎖定收益率 ≥ ${minimumLockedReturnPct.toFixed(2)}% 才送單`}
        description={`0.50/0.50 這種零 edge pair 會被直接阻擋。固定時間規則仍為 T-${noNewEntrySeconds.toFixed(0)} 停止新一輪、T-${hardCancelSeconds.toFixed(0)} 撤銷未完成掛單；CANCEL_REQUEST_ACCEPTED 不視為最終取消，仍會 final reconciliation。`}
      />

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}><OrderCard side="UP" order={upOrder} book={upBook} /></Col>
        <Col xs={24} xl={12}><OrderCard side="DOWN" order={downOrder} book={downBook} /></Col>
      </Row>

      <Card size="small" title="Clone V8.3 參數" style={{ marginTop: 12 }}>
        <Form form={form} layout="vertical" initialValues={settingsFrom(snapshot)}>
          <Row gutter={[12, 0]}>
            <Col xs={12} md={6}><Form.Item name="targetPotentialProfitUsdt" label="每側目標潛在獲利 $"><InputNumber min={0.1} max={100} step={0.1} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="maximumEntryCount" label="每市場最大雙邊入場輪數"><InputNumber min={1} max={50} step={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="maximumLossUsdt" label="最大虧損上限 $"><InputNumber min={0.01} max={10000} step={0.5} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="maximumOrderUsdt" label="每側最高成本 $"><InputNumber min={1} max={10000} step={1} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Row gutter={[12, 0]}>
            <Col xs={12} md={6}><Form.Item name="maximumCombinedPrice" label="最大互補合計價格"><InputNumber min={0.01} max={1} step={0.01} precision={4} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="minimumLockedReturnPct" label="最低鎖定收益率 %"><InputNumber min={0} max={100} step={0.25} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="minimumOrderUsdt" label="每側最低成本 $（Binance ≥1）"><InputNumber min={1} max={1000} step={0.1} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="bidOffsetTicks" label="Best Bid 下移 ticks"><InputNumber min={0} max={20} step={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Row gutter={[12, 0]}>
            <Col xs={12} md={6}>
              <Form.Item name="minimumRemainingSeconds" label="額外最少剩餘秒數">
                <InputNumber min={5} max={299} step={5} precision={0} style={{ width: '100%' }} />
              </Form.Item>
              <Text type="secondary" style={{ fontSize: 12 }}>V8 固定 T-60 禁止新單；此值只有設為 &gt;60 時才會更嚴格。</Text>
            </Col>
          </Row>
          <Space wrap style={{ marginTop: 8 }}>
            <Button type="primary" loading={state.saving} onClick={() => void save()}>儲存 {asset} V8.3 參數</Button>
            <Popconfirm
              title={runtimeEnabled ? `暫停 ${asset} Clone 並撤銷 resting orders？` : `啟用 ${asset} 雙邊 Echtgeld Clone？`}
              description={runtimeEnabled
                ? '只撤銷本 Clone DB 記錄的未完成訂單；已成交 shares 保留。'
                : `Pair gate：合計價 ≤ ${maximumCombinedPrice.toFixed(4)}、鎖定收益率 ≥ ${minimumLockedReturnPct.toFixed(2)}%；最多 ${maximumEntryCount} 輪；T-${noNewEntrySeconds.toFixed(0)} 後不再新進場。`}
              okText="確認"
              cancelText="取消"
              onConfirm={() => void toggle()}
            >
              <Button danger={!runtimeEnabled} disabled={!state.service.ok || interlock.blocked === true || (!runtimeEnabled && riskStopLatched)}>
                {runtimeEnabled ? 'Pause + Cancel Resting' : 'Resume Pair Locked-Edge Echtgeld'}
              </Button>
            </Popconfirm>
            {riskStopLatched ? <Button danger onClick={() => void resetRiskStop()}>重設最大虧損鎖</Button> : null}
          </Space>
        </Form>
      </Card>

      <Card size="small" title="最近 Order Lifecycle" style={{ marginTop: 12 }}>
        <Table<RowObject>
          size="small"
          rowKey={(event) => String(event.id ?? `${event.at_ms}-${event.event_type}`)}
          dataSource={events.slice(0, 12)}
          columns={eventColumns}
          pagination={false}
          scroll={{ x: 720 }}
        />
      </Card>
    </Card>
  )
}

export default function WalletClonePage() {
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
          <Title level={3}>Wallet Maker Clone V8.3</Title>
          <Text type="secondary">Equal-share pair locked-edge Echtgeld · 最大合計價 / 最低鎖定收益率 · 最大輪數 / 最大虧損 · T-60 / T-30</Text>
        </div>
        <Tag color="warning"><SafetyCertificateOutlined /> LOCALHOST WRITE ONLY</Tag>
      </div>
      <Alert
        type="warning"
        showIcon
        message="V8.3 會阻擋 0.50/0.50 零 edge pair"
        description="每一輪先把 UP / DOWN 調整成相同 shares，再用實際 planned cost 計算 worst-case locked PnL。只有合計價格與最低鎖定收益率都過關才會同時送出兩張 Passive LIMIT；上一輪兩側都確認 FILLED 才能進下一輪。"
        style={{ marginBottom: 12 }}
      />
      <Row gutter={[12, 12]}>
        <Col xs={24} xxl={12}><CloneAssetPanel asset="ETH" /></Col>
        <Col xs={24} xxl={12}><CloneAssetPanel asset="BNB" /></Col>
      </Row>
    </>
  )
}
