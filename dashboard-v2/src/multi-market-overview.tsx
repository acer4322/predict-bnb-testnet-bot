import { Alert, Badge, Card, Col, Empty, Row, Space, Statistic, Tag, Typography } from 'antd'
import { LineChartOutlined } from '@ant-design/icons'
import { asNumber, asText, getPath, type ServiceSnapshot } from './store'

const { Text } = Typography

type Point = {
  sampledAtMs: number
  secondsLeft: number | null
  polyUp: number | null
  binanceUp: number | null
  midGap: number | null
}

type AssetName = 'BTC' | 'ETH' | 'BNB'
const ASSETS: AssetName[] = ['BTC', 'ETH', 'BNB']

function formatProbability(value: unknown) {
  const n = asNumber(value)
  return n === null ? '—' : n.toFixed(3)
}

function formatGap(value: unknown) {
  const n = asNumber(value)
  if (n === null) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(3)}`
}

function formatMs(value: unknown) {
  const n = asNumber(value)
  return n === null ? '—' : `${Math.round(n)} ms`
}

function parsePoints(value: unknown): Point[] {
  if (!Array.isArray(value)) return []
  return value
    .map((row) => {
      if (!row || typeof row !== 'object') return null
      const sampledAtMs = asNumber(getPath(row, 'sampledAtMs'))
      if (sampledAtMs === null) return null
      return {
        sampledAtMs,
        secondsLeft: asNumber(getPath(row, 'secondsLeft')),
        polyUp: asNumber(getPath(row, 'polyUp')),
        binanceUp: asNumber(getPath(row, 'binanceUp')),
        midGap: asNumber(getPath(row, 'midGap')),
      }
    })
    .filter((row): row is Point => row !== null)
}

function pointsFor(values: Point[], key: 'polyUp' | 'binanceUp', width: number, height: number) {
  if (values.length < 2) return ''
  const first = values[0].sampledAtMs
  const last = values[values.length - 1].sampledAtMs
  const span = Math.max(1, last - first)
  const coords: string[] = []
  for (const row of values) {
    const value = row[key]
    if (value === null) continue
    const x = ((row.sampledAtMs - first) / span) * width
    const y = (1 - Math.max(0, Math.min(1, value))) * height
    coords.push(`${x.toFixed(1)},${y.toFixed(1)}`)
  }
  return coords.join(' ')
}

function TrajectoryChart({ points }: { points: Point[] }) {
  const width = 640
  const height = 150
  const polyline = pointsFor(points, 'polyUp', width, height)
  const binanceLine = pointsFor(points, 'binanceUp', width, height)

  if (!polyline && !binanceLine) {
    return <div className="trajectory-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待軌跡樣本" /></div>
  }

  return (
    <div className="trajectory-chart-wrap">
      <svg className="trajectory-chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="Polymarket and Binance UP probability trajectory">
        <line x1="0" y1={height / 2} x2={width} y2={height / 2} className="trajectory-midline" />
        <line x1="0" y1={height * 0.25} x2={width} y2={height * 0.25} className="trajectory-gridline" />
        <line x1="0" y1={height * 0.75} x2={width} y2={height * 0.75} className="trajectory-gridline" />
        {polyline ? <polyline points={polyline} className="trajectory-poly-line" fill="none" vectorEffect="non-scaling-stroke" /> : null}
        {binanceLine ? <polyline points={binanceLine} className="trajectory-binance-line" fill="none" vectorEffect="non-scaling-stroke" /> : null}
      </svg>
      <div className="trajectory-axis-label trajectory-axis-top">1.00</div>
      <div className="trajectory-axis-label trajectory-axis-mid">0.50</div>
      <div className="trajectory-axis-label trajectory-axis-bottom">0.00</div>
    </div>
  )
}

function AssetCard({ asset, data }: { asset: AssetName; data: unknown }) {
  const secondsLeft = asNumber(getPath(data, 'secondsLeft'))
  const comparison = getPath(data, 'comparison')
  const polyStatus = asText(getPath(data, 'poly.status'), 'UNKNOWN')
  const binanceStatus = asText(getPath(data, 'binance.status'), 'UNKNOWN')
  const points = parsePoints(getPath(data, 'trajectory'))
  const sourceMode = asText(getPath(data, 'sourceMode'))
  const polyUp = getPath(comparison, 'polyUpMid')
  const binanceUp = getPath(comparison, 'binanceUpMid')
  const midGap = getPath(comparison, 'midGap')
  const edgeUp = getPath(comparison, 'executableEdgeUp')
  const sourceAge = getPath(comparison, 'polySourceAgeMs')
  const polyHealthy = polyStatus === 'LIVE'
  const binanceHealthy = binanceStatus === 'LIVE'
  const slug = asText(getPath(data, 'poly.market.eventSlug'), '')
  const marketId = asText(getPath(data, 'binance.market.marketId'), '')

  return (
    <Card
      className="trajectory-card"
      title={<Space><LineChartOutlined /><strong>{asset}</strong><Tag>{sourceMode === 'EXISTING_BTC_SERVICES' ? 'LIVE FEED MIRROR' : 'OBSERVER'}</Tag></Space>}
      extra={<span className="asset-countdown">{secondsLeft === null ? '—' : `${Math.max(0, Math.round(secondsLeft))}s`}</span>}
    >
      <div className="trajectory-status-row">
        <Badge status={polyHealthy ? 'success' : 'error'} text={`Poly ${polyStatus}`} />
        <Badge status={binanceHealthy ? 'success' : 'error'} text={`Binance ${binanceStatus}`} />
      </div>

      <Row gutter={8} className="trajectory-stats">
        <Col span={6}><Statistic title="Poly UP" value={formatProbability(polyUp)} /></Col>
        <Col span={6}><Statistic title="Binance UP" value={formatProbability(binanceUp)} /></Col>
        <Col span={6}><Statistic title="Mid gap" value={formatGap(midGap)} /></Col>
        <Col span={6}><Statistic title="UP edge" value={formatGap(edgeUp)} /></Col>
      </Row>

      <TrajectoryChart points={points} />
      <div className="trajectory-legend">
        <span><i className="legend-line poly" />Polymarket UP</span>
        <span><i className="legend-line binance" />Binance UP</span>
        <span className="trajectory-samples">{points.length} samples</span>
      </div>
      <div className="trajectory-meta">
        <Text type="secondary">Poly source {formatMs(sourceAge)}</Text>
        {marketId ? <Text type="secondary">Binance #{marketId}</Text> : null}
        {slug ? <Text type="secondary" ellipsis={{ tooltip: slug }}>{slug}</Text> : null}
      </div>
    </Card>
  )
}

export default function MultiMarketOverview({ service }: { service: ServiceSnapshot }) {
  const state = service.data
  const assets = getPath(state, 'assets')

  return (
    <section className="multi-market-section">
      <div className="section-heading-row">
        <div>
          <Typography.Title level={4}>BTC / ETH / BNB 市場比對軌跡</Typography.Title>
          <Text type="secondary">同一個 5 分鐘視窗比較 Polymarket UP mid 與 Binance Prediction UP mid；ETH/BNB 目前只觀測，不參與 Echtgeld。</Text>
        </div>
        <Tag color={service.ok ? 'success' : 'warning'}>8770 · {service.ok ? `${Math.round(service.latencyMs ?? 0)}ms` : 'OFFLINE'}</Tag>
      </div>

      {!service.ok && !assets ? (
        <Alert
          showIcon
          type="info"
          message="Multi-market observer 尚未啟動"
          description="在另一個 PowerShell 執行 python -m predict_bot.multi_prediction_observer；Dashboard V2 仍保持 read-only。"
        />
      ) : (
        <Row gutter={[12, 12]}>
          {ASSETS.map((asset) => (
            <Col xs={24} xl={8} key={asset}>
              <AssetCard asset={asset} data={getPath(assets, asset)} />
            </Col>
          ))}
        </Row>
      )}
    </section>
  )
}
