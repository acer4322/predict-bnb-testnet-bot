import { Alert, Button, Card, Col, InputNumber, Row, Space, Statistic, Tag, Typography, message } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useEchtgeldStore } from './echtgeld-store'

function finite(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function money(value: unknown): string {
  const parsed = finite(value)
  return parsed == null ? '—' : `${parsed >= 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

export default function EchtgeldStopLossPanel() {
  const service = useEchtgeldStore((state) => state.service)
  const saving = useEchtgeldStore((state) => state.saving)
  const saveError = useEchtgeldStore((state) => state.saveError)
  const updateSettings = useEchtgeldStore((state) => state.updateSettings)
  const refresh = useEchtgeldStore((state) => state.refresh)

  const engine = useMemo(
    () => (service.data && typeof service.data === 'object' && !Array.isArray(service.data)
      ? service.data as Record<string, unknown>
      : {}),
    [service.data],
  )
  const risk = useMemo(
    () => (engine.riskControl && typeof engine.riskControl === 'object' && !Array.isArray(engine.riskControl)
      ? engine.riskControl as Record<string, unknown>
      : {}),
    [engine.riskControl],
  )
  const performance = useMemo(
    () => (engine.performance && typeof engine.performance === 'object' && !Array.isArray(engine.performance)
      ? engine.performance as Record<string, unknown>
      : {}),
    [engine.performance],
  )

  const armed = engine.armed === true
  const runtimeStatus = String(engine.runtimeStatus || (armed ? 'ARMED' : 'PAUSED'))
  const persistedStopLoss = finite(risk.stopLossUsdt) ?? finite((engine.config as Record<string, unknown> | undefined)?.stopLossUsdt) ?? 0
  const riskEnabled = risk.enabled === true || persistedStopLoss > 0
  const tripped = risk.tripped === true
  const currentPnl = finite(risk.currentNetPnlUsdt) ?? finite(performance.netPnlUsdt)
  const remaining = finite(risk.remainingLossBufferUsdt)
  const basis = String(risk.basis || 'SETTLED_NET_PNL_USDT')
  const [stopLoss, setStopLoss] = useState<number>(persistedStopLoss)

  useEffect(() => {
    setStopLoss(persistedStopLoss)
  }, [persistedStopLoss])

  const save = async () => {
    if (armed) {
      message.error('請先 PAUSE 8781，再修改 PnL 止損。')
      return
    }
    const value = finite(stopLoss)
    if (value == null || value < 0 || value > 1_000_000) {
      message.error('止損金額必須介於 0～1,000,000 USDT；0 代表關閉。')
      return
    }
    try {
      await updateSettings({ stopLossUsdt: value })
      await refresh()
      message.success(value > 0 ? `8781 PnL 止損已設為 $${value.toFixed(2)}` : '8781 PnL 止損已關閉')
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <Card
      size="small"
      title="8781 PnL 止損"
      extra={(
        <Space size={6}>
          <Tag color={riskEnabled ? (tripped ? 'error' : 'success') : 'default'}>
            {riskEnabled ? (tripped ? '已觸發' : '啟用') : '關閉'}
          </Tag>
          <Tag color={armed ? 'processing' : tripped ? 'error' : 'default'}>{runtimeStatus}</Tag>
        </Space>
      )}
      style={{ marginBottom: 12 }}
    >
      {tripped ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="8781 已因 PnL 止損自動暫停送單"
          description={`已結算累積 PnL ${money(currentPnl)} 已到達或低於 -$${persistedStopLoss.toFixed(2)}。在 PAUSED 狀態提高止損金額，或設為 0 關閉後，才能重新 Resume。`}
        />
      ) : riskEnabled ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`止損監控中：已結算累積 PnL ≤ -$${persistedStopLoss.toFixed(2)} 時自動 PAUSE`}
          description="這是 8781 引擎本身的硬性風控；Dashboard 關閉也會繼續生效。"
        />
      ) : null}

      <Row gutter={[12, 12]} align="middle">
        <Col xs={24} sm={8} md={6}>
          <Statistic title="已結算累積 PnL" value={currentPnl ?? 0} precision={2} prefix="$" />
        </Col>
        <Col xs={24} sm={8} md={6}>
          <Statistic title="目前止損" value={persistedStopLoss} precision={2} prefix="$" suffix={riskEnabled ? '' : ' OFF'} />
        </Col>
        <Col xs={24} sm={8} md={6}>
          <Statistic title="剩餘虧損空間" value={remaining ?? 0} precision={2} prefix="$" suffix={riskEnabled ? '' : ' —'} />
        </Col>
        <Col xs={24} md={6}>
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            <Typography.Text strong>止損金額（USDT）</Typography.Text>
            <Space.Compact style={{ width: '100%' }}>
              <InputNumber
                min={0}
                max={1_000_000}
                step={0.01}
                precision={2}
                value={stopLoss}
                disabled={armed || saving}
                onChange={(value) => setStopLoss(Number(value ?? 0))}
                style={{ width: '100%' }}
              />
              <Button type="primary" onClick={() => void save()} disabled={armed} loading={saving}>
                儲存
              </Button>
            </Space.Compact>
            <Typography.Text type="secondary">0 = 關閉；只有 PAUSED 時可修改</Typography.Text>
          </Space>
        </Col>
      </Row>

      <Typography.Text type="secondary">
        基準：{basis}。使用 8781 durable SUBMITTED 訂單與官方市場結算計算，不使用未結算浮動 PnL；觸發後新 intent、worker 送單與 Resume 都會被風控擋住。
      </Typography.Text>
      {saveError ? <Alert type="error" showIcon style={{ marginTop: 10 }} message={saveError} /> : null}
    </Card>
  )
}
