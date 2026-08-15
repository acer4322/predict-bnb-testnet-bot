import { Alert, Badge, Card, Col, Empty, Row, Space, Statistic, Tag, Typography } from 'antd'
import { LineChartOutlined } from '@ant-design/icons'
import { asNumber, asText, getPath, type ServiceSnapshot } from './store'
import { usePredictFunStore } from './predict-fun-store'

const { Text } = Typography

type Point = {
  sampledAtMs: number
  secondsLeft: number | null
  polyUp: number | null
  polyDown: number | null
  binanceUp: number | null
  binanceDown: number | null
}

type PredictPoint = {
  sampledAtMs: number
  up: number | null
  down: number | null
}

type AssetName = 'BTC' | 'ETH' | 'BNB'
type TrajectorySide = 'UP' | 'DOWN'

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

function difference(left: unknown, right: unknown) {
  const a = asNumber(left)
  const b = asNumber(right)
  return a === null || b === null ? null : a - b
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
        polyDown: asNumber(getPath(row, 'polyDown')),
        binanceUp: asNumber(getPath(row, 'binanceUp')),
        binanceDown: asNumber(getPath(row, 'binanceDown')),
      }
    })
    .filter((row): row is Point => row !== null)
}

function parsePredictPoints(value: unknown): PredictPoint[] {
  if (!Array.isArray(value)) return []
  return value
    .map((row) => {
      if (!row || typeof row !== 'object') return null
      const sampledAtMs = asNumber(getPath(row, 'sampledAtMs'))
      if (sampledAtMs === null) return null
      return {
        sampledAtMs,
        up: asNumber(getPath(row, 'up')),
        down: asNumber(getPath(row, 'down')),
      }
    })
    .filter((row): row is PredictPoint => row !== null)
}

function linePoints<T>(
  values: T[],
  timestamp: (row: T) => number,
  value: (row: T) => number | null,
  width: number,
  height: number,
  first: number,
  last: number,
) {
  if (values.length < 2) return ''
  const span = Math.max(1, last - first)
  const coords: string[] = []
  for (const row of values) {
    const price = value(row)
    if (price === null) continue
    const x = ((timestamp(row) - first) / span) * width
    const y = (1 - Math.max(0, Math.min(1, price))) * height
    coords.push(`${x.toFixed(1)},${y.toFixed(1)}`)
  }
  return coords.length >= 2 ? coords.join(' ') : ''
}

function TrajectoryChart({ points, predictPoints, side }: { points: Point[]; predictPoints: PredictPoint[]; side: TrajectorySide }) {
  const width = 640
  const height = 112
  const allTimes = [...points.map((row) => row.sampledAtMs), ...predictPoints.map((row) => row.sampledAtMs)]
  const first = allTimes.length ? Math.min(...allTimes) : 0
  const last = allTimes.length ? Math.max(...allTimes) : 1
  const polyline = linePoints(
    points,
    (row) => row.sampledAtMs,
    (row) => side === 'UP' ? row.polyUp : row.polyDown,
    width,
    height,
    first,
    last,
  )
  const binanceLine = linePoints(
    points,
    (row) => row.sampledAtMs,
    (row) => side === 'UP' ? row.binanceUp : row.binanceDown,
    width,
    height,
    first,
    last,
  )
  const predictLine = linePoints(
    predictPoints,
    (row) => row.sampledAtMs,
    (row) => side === 'UP' ? row.up : row.down,
    width,
    height,
    first,
    last,
  )

  return (
    <div className="trajectory-side-block">
      <div className="trajectory-side-heading">
        <strong>{side}</strong>
        <Text type="secondary">Polymarket vs Binance Prediction vs Predict.fun</Text>
      </div>
      {!polyline && !binanceLine && !predictLine ? (
        <div className="trajectory-empty trajectory-empty-compact">
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`等待 ${side} 軌跡樣本`} />
        </div>
      ) : (
        <div className="trajectory-chart-wrap trajectory-chart-wrap-compact">
          <svg
            className="trajectory-chart trajectory-chart-compact"
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={`Polymarket Binance Predict.fun ${side} probability trajectory`}
          >
            <line x1="0" y1={height / 2} x2={width} y2={height / 2} className="trajectory-midline" />
            <line x1="0" y1={height * 0.25} x2={width} y2={height * 0.25} className="trajectory-gridline" />
            <line x1="0" y1={height * 0.75} x2={width} y2={height * 0.75} className="trajectory-gridline" />
            {polyline ? <polyline points={polyline} className="trajectory-poly-line" fill="none" vectorEffect="non-scaling-stroke" /> : null}
            {binanceLine ? <polyline points={binanceLine} className="trajectory-binance-line" fill="none" vectorEffect="non-scaling-stroke" /> : null}
            {predictLine ? <polyline points={predictLine} className="trajectory-predict-line" fill="none" vectorEffect="non-scaling-stroke" /> : null}
          </svg>
          <div className="trajectory-axis-label trajectory-axis-top">1.00</div>
          <div className="trajectory-axis-label trajectory-axis-mid trajectory-axis-mid-compact">0.50</div>
          <div className="trajectory-axis-label trajectory-axis-bottom">0.00</div>
        </div>
      )}
    </div>
  )
}

