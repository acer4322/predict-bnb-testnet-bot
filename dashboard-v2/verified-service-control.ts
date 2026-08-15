import { execFile } from 'node:child_process'
import { promises as fs } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { join } from 'node:path'
import type { Plugin } from 'vite'

type ProcessInfo = {
  pid: number
  parentPid: number | null
  commandLine: string
}

type MemberDefinition = {
  port: number
  tokens: string[]
}

type ServiceDefinition = {
  id: string
  label: string
  members: MemberDefinition[]
  rootTokens: string[]
  pidFiles: string[]
}

type StopPlan = {
  rootPids: number[]
  listenerPids: number[]
  ports: Array<{ port: number; pid: number; commandLine: string }>
}

const SERVICES: Record<string, ServiceDefinition> = {
  core: {
    id: 'core',
    label: 'Core Supervisor',
    rootTokens: ['predict_bot.supervisor'],
    pidFiles: ['.api-v2.pid'],
    members: [
      { port: 8766, tokens: ['predict_bot.server_binance_prefetch_v6'] },
      { port: 8767, tokens: ['predict_bot.cross_oracle_storage_retention_v3'] },
      { port: 8768, tokens: ['predict_bot.cross_oracle_strategy_dedicated'] },
      { port: 8769, tokens: ['predict_bot.poly_gap_live_v45'] },
    ],
  },
  multi: {
    id: 'multi',
    label: 'Multi-Asset Supervisor',
    rootTokens: ['predict_bot.multi_asset_live_supervisor'],
    pidFiles: ['.multi-live.pid'],
    members: [
      { port: 8770, tokens: ['predict_bot.multi_prediction_observer_v2'] },
      { port: 8772, tokens: ['predict_bot.poly_gap_multi_asset_live_v3'] },
      { port: 8773, tokens: ['predict_bot.poly_gap_multi_asset_live_v3'] },
      { port: 8774, tokens: ['predict_bot.wallet_maker_clone_live_v8_4', 'predict_bot.wallet_maker_clone_predict_direct_v8_4'] },
      { port: 8775, tokens: ['predict_bot.wallet_maker_clone_live_v8_4', 'predict_bot.wallet_maker_clone_predict_direct_v8_4'] },
    ],
  },
  predict: {
    id: 'predict',
    label: 'Predict.fun Observer',
    rootTokens: ['predict_bot.predict_fun_observer'],
    pidFiles: ['.predict-fun-v2.pid', '.target-taker-echtgeld-predict.pid', '.maker-ebm-v1-predict.pid'],
    members: [{ port: 8771, tokens: ['predict_bot.predict_fun_observer'] }],
  },
  targetTaker: {
    id: 'targetTaker',
    label: 'Target Wallet Official',
    rootTokens: [],
    pidFiles: ['.target-taker-echtgeld-producer.pid', '.target-taker-echtgeld-signal.pid'],
    members: [
      {
        port: 8776,
        tokens: [
          'predict_bot.predict_wallet_shadow_observer_v4_23',
          'predict_bot.target_wallet_official_v1',
          // Accepted only so the new control plane can remove the retired process
          // that old Multi-Asset Supervisor builds may still have launched.
          'predict_bot.predict_wallet_shadow_observer_v4_3',
        ],
      },
      { port: 8777, tokens: ['predict_bot.predict_wallet_taker_signal_collector'] },
    ],
  },
  makerInference: {
    id: 'makerInference',
    label: 'Maker Book Inference · BTC',
    rootTokens: ['predict_bot.predict_wallet_maker_book_inference_collector_v2_1'],
    pidFiles: ['.wallet-shadow-lab-maker-book.pid'],
    members: [{ port: 8778, tokens: ['predict_bot.predict_wallet_maker_book_inference_collector_v2_1'] }],
  },
  makerInferenceEth: {
    id: 'makerInferenceEth',
    label: 'Maker Book Inference · ETH 5M',
    rootTokens: ['predict_bot.predict_wallet_maker_book_inference_collector_eth5m'],
    pidFiles: ['.wallet-shadow-lab-maker-book-eth5m.pid'],
    members: [{ port: 8779, tokens: ['predict_bot.predict_wallet_maker_book_inference_collector_eth5m'] }],
  },
  echtgeld: {
    id: 'echtgeld',
    label: 'Echtgeld Engine',
    rootTokens: ['predict_bot.echtgeld_engine_v2'],
    pidFiles: ['.echtgeld-engine-v2.pid'],
    members: [{ port: 8781, tokens: ['predict_bot.echtgeld_engine_v2'] }],
  },
}

