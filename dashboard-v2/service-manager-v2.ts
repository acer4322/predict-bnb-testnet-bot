import { closeSync, existsSync, mkdirSync, openSync, promises as fs } from 'node:fs'
import { execFile, spawn } from 'node:child_process'
import { dirname, join } from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

type ServiceMode = 'direct' | 'script' | 'self'
type Ownership = 'MANAGED' | 'LEGACY_MANAGED' | 'EXTERNAL' | 'NONE' | 'SELF'
type RuntimeState = 'ONLINE' | 'OFFLINE' | 'PARTIAL' | 'CONFLICT' | 'CRASHED'
type ServiceAction = 'start' | 'stop' | 'restart'

type ServiceMember = {
  port: number
  label: string
  healthPath: string
  required?: boolean
  expectedProcessToken?: string
}

type LegacyPidFile = {
  file: string
  expectedProcessToken: string
}

type ServiceDefinition = {
  id: string
  label: string
  description: string
  mode: ServiceMode
  members: ServiceMember[]
  rootProcessToken?: string
  command?: string
  args?: string[]
  script?: string
  legacyPidFiles?: LegacyPidFile[]
  safetyNote?: string
}

type RegistryEntry = {
  rootPid?: number
  listenerPids?: number[]
  startedAt: number
  source: 'service-manager'
}

type Registry = {
  version: 1
  services: Record<string, RegistryEntry>
}

type ListenerInfo = {
  port: number
  pid: number
  parentPid: number | null
  commandLine: string
}

type ProcessInfo = {
  pid: number
  parentPid: number | null
  commandLine: string
}

type HealthProbe = {
  healthy: boolean
  status: number | null
  payload: unknown
}

type OwnershipResolution = {
  ownership: Ownership
  rootPid: number | null
  ownedPids: number[]
  startedAt: number | null
}

