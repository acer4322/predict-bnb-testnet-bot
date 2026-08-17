import { useEffect } from 'react'
import {
  Alert,
  Button,
  Card,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  CloudServerOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import type { TableColumnsType } from 'antd'
import {
  type ManagedServiceStatus,
  type ServiceAction,
  useServiceControlStore,
} from './service-control-store'

const { Text } = Typography

function stateColor(state: ManagedServiceStatus['state']) {
  if (state === 'ONLINE') return 'success'
  if (state === 'OFFLINE') return 'default'
  if (state === 'CONFLICT' || state === 'CRASHED') return 'error'
  return 'warning'
}

function ownershipColor(ownership: ManagedServiceStatus['ownership']) {
  if (ownership === 'MANAGED') return 'blue'
  if (ownership === 'LEGACY_MANAGED') return 'geekblue'
  if (ownership === 'EXTERNAL') return 'orange'
  if (ownership === 'SELF') return 'purple'
  return 'default'
}

function fmtUptime(startedAt: number | null) {
  if (!startedAt) return '—'
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
}

export default function ServiceControlPanel() {
  const snapshot = useServiceControlStore((state) => state.snapshot)
  const loading = useServiceControlStore((state) => state.loading)
  const error = useServiceControlStore((state) => state.error)
  const actionKeys = useServiceControlStore((state) => state.actionKeys)
  const refresh = useServiceControlStore((state) => state.refresh)
  const serviceAction = useServiceControlStore((state) => state.serviceAction)
  const groupAction = useServiceControlStore((state) => state.groupAction)

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      if (!cancelled && document.visibilityState === 'visible') void refresh()
    }
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

  const runService = async (id: string, action: ServiceAction) => {
    try {
      await serviceAction(id, action)
      message.success(`${id} ${action} 完成`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    }
  }

  const runGroup = async (id: string, action: ServiceAction) => {
    try {
      await groupAction(id, action)
      message.success(`${id} ${action} 完成`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err))
    }
  }

  const columns: TableColumnsType<ManagedServiceStatus> = [
    {
      title: 'Service',
      key: 'service',
      width: 250,
      render: (_, service) => (
        <Space direction="vertical" size={0}>
          <Space size={6} wrap>
            <strong>{service.label}</strong>
            <Tag color={stateColor(service.state)}>{service.state}</Tag>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>{service.description}</Text>
        </Space>
      ),
    },
    {
      title: 'Ports',
      key: 'ports',
      width: 275,
      render: (_, service) => (
        <Space size={[4, 4]} wrap>
          {service.ports.map((port) => (
            <Tooltip key={port.port} title={`${port.label}${port.pid ? ` · PID ${port.pid}` : ''}${port.commandRecognized === false ? ' · process identity mismatch' : ''}`}>
              <Tag color={port.commandRecognized === false ? 'error' : port.healthy ? 'success' : port.pid ? 'warning' : 'default'}>
                {port.port} {port.healthy ? '●' : port.pid ? '◐' : '○'}
              </Tag>
            </Tooltip>
          ))}
        </Space>
      ),
    },
    {
      title: 'Ownership',
      key: 'ownership',
      width: 155,
      render: (_, service) => (
        <Space direction="vertical" size={0}>
          <Tag color={ownershipColor(service.ownership)}>{service.ownership}</Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {service.rootPid ? `root ${service.rootPid}` : service.pids.length ? `PID ${service.pids.join(', ')}` : '—'}
          </Text>
        </Space>
      ),
    },
    {
      title: 'Runtime',
      key: 'runtime',
      width: 150,
      render: (_, service) => service.id === 'echtgeld' ? (
        <Space direction="vertical" size={0}>
          <Tag color={service.runtime?.armed ? 'error' : service.state === 'ONLINE' ? 'gold' : 'default'}>
            {service.runtime?.armed ? 'LIVE ARMED' : service.runtime?.runtimeStatus || 'PAUSED / OFFLINE'}
          </Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>{service.runtime?.version || '—'}</Text>
        </Space>
      ) : <Text type="secondary">{fmtUptime(service.startedAt)}</Text>,
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 245,
      fixed: 'right',
      render: (_, service) => {
        if (!service.controllable) return <Text type="secondary">External launcher only</Text>
        const busy = actionKeys.some((key) => key.startsWith(`${service.id}:`) || key.startsWith('group:'))
        // The verified stop controller re-inspects netstat + command line on every
        // click, so an ONLINE/PARTIAL/CONFLICT service can safely attempt Stop even
        // when the older ownership registry is missing or stale. Unknown processes
        // are still rejected by the backend before taskkill is called.
        const verifiedStopCandidate = !['OFFLINE', 'CRASHED'].includes(service.state)
        const canStop = service.canStop || verifiedStopCandidate
        const canRestart = service.canRestart || verifiedStopCandidate
        return (
          <Space size={4} wrap>
            <Button
              size="small"
              type="primary"
              icon={<PlayCircleOutlined />}
              disabled={!service.canStart || busy}
              loading={actionKeys.includes(`${service.id}:start`)}
              onClick={() => void runService(service.id, 'start')}
            >
              Start
            </Button>
            <Popconfirm
              title={`停止 ${service.label}?`}
              description={service.id === 'echtgeld'
                ? '如果 Engine 已 ARMED 或有未結算訂單，後端會拒絕停止。'
                : '後端會重新用 netstat + process command line 驗證 listener；不認得的 PID 絕不終止。'}
              okText="Stop"
              cancelText="取消"
              onConfirm={() => runService(service.id, 'stop')}
            >
              <Button size="small" danger icon={<StopOutlined />} disabled={!canStop || busy} loading={actionKeys.includes(`${service.id}:stop`)}>
                Stop
              </Button>
            </Popconfirm>
            <Popconfirm
              title={`重啟 ${service.label}?`}
              description="先驗證並關閉實際 listener port，再透過 Dashboard 正常 Start。"
              okText="Restart"
              cancelText="取消"
              onConfirm={() => runService(service.id, 'restart')}
            >
              <Button size="small" icon={<ReloadOutlined />} disabled={!canRestart || busy} loading={actionKeys.includes(`${service.id}:restart`)}>
                Restart
              </Button>
            </Popconfirm>
          </Space>
        )
      },
    },
  ]

  const localhostBlocked = Boolean(error && /localhost|session|403/i.test(error))
  const anyAction = actionKeys.length > 0

  return (
    <Card
      className="section-row"
      title={<Space><CloudServerOutlined /> Service Control</Space>}
      extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => void refresh()}>刷新</Button>}
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="Dashboard V2 服務生命週期控制"
          description="Stop/Restart 現在以實際 TCP listener PID 為準：先驗證 module identity，再終止 supervisor/root + listener，最後重新用 netstat 確認 port 已關閉。8781 仍保留 ARMED / unresolved-order fail-closed。"
        />
        {error ? <Alert type={localhostBlocked ? 'warning' : 'error'} showIcon message={localhostBlocked ? 'Service Control 只允許本機操作' : 'Service Control error'} description={error} /> : null}

        <Space wrap>
          <Button icon={<PlayCircleOutlined />} disabled={anyAction} loading={actionKeys.includes('group:core:start')} onClick={() => void runGroup('core', 'start')}>
            啟動 Core
          </Button>
          <Button icon={<PlayCircleOutlined />} disabled={anyAction} loading={actionKeys.includes('group:market:start')} onClick={() => void runGroup('market', 'start')}>
            啟動 Market Stack
          </Button>
          <Button icon={<PlayCircleOutlined />} disabled={anyAction} loading={actionKeys.includes('group:research:start')} onClick={() => void runGroup('research', 'start')}>
            啟動 Target Wallet Research
          </Button>
          <Button type="primary" icon={<PlayCircleOutlined />} disabled={anyAction} loading={actionKeys.includes('group:all:start')} onClick={() => void runGroup('all', 'start')}>
            啟動全部
          </Button>
          <Popconfirm
            title="停止所有 Dashboard-managed services?"
            description="8781 若仍 ARMED、有未結算訂單，或安全狀態無法確認，停止會被拒絕。Dashboard 4320 本身不會停止。"
            okText="Stop managed services"
            cancelText="取消"
            onConfirm={() => runGroup('all', 'stop')}
          >
            <Button danger icon={<PauseCircleOutlined />} disabled={anyAction} loading={actionKeys.includes('group:all:stop')}>
              停止所有 Managed
            </Button>
          </Popconfirm>
        </Space>

        <Table
          size="small"
          rowKey="id"
          loading={loading && !snapshot}
          dataSource={snapshot?.services || []}
          columns={columns}
          pagination={false}
          scroll={{ x: 1080 }}
        />

        <Text type="secondary">
          Ownership 欄仍用來顯示 registry / legacy PID 狀態，但不再是唯一的 Stop 判定。按 Stop/Restart 時會重新掃實際 listener；只有 command line 符合該服務已知 module，或可驗證為可信 supervisor 子程序時才會終止。停止成功還必須通過「port 確實消失」驗證。
        </Text>
      </Space>
    </Card>
  )
}