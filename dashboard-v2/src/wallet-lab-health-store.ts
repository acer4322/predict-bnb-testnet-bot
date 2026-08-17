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
  const record = value as Record<string, unknown>
  return record.state && typeof record.state === 'object' && !Array.isArray(record.state) ? record.state : value
}

type WalletLabHealthStore = {
  observer8776: ServiceSnapshot
  collector8777: ServiceSnapshot
  makerBook8778: ServiceSnapshot
  makerBookEth8779: ServiceSnapshot
  refresh: () => Promise<void>
}

let refreshInFlight: Promise<void> | null = null

async function read(url: string, previous: ServiceSnapshot): Promise<ServiceSnapshot> {
  const started = performance.now()
  try {
    const response = await axios.get(url, { timeout: 1500, headers: { Accept: 'application/json' } })
    return {
      ok: true,
      loading: false,
      data: unwrap(response.data),
      error: null,
      updatedAt: Date.now(),
      latencyMs: performance.now() - started,
    }
  } catch (error) {
    const now = Date.now()
    const hasRecentSuccess = previous.data !== null && previous.updatedAt !== null && now - previous.updatedAt < 5000
    return {
      ok: hasRecentSuccess,
      loading: false,
      data: previous.data,
      error: axios.isAxiosError(error) ? error.message : error instanceof Error ? error.message : String(error),
      updatedAt: previous.updatedAt,
      latencyMs: performance.now() - started,
    }
  }
}

export const useWalletLabHealthStore = create<WalletLabHealthStore>((set, get) => ({
  observer8776: blank(),
  collector8777: blank(),
  makerBook8778: blank(),
  makerBookEth8779: blank(),
  refresh: async () => {
    if (refreshInFlight) return refreshInFlight
    refreshInFlight = (async () => {
      const previous = get()
      const [observer8776, collector8777, makerBook8778, makerBookEth8779] = await Promise.all([
        read('/bridge/wallet-shadow-health', previous.observer8776),
        read('/bridge/wallet-taker-signals', previous.collector8777),
        read('/bridge/wallet-maker-book-inference', previous.makerBook8778),
        read('/bridge/wallet-maker-book-inference-eth5m', previous.makerBookEth8779),
      ])
      set({ observer8776, collector8777, makerBook8778, makerBookEth8779 })
    })()
    try {
      await refreshInFlight
    } finally {
      refreshInFlight = null
    }
  },
}))