const SERVICES: ServiceDefinition[] = [
  {
    id: 'dashboard',
    label: 'Dashboard V2',
    description: 'Web control plane. It intentionally cannot restart itself.',
    mode: 'self',
    members: [{ port: 4320, label: 'Dashboard V2', healthPath: '/' }],
    safetyNote: 'Use start-dashboard-v2.ps1 / stop-dashboard-v2.ps1 for port 4320.',
  },
  {
    id: 'core',
    label: 'Core Supervisor',
    description: 'One supervisor owns 8766–8769; lifecycle actions are group-wide by design.',
    mode: 'direct',
    members: [
      { port: 8766, label: 'Binance realtime', healthPath: '/health' },
      { port: 8767, label: 'Cross Oracle', healthPath: '/health' },
      { port: 8768, label: 'Strategies', healthPath: '/state' },
      { port: 8769, label: 'BTC live engine', healthPath: '/health' },
    ],
    rootProcessToken: 'predict_bot.supervisor',
    command: 'python',
    args: ['-m', 'predict_bot.supervisor'],
    legacyPidFiles: [{ file: '.api-v2.pid', expectedProcessToken: 'predict_bot.supervisor' }],
  },
  {
    id: 'multi',
    label: 'Multi-Asset Supervisor',
    description: 'Observer + ETH/BNB engines share one supervisor. 8774/8775 are optional clone children.',
    mode: 'direct',
    members: [
      { port: 8770, label: 'Multi-market observer', healthPath: '/health' },
      { port: 8772, label: 'ETH live', healthPath: '/health' },
      { port: 8773, label: 'BNB live', healthPath: '/health' },
      { port: 8774, label: 'ETH clone', healthPath: '/health', required: false },
      { port: 8775, label: 'BNB clone', healthPath: '/health', required: false },
    ],
    rootProcessToken: 'predict_bot.multi_asset_live_supervisor',
    command: 'python',
    args: ['-m', 'predict_bot.multi_asset_live_supervisor'],
    legacyPidFiles: [{ file: '.multi-live.pid', expectedProcessToken: 'predict_bot.multi_asset_live_supervisor' }],
  },
  {
    id: 'predict',
    label: 'Predict.fun Observer',
    description: 'Shared read-only Predict.fun market feed used by target-wallet research services.',
    mode: 'direct',
    members: [
      {
        port: 8771,
        label: 'Predict.fun observer',
        healthPath: '/state',
        expectedProcessToken: 'predict_bot.predict_fun_observer',
      },
    ],
    rootProcessToken: 'predict_bot.predict_fun_observer',
    command: 'python',
    args: ['-m', 'predict_bot.predict_fun_observer'],
    legacyPidFiles: [
      { file: '.predict-fun-v2.pid', expectedProcessToken: 'predict_bot.predict_fun_observer' },
      { file: '.target-taker-echtgeld-predict.pid', expectedProcessToken: 'predict_bot.predict_fun_observer' },
      { file: '.maker-ebm-v1-predict.pid', expectedProcessToken: 'predict_bot.predict_fun_observer' },
    ],
  },
  {
    id: 'targetTaker',
    label: 'Target Wallet Official',
    description: '8776 TARGET_WALLET_OFFICIAL_V2 truth collector. Strategy-free read-only Target ground truth.',
    mode: 'script',
    members: [
      {
        port: 8776,
        label: 'Target Wallet Official',
        healthPath: '/health',
        expectedProcessToken: 'predict_wallet_shadow_observer_v4_23',
      },
    ],
    script: 'start-target-taker-echtgeld-producer-v1.ps1',
    legacyPidFiles: [
      { file: '.target-taker-echtgeld-producer.pid', expectedProcessToken: 'predict_wallet_shadow_observer_v4_23' },
    ],
    safetyNote: 'Read-only target-wallet truth collection. No strategy logic, TradeIntent handoff, or live orders.',
  },
  {
    id: 'makerInference',
    label: 'Maker Book Inference · BTC',
    description: '8778 consumable-quantity Maker lifecycle inference collector. Independent research service.',
    mode: 'direct',
    members: [
      {
        port: 8778,
        label: 'Maker book inference BTC',
        healthPath: '/health',
        expectedProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_v2_1',
      },
    ],
    rootProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_v2_1',
    command: 'python',
    args: ['-m', 'predict_bot.predict_wallet_maker_book_inference_collector_v2_1'],
    legacyPidFiles: [{ file: '.wallet-shadow-lab-maker-book.pid', expectedProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_v2_1' }],
  },
  {
    id: 'makerInferenceEth',
    label: 'Maker Book Inference · ETH 5M',
    description: '8779 forward-only ETH 5M full-book inference collector. Independent research service.',
    mode: 'direct',
    members: [
      {
        port: 8779,
        label: 'Maker book inference ETH 5M',
        healthPath: '/health',
        expectedProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_eth5m',
      },
    ],
    rootProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_eth5m',
    command: 'python',
    args: ['-m', 'predict_bot.predict_wallet_maker_book_inference_collector_eth5m'],
    legacyPidFiles: [{ file: '.wallet-shadow-lab-maker-book-eth5m.pid', expectedProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_eth5m' }],
  },
  {
    id: 'unifiedPublicSource',
    label: 'Unified Public Source',
    description: '8783 public-only feature source for 8784/8785. Reads public market/microstructure inputs only; no Target wallet or Echtgeld path.',
    mode: 'script',
    members: [
      {
        port: 8783,
        label: 'Unified public source',
        healthPath: '/health',
        expectedProcessToken: 'predict_bot.unified_controller_public_source_v1',
      },
    ],
    script: 'start-unified-controller-public-source-v1.ps1',
    legacyPidFiles: [{ file: '.unified-controller-public-source-v1.pid', expectedProcessToken: 'predict_bot.unified_controller_public_source_v1' }],
    safetyNote: 'Paper/public-data service only. No Target wallet reads, Echtgeld handoff, or live orders. Start before 8784 and 8785.',
  },
  {
    id: 'unifiedController',
    label: 'Promoted Own-State Paper',
    description: '8784 frozen promoted own-state paper controller. Depends on 8783 HTTP public source and reads 8778 public-book DB data only.',
    mode: 'script',
    members: [
      {
        port: 8784,
        label: 'Promoted Own-State Paper',
        healthPath: '/health',
        expectedProcessToken: 'predict_bot.unified_controller_paper_v1',
      },
    ],
    script: 'start-unified-controller-paper-v1.ps1',
    legacyPidFiles: [{ file: '.unified-controller-paper-v1.pid', expectedProcessToken: 'predict_bot.unified_controller_paper_v1' }],
    safetyNote: 'Frozen paper-only candidate. Requires 8783 public source plus fresh 8778 public-book data; remains fail-closed in WAITING_SOURCE when either dependency is unavailable.',
  },
  {
    id: 'unifiedFlash',
    label: 'Unified Flash Sandbox',
    description: '8785 paper-only Flash Sandbox for hourly controller experiments. Depends on 8783 public source and the frozen 8784 base.',
    mode: 'script',
    members: [
      {
        port: 8785,
        label: 'Unified Flash Sandbox',
        healthPath: '/health',
        expectedProcessToken: 'predict_bot.unified_controller_flash_sandbox_v1',
      },
    ],
    script: 'start-unified-flash-sandbox-v1.ps1',
    legacyPidFiles: [{ file: '.unified-flash-v1.pid', expectedProcessToken: 'predict_bot.unified_controller_flash_sandbox_v1' }],
    safetyNote: 'Paper-only sandbox. Requires 8783 public source and 8784 base; remains fail-closed in WAITING_SOURCE until both are ready.',
  },
  {
    id: 'makerInferenceBnb',
    label: 'Maker Book Inference · BNB 5M',
    description: '8788 forward-only BNB 5M compressed full-book / lifecycle / Execution Tape collector.',
    mode: 'direct',
    members: [{ port: 8788, label: 'Maker book inference BNB 5M', healthPath: '/health', expectedProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_bnb5m' }],
    rootProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_bnb5m',
    command: 'python',
    args: ['-m', 'predict_bot.predict_wallet_maker_book_inference_collector_bnb5m'],
    legacyPidFiles: [{ file: '.target-bnb5m-maker-book.pid', expectedProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_bnb5m' }],
  },
  {
    id: 'publicResearchBnb',
    label: 'Public Fair Value · BNB 5M',
    description: '8797 target-blind compact BNB public fair-value archive; 500ms snapshots, no raw websocket persistence.',
    mode: 'direct',
    members: [{ port: 8797, label: 'BNB public fair-value archive', healthPath: '/health', expectedProcessToken: 'predict_bot.public_research_archive_bnb5m_v1' }],
    rootProcessToken: 'predict_bot.public_research_archive_bnb5m_v1',
    command: 'python',
    args: ['-m', 'predict_bot.public_research_archive_bnb5m_v1'],
    legacyPidFiles: [{ file: '.target-bnb5m-public.pid', expectedProcessToken: 'predict_bot.public_research_archive_bnb5m_v1' }],
  },
  {
    id: 'echtgeld',
    label: 'Echtgeld Engine',
    description: 'Independent 8781 execution engine. A newly started engine must remain PAUSED/DISARMED.',
    mode: 'script',
    members: [
      {
        port: 8781,
        label: 'Echtgeld engine',
        healthPath: '/health',
        expectedProcessToken: 'predict_bot.echtgeld_engine_v2',
      },
    ],
    script: 'start-echtgeld-engine-v1.ps1',
    legacyPidFiles: [{ file: '.echtgeld-engine-v2.pid', expectedProcessToken: 'predict_bot.echtgeld_engine_v2' }],
    safetyNote: 'Start/restart never arms a new engine. Stop/restart is rejected while armed or while an order is unresolved.',
  },
]

const GROUPS: Record<string, { label: string; start: string[]; stop: string[] }> = {
  core: { label: 'Core', start: ['core'], stop: ['core'] },
  market: { label: 'Market Stack', start: ['core', 'multi', 'predict'], stop: ['predict', 'multi', 'core'] },
  research: {
    label: 'Target Wallet Research',
    start: ['predict', 'targetTaker', 'makerInference', 'makerInferenceEth'],
    stop: ['makerInferenceEth', 'makerInference', 'targetTaker', 'predict'],
  },
  unified: {
    label: 'Unified Paper Stack',
    start: ['predict', 'makerInference', 'unifiedPublicSource', 'unifiedController', 'unifiedFlash'],
    stop: ['unifiedFlash', 'unifiedController', 'unifiedPublicSource', 'makerInference', 'predict'],
  },
  all: {
    label: 'All Managed Services',
    start: ['core', 'multi', 'predict', 'targetTaker', 'makerInference', 'makerInferenceEth', 'echtgeld'],
    stop: ['echtgeld', 'makerInferenceEth', 'makerInference', 'targetTaker', 'predict', 'multi', 'core'],
  },
}

const ALL_PORTS = Array.from(new Set(SERVICES.flatMap((service) => service.members.map((member) => member.port))))
const ALL_PORT_SET = new Set(ALL_PORTS)

function json(res: ServerResponse, status: number, payload: unknown) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(JSON.stringify(payload))
}

function execFileText(file: string, args: string[], timeoutMs = 8_000): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { windowsHide: true, timeout: timeoutMs, maxBuffer: 4 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(String(stderr || error.message || error)))
        return
      }
      resolve(String(stdout || '').trim())
    })
  })
}

async function powershell(command: string, timeoutMs = 8_000): Promise<string> {
  if (process.platform !== 'win32') throw new Error('Service Control currently supports Windows hosts only.')
  return execFileText('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command], timeoutMs)
}

function parseWmicList(raw: string): ProcessInfo | null {
  const values: Record<string, string> = {}
  for (const line of raw.split(/\r?\n/)) {
    const index = line.indexOf('=')
    if (index <= 0) continue
    values[line.slice(0, index).trim()] = line.slice(index + 1).trim()
  }
  const pid = Number(values.ProcessId)
  if (!Number.isInteger(pid) || pid <= 0) return null
  const parent = Number(values.ParentProcessId)
  return {
    pid,
    parentPid: Number.isInteger(parent) && parent >= 0 ? parent : null,
    commandLine: String(values.CommandLine || ''),
  }
}

async function getProcessInfo(pid: number): Promise<ProcessInfo | null> {
  if (!Number.isInteger(pid) || pid <= 0 || process.platform !== 'win32') return null
  const script = `$p=Get-CimInstance Win32_Process -Filter \"ProcessId=${pid}\" -ErrorAction SilentlyContinue; if($p){$p | Select-Object @{n='pid';e={[int]$_.ProcessId}},@{n='parentPid';e={[int]$_.ParentProcessId}},@{n='commandLine';e={[string]$_.CommandLine}} | ConvertTo-Json -Compress}`
  try {
    const raw = await powershell(script, 5_000)
    if (!raw) return null
    if (raw) {
      const row = JSON.parse(raw) as Record<string, unknown>
      const parsedPid = Number(row.pid)
      if (Number.isInteger(parsedPid) && parsedPid > 0) {
        return {
          pid: parsedPid,
          parentPid: row.parentPid === null || row.parentPid === undefined ? null : Number(row.parentPid),
          commandLine: String(row.commandLine || ''),
        }
      }
    }
  } catch {
    // Fall through to WMIC on Windows images where CIM/WMI cmdlets are unavailable or restricted.
  }
  try {
    const raw = await execFileText('wmic.exe', ['process', 'where', `ProcessId=${pid}`, 'get', 'ProcessId,ParentProcessId,CommandLine', '/format:list'], 5_000)
    return parseWmicList(raw)
  } catch {
    return null
  }
}

async function getProcessTableSnapshot(pids: number[] = []): Promise<Map<number, ProcessInfo> | null> {
  if (process.platform !== 'win32') return new Map()
  const ids = Array.from(new Set(pids.filter((pid) => Number.isInteger(pid) && pid > 0)))
  if (!ids.length) return new Map()
  const filter = ids.map((pid) => `ProcessId=${pid}`).join(' OR ')
  const script = [
    `$rows=Get-CimInstance Win32_Process -Filter "${filter}" -ErrorAction SilentlyContinue | Select-Object`,
    "  @{n='pid';e={[int]$_.ProcessId}},@{n='parentPid';e={[int]$_.ParentProcessId}},@{n='commandLine';e={[string]$_.CommandLine}}",
    '$rows | ConvertTo-Json -Compress',
  ].join(' ')
  try {
    const raw = await powershell(script, 8_000)
    const map = new Map<number, ProcessInfo>()
    if (!raw) return map
    for (const row of normalizeJsonRows(JSON.parse(raw))) {
      const pid = Number(row.pid)
      if (!Number.isInteger(pid) || pid <= 0) continue
      map.set(pid, {
        pid,
        parentPid: row.parentPid === null || row.parentPid === undefined ? null : Number(row.parentPid),
        commandLine: String(row.commandLine || ''),
      })
    }
    return map
  } catch {
    return null
  }
}

async function getListenerSnapshot(processTable: Map<number, ProcessInfo> | null = null): Promise<Map<number, ListenerInfo>> {
  const map = new Map<number, ListenerInfo>()
  if (process.platform !== 'win32') return map

  let raw = ''
  try {
    raw = await execFileText('netstat.exe', ['-ano', '-p', 'tcp'], 8_000)
  } catch {
    return map
  }

  const pidByPort = new Map<number, number>()
  for (const line of raw.split(/\r?\n/)) {
    const match = line.trim().match(/^TCP\s+(\S+)\s+(\S+)\s+LISTENING\s+(\d+)$/i)
    if (!match) continue
    const local = match[1]
    const portMatch = local.match(/:(\d+)$/)
    if (!portMatch) continue
    const port = Number(portMatch[1])
    const pid = Number(match[3])
    if (!ALL_PORT_SET.has(port) || !Number.isInteger(pid) || pid <= 0) continue
    pidByPort.set(port, pid)
  }

  const processCache = new Map<number, ProcessInfo | null>()
  await Promise.all(Array.from(new Set(pidByPort.values())).map(async (pid) => {
    processCache.set(pid, processTable?.get(pid) ?? await getProcessInfo(pid))
  }))

  for (const [port, pid] of pidByPort) {
    const info = processCache.get(pid) || null
    map.set(port, {
      port,
      pid,
      parentPid: info?.parentPid ?? null,
      commandLine: info?.commandLine ?? '',
    })
  }
  return map
}

async function getListenerPidSnapshot(): Promise<Map<number, number>> {
  const result = new Map<number, number>()
  if (process.platform !== 'win32') return result
  try {
    const raw = await execFileText('netstat.exe', ['-ano', '-p', 'tcp'], 8_000)
    for (const line of raw.split(/\r?\n/)) {
      const match = line.trim().match(/^TCP\s+(\S+)\s+(\S+)\s+LISTENING\s+(\d+)$/i)
      if (!match) continue
      const portMatch = match[1].match(/:(\d+)$/)
      if (!portMatch) continue
      const port = Number(portMatch[1])
      const pid = Number(match[3])
      if (ALL_PORT_SET.has(port) && Number.isInteger(pid) && pid > 0) result.set(port, pid)
    }
  } catch {
    return result
  }
  return result
}

function commandMatches(commandLine: string, token: string | undefined) {
  return !token || commandLine.toLowerCase().includes(token.toLowerCase())
}

function commandRecognition(commandLine: string, token: string | undefined): boolean | null {
  if (!token) return null
  if (!commandLine.trim()) return null
  return commandMatches(commandLine, token)
}

async function probe(member: ServiceMember): Promise<HealthProbe> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), member.port === 8766 || member.port === 8767 ? 1_200 : 1_800)
  try {
    const response = await fetch(`http://127.0.0.1:${member.port}${member.healthPath}`, {
      signal: controller.signal,
      headers: member.port === 8781
        ? { Accept: 'application/json', Connection: 'close' }
        : { Accept: 'application/json' },
      cache: 'no-store',
    })
    let payload: unknown = null
    const contentType = String(response.headers.get('content-type') || '')
    if (contentType.includes('json')) {
      const raw = await response.text().catch(() => '')
      if (raw) {
        try {
          payload = JSON.parse(raw)
        } catch {
          // Some Python research services serialize non-finite floats as bare
          // NaN/Infinity. They are valid diagnostics values but not strict JSON.
          // Normalize only unquoted value tokens for the read-only health view.
          const normalized = raw.replace(/([:\[,]\s*)(?:NaN|Infinity|-Infinity)(?=\s*[,}\]])/g, '$1null')
          payload = JSON.parse(normalized)
        }
      }
    }
    return { healthy: response.ok, status: response.status, payload }
  } catch {
    return { healthy: false, status: null, payload: null }
  } finally {
    clearTimeout(timer)
  }
}

