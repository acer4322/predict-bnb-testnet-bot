import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Space, Tag, Typography, message } from 'antd'
import { ExperimentOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'

const { Text } = Typography

type AnyRecord = Record<string, any>
type Action = 'start' | 'stop' | 'restart'

let controlSessionToken: string | null = null

async function getControlToken() {
  if (controlSessionToken) return controlSessionToken
  const response = await fetch('/control/session', { cache: 'no-store' })
  const payload = await response.json().catch(() => null) as AnyRecord | null
  if (!response.ok) throw new Error(String(payload?.error || `Control session HTTP ${response.status}`))
  const token = String(payload?.token || '')
  if (!token) throw new Error('Control session returned no token')
  controlSessionToken = token
  return token
}

async function runAction(action: Action) {
  const token = await getControlToken()
  const response = await fetch(`/control/strategy-test/${action}`, {
    method: 'POST',
    cache: 'no-store',
    headers: { Accept: 'application/json', 'X-BTC-Lab-Control': token },
  })
  const payload = await response.json().catch(() => null) as AnyRecord | null
  if (!response.ok) {
    if (response.status === 403) controlSessionToken = null
    throw new Error(String(payload?.error || `HTTP ${response.status}`))
  }
  return payload
}

export default function EbmStrategyTestServiceCard() {
  const [status, setStatus] = useState<AnyRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<Action | null>(null)

  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/control/strategy-test/status', { cache: 'no-store', headers: { Accept: 'application/json' } })
      const payload = await response.json().catch(() => null) as AnyRecord | null
      if (!response.ok) throw new Error(String(payload?.error || `HTTP ${response.status}`))
      setStatus(payload)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const tick = () => { if (!cancelled && document.visibilityState === 'visible') void refresh() }
    tick()
    const timer = window.setInterval(tick, 5000)
    const onVisibility = () => tick()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [refresh])

  const act = async (action: Action) => {
    setBusy(action)
    try {
      await runAction(action)
      message.success(`EBM Strategy Test ${action} 完成`)
      await new Promise((resolve) => window.setTimeout(resolve, 500))
      await refresh()
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err)
      setError(text)
      message.error(text)
    } finally {
      setBusy(null)
    }
  }

  const online = status?.online === true
  const health = status?.health?.payload as AnyRecord | undefined
  const version = health?.version || health?.state?.version || '—'

  return (
    <Card
      className="section-row"
      title={<Space><ExperimentOutlined /> EBM Strategy Test · 8782</Space>}
      extra={<Tag color={online ? 'success' : 'default'}>{online ? 'ONLINE' : 'OFFLINE'}</Tag>}
    >
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="8782 專用於 TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY forward test"
          description="8780 已保留給 ETH Taker public-signal collector，不再與 EBM 測試共用 port。8782 為 PAPER ONLY，缺任何 frozen feature 時 FAIL-CLOSED。"
        />
        {error ? <Alert type="warning" showIcon message="8782 lifecycle/status error" description={error} /> : null}
        <Descriptions size="small" column={{ xs: 1, md: 2, lg: 4 }} bordered>
          <Descriptions.Item label="Port">{status?.port ?? 8782}</Descriptions.Item>
          <Descriptions.Item label="PID">{status?.pid ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Module"><Text code>{status?.module || status?.expectedModule || 'predict_bot.target_taker_public_side_test_v2'}</Text></Descriptions.Item>
          <Descriptions.Item label="Version">{version}</Descriptions.Item>
        </Descriptions>
        <Space wrap>
          <Button type="primary" icon={<PlayCircleOutlined />} disabled={online || busy !== null} loading={busy === 'start'} onClick={() => void act('start')}>Start 8782</Button>
          <Button danger icon={<StopOutlined />} disabled={!online || busy !== null} loading={busy === 'stop'} onClick={() => void act('stop')}>Stop 8782</Button>
          <Button icon={<ReloadOutlined />} disabled={busy !== null} loading={busy === 'restart'} onClick={() => void act('restart')}>Restart 8782</Button>
          <Button icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新</Button>
        </Space>
      </Space>
    </Card>
  )
}
