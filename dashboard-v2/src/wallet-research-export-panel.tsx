import { DownloadOutlined, ExportOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Checkbox, Divider, Space, Tag, Typography } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useWalletLabHealthStore } from './wallet-lab-health-store'
import { useWalletShadowStore } from './wallet-shadow-store'

const { Text } = Typography

type RowObject = Record<string, unknown>
type ExportGroup = 'layer' | 'strategy'

type ExportItem = {
  id: string
  label: string
  filenameStem: string
  group: ExportGroup
  source: string
  payload: unknown
}

function row(value: unknown): RowObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RowObject : {}
}

function rows(value: unknown): RowObject[] {
  return Array.isArray(value)
    ? value.filter((item): item is RowObject => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    : []
}

function hasFields(value: RowObject): boolean {
  return Object.keys(value).length > 0
}

function safeName(value: string): string {
  return value
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120) || 'research-export'
}

function stamp(): string {
  const date = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
}

const SECRET_KEY = /(api[_-]?key|authorization|bearer|password|secret|session[_-]?token|access[_-]?token|refresh[_-]?token|cookie)/i

function scrub(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(scrub)
  if (!value || typeof value !== 'object') return value
  const result: RowObject = {}
  for (const [key, child] of Object.entries(value as RowObject)) {
    result[key] = SECRET_KEY.test(key) ? '[REDACTED]' : scrub(child)
  }
  return result
}

function strategyContainerContext(container: RowObject): RowObject {
  const keys = [
    'paperOnly',
    'forwardOnly',
    'historicalBackfill',
    'targetEventsDriveStrategy',
    'targetEventsUsed',
    'liveOrdersAffected',
    'status',
    'deploymentBoundaryMs',
    'excludedDeploymentMarketId',
    'retired',
  ]
  return Object.fromEntries(keys.filter((key) => key in container).map((key) => [key, container[key]]))
}

function discoverStrategies(snapshot: RowObject): { items: ExportItem[]; topLevelKeys: Set<string> } {
  const items: ExportItem[] = []
  const topLevelKeys = new Set<string>()

  for (const [key, value] of Object.entries(snapshot)) {
    const container = row(value)
    if (!hasFields(container)) continue

    const variants = rows(container.variants)
    if (variants.length > 0) {
      topLevelKeys.add(key)
      const context = strategyContainerContext(container)
      variants.forEach((variant, index) => {
        const cohort = String(variant.cohort ?? variant.label ?? `${key}-${index + 1}`)
        items.push({
          id: `strategy:${key}:${cohort}`,
          label: cohort,
          filenameStem: `strategy-${safeName(cohort)}`,
          group: 'strategy',
          source: `8776/state.${key}.variants[${index}]`,
          payload: {
            strategy: variant,
            containerContext: context,
          },
        })
      })
      continue
    }

    const cohort = typeof container.cohort === 'string' && container.cohort.trim() ? container.cohort.trim() : null
    const strategyLikeKey = /(lab$|inference|audit|consensus|strategy)/i.test(key)
    if (!cohort && !strategyLikeKey) continue

    topLevelKeys.add(key)
    const label = cohort ?? key
    items.push({
      id: `strategy:${key}`,
      label,
      filenameStem: `strategy-${safeName(label)}`,
      group: 'strategy',
      source: `8776/state.${key}`,
      payload: container,
    })
  }

  items.sort((a, b) => a.label.localeCompare(b.label))
  return { items, topLevelKeys }
}

function withoutStrategyContainers(snapshot: RowObject, strategyKeys: Set<string>): RowObject {
  return Object.fromEntries(Object.entries(snapshot).filter(([key]) => !strategyKeys.has(key)))
}

function envelope(item: ExportItem, version: unknown, reportStatus: unknown) {
  return scrub({
    schemaVersion: 'btc5m-research-export-v1',
    generatedAtMs: Date.now(),
    generatedAtIso: new Date().toISOString(),
    project: 'BTC 5M Lab',
    exportKind: item.group,
    id: item.id,
    label: item.label,
    source: item.source,
    walletShadowVersion: version ?? null,
    reportStatus: reportStatus ?? null,
    payload: item.payload,
  })
}