async function readRegistry(path: string): Promise<Registry> {
  try {
    const parsed = JSON.parse(await fs.readFile(path, 'utf8')) as Registry
    if (parsed?.version === 1 && parsed.services && typeof parsed.services === 'object') return parsed
  } catch {
    // Fresh installs legitimately have no registry yet.
  }
  return { version: 1, services: {} }
}

async function writeRegistry(path: string, registry: Registry) {
  await fs.mkdir(dirname(path), { recursive: true })
  const tmp = `${path}.tmp`
  await fs.writeFile(tmp, `${JSON.stringify(registry, null, 2)}\n`, 'utf8')
  await fs.rename(tmp, path)
}

async function readPidFile(root: string, file: string): Promise<number | null> {
  try {
    const raw = (await fs.readFile(join(root, file), 'utf8')).trim()
    const pid = Number(raw)
    return Number.isInteger(pid) && pid > 0 ? pid : null
  } catch {
    return null
  }
}

async function validLegacyPids(root: string, def: ServiceDefinition, processTable: Map<number, ProcessInfo> | null = null): Promise<number[]> {
  const result: number[] = []
  for (const item of def.legacyPidFiles || []) {
    const pid = await readPidFile(root, item.file)
    if (!pid) continue
    const info = processTable?.get(pid) ?? await getProcessInfo(pid)
    if (info && commandMatches(info.commandLine, item.expectedProcessToken)) result.push(pid)
  }
  return Array.from(new Set(result))
}

