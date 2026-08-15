import { Alert, Card, Descriptions, Table, Tag, Typography } from 'antd'
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
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function pct(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(2)}%`
}

function money(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${parsed >= 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

function side(value: unknown) {
  const name = text(value).toUpperCase()
  return <Tag color={name === 'UP' ? 'success' : name === 'DOWN' ? 'error' : 'default'}>{name}</Tag>
}

export default function WalletShadowMakerInventorySharedPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).makerInventoryTakerSharedLab)
  const variants = rows(lab.variants)

  const columns: TableColumnsType<RowObject> = [
    {
      title: '版本', key: 'cohort', width: 245,
      render: (_, item) => <><Tag color={item.dynamicDepth ? 'purple' : 'blue'}>{text(item.label)}</Tag><Text code>{text(item.cohort)}</Text></>,
    },
    { title: '狀態', dataIndex: 'status', width: 175, render: (value) => <Tag color={value === 'ACTIVE' ? 'success' : 'warning'}>{text(value)}</Tag> },
    {
      title: '深度 UP / DOWN', key: 'depth', width: 145,
      render: (_, item) => { const plan = row(row(item.current).depthPlan); return `${text(plan.upLevels, '0')} / ${text(plan.downLevels, '0')}` },
    },
    {
      title: '報價偏斜 / Regime', key: 'skew', width: 205,
      render: (_, item) => { const plan = row(row(item.current).depthPlan); return `${text(plan.upPriceOffsetTicks, '0')} / ${text(plan.downPriceOffsetTicks, '0')} ticks · ${text(plan.regime)}` },
    },
    {
      title: 'Maker Δ / 配對率', key: 'inventory', width: 155,
      render: (_, item) => { const inv = row(row(item.current).inventory); return `${num(inv.makerDelta)?.toFixed(1) ?? '—'} / ${pct(inv.makerPairedCoverage)}` },
    },
    {
      title: '修正方向', key: 'correction', width: 100,
      render: (_, item) => side(row(row(item.current).inventory).correctionSide),
    },
    {
      title: 'Maker / Taker fills', key: 'fills', width: 145,
      render: (_, item) => { const p = row(item.performance); return `${text(p.makerFills, '0')} / ${text(p.takerFills, '0')}` },
    },
    {
      title: '交易 / 結算', key: 'markets', width: 115,
      render: (_, item) => { const p = row(item.performance); return `${text(p.tradedMarkets, '0')} / ${text(p.settledMarkets, '0')}` },
    },
    { title: '勝率', key: 'win', width: 85, render: (_, item) => pct(row(item.performance).winRate) },
    { title: 'Maker PnL', key: 'makerPnl', width: 105, render: (_, item) => money(row(item.performance).makerPnlUsdt) },
    { title: 'Taker PnL', key: 'takerPnl', width: 105, render: (_, item) => money(row(item.performance).takerPnlUsdt) },
    { title: '總 ROI', key: 'roi', width: 90, render: (_, item) => pct(row(item.performance).netRoi) },
    { title: '1 tick', key: 'stress1', width: 85, render: (_, item) => pct(row(item.performance).stress1TickRoi) },
    { title: '2 ticks', key: 'stress2', width: 85, render: (_, item) => pct(row(item.performance).stress2TickRoi) },
    { title: '目標結構相似度', key: 'similarity', width: 130, render: (_, item) => pct(row(item.targetSimilarity).overallStructuralSimilarity) },
  ]

  return (
    <Card title="Maker inventory → Taker decision 共享狀態 Forward Lab" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="測試 Maker 是否以雙邊平衡庫存作為基準，再讓低頻 Taker 修正偏差"
        description="Taker 只有在 Maker 基準已建立、Maker 與未修正總殘差同向，且 direction score 加至少一個 Futures 訊號同意時才成交。所有決策只使用當下公開市場資料；目標錢包事件不會驅動下單。"
      />
      <Table
        style={{ marginTop: 12 }}
        rowKey={(item) => text(item.cohort)}
        dataSource={variants}
        columns={columns}
        size="small"
        pagination={false}
        scroll={{ x: 1880 }}
        locale={{ emptyText: '等待 8776 V0.9 shared cohort' }}
      />
      <Descriptions size="small" column={1} style={{ marginTop: 12 }}>
        <Descriptions.Item label="固定對照">7 層 Maker；隔離檢驗共享庫存訊號本身的效果。</Descriptions.Item>
        <Descriptions.Item label="動態深度">3／7／15 層依一秒波動與 Predict spread 切換；庫存失衡時增加不足側、縮減過重側。</Descriptions.Item>
        <Descriptions.Item label="Reservation skew">第三版會降低庫存過重側報價、提高不足側報價；失衡達 12% 時暫停增加過重側，並在最後 150／60 秒逐步降至最多 7／3 層。</Descriptions.Item>
        <Descriptions.Item label="相似度口徑">事後比較 Maker/Taker 頻率、Maker 雙邊配對率、3 秒內同側時機與 Maker 價格 1 tick；目標事件不參與決策。</Descriptions.Item>
        <Descriptions.Item label="假設">{text(lab.hypothesis)}</Descriptions.Item>
        <Descriptions.Item label="比較目的">{text(lab.comparison)}</Descriptions.Item>
      </Descriptions>
      <Text type="secondary">Paper-only、forward-only、無歷史回填、不在 live allowlist，且 Taker 成本包含 200 bps fee。</Text>
    </Card>
  )
}
