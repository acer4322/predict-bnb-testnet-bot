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

type JsonRecord = Record<string, unknown>

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

function isRecord(value: unknown): value is JsonRecord {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function unwrapPayload(value: unknown): unknown {
  let current = value
  for (let i = 0; i < 3; i += 1) {
    if (!isRecord(current)) break
    if (isRecord(current.state) && ('ok' in current || Object.keys(current).length <= 3)) {
      current = current.state
      continue
    }
    if (isRecord(current.data) && ('ok' in current || 'success' in current)) {
      current = current.data
      continue
    }
    break
  }
  return current
}

function directPath(input: unknown, path: string): unknown {
  let current: unknown = input
  for (const part of path.split('.')) {
    if (!isRecord(current) || !(part in current)) return null
    current = current[part]
  }
  return current ?? null
}

function firstPath(input: unknown, ...paths: string[]): unknown {
  for (const path of paths) {
    const value = directPath(input, path)
    if (value !== null && value !== undefined) return value
  }
  return null
}

function deepKey(input: unknown, keys: string[], depth = 0): unknown {
  if (!isRecord(input) || depth > 5) return null
  for (const key of keys) {
    if (key in input && input[key] !== null && input[key] !== undefined) return input[key]
  }
  for (const value of Object.values(input)) {
    if (!isRecord(value)) continue
    const found = deepKey(value, keys, depth + 1)
    if (found !== null && found !== undefined) return found
  }
  return null
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function midpoint(side: unknown): number | null {
  if (!isRecord(side)) return null
  const bid = finiteNumber(side.bestBid ?? side.bid)
  const ask = finiteNumber(side.bestAsk ?? side.ask)
  if (bid !== null && ask !== null) return (bid + ask) / 2
  return bid ?? ask
}

function normalizeCrossOracle(raw: unknown): unknown {
  const unwrapped = unwrapPayload(raw)
  if (!isRecord(unwrapped)) return unwrapped

  const polymarket = isRecord(unwrapped.polymarket) ? unwrapped.polymarket : null
  if (!polymarket) return unwrapped

  const up = isRecord(polymarket.up) ? polymarket.up : {}
  const down = isRecord(polymarket.down) ? polymarket.down : {}
  const upMid = midpoint(up)
  const downMid = midpoint(down)

  return {
    ...unwrapped,
    polymarket: {
      ...polymarket,
      up: { ...up, mid: upMid },
      down: { ...down, mid: downMid },
      upMid,
      downMid,
    },
  }
}

function normalizeRealtime(raw: unknown): unknown {
  const unwrapped = unwrapPayload(raw)
  if (!isRecord(unwrapped)) return unwrapped

  const marketId = firstPath(
    unwrapped,
    'marketId',
    'market.market_id',
    'market.marketId',
    'observation.market_id',
    'currentObservation.market_id',
  ) ?? deepKey(unwrapped, ['market_id', 'marketId'])

  const secondsLeft = firstPath(
    unwrapped,
    'seconds_left',
    'secondsLeft',
    'observation.seconds_left',
    'currentObservation.seconds_left',
  ) ?? deepKey(unwrapped, ['seconds_left', 'secondsLeft'])

  const upAsk = firstPath(
    unwrapped,
    'up_ask',
    'upAsk',
    'observation.up_ask',
    'currentObservation.up_ask',
  ) ?? deepKey(unwrapped, ['up_ask', 'upAsk'])

  const downAsk = firstPath(
    unwrapped,
    'down_ask',
    'downAsk',
    'observation.down_ask',
    'currentObservation.down_ask',
  ) ?? deepKey(unwrapped, ['down_ask', 'downAsk'])

  const upBid = firstPath(
    unwrapped,
    'up_bid',
    'upBid',
    'observation.up_bid',
    'currentObservation.up_bid',
  ) ?? deepKey(unwrapped, ['up_bid', 'upBid'])

  const downBid = firstPath(
    unwrapped,
    'down_bid',
    'downBid',
    'observation.down_bid',
    'currentObservation.down_bid',
  ) ?? deepKey(unwrapped, ['down_bid', 'downBid'])

  return {
    ...unwrapped,
    marketId,
    seconds_left: secondsLeft,
    up_ask: upAsk,
    down_ask: downAsk,
    up_bid: upBid,
    down_bid: downBid,
  }
}

function sourceAgeFromCrossOracle(crossOracle: unknown): number | null {
  const sourceMs = finiteNumber(firstPath(
    crossOracle,
    'polymarket.up.quoteSourceTimestampMs',
    'polymarket.quoteFreshnessV3.upQuoteSourceTimestampMs',
  ))
  return sourceMs === null ? null : Math.max(0, Date.now() - sourceMs)
}

function quoteReceiptAgeFromCrossOracle(crossOracle: unknown): number | null {
  const receivedMs = finiteNumber(firstPath(
    crossOracle,
    'polymarket.up.quoteReceivedTimestampMs',
    'polymarket.quoteFreshnessV3.upQuoteReceivedTimestampMs',
  ))
  if (receivedMs !== null) return Math.max(0, Date.now() - receivedMs)
  return finiteNumber(firstPath(crossOracle, 'polymarket.ageMs'))
}

function normalizePolyGap(raw: unknown, realtime: unknown, crossOracle: unknown): unknown {
  const unwrapped = unwrapPayload(raw)
  if (!isRecord(unwrapped)) return unwrapped

  const market = isRecord(unwrapped.market) ? unwrapped.market : {}
  const poly = isRecord(unwrapped.poly) ? unwrapped.poly : {}
  const binance = isRecord(unwrapped.binance) ? unwrapped.binance : {}
  const activeRound = isRecord(unwrapped.activeRound) ? unwrapped.activeRound : null

  const marketId = finiteNumber(
    unwrapped.marketId
      ?? market.market_id
      ?? market.marketId
      ?? firstPath(realtime, 'marketId', 'market.market_id'),
  )

  const endMs = finiteNumber(market.end_ms ?? market.endMs)
  const secondsLeft = endMs === null
    ? finiteNumber(firstPath(realtime, 'seconds_left', 'secondsLeft'))
    : Math.max(0, (endMs - Date.now()) / 1000)

  const crossUpMid = finiteNumber(firstPath(crossOracle, 'polymarket.up.mid', 'polymarket.upMid'))
  const crossDownMid = finiteNumber(firstPath(crossOracle, 'polymarket.down.mid', 'polymarket.downMid'))
  const polyUpMid = finiteNumber(poly.upMid) ?? crossUpMid
  const polyDownMid = finiteNumber(poly.downMid) ?? crossDownMid ?? (
    polyUpMid === null ? null : Math.max(0, Math.min(1, 1 - polyUpMid))
  )

  const direction = String(poly.direction ?? unwrapped.direction ?? '').toUpperCase() || null
  const realtimeUpAsk = finiteNumber(firstPath(realtime, 'up_ask', 'upAsk'))
  const realtimeDownAsk = finiteNumber(firstPath(realtime, 'down_ask', 'downAsk'))
  const realtimeUpBid = finiteNumber(firstPath(realtime, 'up_bid', 'upBid'))
  const realtimeDownBid = finiteNumber(firstPath(realtime, 'down_bid', 'downBid'))
  const lastBinanceSide = String(binance.side ?? '').toUpperCase()
  const lastBinanceAsk = finiteNumber(binance.ask)

  const upAsk = realtimeUpAsk ?? (lastBinanceSide === 'UP' ? lastBinanceAsk : null)
  const downAsk = realtimeDownAsk ?? (lastBinanceSide === 'DOWN' ? lastBinanceAsk : null)
  const selectedMid = finiteNumber(poly.selectedMid) ?? (
    direction === 'UP' ? polyUpMid : direction === 'DOWN' ? polyDownMid : null
  )
  const selectedAsk = direction === 'UP' ? upAsk : direction === 'DOWN' ? downAsk : null
  const edge = finiteNumber(unwrapped.edge)
    ?? finiteNumber(activeRound?.entry_edge)
    ?? (selectedMid !== null && selectedAsk !== null ? selectedMid - selectedAsk : null)

  const sourceFreshness = isRecord(unwrapped.sourceFreshnessV41)
    ? unwrapped.sourceFreshnessV41
    : {}
  const currentFreshness = isRecord(sourceFreshness.current)
    ? sourceFreshness.current
    : {}
  const sourceAgeMs = finiteNumber(currentFreshness.sourceAgeMs)
    ?? finiteNumber(poly.signalSourceAgeMs)
    ?? sourceAgeFromCrossOracle(crossOracle)
  const quoteReceiptAgeMs = finiteNumber(currentFreshness.quoteReceiptAgeMs)
    ?? finiteNumber(poly.signalQuoteReceiptAgeMs)
    ?? quoteReceiptAgeFromCrossOracle(crossOracle)

  const activeState = String(activeRound?.state ?? '').toUpperCase()
  const hasPosition = activeRound !== null && ['ENTRY_SYNC', 'OPEN', 'EXIT_QUOTE', 'EXIT_SYNC'].includes(activeState)
  const position = hasPosition
    ? {
        side: activeRound?.side ?? null,
        shares: activeRound?.shares ?? null,
        entryPrice: activeRound?.entry_quote_average ?? activeRound?.entry_binance_ask ?? null,
        pnlUsdt: activeRound?.pnl_usdt ?? null,
        state: activeRound?.state ?? null,
        roundId: activeRound?.id ?? null,
        exitIntent: activeRound?.exit_intent ?? null,
      }
    : null

  return {
    ...unwrapped,
    marketId,
    secondsLeft,
    direction,
    edge,
    poly: {
      ...poly,
      upMid: polyUpMid,
      downMid: polyDownMid,
    },
    binanceBook: {
      up: { bid: realtimeUpBid, ask: upAsk },
      down: { bid: realtimeDownBid, ask: downAsk },
    },
    position,
    sourceFreshnessV41: {
      ...sourceFreshness,
      current: {
        ...currentFreshness,
        sourceAgeMs,
        quoteReceiptAgeMs,
      },
    },
  }
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
    const [rawRealtime, rawPolyGap, rawCrossOracle] = await Promise.all([
      readService('realtime'),
      readService('polyGap'),
      readService('crossOracle'),
    ])

    const realtimeData = rawRealtime.ok
      ? normalizeRealtime(rawRealtime.data)
      : previous.realtime.data
    const crossOracleData = rawCrossOracle.ok
      ? normalizeCrossOracle(rawCrossOracle.data)
      : previous.crossOracle.data
    const polyGapData = rawPolyGap.ok
      ? normalizePolyGap(rawPolyGap.data, realtimeData, crossOracleData)
      : previous.polyGap.data

    set({
      services: {
        realtime: rawRealtime.ok
          ? { ...rawRealtime, data: realtimeData }
          : { ...rawRealtime, data: previous.realtime.data },
        polyGap: rawPolyGap.ok
          ? { ...rawPolyGap, data: polyGapData }
          : { ...rawPolyGap, data: previous.polyGap.data },
        crossOracle: rawCrossOracle.ok
          ? { ...rawCrossOracle, data: crossOracleData }
          : { ...rawCrossOracle, data: previous.crossOracle.data },
      },
    })
  },
}))

export function getPath(input: unknown, ...paths: string[]): unknown {
  for (const path of paths) {
    const value = directPath(input, path)
    if (value !== null && value !== undefined) return value
  }
  return null
}

export function asNumber(value: unknown): number | null {
  return finiteNumber(value)
}

export function asText(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return fallback
}
