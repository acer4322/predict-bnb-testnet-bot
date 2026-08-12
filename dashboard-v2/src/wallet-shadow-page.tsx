import {
  Alert,
  Card,
  Col,
  Descriptions,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  ApiOutlined,
  EyeInvisibleOutlined,
  ExperimentOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import type { TableColumnsType } from 'antd'
import { useWalletShadowStore } from './wallet-shadow-store'

const { Title, Text, Paragraph } = Typography

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

function fixed(value: unknown, digits = 3): string {
  const parsed = number(value)
  return parsed === null ? '—' : parsed.toFixed(digits)
}

function pct(value: unknown): string {
  const parsed = number(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

function time(value: unknown): string {
  const parsed = number(value)
  if (parsed === null || parsed <= 0) return '—'
  const date = new Date(parsed)
  const clock = date.toLocaleTimeString('zh-TW', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
  return `${clock}.${String(date.getMilliseconds()).padStart(3, '0')}`
}

function shortAddress(value: unknown): string {
  const address = text(value, '')
  return address.length > 16 ? `${address.slice(0, 8)}…${address.slice(-6)}` : address || '—'
}

function sideTag(value: unknown) {
  const side = text(value).toUpperCase()
  if (side === 'UP') return <Tag color="success">UP</Tag>
  if (side === 'DOWN') return <Tag color="error">DOWN</Tag>
  return <Tag>{side}</Tag>
}

function roleTag(value: unknown) {
  const role = text(value).toUpperCase()
  return <Tag color={role === 'MAKER' ? 'blue' : role === 'TAKER' ? 'purple' : 'default'}>{role}</Tag>
}

function statusColor(value: unknown): string {
  const status = text(value, '').toUpperCase()
  if (status === 'LIVE') return 'success'
  if (status === 'DEGRADED') return 'warning'
  return 'default'
}

function metricPercent(value: unknown) {
  const parsed = number(value)
  return parsed === null ? 0 : Math.max(0, Math.min(100, parsed * 100))
}

function InventoryCard({ title, inventory }: { title: string; inventory: RowObject }) {
  const makerDelta = number(inventory.makerDelta) ?? 0
  const takerDelta = number(inventory.takerDelta) ?? 0
  return (
    <Card size="small" title={title}>
      <Row gutter={[8, 8]}>
        <Col span={12}><Statistic title="Maker UP" value={number(inventory.makerUpShares) ?? 0} precision={1} /></Col>
        <Col span={12}><Statistic title="Maker DOWN" value={number(inventory.makerDownShares) ?? 0} precision={1} /></Col>
        <Col span={12}><Statistic title="Taker UP" value={number(inventory.takerUpShares) ?? 0} precision={1} /></Col>
        <Col span={12}><Statistic title="Taker DOWN" value={number(inventory.takerDownShares) ?? 0} precision={1} /></Col>
      </Row>
      <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
        <Descriptions.Item label="Maker residual">{makerDelta === 0 ? 'FLAT' : sideTag(makerDelta > 0 ? 'UP' : 'DOWN')} {Math.abs(makerDelta).toFixed(1)}</Descriptions.Item>
        <Descriptions.Item label="Taker residual">{takerDelta === 0 ? 'FLAT' : sideTag(takerDelta > 0 ? 'UP' : 'DOWN')} {Math.abs(takerDelta).toFixed(1)}</Descriptions.Item>
      </Descriptions>
    </Card>
  )
}

export default function WalletShadowPage() {
  const service = useWalletShadowStore((state) => state.service)
  const snapshot = row(service.data)
  const market = row(snapshot.market)
  const book = row(market.book)
  const target = row(snapshot.target)
  const shadow = row(snapshot.shadow)
  const targetInventory = row(target.inventory)
  const shadowInventory = row(shadow.inventory)
  const core = row(snapshot.coreSignal)
  const assumptions = row(snapshot.assumptions)
  const similarity = row(snapshot.similarity)
  const targetEvents = rows(target.events)
  const shadowEvents = rows(shadow.events)

  const targetColumns: TableColumnsType<RowObject> = [
    { title: '時間', key: 'time', width: 105, render: (_, item) => time(item.firstEventMs) },
    { title: 'Role', key: 'role', width: 88, render: (_, item) => roleTag(item.role) },
    { title: '方向', key: 'side', width: 80, render: (_, item) => sideTag(item.side) },
    { title: '型態', key: 'quote', width: 80, render: (_, item) => <Tag>{text(item.quoteType)}</Tag> },
    { title: '均價', key: 'price', width: 90, render: (_, item) => fixed(item.averagePrice) },
    { title: 'Shares', key: 'shares', width: 95, render: (_, item) => fixed(item.shares, 2) },
    { title: 'Legs', key: 'legs', width: 65, render: (_, item) => text(item.fillLegs) },
    { title: 'Order hash', key: 'hash', render: (_, item) => <Text code copyable={Boolean(item.orderHash)}>{text(item.orderHash)}</Text> },
  ]

  const shadowColumns: TableColumnsType<RowObject> = [
    { title: '時間', key: 'time', width: 105, render: (_, item) => time(item.atMs) },
    { title: '事件', key: 'event', width: 155, render: (_, item) => <Tag color={text(item.eventType).includes('TAKER') ? 'purple' : 'blue'}>{text(item.eventType)}</Tag> },
    { title: '方向', key: 'side', width: 80, render: (_, item) => sideTag(item.side) },
    { title: '價格', key: 'price', width: 90, render: (_, item) => fixed(item.price) },
    { title: 'Shares', key: 'shares', width: 85, render: (_, item) => fixed(item.shares, 1) },
    { title: 'Core', key: 'core', width: 150, render: (_, item) => <Space size={4}>{sideTag(item.coreSide)}<Text type="secondary">{text(item.coreSource)}</Text></Space> },
    { title: '推定層級', key: 'inference', width: 180, render: (_, item) => <Tag color={text(item.inference) === 'KNOWN_PATTERN' ? 'green' : 'gold'}>{text(item.inference)}</Tag> },
    { title: '原因', key: 'reason', render: (_, item) => text(item.reason) },
  ]

  const similarityMetrics = [
    ['全部為 BUY/BID', similarity.buyOnlyRate],
    ['Maker = 18 shares', similarity.makerUnitRate],
    ['Maker quote ±1 tick', similarity.makerQuotePriceWithin1Tick],
    ['Maker timing ±3s', similarity.makerFillTimingWithin3s],
    ['Taker side ±5s', similarity.takerSideMatchWithin5s],
    ['Taker timing ±3s', similarity.takerTimingWithin3s],
    ['Matched price ±1 tick', similarity.matchedPriceWithin1Tick],
  ] as Array<[string, unknown]>

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Space align="center" wrap>
          <ExperimentOutlined style={{ fontSize: 24 }} />
          <div>
            <Title level={2} style={{ margin: 0 }}>BTC 5M Wallet Shadow Lab</Title>
            <Text type="secondary">目標錢包真實已成交 parent orders vs 我們的因果 Shadow 模仿事件</Text>
          </div>
        </Space>
      </div>

      {!service.ok && !service.data ? (
        <Alert
          type="warning"
          showIcon
          message="8776 Wallet Shadow observer 尚未連線"
          description={service.error ?? '等待 start-dashboard-v2.ps1 啟動 read-only watcher'}
          style={{ marginBottom: 12 }}
        />
      ) : null}

      <Alert
        type="info"
        showIcon
        icon={<EyeInvisibleOutlined />}
        message="看不到目標錢包未成交掛單；這正是此實驗要驗證的部分"
        description="Target 成交只拿來做事後比對，不會驅動 Shadow。MAKER_QUOTE 是依已知 18-share / cent-grid 模式推定；MAKER_FILL_PROXY 是 book-through / ask-touch 代理；TAKER_INTENT 是我們核心方向與推定 Maker residual 背離時的主動修正假說。"
        style={{ marginBottom: 12 }}
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} md={12} xl={6}>
          <Card size="small">
            <Statistic title="Target wallet" value={shortAddress(snapshot.targetWallet)} prefix={<ApiOutlined />} />
            <Text copyable>{text(snapshot.targetWallet)}</Text>
          </Card>
        </Col>
        <Col xs={12} md={6} xl={4}><Card size="small"><Statistic title="Status" value={text(snapshot.status)} /><Tag color={statusColor(snapshot.status)}>{text(snapshot.version)}</Tag></Card></Col>
        <Col xs={12} md={6} xl={4}><Card size="small"><Statistic title="Predict market" value={text(market.marketId)} prefix="#" /><Text type="secondary">{text(market.title)}</Text></Card></Col>
        <Col xs={12} md={6} xl={5}><Card size="small"><Statistic title="Target parents" value={number(target.parentCount) ?? 0} /><Text type="secondary">M {text(target.makerParents)} / T {text(target.takerParents)}</Text></Card></Col>
        <Col xs={12} md={6} xl={5}><Card size="small"><Statistic title="Shadow events" value={number(shadow.eventCount) ?? 0} /><Text type="secondary">Paper only · no live writes</Text></Card></Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={8}>
          <Card title="Current Predict BTC 5M">
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="UP Bid / Ask">{fixed(book.upBid)} / {fixed(book.upAsk)}</Descriptions.Item>
              <Descriptions.Item label="DOWN Bid / Ask">{fixed(book.downBid)} / {fixed(book.downAsk)}</Descriptions.Item>
              <Descriptions.Item label="Seconds left">{fixed(book.secondsLeft, 1)}s</Descriptions.Item>
              <Descriptions.Item label="Target Maker residual">{sideTag(target.makerResidualSide)}</Descriptions.Item>
              <Descriptions.Item label="Target Taker residual">{sideTag(target.takerResidualSide)}</Descriptions.Item>
              <Descriptions.Item label="Target divergence">{target.makerTakerDivergence === true ? <Tag color="success">YES</Tag> : <Tag>NO / WAIT</Tag>}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="我們的核心方向">
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="Core side">{sideTag(core.side)}</Descriptions.Item>
              <Descriptions.Item label="Source">{text(core.source)}</Descriptions.Item>
              <Descriptions.Item label="Fallback">8771 Predict mid &gt; / &lt; 0.5</Descriptions.Item>
              <Descriptions.Item label="Target events drive Shadow">{assumptions.targetEventsDriveShadow === false ? <Tag color="success">NO</Tag> : <Tag color="error">INVALID</Tag>}</Descriptions.Item>
              <Descriptions.Item label="Open orders visible">{assumptions.openOrdersVisible === true ? 'YES' : 'NO'}</Descriptions.Item>
              <Descriptions.Item label="Maker unit / grid">{fixed(assumptions.makerUnitShares, 0)} shares / {fixed(assumptions.makerGrid, 2)}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="Overall similarity">
            <Progress type="dashboard" percent={metricPercent(similarity.overall)} format={() => pct(similarity.overall)} />
            <Paragraph type="secondary" style={{ marginTop: 8 }}>
              這不是策略勝率，而是「成交事件是否符合我們事前推定的 quote / fill / taker 結構」。
            </Paragraph>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}><InventoryCard title="Target 已成交 Inventory" inventory={targetInventory} /></Col>
        <Col xs={24} xl={12}><InventoryCard title="Shadow 推定 Inventory" inventory={shadowInventory} /></Col>
      </Row>

      <Card title="事件相似度拆解" style={{ marginTop: 12 }}>
        <Row gutter={[12, 12]}>
          {similarityMetrics.map(([label, value]) => (
            <Col xs={12} md={8} xl={6} key={label}>
              <Card size="small">
                <Statistic title={label} value={pct(value)} />
                <Progress percent={metricPercent(value)} showInfo={false} size="small" />
              </Card>
            </Col>
          ))}
        </Row>
      </Card>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}>
          <Card title={<Space><SwapOutlined />Target · 真實已成交 Parent Orders</Space>}>
            <Table
              rowKey={(item) => text(item.id)}
              dataSource={targetEvents}
              columns={targetColumns}
              size="small"
              pagination={{ pageSize: 15, hideOnSinglePage: true }}
              scroll={{ x: 900 }}
              locale={{ emptyText: '本輪尚未抓到目標錢包 BTC 5M 成交' }}
            />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title={<Space><ExperimentOutlined />Shadow · 事前推定事件</Space>}>
            <Table
              rowKey={(item) => text(item.id)}
              dataSource={shadowEvents}
              columns={shadowColumns}
              size="small"
              pagination={{ pageSize: 15, hideOnSinglePage: true }}
              scroll={{ x: 1100 }}
              locale={{ emptyText: '等待第一批有效 book snapshot / divergence 事件' }}
            />
          </Card>
        </Col>
      </Row>

      {snapshot.error ? <Alert type="warning" showIcon message="Wallet Shadow degraded" description={text(snapshot.error)} style={{ marginTop: 12 }} /> : null}
    </>
  )
}
