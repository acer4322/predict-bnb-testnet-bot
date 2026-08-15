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
  let current = value
  for (let i = 0; i < 3; i += 1) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) break
    const row = current as Record<string, unknown>
    if (row.state && typeof row.state === 'object' && !Array.isArray(row.state) && ('ok' in row || Object.keys(row).length <= 3)) {
      current = row.state
      continue
    }
    if (row.data && typeof row.data === 'object' && !Array.isArray(row.data) && ('ok' in row || 'success' in row)) {
      current = row.data
      continue
    }
    break
  }
  return current
}

type StrategyState = {
  service: ServiceSnapshot
  refresh: () => Promise<void>
}

export const useStrategyStore = create<StrategyState>((set, get) => ({
  service: blank(),
  refresh: async () => {
    const previous = get().service
    const started = performance.now()
    try {
      const response = await axios.get('/bridge/strategies', {
        timeout: 3500,
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