function saveJson(item: ExportItem, version: unknown, reportStatus: unknown) {
  const body = JSON.stringify(envelope(item, version, reportStatus), null, 2)
  const blob = new Blob([body], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `btc5m-${item.filenameStem}-${stamp()}.json`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

function estimatedSizeKb(item: ExportItem, version: unknown, reportStatus: unknown): string {
  try {
    const bytes = new TextEncoder().encode(JSON.stringify(envelope(item, version, reportStatus))).byteLength
    return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(bytes < 1024 * 100 ? 1 : 0)} KB`
  } catch {
    return '—'
  }
}

export default function WalletResearchExportPanel() {
  const shadowService = useWalletShadowStore((state) => state.service)
  const observer8776 = useWalletLabHealthStore((state) => state.observer8776)
  const collector8777 = useWalletLabHealthStore((state) => state.collector8777)
  const makerBook8778 = useWalletLabHealthStore((state) => state.makerBook8778)
  const makerBookEth8779 = useWalletLabHealthStore((state) => state.makerBookEth8779)

  const snapshot = row(shadowService.data)
  const observer = row(observer8776.data)
  const version = snapshot.version ?? observer.version
  const reportStatus = snapshot.reportStatus ?? observer.reportStatus

  const items = useMemo(() => {
    const discovered = discoverStrategies(snapshot)
    const result: ExportItem[] = []

    if (hasFields(snapshot)) {
      result.push({
        id: 'layer:8776-core-state',
        label: '8776 · Wallet Shadow core state（策略容器已拆除）',
        filenameStem: 'layer-8776-wallet-shadow-core',
        group: 'layer',
        source: '8776/state',
        payload: withoutStrategyContainers(snapshot, discovered.topLevelKeys),
      })
    }
    if (hasFields(observer)) {
      result.push({
        id: 'layer:8776-health',
        label: '8776 · Observer health / report diagnostics',
        filenameStem: 'layer-8776-health',
        group: 'layer',
        source: '8776/health',
        payload: observer,
      })
    }

    const collector = row(collector8777.data)
    if (hasFields(collector)) {
      result.push({
        id: 'layer:8777-taker-signals',
        label: '8777 · Taker Signal collector',
        filenameStem: 'layer-8777-taker-signals',
        group: 'layer',
        source: '8777/state',
        payload: collector,
      })
    }

    const btcMaker = row(makerBook8778.data)
    if (hasFields(btcMaker)) {
      result.push({
        id: 'layer:8778-maker-book-btc',
        label: '8778 · BTC Maker book inference',
        filenameStem: 'layer-8778-maker-book-btc',
        group: 'layer',
        source: '8778/state',
        payload: btcMaker,
      })
    }

    const ethMaker = row(makerBookEth8779.data)
    if (hasFields(ethMaker)) {
      result.push({
        id: 'layer:8779-maker-book-eth',
        label: '8779 · ETH Maker book inference',
        filenameStem: 'layer-8779-maker-book-eth',
        group: 'layer',
        source: '8779/state',
        payload: ethMaker,
      })
    }

    return [...result, ...discovered.items]
  }, [snapshot, observer, collector8777.data, makerBook8778.data, makerBookEth8779.data])

  const [selected, setSelected] = useState<string[]>([])
  const initialized = useRef(false)

  useEffect(() => {
    if (initialized.current || items.length === 0) return
    initialized.current = true
    const preferred = items
      .filter((item) => item.group === 'layer' || ['TARGET_CORE_INTEGRATED_V1', 'TARGET_CORE_INTEGRATED_V2_TAKER_HEAVY'].includes(item.label))
      .map((item) => item.id)
    setSelected(preferred)
  }, [items])

  const layerItems = items.filter((item) => item.group === 'layer')
  const strategyItems = items.filter((item) => item.group === 'strategy')

  const toggle = (id: string, checked: boolean) => {
    setSelected((current) => checked
      ? current.includes(id) ? current : [...current, id]
      : current.filter((value) => value !== id))
  }

  const selectTargetCore = () => {
    const ids = items
      .filter((item) =>
        ['layer:8776-core-state', 'layer:8776-health', 'layer:8777-taker-signals', 'layer:8778-maker-book-btc'].includes(item.id)
        || ['TARGET_CORE_INTEGRATED_V1', 'TARGET_CORE_INTEGRATED_V2_TAKER_HEAVY'].includes(item.label))
      .map((item) => item.id)
    setSelected(ids)
  }

  const downloadSelected = async () => {
    const chosen = items.filter((item) => selected.includes(item.id))
    for (const item of chosen) {
      saveJson(item, version, reportStatus)
      // Keep files separate. A short gap also makes Chromium's multi-download
      // behavior more predictable without introducing a ZIP dependency.
      await new Promise((resolve) => window.setTimeout(resolve, 120))
    }
  }

  const renderItem = (item: ExportItem) => (
    <div
      key={item.id}
      style={{
        display: 'grid',
        gridTemplateColumns: '28px minmax(220px, 1fr) 90px 115px',
        gap: 8,
        alignItems: 'center',
        padding: '7px 0',
        borderBottom: '1px solid rgba(127,127,127,.14)',
      }}
    >
      <Checkbox checked={selected.includes(item.id)} onChange={(event) => toggle(item.id, event.target.checked)} />
      <div>
        <Text>{item.label}</Text>
        <div><Text type="secondary" style={{ fontSize: 12 }}>{item.source}</Text></div>
      </div>
      <Text type="secondary">{estimatedSizeKb(item, version, reportStatus)}</Text>
      <Button size="small" icon={<DownloadOutlined />} onClick={() => saveJson(item, version, reportStatus)}>
        JSON
      </Button>
    </div>
  )

  return (
    <Card title={<><ExportOutlined /> Research Lab · 分層分享 / ChatGPT 匯出</>} style={{ marginTop: 12 }}>
      <Alert
        type="info"
        showIcon
        message="資料層與策略 cohort 分檔，不把整個 Research Lab 混成單一大型 JSON"
        description="8776 core、8776 health、8777、8778、8779 各自獨立；8776 內具有 variants 的研究 Lab 會再依 cohort 拆成單獨策略檔。匯出只使用 Dashboard 已載入的 current state / recent performance，不掃描整個 SQLite 歷史庫；常見 token / API key / secret 欄位會自動遮蔽。"
      />

      <Space wrap style={{ marginTop: 12 }}>
        <Button onClick={selectTargetCore}>V1 vs V2 快速選取</Button>
        <Button onClick={() => setSelected(items.map((item) => item.id))}>全選目前可用項目</Button>
        <Button onClick={() => setSelected([])}>清除</Button>
        <Button
          type="primary"
          icon={<DownloadOutlined />}
          disabled={selected.length === 0}
          onClick={() => void downloadSelected()}
        >
          下載已選 {selected.length} 個 JSON（分檔）
        </Button>
        <Tag color={String(reportStatus).includes('ERROR') ? 'error' : 'blue'}>report {String(reportStatus ?? '—')}</Tag>
      </Space>

      <Divider orientation="left">資料層</Divider>
      {layerItems.length ? layerItems.map(renderItem) : <Text type="secondary">等待 8776 / 8777 / 8778 / 8779 state 載入。</Text>}

      <Divider orientation="left">策略 / Cohort</Divider>
      <div style={{ maxHeight: 420, overflowY: 'auto', paddingRight: 6 }}>
        {strategyItems.length ? strategyItems.map(renderItem) : (
          <Text type="secondary">目前 8776 state 尚未提供策略容器；observer 本身可能仍在運行或等待第一份 report。</Text>
        )}
      </div>

      <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
        要分享給 ChatGPT 時，直接上傳你要分析的幾個 JSON 即可。例如比較 Target Core V1/V2，建議傳 V1、V2、8777 Taker signals 與 8778 BTC Maker book；不需要上傳整個 DB。若瀏覽器阻擋多檔自動下載，可使用每列右側的 JSON 按鈕逐個下載。
      </Text>
    </Card>
  )
}
