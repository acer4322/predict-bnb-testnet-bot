import { closeSync, mkdirSync, openSync, promises as fs } from 'node:fs'
import { execFile, spawn } from 'node:child_process'
import { join } from 'node:path'
import { Socket } from 'node:net'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

type MakerAction = 'start' | 'stop' | 'restart'

type MakerDefinition = {
  id: 'makerInference' | 'makerInferenceEth'
  port: number
  label: string
  module: string
  pidFile: string
  logStem: string
}

type ProcessInfo = {
  pid: number
  commandLine: string
}

const MAKER_SERVICES: Record<string, MakerDefinition> = {
  makerInference: {
    id: 'makerInference',
    port: 8778,
    label: 'Maker Book Inference · BTC',
    module: 'predict_bot.predict_wallet_maker_book_inference_collector_v2_1',
    pidFile: '.wallet-shadow-lab-maker-book.pid',
    logStem: 'makerInference',
  },
  makerInferenceEth: {
    id: 'makerInferenceEth',
    port: 8779,
    label: 'Maker Book Inference · ETH 5M',
    module: 'predict_bot.predict_wallet_maker_book_inference_collector_eth5m',
    pidFile: '.wallet-shadow-lab-maker-book-eth5m.pid',
    logStem: 'makerInferenceEth',
  },
}

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
  if (process.platform !== 'win32') throw new Error('Maker Service Control currently supports Windows hosts only.')
  return execFileText(
    'powershell.exe',
    ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
    timeoutMs,
  )
}

async function listenerInfo(port: number): Promise<ProcessInfo | null> {
  const script = [
    `$c=Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1`,
    'if($c){',
    '  $p=Get-CimInstance Win32_Process -Filter ("ProcessId=" + $c.OwningProcess) -ErrorAction SilentlyContinue',
    '  if($p){[pscustomobject]@{pid=[int]$p.ProcessId;commandLine=[string]$p.CommandLine} | ConvertTo-Json -Compress}',
    '}',
  ].join('\n')
  try {
    const raw = await powershell(script, 5_000)
    if (!raw) return null
    const row = JSON.parse(raw) as Record<string, unknown>
    const pid = Number(row.pid)
    if (!Number.isInteger(pid) || pid <= 0) return null
    return { pid, commandLine: String(row.commandLine || '') }
  } catch {
    return null
  }
}

async function processInfo(pid: number): Promise<ProcessInfo | null> {
  if (!Number.isInteger(pid) || pid <= 0) return null
  const script = [
    `$p=Get-CimInstance Win32_Process -Filter "ProcessId=${pid}" -ErrorAction SilentlyContinue`,
    'if($p){[pscustomobject]@{pid=[int]$p.ProcessId;commandLine=[string]$p.CommandLine} | ConvertTo-Json -Compress}',
  ].join('\n')
  try {
    const raw = await powershell(script, 5_000)
    if (!raw) return null
    const row = JSON.parse(raw) as Record<string, unknown>
    const actualPid = Number(row.pid)
    if (!Number.isInteger(actualPid) || actualPid <= 0) return null
    return { pid: actualPid, commandLine: String(row.commandLine || '') }
  } catch {
    return null
  }
}

function commandMatches(info: ProcessInfo | null, def: MakerDefinition) {
  return Boolean(info && info.commandLine.toLowerCase().includes(def.module.toLowerCase()))
}

async function readPid(path: string): Promise<number | null> {
  try {
    const pid = Number((await fs.readFile(path, 'utf8')).trim())
    return Number.isInteger(pid) && pid > 0 ? pid : null
  } catch {
    return null
  }
}

async function registryOwnedPid(root: string, def: MakerDefinition): Promise<number | null> {
  try {
    const path = join(root, 'data', 'dashboard-v2-service-registry.json')
    const registry = JSON.parse(await fs.readFile(path, 'utf8')) as Record<string, unknown>
    const services = registry.services && typeof registry.services === 'object'
      ? registry.services as Record<string, unknown>
      : {}
    const entry = services[def.id] && typeof services[def.id] === 'object'
      ? services[def.id] as Record<string, unknown>
      : null
    const pid = Number(entry?.rootPid)
    if (!Number.isInteger(pid) || pid <= 0) return null
    const info = await processInfo(pid)
    return commandMatches(info, def) ? pid : null
  } catch {
    return null
  }
}

