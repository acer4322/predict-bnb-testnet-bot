import { useEffect } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { DollarOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { useLiveMarketsStore, type LiveAsset } from './live-markets-store'
import './live-markets.css'

const { Title, Text } = Typography

type RowObject = Record<string, unknown>

function row(value: unknown): RowObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
}

function valueAt(source: unknown, ...path: string[]): unknown {
  let current: unknown = source
  for (const key of path) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) return undefined
    current = (current as RowObject)[key]
  }
  return current
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function num(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function money(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${parsed >= 0 ? '+' : ''}$${parsed.toFixed(4)}`
}

function price(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : parsed.toFixed(4)
}

function serviceColor(ok: boolean) {
  return ok ? 'success' : 'error'
}

function settingsFrom(snapshot: RowObject | null) {
  const settings = row(snapshot?.settings)
  const levels = settings.shotgunLevels
  return {
    stakeUsdt: num(settings.stakeUsdt) ?? 1,
    maximumLossEnabled: settings.maximumLossEnabled !== false,
    maximumLossUsdt: num(settings.maximumLossUsdt) ?? 10,
    maxEntryPrice: num(settings.maxEntryPrice) ?? 0.9,
    takeProfitPrice: num(settings.takeProfitPrice) ?? 0.95,
    sameMarketReversalExitThreshold: num(settings.sameMarketReversalExitThreshold) ?? 3,
    shotgunEnabled: settings.shotgunEnabled === true,
    shotgunMinPrice: num(settings.shotgunMinPrice) ?? 0.05,
    shotgunMaxPrice: num(settings.shotgunMaxPrice) ?? 0.4,
    shotgunLevels: Array.isArray(levels) ? levels.join(', ') : text(levels, '0.05, 0.10, 0.20, 0.30, 0.40'),
    shotgunOrderUsdt: num(settings.shotgunOrderUsdt) ?? 1,
    leaderGuardMode: text(settings.leaderGuardMode, 'OFF'),
  }
}

function AssetSettings({ asset }: { asset: LiveAsset }) {
  const state = useLiveMarketsStore((store) => store.assets[asset])
  const updateSettings = useLiveMarketsStore((store) => store.updateSettings)
  const refresh = useLiveMarketsStore((store) => store.refresh)
  const [form] = Form.useForm()
  const snapshot = state.snapshot
  const settings = row(snapshot?.settings)
  const summary = row(snapshot?.summary)
  const loss = row(snapshot?.lossGuard)
  const active = row(snapshot?.activeRound)
  const market = row(snapshot?.market)
  const decision = row(snapshot?.runtimeDecisionV44)
  const isolation = row(snapshot?.assetIsolationV1)
  const runtimeEnabled = settings.runtimeEnabled === true
  const masterDisabled = text(snapshot?.status).includes('MASTER_DISABLED')

  useEffect(() => {
    if (snapshot && !form.isFieldsTouched()) form.setFieldsValue(settingsFrom(snapshot))
  }, [snapshot, form])

  const save = async () => {
    try {
      const values = await form.validateFields()
      const payload: Record<string, unknown> = {
        stakeUsdt: Number(values.stakeUsdt),
        maximumLossEnabled: Boolean(values.maximumLossEnabled),
        maximumLossUsdt: Number(values.maximumLossUsdt),
        maxEntryPrice: Number(values.maxEntryPrice),
        takeProfitPrice: Number(values.takeProfitPrice),
        sameMarketReversalExitThreshold: Number(values.sameMarketReversalExitThreshold),
        shotgunEnabled: Boolean(values.shotgunEnabled),
        shotgunMinPrice: Number(values.shotgunMinPrice),
        shotgunMaxPrice: Number(values.shotgunMaxPrice),
        shotgunLevels: String(values.shotgunLevels || ''),
        shotgunOrderUsdt: Number(values.shotgunOrderUsdt),
      }
      if (asset === 'BTC') payload.leaderGuardMode = values.leaderGuardMode
      await updateSettings(asset, payload)
      form.setFieldsValue(settingsFrom(useLiveMarketsStore.getState().assets[asset].snapshot))
      message.success(`${asset} Echtgeld 參數已寫入 engine SQLite`)
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const toggleRuntime = async () => {
    try {
      await updateSettings(asset, { runtimeEnabled: !runtimeEnabled })
      message.success(`${asset} 新進場 ${runtimeEnabled ? '已暫停' : '已啟用'}`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  const resetLoss = async () => {
    try {
      await updateSettings(asset, { resetLoss: true })
      message.success(`${asset} maximum-loss counter 已重設`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <div className="live-asset-pane">
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={8}>
          <Card title={<Space><SafetyCertificateOutlined /> {asset} Echtgeld狀態</Space>}>
            <Space wrap className="live-market-tags">
              <Tag color={serviceColor(state.service.ok)}>{state.service.ok ? `LIVE API ${Math.round(state.service.latencyMs ?? 0)}ms` : 'API OFFLINE'}</Tag>
              <Tag color={runtimeEnabled ? 'success' : 'default'}>{runtimeEnabled ? 'NEW ENTRY ON' : 'NEW ENTRY PAUSED'}</Tag>
              <Tag>{text(snapshot?.version)}</Tag>
            </Space>
            <Descriptions column={1} size="small" className="live-market-descriptions">
              <Descriptions.Item label="Market">#{text(market.market_id ?? market.marketId)}</Descriptions.Item>
              <Descriptions.Item label="Engine status">{text(snapshot?.status)}</Descriptions.Item>
              <Descriptions.Item label="Decision">{text(decision.state, text(snapshot?.status))}</Descriptions.Item>
              <Descriptions.Item label="Block code">{text(decision.code)}</Descriptions.Item>
              <Descriptions.Item label="Position">{Object.keys(active).length ? `${text(active.side)} · ${price(active.shares)} shares` : 'FLAT'}</Descriptions.Item>
              <Descriptions.Item label="Realized PnL">{money(summary.pnlUsdt)}</Descriptions.Item>
              <Descriptions.Item label="Win rate">{num(summary.winRate) === null ? '—' : `${(Number(summary.winRate) * 100).toFixed(1)}%`}</Descriptions.Item>
            </Descriptions>
            {asset !== 'BTC' ? (
              <Alert
                type="info"
                showIcon
                message="跨資產隔離"
                description={`此 ${asset} engine 使用自己的 Binance market、SQLite、round/lock/audit；BTC Paper CHOP 與 BTC Leader regime 不會控制 ${asset}。`}
              />
            ) : null}
          </Card>
        </Col>

        <Col xs={24} xl={16}>
          <Card
            title={<Space><DollarOutlined /> {asset} 5M Poly 多頻實單參數</Space>}
            extra={<Button icon={<ReloadOutlined />} onClick={() => void refresh()}>重新讀取</Button>}
          >
            {!state.service.ok ? <Alert type="error" showIcon message={`${asset} live engine 無法連線`} description={state.service.error || '等待服務啟動'} /> : null}
            {state.saveError ? <Alert className="live-control-alert" type="error" showIcon message="設定寫入失敗" description={state.saveError} /> : null}
            {masterDisabled ? (
              <Alert
                className="live-control-alert"
                type="warning"
                showIcon
                message={`${asset} Echtgeld master switch 尚未開啟`}
                description={asset === 'BTC'
                  ? '需要 PREDICT_POLY_GAP_LIVE_ENABLED=true；參數仍可先儲存。'
                  : `需要 PREDICT_${asset}_POLY_GAP_LIVE_ENABLED=true；在 master switch 開啟以前 runtime 無法 Resume。`}
              />
            ) : null}

            <Form form={form} layout="vertical" initialValues={settingsFrom(snapshot)} className="live-settings-form">
              <Row gutter={[16, 0]}>
                <Col xs={24} md={8}>
                  <Form.Item name="stakeUsdt" label="單筆 Stake (USDT)" rules={[{ required: true }]}>
                    <InputNumber min={1} max={100} step={1} precision={2} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item name="maxEntryPrice" label="最大進場價格" dependencies={['takeProfitPrice']} rules={[
                    { required: true },
                    ({ getFieldValue }) => ({
                      validator(_, value) {
                        if (Number(value) < Number(getFieldValue('takeProfitPrice'))) return Promise.resolve()
                        return Promise.reject(new Error('最大進場價格必須低於止盈價格'))
                      },
                    }),
                  ]}>
                    <InputNumber min={0.01} max={0.98} step={0.01} precision={3} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item name="takeProfitPrice" label="止盈價格（持倉同側 Bid）" rules={[{ required: true }]}>
                    <InputNumber min={0.02} max={0.99} step={0.01} precision={3} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={[16, 0]}>
                <Col xs={24} md={8}>
                  <Form.Item name="maximumLossEnabled" label="最大虧損保護" valuePropName="checked">
                    <Switch checkedChildren="ON" unCheckedChildren="OFF" />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item name="maximumLossUsdt" label="累積最大虧損 (USDT)" rules={[{ required: true }]}>
                    <InputNumber min={0.01} max={1000000} step={1} precision={2} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item name="sameMarketReversalExitThreshold" label="同局反轉出場上限" rules={[{ required: true }]}>
                    <InputNumber min={1} max={20} step={1} precision={0} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Card size="small" title="Shotgun 進場" className="embedded-settings-card">
                <Row gutter={[16, 0]}>
                  <Col xs={24} md={6}>
                    <Form.Item name="shotgunEnabled" label="啟用" valuePropName="checked">
                      <Switch checkedChildren="ON" unCheckedChildren="OFF" />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={6}>
                    <Form.Item name="shotgunMinPrice" label="最低價格"><InputNumber min={0.01} max={0.99} step={0.01} precision={3} style={{ width: '100%' }} /></Form.Item>
                  </Col>
                  <Col xs={12} md={6}>
                    <Form.Item name="shotgunMaxPrice" label="最高價格"><InputNumber min={0.01} max={0.99} step={0.01} precision={3} style={{ width: '100%' }} /></Form.Item>
                  </Col>
                  <Col xs={24} md={6}>
                    <Form.Item name="shotgunOrderUsdt" label="每層金額"><InputNumber min={1} max={100} step={1} precision={2} style={{ width: '100%' }} /></Form.Item>
                  </Col>
                </Row>
                <Form.Item name="shotgunLevels" label="價格層（逗號分隔，最多 5 層）">
                  <Input placeholder="0.05, 0.10, 0.20, 0.30, 0.40" />
                </Form.Item>
                <Text type="secondary">既有規則維持：觸發反轉／TP 不會自動取消先前已送出的 resting Shotgun GTC。</Text>
              </Card>

              <Card size="small" title="Leader Guard" className="embedded-settings-card">
                {asset === 'BTC' ? (
                  <Form.Item name="leaderGuardMode" label="BTC Poly/Binance leader policy">
                    <Select options={[
                      { value: 'OFF', label: 'OFF' },
                      { value: 'POLY_ONLY_ENTRY', label: '只有 POLY_LEADING 才進場' },
                      { value: 'POLY_ONLY_ENTRY_AND_KILL', label: 'Binance/Mixed → 出場並鎖當局' },
                    ]} />
                  </Form.Item>
                ) : (
                  <Alert type="info" showIcon message={`${asset} Leader Guard 固定 OFF`} description="目前 8768 leader research 是 BTC 專屬；正式建立 ETH/BNB 各自 lead-validation 前，不允許拿 BTC regime 控制其他資產。" />
                )}
              </Card>

              <Alert
                className="live-control-alert"
                type="info"
                showIcon
                message="目前沿用 BTC 的止盈／止損性保護"
                description="止盈＝持倉同側 Binance Bid 達 takeProfitPrice；止損性退出＝fresh opposite Poly 反轉立即 SELL；另有累積 maximum-loss guard。現行 V44 沒有額外的固定 Bid stop-loss 價格。"
              />

              <Space wrap>
                <Button type="primary" loading={state.saving} onClick={() => void save()}>儲存 {asset} 參數</Button>
                <Popconfirm
                  title={runtimeEnabled ? `暫停 ${asset} 新進場？` : `啟用 ${asset} Echtgeld 新進場？`}
                  description={runtimeEnabled ? '既有持倉仍由 engine 管理與退出。' : `確認使用目前 ${asset} 參數開始 Echtgeld BUY。`}
                  okText="確認"
                  cancelText="取消"
                  onConfirm={() => void toggleRuntime()}
                >
                  <Button danger={!runtimeEnabled} disabled={!state.service.ok}>
                    {runtimeEnabled ? 'Pause New Entry' : 'Resume Echtgeld'}
                  </Button>
                </Popconfirm>
                <Popconfirm title={`重設 ${asset} maximum-loss counter？`} okText="重設" cancelText="取消" onConfirm={() => void resetLoss()}>
                  <Button>Reset Loss Counter</Button>
                </Popconfirm>
              </Space>
            </Form>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} className="live-risk-row">
        <Col xs={24} lg={8}>
          <Card size="small" title="風控摘要">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Loss tripped"><Tag color={settings.lossTripped === true ? 'error' : 'success'}>{settings.lossTripped === true ? 'TRIPPED' : 'OK'}</Tag></Descriptions.Item>
              <Descriptions.Item label="Maximum loss">{price(settings.maximumLossUsdt)} USDT</Descriptions.Item>
              <Descriptions.Item label="Take profit">{price(settings.takeProfitPrice)}</Descriptions.Item>
              <Descriptions.Item label="Max entry">{price(settings.maxEntryPrice)}</Descriptions.Item>
              <Descriptions.Item label="Minimum edge">{price(settings.minimumEdge)}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card size="small" title="V40 / V42">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Immediate reversal SELL">ON</Descriptions.Item>
              <Descriptions.Item label="Re-entry confirm">{text(valueAt(snapshot, 'rules', 'reversalReentryConfirmMs'), '2000')} ms</Descriptions.Item>
              <Descriptions.Item label="Re-entry min edge">{price(valueAt(snapshot, 'rules', 'reversalReentryMinimumEdge') ?? valueAt(snapshot, 'rules', 'reversalReentryMinEdge'))}</Descriptions.Item>
              <Descriptions.Item label="TP same-market lock">ON</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card size="small" title="Asset isolation">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Symbol">{text(snapshot?.symbol, asset === 'BTC' ? 'BTCUSDT' : `${asset}USDT`)}</Descriptions.Item>
              <Descriptions.Item label="Own DB">{asset === 'BTC' ? 'BTC existing DB' : text(isolation.databaseIsolated, 'true')}</Descriptions.Item>
              <Descriptions.Item label="BTC Paper guard">{asset === 'BTC' ? 'ACTIVE' : 'NOT APPLIED'}</Descriptions.Item>
              <Descriptions.Item label="BTC Leader regime">{asset === 'BTC' ? text(settings.leaderGuardMode, 'OFF') : 'NOT APPLIED'}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>
    </div>
  )
}

export default function LiveMarketsPage() {
  const refresh = useLiveMarketsStore((store) => store.refresh)

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
          <Title level={3}>Live Markets</Title>
          <Text type="secondary">BTC / ETH / BNB 5M · Polymarket signal → Binance Prediction execution。每個資產使用獨立 live state 與設定。</Text>
        </div>
        <Tag color="warning">LOCALHOST WRITE ONLY</Tag>
      </div>
      <Alert
        type="warning"
        showIcon
        message="Echtgeld 控制只允許從這台電腦操作"
        description="Dashboard V2 從 LAN 開啟時仍可查看行情與狀態，但 /control 寫入需要 loopback + 本次 Vite session token；不再沿用舊 Dashboard 的 LAN settings proxy。"
      />
      <Tabs
        className="live-market-tabs"
        defaultActiveKey="BTC"
        items={(['BTC', 'ETH', 'BNB'] as LiveAsset[]).map((asset) => ({
          key: asset,
          label: asset,
          children: <AssetSettings asset={asset} />,
        }))}
      />
    </>
  )
}
