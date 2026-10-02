import { asNumber, asText, getPath, useDashboardStore } from './store'
import { useStrategyStore } from './strategy-store'

export type GenericRow = Record<string, unknown>

export function isRow(value: unknown): value is GenericRow {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

export function rows(value: unknown): GenericRow[] {
  return Array.isArray(value) ? value.filter(isRow) : []
}

export function nestedState(input: unknown) {
  return getPath(input, 'state') ?? input
}

export function fmtPrice(value: unknown, digits = 3) {
  const n = asNumber(value)
  return n === null ? '—' : n.toFixed(digits)
}

export function fmtMs(value: unknown, digits = 0) {
  const n = asNumber(value)
  return n === null ? '—' : `${n.toFixed(digits)} ms`
}

export function fmtMoney(value: unknown, digits = 3) {
  const n = asNumber(value)
  if (n === null) return '—'
  return `${n >= 0 ? '+' : '-'}$${Math.abs(n).toFixed(digits)}`
}

export function fmtPct(value: unknown, digits = 1) {
  const n = asNumber(value)
  return n === null ? '—' : `${(n * 100).toFixed(digits)}%`
}

export function fmtInt(value: unknown) {
  const n = asNumber(value)
  return n === null ? '—' : Math.round(n).toLocaleString()
}

export function fmtTime(value: unknown) {
  const n = asNumber(value)
  if (n === null || n <= 0) return '—'
  return new Date(n).toLocaleTimeString('zh-TW', { hour12: false })
}

export function statusColor(value: unknown) {
  const text = asText(value, '').toUpperCase()
  if (!text) return 'default'
  if (text.includes('ACTIVE') || text.includes('READY') || text.includes('ALLOW') || text.includes('LEADING') || text === 'LIVE' || text === 'OPEN') return 'success'
  if (text.includes('WAIT') || text.includes('CONFIRM') || text.includes('BUILD') || text.includes('SYNC')) return 'processing'
  if (text.includes('BLOCK') || text.includes('STALE') || text.includes('ERROR') || text.includes('DEGRADED') || text.includes('HALT') || text.includes('LOSS')) return 'error'
  if (text.includes('WATCH') || text.includes('PAUSE') || text.includes('UNKNOWN') || text.includes('INSUFFICIENT')) return 'warning'
  if (text.includes('CLOSED') || text.includes('SETTLED')) return 'blue'
  return 'default'
}

export function useDashboardModel() {
  const services = useDashboardStore((state) => state.services)
  const strategyService = useStrategyStore((state) => state.service)
  const realtime = services.realtime.data
  const polyGap = nestedState(services.polyGap.data)
  const crossOracle = nestedState(services.crossOracle.data)
  const multiMarket = nestedState(services.multiMarket.data)
  const strategies = nestedState(strategyService.data)

  const marketId = getPath(polyGap, 'marketId', 'market.marketId')
    ?? getPath(realtime, 'marketId', 'market.market_id', 'observation.market_id', 'currentObservation.market_id')
  const secondsLeft = getPath(polyGap, 'secondsLeft', 'market.secondsLeft')
    ?? getPath(realtime, 'seconds_left', 'observation.seconds_left', 'currentObservation.seconds_left')
  const polyUp = getPath(crossOracle, 'polymarket.up.mid', 'polymarket.upMid')
    ?? getPath(polyGap, 'poly.upMid')
  const polyDown = getPath(crossOracle, 'polymarket.down.mid', 'polymarket.downMid')
    ?? getPath(polyGap, 'poly.downMid')
  const binanceUpAsk = getPath(polyGap, 'binanceBook.up.ask', 'upAsk')
    ?? getPath(realtime, 'up_ask', 'observation.up_ask', 'currentObservation.up_ask')
  const binanceDownAsk = getPath(polyGap, 'binanceBook.down.ask', 'downAsk')
    ?? getPath(realtime, 'down_ask', 'observation.down_ask', 'currentObservation.down_ask')
  const sourceAge = getPath(polyGap, 'sourceFreshnessV41.current.sourceAgeMs', 'poly.signalSourceAgeMs')
  const receiptAge = getPath(polyGap, 'sourceFreshnessV41.current.quoteReceiptAgeMs', 'poly.signalQuoteReceiptAgeMs')
  const direction = getPath(polyGap, 'direction', 'poly.direction', 'signal.direction', 'candidate.direction', 'selectedSide')
  const edge = getPath(polyGap, 'edge', 'signal.edge', 'currentEdge', 'entryEdge')
  const status = getPath(polyGap, 'status', 'runtimeStatus')
  const version = getPath(polyGap, 'version')
  const positionSide = getPath(polyGap, 'position.side', 'activeRound.side')
  const shares = getPath(polyGap, 'position.shares', 'activeRound.shares')
  const entry = getPath(polyGap, 'position.entryPrice', 'activeRound.entry_quote_average', 'activeRound.entry_binance_ask')
  const pnl = getPath(polyGap, 'position.pnlUsdt', 'activeRound.pnl_usdt')

  return {
    services,
    strategyService,
    realtime,
    polyGap,
    crossOracle,
    multiMarket,
    strategies,
    marketId,
    secondsLeft,
    polyUp,
    polyDown,
    binanceUpAsk,
    binanceDownAsk,
    sourceAge,
    receiptAge,
    direction,
    edge,
    status,
    version,
    positionSide,
    shares,
    entry,
    pnl,
  }
}
