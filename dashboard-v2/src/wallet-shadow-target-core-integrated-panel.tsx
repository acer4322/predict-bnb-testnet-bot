import { Alert, Card, Col, Descriptions, Row, Statistic, Table, Tag, Typography } from 'antd'
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

function ratio(value: number | null): string {
  return value === null ? '—' : `${value.toFixed(2)}x`
}

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

function VariantCard({ variant, title, color }: { variant: RowObject; title: string; color: string }) {
  const current = row(variant.current)
  const inventory = row(current.inventory)
  const plan = row(current.depthPlan)
  const decision = row(current.lastDecision)
  const signal = row(decision.signal)
  const performance = row(variant.performance)
  const policy = row(row(variant.policy).targetCoreIntegrated)
  const makerPolicy = row(policy.maker)
  const takerPolicy = row(policy.taker)
  const allocation = row(policy.allocationExperiment)

  const makerCost = num(performance.makerCostUsdt) ?? 0
  const takerCost = num(performance.takerCostUsdt) ?? 0
  const totalComponentCost = makerCost + takerCost
  const makerCostShare = totalComponentCost > 0 ? makerCost / totalComponentCost : null
  const takerCostShare = totalComponentCost > 0 ? takerCost / totalComponentCost : null
  const makerPnl = num(performance.makerPnlUsdt)
  const takerPnl = num(performance.takerPnlUsdt)
  const pnlCoverage = makerPnl !== null && makerPnl < 0 && takerPnl !== null
    ? takerPnl / Math.abs(makerPnl)
    : null

  return (
    <Card size="small" title={<><Tag color={color}>{title}</Tag> {text(variant.cohort)}</>} style={{ height: '100%' }}>
      <Row gutter={[10, 10]}>
        <Col span={8}><Statistic title="狀態" value={text(variant.status)} /></Col>
        <Col span={8}><Statistic title="Maker 層/邊" value={text(makerPolicy.levelsPerSide)} /></Col>
        <Col span={8}><Statistic title="Maker / Taker fills" value={`${text(performance.makerFills, '0')} / ${text(performance.takerFills, '0')}`} /></Col>
        <Col span={8}><Statistic title="Maker PnL" value={money(performance.makerPnlUsdt)} /></Col>
        <Col span={8}><Statistic title="Taker PnL" value={money(performance.takerPnlUsdt)} /></Col>
        <Col span={8}><Statistic title="總 ROI" value={pct(performance.netRoi)} /></Col>
        <Col span={8}><Statistic title="Maker 成本占比" value={pct(makerCostShare)} /></Col>
        <Col span={8}><Statistic title="Taker 成本占比" value={pct(takerCostShare)} /></Col>
        <Col span={8}><Statistic title="Taker 覆蓋 Maker loss" value={ratio(pnlCoverage)} /></Col>
      </Row>

      <Descriptions size="small" column={2} style={{ marginTop: 10 }}>
        <Descriptions.Item label="目前 Market">#{text(current.marketId)}</Descriptions.Item>
        <Descriptions.Item label="Maker 掛單 UP / DOWN">{text(current.upOrders, '0')} / {text(current.downOrders, '0')}</Descriptions.Item>
        <Descriptions.Item label="Maker Regime">{text(plan.regime)}</Descriptions.Item>
        <Descriptions.Item label="Maker Δ">{num(inventory.makerDelta)?.toFixed(1) ?? '—'} shares</Descriptions.Item>
        <Descriptions.Item label="Combined Δ">{num(inventory.combinedDelta)?.toFixed(1) ?? '—'} shares</Descriptions.Item>
        <Descriptions.Item label="Maker 配對">{pct(inventory.makerPairedCoverage)}</Descriptions.Item>
        <Descriptions.Item label="Taker 決策">{text(decision.decision)} · {text(decision.reason)}</Descriptions.Item>
        <Descriptions.Item label="方向 / 信心">{text(decision.side)} / {pct(signal.confidence)}</Descriptions.Item>
        <Descriptions.Item label="含費 edge">{num(decision.estimatedEdgePerShare) === null ? '—' : `${(num(decision.estimatedEdgePerShare)! * 100).toFixed(2)}¢`}</Descriptions.Item>
        <Descriptions.Item label="Taker 本金">{Array.isArray(takerPolicy.principalUsdtRange) ? `${takerPolicy.principalUsdtRange.join('–')} USDT` : '1–15 USDT'}</Descriptions.Item>
        {Object.keys(allocation).length > 0 ? (
          <Descriptions.Item label="Maker slot reduction">{pct(allocation.makerQuoteSlotReduction)}</Descriptions.Item>
        ) : null}
        <Descriptions.Item label="市場 / 已結算">{text(performance.markets, '0')} / {text(performance.settledMarkets, '0')}</Descriptions.Item>
      </Descriptions>

      <Table
        style={{ marginTop: 10 }} rowKey={(item) => text(item.market_id)}
        dataSource={rows(performance.recentMarkets).slice(0, 8)} columns={recentColumns} size="small"
        pagination={false} scroll={{ x: 1050 }}
        locale={{ emptyText: '等待 forward 市場結算；即時狀態仍會先顯示' }}
      />
    </Card>
  )
}

export default function WalletShadowTargetCoreIntegratedPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).makerInventoryTakerSharedLab)
  const variants = rows(lab.variants)
  const v1 = row(variants.find((item) => item.cohort === 'TARGET_CORE_INTEGRATED_V1'))
  const v2 = row(variants.find((item) => item.cohort === 'TARGET_CORE_INTEGRATED_V2_TAKER_HEAVY'))

  const v1Perf = row(v1.performance)
  const v2Perf = row(v2.performance)
  const v1Maker = num(v1Perf.makerPnlUsdt)
  const v1Taker = num(v1Perf.takerPnlUsdt)
  const v2Maker = num(v2Perf.makerPnlUsdt)
  const v2Taker = num(v2Perf.takerPnlUsdt)

  return (
    <Card title="Target Core Integrated · V1 vs Taker-heavy V2" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="V2 只降低 Maker 曝險，不改目前已較有成效的 Taker 訊號門檻與 sizing。"
        description="V1 每邊 15 層；V2 每邊 5 層，Maker 同時 quote slots 約降低 66.7%。兩者維持相同 18-share Maker 單位、公開微結構 Taker score、含費 edge、cooldown 與 1–15 USDT sizing。目標是檢驗 Taker 正收益能否穩定覆蓋剩餘 Maker loss。"
        style={{ marginBottom: 12 }}
      />

      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={12} md={6}><Statistic title="V1 Maker / Taker PnL" value={`${money(v1Maker)} / ${money(v1Taker)}`} /></Col>
        <Col xs={12} md={6}><Statistic title="V2 Maker / Taker PnL" value={`${money(v2Maker)} / ${money(v2Taker)}`} /></Col>
        <Col xs={12} md={6}><Statistic title="V1 總 ROI" value={pct(v1Perf.netRoi)} /></Col>
        <Col xs={12} md={6}><Statistic title="V2 總 ROI" value={pct(v2Perf.netRoi)} /></Col>
      </Row>

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={12}><VariantCard variant={v1} title="V1 CONTROL" color="blue" /></Col>
        <Col xs={24} xl={12}><VariantCard variant={v2} title="V2 TAKER-HEAVY" color="purple" /></Col>
      </Row>

      <Text type="secondary" style={{ display: 'block', marginTop: 10 }}>
        判定 V2 有價值時優先看：Taker PnL 是否持續為正、Taker PnL / |Maker loss| 是否長期大於 1、總 PnL 與 +1/+2 tick stress ROI 是否仍為正。V2 是獨立 forward paper cohort，不回填 V1 歷史資料，也不加入 live allowlist。
      </Text>
    </Card>
  )
}
