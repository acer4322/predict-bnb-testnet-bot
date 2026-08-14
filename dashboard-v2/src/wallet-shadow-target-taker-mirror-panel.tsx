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

export default function WalletShadowTargetTakerMirrorPanel() {
  const service = useWalletShadowStore((state) => state.service)
  const audit = row(row(service.data).targetTakerMirrorAudit)
  const performance = row(audit.performance)
  const evidence = row(audit.signalEvidence)
  const signalFields = row(evidence.fields)

  const matrixColumns: TableColumnsType<RowObject> = [
    { title: '延遲', dataIndex: 'horizonMs', width: 75, render: (value) => `${text(value)}ms` },
    { title: '尺寸', dataIndex: 'profile', width: 190, render: (value) => <Tag>{text(value)}</Tag> },
    { title: '擷取 / 可執行', key: 'capture', width: 115, render: (_, item) => `${text(item.captures, '0')} / ${text(item.fullyExecutable, '0')}` },
    { title: '可執行率', dataIndex: 'executionRate', width: 95, render: pct },
    { title: '已結算', dataIndex: 'settled', width: 75 },
    { title: '方向命中', dataIndex: 'winRate', width: 95, render: pct },
    { title: '含費 ROI', dataIndex: 'roi', width: 95, render: pct },
    { title: '+1 tick', dataIndex: 'stress1TickRoi', width: 90, render: pct },
    { title: '+2 ticks', dataIndex: 'stress2TickRoi', width: 90, render: pct },
    { title: '相對目標價格', dataIndex: 'medianPriceSlippageVsTarget', width: 115, render: (value) => num(value) === null ? '—' : `${(num(value)! * 100).toFixed(2)}¢` },
    { title: '偵測後取樣', dataIndex: 'medianActualDelayMs', width: 105, render: (value) => num(value) === null ? '—' : `${num(value)!.toFixed(0)}ms` },
    { title: '端到端延遲', dataIndex: 'medianEndToEndLatencyMs', width: 105, render: (value) => num(value) === null ? '—' : `${num(value)!.toFixed(0)}ms` },
  ]

  const signalRows = Object.entries(signalFields).map(([name, value]) => ({ name, ...row(value) }))
  const signalColumns: TableColumnsType<RowObject> = [
    { title: '嚴格前置訊號', dataIndex: 'name', width: 170 },
    { title: '可比較 parents', dataIndex: 'comparable', width: 115 },
    { title: '方向一致率', dataIndex: 'matchRate', width: 105, render: pct },
    { title: 'Shares 加權一致率', dataIndex: 'shareWeightedMatchRate', width: 135, render: pct },
  ]

  const recentColumns: TableColumnsType<RowObject> = [
    { title: 'Market', dataIndex: 'market_id', width: 90, render: (value) => `#${text(value)}` },
    { title: '方向', dataIndex: 'side', width: 70, render: (value) => <Tag>{text(value)}</Tag> },
    { title: '目標價格', dataIndex: 'target_average_price', width: 95 },
    { title: '首次 shares', dataIndex: 'target_shares_at_detection', width: 100 },
    { title: '最終 shares', dataIndex: 'target_latest_shares', width: 100 },
    { title: '偵測延遲', dataIndex: 'detection_lag_ms', width: 95, render: (value) => `${text(value)}ms` },
    { title: '前置訊號距離', dataIndex: 'strict_pre_signal_lead_ms', width: 110, render: (value) => num(value) === null ? '—' : `${text(value)}ms` },
    { title: 'Book', dataIndex: 'book_status', width: 110, render: (value) => <Tag color={value === 'OK' ? 'success' : 'warning'}>{text(value)}</Tag> },
    { title: '$1 VWAP', dataIndex: 'vwap', width: 90 },
  ]

  return (
    <Card title="TARGET_TAKER_MIRROR_AUDIT_V1 · Forward Paper" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="分開檢驗可跟單性與可能的 Taker 上游訊號"
        description="每個目標 Taker parent 首次被本機看到後，於 0/250/500/1000/2000ms 保存完整 orderbook，計算 $1、$5、18 shares 與目標當下 shares 的深度 VWAP。這是事件驅動執行稽核，不算自主策略，也不代表已證明目標使用任何單一公開訊號。"
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, minmax(130px, 1fr))', gap: 12, marginTop: 12 }}>
        <Statistic title="狀態" value={text(audit.status)} />
        <Statistic title="Target parents" value={text(performance.targetParents, '0')} />
        <Statistic title="Book 擷取列" value={text(performance.captureRows, '0')} />
        <Statistic title="偵測延遲中位數" value={num(performance.medianDetectionLagMs) === null ? '—' : `${num(performance.medianDetectionLagMs)!.toFixed(0)}ms`} />
        <Statistic title="嚴格前置 context" value={`${text(evidence.strictPreSignalContexts, '0')} / ${text(evidence.parents, '0')}`} />
        <Statistic title="Context 覆蓋率" value={pct(evidence.coverage)} />
      </div>

      <Descriptions size="small" column={3} style={{ marginTop: 12 }}>
        <Descriptions.Item label="部署邊界">{text(audit.deploymentBoundaryMs)}</Descriptions.Item>
        <Descriptions.Item label="排除部署市場">#{text(audit.excludedDeploymentMarketId)}</Descriptions.Item>
        <Descriptions.Item label="實盤影響">{audit.liveOrdersAffected === false ? <Tag color="success">NONE</Tag> : <Tag color="error">INVALID</Tag>}</Descriptions.Item>
      </Descriptions>

      <Table
        style={{ marginTop: 12 }} rowKey={(item) => `${text(item.horizonMs)}:${text(item.profile)}`}
        dataSource={rows(performance.matrix)} columns={matrixColumns} size="small" pagination={false}
        scroll={{ x: 1180 }} locale={{ emptyText: '等待下一個完整市場的目標 Taker 事件' }}
      />

      <Table
        style={{ marginTop: 12 }} rowKey={(item) => text(item.name)}
        dataSource={signalRows} columns={signalColumns} size="small" pagination={false}
        locale={{ emptyText: '尚無嚴格前置訊號樣本' }}
      />

      <Table
        style={{ marginTop: 12 }} rowKey={(item) => text(item.parent_id)}
        dataSource={rows(performance.recentParents)} columns={recentColumns} size="small" pagination={false}
        scroll={{ x: 950 }} locale={{ emptyText: '尚無 forward target Taker parent' }}
      />
      <Text type="secondary">
        含 200 bps Taker fee；只有完整通過最低 1 USDT、完整深度與 ≤2 秒 orderbook 新鮮度的樣本才列入 ROI。結果與自主策略完全分帳。
      </Text>
    </Card>
  )
}
