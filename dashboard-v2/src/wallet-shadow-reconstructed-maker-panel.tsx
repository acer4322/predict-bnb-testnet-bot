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

function StrategySummary({ lab, title, v2 = false }: { lab: RowObject; title: string; v2?: boolean }) {
  const current = row(lab.current)
  const inventory = row(current.inventory)
  const performance = row(lab.performance)
  const policy = row(lab.policy)
  const pairedCoverage = num(inventory.pairedCoverage)
  const toxic = row(current.lastToxicFlow)
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
        <Descriptions.Item label="Concurrent depth">{text(policy.activeBandLevelsPerSide)} / side</Descriptions.Item>
        <Descriptions.Item label="庫存殘餘">{text(inventory.residualSide)} {num(inventory.deltaShares)?.toFixed(1) ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="Paired median">{pct(performance.pairedCoverageMedian)}</Descriptions.Item>
        {v2 ? <Descriptions.Item label="Adverse residual rate">{pct(performance.adverseResidualRate)}</Descriptions.Item> : null}
        {v2 ? <Descriptions.Item label="Toxic flow">{toxic.toxic === true ? `${text(toxic.pressureSide)} pressure → cancel ${text(toxic.cancelSide)}` : 'CLEAR'}</Descriptions.Item> : null}
        {v2 ? <Descriptions.Item label="撤單計數">{JSON.stringify(row(current.cancelCounts))}</Descriptions.Item> : null}
      </Descriptions>
      <Progress
        percent={(pairedCoverage ?? 0) * 100}
        status="active"
        format={() => `目前 paired ${pct(pairedCoverage)}`}
      />
    </Card>
  )
}

export default function WalletShadowReconstructedMakerPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const snapshot = row(service.data)
  const v1 = row(snapshot.reconstructedMakerRulesLab)
  const v2 = row(snapshot.reconstructedMakerRulesV2Lab)
  const v1Performance = row(v1.performance)
  const v2Performance = row(v2.performance)

  return (
    <Card title="Reconstructed Maker · V1 vs V2 Forward A/B" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="V2 專門測試 V1 最大失敗來源：把累積成交廣度誤當 concurrent depth，以及沒有因果撤單 / pair-first repair。"
        description="V1 完整保留。V2 仍使用相同 0.01 grid、18 shares、0.99 pair cap 與 later-ask-touch fill proxy，但縮成 5 層近價帶，加入 toxic-flow cancel、pair-first repair 與 3 秒 recenter。兩者皆 paper-only / forward-only。"
      />
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}>
          <StrategySummary lab={v1} title="V1 · GRID18_SOFTPOOL · Control" />
        </Col>
        <Col xs={24} xl={12}>
          <StrategySummary lab={v2} title="V2 · GRID18_SOFTPOOL · Causal lifecycle" v2 />
        </Col>
      </Row>

      <Card size="small" title="V2 最近市場" style={{ marginTop: 12 }}>
        <Table
          size="small"
          rowKey={(item) => text(item.market_id)}
          columns={columns}
          dataSource={rows(v2Performance.recentMarkets)}
          pagination={false}
          scroll={{ x: 1115 }}
        />
      </Card>
      <Card size="small" title="V1 最近市場（control）" style={{ marginTop: 12 }}>
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
