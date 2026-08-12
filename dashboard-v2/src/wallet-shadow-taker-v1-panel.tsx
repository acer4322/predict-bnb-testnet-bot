import { Alert, Card, Col, Descriptions, Row, Space, Statistic, Table, Tag, Typography } from 'antd'
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
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

function money(value: unknown): string {
  const parsed = number(value)
  if (parsed === null) return '—'
  return `${parsed > 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

function fixed(value: unknown, digits = 2): string {
  const parsed = number(value)
  return parsed === null ? '—' : parsed.toFixed(digits)
}

function time(value: unknown): string {
  const parsed = number(value)
  if (parsed === null || parsed <= 0) return '—'
  const date = new Date(parsed)
  return date.toLocaleTimeString('zh-TW', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
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
  return <Tag>{status}</Tag>
}

export default function WalletShadowTakerV1Panel() {
  const service = useWalletShadowStore((state) => state.service)
  const snapshot = row(service.data)
  const v0 = row(snapshot.performance)
  const takerV1 = row(snapshot.takerV1)
  const v1 = row(takerV1.performance)
  const current = row(takerV1.current)
  const inventory = row(current.inventory)
  const similarity = row(takerV1.similarity)
  const config = row(takerV1.config)
  const v1Events = rows(current.events)
  const v1Markets = rows(v1.recentMarkets)

  const eventColumns: TableColumnsType<RowObject> = [
    { title: '時間', key: 'time', width: 90, render: (_, item) => time(item.atMs) },
    { title: 'Trigger', key: 'trigger', width: 125, render: (_, item) => <Tag color={text(item.trigger) === 'MAKER_FOLLOW' ? 'blue' : 'purple'}>{text(item.trigger)}</Tag> },
    { title: '方向', key: 'side', width: 75, render: (_, item) => sideTag(item.side) },
    { title: 'Ask', key: 'price', width: 75, render: (_, item) => fixed(item.price, 3) },
    { title: 'Shares', key: 'shares', width: 75, render: (_, item) => fixed(item.shares, 1) },
    { title: 'Core', key: 'core', width: 75, render: (_, item) => sideTag(item.coreSide) },
    { title: '原因', key: 'reason', render: (_, item) => text(item.reason) },
  ]

  const marketColumns: TableColumnsType<RowObject> = [
    { title: 'Market', key: 'market', width: 90, render: (_, item) => `#${text(item.market_id)}` },
    { title: 'Winner', key: 'winner', width: 80, render: (_, item) => sideTag(item.winner) },
    { title: 'Result', key: 'result', width: 85, render: (_, item) => resultTag(item.status) },
    { title: 'Fills', key: 'fills', width: 65, render: (_, item) => text(item.fill_count) },
    { title: 'Cost', key: 'cost', width: 95, render: (_, item) => `$${fixed(item.cost_usdt)}` },
    { title: 'PnL', key: 'pnl', width: 95, render: (_, item) => <strong>{money(item.gross_pnl_usdt)}</strong> },
    { title: 'ROI', key: 'roi', width: 75, render: (_, item) => pct(item.gross_roi) },
    { title: 'Taker PnL', key: 'taker', width: 105, render: (_, item) => money(item.taker_pnl_usdt) },
  ]

  return (
    <Card title="Taker A/B · V0 residual correction vs V1 execution switch" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="V1 不覆蓋 V0，兩組同時 PAPER forward-test"
        description="V1 只從新版實際啟動時間後計分；共用相同 Maker fill proxy，但用獨立 Taker 邏輯。MAKER_FOLLOW 測試被動成交後約 +1~2 tick 轉 aggressive；CORE_FLIP 只在核心方向翻轉時做小額 probe。"
        style={{ marginBottom: 12 }}
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={12}>
          <Card size="small" title="Taker V0 · 原 residual correction">
            <Row gutter={[8, 8]}>
              <Col span={8}><Statistic title="Gross PnL" value={money(v0.grossPnlUsdt)} /></Col>
              <Col span={8}><Statistic title="Win rate" value={pct(v0.winRate)} /></Col>
              <Col span={8}><Statistic title="ROI" value={pct(v0.grossRoi)} /></Col>
              <Col span={8}><Statistic title="Taker PnL" value={money(v0.takerGrossPnlUsdt)} /></Col>
              <Col span={8}><Statistic title="Taker cost" value={`$${fixed(v0.takerCostUsdt)}`} /></Col>
              <Col span={8}><Statistic title="Settled" value={number(v0.settledMarkets) ?? 0} /></Col>
            </Row>
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card size="small" title="Taker V1 · small bidirectional execution switch">
            <Row gutter={[8, 8]}>
              <Col span={8}><Statistic title="Gross PnL" value={money(v1.grossPnlUsdt)} /></Col>
              <Col span={8}><Statistic title="Win rate" value={pct(v1.winRate)} /></Col>
              <Col span={8}><Statistic title="ROI" value={pct(v1.grossRoi)} /></Col>
              <Col span={8}><Statistic title="Taker PnL" value={money(v1.takerGrossPnlUsdt)} /></Col>
              <Col span={8}><Statistic title="Taker cost" value={`$${fixed(v1.takerCostUsdt)}`} /></Col>
              <Col span={8}><Statistic title="Settled" value={number(v1.settledMarkets) ?? 0} /></Col>
            </Row>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}>
          <Card size="small" title="V1 當輪行為">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Events">{text(current.eventCount, '0')}</Descriptions.Item>
              <Descriptions.Item label="UP / DOWN shares">{fixed(inventory.upShares, 1)} / {fixed(inventory.downShares, 1)}</Descriptions.Item>
              <Descriptions.Item label="Net residual">{sideTag(inventory.residualSide)} {fixed(Math.abs(number(inventory.delta) ?? 0), 1)}</Descriptions.Item>
              <Descriptions.Item label="Total shares">{fixed(inventory.totalShares, 1)}</Descriptions.Item>
              <Descriptions.Item label="Target parents">{text(similarity.targetParents, '0')}</Descriptions.Item>
              <Descriptions.Item label="V1 / Target count">{fixed(similarity.eventCountRatioV1ToTarget, 2)}x</Descriptions.Item>
              <Descriptions.Item label="Side <=5s">{pct(similarity.sideMatchWithin5s)}</Descriptions.Item>
              <Descriptions.Item label="Timing <=3s">{pct(similarity.sameSideTimingWithin3s)}</Descriptions.Item>
              <Descriptions.Item label="Residual target / V1">{sideTag(similarity.targetResidualSide)} / {sideTag(similarity.v1ResidualSide)}</Descriptions.Item>
              <Descriptions.Item label="Residual match">{similarity.residualSideMatch === true ? <Tag color="success">YES</Tag> : <Tag>NO / WAIT</Tag>}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card size="small" title="V1 風控／參數">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Base / flip shares">{fixed(config.baseShares, 0)} / {fixed(config.coreFlipShares, 0)}</Descriptions.Item>
              <Descriptions.Item label="Max event">{fixed(config.maxEventShares, 0)}</Descriptions.Item>
              <Descriptions.Item label="Max net">{fixed(config.maxNetShares, 0)}</Descriptions.Item>
              <Descriptions.Item label="Max total / market">{fixed(config.maxTotalShares, 0)}</Descriptions.Item>
              <Descriptions.Item label="Maker-follow delta">+{fixed(config.followMaxPriceDelta, 3)}</Descriptions.Item>
              <Descriptions.Item label="Max ask">{fixed(config.maxAsk, 2)}</Descriptions.Item>
              <Descriptions.Item label="Cooldown">{fixed((number(config.cooldownMs) ?? 0) / 1000, 1)}s</Descriptions.Item>
              <Descriptions.Item label="Stored V1 events">{text(v1.storedTakerEvents, '0')}</Descriptions.Item>
            </Descriptions>
            <Text type="secondary">Inventory 只限制曝險，不決定方向；因此 V1 可以在同一市場雙向 Taker。</Text>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="V1 最近事件" style={{ marginTop: 12 }}>
        <Table
          rowKey={(item) => text(item.id)}
          dataSource={v1Events}
          columns={eventColumns}
          size="small"
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          scroll={{ x: 850 }}
          locale={{ emptyText: '等待 MAKER_FOLLOW 或 CORE_FLIP' }}
        />
      </Card>

      <Card size="small" title="V1 最近已結算市場" style={{ marginTop: 12 }}>
        <Table
          rowKey={(item) => text(item.market_id)}
          dataSource={v1Markets}
          columns={marketColumns}
          size="small"
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          scroll={{ x: 850 }}
          locale={{ emptyText: 'V1 從新版啟動後才開始累積結算結果' }}
        />
      </Card>
    </Card>
  )
}
