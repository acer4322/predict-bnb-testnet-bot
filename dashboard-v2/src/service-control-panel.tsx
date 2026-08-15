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
  const actionKey = useServiceControlStore((state) => state.actionKey)
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
        const busy = Boolean(actionKey?.startsWith(`${service.id}:`) || actionKey?.startsWith('group:'))
        return (
          <Space size={4} wrap>
            <Button
              size="small"
              type="primary"
              icon={<PlayCircleOutlined />}
              disabled={!service.canStart || busy}
              loading={actionKey === `${service.id}:start`}
              onClick={() => void runService(service.id, 'start')}
            >
              Start
            </Button>
            <Popconfirm
              title={`停止 ${service.label}?`}
              description={service.id === 'echtgeld' ? '如果 Engine 已 ARMED 或有未結算訂單，後端會拒絕停止。' : '只會停止 Dashboard 驗證為自己管理的 PID。'}
              okText="Stop"
              cancelText="取消"
              onConfirm={() => runService(service.id, 'stop')}
            >
              <Button size="small" danger icon={<StopOutlined />} disabled={!service.canStop || busy} loading={actionKey === `${service.id}:stop`}>
                Stop
              </Button>
            </Popconfirm>
            <Popconfirm
              title={`重啟 ${service.label}?`}
              description="External/unowned process 不會被終止。"
              okText="Restart"
              cancelText="取消"
              onConfirm={() => runService(service.id, 'restart')}
            >
              <Button size="small" icon={<ReloadOutlined />} disabled={!service.canRestart || busy} loading={actionKey === `${service.id}:restart`}>
                Restart
              </Button>
            </Popconfirm>
          </Space>
        )
      },
    },
  ]

  const localhostBlocked = Boolean(error && /localhost|session|403/i.test(error))

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
          message="Dashboard V2 現在是服務生命週期控制中心"
          description="控制 API 僅允許 localhost + 當前 Vite session token。8766–8769 與 8770/8772/8773 以 supervisor 群組操作；8778/8779 可獨立啟停研究 collector；8781 啟動後仍維持 PAUSED/DISARMED。4320 不會從頁面內自我重啟。"
        />
        {error ? <Alert type={localhostBlocked ? 'warning' : 'error'} showIcon message={localhostBlocked ? 'Service Control 只允許本機操作' : 'Service Control error'} description={error} /> : null}

        <Space wrap>
          <Button icon={<PlayCircleOutlined />} disabled={Boolean(actionKey)} loading={actionKey === 'group:core:start'} onClick={() => void runGroup('core', 'start')}>
            啟動 Core
          </Button>
          <Button icon={<PlayCircleOutlined />} disabled={Boolean(actionKey)} loading={actionKey === 'group:market:start'} onClick={() => void runGroup('market', 'start')}>
            啟動 Market Stack
          </Button>
          <Button icon={<PlayCircleOutlined />} disabled={Boolean(actionKey)} loading={actionKey === 'group:research:start'} onClick={() => void runGroup('research', 'start')}>
            啟動 Maker Research
          </Button>
          <Button type="primary" icon={<PlayCircleOutlined />} disabled={Boolean(actionKey)} loading={actionKey === 'group:ebm:start'} onClick={() => void runGroup('ebm', 'start')}>
            啟動 EBM Echtgeld
          </Button>
          <Button type="primary" icon={<PlayCircleOutlined />} disabled={Boolean(actionKey)} loading={actionKey === 'group:all:start'} onClick={() => void runGroup('all', 'start')}>
            啟動全部
          </Button>
          <Popconfirm
            title="停止所有 Dashboard-managed services?"
            description="8781 若仍 ARMED、有未結算訂單，或安全狀態無法確認，整組停止會在動任何 managed process 前被拒絕。EXTERNAL 服務會保留，Dashboard 4320 本身不會停止。"
            okText="Stop managed services"
            cancelText="取消"
            onConfirm={() => runGroup('all', 'stop')}
          >
            <Button danger icon={<PauseCircleOutlined />} disabled={Boolean(actionKey)} loading={actionKey === 'group:all:stop'}>
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
          EXTERNAL 表示 port 雖然在線，但沒有 Dashboard registry 或可信 launcher PID ownership；此頁故意不提供 Stop/Restart，避免誤殺其他 Python process。舊版 launcher 留下的可信 PID file 會顯示 LEGACY_MANAGED，仍可安全接管生命週期操作。
        </Text>
      </Space>
    </Card>
  )
}
