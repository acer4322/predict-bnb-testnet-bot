import { Alert, Card, Descriptions, Statistic, Table, Tag, Typography } from 'antd'
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
  if (value === null || value === undefined || value === '') return null
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
  return parsed === null ? '—' : `${parsed > 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

export default function WalletShadowRecenteredPooledPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).makerInventoryTakerSharedLab)
  const variant = rows(lab.variants).find((item) => item.cohort === 'RECENTERED_POOLED_INVENTORY_V1')
  const current = row(variant?.current)
  const inventory = row(current.inventory)
  const plan = row(current.depthPlan)
  const performance = row(variant?.performance)
  const similarity = row(variant?.targetSimilarity)
  const policy = row(variant?.policy)
  const pooledPolicy = row(policy.pooledInventory)

  const recentColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 100, render: (value) => `#${text(value)}` },
    { title: 'Winner', dataIndex: 'winner', width: 80, render: (value) => <Tag>{text(value)}</Tag> },
    { title: '結果', dataIndex: 'status', width: 80, render: (value) => <Tag color={value === 'WIN' ? 'success' : value === 'LOSS' ? 'error' : 'default'}>{text(value)}</Tag> },
    { title: 'Maker / Taker fills', key: 'fills', width: 145, render: (_, item) => `${text(item.maker_fill_count, '0')} / ${text(item.taker_fill_count, '0')}` },
    { title: 'Maker PnL', dataIndex: 'maker_pnl_usdt', width: 105, render: money },
    { title: 'Taker PnL', dataIndex: 'taker_pnl_usdt', width: 105, render: money },
    { title: '總 ROI', dataIndex: 'net_roi', width: 90, render: pct },
    { title: '+1 tick', dataIndex: 'stress_1tick_roi', width: 90, render: pct },
    { title: '+2 ticks', dataIndex: 'stress_2tick_roi', width: 90, render: pct },
  ]

  return (
    <Card title="RECENTERED_POOLED_INVENTORY_V1 · Forward Paper" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="Maker 本身直接受 pooled inventory guard 控制"
        description="每側最多 3 層；一側多出至少 18 shares 或失衡達 8% 時，立即停止過重側並批次重置到不足側。舊三版與歷史成績不變；本版從部署後下一個完整市場才計分。"
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, minmax(130px, 1fr))', gap: 12, marginTop: 12 }}>
        <Statistic title="狀態" value={text(variant?.status)} />
        <Statistic title="目前配對覆蓋" value={pct(inventory.makerPairedCoverage)} />
        <Statistic title="已結算配對中位數" value={pct(performance.finalMakerPairedCoverageMedian)} />
        <Statistic title="已結算失衡中位數" value={pct(performance.finalMakerImbalanceMedian)} />
        <Statistic title="Maker PnL" value={money(performance.makerPnlUsdt)} />
        <Statistic title="總 ROI" value={pct(performance.netRoi)} />
      </div>

      <Descriptions size="small" column={3} style={{ marginTop: 12 }}>
        <Descriptions.Item label="目前 Market">#{text(current.marketId)}</Descriptions.Item>
        <Descriptions.Item label="掛單 UP / DOWN">{text(current.upOrders, '0')} / {text(current.downOrders, '0')}</Descriptions.Item>
        <Descriptions.Item label="深度 UP / DOWN">{text(plan.upLevels, '0')} / {text(plan.downLevels, '0')}</Descriptions.Item>
        <Descriptions.Item label="Maker Δ">{num(inventory.makerDelta)?.toFixed(1) ?? '—'} shares</Descriptions.Item>
        <Descriptions.Item label="Regime">{text(plan.regime)}</Descriptions.Item>
        <Descriptions.Item label="修正側">{text(inventory.correctionSide)}</Descriptions.Item>
        <Descriptions.Item label="市場 / 已結算">{text(performance.markets, '0')} / {text(performance.settledMarkets, '0')}</Descriptions.Item>
        <Descriptions.Item label="Maker / Taker fills">{text(performance.makerFills, '0')} / {text(performance.takerFills, '0')}</Descriptions.Item>
        <Descriptions.Item label="Target / Paper 配對">{pct(similarity.targetMakerPairedCoverage)} / {pct(similarity.paperMakerPairedCoverage)}</Descriptions.Item>
        <Descriptions.Item label="每側最大層數">{text(pooledPolicy.maximumConcurrentLevelsPerSide, '3')}</Descriptions.Item>
        <Descriptions.Item label="Reduce-only shares">{text(pooledPolicy.immediateReduceOnlyAtShares, '18')}</Descriptions.Item>
        <Descriptions.Item label="Reduce-only ratio">{pct(pooledPolicy.immediateReduceOnlyAtRatio)}</Descriptions.Item>
      </Descriptions>

      <Table
        style={{ marginTop: 12 }}
        rowKey={(item) => text(item.market_id)}
        dataSource={rows(performance.recentMarkets)}
        columns={recentColumns}
        size="small"
        pagination={false}
        scroll={{ x: 980 }}
        locale={{ emptyText: '等待部署後第一個完整市場結算' }}
      />
      <Text type="secondary">
        Paper-only、forward-only、無歷史回填；目標錢包事件僅供事後比較，不參與掛單或 Taker 決策。Maker fill 仍使用完整 18-share Ask-touch 壓力代理。
      </Text>
    </Card>
  )
}