const GROUPS: Record<string, { stop: string[]; start: string[] }> = {
  core: { stop: ['core'], start: ['core'] },
  market: { stop: ['predict', 'multi', 'core'], start: ['core', 'multi', 'predict'] },
  research: {
    stop: ['makerInferenceEth', 'makerInference', 'targetTaker', 'predict'],
    start: ['predict', 'targetTaker', 'makerInference', 'makerInferenceEth'],
  },
  all: {
    stop: ['echtgeld', 'makerInferenceEth', 'makerInference', 'targetTaker', 'predict', 'multi', 'core'],
    start: ['core', 'multi', 'predict', 'targetTaker', 'makerInference', 'makerInferenceEth', 'echtgeld'],
  },
}

function json(res: ServerResponse, status: number, payload: unknown) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(JSON.stringify(payload))
}

function execText(file: string, args: string[], timeoutMs = 8_000): Promise<string> {
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

async function powershell(command: string, timeoutMs = 5_000) {
  return execText('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command], timeoutMs)
}

function parseWmic(raw: string): ProcessInfo | null {
  const values: Record<string, string> = {}
  for (const line of raw.split(/\r?\n/)) {
    const at = line.indexOf('=')
    if (at <= 0) continue
    values[line.slice(0, at).trim()] = line.slice(at + 1).trim()
  }
  const pid = Number(values.ProcessId)
  if (!Number.isInteger(pid) || pid <= 0) return null
  const parentPid = Number(values.ParentProcessId)
  return {
    pid,
    parentPid: Number.isInteger(parentPid) && parentPid >= 0 ? parentPid : null,
    commandLine: String(values.CommandLine || ''),
  }
}

async function processInfo(pid: number): Promise<ProcessInfo | null> {
  if (!Number.isInteger(pid) || pid <= 0 || process.platform !== 'win32') return null
  try {
    const script = `$p=Get-CimInstance Win32_Process -Filter \"ProcessId=${pid}\" -ErrorAction SilentlyContinue; if($p){$p | Select-Object @{n='pid';e={[int]$_.ProcessId}},@{n='parentPid';e={[int]$_.ParentProcessId}},@{n='commandLine';e={[string]$_.CommandLine}} | ConvertTo-Json -Compress}`
    const raw = await powershell(script)
    if (raw) {
      const row = JSON.parse(raw) as Record<string, unknown>
      const actual = Number(row.pid)
      if (Number.isInteger(actual) && actual > 0) {
        return {
          pid: actual,
          parentPid: row.parentPid === null || row.parentPid === undefined ? null : Number(row.parentPid),
          commandLine: String(row.commandLine || ''),
        }
      }
    }
  } catch {
    // Fall through to WMIC for older/restricted Windows environments.
  }
  try {
    return parseWmic(await execText('wmic.exe', ['process', 'where', `ProcessId=${pid}`, 'get', 'ProcessId,ParentProcessId,CommandLine', '/format:list'], 5_000))
  } catch {
    return null
  }
}

async function listenerPids(): Promise<Map<number, number>> {
  const result = new Map<number, number>()
  if (process.platform !== 'win32') return result
  const raw = await execText('netstat.exe', ['-ano', '-p', 'tcp'], 8_000)
  for (const line of raw.split(/\r?\n/)) {
    const match = line.trim().match(/^TCP\s+(\S+)\s+\S+\s+LISTENING\s+(\d+)$/i)
    if (!match) continue
    const portMatch = match[1].match(/:(\d+)$/)
    if (!portMatch) continue
    const port = Number(portMatch[1])
    const pid = Number(match[2])
    if (Number.isInteger(port) && Number.isInteger(pid) && pid > 0) result.set(port, pid)
  }
  return result
}

function matchesToken(commandLine: string, tokens: string[]) {
  const lower = commandLine.toLowerCase()
  return tokens.some((token) => lower.includes(token.toLowerCase()))
}

async function readPid(path: string): Promise<number | null> {
  try {
    const pid = Number((await fs.readFile(path, 'utf8')).trim())
    return Number.isInteger(pid) && pid > 0 ? pid : null
  } catch {
    return null
  }
}

async function registryPids(root: string, id: string): Promise<number[]> {
  try {
    const raw = JSON.parse(await fs.readFile(join(root, 'data', 'dashboard-v2-service-registry.json'), 'utf8')) as Record<string, unknown>
    const services = raw.services && typeof raw.services === 'object' ? raw.services as Record<string, unknown> : {}
    const entry = services[id] && typeof services[id] === 'object' ? services[id] as Record<string, unknown> : null
    if (!entry) return []
    const candidates = [Number(entry.rootPid)]
    if (Array.isArray(entry.listenerPids)) candidates.push(...entry.listenerPids.map(Number))
    return Array.from(new Set(candidates.filter((pid) => Number.isInteger(pid) && pid > 0)))
  } catch {
    return []
  }
}

async function trustedPids(root: string, def: ServiceDefinition) {
  const candidates = new Set<number>(await registryPids(root, def.id))
  for (const file of def.pidFiles) {
    const pid = await readPid(join(root, file))
    if (pid) candidates.add(pid)
  }

  const roots = new Set<number>()
  const direct = new Set<number>()
  const memberTokens = def.members.flatMap((member) => member.tokens)
  for (const pid of candidates) {
    const info = await processInfo(pid)
    if (!info) continue
    if (matchesToken(info.commandLine, def.rootTokens)) roots.add(pid)
    if (matchesToken(info.commandLine, memberTokens)) direct.add(pid)
  }
  return { roots, direct }
}

async function descendsFrom(pid: number, roots: Set<number>) {
  if (!roots.size) return false
  let current = pid
  const seen = new Set<number>()
  for (let depth = 0; depth < 16; depth += 1) {
    if (roots.has(current)) return true
    if (seen.has(current)) return false
    seen.add(current)
    const info = await processInfo(current)
    const parent = info?.parentPid
    if (!parent || parent <= 0 || parent === current) return false
    current = parent
  }
  return false
}

async function echtgeldUnsafeToStop() {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 2_500)
  try {
    const response = await fetch('http://127.0.0.1:8781/state', {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
    if (!response.ok) return `Cannot verify Echtgeld safety state (HTTP ${response.status}). Stop/restart is blocked.`
    const raw = await response.json().catch(() => null)
    const payload = raw && typeof raw === 'object' && !Array.isArray(raw)
      ? ((raw as Record<string, unknown>).state && typeof (raw as Record<string, unknown>).state === 'object'
          ? (raw as Record<string, unknown>).state as Record<string, unknown>
          : raw as Record<string, unknown>)
      : null
    if (!payload) return 'Cannot verify Echtgeld safety state. Stop/restart is blocked.'
    if (Boolean(payload.armed)) return 'Echtgeld Engine is LIVE ARMED. Pause it before stopping/restarting.'
    const orders = Array.isArray(payload.recentOrders) ? payload.recentOrders : []
    const active = orders.some((value) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return false
      const order = value as Record<string, unknown>
      const status = String(order.status || '').toUpperCase()
      const result = String(order.resultStatus || '').toUpperCase()
      return ['ATTEMPTING', 'QUEUED', 'PROCESSING'].includes(status) || (status === 'SUBMITTED' && (!result || result === 'PENDING'))
    })
    return active ? 'Echtgeld has an unresolved order/position. Resolve it before stopping/restarting.' : null
  } catch (error) {
    return `Cannot verify Echtgeld safety state (${error instanceof Error ? error.message : String(error)}). Stop/restart is blocked.`
  } finally {
    clearTimeout(timer)
  }
}

async function buildStopPlan(root: string, def: ServiceDefinition): Promise<StopPlan> {
  if (process.platform !== 'win32') throw new Error('Verified Service Control currently supports Windows hosts only.')
  if (def.id === 'echtgeld') {
    const unsafe = await echtgeldUnsafeToStop()
    if (unsafe) throw new Error(unsafe)
  }

  const listeners = await listenerPids()
  const trusted = await trustedPids(root, def)
  const rootPids = Array.from(trusted.roots)
  const listenerTargets: Array<{ port: number; pid: number; commandLine: string }> = []

  for (const member of def.members) {
    const pid = listeners.get(member.port)
    if (!pid) continue
    const info = await processInfo(pid)
    if (!info) throw new Error(`Port ${member.port} is listening on PID ${pid}, but its process identity cannot be verified.`)
    const recognized = matchesToken(info.commandLine, member.tokens)
    const ownedDirectly = trusted.direct.has(pid)
    const underTrustedRoot = await descendsFrom(pid, trusted.roots)
    if (!recognized && !ownedDirectly && !underTrustedRoot) {
      throw new Error(`Port ${member.port} PID ${pid} is not a recognized ${def.label} process; refusing to terminate it. command=${info.commandLine}`)
    }
    listenerTargets.push({ port: member.port, pid, commandLine: info.commandLine })
  }

  return {
    rootPids,
    listenerPids: Array.from(new Set(listenerTargets.map((item) => item.pid))),
    ports: listenerTargets,
  }
}

async function killPid(pid: number) {
  try {
    await execText('taskkill.exe', ['/PID', String(pid), '/T', '/F'], 10_000)
  } catch (error) {
    if (await processInfo(pid)) throw error
  }
}

async function clearOwnershipFiles(root: string, def: ServiceDefinition) {
  await Promise.all(def.pidFiles.map((file) => fs.unlink(join(root, file)).catch(() => undefined)))
  const registryPath = join(root, 'data', 'dashboard-v2-service-registry.json')
  try {
    const registry = JSON.parse(await fs.readFile(registryPath, 'utf8')) as Record<string, unknown>
    const services = registry.services && typeof registry.services === 'object'
      ? registry.services as Record<string, unknown>
      : null
    if (!services || !(def.id in services)) return
    delete services[def.id]
    const tmp = `${registryPath}.verified-stop.tmp`
    await fs.writeFile(tmp, `${JSON.stringify(registry, null, 2)}\n`, 'utf8')
    await fs.rename(tmp, registryPath)
  } catch {
    // A missing or stale registry must not make a verified port stop fail.
  }
}

async function waitPortsClosed(def: ServiceDefinition, timeoutMs = 12_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const listeners = await listenerPids()
    if (!def.members.some((member) => listeners.has(member.port))) return []
    await new Promise((resolve) => setTimeout(resolve, 350))
  }

  const listeners = await listenerPids()
  const remaining: Array<{ port: number; pid: number; commandLine: string }> = []
  for (const member of def.members) {
    const pid = listeners.get(member.port)
    if (!pid) continue
    const info = await processInfo(pid)
    remaining.push({ port: member.port, pid, commandLine: info?.commandLine || '' })
  }
  return remaining
}

async function stopService(root: string, id: string) {
  const def = SERVICES[id]
  if (!def) throw new Error(`Unknown service ${id}`)
  const plan = await buildStopPlan(root, def)
  if (!plan.rootPids.length && !plan.listenerPids.length) {
    await clearOwnershipFiles(root, def)
    return { ok: true, unchanged: true, service: id, verifiedPortsClosed: true }
  }

  // Kill the supervisor/root first so it cannot immediately respawn a child,
  // then explicitly terminate every listener PID captured before the root died.
  for (const pid of plan.rootPids) await killPid(pid)
  for (const pid of plan.listenerPids) await killPid(pid)

  const remaining = await waitPortsClosed(def)
  if (remaining.length) {
    const detail = remaining.map((item) => `${item.port}->PID ${item.pid} ${item.commandLine}`).join(' | ')
    const hint = id === 'targetTaker' && remaining.some((item) => item.port === 8776)
      ? ' The old Multi-Asset Supervisor used to respawn 8776; restart Multi-Asset Supervisor once after pulling the new build.'
      : ''
    throw new Error(`${def.label} stop did not close every port: ${detail}.${hint}`)
  }

  await clearOwnershipFiles(root, def)
  return {
    ok: true,
    unchanged: false,
    service: id,
    killedRootPids: plan.rootPids,
    killedListenerPids: plan.listenerPids,
    verifiedPortsClosed: true,
  }
}

async function startThroughDashboard(id: string, token: string) {
  const response = await fetch(`http://127.0.0.1:4320/control/services/${encodeURIComponent(id)}/start`, {
    method: 'POST',
    cache: 'no-store',
    headers: { Accept: 'application/json', 'X-BTC-Lab-Control': token },
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const message = payload && typeof payload === 'object' && !Array.isArray(payload) && 'error' in payload
      ? String((payload as Record<string, unknown>).error)
      : `HTTP ${response.status}`
    throw new Error(`${id} stopped but failed to start again: ${message}`)
  }
  return payload
}

export function verifiedServiceControlPlugin(repoRoot: string): Plugin {
  const busy = new Set<string>()
  return {
    name: 'btc5m-verified-service-stop-control',
    configureServer(server) {
      server.middlewares.use(async (req: IncomingMessage, res: ServerResponse, next) => {
        const url = new URL(String(req.url || '/'), 'http://127.0.0.1')
        const serviceMatch = url.pathname.match(/^\/control\/services\/([A-Za-z0-9_-]+)\/(stop|restart)$/)
        if (!serviceMatch) {
          next()
          return
        }
        if (req.method !== 'POST') {
          json(res, 405, { ok: false, error: 'POST required' })
          return
        }

        const id = serviceMatch[1]
        const action = serviceMatch[2]
        if (!SERVICES[id]) {
          next()
          return
        }
        if (busy.has(id)) {
          json(res, 409, { ok: false, error: `${id} already has a verified lifecycle action in progress` })
          return
        }

        busy.add(id)
        try {
          const stopped = await stopService(repoRoot, id)
          if (action === 'stop') {
            json(res, 200, stopped)
            return
          }
          const token = String(req.headers['x-btc-lab-control'] || '')
          if (!token) throw new Error('Missing localhost control token for restart')
          const started = await startThroughDashboard(id, token)
          json(res, 200, { ok: true, action: 'restart', stopped, started })
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          const conflict = /refusing|unresolved|ARMED|Cannot verify|not a recognized|did not close/i.test(message)
          json(res, conflict ? 409 : 500, { ok: false, error: message })
        } finally {
          busy.delete(id)
        }
      })
    },
  }
}
