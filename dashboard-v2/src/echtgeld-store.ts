import axios from 'axios'
import { create } from 'zustand'
import type { ServiceSnapshot } from './store'

const blank = (): ServiceSnapshot => ({
  ok: false,
  loading: true,
  data: null,
  error: null,
  updatedAt: null,
  latencyMs: null,
})

function unwrap(value: unknown): unknown {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value
  const row = value as Record<string, unknown>
  if (row.state && typeof row.state === 'object' && !Array.isArray(row.state)) return row.state
  return value
}

type EchtgeldStore = {
  service: ServiceSnapshot
  saving: boolean
  saveError: string | null
  refresh: () => Promise<void>
  pause: (reason?: string) => Promise<void>
  resume: () => Promise<void>
  updateSettings: (values: Record<string, unknown>) => Promise<void>
}

let refreshInFlight: Promise<void> | null = null
let controlToken: string | null = null

async function getControlToken(): Promise<string> {
  if (controlToken) return controlToken
  const response = await fetch('/control/session', {
    method: 'GET',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload || typeof payload !== 'object') {
    const message = payload && typeof payload === 'object' && 'error' in payload
      ? String((payload as Record<string, unknown>).error)
      : `control session unavailable (HTTP ${response.status})`
    throw new Error(message)
  }
  const token = String((payload as Record<string, unknown>).token || '')
  if (!token) throw new Error('control session returned no token')
  controlToken = token
  return token
}

async function control(path: string, body: Record<string, unknown>): Promise<unknown> {
  const token = await getControlToken()
  const response = await fetch(path, {
    method: 'POST',
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-BTC-Lab-Control': token,
    },
    body: JSON.stringify(body),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 403) controlToken = null
    const message = payload && typeof payload === 'object' && 'error' in payload
      ? String((payload as Record<string, unknown>).error)
      : `HTTP ${response.status}`
    throw new Error(message)
  }
  return unwrap(payload)
}

export const useEchtgeldStore = create<EchtgeldStore>((set, get) => ({
  service: blank(),
  saving: false,
  saveError: null,

  refresh: async () => {
    if (refreshInFlight) return refreshInFlight
    refreshInFlight = (async () => {
      const previous = get().service
      const started = performance.now()
      try {
        const response = await axios.get('/bridge/echtgeld', {
          timeout: 5000,
          headers: { Accept: 'application/json' },
        })
        set({
          service: {
            ok: true,
            loading: false,
            data: unwrap(response.data),
            error: null,
            updatedAt: Date.now(),
            latencyMs: performance.now() - started,
          },
        })
      } catch (error) {
        set({
          service: {
            ok: false,
            loading: false,
            data: previous.data,
            error: axios.isAxiosError(error)
              ? error.message
              : error instanceof Error
                ? error.message
                : String(error),
            updatedAt: Date.now(),
            latencyMs: performance.now() - started,
          },
        })
      }
    })()
    try {
      await refreshInFlight
    } finally {
      refreshInFlight = null
    }
  },

  pause: async (reason = 'dashboard-v2') => {
    set({ saving: true, saveError: null })
    try {
      const state = await control('/control/echtgeld/pause', { reason })
      const previous = get().service
      set({
        service: {
          ...previous,
          ok: true,
          loading: false,
          data: state,
          error: null,
          updatedAt: Date.now(),
        },
        saving: false,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      set({ saving: false, saveError: message })
      throw error
    }
  },

  resume: async () => {
    set({ saving: true, saveError: null })
    try {
      const state = await control('/control/echtgeld/resume', {})
      const previous = get().service
      set({
        service: {
          ...previous,
          ok: true,
          loading: false,
          data: state,
          error: null,
          updatedAt: Date.now(),
        },
        saving: false,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      set({ saving: false, saveError: message })
      throw error
    }
  },

  updateSettings: async (values) => {
    set({ saving: true, saveError: null })
    try {
      const state = await control('/control/echtgeld/settings', values)
      const previous = get().service
      set({
        service: {
          ...previous,
          ok: true,
          loading: false,
          data: state,
          error: null,
          updatedAt: Date.now(),
        },
        saving: false,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      set({ saving: false, saveError: message })
      throw error
    }
  },
}))