async function validRegistryScriptPids(def: ServiceDefinition, entry: RegistryEntry, processTable: Map<number, ProcessInfo> | null = null): Promise<number[]> {
  const candidates = Array.from(new Set(entry.listenerPids || []))
  if (!candidates.length) return []
  const tokens = def.members.map((member) => member.expectedProcessToken).filter((value): value is string => Boolean(value))
  const valid: number[] = []
  for (const pid of candidates) {
    const info = processTable?.get(pid) ?? await getProcessInfo(pid)
    if (!info) continue
    if (!tokens.length || tokens.some((token) => commandMatches(info.commandLine, token))) valid.push(pid)
  }
  return valid
}

async function resolveOwnership(
  root: string,
  def: ServiceDefinition,
  registry: Registry,
  listeners: Map<number, ListenerInfo>,
  processTable: Map<number, ProcessInfo> | null = null,
): Promise<OwnershipResolution> {
  if (def.mode === 'self') return { ownership: 'SELF', rootPid: process.pid, ownedPids: [process.pid], startedAt: null }

  const onlineListeners = def.members.map((member) => listeners.get(member.port)).filter((item): item is ListenerInfo => Boolean(item))
  const entry = registry.services[def.id]

  // Registry/PID ownership is evaluated before listener availability. The old
  // manager returned NONE here when Get-NetTCPConnection yielded no rows, which
  // disabled every Stop/Restart button even when valid managed PID files existed.
  if (entry) {
    if (def.mode === 'direct' && entry.rootPid) {
      const info = processTable?.get(entry.rootPid) ?? await getProcessInfo(entry.rootPid)
      if (info && commandMatches(info.commandLine, def.rootProcessToken)) {
        return { ownership: 'MANAGED', rootPid: entry.rootPid, ownedPids: [entry.rootPid], startedAt: entry.startedAt }
      }
    }
    if (def.mode === 'script') {
      const valid = await validRegistryScriptPids(def, entry, processTable)
      if (valid.length && valid.length === new Set(entry.listenerPids || []).size) {
        return { ownership: 'MANAGED', rootPid: null, ownedPids: valid, startedAt: entry.startedAt }
      }
    }
  }

  const legacy = await validLegacyPids(root, def, processTable)
  if (legacy.length) {
    if (def.mode === 'direct') {
      return { ownership: 'LEGACY_MANAGED', rootPid: legacy[0], ownedPids: legacy, startedAt: null }
    }
    const requiredLegacyFiles = (def.legacyPidFiles || []).length
    if (legacy.length >= Math.max(1, requiredLegacyFiles)) {
      return { ownership: 'LEGACY_MANAGED', rootPid: null, ownedPids: legacy, startedAt: null }
    }
  }

  if (!onlineListeners.length) return { ownership: 'NONE', rootPid: null, ownedPids: [], startedAt: null }
  return { ownership: 'EXTERNAL', rootPid: null, ownedPids: [], startedAt: null }
}

