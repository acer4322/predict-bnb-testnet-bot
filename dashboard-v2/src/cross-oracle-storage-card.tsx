import {
  Alert,
  Card,
  Col,
  Descriptions,
  Progress,
  Row,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd'
import { DatabaseOutlined } from '@ant-design/icons'
import { asNumber, asText, getPath } from './store'
import { fmtInt, fmtMs, fmtTime, useDashboardModel } from './dashboard-model'

const { Text } = Typography

function fmtBytes(value: unknown): string {
  const parsed = asNumber(value)
  if (parsed === null || parsed < 0) return '—'
  if (parsed < 1024) return `${Math.round(parsed)} B`
  const kib = parsed / 1024
  if (kib < 1024) return `${kib.toFixed(1)} KiB`
  const mib = kib / 1024
  if (mib < 1024) return `${mib.toFixed(1)} MiB`
  const gib = mib / 1024
  return `${gib.toFixed(2)} GiB`
}

function fmtPct(value: unknown): string {
  const parsed = asNumber(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(2)}%`
}

function retentionColor(status: string): string {
  if (status === 'HEALTHY') return 'success'
  if (status === 'CATCHING_UP' || status === 'STARTING') return 'processing'
  if (status === 'STALE') return 'warning'
  if (status === 'ERROR') return 'error'
  return 'default'
}

function progressStatus(status: string): 'normal' | 'active' | 'exception' | 'success' {
  if (status === 'HEALTHY') return 'success'
  if (status === 'ERROR' || status === 'STALE') return 'exception'
  if (status === 'CATCHING_UP' || status === 'STARTING') return 'active'
  return 'normal'
}

export default function CrossOracleStorageCard() {
  const model = useDashboardModel()
  const service = model.services.crossOracle
  const storage = getPath(model.crossOracle, 'storage')
  const last = getPath(storage, 'lastRetentionCleanup')
  const status = asText(getPath(storage, 'retentionStatus'), 'UNKNOWN').toUpperCase()
  const reusableFraction = asNumber(getPath(storage, 'reusableFraction'))
  const progress = reusableFraction === null
    ? 0
    : Math.max(0, Math.min(100, reusableFraction * 100))
  const backlog = getPath(last, 'polyExpiredMarketsRemaining')
  const oldest = asText(getPath(last, 'polyOldestPendingMarket'), 'None')
  const retentionError = asText(getPath(storage, 'retentionError'), '')
  const metricsError = asText(getPath(storage, 'storageMetricsError'), '')

  return (
    <Card
      title={<Space><DatabaseOutlined />Cross Oracle Storage / Retention</Space>}
      extra={<Tag color={retentionColor(status)}>{status}</Tag>}
      style={{ marginTop: 12 }}
    >
      {!service.ok ? (
        <Alert
          type="warning"
          showIcon
          message="8767 Cross Oracle telemetry 目前離線"
          description={service.error ?? '等待 Cross Oracle service 回報 storage telemetry'}
          style={{ marginBottom: 12 }}
        />
      ) : null}

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <Statistic title="DB allocated" value={fmtBytes(getPath(storage, 'dbBytes'))} />
          <Text type="secondary">SQLite main DB pages</Text>
        </Col>
        <Col xs={12} md={6}>
          <Statistic title="Reusable" value={fmtBytes(getPath(storage, 'reusableBytes'))} />
          <Text type="secondary">{fmtPct(getPath(storage, 'reusableFraction'))} freelist</Text>
        </Col>
        <Col xs={12} md={6}>
          <Statistic title="Live pages approx" value={fmtBytes(getPath(storage, 'liveBytesApprox'))} />
          <Text type="secondary">allocated − reusable</Text>
        </Col>
        <Col xs={12} md={6}>
          <Statistic title="Expired backlog" value={fmtInt(backlog)} />
          <Text type="secondary">Poly markets beyond retention</Text>
        </Col>
      </Row>

      <div style={{ marginTop: 14 }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
          <Text type="secondary">SQLite reusable pages</Text>
          <Text>{fmtInt(getPath(storage, 'freelistCount'))} / {fmtInt(getPath(storage, 'pageCount'))} pages</Text>
        </Space>
        <Progress
          percent={Number(progress.toFixed(2))}
          status={progressStatus(status)}
          strokeLinecap="butt"
        />
      </div>

      <Descriptions size="small" column={{ xs: 1, md: 2 }} style={{ marginTop: 8 }}>
        <Descriptions.Item label="Oldest pending market">{oldest}</Descriptions.Item>
        <Descriptions.Item label="Last cleanup">{fmtTime(getPath(storage, 'lastRetentionCleanupAtMs'))} · age {fmtMs(getPath(storage, 'lastRetentionCleanupAgeMs'))}</Descriptions.Item>
        <Descriptions.Item label="Last Poly deleted">{fmtInt(getPath(last, 'polyEvents'))} events · {fmtInt(getPath(last, 'polyMarketsDrained'))} markets drained</Descriptions.Item>
        <Descriptions.Item label="Last Chainlink deleted">{fmtInt(getPath(last, 'chainlinkTicks'))} ticks</Descriptions.Item>
        <Descriptions.Item label="Cleanup duration">{fmtMs(getPath(last, 'durationMs'))}</Descriptions.Item>
        <Descriptions.Item label="Policy">Poly {asText(getPath(storage, 'polyRawRetentionHours'))}h · Chainlink {asText(getPath(storage, 'chainlinkRetentionHours'))}h · every {asText(getPath(storage, 'cleanupIntervalSeconds'))}s</Descriptions.Item>
        <Descriptions.Item label="Batch budget">{fmtInt(getPath(storage, 'polyDeleteBatchRows'))} rows × {fmtInt(getPath(storage, 'polyDeleteBatchesPerCleanup'))}</Descriptions.Item>
        <Descriptions.Item label="Runtime profile">{asText(getPath(storage, 'runtimeProfile'))}</Descriptions.Item>
      </Descriptions>

      {retentionError ? <Alert type="error" showIcon message="Retention error" description={retentionError} style={{ marginTop: 12 }} /> : null}
      {metricsError ? <Alert type="warning" showIcon message="Storage metrics unavailable" description={metricsError} style={{ marginTop: 12 }} /> : null}
    </Card>
  )
}
