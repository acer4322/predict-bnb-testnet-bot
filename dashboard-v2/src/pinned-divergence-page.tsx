import { useEffect } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  InputNumber,
  Row,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { AimOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { useLiveMarketsStore, type LiveAsset } from './live-markets-store'

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

function ratio(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

function price(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : parsed.toFixed(4)
}

function ms(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${Math.round(parsed)} ms`
}

function strategyForm(snapshot: RowObject | null) {
  const settings = row(snapshot?.settings)
  return {
    entryStrategyMode: text(settings.entryStrategyMode, 'POLY_GAP'),
    pinnedBinanceCenter: num(settings.pinnedBinanceCenter) ?? 0.50,
    pinnedBinanceHalfWidth: num(settings.pinnedBinanceHalfWidth) ?? 0.03,
    pinnedMinimumDurationSeconds: num(settings.pinnedMinimumDurationSeconds) ?? 5,
    pinnedMaximumRange: num(settings.pinnedMaximumRange) ?? 0.04,
    pinnedRequiredRatio: num(settings.pinnedRequiredRatio) ?? 0.80,
    pinnedPolyThreshold: num(settings.pinnedPolyThreshold) ?? 0.85,
    pinnedMinimumGap: num(settings.pinnedMinimumGap) ?? 0.30,
    pinnedMinimumRemainingSeconds: num(settings.pinnedMinimumRemainingSeconds) ?? 20,
    pinnedMaximumSelectedAsk: num(settings.pinnedMaximumSelectedAsk) ?? 0.60,
    pinnedOneEntryPerMarket: settings.pinnedOneEntryPerMarket !== false,
  }
}

function stateColor(state: string) {
  if (state === 'ARMED') return 'success'
  if (state.startsWith('BLOCKED')) return 'error'
  if (state === 'PINNING') return 'processing'
  return 'default'
}

function AssetPinnedPanel({ asset }: { asset: LiveAsset }) {
  const live = useLiveMarketsStore((store) => store.assets[asset])
  const updateSettings = useLiveMarketsStore((store) => store.updateSettings)
  const refresh = useLiveMarketsStore((store) => store.refresh)
  const [form] = Form.useForm()
  const snapshot = live.snapshot
  const settings = row(snapshot?.settings)
  const pinned = row(snapshot?.pinnedDivergence)
  const evaluation = row(pinned.lastEvaluation)
  const market = row(snapshot?.market)
  const selected = text(settings.entryStrategyMode, 'POLY_GAP') === 'PINNED_DIVERGENCE'

  useEffect(() => {
    if (snapshot && !form.isFieldsTouched()) form.setFieldsValue(strategyForm(snapshot))
  }, [snapshot, form])

  const save = async () => {
    try {
      const values = await form.validateFields()
      await updateSettings(asset, {
        entryStrategyMode: values.entryStrategyMode,
        pinnedBinanceCenter: Number(values.pinnedBinanceCenter),
        pinnedBinanceHalfWidth: Number(values.pinnedBinanceHalfWidth),
        pinnedMinimumDurationSeconds: Number(values.pinnedMinimumDurationSeconds),
        pinnedMaximumRange: Number(values.pinnedMaximumRange),
        pinnedRequiredRatio: Number(values.pinnedRequiredRatio),
        pinnedPolyThreshold: Number(values.pinnedPolyThreshold),
        pinnedMinimumGap: Number(values.pinnedMinimumGap),
        pinnedMinimumRemainingSeconds: Number(values.pinnedMinimumRemainingSeconds),
        pinnedMaximumSelectedAsk: Number(values.pinnedMaximumSelectedAsk),
        pinnedOneEntryPerMarket: Boolean(values.pinnedOneEntryPerMarket),
      })
      form.setFieldsValue(strategyForm(useLiveMarketsStore.getState().assets[asset].snapshot))
      message.success(`${asset} Pinned Divergence 參數已寫入 engine SQLite`)
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={8}>
          <Card title={<Space><AimOutlined /> {asset} 即時偵測</Space>}>
            <Space wrap style={{ marginBottom: 12 }}>
              <Tag color={live.service.ok ? 'success' : 'error'}>{live.service.ok ? 'ENGINE ONLINE' : 'ENGINE OFFLINE'}</Tag>
              <Tag color={selected ? 'purple' : 'default'}>{selected ? 'PINNED STRATEGY SELECTED' : 'POLY GAP SELECTED'}</Tag>
              <Tag color={stateColor(text(evaluation.state, 'NOT_EVALUATED'))}>{text(evaluation.state, 'NOT_EVALUATED')}</Tag>
            </Space>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Engine">{text(snapshot?.version)}</Descriptions.Item>
              <Descriptions.Item label="Market">#{text(market.market_id ?? market.marketId)}</Descriptions.Item>
              <Descriptions.Item label="Reason">{text(evaluation.reason)}</Descriptions.Item>
              <Descriptions.Item label="Direction">{text(evaluation.direction)}</Descriptions.Item>
              <Descriptions.Item label="Poly selected">{price(evaluation.polySelected)}</Descriptions.Item>
              <Descriptions.Item label="Binance UP mid">{price(evaluation.binanceUpMid)}</Descriptions.Item>
              <Descriptions.Item label="Binance DOWN mid">{price(evaluation.binanceDownMid)}</Descriptions.Item>
              <Descriptions.Item label="Selected mid">{price(evaluation.binanceSelectedMid)}</Descriptions.Item>
              <Descriptions.Item label="Poly - Binance gap">{price(evaluation.divergenceGap)}</Descriptions.Item>
              <Descriptions.Item label="Pin duration">{ms(evaluation.pinDurationMs)}</Descriptions.Item>
              <Descriptions.Item label="Pin ratio">{ratio(evaluation.pinRatio)}</Descriptions.Item>
              <Descriptions.Item label="UP range">{price(evaluation.binanceUpRange)}</Descriptions.Item>
              <Descriptions.Item label="DOWN range">{price(evaluation.binanceDownRange)}</Descriptions.Item>
              <Descriptions.Item label="Binance observer age">{ms(evaluation.binanceObserverAgeMs)}</Descriptions.Item>
              <Descriptions.Item label="Seconds left">{price(evaluation.secondsLeft)}</Descriptions.Item>
            </Descriptions>
            <Alert
              type={evaluation.allowed === true ? 'success' : 'info'}
              showIcon
              message={evaluation.allowed === true ? 'Pinned entry condition 已成立' : '目前不授權 Pinned BUY'}
              description="即使顯示 ARMED，engine 還會再讀 fresh Binance direct book、檢查最大進場價、signed quote 與 post-quote edge；這個面板本身不會送單。"
            />
          </Card>
        </Col>

        <Col xs={24} xl={16}>
          <Card
            title={`${asset} · R_PINNED_BINANCE_POLY_DIVERGENCE`}
            extra={<Button icon={<ReloadOutlined />} onClick={() => void refresh()}>重新讀取</Button>}
          >
            {!live.service.ok ? <Alert type="error" showIcon message={`${asset} live engine 無法連線`} description={live.service.error || '等待服務啟動'} /> : null}
            {live.saveError ? <Alert type="error" showIcon message="設定寫入失敗" description={live.saveError} style={{ marginBottom: 12 }} /> : null}
            <Alert
              type="warning"
              showIcon
              message="策略選擇是互斥的"
              description="選 PINNED_DIVERGENCE 時，新 BUY 只在 Binance 長時間釘在設定區間且 Poly 強烈偏向時才會被放行；既有持倉的反轉 SELL / TAKE_PROFIT / maximum-loss 不受這個 gate 影響。"
              style={{ marginBottom: 12 }}
            />
            <Form form={form} layout="vertical" initialValues={strategyForm(snapshot)}>
              <Row gutter={[16, 0]}>
                <Col xs={24} md={8}>
                  <Form.Item name="entryStrategyMode" label="新進場策略">
                    <Select options={[
                      { value: 'POLY_GAP', label: 'POLY_GAP · 原本多頻策略' },
                      { value: 'PINNED_DIVERGENCE', label: 'PINNED_DIVERGENCE · Binance 釘住 / Poly 強偏' },
                    ]} />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8}>
                  <Form.Item name="pinnedBinanceCenter" label="Binance Pin 中心">
                    <InputNumber min={0.10} max={0.90} step={0.01} precision={3} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={12} md={8}>
                  <Form.Item name="pinnedBinanceHalfWidth" label="Pin ± 半寬">
                    <InputNumber min={0.005} max={0.20} step={0.005} precision={3} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={[16, 0]}>
                <Col xs={12} md={6}>
                  <Form.Item name="pinnedMinimumDurationSeconds" label="最短釘住秒數">
                    <InputNumber min={1} max={120} step={1} precision={1} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={12} md={6}>
                  <Form.Item name="pinnedMaximumRange" label="Pin 視窗最大 Range">
                    <InputNumber min={0.001} max={0.30} step={0.005} precision={3} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={12} md={6}>
                  <Form.Item name="pinnedRequiredRatio" label="Pin 樣本比例">
                    <InputNumber min={0.50} max={1.0} step={0.05} precision={2} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={12} md={6}>
                  <Form.Item name="pinnedMinimumRemainingSeconds" label="至少剩餘秒數">
                    <InputNumber min={1} max={299} step={5} precision={0} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={[16, 0]}>
                <Col xs={12} md={6}>
                  <Form.Item name="pinnedPolyThreshold" label="Poly 最低強度">
                    <InputNumber min={0.55} max={0.99} step={0.01} precision={3} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={12} md={6}>
                  <Form.Item name="pinnedMinimumGap" label="最小 Poly-Binance Gap">
                    <InputNumber min={0.01} max={0.80} step={0.01} precision={3} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={12} md={6}>
                  <Form.Item name="pinnedMaximumSelectedAsk" label="Fresh Selected Ask 上限">
                    <InputNumber min={0.05} max={0.99} step={0.01} precision={3} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={12} md={6}>
                  <Form.Item name="pinnedOneEntryPerMarket" label="同局最多一次成功 Pinned Entry" valuePropName="checked">
                    <Switch checkedChildren="ON" unCheckedChildren="OFF" />
                  </Form.Item>
                </Col>
              </Row>

              <Space wrap>
                <Button type="primary" icon={<SaveOutlined />} loading={live.saving} onClick={() => void save()}>
                  儲存 {asset} Pinned 策略
                </Button>
                <Text type="secondary">Runtime ON/OFF、Stake、止盈與 maximum-loss 仍在 Live Markets 頁設定。</Text>
              </Space>
            </Form>
          </Card>
        </Col>
      </Row>
    </Space>
  )
}

export default function PinnedDivergencePage() {
  const refresh = useLiveMarketsStore((store) => store.refresh)

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

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <div>
        <Title level={3} style={{ marginBottom: 4 }}>Pinned Binance / Strong Poly Divergence</Title>
        <Text type="secondary">
          BTC / ETH / BNB 共用同一策略定義，但每個 asset 的參數、round、DB 與實單 runtime 都獨立。
        </Text>
      </div>
      <Alert
        type="info"
        showIcon
        message="偵測 Binance Prediction 機率被釘在約 0.5 / 0.5，而 Polymarket 已形成強烈方向"
        description="策略使用 8770 的多市場軌跡判斷持續 pin，再由各 Echtgeld engine 用 fresh direct Binance book 做最後確認。預設門檻只是第一版可調參基準，不代表已完成歷史最佳化。"
      />
      <Tabs
        destroyInactiveTabPane={false}
        items={(['BTC', 'ETH', 'BNB'] as LiveAsset[]).map((asset) => ({
          key: asset,
          label: asset,
          children: <AssetPinnedPanel asset={asset} />,
        }))}
      />
    </Space>
  )
}