function unwrapHealthPayload(payload: unknown): Record<string, unknown> {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return {}
  const row = payload as Record<string, unknown>
  if (row.state && typeof row.state === 'object' && !Array.isArray(row.state)) return row.state as Record<string, unknown>
  return row
}

async function getServiceSnapshot(root: string, def: ServiceDefinition, registry: Registry, listeners: Map<number, ListenerInfo>, processTable: Map<number, ProcessInfo> | null = null) {
  const memberRows = await Promise.all(def.members.map(async (member) => {
    const listener = listeners.get(member.port)
    const health = def.mode === 'self' ? { healthy: true, status: 200, payload: null } : await probe(member)
    return {
      port: member.port,
      label: member.label,
      required: member.required !== false,
      healthy: health.healthy,
      httpStatus: health.status,
      pid: def.mode === 'self' ? process.pid : listener?.pid ?? null,
      commandRecognized: def.mode === 'self' ? true : listener ? commandRecognition(listener.commandLine, member.expectedProcessToken) : null,
      payload: health.payload,
    }
  }))

  const required = memberRows.filter((member) => member.required)
  const conflicts = required.filter((member) => member.pid && member.commandRecognized === false)
  const healthyCount = required.filter((member) => member.healthy).length
  const registryEntry = registry.services[def.id]
  let state: RuntimeState
  if (def.mode === 'self') state = 'ONLINE'
  else if (conflicts.length) state = 'CONFLICT'
  else if (required.length > 0 && healthyCount === required.length) state = 'ONLINE'
  else if (healthyCount === 0 && required.every((member) => member.pid === null)) state = registryEntry ? 'CRASHED' : 'OFFLINE'
  else state = 'PARTIAL'

  const ownership = await resolveOwnership(root, def, registry, listeners, processTable)
  let runtime: Record<string, unknown> | null = null
  if (def.id === 'echtgeld') {
    const health = unwrapHealthPayload(memberRows[0]?.payload)
    runtime = {
      armed: Boolean(health.armed),
      runtimeStatus: health.runtimeStatus ?? null,
      version: health.version ?? null,
    }
  }
  if (def.id === 'targetTaker') {
    const health = unwrapHealthPayload(memberRows[0]?.payload)
    runtime = {
      version: health.version ?? null,
      runtimeStatus: health.status ?? null,
    }
  }
  if (def.id === 'unifiedPublicSource') {
    const health = unwrapHealthPayload(memberRows[0]?.payload)
    runtime = {
      version: health.version ?? null,
      runtimeStatus: health.status ?? null,
      sourceReady: health.strategyInputReady === true && health.processHealthy !== false,
      sourceWaitReason: health.lastError ?? (health.strategyInputReady === true ? null : health.strategyStatus ?? null),
    }
  }
  if (def.id === 'unifiedController' || def.id === 'unifiedFlash') {
    const health = unwrapHealthPayload(memberRows[0]?.payload)
    runtime = {
      version: health.version ?? null,
      runtimeStatus: health.status ?? null,
      sourceReady: health.sourceReady === true,
      sourceWaitReason: health.sourceWaitReason ?? null,
    }
  }

  const observedPids = memberRows.map((member) => member.pid).filter((pid): pid is number => Boolean(pid))
  const pids = Array.from(new Set([...observedPids, ...ownership.ownedPids]))
  const managed = ownership.ownership === 'MANAGED' || ownership.ownership === 'LEGACY_MANAGED'

  return {
    id: def.id,
    label: def.label,
    description: def.description,
    mode: def.mode,
    state,
    ownership: ownership.ownership,
    rootPid: ownership.rootPid,
    pids,
    startedAt: ownership.startedAt,
    controllable: def.mode !== 'self',
    canStart: def.mode !== 'self' && (state === 'OFFLINE' || state === 'CRASHED'),
    canStop: def.mode !== 'self' && managed,
    canRestart: def.mode !== 'self' && managed,
    safetyNote: def.safetyNote ?? null,
    runtime,
    ports: memberRows.map(({ payload: _payload, ...member }) => member),
  }
}

