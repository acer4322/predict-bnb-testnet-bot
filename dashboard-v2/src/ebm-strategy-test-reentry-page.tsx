import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Card, Col, Descriptions, Row, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import EbmStrategyTestPage from './ebm-strategy-test-page'

const { Text, Title } = Typography

type AnyRecord = Record<string, any>

function number(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}
function fmt(value: unknown, digits = 3) { const parsed = number(value); return parsed === null ? '—' : parsed.toFixed(digits) }
function pct(value: unknown, digits = 1) { const parsed = number(value); return parsed === null ? '—' : `${(parsed * 100).toFixed(digits)}%` }
function money(value: unknown) { const parsed = number(value); return parsed === null ? '—' : `${parsed >= 0 ? '+' : ''}$${parsed.toFixed(3)}` }
function time(value: unknown) { const parsed = number(value); return parsed === null || parsed <= 0 ? '—' : new Date(parsed).toLocaleString() }
function resultColor(status: string) { return status === 'WIN' ? 'success' : status === 'LOSS' ? 'error' : status === 'FLAT' ? 'warning' : 'default' }

function EbmReentryPanel() {
  const [state, setState] = useState<AnyRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/bridge/ebm-strategy-test', { cache: 'no-store', headers: { Accept: 'application/json' } })
      if (!response.ok) throw new Error(`8782 HTTP ${response.status}`)
      setState(await response.json() as AnyRecord)
      setError(null)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
  }, [])
  useEffect(() => {
    let cancelled = false
    const tick = () => { if (!cancelled && document.visibilityState === 'visible') void refresh() }
    tick()
    const timer = window.setInterval(tick, 1000)
    const visibility = () => tick()
    document.addEventListener('visibilitychange', visibility)
    return () => { cancelled = true; window.clearInterval(timer); document.removeEventListener('visibilitychange', visibility) }
  }, [refresh])

  const model = (state?.reentryModel || {}) as AnyRecord
  const policy = (state?.reentryPolicy || {}) as AnyRecord
  const performance = (state?.reentryPerformance || {}) as AnyRecord
  const diagnostic = (state?.reentryDiagnostics || {}) as AnyRecord
  const sequence = Array.isArray(state?.currentEntrySequence) ? state.currentEntrySequence as AnyRecord[] : []
  const ordinalStats = Array.isArray(performance.ordinalStats) ? performance.ordinalStats as AnyRecord[] : []
  const recentEntries = Array.isArray(performance.recentEntries) ? performance.recentEntries as AnyRecord[] : []
  const recentDecisions = Array.isArray(state?.recentReentryDecisions) ? state.recentReentryDecisions as AnyRecord[] : []

  const sequenceColumns = useMemo<TableColumnsType<AnyRecord>>(() => [
    { title: '#', dataIndex: 'entry_ordinal', width: 55 },
    { title: 'Source', dataIndex: 'entry_source', width: 115, render: (value) => <Tag color={value === 'REENTRY_EBM' ? 'purple' : 'blue'}>{value || '—'}</Tag> },
    { title: 'Side', dataIndex: 'side', width: 70 },
    { title: 'Ask', dataIndex: 'observed_ask', width: 75, render: (value) => fmt(value) },
    { title: 'P(reentry)', dataIndex: 'reentry_probability', width: 105, render: (value) => pct(value, 2) },
    { title: 'Raw P', dataIndex: 'raw_reentry_probability', width: 90, render: (value) => pct(value, 2) },
    { title: 'Delay', dataIndex: 'ms_since_prev_entry', width: 90, render: (value) => number(value) === null ? '—' : `${fmt(value, 0)} ms` },
    { title: 'Time', dataIndex: 'decision_at_ms', width: 175, render: time },
  ], [])
  const ordinalColumns = useMemo<TableColumnsType<AnyRecord>>(() => [
    { title: 'Entry ordinal', dataIndex: 'ordinal_bucket', width: 120 },
    { title: 'Settled', dataIndex: 'settled_entries', width: 90 },
    { title: 'W / L', width: 95, render: (_, row) => `${row.wins ?? 0} / ${row.losses ?? 0}` },
    { title: 'Win rate', dataIndex: 'win_rate', width: 100, render: (value) => pct(value) },
    { title: 'PnL', dataIndex: 'net_pnl_usdt', width: 100, render: money },
    { title: 'ROI', dataIndex: 'net_roi', width: 90, render: (value) => pct(value) },
  ], [])
  const settledColumns = useMemo<TableColumnsType<AnyRecord>>(() => [
    { title: 'Market', dataIndex: 'market_id', width: 90 }, { title: '#', dataIndex: 'entry_ordinal', width: 50 },
    { title: 'Source', dataIndex: 'entry_source', width: 115, render: (value) => <Tag color={value === 'REENTRY_EBM' ? 'purple' : 'blue'}>{value || '—'}</Tag> },
    { title: 'Result', dataIndex: 'status', width: 80, render: (value) => <Tag color={resultColor(String(value))}>{value || '—'}</Tag> },
    { title: 'Side', dataIndex: 'side', width: 65 }, { title: 'Ask', dataIndex: 'observed_ask', width: 70, render: (value) => fmt(value) },
    { title: 'P(reentry)', dataIndex: 'reentry_probability', width: 105, render: (value) => pct(value, 2) },
    { title: 'PnL', dataIndex: 'net_pnl_usdt', width: 90, render: money }, { title: 'Resolved', dataIndex: 'resolved_at_ms', width: 175, render: time },
  ], [])
  const decisionColumns = useMemo<TableColumnsType<AnyRecord>>(() => [
    { title: 'Market', dataIndex: 'market_id', width: 90 },
    { title: 'Decision', dataIndex: 'decision', width: 85, render: (value) => <Tag color={value === 'TRADE' ? 'success' : 'default'}>{value || '—'}</Tag> },
    { title: 'Reason', dataIndex: 'reason', width: 250 }, { title: 'Side', dataIndex: 'side', width: 65 },
    { title: 'P(reentry)', dataIndex: 'reentry_probability', width: 105, render: (value) => pct(value, 2) },
    { title: 'Ask', dataIndex: 'observed_ask', width: 70, render: (value) => fmt(value) },
    { title: 'Delay', dataIndex: 'ms_since_prev_entry', width: 90, render: (value) => number(value) === null ? '—' : `${fmt(value, 0)} ms` },
    { title: 'Entries', dataIndex: 'entry_count_so_far', width: 75 },
  ], [])

  if (!state) return <Alert type={error ? 'warning' : 'info'} showIcon message="SAME-SIDE Re-entry EBM" description={error || '等待 8782 狀態…'} />

  return <Space direction="vertical" size={12} style={{ width: '100%' }}>
    <Card><Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Space wrap>
        <Title level={4} style={{ margin: 0 }}>SAME-SIDE Re-entry EBM · 多次進場測試</Title>
        <Tag color="purple">PUBLIC_ACTOR</Tag><Tag color="gold">PAPER ONLY</Tag>
        <Tag color={model.loaded ? 'success' : 'error'}>REENTRY EBM {model.loaded ? 'LOADED' : 'NOT LOADED'}</Tag>
        <Tag color={model.calibratorLoaded ? 'success' : 'warning'}>CALIBRATOR {model.calibratorLoaded ? 'LOADED' : 'NONE'}</Tag>
        <Tag color="blue">SAME SIDE ONLY</Tag><Tag>ENTRY CAP: NONE</Tag>
      </Space>
      <Text type="secondary">Entry #1 完全沿用原本 TARGET_TAKER_PUBLIC_SIDE_V1 EBM control；#2 之後只由 SAME_SIDE 5 秒 PUBLIC_ACTOR EBM 決定。actor_* 只由本策略自己的 paper entries 重建，不讀 Target live fills / parent orders。</Text>
    </Space></Card>
    {!model.loaded ? <Alert type="warning" showIcon message="Re-entry model 尚未產生，Control 仍會正常跑；Re-entry fail-closed" description={model.error || '執行 python tools/train_target_taker_reentry_runtime_v1.py 產生 SAME_SIDE PUBLIC_ACTOR artifact，然後 Restart 8782。'} /> : null}
    <Row gutter={[12, 12]}>
      <Col xs={12} md={6}><Card><Statistic title="Sequence Net PnL" value={number(performance.netPnlUsdt) ?? 0} precision={3} prefix="$" /></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="Entry Win rate" value={(number(performance.winRate) ?? 0) * 100} precision={1} suffix="%" /></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="Total / Re-entry" value={`${performance.totalEntries ?? 0} / ${performance.reentryEntries ?? 0}`} /></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="Markets with re-entry" value={performance.marketsWithReentry ?? 0} /></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="Avg entries / market" value={number(performance.avgEntriesPerMarket) ?? 0} precision={2} /></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="Sequence ROI" value={(number(performance.netRoi) ?? 0) * 100} precision={1} suffix="%" /></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="Settled entries" value={performance.settledEntries ?? 0} /></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="Current sequence" value={sequence.length} /></Card></Col>
    </Row>
    <Card title="Re-entry Model / Current Decision"><Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }} bordered>
      <Descriptions.Item label="Task">{policy.task || 'SAME_SIDE_REENTRY_WITHIN_5S'}</Descriptions.Item>
      <Descriptions.Item label="Threshold">{pct(policy.probabilityThreshold, 1)}</Descriptions.Item>
      <Descriptions.Item label="Risk window">{`${policy.minReentryDelayMs ?? 250}–${policy.maxReentryDelayMs ?? 5000} ms`}</Descriptions.Item>
      <Descriptions.Item label="Feature count">{policy.requiredFeatureCount ?? model.features?.length ?? '—'}</Descriptions.Item>
      <Descriptions.Item label="Signal"><Tag color={diagnostic.signalStatus === 'OK' ? 'success' : 'default'}>{diagnostic.signalStatus || '—'}</Tag></Descriptions.Item>
      <Descriptions.Item label="Decision"><Tag color={diagnostic.decision === 'TRADE' ? 'success' : 'default'}>{diagnostic.decision || '—'}</Tag></Descriptions.Item>
      <Descriptions.Item label="P(reentry)">{pct(diagnostic.probability, 3)}</Descriptions.Item><Descriptions.Item label="Raw P">{pct(diagnostic.rawProbability, 3)}</Descriptions.Item>
      <Descriptions.Item label="Reason" span={2}>{diagnostic.reason || '—'}</Descriptions.Item><Descriptions.Item label="Side">{diagnostic.side || '—'}</Descriptions.Item>
      <Descriptions.Item label="Ask">{fmt(diagnostic.ask, 4)}</Descriptions.Item><Descriptions.Item label="Elapsed">{number(diagnostic.msSincePrevEntry) === null ? '—' : `${fmt(diagnostic.msSincePrevEntry, 0)} ms`}</Descriptions.Item>
      <Descriptions.Item label="Entries so far">{diagnostic.entryCountSoFar ?? '—'}</Descriptions.Item><Descriptions.Item label="Input">{`${diagnostic.availableFeatureCount ?? 0}/${diagnostic.requiredFeatureCount ?? policy.requiredFeatureCount ?? '—'}`}</Descriptions.Item>
      <Descriptions.Item label="Deployment market excluded">{state.reentryExcludedDeploymentMarketId ?? '—'}</Descriptions.Item>
    </Descriptions></Card>
    <Card title={`Current Entry Sequence · ${sequence.length} entries`}><Table rowKey={(row) => `${row.market_id}-${row.entry_ordinal}`} size="small" pagination={false} scroll={{ x: 900 }} dataSource={sequence} columns={sequenceColumns} /></Card>
    <Card title="Entry Ordinal Performance · #1 vs #2 vs #3 vs #4+"><Table rowKey="ordinal_bucket" size="small" pagination={false} dataSource={ordinalStats} columns={ordinalColumns} /></Card>
    <Card title="Recent Settled Sequence Entries"><Table rowKey={(row) => `${row.market_id}-${row.entry_ordinal}`} size="small" pagination={{ pageSize: 15, hideOnSinglePage: true }} scroll={{ x: 950 }} dataSource={recentEntries} columns={settledColumns} /></Card>
    <Card title="Recent Re-entry Decision Changes"><Table rowKey={(row, index) => `${row.market_id}-${row.decision_at_ms}-${index}`} size="small" pagination={{ pageSize: 15, hideOnSinglePage: true }} scroll={{ x: 950 }} dataSource={recentDecisions} columns={decisionColumns} /></Card>
  </Space>
}

export default function EbmStrategyTestReentryPage() {
  return <Space direction="vertical" size={14} style={{ width: '100%' }}><EbmStrategyTestPage /><EbmReentryPanel /></Space>
}
