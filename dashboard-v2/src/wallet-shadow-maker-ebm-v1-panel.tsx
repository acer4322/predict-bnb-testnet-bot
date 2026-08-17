import { Alert, Card, Col, Descriptions, Progress, Row, Statistic, Table, Tag, Typography } from 'antd'
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

function pct(value: unknown, digits = 1): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(digits)}%`
}

function money(value: unknown): string {
  const parsed = num(value)
  if (parsed === null) return '—'
  return `${parsed > 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

function fixed(value: unknown, digits = 3): string {
  const parsed = num(value)
  return parsed === null ? '—' : parsed.toFixed(digits)
}

function sideTag(value: unknown) {
  const side = text(value).toUpperCase()
  if (side === 'UP') return <Tag color="success">UP</Tag>
  if (side === 'DOWN') return <Tag color="error">DOWN</Tag>
  return <Tag>{side}</Tag>
}

function resultTag(value: unknown) {
  const status = text(value).toUpperCase()
  if (status === 'WIN') return <Tag color="success">WIN</Tag>
  if (status === 'LOSS') return <Tag color="error">LOSS</Tag>
  if (status === 'FLAT') return <Tag color="warning">FLAT</Tag>
  if (status === 'NO_FILL') return <Tag>NO FILL</Tag>
  return <Tag>{status}</Tag>
}

function decisionTag(value: unknown) {
  const decision = text(value).toUpperCase()
  if (decision === 'QUOTE') return <Tag color="processing">QUOTE</Tag>
  if (decision === 'IDLE') return <Tag>IDLE</Tag>
  return <Tag>{decision}</Tag>
}

const recentColumns: TableColumnsType<RowObject> = [
  { title: 'Market', dataIndex: 'market_id', width: 88, render: (value) => `#${text(value)}` },
  { title: 'Winner', dataIndex: 'winner', width: 80, render: sideTag },
  { title: 'Result', dataIndex: 'status', width: 88, render: resultTag },
  { title: 'Fills', dataIndex: 'fill_count', width: 70, render: (value) => text(value, '0') },
  { title: 'UP shares', dataIndex: 'up_shares', width: 92, render: (value) => fixed(value, 1) },
  { title: 'DOWN shares', dataIndex: 'down_shares', width: 106, render: (value) => fixed(value, 1) },
  { title: 'Cost', dataIndex: 'cost_usdt', width: 92, render: (value) => `$${fixed(value, 2)}` },
  { title: 'PnL', dataIndex: 'net_pnl_usdt', width: 92, render: (value) => <strong>{money(value)}</strong> },
  { title: 'ROI', dataIndex: 'net_roi', width: 82, render: (value) => pct(value) },
  { title: 'Paired', dataIndex: 'paired_coverage', width: 86, render: (value) => pct(value) },
]

function CohortCard({ cohort, title, color }: { cohort: RowObject; title: string; color: string }) {
  const decision = row(cohort.lastDecision)
  const hazard = row(decision.hazard)
  const levels = row(decision.levels)
  const upLevel = row(levels.UP)
  const downLevel = row(levels.DOWN)
  const inventory = row(cohort.inventory)
  const performance = row(cohort.performance)
  const orders = rows(cohort.orders)

  const hazardP = num(hazard.probability)
  const hazardThreshold = num(hazard.threshold) ?? 0.60
  const upLevelP = num(upLevel.probability)
  const downLevelP = num(downLevel.probability)
  const hazardPercent = hazardP === null ? 0 : Math.max(0, Math.min(100, hazardP * 100))

  return (
    <Card
      size="small"
      title={<><Tag color={color}>{title}</Tag> {text(cohort.status)}</>}
      style={{ height: '100%' }}
    >
      <Row gutter={[10, 10]}>
        <Col xs={12} md={8}><Statistic title="Traded / Settled" value={`${text(performance.tradedMarkets, '0')} / ${text(performance.settledMarkets, '0')}`} /></Col>
        <Col xs={12} md={8}><Statistic title="Win rate" value={pct(performance.winRate)} /></Col>
        <Col xs={12} md={8}><Statistic title="Fills" value={text(performance.fills, '0')} /></Col>
        <Col xs={12} md={8}><Statistic title="Net PnL" value={money(performance.netPnlUsdt)} /></Col>
        <Col xs={12} md={8}><Statistic title="Net ROI" value={pct(performance.netRoi)} /></Col>
        <Col xs={12} md={8}><Statistic title="Recent paired" value={pct(performance.recentPairedCoverageMean)} /></Col>
      </Row>

      <Descriptions size="small" column={2} style={{ marginTop: 10 }}>
        <Descriptions.Item label="目前決策">{decisionTag(decision.decision)} {text(decision.reason)}</Descriptions.Item>
        <Descriptions.Item label="Active orders">{text(cohort.activeOrders, '0')}</Descriptions.Item>
        <Descriptions.Item label="Hazard P(placement≤5s)">{pct(hazardP, 2)}</Descriptions.Item>
        <Descriptions.Item label="Hazard gate">{hazard.used === false ? <Tag>BYPASS</Tag> : `${pct(hazardThreshold, 0)} threshold`}</Descriptions.Item>
        <Descriptions.Item label="UP level P(≤2 ticks)">{pct(upLevelP, 2)} · offset {text(upLevel.offsetTicks)}t</Descriptions.Item>
        <Descriptions.Item label="DOWN level P(≤2 ticks)">{pct(downLevelP, 2)} · offset {text(downLevel.offsetTicks)}t</Descriptions.Item>
        <Descriptions.Item label="Inventory">UP {fixed(inventory.upShares, 0)} / DOWN {fixed(inventory.downShares, 0)}</Descriptions.Item>
        <Descriptions.Item label="Delta">{fixed(inventory.deltaShares, 0)} shares</Descriptions.Item>
        <Descriptions.Item label="Paired coverage">{pct(inventory.pairedCoverage)}</Descriptions.Item>
        <Descriptions.Item label="Imbalance">{pct(inventory.imbalanceRatio)}</Descriptions.Item>
        <Descriptions.Item label="Current fill count">{text(cohort.fillCount, '0')}</Descriptions.Item>
        <Descriptions.Item label="Seconds left">{fixed(decision.secondsLeft, 1)}s</Descriptions.Item>
      </Descriptions>

      <div style={{ marginTop: 8 }}>
        <Text type="secondary">Hazard EBM · gate {pct(hazardThreshold, 0)}</Text>
        <Progress
          percent={hazardPercent}
          status={hazard.used !== false && hazardP !== null && hazardP >= hazardThreshold ? 'success' : 'normal'}
          showInfo={false}
          size="small"
        />
      </div>

      <div style={{ marginTop: 8 }}>
        <Text type="secondary">目前掛單：</Text>{' '}
        {orders.length
          ? orders.slice(0, 6).map((order) => (
            <Tag key={`${text(order.side)}:${text(order.priceTick)}`}>
              {text(order.side)} {fixed(order.price, 2)} · {fixed(order.shares, 0)}sh
            </Tag>
          ))
          : <Tag>NONE</Tag>}
      </div>

      <Table
        style={{ marginTop: 10 }}
        rowKey={(item) => text(item.market_id)}
        dataSource={rows(performance.recentMarkets).slice(0, 10)}
        columns={recentColumns}
        size="small"
        pagination={false}
        scroll={{ x: 900 }}
        locale={{ emptyText: '等待 Maker EBM forward paper 市場結算' }}
      />
    </Card>
  )
}

export default function WalletShadowMakerEbmV1Panel() {
  const service = useWalletShadowStore((state) => state.service)
  const lab = row(row(service.data).makerEbmV1Lab)
  const models = row(lab.models)
  const policy = row(lab.policy)
  const cohorts = row(lab.cohorts)
  const hazardOnly = row(cohorts.MAKER_EBM_HAZARD_V1)
  const levelOnly = row(cohorts.MAKER_EBM_LEVEL_V1)
  const combined = row(cohorts.TARGET_MAKER_EBM_V1)

  if (!Object.keys(lab).length) {
    return (
      <Alert
        type="warning"
        showIcon
        message="Maker EBM V1 尚未出現在 8776 state"
        description="先產生 frozen Maker EBM artifacts，再用 start-maker-ebm-v1.ps1 啟動 v4.20。"
      />
    )
  }

  return (
    <Card title="Maker EBM V1 · Frozen Hazard + Level Forward A/B" style={{ marginTop: 12 }}>
      <Alert
        type={models.loaded === true ? 'success' : 'warning'}
        showIcon
        message={models.loaded === true ? 'Hazard + Level EBM 已載入，三組 paper cohort 正在 forward 測試' : 'Maker EBM 尚未載入，cohorts 會 fail closed'}
        description={models.loaded === true
          ? 'HAZARD_ONLY 測 WHEN；LEVEL_ONLY 測 WHERE；TARGET_MAKER_EBM_V1 同時測 WHEN+WHERE。方向只看各 cohort 自己的 paper inventory，固定 18 shares；Target 即時事件不能觸發掛單。'
          : text(models.error, `Hazard: ${text(models.hazardPath)} · Level: ${text(models.levelPath)}`)}
        style={{ marginBottom: 12 }}
      />

      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={12} md={6}><Statistic title="Version" value={text(lab.version)} /></Col>
        <Col xs={12} md={6}><Statistic title="Models" value={models.loaded === true ? '2 LOADED' : 'OFFLINE'} /></Col>
        <Col xs={12} md={6}><Statistic title="Fixed order" value={`${fixed(policy.sharesPerOrder, 0)} shares`} /></Col>
        <Col xs={12} md={6}><Statistic title="Pair cap" value={fixed(policy.maximumPairPriceSum, 2)} /></Col>
      </Row>

      <Row gutter={[12, 12]}>
        <Col xs={24} xxl={8}><CohortCard cohort={hazardOnly} title="HAZARD ONLY · WHEN" color="blue" /></Col>
        <Col xs={24} xxl={8}><CohortCard cohort={levelOnly} title="LEVEL ONLY · WHERE" color="gold" /></Col>
        <Col xs={24} xxl={8}><CohortCard cohort={combined} title="HAZARD + LEVEL" color="purple" /></Col>
      </Row>

      <Alert
        type="info"
        showIcon
        style={{ marginTop: 12 }}
        message="研究邊界"
        description="Hazard/Level 模型學的是 8778 從匿名 public book + 已知 Target Maker fills 反推出來的高信心 inferred placements，不是私有掛單 ground truth。Forward runtime 只使用公開市場特徵與自己的 paper inventory。EBM probability 是排序/gate score，不宣稱是真實校準機率。"
      />

      <Text type="secondary" style={{ display: 'block', marginTop: 10 }}>
        A/B 最重要的是比較 Net PnL、ROI、fills 與 paired coverage：HAZARD_ONLY 能隔離 WHEN 的價值；LEVEL_ONLY 能隔離 WHERE；Combined 才回答兩者結合是否真的改善 Maker forward PnL。Paper only；不在任何 live allowlist。
      </Text>
    </Card>
  )
}
