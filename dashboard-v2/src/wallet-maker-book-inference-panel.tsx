import { Alert, Card, Col, Descriptions, Progress, Row, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useWalletLabHealthStore } from './wallet-lab-health-store'
import type { ServiceSnapshot } from './store'

type RowObject = Record<string, unknown>
const { Text } = Typography

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

function pct(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

function ms(value: unknown): string {
  const parsed = num(value)
  if (parsed === null) return '—'
  return parsed < 1000 ? `${parsed.toFixed(0)} ms` : `${(parsed / 1000).toFixed(2)} s`
}

function usd(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `$${parsed.toFixed(2)}`
}

function dateTime(value: unknown): string {
  const parsed = num(value)
  return parsed === null || parsed <= 0 ? '—' : new Date(parsed).toLocaleString('zh-TW', { hour12: false })
}

function side(value: unknown) {
  const valueText = text(value)
  return <Tag color={valueText === 'UP' ? 'success' : valueText === 'DOWN' ? 'error' : 'default'}>{valueText}</Tag>
}

function role(value: unknown) {
  const valueText = text(value).toUpperCase()
  return <Tag color={valueText === 'MAKER' ? 'blue' : valueText === 'TAKER' ? 'purple' : 'default'}>{valueText}</Tag>
}

function InferenceCard({ service, asset, port }: { service: ServiceSnapshot; asset: string; port: number }) {
  const data = row(service.data)
  const current = row(data.current)
  const storage = row(data.storage)
  const websocket = row(data.websocket)
  const inference = row(data.targetInference)
  const activity = row(data.targetActivity)
  const lifecycle = row(data.lifecycleInference)
  const capital = row(lifecycle.capitalLowerBound)
  const allocation = row(lifecycle.allocationDiagnostics)
  const roleSeparation = row(data.roleSeparation)
  const poll = row(activity.pollDiagnostics)
  const matched = num(inference.matched) ?? 0
  const total = num(inference.target_events ?? inference.targetEvents) ?? 0
  const matchRate = total > 0 ? matched / total : 0
  const hasV21 = lifecycle.consumableQuantityAllocation === true
  const bookFresh = num(current.sampleAgeMs) !== null && (num(current.sampleAgeMs) ?? Infinity) < 5000
  const activityFresh = num(activity.latestAgeMs) !== null && (num(activity.latestAgeMs) ?? Infinity) < 10_000

  const activityColumns: TableColumnsType<RowObject> = [
    { title: 'Time', dataIndex: 'event_ms', width: 165, render: dateTime },
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Role', dataIndex: 'role', width: 85, render: role },
    { title: 'Quote', dataIndex: 'quote_type', width: 75, render: (value) => <Tag>{text(value)}</Tag> },
    { title: 'Side', dataIndex: 'side', width: 75, render: side },
    { title: 'Price', dataIndex: 'price', width: 80, render: (value) => num(value)?.toFixed(4) ?? '—' },
    { title: 'Shares', dataIndex: 'shares', width: 85, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Observed delay', key: 'delay', width: 120, render: (_, item) => {
      const eventMs = num(item.event_ms)
      const observed = num(item.observed_at_ms)
      return eventMs !== null && observed !== null ? ms(Math.max(0, observed - eventMs)) : '—'
    } },
    { title: 'Order hash', dataIndex: 'order_hash', ellipsis: true },
  ]

  const targetColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Side', dataIndex: 'side', width: 70, render: side },
    { title: 'Price', dataIndex: 'target_price', width: 75 },
    { title: 'Shares', dataIndex: 'target_shares', width: 85, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Book', key: 'book', width: 110, render: (_, item) => `${text(item.native_book_side)} ${text(item.native_price)}` },
    { title: 'Status', dataIndex: 'status', width: 95, render: (value) => <Tag color={value === 'MATCHED' ? 'success' : 'warning'}>{text(value)}</Tag> },
    { title: 'Decrease', dataIndex: 'observed_decrease', width: 90, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Delay', dataIndex: 'event_delay_ms', width: 85, render: ms },
    { title: 'Confidence', dataIndex: 'match_confidence', width: 95, render: pct },
  ]

  const parentColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Side', dataIndex: 'target_side', width: 70, render: side },
    { title: 'Price', dataIndex: 'target_price', width: 70 },
    { title: 'Fills', dataIndex: 'target_fill_count', width: 60 },
    { title: 'Filled', dataIndex: 'target_filled_shares', width: 80, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Expected', dataIndex: 'expected_parent_shares', width: 85, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Fill alloc.', dataIndex: 'fill_allocation_coverage', width: 90, render: pct },
    { title: 'Placement', dataIndex: 'placement_coverage', width: 90, render: pct },
    { title: 'Resting', dataIndex: 'resting_ms', width: 85, render: ms },
    { title: 'After', dataIndex: 'post_action', width: 200, render: (value) => <Tag>{text(value)}</Tag> },
    { title: 'Delay', dataIndex: 'post_action_delay_ms', width: 85, render: ms },
    { title: 'Confidence', dataIndex: 'confidence', width: 95, render: pct },
  ]

  const cancelColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Side', dataIndex: 'target_side', width: 70, render: side },
    { title: 'Price', dataIndex: 'target_price', width: 70 },
    { title: 'Allocated', dataIndex: 'allocated_quantity', width: 90, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Resting', dataIndex: 'resting_ms', width: 85, render: ms },
    { title: 'After', dataIndex: 'post_action', width: 170 },
    { title: 'Reason', dataIndex: 'likely_reason', width: 170, render: (value) => <Tag color="warning">{text(value)}</Tag> },
    { title: 'Confidence', dataIndex: 'confidence', width: 90, render: pct },
  ]

  return (
    <Card title={`TARGET_MAKER_BOOK_INFERENCE · ${asset} 5M · ${port}`} style={{ marginTop: 12 }}>
      {!service.ok && !service.data ? (
        <Alert type="error" showIcon message={`${port} collector offline`} description={service.error || 'No /state response'} />
      ) : null}

      <Alert
        type={hasV21 ? 'success' : 'warning'}
        showIcon
        message={hasV21 ? 'V2.1 full-book consumable lifecycle 已啟用' : '尚未確認 V2.1 lifecycle payload'}
        description={hasV21
          ? `${asset} 與另一資產使用同一套 public full-book delta / consumable quantity lifecycle。MAKER 與 TAKER activity 都保留；只有 MAKER BID 進 anonymous resting-book inference。`
          : '若 collector 正常但此欄仍沒有 consumableQuantityAllocation=true，請檢查後端版本是否尚未重啟。'}
        style={{ marginBottom: 12 }}
      />

      <Row gutter={[10, 10]}>
        <Col xs={12} md={6} xl={3}><Statistic title="Status" value={text(data.status, service.ok ? 'CONNECTED' : 'OFFLINE')} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Market" value={`#${text(current.marketId)}`} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Book updates" value={num(storage.updates) ?? 0} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Book age" value={ms(current.sampleAgeMs)} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Maker events" value={num(activity.maker_events ?? activity.makerEvents) ?? 0} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Taker events" value={num(activity.taker_events ?? activity.takerEvents) ?? 0} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Matched Maker" value={matched} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Match rate" value={pct(matchRate)} /></Col>
      </Row>
      <Progress percent={matchRate * 100} format={() => `Maker fill ↔ book decrease ${(matchRate * 100).toFixed(1)}%`} />

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}>
          <Card size="small" title="Public full order-book capture">
            <Row gutter={[8, 8]}>
              <Col xs={12} md={8}><Statistic title="WebSocket" value={text(websocket.status)} /></Col>
              <Col xs={12} md={8}><Statistic title="Freshness" value={bookFresh ? 'FRESH' : 'STALE / WAIT'} /></Col>
              <Col xs={12} md={8}><Statistic title="Checkpoints" value={num(storage.checkpoints) ?? 0} /></Col>
              <Col xs={12} md={8}><Statistic title="Updates this run" value={num(storage.updatesWrittenThisRun) ?? 0} /></Col>
              <Col xs={12} md={8}><Statistic title="Level Δ this run" value={num(storage.levelChangesWrittenThisRun) ?? 0} /></Col>
              <Col xs={12} md={8}><Statistic title="Stored markets" value={num(storage.markets) ?? 0} /></Col>
            </Row>
            <Descriptions size="small" column={2} style={{ marginTop: 8 }}>
              <Descriptions.Item label="Last source">{dateTime(current.lastSourceMs)}</Descriptions.Item>
              <Descriptions.Item label="Last received">{dateTime(current.lastReceivedMs)}</Descriptions.Item>
              <Descriptions.Item label="Latest storage age">{ms(storage.latestAgeMs)}</Descriptions.Item>
              <Descriptions.Item label="DB">{num(storage.databaseBytes) === null ? '—' : `${(num(storage.databaseBytes)! / 1024 / 1024).toFixed(1)} MB`}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card size="small" title="Target Maker / Taker activity">
            <Row gutter={[8, 8]}>
              <Col xs={12} md={6}><Statistic title="Total" value={num(activity.total_events ?? activity.totalEvents) ?? 0} /></Col>
              <Col xs={12} md={6}><Statistic title="Maker shares" value={num(activity.maker_shares ?? activity.makerShares)?.toFixed(2) ?? '0'} /></Col>
              <Col xs={12} md={6}><Statistic title="Taker shares" value={num(activity.taker_shares ?? activity.takerShares)?.toFixed(2) ?? '0'} /></Col>
              <Col xs={12} md={6}><Statistic title="Latest" value={activityFresh ? 'FRESH' : ms(activity.latestAgeMs)} /></Col>
            </Row>
            <Descriptions size="small" column={2} style={{ marginTop: 8 }}>
              <Descriptions.Item label="MAKER boundary">{text(roleSeparation.MAKER, text(activity.makerMatchingBoundary))}</Descriptions.Item>
              <Descriptions.Item label="TAKER boundary">{text(roleSeparation.TAKER, 'retained separately; excluded from Maker lifecycle')}</Descriptions.Item>
              <Descriptions.Item label="Ledger poll age">{ms(poll.lastPollAgeMs)}</Descriptions.Item>
              <Descriptions.Item label="Source">8776 Official target ledger</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Table
        style={{ marginTop: 12 }}
        rowKey={(item) => text(item.source_leg_id)}
        size="small"
        columns={activityColumns}
        dataSource={rows(activity.recent)}
        pagination={false}
        scroll={{ x: 1000 }}
        title={() => `最近 Target Maker + Taker activity · ${asset}`}
      />

      {hasV21 ? (
        <>
          <Card size="small" title="V2.1 Parent lifecycle · consumable allocation" style={{ marginTop: 12 }}>
            <Row gutter={[8, 8]}>
              <Col xs={12} md={6} xl={3}><Statistic title="Parents" value={num(lifecycle.parents) ?? 0} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Markets reconciled" value={num(lifecycle.reconciledMarkets) ?? 0} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Parent placement" value={pct(lifecycle.parentPlacementRate)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Placement coverage" value={pct(lifecycle.averagePlacementCoverage)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Fill allocation" value={pct(lifecycle.averageFillAllocationCoverage)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Resting median" value={ms(lifecycle.medianRestingMs)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Multi-fill" value={pct(lifecycle.multiFillParentRate)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Capital LB p90" value={usd(capital.p90PeakUsdt)} /></Col>
            </Row>
            <Descriptions size="small" column={3} style={{ marginTop: 8 }}>
              <Descriptions.Item label="Allocations">{text(allocation.allocations, '0')}</Descriptions.Item>
              <Descriptions.Item label="Allocated shares">{num(allocation.allocated_shares)?.toFixed(1) ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="Public events used">{text(allocation.public_events, '0')}</Descriptions.Item>
            </Descriptions>
          </Card>
          <Table
            style={{ marginTop: 12 }}
            rowKey={(item) => text(item.parent_id)}
            size="small"
            columns={parentColumns}
            dataSource={rows(lifecycle.recentParents)}
            pagination={false}
            scroll={{ x: 1200 }}
            title={() => `最近 Target Maker parents · ${asset}`}
          />
          <Table
            style={{ marginTop: 12 }}
            rowKey={(item) => text(item.candidate_id)}
            size="small"
            columns={cancelColumns}
            dataSource={rows(lifecycle.recentCancelCandidates)}
            pagination={false}
            scroll={{ x: 1000 }}
            title={() => '最近 anonymous cancel candidates（ownership 未證明）'}
          />
        </>
      ) : null}

      <Table
        style={{ marginTop: 12 }}
        rowKey={(item) => text(item.leg_id)}
        size="small"
        columns={targetColumns}
        dataSource={rows(inference.recent)}
        pagination={false}
        scroll={{ x: 900 }}
        title={() => `最近 Target Maker fill matching · ${asset}`}
      />

      <Text type="secondary">
        {port} 是 read-only research collector；public CLOB level 是匿名 aggregate。Maker placement/cancel ownership 只做機率式推論，TAKER activity 不會被混進 Maker lifecycle，也不會驅動 live orders。
      </Text>
    </Card>
  )
}

export default function WalletMakerBookInferencePanel() {
  const btc = useWalletLabHealthStore((state) => state.makerBook8778)
  const eth = useWalletLabHealthStore((state) => state.makerBookEth8779)
  return (
    <>
      <InferenceCard service={btc} asset="BTC" port={8778} />
      <InferenceCard service={eth} asset="ETH" port={8779} />
    </>
  )
}
