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
      { port: 8766, label: 'Binance realtime', healthPath: '/api/realtime' },
      { port: 8767, label: 'Cross Oracle', healthPath: '/state' },
      { port: 8768, label: 'Strategies', healthPath: '/state' },
      { port: 8769, label: 'BTC live engine', healthPath: '/state' },
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
      { port: 8770, label: 'Multi-market observer', healthPath: '/state' },
      { port: 8772, label: 'ETH live', healthPath: '/state' },
      { port: 8773, label: 'BNB live', healthPath: '/state' },
      { port: 8774, label: 'ETH clone', healthPath: '/state', required: false },
      { port: 8775, label: 'BNB clone', healthPath: '/state', required: false },
    ],
    rootProcessToken: 'predict_bot.multi_asset_live_supervisor',
    command: 'python',
    args: ['-m', 'predict_bot.multi_asset_live_supervisor'],
    legacyPidFiles: [{ file: '.multi-live.pid', expectedProcessToken: 'predict_bot.multi_asset_live_supervisor' }],
  },
  {
    id: 'predict',
    label: 'Predict.fun Observer',
    description: 'Shared read-only Predict.fun market feed used by EBM/research services.',
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
    label: 'EBM Target Taker Producer',
    description: '8776 v4.23 intent producer + 8777 public signal collector. Producer remains paper-only.',
    mode: 'script',
    members: [
      {
        port: 8776,
        label: 'EBM / Wallet Shadow',
        healthPath: '/health',
        expectedProcessToken: 'predict_wallet_shadow_observer_v4_23',
      },
      {
        port: 8777,
        label: 'Taker signal collector',
        healthPath: '/state',
        expectedProcessToken: 'predict_wallet_taker_signal_collector',
      },
    ],
    script: 'start-target-taker-echtgeld-producer-v1.ps1',
    legacyPidFiles: [
      { file: '.target-taker-echtgeld-producer.pid', expectedProcessToken: 'predict_wallet_shadow_observer_v4_23' },
      { file: '.target-taker-echtgeld-signal.pid', expectedProcessToken: 'predict_wallet_taker_signal_collector' },
    ],
    safetyNote: 'Target wallet events never trigger Echtgeld orders; this service only emits localhost TradeIntent events.',
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
        healthPath: '/state',
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
        healthPath: '/state',
        expectedProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_eth5m',
      },
    ],
    rootProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_eth5m',
    command: 'python',
    args: ['-m', 'predict_bot.predict_wallet_maker_book_inference_collector_eth5m'],
    legacyPidFiles: [{ file: '.wallet-shadow-lab-maker-book-eth5m.pid', expectedProcessToken: 'predict_bot.predict_wallet_maker_book_inference_collector_eth5m' }],
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
  research: { label: 'Maker Research', start: ['predict', 'makerInference', 'makerInferenceEth'], stop: ['makerInferenceEth', 'makerInference', 'predict'] },
  ebm: { label: 'EBM Echtgeld Stack', start: ['predict', 'targetTaker', 'echtgeld'], stop: ['echtgeld', 'targetTaker', 'predict'] },
  all: {
    label: 'All Managed Services',
    start: ['core', 'multi', 'predict', 'makerInference', 'makerInferenceEth', 'targetTaker', 'echtgeld'],
    stop: ['echtgeld', 'targetTaker', 'makerInferenceEth', 'makerInference', 'predict', 'multi', 'core'],
  },
}

const ALL_PORTS = Array.from(new Set(SERVICES.flatMap((service) => service.members.map((member) => member.port))))

function json(res: ServerResponse, status: number, payload: unknown) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(JSON.stringify(payload))
}

