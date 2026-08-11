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
  Switch,
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

function clock(value: unknown): string {
  const parsed = num(value)
  if (parsed === null || parsed <= 0) return '—'
  return new Date(parsed).toLocaleTimeString('zh-TW', { hour12: false })
}

function statusColor(value: unknown): string {
  const status = text(value, '').toUpperCase()
  if (['FILLED', 'BOTH_FILLED', 'RESTING', 'BOTH_RESTING', 'PAIR_ACTIVE'].includes(status)) return 'success'
  if (['PARTIAL_FILL', 'ONE_FILLED', 'PARTIAL_SUBMISSION', 'CANCELING', 'PLACING_BOTH'].includes(status)) return 'processing'
  if (['AMBIGUOUS', 'REJECTED', 'FAILED', 'BLOCKED_NORMAL_LIVE'].includes(status)) return 'error'
  if (['CANCELED', 'ROLLED', 'PAUSED', 'SAFE_PAUSED'].includes(status)) return 'default'
  return 'warning'
}

function settingsFrom(snapshot: RowObject | null) {
  const settings = row(snapshot?.settings)
  return {
    targetPotentialProfitUsdt: num(settings.targetPotentialProfitUsdt) ?? 1,
    bidOffsetTicks: num(settings.bidOffsetTicks) ?? 0,
    minimumOrderUsdt: num(settings.minimumOrderUsdt) ?? 1,
    maximumOrderUsdt: num(settings.maximumOrderUsdt) ?? 25,
    minimumRemainingSeconds: num(settings.minimumRemainingSeconds) ?? 30,
    autoRequote: settings.autoRequote === true,
    requoteTicks: num(settings.requoteTicks) ?? 2,
    maxOrderAgeSeconds: num(settings.maxOrderAgeSeconds) ?? 30,
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
  const events = Array.isArray(snapshot?.recentEvents) ? snapshot?.recentEvents as RowObject[] : []
  const runtimeEnabled = settings.runtimeEnabled === true
  const masterEnabled = snapshot?.masterEnabled === true

  useEffect(() => {
    if (snapshot && !form.isFieldsTouched()) form.setFieldsValue(settingsFrom(snapshot))
  }, [snapshot, form])

  const save = async () => {
    try {
      const values = await form.validateFields()
      await updateSettings(asset, {
        targetPotentialProfitUsdt: Number(values.targetPotentialProfitUsdt),
        bidOffsetTicks: Number(values.bidOffsetTicks),
        minimumOrderUsdt: Number(values.minimumOrderUsdt),
        maximumOrderUsdt: Number(values.maximumOrderUsdt),
        minimumRemainingSeconds: Number(values.minimumRemainingSeconds),
        autoRequote: Boolean(values.autoRequote),
        requoteTicks: Number(values.requoteTicks),
        maxOrderAgeSeconds: Number(values.maxOrderAgeSeconds),
      })
      form.setFieldsValue(settingsFrom(useWalletCloneStore.getState().assets[asset].snapshot))
      message.success(`${asset} Clone 參數已寫入獨立 SQLite`)
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

  const eventColumns = [
    { title: '時間', dataIndex: 'at_ms', width: 88, render: clock },
    { title: '事件', dataIndex: 'event_type', width: 180, render: (value: unknown) => <Tag>{text(value)}</Tag> },
    { title: '內容', dataIndex: 'message', render: text },
  ]

  return (
    <Card
      title={<Space><SwapOutlined /><strong>{asset} 5M Wallet Maker Clone</strong></Space>}
      extra={<Button size="small" icon={<ReloadOutlined />} onClick={() => void refresh()}>更新</Button>}
    >
      <Space wrap style={{ marginBottom: 12 }}>
        <Tag color={state.service.ok ? 'success' : 'error'}>{state.service.ok ? `API ${Math.round(state.service.latencyMs ?? 0)}ms` : 'OFFLINE'}</Tag>
        <Tag color={runtimeEnabled ? 'error' : 'default'}>{runtimeEnabled ? 'REAL-MONEY CLONE ON' : 'SAFE PAUSED'}</Tag>
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
      {state.saveError ? <Alert type="error" showIcon message="Clone 設定寫入失敗" description={state.saveError} style={{ marginTop: 8 }} /> : null}

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} md={12}>
          <Card size="small" title="Pair / Market">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Market">#{text(market.market_id)}</Descriptions.Item>
              <Descriptions.Item label="Remaining">{num(market.secondsLeft) === null ? '—' : `${Number(market.secondsLeft).toFixed(1)}s`}</Descriptions.Item>
              <Descriptions.Item label="Pair state"><Tag color={statusColor(pair.state)}>{text(pair.state)}</Tag></Descriptions.Item>
              <Descriptions.Item label="UP↔DOWN place skew">{amount(pair.pair_place_skew_ms)} ms</Descriptions.Item>
              <Descriptions.Item label="Filled cost">${amount(summary.filledCostUsdt)}</Descriptions.Item>
              <Descriptions.Item label="Inventory">UP {amount(summary.upFilledShares)} / DOWN {amount(summary.downFilledShares)}</Descriptions.Item>
              <Descriptions.Item label="Execution path">{text(snapshot?.executionPath)}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card size="small" title="Safety / Interlock">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Soft post-only">ON · 送單前二次 Ask 檢查</Descriptions.Item>
              <Descriptions.Item label="Normal live">{interlock.blocked === true ? 'BLOCKED' : text(interlock.reason, 'WAITING CHECK')}</Descriptions.Item>
              <Descriptions.Item label="Auto SELL">OFF · 已成交 shares 預設持有到結算</Descriptions.Item>
              <Descriptions.Item label="Pause behavior">撤銷本策略記錄的 resting orders</Descriptions.Item>
              <Descriptions.Item label="MINT / NORMAL">成交後另做 match reconciliation；此頁不猜</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}><OrderCard side="UP" order={upOrder} book={upBook} /></Col>
        <Col xs={24} xl={12}><OrderCard side="DOWN" order={downOrder} book={downBook} /></Col>
      </Row>

      <Card size="small" title="Clone V1 參數" style={{ marginTop: 12 }}>
        <Form form={form} layout="vertical" initialValues={settingsFrom(snapshot)}>
          <Row gutter={[12, 0]}>
            <Col xs={12} md={6}><Form.Item name="targetPotentialProfitUsdt" label="每側目標潛在獲利 $"><InputNumber min={0.1} max={100} step={0.1} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="bidOffsetTicks" label="Best Bid 下移 ticks"><InputNumber min={0} max={20} step={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="minimumOrderUsdt" label="每側最低成本 $"><InputNumber min={0.01} max={1000} step={0.1} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="maximumOrderUsdt" label="每側最高成本 $"><InputNumber min={0.1} max={10000} step={1} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Row gutter={[12, 0]}>
            <Col xs={12} md={6}><Form.Item name="minimumRemainingSeconds" label="最少剩餘秒數"><InputNumber min={5} max={299} step={5} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="autoRequote" label="Auto requote（V1 預設 OFF）" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="requoteTicks" label="Requote ticks"><InputNumber min={1} max={20} step={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="maxOrderAgeSeconds" label="最大掛單秒數"><InputNumber min={2} max={299} step={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Space wrap>
            <Button type="primary" loading={state.saving} onClick={() => void save()}>儲存 {asset} Clone 參數</Button>
            <Popconfirm
              title={runtimeEnabled ? `暫停 ${asset} Clone 並撤銷 resting orders？` : `啟用 ${asset} 雙邊 Echtgeld Clone？`}
              description={runtimeEnabled
                ? '只撤銷本 Clone DB 記錄的未完成訂單；已成交 shares 保留。'
                : '會同時送出 UP / DOWN LIMIT GTC；請先確認原本 Live engine 已 Pause。'}
              okText="確認"
              cancelText="取消"
              onConfirm={() => void toggle()}
            >
              <Button danger={!runtimeEnabled} disabled={!state.service.ok || interlock.blocked === true}>
                {runtimeEnabled ? 'Pause + Cancel Resting' : 'Resume Dual-Sided Echtgeld'}
              </Button>
            </Popconfirm>
          </Space>
        </Form>
      </Card>

      <Card size="small" title="最近 Order Lifecycle" style={{ marginTop: 12 }}>
        <Table
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
          <Title level={3}>Wallet Maker Clone</Title>
          <Text type="secondary">0x9ddb execution 模仿實驗 · ETH / BNB 5M · 雙側 Passive LIMIT/GTC · 實單流程可視化</Text>
        </div>
        <Tag color="warning"><SafetyCertificateOutlined /> LOCALHOST WRITE ONLY</Tag>
      </div>
      <Alert
        type="warning"
        showIcon
        message="Clone V1 是 execution replication，不是假設已破解方向訊號"
        description="每輪同時維護 UP / DOWN 被動單，先驗證成交、部分成交、MINT/normal 後續與 adverse selection。Normal ETH/BNB Live engine 與 Clone 有硬 interlock，不應同時運作。"
        style={{ marginBottom: 12 }}
      />
      <Row gutter={[12, 12]}>
        <Col xs={24} xxl={12}><CloneAssetPanel asset="ETH" /></Col>
        <Col xs={24} xxl={12}><CloneAssetPanel asset="BNB" /></Col>
      </Row>
    </>
  )
}