async function allSnapshots(root: string, registryPath: string) {
  const [registry, listenerPids] = await Promise.all([readRegistry(registryPath), getListenerPidSnapshot()])
  const candidates = new Set<number>(listenerPids.values())
  for (const entry of Object.values(registry.services)) {
    if (entry.rootPid) candidates.add(entry.rootPid)
    for (const pid of entry.listenerPids || []) candidates.add(pid)
  }
  await Promise.all(SERVICES.flatMap((def) => (def.legacyPidFiles || []).map(async (item) => {
    const pid = await readPidFile(root, item.file)
    if (pid) candidates.add(pid)
  })))
  const processTable = await getProcessTableSnapshot(Array.from(candidates))
  const listeners = new Map<number, ListenerInfo>()
  for (const [port, pid] of listenerPids) {
    const info = processTable?.get(pid) || null
    listeners.set(port, { port, pid, parentPid: info?.parentPid ?? null, commandLine: info?.commandLine ?? '' })
  }
  const services = await Promise.all(SERVICES.map((def) => getServiceSnapshot(root, def, registry, listeners, processTable)))
  return {
    ok: true,
    generatedAt: Date.now(),
    hostControl: process.platform === 'win32',
    dashboardRestartExternal: true,
    processInspection: {
      listenerDiscovery: 'netstat.exe -ano -p tcp',
      listenersFound: listeners.size,
      expectedPorts: ALL_PORTS.length,
    },
    groups: Object.entries(GROUPS).map(([id, group]) => ({ id, label: group.label })),
    services,
  }
}

function serviceById(id: string) {
  return SERVICES.find((service) => service.id === id) || null
}

function ensureLogs(root: string) {
  const data = join(root, 'data')
  mkdirSync(data, { recursive: true })
  return data
}

function spawnDetachedLogged(root: string, id: string, command: string, args: string[]) {
  const data = ensureLogs(root)
  const stdoutFd = openSync(join(data, `service-manager-${id}.stdout.log`), 'a')
  const stderrFd = openSync(join(data, `service-manager-${id}.stderr.log`), 'a')
  try {
    const child = spawn(command, args, {
      cwd: root,
      env: process.env,
      windowsHide: true,
      detached: true,
      stdio: ['ignore', stdoutFd, stderrFd],
    })
    child.unref()
    if (!child.pid) throw new Error(`Failed to obtain PID for ${id}`)
    return child.pid
  } finally {
    closeSync(stdoutFd)
    closeSync(stderrFd)
  }
}

function runScriptLogged(root: string, id: string, script: string): Promise<void> {
  const full = join(root, script)
  if (!existsSync(full)) return Promise.reject(new Error(`Launcher is missing: ${script}`))
  const data = ensureLogs(root)
  const stdoutFd = openSync(join(data, `service-manager-${id}.stdout.log`), 'a')
  const stderrFd = openSync(join(data, `service-manager-${id}.stderr.log`), 'a')
  return new Promise((resolve, reject) => {
    let closed = false
    const closeLogs = () => {
      if (closed) return
      closed = true
      closeSync(stdoutFd)
      closeSync(stderrFd)
    }
    const child = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', full, '-NoBrowser'], {
      cwd: root,
      env: process.env,
      windowsHide: true,
      stdio: ['ignore', stdoutFd, stderrFd],
    })
    child.on('error', (error) => {
      closeLogs()
      reject(error)
    })
    child.on('close', (code) => {
      closeLogs()
      if (code === 0) resolve()
      else reject(new Error(`${script} exited with code ${code}. Check data/service-manager-${id}.stderr.log`))
    })
  })
}

async function waitForService(root: string, registryPath: string, id: string, desired: 'ONLINE' | 'OFFLINE', timeoutMs: number) {
  const def = serviceById(id)
  if (!def) throw new Error(`Unknown service ${id}`)
  const deadline = Date.now() + timeoutMs
  let lastState = 'UNKNOWN'
  while (Date.now() < deadline) {
    const [registry, listeners] = await Promise.all([readRegistry(registryPath), getListenerSnapshot()])
    const snapshot = await getServiceSnapshot(root, def, registry, listeners)
    lastState = snapshot.state
    if (desired === 'ONLINE' && snapshot.state === 'ONLINE') return snapshot
    if (desired === 'OFFLINE' && snapshot.state === 'OFFLINE') return snapshot
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`${def.label} did not become ${desired}; last state=${lastState}`)
}

async function writeManagedEntry(registryPath: string, id: string, entry: RegistryEntry | null) {
  const registry = await readRegistry(registryPath)
  if (entry) registry.services[id] = entry
  else delete registry.services[id]
  await writeRegistry(registryPath, registry)
}

async function existingOwnedProcess(root: string, def: ServiceDefinition, registryPath: string): Promise<OwnershipResolution | null> {
  const registry = await readRegistry(registryPath)
  const ownership = await resolveOwnership(root, def, registry, new Map())
  return ownership.ownership === 'MANAGED' || ownership.ownership === 'LEGACY_MANAGED' ? ownership : null
}

