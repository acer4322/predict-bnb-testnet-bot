import { Alert, Card, Col, Descriptions, Row, Statistic, Table, Tag, Typography } from 'antd'
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

function number(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function fixed(value: unknown, digits = 2): string {
  const parsed = number(value)
  return parsed === null ? '—' : parsed.toFixed(digits)
}

function pct(value: unknown): string {
  const parsed = number(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

function money(value: unknown): string {
  const parsed = number(value)
  if (parsed === null) return '—'
  return `${parsed > 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

function time(value: unknown): string {
  const parsed = number(value)
  if (parsed === null || parsed <= 0) return '—'
  return new Date(parsed).toLocaleTimeString('zh-TW', {
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

function decisionTag(value: unknown) {
  const decision = text(value).toUpperCase()
  if (decision === 'TRADE') return <Tag color="success">TRADE</Tag>
  if (decision === 'SKIP') return <Tag color="warning">SKIP</Tag>
  return <Tag>{decision}</Tag>
}

function resultTag(value: unknown) {
  const status = text(value).toUpperCase()
  if (status === 'WIN') return <Tag color="success">WIN</Tag>
  if (status === 'LOSS') return <Tag color="error">LOSS</Tag>
  if (status === 'FLAT') return <Tag color="warning">FLAT</Tag>
  return <Tag>{status}</Tag>
}

export default function WalletShadowSpotStrikePanel() {
  const service = useWalletShadowStore((state) => state.service)
  const snapshot = row(service.data)
  const forward = row(snapshot.spotStrikeForward)
  const performance = row(forward.performance)
  const config = row(forward.config)
  const availability = row(forward.availability)
  const retention = row(forward.retention)
  const currentDecision = row(forward.currentDecision)
  const currentEvent = row(forward.currentEvent)
  const similarity = row(forward.targetSimilarity)
  const latestSimilarity = row(similarity.latest)
  const targetAtDecision = row(latestSimilarity.targetAtDecision)
  const targetFinal = row(latestSimilarity.targetFinalSoFar)
  const storedRows = row(performance.storedRows)
  const breakdown = rows(performance.decisionBreakdown)
  const recentDecisions = rows(performance.recentDecisions)
  const recentMarkets = rows(performance.recentMarkets)

  const decisionColumns: TableColumnsType<RowObject> = [
    { title: '時間', key: 'time', width: 88, render: (_, item) => time(item.decision_at_ms) },
    { title: 'Market', key: 'market', width: 88, render: (_, item) => `#${text(item.market_id)}` },
    { title: '決策', key: 'decision', width: 82, render: (_, item) => decisionTag(item.decision) },
    { title: '方向', key: 'side', width: 72, render: (_, item) => sideTag(item.side) },
    { title: 'T-', key: 'seconds', width: 66, render: (_, item) => `${fixed(item.seconds_left, 1)}s` },
    { title: 'Ask', key: 'ask', width: 70, render: (_, item) => fixed(item.observed_ask, 3) },
    { title: '位移', key: 'bps', width: 82, render: (_, item) => `${fixed(item.displacement_bps, 3)} bps` },
    { title: '原因', key: 'reason', render: (_, item) => text(item.reason) },
  ]

  const resultColumns: TableColumnsType<RowObject> = [
    { title: 'Market', key: 'market', width: 88, render: (_, item) => `#${text(item.market_id)}` },
    { title: '方向', key: 'side', width: 72, render: (_, item) => sideTag(item.side) },
    { title: 'Winner', key: 'winner', width: 78, render: (_, item) => sideTag(item.winner) },
    { title: '結果', key: 'status', width: 78, render: (_, item) => resultTag(item.status) },
    { title: 'Ask', key: 'ask', width: 70, render: (_, item) => fixed(item.observed_ask, 3) },
    { title: 'Net PnL', key: 'pnl', width: 95, render: (_, item) => <strong>{money(item.net_pnl_usdt)}</strong> },
    { title: 'ROI', key: 'roi', width: 78, render: (_, item) => pct(item.net_roi) },
    { title: '結算', key: 'resolved', width: 88, render: (_, item) => time(item.resolved_at_ms) },
  ]

  const breakdownColumns: TableColumnsType<RowObject> = [
    { title: 'Decision', key: 'decision', width: 90, render: (_, item) => decisionTag(item.decision) },
    { title: 'Reason', dataIndex: 'reason', key: 'reason' },
    { title: 'Count', dataIndex: 'count', key: 'count', width: 80 },
  ]

  return (
    <Card title="Spot/Strike 模仿策略 · T-10s Forward Cohort" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="獨立 PAPER forward-test，不讀 Target 來做決策"
        description="在約 T-10s 讀 causal spot/startPrice 位移決定 UP/DOWN，再以 Predict 當下 ask 模擬 1 USDT Taker 進場並持有到官方結算。Target Taker 只在事後用來衡量模仿程度。"
        style={{ marginBottom: 12 }}
      />

      {availability.simulationDbAvailable === false ? (
        <Alert
          type="warning"
          showIcon
          message="Spot/Strike 資料源目前不可用，但 8776 其他 Shadow 組不會被拖下線"
          description={`${text(availability.simulationDbPath)} · ${text(availability.simulationDbError)}`}
          style={{ marginBottom: 12 }}
        />
      ) : null}

      <Row gutter={[12, 12]}>
        <Col xs={12} md={8} xl={3}><Statistic title="Net PnL" value={money(performance.netPnlUsdt)} /></Col>
        <Col xs={12} md={8} xl={3}><Statistic title="Win rate" value={pct(performance.winRate)} /></Col>
        <Col xs={12} md={8} xl={3}><Statistic title="Net ROI" value={pct(performance.netRoi)} /></Col>
        <Col xs={12} md={8} xl={3}><Statistic title="W / L" value={`${text(performance.wins, '0')} / ${text(performance.losses, '0')}`} /></Col>
        <Col xs={12} md={8} xl={3}><Statistic title="Trades / Decisions" value={`${text(performance.events, '0')} / ${text(performance.decisions, '0')}`} /></Col>
        <Col xs={12} md={8} xl={3}><Statistic title="Trade rate" value={pct(performance.tradeRate)} /></Col>
        <Col xs={12} md={8} xl={3}><Statistic title="Max drawdown" value={money(performance.maxDrawdownUsdt)} /></Col>
        <Col xs={12} md={8} xl={3}><Statistic title="Loss streak" value={number(performance.longestLossStreak) ?? 0} /></Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={8}>
          <Card size="small" title="本輪決策">
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="Decision">{currentDecision.decision ? decisionTag(currentDecision.decision) : <Tag>WAIT T-10s</Tag>}</Descriptions.Item>
              <Descriptions.Item label="Reason">{text(currentDecision.reason)}</Descriptions.Item>
              <Descriptions.Item label="Side">{sideTag(currentDecision.side)}</Descriptions.Item>
              <Descriptions.Item label="Seconds left">{fixed(currentDecision.secondsLeft, 2)}s</Descriptions.Item>
              <Descriptions.Item label="Observed ask">{fixed(currentDecision.observedAsk, 3)}</Descriptions.Item>
              <Descriptions.Item label="Effective cost">{fixed(currentEvent.effectiveUnitCost, 4)}</Descriptions.Item>
              <Descriptions.Item label="Spot displacement">{fixed(currentDecision.displacementBps, 4)} bps</Descriptions.Item>
              <Descriptions.Item label="Start / Spot">{fixed(currentDecision.startPrice, 2)} / {fixed(currentDecision.spotPrice, 2)}</Descriptions.Item>
              <Descriptions.Item label="Spot age / Predict age">{fixed(currentDecision.spotAgeMs, 0)} / {fixed(currentDecision.predictionReceiptAgeMs, 0)} ms</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        <Col xs={24} xl={8}>
          <Card size="small" title="Locked policy / 資料保存">
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="Cohort">{text(forward.cohort)}</Descriptions.Item>
              <Descriptions.Item label="Decision window">T-{fixed(config.decisionSeconds, 0)}s，最晚 +{fixed(config.maxDecisionLatenessSeconds, 0)}s</Descriptions.Item>
              <Descriptions.Item label="Max ask">{fixed(config.maxAsk, 2)}</Descriptions.Item>
              <Descriptions.Item label="Max input age">{fixed(config.maxInputAgeMs, 0)} ms</Descriptions.Item>
              <Descriptions.Item label="Stake / fee">${fixed(config.stakeUsdt, 2)} / {fixed(config.feeRateBps, 0)} bps</Descriptions.Item>
              <Descriptions.Item label="Retention">{text(retention.days, '7')} days · bounded</Descriptions.Item>
              <Descriptions.Item label="Stored D / E / R">{text(storedRows.decisions, '0')} / {text(storedRows.events, '0')} / {text(storedRows.results, '0')}</Descriptions.Item>
              <Descriptions.Item label="Pending settlement">{text(performance.pendingMarkets, '0')}</Descriptions.Item>
            </Descriptions>
            <Text type="secondary">Decisions / events / results 跟 Wallet Shadow 使用同一個 rolling retention，不會永久累積。</Text>
          </Card>
        </Col>

        <Col xs={24} xl={8}>
          <Card size="small" title="Target Taker 模仿度 · rolling window">
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="At decision residual">{pct(similarity.atDecisionMatchRate)} ({text(similarity.atDecisionMatches, '0')}/{text(similarity.atDecisionComparable, '0')})</Descriptions.Item>
              <Descriptions.Item label="Final residual">{pct(similarity.finalMatchRate)} ({text(similarity.finalMatches, '0')}/{text(similarity.finalComparable, '0')})</Descriptions.Item>
              <Descriptions.Item label="Capital at decision">{pct(similarity.capitalAtDecisionMatchRate)} ({text(similarity.capitalAtDecisionMatches, '0')}/{text(similarity.capitalAtDecisionComparable, '0')})</Descriptions.Item>
              <Descriptions.Item label="Capital final">{pct(similarity.capitalFinalMatchRate)} ({text(similarity.capitalFinalMatches, '0')}/{text(similarity.capitalFinalComparable, '0')})</Descriptions.Item>
              <Descriptions.Item label="Latest signal / target@decision">{sideTag(latestSimilarity.side)} / {sideTag(targetAtDecision.side)}</Descriptions.Item>
              <Descriptions.Item label="Latest target final-so-far">{sideTag(targetFinal.side)}</Descriptions.Item>
            </Descriptions>
            <Text type="secondary">相似度只做事後診斷；Target fills 不會驅動這個 cohort。</Text>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={9}>
          <Card size="small" title="決策 / Skip 原因">
            <Table
              rowKey={(item) => `${text(item.decision)}:${text(item.reason)}`}
              dataSource={breakdown}
              columns={breakdownColumns}
              size="small"
              pagination={false}
              locale={{ emptyText: '等待第一個 T-10s decision' }}
            />
          </Card>
        </Col>
        <Col xs={24} xl={15}>
          <Card size="small" title="最近 Decisions">
            <Table
              rowKey={(item) => `${text(item.market_id)}:${text(item.decision_at_ms)}`}
              dataSource={recentDecisions}
              columns={decisionColumns}
              size="small"
              pagination={{ pageSize: 8, hideOnSinglePage: true }}
              scroll={{ x: 900 }}
              locale={{ emptyText: '等待新版 observer 累積 forward decisions' }}
            />
          </Card>
        </Col>
      </Row>

      <Card size="small" title="最近已結算 Spot/Strike Paper 市場" style={{ marginTop: 12 }}>
        <Table
          rowKey={(item) => text(item.market_id)}
          dataSource={recentMarkets}
          columns={resultColumns}
          size="small"
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          scroll={{ x: 760 }}
          locale={{ emptyText: '等待第一批 forward trade 結算' }}
        />
      </Card>
    </Card>
  )
}
