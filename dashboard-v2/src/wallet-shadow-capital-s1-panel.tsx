import { Alert, Card, Col, Descriptions, Row, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useWalletShadowStore } from './wallet-shadow-store'

const { Text } = Typography
type RowObject = Record<string, unknown>

function row(value: unknown): RowObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
}

function rows(value: unknown): RowObject[] {
  return Array.isArray(value) ? value.filter((item): item is RowObject => Boolean(item && typeof item === 'object' && !Array.isArray(item))) : []
}

function number(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value)
}

function fixed(value: unknown, digits = 2): string {
  const parsed = number(value)
  return parsed === null ? '—' : parsed.toFixed(digits)
}

function pct(value: unknown): string {
  const parsed = number(value)
  return parsed === null ? '—' : `${(parsed * 100).toFixed(1)}%`
}

function money(value: unknown): string {
  const parsed = number(value)
  if (parsed === null) return '—'
  return `${parsed > 0 ? '+' : ''}$${parsed.toFixed(2)}`
}

function time(value: unknown): string {
  const parsed = number(value)
  return parsed && parsed > 0
    ? new Date(parsed).toLocaleTimeString('zh-TW', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—'
}

function sideTag(value: unknown) {
  const side = text(value).toUpperCase()
  if (side === 'UP') return <Tag color="success">UP</Tag>
  if (side === 'DOWN') return <Tag color="error">DOWN</Tag>
  return <Tag>{side}</Tag>
}

function resultTag(value: unknown) {
  const status = text(value).toUpperCase()
  if (status === 'WIN') return <Tag color="success">WIN</Tag>
  if (status === 'LOSS') return <Tag color="error">LOSS</Tag>
  return <Tag>{status}</Tag>
}

function ComparisonCard({ title, performance, capital = false }: { title: string; performance: RowObject; capital?: boolean }) {
  return (
    <Card size="small" title={title}>
      <Row gutter={[8, 8]}>
        <Col span={8}><Statistic title="Gross PnL" value={money(performance.grossPnlUsdt)} /></Col>
        <Col span={8}><Statistic title="Win rate" value={pct(performance.winRate)} /></Col>
        <Col span={8}><Statistic title="Gross ROI" value={pct(performance.grossRoi)} /></Col>
        <Col span={8}><Statistic title="總成本" value={`$${fixed(performance.grossCostUsdt ?? performance.costUsdt)}`} /></Col>
        <Col span={8}><Statistic title="已結算" value={number(performance.settledMarkets) ?? 0} /></Col>
        <Col span={8}><Statistic title={capital ? '事件保留率' : '交易市場'} value={capital ? pct(performance.eventRetention) : number(performance.tradedMarkets) ?? 0} /></Col>
      </Row>
    </Card>
  )
}

export default function WalletShadowCapitalS1Panel() {
  const service = useWalletShadowStore((state) => state.service)
  const snapshot = row(service.data)
  const v0 = row(snapshot.performance)
  const v1 = row(row(snapshot.takerV1).performance)
  const cohort = row(snapshot.capitalS1)
  const performance = row(cohort.performance)
  const config = row(cohort.config)
  const current = row(cohort.current)
  const recentEvents = rows(current.events)
  const recentMarkets = rows(performance.recentMarkets)
  const cap100 = row(snapshot.capitalS1Cap100Stress)
  const cap100Performance = row(cap100.performance)
  const cap100Current = row(cap100.current)
  const cap100Events = rows(cap100Current.events)
  const cap100Markets = rows(cap100Performance.recentMarkets)
  const min1 = row(snapshot.min1ExecCap100)
  const min1Performance = row(min1.performance)
  const min1Current = row(min1.current)
  const min1Events = rows(min1Current.events)
  const min1Markets = rows(min1Performance.recentMarkets)
  const growth = row(snapshot.min1WalletGrowth)
  const growthPerformance = row(growth.performance)
  const growthCurrent = row(growth.current)
  const growthMarkets = rows(growthPerformance.recentMarkets)
  const time20 = row(snapshot.min1WalletGrowthTime20)
  const time20Performance = row(time20.performance)
  const time20Current = row(time20.current)
  const time20Markets = rows(time20Performance.recentMarkets)
  const batched = row(snapshot.min1BatchedMakerTakerReserve)
  const batchedPerformance = row(batched.performance)
  const batchedCurrent = row(batched.current)
  const batchedEvidence = row(batched.targetRatioEvidence)
  const batchedMakerEvidence = row(batchedEvidence.maker)
  const batchedTakerEvidence = row(batchedEvidence.taker)
  const batchedMarkets = rows(batchedPerformance.recentMarkets)

  const eventColumns: TableColumnsType<RowObject> = [
    { title: '時間', key: 'time', width: 90, render: (_, item) => time(item.atMs) },
    { title: '來源事件', key: 'event', width: 140, render: (_, item) => <Tag color={text(item.role) === 'TAKER' ? 'purple' : 'blue'}>{text(item.eventType)}</Tag> },
    { title: '方向', key: 'side', width: 75, render: (_, item) => sideTag(item.side) },
    { title: '原始 / S1 shares', key: 'shares', width: 125, render: (_, item) => `${fixed(item.originalShares, 1)} / ${fixed(item.scaledShares, 2)}` },
    { title: '價格', key: 'price', width: 75, render: (_, item) => fixed(item.price, 3) },
    { title: '決策', key: 'accepted', width: 80, render: (_, item) => item.accepted === true ? <Tag color="success">保留</Tag> : <Tag color="warning">阻擋</Tag> },
    { title: '原因', key: 'reason', render: (_, item) => text(item.reason) },
  ]

  const resultColumns: TableColumnsType<RowObject> = [
    { title: 'Market', key: 'market', width: 85, render: (_, item) => `#${text(item.market_id)}` },
    { title: 'Winner', key: 'winner', width: 75, render: (_, item) => sideTag(item.winner) },
    { title: '結果', key: 'status', width: 75, render: (_, item) => resultTag(item.status) },
    { title: '成本', key: 'cost', width: 85, render: (_, item) => `$${fixed(item.cost_usdt)}` },
    { title: 'Gross PnL', key: 'gross', width: 100, render: (_, item) => <strong>{money(item.gross_pnl_usdt)}</strong> },
    { title: 'Gross ROI', key: 'roi', width: 85, render: (_, item) => pct(item.gross_roi) },
    { title: '200bps ROI', key: 'fee', width: 90, render: (_, item) => pct(item.fee_net_roi) },
    { title: '+1 tick', key: 'stress1', width: 80, render: (_, item) => pct(item.stress_1tick_roi) },
    { title: '+2 ticks', key: 'stress2', width: 80, render: (_, item) => pct(item.stress_2tick_roi) },
  ]

  const cap100Columns: TableColumnsType<RowObject> = [
    { title: 'Market', key: 'market', width: 85, render: (_, item) => `#${text(item.market_id)}` },
    { title: '結果', key: 'status', width: 75, render: (_, item) => resultTag(item.status) },
    { title: '是否截斷', key: 'truncated', width: 85, render: (_, item) => item.truncated === 1 ? <Tag color="warning">YES</Tag> : <Tag color="success">NO</Tag> },
    { title: '截斷 / 阻擋事件', key: 'events', width: 115, render: (_, item) => `${text(item.truncated_event_count, '0')} / ${text(item.blocked_event_count, '0')}` },
    { title: '資本', key: 'capital', width: 85, render: (_, item) => `$${fixed(item.capital_cost_usdt)}` },
    { title: '封頂 PnL', key: 'pnl', width: 95, render: (_, item) => <strong>{money(item.net_pnl_usdt)}</strong> },
    { title: '封頂 ROI', key: 'roi', width: 85, render: (_, item) => pct(item.net_roi) },
    { title: '相對 S1 PnL 差', key: 'delta', width: 105, render: (_, item) => money(item.pnl_delta_usdt) },
  ]
  const cap100EventColumns: TableColumnsType<RowObject> = [
    { title: '時間', key: 'time', width: 90, render: (_, item) => time(item.atMs) },
    { title: '角色 / 方向', key: 'side', width: 120, render: (_, item) => <>{<Tag>{text(item.role)}</Tag>}{sideTag(item.side)}</> },
    { title: '要求 / 執行 shares', key: 'shares', width: 135, render: (_, item) => `${fixed(item.requestedShares, 2)} / ${fixed(item.executedShares, 2)}` },
    { title: '價格', key: 'price', width: 75, render: (_, item) => fixed(item.price, 3) },
    { title: '本筆資本', key: 'capital', width: 90, render: (_, item) => `$${fixed(item.capitalCostUsdt)}` },
    { title: '累積資本', key: 'used', width: 90, render: (_, item) => `$${fixed(item.usedCapitalAfterUsdt)}` },
    { title: '狀態', key: 'status', render: (_, item) => item.blocked === true ? <Tag color="error">完全阻擋</Tag> : item.truncated === true || item.capTruncated === true ? <Tag color="warning">部分截斷</Tag> : item.minimumUplift === true ? <Tag color="processing">最低單放大</Tag> : <Tag color="success">完整執行</Tag> },
  ]
  const min1MarketColumns: TableColumnsType<RowObject> = [
    { title: 'Market', key: 'market', width: 85, render: (_, item) => `#${text(item.market_id)}` },
    { title: 'Winner', key: 'winner', width: 75, render: (_, item) => sideTag(item.winner) },
    { title: '結果', key: 'status', width: 75, render: (_, item) => resultTag(item.status) },
    { title: '資本', key: 'capital', width: 85, render: (_, item) => `$${fixed(item.capital_cost_usdt)}` },
    { title: 'Net PnL', key: 'pnl', width: 95, render: (_, item) => <strong>{money(item.net_pnl_usdt)}</strong> },
    { title: 'Net ROI', key: 'roi', width: 85, render: (_, item) => pct(item.net_roi) },
    { title: '最低單放大', key: 'uplift', width: 90, render: (_, item) => text(item.minimum_uplift_events, '0') },
    { title: '截斷 / 阻擋', key: 'blocked', width: 100, render: (_, item) => `${text(item.cap_truncated_events, '0')} / ${text(item.blocked_events, '0')}` },
    { title: '撞上限', key: 'cap', width: 75, render: (_, item) => item.cap_hit === 1 ? <Tag color="warning">YES</Tag> : <Tag color="success">NO</Tag> },
  ]

  return (
    <Card title="V0 Capital S1 · 高頻低資本 Forward Paper" style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="保留 V0 的進場節奏，縮小單筆曝險並阻擋第二次 Taker 換邊後的追價"
        description="S1 不讀目標錢包事件，也不回填舊市場。Maker 18 shares 縮為 1；Taker 原始單筆先封頂 36 再縮為最多 2 shares。部署當下市場排除，從下一個完整市場開始計分。"
        style={{ marginBottom: 12 }}
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={8}><ComparisonCard title="V0 原始基準" performance={v0} /></Col>
        <Col xs={24} xl={8}><ComparisonCard title="Taker V1 對照" performance={v1} /></Col>
        <Col xs={24} xl={8}><ComparisonCard title="V0 Capital S1" performance={performance} capital /></Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={12}>
          <Card size="small" title="S1 成本與壓力測試">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="平均 / 最大單場成本">${fixed(performance.averageCostPerMarketUsdt)} / ${fixed(performance.maximumCostPerMarketUsdt)}</Descriptions.Item>
              <Descriptions.Item label="最大回撤">{money(performance.maxDrawdownUsdt)}</Descriptions.Item>
              <Descriptions.Item label="200 bps 淨 PnL / ROI">{money(performance.feeNetPnlUsdt)} / {pct(performance.feeNetRoi)}</Descriptions.Item>
              <Descriptions.Item label="1 / 2 tick 壓力 ROI">{pct(performance.stress1TickRoi)} / {pct(performance.stress2TickRoi)}</Descriptions.Item>
              <Descriptions.Item label="來源 / 保留事件">{text(performance.sourceEvents, '0')} / {text(performance.acceptedEvents, '0')}</Descriptions.Item>
              <Descriptions.Item label="最長連敗">{text(performance.longestLossStreak, '0')}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card size="small" title="鎖定策略與當輪狀態">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="狀態"><Tag color={text(cohort.status) === 'ACTIVE' ? 'success' : 'processing'}>{text(cohort.status)}</Tag></Descriptions.Item>
              <Descriptions.Item label="當輪啟用">{current.active === true ? <Tag color="success">YES</Tag> : <Tag>NO</Tag>}</Descriptions.Item>
              <Descriptions.Item label="Maker effective">{fixed(config.makerEffectiveShares, 1)} share</Descriptions.Item>
              <Descriptions.Item label="Taker effective cap">{fixed(config.takerEffectiveCapShares, 1)} shares</Descriptions.Item>
              <Descriptions.Item label="Taker 換邊 / 上限">{text(current.takerSideSwitches, '0')} / {text(config.maxTakerSideSwitches, '1')}</Descriptions.Item>
              <Descriptions.Item label="後續 Taker 阻擋">{current.takerBlocked === true ? <Tag color="warning">YES</Tag> : <Tag color="success">NO</Tag>}</Descriptions.Item>
              <Descriptions.Item label="最後 Taker 方向">{sideTag(current.lastTakerSide)}</Descriptions.Item>
              <Descriptions.Item label="排除部署市場">#{text(performance.excludedDeploymentMarketId)}</Descriptions.Item>
            </Descriptions>
            <Text type="secondary">{text(cohort.accountingCaveat)}</Text>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="S1 當輪最近事件" style={{ marginTop: 12 }}>
        <Table rowKey={(item) => text(item.id)} dataSource={recentEvents} columns={eventColumns} size="small" pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 900 }} locale={{ emptyText: '等待下一個完整市場的 V0 fill / taker 事件' }} />
      </Card>

      <Card size="small" title="S1 最近已結算市場" style={{ marginTop: 12 }}>
        <Table rowKey={(item) => text(item.market_id)} dataSource={recentMarkets} columns={resultColumns} size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 820 }} locale={{ emptyText: 'Forward cohort 尚無已結算完整市場' }} />
      </Card>

      <Card title="S1 CAP100 壓力測試 · 每市場費用內含最大 100 USDT" style={{ marginTop: 12 }}>
        <Alert
          type="warning"
          showIcon
          message="專門檢查策略是否因中途撞到資本上限而失去後續對沖／修正事件"
          description="這是 S1 的獨立 forward 對照。最後一筆會縮到剩餘額度，額度用完後所有後續合格事件仍被記錄但不成交；因此能量化被截斷後是否出現大虧損，而不是把不完整市場藏起來。"
          style={{ marginBottom: 12 }}
        />
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6} xl={3}><Statistic title="Net PnL" value={money(cap100Performance.netPnlUsdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Net ROI" value={pct(cap100Performance.netRoi)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Win rate" value={pct(cap100Performance.winRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="截斷市場率" value={pct(cap100Performance.truncatedMarketRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="截斷 / 阻擋事件" value={`${text(cap100Performance.truncatedEvents, '0')} / ${text(cap100Performance.blockedEvents, '0')}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="相對 S1 PnL 差" value={money(cap100Performance.pnlDeltaVsS1Usdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="最大單場資本" value={`$${fixed(cap100Performance.maximumCapitalPerMarketUsdt)}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="最大回撤" value={money(cap100Performance.maxDrawdownUsdt)} /></Col>
        </Row>
        <Descriptions size="small" column={{ xs: 1, md: 2, xl: 4 }} style={{ marginTop: 12 }}>
          <Descriptions.Item label="狀態"><Tag color={text(cap100.status) === 'ACTIVE' ? 'success' : 'processing'}>{text(cap100.status)}</Tag></Descriptions.Item>
          <Descriptions.Item label="當輪已用 / 剩餘">${fixed(cap100Current.usedCapitalUsdt)} / ${fixed(cap100Current.remainingCapitalUsdt)}</Descriptions.Item>
          <Descriptions.Item label="當輪已截斷">{cap100Current.truncated === true ? <Tag color="warning">YES</Tag> : <Tag color="success">NO</Tag>}</Descriptions.Item>
          <Descriptions.Item label="平均單場資本">${fixed(cap100Performance.averageCapitalPerMarketUsdt)}</Descriptions.Item>
        </Descriptions>
        <Card size="small" title="CAP100 當輪事件" style={{ marginTop: 12 }}>
          <Table rowKey={(item) => text(item.id)} dataSource={cap100Events} columns={cap100EventColumns} size="small" pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 800 }} locale={{ emptyText: '等待下一個完整市場' }} />
        </Card>
        <Card size="small" title="CAP100 最近已結算市場" style={{ marginTop: 12 }}>
          <Table rowKey={(item) => text(item.market_id)} dataSource={cap100Markets} columns={cap100Columns} size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 800 }} locale={{ emptyText: '尚無已結算 forward 市場' }} />
        </Card>
      </Card>

      <Card title="MIN1_EXEC_CAP100 · 最低單可執行約束" style={{ marginTop: 12 }}>
        <Alert
          type="info"
          showIcon
          message="每筆本金至少 1 USDT，並用實際成交 shares 重新運行 inventory 與 Taker 邏輯"
          description="這不是把舊 S1 每筆事後補到 $1。Maker 最低單造成的 shares 放大會改變 residual，Taker 因此由本 cohort 自己重新觸發；每市場本金加 Taker fee 仍不得超過 $100。剩餘資本不足最低單時直接阻擋。"
          style={{ marginBottom: 12 }}
        />
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6} xl={3}><Statistic title="Net PnL" value={money(min1Performance.netPnlUsdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Net ROI" value={pct(min1Performance.netRoi)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Win rate" value={pct(min1Performance.winRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="撞 $100 市場率" value={pct(min1Performance.capHitMarketRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="最低單放大率" value={pct(min1Performance.minimumUpliftRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="執行 / 全事件" value={`${text(min1Performance.executedEvents, '0')} / ${text(min1Performance.events, '0')}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="最大單場資本" value={`$${fixed(min1Performance.maximumCapitalPerMarketUsdt)}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="最大回撤" value={money(min1Performance.maxDrawdownUsdt)} /></Col>
        </Row>
        <Descriptions size="small" column={{ xs: 1, md: 2, xl: 4 }} style={{ marginTop: 12 }}>
          <Descriptions.Item label="狀態"><Tag color={text(min1.status) === 'ACTIVE' ? 'success' : 'processing'}>{text(min1.status)}</Tag></Descriptions.Item>
          <Descriptions.Item label="當輪已用 / 剩餘">${fixed(min1Current.usedCapitalUsdt)} / ${fixed(min1Current.remainingCapitalUsdt)}</Descriptions.Item>
          <Descriptions.Item label="Maker UP / DOWN">{fixed(min1Current.makerUpShares, 2)} / {fixed(min1Current.makerDownShares, 2)}</Descriptions.Item>
          <Descriptions.Item label="Maker residual">{fixed(min1Current.makerResidualShares, 2)} shares</Descriptions.Item>
          <Descriptions.Item label="Taker UP / DOWN">{fixed(min1Current.takerUpShares, 2)} / {fixed(min1Current.takerDownShares, 2)}</Descriptions.Item>
          <Descriptions.Item label="Taker 換邊">{text(min1Current.takerSideSwitches, '0')}</Descriptions.Item>
          <Descriptions.Item label="被阻擋事件">{text(min1Performance.blockedEvents, '0')}</Descriptions.Item>
          <Descriptions.Item label="最長連敗">{text(min1Performance.longestLossStreak, '0')}</Descriptions.Item>
        </Descriptions>
        <Text type="secondary">{text(min1.executionCaveat)}</Text>
        <Card size="small" title="MIN1 當輪事件" style={{ marginTop: 12 }}>
          <Table rowKey={(item) => text(item.id)} dataSource={min1Events} columns={cap100EventColumns} size="small" pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 800 }} locale={{ emptyText: '等待下一個完整市場' }} />
        </Card>
        <Card size="small" title="MIN1 最近已結算市場" style={{ marginTop: 12 }}>
          <Table rowKey={(item) => text(item.market_id)} dataSource={min1Markets} columns={min1MarketColumns} size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 850 }} locale={{ emptyText: '尚無已結算 forward 市場' }} />
        </Card>
      </Card>

      <Card title="MIN1_WALLET_GROWTH · 100 USDT 自動成長錢包" style={{ marginTop: 12 }}>
        <Alert type="success" showIcon
          message="沒有永久 100 USDT 上限；錢包成長後，下一市場的事前預算會自動放大"
          description="每市場開始時不可重做地鎖定當時可用現金的 20%（至少 $1），其餘保留給後續市場與結算延遲。市場內不超支，結算 payout 回到錢包；未結算資本不能重複使用。餘額低於 $1 才判定無法繼續。"
          style={{ marginBottom: 12 }} />
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6} xl={3}><Statistic title="可用現金" value={`$${fixed(growthPerformance.availableCashUsdt)}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="已實現錢包價值" value={`$${fixed(growthPerformance.realizedWalletValueUsdt)}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="錢包成長率" value={pct(growthPerformance.walletGrowthRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="累計 Net PnL" value={money(growthPerformance.netPnlUsdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="部署資本 ROI" value={pct(growthPerformance.netRoiOnDeployedCapital)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="勝率" value={pct(growthPerformance.winRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="最大回撤" value={money(growthPerformance.maxDrawdownUsdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="預算耗盡市場" value={text(growthPerformance.budgetHitMarkets, '0')} /></Col>
        </Row>
        <Descriptions size="small" column={{ xs: 1, md: 2, xl: 4 }} style={{ marginTop: 12 }}>
          <Descriptions.Item label="狀態"><Tag color={text(growth.status) === 'BANKRUPT' ? 'error' : text(growth.status) === 'ACTIVE' ? 'success' : 'processing'}>{text(growth.status)}</Tag></Descriptions.Item>
          <Descriptions.Item label="當輪規劃預算">${fixed(growthCurrent.plannedBudgetUsdt)}</Descriptions.Item>
          <Descriptions.Item label="當輪已用 / 剩餘">${fixed(growthCurrent.usedCapitalUsdt)} / ${fixed(growthCurrent.remainingMarketBudgetUsdt)}</Descriptions.Item>
          <Descriptions.Item label="事前配置比例">20% available cash</Descriptions.Item>
          <Descriptions.Item label="已結算 / 待結算">{text(growthPerformance.settledMarkets, '0')} / {text(growthPerformance.pendingMarkets, '0')}</Descriptions.Item>
          <Descriptions.Item label="阻擋事件">{text(growthPerformance.blockedEvents, '0')}</Descriptions.Item>
          <Descriptions.Item label="最長連敗">{text(growthPerformance.longestLossStreak, '0')}</Descriptions.Item>
          <Descriptions.Item label="永久錢包上限"><Tag color="success">NONE</Tag></Descriptions.Item>
        </Descriptions>
        <Text type="secondary">{text(growth.executionCaveat)}</Text>
        <Card size="small" title="成長錢包最近已結算市場" style={{ marginTop: 12 }}>
          <Table rowKey={(item) => text(item.market_id)} dataSource={growthMarkets} columns={min1MarketColumns} size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 850 }} locale={{ emptyText: '等待第一個完整 forward 市場結算' }} />
        </Card>
      </Card>

      <Card title="MIN1_WALLET_GROWTH_TIME20 · 每分鐘累積解鎖 20%" style={{ marginTop: 12 }}>
        <Alert type="info" showIcon
          message="同一筆成長錢包市場預算分五段投入，用來對照一次開放全部預算是否更容易過早耗盡"
          description="市場開始仍鎖定當時可用現金的 20% 作為本場總預算；第 1 至第 5 分鐘分別累積解鎖 20%、40%、60%、80%、100%。前段未用額度可帶到後段，但不可提前借用未解鎖額度，每筆本金仍至少 1 USDT。"
          style={{ marginBottom: 12 }} />
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6} xl={3}><Statistic title="可用現金" value={`$${fixed(time20Performance.availableCashUsdt)}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="已實現錢包價值" value={`$${fixed(time20Performance.realizedWalletValueUsdt)}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="錢包成長率" value={pct(time20Performance.walletGrowthRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="累計 Net PnL" value={money(time20Performance.netPnlUsdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="部署資本 ROI" value={pct(time20Performance.netRoiOnDeployedCapital)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="勝率" value={pct(time20Performance.winRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="最大回撤" value={money(time20Performance.maxDrawdownUsdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="預算耗盡市場" value={text(time20Performance.budgetHitMarkets, '0')} /></Col>
        </Row>
        <Descriptions size="small" column={{ xs: 1, md: 2, xl: 4 }} style={{ marginTop: 12 }}>
          <Descriptions.Item label="狀態"><Tag color={text(time20.status) === 'BANKRUPT' ? 'error' : text(time20.status) === 'ACTIVE' ? 'success' : 'processing'}>{text(time20.status)}</Tag></Descriptions.Item>
          <Descriptions.Item label="當輪規劃總預算">${fixed(time20Current.plannedBudgetUsdt)}</Descriptions.Item>
          <Descriptions.Item label="目前累積解鎖">{pct(time20Current.unlockedFraction)} / ${fixed(time20Current.unlockedBudgetUsdt)}</Descriptions.Item>
          <Descriptions.Item label="已用 / 已解鎖剩餘">${fixed(time20Current.usedCapitalUsdt)} / ${fixed(time20Current.remainingUnlockedBudgetUsdt)}</Descriptions.Item>
          <Descriptions.Item label="全場尚未使用">${fixed(time20Current.remainingMarketBudgetUsdt)}</Descriptions.Item>
          <Descriptions.Item label="已結算 / 待結算">{text(time20Performance.settledMarkets, '0')} / {text(time20Performance.pendingMarkets, '0')}</Descriptions.Item>
          <Descriptions.Item label="阻擋事件">{text(time20Performance.blockedEvents, '0')}</Descriptions.Item>
          <Descriptions.Item label="永久錢包上限"><Tag color="success">NONE</Tag></Descriptions.Item>
        </Descriptions>
        <Text type="secondary">{text(time20.executionCaveat)}</Text>
        <Card size="small" title="TIME20 最近已結算市場" style={{ marginTop: 12 }}>
          <Table rowKey={(item) => text(item.market_id)} dataSource={time20Markets} columns={min1MarketColumns} size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 850 }} locale={{ emptyText: '等待第一個完整 forward 市場結算' }} />
        </Card>
      </Card>

      <Card title="MIN1_BATCHED_MAKER_TAKER_RESERVE · 批次 Maker / Taker 預留" style={{ marginTop: 12 }}>
        <Alert type="success" showIcon
          message="避免把每個低價 Maker 訊號各自放大成 1 USDT；18:1 只作 Maker 單位縮放，不再綁定 Taker"
          description="同方向 Maker 訊號先累積至合法 $1 才成交，Maker 最多使用本場預算 20%；另外 80% 保留給連續兩次同方向確認後的淨 residual Taker 修正。這是全新 forward cohort，不回填既有結果。"
          style={{ marginBottom: 12 }} />
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6} xl={3}><Statistic title="可用現金" value={`$${fixed(batchedPerformance.availableCashUsdt)}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="錢包成長率" value={pct(batchedPerformance.walletGrowthRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Net PnL" value={money(batchedPerformance.netPnlUsdt)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="勝率" value={pct(batchedPerformance.winRate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Maker 已用" value={`$${fixed(batchedCurrent.makerCapitalUsdt)}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Taker 已用" value={`$${fixed(batchedCurrent.takerCapitalUsdt)}`} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Maker 18倍數率" value={pct(batchedMakerEvidence.multipleOf18Rate)} /></Col>
          <Col xs={12} md={6} xl={3}><Statistic title="Taker 18倍數率" value={pct(batchedTakerEvidence.multipleOf18Rate)} /></Col>
        </Row>
        <Descriptions size="small" column={{ xs: 1, md: 2, xl: 4 }} style={{ marginTop: 12 }}>
          <Descriptions.Item label="狀態"><Tag color={text(batched.status) === 'BANKRUPT' ? 'error' : text(batched.status) === 'ACTIVE' ? 'success' : 'processing'}>{text(batched.status)}</Tag></Descriptions.Item>
          <Descriptions.Item label="總預算 / Maker 上限 / Taker 預留">${fixed(batchedCurrent.plannedBudgetUsdt)} / ${fixed(batchedCurrent.makerBudgetUsdt)} / ${fixed(batchedCurrent.takerReserveUsdt)}</Descriptions.Item>
          <Descriptions.Item label="待累積 Maker UP / DOWN">{fixed(row(batchedCurrent.pendingMakerShares).UP, 2)} / {fixed(row(batchedCurrent.pendingMakerShares).DOWN, 2)} shares</Descriptions.Item>
          <Descriptions.Item label="淨 residual">{fixed(batchedCurrent.netResidualShares, 2)} shares</Descriptions.Item>
          <Descriptions.Item label="方向確認">{text(batchedCurrent.coreCandidateSide)} × {text(batchedCurrent.coreStabilityCount, '0')}</Descriptions.Item>
          <Descriptions.Item label="目標 Maker 聚合單中位數">{fixed(batchedMakerEvidence.medianShares, 2)} shares</Descriptions.Item>
          <Descriptions.Item label="目標 Taker 聚合單中位數">{fixed(batchedTakerEvidence.medianShares, 2)} shares</Descriptions.Item>
          <Descriptions.Item label="已結算 / 待結算">{text(batchedPerformance.settledMarkets, '0')} / {text(batchedPerformance.pendingMarkets, '0')}</Descriptions.Item>
        </Descriptions>
        <Text type="secondary">{text(batchedEvidence.interpretation)}　{text(batched.executionCaveat)}</Text>
        <Card size="small" title="BATCHED RESERVE 最近已結算市場" style={{ marginTop: 12 }}>
          <Table rowKey={(item) => text(item.market_id)} dataSource={batchedMarkets} columns={min1MarketColumns} size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 850 }} locale={{ emptyText: '等待第一個完整 forward 市場結算' }} />
        </Card>
      </Card>
    </Card>
  )
}
