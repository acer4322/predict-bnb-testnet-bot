import axios from 'axios'
import { create } from 'zustand'

export type ServiceKey = 'realtime' | 'polyGap' | 'crossOracle'

export type ServiceSnapshot = {
  ok: boolean
  loading: boolean
  data: unknown
  error: string | null
  updatedAt: number | null
  latencyMs: number | null
}

type DashboardState = {
  services: Record<ServiceKey, ServiceSnapshot>
  refresh: () => Promise<void>
}

const blank = (): ServiceSnapshot => ({
  ok: false,
  loading: true,
  data: null,
  error: null,
  updatedAt: null,
  latencyMs: null,
})

const endpoints: Record<ServiceKey, string> = {
  realtime: '/bridge/realtime',
  polyGap: '/bridge/poly-gap',
  crossOracle: '/bridge/cross-oracle',
}

async function readService(key: ServiceKey): Promise<ServiceSnapshot> {
  const started = performance.now()
  try {
    const response = await axios.get(endpoints[key], {
      timeout: 1800,
      headers: { Accept: 'application/json' },
    })
    return {
      ok: true,
      loading: false,
      data: response.data,
      error: null,
      updatedAt: Date.now(),
      latencyMs: performance.now() - started,
    }
  } catch (error) {
    return {
      ok: false,
      loading: false,
      data: null,
      error: axios.isAxiosError(error)
        ? error.message
        : error instanceof Error
          ? error.message
          : String(error),
      updatedAt: Date.now(),
      latencyMs: performance.now() - started,
    }
  }
}

export const useDashboardStore = create<DashboardState>((set, get) => ({
  services: {
    realtime: blank(),
    polyGap: blank(),
    crossOracle: blank(),
  },
  refresh: async () => {
    const previous = get().services
    const [realtime, polyGap, crossOracle] = await Promise.all([
      readService('realtime'),
      readService('polyGap'),
      readService('crossOracle'),
    ])
    set({
      services: {
        realtime: realtime.ok ? realtime : { ...realtime, data: previous.realtime.data },
        polyGap: polyGap.ok ? polyGap : { ...polyGap, data: previous.polyGap.data },
        crossOracle: crossOracle.ok ? crossOracle : { ...crossOracle, data: previous.crossOracle.data },
      },
    })
  },
}))

export function getPath(input: unknown, ...paths: string[]): unknown {
  for (const path of paths) {
    let current: unknown = input
    let found = true
    for (const part of path.split('.')) {
      if (current == null || typeof current !== 'object' || !(part in current)) {
        found = false
        break
      }
      current = (current as Record<string, unknown>)[part]
    }
    if (found && current !== undefined && current !== null) return current
  }
  return null
}

export function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export function asText(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return fallback
}
