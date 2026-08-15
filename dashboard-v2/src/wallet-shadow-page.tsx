import { Alert, Card, Col, Descriptions, Row, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { DatabaseOutlined, ExperimentOutlined } from '@ant-design/icons'
import { useWalletShadowStore } from './wallet-shadow-store'
import WalletMakerBookInferencePanel from './wallet-maker-book-inference-panel'

const { Text, Title } = Typography

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
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

function money(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `$${parsed.toFixed(2)}`
}

function shares(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : parsed.toFixed(2)
}

function dateTime(value: unknown): string {
  const parsed = num(value)
  if (parsed === null) return '—'
  return new Date(parsed).toLocaleString()
}

function sideTag(value: unknown) {
  const valueText = text(value)
  return <Tag color={valueText === 'UP' ? 'success' : valueText === 'DOWN' ? 'error' : 'default'}>{valueText}</Tag>
}

function resultTag(value: unknown) {
  const status = text(value)
  return <Tag color={status === 'WIN' ? 'success' : status === 'LOSS' ? 'error' : status === 'FLAT' ? 'warning' : 'default'}>{status}</Tag>
}

function sourceTag(value: unknown) {
  const source = text(value)
  if (source === 'LEGACY_WALLET_SHADOW_TARGET_ACCOUNTING') return <Tag color="purple">舊帳本 / 回算</Tag>
  if (source === 'TARGET_WALLET_OFFICIAL_V2') return <Tag color="blue">Official V2</Tag>
  return <Tag>{source}</Tag>
}

function InventoryCard({ asset, data }: { asset: string; data: RowObject }) {
  return (
    <Card title={`${asset} 5M · Target 已成交 Inventory`}>
      <Row gutter={[10, 10]}>
        <Col xs={12} md={6}><Statistic title="Maker UP" value={shares(data.makerUpShares)} /></Col>
        <Col xs={12} md={6}><Statistic title="Maker DOWN" value={shares(data.makerDownShares)} /></Col>
        <Col xs={12} md={6}><Statistic title="Taker UP" value={shares(data.takerUpShares)} /></Col>
        <Col xs={12} md={6}><Statistic title="Taker DOWN" value={shares(data.takerDownShares)} /></Col>
      </Row>
      <Descriptions size="small" column={2} style={{ marginTop: 10 }}>
        <Descriptions.Item label="Maker delta">{shares(data.makerDelta)}</Descriptions.Item>
        <Descriptions.Item label="Taker delta">{shares(data.takerDelta)}</Descriptions.Item>
      </Descriptions>
    </Card>
  )
}

const parentColumns: TableColumnsType<RowObject> = [
  { title: 'Asset', dataIndex: 'asset', width: 70, render: (value) => <Tag>{text(value)}</Tag> },
  { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
  { title: 'Role', dataIndex: 'role', width: 80 },
  { title: 'Side', dataIndex: 'side', width: 70, render: sideTag },
  { title: 'Quote', dataIndex: 'quote_type', width: 75 },
  { title: 'Avg price', dataIndex: 'average_price', width: 90, render: (value) => num(value)?.toFixed(4) ?? '—' },
  { title: 'Shares', dataIndex: 'shares', width: 90, render: shares },
  { title: 'Legs', dataIndex: 'fill_legs', width: 65 },
  { title: 'First fill', dataIndex: 'first_event_ms', width: 170, render: dateTime },
  { title: 'Last fill', dataIndex: 'last_event_ms', width: 170, render: dateTime },
  { title: 'Order hash', dataIndex: 'order_hash', width: 220, ellipsis: true, render: (value) => <Text copyable={Boolean(value)}>{text(value)}</Text> },
]

const resultColumns: TableColumnsType<RowObject> = [
  { title: 'Asset', dataIndex: 'asset', width: 70, render: (value) => <Tag>{text(value)}</Tag> },
  { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
  { title: 'Winner', dataIndex: 'winner', width: 75, render: sideTag },
  { title: 'Fills', dataIndex: 'fill_count', width: 65 },
  { title: 'Parents', dataIndex: 'parent_count', width: 75 },
  { title: 'Buy', dataIndex: 'buy_notional_usdt', width: 90, render: money },
  { title: 'Sell', dataIndex: 'sell_proceeds_usdt', width: 90, render: money },
  { title: 'Payout', dataIndex: 'payout_usdt', width: 90, render: money },
  { title: 'Net PnL', dataIndex: 'net_pnl_usdt', width: 95, render: money },
  { title: 'ROI', dataIndex: 'net_roi', width: 80, render: pct },
  { title: 'Maker PnL', dataIndex: 'maker_net_pnl_usdt', width: 100, render: money },
  { title: 'Taker PnL', dataIndex: 'taker_net_pnl_usdt', width: 100, render: money },
  { title: 'Resolved', dataIndex: 'resolved_at_ms', width: 175, render: dateTime },
]

const historicalColumns: TableColumnsType<RowObject> = [
  { title: 'Asset', dataIndex: 'asset', width: 70, render: (value) => <Tag>{text(value, 'BTC')}</Tag> },
  { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
  { title: 'Winner', dataIndex: 'winner', width: 75, render: sideTag },
  { title: '目標結果', dataIndex: 'status', width: 90, render: resultTag },
  {
    title: '真實 fills', key: 'fills', width: 85,
    render: (_, item) => text(item.event_count ?? item.fill_count, '0'),
  },
  { title: '投入', dataIndex: 'buy_notional_usdt', width: 90, render: money },
  { title: 'Payout', dataIndex: 'payout_usdt', width: 90, render: money },
  { title: 'Net PnL', dataIndex: 'net_pnl_usdt', width: 100, render: money },
  { title: 'ROI', dataIndex: 'net_roi', width: 80, render: pct },
  { title: 'Share 信念', dataIndex: 'share_conviction_side', width: 105, render: sideTag },
  { title: 'Capital 信念', dataIndex: 'capital_conviction_side', width: 110, render: sideTag },
  { title: '來源', dataIndex: 'source', width: 120, render: sourceTag },
  { title: 'Resolved', dataIndex: 'resolved_at_ms', width: 175, render: dateTime },
]

export default function WalletShadowPage() {
  const service = useWalletShadowStore((state) => state.service)
  const snapshot = row(service.data)
  const health = row(snapshot.health)
  const storage = row(snapshot.storage)
  const performance = row(snapshot.targetPerformance)
  const historical = row(snapshot.targetHistoricalPerformance)
  const historicalSources = row(historical.sources)
  const legacySource = row(historicalSources.legacy)
  const assets = row(snapshot.assets)
  const btc = row(assets.BTC)
  const eth = row(assets.ETH)
  const btcMarket = row(btc.market)
  const ethMarket = row(eth.market)
  const btcInventory = row(btc.inventory)
  const ethInventory = row(eth.inventory)
  const targetEvents = rows(snapshot.targetEvents)
  const recentMarkets = rows(snapshot.targetRecentMarkets)
  const historicalMarkets = rows(snapshot.targetHistoricalRecentMarkets)
  const dbBytes = num(storage.databaseBytes)
  const historyRestored = snapshot.historicalTargetResultsRestored === true

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Title level={2} style={{ margin: 0 }}><ExperimentOutlined /> Target Wallet Research</Title>
        <Text type="secondary">8776 Official truth ledger + 8778/8779 TARGET_MAKER_BOOK_INFERENCE</Text>
      </div>

      {!service.ok && !service.data ? (
        <Alert type="warning" showIcon message="8776 Target Wallet Official 尚未連線" description={service.error ?? '等待 TARGET_WALLET_OFFICIAL_V2_LEGACY_HISTORY'} style={{ marginBottom: 12 }} />
      ) : null}

      <Alert
        type="success"
        showIcon
        message="8776 維持單一職責 Official collector；舊 Target 勝敗帳本只讀恢復"
        description="8776 runtime 仍只收集 BTC5M / ETH5M 目標錢包真實成交、Parent Orders、Inventory 與官方結算，不含 Shadow、EBM、TradeIntent 或 Echtgeld 邏輯。舊 predict_wallet_shadow.db 現在只以 SQLite read-only 模式提供重建前已存在的 Target 歷史勝敗/會計結果，不會寫入，也不會成為策略輸入。"
        style={{ marginBottom: 12 }}
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} md={12} xl={6}><Card><Statistic title="8776 status" value={text(snapshot.status, service.ok ? 'ONLINE' : 'OFFLINE')} /><Text type="secondary">{text(snapshot.version)}</Text></Card></Col>
        <Col xs={24} md={12} xl={6}><Card><Statistic title="Stored fill legs" value={num(storage.storedFillLegs) ?? 0} /><Text type="secondary">Permanent retention</Text></Card></Col>
        <Col xs={24} md={12} xl={6}><Card><Statistic title="Stored parent orders" value={num(storage.storedParentOrders) ?? 0} /><Text type="secondary">BTC + ETH</Text></Card></Col>
        <Col xs={24} md={12} xl={6}><Card><Statistic title="Official DB" value={dbBytes === null ? '—' : `${(dbBytes / 1024 / 1024).toFixed(1)} MB`} prefix={<DatabaseOutlined />} /><Text type="secondary">target_wallet_official_v1.db</Text></Card></Col>
      </Row>

      <Card title={`Target Wallet Official Performance · rolling ${text(performance.windowDays, '30')} days`} style={{ marginTop: 12 }}>
        <Row gutter={[12, 12]}>
          <Col xs={12} md={8} xl={3}><Statistic title="Net PnL" value={money(performance.netPnlUsdt)} /></Col>
          <Col xs={12} md={8} xl={3}><Statistic title="Win rate" value={pct(performance.winRate)} /></Col>
          <Col xs={12} md={8} xl={3}><Statistic title="Net ROI" value={pct(performance.netRoi)} /></Col>
          <Col xs={12} md={8} xl={3}><Statistic title="W / L / Flat" value={`${text(performance.wins, '0')} / ${text(performance.losses, '0')} / ${text(performance.flats, '0')}`} /></Col>
          <Col xs={12} md={8} xl={3}><Statistic title="Buy" value={money(performance.buyNotionalUsdt)} /></Col>
          <Col xs={12} md={8} xl={3}><Statistic title="Payout" value={money(performance.payoutUsdt)} /></Col>
          <Col xs={12} md={8} xl={3}><Statistic title="Maker PnL" value={money(performance.makerNetPnlUsdt)} /></Col>
          <Col xs={12} md={8} xl={3}><Statistic title="Taker PnL" value={money(performance.takerNetPnlUsdt)} /></Col>
        </Row>
        <Alert type="info" showIcon style={{ marginTop: 10 }} message={text(performance.accounting)} description={`Fee accounting: ${text(performance.feeAccounting)}. Raw fills are retained so fee handling can be refined later without losing source data.`} />
      </Card>

      <Card title="Target · 歷史勝敗（重建前 + Official V2）" style={{ marginTop: 12 }}>
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6} xl={3}><Statistic title="Settled" value={num(historical.settledMarkets) ?? 0} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="W / L / Flat" value={`${text(historical.wins, '0')} / ${text(historical.losses, '0')} / ${text(historical.flats, '0')}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Win rate" value={pct(historical.winRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Net PnL" value={money(historical.netPnlUsdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Net ROI" value={pct(historical.netRoi)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="舊帳本 markets" value={num(historical.legacyMarkets) ?? 0} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Official V2 markets" value={num(historical.currentOfficialMarkets) ?? 0} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="歷史回算 markets" value={num(historical.historicallyReconstructedMarkets) ?? 0} /></Col>
        </Row>
        {historyRestored && legacySource.available === true ? (
          <Alert
            type="success"
            showIcon
            style={{ marginTop: 10 }}
            message={`舊 Target 歷史勝敗已恢復 · ${text(legacySource.rows, '0')} rows · READ ONLY`}
            description={`${text(historical.dedupeRule)}。${text(historical.accountingCaveat)}`}
          />
        ) : (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 10 }}
            message="舊 Target 歷史帳本目前不可讀"
            description={`${text(legacySource.error, '尚未從 8776 收到 historical source 狀態')}。新 Official 結算仍可正常累積，舊資料不會被猜測或偽造。`}
          />
        )}
        <Table
          rowKey={(item) => `${text(item.market_id)}-${text(item.source)}`}
          dataSource={historicalMarkets}
          columns={historicalColumns}
          size="small"
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          scroll={{ x: 1350 }}
          style={{ marginTop: 10 }}
          locale={{ emptyText: '等待讀取舊 Target 歷史帳本或新 Official 結算' }}
        />
      </Card>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}><InventoryCard asset="BTC" data={btcInventory} /></Col>
        <Col xs={24} xl={12}><InventoryCard asset="ETH" data={ethInventory} /></Col>
      </Row>

      <Card title="Current target markets" style={{ marginTop: 12 }}>
        <Descriptions size="small" column={2}>
          <Descriptions.Item label="BTC 5M">#{text(btcMarket.marketId)} · {text(btcMarket.title)}</Descriptions.Item>
          <Descriptions.Item label="ETH 5M">#{text(ethMarket.marketId)} · {text(ethMarket.title)}</Descriptions.Item>
          <Descriptions.Item label="Target wallet"><Text copyable>{text(snapshot.targetWallet)}</Text></Descriptions.Item>
          <Descriptions.Item label="Last poll age">{num(health.lastPollAgeMs) === null ? '—' : `${num(health.lastPollAgeMs)?.toFixed(0)} ms`}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="Target · 真實已成交 Parent Orders" style={{ marginTop: 12 }}>
        <Table rowKey={(item) => text(item.id)} dataSource={targetEvents} columns={parentColumns} size="small" pagination={{ pageSize: 20, hideOnSinglePage: true }} scroll={{ x: 1350 }} locale={{ emptyText: '等待 BTC / ETH 5M 目標錢包真實成交' }} />
      </Card>

      <Card title="Target Wallet Official · 最近已結算市場（新帳本）" style={{ marginTop: 12 }}>
        <Table rowKey={(item) => text(item.market_id)} dataSource={recentMarkets} columns={resultColumns} size="small" pagination={{ pageSize: 15, hideOnSinglePage: true }} scroll={{ x: 1350 }} locale={{ emptyText: '等待新 Official ledger 的第一批市場結算' }} />
      </Card>

      <WalletMakerBookInferencePanel />

      {snapshot.error ? <Alert type="warning" showIcon message="8776 degraded" description={text(snapshot.error)} style={{ marginTop: 12 }} /> : null}
    </>
  )
}