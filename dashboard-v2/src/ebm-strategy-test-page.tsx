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
type FeatureRow = { name: string; value: unknown; missing: boolean }
type GateRow = { name: string; pass: boolean; actual: string; rule: string }
type InputRow = { name: string; value: unknown; missing: boolean }

let controlSessionToken: string | null = null

function number(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
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

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return 'MISSING'
  if (typeof value === 'number') return Number.isFinite(value) ? value.toFixed(6) : 'MISSING'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

function gateActual(gate: AnyRecord) {
  const parts: string[] = []
  for (const key of ['status', 'actual', 'actualMs', 'actualSeconds', 'available', 'required', 'expected']) {
    const value = gate[key]
    if (value !== null && value !== undefined && value !== '') parts.push(`${key}=${displayValue(value)}`)
  }
  if (Array.isArray(gate.missing) && gate.missing.length) parts.push(`missing=${gate.missing.join(', ')}`)
  return parts.join(' · ') || '—'
}

function gateRule(gate: AnyRecord) {
  if (gate.rule) return String(gate.rule)
  if (gate.maxMs !== undefined) return `≤ ${gate.maxMs} ms`
  if (gate.minExclusiveSeconds !== undefined) return `> ${gate.minExclusiveSeconds}s`
  if (gate.minInclusive !== undefined) return `≥ ${gate.minInclusive}`
  if (gate.maxInclusive !== undefined) return `${gate.minExclusive !== undefined ? `> ${gate.minExclusive} and ` : ''}≤ ${gate.maxInclusive}`
  return '—'
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
    setLoading(true)
    try {
      const response = await fetch('/bridge/ebm-strategy-test', {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) throw new Error(`8782 HTTP ${response.status}`)
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
  }, [])

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
  const diagnostics = (state?.calculationDiagnostics || {}) as AnyRecord
  const integrity = (state?.dataIntegrity || {}) as AnyRecord
  const modelOutputs = (diagnostics.modelOutputs || {}) as AnyRecord
  const finalDiag = (diagnostics.final || {}) as AnyRecord
  const diagnosticFeatures = (diagnostics.frozenFeatures || signal.features || {}) as AnyRecord
  const missingFeatures = Array.isArray(diagnostics.missingFeatures)
    ? diagnostics.missingFeatures.map(String)
    : Array.isArray(signal.missingFeatures) ? signal.missingFeatures.map(String) : []
  const requiredFeatures = number(diagnostics.requiredFeatureCount ?? integrity.requiredFeatureCount ?? policy.requiredFeatureCount) ?? 16
  const availableFeatures = number(diagnostics.availableFeatureCount ?? integrity.availableFeatureCount) ?? Math.max(0, requiredFeatures - missingFeatures.length)
  const integrityReady = diagnostics.ready === true || integrity.ready === true
  const inferenceAttempted = diagnostics.inferenceAttempted === true

  const features = useMemo<FeatureRow[]>(() => {
    const featureNames = Array.isArray(model.features) ? model.features.map(String) : []
    const missing = new Set(missingFeatures)
    return featureNames.map((name) => ({
      name,
      value: diagnosticFeatures[name],
      missing: missing.has(name) || diagnosticFeatures[name] === null || diagnosticFeatures[name] === undefined || diagnosticFeatures[name] === '',
    }))
  }, [model.features, diagnosticFeatures, missingFeatures])

  const gates = useMemo<GateRow[]>(() => Object.entries((diagnostics.gates || decision.gates || {}) as AnyRecord).map(([name, value]) => {
    const gate = value && typeof value === 'object' && !Array.isArray(value) ? value as AnyRecord : {}
    return { name, pass: gate.pass === true, actual: gateActual(gate), rule: gateRule(gate) }
  }), [diagnostics.gates, decision.gates])

  const rawInputs = useMemo<InputRow[]>(() => Object.entries((diagnostics.rawPublicInputs || {}) as AnyRecord).map(([name, value]) => ({
    name,
    value,
    missing: value === null || value === undefined || value === '' || (typeof value === 'number' && !Number.isFinite(value)),
  })), [diagnostics.rawPublicInputs])

  const resultColumns: TableColumnsType<AnyRecord> = [
    { title: 'Market', dataIndex: 'market_id', width: 95 },
    { title: 'Winner', dataIndex: 'winner', width: 80 },
    { title: 'Result', dataIndex: 'status', width: 90, render: (value) => <Tag color={resultColor(String(value))}>{String(value || '—')}</Tag> },
    { title: 'Side', dataIndex: 'side', width: 70, render: (value) => value || '—' },
    { title: 'Ask', dataIndex: 'observed_ask', width: 80, render: (value) => fmt(value, 3) },
    { title: 'PnL', dataIndex: 'net_pnl_usdt', width: 90, render: (value) => money(value) },
    { title: 'ROI', dataIndex: 'net_roi', width: 80, render: (value) => pct(value) },
    { title: 'Resolved', dataIndex: 'resolved_at_ms', width: 170, render: time },
  ]

  const decisionColumns: TableColumnsType<AnyRecord> = [
    { title: 'Market', dataIndex: 'market_id', width: 90 },
    { title: 'Decision', dataIndex: 'decision', width: 90, render: (value) => <Tag color={value === 'TRADE' ? 'success' : 'default'}>{String(value)}</Tag> },
    { title: 'Reason', dataIndex: 'reason', width: 240 },
    { title: 'Side', dataIndex: 'side', width: 70, render: (value) => value || '—' },
    { title: 'P(side)', dataIndex: 'selected_probability', width: 90, render: (value) => pct(value) },
    { title: 'Ask', dataIndex: 'observed_ask', width: 75, render: (value) => fmt(value, 3) },
    { title: 'Left', dataIndex: 'seconds_left', width: 75, render: (value) => `${fmt(value, 1)}s` },
    { title: 'Features', dataIndex: 'available_features', width: 85, render: (value) => `${value ?? 0}/${requiredFeatures}` },
  ]

  const featureColumns: TableColumnsType<FeatureRow> = [
    { title: 'Frozen EBM feature', dataIndex: 'name' },
    { title: 'Current value', dataIndex: 'value', width: 210, render: (value, row) => row.missing ? <Tag color="error">MISSING</Tag> : <Text code>{displayValue(value)}</Text> },
    { title: 'Status', key: 'status', width: 100, render: (_, row) => <Tag color={row.missing ? 'error' : 'success'}>{row.missing ? 'FAIL' : 'OK'}</Tag> },
  ]

  const gateColumns: TableColumnsType<GateRow> = [
    { title: 'Gate', dataIndex: 'name', width: 180 },
    { title: 'Actual', dataIndex: 'actual' },
    { title: 'Rule / Threshold', dataIndex: 'rule', width: 230 },
    { title: 'Result', dataIndex: 'pass', width: 100, render: (pass) => <Tag color={pass ? 'success' : 'error'}>{pass ? 'PASS' : 'FAIL'}</Tag> },
  ]

  const inputColumns: TableColumnsType<InputRow> = [
    { title: 'Raw public input', dataIndex: 'name' },
    { title: 'Value', dataIndex: 'value', width: 260, render: (value, row) => row.missing ? <Tag color="error">MISSING</Tag> : <Text code>{displayValue(value)}</Text> },
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
            <Tag color={online ? 'success' : 'error'}>8782 {online ? 'ONLINE' : 'OFFLINE'}</Tag>
            <Tag color="blue">TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY</Tag>
            <Tag color="gold">PAPER ONLY</Tag>
            <Tag color={model.loaded ? 'success' : 'error'}>EBM {model.loaded ? 'LOADED' : 'NOT LOADED'}</Tag>
            {online ? <Tag color={integrityReady ? 'success' : 'error'}>INPUT {integrityReady ? 'READY' : 'FAIL-CLOSED'}</Tag> : null}
          </Space>
          <Text type="secondary">
            獨立 8782 forward test。8780 保留給 ETH Taker collector；8776 只透過 HTTP 提供 market identity 與官方 settlement。Target fills / Inventory / Parent Orders 不進入 EBM 特徵，也不會寫回 8776。
          </Text>
          <Space wrap>
            <Button type="primary" icon={<PlayCircleOutlined />} disabled={online || action !== null} loading={action === 'start'} onClick={() => void run('start')}>Start 8782</Button>
            <Button danger icon={<StopOutlined />} disabled={!online || action !== null} loading={action === 'stop'} onClick={() => void run('stop')}>Stop</Button>
            <Button icon={<ReloadOutlined />} disabled={!online || action !== null} loading={action === 'restart'} onClick={() => void run('restart')}>Restart</Button>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void refresh()}>刷新</Button>
          </Space>
        </Space>
      </Card>

      {error && !online ? <Alert type="warning" showIcon message="8782 尚未提供策略測試狀態" description={`${error}。可以直接按 Start 8782；如果啟動失敗，查看 data/service-manager-strategyTest.stderr.log。`} /> : null}

      {online ? (
        <>
          <Alert
            type={integrityReady ? 'success' : 'error'}
            showIcon
            message={integrityReady ? `EBM Input READY · ${availableFeatures}/${requiredFeatures}` : `FAIL-CLOSED · ${availableFeatures}/${requiredFeatures} frozen features available`}
            description={integrityReady
              ? `Frozen features 全部是有限數值；model inference=${inferenceAttempted ? 'YES' : 'NO'}。只有所有 gate 通過才允許 paper trade。`
              : `缺失：${missingFeatures.length ? missingFeatures.join(', ') : '尚未形成完整 signal'}。缺任何一項時不呼叫 predict_proba，也不允許 TRADE。Process 保持在線只為等待資料恢復。`}
          />

          <Alert type={sourceOk ? 'success' : 'error'} showIcon message={sourceOk ? '策略與 8776 已隔離' : '資料邊界異常，請停止 8782 檢查'} description={state?.evidenceBoundary || '—'} />

          <Row gutter={[12, 12]}>
            <Col xs={12} md={6}><Card><Statistic title="Net PnL" value={number(performance.netPnlUsdt) ?? 0} precision={3} prefix="$" /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Win rate" value={(number(performance.winRate) ?? 0) * 100} precision={1} suffix="%" /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Net ROI" value={(number(performance.netRoi) ?? 0) * 100} precision={1} suffix="%" /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Trades / Settled" value={`${performance.tradedMarkets ?? 0} / ${performance.settledMarkets ?? 0}`} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Max Drawdown" value={number(performance.maxDrawdownUsdt) ?? 0} precision={3} prefix="$" /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Longest loss streak" value={performance.longestLossStreak ?? 0} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Frozen features" value={`${availableFeatures} / ${requiredFeatures}`} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Inference attempted" value={inferenceAttempted ? 'YES' : 'NO'} /></Card></Col>
          </Row>

          <Card title="模型計算診斷 · Model outputs">
            <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }} bordered>
              <Descriptions.Item label="Signal status"><Tag color={diagnostics.signalStatus === 'OK' ? 'success' : 'error'}>{diagnostics.signalStatus || signal.status || '—'}</Tag></Descriptions.Item>
              <Descriptions.Item label="Selected side">{modelOutputs.selectedSide || signal.side || '—'}</Descriptions.Item>
              <Descriptions.Item label="P(UP)">{pct(modelOutputs.probabilityUp ?? signal.probabilityUp, 3)}</Descriptions.Item>
              <Descriptions.Item label="P(DOWN)">{pct(modelOutputs.probabilityDown ?? signal.probabilityDown, 3)}</Descriptions.Item>
              <Descriptions.Item label="Selected probability">{pct(modelOutputs.selectedProbability ?? signal.selectedProbability, 3)}</Descriptions.Item>
              <Descriptions.Item label="Signed score">{fmt(modelOutputs.signedScore ?? signal.score, 6)}</Descriptions.Item>
              <Descriptions.Item label="Confidence">{pct(modelOutputs.confidence ?? signal.confidence, 3)}</Descriptions.Item>
              <Descriptions.Item label="Threshold">{pct(modelOutputs.probabilityThreshold ?? signal.threshold ?? policy.sideProbabilityThreshold, 1)}</Descriptions.Item>
              <Descriptions.Item label="Final decision"><Tag color={(finalDiag.decision ?? decision.decision) === 'TRADE' ? 'success' : 'default'}>{finalDiag.decision ?? decision.decision ?? '—'}</Tag></Descriptions.Item>
              <Descriptions.Item label="Final reason" span={2}>{finalDiag.reason ?? decision.reason ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="Trade allowed now"><Tag color={diagnostics.tradeAllowedNow === true ? 'success' : 'error'}>{diagnostics.tradeAllowedNow === true ? 'YES' : 'NO'}</Tag></Descriptions.Item>
              <Descriptions.Item label="Calibration claim">{modelOutputs.probabilityCalibrationClaim === true ? 'YES' : 'NO'}</Descriptions.Item>
            </Descriptions>
            <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>{diagnostics.note || '顯示模型輸入、衍生特徵、機率與 gate；不是隱藏推理文字。'}</Text>
          </Card>

          <Card title="Decision Gates · 實際值 vs 門檻">
            <Table rowKey="name" size="small" pagination={false} dataSource={gates} columns={gateColumns} scroll={{ x: 850 }} />
          </Card>

          <Card title={`Frozen EBM Features · ${availableFeatures}/${requiredFeatures} available`}>
            <Table rowKey="name" size="small" pagination={false} dataSource={features} columns={featureColumns} />
          </Card>

          <Card title="Raw Public Inputs · 原始公開資料">
            <Alert type="info" showIcon message="空值絕不顯示成 0" description="null / undefined / NaN / 空字串一律顯示 MISSING。Frozen feature 若因此不完整，策略直接 FAIL-CLOSED。" style={{ marginBottom: 10 }} />
            <Table rowKey="name" size="small" pagination={{ pageSize: 25, hideOnSinglePage: true }} dataSource={rawInputs} columns={inputColumns} />
          </Card>

          <Row gutter={[12, 12]}>
            <Col xs={24} xl={12}>
              <Card title="Current Market / Decision">
                <Descriptions size="small" column={2} bordered>
                  <Descriptions.Item label="Market ID">{currentMarket.marketId ?? '—'}</Descriptions.Item>
                  <Descriptions.Item label="Forward active"><Tag color={currentMarket.active ? 'success' : 'warning'}>{currentMarket.active ? 'YES' : 'WAIT NEXT COMPLETE MARKET'}</Tag></Descriptions.Item>
                  <Descriptions.Item label="Decision"><Tag color={decision.decision === 'TRADE' ? 'success' : 'default'}>{decision.decision || '—'}</Tag></Descriptions.Item>
                  <Descriptions.Item label="Reason">{decision.reason || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Side">{decision.side || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Selected probability">{pct(signal.selectedProbability, 3)}</Descriptions.Item>
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
                  <Descriptions.Item label="Required features">{policy.requiredFeatureCount ?? 16}/16</Descriptions.Item>
                  <Descriptions.Item label="Missing policy"><Tag color="error">{policy.missingFeaturePolicy || 'FAIL CLOSED'}</Tag></Descriptions.Item>
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
                  <Descriptions.Item label="8776"><Tag color={sources.targetOfficial?.status === 'ONLINE' ? 'success' : 'warning'}>{sources.targetOfficial?.version || '—'}</Tag> market identity + official settlement only</Descriptions.Item>
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

          <Card title="Recent Settled Markets">
            <Table rowKey={(row) => String(row.market_id)} size="small" pagination={false} scroll={{ x: 850 }} dataSource={Array.isArray(performance.recentMarkets) ? performance.recentMarkets : []} columns={resultColumns} />
          </Card>

          <Card title="Recent Decision Changes">
            <Table rowKey={(row, index) => `${row.market_id}-${row.decision_at_ms}-${index}`} size="small" pagination={false} scroll={{ x: 900 }} dataSource={Array.isArray(state?.recentDecisions) ? state.recentDecisions : []} columns={decisionColumns} />
          </Card>

          {model.error ? <Alert type="error" showIcon message="EBM model unavailable" description={String(model.error)} /> : null}
        </>
      ) : null}
    </Space>
  )
}