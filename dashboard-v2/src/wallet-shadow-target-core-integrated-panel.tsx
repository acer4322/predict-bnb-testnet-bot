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

export default function WalletShadowTargetCoreIntegratedPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).makerInventoryTakerSharedLab)
  const variant = rows(lab.variants).find((item) => item.cohort === 'TARGET_CORE_INTEGRATED_V1')
  const current = row(variant?.current)
  const inventory = row(current.inventory)
  const plan = row(current.depthPlan)
  const decision = row(current.lastDecision)
  const signal = row(decision.signal)
  const performance = row(variant?.performance)
  const policy = row(row(variant?.policy).targetCoreIntegrated)
  const makerPolicy = row(policy.maker)
  const takerPolicy = row(policy.taker)

  const recentColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Winner', dataIndex: 'winner', width: 75, render: (value) => <Tag>{text(value)}</Tag> },
    { title: '結果', dataIndex: 'status', width: 75, render: (value) => <Tag color={value === 'WIN' ? 'success' : value === 'LOSS' ? 'error' : 'default'}>{text(value)}</Tag> },
    { title: 'Maker / Taker', key: 'fills', width: 125, render: (_, item) => `${text(item.maker_fill_count, '0')} / ${text(item.taker_fill_count, '0')}` },
    { title: 'Maker PnL', dataIndex: 'maker_pnl_usdt', width: 105, render: money },
    { title: 'Taker PnL', dataIndex: 'taker_pnl_usdt', width: 105, render: money },
    { title: '總 PnL', dataIndex: 'net_pnl_usdt', width: 105, render: money },
    { title: 'ROI', dataIndex: 'net_roi', width: 90, render: pct },
    { title: '+1 tick', dataIndex: 'stress_1tick_roi', width: 90, render: pct },
    { title: '+2 ticks', dataIndex: 'stress_2tick_roi', width: 90, render: pct },
  ]

  return (
    <Card title="TARGET_CORE_INTEGRATED_V1 · Forward Paper" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="Maker 軟庫存走廊與自主 Taker 共用一份風險狀態"
        description="Maker 保留 18-share、一分網格與配對價格上限；±54 shares 內維持雙邊深度，54–108 柔性傾斜，超過 108 才缺口修復。Taker 只使用新鮮公開微結構連續分數與含費 edge，根據信心、價格優勢及共享庫存自動配置 1–15 USDT。"
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(125px, 1fr))', gap: 12, marginTop: 12 }}>
        <Statistic title="狀態" value={text(variant?.status)} />
        <Statistic title="目前 Maker 配對" value={pct(inventory.makerPairedCoverage)} />
        <Statistic title="結算配對中位數" value={pct(performance.finalMakerPairedCoverageMedian)} />
        <Statistic title="結算失衡中位數" value={pct(performance.finalMakerImbalanceMedian)} />
        <Statistic title="Maker PnL" value={money(performance.makerPnlUsdt)} />
        <Statistic title="Taker PnL" value={money(performance.takerPnlUsdt)} />
        <Statistic title="總 ROI" value={pct(performance.netRoi)} />
      </div>

      <Descriptions size="small" column={3} style={{ marginTop: 12 }}>
        <Descriptions.Item label="目前 Market">#{text(current.marketId)}</Descriptions.Item>
        <Descriptions.Item label="Maker 掛單 UP / DOWN">{text(current.upOrders, '0')} / {text(current.downOrders, '0')}</Descriptions.Item>
        <Descriptions.Item label="Maker Regime">{text(plan.regime)}</Descriptions.Item>
        <Descriptions.Item label="Maker Δ">{num(inventory.makerDelta)?.toFixed(1) ?? '—'} shares</Descriptions.Item>
        <Descriptions.Item label="Combined Δ">{num(inventory.combinedDelta)?.toFixed(1) ?? '—'} shares</Descriptions.Item>
        <Descriptions.Item label="軟 / 硬走廊">{text(makerPolicy.softCorridorShares)} / {text(makerPolicy.hardCorridorShares)} shares</Descriptions.Item>
        <Descriptions.Item label="Taker 決策">{text(decision.decision)} · {text(decision.reason)}</Descriptions.Item>
        <Descriptions.Item label="訊號方向 / 信心">{text(decision.side)} / {pct(signal.confidence)}</Descriptions.Item>
        <Descriptions.Item label="含費估算 edge">{num(decision.estimatedEdgePerShare) === null ? '—' : `${(num(decision.estimatedEdgePerShare)! * 100).toFixed(2)}¢`}</Descriptions.Item>
        <Descriptions.Item label="Taker 動態本金">{Array.isArray(takerPolicy.principalUsdtRange) ? `${takerPolicy.principalUsdtRange.join('–')} USDT` : '1–15 USDT'}</Descriptions.Item>
        <Descriptions.Item label="市場 / 已結算">{text(performance.markets, '0')} / {text(performance.settledMarkets, '0')}</Descriptions.Item>
        <Descriptions.Item label="Maker / Taker fills">{text(performance.makerFills, '0')} / {text(performance.takerFills, '0')}</Descriptions.Item>
      </Descriptions>

      <Table
        style={{ marginTop: 12 }} rowKey={(item) => text(item.market_id)}
        dataSource={rows(performance.recentMarkets)} columns={recentColumns} size="small"
        pagination={false} scroll={{ x: 1050 }}
        locale={{ emptyText: '等待部署後下一個完整市場結算' }}
      />
      <Text type="secondary">
        Paper-only、forward-only、無歷史回填；目標錢包事件完全不參與掛單、Taker 方向或尺寸。Maker、Taker 與整體績效分開報告，候選未加入 live allowlist。
      </Text>
    </Card>
  )
}
