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
  refresh: () => Promise<void>
}

export const useWalletShadowStore = create<WalletShadowStore>((set, get) => ({
  service: blank(),
  refresh: async () => {
    const previous = get().service
    const started = performance.now()
    try {
      const response = await axios.get('/bridge/wallet-shadow', {
        timeout: 2500,
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
  },
}))
