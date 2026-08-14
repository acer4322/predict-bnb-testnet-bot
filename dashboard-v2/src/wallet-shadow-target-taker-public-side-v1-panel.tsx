import { Alert, Card, Col, Descriptions, Progress, Row, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useWalletShadowStore } from './wallet-shadow-store'

const { Text } = Typography

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
  if (status === 'NO_TRADE') return <Tag>NO TRADE</Tag>
  return <Tag>{status}</Tag>
}

function decisionTag(value: unknown) {
  const decision = text(value).toUpperCase()
  if (decision === 'TRADE') return <Tag color="success">TRADE</Tag>
  if (decision === 'SKIP') return <Tag>SKIP</Tag>
  return <Tag>{decision}</Tag>
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
    <Card
      size="small"
      title={<><Tag color={color}>{title}</Tag> {text(cohort.status)}</>}
      style={{ height: '100%' }}
    >
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
        <Progress
          percent={confidencePercent}
          status={selectedProbability !== null && selectedProbability >= threshold ? 'success' : 'normal'}
          showInfo={false}
          size="small"
        />
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

export default function WalletShadowTargetTakerPublicSideV1Panel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).targetTakerPublicSideV1Lab)
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
        message="Target Taker Public Side V1 尚未出現在 8776 state"
        description="確認 8776 已升級到 PREDICT_WALLET_SHADOW_V0_24_TARGET_TAKER_PUBLIC_SIDE_V1，並使用 start-target-taker-public-side-v1.ps1 啟動。"
      />
    )
  }

  return (
    <Card title="Target Taker Public Side V1 · Frozen EBM Forward A/B" style={{ marginTop: 12 }}>
      <Alert
        type={model.loaded === true ? 'success' : 'warning'}
        showIcon
        message={model.loaded === true ? 'compact_side EBM 已載入，兩個 paper cohort 正在 forward 測試' : 'Side EBM 尚未載入，兩個 cohort 會 fail closed'}
        description={model.loaded === true
          ? 'SIDE_ONLY 直接測 frozen compact_side；HAZARD_SIDE 使用同一顆 EBM，但額外要求 Lifecycle V3 自己的 paper Maker fill 後 5 秒 time+price gate。固定 $1 sizing，Target 即時成交不能觸發策略。'
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
        A/B 主要看 SIDE_ONLY vs HAZARD_SIDE 的 forward win rate、Net PnL、ROI、drawdown 與 trade rate。EBM probability 是 class-weighted model score，只拿來做固定門檻與排序，不宣稱是校準後真實勝率。Paper only；不在任何 live allowlist。
      </Text>
    </Card>
  )
}
