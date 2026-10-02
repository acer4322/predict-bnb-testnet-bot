import { create } from 'zustand'

export type CloneAsset = 'ETH' | 'BNB'

type CloneServiceState = {
  ok: boolean
  loading: boolean
  latencyMs: number | null
  error: string | null
}

type CloneAssetState = {
  service: CloneServiceState
  snapshot: Record<string, unknown> | null
  saving: boolean
  saveError: string | null
}

type WalletCloneStore = {
  assets: Record<CloneAsset, CloneAssetState>
  refresh: () => Promise<void>
  updateSettings: (asset: CloneAsset, values: Record<string, unknown>) => Promise<Record<string, unknown>>
}

const READ_ENDPOINTS: Record<CloneAsset, string> = {
  ETH: '/bridge/eth-clone',
  BNB: '/bridge/bnb-clone',
}

const CONTROL_ENDPOINTS: Record<CloneAsset, string> = {
  ETH: '/control/eth-clone',
  BNB: '/control/bnb-clone',
}

function emptyAsset(): CloneAssetState {
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
  return nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as Record<string, unknown>
    : row
}

async function fetchAsset(asset: CloneAsset, previous: CloneAssetState): Promise<CloneAssetState> {
  const started = performance.now()
  try {
    const response = await fetch(READ_ENDPOINTS[asset], {
      method: 'GET',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = payload && typeof payload === 'object' && 'error' in payload
        ? String((payload as Record<string, unknown>).error)
        : `HTTP ${response.status}`
      throw new Error(detail)
    }
    const snapshot = unwrapState(payload)
    if (!snapshot) throw new Error('clone engine returned no state')
    return {
      ...previous,
      service: { ok: true, loading: false, latencyMs: performance.now() - started, error: null },
      snapshot,
    }
  } catch (error) {
    return {
      ...previous,
      service: {
        ok: false,
        loading: false,
        latencyMs: performance.now() - started,
        error: error instanceof Error ? error.message : String(error),
      },
    }
  }
}

let controlToken: string | null = null

async function getControlToken(): Promise<string> {
  if (controlToken) return controlToken
  const response = await fetch('/control/session', { method: 'GET', cache: 'no-store', headers: { Accept: 'application/json' } })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload || typeof payload !== 'object') {
    throw new Error(`control session unavailable (HTTP ${response.status})`)
  }
  const token = String((payload as Record<string, unknown>).token || '')
  if (!token) throw new Error('control session returned no token')
  controlToken = token
  return token
}

export const useWalletCloneStore = create<WalletCloneStore>((set, get) => ({
  assets: { ETH: emptyAsset(), BNB: emptyAsset() },

  refresh: async () => {
    const previous = get().assets
    set({
      assets: {
        ETH: { ...previous.ETH, service: { ...previous.ETH.service, loading: true } },
        BNB: { ...previous.BNB, service: { ...previous.BNB.service, loading: true } },
      },
    })
    const [ETH, BNB] = await Promise.all([
      fetchAsset('ETH', previous.ETH),
      fetchAsset('BNB', previous.BNB),
    ])
    set({ assets: { ETH, BNB } })
  },

  updateSettings: async (asset, values) => {
    const before = get().assets
    set({ assets: { ...before, [asset]: { ...before[asset], saving: true, saveError: null } } })
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
        if (response.status === 403) controlToken = null
        const detail = payload && typeof payload === 'object' && 'error' in payload
          ? String((payload as Record<string, unknown>).error)
          : `HTTP ${response.status}`
        throw new Error(detail)
      }
      const snapshot = unwrapState(payload)
      if (!snapshot) throw new Error('clone settings update returned no state')
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
      set({ assets: { ...current, [asset]: { ...current[asset], saving: false, saveError: message } } })
      throw error
    }
  },
}))
