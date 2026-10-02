import { execFile } from 'node:child_process'
import type { IncomingMessage, ServerResponse } from 'node:http'
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

type SupervisorDefinition = {
  id: 'multi'
  label: string
  rootToken: string
  members: MemberDefinition[]
}

const MULTI: SupervisorDefinition = {
  id: 'multi',
  label: 'Multi-Asset Supervisor',
  rootToken: 'predict_bot.multi_asset_live_supervisor',
  members: [
    { port: 8770, tokens: ['predict_bot.multi_prediction_observer_v2'] },
    { port: 8772, tokens: ['predict_bot.poly_gap_multi_asset_live_v3'] },
    { port: 8773, tokens: ['predict_bot.poly_gap_multi_asset_live_v3'] },
    { port: 8774, tokens: ['predict_bot.wallet_maker_clone_live_v8_4', 'predict_bot.wallet_maker_clone_predict_direct_v8_4'] },
    { port: 8775, tokens: ['predict_bot.wallet_maker_clone_live_v8_4', 'predict_bot.wallet_maker_clone_predict_direct_v8_4'] },
  ],
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
    // Fall through to WMIC where available.
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

function matches(commandLine: string, tokens: string[]) {
  const lower = commandLine.toLowerCase()
  return tokens.some((token) => lower.includes(token.toLowerCase()))
}

async function findAncestor(pid: number, token: string): Promise<ProcessInfo | null> {
  let current = pid
  const seen = new Set<number>()
  for (let depth = 0; depth < 20; depth += 1) {
    if (seen.has(current)) return null
    seen.add(current)
    const info = await processInfo(current)
    if (!info) return null
    if (info.commandLine.toLowerCase().includes(token.toLowerCase())) return info
    const parent = info.parentPid
    if (!parent || parent <= 0 || parent === current) return null
    current = parent
  }
  return null
}

async function buildRecoveryPlan(def: SupervisorDefinition) {
  const listeners = await listenerPids()
  const roots = new Map<number, ProcessInfo>()
  const children: Array<{ port: number; pid: number; commandLine: string }> = []

  for (const member of def.members) {
    const pid = listeners.get(member.port)
    if (!pid) continue
    const info = await processInfo(pid)
    if (!info) throw new Error(`Port ${member.port} PID ${pid} identity could not be inspected.`)
    if (!matches(info.commandLine, member.tokens)) {
      throw new Error(`Port ${member.port} PID ${pid} is not a recognized ${def.label} child; refusing recovery. command=${info.commandLine}`)
    }
    children.push({ port: member.port, pid, commandLine: info.commandLine })
    const root = await findAncestor(pid, def.rootToken)
    if (root) roots.set(root.pid, root)
  }

  if (!children.length) return { children, roots: [] as ProcessInfo[] }
  if (!roots.size) {
    throw new Error(`${def.label} children are listening, but no ${def.rootToken} ancestor could be verified. Refusing to terminate detached processes automatically.`)
  }
  if (roots.size > 1) {
    throw new Error(`${def.label} listeners belong to multiple supervisor roots (${Array.from(roots.keys()).join(', ')}); refusing ambiguous recovery.`)
  }
  return { children, roots: Array.from(roots.values()) }
}

async function killPid(pid: number) {
  try {
    await execText('taskkill.exe', ['/PID', String(pid), '/T', '/F'], 10_000)
  } catch (error) {
    if (await processInfo(pid)) throw error
  }
}

async function waitPortsClosed(def: SupervisorDefinition, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const listeners = await listenerPids()
    if (!def.members.some((member) => listeners.has(member.port))) return []
    await new Promise((resolve) => setTimeout(resolve, 350))
  }
  const listeners = await listenerPids()
  return def.members
    .map((member) => ({ member, pid: listeners.get(member.port) || null }))
    .filter((item) => item.pid !== null)
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
    throw new Error(`${id} legacy supervisor stopped but failed to start the new build: ${message}`)
  }
  return payload
}

export function legacySupervisorRecoveryPlugin(): Plugin {
  let busy = false
  return {
    name: 'btc5m-legacy-supervisor-recovery',
    configureServer(server) {
      server.middlewares.use(async (req: IncomingMessage, res: ServerResponse, next) => {
        const url = new URL(String(req.url || '/'), 'http://127.0.0.1')
        const match = url.pathname.match(/^\/control\/services\/multi\/(stop|restart)$/)
        if (!match) {
          next()
          return
        }
        if (req.method !== 'POST') {
          json(res, 405, { ok: false, error: 'POST required' })
          return
        }
        if (busy) {
          json(res, 409, { ok: false, error: 'Multi-Asset Supervisor recovery already in progress' })
          return
        }

        busy = true
        try {
          const plan = await buildRecoveryPlan(MULTI)
          if (!plan.children.length) {
            next()
            return
          }

          for (const root of plan.roots) await killPid(root.pid)
          const remaining = await waitPortsClosed(MULTI)
          if (remaining.length) {
            throw new Error(`Legacy Multi supervisor stopped, but ports remain open: ${remaining.map((item) => `${item.member.port}->PID ${item.pid}`).join(' | ')}`)
          }

          if (match[1] === 'stop') {
            json(res, 200, {
              ok: true,
              recoveredLegacySupervisor: true,
              action: 'stop',
              killedRootPids: plan.roots.map((root) => root.pid),
              formerChildren: plan.children,
            })
            return
          }

          const token = String(req.headers['x-btc-lab-control'] || '')
          if (!token) throw new Error('Missing localhost control token for Multi restart')
          const started = await startThroughDashboard('multi', token)
          json(res, 200, {
            ok: true,
            recoveredLegacySupervisor: true,
            action: 'restart',
            killedRootPids: plan.roots.map((root) => root.pid),
            formerChildren: plan.children,
            started,
          })
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          json(res, /refusing|ambiguous|could not be inspected|remain open/i.test(message) ? 409 : 500, { ok: false, error: message })
        } finally {
          busy = false
        }
      })
    },
  }
}