async function startService(root: string, registryPath: string, id: string) {
  const def = serviceById(id)
  if (!def || def.mode === 'self') throw new Error(`Service ${id} cannot be started from Dashboard.`)
  const before = await allSnapshots(root, registryPath)
  const snapshot = before.services.find((service) => service.id === id)
  if (!snapshot) throw new Error(`Unknown service ${id}`)
  if (snapshot.state === 'ONLINE') return { ok: true, unchanged: true, service: snapshot }
  if (snapshot.state !== 'OFFLINE' && snapshot.state !== 'CRASHED') {
    throw new Error(`${def.label} is ${snapshot.state}; refusing to start over a partial/conflicting listener.`)
  }

  const alreadyOwned = await existingOwnedProcess(root, def, registryPath)
  if (alreadyOwned?.ownedPids.length) {
    try {
      const online = await waitForService(root, registryPath, id, 'ONLINE', 15_000)
      return { ok: true, unchanged: true, alreadyStarting: true, service: online }
    } catch {
      const stillAlive = await Promise.all(alreadyOwned.ownedPids.map((pid) => getProcessInfo(pid)))
      if (stillAlive.some(Boolean)) {
        throw new Error(`${def.label} already has an owned process starting (PID ${alreadyOwned.ownedPids.join(', ')}); refusing duplicate spawn.`)
      }
    }
  }

  const startedAt = Date.now()
  if (def.mode === 'direct') {
    const pid = spawnDetachedLogged(root, id, def.command!, def.args || [])
    await writeManagedEntry(registryPath, id, { rootPid: pid, startedAt, source: 'service-manager' })
  } else {
    await runScriptLogged(root, id, def.script!)
    const listeners = await getListenerSnapshot()
    const listenerPids = def.members
      .map((member) => listeners.get(member.port)?.pid)
      .filter((pid): pid is number => Boolean(pid))
    await writeManagedEntry(registryPath, id, { listenerPids, startedAt, source: 'service-manager' })
  }

  const online = await waitForService(root, registryPath, id, 'ONLINE', def.id === 'targetTaker' ? 60_000 : 50_000)
  if (id === 'echtgeld' && Boolean(online.runtime?.armed)) {
    throw new Error('New Echtgeld Engine unexpectedly became ARMED. Stop it manually and inspect the engine logs.')
  }
  return { ok: true, unchanged: false, service: online }
}

async function echtgeldUnsafeToStop() {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 2_500)
  try {
    const response = await fetch('http://127.0.0.1:8781/state', { signal: controller.signal, headers: { Accept: 'application/json', Connection: 'close' }, cache: 'no-store' })
    if (!response.ok) return `Cannot verify Echtgeld safety state (HTTP ${response.status}). Stop/restart is blocked.`
    const payload = unwrapHealthPayload(await response.json().catch(() => null))
    if (!Object.keys(payload).length) return 'Cannot verify Echtgeld safety state. Stop/restart is blocked.'
    if (Boolean(payload.armed)) return 'Echtgeld Engine is LIVE ARMED. Pause it from EBM Echtgeld before stopping/restarting the process.'
    const orders = Array.isArray(payload.recentOrders) ? payload.recentOrders : []
    const active = orders.some((value) => {
      if (!value || typeof value !== 'object') return false
      const order = value as Record<string, unknown>
      const status = String(order.status || '').toUpperCase()
      const result = String(order.resultStatus || '').toUpperCase()
      return ['ATTEMPTING', 'QUEUED', 'PROCESSING'].includes(status) || (status === 'SUBMITTED' && (!result || result === 'PENDING'))
    })
    if (active) return 'Echtgeld has an unresolved order/position. Resolve it before stopping/restarting the engine.'
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    return `Cannot verify Echtgeld safety state (${reason}). Stop/restart is blocked.`
  } finally {
    clearTimeout(timer)
  }
  return null
}

async function killTree(pid: number) {
  if (process.platform !== 'win32') throw new Error('Service Control currently supports Windows hosts only.')
  try {
    await execFileText('taskkill.exe', ['/PID', String(pid), '/T', '/F'], 10_000)
  } catch (error) {
    const stillAlive = await getProcessInfo(pid)
    if (stillAlive) throw error
  }
}

async function stopService(root: string, registryPath: string, id: string) {
  const def = serviceById(id)
  if (!def || def.mode === 'self') throw new Error(`Service ${id} cannot be stopped from Dashboard.`)

  const registry = await readRegistry(registryPath)
  const listeners = await getListenerSnapshot()
  const snapshot = await getServiceSnapshot(root, def, registry, listeners)
  if (snapshot.state === 'OFFLINE' || snapshot.state === 'CRASHED') {
    await writeManagedEntry(registryPath, id, null)
    return { ok: true, unchanged: true, service: { ...snapshot, state: 'OFFLINE' as RuntimeState } }
  }
  if (!['MANAGED', 'LEGACY_MANAGED'].includes(snapshot.ownership)) {
    throw new Error(`${def.label} is ${snapshot.ownership}. Dashboard will not kill an externally-owned or unverifiable process.`)
  }
  if (id === 'echtgeld') {
    const unsafe = await echtgeldUnsafeToStop()
    if (unsafe) throw new Error(unsafe)
  }

  const ownership = await resolveOwnership(root, def, registry, listeners)
  const killPids = def.mode === 'direct'
    ? ownership.rootPid ? [ownership.rootPid] : []
    : ownership.ownedPids
  if (!killPids.length) throw new Error(`No verified owned PID is available for ${def.label}.`)

  for (const pid of Array.from(new Set(killPids))) {
    const info = await getProcessInfo(pid)
    if (!info) throw new Error(`PID ${pid} disappeared before stop; refresh Service Control and retry.`)
    const tokens = def.mode === 'direct'
      ? [def.rootProcessToken].filter((value): value is string => Boolean(value))
      : def.members.map((member) => member.expectedProcessToken).filter((value): value is string => Boolean(value))
    if (tokens.length && !tokens.some((token) => commandMatches(info.commandLine, token))) {
      throw new Error(`PID ${pid} no longer matches ${def.label}; refusing to terminate it.`)
    }
  }

  for (const pid of Array.from(new Set(killPids))) await killTree(pid)
  for (const legacy of def.legacyPidFiles || []) {
    await fs.unlink(join(root, legacy.file)).catch(() => undefined)
  }
  await writeManagedEntry(registryPath, id, null)
  const offline = await waitForService(root, registryPath, id, 'OFFLINE', 20_000)
  return { ok: true, unchanged: false, service: offline }
}

