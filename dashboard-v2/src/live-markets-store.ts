import { create } from 'zustand'

export type LiveAsset = 'BTC' | 'ETH' | 'BNB'

export type LiveServiceState = {
  ok: boolean
  loading: boolean
  latencyMs: number | null
  error: string | null
}

type AssetLiveState = {
  service: LiveServiceState
  snapshot: Record<string, unknown> | null
  saving: boolean
  saveError: string | null
}

type LiveMarketsStore = {
  assets: Record<LiveAsset, AssetLiveState>
  refresh: () => Promise<void>
  updateSettings: (asset: LiveAsset, values: Record<string, unknown>) => Promise<Record<string, unknown>>
}

const READ_ENDPOINTS: Record<LiveAsset, string> = {
  BTC: '/bridge/poly-gap',
  ETH: '/bridge/eth-live',
  BNB: '/bridge/bnb-live',
}

const CONTROL_ENDPOINTS: Record<LiveAsset, string> = {
  BTC: '/control/btc-live',
  ETH: '/control/eth-live',
  BNB: '/control/bnb-live',
}

function emptyAsset(): AssetLiveState {
  return {
    service: { ok: false, loading: false, latencyMs: null, error: null },
    snapshot: null,
    saving: false,
    saveError: null,
  }
}

function unwrapState(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const row = value as Record<string, unknown>
  const nested = row.state
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    return nested as Record<string, unknown>
  }
  return row
}

async function fetchAsset(asset: LiveAsset, previous: AssetLiveState): Promise<AssetLiveState> {
  const started = performance.now()
  try {
    const response = await fetch(READ_ENDPOINTS[asset], {
      method: 'GET',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      const message = payload && typeof payload === 'object' && 'error' in payload
        ? String((payload as Record<string, unknown>).error)
        : `HTTP ${response.status}`
      throw new Error(message)
    }
    const snapshot = unwrapState(payload)
    if (!snapshot) throw new Error('live engine returned no state object')
    return {
      ...previous,
      service: {
        ok: true,
        loading: false,
        latencyMs: Math.max(0, performance.now() - started),
        error: null,
      },
      snapshot,
    }
  } catch (error) {
    return {
      ...previous,
      service: {
        ok: false,
        loading: false,
        latencyMs: Math.max(0, performance.now() - started),
        error: error instanceof Error ? error.message : String(error),
      },
      // Keep the last successful snapshot so a short sidecar restart does not
      // make the settings form flicker back to empty defaults.
      snapshot: previous.snapshot,
    }
  }
}

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

export const useLiveMarketsStore = create<LiveMarketsStore>((set, get) => ({
  assets: {
    BTC: emptyAsset(),
    ETH: emptyAsset(),
    BNB: emptyAsset(),
  },

  refresh: async () => {
    const previous = get().assets
    set({
      assets: {
        BTC: { ...previous.BTC, service: { ...previous.BTC.service, loading: true } },
        ETH: { ...previous.ETH, service: { ...previous.ETH.service, loading: true } },
        BNB: { ...previous.BNB, service: { ...previous.BNB.service, loading: true } },
      },
    })
    const [BTC, ETH, BNB] = await Promise.all([
      fetchAsset('BTC', previous.BTC),
      fetchAsset('ETH', previous.ETH),
      fetchAsset('BNB', previous.BNB),
    ])
    set({ assets: { BTC, ETH, BNB } })
  },

  updateSettings: async (asset, values) => {
    const before = get().assets
    set({
      assets: {
        ...before,
        [asset]: { ...before[asset], saving: true, saveError: null },
      },
    })
    try {
      const token = await getControlToken()
      const response = await fetch(CONTROL_ENDPOINTS[asset], {
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
        const message = payload && typeof payload === 'object' && 'error' in payload
          ? String((payload as Record<string, unknown>).error)
          : `HTTP ${response.status}`
        if (response.status === 403) controlToken = null
        throw new Error(message)
      }
      const snapshot = unwrapState(payload)
      if (!snapshot) throw new Error('settings update returned no state')
      const current = get().assets
      set({
        assets: {
          ...current,
          [asset]: {
            ...current[asset],
            saving: false,
            saveError: null,
            snapshot,
            service: { ...current[asset].service, ok: true, error: null },
          },
        },
      })
      return snapshot
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      const current = get().assets
      set({
        assets: {
          ...current,
          [asset]: { ...current[asset], saving: false, saveError: message },
        },
      })
      throw error
    }
  },
}))
