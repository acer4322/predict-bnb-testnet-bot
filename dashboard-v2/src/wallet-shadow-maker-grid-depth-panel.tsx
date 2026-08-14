import { Alert, Card, Descriptions, Table, Tag, Typography } from 'antd'
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

function number(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function pct(value: unknown): string {
  const parsed = number(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(2)}%`
}

function money(value: unknown): string {
  const parsed = number(value)
  return parsed === null ? '—' : `${parsed >= 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

export default function WalletShadowMakerGridDepthPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).makerGridDepthLab)
  const evidence = row(lab.sharedEvidence)
  const variants = rows(lab.variants)

  const columns: TableColumnsType<RowObject> = [
    { title: '版本', key: 'name', width: 175, render: (_, item) => <><Tag color={item.levels === 3 ? 'cyan' : item.levels === 7 ? 'blue' : 'purple'}>{text(item.label)}</Tag>{text(item.cohort)}</> },
    { title: '每邊層數', dataIndex: 'levels', width: 85 },
    { title: '狀態', key: 'status', width: 145, render: (_, item) => <Tag color={item.status === 'ACTIVE' ? 'success' : 'warning'}>{text(item.status)}</Tag> },
    { title: '目前掛單 UP / DOWN', key: 'orders', width: 145, render: (_, item) => { const current = row(item.current); return `${text(current.upOrders, '0')} / ${text(current.downOrders, '0')}` } },
    { title: '市場 / 已結算', key: 'markets', width: 115, render: (_, item) => { const p = row(item.performance); return `${text(p.markets, '0')} / ${text(p.settledMarkets, '0')}` } },
    { title: 'Fills', key: 'fills', width: 70, render: (_, item) => text(row(item.performance).fills, '0') },
    { title: 'Win rate', key: 'win', width: 90, render: (_, item) => pct(row(item.performance).winRate) },
    { title: '成本', key: 'cost', width: 95, render: (_, item) => `$${number(row(item.performance).costUsdt)?.toFixed(2) ?? '—'}` },
    { title: 'Net PnL', key: 'pnl', width: 100, render: (_, item) => money(row(item.performance).netPnlUsdt) },
    { title: 'Net ROI', key: 'roi', width: 90, render: (_, item) => pct(row(item.performance).netRoi) },
  ]

  return (
    <Card title="Maker Grid Depth Lab · 3 / 7 / 15 層 Forward Paper" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="三版只改每邊同時掛單層數，其餘規則完全相同"
        description="每層 18 shares、間隔 0.01、每筆至少 1 USDT、雙邊同層價格和不超過 0.99；剩 30 秒停止新增與重掛。只有已休息至少 250ms 的 BID 被之後 observed Ask 觸及，才計為 paper fill。"
      />
      <Table
        style={{ marginTop: 12 }}
        rowKey={(item) => text(item.cohort)}
        dataSource={variants}
        columns={columns}
        size="small"
        pagination={false}
        scroll={{ x: 1120 }}
        locale={{ emptyText: '等待 8776 Maker Grid cohort 狀態' }}
      />
      <Descriptions size="small" column={1} style={{ marginTop: 12 }}>
        <Descriptions.Item label="層數依據">{text(evidence.depths)}</Descriptions.Item>
        <Descriptions.Item label="30 秒截止依據">{text(evidence.activeCutoff)}</Descriptions.Item>
        <Descriptions.Item label="不可觀測限制">{text(evidence.limitations)}</Descriptions.Item>
      </Descriptions>
      <Text type="secondary">Forward-only、paper-only；部署當下市場排除，從下一個完整 BTC 5M 市場開始。三版均不在 live allowlist。</Text>
    </Card>
  )
}
