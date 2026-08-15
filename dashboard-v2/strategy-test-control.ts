import { closeSync, mkdirSync, openSync, promises as fs } from 'node:fs'
import { execFile, spawn } from 'node:child_process'
import { join } from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

const PORT = 8780
const MODULE = 'predict_bot.target_taker_public_side_test_v2'
const RECOGNIZED_MODULES = [
  'predict_bot.target_taker_public_side_test_v1',
  'predict_bot.target_taker_public_side_test_v2',
]
const PID_FILE = '.target-taker-public-side-test.pid'

type ProcessInfo = { pid: number; commandLine: string }

function json(res: ServerResponse, status: number, payload: unknown) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(JSON.stringify(payload))
}

function execText(file: string, args: string[], timeoutMs = 8_000): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { windowsHide: true, timeout: timeoutMs, maxBuffer: 2 * 1024 * 1024 }, (error, stdout, stderr) => {
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

async function processInfo(pid: number): Promise<ProcessInfo | null> {
  if (!Number.isInteger(pid) || pid <= 0 || process.platform !== 'win32') return null
  try {
    const raw = await powershell(
      `$p=Get-CimInstance Win32_Process -Filter \"ProcessId=${pid}\" -ErrorAction SilentlyContinue; if($p){$p | Select-Object @{n='pid';e={[int]$_.ProcessId}},@{n='commandLine';e={[string]$_.CommandLine}} | ConvertTo-Json -Compress}`,
    )
    if (!raw) return null
    const row = JSON.parse(raw) as Record<string, unknown>
    const actual = Number(row.pid)
    return Number.isInteger(actual) && actual > 0
      ? { pid: actual, commandLine: String(row.commandLine || '') }
      : null
  } catch {
    return null
  }
}

async function listener(): Promise<ProcessInfo | null> {
  if (process.platform !== 'win32') return null
  try {
    const raw = await execText('netstat.exe', ['-ano', '-p', 'tcp'])
    for (const line of raw.split(/\r?\n/)) {
      const match = line.trim().match(/^TCP\s+(\S+)\s+\S+\s+LISTENING\s+(\d+)$/i)
      if (!match) continue
      const portMatch = match[1].match(/:(\d+)$/)
      if (!portMatch || Number(portMatch[1]) !== PORT) continue
      return processInfo(Number(match[2]))
    }
  } catch {
    return null
  }
  return null
}

function recognized(info: ProcessInfo | null) {
  if (!info) return false
  const command = info.commandLine.toLowerCase()
  return RECOGNIZED_MODULES.some((module) => command.includes(module.toLowerCase()))
}

function currentModule(info: ProcessInfo | null) {
  if (!info) return null
  const command = info.commandLine.toLowerCase()
  return RECOGNIZED_MODULES.find((module) => command.includes(module.toLowerCase())) ?? null
}

async function probeHealth() {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 1_500)
  try {
    const response = await fetch(`http://127.0.0.1:${PORT}/health`, {
      signal: controller.signal,
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    const payload = await response.json().catch(() => null)
    return { ok: response.ok, status: response.status, payload }
  } catch {
    return { ok: false, status: null, payload: null }
  } finally {
    clearTimeout(timer)
  }
}

async function waitFor(expected: 'online' | 'offline', timeoutMs: number) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const active = await listener()
    if (expected === 'offline' && !active) return
    if (expected === 'online' && active && recognized(active)) {
      const health = await probeHealth()
      if (health.ok) return
    }
    await new Promise((resolve) => setTimeout(resolve, 300))
  }
  throw new Error(`8780 strategy test did not become ${expected} within ${timeoutMs}ms`)
}

async function writePid(root: string, pid: number) {
  await fs.writeFile(join(root, PID_FILE), `${pid}\n`, 'utf8')
}

async function clearPid(root: string) {
  await fs.unlink(join(root, PID_FILE)).catch(() => undefined)
}

