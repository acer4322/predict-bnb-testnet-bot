import { Alert, Card, Col, Descriptions, Progress, Row, Statistic, Table, Tag } from 'antd'
import type { TableColumnsType } from 'antd'
import { useWalletShadowStore } from './wallet-shadow-store'

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
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '-'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function money(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '-' : `${parsed >= 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

function usd(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '-' : `$${parsed.toFixed(2)}`
}

function pct(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '-' : `${(parsed * 100).toFixed(2)}%`
}

const columns: TableColumnsType<RowObject> = [
  { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
  { title: 'Winner', dataIndex: 'winner', width: 80, render: (value) => <Tag color={value === 'UP' ? 'success' : 'error'}>{text(value)}</Tag> },
  { title: 'Status', dataIndex: 'status', width: 90 },
  { title: 'Fills', dataIndex: 'fill_count', width: 70 },
  { title: 'UP / DOWN shares', key: 'shares', width: 145, render: (_, item) => `${num(item.up_shares)?.toFixed(1) ?? '-'} / ${num(item.down_shares)?.toFixed(1) ?? '-'}` },
  { title: 'Paired coverage', dataIndex: 'paired_coverage', width: 125, render: pct },
  { title: 'Residual', key: 'residual', width: 120, render: (_, item) => `${text(item.residual_side)} ${num(item.residual_shares)?.toFixed(1) ?? '-'}` },
  { title: 'Cost', dataIndex: 'cost_usdt', width: 100, render: usd },
  { title: 'PnL', dataIndex: 'net_pnl_usdt', width: 105, render: money },
  { title: 'ROI', dataIndex: 'net_roi', width: 90, render: pct },
]

const stateTakerColumns: TableColumnsType<RowObject> = [
  { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
  { title: 'Winner', dataIndex: 'winner', width: 80, render: (value) => <Tag color={value === 'UP' ? 'success' : 'error'}>{text(value)}</Tag> },
  { title: 'Result', dataIndex: 'status', width: 90 },
  { title: 'Maker fills', dataIndex: 'maker_fill_count', width: 95 },
  { title: 'Taker fills', dataIndex: 'taker_fill_count', width: 90 },
  { title: 'Maker PnL', dataIndex: 'maker_pnl_usdt', width: 105, render: money },
  { title: 'Taker PnL', dataIndex: 'taker_pnl_usdt', width: 105, render: money },
  { title: 'Combined PnL', dataIndex: 'net_pnl_usdt', width: 115, render: money },
  { title: 'ROI', dataIndex: 'net_roi', width: 90, render: pct },
]

function StrategySummary({
  lab,
  title,
  v2 = false,
  lifecycle = false,
}: {
  lab: RowObject
  title: string
  v2?: boolean
  lifecycle?: boolean
}) {
  const current = row(lab.current)
  const inventory = row(current.inventory)
  const performance = row(lab.performance)
  const policy = row(lab.policy)
  const pairedCoverage = num(inventory.pairedCoverage)
  const toxic = row(current.lastToxicFlow)
  const lastPlan = row(current.lastPlan)
  const lifecycleCounts = row(current.lifecycleCounts)
  const depth = lifecycle ? policy.openingLevelsPerSide : policy.activeBandLevelsPerSide

  return (
    <Card size="small" title={title}>
      <Row gutter={[8, 8]}>
        <Col span={8}><Statistic title="狀態" value={text(lab.status)} /></Col>
        <Col span={8}><Statistic title="UP / DOWN 掛單" value={`${text(current.upOrders, '0')} / ${text(current.downOrders, '0')}`} /></Col>
        <Col span={8}><Statistic title="目前保留資金" value={usd(current.currentReservedUsdt)} /></Col>
        <Col span={8}><Statistic title="Fills" value={num(performance.fills) ?? 0} /></Col>
        <Col span={8}><Statistic title="Net PnL" value={money(performance.netPnlUsdt)} /></Col>
        <Col span={8}><Statistic title="Net ROI" value={pct(performance.netRoi)} /></Col>
      </Row>
      <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
        <Descriptions.Item label="初始化">{text(current.initializationStatus)}</Descriptions.Item>
        <Descriptions.Item label={lifecycle ? 'Opening depth' : 'Concurrent depth'}>{text(depth)} / side</Descriptions.Item>
        <Descriptions.Item label="庫存殘餘">{text(inventory.residualSide)} {num(inventory.deltaShares)?.toFixed(1) ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="Paired median">{pct(performance.pairedCoverageMedian)}</Descriptions.Item>
        {(v2 || lifecycle) ? <Descriptions.Item label="Adverse residual rate">{pct(performance.adverseResidualRate)}</Descriptions.Item> : null}
        {v2 ? <Descriptions.Item label="Toxic flow">{toxic.toxic === true ? `${text(toxic.pressureSide)} pressure → cancel ${text(toxic.cancelSide)}` : 'CLEAR'}</Descriptions.Item> : null}
        {v2 ? <Descriptions.Item label="撤單計數">{JSON.stringify(row(current.cancelCounts))}</Descriptions.Item> : null}
        {lifecycle ? <Descriptions.Item label="Pending post-fill plans">{text(current.pendingPostFillPlans, '0')}</Descriptions.Item> : null}
        {lifecycle ? <Descriptions.Item label="Lifecycle 計數">{JSON.stringify(lifecycleCounts)}</Descriptions.Item> : null}
        {lifecycle ? <Descriptions.Item label="最後決策">{text(lastPlan.resolvedAction ?? lastPlan.action)} {lastPlan.targetPriceTick !== undefined ? `→ tick ${text(lastPlan.targetPriceTick)}` : ''}</Descriptions.Item> : null}
      </Descriptions>
      <Progress
        percent={(pairedCoverage ?? 0) * 100}
        status="active"
        format={() => `目前 paired ${pct(pairedCoverage)}`}
      />
    </Card>
  )
}

function StateTakerSummary({ lab }: { lab: RowObject }) {
  const current = row(lab.current)
  const inventory = row(current.inventory)
  const lastDecision = row(current.lastDecision)
  const signal = row(lastDecision.signal)
  const performance = row(lab.performance)
  const remainingMs = num(current.eligibilityRemainingMs) ?? 0
  const imbalance = num(inventory.combinedImbalanceRatio)

  return (
    <Card size="small" title="V4 · Lifecycle-gated State Taker · Experiment">
      <Row gutter={[8, 8]}>
        <Col xs={12} md={6}><Statistic title="State" value={text(current.state)} /></Col>
        <Col xs={12} md={6}><Statistic title="Eligibility left" value={`${(remainingMs / 1000).toFixed(2)}s`} /></Col>
        <Col xs={12} md={6}><Statistic title="Taker fills" value={num(performance.takerFills) ?? 0} /></Col>
        <Col xs={12} md={6}><Statistic title="Combined ROI" value={pct(performance.netRoi)} /></Col>
        <Col xs={12} md={6}><Statistic title="Maker PnL" value={money(performance.makerPnlUsdt)} /></Col>
        <Col xs={12} md={6}><Statistic title="Taker PnL" value={money(performance.takerPnlUsdt)} /></Col>
        <Col xs={12} md={6}><Statistic title="Combined PnL" value={money(performance.netPnlUsdt)} /></Col>
        <Col xs={12} md={6}><Statistic title="Δ vs same Maker" value={money(performance.deltaVsSameMakerOnlyPnlUsdt)} /></Col>
      </Row>
      <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
        <Descriptions.Item label="Eligibility open / expire / consumed">{text(current.eligibilityOpens, '0')} / {text(current.eligibilityExpires, '0')} / {text(current.eligibilityConsumed, '0')}</Descriptions.Item>
        <Descriptions.Item label="Directional / repair Taker">{text(current.directionalTakers, '0')} / {text(current.inventoryRepairTakers, '0')}</Descriptions.Item>
        <Descriptions.Item label="Combined inventory">UP {num(inventory.combinedUpShares)?.toFixed(1) ?? '-'} / DOWN {num(inventory.combinedDownShares)?.toFixed(1) ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="Heavy / repair side">{text(inventory.heavySide)} / {text(inventory.repairSide)} · imbalance {pct(imbalance)}</Descriptions.Item>
        <Descriptions.Item label="Last decision">{text(lastDecision.decision)} · {text(lastDecision.reason)}</Descriptions.Item>
        <Descriptions.Item label="Signal / threshold">{num(signal.score)?.toFixed(3) ?? '-'} / {num(lastDecision.decisionThreshold)?.toFixed(3) ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="Time / price regime">{text(lastDecision.timeRegime)} · {text(lastDecision.priceBucket)} · {num(lastDecision.secondsLeft)?.toFixed(1) ?? '-'}s</Descriptions.Item>
        <Descriptions.Item label="Intent / side">{text(lastDecision.intent)} / {text(lastDecision.side)}</Descriptions.Item>
      </Descriptions>
    </Card>
  )
}

export default function WalletShadowReconstructedMakerPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const snapshot = row(service.data)
  const v1 = row(snapshot.reconstructedMakerRulesLab)
  const v2 = row(snapshot.reconstructedMakerRulesV2Lab)
  const v3 = row(snapshot.reconstructedMakerLifecycleV3Lab)
  const v4 = row(snapshot.lifecycleStateTakerV4Lab)
  const v1Performance = row(v1.performance)
  const v2Performance = row(v2.performance)
  const v3Performance = row(v3.performance)
  const v4Performance = row(v4.performance)
  const v3Available = Object.keys(v3).length > 0
  const v4Available = Object.keys(v4).length > 0

  return (
    <Card title="Reconstructed Maker · Forward Lifecycle A/B" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="Lifecycle V3 測 Maker fill-triggered state machine；State Taker V4 再用同一批 Maker fills 測 5 秒 Taker eligibility。"
        description="V4 不複製 Maker 路徑，也不使用 target-wallet events。自己的完整 18-share paper Maker fill 只會打開 5 秒 eligibility；fill 當下不能 Taker，下一個 public snapshot 起才依 time regime、combined inventory、6-feature public signal 與 fee-adjusted edge 決定 DIRECTIONAL_TAKER 或 INVENTORY_REPAIR_TAKER。"
      />
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={8}>
          <StrategySummary lab={v1} title="V1 · GRID18_SOFTPOOL · Original control" />
        </Col>
        <Col xs={24} xl={8}>
          <StrategySummary lab={v2} title="V2 · 3s recenter · Control" v2 />
        </Col>
        <Col xs={24} xl={8}>
          {v3Available
            ? <StrategySummary lab={v3} title="V3 · Fill-triggered lifecycle · Maker control" lifecycle />
            : <Card size="small" title="V3 · Fill-triggered lifecycle · Maker control"><Alert type="warning" showIcon message="V3 observer 尚未啟用" description="執行 switch-wallet-shadow-lifecycle-v3.ps1 後，這裡會顯示新的 forward-only cohort。" /></Card>}
        </Col>
      </Row>

      <div style={{ marginTop: 12 }}>
        {v4Available
          ? <StateTakerSummary lab={v4} />
          : <Card size="small" title="V4 · Lifecycle-gated State Taker · Experiment"><Alert type="warning" showIcon message="State Taker V4 尚未啟用" description="執行 switch-wallet-shadow-state-taker-v4.ps1。V4 會排除啟動當下市場，從下一個完整市場開始 forward-only A/B。" /></Card>}
      </div>

      {v4Available ? (
        <Card size="small" title="State Taker V4 最近市場 · same Maker path + Taker overlay" style={{ marginTop: 12 }}>
          <Table
            size="small"
            rowKey={(item) => text(item.market_id)}
            columns={stateTakerColumns}
            dataSource={rows(v4Performance.recentMarkets)}
            pagination={false}
            scroll={{ x: 960 }}
          />
        </Card>
      ) : null}
      {v3Available ? (
        <Card size="small" title="Lifecycle V3 最近市場 · same Maker-only control" style={{ marginTop: 12 }}>
          <Table
            size="small"
            rowKey={(item) => text(item.market_id)}
            columns={columns}
            dataSource={rows(v3Performance.recentMarkets)}
            pagination={false}
            scroll={{ x: 1115 }}
          />
        </Card>
      ) : null}
      <Card size="small" title="Maker Rules V2 最近市場（control）" style={{ marginTop: 12 }}>
        <Table
          size="small"
          rowKey={(item) => text(item.market_id)}
          columns={columns}
          dataSource={rows(v2Performance.recentMarkets)}
          pagination={false}
          scroll={{ x: 1115 }}
        />
      </Card>
      <Card size="small" title="V1 最近市場（historical control）" style={{ marginTop: 12 }}>
        <Table
          size="small"
          rowKey={(item) => text(item.market_id)}
          columns={columns}
          dataSource={rows(v1Performance.recentMarkets)}
          pagination={false}
          scroll={{ x: 1115 }}
        />
      </Card>
    </Card>
  )
}