async function ownedPid(root: string, def: MakerDefinition): Promise<number | null> {
  const legacyPath = join(root, def.pidFile)
  const legacyPid = await readPid(legacyPath)
  if (legacyPid) {
    const info = await processInfo(legacyPid)
    if (commandMatches(info, def)) return legacyPid
  }
  return registryOwnedPid(root, def)
}

function portOpen(port: number, timeoutMs = 350): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new Socket()
    let settled = false
    const finish = (value: boolean) => {
      if (settled) return
      settled = true
      socket.destroy()
      resolve(value)
    }
    socket.setTimeout(timeoutMs)
    socket.once('connect', () => finish(true))
    socket.once('timeout', () => finish(false))
    socket.once('error', () => finish(false))
    socket.connect(port, '127.0.0.1')
  })
}

async function waitForPort(port: number, desiredOpen: boolean, timeoutMs: number) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if ((await portOpen(port)) === desiredOpen) return true
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return (await portOpen(port)) === desiredOpen
}

async function killTree(pid: number) {
  try {
    await execFileText('taskkill.exe', ['/PID', String(pid), '/T', '/F'], 10_000)
  } catch (error) {
    const stillAlive = await processInfo(pid)
    if (stillAlive) throw error
  }
}

function spawnMaker(root: string, def: MakerDefinition) {
  const data = join(root, 'data')
  mkdirSync(data, { recursive: true })
  const stdoutPath = join(data, `service-manager-${def.logStem}.stdout.log`)
  const stderrPath = join(data, `service-manager-${def.logStem}.stderr.log`)
  const stdoutFd = openSync(stdoutPath, 'a')
  const stderrFd = openSync(stderrPath, 'a')
  try {
    const child = spawn('python', ['-m', def.module], {
      cwd: root,
      env: process.env,
      windowsHide: true,
      detached: true,
      stdio: ['ignore', stdoutFd, stderrFd],
    })
    child.unref()
    if (!child.pid) throw new Error(`Failed to obtain PID for ${def.label}`)
    return { pid: child.pid, child, stdoutPath, stderrPath }
  } finally {
    closeSync(stdoutFd)
    closeSync(stderrFd)
  }
}