async function start(root: string) {
  const existing = await listener()
  if (existing) {
    if (!recognized(existing)) {
      throw new Error(`Port 8780 is occupied by an unrecognized process. PID=${existing.pid} command=${existing.commandLine}`)
    }
    const runningModule = currentModule(existing)
    if (runningModule !== MODULE) {
      throw new Error(
        `Port 8780 is still running ${runningModule || 'an older recognized strategy test'}. Use Restart so it can be safely replaced by ${MODULE}.`,
      )
    }
    return { ok: true, unchanged: true, pid: existing.pid, module: runningModule, health: await probeHealth() }
  }

  const data = join(root, 'data')
  mkdirSync(data, { recursive: true })
  const stdoutFd = openSync(join(data, 'service-manager-strategyTest.stdout.log'), 'a')
  const stderrFd = openSync(join(data, 'service-manager-strategyTest.stderr.log'), 'a')
  let pid: number
  try {
    const child = spawn('python', ['-m', MODULE], {
      cwd: root,
      env: process.env,
      windowsHide: true,
      detached: true,
      stdio: ['ignore', stdoutFd, stderrFd],
    })
    child.unref()
    if (!child.pid) throw new Error('Failed to obtain PID for 8780 strategy test')
    pid = child.pid
  } finally {
    closeSync(stdoutFd)
    closeSync(stderrFd)
  }
  await writePid(root, pid)
  await waitFor('online', 30_000)
  return { ok: true, unchanged: false, pid, module: MODULE, health: await probeHealth() }
}

async function stop(root: string) {
  const existing = await listener()
  if (!existing) {
    await clearPid(root)
    return { ok: true, unchanged: true, verifiedPortClosed: true }
  }
  if (!recognized(existing)) {
    throw new Error(`Port 8780 PID ${existing.pid} is not a recognized EBM strategy-test process; refusing to terminate it. command=${existing.commandLine}`)
  }
  const stoppedModule = currentModule(existing)
  try {
    await execText('taskkill.exe', ['/PID', String(existing.pid), '/T', '/F'], 10_000)
  } catch (error) {
    if (await processInfo(existing.pid)) throw error
  }
  await waitFor('offline', 12_000)
  await clearPid(root)
  return { ok: true, unchanged: false, killedPid: existing.pid, module: stoppedModule, verifiedPortClosed: true }
}

export function strategyTestControlPlugin(repoRoot: string): Plugin {
  let busy = false
  return {
    name: 'btc5m-target-public-side-strategy-test-control',
    configureServer(server) {
      server.middlewares.use(async (req: IncomingMessage, res: ServerResponse, next) => {
        const url = new URL(String(req.url || '/'), 'http://127.0.0.1')
        const match = url.pathname.match(/^\/control\/strategy-test\/(start|stop|restart|status)$/)
        if (!match) {
          next()
          return
        }
        if (match[1] === 'status' && req.method === 'GET') {
          const active = await listener()
          const health = active && recognized(active) ? await probeHealth() : null
          json(res, 200, {
            ok: true,
            online: Boolean(active && recognized(active) && health?.ok),
            port: PORT,
            pid: active?.pid ?? null,
            recognized: active ? recognized(active) : null,
            module: currentModule(active),
            expectedModule: MODULE,
            upgradeRequired: Boolean(active && recognized(active) && currentModule(active) !== MODULE),
            health,
          })
          return
        }
        if (req.method !== 'POST') {
          json(res, 405, { ok: false, error: 'POST required' })
          return
        }
        if (busy) {
          json(res, 409, { ok: false, error: '8780 strategy test lifecycle action already in progress' })
          return
        }
        busy = true
        try {
          const action = match[1]
          const result = action === 'start'
            ? await start(repoRoot)
            : action === 'stop'
              ? await stop(repoRoot)
              : { stopped: await stop(repoRoot), started: await start(repoRoot) }
          json(res, 200, { ok: true, action, result })
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          json(res, /occupied|refusing|Use Restart/i.test(message) ? 409 : 500, { ok: false, error: message })
        } finally {
          busy = false
        }
      })
    },
  }
}