function GapStrip({ poly, binance, predict, execEdge }: { poly: unknown; binance: unknown; predict: unknown; execEdge: unknown }) {
  return (
    <div className="trajectory-gap-strip">
      <span>P−B <strong>{formatGap(difference(poly, binance))}</strong></span>
      <span>F−B <strong>{formatGap(difference(predict, binance))}</strong></span>
      <span>P−F <strong>{formatGap(difference(poly, predict))}</strong></span>
      <span>Poly exec <strong>{formatGap(execEdge)}</strong></span>
    </div>
  )
}

function AssetCard({ asset, data, predictData }: { asset: AssetName; data: unknown; predictData: unknown }) {
  const secondsLeft = asNumber(getPath(data, 'secondsLeft'))
  const comparison = getPath(data, 'comparison')
  const polyStatus = asText(getPath(data, 'poly.status'), 'UNKNOWN')
  const binanceStatus = asText(getPath(data, 'binance.status'), 'UNKNOWN')
  const predictStatus = asText(getPath(predictData, 'status'), 'OFFLINE')
  const points = parsePoints(getPath(data, 'trajectory'))
  const predictPoints = parsePredictPoints(getPath(predictData, 'trajectory'))
  const sourceMode = asText(getPath(data, 'sourceMode'))

  const polyUp = getPath(comparison, 'polyUpMid')
  const polyDown = getPath(comparison, 'polyDownMid')
  const binanceUp = getPath(comparison, 'binanceUpMid')
  const binanceDown = getPath(comparison, 'binanceDownMid')
  const predictUp = getPath(predictData, 'up.mid')
  const predictDown = getPath(predictData, 'down.mid')
  const edgeUp = getPath(comparison, 'executableEdgeUp')
  const edgeDown = getPath(comparison, 'executableEdgeDown')
  const sourceAge = getPath(comparison, 'polySourceAgeMs')
  const predictSourceAge = getPath(predictData, 'sourceAgeMs')

  const polyHealthy = polyStatus === 'LIVE'
  const binanceHealthy = binanceStatus === 'LIVE'
  const predictHealthy = predictStatus === 'LIVE'
  const slug = asText(getPath(data, 'poly.market.eventSlug'), '')
  const marketId = asText(getPath(data, 'binance.market.marketId'), '')
  const predictMarketId = asText(getPath(predictData, 'market.id'), '')

  return (
    <Card
      className="trajectory-card"
      title={<Space><LineChartOutlined /><strong>{asset}</strong><Tag>{sourceMode === 'EXISTING_BTC_SERVICES' ? 'LIVE FEED MIRROR' : 'OBSERVER'}</Tag></Space>}
      extra={<span className="asset-countdown">{secondsLeft === null ? '—' : `${Math.max(0, Math.round(secondsLeft))}s`}</span>}
    >
      <div className="trajectory-status-row">
        <Badge status={polyHealthy ? 'success' : 'error'} text={`Poly ${polyStatus}`} />
        <Badge status={binanceHealthy ? 'success' : 'error'} text={`Binance ${binanceStatus}`} />
        <Badge status={predictHealthy ? 'success' : predictStatus === 'CONFIG_REQUIRED' ? 'warning' : 'error'} text={`Predict ${predictStatus}`} />
      </div>

      <div className="trajectory-side-stats">
        <div className="trajectory-side-label up">UP</div>
        <Row gutter={8} className="trajectory-stats">
          <Col span={8}><Statistic title="Poly" value={formatProbability(polyUp)} /></Col>
          <Col span={8}><Statistic title="Binance" value={formatProbability(binanceUp)} /></Col>
          <Col span={8}><Statistic title="Predict" value={formatProbability(predictUp)} /></Col>
        </Row>
        <GapStrip poly={polyUp} binance={binanceUp} predict={predictUp} execEdge={edgeUp} />
      </div>

      <TrajectoryChart points={points} predictPoints={predictPoints} side="UP" />

      <div className="trajectory-side-stats trajectory-down-stats">
        <div className="trajectory-side-label down">DOWN</div>
        <Row gutter={8} className="trajectory-stats">
          <Col span={8}><Statistic title="Poly" value={formatProbability(polyDown)} /></Col>
          <Col span={8}><Statistic title="Binance" value={formatProbability(binanceDown)} /></Col>
          <Col span={8}><Statistic title="Predict" value={formatProbability(predictDown)} /></Col>
        </Row>
        <GapStrip poly={polyDown} binance={binanceDown} predict={predictDown} execEdge={edgeDown} />
      </div>

      <TrajectoryChart points={points} predictPoints={predictPoints} side="DOWN" />

      <div className="trajectory-legend">
        <span><i className="legend-line poly" />Polymarket</span>
        <span><i className="legend-line binance" />Binance</span>
        <span><i className="legend-line predict" />Predict.fun</span>
        <span className="trajectory-samples">{points.length}/{predictPoints.length} samples</span>
      </div>
      <div className="trajectory-meta trajectory-meta-four">
        <Text type="secondary">Poly source {formatMs(sourceAge)}</Text>
        <Text type="secondary">Predict source {formatMs(predictSourceAge)}</Text>
        {marketId ? <Text type="secondary">Binance #{marketId}</Text> : null}
        {predictMarketId ? <Text type="secondary">Predict #{predictMarketId}</Text> : null}
        {slug ? <Text type="secondary" ellipsis={{ tooltip: slug }}>{slug}</Text> : null}
      </div>
    </Card>
  )
}

export default function MultiMarketOverview({ service }: { service: ServiceSnapshot }) {
  const predictService = usePredictFunStore((state) => state.service)
  const state = service.data
  const assets = getPath(state, 'assets')
  const predictAssets = getPath(predictService.data, 'assets')
  const predictConfigured = getPath(predictService.data, 'apiKeyConfigured') === true

  return (
    <section className="multi-market-section">
      <div className="section-heading-row">
        <div>
          <Typography.Title level={4}>BTC / ETH / BNB 市場比對軌跡</Typography.Title>
          <Text type="secondary">UP / DOWN 同時比較 Polymarket、Binance Prediction 與 Predict.fun；ETH/BNB 仍只觀測，不參與 Echtgeld。</Text>
        </div>
        <Space size={4} wrap>
          <Tag color={service.ok ? 'success' : 'warning'}>8770 · {service.ok ? `${Math.round(service.latencyMs ?? 0)}ms` : 'OFFLINE'}</Tag>
          <Tag color={predictService.ok ? 'success' : 'warning'}>8771 · {predictService.ok ? `${Math.round(predictService.latencyMs ?? 0)}ms` : 'OFFLINE'}</Tag>
        </Space>
      </div>

      {!service.ok && !assets ? (
        <Alert
          showIcon
          type="info"
          message="Multi-market observer 尚未啟動"
          description="執行 python -m predict_bot.multi_prediction_observer；Predict.fun 是獨立 8771，不影響既有 BTC/ETH/BNB observer。"
        />
      ) : null}

      {predictService.ok && !predictConfigured ? (
        <Alert
          showIcon
          type="warning"
          className="predict-config-alert"
          message="Predict.fun API key 尚未設定"
          description="8771 已啟動但保持 CONFIG_REQUIRED；設定 PREDICT_FUN_API_KEY 後重啟 8771 即可加入第三條軌跡。"
        />
      ) : null}

      {assets ? (
        <Row gutter={[12, 12]}>
          {ASSETS.map((asset) => (
            <Col xs={24} xl={8} key={asset}>
              <AssetCard asset={asset} data={getPath(assets, asset)} predictData={getPath(predictAssets, asset)} />
            </Col>
          ))}
        </Row>
      ) : null}
    </section>
  )
}
