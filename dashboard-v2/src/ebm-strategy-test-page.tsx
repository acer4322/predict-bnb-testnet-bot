import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ExperimentOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import type { TableColumnsType } from 'antd'

const { Text, Title } = Typography

type AnyRecord = Record<string, any>

type FeatureRow = {
  name: string
  value: unknown
  missing: boolean
}

let controlSessionToken: string | null = null

function number(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function fmt(value: unknown, digits = 4) {
  const parsed = number(value)
  return parsed === null ? '—' : parsed.toFixed(digits)
}

function pct(value: unknown, digits = 1) {
  const parsed = number(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(digits)}%`
}

function money(value: unknown) {
  const parsed = number(value)
  return parsed === null ? '—' : `${parsed >= 0 ? '+' : ''}$${parsed.toFixed(3)}`
}

function time(value: unknown) {
  const parsed = number(value)
  if (parsed === null || parsed <= 0) return '—'
  return new Date(parsed).toLocaleString()
}

async function getControlToken() {
  if (controlSessionToken) return controlSessionToken
  const response = await fetch('/control/session', { cache: 'no-store' })
  const payload = await response.json().catch(() => null) as AnyRecord | null
  if (!response.ok) throw new Error(String(payload?.error || `Control session HTTP ${response.status}`))
  const token = String(payload?.token || '')
  if (!token) throw new Error('Control session returned no token')
  controlSessionToken = token
  return token
}

async function lifecycle(action: 'start' | 'stop' | 'restart') {
  const token = await getControlToken()
  const response = await fetch(`/control/strategy-test/${action}`, {
    method: 'POST',
    cache: 'no-store',
    headers: { Accept: 'application/json', 'X-BTC-Lab-Control': token },
  })
  const payload = await response.json().catch(() => null) as AnyRecord | null
  if (!response.ok) {
    if (response.status === 403) controlSessionToken = null
    throw new Error(String(payload?.error || `HTTP ${response.status}`))
  }
  return payload
}

function resultColor(status: string) {
  if (status === 'WIN') return 'success'
  if (status === 'LOSS') return 'error'
  if (status === 'FLAT') return 'warning'
  return 'default'
}

export default function EbmStrategyTestPage() {
  const [state, setState] = useState<AnyRecord | null>(null)
  const [online, setOnline] = useState(false)
  const [loading, setLoading] = useState(false)
  const [action, setAction] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (loading) return
    setLoading(true)
    try {
      const response = await fetch('/bridge/ebm-strategy-test', {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) throw new Error(`8780 HTTP ${response.status}`)
      const payload = await response.json() as AnyRecord
      setState(payload)
      setOnline(true)
      setError(null)
    } catch (err) {
      setOnline(false)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [loading])

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

  const run = async (next: 'start' | 'stop' | 'restart') => {
    setAction(next)
    try {
      await lifecycle(next)
      message.success(`EBM strategy test ${next} 完成`)
      await new Promise((resolve) => window.setTimeout(resolve, 600))
      await refresh()
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err)
      setError(text)
      message.error(text)
    } finally {
      setAction(null)
    }
  }

  const performance = (state?.performance || {}) as AnyRecord
  const decision = (state?.lastDecision || {}) as AnyRecord
  const signal = (decision.signal || {}) as AnyRecord
  const currentMarket = (state?.currentMarket || {}) as AnyRecord
  const currentTrade = (state?.currentTrade || {}) as AnyRecord
  const policy = (state?.policy || {}) as AnyRecord
  const model = (state?.model || {}) as AnyRecord
  const health = (state?.health || {}) as AnyRecord
  const sources = (state?.sources || {}) as AnyRecord

  const features = useMemo<FeatureRow[]>(() => {
    const featureNames = Array.isArray(model.features) ? model.features.map(String) : []
    const values = (signal.features || {}) as AnyRecord
    const missing = new Set(Array.isArray(signal.missingFeatures) ? signal.missingFeatures.map(String) : [])
    return featureNames.map((name) => ({ name, value: values[name], missing: missing.has(name) || values[name] == null }))
  }, [model.features, signal.features, signal.missingFeatures])

  const resultColumns: TableColumnsType<AnyRecord> = [
    { title: 'Market', dataIndex: 'market_id', width: 95 },
    { title: 'Winner', dataIndex: 'winner', width: 80 },
    {
      title: 'Result', dataIndex: 'status', width: 90,
      render: (value) => <Tag color={resultColor(String(value))}>{String(value || '—')}</Tag>,
    },
    { title: 'Side', dataIndex: 'side', width: 70, render: (value) => value || '—' },
    { title: 'Ask', dataIndex: 'observed_ask', width: 80, render: (value) => fmt(value, 3) },
    { title: 'PnL', dataIndex: 'net_pnl_usdt', width: 90, render: (value) => money(value) },
    { title: 'ROI', dataIndex: 'net_roi', width: 80, render: (value) => pct(value) },
    { title: 'Resolved', dataIndex: 'resolved_at_ms', width: 170, render: time },
  ]

  const decisionColumns: TableColumnsType<AnyRecord> = [
    { title: 'Market', dataIndex: 'market_id', width: 90 },
    {
      title: 'Decision', dataIndex: 'decision', width: 90,
      render: (value) => <Tag color={value === 'TRADE' ? 'success' : 'default'}>{String(value)}</Tag>,
    },
    { title: 'Reason', dataIndex: 'reason', width: 230 },
    { title: 'Side', dataIndex: 'side', width: 70, render: (value) => value || '—' },
    { title: 'P(side)', dataIndex: 'selected_probability', width: 90, render: (value) => pct(value) },
    { title: 'Ask', dataIndex: 'observed_ask', width: 75, render: (value) => fmt(value, 3) },
    { title: 'Left', dataIndex: 'seconds_left', width: 75, render: (value) => `${fmt(value, 1)}s` },
    { title: 'Features', dataIndex: 'available_features', width: 80, render: (value) => `${value ?? 0}/16` },
  ]

  const featureColumns: TableColumnsType<FeatureRow> = [
    { title: 'Frozen EBM feature', dataIndex: 'name' },
    {
      title: 'Current value', dataIndex: 'value', width: 180,
      render: (value, row) => row.missing ? <Tag color="error">MISSING</Tag> : <Text code>{fmt(value, 6)}</Text>,
    },
  ]

  const sourceOk = health.directOfficialDbAccess === false
    && health.writesTo8776 === false
    && health.targetEventsUsedForDecision === false

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <Card>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space wrap>
            <ExperimentOutlined />
            <Title level={3} style={{ margin: 0 }}>EBM 策略測試</Title>
            <Tag color={online ? 'success' : 'error'}>8780 {online ? 'ONLINE' : 'OFFLINE'}</Tag>
            <Tag color="blue">TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY</Tag>
            <Tag color="gold">PAPER ONLY</Tag>
            <Tag color={model.loaded ? 'success' : 'error'}>EBM {model.loaded ? 'LOADED' : 'NOT LOADED'}</Tag>
          </Space>
          <Text type="secondary">
            獨立 8780 forward test。8776 只透過 HTTP 提供目前 market identity 與官方 settlement；Target fills / Inventory / Parent Orders 不進入 EBM 特徵，也不會寫回 8776。
          </Text>
          <Space wrap>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              disabled={online || action !== null}
              loading={action === 'start'}
              onClick={() => void run('start')}
            >Start 8780</Button>
            <Button
              danger
              icon={<StopOutlined />}
              disabled={!online || action !== null}
              loading={action === 'stop'}
              onClick={() => void run('stop')}
            >Stop</Button>
            <Button
              icon={<ReloadOutlined />}
              disabled={!online || action !== null}
              loading={action === 'restart'}
              onClick={() => void run('restart')}
            >Restart</Button>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void refresh()}>刷新</Button>
          </Space>
        </Space>
      </Card>

      {error && !online ? (
        <Alert
          type="warning"
          showIcon
          message="8780 尚未提供策略測試狀態"
          description={`${error}。可以直接按 Start 8780；如果啟動失敗，查看 data/service-manager-strategyTest.stderr.log。`}
        />
      ) : null}

      {online ? (
        <>
          <Alert
            type={sourceOk ? 'success' : 'error'}
            showIcon
            message={sourceOk ? '策略與 8776 已隔離' : '資料邊界異常，請停止 8780 檢查'}
            description={state?.evidenceBoundary || '—'}
          />

          <Row gutter={[12, 12]}>
            <Col xs={12} md={6}><Card><Statistic title="Net PnL" value={number(performance.netPnlUsdt) ?? 0} precision={3} prefix="$" /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Win rate" value={(number(performance.winRate) ?? 0) * 100} precision={1} suffix="%" /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Net ROI" value={(number(performance.netRoi) ?? 0) * 100} precision={1} suffix="%" /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Trades / Settled" value={`${performance.tradedMarkets ?? 0} / ${performance.settledMarkets ?? 0}`} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Max Drawdown" value={number(performance.maxDrawdownUsdt) ?? 0} precision={3} prefix="$" /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Longest loss streak" value={performance.longestLossStreak ?? 0} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Markets" value={performance.markets ?? 0} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Pending" value={performance.pendingMarkets ?? 0} /></Card></Col>
          </Row>

          <Row gutter={[12, 12]}>
            <Col xs={24} xl={12}>
              <Card title="Current Market / Decision">
                <Descriptions size="small" column={2} bordered>
                  <Descriptions.Item label="Market ID">{currentMarket.marketId ?? '—'}</Descriptions.Item>
                  <Descriptions.Item label="Forward active">
                    <Tag color={currentMarket.active ? 'success' : 'warning'}>{currentMarket.active ? 'YES' : 'WAIT NEXT COMPLETE MARKET'}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="Decision">
                    <Tag color={decision.decision === 'TRADE' ? 'success' : 'default'}>{decision.decision || '—'}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="Reason">{decision.reason || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Side">{decision.side || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Selected probability">{pct(signal.selectedProbability)}</Descriptions.Item>
                  <Descriptions.Item label="Ask">{fmt(decision.ask, 4)}</Descriptions.Item>
                  <Descriptions.Item label="Seconds left">{fmt(decision.secondsLeft, 1)}s</Descriptions.Item>
                  <Descriptions.Item label="Sample age">{fmt(decision.sampleAgeMs, 0)} ms</Descriptions.Item>
                  <Descriptions.Item label="Predict receipt age">{fmt(decision.predictReceiptAgeMs, 0)} ms</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card title="Frozen Policy">
                <Descriptions size="small" column={2} bordered>
                  <Descriptions.Item label="P(side) threshold">{pct(policy.sideProbabilityThreshold)}</Descriptions.Item>
                  <Descriptions.Item label="Max ask">{fmt(policy.maxAsk, 2)}</Descriptions.Item>
                  <Descriptions.Item label="Stake">${fmt(policy.fixedStakeUsdt, 2)}</Descriptions.Item>
                  <Descriptions.Item label="Fee">{fmt(policy.feeRateBps, 0)} bps</Descriptions.Item>
                  <Descriptions.Item label="One entry / market">YES</Descriptions.Item>
                  <Descriptions.Item label="Calibration claim">NO</Descriptions.Item>
                  <Descriptions.Item label="Deployment market excluded">{state?.excludedDeploymentMarketId ?? '—'}</Descriptions.Item>
                  <Descriptions.Item label="Deployed">{time(state?.deployedAtMs)}</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          </Row>

          <Row gutter={[12, 12]}>
            <Col xs={24} xl={12}>
              <Card title="Current Paper Trade">
                {Object.keys(currentTrade).length ? (
                  <Descriptions size="small" column={2} bordered>
                    <Descriptions.Item label="Side">{currentTrade.side || '—'}</Descriptions.Item>
                    <Descriptions.Item label="Ask">{fmt(currentTrade.observed_ask ?? currentTrade.ask, 4)}</Descriptions.Item>
                    <Descriptions.Item label="Stake">${fmt(currentTrade.stake_usdt ?? currentTrade.stakeUsdt, 2)}</Descriptions.Item>
                    <Descriptions.Item label="Shares">{fmt(currentTrade.shares, 5)}</Descriptions.Item>
                    <Descriptions.Item label="P(side)">{pct(currentTrade.selected_probability ?? currentTrade.selectedProbability)}</Descriptions.Item>
                    <Descriptions.Item label="Decision time">{time(currentTrade.decision_at_ms ?? currentTrade.decisionAtMs)}</Descriptions.Item>
                  </Descriptions>
                ) : <Text type="secondary">目前市場尚未觸發 paper trade。</Text>}
              </Card>
            </Col>
            <Col xs={24} xl={12}>
              <Card title="Data Boundary">
                <Descriptions size="small" column={1} bordered>
                  <Descriptions.Item label="8776">
                    <Tag color={sources.targetOfficial?.status === 'ONLINE' ? 'success' : 'warning'}>{sources.targetOfficial?.version || '—'}</Tag>
                    market identity + official settlement only
                  </Descriptions.Item>
                  <Descriptions.Item label="Direct 8776 DB">{String(health.directOfficialDbAccess)}</Descriptions.Item>
                  <Descriptions.Item label="Writes to 8776">{String(health.writesTo8776)}</Descriptions.Item>
                  <Descriptions.Item label="Target events as model input">{String(health.targetEventsUsedForDecision)}</Descriptions.Item>
                  <Descriptions.Item label="Public Predict">8771 top-of-book</Descriptions.Item>
                  <Descriptions.Item label="Spot/Futures archive">{sources.spotFutures?.archive || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Chainlink">{sources.chainlink?.status || '—'}</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          </Row>

          <Card title={`Frozen EBM Features · ${features.filter((row) => !row.missing).length}/16 available`}>
            <Table rowKey="name" size="small" pagination={false} dataSource={features} columns={featureColumns} />
          </Card>

          <Card title="Recent Settled Markets">
            <Table
              rowKey={(row) => String(row.market_id)}
              size="small"
              pagination={false}
              scroll={{ x: 850 }}
              dataSource={Array.isArray(performance.recentMarkets) ? performance.recentMarkets : []}
              columns={resultColumns}
            />
          </Card>

          <Card title="Recent Decision Changes">
            <Table
              rowKey={(row, index) => `${row.market_id}-${row.decision_at_ms}-${index}`}
              size="small"
              pagination={false}
              scroll={{ x: 900 }}
              dataSource={Array.isArray(state?.recentDecisions) ? state.recentDecisions : []}
              columns={decisionColumns}
            />
          </Card>

          {model.error ? <Alert type="error" showIcon message="EBM model unavailable" description={String(model.error)} /> : null}
        </>
      ) : null}
    </Space>
  )
}