async function restartService(root: string, registryPath: string, id: string) {
  await stopService(root, registryPath, id)
  return startService(root, registryPath, id)
}

async function preflightGroup(root: string, registryPath: string, ids: string[], action: 'stop' | 'restart') {
  const state = await allSnapshots(root, registryPath)
  const echtgeld = state.services.find((service) => service.id === 'echtgeld')
  if (
    ids.includes('echtgeld') &&
    echtgeld &&
    !['OFFLINE', 'CRASHED'].includes(echtgeld.state) &&
    ['MANAGED', 'LEGACY_MANAGED'].includes(echtgeld.ownership)
  ) {
    const unsafe = await echtgeldUnsafeToStop()
    if (unsafe) throw new Error(unsafe)
  }
  if (action === 'restart') {
    for (const id of ids) {
      const snapshot = state.services.find((service) => service.id === id)
      if (!snapshot || snapshot.state === 'OFFLINE' || snapshot.state === 'CRASHED') continue
      if (!['MANAGED', 'LEGACY_MANAGED'].includes(snapshot.ownership)) {
        throw new Error(`${snapshot.label} is ${snapshot.ownership}; group restart aborted before changing anything.`)
      }
    }
  }
  return state
}

async function runGroup(root: string, registryPath: string, groupId: string, action: ServiceAction) {
  const group = GROUPS[groupId]
  if (!group) throw new Error(`Unknown service group ${groupId}`)
  const results: unknown[] = []
  if (action === 'stop' || action === 'restart') {
    const before = await preflightGroup(root, registryPath, group.stop, action)
    for (const id of group.stop) {
      const snapshot = before.services.find((service) => service.id === id)
      if (!snapshot || snapshot.state === 'OFFLINE' || snapshot.state === 'CRASHED') {
        results.push(await stopService(root, registryPath, id))
        continue
      }
      if (action === 'stop' && !['MANAGED', 'LEGACY_MANAGED'].includes(snapshot.ownership)) {
        results.push({ ok: true, skipped: true, service: id, reason: `${snapshot.ownership} process left untouched` })
        continue
      }
      results.push(await stopService(root, registryPath, id))
    }
  }
  if (action === 'start' || action === 'restart') {
    for (const id of group.start) results.push(await startService(root, registryPath, id))
  }
  return { ok: true, group: groupId, action, results, state: await allSnapshots(root, registryPath) }
}

function errorStatus(message: string) {
  if (message.includes('EXTERNAL') || message.includes('externally-owned') || message.includes('unverifiable') || message.includes('partial/conflicting') || message.includes('CONFLICT')) return 409
  if (message.includes('ARMED') || message.includes('unresolved') || message.includes('Cannot verify Echtgeld')) return 409
  if (message.includes('already has an owned process starting') || message.includes('refusing duplicate spawn')) return 409
  if (message.includes('Unknown')) return 404
  if (message.includes('Windows hosts only')) return 501
  return 500
}

export function serviceManagerV2Plugin(repoRoot: string): Plugin {
  const registryPath = join(repoRoot, 'data', 'dashboard-v2-service-registry.json')
  const busy = new Set<string>()
  return {
    name: 'btc5m-dashboard-service-manager-v2',
    configureServer(server) {
      server.middlewares.use(async (req: IncomingMessage, res: ServerResponse, next) => {
        const url = new URL(String(req.url || '/'), 'http://127.0.0.1')
        if (url.pathname === '/control/services' && req.method === 'GET') {
          try {
            json(res, 200, await allSnapshots(repoRoot, registryPath))
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error)
            json(res, errorStatus(message), { ok: false, error: message })
          }
          return
        }

        const serviceMatch = url.pathname.match(/^\/control\/services\/([A-Za-z0-9_-]+)\/(start|stop|restart)$/)
        const groupMatch = url.pathname.match(/^\/control\/service-groups\/([A-Za-z0-9_-]+)\/(start|stop|restart)$/)
        if (!serviceMatch && !groupMatch) {
          next()
          return
        }
        if (req.method !== 'POST') {
          json(res, 405, { ok: false, error: 'POST required' })
          return
        }

        const key = serviceMatch ? `service:${serviceMatch[1]}` : `group:${groupMatch![1]}`
        const groupBusy = Array.from(busy).some((value) => value.startsWith('group:'))
        if ((serviceMatch && (busy.has(key) || groupBusy)) || (groupMatch && busy.size > 0)) {
          json(res, 409, { ok: false, error: `${key} has a conflicting lifecycle action in progress` })
          return
        }

        busy.add(key)
        try {
          const action = (serviceMatch?.[2] || groupMatch?.[2]) as ServiceAction
          const result = serviceMatch
            ? action === 'start'
              ? await startService(repoRoot, registryPath, serviceMatch[1])
              : action === 'stop'
                ? await stopService(repoRoot, registryPath, serviceMatch[1])
                : await restartService(repoRoot, registryPath, serviceMatch[1])
            : await runGroup(repoRoot, registryPath, groupMatch![1], action)
          json(res, 200, result)
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          json(res, errorStatus(message), { ok: false, error: message })
        } finally {
          busy.delete(key)
        }
      })
    },
  }
}
