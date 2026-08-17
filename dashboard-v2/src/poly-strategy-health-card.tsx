import { useEffect, useMemo, useState } from 'react'
import { Alert, Card, Col, Descriptions, Row, Space, Statistic, Tag, Typography } from 'antd'
import { SafetyCertificateOutlined } from '@ant-design/icons'

const { Text } = Typography

type RowObject = Record<string, unknown>

const row = (value: unknown): RowObject => value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
const rows = (value: unknown): RowObject[] => Array.isArray(value)
  ? value.filter((item): item is RowObject => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
  : []
const text = (value: unknown, fallback = '—') => value === null || value === undefined || value === '' ? fallback : String(value)
const num = (value: unknown): number | null => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function statusColor(status: unknown): string {
  const value = text(status).toUpperCase()
  if (['HEALTHY', 'WAITING_SIGNAL', 'ACTIVE_POSITION', 'ENTRY_DISABLED'].includes(value)) return 'success'
  if (['DEGRADED', 'STALE_MARKET_DATA'].includes(value)) return 'warning'
  return value === '—' ? 'default' : 'error'
}

function ms(value: unknown): string {
  const parsed = num(value)
  return parsed === null ? '—' : `${parsed.toFixed(0)}ms`
}

export default function PolyStrategyHealthCard() {
  const [diagnostics, setDiagnostics] = useState<RowObject>({})
  const [error, setError] = useState<string | null>(null)
  const [latencyMs, setLatencyMs] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    let inFlight = false
    const refresh = async () => {
      if (cancelled || inFlight || document.visibilityState !== 'visible') return
      inFlight = true
      const started = performance.now()
      try {
        const response = await fetch('/bridge/poly-fast-diagnostics', {
          cache: 'no-store',
          headers: { Accept: 'application/json' },
        })
        const payload = await response.json().catch(() => null)
        if (!response.ok || !payload || typeof payload !== 'object') {
          throw new Error(payload && typeof payload === 'object' && 'error' in payload ? String((payload as RowObject).error) : `HTTP ${response.status}`)
        }
        const body = row((payload as RowObject).diagnostics)
        if (!Object.keys(body).length) throw new Error('8792 returned no diagnostics object')
        if (!cancelled) {
          setDiagnostics(body)
          setError(null)
          setLatencyMs(Math.max(0, performance.now() - started))
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason))
          setLatencyMs(Math.max(0, performance.now() - started))
        }
      } finally {
        inFlight = false
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 2_000)
    const visibility = () => { if (document.visibilityState === 'visible') void refresh() }
    document.addEventListener('visibilitychange', visibility)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', visibility)
    }
  }, [])

  const assets = row(diagnostics.assets)
  const warnings = rows(diagnostics.warnings)
  const runtime = row(diagnostics.runtime)
  const overall = text(diagnostics.status, error ? 'OFFLINE' : 'WAITING')
  const assetNames = useMemo(() => ['BTC', 'ETH', 'BNB'], [])

  return (
    <Card
      title={<Space><SafetyCertificateOutlined /> Poly Strategy Health · 8792</Space>}
      extra={<Space><Tag color={statusColor(overall)}>{overall}</Tag><Text type="secondary">{latencyMs === null ? '—' : `${latencyMs.toFixed(0)}ms`}</Text></Space>}
    >
      {error ? <Alert type="error" showIcon message="8792 diagnostics unavailable" description={error} style={{ marginBottom: 12 }} /> : null}
      {!error && warnings.length ? (
        <Alert
          type="warning"
          showIcon
          message={`${warnings.length} 個診斷警告`}
          description={warnings.map((item) => `${text(item.asset)}: ${text(item.status)} · ${rows(item.reasons).map(String).join(', ') || text(item.reasons)}`).join(' | ')}
          style={{ marginBottom: 12 }}
        />
      ) : null}

      <Row gutter={[10, 10]}>
        {assetNames.map((asset) => {
          const item = row(assets[asset])
          const market = row(item.market)
          const evaluator = row(item.evaluator)
          const gateway = row(item.gateway)
          const lifecycle = row(item.lifecycle)
          const postReject = row(item.postRejectRearm)
          const takeProfit = row(item.takeProfit)
          return (
            <Col xs={24} xl={8} key={asset}>
              <Card size="small" title={<Space><Text strong>{asset}</Text><Tag color={statusColor(item.status)}>{text(item.status, 'UNKNOWN')}</Tag></Space>}>
                <Descriptions size="small" column={1}>
                  <Descriptions.Item label="Market">#{text(item.marketId)} · {num(item.secondsLeft)?.toFixed(1) ?? '—'}s</Descriptions.Item>
                  <Descriptions.Item label="Feeds">Poly {text(market.polyStatus)} / Binance {text(market.binanceStatus)}</Descriptions.Item>
                  <Descriptions.Item label="Book age">Poly {ms(market.polyBookAgeMs)} / Binance {ms(market.binanceBookAgeMs)}</Descriptions.Item>
                  <Descriptions.Item label="Bucket">{market.bucketAligned === true ? <Tag color="success">ALIGNED</Tag> : market.bucketAligned === false ? <Tag color="error">MISMATCH</Tag> : <Tag>UNKNOWN</Tag>}</Descriptions.Item>
                  <Descriptions.Item label="Lifecycle">{text(lifecycle.phase)}</Descriptions.Item>
                  <Descriptions.Item label="Evaluator">{text(evaluator.blockingReason)} · checks {text(evaluator.checks, '0')} · triggers {text(evaluator.triggers, '0')}</Descriptions.Item>
                  <Descriptions.Item label="Gateway">{text(gateway.lastStatus)} · attempts {text(gateway.attempts, '0')} · queued {text(gateway.queued, '0')}</Descriptions.Item>
                  <Descriptions.Item label="Safe rearm">{postReject.active === true ? 'ACTIVE' : 'idle'} · eval {text(postReject.evaluationsAfterRearm, '0')} / ready {text(postReject.entryReadyAfterRearm, '0')} / intent {text(postReject.newIntentAfterRearm, '0')}</Descriptions.Item>
                  <Descriptions.Item label="Take profit">{takeProfit.enabled === true ? `ON @ ${text(takeProfit.price)} · ${text(takeProfit.triggers, '0')} triggers` : 'OFF / UNKNOWN'}</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          )
        })}
      </Row>

      <Row gutter={[10, 10]} style={{ marginTop: 12 }}>
        <Col xs={12} md={6}><Statistic title="Overall" value={overall} /></Col>
        <Col xs={12} md={6}><Statistic title="Warnings" value={warnings.length} /></Col>
        <Col xs={12} md={6}><Statistic title="Entry assets" value={Array.isArray(runtime.entryAssets) ? runtime.entryAssets.join(', ') : 'BTC, ETH'} /></Col>
        <Col xs={12} md={6}><Statistic title="State payload" value="COMPACT" /></Col>
      </Row>
      <Text type="secondary">排查時優先貼 http://127.0.0.1:8792/diagnostics；只有這裡出現無法判定的警告時才需要完整 /state。</Text>
    </Card>
  )
}
