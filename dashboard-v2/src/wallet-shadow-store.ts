import axios from 'axios'
import { create } from 'zustand'
import type { ServiceSnapshot } from './store'

const SIDE_ONLY = 'TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY'

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

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function producerCompatibility(officialValue: unknown, producerValue: unknown, producerError: string | null) {
  const official = record(unwrap(officialValue))
  const producer = record(unwrap(producerValue))
  const latestSnapshot = record(producer.latestPublicSnapshot)
  const rawDecision = record(producer.lastDecision)
  const decision = Object.keys(rawDecision).length
    ? {
        ...rawDecision,
        sampledAtMs: rawDecision.sampledAtMs ?? latestSnapshot.sampledAtMs,
      }
    : {}
  const trade = record(producer.currentTrade)
  const handoff = record(producer.echtgeldHandoff)
  const currentEvent = Object.keys(trade).length
    ? {
        ...trade,
        observedAsk: trade.observedAsk ?? trade.observed_ask,
        selectedProbability: trade.selectedProbability ?? trade.selected_probability,
        decisionAtMs: trade.decisionAtMs ?? trade.decision_at_ms,
      }
    : {}

  return {
    ...official,
    // Compatibility shape for the legacy Echtgeld page. The values below are
    // now sourced from the actual frozen EBM runtime on 8782, never 8776.
    targetTakerPublicSideV1Lab: {
      source: '8782_TARGET_TAKER_PUBLIC_SIDE_EBM',
      online: Object.keys(producer).length > 0 && producerError === null,
      error: producerError,
      cohorts: {
        [SIDE_ONLY]: {
          lastDecision: decision,
          currentEvent,
          currentTrade: trade,
          currentMarket: record(producer.currentMarket),
          policy: record(producer.policy),
          model: record(producer.model),
          dataIntegrity: record(producer.dataIntegrity),
          calculationDiagnostics: record(producer.calculationDiagnostics),
        },
      },
    },
    targetTakerEchtgeldProducerV1: {
      ...handoff,
      source: '8782',
      online: Object.keys(producer).length > 0 && producerError === null,
      error: producerError,
    },
    targetTakerPublicSideProducer8782: {
      ...producer,
      online: Object.keys(producer).length > 0 && producerError === null,
      fetchError: producerError,
    },
  }
}

type WalletShadowStore = {
  service: ServiceSnapshot
  producer8782: ServiceSnapshot
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
  producer8782: blank(),
  targetTakerSaving: false,
  targetTakerSaveError: null,

  refresh: async () => {
    if (refreshInFlight) return refreshInFlight
    refreshInFlight = (async () => {
      const previous = get()
      const started = performance.now()
      const [officialResult, producerResult] = await Promise.allSettled([
        axios.get('/bridge/wallet-shadow', {
          timeout: 4000,
          headers: { Accept: 'application/json' },
        }),
        axios.get('/bridge/ebm-strategy-test', {
          timeout: 2500,
          headers: { Accept: 'application/json' },
        }),
      ])

      const now = Date.now()
      const officialOk = officialResult.status === 'fulfilled'
      const producerOk = producerResult.status === 'fulfilled'
      const officialError = officialOk
        ? null
        : axios.isAxiosError(officialResult.reason)
          ? officialResult.reason.message
          : officialResult.reason instanceof Error
            ? officialResult.reason.message
            : String(officialResult.reason)
      const producerError = producerOk
        ? null
        : axios.isAxiosError(producerResult.reason)
          ? producerResult.reason.message
          : producerResult.reason instanceof Error
            ? producerResult.reason.message
            : String(producerResult.reason)

      const officialData = officialOk
        ? unwrap(officialResult.value.data)
        : previous.service.data
      const producerData = producerOk
        ? unwrap(producerResult.value.data)
        : null

      set({
        service: {
          ok: officialOk || previous.service.data !== null,
          loading: false,
          data: producerCompatibility(officialData, producerData, producerError),
          error: officialError,
          updatedAt: now,
          latencyMs: performance.now() - started,
        },
        producer8782: {
          ok: producerOk,
          loading: false,
          data: producerOk ? producerData : null,
          error: producerError,
          updatedAt: now,
          latencyMs: performance.now() - started,
        },
      })
    })()
    try {
      await refreshInFlight
    } finally {
      refreshInFlight = null
    }
  },

  // Retained only for the separately preserved legacy Echtgeld page. The new
  // 8776 Official collector intentionally exposes no /settings endpoint, and
  // Target Wallet Research never calls this method.
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
