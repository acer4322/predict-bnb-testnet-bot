import { Alert, Card, Col, Descriptions, Row, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useWalletShadowStore } from './wallet-shadow-store'

const { Text } = Typography
type RowObject = Record<string, unknown>

function row(value: unknown): RowObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
}

function rows(value: unknown): RowObject[] {
  return Array.isArray(value) ? value.filter((item): item is RowObject => Boolean(item && typeof item === 'object' && !Array.isArray(item))) : []
}

function num(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function pct(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

function money(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${parsed >= 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

function sideTag(value: unknown) {
  const side = text(value).toUpperCase()
  return <Tag color={side === 'UP' ? 'success' : side === 'DOWN' ? 'error' : 'default'}>{side}</Tag>
}

export default function WalletShadowTakerSignalV2Panel() {
  const service = useWalletShadowStore((state) => state.service)
  const snapshot = row(service.data)
  const strategy = row(snapshot.takerSignalConsensusV2)
  const config = row(strategy.config)
  const collector = row(strategy.collector)
  const current = row(strategy.current)
  const performance = row(strategy.performance)
  const targetSimilarity = row(strategy.targetSimilarity)
  const lastDecision = row(current.lastDecision)
  const recent = rows(performance.recentMarkets)
  const events = rows(current.events)
  const retired = row(snapshot.retiredCohorts)

  const eventColumns: TableColumnsType<RowObject> = [
    { title: '方向', key: 'side', width: 75, render: (_, item) => sideTag(item.side) },
    { title: '票數 UP / DOWN', key: 'votes', width: 130, render: (_, item) => `${text(item.upVotes, '0')} / ${text(item.downVotes, '0')}` },
    { title: 'Observed Ask', key: 'ask', width: 110, render: (_, item) => num(item.ask ?? item.observed_ask)?.toFixed(3) ?? '—' },
    { title: '本金', key: 'principal', width: 85, render: (_, item) => `$${num(item.principalUsdt ?? item.principal_usdt)?.toFixed(2) ?? '—'}` },
    { title: '含費成本', key: 'cost', width: 100, render: (_, item) => `$${num(item.totalCostUsdt ?? item.total_cost_usdt)?.toFixed(2) ?? '—'}` },
  ]

  const marketColumns: TableColumnsType<RowObject> = [
    { title: 'Market', key: 'market', width: 95, render: (_, item) => `#${text(item.market_id)}` },
    { title: 'Winner', key: 'winner', width: 80, render: (_, item) => sideTag(item.winner) },
    { title: 'Result', dataIndex: 'status', width: 85 },
    { title: 'Fills', dataIndex: 'fill_count', width: 70 },
    { title: 'Cost', key: 'cost', width: 95, render: (_, item) => `$${num(item.total_cost_usdt)?.toFixed(2) ?? '—'}` },
    { title: 'Net PnL', key: 'pnl', width: 100, render: (_, item) => money(item.net_pnl_usdt) },
    { title: 'Net ROI', key: 'roi', width: 85, render: (_, item) => pct(item.net_roi) },
    { title: '+1 tick', key: 'stress1', width: 85, render: (_, item) => pct(item.stress_1tick_roi) },
    { title: '+2 ticks', key: 'stress2', width: 85, render: (_, item) => pct(item.stress_2tick_roi) },
  ]

  return (
    <Card title="TAKER_SIGNAL_CONSENSUS_V2 · 訊號回推 Forward Paper" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="四訊號至少三項同向才下單；目標錢包事件不參與決策"
        description="固定每次 5 USDT 本金、計入 200 bps Taker fee；同向 10 秒、反向 2 秒冷卻。部署當下市場排除，從下一個完整 BTC 5M 市場開始計分。"
      />
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={12} md={6}><Statistic title="狀態" value={text(strategy.status)} /></Col>
        <Col xs={12} md={6}><Statistic title="Collector" value={text(collector.status)} /></Col>
        <Col xs={12} md={6}><Statistic title="市場 / 已結算" value={`${text(performance.markets, '0')} / ${text(performance.settledMarkets, '0')}`} /></Col>
        <Col xs={12} md={6}><Statistic title="Fills" value={num(performance.fills) ?? 0} /></Col>
        <Col xs={12} md={6}><Statistic title="Win rate" value={pct(performance.winRate)} /></Col>
        <Col xs={12} md={6}><Statistic title="Net PnL" value={money(performance.netPnlUsdt)} /></Col>
        <Col xs={12} md={6}><Statistic title="Net ROI" value={pct(performance.netRoi)} /></Col>
        <Col xs={12} md={6}><Statistic title="1 / 2 tick ROI" value={`${pct(performance.stress1TickRoi)} / ${pct(performance.stress2TickRoi)}`} /></Col>
        <Col xs={12} md={6}><Statistic title="策略 / Target 次數" value={`${text(targetSimilarity.strategyEvents, '0')} / ${text(targetSimilarity.targetParents, '0')}`} /></Col>
        <Col xs={12} md={6}><Statistic title="Target 5s 內配對" value={pct(targetSimilarity.nearestTargetWithin5sRate)} /></Col>
        <Col xs={12} md={6}><Statistic title="5s 配對方向命中" value={pct(targetSimilarity.sideMatchWithin5sRate)} /></Col>
        <Col xs={12} md={6}><Statistic title="5s 配對中位 lag" value={`${text(targetSimilarity.medianAbsoluteLagMsWithin5s)} ms`} /></Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}>
          <Card size="small" title="目前決策">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Decision">{text(lastDecision.decision)}</Descriptions.Item>
              <Descriptions.Item label="Reason">{text(lastDecision.reason)}</Descriptions.Item>
              <Descriptions.Item label="Side">{sideTag(lastDecision.side)}</Descriptions.Item>
              <Descriptions.Item label="Votes">{text(lastDecision.upVotes, '0')} UP / {text(lastDecision.downVotes, '0')} DOWN</Descriptions.Item>
              <Descriptions.Item label="Sample age">{text(lastDecision.sampleAgeMs)} ms</Descriptions.Item>
              <Descriptions.Item label="Seconds left">{text(lastDecision.secondsLeft)}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card size="small" title="固定策略規格">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Quorum">{text(config.quorum)} / 4</Descriptions.Item>
              <Descriptions.Item label="Stake">${text(config.stakeUsdt)}</Descriptions.Item>
              <Descriptions.Item label="Taker fee">{text(config.takerFeeBps)} bps</Descriptions.Item>
              <Descriptions.Item label="Max ask">{text(config.maxAsk)}</Descriptions.Item>
              <Descriptions.Item label="Same-side cooldown">{text(config.repeatCooldownMs)} ms</Descriptions.Item>
              <Descriptions.Item label="Flip cooldown">{text(config.flipCooldownMs)} ms</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="本市場 paper intents" style={{ marginTop: 12 }}>
        <Table rowKey={(item) => text(item.id)} dataSource={events} columns={eventColumns} size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 620 }} />
      </Card>
      <Card size="small" title="已結算市場" style={{ marginTop: 12 }}>
        <Table rowKey={(item) => text(item.market_id)} dataSource={recent} columns={marketColumns} size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 850 }} />
      </Card>
      <Text type="secondary">
        已退役且停止新增事件：{rows(retired.cohorts).length ? rows(retired.cohorts).map(String).join(', ') : Array.isArray(retired.cohorts) ? retired.cohorts.join(', ') : '舊測試 cohort'}。歷史資料保留供稽核。
      </Text>
    </Card>
  )
}
