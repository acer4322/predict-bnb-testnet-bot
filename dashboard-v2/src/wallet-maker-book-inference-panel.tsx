import { Alert, Card, Descriptions, Progress, Statistic, Table, Tag } from 'antd'
import type { TableColumnsType } from 'antd'
import { useWalletLabHealthStore } from './wallet-lab-health-store'
import type { ServiceSnapshot } from './store'

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
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

function InferenceCard({ service, asset, port }: { service: ServiceSnapshot; asset: string; port: number }) {
  const data = row(service.data)
  const current = row(data.current)
  const storage = row(data.storage)
  const inference = row(data.targetInference)
  const activity = row(data.targetActivity)
  const poll = row(activity.pollDiagnostics)
  const pollRequests = row(poll.requestsThisRun)
  const pollRows = row(poll.rowsReturnedThisRun)
  const matched = num(inference.matched) ?? 0
  const total = num(inference.target_events ?? inference.targetEvents) ?? 0
  const matchRate = total > 0 ? matched / total : 0

  const columns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: '方向', dataIndex: 'side', width: 70, render: (value) => <Tag color={value === 'UP' ? 'success' : 'error'}>{text(value)}</Tag> },
    { title: '目標價格', dataIndex: 'target_price', width: 90 },
    { title: 'Shares', dataIndex: 'target_shares', width: 90, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: '原生層', key: 'native', width: 105, render: (_, item) => `${text(item.native_book_side)} ${text(item.native_price)}` },
    { title: '狀態', dataIndex: 'status', width: 90, render: (value) => <Tag color={value === 'MATCHED' ? 'success' : value === 'UNMATCHED' ? 'warning' : 'processing'}>{text(value)}</Tag> },
    { title: '深度減少', dataIndex: 'observed_decrease', width: 95, render: (value) => num(value)?.toFixed(2) ?? '—' },
    { title: '延遲', dataIndex: 'event_delay_ms', width: 85, render: (value) => num(value) === null ? '—' : `${num(value)} ms` },
    { title: '信心', dataIndex: 'match_confidence', width: 85, render: pct },
  ]

  const activityColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: 'Role', dataIndex: 'role', width: 80, render: (value) => <Tag color={value === 'TAKER' ? 'blue' : 'purple'}>{text(value)}</Tag> },
    { title: 'Side', dataIndex: 'side', width: 70, render: (value) => <Tag color={value === 'UP' ? 'success' : 'error'}>{text(value)}</Tag> },
    { title: 'Quote', dataIndex: 'quote_type', width: 75 },
    { title: 'Price', dataIndex: 'price', width: 85 },
    { title: 'Shares', dataIndex: 'shares', width: 90, render: (value) => num(value)?.toFixed(2) ?? '-' },
    { title: 'Event time', dataIndex: 'event_ms', width: 150, render: (value) => num(value) === null ? '-' : new Date(num(value)!).toLocaleTimeString() },
  ]

  return (
    <Card title={`TARGET_MAKER_BOOK_INFERENCE · ${asset} 5M · ${port}`} style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="完整公開深度只能做機率式目標掛單配對"
        description="收集器保存壓縮價位變動與每10秒完整checkpoint。MATCHED代表目標Maker成交附近出現同價位深度下降，不代表匿名掛單身分已被交易所證明。"
      />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(120px, 1fr))', gap: 12, marginTop: 12 }}>
        <Statistic title="狀態" value={text(data.status, service.ok ? 'CONNECTED' : 'OFFLINE')} />
        <Statistic title="當前市場" value={`#${text(current.marketId)}`} />
        <Statistic title="Book updates" value={num(storage.updates) ?? 0} />
        <Statistic title="Checkpoints" value={num(storage.checkpoints) ?? 0} />
        <Statistic title="Target Maker fills" value={num(activity.maker_events) ?? 0} />
        <Statistic title="Target Taker fills" value={num(activity.taker_events) ?? 0} />
        <Statistic title="目標Maker事件" value={total} />
        <Statistic title="已配對" value={matched} />
        <Statistic title="高信心" value={num(inference.high_confidence ?? inference.highConfidence) ?? 0} />
      </div>
      <Descriptions size="small" column={3} style={{ marginTop: 12 }}>
        <Descriptions.Item label="Forward phase">{text(current.phase)}</Descriptions.Item>
        <Descriptions.Item label="部署排除市場">#{text(data.excludedDeploymentMarketId)}</Descriptions.Item>
        <Descriptions.Item label="WebSocket">{text(row(data.websocket).status)}</Descriptions.Item>
        <Descriptions.Item label="Target API polling">{`M ${text(pollRequests.MAKER, '0')}/${text(pollRows.MAKER, '0')} rows; T ${text(pollRequests.TAKER, '0')}/${text(pollRows.TAKER, '0')} rows`}</Descriptions.Item>
        <Descriptions.Item label="最後樣本年齡">{num(current.sampleAgeMs) === null ? '—' : `${num(current.sampleAgeMs)} ms`}</Descriptions.Item>
        <Descriptions.Item label="資料庫大小">{num(storage.databaseBytes) === null ? '—' : `${(num(storage.databaseBytes)! / 1024 / 1024).toFixed(1)} MB`}</Descriptions.Item>
        <Descriptions.Item label="平均配對信心">{pct(inference.average_confidence ?? inference.averageConfidence)}</Descriptions.Item>
      </Descriptions>
      <Progress percent={matchRate * 100} status="active" format={() => `配對率 ${(matchRate * 100).toFixed(1)}%`} />
      <Table
        style={{ marginTop: 12 }}
        size="small"
        rowKey={(item) => text(item.leg_id)}
        columns={columns}
        dataSource={rows(inference.recent)}
        pagination={false}
        scroll={{ x: 900 }}
      />
      {asset === 'ETH' && (
        <Table
          style={{ marginTop: 12 }}
          size="small"
          rowKey={(item) => text(item.source_leg_id)}
          columns={activityColumns}
          dataSource={rows(activity.recent)}
          pagination={false}
          scroll={{ x: 760 }}
          title={() => 'Forward target wallet activity (Maker + Taker)'}
        />
      )}
    </Card>
  )
}

export default function WalletMakerBookInferencePanel() {
  const btc = useWalletLabHealthStore((state) => state.makerBook8778)
  const eth = useWalletLabHealthStore((state) => state.makerBookEth8779)
  return (
    <>
      <InferenceCard service={btc} asset="BTC" port={8778} />
      <InferenceCard service={eth} asset="ETH" port={8779} />
    </>
  )
}
