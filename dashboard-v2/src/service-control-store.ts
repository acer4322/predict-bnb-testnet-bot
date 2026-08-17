import { create } from 'zustand'

export type ServiceOwnership = 'MANAGED' | 'LEGACY_MANAGED' | 'EXTERNAL' | 'NONE' | 'SELF'
export type ServiceRuntimeState = 'ONLINE' | 'OFFLINE' | 'PARTIAL' | 'CONFLICT' | 'CRASHED'
export type ServiceAction = 'start' | 'stop' | 'restart'

export type ServicePortStatus = {
  port: number
  label: string
  required: boolean
  healthy: boolean
  httpStatus: number | null
  pid: number | null
  commandRecognized: boolean | null
}

export type ManagedServiceStatus = {
  id: string
  label: string
  description: string
  mode: 'direct' | 'script' | 'self'
  state: ServiceRuntimeState
  ownership: ServiceOwnership
  rootPid: number | null
  pids: number[]
  startedAt: number | null
  controllable: boolean
  canStart: boolean
  canStop: boolean
  canRestart: boolean
  safetyNote: string | null
  runtime: {
    armed?: boolean
    runtimeStatus?: string | null
    version?: string | null
  } | null
  ports: ServicePortStatus[]
}

export type ServiceControlSnapshot = {
  ok: boolean
  generatedAt: number
  hostControl: boolean
  dashboardRestartExternal: boolean
  groups: Array<{ id: string; label: string }>
  services: ManagedServiceStatus[]
}

type ServiceControlStore = {
  snapshot: ServiceControlSnapshot | null
  loading: boolean
  error: string | null
  actionKeys: string[]
  refresh: () => Promise<void>
  serviceAction: (id: string, action: ServiceAction) => Promise<void>
  groupAction: (id: string, action: ServiceAction) => Promise<void>
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

let token: string | null = null
let refreshPromise: Promise<void> | null = null

async function controlToken() {
  if (token) return token
  const response = await fetch('/control/session', { method: 'GET', cache: 'no-store', headers: { Accept: 'application/json' } })
  const payload = await response.json().catch(() => null) as Record<string, unknown> | null
  if (!response.ok) throw new Error(payload?.error ? String(payload.error) : `Control session unavailable (HTTP ${response.status})`)
  const value = String(payload?.token || '')
  if (!value) throw new Error('Control session returned no token')
  token = value
  return value
}

async function request(path: string, method: 'GET' | 'POST') {
  const sessionToken = await controlToken()
  const response = await fetch(path, {
    method,
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
      'X-BTC-Lab-Control': sessionToken,
    },
  })
  const payload = await response.json().catch(() => null) as Record<string, unknown> | null
  if (!response.ok) {
    if (response.status === 403) token = null
    throw new Error(payload?.error ? String(payload.error) : `HTTP ${response.status}`)
  }
  return payload
}

function addActionKey(keys: string[], key: string) {
  return keys.includes(key) ? keys : [...keys, key]
}

function removeActionKey(keys: string[], key: string) {
  return keys.filter((value) => value !== key)
}

async function verifiedGroupAction(id: string, action: ServiceAction) {
  const group = GROUPS[id]
  if (!group || action === 'start') {
    await request(`/control/service-groups/${encodeURIComponent(id)}/${action}`, 'POST')
    return
  }

  // Stop/restart is intentionally decomposed into individual service calls so
  // every service goes through verified-service-control.ts and must prove that
  // its actual TCP listeners disappeared. For "all", Echtgeld is first so an
  // ARMED/unresolved engine fails closed before any other service is changed.
  for (const serviceId of group.stop) {
    await request(`/control/services/${encodeURIComponent(serviceId)}/stop`, 'POST')
  }
  if (action === 'restart') {
    for (const serviceId of group.start) {
      await request(`/control/services/${encodeURIComponent(serviceId)}/start`, 'POST')
    }
  }
}

export const useServiceControlStore = create<ServiceControlStore>((set, get) => ({
  snapshot: null,
  loading: false,
  error: null,
  actionKeys: [],

  refresh: async () => {
    if (refreshPromise) return refreshPromise
    refreshPromise = (async () => {
      set({ loading: true })
      try {
        const payload = await request('/control/services', 'GET') as unknown as ServiceControlSnapshot
        set({ snapshot: payload, loading: false, error: null })
      } catch (error) {
        set({ loading: false, error: error instanceof Error ? error.message : String(error) })
      }
    })()
    try {
      await refreshPromise
    } finally {
      refreshPromise = null
    }
  },

  serviceAction: async (id, action) => {
    const key = `${id}:${action}`
    set((state) => ({ actionKeys: addActionKey(state.actionKeys, key), error: null }))
    try {
      await request(`/control/services/${encodeURIComponent(id)}/${action}`, 'POST')
      set((state) => ({ actionKeys: removeActionKey(state.actionKeys, key) }))
      await get().refresh()
    } catch (error) {
      set((state) => ({
        actionKeys: removeActionKey(state.actionKeys, key),
        error: error instanceof Error ? error.message : String(error),
      }))
      throw error
    }
  },

  groupAction: async (id, action) => {
    const key = `group:${id}:${action}`
    set((state) => ({ actionKeys: addActionKey(state.actionKeys, key), error: null }))
    try {
      await verifiedGroupAction(id, action)
      set((state) => ({ actionKeys: removeActionKey(state.actionKeys, key) }))
      await get().refresh()
    } catch (error) {
      set((state) => ({
        actionKeys: removeActionKey(state.actionKeys, key),
        error: error instanceof Error ? error.message : String(error),
      }))
      throw error
    }
  },
}))