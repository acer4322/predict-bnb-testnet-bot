import { useMemo } from 'react'
import {
  Alert,
  Badge,
  Card,
  Col,
  Collapse,
  Descriptions,
  Empty,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { RadarChartOutlined } from '@ant-design/icons'
import type { TableColumnsType } from 'antd'
import { asNumber, asText, getPath, type ServiceSnapshot } from './store'
import MultiMarketOverview from './multi-market-overview'
import {
  fmtInt,
  fmtMoney,
  fmtMs,
  fmtPct,
  fmtPrice,
  fmtTime,
  isRow,
  rows,
  statusColor,
  type GenericRow,
  useDashboardModel,
} from './dashboard-model'

const { Title, Text } = Typography

function PageHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="page-heading">
      <div>
        <Title level={3}>{title}</Title>
        <Text type="secondary">{subtitle}</Text>
      </div>
      <Tag color="blue">READ ONLY</Tag>
    </div>
  )
}

function MetricCard({ title, value, suffix, detail }: { title: string; value: string; suffix?: string; detail?: string }) {
  return (
    <Card className="metric-card" size="small">
      <Statistic title={title} value={value} suffix={suffix} />
      {detail ? <Text type="secondary" className="metric-detail">{detail}</Text> : null}
    </Card>
  )
}

function StateTag({ value, fallback = 'UNKNOWN' }: { value: unknown; fallback?: string }) {
  const text = asText(value, fallback)
  return <Tag color={statusColor(text)}>{text}</Tag>
}

function boolLabel(value: unknown, yes = 'YES', no = 'NO') {
  return value === true ? yes : value === false ? no : '—'
}

function decisionBlock(polyGap: unknown) {
  const decision = getPath(polyGap, 'runtimeDecisionV44')
  return isRow(decision) ? decision : {}
}

function recentLiveRounds(polyGap: unknown) {
  return rows(getPath(polyGap, 'recentRounds'))
}

function recentLiveEvents(polyGap: unknown) {
  return rows(getPath(polyGap, 'recentEvents'))
}

function strategySummaryRows(strategies: unknown): GenericRow[] {
  const value = getPath(strategies, 'summaries')
  if (!isRow(value)) return []
  return Object.entries(value).map(([strategy, raw]) => ({ strategy, ...(isRow(raw) ? raw : {}) }))
}

