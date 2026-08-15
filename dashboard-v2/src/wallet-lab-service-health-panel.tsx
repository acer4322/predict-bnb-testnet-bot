import { Alert, Card, Col, Descriptions, Row, Statistic, Tag, Typography } from 'antd'
import { useWalletLabHealthStore } from './wallet-lab-health-store'
import WalletResearchExportPanel from './wallet-research-export-panel'

const { Text } = Typography
type RowObject = Record<string, unknown>

function row(value: unknown): RowObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function num(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function age(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : parsed < 1000 ? `${parsed.toFixed(0)} ms` : `${(parsed / 1000).toFixed(2)} s`
}

function statusTag(value: unknown, connected: boolean) {
  const status = text(value, connected ? 'CONNECTED' : 'OFFLINE')
  return <Tag color={connected && ['LIVE', 'PARTIAL', 'CONNECTED'].includes(status) ? 'success' : connected ? 'warning' : 'error'}>{status}</Tag>
}

export default function WalletLabServiceHealthPanel() {
  const observer = useWalletLabHealthStore((state) => state.observer8776)
  const collector = useWalletLabHealthStore((state) => state.collector8777)
  const observerData = row(observer.data)
  const collectorData = row(collector.data)
  const storage = row(collectorData.storage)
  const latest = row(collectorData.latest)

  return (
    <>
      <Card title="Wallet Shadow 服務健康：8776 / 8777" style={{ marginTop: 12 }}>
        <Row gutter={[12, 12]}>
          <Col xs={24} xl={12}>
            <Card size="small" title={<>8776 Observer {statusTag(observerData.status, observer.ok)}</>}>
              <Row gutter={8}>
                <Col span={8}><Statistic title="Health latency" value={age(observer.latencyMs)} /></Col>
                <Col span={8}><Statistic title="Poll age" value={age(observerData.pollAgeMs)} /></Col>
                <Col span={8}><Statistic title="Full state generation" value={age(observerData.lastFullStateGenerationMs)} /></Col>
              </Row>
              <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
                <Descriptions.Item label="Version">{text(observerData.version)}</Descriptions.Item>
                <Descriptions.Item label="Market">#{text(observerData.marketId)}</Descriptions.Item>
                <Descriptions.Item label="Error">{text(observerData.error, 'none')}</Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>
          <Col xs={24} xl={12}>
            <Card size="small" title={<>8777 Public signal collector {statusTag(collectorData.status, collector.ok)}</>}>
              <Row gutter={8}>
                <Col span={8}><Statistic title="HTTP latency" value={age(collector.latencyMs)} /></Col>
                <Col span={8}><Statistic title="Sample age" value={age(collectorData.sampleAgeMs)} /></Col>
                <Col span={8}><Statistic title="Market" value={`#${text(collectorData.marketId)}`} /></Col>
              </Row>
              <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
                <Descriptions.Item label="Latest signal time">{text(latest.sampled_at_ms ?? latest.sampledAtMs)}</Descriptions.Item>
                <Descriptions.Item label="Samples this run">{text(storage.samplesWrittenThisRun, '0')}</Descriptions.Item>
                <Descriptions.Item label="Archive">{text(storage.privateOneSecondArchiveDb)}</Descriptions.Item>
                <Descriptions.Item label="Collector error">{text(collectorData.error, 'none')}</Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>
        </Row>
        {!observer.ok || !collector.ok ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 12 }}
            message="觀測路徑有連線失敗"
            description={`8776: ${observer.error ?? 'ok'}；8777: ${collector.error ?? 'ok'}。完整策略資料逾時不再等同於 8776 程序離線。`}
          />
        ) : null}
        <Text type="secondary">Health 每秒檢查且禁止重疊；大型 8776 /state 只在本頁每 3 秒更新一次。</Text>
      </Card>
      <WalletResearchExportPanel />
    </>
  )
}
