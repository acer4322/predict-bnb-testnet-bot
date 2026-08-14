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

function number(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function money(value: unknown): string {
  const parsed = number(value)
  if (parsed === null) return '—'
  return `${parsed > 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

function usd(value: unknown): string {
  const parsed = number(value)
  return parsed === null ? '—' : `$${parsed.toFixed(2)}`
}

function pct(value: unknown): string {
  const parsed = number(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(2)}%`
}

function decision(value: unknown): string {
  const item = row(value)
  return item.decision ? `${text(item.decision)} · ${text(item.reason)}` : '—'
}

export default function WalletShadowWideMakerFlowPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).wideMakerFlowTailLab)
  const variants = rows(lab.variants)

  const columns: TableColumnsType<RowObject> = [
    {
      title: '版本', key: 'cohort', width: 210,
      render: (_, item) => (
        <>
          <Tag color={item.refillMode === 'NONE' ? 'cyan' : item.refillMode === 'ALL_UNTIL_30S' ? 'blue' : 'purple'}>
            {text(item.label)}
          </Tag>
          <div>{text(item.cohort)}</div>
        </>
      ),
    },
    {
      title: 'Forward 狀態', key: 'status', width: 150,
      render: (_, item) => {
        const current = row(item.current)
        return <><Tag color={item.status === 'ACTIVE' ? 'success' : 'warning'}>{text(item.status)}</Tag><div>{text(current.initializationStatus)}</div></>
      },
    },
    {
      title: '掛單 UP / DOWN', key: 'orders', width: 125,
      render: (_, item) => {
        const current = row(item.current)
        return `${text(current.upOrders, '0')} / ${text(current.downOrders, '0')}`
      },
    },
    {
      title: '保留資金 現在 / 峰值', key: 'reserve', width: 165,
      render: (_, item) => {
        const current = row(item.current)
        return `${usd(current.currentReservedUsdt)} / ${usd(current.peakReservedUsdt)}`
      },
    },
    {
      title: '市場 / 已結算', key: 'markets', width: 110,
      render: (_, item) => {
        const perf = row(item.performance)
        return `${text(perf.markets, '0')} / ${text(perf.settledMarkets, '0')}`
      },
    },
    {
      title: 'Maker fills / PnL', key: 'maker', width: 135,
      render: (_, item) => {
        const perf = row(item.performance)
        return `${text(perf.makerFills, '0')} / ${money(perf.makerPnlUsdt)}`
      },
    },
    {
      title: 'Alpha Taker fills / PnL', key: 'primary', width: 155,
      render: (_, item) => {
        const perf = row(item.performance)
        return `${text(perf.primaryTakerFills, '0')} / ${money(perf.primaryPnlUsdt)}`
      },
    },
    {
      title: '保險 fills / Cost / PnL', key: 'insurance', width: 180,
      render: (_, item) => {
        const perf = row(item.performance)
        return `${text(perf.insuranceFills, '0')} / ${usd(perf.insuranceCostUsdt)} / ${money(perf.insurancePnlUsdt)}`
      },
    },
    {
      title: '合併 PnL / ROI', key: 'combined', width: 145,
      render: (_, item) => {
        const perf = row(item.performance)
        return `${money(perf.netPnlUsdt)} / ${pct(perf.netRoi)}`
      },
    },
    {
      title: '最大回撤 / 保險改善', key: 'drawdown', width: 170,
      render: (_, item) => {
        const perf = row(item.performance)
        return `${usd(perf.maxDrawdownUsdt)} / ${money(perf.insuranceDrawdownImprovementUsdt)}`
      },
    },
    {
      title: '目前 Alpha / Tail', key: 'decisions', width: 260,
      render: (_, item) => {
        const current = row(item.current)
        return <><div>{decision(current.lastPrimaryDecision)}</div><div>{decision(current.lastTailDecision)}</div></>
      },
    },
  ]

  return (
    <Card title="Wide Maker + Maker Flow Alpha + Tail Insurance · Forward Paper" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="三種 Maker 補掛方式，共享同一組因果 Taker 與尾盤保險"
        description="開局只在 0.06 到當時 best bid 建立 18-share 一分網格，絕不跨 Ask。MAKER_FLOW_ALPHA 僅使用本紙上策略嚴格前置 1–5 秒的 Maker fills；INVENTORY_GUARD 降低或禁止過度同向加碼；TAIL_INSURANCE_MIN1 以反向 Ask ≤0.12、每筆 1 USDT、約 2% Alpha Taker 本金預算獨立記帳。"
      />
      <Table
        style={{ marginTop: 12 }}
        rowKey={(item) => text(item.cohort)}
        dataSource={variants}
        columns={columns}
        size="small"
        pagination={false}
        scroll={{ x: 1810 }}
        locale={{ emptyText: '等待 8776 V0.11 forward cohort' }}
      />
      <Descriptions size="small" column={1} style={{ marginTop: 12 }}>
        <Descriptions.Item label="假設">{text(lab.hypothesis)}</Descriptions.Item>
        <Descriptions.Item label="證據邊界">{text(lab.evidenceBoundary)}</Descriptions.Item>
        <Descriptions.Item label="比較方式">STATIC 不補掛；REFILL 在價格回升後同價補掛至剩餘 30 秒；RAILS 只補當時 best bid 內側 7 層。</Descriptions.Item>
      </Descriptions>
      <Text type="secondary">Paper-only、forward-only、無歷史回填、目標錢包事件不參與決策、未加入 live allowlist。Maker fill 使用完整 18 shares 的保守代理，可能高估實際部分成交風險。</Text>
    </Card>
  )
}