function execFileText(file: string, args: string[], timeoutMs = 8_000): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { windowsHide: true, timeout: timeoutMs, maxBuffer: 1024 * 1024 }, (error, stdout, stderr) => {
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

function normalizeJsonRows(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
  if (value && typeof value === 'object') return [value as Record<string, unknown>]
  return []
}

async function getListenerSnapshot(): Promise<Map<number, ListenerInfo>> {
  const map = new Map<number, ListenerInfo>()
  if (process.platform !== 'win32') return map
  const list = ALL_PORTS.join(',')
  const script = [
    `$ports=@(${list})`,
    '$rows=@()',
    'Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $ports -contains [int]$_.LocalPort } | ForEach-Object {',
    '  $p=Get-CimInstance Win32_Process -Filter ("ProcessId=" + $_.OwningProcess) -ErrorAction SilentlyContinue',
    '  $rows += [pscustomobject]@{port=[int]$_.LocalPort;pid=[int]$_.OwningProcess;parentPid=$(if($p){[int]$p.ParentProcessId}else{$null});commandLine=$(if($p){[string]$p.CommandLine}else{""})}',
    '}',
    '$rows | ConvertTo-Json -Compress',
  ].join('\n')
  try {
    const raw = await powershell(script, 10_000)
    if (!raw) return map
    for (const item of normalizeJsonRows(JSON.parse(raw))) {
      const port = Number(item.port)
      const pid = Number(item.pid)
      if (!Number.isFinite(port) || !Number.isFinite(pid)) continue
      map.set(port, {
        port,
        pid,
        parentPid: item.parentPid === null || item.parentPid === undefined ? null : Number(item.parentPid),
        commandLine: String(item.commandLine || ''),
      })
    }
  } catch {
    // Status will still fall back to HTTP health probes when process inspection fails.
  }
  return map
}

async function getProcessInfo(pid: number): Promise<ProcessInfo | null> {
  if (!Number.isInteger(pid) || pid <= 0 || process.platform !== 'win32') return null
  const script = `$p=Get-CimInstance Win32_Process -Filter \"ProcessId=${pid}\" -ErrorAction SilentlyContinue; if($p){$p | Select-Object @{n='pid';e={[int]$_.ProcessId}},@{n='parentPid';e={[int]$_.ParentProcessId}},@{n='commandLine';e={[string]$_.CommandLine}} | ConvertTo-Json -Compress}`
  try {
    const raw = await powershell(script, 5_000)
    if (!raw) return null
    const row = JSON.parse(raw) as Record<string, unknown>
    return {
      pid: Number(row.pid),
      parentPid: row.parentPid === null || row.parentPid === undefined ? null : Number(row.parentPid),
      commandLine: String(row.commandLine || ''),
    }
  } catch {
    return null
  }
}

function commandMatches(commandLine: string, token: string | undefined) {
  return !token || commandLine.toLowerCase().includes(token.toLowerCase())
}

async function probe(member: ServiceMember): Promise<HealthProbe> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 1_800)
  try {
    const response = await fetch(`http://127.0.0.1:${member.port}${member.healthPath}`, {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
    let payload: unknown = null
    const contentType = String(response.headers.get('content-type') || '')
    if (contentType.includes('json')) payload = await response.json().catch(() => null)
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

async function validLegacyPids(root: string, def: ServiceDefinition): Promise<number[]> {
  const result: number[] = []
  for (const item of def.legacyPidFiles || []) {
    const pid = await readPidFile(root, item.file)
    if (!pid) continue
    const info = await getProcessInfo(pid)
    if (info && commandMatches(info.commandLine, item.expectedProcessToken)) result.push(pid)
  }
  return Array.from(new Set(result))
}

async function resolveOwnership(
  root: string,
  def: ServiceDefinition,
  registry: Registry,
  listeners: Map<number, ListenerInfo>,
): Promise<{ ownership: Ownership; rootPid: number | null; ownedPids: number[]; startedAt: number | null }> {
  if (def.mode === 'self') return { ownership: 'SELF', rootPid: null, ownedPids: [], startedAt: null }

  const onlineListeners = def.members.map((member) => listeners.get(member.port)).filter((item): item is ListenerInfo => Boolean(item))
  if (!onlineListeners.length) return { ownership: 'NONE', rootPid: null, ownedPids: [], startedAt: null }

  const entry = registry.services[def.id]
  if (entry) {
    if (def.mode === 'direct' && entry.rootPid) {
      const info = await getProcessInfo(entry.rootPid)
      if (info && commandMatches(info.commandLine, def.rootProcessToken)) {
        return { ownership: 'MANAGED', rootPid: entry.rootPid, ownedPids: [entry.rootPid], startedAt: entry.startedAt }
      }
    }
    if (def.mode === 'script' && entry.listenerPids?.length) {
      const expected = new Set(entry.listenerPids)
      const listenersStillMatch = onlineListeners.every((listener) => {
        const member = def.members.find((candidate) => candidate.port === listener.port)
        return expected.has(listener.pid) && commandMatches(listener.commandLine, member?.expectedProcessToken)
      })
      if (listenersStillMatch) {
        return { ownership: 'MANAGED', rootPid: null, ownedPids: entry.listenerPids, startedAt: entry.startedAt }
      }
    }
  }

  const legacy = await validLegacyPids(root, def)
  if (legacy.length) {
    if (def.mode === 'direct') {
      return { ownership: 'LEGACY_MANAGED', rootPid: legacy[0], ownedPids: legacy, startedAt: null }
    }
    const expected = new Set(legacy)
    if (onlineListeners.every((listener) => expected.has(listener.pid))) {
      return { ownership: 'LEGACY_MANAGED', rootPid: null, ownedPids: legacy, startedAt: null }
    }
  }

  return { ownership: 'EXTERNAL', rootPid: null, ownedPids: [], startedAt: null }
}

function unwrapHealthPayload(payload: unknown): Record<string, unknown> {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return {}
  const row = payload as Record<string, unknown>
  if (row.state && typeof row.state === 'object' && !Array.isArray(row.state)) return row.state as Record<string, unknown>
  return row
}

async function getServiceSnapshot(root: string, def: ServiceDefinition, registry: Registry, listeners: Map<number, ListenerInfo>) {
  const memberRows = await Promise.all(def.members.map(async (member) => {
    const listener = listeners.get(member.port)
    const health = await probe(member)
    const commandRecognized = listener ? commandMatches(listener.commandLine, member.expectedProcessToken) : null
    return {
      port: member.port,
      label: member.label,
      required: member.required !== false,
      healthy: health.healthy,
      httpStatus: health.status,
      pid: listener?.pid ?? null,
      commandRecognized,
      payload: health.payload,
    }
  }))

  const required = memberRows.filter((member) => member.required)
  const conflicts = required.filter((member) => member.pid && member.commandRecognized === false)
  const healthyCount = required.filter((member) => member.healthy).length
  const registryEntry = registry.services[def.id]
  let state: RuntimeState
  if (conflicts.length) state = 'CONFLICT'
  else if (required.length > 0 && healthyCount === required.length) state = 'ONLINE'
  else if (healthyCount === 0 && required.every((member) => member.pid === null)) state = registryEntry ? 'CRASHED' : 'OFFLINE'
  else state = 'PARTIAL'

  const ownership = await resolveOwnership(root, def, registry, listeners)
  let runtime: Record<string, unknown> | null = null
  if (def.id === 'echtgeld') {
    const health = unwrapHealthPayload(memberRows[0]?.payload)
    runtime = {
      armed: Boolean(health.armed),
      runtimeStatus: health.runtimeStatus ?? null,
      version: health.version ?? null,
    }
  }

  return {
    id: def.id,
    label: def.label,
    description: def.description,
    mode: def.mode,
    state,
    ownership: ownership.ownership,
    rootPid: ownership.rootPid,
    pids: Array.from(new Set(memberRows.map((member) => member.pid).filter((pid): pid is number => Boolean(pid)))),
    startedAt: ownership.startedAt,
    controllable: def.mode !== 'self',
    canStart: def.mode !== 'self' && (state === 'OFFLINE' || state === 'CRASHED'),
    canStop: def.mode !== 'self' && (ownership.ownership === 'MANAGED' || ownership.ownership === 'LEGACY_MANAGED'),
    canRestart: def.mode !== 'self' && (ownership.ownership === 'MANAGED' || ownership.ownership === 'LEGACY_MANAGED'),
    safetyNote: def.safetyNote ?? null,
    runtime,
    ports: memberRows.map(({ payload: _payload, ...member }) => member),
  }
}

async function allSnapshots(root: string, registryPath: string) {
  const [registry, listeners] = await Promise.all([readRegistry(registryPath), getListenerSnapshot()])
  const services = await Promise.all(SERVICES.map((def) => getServiceSnapshot(root, def, registry, listeners)))
  return {
    ok: true,
    generatedAt: Date.now(),
    hostControl: process.platform === 'win32',
    dashboardRestartExternal: true,
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

  const online = await waitForService(root, registryPath, id, 'ONLINE', def.id === 'targetTaker' ? 75_000 : 50_000)
  if (id === 'echtgeld' && Boolean(online.runtime?.armed)) {
    throw new Error('New Echtgeld Engine unexpectedly became ARMED. Stop it manually and inspect the engine logs.')
  }
  return { ok: true, unchanged: false, service: online }
}

async function echtgeldUnsafeToStop() {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 2_500)
  try {
    const response = await fetch('http://127.0.0.1:8781/state', { signal: controller.signal, headers: { Accept: 'application/json' } })
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
    throw new Error(`${def.label} is ${snapshot.ownership}. Dashboard will not kill an externally-owned process.`)
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
  if (message.includes('EXTERNAL') || message.includes('externally-owned') || message.includes('partial/conflicting') || message.includes('CONFLICT')) return 409
  if (message.includes('ARMED') || message.includes('unresolved') || message.includes('Cannot verify Echtgeld')) return 409
  if (message.includes('Unknown')) return 404
  if (message.includes('Windows hosts only')) return 501
  return 500
}

export function serviceManagerPlugin(repoRoot: string): Plugin {
  const registryPath = join(repoRoot, 'data', 'dashboard-v2-service-registry.json')
  let busyKey: string | null = null
  return {
    name: 'btc5m-dashboard-service-manager',
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
        if (busyKey) {
          json(res, 409, { ok: false, error: `${busyKey} already has a lifecycle action in progress` })
          return
        }
        busyKey = key
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
          busyKey = null
        }
      })
    },
  }
}