async function startMaker(root: string, def: MakerDefinition) {
  const existing = await listenerInfo(def.port)
  if (existing) {
    if (!commandMatches(existing, def)) {
      throw new Error(`Port ${def.port} is occupied by an unrecognized process. PID=${existing.pid} command=${existing.commandLine}`)
    }
    return {
      ok: true,
      unchanged: true,
      service: def.id,
      state: 'ONLINE',
      pid: existing.pid,
      ownership: (await ownedPid(root, def)) === existing.pid ? 'MANAGED_OR_LEGACY' : 'EXTERNAL',
    }
  }

  // A previous Dashboard version may already have spawned this process and be
  // waiting on its heavy /state endpoint. Do not create a duplicate merely
  // because the listener is not ready yet; follow the verified owned PID first.
  const existingOwned = await ownedPid(root, def)
  if (existingOwned) {
    const opened = await waitForPort(def.port, true, 15_000)
    if (opened) {
      const listener = await listenerInfo(def.port)
      if (listener?.pid === existingOwned && commandMatches(listener, def)) {
        return {
          ok: true,
          unchanged: true,
          service: def.id,
          state: 'ONLINE',
          pid: existingOwned,
          ownership: 'MANAGED_OR_LEGACY',
          readiness: 'VERIFIED_EXISTING_LISTENER',
        }
      }
      throw new Error(
        `Port ${def.port} opened while verified ${def.label} PID ${existingOwned} was starting, ` +
        `but listener ownership does not match. Refusing to spawn another process.`,
      )
    }
    if (await processInfo(existingOwned)) {
      throw new Error(
        `${def.label} PID ${existingOwned} is already starting but has not opened port ${def.port} yet. ` +
        'No duplicate process was started.',
      )
    }
  }

  const launched = spawnMaker(root, def)
  const pidFile = join(root, def.pidFile)
  await fs.writeFile(pidFile, `${launched.pid}\n`, 'utf8')

  let exitCode: number | null = null
  let exited = false
  launched.child.once('exit', (code) => {
    exited = true
    exitCode = code
  })

  const deadline = Date.now() + 45_000
  while (Date.now() < deadline) {
    if (exited) {
      await fs.unlink(pidFile).catch(() => undefined)
      throw new Error(
        `${def.label} exited before port ${def.port} became ready (exit=${exitCode}). ` +
        `Check ${launched.stderrPath}`,
      )
    }
    if (await portOpen(def.port)) {
      const listener = await listenerInfo(def.port)
      if (!listener) {
        await new Promise((resolve) => setTimeout(resolve, 200))
        continue
      }
      if (listener.pid !== launched.pid || !commandMatches(listener, def)) {
        await killTree(launched.pid).catch(() => undefined)
        await fs.unlink(pidFile).catch(() => undefined)
        throw new Error(
          `Port ${def.port} became occupied by a different process while starting ${def.label}. ` +
          `Expected PID=${launched.pid}; listener PID=${listener.pid} command=${listener.commandLine}`,
        )
      }
      return {
        ok: true,
        unchanged: false,
        service: def.id,
        state: 'ONLINE',
        pid: launched.pid,
        readiness: 'VERIFIED_LISTENER',
        note: 'Lifecycle readiness is based on verified process identity + listening port; full /state is research telemetry, not a startup gate.',
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }

  const info = await processInfo(launched.pid)
  if (!info) await fs.unlink(pidFile).catch(() => undefined)
  throw new Error(
    `${def.label} did not open port ${def.port} within 45 seconds. ` +
    `Process ${info ? 'is still running' : 'has exited'}; check ${launched.stderrPath}`,
  )
}

async function stopMaker(root: string, def: MakerDefinition) {
  const pid = await ownedPid(root, def)
  const listener = await listenerInfo(def.port)
  if (!pid) {
    if (!listener) {
      await fs.unlink(join(root, def.pidFile)).catch(() => undefined)
      return { ok: true, unchanged: true, service: def.id, state: 'OFFLINE' }
    }
    throw new Error(
      `${def.label} is EXTERNAL on port ${def.port}. Dashboard has no verified owned PID and will not kill it. ` +
      `PID=${listener.pid} command=${listener.commandLine}`,
    )
  }

  const info = await processInfo(pid)
  if (!commandMatches(info, def)) {
    throw new Error(`Owned PID ${pid} no longer matches ${def.module}; refusing to kill it.`)
  }

  await killTree(pid)
  await fs.unlink(join(root, def.pidFile)).catch(() => undefined)

  if (listener?.pid === pid) {
    const closed = await waitForPort(def.port, false, 12_000)
    if (!closed) {
      const after = await listenerInfo(def.port)
      if (after?.pid === pid) throw new Error(`${def.label} PID ${pid} did not release port ${def.port}.`)
    }
  }

  return { ok: true, unchanged: false, service: def.id, state: 'OFFLINE', pid }
}

async function restartMaker(root: string, def: MakerDefinition) {
  await stopMaker(root, def)
  return startMaker(root, def)
}

function errorStatus(message: string) {
  if (/EXTERNAL|unrecognized|different process|refusing|occupied|already starting/i.test(message)) return 409
  if (/Windows hosts only/i.test(message)) return 501
  return 500
}

export function makerServiceControlPlugin(repoRoot: string): Plugin {
  const busy = new Set<string>()
  return {
    name: 'btc5m-maker-service-control-v2',
    configureServer(server) {
      server.middlewares.use(async (req: IncomingMessage, res: ServerResponse, next) => {
        const url = new URL(String(req.url || '/'), 'http://127.0.0.1')
        const match = url.pathname.match(/^\/control\/services\/(makerInference|makerInferenceEth)\/(start|stop|restart)$/)
        if (!match) {
          next()
          return
        }
        if (req.method !== 'POST') {
          json(res, 405, { ok: false, error: 'POST required' })
          return
        }

        const id = match[1]
        const action = match[2] as MakerAction
        const def = MAKER_SERVICES[id]
        if (!def) {
          json(res, 404, { ok: false, error: `Unknown Maker service ${id}` })
          return
        }
        if (busy.has(id)) {
          json(res, 409, { ok: false, error: `service:${id} already has a lifecycle action in progress` })
          return
        }

        busy.add(id)
        try {
          const result = action === 'start'
            ? await startMaker(repoRoot, def)
            : action === 'stop'
              ? await stopMaker(repoRoot, def)
              : await restartMaker(repoRoot, def)
          json(res, 200, result)
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          json(res, errorStatus(message), { ok: false, error: message })
        } finally {
          busy.delete(id)
        }
      })
    },
  }
}