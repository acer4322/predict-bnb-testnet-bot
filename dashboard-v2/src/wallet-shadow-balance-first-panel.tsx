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

export default function WalletShadowBalanceFirstPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).makerInventoryTakerSharedLab)
  const variant = rows(lab.variants).find((item) => item.cohort === 'BALANCE_FIRST_POOLED_V2')
  const current = row(variant?.current)
  const inventory = row(current.inventory)
  const plan = row(current.depthPlan)
  const performance = row(variant?.performance)
  const policy = row(row(variant?.policy).pooledInventory)
  const coverage = num(performance.finalMakerPairedCoverageMedian)
  const imbalance = num(performance.finalMakerImbalanceMedian)
  const evaluated = (num(performance.pairedCoverageMarkets) ?? 0) > 0
  const targetMet = evaluated && coverage !== null && imbalance !== null && coverage >= 0.90 && imbalance <= 0.10

  const recentColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Winner', dataIndex: 'winner', width: 75, render: (value) => <Tag>{text(value)}</Tag> },
    { title: '結果', dataIndex: 'status', width: 75, render: (value) => <Tag color={value === 'WIN' ? 'success' : value === 'LOSS' ? 'error' : 'default'}>{text(value)}</Tag> },
    { title: 'Maker fills', dataIndex: 'maker_fill_count', width: 100 },
    { title: 'Maker PnL', dataIndex: 'maker_pnl_usdt', width: 105, render: money },
    { title: '總 ROI', dataIndex: 'net_roi', width: 90, render: pct },
    { title: '+1 tick', dataIndex: 'stress_1tick_roi', width: 90, render: pct },
    { title: '+2 ticks', dataIndex: 'stress_2tick_roi', width: 90, render: pct },
  ]

  return (
    <Card title="BALANCE_FIRST_POOLED_V2 · Forward Paper" style={{ marginTop: 12 }}>
      <Alert
        type={targetMet ? 'success' : 'info'}
        showIcon
        message={targetMet ? '目前已達庫存平衡研究目標' : '以 Target 約 91% 配對覆蓋為主要目標'}
        description="平衡時每側只開一個 18-share 配對；產生缺口後撤掉重側，只掛與缺少 lots 數量相同的修復單，避免三層同時成交後反向過沖。剩餘 60 秒停止建立新的雙邊風險，只允許缺口側修復。"
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, minmax(130px, 1fr))', gap: 12, marginTop: 12 }}>
        <Statistic title="狀態" value={text(variant?.status)} />
        <Statistic title="目前配對覆蓋" value={pct(inventory.makerPairedCoverage)} />
        <Statistic title="結算配對中位數" value={pct(coverage)} />
        <Statistic title="結算失衡中位數" value={pct(imbalance)} />
        <Statistic title="Maker PnL" value={money(performance.makerPnlUsdt)} />
        <Statistic title="平衡目標" value={evaluated ? (targetMet ? 'PASS' : 'WAIT') : 'NO SAMPLE'} />
      </div>

      <Descriptions size="small" column={3} style={{ marginTop: 12 }}>
        <Descriptions.Item label="目前 Market">#{text(current.marketId)}</Descriptions.Item>
        <Descriptions.Item label="掛單 UP / DOWN">{text(current.upOrders, '0')} / {text(current.downOrders, '0')}</Descriptions.Item>
        <Descriptions.Item label="Maker Δ">{num(inventory.makerDelta)?.toFixed(1) ?? '—'} shares</Descriptions.Item>
        <Descriptions.Item label="Regime">{text(plan.regime)}</Descriptions.Item>
        <Descriptions.Item label="Late risk freeze">{plan.lateRiskFreeze === true ? <Tag color="success">ON</Tag> : <Tag>OFF</Tag>}</Descriptions.Item>
        <Descriptions.Item label="市場 / 已結算">{text(performance.markets, '0')} / {text(performance.settledMarkets, '0')}</Descriptions.Item>
        <Descriptions.Item label="平衡時每側層數">{text(policy.balancedLevelsPerSide, '1')}</Descriptions.Item>
        <Descriptions.Item label="最大修復層數">{text(policy.maximumConcurrentLevelsPerSide, '3')}</Descriptions.Item>
        <Descriptions.Item label="停止新風險">剩餘 {text(policy.stopNewBalancedRiskAtSecondsLeft, '60')} 秒</Descriptions.Item>
      </Descriptions>

      <Table
        style={{ marginTop: 12 }}
        rowKey={(item) => text(item.market_id)}
        dataSource={rows(performance.recentMarkets)}
        columns={recentColumns}
        size="small"
        pagination={false}
        scroll={{ x: 800 }}
        locale={{ emptyText: '等待部署後下一個完整市場結算' }}
      />
      <Text type="secondary">
        Paper-only、forward-only、無歷史回填；V1 成績保留為對照，V2 不讀取目標錢包事件做決策，也不進入 live allowlist。
      </Text>
    </Card>
  )
}
