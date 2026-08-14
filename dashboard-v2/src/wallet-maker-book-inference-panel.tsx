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

function side(value: unknown) {
  const valueText = text(value)
  return <Tag color={valueText === 'UP' ? 'success' : valueText === 'DOWN' ? 'error' : 'default'}>{valueText}</Tag>
}

function InferenceCard({ service, asset, port }: { service: ServiceSnapshot; asset: string; port: number }) {
  const data = row(service.data)
  const current = row(data.current)
  const storage = row(data.storage)
  const inference = row(data.targetInference)
  const lifecycle = row(data.lifecycleInference)
  const capital = row(lifecycle.capitalLowerBound)
  const allocation = row(lifecycle.allocationDiagnostics)
  const matched = num(inference.matched) ?? 0
  const total = num(inference.target_events ?? inference.targetEvents) ?? 0
  const matchRate = total > 0 ? matched / total : 0
  const hasV21 = asset === 'BTC' && lifecycle.consumableQuantityAllocation === true

  const targetColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Side', dataIndex: 'side', width: 70, render: side },
    { title: 'Price', dataIndex: 'target_price', width: 75 },
    { title: 'Shares', dataIndex: 'target_shares', width: 85, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Book', key: 'book', width: 100, render: (_, item) => `${text(item.native_book_side)} ${text(item.native_price)}` },
    { title: 'Status', dataIndex: 'status', width: 90, render: (value) => <Tag color={value === 'MATCHED' ? 'success' : 'warning'}>{text(value)}</Tag> },
    { title: 'Decrease', dataIndex: 'observed_decrease', width: 90, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Delay', dataIndex: 'event_delay_ms', width: 85, render: ms },
    { title: 'Confidence', dataIndex: 'match_confidence', width: 95, render: pct },
  ]

  const parentColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Side', dataIndex: 'target_side', width: 70, render: side },
    { title: 'Price', dataIndex: 'target_price', width: 70 },
    { title: 'Fills', dataIndex: 'target_fill_count', width: 60 },
    { title: 'Filled shares', dataIndex: 'target_filled_shares', width: 100, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Expected parent', dataIndex: 'expected_parent_shares', width: 105, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Fill alloc.', dataIndex: 'fill_allocation_coverage', width: 90, render: pct },
    { title: 'Placement alloc.', dataIndex: 'placement_coverage', width: 105, render: pct },
    { title: 'Resting', dataIndex: 'resting_ms', width: 85, render: ms },
    { title: 'Next parent', dataIndex: 'post_action', width: 230, render: (value) => <Tag>{text(value)}</Tag> },
    { title: 'Delay', dataIndex: 'post_action_delay_ms', width: 85, render: ms },
    { title: 'Multi-fill', dataIndex: 'multi_fill_parent', width: 80, render: (value) => value ? <Tag color="blue">YES</Tag> : <Tag>NO</Tag> },
    { title: 'Confidence', dataIndex: 'confidence', width: 95, render: pct },
  ]

  const reasonColumns: TableColumnsType<RowObject> = [
    { title: 'Speculative cancel reason', dataIndex: 'reason' },
    { title: 'Candidates', dataIndex: 'count', width: 95 },
    { title: 'Allocated shares', dataIndex: 'allocatedShares', width: 120, render: (value) => num(value)?.toFixed(1) ?? '—' },
    { title: 'Avg confidence', dataIndex: 'averageConfidence', width: 110, render: pct },
  ]

  const cancelColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Side', dataIndex: 'target_side', width: 70, render: side },
    { title: 'Price', dataIndex: 'target_price', width: 70 },
    { title: 'Allocated qty', dataIndex: 'allocated_quantity', width: 100, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: 'Resting', dataIndex: 'resting_ms', width: 85, render: ms },
    { title: 'After', dataIndex: 'post_action', width: 175 },
    { title: 'Reason', dataIndex: 'likely_reason', width: 170, render: (value) => <Tag color="warning">{text(value)}</Tag> },
    { title: 'Pressure', dataIndex: 'pressure_side', width: 85 },
    { title: 'T-left', dataIndex: 'seconds_left', width: 75, render: (value) => num(value) === null ? '—' : `${num(value)?.toFixed(1)}s` },
    { title: 'Confidence', dataIndex: 'confidence', width: 90, render: pct },
  ]

  return (
    <Card title={`TARGET_MAKER_BOOK_INFERENCE · ${asset} 5M · ${port}`} style={{ marginTop: 12 }}>
      <Alert
        type={hasV21 ? 'success' : 'info'}
        showIcon
        message={hasV21 ? 'V2.1：可消耗 quantity + parent lifecycle 已啟用' : '完整公開深度只能做機率式目標掛單配對'}
        description={hasV21
          ? '同一筆 public +depth / -depth 不能再無限重複配對；quantity 被配置後會扣除 remaining。相同 order hash 的多段 target fills 先合併成 parent，再計算 placement、partial/multi-fill、下一張 parent 的 refill/reprice。匿名 cancel 仍只標 speculative。'
          : 'MATCHED 代表目標 Maker fill 附近存在同價位 aggregate depth decrease，不等於交易所證明匿名 resting order 的身分。'}
      />

      <Row gutter={[10, 10]} style={{ marginTop: 12 }}>
        <Col xs={12} md={6} xl={3}><Statistic title="Status" value={text(data.status, service.ok ? 'CONNECTED' : 'OFFLINE')} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Market" value={`#${text(current.marketId)}`} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Book updates" value={num(storage.updates) ?? 0} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Target events" value={total} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Matched" value={matched} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="Match rate" value={pct(matchRate)} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="High confidence" value={num(inference.high_confidence ?? inference.highConfidence) ?? 0} /></Col>
        <Col xs={12} md={6} xl={3}><Statistic title="DB" value={num(storage.databaseBytes) === null ? '—' : `${(num(storage.databaseBytes)! / 1024 / 1024).toFixed(1)} MB`} /></Col>
      </Row>
      <Progress percent={matchRate * 100} format={() => `Target fill book match ${(matchRate * 100).toFixed(1)}%`} />

      <Descriptions size="small" column={3} style={{ marginTop: 8 }}>
        <Descriptions.Item label="Version">{text(data.version)}</Descriptions.Item>
        <Descriptions.Item label="WebSocket">{text(row(data.websocket).status)}</Descriptions.Item>
        <Descriptions.Item label="Book age">{ms(current.sampleAgeMs)}</Descriptions.Item>
      </Descriptions>

      {hasV21 && (
        <>
          <Card size="small" title="V2.1 Parent lifecycle · consumable allocation" style={{ marginTop: 12 }}>
            <Row gutter={[8, 8]}>
              <Col xs={12} md={6} xl={3}><Statistic title="Parents" value={num(lifecycle.parents) ?? 0} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Markets reconciled" value={num(lifecycle.reconciledMarkets) ?? 0} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Parent placement" value={pct(lifecycle.parentPlacementRate)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Placement coverage" value={pct(lifecycle.averagePlacementCoverage)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Fill allocation" value={pct(lifecycle.averageFillAllocationCoverage)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Resting median" value={ms(lifecycle.medianRestingMs)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Resting p90" value={ms(lifecycle.p90RestingMs)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Multi-fill parent" value={pct(lifecycle.multiFillParentRate)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Confirmed refill" value={pct(lifecycle.confirmedSamePriceRefillRate)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Confirmed reprice" value={pct(lifecycle.confirmedRepriceRate)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Observed parent ≈18" value={pct(lifecycle.observedParentFilledNear18Rate)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Placement supports 18" value={pct(lifecycle.placementSupports18Rate)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Median parent filled" value={num(lifecycle.medianObservedParentFilledShares)?.toFixed(2) ?? '—'} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Capital LB median" value={usd(capital.medianPeakUsdt)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Capital LB p90" value={usd(capital.p90PeakUsdt)} /></Col>
              <Col xs={12} md={6} xl={3}><Statistic title="Cancel candidates" value={num(lifecycle.cancelCandidates) ?? 0} /></Col>
            </Row>
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 10 }}
              message="V2 many-to-one lifecycle 已退休"
              description={`${text(lifecycle.v2Correction)}。Capital 仍是 lower bound：未成交或無法歸屬目標的 resting orders 不會硬算進目標資金。`}
            />
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
            scroll={{ x: 1450 }}
            title={() => '最近 Target Maker parents'}
          />

          <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
            <Col xs={24} xl={9}>
              <Table
                rowKey={(item) => text(item.reason)}
                size="small"
                columns={reasonColumns}
                dataSource={rows(lifecycle.cancelReasonBreakdown)}
                pagination={false}
                title={() => 'Speculative cancel reason · quantity-deduped'}
              />
            </Col>
            <Col xs={24} xl={15}>
              <Table
                rowKey={(item) => text(item.candidate_id)}
                size="small"
                columns={cancelColumns}
                dataSource={rows(lifecycle.recentCancelCandidates)}
                pagination={false}
                scroll={{ x: 1100 }}
                title={() => '最近匿名 cancel candidates（ownership 未證明）'}
              />
            </Col>
          </Row>
        </>
      )}

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
        8778/8779 都是 read-only research collectors；public CLOB level 是匿名 aggregate，任何 placement / cancel ownership 都只能做機率式推論，不會驅動 live orders。
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