export function OverviewPage() {
  const m = useDashboardModel()
  const seconds = asNumber(m.secondsLeft)
  const sourceAge = asNumber(m.sourceAge)
  const sourceFresh = sourceAge !== null && sourceAge <= 750
  const decision = decisionBlock(m.polyGap)

  return (
    <>
      <PageHeading title="總覽" subtitle="BTC Echtgeld hot path 保留在第一屏；下方比較 BTC / ETH / BNB 的 UP/DOWN 跨市場軌跡。" />
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={16}>
          <Card title={<Space><RadarChartOutlined /> BTC 5M Market</Space>} extra={<Tag color="geekblue">#{asText(m.marketId)}</Tag>}>
            <Row gutter={[12, 12]}>
              <Col xs={12} md={6}><MetricCard title="剩餘" value={seconds === null ? '—' : `${Math.max(0, Math.round(seconds))}`} suffix="s" /></Col>
              <Col xs={12} md={6}><MetricCard title="方向" value={asText(m.direction)} detail="Poly signal" /></Col>
              <Col xs={12} md={6}><MetricCard title="Edge" value={fmtPrice(m.edge)} /></Col>
              <Col xs={12} md={6}><MetricCard title="Source age" value={fmtMs(m.sourceAge)} detail={sourceAge === null ? '等待 freshness telemetry' : sourceFresh ? '新鮮' : '超過 750ms'} /></Col>
            </Row>
            <div className="market-grid">
              <div className="venue-panel">
                <Text type="secondary">POLYMARKET</Text>
                <div className="quote-row"><span>UP</span><strong>{fmtPrice(m.polyUp)}</strong></div>
                <div className="quote-row"><span>DOWN</span><strong>{fmtPrice(m.polyDown)}</strong></div>
                <div className="quote-foot">Receipt age {fmtMs(m.receiptAge)}</div>
              </div>
              <div className="venue-panel">
                <Text type="secondary">BINANCE PREDICTION</Text>
                <div className="quote-row"><span>UP Ask</span><strong>{fmtPrice(m.binanceUpAsk)}</strong></div>
                <div className="quote-row"><span>DOWN Ask</span><strong>{fmtPrice(m.binanceDownAsk)}</strong></div>
                <div className="quote-foot">Execution venue</div>
              </div>
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Echtgeld Position" className="position-card">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Side"><StateTag value={m.positionSide} fallback="FLAT" /></Descriptions.Item>
              <Descriptions.Item label="Shares">{fmtPrice(m.shares, 4)}</Descriptions.Item>
              <Descriptions.Item label="Entry">{fmtPrice(m.entry)}</Descriptions.Item>
              <Descriptions.Item label="PnL"><strong>{fmtMoney(m.pnl)}</strong></Descriptions.Item>
            </Descriptions>
          </Card>
          <Card title="Execution" className="stack-card">
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <StateTag value={getPath(decision, 'state') ?? m.status} fallback="WAITING" />
                <Tag>{asText(getPath(decision, 'source'), 'SYSTEM')}</Tag>
              </Space>
              <Text type="secondary">{asText(getPath(decision, 'code'), asText(m.status, 'WAITING'))}</Text>
              <Text type="secondary">{asText(m.version)}</Text>
              <div>
                <Text type="secondary">Source freshness</Text>
                <Progress percent={sourceAge === null ? 0 : Math.max(0, Math.min(100, 100 - (sourceAge / 750) * 100))} showInfo={false} status={sourceAge !== null && !sourceFresh ? 'exception' : 'normal'} />
              </div>
            </Space>
          </Card>
        </Col>
      </Row>
      <MultiMarketOverview service={m.services.multiMarket} />
    </>
  )
}

export function LivePage() {
  const m = useDashboardModel()
  const summary = getPath(m.polyGap, 'summary')
  const settings = getPath(m.polyGap, 'settings')
  const loss = getPath(m.polyGap, 'lossGuard')
  const active = getPath(m.polyGap, 'activeRound')
  const decision = decisionBlock(m.polyGap)
  const entryLatency = getPath(m.polyGap, 'lastEntryLatency')
  const exitLatency = getPath(m.polyGap, 'lastExitLatency')
  const idempotency = getPath(m.polyGap, 'executionIdempotencyV44')
  const liveRounds = recentLiveRounds(m.polyGap).slice(0, 12)
  const liveEvents = recentLiveEvents(m.polyGap).slice(0, 12)

  const roundColumns: TableColumnsType<GenericRow> = [
    { title: 'Round', key: 'round', width: 80, render: (_, row) => `#${asText(getPath(row, 'round_no', 'id'))}` },
    { title: 'Market', key: 'market', width: 100, render: (_, row) => asText(getPath(row, 'market_id')) },
    { title: 'Side', key: 'side', width: 75, render: (_, row) => <StateTag value={getPath(row, 'side')} /> },
    { title: 'State', key: 'state', width: 110, render: (_, row) => <StateTag value={getPath(row, 'state')} /> },
    { title: 'Entry', key: 'entry', render: (_, row) => fmtPrice(getPath(row, 'entry_quote_average', 'entry_binance_ask')) },
    { title: 'Exit', key: 'exit', render: (_, row) => fmtPrice(getPath(row, 'exit_quote_average')) },
    { title: 'PnL', key: 'pnl', render: (_, row) => <strong>{fmtMoney(getPath(row, 'pnl_usdt'))}</strong> },
    { title: 'Close reason', key: 'reason', render: (_, row) => asText(getPath(row, 'close_reason')) },
  ]

  const eventColumns: TableColumnsType<GenericRow> = [
    { title: 'Time', key: 'time', width: 100, render: (_, row) => fmtTime(getPath(row, 'at_ms')) },
    { title: 'Level', key: 'level', width: 80, render: (_, row) => <StateTag value={getPath(row, 'level')} /> },
    { title: 'Event', key: 'event', width: 250, render: (_, row) => <strong>{asText(getPath(row, 'event_type'))}</strong> },
    { title: 'Market', key: 'market', width: 100, render: (_, row) => asText(getPath(row, 'market_id')) },
    { title: 'Message', key: 'message', render: (_, row) => asText(getPath(row, 'message')) },
  ]

  return (
    <>
      <PageHeading title="Echtgeld" subtitle="V44 Echtgeld engine 的持倉、風控、執行延遲與最近 round；仍沒有任何寫入控制。" />
      <Alert type="info" showIcon message="Dashboard V2 目前只讀。Runtime、Stake、Strategy、Manual SELL、Shotgun、Leader Guard 都不能從這個頁面修改。" />

      <Row gutter={[12, 12]} className="section-row">
        <Col xs={12} lg={6}><MetricCard title="Runtime" value={boolLabel(getPath(settings, 'runtimeEnabled'), 'RUNNING', 'PAUSED')} detail={asText(m.status)} /></Col>
        <Col xs={12} lg={6}><MetricCard title="Realized PnL" value={fmtMoney(getPath(summary, 'pnlUsdt'))} detail={`${fmtInt(getPath(summary, 'closedRounds'))} closed rounds`} /></Col>
        <Col xs={12} lg={6}><MetricCard title="Win rate" value={fmtPct(getPath(summary, 'winRate'))} detail={`${fmtInt(getPath(summary, 'wins'))}W / ${fmtInt(getPath(summary, 'losses'))}L`} /></Col>
        <Col xs={12} lg={6}><MetricCard title="Loss headroom" value={fmtMoney(getPath(loss, 'remainingBeforePauseUsdt'))} detail={`Limit ${fmtMoney(getPath(loss, 'maximumLossUsdt'))}`} /></Col>
      </Row>

      <Row gutter={[12, 12]} className="section-row">
        <Col xs={24} xl={8}>
          <Card title="Runtime Decision" className="detail-card-height">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="State"><StateTag value={getPath(decision, 'state')} /></Descriptions.Item>
              <Descriptions.Item label="Source"><Tag>{asText(getPath(decision, 'source'))}</Tag></Descriptions.Item>
              <Descriptions.Item label="Code">{asText(getPath(decision, 'code'))}</Descriptions.Item>
              <Descriptions.Item label="New BUY affected">{boolLabel(getPath(decision, 'affectsNewBuy'))}</Descriptions.Item>
              <Descriptions.Item label="SELL affected">{boolLabel(getPath(decision, 'affectsSellExit'))}</Descriptions.Item>
              <Descriptions.Item label="Reason"><Text type="secondary">{asText(getPath(decision, 'reason'), '—')}</Text></Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="Current Position" className="detail-card-height">
            {isRow(active) ? (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Round">#{asText(getPath(active, 'round_no', 'id'))}</Descriptions.Item>
                <Descriptions.Item label="Side"><StateTag value={getPath(active, 'side')} /></Descriptions.Item>
                <Descriptions.Item label="State"><StateTag value={getPath(active, 'state')} /></Descriptions.Item>
                <Descriptions.Item label="Shares">{fmtPrice(getPath(active, 'shares'), 6)}</Descriptions.Item>
                <Descriptions.Item label="Entry">{fmtPrice(getPath(active, 'entry_quote_average', 'entry_binance_ask'))}</Descriptions.Item>
                <Descriptions.Item label="Entry edge">{fmtPrice(getPath(active, 'entry_edge'))}</Descriptions.Item>
                <Descriptions.Item label="Exit intent">{asText(getPath(active, 'exit_intent'))}</Descriptions.Item>
              </Descriptions>
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目前沒有 Echtgeld 持倉" />}
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="Risk / Settings" className="detail-card-height">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Stake">${fmtPrice(getPath(settings, 'stakeUsdt'), 2)}</Descriptions.Item>
              <Descriptions.Item label="Minimum edge">{fmtPrice(getPath(settings, 'minimumEdge'))}</Descriptions.Item>
              <Descriptions.Item label="Maximum loss"><StateTag value={getPath(loss, 'tripped') === true ? 'TRIPPED' : 'OK'} /></Descriptions.Item>
              <Descriptions.Item label="Net PnL since reset">{fmtMoney(getPath(loss, 'netPnlUsdt'))}</Descriptions.Item>
              <Descriptions.Item label="Current loss">{fmtMoney(getPath(loss, 'currentLossUsdt'))}</Descriptions.Item>
              <Descriptions.Item label="Halted market">{asText(getPath(m.polyGap, 'haltedMarketId'))}</Descriptions.Item>
              <Descriptions.Item label="Halt reason">{asText(getPath(m.polyGap, 'haltedReason'))}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} className="section-row">
        <Col xs={24} lg={12}>
          <Card title="Last Entry Latency">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Signal → Quote">{fmtMs(getPath(entryLatency, 'signalToQuoteStartMs'))}</Descriptions.Item>
              <Descriptions.Item label="Quote RTT">{fmtMs(getPath(entryLatency, 'quoteRttMs'), 1)}</Descriptions.Item>
              <Descriptions.Item label="Signal → Quote response">{fmtMs(getPath(entryLatency, 'signalToQuoteResponseMs'))}</Descriptions.Item>
              <Descriptions.Item label="Edge after quote">{fmtPrice(getPath(entryLatency, 'edgeAfterQuote'))}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Last Exit Latency">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Signal → Quote">{fmtMs(getPath(exitLatency, 'signalToQuoteStartMs'))}</Descriptions.Item>
              <Descriptions.Item label="Quote RTT">{fmtMs(getPath(exitLatency, 'quoteRttMs'), 1)}</Descriptions.Item>
              <Descriptions.Item label="Signal → Quote response">{fmtMs(getPath(exitLatency, 'signalToQuoteResponseMs'))}</Descriptions.Item>
              <Descriptions.Item label="In-flight actions">{fmtInt(rows(getPath(idempotency, 'inFlight')).length)}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Card title="Recent Echtgeld Rounds" className="section-row">
        <Table rowKey={(row) => asText(getPath(row, 'id'))} dataSource={liveRounds} columns={roundColumns} size="small" pagination={false} scroll={{ x: 950 }} locale={{ emptyText: '尚無 Echtgeld round' }} />
      </Card>
      <Card title="Recent Live Events" className="section-row">
        <Table rowKey={(row) => asText(getPath(row, 'id'))} dataSource={liveEvents} columns={eventColumns} size="small" pagination={false} scroll={{ x: 1000 }} locale={{ emptyText: '尚無 Live event' }} />
      </Card>
    </>
  )
}

export function PolyGapPage() {
  const m = useDashboardModel()
  const decision = decisionBlock(m.polyGap)
  const freshness = getPath(m.polyGap, 'sourceFreshnessV41')
  const currentFresh = getPath(freshness, 'current')
  const reentry = getPath(m.polyGap, 'reversalReentryV40')
  const reentryActive = getPath(reentry, 'active')
  const reentryCheck = getPath(reentry, 'lastExecutableCheck')
  const leader = getPath(m.polyGap, 'leaderGuardV38', 'leaderGuard')
  const tp = getPath(m.polyGap, 'takeProfitMarketLockV42')
  const confirmMs = asNumber(getPath(reentry, 'reentryConfirmMs')) ?? 2000
  const confirmStarted = asNumber(getPath(reentryActive, 'confirmationStartedAtMs'))
  const confirmPct = confirmStarted === null ? 0 : Math.max(0, Math.min(100, ((Date.now() - confirmStarted) / confirmMs) * 100))

  const relevantEvents = recentLiveEvents(m.polyGap)
    .filter((row) => /(REVERSAL|SOURCE|LEADER|TAKE_PROFIT|ENTRY_|EXIT_)/.test(asText(getPath(row, 'event_type'), '')))
    .slice(0, 20)
  const eventColumns: TableColumnsType<GenericRow> = [
    { title: 'Time', key: 'time', width: 100, render: (_, row) => fmtTime(getPath(row, 'at_ms')) },
    { title: 'Event', key: 'event', width: 290, render: (_, row) => <strong>{asText(getPath(row, 'event_type'))}</strong> },
    { title: 'Round', key: 'round', width: 80, render: (_, row) => asText(getPath(row, 'round_id')) },
    { title: 'Message', key: 'message', render: (_, row) => asText(getPath(row, 'message')) },
  ]

  return (
    <>
      <PageHeading title="Poly Gap" subtitle="把 V44 的決策、Poly freshness、V40 re-entry、V38 Leader Guard 與 V42 TP lock 拆開看。" />

      <Row gutter={[12, 12]}>
        <Col xs={12} lg={6}><MetricCard title="Decision" value={asText(getPath(decision, 'state'))} detail={`${asText(getPath(decision, 'source'))} · ${asText(getPath(decision, 'code'))}`} /></Col>
        <Col xs={12} lg={6}><MetricCard title="Direction" value={asText(m.direction)} detail={`Edge ${fmtPrice(m.edge)}`} /></Col>
        <Col xs={12} lg={6}><MetricCard title="Source age" value={fmtMs(getPath(currentFresh, 'sourceAgeMs') ?? m.sourceAge)} detail={`Policy ${asText(getPath(currentFresh, 'policy', 'currentPolicy') ?? getPath(freshness, 'currentPolicy'))}`} /></Col>
        <Col xs={12} lg={6}><MetricCard title="Transport age" value={fmtMs(getPath(currentFresh, 'transportAgeMs'))} detail={asText(getPath(currentFresh, 'timestampSource'))} /></Col>
      </Row>

      <Row gutter={[12, 12]} className="section-row">
        <Col xs={24} xl={6}>
          <Card title="Source Freshness" className="guard-card">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Policy"><StateTag value={getPath(currentFresh, 'policy') ?? getPath(freshness, 'currentPolicy')} /></Descriptions.Item>
              <Descriptions.Item label="Source age">{fmtMs(getPath(currentFresh, 'sourceAgeMs'))}</Descriptions.Item>
              <Descriptions.Item label="Receipt age">{fmtMs(getPath(currentFresh, 'quoteReceiptAgeMs'))}</Descriptions.Item>
              <Descriptions.Item label="Transport age">{fmtMs(getPath(currentFresh, 'transportAgeMs'))}</Descriptions.Item>
              <Descriptions.Item label="Max source">{fmtMs(getPath(freshness, 'maxSignalSourceAgeMs'))}</Descriptions.Item>
              <Descriptions.Item label="Blocks">{fmtInt(getPath(freshness, 'blocks'))}</Descriptions.Item>
              <Descriptions.Item label="Timestamp source">{asText(getPath(currentFresh, 'timestampSource'))}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={6}>
          <Card title="V40 Reversal Re-entry" className="guard-card">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Active"><StateTag value={isRow(reentryActive) && Object.keys(reentryActive).length ? 'ACTIVE' : 'INACTIVE'} /></Descriptions.Item>
              <Descriptions.Item label="Target">{asText(getPath(reentryActive, 'targetSide'))}</Descriptions.Item>
              <Descriptions.Item label="Last direction">{asText(getPath(reentryActive, 'lastDirection'))}</Descriptions.Item>
              <Descriptions.Item label="Last reset">{asText(getPath(reentryActive, 'lastResetReason'))}</Descriptions.Item>
              <Descriptions.Item label="Min edge">{fmtPrice(getPath(reentry, 'reentryMinimumExecutableEdge'))}</Descriptions.Item>
              <Descriptions.Item label="Last book edge">{fmtPrice(getPath(reentryCheck, 'bookEdge'))}</Descriptions.Item>
            </Descriptions>
            <Text type="secondary">2s confirmation</Text>
            <Progress percent={confirmPct} showInfo={false} />
          </Card>
        </Col>
        <Col xs={24} xl={6}>
          <Card title="V38 Leader Guard" className="guard-card">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Mode">{asText(getPath(leader, 'mode'))}</Descriptions.Item>
              <Descriptions.Item label="Enabled">{boolLabel(getPath(leader, 'enabled'))}</Descriptions.Item>
              <Descriptions.Item label="Regime"><StateTag value={getPath(leader, 'current.regime', 'leader.regime', 'regime')} /></Descriptions.Item>
              <Descriptions.Item label="Fresh">{boolLabel(getPath(leader, 'current.fresh', 'leader.fresh', 'fresh'))}</Descriptions.Item>
              <Descriptions.Item label="Age">{fmtMs(getPath(leader, 'current.ageMs', 'leader.ageMs', 'ageMs'))}</Descriptions.Item>
              <Descriptions.Item label="Market lock"><StateTag value={getPath(leader, 'currentMarketLocked') === true || isRow(getPath(leader, 'currentLock')) ? 'LOCKED' : 'OPEN'} /></Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={6}>
          <Card title="V42 Take-Profit Lock" className="guard-card">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Current market"><StateTag value={getPath(tp, 'currentMarketLocked') === true ? 'LOCKED' : 'OPEN'} /></Descriptions.Item>
              <Descriptions.Item label="Entry blocks">{fmtInt(getPath(tp, 'entryBlocks'))}</Descriptions.Item>
              <Descriptions.Item label="Source round">{asText(getPath(tp, 'currentLock.sourceRoundId', 'lastLock.sourceRoundId'))}</Descriptions.Item>
              <Descriptions.Item label="Trigger bid">{fmtPrice(getPath(tp, 'currentLock.triggerBid', 'lastLock.triggerBid'))}</Descriptions.Item>
              <Descriptions.Item label="TP price">{fmtPrice(getPath(tp, 'currentLock.takeProfitPrice', 'lastLock.takeProfitPrice'))}</Descriptions.Item>
              <Descriptions.Item label="Triggered">{fmtTime(getPath(tp, 'currentLock.triggeredAtMs', 'lastLock.triggeredAtMs'))}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Card title="Guard / Reversal Events" className="section-row">
        <Table rowKey={(row) => asText(getPath(row, 'id'))} dataSource={relevantEvents} columns={eventColumns} size="small" pagination={false} scroll={{ x: 900 }} locale={{ emptyText: '目前沒有 guard / reversal event' }} />
      </Card>
    </>
  )
}

export function StrategiesPage() {
  const m = useDashboardModel()
  const runtime = getPath(m.strategies, 'runtime')
  const lead = getPath(m.strategies, 'polyBinanceLeadValidation')
  const summaries = strategySummaryRows(m.strategies)
  const openPositions = rows(getPath(m.strategies, 'openPositions'))
  const lastFlip = getPath(m.strategies, 'lastFlip')

  const summaryColumns: TableColumnsType<GenericRow> = [
    { title: 'Strategy', key: 'strategy', width: 220, render: (_, row) => <strong>{asText(getPath(row, 'strategy'))}</strong> },
    { title: 'Trades', key: 'trades', render: (_, row) => fmtInt(getPath(row, 'trades', 'totalTrades')) },
    { title: 'Open', key: 'open', render: (_, row) => fmtInt(getPath(row, 'open', 'openTrades', 'openPositions')) },
    { title: 'Settled', key: 'settled', render: (_, row) => fmtInt(getPath(row, 'settled', 'closedTrades', 'settledTrades')) },
    { title: 'Wins', key: 'wins', render: (_, row) => fmtInt(getPath(row, 'wins')) },
    { title: 'Losses', key: 'losses', render: (_, row) => fmtInt(getPath(row, 'losses')) },
    { title: 'Win rate', key: 'winRate', render: (_, row) => fmtPct(getPath(row, 'winRate')) },
    { title: 'PnL', key: 'pnl', render: (_, row) => <strong>{fmtMoney(getPath(row, 'grossPnlUsdt', 'pnlUsdt'))}</strong> },
  ]

  const openColumns: TableColumnsType<GenericRow> = [
    { title: 'Strategy', key: 'strategy', render: (_, row) => asText(getPath(row, 'strategy')) },
    { title: 'Market', key: 'market', render: (_, row) => asText(getPath(row, 'binance_market_id', 'binanceMarketId')) },
    { title: 'Side', key: 'side', render: (_, row) => <StateTag value={getPath(row, 'side')} /> },
    { title: 'Entry', key: 'entry', render: (_, row) => fmtPrice(getPath(row, 'entry_price', 'entryPrice')) },
    { title: 'Shares', key: 'shares', render: (_, row) => fmtPrice(getPath(row, 'shares'), 4) },
    { title: 'Opened', key: 'opened', render: (_, row) => fmtTime(getPath(row, 'opened_at_ms', 'openedAtMs')) },
  ]

  return (
    <>
      <PageHeading title="Strategies" subtitle="8768 Paper 策略、Poly/Binance 對齊狀態與 lead-validation；以 2 秒慢速共享快照更新。" />

      {!m.strategyService.ok && !m.strategies ? <Alert type="warning" showIcon message="8768 strategy sidecar 離線" description={m.strategyService.error ?? '尚未收到資料'} /> : null}

      <Row gutter={[12, 12]}>
        <Col xs={12} lg={6}><MetricCard title="8768 Runtime" value={asText(getPath(runtime, 'status'))} detail={asText(getPath(runtime, 'error'), 'No error')} /></Col>
        <Col xs={12} lg={6}><MetricCard title="Alignment" value={getPath(runtime, 'aligned') === true ? 'ALIGNED' : 'NOT ALIGNED'} detail={`Skew ${fmtPrice(getPath(runtime, 'secondsLeftSkew'), 2)}s`} /></Col>
        <Col xs={12} lg={6}><MetricCard title="Poly → Binance Gap" value={fmtPrice(getPath(runtime, 'probabilityGap'))} detail={`${asText(getPath(runtime, 'polyDirection'))} / ${asText(getPath(runtime, 'binanceDirection'))}`} /></Col>
        <Col xs={12} lg={6}><MetricCard title="Leader Regime" value={asText(getPath(lead, 'currentRegime'))} detail={`${fmtInt(getPath(lead, 'sampleRows'))} rows · ${fmtInt(getPath(lead, 'sampledMarketsTotal'))} markets`} /></Col>
      </Row>

      <Row gutter={[12, 12]} className="section-row">
        <Col xs={24} lg={12}>
          <Card title="Current Cross-Venue Runtime">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Binance market">#{asText(getPath(runtime, 'binanceMarketId'))}</Descriptions.Item>
              <Descriptions.Item label="Poly slug">{asText(getPath(runtime, 'polyMarketSlug'))}</Descriptions.Item>
              <Descriptions.Item label="Poly UP">{fmtPrice(getPath(runtime, 'polyUpMid'))}</Descriptions.Item>
              <Descriptions.Item label="Binance UP">{fmtPrice(getPath(runtime, 'binanceUpMid'))}</Descriptions.Item>
              <Descriptions.Item label="Poly direction"><StateTag value={getPath(runtime, 'polyDirection')} /></Descriptions.Item>
              <Descriptions.Item label="Binance direction"><StateTag value={getPath(runtime, 'binanceDirection')} /></Descriptions.Item>
              <Descriptions.Item label="Last flip">{asText(getPath(lastFlip, 'from'))} → {asText(getPath(lastFlip, 'to'))}</Descriptions.Item>
              <Descriptions.Item label="Flip time">{fmtTime(getPath(lastFlip, 'atMs'))}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Lead Validation">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Regime"><StateTag value={getPath(lead, 'currentRegime')} /></Descriptions.Item>
              <Descriptions.Item label="Version">{asText(getPath(lead, 'version'))}</Descriptions.Item>
              <Descriptions.Item label="Sample rows">{fmtInt(getPath(lead, 'sampleRows'))}</Descriptions.Item>
              <Descriptions.Item label="Markets">{fmtInt(getPath(lead, 'sampledMarketsTotal'))}</Descriptions.Item>
              <Descriptions.Item label="Min coverage">{fmtMs(getPath(lead, 'minimumMarketCoverageMs'))}</Descriptions.Item>
              <Descriptions.Item label="Min samples">{fmtInt(getPath(lead, 'minimumMarketSamples'))}</Descriptions.Item>
              <Descriptions.Item label="Retention">{fmtInt(getPath(lead, 'retentionMarkets'))} markets</Descriptions.Item>
              <Descriptions.Item label="Detailed markets">{fmtInt(getPath(lead, 'detailedEventMarkets'))}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Card title="Paper Strategy Performance" className="section-row">
        <Table rowKey={(row) => asText(getPath(row, 'strategy'))} dataSource={summaries} columns={summaryColumns} pagination={false} size="small" scroll={{ x: 900 }} locale={{ emptyText: '8768 尚無 strategy summary' }} />
      </Card>
      <Card title={`Open Paper Positions · ${openPositions.length}`} className="section-row">
        <Table rowKey={(row) => asText(getPath(row, 'id'))} dataSource={openPositions} columns={openColumns} pagination={false} size="small" scroll={{ x: 800 }} locale={{ emptyText: '目前沒有 Paper 持倉' }} />
      </Card>
    </>
  )
}

export function TradesPage() {
  const m = useDashboardModel()
  const liveRounds = recentLiveRounds(m.polyGap)
  const actions = rows(getPath(m.polyGap, 'executionIdempotencyV44.recentActions'))
  const paper = rows(getPath(m.strategies, 'recentTrades'))

  const liveColumns: TableColumnsType<GenericRow> = [
    { title: 'Market', key: 'market', width: 100, render: (_, row) => asText(getPath(row, 'market_id')) },
    { title: 'Round', key: 'round', width: 75, render: (_, row) => asText(getPath(row, 'round_no')) },
    { title: 'Side', key: 'side', width: 70, render: (_, row) => <StateTag value={getPath(row, 'side')} /> },
    { title: 'State', key: 'state', width: 110, render: (_, row) => <StateTag value={getPath(row, 'state')} /> },
    { title: 'Entry', key: 'entry', render: (_, row) => fmtPrice(getPath(row, 'entry_quote_average', 'entry_binance_ask')) },
    { title: 'Exit', key: 'exit', render: (_, row) => fmtPrice(getPath(row, 'exit_quote_average')) },
    { title: 'Shares', key: 'shares', render: (_, row) => fmtPrice(getPath(row, 'shares'), 4) },
    { title: 'PnL', key: 'pnl', render: (_, row) => <strong>{fmtMoney(getPath(row, 'pnl_usdt'))}</strong> },
    { title: 'Reason', key: 'reason', width: 220, render: (_, row) => asText(getPath(row, 'close_reason', 'error_kind')) },
    { title: 'Updated', key: 'updated', width: 100, render: (_, row) => fmtTime(getPath(row, 'updated_at_ms')) },
  ]

  const actionColumns: TableColumnsType<GenericRow> = [
    { title: 'Action key', key: 'key', width: 260, render: (_, row) => <code>{asText(getPath(row, 'action_key'))}</code> },
    { title: 'Action', key: 'action', width: 80, render: (_, row) => <StateTag value={getPath(row, 'action')} /> },
    { title: 'Attempt', key: 'attempt', width: 80, render: (_, row) => asText(getPath(row, 'attempt_no')) },
    { title: 'Outcome', key: 'outcome', render: (_, row) => <StateTag value={getPath(row, 'outcome')} /> },
    { title: 'Order status', key: 'status', render: (_, row) => asText(getPath(row, 'order_status')) },
    { title: 'Order ID', key: 'order', render: (_, row) => asText(getPath(row, 'order_id')) },
    { title: 'Created', key: 'created', render: (_, row) => fmtTime(getPath(row, 'created_at_ms')) },
  ]

  const paperColumns: TableColumnsType<GenericRow> = [
    { title: 'Strategy', key: 'strategy', width: 210, render: (_, row) => <strong>{asText(getPath(row, 'strategy'))}</strong> },
    { title: 'Market', key: 'market', width: 100, render: (_, row) => asText(getPath(row, 'binance_market_id', 'binanceMarketId')) },
    { title: 'Side', key: 'side', width: 70, render: (_, row) => <StateTag value={getPath(row, 'side')} /> },
    { title: 'Status', key: 'status', width: 120, render: (_, row) => <StateTag value={getPath(row, 'status')} /> },
    { title: 'Entry', key: 'entry', render: (_, row) => fmtPrice(getPath(row, 'entry_price')) },
    { title: 'Exit', key: 'exit', render: (_, row) => fmtPrice(getPath(row, 'exit_price')) },
    { title: 'PnL', key: 'pnl', render: (_, row) => <strong>{fmtMoney(getPath(row, 'gross_pnl_usdt'))}</strong> },
    { title: 'Entry reason', key: 'entryReason', width: 220, render: (_, row) => asText(getPath(row, 'entry_reason')) },
    { title: 'Exit reason', key: 'exitReason', width: 200, render: (_, row) => asText(getPath(row, 'exit_reason')) },
  ]

  return (
    <>
      <PageHeading title="Trades" subtitle="把 Echtgeld rounds、V44 execution attempts 與 8768 Paper trades 分開，不再從 8766 猜資料路徑。" />
      <Tabs
        items={[
          {
            key: 'live',
            label: `Echtgeld Rounds (${liveRounds.length})`,
            children: <Card><Table rowKey={(row) => asText(getPath(row, 'id'))} dataSource={liveRounds} columns={liveColumns} size="small" pagination={{ pageSize: 15 }} scroll={{ x: 1100 }} locale={{ emptyText: '尚無 Echtgeld round' }} /></Card>,
          },
          {
            key: 'execution',
            label: `Execution Attempts (${actions.length})`,
            children: <Card><Table rowKey={(row) => asText(getPath(row, 'action_key'))} dataSource={actions} columns={actionColumns} size="small" pagination={false} scroll={{ x: 1000 }} locale={{ emptyText: 'V44 尚無 execution attempt' }} /></Card>,
          },
          {
            key: 'paper',
            label: `Paper Trades (${paper.length})`,
            children: <Card><Table rowKey={(row) => asText(getPath(row, 'id'))} dataSource={paper} columns={paperColumns} size="small" pagination={{ pageSize: 15 }} scroll={{ x: 1150 }} locale={{ emptyText: '8768 尚無 Paper trade' }} /></Card>,
          },
        ]}
      />
    </>
  )
}

function ServiceHealthCard({ label, service, detail }: { label: string; service: ServiceSnapshot; detail?: string }) {
  return (
    <Card title={label} className="service-health-card">
      <Space direction="vertical" size={5}>
        <Badge status={service.ok ? 'success' : 'error'} text={service.ok ? 'ONLINE' : 'OFFLINE'} />
        <Text>HTTP {service.latencyMs === null ? '—' : `${Math.round(service.latencyMs)} ms`}</Text>
        {detail ? <Text type="secondary">{detail}</Text> : null}
        {service.error ? <Text type="danger">{service.error}</Text> : null}
      </Space>
    </Card>
  )
}

function RawJson({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value ?? { status: 'no snapshot' }, null, 2)}</pre>
}

export function DiagnosticsPage() {
  const m = useDashboardModel()
  const poly = getPath(m.crossOracle, 'polymarket')
  const continuity = getPath(m.crossOracle, 'continuity')
  const gamma = getPath(m.crossOracle, 'gammaRedundantDiscovery', 'polymarket.gammaRedundantDiscovery')
  const decision = decisionBlock(m.polyGap)
  const lead = getPath(m.strategies, 'polyBinanceLeadValidation')
  const multiAssets = getPath(m.multiMarket, 'assets')

  const rawItems = [
    { key: '8766', label: '8766 realtime raw', children: <RawJson value={m.realtime} /> },
    { key: '8767', label: '8767 cross-oracle raw', children: <RawJson value={m.crossOracle} /> },
    { key: '8768', label: '8768 strategies raw', children: <RawJson value={m.strategies} /> },
    { key: '8769', label: '8769 V44 raw', children: <RawJson value={m.polyGap} /> },
    { key: '8770', label: '8770 multi-market raw', children: <RawJson value={m.multiMarket} /> },
  ]

  return (
    <>
      <PageHeading title="Diagnostics" subtitle="先看關鍵健康指標；完整 raw snapshot 收在頁面底部，需要時才展開。" />
      <Row gutter={[12, 12]}>
        <Col xs={24} md={12} xl={5}><ServiceHealthCard label="8766 · Binance realtime" service={m.services.realtime} detail={`Market #${asText(m.marketId)}`} /></Col>
        <Col xs={24} md={12} xl={5}><ServiceHealthCard label="8767 · Poly collector" service={m.services.crossOracle} detail={asText(getPath(poly, 'status'))} /></Col>
        <Col xs={24} md={12} xl={5}><ServiceHealthCard label="8768 · Strategies" service={m.strategyService} detail={asText(getPath(m.strategies, 'runtime.status'))} /></Col>
        <Col xs={24} md={12} xl={5}><ServiceHealthCard label="8769 · V44 Echtgeld" service={m.services.polyGap} detail={asText(getPath(decision, 'code') ?? m.status)} /></Col>
        <Col xs={24} md={12} xl={4}><ServiceHealthCard label="8770 · Multi-market" service={m.services.multiMarket} detail="BTC / ETH / BNB" /></Col>
      </Row>

      <Row gutter={[12, 12]} className="section-row">
        <Col xs={24} xl={8}>
          <Card title="Polymarket Feed / Discovery" className="diagnostic-detail-card">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Status"><StateTag value={getPath(poly, 'status')} /></Descriptions.Item>
              <Descriptions.Item label="Receipt age">{fmtMs(getPath(poly, 'ageMs'))}</Descriptions.Item>
              <Descriptions.Item label="Continuity gap"><StateTag value={getPath(continuity, 'gapActive') === true ? 'GAP ACTIVE' : 'OK'} /></Descriptions.Item>
              <Descriptions.Item label="Gap generation">{asText(getPath(continuity, 'gapGeneration'))}</Descriptions.Item>
              <Descriptions.Item label="Gamma method">{asText(getPath(gamma, 'lastMethod'))}</Descriptions.Item>
              <Descriptions.Item label="Gamma latency">{fmtMs(getPath(gamma, 'lastLatencyMs'), 1)}</Descriptions.Item>
              <Descriptions.Item label="Gamma last error">{asText(getPath(gamma, 'lastError'))}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="V44 Execution Diagnostics" className="diagnostic-detail-card">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Decision"><StateTag value={getPath(decision, 'state')} /></Descriptions.Item>
              <Descriptions.Item label="Code">{asText(getPath(decision, 'code'))}</Descriptions.Item>
              <Descriptions.Item label="Source">{asText(getPath(decision, 'source'))}</Descriptions.Item>
              <Descriptions.Item label="Last error">{asText(getPath(m.polyGap, 'lastError'))}</Descriptions.Item>
              <Descriptions.Item label="Halted market">{asText(getPath(m.polyGap, 'haltedMarketId'))}</Descriptions.Item>
              <Descriptions.Item label="Duplicate blocks">{fmtInt(getPath(m.polyGap, 'executionIdempotencyV44.duplicateInFlightBlocks'))}</Descriptions.Item>
              <Descriptions.Item label="Last action key"><code>{asText(getPath(m.polyGap, 'executionIdempotencyV44.lastActionKey'))}</code></Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="Research / Multi-market" className="diagnostic-detail-card">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Leader regime"><StateTag value={getPath(lead, 'currentRegime')} /></Descriptions.Item>
              <Descriptions.Item label="Lead samples">{fmtInt(getPath(lead, 'sampleRows'))}</Descriptions.Item>
              <Descriptions.Item label="Qualified markets">{fmtInt(getPath(lead, 'sampledMarketsTotal'))}</Descriptions.Item>
              <Descriptions.Item label="BTC observer"><StateTag value={getPath(multiAssets, 'BTC.binance.status')} /></Descriptions.Item>
              <Descriptions.Item label="ETH observer"><StateTag value={getPath(multiAssets, 'ETH.binance.status')} /></Descriptions.Item>
              <Descriptions.Item label="BNB observer"><StateTag value={getPath(multiAssets, 'BNB.binance.status')} /></Descriptions.Item>
              <Descriptions.Item label="8770 error">{asText(getPath(m.multiMarket, 'lastError'))}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Card title="Raw snapshots" className="section-row">
        <Collapse items={rawItems} />
      </Card>
    </>
  )
}
