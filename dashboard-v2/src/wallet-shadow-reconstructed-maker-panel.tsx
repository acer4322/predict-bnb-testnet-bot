import { Alert, Card, Descriptions, Progress, Statistic, Table, Tag } from 'antd'
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

export default function WalletShadowReconstructedMakerPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).reconstructedMakerRulesLab)
  const current = row(lab.current)
  const inventory = row(current.inventory)
  const performance = row(lab.performance)
  const policy = row(lab.policy)

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

  const pairedCoverage = num(inventory.pairedCoverage)
  return (
    <Card title="TARGET_MAKER_RULES_GRID18_SOFTPOOL_V1 — Forward Paper" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="依最新回推證據建立：雙邊一分網格、18 shares、廣域 rails、28 層動態區、軟性庫存池與 30 秒停止補掛。"
        description="此策略只使用公開因果資料。目標交易不驅動決策，Maker rebate 與排隊優先權不列入績效，且不在任何 live allowlist。"
      />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(8, minmax(115px, 1fr))', gap: 12, marginTop: 12 }}>
        <Statistic title="Forward 狀態" value={text(lab.status)} />
        <Statistic title="市場" value={`#${text(current.marketId)}`} />
        <Statistic title="UP / DOWN 掛單" value={`${text(current.upOrders, '0')} / ${text(current.downOrders, '0')}`} />
        <Statistic title="目前保留資金" value={usd(current.currentReservedUsdt)} />
        <Statistic title="Maker fills" value={num(performance.fills) ?? 0} />
        <Statistic title="已結算市場" value={num(performance.settledMarkets) ?? 0} />
        <Statistic title="淨 PnL" value={money(performance.netPnlUsdt)} />
        <Statistic title="淨 ROI" value={pct(performance.netRoi)} />
      </div>
      <Descriptions size="small" column={3} style={{ marginTop: 12 }}>
        <Descriptions.Item label="初始化">{text(current.initializationStatus)}</Descriptions.Item>
        <Descriptions.Item label="部署排除市場">#{text(lab.excludedDeploymentMarketId)}</Descriptions.Item>
        <Descriptions.Item label="尾盤凍結">{current.frozen === true ? 'YES' : 'NO'}</Descriptions.Item>
        <Descriptions.Item label="庫存殘餘">{text(inventory.residualSide)} {num(inventory.deltaShares)?.toFixed(1) ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="Grid / 單筆">{text(policy.grid)} / {text(policy.sharesPerOrder)} shares</Descriptions.Item>
        <Descriptions.Item label="Rails / 動態層數">{rows(policy.openingRails).join('–') || '0.06–0.94'} / {text(policy.activeBandLevelsPerSide)}</Descriptions.Item>
      </Descriptions>
      <Progress
        percent={(pairedCoverage ?? 0) * 100}
        status="active"
        format={() => `目前 paired coverage ${pct(pairedCoverage)}`}
      />
      <Table
        style={{ marginTop: 12 }}
        size="small"
        rowKey={(item) => text(item.market_id)}
        columns={columns}
        dataSource={rows(performance.recentMarkets)}
        pagination={false}
        scroll={{ x: 1115 }}
      />
    </Card>
  )
}
