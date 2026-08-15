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

type WalletShadowStore = {
  service: ServiceSnapshot
  targetTakerSaving: boolean
  targetTakerSaveError: string | null
  refresh: () => Promise<void>
  updateTargetTakerSettings: (values: Record<string, unknown>) => Promise<void>
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

export const useWalletShadowStore = create<WalletShadowStore>((set, get) => ({
  service: blank(),
  targetTakerSaving: false,
  targetTakerSaveError: null,

  refresh: async () => {
    if (refreshInFlight) return refreshInFlight
    refreshInFlight = (async () => {
      const previous = get().service
      const started = performance.now()
      try {
        const response = await axios.get('/bridge/wallet-shadow', {
          // The intentionally comprehensive 8776 snapshot can take 10-15s on
          // the multi-GB research ledger. refreshInFlight still guarantees
          // that the 3s UI timer cannot overlap these expensive reads.
          timeout: 20000,
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
            ok: previous.data !== null,
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

  updateTargetTakerSettings: async (values) => {
    set({ targetTakerSaving: true, targetTakerSaveError: null })
    try {
      const token = await getControlToken()
      const response = await fetch('/control/target-taker-v1', {
        method: 'POST',
        cache: 'no-store',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-BTC-Lab-Control': token,
        },
        body: JSON.stringify(values),
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        if (response.status === 403) controlToken = null
        const message = payload && typeof payload === 'object' && 'error' in payload
          ? String((payload as Record<string, unknown>).error)
          : `HTTP ${response.status}`
        throw new Error(message)
      }
      const state = unwrap(payload)
      if (!state || typeof state !== 'object' || Array.isArray(state)) {
        throw new Error('Target Taker settings update returned no state object')
      }
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
        targetTakerSaving: false,
        targetTakerSaveError: null,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      set({ targetTakerSaving: false, targetTakerSaveError: message })
      throw error
    }
  },
}))
