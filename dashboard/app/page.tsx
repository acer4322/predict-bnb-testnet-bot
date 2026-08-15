"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

type NumericConfig = Record<string, number | boolean>;
type StrategyId = "A" | "B" | "B2" | "C" | "D" | "E" | "F" | "E2" | "G" | "H" | "I" | "J" | "K" | "L" | "M"
  | "M0" | "M01" | "M01T180" | "M01T180D" | "M01TASYM" | "M01O" | "M01O_F1" | "M01O_LIVE" | "M01F" | "M01R" | "M0W" | "M01W" | "M1" | "M2" | "M3" | "M4" | "M5" | "M6" | "M7_1" | "M7_2" | "M7_3" | "M7_5"
  | "R_MICROPRICE" | "R_MICROPRICE_REVERSE" | "R_OFI" | "R_OFI_MIN040" | "R_OFI_EVENT_CUM" | "R_OFI_EVENT_CUM_FILTERED" | "R_FUTURES_LEAD" | "R_FUTURES_LEAD_CONTINUOUS_V2" | "R_FUTURES_LEAD_REVERSE" | "R_FUTURES_LEAD_REGIME_REVERSE_3L" | "R_FUTURES_LEAD_EXIT30" | "R_FUTURES_LEAD_DISTANCE" | "R_FUTURES_LEAD_EXIT30_DISTANCE" | "R_FUTURES_LEAD_SIGNAL_100" | "R_FUTURES_LEAD_MIN_ENTRY_020" | "R_FUTURES_LEAD_OBSERVER_F1" | "R_FUTURES_LEAD_OBSERVER_V2" | "R_FUTURES_LEAD_OBSERVER_V3" | "R_FUTURES_LEAD_OBSERVER_V4" | "R_FUTURES_LEAD_OBSERVER_V6" | "R_OFI_OBSERVER_V3" | "R_MICROPRICE_OBSERVER_V3" | "R_MICROPRICE_OBSERVER_V6" | "R_CALIBRATED_VALUE_OBSERVER_V6" | "R_MICROPRICE_OBSERVER_AUTO_V6" | "R_CALIBRATED_VALUE_OBSERVER_AUTO_V6" | "R_CALIBRATED_VALUE" | "R_CALIBRATED_VALUE_REVERSE" | "R_CALIBRATED_VALUE_CONTINUOUS_V2" | "R_CONSENSUS" | "R_CONFIRM_ADD_10";
type Observation = {
  timestamp: string; topic_id: number; market_id: number; title: string;
  start_price: number; spot_price: number; seconds_left: number;
  up_ask: number | null; up_bid: number | null; down_ask: number | null; down_bid: number | null;
  book_skew_ms?: number | null; book_age_ms?: number | null;
  up_book_timestamp_ms?: number | null; down_book_timestamp_ms?: number | null;
  observed_timestamp_ms?: number | null; collection_latency_ms?: number | null;
  spot_age_ms?: number | null; futures_price?: number | null; futures_timestamp_ms?: number | null; futures_age_ms?: number | null;
  futures_agg_trade_id?: number | null;
};
type Trade = {
  id: number; strategy: StrategyId; market_id: number; side: string; status: string;
  entry_price: number; target_price: number | null; exit_price: number | null;
  stake: number; shares: number; fees: number; pnl: number | null; opened_at: string; note: string;
  diagnostics_json?: string | null;
  diagnostics?: TradeDiagnostics | null;
};
type TradePage = {
  scope: string; storage: string; strategies: StrategyId[]; trades: Trade[];
  page: number; pageSize: number; total: number; totalPages: number;
};
type TradeDiagnostics = {
  signal_timestamp?: string | number | null; execution_timestamp?: string | number | null;
  requested_delay_seconds?: number | null; actual_delay_seconds?: number | null;
  execution_lag_seconds?: number | null;
};
type Summary = {
  trades: number; open: number; wins: number; losses: number; realized_pnl: number;
  currentWinStreak?: number; averageWinStreak?: number;
  currentLossStreak?: number; averageLossStreak?: number;
  averageWinLossCycleStreak?: number;
  resetAt?: string | null; reset_at?: string | null;
  carriedOpen?: number; totalOpen?: number; cutoffTradeId?: number | null;
};
type M0HourlyBucket = {
  hour: number; label: string; settledTrades: number; wins: number; losses: number;
  winRatePct: number | null;
  averageWinStreak: number | null; averageLossStreak: number | null;
  winThenLossCount: number; winThenLossOpportunities: number;
  winThenLossRatePct: number | null;
};
type M0HourlyPerformance = {
  timezone?: string; utcOffset?: string; basis?: string; resetAt?: string | null;
  streakBasis?: string; winThenLossBasis?: string;
  cutoffTradeId?: number | null; firstOpenedAt?: string | null; lastOpenedAt?: string | null;
  settledTrades?: number; hours?: M0HourlyBucket[];
};
type ResetState = { status: "loading" | "success" | "error"; message: string };
type StrategyView = "live-m0w" | "research" | "reliability-shadow" | "lead-observer" | "m-series" | "pair-arb" | "legacy" | "paused";
const TEMPORARILY_STOPPED_THRESHOLD_USDT = -500;
const TEMPORARILY_STOPPED_STRATEGIES: StrategyId[] = ["A", "C", "D", "J", "L", "M", "M2", "M4", "M5", "M6"];
const ALL_MX_SUFFIXES = ["T60", "T70", "T80", "T90", "T98", "P50", "P10", "REV"] as const;
type StrategyHState = {
  mode: string; reversalStreak: number; lossStreak: number;
  lastProcessedMarketId?: number | null; armedAt?: string | null; updatedAt?: string | null;
  pendingOfficialSettlements?: number; recentEvents?: unknown[];
};
type MicrostructureStream = {
  status?: string | null; eventRate?: number | null; lastEventAt?: string | null;
  transportLatencyMs?: number | null; latencyMs?: number | null; error?: string | null;
  marketId?: number | null; bookMapping?: string | null;
  bookVersionAgeMs?: number | null; localReceiptAgeMs?: number | null;
  orientationHealthy?: boolean | null; orientationTimedOut?: boolean | null;
  orientationStatus?: string | null;
  orientationUnverifiedAgeMs?: number | null; orientationFailureReason?: string | null;
  orientationAttempts?: number | null; orientationCandidateCount?: number | null;
  eligiblePredictionEvents?: number | null; unverifiedPredictionEvents?: number | null;
};
type MicrostructureMetrics = {
  spotPrice?: number | null; spotMicroprice?: number | null; spotQueueImbalance?: number | null;
  spotTakerImbalance250ms?: number | null; spotTakerImbalance1s?: number | null;
  futuresPrice?: number | null; futuresMicroprice?: number | null; futuresQueueImbalance?: number | null;
  futuresTakerImbalance250ms?: number | null; futuresTakerImbalance1s?: number | null;
  perpSpotBasisBps?: number | null; predictionUpMid?: number | null;
  predictionRemovalDirection?: string | number | null; predictionRemovalStrength?: number | null;
  volatilityAlert?: string | number | boolean | null; directionBias?: string | number | null;
};
type LiquidityEvent = {
  timestamp?: string | null; side?: string | null; kind?: string | null;
  strength?: number | null; price?: number | null; detail?: string | null;
};
type MicrostructureState = {
  status?: string | null;
  streams?: { spot_trade?: MicrostructureStream; spot_book?: MicrostructureStream; futures?: MicrostructureStream; prediction?: MicrostructureStream };
  metrics?: MicrostructureMetrics;
  recentLiquidityEvents?: LiquidityEvent[];
  predictionLiquidityEvents?: LiquidityEvent[];
  storage?: {
    events?: number | null; snapshots?: number | null; retentionHours?: number | null;
    dbBytes?: number | null; eventsRows?: number | null; snapshotsRows?: number | null;
    liquidityRows?: number | null; rawRetentionHours?: number | null; snapshotRetentionHours?: number | null;
    gapRows?: number | null; droppedEvents?: number | null; queueDepth?: number | null;
    writerStatus?: string | null; writerError?: string | null; writerLagMs?: number | null;
  };
};
type MRealtimeState = {
  status?: string | null; mode?: string | null; paperOnly?: boolean | null;
  marketId?: number | null; eventRate?: number | null; queueDepth?: number | null;
  processedEvents?: number | null; droppedEvents?: number | null; evaluations?: number | null;
  acceptedPredictionEvents?: number | null; rejectedUnverifiedPredictionEvents?: number | null;
  lastAcceptedPredictionAt?: string | number | null;
  lastEventAt?: string | number | null; lastDecisionAt?: string | number | null;
  lastQueueDelayMs?: number | null; lastDecisionDurationMs?: number | null;
  spotAgeMs?: number | null; futuresAgeMs?: number | null; predictionBookAgeMs?: number | null;
  spotTradeAgeMs?: number | null; spotBookAgeMs?: number | null;
  spotTradeIngressAgeMs?: number | null; spotTradeProcessedAgeMs?: number | null;
  spotPriceSource?: string | null;
  predictionStrategyBookAgeMs?: number | null; predictionRestReceiptAgeMs?: number | null;
  predictionExchangeContentAgeMs?: number | null; predictionLatestRestEligible?: boolean | null;
  schedulerTickMs?: number | null; m7EventReorderGraceMs?: number | null;
  m01oGateEnabled?: boolean; m01oMinObserverSamples?: number | null;
  m01oMinCurrentRangeScore?: number | null;
  m01oF2Enabled?: boolean; m01oF1Enabled?: boolean; m01oLiveEnabled?: boolean;
  marketDataIntegrityOk?: boolean | null; integritySkippedEvaluations?: number | null;
  rejectedTradeReplays?: number | null; outOfWindowSkips?: number | null;
  error?: string | null;
};
type MExitVariant = string;
type MExitSummary = {
  realizedPnl?: number | null; openPositions?: number | null;
  trades?: number | null; wins?: number | null; losses?: number | null;
  requestedEntryQty?: number | null; filledEntryQty?: number | null;
  requestedExitQty?: number | null; filledExitQty?: number | null;
  fullyUnfilledIntentRatio?: number | null; partialFillIntentRatio?: number | null;
  unfilledRatio?: number | null; partialFillRatio?: number | null;
  remainingShares?: number | null;
  entryFillRatio?: number | null; exitFillRatio?: number | null;
};
type MExitDetailRow = Record<string, unknown>;
type MExitExperimentState = {
  status?: string | null; updatedAt?: string | number | null;
  signalFamily?: "M" | "M0" | string;
  summaries?: Partial<Record<MExitVariant, MExitSummary>>;
  positions?: MExitDetailRow[]; fills?: MExitDetailRow[]; orders?: MExitDetailRow[];
};
type PairArbSummary = {
  strategyId?: string; enabled?: boolean; minimumNetEdge?: number;
  trades?: number; lockedPnl?: number; totalCost?: number; roi?: number | null;
  stressedPnl005?: number; stressedPnl010?: number; totalShares?: number;
  averageNetEdge?: number | null; averageSecondsLeft?: number | null;
  averageBookSkewMs?: number | null; averageBookAgeMs?: number | null;
  maxDrawdown?: number; eligibleMarkets?: number; pairFillRate?: number | null;
  quantityFillRate?: number | null; requestedShares?: number;
  filledShares?: number; partialFills?: number;
};
type PairArbTrade = {
  id: number; strategy: string; market_id: number; signal_timestamp: string;
  seconds_left: number; up_ask: number; down_ask: number; shares: number;
  locked_pnl: number; net_edge_per_share: number;
  requested_shares?: number; fill_ratio?: number; partial_fill?: number;
  up_fill_vwap?: number | null; down_fill_vwap?: number | null;
  up_levels_consumed?: number; down_levels_consumed?: number;
  stressed_pnl_005: number; stressed_pnl_010: number;
  book_skew_ms: number; book_age_ms: number;
};
type PairArbState = {
  status?: string; updatedAt?: string; paperOnly?: boolean;
  fillModel?: string; atomicExecutionAssumed?: boolean;
  sharedStakeUsdt?: number; maxBookSkewMs?: number; maxBookAgeMs?: number;
  summaries?: Partial<Record<"PAIR_ARB_010" | "PAIR_ARB_QC_015" | "PAIR_ARB_020" | "PAIR_ARB_RISK_020", PairArbSummary>>;
  diagnostics?: {
    source?: string; transport?: string; evaluations?: number;
    marketsEvaluated?: number; validBookEvaluations?: number;
    bestNetEdge?: number | null;
    eligibleSnapshots?: Partial<Record<"PAIR_ARB_010" | "PAIR_ARB_QC_015" | "PAIR_ARB_020" | "PAIR_ARB_RISK_020", number>>;
    rejectionCounts?: {
      invalidBook?: number; bookSkewExceeded?: number;
      bookAgeExceeded?: number; edgeBelow010?: number;
    };
    latestMarket?: {
      marketId?: number; evaluatedAt?: string; reason?: string;
      netEdge?: number | null; upAsk?: number | null; downAsk?: number | null;
      bookSkewMs?: number | null; bookAgeMs?: number | null;
    } | null;
  };
  recentTrades?: PairArbTrade[];
};
type LiveM0WOrder = {
  id: number; strategy: string; topic_id: number; market_id: number; side: string;
  signal_price: number; max_stake_usdt: number; requested_amount_wei: string;
  status: string; order_type: string; time_in_force: string; account_type: string;
  quote_average_price?: number | null; quote_amount_in_wei?: string | null;
  quote_amount_out_wei?: string | null; quote_expires_at?: number | null;
  order_id?: string | null; maker_usdt_amount?: number | null;
  filled_usdt_amount?: number | null; filled_share_qty?: number | null;
  fill_percentage?: number | null; market_provider_fee?: number | null;
  network_fee?: number | null; realized_pnl?: number | null;
  signal_at: string; attempted_at?: string | null; submitted_at?: string | null;
  updated_at: string; error_kind?: string | null; error_message?: string | null;
  settlement_status?: string | null; settlement_result?: "WIN" | "LOSS" | null;
  settlement_cost_usdt?: number | null; settlement_payout_usdt?: number | null;
  settlement_pnl_usdt?: number | null; settlement_roi_pct?: number | null;
  settled_at?: string | null;
};
type LiveM0WEvent = {
  id: number; timestamp: string; level: string; event_type: string;
  market_id?: number | null; message: string;
};
type LiveActivePosition = {
  order_local_id: number; strategy: string; topic_id: number; market_id: number;
  side: "UP" | "DOWN" | string; entry_status: string;
  quote_average_price?: number | null; quote_amount_in_wei?: string | null;
  filled_usdt_amount?: number | null; filled_share_qty?: number | null;
  market_provider_fee?: number | null; network_fee?: number | null;
  signal_at: string; updated_at: string;
  exit_id?: number | null; exit_status?: string | null;
  exit_order_type?: "LIMIT" | "MARKET" | null;
  exit_time_in_force?: string | null; exit_sell_shares?: number | null;
  exit_price_limit?: number | null; exit_order_id?: string | null;
  exit_error_kind?: string | null; exit_error_message?: string | null;
  exit_submitted_at?: string | null; exit_updated_at?: string | null;
};
type LiveRedeem = {
  id: number; token?: string; market_id?: number | null; topic_id?: number | null;
  chain_id?: string; outcome_name?: string; shares?: number; claimable_value?: number;
  end_date_ms?: number; status: string; discovered_at?: string; eligible_at?: string;
  attempted_at?: string | null; attempt_count?: number; batch_id?: string | null;
  request_id?: string | null; tx_hash?: string | null; exchange_status?: string | null;
  completed_at?: string | null; updated_at?: string; error_message?: string | null;
};
type ReliabilityCandidateTagId = "RC_LOW_ENTRY" | "MP_LATE_WINDOW" | "MP_FRESH_BOOK";
const RELIABILITY_CANDIDATE_TAG_IDS: ReliabilityCandidateTagId[] = [
  "RC_LOW_ENTRY", "MP_LATE_WINDOW", "MP_FRESH_BOOK",
];
type LiveRules = {
  strategy: string; strategies: string[]; maxStakeUsdt: number; strategyStakesUsdt: number[];
  minHourlyWinRatePct: number; maxHourlyWinThenLossRatePct: number;
  futuresLeadObserverEnabled: boolean;
  futuresLeadObserverVersion: "F1" | "V2" | "V3" | "V4" | "V6";
  strategyObserverEnabled: boolean[];
  strategyObserverVersions: Array<"F1" | "V2" | "V3" | "V4" | "V6">;
  strategyDrawdownControlEnabled: boolean[];
  strategyLossCooldownEnabled: boolean[];
  reliabilityGateTags: ReliabilityCandidateTagId[];
};
const DEFAULT_LIVE_RULES: LiveRules = {
  strategy: "M01O_F1", strategies: ["M01O_F1"], maxStakeUsdt: 1, strategyStakesUsdt: [1],
  minHourlyWinRatePct: 50, maxHourlyWinThenLossRatePct: 50,
  futuresLeadObserverEnabled: false, futuresLeadObserverVersion: "F1",
  strategyObserverEnabled: [false], strategyObserverVersions: ["F1"],
  strategyDrawdownControlEnabled: [false],
  strategyLossCooldownEnabled: [false],
  reliabilityGateTags: [],
};
const LIVE_TABLE_PAGE_SIZE = 10;
const LIVE_STRATEGY_SLOT_INDEXES = [0, 1, 2, 3] as const;
const LIVE_OBSERVER_SLOT_INDEXES = [0, 1, 2] as const;
const LIVE_STRATEGY_SLOT_NAMES = ["一", "二", "三", "四"] as const;
const EMPTY_OBSERVER_TRADE_PAGE: TradePage = {
  scope: "FUTURES_LEAD_OBSERVER",
  storage: "SQLITE_FULL_HISTORY",
  strategies: [],
  trades: [],
  page: 1,
  pageSize: LIVE_TABLE_PAGE_SIZE,
  total: 0,
  totalPages: 1,
};
const LIVE_RULE_DRAFT_STORAGE_KEY = "btc5m-live-rules-draft-v1";
function parseLiveRulesDraft(raw: string | null): LiveRules | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<LiveRules>;
    const strategy = typeof value.strategy === "string" ? value.strategy : "";
    const strategies = Array.isArray(value.strategies)
      ? value.strategies.filter((item): item is string => typeof item === "string").slice(0, 4)
      : strategy ? [strategy] : [];
    const maxStakeUsdt = Number(value.maxStakeUsdt);
    const strategyStakesUsdt = Array.isArray(value.strategyStakesUsdt)
      ? value.strategyStakesUsdt.map(Number).slice(0, strategies.length)
      : strategies.map(() => maxStakeUsdt);
    const minHourlyWinRatePct = Number(value.minHourlyWinRatePct);
    const maxHourlyWinThenLossRatePct = Number(value.maxHourlyWinThenLossRatePct);
    const futuresLeadObserverEnabled = value.futuresLeadObserverEnabled === true;
    const futuresLeadObserverVersion = (["F1", "V2", "V3", "V4", "V6"] as const).includes(value.futuresLeadObserverVersion as "F1" | "V2" | "V3" | "V4" | "V6") ? value.futuresLeadObserverVersion as "F1" | "V2" | "V3" | "V4" | "V6" : "F1";
    const strategyObserverEnabled = Array.isArray(value.strategyObserverEnabled)
      ? value.strategyObserverEnabled.slice(0, strategies.length).map(Boolean)
      : strategies.map(() => futuresLeadObserverEnabled);
    if (strategyObserverEnabled.length >= 4) strategyObserverEnabled[3] = false;
    const strategyObserverVersions = Array.isArray(value.strategyObserverVersions)
      ? value.strategyObserverVersions.slice(0, strategies.length).map(version => (["F1", "V2", "V3", "V4", "V6"] as const).includes(version) ? version : "F1")
      : strategies.map(() => futuresLeadObserverVersion);
    const strategyDrawdownControlEnabled = Array.isArray(value.strategyDrawdownControlEnabled)
      ? value.strategyDrawdownControlEnabled.slice(0, strategies.length).map(Boolean)
      : strategies.map(() => false);
    const strategyLossCooldownEnabled = Array.isArray(value.strategyLossCooldownEnabled)
      ? value.strategyLossCooldownEnabled.slice(0, strategies.length).map(Boolean)
      : strategies.map(() => false);
    const reliabilityGateTags = Array.isArray(value.reliabilityGateTags)
      ? value.reliabilityGateTags.filter((tag): tag is ReliabilityCandidateTagId => RELIABILITY_CANDIDATE_TAG_IDS.includes(tag as ReliabilityCandidateTagId))
      : [];
    if (!strategies.length || strategyStakesUsdt.length !== strategies.length || ![...strategyStakesUsdt, maxStakeUsdt, minHourlyWinRatePct, maxHourlyWinThenLossRatePct].every(Number.isFinite)) return null;
    if (strategyObserverEnabled.length !== strategies.length || strategyObserverVersions.length !== strategies.length || strategyDrawdownControlEnabled.length !== strategies.length || strategyLossCooldownEnabled.length !== strategies.length) return null;
    return { strategy: strategies[0], strategies, maxStakeUsdt: strategyStakesUsdt[0], strategyStakesUsdt, minHourlyWinRatePct, maxHourlyWinThenLossRatePct, futuresLeadObserverEnabled: strategyObserverEnabled[0], futuresLeadObserverVersion: strategyObserverVersions[0], strategyObserverEnabled, strategyObserverVersions, strategyDrawdownControlEnabled, strategyLossCooldownEnabled, reliabilityGateTags };
  } catch { return null; }
}
const LIVE_STRATEGY_LABELS: Record<string, string> = {
  R_FUTURES_LEAD_DISTANCE: "測試版 · 雙窗距離模型",
  R_FUTURES_LEAD_SIGNAL_100: "測試版 · Lead 強度 ≥ 1.00 bps",
  R_FUTURES_LEAD_MIN_ENTRY_020: "測試版 · 進場價 > 0.20",
  M: "M · 首次現貨偏離", M0: "M0 · 固定種子隨機", M01: "M01 · 隨機方向等 0.30",
  M01T180: "M01T180 · 剩餘 >180 秒等 0.30",
  M01O_F1: "F1 · Observer 判斷 M01 進場",
  M0W: "M0W · 上盤 M0 勝才跟單", M01W: "M01W · 上盤勝再等 0.30", M1: "M1 · 永遠 UP",
  M2: "M2 · 首次非零偏離", M3: "M3 · 至少 1 bps", M4: "M4 · 連續方向確認",
  M5: "M5 · 永續成交方向", M6: "M6 · 基差校正",
  M7_1: "M7_1 · 開盤 +1 秒", M7_2: "M7_2 · 開盤 +2 秒",
  M7_3: "M7_3 · 開盤 +3 秒", M7_5: "M7_5 · 開盤 +5 秒",
  PAIR_ARB_010: "互補 0.010 · UP＋DOWN 含費淨邊際",
  PAIR_ARB_QC_015: "簽名報價確認 0.015 · 等份額雙腿",
  PAIR_ARB_020: "互補 0.020 · UP＋DOWN 含費淨邊際",
  PAIR_ARB_RISK_020: "有限風險互補 · 含費最多 -0.020/share",
  R_FUTURES_LEAD: "研究實單 · 永續領先現貨",
  R_FUTURES_LEAD_CONTINUOUS_V2: "Shadow · Lead 持續校準 V2",
  R_FUTURES_LEAD_REVERSE: "研究實單 2 · 永續領先反向避險",
  R_FUTURES_LEAD_REGIME_REVERSE_3L: "研究實單 · Lead 連敗三筆正反切換",
  R_MICROPRICE: "研究實單 · Microprice 深度失衡",
  R_OFI: "研究實單 · 10 秒訂單流不平衡",
  R_CALIBRATED_VALUE: "研究實單 · 校準機率價值",
  R_CALIBRATED_VALUE_CONTINUOUS_V2: "Shadow · Value 持續校準 V2",
  R_OFI_EVENT_CUM: "研究實單 · 累積事件級 OFI",
};
const LIVE_OBSERVER_STRATEGIES = new Set([
  "R_FUTURES_LEAD",
  "R_FUTURES_LEAD_REVERSE",
  "R_FUTURES_LEAD_REGIME_REVERSE_3L",
  "R_FUTURES_LEAD_DISTANCE",
  "R_FUTURES_LEAD_SIGNAL_100",
  "R_FUTURES_LEAD_MIN_ENTRY_020",
  "R_MICROPRICE",
  "R_OFI",
  "R_CALIBRATED_VALUE",
]);
type LiveStrategyPerformance = {
  executedTrades?: number; settledTrades?: number; unsettledTrades?: number;
  wins?: number; losses?: number; winRatePct?: number | null;
  settledCostUsdt?: number; settledPayoutUsdt?: number;
  profitUsdt?: number; roiPct?: number | null;
};
type ReliabilityCohort = {
  samples?: number; settledSamples?: number; pendingSamples?: number;
  wins?: number; losses?: number; winRatePct?: number | null;
  costUsdt?: number; pnlUsdt?: number; returnOnCostPct?: number | null;
};
type LiveReliabilityTag = {
  id: string; strategy?: string; status?: ReliabilityShadowStatus;
  title?: string; condition?: string; matchDecision?: "ALLOW" | "BLOCK";
  activationAvailable?: boolean; enabledForLive?: boolean;
  original?: ReliabilityCohort; allowed?: ReliabilityCohort;
  blocked?: ReliabilityCohort; unavailable?: ReliabilityCohort;
  policyPnlUsdt?: number; deltaVsOriginalPnlUsdt?: number;
  avoidedOriginalPnlUsdt?: number;
};
type LiveReliabilityResearch = {
  source?: string; paperOrdersIncluded?: boolean; blockedOrRejectedOrdersIncluded?: boolean;
  copiedSamples?: number; settledSamples?: number; pendingSamples?: number;
  enabledLiveTags?: string[]; tags?: LiveReliabilityTag[];
  confirmationAdd?: LiveConfirmationAddResearch;
  recentSamples?: Array<{
    orderLocalId?: number; strategy?: string; marketId?: number; side?: string;
    executedEntryPrice?: number | null; secondsLeft?: number | null; bookAgeMs?: number | null;
    capturedAt?: string | null; settlementResult?: string | null;
    settlementCostUsdt?: number | null; settlementPnlUsdt?: number | null;
    settledAt?: string | null;
    tagDecisions?: Array<{ id?: string; conditionMatched?: boolean | null; decision?: string }>;
  }>;
};
type ConfirmationAddMetrics = {
  samples?: number; settledSamples?: number; officialSamples?: number; pendingSamples?: number;
  wins?: number; losses?: number; stakeUsdt?: number; feesUsdt?: number; pnlUsdt?: number;
  returnOnCostPct?: number | null; originalCostUsdt?: number; originalPnlUsdt?: number;
  hypotheticalStakeUsdt?: number; hypotheticalFeesUsdt?: number; hypotheticalCostUsdt?: number;
  hypotheticalPnlUsdt?: number; hypotheticalReturnOnCostPct?: number | null;
  deltaVsOriginalPnlUsdt?: number;
};
type ConfirmationAddRecent = {
  orderLocalId?: number; sourceTradeId?: number; shadowTradeId?: number;
  strategy?: string; sourceStrategy?: string; marketId?: number; side?: string;
  basePrice?: number; filledTranches?: number; stakeUsdt?: number; feesUsdt?: number;
  hypotheticalStakeUsdt?: number; hypotheticalFeesUsdt?: number;
  settlementResult?: string | null; result?: string | null; status?: string;
  originalPnlUsdt?: number | null; hypotheticalPnlUsdt?: number | null;
  deltaVsOriginalPnlUsdt?: number | null; pnlUsdt?: number | null;
  createdAt?: string; openedAt?: string; settledAt?: string | null; closedAt?: string | null;
};
type LiveConfirmationAddResearch = {
  status?: string; source?: string; paperOnly?: boolean; liveOrdersAffected?: boolean;
  historicalBackfill?: boolean; overall?: ConfirmationAddMetrics;
  byStrategy?: Record<string, ConfirmationAddMetrics>; recent?: ConfirmationAddRecent[];
};
type PaperConfirmationAddResearch = {
  strategy?: string; status?: string; paperOnly?: boolean; liveOrdersAffected?: boolean;
  sourceStrategies?: string[]; overall?: ConfirmationAddMetrics;
  bySource?: Record<string, ConfirmationAddMetrics>; recent?: ConfirmationAddRecent[];
};
type LiveM0WState = {
  status?: string | null; configuredEnabled?: boolean; runtimeEnabled?: boolean;
  armed?: boolean; realMoney?: boolean; strategy?: string; strategies?: string[]; maxStakeUsdt?: number; strategyStakesUsdt?: number[];
  orderType?: string; timeInForce?: string; accountType?: string;
  fundingSource?: string; credentialSource?: string; wallet?: string | null;
  sasStatus?: string; quoteAccess?: string; lastError?: string | null;
  lastPreflightAt?: string | null; lastOrderSyncAt?: string | null;
  lastSettlementSyncAt?: string | null;
  lastSignalAt?: string | null; lastSignalMarketId?: number | null;
  droppedSignals?: number; queueDepth?: number; updatedAt?: string;
  orderLatency?: {
    marketId?: number; strategy?: string; side?: string; outcome?: string;
    queueMs?: number; preQuoteMs?: number; quotePhaseMs?: number;
    quoteNetworkMs?: number; quoteToPlaceMs?: number | null;
    placeNetworkMs?: number | null; totalMs?: number; measuredAt?: string;
    eventToPlaceResponseMs?: number | null; marketEventToQuoteStartMs?: number | null;
    marketEventToPlaceStartMs?: number | null; decisionAndStoreMs?: number | null;
    storeMs?: number | null; acceptedLedgerMs?: number | null;
  } | null;
  lastOrderLatency?: LiveM0WState["orderLatency"];
  lastLocalPriceCheck?: Record<string, number | string | boolean | null> | null;
  lastDepthCheck?: Record<string, number | string | boolean | null> | null;
  lastQuoteAttempt?: Record<string, number | string | boolean | null> | null;
  drawdownReferenceSource?: string | null;
  drawdownReferenceAgeMs?: number | null;
  lastDrawdownReference?: Record<string, number | string | null> | null;
  attemptSummary?: {
    window?: number; sampleSize?: number;
    outcomes?: {
      submitted?: number; blockedStaleBook?: number; blockedLocalPriceMoved?: number;
      blockedInsufficientCapacity?: number; blockedEstimatedVwapTooHigh?: number;
      quoteRejected?: number; placementRejected?: number; placementAmbiguous?: number;
    };
    latency?: Record<string, { p50?: number | null; p90?: number | null; p95?: number | null; max?: number | null }>;
  };
  sqliteJournalMode?: string; sqliteSynchronous?: string; sqliteBusyTimeoutMs?: number;
  liveDbPath?: string; sqlitePathWarning?: string | null;
  rules?: LiveRules; supportedStrategies?: string[]; maxSelectableStrategies?: number;
  strategyLossCooldownStates?: Array<{
    strategy?: string; enabled?: boolean; consecutiveLosses?: number;
    cooldownPending?: boolean; lastResult?: "WIN" | "LOSS" | null;
    lastSettlementMarketId?: number | null; lastSkippedMarketId?: number | null;
    lastSkippedAt?: string | null; updatedAt?: string | null;
  }>;
  configurableStakeRangeUsdt?: { min?: number; max?: number };
  balances?: Array<{ accountType?: string; availableBalance?: number | null; enabled?: boolean }>;
  quota?: { dailyLimit?: number | null; remainingDailyLimit?: number | null };
  hourlyGuard?: {
    status?: string; blocked?: boolean; timezone?: string; utcOffset?: string;
    hour?: number; label?: string; evaluatedAt?: string;
    settledTrades?: number; wins?: number; losses?: number;
    winRatePct?: number | null; winThenLossCount?: number;
    winThenLossOpportunities?: number; winThenLossRatePct?: number | null;
    minWinRatePct?: number; maxWinThenLossRatePct?: number;
    reasons?: string[]; resetAt?: string | null;
  };
  portfolio?: {
    activePositionsCount?: number; totalRealizedPnl?: number | null;
    totalUnrealizedPnl?: number | null; totalPnl?: number | null;
    totalCostBasis?: number | null; totalCurrentValue?: number | null;
  };
  summary?: {
    signals?: number; submitted?: number; filledOrders?: number; partialOrders?: number;
    rejected?: number; ambiguous?: number; filledUsdt?: number;
    realizedPnl?: number; fees?: number;
  };
  performance?: LiveStrategyPerformance;
  performances?: Record<string, LiveStrategyPerformance>;
  autoRedeem?: {
    enabled?: boolean; status?: string; delaySeconds?: number; scanIntervalSeconds?: number;
    claimableCount?: number; claimableAmount?: number; lastScanAt?: string | null;
    lastSuccessAt?: string | null; lastError?: string | null;
    summary?: {
      discovered?: number; ready?: number; pending?: number; completed?: number;
      failed?: number; ambiguous?: number; redeemedValue?: number;
    };
    redeems?: LiveRedeem[];
  };
  orders?: LiveM0WOrder[]; events?: LiveM0WEvent[];
  activePositions?: LiveActivePosition[];
  reliabilityResearch?: LiveReliabilityResearch;
  policy?: {
    oneAttemptPerMarket?: boolean; oneAttemptPerStrategyPerMarket?: boolean;
    retryAmbiguousPlacement?: boolean;
    marketOrderDisabled?: boolean; autoRedeemDelaySeconds?: number;
    pairQc015?: {
      minimumLockedPnlUsdt?: number; minimumLockedRoiPct?: number;
      minimumQuoteCapacityPct?: number; maximumNetShareMismatchPct?: number;
      minimumQuoteExpiryMs?: number; maximumQuoteRttMs?: number;
      networkAndRoundingBufferUsdt?: number; equalShareRequote?: boolean;
      shadowOnlyWhilePaused?: boolean; minimumShadowSamples?: number;
      shadowSamples?: number; liveReady?: boolean;
      placementBlockedUntilShadowReady?: boolean;
    };
    retryAmbiguousRedeem?: boolean; reason?: string;
  };
};
const EMPTY_SUMMARY: Summary = { trades: 0, open: 0, wins: 0, losses: 0, realized_pnl: 0 };
type MarketObserverMetrics = {
  winner_touch_rate?: number | null; both_sides_touch_rate?: number | null;
  conditional_fill_win_rate?: number | null; m01_conditional_win_rate?: number | null;
  m01_average_fill_price?: number | null; m01_break_even_win_rate?: number | null;
  m01_edge?: number | null; m01_settled_fill_sample_count?: number;
  cost_status?: string; m01_cost_excluded?: boolean;
  average_effective_crossovers?: number | null; avg_effective_crossover_count?: number | null;
  median_er_60s?: number | null; early_er_60s_median?: number | null;
  p75_er_60s_median?: number | null; range_score?: number; trend_score?: number;
  reasons?: string[]; conflicts?: string[]; provisional?: boolean;
  sample_count?: number; sample_size?: number; rolling_round_limit?: number;
};

type MarketObserverGate = {
  allowed?: boolean; status?: string; reason?: string;
  profile?: "F2" | "F1" | "LIVE" | string; blockCategory?: string;
  paperOnly?: boolean; liveOrdersAffected?: boolean;
  historicalState?: string; historicalSampleCount?: number;
  historicalProvisional?: boolean; historicalRangeScore?: number; historicalTrendScore?: number;
  currentMarketId?: number | null; currentRangeScore?: number;
  currentBothSidesTouched?: boolean; currentEffectiveCrossovers?: number;
  currentMedianEr60s?: number | null; currentTrendVeto?: boolean;
  currentShortEr?: number | null; currentShortErWindowSeconds?: number;
  currentPhase?: string; currentMarketAgeSeconds?: number | null;
  validEr60sObservations?: number; er60sObservationSpanSeconds?: number;
  trendVetoReady?: boolean; spotAgeSeconds?: number | null;
  bookAgeSeconds?: number | null; dataQualityStatus?: string;
  indicatorReadiness?: string;
  currentEvidence?: string[]; blockers?: string[];
  minSettledSamples?: number; minCurrentRangeScore?: number;
};

type MarketObserverState = {
  status?: string; observeOnly?: boolean; paperGateOnly?: boolean;
  liveOrdersAffected?: boolean; paperStrategyConsumers?: string[];
  state?: "RANGE" | "UNCERTAIN" | "TREND" | string; reason?: string;
  provisional?: boolean; sampleCount?: number; rollingRoundLimit?: number;
  touchThreshold?: number; crossoverDeadbandBps?: number;
  winnerTouchRate?: number | null; bothSidesTouchRate?: number | null;
  averageEffectiveCrossovers?: number | null; medianEr60s?: number | null;
  earlyEr60sMedian?: number | null; p75Er60sMedian?: number | null;
  rangeScore?: number; trendScore?: number; reasons?: string[]; conflicts?: string[];
  m01ConditionalWinRate?: number | null; m01AverageFillPrice?: number | null;
  m01BreakEvenWinRate?: number | null; m01Edge?: number | null;
  m01SettledFillSampleCount?: number; costStatus?: string;
  m01oGate?: MarketObserverGate;
  m01oGates?: Partial<Record<"F2" | "F1" | "LIVE", MarketObserverGate>>;
  metrics?: MarketObserverMetrics;
  currentRound?: {
    marketId?: number | null; startPrice?: number | null;
    crossoverCount?: number; effectiveCrossoverCount?: number;
    upMinAsk?: number | null; downMinAsk?: number | null;
    upTouched?: boolean; downTouched?: boolean; bothTouched?: boolean;
    currentEr60s?: number | null; currentMedianEr60s?: number | null;
    earlyEr60s?: number | null;
  };
  roundsHistory?: unknown[];
};

type M01OFilterGroup = {
  strategyId?: StrategyId; profile?: string; label?: string;
  resetAt?: string | null; candidateMarkets?: number; settledCandidateMarkets?: number;
  allowedMarkets?: number; openedTrades?: number; tradeCoverageRate?: number | null;
  actualFillRate?: number | null; settledTrades?: number; wins?: number; losses?: number;
  winRate?: number | null; averageEntryPrice?: number | null; averagePnl?: number | null;
  realizedPnl?: number; grossProfit?: number; grossLoss?: number;
  profitFactor?: number | null; profitFactorInfinite?: boolean; maxDrawdown?: number;
  observedDays?: number; zeroTradeDays?: number; zeroTradeDayRate?: number | null;
  blockedCounterfactualSettled?: number; blockedCounterfactualWins?: number;
  blockedCounterfactualLosses?: number; blockedCounterfactualWinRate?: number | null;
  blockedCounterfactualPnl?: number; blockCategories?: Record<string, number>;
};
type M01OFilterExperimentState = {
  status?: string; paperOnly?: boolean; liveOrdersAffected?: boolean;
  candidateBasis?: string; costBasis?: string;
  sampleTarget?: { minimum?: number; preferred?: number };
  groups?: Partial<Record<"F2" | "F1" | "LIVE", M01OFilterGroup>>;
};

type ResearchStrategyState = {
  enabled?: boolean; stakeUsdt?: number;
  selectedBacktestParameters?: Record<string, number>;
  directionControl?: {
    configuredMode?: "AUTO" | "FORWARD" | "REVERSE";
    configuredModeCode?: number;
    automaticDirection?: "FORWARD" | "REVERSE";
    effectiveDirection?: "FORWARD" | "REVERSE";
    manualOverride?: boolean;
    historyReady?: boolean;
    recentLeadResults?: string[];
    recentLeadMarketIds?: number[];
    rule?: string;
    appliesToPaperAndLive?: boolean;
  };
  chronologicalValidation?: {
    status?: string; samples?: number; minimum?: number; preferred?: number;
    splitRule?: string; thresholdsFrozen?: boolean;
    groups?: Record<string, { target?: number; samples?: number; settled?: number; wins?: number; losses?: number; realizedPnl?: number }>;
  } | null;
  continuousCalibration?: {
    status?: string; sourceStrategy?: string; officialSourceSamples?: number;
    officialSourceWins?: number; minimumHistory?: number; minimumBucketHistory?: number;
    historyWindow?: number; priorStrength?: number; minimumEdge?: number;
    officialOnly?: boolean; causalNextMarketOnly?: boolean;
  };
  observerAutoV6?: {
    allowed?: boolean; status?: string; mode?: "APPLY_V6" | "BYPASS_V6";
    reason?: string; paperOnly?: boolean; liveOrdersAffected?: boolean;
    officialHistoryOnly?: boolean; currentMarketExcluded?: boolean;
    sourceStrategy?: string;
    fastWindow?: { samples?: number; allowedSamples?: number; blockedSamples?: number; passRatePct?: number | null; sourceUnitPnl?: number; allowedUnitPnl?: number; blockedUnitPnl?: number };
    slowWindow?: { samples?: number; allowedSamples?: number; blockedSamples?: number; passRatePct?: number | null; sourceUnitPnl?: number; allowedUnitPnl?: number; blockedUnitPnl?: number };
  };
};
type ResearchForwardState = {
  status?: string; paperOnly?: boolean; liveOrdersAffected?: boolean;
  strategies?: Partial<Record<StrategyId, ResearchStrategyState>>;
  minimumStakeUsdt?: number; minimumBasis?: string;
  sharedCapitalCapUsdt?: number; openExposureUsdt?: number; availableExposureUsdt?: number;
  shadowOpenExposureUsdt?: number; shadowCapitalModel?: string;
  confirmationAdd?: PaperConfirmationAddResearch;
  execution?: {
    actualPredictionTopOfBook?: boolean; fullFirstLevelDepthRequired?: boolean;
    partialFillsAllowed?: boolean; slippageBps?: number; maxSpread?: number;
    maxBookAgeMs?: number; maxBookSkewMs?: number;
  };
};

type State = {
  connection: { status: string; error: string | null; updatedAt: string | null; intervalSeconds?: number; rateLimits?: Record<string, string> };
  config: NumericConfig; latest: Observation | null; history: Observation[];
  trades: Trade[]; summaries: Record<StrategyId, Summary>; strategyH?: StrategyHState;
  microstructure?: MicrostructureState | null;
  mRealtime?: MRealtimeState | null;
  marketObserver?: MarketObserverState | null;
  m01oFilterExperiment?: M01OFilterExperimentState | null;
  researchForward?: ResearchForwardState | null;
  mExitExperiment?: MExitExperimentState | null;
  m0ExitExperiment?: MExitExperimentState | null;
  pairArbExperiment?: PairArbState | null;
  m0HourlyPerformance?: M0HourlyPerformance | null;
  liveM0W?: LiveM0WState | null;
};

type RealtimeState = Pick<State, "connection" | "latest" | "microstructure" | "mRealtime" | "marketObserver"> & {
  generatedAt?: string;
};

const REALTIME_REFRESH_MS = 1000;
const STATISTICS_REFRESH_MS = 15000;
const MEMORY_RECYCLE_MS = 30 * 60 * 1000;
const DASHBOARD_SESSION_STORAGE_KEY = "btc-5m-dashboard-session-v1";

type DashboardSessionState = {
  strategyView: StrategyView;
  observerTradePageNumber: number;
  scrollY: number;
};

function parseDashboardSession(raw: string | null): DashboardSessionState | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<DashboardSessionState>;
    const validViews: StrategyView[] = ["live-m0w", "research", "reliability-shadow", "lead-observer", "m-series", "pair-arb", "legacy", "paused"];
    if (!parsed.strategyView || !validViews.includes(parsed.strategyView)) return null;
    return {
      strategyView: parsed.strategyView,
      observerTradePageNumber: Math.max(1, Math.floor(Number(parsed.observerTradePageNumber) || 1)),
      scrollY: Math.max(0, Number(parsed.scrollY) || 0),
    };
  } catch {
    return null;
  }
}

async function fetchDashboardJson<T>(path: string, pending: Set<AbortController>): Promise<T> {
  const controller = new AbortController();
  pending.add(controller);
  try {
    const response = await fetch(apiUrl(path), { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new Error(`dashboard request failed: ${response.status}`);
    return await response.json() as T;
  } finally {
    pending.delete(controller);
  }
}

function abortPendingRequests(pending: Set<AbortController>) {
  pending.forEach(controller => controller.abort());
  pending.clear();
}

const initial: State = {
  connection: { status: "CONNECTING", error: null, updatedAt: null },
  config: {}, latest: null, history: [], trades: [],
  summaries: {
    A: { ...EMPTY_SUMMARY }, B: { ...EMPTY_SUMMARY }, B2: { ...EMPTY_SUMMARY }, C: { ...EMPTY_SUMMARY },
    D: { ...EMPTY_SUMMARY }, E: { ...EMPTY_SUMMARY }, F: { ...EMPTY_SUMMARY },
    E2: { ...EMPTY_SUMMARY }, G: { ...EMPTY_SUMMARY }, H: { ...EMPTY_SUMMARY },
    I: { ...EMPTY_SUMMARY }, J: { ...EMPTY_SUMMARY }, K: { ...EMPTY_SUMMARY }, L: { ...EMPTY_SUMMARY },
    M: { ...EMPTY_SUMMARY }, M0: { ...EMPTY_SUMMARY }, M01: { ...EMPTY_SUMMARY }, M01T180: { ...EMPTY_SUMMARY }, M01T180D: { ...EMPTY_SUMMARY }, M01TASYM: { ...EMPTY_SUMMARY }, M01O: { ...EMPTY_SUMMARY }, M01O_F1: { ...EMPTY_SUMMARY }, M01O_LIVE: { ...EMPTY_SUMMARY }, M01F: { ...EMPTY_SUMMARY }, M01R: { ...EMPTY_SUMMARY },
    M0W: { ...EMPTY_SUMMARY }, M01W: { ...EMPTY_SUMMARY }, M1: { ...EMPTY_SUMMARY },
    M2: { ...EMPTY_SUMMARY }, M3: { ...EMPTY_SUMMARY }, M4: { ...EMPTY_SUMMARY },
    M5: { ...EMPTY_SUMMARY }, M6: { ...EMPTY_SUMMARY },
    M7_1: { ...EMPTY_SUMMARY }, M7_2: { ...EMPTY_SUMMARY },
    M7_3: { ...EMPTY_SUMMARY }, M7_5: { ...EMPTY_SUMMARY },
    R_MICROPRICE: { ...EMPTY_SUMMARY }, R_OFI: { ...EMPTY_SUMMARY },
    R_OFI_MIN040: { ...EMPTY_SUMMARY }, R_OFI_EVENT_CUM: { ...EMPTY_SUMMARY }, R_OFI_EVENT_CUM_FILTERED: { ...EMPTY_SUMMARY },
    R_FUTURES_LEAD: { ...EMPTY_SUMMARY }, R_FUTURES_LEAD_CONTINUOUS_V2: { ...EMPTY_SUMMARY }, R_FUTURES_LEAD_REVERSE: { ...EMPTY_SUMMARY }, R_FUTURES_LEAD_REGIME_REVERSE_3L: { ...EMPTY_SUMMARY },
    R_FUTURES_LEAD_EXIT30: { ...EMPTY_SUMMARY }, R_FUTURES_LEAD_DISTANCE: { ...EMPTY_SUMMARY }, R_FUTURES_LEAD_EXIT30_DISTANCE: { ...EMPTY_SUMMARY },
    R_FUTURES_LEAD_SIGNAL_100: { ...EMPTY_SUMMARY }, R_FUTURES_LEAD_MIN_ENTRY_020: { ...EMPTY_SUMMARY },
    R_FUTURES_LEAD_OBSERVER_F1: { ...EMPTY_SUMMARY }, R_FUTURES_LEAD_OBSERVER_V2: { ...EMPTY_SUMMARY },
    R_FUTURES_LEAD_OBSERVER_V3: { ...EMPTY_SUMMARY }, R_FUTURES_LEAD_OBSERVER_V4: { ...EMPTY_SUMMARY }, R_FUTURES_LEAD_OBSERVER_V6: { ...EMPTY_SUMMARY },
    R_OFI_OBSERVER_V3: { ...EMPTY_SUMMARY }, R_MICROPRICE_OBSERVER_V3: { ...EMPTY_SUMMARY }, R_MICROPRICE_OBSERVER_V6: { ...EMPTY_SUMMARY }, R_CALIBRATED_VALUE_OBSERVER_V6: { ...EMPTY_SUMMARY },
    R_MICROPRICE_OBSERVER_AUTO_V6: { ...EMPTY_SUMMARY }, R_CALIBRATED_VALUE_OBSERVER_AUTO_V6: { ...EMPTY_SUMMARY },
    R_CALIBRATED_VALUE: { ...EMPTY_SUMMARY }, R_CALIBRATED_VALUE_CONTINUOUS_V2: { ...EMPTY_SUMMARY },
    R_CONSENSUS: { ...EMPTY_SUMMARY },
    R_CONFIRM_ADD_10: { ...EMPTY_SUMMARY },
  },
};

function mergeObservationHistory(history: Observation[], latest: Observation | null) {
  if (!latest) return history;
  const withoutDuplicate = history.filter(row => !(
    row.market_id === latest.market_id && row.timestamp === latest.timestamp
  ));
  return [...withoutDuplicate, latest].slice(-90);
}

function money(value: number | null | undefined, digits = 2) {
  return value == null ? "—" : `$${value.toFixed(digits)}`;
}
function price(value: number | null | undefined) {
  return value == null ? "—" : value.toFixed(3);
}
function clock(seconds: number | undefined) {
  const safe = Math.max(0, Math.round(seconds ?? 0));
  return `${String(Math.floor(safe / 60)).padStart(2, "0")}:${String(safe % 60).padStart(2, "0")}`;
}
function fmtTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-TW", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(value));
}
function fmtTimeMs(value: string | number | null | undefined) {
  if (value == null || value === "") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-TW", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3, hour12: false,
  }).format(date);
}
function fmtDateTime(value: string | null | undefined) {
  if (!value) return "全部歷史";
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date(value));
}
function tradeDiagnostics(trade: Trade): TradeDiagnostics | null {
  if (trade.diagnostics && typeof trade.diagnostics === "object") return trade.diagnostics;
  if (!trade.diagnostics_json) return null;
  try {
    const parsed = JSON.parse(trade.diagnostics_json) as unknown;
    return parsed && typeof parsed === "object" ? parsed as TradeDiagnostics : null;
  } catch {
    return null;
  }
}
function finite(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function decimal(value: number | null | undefined, digits = 2) {
  const safe = finite(value);
  return safe == null ? "—" : safe.toFixed(digits);
}
function signed(value: number | null | undefined, digits = 2, suffix = "") {
  const safe = finite(value);
  return safe == null ? "—" : `${safe > 0 ? "+" : ""}${safe.toFixed(digits)}${suffix}`;
}
function imbalance(value: number | null | undefined) {
  const safe = finite(value);
  return safe == null ? "—" : `${safe > 0 ? "+" : ""}${(safe * 100).toFixed(1)}%`;
}
function compact(value: number | null | undefined) {
  const safe = finite(value);
  return safe == null ? "—" : new Intl.NumberFormat("zh-TW", { notation: "compact", maximumFractionDigits: 1 }).format(safe);
}
function bytes(value: number | null | undefined) {
  const safe = finite(value);
  if (safe == null) return "—";
  if (safe < 1024) return `${safe.toFixed(0)} B`;
  if (safe < 1024 ** 2) return `${(safe / 1024).toFixed(1)} KB`;
  if (safe < 1024 ** 3) return `${(safe / 1024 ** 2).toFixed(1)} MB`;
  return `${(safe / 1024 ** 3).toFixed(1)} GB`;
}

function OptionalPanel({
  title,
  detail,
  children,
}: {
  title: string;
  detail: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return <section className="optional-panel">
    <div>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {open ? "隱藏" : "顯示"}
    </button>
    {open && <div className="optional-panel-content">{children}</div>}
  </section>;
}

function ActiveLivePosition({
  position,
  latest,
  sellState,
  onSell,
}: {
  position?: LiveActivePosition;
  latest?: Observation;
  sellState: { orderId: number | null; status: "idle" | "loading" | "success" | "error"; message: string };
  onSell: (position: LiveActivePosition, orderType: "LIMIT" | "MARKET") => void;
}) {
  const shares = position ? finite(position.filled_share_qty) ?? 0 : null;
  const quoteCost = position?.quote_amount_in_wei
    ? Number(position.quote_amount_in_wei) / 1e18
    : null;
  const displayCost = finite(position?.filled_usdt_amount);
  const positiveCosts = [quoteCost, displayCost].filter(
    (value): value is number => value != null && Number.isFinite(value) && value > 0,
  );
  const cost = positiveCosts.length ? Math.min(...positiveCosts) : null;
  const bid = position?.side === "UP" ? finite(latest?.up_bid) : position ? finite(latest?.down_bid) : null;
  const currentValue = bid == null || shares == null ? null : shares * bid;
  const pnl = currentValue == null || cost == null ? null : currentValue - cost - (finite(position?.network_fee) ?? 0);
  const roi = pnl == null || cost == null || cost <= 0 ? null : pnl / cost * 100;
  const pendingExit = Boolean(position?.exit_status && !/REJECTED|FAILED|CANCELED|CANCELLED/.test(position.exit_status));
  const busy = Boolean(position && sellState.status === "loading" && sellState.orderId === position.order_local_id);
  const canSell = Boolean(position?.entry_status === "FILLED" && !pendingExit && !busy);
  const statusText = !position
    ? "等待成交"
    : pendingExit
    ? `賣單 ${position.exit_order_type ?? ""} · ${position.exit_status}`
    : position.exit_error_message
      ? `上次賣出未成立：${position.exit_error_message}`
      : "持倉執行中";

  return <section className={`active-live-position ${position ? "" : "empty"}`} aria-label="目前實單持倉">
    <div className="active-live-position-head">
      <div><span className="live-real-badge">CURRENT REAL POSITION</span><strong>{position ? `${position.strategy} · ${position.side}` : "目前沒有持倉"}</strong><small>Market #{position?.market_id ?? latest?.market_id ?? "—"}{position ? ` · ${decimal(shares, 8)} shares` : " · 本市場目前沒有已成交實單"}</small></div>
      <span className={`active-position-status ${pendingExit ? "pending" : ""}`}>{statusText}</span>
    </div>
    <div className="active-live-position-grid">
      <div><span>投入成本</span><strong>{money(cost, 6)}</strong><small>實際成交成本</small></div>
      <div><span>目前買一</span><strong>{price(bid)}</strong><small>{position ? `${position.side} 即時 bid` : "等待持倉方向"}</small></div>
      <div><span>目前可售價值</span><strong>{money(currentValue, 6)}</strong><small>shares × 目前買一</small></div>
      <div><span>當前收益</span><strong className={(pnl ?? 0) >= 0 ? "positive" : "negative"}>{money(pnl, 6)}</strong><small>ROI {signed(roi, 2, "%")} · 未扣本次賣出費用</small></div>
    </div>
    <div className="active-live-position-actions">
      <p>現價賣出會用目前最佳買價送 LIMIT GTC；市場價賣出會用 MARKET FOK，成交價可能因深度與滑點低於畫面價格。同一區域網路內由本機面板網址開啟的裝置也可操作，公網與不相符來源仍會被拒絕。</p>
      <div>
        <button type="button" className="limit-sell" disabled={!canSell || bid == null} onClick={() => position && onSell(position, "LIMIT")}>{busy ? "送出中…" : "現價 LIMIT 賣出"}</button>
        <button type="button" className="market-sell" disabled={!canSell} onClick={() => position && onSell(position, "MARKET")}>{busy ? "送出中…" : "MARKET 市場價賣出"}</button>
      </div>
    </div>
    {!position && <p className="active-live-position-message">任一實單策略成交後，這裡會自動顯示投入成本、即時買一、目前可售價值與當前收益，並啟用兩種賣出按鈕。</p>}
    {position && sellState.orderId === position.order_local_id && sellState.message && <p className={`active-live-position-message ${sellState.status}`}>{sellState.message}</p>}
  </section>;
}

function directionTone(value: string | number | null | undefined) {
  if (typeof value === "number") return value > 0 ? "positive" : value < 0 ? "negative" : "";
  const normalized = String(value ?? "").toUpperCase();
  if (/UP|BUY|BULL|POSITIVE|LONG/.test(normalized)) return "positive";
  if (/DOWN|SELL|BEAR|NEGATIVE|SHORT/.test(normalized)) return "negative";
  return "";
}

function streamIsLive(status: string | null | undefined) {
  return /LIVE|CONNECTED|RUNNING|OPEN|HEALTHY/i.test(status ?? "");
}

function volatilityLabel(value: MicrostructureMetrics["volatilityAlert"]) {
  if (value == null) return "等待資料";
  if (typeof value === "boolean") return value ? "波動警戒" : "平穩";
  if (typeof value === "number") return `${Math.max(0, value * 100).toFixed(0)}%`;
  return value;
}

function biasLabel(value: MicrostructureMetrics["directionBias"]) {
  if (typeof value === "number") return imbalance(value);
  return value == null || value === "" ? "中性 / 資料不足" : String(value);
}

function MicrostructureMonitor({ data }: { data?: MicrostructureState | null }) {
  const metrics = data?.metrics ?? {};
  const streams = data?.streams ?? {};
  const rawEvents = Array.isArray(data?.recentLiquidityEvents)
    ? data.recentLiquidityEvents
    : Array.isArray(data?.predictionLiquidityEvents) ? data.predictionLiquidityEvents : [];
  const events = rawEvents.slice(0, 8);
  const streamCards = [
    { key: "spot_trade", title: "Binance Spot trade", stream: streams.spot_trade },
    { key: "spot_book", title: "Binance Spot book/depth", stream: streams.spot_book },
    { key: "futures", title: "USDT 永續", stream: streams.futures },
    { key: "prediction", title: "Prediction 訂單簿", stream: streams.prediction },
  ];
  const alert = metrics.volatilityAlert;
  const alertActive = alert === true || (typeof alert === "number" && alert > 0) || (typeof alert === "string" && /ALERT|HIGH|SPIKE|警戒|劇烈/i.test(alert));
  const monitorLive = data?.status === "LIVE";

  return <section className="micro-panel" aria-label="微結構即時觀測器">
    <div className="micro-header">
      <div>
        <span className="eyebrow">MARKET MICROSTRUCTURE · READ ONLY</span>
        <div className="micro-title-row"><h2>微結構即時觀測器</h2><span className="observe-only">觀測，不下單</span></div>
        <p>API 有提供時，以毫秒 timestamp／latency 欄位記錄收到的事件；各來源更新頻率不同，不代表上游行情為 1 ms。前端仍每 1 秒刷新一次。</p>
      </div>
      <span className={`micro-status ${monitorLive ? "live" : ""}`}><i />{data?.status || (monitorLive ? "STREAMING" : "等待啟動")}</span>
    </div>

    <div className="micro-stream-grid">
      {streamCards.map(({ key, title, stream }) => {
        const correctedLatency = finite(stream?.transportLatencyMs);
        const predictionReceiptAge = finite(stream?.localReceiptAgeMs);
        const shownLatency = key === "prediction"
          ? predictionReceiptAge
          : correctedLatency ?? finite(stream?.latencyMs);
        const latencyLabel = key === "prediction"
          ? "本機收件 age"
          : correctedLatency == null ? "表觀延遲" : "校正延遲";
        return <article className="stream-card" key={key}>
          <div className="stream-card-head"><strong>{title}</strong><span className={streamIsLive(stream?.status) ? "stream-live" : ""}>{stream?.status || "STANDBY"}</span></div>
          <div className="stream-stats">
            <div><span>事件率</span><b>{decimal(stream?.eventRate, 1)} <small>event/s</small></b></div>
            <div><span>{latencyLabel}</span><b>{decimal(shownLatency, 0)} <small>ms</small></b></div>
          </div>
          <p className={stream?.error || (key === "prediction" && stream?.orientationHealthy === false) ? "stream-error" : ""}>{stream?.error || `最後事件 ${fmtTime(stream?.lastEventAt)}${key === "prediction" && stream?.orientationStatus ? ` · orientation ${stream.orientationStatus}` : ""}${key === "prediction" && stream?.bookMapping ? ` · ${stream.bookMapping}` : ""}${key === "prediction" && stream?.orientationFailureReason ? ` · ${stream.orientationFailureReason}` : ""}`}</p>
          {key === "prediction" && <p>Book version age {metric(stream?.bookVersionAgeMs, " ms")} · transport latency 無可信 server-send timestamp，固定顯示空值</p>}
        </article>;
      })}
    </div>

    <div className="micro-measure-grid">
      <article className="measure-group">
        <div className="measure-heading"><div><span>SPOT</span><strong>{money(metrics.spotPrice)}</strong></div><small>micro {money(metrics.spotMicroprice)}</small></div>
        <div className="measure-values">
          <div><span>Queue imbalance</span><b className={directionTone(metrics.spotQueueImbalance)}>{imbalance(metrics.spotQueueImbalance)}</b></div>
          <div><span>Taker 250 ms</span><b className={directionTone(metrics.spotTakerImbalance250ms)}>{imbalance(metrics.spotTakerImbalance250ms)}</b></div>
          <div><span>Taker 1 s</span><b className={directionTone(metrics.spotTakerImbalance1s)}>{imbalance(metrics.spotTakerImbalance1s)}</b></div>
        </div>
      </article>
      <article className="measure-group">
        <div className="measure-heading"><div><span>PERPETUAL</span><strong>{money(metrics.futuresPrice)}</strong></div><small>micro {money(metrics.futuresMicroprice)}</small></div>
        <div className="measure-values">
          <div><span>Queue imbalance</span><b className={directionTone(metrics.futuresQueueImbalance)}>{imbalance(metrics.futuresQueueImbalance)}</b></div>
          <div><span>Taker 250 ms</span><b className={directionTone(metrics.futuresTakerImbalance250ms)}>{imbalance(metrics.futuresTakerImbalance250ms)}</b></div>
          <div><span>Taker 1 s</span><b className={directionTone(metrics.futuresTakerImbalance1s)}>{imbalance(metrics.futuresTakerImbalance1s)}</b></div>
        </div>
      </article>
      <article className="measure-group cross-market">
        <div className="measure-heading"><div><span>CROSS MARKET</span><strong className={directionTone(metrics.perpSpotBasisBps)}>{signed(metrics.perpSpotBasisBps, 2, " bps")}</strong></div><small>永續－現貨基差</small></div>
        <div className="measure-values">
          <div><span>Prediction UP mid</span><b>{price(metrics.predictionUpMid)}</b></div>
          <div><span>流動性移除方向</span><b className={directionTone(metrics.predictionRemovalDirection)}>{metrics.predictionRemovalDirection ?? "—"}</b></div>
          <div><span>移除強度</span><b>{imbalance(metrics.predictionRemovalStrength)}</b></div>
        </div>
      </article>
    </div>

    <div className="micro-signal-grid">
      <article className={`signal-card ${alertActive ? "alert" : ""}`}><span>波動警報</span><strong>{volatilityLabel(alert)}</strong><small>只標記異常波動，不觸發訂單</small></article>
      <article className={`signal-card ${directionTone(metrics.directionBias)}`}><span>方向偏差</span><strong>{biasLabel(metrics.directionBias)}</strong><small>由訂單流合成，尚非交易訊號</small></article>
      <article className="signal-card storage-card">
        <span>本地觀測資料</span>
        <strong>{compact(data?.storage?.eventsRows ?? data?.storage?.events)} events</strong>
        <small>{compact(data?.storage?.snapshotsRows ?? data?.storage?.snapshots)} snapshots · {compact(data?.storage?.liquidityRows)} removals</small>
        <small>Writer {data?.storage?.writerStatus ?? "—"} · lag {decimal(data?.storage?.writerLagMs, 1)} ms · queue {compact(data?.storage?.queueDepth)}</small>
        <small>持久化 gaps {compact(data?.storage?.gapRows)} · dropped {compact(data?.storage?.droppedEvents)}</small>
        {data?.storage?.writerError && <small className="stream-error">Writer error: {data.storage.writerError}</small>}
        <small>DB {bytes(data?.storage?.dbBytes)} · 原始 {decimal(data?.storage?.rawRetentionHours ?? data?.storage?.retentionHours, 0)}h / 快照 {decimal(data?.storage?.snapshotRetentionHours ?? data?.storage?.retentionHours, 0)}h</small>
      </article>
    </div>

    <div className="liquidity-block">
      <div className="liquidity-title"><div><span className="eyebrow">LIQUIDITY REMOVAL</span><h3>最近流動性移除</h3></div><small>最多顯示 8 筆新事件</small></div>
      <div className="liquidity-list" role="table" aria-label="最近流動性移除事件">
        <div className="liquidity-row liquidity-columns" role="row"><span>時間</span><span>側別</span><span>類型</span><span>強度</span><span>價格 / 說明</span></div>
        {events.length === 0
          ? <div className="micro-empty">尚未收到流動性移除事件。</div>
          : events.map((event, index) => <div className="liquidity-row" role="row" key={`${event.timestamp ?? "event"}-${index}`}>
            <span data-label="時間">{fmtTime(event.timestamp)}</span>
            <strong data-label="側別" className={directionTone(event.side)}>{event.side || "—"}</strong>
            <span data-label="類型">{event.kind || "REMOVAL"}</span>
            <strong data-label="強度">{imbalance(event.strength)}</strong>
            <span data-label="價格 / 說明"><b>{price(event.price)}</b>{event.detail && <small>{event.detail}</small>}</span>
          </div>)}
      </div>
    </div>
  </section>;
}

function MarketChart({ data }: { data: Observation[] }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || data.length < 2) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
    const ctx = canvas.getContext("2d"); if (!ctx) return;
    ctx.scale(dpr, dpr);
    const w = rect.width, h = rect.height, pad = 18;
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(255,255,255,.07)"; ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) { const y = (h / 4) * i; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
    const draw = (values: (number | null)[], color: string) => {
      ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2.4; ctx.lineJoin = "round";
      values.forEach((value, i) => {
        if (value == null) return;
        const x = pad + (i / Math.max(1, values.length - 1)) * (w - pad * 2);
        const y = h - pad - value * (h - pad * 2);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    };
    draw(data.map(d => d.up_ask), "#8df4c0");
    draw(data.map(d => d.down_ask), "#ffb45c");
  }, [data]);
  return <canvas ref={ref} className="market-chart" aria-label="UP 與 DOWN 價格走勢" />;
}

function MarketTiming({ latest }: { latest: Observation | null }) {
  const timingItems = [
    { label: "API 快照時間", value: fmtTimeMs(latest?.observed_timestamp_ms ?? latest?.timestamp), detail: "本機收到／產生的快照欄位" },
    { label: "UP 簿 timestamp", value: fmtTimeMs(latest?.up_book_timestamp_ms), detail: "API 有提供才顯示" },
    { label: "DOWN 簿 timestamp", value: fmtTimeMs(latest?.down_book_timestamp_ms), detail: "API 有提供才顯示" },
    { label: "簿年齡", value: latest?.book_age_ms == null ? "—" : `${decimal(latest.book_age_ms, 1)} ms`, detail: "快照建立時計算" },
    { label: "兩側簿時差", value: latest?.book_skew_ms == null ? "—" : `${decimal(latest.book_skew_ms, 1)} ms`, detail: "UP／DOWN timestamp 差" },
    { label: "收集延遲", value: latest?.collection_latency_ms == null ? "—" : `${decimal(latest.collection_latency_ms, 1)} ms`, detail: "API 有提供才顯示" },
    { label: "永續成交價", value: price(latest?.futures_price), detail: latest?.futures_agg_trade_id == null ? "USD-M aggTrade／last traded" : `aggTrade #${latest.futures_agg_trade_id}` },
    { label: "永續 timestamp", value: fmtTimeMs(latest?.futures_timestamp_ms), detail: "API 有提供才顯示" },
    { label: "永續資料年齡", value: latest?.futures_age_ms == null ? "—" : `${decimal(latest.futures_age_ms, 1)} ms`, detail: "快照建立時計算" },
  ];
  return <section className="timing-panel" aria-label="毫秒 timestamp 與延遲欄位">
    <div className="timing-heading">
      <div><span className="eyebrow">TIMESTAMP · LATENCY</span><h2>毫秒欄位</h2></div>
      <p>只顯示 API 實際提供的 timestamp／latency；這些值不代表上游行情具備毫秒精度，也不是成交延遲保證。</p>
    </div>
    <div className="timing-grid">
      {timingItems.map(item => <article key={item.label}><span>{item.label}</span><strong>{item.value}</strong><small>{item.detail}</small></article>)}
    </div>
  </section>;
}

function TradeTiming({ trade }: { trade: Trade }) {
  if (!trade.strategy.startsWith("M7_")) return null;
  const diagnostics = tradeDiagnostics(trade);
  if (!diagnostics) return null;
  const hasTiming = diagnostics.signal_timestamp != null || diagnostics.execution_timestamp != null
    || diagnostics.actual_delay_seconds != null || diagnostics.execution_lag_seconds != null;
  if (!hasTiming) return null;
  return <small className="m7-trade-timing">
    <span>signal {fmtTimeMs(diagnostics.signal_timestamp)} · execution {fmtTimeMs(diagnostics.execution_timestamp)}</span>
    <span>要求 {decimal(diagnostics.requested_delay_seconds, 3)} s · 實際 {diagnostics.actual_delay_seconds == null ? "—" : `${decimal(diagnostics.actual_delay_seconds * 1000, 1)} ms`} · lag {diagnostics.execution_lag_seconds == null ? "—" : `${decimal(diagnostics.execution_lag_seconds * 1000, 1)} ms`}</span>
    <em>本機策略 diagnostics，不代表上游行情延遲</em>
  </small>;
}

function MRealtimePanel({ data }: { data?: MRealtimeState | null }) {
  const status = data?.status?.toUpperCase() ?? "WAITING";
  const isLive = status === "LIVE" || status === "RUNNING" || status === "CONNECTED";
  const metric = (value: number | null | undefined, suffix = "") => value == null ? "—" : `${decimal(value, 1)}${suffix}`;
  const count = (value: number | null | undefined) => value == null ? "—" : Math.round(value).toLocaleString("zh-TW");
  return <section className="m-realtime-panel" aria-label="M 系列 WebSocket 毫秒實驗引擎狀態">
    <div className="m-realtime-head">
      <div>
        <div className="m-realtime-title-row"><span className="eyebrow">EVENT-DRIVEN · PAPER ONLY</span><h3>WS 毫秒實驗引擎</h3><span className={`m-realtime-status ${isLive ? "live" : ""}`}><i />{status}</span></div>
        <p>後端按收到的事件獨立判斷；網站每 1 秒只刷新監察畫面，不會把策略降成每秒執行。</p>
      </div>
      <div className="m-realtime-mode"><span>模式</span><strong>{data?.mode ?? "—"}</strong><small>{data?.paperOnly == null ? "paper-only 尚待 API 回報" : data.paperOnly ? "只做模擬交易" : "警告：API 回報非 paper-only"}</small></div>
    </div>
    <div className="m-realtime-grid">
      <article><span>市場 / 事件率</span><strong>#{data?.marketId ?? "—"}</strong><small>{metric(data?.eventRate, " event/s")}</small></article>
      <article><span>佇列</span><strong>{count(data?.queueDepth)}</strong><small>delay {metric(data?.lastQueueDelayMs, " ms")}</small></article>
      <article><span>已處理 / 丟棄</span><strong>{count(data?.processedEvents)} / {count(data?.droppedEvents)}</strong><small>{data?.marketDataIntegrityOk == null ? "integrity —" : data.marketDataIntegrityOk ? "integrity OK" : "integrity DEGRADED"} · replay {count(data?.rejectedTradeReplays)}</small></article>
      <article><span>決策耗時</span><strong>{metric(data?.lastDecisionDurationMs, " ms")}</strong><small>scheduler {metric(data?.schedulerTickMs, " ms")} · M7 reorder {metric(data?.m7EventReorderGraceMs, " ms")}</small></article>
      <article><span>Spot trade ingress</span><strong>{metric(data?.spotTradeIngressAgeMs, " ms")}</strong><small>socket callback age</small></article>
      <article><span>Spot trade processed</span><strong>{metric(data?.spotTradeProcessedAgeMs, " ms")}</strong><small>{data?.spotPriceSource || "no effective Spot source"}</small></article>
      <article><span>Spot trade age</span><strong>{metric(data?.spotTradeAgeMs, " ms")}</strong><small>last accepted Spot trade</small></article>
      <article><span>Spot book age</span><strong>{metric(data?.spotBookAgeMs, " ms")}</strong><small>independent bookTicker socket</small></article>
      <article><span>Futures 年齡</span><strong>{metric(data?.futuresAgeMs, " ms")}</strong><small>last traded feed</small></article>
      <article><span>Prediction 策略可用簿年齡</span><strong>{metric(data?.predictionStrategyBookAgeMs ?? data?.predictionBookAgeMs, " ms")}</strong><small>最後通過新鮮度閘門 · {fmtTimeMs(data?.lastAcceptedPredictionAt)}</small></article>
      <article><span>Prediction REST 接收年齡</span><strong>{metric(data?.predictionRestReceiptAgeMs, " ms")}</strong><small>最近一次 REST 快照抵達本機</small></article>
      <article><span>Prediction 交易所內容年齡</span><strong>{metric(data?.predictionExchangeContentAgeMs, " ms")}</strong><small>最新 REST 內容版本 · {data?.predictionLatestRestEligible == null ? "資格 —" : data.predictionLatestRestEligible ? "策略接受" : "策略拒絕"}</small></article>
      <article><span>Prediction 驗證計數</span><strong>{count(data?.acceptedPredictionEvents)} / {count(data?.rejectedUnverifiedPredictionEvents)}</strong><small>接受 / 拒絕未驗證</small></article>
    </div>
    <p className="m-realtime-caveat">「毫秒」是本機排程、queue、age 與 diagnostics 的量測單位；Prediction 上游更新並非 1 ms，這裡也不宣稱毫秒成交能力。</p>
    {data?.error && <p className="m-realtime-error" role="alert">{data.error}</p>}
  </section>;
}

function MarketObserverPanel({ data }: { data?: MarketObserverState | null }) {
  if (!data) return null;
  const metrics = data.metrics || {};
  const current = data.currentRound || {};
  const stateName = data.state || "UNCERTAIN";
  const sampleCount = data.sampleCount ?? metrics.sample_count ?? metrics.sample_size ?? 0;
  const rollingLimit = data.rollingRoundLimit ?? metrics.rolling_round_limit ?? 20;
  const provisional = data.provisional ?? metrics.provisional ?? sampleCount < 12;
  const winnerTouch = data.winnerTouchRate ?? metrics.winner_touch_rate;
  const bothTouch = data.bothSidesTouchRate ?? metrics.both_sides_touch_rate;
  const crossovers = data.averageEffectiveCrossovers ?? metrics.average_effective_crossovers ?? metrics.avg_effective_crossover_count;
  const medianEr = data.medianEr60s ?? metrics.median_er_60s;
  const earlyEr = data.earlyEr60sMedian ?? metrics.early_er_60s_median;
  const p75Er = data.p75Er60sMedian ?? metrics.p75_er_60s_median;
  const conditionalWin = data.m01ConditionalWinRate ?? metrics.m01_conditional_win_rate ?? metrics.conditional_fill_win_rate;
  const averageFill = data.m01AverageFillPrice ?? metrics.m01_average_fill_price;
  const breakEven = data.m01BreakEvenWinRate ?? metrics.m01_break_even_win_rate;
  const m01Edge = data.m01Edge ?? metrics.m01_edge;
  const settledFills = data.m01SettledFillSampleCount ?? metrics.m01_settled_fill_sample_count;
  const rangeScore = data.rangeScore ?? metrics.range_score;
  const trendScore = data.trendScore ?? metrics.trend_score;
  const reasons = data.reasons ?? metrics.reasons ?? [];
  const conflicts = data.conflicts ?? metrics.conflicts ?? [];
  const costStatus = data.costStatus ?? metrics.cost_status ?? (metrics.m01_cost_excluded ? "cost_excluded" : "unavailable");
  const costLabel = costStatus === "cost_excluded" ? "未計交易成本" : costStatus === "cost_included" ? "已含紀錄費用" : "—";
  const edgeValue = finite(m01Edge);
  const priceText = (value: number | null | undefined, digits = 3) => value == null ? "—" : decimal(value, digits);
  const percentText = (value: number | null | undefined) => {
    const safe = finite(value);
    if (safe == null) return "—";
    return `${(Math.abs(safe) <= 1 ? safe * 100 : safe).toFixed(1)}%`;
  };

  return (
    <section className={`m-realtime-panel market-observer-panel ${stateName.toLowerCase()}`} aria-label="市場狀態觀測器">
      <div className="m-realtime-head">
        <div>
          <div className="m-realtime-title-row">
            <span className="eyebrow">OBSERVATION + FAIL-CLOSED M01 GATES</span>
            <h3>市場狀態觀測器 (Market Observer)</h3>
            <span className={`m-realtime-status market-state-${stateName.toLowerCase()}`}>
              <i />
              {stateName}
            </span>
            <span className={`market-observer-phase ${provisional ? "provisional" : "final"}`}>{provisional ? "暫定狀態" : "正式狀態"}</span>
          </div>
          <p>
            依最近已結算市場辨識震盪反轉或單邊趨勢；M01O 模擬策略會讀取放行門檻。只有你在正式實單策略中明確選擇 F1 時，F1 門檻才會影響新實單；原 M01、M01T180 不受影響。
          </p>
        </div>
        <div className="m-realtime-mode">
          <span>樣本數／滾動上限</span>
          <strong>{sampleCount}／{rollingLimit} 輪</strong>
          <small>{provisional ? "12 輪前僅為暫定判讀" : "已達正式狀態門檻"}</small>
        </div>
      </div>

      <div className="market-observer-score-strip" aria-label="市場狀態計分">
        <div><span>RANGE score</span><strong>{rangeScore ?? "—"}</strong></div>
        <div><span>TREND score</span><strong>{trendScore ?? "—"}</strong></div>
        <div><span>判定分差</span><strong>{rangeScore == null || trendScore == null ? "—" : Math.abs(rangeScore - trendScore)}</strong><small>至少 2 分才分類</small></div>
      </div>

      <div className="m-realtime-grid">
        <article><span>Winner Touch Rate</span><strong>{percentText(winnerTouch)}</strong><small>最終贏家 Ask 曾 ≤ {priceText(data.touchThreshold ?? 0.30, 2)}</small></article>
        <article><span>Both-Sides Touch Rate</span><strong>{percentText(bothTouch)}</strong><small>UP／DOWN 直接最佳 Ask 都曾進入掛單區</small></article>
        <article><span>平均有效穿越</span><strong>{crossovers == null ? "—" : `${decimal(crossovers, 2)} 次`}</strong><small>startPrice deadband 完整跨越</small></article>
        <article><span>Median 60s ER</span><strong>{priceText(medianEr)}</strong><small>分類採用的滾動 60 秒中位效率</small></article>
        <article><span>Early 60s ER</span><strong>{priceText(earlyEr)}</strong><small>每盤開局第一個完整 60 秒 ER 中位數</small></article>
        <article><span>P75 60s ER</span><strong>{priceText(p75Er)}</strong><small>每盤 60 秒 ER 第 75 百分位之中位數</small></article>
        <article><span>M01 Conditional Fill Win Rate</span><strong>{percentText(conditionalWin)}</strong><small>只含實際成交且正式結算 WIN／LOSS</small></article>
        <article><span>M01 平均成交價</span><strong>{priceText(averageFill)}</strong><small>{settledFills == null ? "成交樣本 —" : `正式結算成交樣本 ${settledFills} 輪`}</small></article>
        <article><span>M01 損益平衡勝率</span><strong>{percentText(breakEven)}</strong><small>{costLabel}</small></article>
        <article className={edgeValue != null && edgeValue < 0 ? "negative" : ""}><span>M01 Edge</span><strong>{percentText(m01Edge)}</strong><small>條件勝率 − 損益平衡勝率</small></article>
        <article><span>Touch／Deadband</span><strong>{priceText(data.touchThreshold, 2)}／{priceText(data.crossoverDeadbandBps, 1)} bps</strong><small>Ask 掛單區／有效穿越濾網</small></article>
        <article><span>目前狀態</span><strong>{stateName}</strong><small>{provisional ? "暫定判讀" : "正式判讀"}</small></article>
      </div>

      <div className="market-observer-explainer">
        <p><strong>Winner Touch Rate 高：</strong>最終贏家經常曾跌至掛單區後反轉，偏 RANGE／震盪反轉。</p>
        <p><strong>Winner Touch Rate 低：</strong>最終贏家通常不回頭，主要只有輸家容易成交，偏 TREND／單邊趨勢。</p>
      </div>

      {edgeValue != null && edgeValue < 0 && <p className="market-observer-alert" role="alert">M01 條件成交目前為負期望；這項歷史期望警示不直接控制實單。選擇 F1 時採用的是市場狀態門檻。</p>}

      <div className="market-observer-reasons">
        <div><strong>判定摘要</strong><p>{data.reason || "資料累積中"}</p></div>
        <div><strong>判定原因</strong>{reasons.length ? <ul>{reasons.map((item, index) => <li key={`observer-reason-${index}`}>{item}</li>)}</ul> : <p>—</p>}</div>
        <div><strong>衝突原因</strong>{conflicts.length ? <ul>{conflicts.map((item, index) => <li key={`observer-conflict-${index}`}>{item}</li>)}</ul> : <p>—</p>}</div>
      </div>

      <p className="m-realtime-caveat">當前盤 #{current.marketId ?? "—"}：有效穿越 {current.effectiveCrossoverCount ?? current.crossoverCount ?? 0} 次｜Median 60s ER {priceText(current.currentMedianEr60s ?? current.currentEr60s)}｜UP 觸及 {current.upTouched == null ? "—" : current.upTouched ? "是" : "否"}｜DOWN 觸及 {current.downTouched == null ? "—" : current.downTouched ? "是" : "否"}</p>
    </section>
  );
}

function M01OFilterExperimentPanel({ data, observer }: {
  data?: M01OFilterExperimentState | null;
  observer?: MarketObserverState | null;
}) {
  const definitions = [
    { profile: "F2" as const, title: "F2 嚴格組", tone: "strict", rule: "歷史 RANGE＋當輪分數 ≥2＋無 TREND override" },
    { profile: "F1" as const, title: "F1 寬鬆組", tone: "relaxed", rule: "歷史非 TREND＋當輪分數 ≥1＋無 TREND override" },
    { profile: "LIVE" as const, title: "LIVE 當輪優先組", tone: "live", rule: "歷史樣本 ≥6、非 TREND，且當輪雙邊 Ask 都曾 ≤0.30" },
  ];
  const target = data?.sampleTarget?.minimum ?? 200;
  const percent = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
  return <section className="m01o-filter-panel" aria-labelledby="m01o-filter-title">
    <div className="m01o-filter-head">
      <div><span className="eyebrow">SHARED M01 CANDIDATE · PAPER ONLY</span><h3 id="m01o-filter-title">M01 過濾強度三組對照</h3><p>三組共用同一 market ID、固定種子方向、Ask 價格、可見深度與模擬成交規則；唯一變因是市場狀況過濾強度。</p></div>
      <div className="m01o-filter-target"><span>分析門檻</span><strong>200–300 輪</strong><small>{data?.status ?? "COLLECTING"} · 不影響實單</small></div>
    </div>
    <div className="m01o-filter-grid">
      {definitions.map(definition => {
        const group = data?.groups?.[definition.profile];
        const gate = observer?.m01oGates?.[definition.profile] ?? (definition.profile === "F2" ? observer?.m01oGate : undefined);
        const candidates = group?.candidateMarkets ?? 0;
        const progress = Math.min(100, target > 0 ? candidates / target * 100 : 0);
        const profitFactor = group?.profitFactorInfinite ? "∞" : decimal(group?.profitFactor, 2);
        const block = group?.blockCategories ?? {};
        return <article className={`m01o-filter-card ${definition.tone}`} key={definition.profile}>
          <div className="m01o-filter-card-head"><div><span>{definition.profile}</span><h4>{definition.title}</h4></div><span className={`m01o-gate-state ${gate?.allowed ? "allow" : gate?.blockCategory?.toLowerCase() ?? "block"}`}>{gate?.allowed ? "ALLOW" : gate?.blockCategory ?? "WAITING"}</span></div>
          <p className="m01o-filter-rule">{definition.rule}</p>
          <div className="m01o-current-gate">
            <span>{gate?.currentPhase ?? "等待當輪"}</span>
            <strong>分數 {gate?.currentRangeScore ?? 0}{definition.profile === "LIVE" ? " · 雙邊觸及" : ""}</strong>
            <small>{gate?.reason ?? "等待觀測器資料"}</small>
          </div>
          <div className="m01o-filter-metrics">
            <div><span>候選／已交易</span><strong>{candidates} / {group?.openedTrades ?? 0}</strong></div>
            <div><span>交易覆蓋率</span><strong>{percent(group?.tradeCoverageRate)}</strong></div>
            <div><span>實際成交率</span><strong>{percent(group?.actualFillRate)}</strong></div>
            <div><span>勝率</span><strong>{percent(group?.winRate)}</strong><small>{group?.wins ?? 0} 勝 / {group?.losses ?? 0} 負</small></div>
            <div><span>平均成交價</span><strong>{price(group?.averageEntryPrice)}</strong></div>
            <div><span>平均每筆損益</span><strong className={(group?.averagePnl ?? 0) >= 0 ? "positive" : "negative"}>{money(group?.averagePnl)}</strong></div>
            <div><span>Profit Factor</span><strong>{profitFactor}</strong></div>
            <div><span>最大回撤</span><strong className="negative">{money(group?.maxDrawdown)}</strong></div>
            <div><span>每日零交易比例</span><strong>{percent(group?.zeroTradeDayRate)}</strong><small>{group?.zeroTradeDays ?? 0} / {group?.observedDays ?? 0} 日</small></div>
            <div><span>已實現損益</span><strong className={(group?.realizedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(group?.realizedPnl)}</strong></div>
          </div>
          <div className="m01o-counterfactual"><span>被擋交易的反事實結果</span><strong>{group?.blockedCounterfactualWins ?? 0} 勝 / {group?.blockedCounterfactualLosses ?? 0} 負</strong><small>若照原 M01 成交：勝率 {percent(group?.blockedCounterfactualWinRate)} · 損益 {money(group?.blockedCounterfactualPnl)}</small></div>
          <div className="m01o-block-counts"><span>NOT_READY {block.NOT_READY ?? 0}</span><span>MISSING_OR_STALE {block.MISSING_OR_STALE ?? 0}</span><span>FILTERED {block.FILTERED ?? 0}</span></div>
          <div className="m01o-sample-progress"><i style={{ width: `${progress}%` }} /></div>
          <small className="m01o-progress-copy">樣本進度 {candidates} / {target}；滿 200 輪後再比較品質與頻率</small>
        </article>;
      })}
    </div>
  </section>;
}

type ReliabilityShadowStatus = "candidate" | "warning" | "limited";
type ReliabilityShadowSplit = { label: string; samples: number; returnOnStake: string };
type ReliabilityShadowTag = {
  id: string;
  strategy: string;
  title: string;
  status: ReliabilityShadowStatus;
  condition: string;
  finding: string;
  splits: ReliabilityShadowSplit[];
  daily: string;
  caution: string;
};

const RELIABILITY_SHADOW_TAGS: ReliabilityShadowTag[] = [
  {
    id: "RC_LOW_ENTRY",
    strategy: "R_CALIBRATED_VALUE",
    title: "低進場價可靠區",
    status: "candidate",
    condition: "entry_price ≤ 0.376875",
    finding: "三個時間切分皆維持正值，是目前最一致的價位型可靠候選。",
    splits: [
      { label: "開發", samples: 13, returnOnStake: "+12.4%" },
      { label: "驗證", samples: 10, returnOnStake: "+7.8%" },
      { label: "留後", samples: 15, returnOnStake: "+15.1%" },
    ],
    daily: "7 日中 6 日為正",
    caution: "門檻只由開發段建立；仍需新的前向樣本確認。",
  },
  {
    id: "RC_STALE_QUOTE",
    strategy: "R_CALIBRATED_VALUE",
    title: "報價過舊失準警戒",
    status: "warning",
    condition: "book_age_ms > 828.5",
    finding: "開發與驗證段呈負值，留後段接近零，較像訊號品質退化條件。",
    splits: [
      { label: "開發", samples: 13, returnOnStake: "−2.5%" },
      { label: "驗證", samples: 9, returnOnStake: "−5.3%" },
      { label: "留後", samples: 10, returnOnStake: "+0.5%" },
    ],
    daily: "7 日中僅 3 日為正",
    caution: "目前只做警戒標籤，不直接阻擋訊號。",
  },
  {
    id: "FL_DIRECTION",
    strategy: "R_FUTURES_LEAD",
    title: "方向差異觀測",
    status: "limited",
    condition: "signal side = DOWN（另列 UP 對照）",
    finding: "DOWN 在三段皆為正；UP 從開發負值轉為後段正值，方向效果並不穩定。",
    splits: [
      { label: "開發", samples: 4, returnOnStake: "+19.8%" },
      { label: "驗證", samples: 9, returnOnStake: "+27.2%" },
      { label: "留後", samples: 9, returnOnStake: "+32.4%" },
    ],
    daily: "DOWN 開發段僅 4 筆",
    caution: "樣本太少，不應升級成方向阻擋；UP 留後仍為 +6.0%。",
  },
  {
    id: "MP_LATE_WINDOW",
    strategy: "R_MICROPRICE",
    title: "較晚進場可靠區",
    status: "candidate",
    condition: "seconds_left ≤ 178.432",
    finding: "三個時間切分皆為正，晚於原始 180 秒附近的觸發品質較好。",
    splits: [
      { label: "開發", samples: 11, returnOnStake: "+41.0%" },
      { label: "驗證", samples: 8, returnOnStake: "+20.7%" },
      { label: "留後", samples: 11, returnOnStake: "+20.6%" },
    ],
    daily: "有交易的 6 日中 5 日為正",
    caution: "與新鮮簿標籤分開追蹤，不能把兩者效果相加。",
  },
  {
    id: "MP_FRESH_BOOK",
    strategy: "R_MICROPRICE",
    title: "新鮮訂單簿可靠區",
    status: "candidate",
    condition: "book_age_ms ≤ 422.5",
    finding: "三個時間切分皆為正，但留後優勢縮小，適合繼續累積而非立即套用。",
    splits: [
      { label: "開發", samples: 11, returnOnStake: "+25.5%" },
      { label: "驗證", samples: 7, returnOnStake: "+15.9%" },
      { label: "留後", samples: 7, returnOnStake: "+7.7%" },
    ],
    daily: "留後仍正、但效果遞減",
    caution: "與晚進場樣本交集很小，必須視為獨立假說。",
  },
  {
    id: "MP_MIDPRICE_WEAK",
    strategy: "R_MICROPRICE",
    title: "中低價開發假象警戒",
    status: "warning",
    condition: "0.286425 < entry_price ≤ 0.39195",
    finding: "開發段漂亮，但驗證與留後皆轉負，是典型樣本內有效、樣本外失準。",
    splits: [
      { label: "開發", samples: 14, returnOnStake: "+21.0%" },
      { label: "驗證", samples: 10, returnOnStake: "−6.2%" },
      { label: "留後", samples: 10, returnOnStake: "−12.8%" },
    ],
    daily: "後兩段連續失效",
    caution: "保留作反過度擬合警報，不應當成進場條件。",
  },
];

const RELIABILITY_STATUS_LABEL: Record<ReliabilityShadowStatus, string> = {
  candidate: "候選可靠區",
  warning: "失準警戒",
  limited: "樣本不足",
};
const RELIABILITY_LIVE_GATE_OPTIONS = RELIABILITY_SHADOW_TAGS.filter(
  (tag): tag is ReliabilityShadowTag & { id: ReliabilityCandidateTagId } => RELIABILITY_CANDIDATE_TAG_IDS.includes(tag.id as ReliabilityCandidateTagId),
);

function ConfirmationAddSummaryPanel({
  title, subtitle, overall, groups, recent, realFill,
}: {
  title: string; subtitle: string; overall?: ConfirmationAddMetrics;
  groups?: Record<string, ConfirmationAddMetrics>; recent?: ConfirmationAddRecent[];
  realFill: boolean;
}) {
  const hypotheticalPnl = realFill ? overall?.hypotheticalPnlUsdt : overall?.pnlUsdt;
  const roi = realFill ? overall?.hypotheticalReturnOnCostPct : overall?.returnOnCostPct;
  const settled = overall?.settledSamples ?? overall?.officialSamples ?? 0;
  return <section className="shadow-tag-live-orders confirmation-add-summary" aria-label={title}>
    <div><span className="eyebrow">CONFIRMATION ADD · FORWARD ONLY</span><h3>{title}</h3><p>{subtitle}</p></div>
    <div className="shadow-tag-summary">
      <article><span>鏡像樣本</span><strong>{overall?.samples ?? 0}</strong><small>只從功能啟用後新增</small></article>
      <article className="candidate"><span>完成結算</span><strong>{settled}</strong><small>等待中 {overall?.pendingSamples ?? 0}</small></article>
      <article className={(hypotheticalPnl ?? 0) >= 0 ? "candidate" : "warning"}><span>確認加碼損益</span><strong>{money(hypotheticalPnl ?? 0)}</strong><small>ROI {roi == null ? "—" : ratio(roi / 100)}</small></article>
      <article className="limited"><span>平均投入</span><strong>{money(settled ? (realFill ? overall?.hypotheticalStakeUsdt ?? 0 : overall?.stakeUsdt ?? 0) / settled : 0)}</strong><small>每筆上限 5 USDT</small></article>
    </div>
    {realFill && <div className="shadow-tag-forward-comparison">
      <div><span>原實單損益</span><strong>{money(overall?.originalPnlUsdt ?? 0)}</strong><small>同一批真實成交</small></div>
      <div><span>改用確認加碼</span><strong>{money(overall?.hypotheticalPnlUsdt ?? 0)}</strong><small>1.0× 至 1.4× 五檔</small></div>
      <div><span>相對差異</span><strong className={(overall?.deltaVsOriginalPnlUsdt ?? 0) >= 0 ? "positive" : "negative"}>{money(overall?.deltaVsOriginalPnlUsdt ?? 0)}</strong><small>鏡像減原實單</small></div>
    </div>}
    <div className="shadow-tag-splits" aria-label="確認加碼來源策略統計">
      {Object.entries(groups ?? {}).map(([strategy, metrics]) => <div key={strategy}>
        <span>{strategy} · n={metrics.settledSamples ?? metrics.officialSamples ?? 0}</span>
        <strong className={((realFill ? metrics.hypotheticalPnlUsdt : metrics.pnlUsdt) ?? 0) >= 0 ? "positive" : "negative"}>{money((realFill ? metrics.hypotheticalPnlUsdt : metrics.pnlUsdt) ?? 0)}</strong>
        <small>等待 {metrics.pendingSamples ?? 0}</small>
      </div>)}
    </div>
    <div className="table-scroll"><table><thead><tr><th>時間</th><th>來源</th><th>市場／方向</th><th>已成交檔</th><th>投入</th><th>結果</th></tr></thead><tbody>
      {(recent ?? []).length === 0 ? <tr><td colSpan={6} className="empty">等待功能啟用後的新來源成交；不回填舊資料。</td></tr> : (recent ?? []).slice(0, 20).map((item, index) => <tr key={`${item.orderLocalId ?? item.sourceTradeId ?? index}`}>
        <td>{fmtTimeMs(item.createdAt ?? item.openedAt)}</td>
        <td>{item.strategy ?? item.sourceStrategy ?? "—"}</td>
        <td>#{item.marketId ?? "—"} · {item.side ?? "—"}</td>
        <td>{item.filledTranches ?? 0}/5</td>
        <td>{money(item.hypotheticalStakeUsdt ?? item.stakeUsdt ?? 0)}</td>
        <td className={((item.hypotheticalPnlUsdt ?? item.pnlUsdt) ?? 0) >= 0 ? "positive" : "negative"}>{item.settlementResult ?? item.result ?? item.status ?? "PENDING"} · {(item.hypotheticalPnlUsdt ?? item.pnlUsdt) == null ? "—" : money((item.hypotheticalPnlUsdt ?? item.pnlUsdt) ?? 0)}</td>
      </tr>)}
    </tbody></table></div>
    <small>規則：原成交價先記 1 USDT；Ask 達到原價 1.1×、1.2×、1.3×、1.4× 各再記 1 USDT；剩餘時間 ≤30 秒停止。盤口需通過新鮮度、時間差、價差與深度檢查，採 50 bps 滑價與 200 bps 費用。此區不會送出訂單。</small>
  </section>;
}

function ReliabilityShadowPanel({ data }: { data?: LiveM0WState | null }) {
  const counts = RELIABILITY_SHADOW_TAGS.reduce((current, tag) => {
    current[tag.status] += 1;
    return current;
  }, { candidate: 0, warning: 0, limited: 0 });
  const research = data?.reliabilityResearch;
  const forwardById = new Map((research?.tags ?? []).map(tag => [tag.id, tag]));
  const recentSamples = research?.recentSamples ?? [];
  const cohortCopy = (cohort?: ReliabilityCohort) => `${cohort?.settledSamples ?? 0} 已結算 · ${money(cohort?.pnlUsdt ?? 0)}`;

  return <div className="reliability-shadow-panel" role="tabpanel" id="reliability-shadow-panel" aria-labelledby="reliability-shadow-tab">
    <section className="shadow-tag-guard">
      <div>
        <span className="eyebrow">REAL FILLS · COUNTERFACTUAL RESEARCH</span>
        <h3>實單成交鏡像可靠／失準研究</h3>
        <p>Binance 回報實際成交量後才複製研究快照，官方結算再回填原訂單結果；每條標籤獨立顯示原單、假設放行與假設阻擋結果。紙上單、報價拒絕與未成交單不混入。</p>
      </div>
      <span className="shadow-tag-badge">LIVE FILL MIRROR</span>
    </section>

    <section className="shadow-tag-summary" aria-label="實單鏡像樣本摘要">
      <article><span>已複製實單成交</span><strong>{research?.copiedSamples ?? 0}</strong><small>只計實際 filled cost &gt; 0</small></article>
      <article className="candidate"><span>已官方結算</span><strong>{research?.settledSamples ?? 0}</strong><small>原單 WIN／LOSS 與 PnL</small></article>
      <article className="warning"><span>等待結算</span><strong>{research?.pendingSamples ?? 0}</strong><small>不提前計入成效</small></article>
      <article className="limited"><span>目前啟用候選</span><strong>{research?.enabledLiveTags?.length ?? 0}</strong><small>三個候選預設全部關閉</small></article>
    </section>

    <ConfirmationAddSummaryPanel
      title="實單成交改用順勢確認加碼，結果會怎樣？"
      subtitle="只在真實策略確實成交後建立鏡像，之後用實際記錄的 Prediction 盤口逐檔追蹤；原實單完全不受影響。"
      overall={research?.confirmationAdd?.overall}
      groups={research?.confirmationAdd?.byStrategy}
      recent={research?.confirmationAdd?.recent}
      realFill
    />

    <div className="shadow-tag-grid">
      {RELIABILITY_SHADOW_TAGS.map(tag => {
        const forward = forwardById.get(tag.id);
        const delta = forward?.deltaVsOriginalPnlUsdt ?? 0;
        return <article className={`shadow-tag-card ${tag.status}`} key={tag.id}>
        <div className="shadow-tag-card-head">
          <div><span>{tag.strategy}</span><h4>{tag.title}</h4></div>
          <strong>{RELIABILITY_STATUS_LABEL[tag.status]}</strong>
        </div>
        <code>{tag.id} · {tag.condition}</code>
        <p>{tag.finding}</p>
        <div className="shadow-tag-splits" aria-label={`${tag.id} 封存七日回放基準`}>
          {tag.splits.map(split => <div key={split.label}>
            <span>封存{split.label} · n={split.samples}</span>
            <strong className={split.returnOnStake.startsWith("−") ? "negative" : "positive"}>{split.returnOnStake}</strong>
            <small>已實現損益／本金</small>
          </div>)}
        </div>
        <div className="shadow-tag-forward-comparison">
          <div><span>原實單結果</span><strong>{cohortCopy(forward?.original)}</strong><small>所有適用成交，不改寫原單</small></div>
          <div><span>假設放行</span><strong>{cohortCopy(forward?.allowed)}</strong><small>規則判為 ALLOW 的原單</small></div>
          <div><span>假設阻擋</span><strong>{cohortCopy(forward?.blocked)}</strong><small>這些原單若阻擋，策略 PnL 視為 0</small></div>
          <div><span>套用後反事實</span><strong className={delta >= 0 ? "positive" : "negative"}>{money(forward?.policyPnlUsdt ?? 0)} · Δ {money(delta)}</strong><small>{forward?.enabledForLive ? "實單開關已啟用" : forward?.activationAvailable ? "可選但目前未啟用" : "只研究，不提供實單開關"}</small></div>
        </div>
        <div className="shadow-tag-foot"><span>{tag.daily}</span><small>{tag.caution}</small></div>
      </article>})}
    </div>

    <section className="shadow-tag-live-orders" aria-label="最近實單鏡像樣本">
      <div><span className="eyebrow">SOURCE ORDER AUDIT</span><h3>最近原訂單與標籤判定</h3></div>
      <div className="table-scroll"><table><thead><tr><th>成交</th><th>策略／市場</th><th>方向／進場</th><th>原單結果</th><th>各標籤放行／阻擋</th></tr></thead><tbody>
        {recentSamples.length === 0 ? <tr><td colSpan={5} className="empty">等待部署後的新實單成交；不回填缺少完整成交快照的舊訂單。</td></tr> : recentSamples.map(sample => <tr key={sample.orderLocalId}><td>{fmtTimeMs(sample.capturedAt)}</td><td>{sample.strategy}<small>#{sample.marketId}</small></td><td>{sample.side} · {price(sample.executedEntryPrice)}</td><td className={(sample.settlementPnlUsdt ?? 0) >= 0 ? "positive" : "negative"}>{sample.settlementResult ?? "等待結算"} · {sample.settlementPnlUsdt == null ? "—" : money(sample.settlementPnlUsdt)}</td><td>{(sample.tagDecisions ?? []).map(decision => <span className={`shadow-decision ${String(decision.decision).toLowerCase()}`} key={decision.id}>{decision.id} {decision.decision}</span>)}</td></tr>)}
      </tbody></table></div>
    </section>

    <section className="shadow-tag-method">
      <div><span className="eyebrow">EVIDENCE BOUNDARY</span><h3>原單是真實成交；放行／阻擋是反事實</h3></div>
      <p>原訂單結算來自獨立實單帳本與官方 token outcome。假設阻擋不會建立第二筆訂單，也不假設被省下的資金會改投其他市場；套用後 PnL 等於放行組原單 PnL，阻擋組視為零。封存七日回放只保留作研究起點，不會混入新的實單前向樣本。</p>
      <span className="shadow-tag-forward-state">前向實單樣本：{research?.settledSamples ?? 0} 已結算／{research?.pendingSamples ?? 0} 待結算</span>
    </section>
  </div>;
}

const RESEARCH_STRATEGY_CARDS: Array<{ id: StrategyId; title: string; rule: string; tone: string; shadow?: boolean }> = [
  { id: "R_FUTURES_LEAD_SIGNAL_100", title: "Lead 強訊號測試版", rule: "依附同市場已開啟的 R_FUTURES_LEAD，只保留絕對 lead 強度 ≥ 1.00 bps；獨立 Shadow，不占主要研究資金池。", tone: "blue", shadow: true },
  { id: "R_FUTURES_LEAD_MIN_ENTRY_020", title: "Lead 排除低價測試版", rule: "依附同市場已開啟的 R_FUTURES_LEAD，只保留模擬成交價 > 0.20；用來隔離近期低價長尾樣本的失效風險。", tone: "amber", shadow: true },
  { id: "R_MICROPRICE", title: "Microprice 深度失衡", rule: "剩餘 180 秒，以 UP／DOWN 第一檔數量失衡差決定方向。", tone: "cyan" },
  { id: "R_OFI", title: "Order Flow Imbalance", rule: "剩餘 60 秒，比較 10 秒訂單流變化；目前回測的首選候選。", tone: "mint" },
  { id: "R_FUTURES_LEAD", title: "永續領先現貨", rule: "剩餘 180 秒，永續 3 秒報酬幅度領先現貨至少 0.25 bps 才進場。", tone: "blue" },
  { id: "R_CALIBRATED_VALUE", title: "校準機率價值", rule: "剩餘 60 秒，以凍結校準係數估計勝率，扣除成交價與費用後仍有淨優勢才進場。", tone: "purple" },
  { id: "R_CONSENSUS", title: "五訊號共識", rule: "剩餘 180 秒，Microprice、OFI、永續、現貨與機率價格至少四票同向。", tone: "amber" },
  { id: "R_MICROPRICE_REVERSE",title: "Microprice 反方向",rule: "依賴式 Shadow：只有 R_MICROPRICE 實際建立紙上倉位後才建立配對影子；原單 UP 就開 DOWN，原單 DOWN 就開 UP，使用反向側實際 Ask，不會獨立觸發。",tone: "coral",shadow: true,},
  { id: "R_CALIBRATED_VALUE_REVERSE",title: "校準價值反方向",rule: "依賴式 Shadow：只有 R_CALIBRATED_VALUE 實際建立紙上倉位後才建立配對影子；使用相反方向與反向側實際 Ask，不會獨立觸發。",tone: "amber",shadow: true,},
  { id: "R_CALIBRATED_VALUE_CONTINUOUS_V2", title: "Calibrated Value · 持續校準 V2", rule: "依賴式 Shadow：來源 Value 紙上單成立後，只使用當前市場以前已官方結算的最近 200 筆；依方向與機率十分位持續更新，總歷史至少 20 筆、同桶至少 5 筆且校準後淨 edge ≥0.01 才跟單。", tone: "purple", shadow: true },
  { id: "R_FUTURES_LEAD_CONTINUOUS_V2", title: "Futures Lead · 持續校準 V2", rule: "依賴式 Shadow：來源 Lead 紙上單成立後，只使用當前市場以前已官方結算的最近 200 筆；依方向與 lead 強度桶持續更新，總歷史至少 20 筆、同桶至少 5 筆且校準後淨 edge ≥0.01 才跟單。", tone: "blue", shadow: true },
  { id: "R_OFI_MIN040", title: "OFI＋最低價 0.40", rule: "獨立 Shadow：沿用原 OFI 訊號，但進場側實際 Ask 必須至少 0.40。", tone: "mint", shadow: true },
  { id: "R_OFI_EVENT_CUM", title: "累積事件級 OFI", rule: "獨立 Shadow：累加最近 10 秒每筆 Prediction 訂單簿事件的正規化 OFI，不使用單一端點差。", tone: "cyan", shadow: true },
  { id: "R_OFI_EVENT_CUM_FILTERED", title: "累積 OFI 強訊號過濾", rule: "獨立 Shadow：保留累積事件級 OFI，僅接受 |OFI| ≥ 2.0 且實際 Ask 介於 0.40–0.69。", tone: "green", shadow: true },
  { id: "R_FUTURES_LEAD_REVERSE", title: "永續領先反方向", rule: "依賴式 Shadow：只有 R_FUTURES_LEAD 實際建立紙上倉位後才跟單；原單 UP 就開 DOWN，原單 DOWN 就開 UP，不會獨立觸發。", tone: "coral", shadow: true },
  { id: "R_FUTURES_LEAD_REGIME_REVERSE_3L", title: "永續領先 · 連敗三筆正反切換", rule: "完整切換 Shadow：最近三筆原始 R_FUTURES_LEAD 結算全敗時反向，否則維持原方向；歷史永遠按原始 Lead 方向更新。", tone: "amber", shadow: true },
  { id: "R_FUTURES_LEAD_EXIT30", title: "永續領先 · 30 秒退出", rule: "獨立 Shadow：兩個連續 3 秒窗的 signed residual 同向確認，來源 age ≤500ms、Prediction age ≤1000ms；進場後第 30–45 秒第一個新鮮且足額 bid 全數退出。", tone: "blue", shadow: true },
  { id: "R_FUTURES_LEAD_DISTANCE", title: "永續領先 · 履約價距離", rule: "獨立 Shadow：相同雙窗確認，以履約價距離除以剩餘時間實現波動率估算終局機率；扣除進場價、滑價與費用後 edge ≥0.03 才進場並持有至結算。", tone: "purple", shadow: true },
  { id: "R_FUTURES_LEAD_EXIT30_DISTANCE", title: "永續領先 · 距離＋30 秒退出", rule: "獨立 Shadow：同時套用履約價終局機率 gate 與 30 秒足額 bid 退出，用來分離兩項改動的組合效果。", tone: "green", shadow: true },
];

function ResearchForwardPanel({ data, summaries, config, live, onConfig, onReset, resetStates }: {
  data?: ResearchForwardState | null;
  summaries: Record<StrategyId, Summary>;
  config: NumericConfig;
  live?: LiveM0WState | null;
  onConfig: (key: string, value: number | boolean) => void;
  onReset: (strategy: StrategyId) => void;
  resetStates: Partial<Record<StrategyId, ResetState>>;
}) {
  const cap = Number(config.strategy_research_shared_cap_usdt ?? data?.sharedCapitalCapUsdt ?? 100);
  const exposure = Number(data?.openExposureUsdt ?? 0);
  return <div className="m-exit-experiment research-forward-panel" role="tabpanel" id="research-panel" aria-labelledby="research-tab">
    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">FORWARD PAPER · FIVE PRIMARY + TEN SHADOWS</span><h3>五組主策略＋十二組獨立 Shadow</h3></div>
      <p>五組主策略共用 100 USDT 模擬曝險；十二組 Shadow 各自獨立做反事實對照。兩組持續校準 V2 只跟隨同市場已開出的來源 paper 單，且永遠不在 live executor 白名單。</p>
    </section>
    <section className="m-exit-rules" aria-label="研究策略資金安全設定">
      <div className="m-exit-rules-head"><div><span className="eyebrow">SHARED CAPITAL GUARD</span><h3>資金與成交安全</h3></div><span className="m-exit-api-state live">{data?.status ?? "等待 API"}</span></div>
      <div className="m-exit-rule-grid">
        <article><span>共享上限</span><strong>{money(cap)}</strong><small>五組 OPEN 本金合計</small></article>
        <article><span>目前曝險</span><strong>{money(exposure)}</strong><small>可用 {money(Math.max(0, cap - exposure))}</small></article>
        <article><span>最低金額</span><strong>{money(data?.minimumStakeUsdt ?? 2)}</strong><small>高於約 1.5 USDT 操作門檻</small></article>
        <article><span>成交條件</span><strong>第一檔完整深度</strong><small>禁止部分成交與重複使用深度</small></article>
        <article><span>Shadow 曝險</span><strong>{money(data?.shadowOpenExposureUsdt ?? 0)}</strong><small>不占主策略共享額度／深度</small></article>
      </div>
      <div className="fields">
        <NumberField label="五組共享曝險上限" name="strategy_research_shared_cap_usdt" value={cap} step={5} suffix="USDT" onChange={onConfig} />
        <NumberField label="模擬不利滑價" name="strategy_research_slippage_bps" value={Number(config.strategy_research_slippage_bps ?? 50)} step={5} suffix="bps" onChange={onConfig} />
        <NumberField label="最大價差" name="strategy_research_max_spread" value={Number(config.strategy_research_max_spread ?? .03)} step={.01} onChange={onConfig} />
        <NumberField label="最大訂單簿年齡" name="strategy_research_max_book_age_ms" value={Number(config.strategy_research_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
      </div>
    </section>
    <ConfirmationAddSummaryPanel
      title="一般策略 · 順勢確認加碼 Shadow"
      subtitle="來源紙上策略成立後先投入 1 USDT，只有盤口沿原方向走到 1.1×、1.2×、1.3×、1.4× 才各加 1 USDT。"
      overall={data?.confirmationAdd?.overall}
      groups={data?.confirmationAdd?.bySource}
      recent={data?.confirmationAdd?.recent}
      realFill={false}
    />
    <div className="m-exit-summary-grid research-strategy-grid">
      {RESEARCH_STRATEGY_CARDS.map(card => {
        const key = card.id.toLowerCase();
        const enabledKey = `strategy_${key}_enabled`;
        const stakeKey = `strategy_${key}_stake`;
        const enabled = Boolean(config[enabledKey] ?? data?.strategies?.[card.id]?.enabled ?? true);
        const stake = Number(config[stakeKey] ?? data?.strategies?.[card.id]?.stakeUsdt ?? 5);
        const summary = summaries[card.id] ?? EMPTY_SUMMARY;
        const settled = summary.wins + summary.losses;
        const parameters = data?.strategies?.[card.id]?.selectedBacktestParameters ?? {};
        const validation = data?.strategies?.[card.id]?.chronologicalValidation;
        const continuousCalibration = data?.strategies?.[card.id]?.continuousCalibration;
        const directionControl = data?.strategies?.[card.id]?.directionControl;
        const savedDirectionMode = directionControl?.configuredMode ?? "AUTO";
        const directionModeCode = Number(
          config.strategy_r_futures_lead_regime_reverse_3l_direction_mode
          ?? directionControl?.configuredModeCode
          ?? 0
        );
        const selectedDirectionMode: "AUTO" | "FORWARD" | "REVERSE" =
          directionModeCode === 1 ? "FORWARD" : directionModeCode === 2 ? "REVERSE" : "AUTO";
        const displayedDirection = selectedDirectionMode === "AUTO"
          ? directionControl?.automaticDirection ?? "FORWARD"
          : selectedDirectionMode;
        const directionDirty = selectedDirectionMode !== savedDirectionMode;
        const regimeLiveSelected = card.id === "R_FUTURES_LEAD_REGIME_REVERSE_3L"
          && (live?.strategies ?? []).includes(card.id);
        return <article className={`m-exit-card ${card.tone}`} key={card.id}>
          <div className="m-exit-card-head"><div><span className="eyebrow">{card.id} · {card.shadow ? "ISOLATED SHADOW" : "PAPER ONLY"}</span><h3>{card.title}</h3></div><button type="button" className={`toggle ${enabled ? "on" : ""}`} onClick={() => onConfig(enabledKey, !enabled)} aria-label={`${card.id}${enabled ? "停用" : "啟用"}`}><i /></button></div>
          <div className="m-exit-primary-stats">
            <div><span>已實現收益</span><strong className={summary.realized_pnl >= 0 ? "positive" : "negative"}>{money(summary.realized_pnl)}</strong></div>
            <div><span>勝率</span><strong>{settled ? ratio(summary.wins / settled) : "—"}</strong></div>
            <div><span>未結算</span><strong>{summary.totalOpen ?? summary.open ?? 0}</strong></div>
          </div>
          {card.id === "R_FUTURES_LEAD_REGIME_REVERSE_3L" && <div className={`regime-direction-control ${displayedDirection === "REVERSE" ? "reverse" : "forward"}`}>
            <div className="regime-direction-status">
              <span className="state-badge">目前有效：{displayedDirection === "REVERSE" ? "反方向" : "正方向"}</span>
              <span>AUTO 判定 <b>{directionControl?.automaticDirection === "REVERSE" ? "反方向" : "正方向"}</b></span>
              <span>控制模式 <b>{selectedDirectionMode === "AUTO" ? "自動" : selectedDirectionMode === "REVERSE" ? "強制反方向" : "強制正方向"}</b></span>
              <span>實單 <b>{regimeLiveSelected ? `已選用 · ${live?.strategyStakesUsdt?.[(live?.strategies ?? []).indexOf(card.id)] ?? "—"} USDT` : "未選用"}</b></span>
            </div>
            <div className="regime-direction-buttons" role="group" aria-label="3L 正反方向控制">
              {([0, 1, 2] as const).map(mode => {
                const label = mode === 0 ? "AUTO" : mode === 1 ? "強制正方向" : "強制反方向";
                return <button key={mode} type="button" className={directionModeCode === mode ? "active" : ""} onClick={() => onConfig("strategy_r_futures_lead_regime_reverse_3l_direction_mode", mode)}>{label}</button>;
              })}
            </div>
            <small className={directionDirty ? "pending" : ""}>{directionDirty ? "方向模式有未儲存變更；按上方「儲存參數」後才會同時套用 paper 與後續實單訊號。" : "此方向同時套用 paper 與後續實單訊號；不修改已送出的訂單。"}</small>
            <small>最近原始 Lead：{directionControl?.recentLeadResults?.length ? directionControl.recentLeadResults.map(result => result === "SETTLED_LOSS" ? "敗" : "勝").join(" → ") : "尚無三筆已結算歷史"}</small>
          </div>}
          {continuousCalibration && <div className={`continuous-calibration-state ${continuousCalibration.status === "READY" ? "ready" : "warmup"}`}>
            <span>持續校準 {continuousCalibration.status === "READY" ? "READY" : "WARMUP"}</span>
            <strong>{continuousCalibration.officialSourceSamples ?? 0} / {continuousCalibration.minimumHistory ?? 20} 筆官方來源結果</strong>
            <small>來源 {continuousCalibration.sourceStrategy} · 最近 {continuousCalibration.historyWindow ?? 200} 筆 · 同方向／同區間至少 {continuousCalibration.minimumBucketHistory ?? 5} 筆 · 下一市場才生效</small>
          </div>}
          <div className="fields"><NumberField label="每筆模擬本金" name={stakeKey} value={stake} step={1} suffix="USDT" onChange={onConfig} /></div>
          <p>{card.rule}</p>
          <small>{Object.entries(parameters).map(([name, value]) => `${name}=${value}`).join(" · ")}</small>
          {validation && <small>固定 chronological cohort：{validation.samples ?? 0}/{validation.preferred ?? 200}；狀態 {validation.status ?? "COLLECTING_MINIMUM"}。前 60 development、20 validation、20 holdout，101–200 為凍結確認樣本。</small>}
          <div className="summary-reset"><button type="button" onClick={() => onReset(card.id)}>重設統計起點</button><span>{resetStates[card.id]?.message ?? ""}</span></div>
        </article>;
      })}
    </div>
  </div>;
}

const FUTURES_LEAD_OBSERVER_CARDS: Array<{ id: StrategyId; version: "F1" | "V2" | "V3" | "V4" | "V6" | "AUTO_V6"; source: "R_FUTURES_LEAD" | "R_OFI" | "R_MICROPRICE" | "R_CALIBRATED_VALUE"; title: string; rule: string; tone: string }> = [
  { id: "R_FUTURES_LEAD_OBSERVER_F1", version: "F1", source: "R_FUTURES_LEAD", title: "原始 F1", rule: "F1 原始規則：歷史不得為 TREND、當輪 range score ≥ 1，且不得觸發 current trend veto。", tone: "cyan" },
  { id: "R_FUTURES_LEAD_OBSERVER_V2", version: "V2", source: "R_FUTURES_LEAD", title: "V2 Anti-exhaustion", rule: "忽略歷史 TREND 標籤；當輪 range score ≥ 1 且沒有 current trend veto 才通過。", tone: "mint" },
  { id: "R_FUTURES_LEAD_OBSERVER_V3", version: "V3", source: "R_FUTURES_LEAD", title: "V3 Crossover", rule: "當輪有效穿越至少 2 次，且沒有 current trend veto 才通過。", tone: "blue" },
  { id: "R_FUTURES_LEAD_OBSERVER_V4", version: "V4", source: "R_FUTURES_LEAD", title: "V4 Low ER", rule: "當輪 phase ER ≤ 0.35，且沒有 current trend veto；前 60 秒使用 short ER，之後使用 median 60s ER。", tone: "purple" },
  { id: "R_FUTURES_LEAD_OBSERVER_V6", version: "V6", source: "R_FUTURES_LEAD", title: "V6 Crossover · No Dual Touch", rule: "V3 條件再加上 UP／DOWN 不得都已觸價，用來避開雙邊耗盡盤。", tone: "amber" },
  { id: "R_OFI_OBSERVER_V3", version: "V3", source: "R_OFI", title: "R_OFI + V3", rule: "同市場 R_OFI 實際模擬單開出後，V3 通過才以原方向建立獨立 Shadow。", tone: "blue" },
  { id: "R_MICROPRICE_OBSERVER_V3", version: "V3", source: "R_MICROPRICE", title: "R_MICROPRICE + V3", rule: "同市場 R_MICROPRICE 實際模擬單開出後，V3 通過才以原方向建立獨立 Shadow。", tone: "purple" },
  { id: "R_MICROPRICE_OBSERVER_V6", version: "V6", source: "R_MICROPRICE", title: "R_MICROPRICE + V6", rule: "同市場 R_MICROPRICE 實際模擬單開出後，V6 通過才以原方向建立獨立 Shadow。", tone: "amber" },
  { id: "R_CALIBRATED_VALUE_OBSERVER_V6", version: "V6", source: "R_CALIBRATED_VALUE", title: "R_CALIBRATED_VALUE + V6", rule: "同市場 R_CALIBRATED_VALUE 實際模擬單開出後，V6 通過才以原方向建立獨立 Shadow；維持來源單的可執行價格、深度、費用與滑價。", tone: "green" },
  { id: "R_MICROPRICE_OBSERVER_AUTO_V6", version: "AUTO_V6", source: "R_MICROPRICE", title: "R_MICROPRICE · AUTO V6", rule: "只用當前市場以前的官方結算：30 筆近期窗與 100 筆慢窗都證明 V6 有效時套用，否則自動 bypass。", tone: "amber" },
  { id: "R_CALIBRATED_VALUE_OBSERVER_AUTO_V6", version: "AUTO_V6", source: "R_CALIBRATED_VALUE", title: "R_CALIBRATED_VALUE · AUTO V6", rule: "只用當前市場以前的官方結算：30 筆近期窗與 100 筆慢窗都證明 V6 有效時套用，否則自動 bypass。", tone: "green" },
];

function leadObserverAllows(version: "F1" | "V2" | "V3" | "V4" | "V6", gate?: MarketObserverGate) {
  if (!gate || gate.profile !== "F1" || gate.dataQualityStatus !== "READY") return false;
  if ((gate.historicalSampleCount ?? 0) < (gate.minSettledSamples ?? 6)) return false;
  const noVeto = gate.currentTrendVeto !== true;
  if (version === "F1") return gate.allowed === true && gate.status === "ALLOW";
  if (version === "V2") return (gate.currentRangeScore ?? 0) >= 1 && noVeto;
  if (version === "V3") return (gate.currentEffectiveCrossovers ?? 0) >= 2 && noVeto;
  const phaseEr = gate.currentPhase === "EARLY_0_60S" ? gate.currentShortEr : gate.currentMedianEr60s;
  if (version === "V4") return phaseEr != null && phaseEr <= .35 && noVeto;
  return (gate.currentEffectiveCrossovers ?? 0) >= 2 && noVeto && gate.currentBothSidesTouched !== true;
}

function FuturesLeadObserverPanel({ data, summaries, config, observer, onConfig, onReset, resetStates }: {
  data?: ResearchForwardState | null;
  summaries: Record<StrategyId, Summary>;
  config: NumericConfig;
  observer?: MarketObserverState | null;
  onConfig: (key: string, value: number | boolean) => void;
  onReset: (strategy: StrategyId) => void;
  resetStates: Partial<Record<StrategyId, ResetState>>;
}) {
  const gate = observer?.m01oGates?.F1;
  return <div className="m-exit-experiment research-forward-panel" role="tabpanel" id="lead-observer-panel" aria-labelledby="lead-observer-tab">
    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">OBSERVER · ELEVEN INDEPENDENT SHADOWS</span><h3>Observer 版本與策略組合觀測</h3></div>
      <p>固定 Observer 與兩組 AUTO V6 都只依賴同市場已實際開出的來源模擬單；AUTO 僅讀先前官方結算，仍是獨立 paper Shadow，不影響真金。</p>
    </section>
    <section className="m-exit-rules" aria-label="Futures Lead Observer 當輪狀態">
      <div className="m-exit-rules-head"><div><span className="eyebrow">CURRENT MARKET · FROZEN RULES</span><h3>當輪 Observer 證據</h3></div><span className={`m-exit-api-state ${gate?.dataQualityStatus === "READY" ? "live" : ""}`}>{gate?.dataQualityStatus ?? "等待資料"}</span></div>
      <div className="m-exit-rule-grid">
        <article><span>市場</span><strong>#{gate?.currentMarketId ?? "—"}</strong><small>F1 context</small></article>
        <article><span>歷史樣本</span><strong>{gate?.historicalSampleCount ?? 0} / {gate?.minSettledSamples ?? 6}</strong><small>{gate?.historicalState ?? "尚未判定"}</small></article>
        <article><span>Range score</span><strong>{gate?.currentRangeScore ?? 0}</strong><small>trend veto {gate?.currentTrendVeto ? "是" : "否"}</small></article>
        <article><span>有效穿越</span><strong>{gate?.currentEffectiveCrossovers ?? 0}</strong><small>雙邊觸價 {gate?.currentBothSidesTouched ? "是" : "否"}</small></article>
        <article><span>Phase ER</span><strong>{price(gate?.currentPhase === "EARLY_0_60S" ? gate?.currentShortEr : gate?.currentMedianEr60s)}</strong><small>{gate?.currentPhase ?? "—"}</small></article>
      </div>
    </section>
    <div className="m-exit-summary-grid research-strategy-grid">
      {FUTURES_LEAD_OBSERVER_CARDS.map(card => {
        const enabledKey = `strategy_${card.id.toLowerCase()}_enabled`;
        const stakeKey = `strategy_${card.id.toLowerCase()}_stake`;
        const enabled = Boolean(config[enabledKey] ?? data?.strategies?.[card.id]?.enabled ?? true);
        const stake = Number(config[stakeKey] ?? data?.strategies?.[card.id]?.stakeUsdt ?? 5);
        const summary = summaries[card.id] ?? EMPTY_SUMMARY;
        const settled = summary.wins + summary.losses;
        const validation = data?.strategies?.[card.id]?.chronologicalValidation;
        const autoV6 = data?.strategies?.[card.id]?.observerAutoV6;
        const allows = card.version === "AUTO_V6"
          ? autoV6?.mode === "BYPASS_V6" || (autoV6?.mode === "APPLY_V6" && leadObserverAllows("V6", gate))
          : leadObserverAllows(card.version, gate);
        return <article className={`m-exit-card ${card.tone}`} key={card.id}>
          <div className="m-exit-card-head"><div><span className="eyebrow">{card.id} · ISOLATED PAPER</span><h3>{card.title}</h3></div><button type="button" className={`toggle ${enabled ? "on" : ""}`} onClick={() => onConfig(enabledKey, !enabled)} aria-label={`${card.id}${enabled ? "停用" : "啟用"}`}><i /></button></div>
          <div className={`regime-direction-control ${allows ? "forward" : "reverse"}`}><span className="state-badge">{card.version === "AUTO_V6" ? `AUTO：${autoV6?.mode ?? "等待"}` : "當輪"} · {allows ? "通過" : "不下單"}</span></div>
          <div className="m-exit-primary-stats">
            <div><span>已實現收益</span><strong className={summary.realized_pnl >= 0 ? "positive" : "negative"}>{money(summary.realized_pnl)}</strong></div>
            <div><span>勝率</span><strong>{settled ? ratio(summary.wins / settled) : "—"}</strong></div>
            <div><span>交易／未結算</span><strong>{summary.trades} / {summary.totalOpen ?? summary.open ?? 0}</strong></div>
          </div>
          <div className="fields"><NumberField label="每筆模擬本金" name={stakeKey} value={stake} step={1} suffix="USDT" onChange={onConfig} /></div>
          <p>{card.rule}</p>
          <small>來源策略：{card.source}；Observer：{card.version}；paper only，不可轉送實單。</small>
          {card.version === "AUTO_V6" && <small>近期 {autoV6?.fastWindow?.samples ?? 0}/30 · 慢窗 {autoV6?.slowWindow?.samples ?? 0}/100 · {autoV6?.reason ?? "等待官方樣本"}</small>}
          <small>固定 chronological cohort：{validation?.samples ?? 0}/{validation?.preferred ?? 200}；{validation?.status ?? "COLLECTING_MINIMUM"}。門檻不依事後績效調整。</small>
          <div className="summary-reset"><button type="button" onClick={() => onReset(card.id)}>重設統計起點</button><span>{resetStates[card.id]?.message ?? ""}</span></div>
        </article>;
      })}
    </div>
  </div>;
}

const M_EXIT_VARIANTS: Array<{ suffix: string; title: string; kicker: string; rule: string; tone: string }> = [
  { suffix: "T60", title: "固定 0.60 全倉止盈", kicker: "FULL EXIT · 0.60", rule: "同側買價觸及 0.60 後掛出全部剩餘 shares；深度不足時保留未成交部位。", tone: "mint" },
  { suffix: "T70", title: "固定 0.70 全倉止盈", kicker: "FULL EXIT · 0.70", rule: "同側買價觸及 0.70 後掛出全部剩餘 shares；深度不足時保留未成交部位。", tone: "blue" },
  { suffix: "T80", title: "固定 0.80 全倉止盈", kicker: "FULL EXIT · 0.80", rule: "同側買價觸及 0.80 後掛出全部剩餘 shares；深度不足時保留未成交部位。", tone: "cyan" },
  { suffix: "T90", title: "固定 0.90 全倉止盈", kicker: "FULL EXIT · 0.90", rule: "同側買價觸及 0.90 後掛出全部剩餘 shares；深度不足時保留未成交部位。", tone: "amber" },
  { suffix: "T98", title: "固定 0.98 全倉止盈", kicker: "FULL EXIT · 0.98", rule: "同側買價觸及 0.98 後掛出全部剩餘 shares；深度不足時保留未成交部位。", tone: "orange" },
  { suffix: "P50", title: "每上漲 50% 賣一半", kicker: "SCALE OUT · 50% / 50%", rule: "以最終進場 VWAP 為準：1.5 倍賣原始成交量 50%，2.0 倍賣剩餘 50%。", tone: "purple" },
  { suffix: "P10", title: "每上漲 10% 賣 10%", kicker: "SCALE OUT · 10% / 10%", rule: "以最終進場 VWAP 為準，1.1～2.0 倍每階賣原始成交量 10%。", tone: "magenta" },
  { suffix: "REV", title: "反向穿越即退出", kicker: "STARTPRICE REVERSAL", rule: "標的反向穿越正式 startPrice 即退出；持倉側 bid 先站上 0.50、再跌回 0.50 以下也退出。", tone: "rose" },
];

function ratio(value: number | null | undefined) {
  const safe = finite(value);
  if (safe == null) return "—";
  const percent = Math.abs(safe) <= 1 ? safe * 100 : safe;
  return `${percent.toFixed(1)}%`;
}

function derivedRatio(filled: number | null | undefined, requested: number | null | undefined) {
  const safeFilled = finite(filled);
  const safeRequested = finite(requested);
  return safeFilled == null || safeRequested == null || safeRequested <= 0 ? null : safeFilled / safeRequested;
}

function rowValue(row: MExitDetailRow, ...keys: string[]) {
  for (const key of keys) if (row[key] != null && row[key] !== "") return row[key];
  return null;
}

function rowNumber(row: MExitDetailRow, ...keys: string[]) {
  const value = rowValue(row, ...keys);
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function rowText(row: MExitDetailRow, ...keys: string[]) {
  const value = rowValue(row, ...keys);
  return value == null ? "—" : String(value);
}

function exitRowVariant(row: MExitDetailRow) {
  return rowText(row, "variant", "strategy", "experimentId", "experiment_id");
}

function MExitExperimentPanel({ data, signalFamily = "M", variantSuffixes, pausedView = false, config, onConfig }: {
  data?: MExitExperimentState | null;
  signalFamily?: "M" | "M0";
  variantSuffixes?: readonly string[];
  pausedView?: boolean;
  config?: NumericConfig;
  onConfig?: (key: string, value: number | boolean) => void;
}) {
  const summaries = data?.summaries ?? {};
  const hasPayload = Boolean(data);
  const isM0 = signalFamily === "M0";
  const strategyPrefix = isM0 ? "M0X" : "MX";
  const selectedVariants = M_EXIT_VARIANTS.filter(variant => !variantSuffixes || variantSuffixes.includes(variant.suffix));
  const selectedVariantIds = new Set(selectedVariants.map(variant => `${strategyPrefix}_${variant.suffix}`));
  const positions = Array.isArray(data?.positions) ? data.positions.filter(row => selectedVariantIds.has(exitRowVariant(row))).slice(0, 100) : [];
  const orders = Array.isArray(data?.orders) ? data.orders.filter(row => selectedVariantIds.has(exitRowVariant(row))).slice(0, 100) : [];
  const fills = Array.isArray(data?.fills) ? data.fills.filter(row => selectedVariantIds.has(exitRowVariant(row))).slice(0, 100) : [];

  return <div className="m-exit-experiment">
    <section className={`strategy-family-intro m-exit-intro ${pausedView ? "paused" : ""}`}>
      <div><span className="eyebrow">{pausedView ? "TEMPORARILY STOPPED" : `${signalFamily} EXIT BRANCH · PAPER ONLY`}</span><h3>{pausedView ? `${selectedVariants.length} 個已停止的 ${signalFamily} 出場分支` : `${signalFamily} 出場分支實驗`}</h3></div>
      <p>{pausedView ? `依使用者決定停止整個 ${signalFamily} 出場分支系列；不再建立新 intent，既有歷史與未平倉結算仍保留。` : `${selectedVariants.length} 組共用${isM0 ? " M0 固定種子隨機" : "原 M"}方向訊號：只在開盤後前 10 秒建立限價不高於 0.50 的進場 intent。每組要求數量與 M 出場實驗完全相同；窗口結束仍未填滿會標記 EXPIRED_UNFILLED 或 EXPIRED_PARTIAL。`}</p>
    </section>

    <section className="m-exit-rules" aria-labelledby="m-exit-rules-title">
      <div className="m-exit-rules-head"><div><span className="eyebrow">SHARED ENTRY · DIFFERENT EXITS</span><h3 id="m-exit-rules-title">共同進場，{selectedVariants.length} 種退出方法</h3></div><span className={`m-exit-api-state ${hasPayload ? "live" : ""}`}>{data?.status ?? (hasPayload ? "READY" : "等待 API")}</span></div>
      <div className="m-exit-rule-grid">
        <article><span>訊號</span><strong>{isM0 ? "M0 隨機方向" : "原 M 方向"}</strong><small>{isM0 ? "固定種子＋market ID，可重現" : "第一個有效非零偏離"}</small></article>
        <article><span>進場窗口</span><strong>開盤 10 秒</strong><small>逾時不追價</small></article>
        <article><span>最高進場限價</span><strong>0.50</strong><small>ask 高於 0.50 不買</small></article>
        <article><span>成交模型</span><strong>依可見深度</strong><small>保留未成交與部分成交</small></article>
      </div>
      <p>頁面同時追蹤進場／出場數量填單率、完全未成交 intent 比例、部分成交 intent 比例與尚未退出的 shares，避免只看已實現收益而忽略成交品質。</p>
    </section>

    <div className="m-exit-summary-grid">
      {selectedVariants.map(variant => {
        const variantId = `${strategyPrefix}_${variant.suffix}`;
        const enabledKey = `strategy_${variantId.toLowerCase()}_enabled`;
        const enabled = Boolean(config?.[enabledKey] ?? true);
        const summary = summaries[variantId];
        const entryFillRatio = summary?.entryFillRatio ?? derivedRatio(summary?.filledEntryQty, summary?.requestedEntryQty);
        const exitFillRatio = summary?.exitFillRatio ?? derivedRatio(summary?.filledExitQty, summary?.requestedExitQty);
        const fullyUnfilled = summary?.fullyUnfilledIntentRatio ?? summary?.unfilledRatio;
        const partialFill = summary?.partialFillIntentRatio ?? summary?.partialFillRatio;
        return <article className={`m-exit-card ${variant.tone}`} key={variantId}>
          <div className="m-exit-card-head"><div><span className="eyebrow">{variantId} · {variant.kicker}</span><h3>{variant.title}</h3></div><div className="m-exit-card-actions"><span className="m-exit-id">{variantId}</span>{pausedView && onConfig && <button type="button" className={`toggle ${enabled ? "on" : ""}`} onClick={() => onConfig(enabledKey, !enabled)} aria-label={`${variantId}${enabled ? "停用" : "啟用"}`}><i /></button>}</div></div>
          <div className="m-exit-primary-stats">
            <div><span>已實現收益</span><strong className={(summary?.realizedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(summary?.realizedPnl)}</strong></div>
            <div><span>未平倉</span><strong>{summary?.openPositions == null ? "—" : Math.round(summary.openPositions)}</strong></div>
            <div><span>剩餘 shares</span><strong>{decimal(summary?.remainingShares, 4)}</strong></div>
          </div>
          <div className="m-exit-fill-grid">
            <div><span>已成交交易</span><strong>{summary?.trades == null ? "—" : Math.round(summary.trades)}</strong><small>完全未成交 intent 不計</small></div>
            <div><span>勝 / 負</span><strong>{summary?.wins == null || summary?.losses == null ? "—" : `${Math.round(summary.wins)} / ${Math.round(summary.losses)}`}</strong><small>未平倉暫不判定</small></div>
            <div><span>進場填單率</span><strong>{ratio(entryFillRatio)}</strong><small>{decimal(summary?.filledEntryQty, 4)} / {decimal(summary?.requestedEntryQty, 4)}</small></div>
            <div><span>出場填單率</span><strong>{ratio(exitFillRatio)}</strong><small>{decimal(summary?.filledExitQty, 4)} / {decimal(summary?.requestedExitQty, 4)}</small></div>
            <div><span>完全未成交 intent</span><strong>{ratio(fullyUnfilled)}</strong><small>窗口結束仍為零成交</small></div>
            <div><span>部分成交 intent</span><strong>{ratio(partialFill)}</strong><small>有成交但未填滿</small></div>
          </div>
          <p>{variant.rule}</p>
        </article>;
      })}
    </div>

    <section className="m-exit-details" aria-label={`${pausedView ? "已停止" : signalFamily} 出場分支部位與委託明細`}>
      <div className="m-exit-detail-heading"><div><span className="eyebrow">OPEN RISK</span><h3>未平倉部位</h3></div><span>{positions.length} 筆</span></div>
      <div className="table-scroll"><table className="m-exit-table"><thead><tr><th>分支</th><th>市場</th><th>方向</th><th>進場均價</th><th>目標 / 規則</th><th>原始 shares</th><th>剩餘 shares</th><th>狀態</th><th>開倉時間</th></tr></thead><tbody>
        {positions.length === 0 ? <tr><td colSpan={9} className="empty">目前沒有未平倉部位，或後端尚未提供 positions。</td></tr> : positions.map((row, index) => <tr key={String(rowValue(row, "id", "positionId", "position_id") ?? `position-${index}`)}><td><span className="m-exit-variant-pill">{exitRowVariant(row)}</span></td><td>#{rowText(row, "marketId", "market_id")}</td><td className={rowText(row, "side") === "UP" ? "positive" : "amber-text"}>{rowText(row, "side")}</td><td>{price(rowNumber(row, "averageEntryPrice", "avgEntryPrice", "entryPrice", "average_entry_price", "entry_price"))}</td><td>{rowText(row, "targetPrice", "target_price", "exitRule", "exit_rule")}</td><td>{decimal(rowNumber(row, "entryShares", "shares", "entry_shares"), 4)}</td><td>{decimal(rowNumber(row, "remainingShares", "remaining_shares"), 4)}</td><td><span className="trade-status">{rowText(row, "status")}</span></td><td>{fmtTimeMs(rowValue(row, "openedAt", "opened_at", "createdAt", "created_at") as string | number | null)}</td></tr>)}
      </tbody></table></div>

      <div className="m-exit-detail-heading split"><div><span className="eyebrow">ORDER INTENTS</span><h3>委託與未成交明細</h3></div><span>{orders.length} 筆</span></div>
      <div className="table-scroll"><table className="m-exit-table"><thead><tr><th>分支</th><th>市場</th><th>階段</th><th>方向</th><th>限價</th><th>要求數量</th><th>成交數量</th><th>未成交數量</th><th>狀態</th><th>建立時間</th></tr></thead><tbody>
        {orders.length === 0 ? <tr><td colSpan={10} className="empty">尚無委託 intent；EXPIRED_UNFILLED、EXPIRED_PARTIAL 與已填滿委託都會列在這裡。</td></tr> : orders.map((row, index) => <tr key={String(rowValue(row, "id", "orderId", "order_id") ?? `order-${index}`)}><td><span className="m-exit-variant-pill">{exitRowVariant(row)}</span></td><td>#{rowText(row, "marketId", "market_id")}</td><td>{rowText(row, "leg", "action", "phase", "orderType", "order_type")}</td><td>{rowText(row, "side")}</td><td>{price(rowNumber(row, "limitPrice", "price", "limit_price"))}</td><td>{decimal(rowNumber(row, "requestedQty", "requestedShares", "requested_qty", "requested_shares"), 4)}</td><td>{decimal(rowNumber(row, "filledQty", "filledShares", "filled_qty", "filled_shares"), 4)}</td><td>{decimal(rowNumber(row, "remainingQty", "unfilledQty", "remaining_qty", "unfilled_qty"), 4)}</td><td><span className="trade-status">{rowText(row, "status")}</span></td><td>{fmtTimeMs(rowValue(row, "createdAt", "created_at", "timestamp") as string | number | null)}</td></tr>)}
      </tbody></table></div>

      <div className="m-exit-detail-heading split"><div><span className="eyebrow">ACTUAL PAPER FILLS</span><h3>部分成交紀錄</h3></div><span>{fills.length} 筆</span></div>
      <div className="table-scroll"><table className="m-exit-table"><thead><tr><th>分支</th><th>市場</th><th>階段</th><th>方向</th><th>成交價</th><th>成交數量</th><th>手續費</th><th>成交時間</th></tr></thead><tbody>
        {fills.length === 0 ? <tr><td colSpan={8} className="empty">尚無模擬成交；這裡不會顯示未實際填到的數量。</td></tr> : fills.map((row, index) => <tr key={String(rowValue(row, "id", "fillId", "fill_id") ?? `fill-${index}`)}><td><span className="m-exit-variant-pill">{exitRowVariant(row)}</span></td><td>#{rowText(row, "marketId", "market_id")}</td><td>{rowText(row, "leg", "action", "phase")}</td><td>{rowText(row, "side")}</td><td>{price(rowNumber(row, "price", "fillPrice", "fill_price"))}</td><td>{decimal(rowNumber(row, "qty", "shares", "filledQty", "filled_qty"), 4)}</td><td>{money(rowNumber(row, "fee", "fees"), 4)}</td><td>{fmtTimeMs(rowValue(row, "filledAt", "filled_at", "timestamp") as string | number | null)}</td></tr>)}
      </tbody></table></div>
      {data?.updatedAt != null && <p className="m-exit-updated">最後同步 {fmtTimeMs(data.updatedAt)}</p>}
    </section>
  </div>;
}

function PairArbPanel({ data, config, onConfig }: {
  data?: PairArbState | null;
  config: NumericConfig;
  onConfig: (key: string, value: number | boolean) => void;
}) {
  const variants = [
    { id: "PAIR_ARB_010" as const, threshold: .010, tone: "cyan" },
    { id: "PAIR_ARB_QC_015" as const, threshold: .015, tone: "cyan" },
    { id: "PAIR_ARB_020" as const, threshold: .020, tone: "mint" },
    { id: "PAIR_ARB_RISK_020" as const, threshold: -.020, tone: "rose" },
  ];
  const trades = Array.isArray(data?.recentTrades) ? data.recentTrades : [];
  const diagnostics = data?.diagnostics;
  const latestEvaluation = diagnostics?.latestMarket;
  return <div className="m-exit-experiment">
    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">COMPLEMENTARY PAIR · PAPER ONLY</span><h3>UP＋DOWN 互補鎖利測試</h3></div>
      <p>同一張有效 Prediction 快照，逐檔扣除 UP／DOWN 可見賣盤數量；只有該檔扣除雙腿 taker fee 後仍達門檻才成交。深度不足會部分成交，不再把要求數量直接視為成交。</p>
    </section>

    <section className="m-exit-rules" aria-labelledby="pair-arb-rules-title">
      <div className="m-exit-rules-head"><div><span className="eyebrow">VISIBLE DEPTH · FEE-AWARE EDGE</span><h3 id="pair-arb-rules-title">共同訊號，三個風險門檻</h3></div><span className={`m-exit-api-state ${data ? "live" : ""}`}>{data?.status ?? "等待 API"}</span></div>
      <div className="m-exit-rule-grid">
        <article><span>組合</span><strong>同 shares 買雙側</strong><small>結算固定回收 1 USDT / share</small></article>
        <article><span>PAIR_ARB_010</span><strong>淨優勢 ≥ 0.010</strong><small>較多機會、較小安全邊際</small></article>
        <article><span>PAIR_ARB_020</span><strong>淨優勢 ≥ 0.020</strong><small>較少機會、較大安全邊際</small></article>
        <article><span>有限風險版本</span><strong>淨優勢 ≥ -0.020</strong><small>最多承擔約 0.02 USDT / share 含費風險</small></article>
        <article><span>成交模型</span><strong>逐檔深度撮合</strong><small>雙側同 shares、按各檔數量部分成交</small></article>
      </div>
      <div className="fields">
        <NumberField label="每市場雙側總本金" name="strategy_pair_arb_stake" value={Number(config.strategy_pair_arb_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
        <NumberField label="雙側簿最大時差" name="strategy_pair_arb_max_book_skew_ms" value={Number(config.strategy_pair_arb_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
        <NumberField label="訂單簿最大年齡" name="strategy_pair_arb_max_book_age_ms" value={Number(config.strategy_pair_arb_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
      </div>
      <p>壓力損益會額外扣除每腿 0.005 或 0.010 的價格滑點；它用來暴露雙腿非原子執行風險，不是成交保證。</p>
    </section>

    <div className="m-exit-summary-grid">
      {variants.map(variant => {
        const summary = data?.summaries?.[variant.id];
        const enabledKey = `strategy_${variant.id.toLowerCase()}_enabled`;
        const enabled = Boolean(config[enabledKey] ?? true);
        return <article className={`m-exit-card ${variant.tone}`} key={variant.id}>
          <div className="m-exit-card-head"><div><span className="eyebrow">MIN NET EDGE · {variant.threshold.toFixed(3)}</span><h3>{variant.id}</h3></div><div className="m-exit-card-actions"><span className="m-exit-id">PAPER</span><button type="button" className={`toggle ${enabled ? "on" : ""}`} onClick={() => onConfig(enabledKey, !enabled)} aria-label={`${variant.id}${enabled ? "停用" : "啟用"}`}><i /></button></div></div>
          <div className="m-exit-primary-stats">
            <div><span>鎖定收益</span><strong className={(summary?.lockedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(summary?.lockedPnl, 4)}</strong></div>
            <div><span>交易數</span><strong>{summary?.trades ?? 0}</strong></div>
            <div><span>含費 ROI</span><strong>{summary?.roi == null ? "—" : ratio(summary.roi)}</strong></div>
          </div>
          <div className="m-exit-fill-grid">
            <div><span>每腿滑點 0.005</span><strong className={(summary?.stressedPnl005 ?? 0) >= 0 ? "positive" : "negative"}>{money(summary?.stressedPnl005, 4)}</strong><small>雙腿共扣 0.010 / share</small></div>
            <div><span>每腿滑點 0.010</span><strong className={(summary?.stressedPnl010 ?? 0) >= 0 ? "positive" : "negative"}>{money(summary?.stressedPnl010, 4)}</strong><small>雙腿共扣 0.020 / share</small></div>
            <div><span>平均淨優勢</span><strong>{price(summary?.averageNetEdge)}</strong><small>每 share、已扣雙腿費用</small></div>
            <div><span>最大回撤</span><strong>{money(summary?.maxDrawdown, 4)}</strong><small>鎖定收益序列</small></div>
            <div><span>數量填單率</span><strong>{ratio(summary?.quantityFillRate)}</strong><small>{decimal(summary?.filledShares, 4)} / {decimal(summary?.requestedShares, 4)} shares · 部分 {summary?.partialFills ?? 0}</small></div>
            <div><span>平均簿品質</span><strong>{decimal(summary?.averageBookSkewMs, 1)} ms</strong><small>age {decimal(summary?.averageBookAgeMs, 1)} ms</small></div>
          </div>
        </article>;
      })}
    </div>

    <section className="m-exit-rules" aria-labelledby="pair-arb-diagnostics-title">
      <div className="m-exit-rules-head"><div><span className="eyebrow">DUAL-BOOK EVALUATION HEALTH</span><h3 id="pair-arb-diagnostics-title">互補評估診斷</h3></div><span className={`m-exit-api-state ${diagnostics ? "live" : ""}`}>{diagnostics ? "雙簿資料已接通" : "等待雙簿資料"}</span></div>
      <div className="m-exit-rule-grid">
        <article><span>評估快照／市場</span><strong>{diagnostics?.evaluations ?? 0}／{diagnostics?.marketsEvaluated ?? 0}</strong><small>有效雙簿 {diagnostics?.validBookEvaluations ?? 0} 次</small></article>
        <article><span>最佳含費淨優勢</span><strong>{price(diagnostics?.bestNetEdge)}</strong><small>010 機會 {diagnostics?.eligibleSnapshots?.PAIR_ARB_010 ?? 0} 次</small></article>
        <article><span>低於最低風險門檻</span><strong>{diagnostics?.rejectionCounts?.edgeBelow010 ?? 0}</strong><small>簿過期 {diagnostics?.rejectionCounts?.bookAgeExceeded ?? 0} · 時差過大 {diagnostics?.rejectionCounts?.bookSkewExceeded ?? 0}</small></article>
        <article><span>最新市場</span><strong>{latestEvaluation?.marketId == null ? "—" : `#${latestEvaluation.marketId}`}</strong><small>{latestEvaluation?.reason ?? "尚未評估"} · edge {price(latestEvaluation?.netEdge)}</small></article>
      </div>
      <p>資料源：獨立 UP／DOWN outcome token 訂單簿並行請求；只有兩簿時間差、年齡、可見深度與含費淨優勢全部通過才記為紙上成交。</p>
    </section>

    <section className="m-exit-details" aria-label="互補測試最近配對交易">
      <div className="m-exit-detail-heading"><div><span className="eyebrow">ORDERBOOK-QUANTITY PAPER FILLS</span><h3>最近互補配對</h3></div><span>{trades.length} 筆</span></div>
      <div className="table-scroll"><table className="m-exit-table"><thead><tr><th>時間</th><th>策略</th><th>市場</th><th>UP / DOWN VWAP</th><th>要求 / 成交 shares</th><th>淨優勢</th><th>鎖定收益</th><th>滑點 .005 / .010</th><th>skew / age</th><th>剩餘秒數</th></tr></thead><tbody>
        {trades.length === 0 ? <tr><td colSpan={10} className="empty">等待同一有效快照出現含費正套利，兩組不會混入 M 系列帳本。</td></tr> : trades.map(trade => <tr key={`${trade.strategy}-${trade.id}`}><td>{fmtTimeMs(trade.signal_timestamp)}</td><td><span className="m-exit-variant-pill">{trade.strategy}</span></td><td>#{trade.market_id}</td><td>{price(trade.up_fill_vwap ?? trade.up_ask)} / {price(trade.down_fill_vwap ?? trade.down_ask)}</td><td>{decimal(trade.requested_shares, 4)} / {decimal(trade.shares, 4)}</td><td>{price(trade.net_edge_per_share)}</td><td className={trade.locked_pnl >= 0 ? "positive" : "negative"}>{money(trade.locked_pnl, 4)}</td><td>{money(trade.stressed_pnl_005, 4)} / {money(trade.stressed_pnl_010, 4)}</td><td>{decimal(trade.book_skew_ms, 1)} / {decimal(trade.book_age_ms, 1)} ms</td><td>{decimal(trade.seconds_left, 1)}</td></tr>)}
      </tbody></table></div>
      <p className="m-exit-updated">模型：{data?.fillModel ?? "synchronized_dual_token_rest_visible_depth"} · 原子成交假設 {data?.atomicExecutionAssumed === false ? "否" : "是"} · 最後同步 {fmtTimeMs(data?.updatedAt)}</p>
    </section>
  </div>;
}

function LiveM0WPanel({ data, controlState, rulesSaveState, rulesDraft, rulesDirty, onControl, onRulesUpdate, onRulesReset, onRulesSave }: {
  data?: LiveM0WState | null;
  controlState: string;
  rulesSaveState: string;
  rulesDraft: LiveRules;
  rulesDirty: boolean;
  onControl: (action: "pause" | "resume") => void;
  onRulesUpdate: <K extends keyof LiveRules>(key: K, value: LiveRules[K]) => void;
  onRulesReset: () => void;
  onRulesSave: (rules: LiveRules) => Promise<boolean>;
}) {
  const orders = Array.isArray(data?.orders) ? data.orders : [];
  const events = Array.isArray(data?.events) ? data.events.slice(0, 20) : [];
  const summary = data?.summary ?? {};
  const performance = data?.performance ?? {};
  const performances = data?.performances ?? {};
  const portfolio = data?.portfolio ?? {};
  const orderLatency = data?.orderLatency;
  const attemptSummary = data?.attemptSummary;
  const attemptOutcomes = attemptSummary?.outcomes ?? {};
  const attemptLatency = attemptSummary?.latency ?? {};
  const localPriceCheck = data?.lastLocalPriceCheck;
  const depthCheck = data?.lastDepthCheck;
  const quoteAttempt = data?.lastQuoteAttempt;
  const autoRedeem = data?.autoRedeem ?? {};
  const hourlyGuard = data?.hourlyGuard ?? {};
  const qcPolicy = data?.policy?.pairQc015;
  const redeemSummary = autoRedeem.summary ?? {};
  const redeems = Array.isArray(autoRedeem.redeems) ? autoRedeem.redeems : [];
  const [redeemPage, setRedeemPage] = useState(1);
  const [orderPage, setOrderPage] = useState(1);
  const redeemPageCount = Math.max(1, Math.ceil(redeems.length / LIVE_TABLE_PAGE_SIZE));
  const orderPageCount = Math.max(1, Math.ceil(orders.length / LIVE_TABLE_PAGE_SIZE));
  const activeRedeemPage = Math.min(redeemPage, redeemPageCount);
  const activeOrderPage = Math.min(orderPage, orderPageCount);
  const visibleRedeems = redeems.slice(
    (activeRedeemPage - 1) * LIVE_TABLE_PAGE_SIZE,
    activeRedeemPage * LIVE_TABLE_PAGE_SIZE,
  );
  const visibleOrders = orders.slice(
    (activeOrderPage - 1) * LIVE_TABLE_PAGE_SIZE,
    activeOrderPage * LIVE_TABLE_PAGE_SIZE,
  );
  const status = String(data?.status ?? "WAITING").toUpperCase();
  const live = Boolean(data?.armed && data?.runtimeEnabled);
  const danger = /ERROR|BLOCKED|REJECTED|AMBIGUOUS/.test(status);
  const hourlyGuardStatus = String(hourlyGuard.status ?? "WAITING").toUpperCase();
  const hourlyBlocked = hourlyGuard.blocked === true;
  const hourlyGuardClass = hourlyBlocked ? "blocked" : hourlyGuardStatus === "ALLOW" ? "allow" : "waiting";
  const hourlyReasons = Array.isArray(hourlyGuard.reasons) ? hourlyGuard.reasons : [];
  useEffect(() => {
    setRedeemPage(current => Math.min(current, redeemPageCount));
  }, [redeemPageCount]);
  useEffect(() => {
    setOrderPage(current => Math.min(current, orderPageCount));
  }, [orderPageCount]);
  const tablePager = (
    label: string,
    page: number,
    totalItems: number,
    setPage: (page: number) => void,
  ) => {
    const totalPages = Math.max(1, Math.ceil(totalItems / LIVE_TABLE_PAGE_SIZE));
    const firstItem = totalItems === 0 ? 0 : (page - 1) * LIVE_TABLE_PAGE_SIZE + 1;
    const lastItem = Math.min(page * LIVE_TABLE_PAGE_SIZE, totalItems);
    return <nav className="live-table-pager" aria-label={`${label}分頁`}>
      <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>← 上一頁</button>
      <span><strong>第 {page} / {totalPages} 頁</strong><small>顯示 {firstItem}–{lastItem} / {totalItems} 筆 · 每頁最多 10 筆</small></span>
      <button type="button" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一頁 →</button>
    </nav>;
  };
  const saveRules = async () => {
    if (!rulesDirty) return;
    const strategySummary = rulesDraft.strategies.map((strategy, index) => `${strategy} ${rulesDraft.strategyStakesUsdt[index]} USDT`).join("、");
    const observerSummary = rulesDraft.strategies.map((strategy, index) => (
      `${strategy} Observer ${rulesDraft.strategyObserverEnabled[index] ? rulesDraft.strategyObserverVersions[index] : "關閉"}`
    )).join("、");
    const drawdownSummary = rulesDraft.strategies.map((strategy, index) => (
      `${strategy} 回撤控制 ${rulesDraft.strategyDrawdownControlEnabled[index] ? "開啟" : "關閉"}`
    )).join("、");
    const cooldownSummary = rulesDraft.strategies.map((strategy, index) => (
      `${strategy} 兩連敗冷卻 ${rulesDraft.strategyLossCooldownEnabled[index] ? "開啟" : "關閉"}`
    )).join("、");
    const reliabilitySummary = `可靠候選 ${rulesDraft.reliabilityGateTags.length ? rulesDraft.reliabilityGateTags.join("＋") : "全部關閉"}`;
    const summary = `${strategySummary}、${observerSummary}、${drawdownSummary}、${cooldownSummary}、${reliabilitySummary}、M0 時段勝率至少 ${rulesDraft.minHourlyWinRatePct}%、一勝一敗率最多 ${rulesDraft.maxHourlyWinThenLossRatePct}%`;
    if (!window.confirm(`確定更新正式實單規則？\n\n${summary}\n\n新規則只影響之後的新訊號，不會修改既有訂單。`)) return;
    await onRulesSave(rulesDraft);
  };
  const strategyOptions = data?.supportedStrategies ?? Object.keys(LIVE_STRATEGY_LABELS);
  const activeStrategies = data?.strategies ?? (data?.strategy ? [data.strategy] : ["M0W"]);
  const balanceTotal = (data?.balances ?? []).filter(item => item.enabled)
    .reduce((total, item) => total + Number(item.availableBalance ?? 0), 0);
  const fillRatio = (order: LiveM0WOrder) => {
    const raw = finite(order.fill_percentage);
    if (raw == null) return "—";
    return `${(Math.abs(raw) <= 1 ? raw * 100 : raw).toFixed(1)}%`;
  };
  const updateStrategySlot = (index: number, value: string) => {
    const strategies = [...rulesDraft.strategies];
    const stakes = [...rulesDraft.strategyStakesUsdt];
    const observers = [...rulesDraft.strategyObserverEnabled];
    const observerVersions = [...rulesDraft.strategyObserverVersions];
    const drawdownControls = [...rulesDraft.strategyDrawdownControlEnabled];
    const lossCooldowns = [...rulesDraft.strategyLossCooldownEnabled];
    if (!value) {
      strategies.splice(index);
      stakes.splice(index);
      observers.splice(index);
      observerVersions.splice(index);
      drawdownControls.splice(index);
      lossCooldowns.splice(index);
    } else {
      strategies[index] = value;
      stakes[index] = stakes[index] ?? stakes[0] ?? 1;
      observers[index] = LIVE_OBSERVER_STRATEGIES.has(value)
        ? observers[index] ?? false
        : false;
      observerVersions[index] = observerVersions[index] ?? "F1";
      drawdownControls[index] = drawdownControls[index] ?? false;
      lossCooldowns[index] = lossCooldowns[index] ?? false;
    }
    onRulesUpdate("strategies", strategies);
    onRulesUpdate("strategyStakesUsdt", stakes);
    onRulesUpdate("strategyObserverEnabled", observers);
    onRulesUpdate("strategyObserverVersions", observerVersions);
    onRulesUpdate("strategyDrawdownControlEnabled", drawdownControls);
    onRulesUpdate("strategyLossCooldownEnabled", lossCooldowns);
    onRulesUpdate("reliabilityGateTags", rulesDraft.reliabilityGateTags.filter(tag => {
      const definition = RELIABILITY_LIVE_GATE_OPTIONS.find(option => option.id === tag);
      return Boolean(definition && strategies.includes(definition.strategy));
    }));
    if (index === 0) {
      onRulesUpdate("strategy", strategies[0]);
      onRulesUpdate("maxStakeUsdt", stakes[0]);
      onRulesUpdate("futuresLeadObserverEnabled", observers[0]);
      onRulesUpdate("futuresLeadObserverVersion", observerVersions[0]);
    }
  };
  const updateStrategyStake = (index: number, value: number) => {
    const stakes = [...rulesDraft.strategyStakesUsdt];
    stakes[index] = value;
    onRulesUpdate("strategyStakesUsdt", stakes);
    if (index === 0) onRulesUpdate("maxStakeUsdt", value);
  };
  const updateStrategyObserver = (index: number, enabled: boolean) => {
    const observers = [...rulesDraft.strategyObserverEnabled];
    observers[index] = enabled;
    onRulesUpdate("strategyObserverEnabled", observers);
    if (index === 0) onRulesUpdate("futuresLeadObserverEnabled", enabled);
  };
  const updateStrategyObserverVersion = (
    index: number,
    version: LiveRules["futuresLeadObserverVersion"],
  ) => {
    const versions = [...rulesDraft.strategyObserverVersions];
    versions[index] = version;
    onRulesUpdate("strategyObserverVersions", versions);
    if (index === 0) onRulesUpdate("futuresLeadObserverVersion", version);
  };
  const updateStrategyDrawdownControl = (index: number, enabled: boolean) => {
    const controls = [...rulesDraft.strategyDrawdownControlEnabled];
    controls[index] = enabled;
    onRulesUpdate("strategyDrawdownControlEnabled", controls);
  };
  const updateStrategyLossCooldown = (index: number, enabled: boolean) => {
    const controls = [...rulesDraft.strategyLossCooldownEnabled];
    controls[index] = enabled;
    onRulesUpdate("strategyLossCooldownEnabled", controls);
  };
  const updateReliabilityGate = (tag: ReliabilityCandidateTagId, enabled: boolean) => {
    const tags = enabled
      ? [...new Set([...rulesDraft.reliabilityGateTags, tag])]
      : rulesDraft.reliabilityGateTags.filter(item => item !== tag);
    onRulesUpdate("reliabilityGateTags", tags);
  };

  return <div className="live-m0w-console" role="tabpanel" id="live-m0w-panel" aria-labelledby="live-m0w-tab">
    <section className="live-warning" aria-label="M0W 真實資金警告">
      <div><span className="live-real-badge">REAL MONEY</span><h3>{activeStrategies.join(" ＋ ")} 正式實單</h3><p>這個分頁只顯示 Binance 正式 Prediction 訂單與獨立實單帳本；規則修改只影響後續新訊號。</p></div>
      <div className="live-control">
        <span className={`live-engine-state ${live ? "armed" : danger ? "danger" : ""}`}><i />{status}</span>
        <button type="button" className={live ? "pause" : "resume"} disabled={controlState === "送出中"} onClick={() => onControl(live ? "pause" : "resume")}>{controlState === "送出中" ? "處理中…" : live ? "立即暫停實單" : "在本機恢復實單"}</button>
        <small>{controlState}</small>
      </div>
    </section>

    <section className="live-rules-editor" aria-labelledby="live-rules-title" onKeyDown={event => { if (event.key === "Enter") event.preventDefault(); }}>
      <div className="live-rules-head"><div><span className="eyebrow">PERSISTENT LIVE RULES</span><h3 id="live-rules-title">正式實單規則</h3></div><span className={rulesDirty ? "dirty" : "synced"}>{rulesDirty ? "有未儲存變更" : rulesSaveState}</span></div>
      <div className="live-rules-grid">
        {LIVE_STRATEGY_SLOT_INDEXES.map(index => {
          const selected = rulesDraft.strategies[index] ?? "";
          const slotEnabled = index === 0 || Boolean(rulesDraft.strategies[index - 1]);
          return <label key={`live-strategy-${index}`}><span>實單策略 {index + 1}</span><select aria-label={`實單策略 ${index + 1}`} disabled={!slotEnabled} value={selected} onChange={event => updateStrategySlot(index, event.target.value)}>{index > 0 && <option value="">不啟用第{LIVE_STRATEGY_SLOT_NAMES[index]}策略</option>}{strategyOptions.filter(strategy => strategy === selected || !rulesDraft.strategies.includes(strategy)).map(strategy => <option key={strategy} value={strategy}>{LIVE_STRATEGY_LABELS[strategy] ?? strategy}</option>)}</select><div className="live-rule-number"><input type="number" min={data?.configurableStakeRangeUsdt?.min ?? .01} max={data?.configurableStakeRangeUsdt?.max ?? 100} step="0.01" disabled={!selected} value={rulesDraft.strategyStakesUsdt[index] ?? rulesDraft.strategyStakesUsdt[0]} onChange={event => updateStrategyStake(index, Number(event.target.value))} /><b>USDT</b></div><small>{index === 3 ? "第四格固定不使用 Observer；只執行所選策略本身" : index === 1 ? "Lead＋Reverse 仍會先取得兩腿 signed quote；其餘策略可獨立執行" : `策略 ${index + 1} 每筆／每組互補單的獨立上限`}</small></label>;
        })}
        {RELIABILITY_LIVE_GATE_OPTIONS.map(option => {
          const supported = rulesDraft.strategies.includes(option.strategy);
          const enabled = rulesDraft.reliabilityGateTags.includes(option.id);
          return <label className="live-reliability-gate" key={`live-reliability-${option.id}`}><span>{option.title}</span><select aria-label={`${option.id} 實單可靠候選`} disabled={!supported} value={enabled ? "enabled" : "disabled"} onChange={event => updateReliabilityGate(option.id, event.target.value === "enabled")}><option value="disabled">不套用（預設）</option><option value="enabled">套用為實單放行條件</option></select><small>{option.id} · {option.condition}；{supported ? "缺少欄位時 fail closed" : `只適用 ${option.strategy}`}</small></label>;
        })}
        {LIVE_OBSERVER_SLOT_INDEXES.flatMap(index => {
          const selected = rulesDraft.strategies[index];
          const observerSupported = Boolean(selected && LIVE_OBSERVER_STRATEGIES.has(selected));
          return [
            <label key={`live-observer-${index}`}><span>策略 {index + 1} Observer</span><select aria-label={`策略 ${index + 1} 是否使用 Observer`} disabled={!observerSupported} value={rulesDraft.strategyObserverEnabled[index] ? "enabled" : "disabled"} onChange={event => updateStrategyObserver(index, event.target.value === "enabled")}><option value="disabled">不使用 Observer</option><option value="enabled">使用 Observer</option></select><small>{observerSupported ? "與其他策略槽位獨立；資料缺失時 fail closed" : "此策略目前不套用 live Observer"}</small></label>,
            <label key={`live-observer-version-${index}`}><span>策略 {index + 1} Observer 版本</span><select aria-label={`策略 ${index + 1} Observer 版本`} disabled={!observerSupported} value={rulesDraft.strategyObserverVersions[index] ?? "F1"} onChange={event => updateStrategyObserverVersion(index, event.target.value as LiveRules["futuresLeadObserverVersion"])}>{(["F1", "V2", "V3", "V4", "V6"] as const).map(version => <option key={version} value={version}>{version === "F1" ? "原始 F1" : version}</option>)}</select><small>可先選版本再開啟 Observer；門檻不會回頭挑歷史最佳值</small></label>,
          ];
        })}
        {LIVE_STRATEGY_SLOT_INDEXES.map(index => {
          const selected = rulesDraft.strategies[index];
          return <label key={`live-drawdown-control-${index}`}><span>策略 {index + 1} 回撤控制器</span><select aria-label={`策略 ${index + 1} 是否使用回撤控制器`} disabled={!selected} value={rulesDraft.strategyDrawdownControlEnabled[index] ? "enabled" : "disabled"} onChange={event => updateStrategyDrawdownControl(index, event.target.value === "enabled")}><option value="disabled">不使用回撤控制器</option><option value="enabled">使用回撤控制器</option></select><small>{selected ? "各槽位獨立；歷史或訊號快照缺失時 fail closed" : "請先選擇此槽位的實單策略"}</small></label>;
        })}
        {LIVE_STRATEGY_SLOT_INDEXES.map(index => {
          const selected = rulesDraft.strategies[index];
          const cooldownState = data?.strategyLossCooldownStates?.[index];
          const cooldownStatus = cooldownState?.cooldownPending
            ? `等待冷卻下一個市場；目前連敗 ${cooldownState.consecutiveLosses ?? 2}`
            : `目前連敗 ${cooldownState?.consecutiveLosses ?? 0}`;
          return <label key={`live-loss-cooldown-${index}`}><span>策略 {index + 1} 兩連敗冷卻</span><select aria-label={`策略 ${index + 1} 是否使用兩連敗冷卻`} disabled={!selected} value={rulesDraft.strategyLossCooldownEnabled[index] ? "enabled" : "disabled"} onChange={event => updateStrategyLossCooldown(index, event.target.value === "enabled")}><option value="disabled">不使用兩連敗冷卻</option><option value="enabled">連敗兩次後跳過下一市場</option></select><small>{selected ? `${cooldownStatus}；只計此策略官方結算，跳過後歸零` : "請先選擇此槽位的實單策略"}</small></label>;
        })}
        <label><span>M0 每小時最低勝率</span><div className="live-rule-number"><input type="number" min="0" max="100" step="0.1" value={rulesDraft.minHourlyWinRatePct} onChange={event => onRulesUpdate("minHourlyWinRatePct", Number(event.target.value))} /><b>%</b></div><small>實際勝率低於此值便暫停該小時</small></label>
        <label><span>M0 一勝一敗率上限</span><div className="live-rule-number"><input type="number" min="0" max="100" step="0.1" value={rulesDraft.maxHourlyWinThenLossRatePct} onChange={event => onRulesUpdate("maxHourlyWinThenLossRatePct", Number(event.target.value))} /><b>%</b></div><small>實際比率高於此值便暫停該小時</small></label>
      </div>
      <div className="live-rules-actions"><p>未儲存草稿會保留在這個瀏覽器，切換頁籤或重新整理不會消失；只有按下「確認並套用」才會改動後端實單規則。策略必須在「M 系列主實驗」保持啟用。同一個私有區域網路中的手機也能正式儲存；恢復實單仍只能在這台電腦操作。</p><div><button type="button" className="secondary" onClick={onRulesReset} disabled={!rulesDirty}>放棄草稿</button><button type="button" onClick={saveRules} disabled={!rulesDirty || rulesSaveState === "儲存中…"}>{rulesSaveState === "儲存中…" ? "儲存中…" : "確認並套用新規則"}</button></div></div>
    </section>

    <section className={`live-hourly-guard ${hourlyGuardClass}`} aria-label="M0 每小時實單閘門">
      <div className="live-hourly-guard-head">
        <div><span className="eyebrow">M0 HOURLY LIVE GUARD · ASIA/TAIPEI</span><h3>M0 每小時實單閘門</h3></div>
        <strong>{hourlyBlocked ? "本時段暫停下單" : hourlyGuardStatus === "ALLOW" ? "本時段允許下單" : "等待統計判定"}</strong>
      </div>
      <div className="live-hourly-guard-metrics">
        <div><span>台北時段</span><strong>{hourlyGuard.label ?? "—"}</strong><small>每小時自動重新判定</small></div>
        <div><span>M0 獨立勝率</span><strong>{hourlyGuard.winRatePct == null ? "—" : `${hourlyGuard.winRatePct.toFixed(2)}%`}</strong><small>低於 {hourlyGuard.minWinRatePct ?? 50}% 即停單 · n={hourlyGuard.settledTrades ?? 0}</small></div>
        <div><span>一勝一敗率</span><strong>{hourlyGuard.winThenLossRatePct == null ? "—" : `${hourlyGuard.winThenLossRatePct.toFixed(2)}%`}</strong><small>超過 {hourlyGuard.maxWinThenLossRatePct ?? 50}% 即停單 · {hourlyGuard.winThenLossCount ?? 0}/{hourlyGuard.winThenLossOpportunities ?? 0}</small></div>
      </div>
      <p>{hourlyReasons.length > 0 ? hourlyReasons.join("；") : "兩項門檻皆未觸發；剛好 50% 仍允許下單。缺少一勝一敗樣本時不以該條件停單。"}</p>
    </section>

    <section className="live-policy-grid" aria-label="實單不可變規則">
      <article><span>實單訊號策略</span><strong>{activeStrategies.join(" ＋ ")}</strong><small>最多同時執行四種；各策略每市場各自防重複</small></article>
      <article><span>每市場硬上限</span><strong className="live-money">{activeStrategies.map((strategy, index) => `${strategy} ${money(data?.strategyStakesUsdt?.[index] ?? data?.maxStakeUsdt ?? 1)}`).join(" · ")}</strong><small>各策略使用已儲存上限，不會因最低額自動加大</small></article>
      <article><span>訂單方式</span><strong>{data?.orderType ?? "LIMIT"} · {data?.timeInForce ?? "GTC"}</strong><small>MARKET 約需 1.5 USDT，故一律禁用；timeout／5xx 標為不確定，不盲目重送</small></article>
      <article><span>防重複</span><strong>每策略／每市場一次</strong><small>四個策略可各執行一次；各自不重送</small></article>
    </section>

    <section className="live-health-grid">
      {activeStrategies.includes("PAIR_ARB_QC_015") && <article><span>QC SIGNED-QUOTE SHADOW</span><strong>{qcPolicy?.shadowSamples ?? 0} / {qcPolicy?.minimumShadowSamples ?? 200}</strong><small>QC placement stays locked until ready; other live strategies keep running</small></article>}
      <article><span>執行 / SAS</span><strong className={live ? "positive" : danger ? "negative" : "amber-text"}>{data?.armed ? "ARMED" : "NOT ARMED"}</strong><small>SAS {data?.sasStatus ?? "UNVERIFIED"} · quote {data?.quoteAccess ?? "UNVERIFIED"}</small></article>
      <article><span>Prediction 餘額</span><strong>{money(balanceTotal)}</strong><small>{(data?.balances ?? []).map(item => `${item.accountType} ${money(item.availableBalance)}`).join(" · ") || "等待同步"}</small></article>
      <article><span>當日剩餘配額</span><strong>{money(data?.quota?.remainingDailyLimit)}</strong><small>上限 {money(data?.quota?.dailyLimit)}</small></article>
      <article><span>帳戶正式持倉 / 帳戶總 PnL</span><strong>{portfolio.activePositionsCount ?? "—"} / <b className={(portfolio.totalPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(portfolio.totalPnl)}</b></strong><small>全帳戶未實現 {money(portfolio.totalUnrealizedPnl)} · 已實現 {money(portfolio.totalRealizedPnl)}</small></article>
      <article><span>訊號 / 已送單</span><strong>{summary.signals ?? 0} / {summary.submitted ?? 0}</strong><small>成交 {summary.filledOrders ?? 0} · 部分 {summary.partialOrders ?? 0}</small></article>
      <article><span>實際成交額 / 訂單費用</span><strong>{money(summary.filledUsdt)} / {money(summary.fees, 4)}</strong><small>拒單 {summary.rejected ?? 0} · 不確定 {summary.ambiguous ?? 0}</small></article>
      <article><span>最近一筆送單總延遲</span><strong>{orderLatency?.totalMs == null ? "—" : `${decimal(orderLatency.totalMs, 1)} ms`}</strong><small>{orderLatency ? `queue ${decimal(orderLatency.queueMs, 1)} · 預檢 ${decimal(orderLatency.preQuoteMs, 1)} · quote 網路 ${decimal(orderLatency.quoteNetworkMs, 1)} · quote→送出 ${orderLatency.quoteToPlaceMs == null ? "—" : decimal(orderLatency.quoteToPlaceMs, 1)} · 送單網路 ${orderLatency.placeNetworkMs == null ? "—" : decimal(orderLatency.placeNetworkMs, 1)} ms` : "等待第一筆新訂單量測"}</small><small>{orderLatency ? `${orderLatency.strategy ?? "—"} ${orderLatency.side ?? ""} · #${orderLatency.marketId ?? "—"} · ${orderLatency.outcome ?? "—"}` : "只量測實單 worker 收到訊號後的本機與 Binance 往返時間"}</small></article>
      <article><span>最近 100 筆結果分類</span><strong>{attemptSummary?.sampleSize ?? 0} 筆</strong><small>送出 {attemptOutcomes.submitted ?? 0} · stale {attemptOutcomes.blockedStaleBook ?? 0} · 已超價 {attemptOutcomes.blockedLocalPriceMoved ?? 0} · 深度 {attemptOutcomes.blockedInsufficientCapacity ?? 0}</small><small>quote 拒絕 {attemptOutcomes.quoteRejected ?? 0} · place 拒絕 {attemptOutcomes.placementRejected ?? 0} · ambiguous {attemptOutcomes.placementAmbiguous ?? 0}</small></article>
      <article><span>端到端延遲分布</span><strong>{attemptLatency.eventToPlaceResponseMs?.p95 == null ? "—" : `${decimal(attemptLatency.eventToPlaceResponseMs.p95, 1)} ms p95`}</strong><small>p50 {decimal(attemptLatency.eventToPlaceResponseMs?.p50, 1)} · p90 {decimal(attemptLatency.eventToPlaceResponseMs?.p90, 1)} · max {decimal(attemptLatency.eventToPlaceResponseMs?.max, 1)} ms</small><small>queue p95 {decimal(attemptLatency.queueMs?.p95, 1)} · pre-quote p95 {decimal(attemptLatency.preQuoteMs?.p95, 1)} · quote net p95 {decimal(attemptLatency.quoteNetworkMs?.p95, 1)} ms</small></article>
      <article><span>最新本機價格／深度檢查</span><strong>{String(localPriceCheck?.status ?? "—")}</strong><small>ask {decimal(localPriceCheck?.latestLocalAsk, 4)} · ceiling {decimal(localPriceCheck?.maximumExecutionPrice, 4)} · age {decimal(localPriceCheck?.latestLocalBookAgeMs, 1)} ms</small><small>depth {String(depthCheck?.status ?? "—")} · coverage {decimal(depthCheck?.depthCoverageRatio, 3)} · levels {depthCheck?.depthLevelsConsumed ?? "—"} · VWAP {depthCheck?.vwapAvailable ? decimal(depthCheck?.estimatedVwap, 4) : "unavailable"}</small></article>
      <article><span>最新 signed quote</span><strong>{quoteAttempt?.quoteAttempts == null ? "—" : `${quoteAttempt.quoteAttempts} 次`}</strong><small>re-quote {quoteAttempt?.requoteTriggered ? "是" : "否"} · first {decimal(quoteAttempt?.firstQuoteAveragePrice, 4)} · second {decimal(quoteAttempt?.secondQuoteAveragePrice, 4)}</small><small>{String(quoteAttempt?.finalOutcome ?? "等待新 attempt")}</small></article>
      <article><span>Drawdown Spot 重驗</span><strong>{data?.drawdownReferenceSource ?? "—"}</strong><small>reference age {data?.drawdownReferenceAgeMs == null ? "—" : `${decimal(data.drawdownReferenceAgeMs, 1)} ms`}</small><small>trade ≤ 2000 ms；book microprice／midpoint ≤ 500 ms</small></article>
      <article><span>Live SQLite</span><strong>{data?.sqliteJournalMode ?? "—"} · {data?.sqliteSynchronous ?? "—"}</strong><small>busy timeout {data?.sqliteBusyTimeoutMs ?? "—"} ms · {data?.liveDbPath ?? "—"}</small><small className={data?.sqlitePathWarning ? "negative" : "positive"}>{data?.sqlitePathWarning ?? "本機路徑未偵測到同步／網路磁碟警告"}</small></article>
      {activeStrategies.map(strategy => {
        const strategyPerformance = performances[strategy]
          ?? (strategy === data?.strategy ? performance : {});
        return <article key={strategy} className="live-strategy-performance">
          <span>{strategy} 獨立實單收益</span>
          <strong className={(strategyPerformance.profitUsdt ?? 0) >= 0 ? "positive" : "negative"}>{money(strategyPerformance.profitUsdt, 6)}</strong>
          <small>勝率 {strategyPerformance.winRatePct == null ? "—" : `${strategyPerformance.winRatePct.toFixed(2)}%`} · ROI {signed(strategyPerformance.roiPct, 2, "%")}</small>
          <small>{strategyPerformance.wins ?? 0} 勝 / {strategyPerformance.losses ?? 0} 負 · 已結算 {strategyPerformance.settledTrades ?? 0} · 未結算 {strategyPerformance.unsettledTrades ?? 0}</small>
        </article>;
      })}
      <article><span>自動領取</span><strong className={/ERROR|ATTENTION|BLOCKED/.test(String(autoRedeem.status ?? "")) ? "negative" : "positive"}>{autoRedeem.enabled ? autoRedeem.status ?? "WAITING" : "DISABLED"}</strong><small>待領 {autoRedeem.claimableCount ?? 0} 筆 / {money(autoRedeem.claimableAmount)} · 已完成 {redeemSummary.completed ?? 0} 筆</small></article>
      <article><span>累計自動領取</span><strong>{money(redeemSummary.redeemedValue)}</strong><small>處理中 {redeemSummary.pending ?? 0} · 失敗 {redeemSummary.failed ?? 0} · 不確定 {redeemSummary.ambiguous ?? 0}</small></article>
    </section>

    <section className="live-meta">
      <div><span>Key 來源</span><strong>{data?.credentialSource ?? "—"}</strong></div>
      <div><span>Wallet</span><strong>{data?.wallet ?? "—"}</strong></div>
      <div><span>最後預檢</span><strong>{fmtTimeMs(data?.lastPreflightAt)}</strong></div>
      <div><span>最後訂單同步</span><strong>{fmtTimeMs(data?.lastOrderSyncAt)}</strong></div>
      <div><span>最後策略結算同步</span><strong>{fmtTimeMs(data?.lastSettlementSyncAt)}</strong></div>
      <div><span>最後實單訊號</span><strong>#{data?.lastSignalMarketId ?? "—"} · {fmtTimeMs(data?.lastSignalAt)}</strong></div>
      <div><span>最後領取掃描</span><strong>{fmtTimeMs(autoRedeem.lastScanAt)}</strong></div>
      <div><span>最後領取成功</span><strong>{fmtTimeMs(autoRedeem.lastSuccessAt)}</strong></div>
    </section>

    {data?.lastError && <aside className="live-error" role="alert"><strong>實單執行需要處理</strong><span>{data.lastError}</span></aside>}
    {autoRedeem.lastError && <aside className="live-error" role="alert"><strong>自動領取需要核對</strong><span>{autoRedeem.lastError}</span></aside>}

    <section className="live-orders live-redemptions">
      <div className="section-heading"><div><span className="eyebrow">BINANCE AUTO REDEEM · 60 SECOND DELAY</span><h3>勝方結算領取</h3></div><span className="record-count">{redeems.length} 筆領取紀錄</span></div>
      <div className="table-scroll"><table className="live-order-table"><thead><tr><th>市場</th><th>結果</th><th>結算時間</th><th>Shares</th><th>可領 USDT</th><th>嘗試次數</th><th>Batch / Request</th><th>Tx Hash</th><th>交易所狀態</th><th>本地狀態</th><th>完成時間</th></tr></thead><tbody>
        {redeems.length === 0 ? <tr><td colSpan={11} className="empty">目前沒有自動領取紀錄；只會處理 Binance 明確標記為 PENDING_CLAIM 且 canClaim=true 的勝方倉位。</td></tr> : visibleRedeems.map(redeem => <tr key={redeem.id}>
          <td>#{redeem.market_id ?? "—"}</td><td>{redeem.outcome_name || "—"}</td><td>{fmtTimeMs(redeem.end_date_ms)}</td><td>{decimal(redeem.shares, 8)}</td><td>{money(redeem.claimable_value, 8)}</td><td>{redeem.attempt_count ?? 0}</td><td>{redeem.batch_id || redeem.request_id || "—"}</td><td>{redeem.tx_hash ? `${redeem.tx_hash.slice(0, 10)}…${redeem.tx_hash.slice(-6)}` : "—"}</td><td>{redeem.exchange_status || "—"}</td><td><span className={`trade-status ${redeem.status.toLowerCase()}`}>{redeem.status.replaceAll("_", " ")}</span>{redeem.error_message && <small className="live-row-error">{redeem.error_message}</small>}</td><td>{fmtTimeMs(redeem.completed_at)}</td>
        </tr>)}
      </tbody></table></div>
      {tablePager("勝方結算領取", activeRedeemPage, redeems.length, setRedeemPage)}
    </section>

    <section className="live-orders">
      <div className="section-heading"><div><span className="eyebrow">BINANCE LIVE ORDERS · SEPARATE SQLITE</span><h3>正式訂單、成交與損益</h3></div><span className="record-count">{orders.length} 筆實單紀錄</span></div>
      <div className="table-scroll"><table className="live-order-table"><thead><tr><th>訊號時間</th><th>策略</th><th>市場</th><th>方向</th><th>限價</th><th>硬上限</th><th>Binance Order ID</th><th>成交 USDT</th><th>成交 shares</th><th>填單率</th><th>訂單狀態</th><th>策略結算</th><th className="right">策略收益</th><th className="right">策略 ROI</th></tr></thead><tbody>
        {orders.length === 0 ? <tr><td colSpan={14} className="empty">尚無符合目前實單策略與時段閘門的正式訊號。選定策略必須在主實驗保持啟用，並收到有效 Prediction 簿事件才會送單。</td></tr> : visibleOrders.map(order => <tr key={order.id}>
          <td>{fmtTimeMs(order.signal_at)}</td><td><strong>{order.strategy}</strong></td><td>#{order.market_id}</td><td className={order.side === "UP" ? "positive" : "amber-text"}>{order.side}</td><td>{price(order.signal_price)}</td><td>{money(order.max_stake_usdt)}</td><td>{order.order_id ?? "—"}</td><td>{money(order.filled_usdt_amount)}</td><td>{decimal(order.filled_share_qty, 6)}</td><td>{fillRatio(order)}</td><td><span className={`trade-status ${order.status.toLowerCase()}`}>{order.status.replaceAll("_", " ")}</span>{order.error_message && <small className="live-row-error">{order.error_message}</small>}</td><td><span className={`trade-status ${(order.settlement_result ?? "pending").toLowerCase()}`}>{order.settlement_result ? `${order.settlement_result}${order.settlement_status === "MANUAL_EXIT_FILLED" ? " · 手動平倉" : ""}` : order.filled_usdt_amount ? "待結算" : "—"}</span></td><td className={`right ${order.settlement_pnl_usdt == null ? "" : order.settlement_pnl_usdt >= 0 ? "positive" : "negative"}`}>{money(order.settlement_pnl_usdt, 6)}</td><td className={`right ${order.settlement_roi_pct == null ? "" : order.settlement_roi_pct >= 0 ? "positive" : "negative"}`}>{signed(order.settlement_roi_pct, 2, "%")}</td>
        </tr>)}
      </tbody></table></div>
      {tablePager("正式訂單、成交與損益", activeOrderPage, orders.length, setOrderPage)}
    </section>

    <section className="live-events">
      <div className="section-heading"><div><span className="eyebrow">AUDIT TRAIL</span><h3>實單事件紀錄</h3></div><span className="record-count">最近 {events.length} 筆</span></div>
      <div className="live-event-list">{events.length === 0 ? <p className="empty">等待實單引擎事件。</p> : events.map(event => <article key={event.id} className={event.level.toLowerCase()}><time>{fmtTimeMs(event.timestamp)}</time><strong>{event.event_type}</strong><span>{event.market_id ? `#${event.market_id} · ` : ""}{event.message}</span></article>)}</div>
    </section>
  </div>;
}

function M0HourlyPanel({ data }: { data?: M0HourlyPerformance | null }) {
  const byHour = new Map((data?.hours ?? []).map(bucket => [bucket.hour, bucket]));
  const hours: M0HourlyBucket[] = Array.from({ length: 24 }, (_, hour) => byHour.get(hour) ?? ({
    hour, label: `${String(hour).padStart(2, "0")}:00–${String(hour).padStart(2, "0")}:59`,
    settledTrades: 0, wins: 0, losses: 0, winRatePct: null,
    averageWinStreak: null, averageLossStreak: null,
    winThenLossCount: 0, winThenLossOpportunities: 0, winThenLossRatePct: null,
  }));
  const populated = hours.filter(bucket => bucket.settledTrades > 0 && bucket.winRatePct != null);
  const qualified = populated.filter(bucket => bucket.settledTrades >= 10);
  const ranked = qualified.length > 0 ? qualified : populated;
  const best = [...ranked].sort((a, b) => (b.winRatePct ?? -1) - (a.winRatePct ?? -1) || b.settledTrades - a.settledTrades)[0];
  const worst = [...ranked].sort((a, b) => (a.winRatePct ?? 101) - (b.winRatePct ?? 101) || b.settledTrades - a.settledTrades)[0];
  const totalWins = hours.reduce((sum, bucket) => sum + bucket.wins, 0);
  const totalSettled = hours.reduce((sum, bucket) => sum + bucket.settledTrades, 0);
  const overallRate = totalSettled > 0 ? (totalWins / totalSettled) * 100 : null;
  const maxSamples = Math.max(1, ...hours.map(bucket => bucket.settledTrades));
  const rateClass = (bucket: M0HourlyBucket) => bucket.winRatePct == null ? "empty" : bucket.winRatePct >= 55 ? "high" : bucket.winRatePct < 45 ? "low" : "neutral";
  const bucketSummary = (bucket?: M0HourlyBucket) => bucket ? `${bucket.label} · ${bucket.winRatePct?.toFixed(1)}% · n=${bucket.settledTrades}` : "樣本不足";

  return <section className="m0-hourly-panel" aria-labelledby="m0-hourly-title">
    <div className="m0-hourly-head">
      <div><span className="eyebrow">M0 · 24 HOUR DISTRIBUTION</span><h3 id="m0-hourly-title">M0 每小時獨立勝率</h3></div>
      <div className="m0-hourly-zone"><strong>{data?.timezone ?? "Asia/Taipei"}</strong><span>{data?.utcOffset ?? "+08:00"} · 24 小時制</span></div>
    </div>
    <p className="m0-hourly-note">依 M0 進場時間分桶，只計正式結算的 SETTLED_WIN／SETTLED_LOSS；每個小時各自顯示勝率、平均連勝、平均連敗及一勝一敗率，未結算與無成交不進入分母。{data?.resetAt ? `目前沿用 M0 測量起點 ${fmtDateTime(data.resetAt)}。` : "目前使用全部 M0 歷史。"}</p>
    <div className="m0-hourly-analysis">
      <div><span>整體已結算勝率</span><strong>{overallRate == null ? "—" : `${overallRate.toFixed(2)}%`}</strong><small>{totalWins} 勝 / {totalSettled - totalWins} 負 · n={totalSettled}</small></div>
      <div><span>{qualified.length ? "最佳時段（n≥10）" : "最佳時段"}</span><strong className="positive">{best ? `${best.winRatePct?.toFixed(1)}%` : "—"}</strong><small>{bucketSummary(best)}</small></div>
      <div><span>{qualified.length ? "最低時段（n≥10）" : "最低時段"}</span><strong className="negative">{worst ? `${worst.winRatePct?.toFixed(1)}%` : "—"}</strong><small>{bucketSummary(worst)}</small></div>
      <div><span>已有樣本時段</span><strong>{populated.length} / 24</strong><small>{fmtDateTime(data?.firstOpenedAt)} ～ {fmtDateTime(data?.lastOpenedAt)}</small></div>
    </div>
    <div className="m0-hourly-grid" role="list" aria-label="M0 台北時間每小時勝率">
      {hours.map(bucket => <article key={bucket.hour} className={rateClass(bucket)} role="listitem" title={`${bucket.label}，${bucket.wins} 勝 ${bucket.losses} 負；平均連勝 ${bucket.averageWinStreak?.toFixed(2) ?? "—"}，平均連敗 ${bucket.averageLossStreak?.toFixed(2) ?? "—"}，一勝一敗率 ${bucket.winThenLossRatePct?.toFixed(1) ?? "—"}%`}>
        <span>{bucket.label}</span>
        <strong>{bucket.winRatePct == null ? "—" : `${bucket.winRatePct.toFixed(1)}%`}</strong>
        <small>{bucket.wins} 勝 / {bucket.losses} 負 · n={bucket.settledTrades}</small>
        <dl className="m0-hourly-patterns">
          <div><dt>平均連勝</dt><dd>{bucket.averageWinStreak == null ? "—" : bucket.averageWinStreak.toFixed(2)}</dd></div>
          <div><dt>平均連敗</dt><dd>{bucket.averageLossStreak == null ? "—" : bucket.averageLossStreak.toFixed(2)}</dd></div>
          <div title="上一個相鄰 M0 已勝時，本局轉敗的比例；方向層面等同 M0W 理論敗率"><dt>一勝一敗率</dt><dd>{bucket.winThenLossRatePct == null ? "—" : `${bucket.winThenLossRatePct.toFixed(1)}%`}<small>{bucket.winThenLossOpportunities > 0 ? `${bucket.winThenLossCount}/${bucket.winThenLossOpportunities}` : "無可進場樣本"}</small></dd></div>
        </dl>
        <div className="m0-hourly-sample"><i style={{ width: `${(bucket.settledTrades / maxSamples) * 100}%` }} /></div>
      </article>)}
    </div>
    <p className="m0-hourly-foot">色彩以 50% 勝率為中心：綠色 ≥55%，紅色 &lt;45%。平均連勝／連敗只串接同一台北日期、同一小時內相鄰的 5 分鐘局，跨小時、跨日或資料缺口都會重新起算；一勝一敗率則把本局歸到其進場小時，計算「上一個相鄰 M0 勝、本局敗」÷「上一個相鄰 M0 勝」，是方向層面的 M0W 理論敗率。判讀時應同時看 n 與一勝一敗分母。</p>
  </section>;
}

function NumberField({ label, name, value, step = 0.01, suffix, onChange }: { label: string; name: string; value: number; step?: number; suffix?: string; onChange: (name: string, value: number) => void }) {
  return <label className="field"><span>{label}</span><div><input aria-label={label} type="number" min="0" step={step} value={value} onChange={e => onChange(name, Number(e.target.value))} />{suffix && <b>{suffix}</b>}</div></label>;
}

function StrategyCard({ id, title, kicker, accent, summary, config, strategyH, marketObserver, resetState, onConfig, onReset }: { id: StrategyId; title: string; kicker: string; accent: string; summary: Summary; config: NumericConfig; strategyH?: StrategyHState; marketObserver?: MarketObserverState | null; resetState?: ResetState; onConfig: (key: string, value: number | boolean) => void; onReset: (strategy: StrategyId) => void }) {
  const prefix = `strategy_${id.toLowerCase()}`;
  const enabled = Boolean(config[`${prefix}_enabled`]);
  const experimental = id.startsWith("M") || id === "B2" || id === "E" || id === "F" || id === "E2" || id === "G" || id === "H" || id === "I" || id === "J" || id === "K" || id === "L";
  const mFields = (key: string, extra: ReactNode = null, shared = false) => <>
    <NumberField label={shared ? "M7 共用進場窗" : "開盤進場窗口"} name={`strategy_${key}_entry_window_seconds`} value={Number(config[`strategy_${key}_entry_window_seconds`] ?? 10)} step={1} suffix="秒" onChange={onConfig} />
    <NumberField label={shared ? "M7 共用本金" : "單筆本金上限"} name={`strategy_${key}_stake`} value={Number(config[`strategy_${key}_stake`] ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
    <NumberField label={shared ? "M7 共用簿時差" : "訂單簿最大時差"} name={`strategy_${key}_max_book_skew_ms`} value={Number(config[`strategy_${key}_max_book_skew_ms`] ?? 500)} step={50} suffix="ms" onChange={onConfig} />
    <NumberField label={shared ? "M7 共用簿年齡" : "訂單簿最大年齡"} name={`strategy_${key}_max_book_age_ms`} value={Number(config[`strategy_${key}_max_book_age_ms`] ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
    {extra}
  </>;
  let fields;
  let logic;

  if (id === "A") {
    fields = <>
      <NumberField label="觀察窗" name="strategy_a_window_seconds" value={Number(config.strategy_a_window_seconds ?? 120)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最小價差" name="strategy_a_min_gap" value={Number(config.strategy_a_min_gap ?? .2)} onChange={onConfig} />
      <NumberField label="最高買價" name="strategy_a_max_entry" value={Number(config.strategy_a_max_entry ?? .2)} onChange={onConfig} />
      <NumberField label="掛單目標" name="strategy_a_target" value={Number(config.strategy_a_target ?? .4)} onChange={onConfig} />
      <NumberField label="每筆本金" name="strategy_a_stake" value={Number(config.strategy_a_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = "前兩分鐘只買較便宜的一側，需同時符合價差與最高買價；最佳買價碰到固定目標才視為出場。";
  } else if (id === "B") {
    const windowSeconds = Number(config.strategy_b_last_seconds ?? 20);
    const minPrice = Number(config.strategy_b_min_price ?? .9);
    const maxPrice = Number(config.strategy_b_max_price ?? .95);
    const stopPrice = Number(config.strategy_b_stop_loss_price ?? .6);
    const stake = Number(config.strategy_b_stake ?? 10);
    fields = <>
      <NumberField label="尾盤窗口" name="strategy_b_last_seconds" value={windowSeconds} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最低價格" name="strategy_b_min_price" value={minPrice} onChange={onConfig} />
      <NumberField label="最高價格" name="strategy_b_max_price" value={maxPrice} onChange={onConfig} />
      <NumberField label="止損價格" name="strategy_b_stop_loss_price" value={stopPrice} onChange={onConfig} />
      <NumberField label="每筆本金" name="strategy_b_stake" value={stake} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = `倒數 ${windowSeconds} 秒內只在 ${minPrice.toFixed(2)}～${maxPrice.toFixed(2)} 買入領先側，超過上限不追價；每筆使用固定本金 ${stake.toFixed(2)} USDT，不採用 B2 的時間加碼。進場後不再無條件持有到結算：系統每秒更新時若首次觀測到同側最佳買價嚴格低於 ${stopPrice.toFixed(2)}，且最佳買量足以完整退出，便按實際最佳買價模擬止損；損益同時扣除進場與出場費用。`;
  } else if (id === "B2") {
    fields = <>
      <NumberField label="尾盤窗口" name="strategy_b2_last_seconds" value={Number(config.strategy_b2_last_seconds ?? 20)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最少剩餘時間" name="strategy_b2_min_seconds_left" value={Number(config.strategy_b2_min_seconds_left ?? 3)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最低價格" name="strategy_b2_min_price" value={Number(config.strategy_b2_min_price ?? .9)} onChange={onConfig} />
      <NumberField label="最高價格" name="strategy_b2_max_price" value={Number(config.strategy_b2_max_price ?? .95)} onChange={onConfig} />
      <NumberField label="最低本金" name="strategy_b2_min_stake" value={Number(config.strategy_b2_min_stake ?? 5)} step={1} suffix="USDT" onChange={onConfig} />
      <NumberField label="最高本金" name="strategy_b2_max_stake" value={Number(config.strategy_b2_max_stake ?? 20)} step={1} suffix="USDT" onChange={onConfig} />
      <NumberField label="加碼曲線" name="strategy_b2_size_curve" value={Number(config.strategy_b2_size_curve ?? 2)} step={.1} suffix="次方" onChange={onConfig} />
      <NumberField label="止損價格" name="strategy_b2_stop_loss_price" value={Number(config.strategy_b2_stop_loss_price ?? .6)} onChange={onConfig} />
    </>;
    logic = "獨立於策略 B：剩餘 20～3 秒只在 0.90～0.95 買入領先側，本金依剩餘時間由最低值逐步增加到最高值；加碼曲線越大，資金越集中在接近 3 秒的位置，低於 3 秒不再開新倉。進場後若系統每秒更新時首次觀測到同側最佳買價低於 0.60，且可見買量足以完整退出，便按當下最佳買價模擬止損；已有倉位低於 3 秒仍會檢查止損。時間越短風險越低只是待驗證的時間風險假設，並非毫秒級成交保證。";
  } else if (id === "C") {
    fields = <>
      <NumberField label="觀察窗" name="strategy_c_window_seconds" value={Number(config.strategy_c_window_seconds ?? 120)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最小價差" name="strategy_c_min_gap" value={Number(config.strategy_c_min_gap ?? .2)} onChange={onConfig} />
      <NumberField label="最高買價" name="strategy_c_max_entry" value={Number(config.strategy_c_max_entry ?? .2)} onChange={onConfig} />
      <NumberField label="目標倍數" name="strategy_c_target_multiplier" value={Number(config.strategy_c_target_multiplier ?? 2)} step={.1} suffix="倍" onChange={onConfig} />
      <NumberField label="每筆本金" name="strategy_c_stake" value={Number(config.strategy_c_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = "前段觀察條件與策略 A 相同，但出場價依實際買價乘上目標倍數；例如 0.15 買入、0.30 出場。";
  } else if (id === "D" || id === "F") {
    const strict = id === "F";
    const key = strict ? "f" : "d";
    fields = <>
      <NumberField label="第一腿窗口" name={`strategy_${key}_window_seconds`} value={Number(config[`strategy_${key}_window_seconds`] ?? 120)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最小價差" name={`strategy_${key}_min_gap`} value={Number(config[`strategy_${key}_min_gap`] ?? .2)} onChange={onConfig} />
      <NumberField label="第一腿最高價" name={`strategy_${key}_max_first_entry`} value={Number(config[`strategy_${key}_max_first_entry`] ?? .3)} onChange={onConfig} />
      <NumberField label="含費配對上限" name={`strategy_${key}_max_pair_cost`} value={Number(config[`strategy_${key}_max_pair_cost`] ?? (strict ? .9 : .95))} onChange={onConfig} />
      <NumberField label="最長等待" name={`strategy_${key}_max_wait_seconds`} value={Number(config[`strategy_${key}_max_wait_seconds`] ?? 120)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="強制退出" name={`strategy_${key}_force_exit_seconds`} value={Number(config[`strategy_${key}_force_exit_seconds`] ?? 20)} step={1} suffix="秒前" onChange={onConfig} />
      <NumberField label="第一腿本金" name={`strategy_${key}_stake`} value={Number(config[`strategy_${key}_stake`] ?? (strict ? 5 : 10))} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = strict
      ? "策略 D 的嚴格實驗版：第一腿規則相同，但雙邊含費配對成本上限收緊至 0.90、第一腿本金降至 5 USDT；逾時或剩餘 20 秒時退出。"
      : "先買前段低價側；僅在相反側可使雙邊含費配對成本不超過上限時補齊。逾時或進入尾盤則按同側最佳買價退出。";
  } else if (id === "E") {
    fields = <>
      <NumberField label="進場窗上限" name="strategy_e_max_seconds_left" value={Number(config.strategy_e_max_seconds_left ?? 90)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="進場窗下限" name="strategy_e_min_seconds_left" value={Number(config.strategy_e_min_seconds_left ?? 30)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最小現貨動能" name="strategy_e_min_spot_move_bps" value={Number(config.strategy_e_min_spot_move_bps ?? 3)} step={.1} suffix="bps" onChange={onConfig} />
      <NumberField label="最低市場價" name="strategy_e_min_entry" value={Number(config.strategy_e_min_entry ?? .6)} onChange={onConfig} />
      <NumberField label="最高市場價" name="strategy_e_max_entry" value={Number(config.strategy_e_max_entry ?? .9)} onChange={onConfig} />
      <NumberField label="最大買賣價差" name="strategy_e_max_spread" value={Number(config.strategy_e_max_spread ?? .04)} onChange={onConfig} />
      <NumberField label="基準最低剩餘" name="strategy_e_min_baseline_seconds_left" value={Number(config.strategy_e_min_baseline_seconds_left ?? 290)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="每筆本金" name="strategy_e_stake" value={Number(config.strategy_e_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = "先用市場開始、剩餘至少 290 秒的同源 BTC 價格建立基準；剩餘 90～30 秒時，現貨移動至少 3 bps 且與預測市場方向一致，並通過 0.60～0.90 價格及 0.04 買賣價差限制才進場。";
  } else if (id === "E2") {
    fields = <>
      <NumberField label="進場窗上限" name="strategy_e2_max_seconds_left" value={Number(config.strategy_e2_max_seconds_left ?? 60)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="進場窗下限" name="strategy_e2_min_seconds_left" value={Number(config.strategy_e2_min_seconds_left ?? 30)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最低市場價" name="strategy_e2_min_entry" value={Number(config.strategy_e2_min_entry ?? .6)} onChange={onConfig} />
      <NumberField label="最高市場價" name="strategy_e2_max_entry" value={Number(config.strategy_e2_max_entry ?? .79)} onChange={onConfig} />
      <NumberField label="最大買賣價差" name="strategy_e2_max_spread" value={Number(config.strategy_e2_max_spread ?? .02)} onChange={onConfig} />
      <NumberField label="基準最低剩餘" name="strategy_e2_min_baseline_seconds_left" value={Number(config.strategy_e2_min_baseline_seconds_left ?? 290)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="全段波動回看" name="strategy_e2_lookback_observations" value={Number(config.strategy_e2_lookback_observations ?? 300)} step={1} suffix="筆" onChange={onConfig} />
      <NumberField label="最少報酬樣本" name="strategy_e2_min_return_samples" value={Number(config.strategy_e2_min_return_samples ?? 60)} step={1} suffix="筆" onChange={onConfig} />
      <NumberField label="最小標準化動能" name="strategy_e2_min_move_z" value={Number(config.strategy_e2_min_move_z ?? .75)} step={.05} suffix="σ" onChange={onConfig} />
      <NumberField label="波動率下限" name="strategy_e2_sigma_floor" value={Number(config.strategy_e2_sigma_floor ?? .00002)} step={.00001} onChange={onConfig} />
      <NumberField label="訂單簿最大時差" name="strategy_e2_max_book_skew_ms" value={Number(config.strategy_e2_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
      <NumberField label="訂單簿最大年齡" name="strategy_e2_max_book_age_ms" value={Number(config.strategy_e2_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
      <NumberField label="每筆本金" name="strategy_e2_stake" value={Number(config.strategy_e2_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = "策略 E 的獨立收窄版：以第一筆觀察至今的完整波動標準化現貨移動；剩餘 60～30 秒時至少達 0.75σ，市場價介於 0.60～0.79、價差不超過 0.02，且報價新鮮才模擬進場。";
  } else if (id === "G") {
    fields = <>
      <NumberField label="進場窗上限" name="strategy_g_max_seconds_left" value={Number(config.strategy_g_max_seconds_left ?? 180)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="進場窗下限" name="strategy_g_min_seconds_left" value={Number(config.strategy_g_min_seconds_left ?? 30)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最低市場價" name="strategy_g_min_entry" value={Number(config.strategy_g_min_entry ?? .55)} onChange={onConfig} />
      <NumberField label="最高市場價" name="strategy_g_max_entry" value={Number(config.strategy_g_max_entry ?? .85)} onChange={onConfig} />
      <NumberField label="最大買賣價差" name="strategy_g_max_spread" value={Number(config.strategy_g_max_spread ?? .02)} onChange={onConfig} />
      <NumberField label="基準最低剩餘" name="strategy_g_min_baseline_seconds_left" value={Number(config.strategy_g_min_baseline_seconds_left ?? 290)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="波動回看筆數" name="strategy_g_lookback_observations" value={Number(config.strategy_g_lookback_observations ?? 120)} step={1} suffix="筆" onChange={onConfig} />
      <NumberField label="最少報酬樣本" name="strategy_g_min_return_samples" value={Number(config.strategy_g_min_return_samples ?? 60)} step={1} suffix="筆" onChange={onConfig} />
      <NumberField label="波動率下限" name="strategy_g_sigma_floor" value={Number(config.strategy_g_sigma_floor ?? .00002)} step={.00001} onChange={onConfig} />
      <NumberField label="機率收縮係數" name="strategy_g_probability_shrinkage" value={Number(config.strategy_g_probability_shrinkage ?? .5)} step={.05} onChange={onConfig} />
      <NumberField label="延遲假設" name="strategy_g_latency_seconds" value={Number(config.strategy_g_latency_seconds ?? 2)} step={.5} suffix="秒" onChange={onConfig} />
      <NumberField label="不確定性扣減" name="strategy_g_uncertainty_margin" value={Number(config.strategy_g_uncertainty_margin ?? .03)} step={.01} onChange={onConfig} />
      <NumberField label="最低淨優勢" name="strategy_g_min_net_edge" value={Number(config.strategy_g_min_net_edge ?? .05)} step={.01} onChange={onConfig} />
      <NumberField label="價差優勢倍數" name="strategy_g_spread_edge_multiplier" value={Number(config.strategy_g_spread_edge_multiplier ?? 3)} step={.5} suffix="倍" onChange={onConfig} />
      <NumberField label="預期滑價" name="strategy_g_slippage_bps" value={Number(config.strategy_g_slippage_bps ?? 50)} step={1} suffix="bps" onChange={onConfig} />
      <NumberField label="訂單簿最大時差" name="strategy_g_max_book_skew_ms" value={Number(config.strategy_g_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
      <NumberField label="訂單簿最大年齡" name="strategy_g_max_book_age_ms" value={Number(config.strategy_g_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
      <NumberField label="每筆本金" name="strategy_g_stake" value={Number(config.strategy_g_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = "以正式 startPrice、剩餘時間與短期波動估算保守勝率，再用機率收縮、不確定性、延遲及會實際計入損益的滑價扣減。只有淨優勢仍高於固定門檻與三倍價差，且報價新鮮，才模擬進場。";
  } else if (id === "H") {
    fields = <>
      <NumberField label="領先方判定價" name="strategy_h_leader_min_price" value={Number(config.strategy_h_leader_min_price ?? .9)} onChange={onConfig} />
      <NumberField label="逆轉判定窗口" name="strategy_h_reversal_seconds" value={Number(config.strategy_h_reversal_seconds ?? 5)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="窗口前回看" name="strategy_h_anchor_lookback_seconds" value={Number(config.strategy_h_anchor_lookback_seconds ?? 2.5)} step={.5} suffix="秒" onChange={onConfig} />
      <NumberField label="連續逆轉啟動" name="strategy_h_required_reversals" value={Number(config.strategy_h_required_reversals ?? 2)} step={1} suffix="局" onChange={onConfig} />
      <NumberField label="進場窗口" name="strategy_h_entry_window_seconds" value={Number(config.strategy_h_entry_window_seconds ?? 20)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最高買價" name="strategy_h_max_entry" value={Number(config.strategy_h_max_entry ?? .01)} step={.01} onChange={onConfig} />
      <NumberField label="連敗後停用" name="strategy_h_max_consecutive_losses" value={Number(config.strategy_h_max_consecutive_losses ?? 2)} step={1} suffix="筆" onChange={onConfig} />
      <NumberField label="訂單簿最大時差" name="strategy_h_max_book_skew_ms" value={Number(config.strategy_h_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
      <NumberField label="訂單簿最大年齡" name="strategy_h_max_book_age_ms" value={Number(config.strategy_h_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
      <NumberField label="本金上限" name="strategy_h_stake" value={Number(config.strategy_h_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = "逆轉狙擊偵測器先等待連續 2 局出現尾盤逆轉：原領先側曾達 0.90，卻在最後 5 秒翻成另一側勝出。武裝後只在最後 20 秒買入價格不高於 0.01 的一側並持有到結算；H 若連續失敗 2 次就解除武裝，重新等待兩次連續逆轉。符合條件時承接最佳賣價上任何可見的正數數量，本金設定僅為上限；帳本本金與損益依實際部分成交計算。這是在驗證狙擊假說，不會把 0.01 本身視為狙擊證據。";
  } else if (id === "I") {
    fields = <>
      <NumberField label="最高買價" name="strategy_i_max_entry" value={Number(config.strategy_i_max_entry ?? .01)} step={.01} onChange={onConfig} />
      <NumberField label="本金上限" name="strategy_i_stake" value={Number(config.strategy_i_stake ?? 1)} step={.1} suffix="USDT" onChange={onConfig} />
      <NumberField label="最少剩餘時間" name="strategy_i_min_seconds_left" value={Number(config.strategy_i_min_seconds_left ?? 3)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="訂單簿最大時差" name="strategy_i_max_book_skew_ms" value={Number(config.strategy_i_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
      <NumberField label="訂單簿最大年齡" name="strategy_i_max_book_age_ms" value={Number(config.strategy_i_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
    </>;
    logic = "獨立運行；只在剩餘時間大於 3 秒時，任一側賣價不高於 0.01 就以最多 1 USDT 模擬買入並持有到正式結算，最後 3 秒一律跳過。符合條件時承接最佳賣價上任何可見的正數數量，本金設定僅為上限；帳本本金與損益依實際部分成交計算。這是高風險資料實驗，不代表具有正期望值或交易優勢。";
  } else if (id === "J") {
    fields = <>
      <NumberField label="尾盤窗口" name="strategy_j_window_seconds" value={Number(config.strategy_j_window_seconds ?? 120)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最少剩餘時間" name="strategy_j_min_seconds_left" value={Number(config.strategy_j_min_seconds_left ?? 3)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="最小價差" name="strategy_j_min_gap" value={Number(config.strategy_j_min_gap ?? .2)} onChange={onConfig} />
      <NumberField label="最高買價" name="strategy_j_max_entry" value={Number(config.strategy_j_max_entry ?? .2)} onChange={onConfig} />
      <NumberField label="目標倍數" name="strategy_j_target_multiplier" value={Number(config.strategy_j_target_multiplier ?? 2)} step={.1} suffix="倍" onChange={onConfig} />
      <NumberField label="訂單簿最大時差" name="strategy_j_max_book_skew_ms" value={Number(config.strategy_j_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
      <NumberField label="訂單簿最大年齡" name="strategy_j_max_book_age_ms" value={Number(config.strategy_j_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
      <NumberField label="本金上限" name="strategy_j_stake" value={Number(config.strategy_j_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = "策略 C 的獨立尾盤版：只在最後 120 秒且剩餘時間大於 3 秒時，兩側價差至少 0.20 且便宜側賣價不高於 0.20 才買入；以實際買價的 2 倍出場，未達目標則持有到正式結算。訂單簿必須新鮮；符合條件時承接最佳賣價上任何可見的正數數量，本金設定僅為上限，帳本本金與損益依實際部分成交計算。";
  } else if (id === "K") {
    fields = <>
      <NumberField label="進場窗上限" name="strategy_k_max_seconds_left" value={Number(config.strategy_k_max_seconds_left ?? 180)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="進場窗下限" name="strategy_k_min_seconds_left" value={Number(config.strategy_k_min_seconds_left ?? 20)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="基準最低剩餘" name="strategy_k_min_baseline_seconds_left" value={Number(config.strategy_k_min_baseline_seconds_left ?? 290)} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="波動回看筆數" name="strategy_k_lookback_observations" value={Number(config.strategy_k_lookback_observations ?? 120)} step={1} suffix="筆" onChange={onConfig} />
      <NumberField label="最少報酬樣本" name="strategy_k_min_return_samples" value={Number(config.strategy_k_min_return_samples ?? 60)} step={1} suffix="筆" onChange={onConfig} />
      <NumberField label="波動率下限" name="strategy_k_sigma_floor" value={Number(config.strategy_k_sigma_floor ?? .00002)} step={.00001} onChange={onConfig} />
      <NumberField label="基差不確定性" name="strategy_k_basis_uncertainty_bps" value={Number(config.strategy_k_basis_uncertainty_bps ?? 1.5)} step={.1} suffix="bps" onChange={onConfig} />
      <NumberField label="機率校準斜率" name="strategy_k_calibration_slope" value={Number(config.strategy_k_calibration_slope ?? .5)} step={.05} onChange={onConfig} />
      <NumberField label="最低模型勝率" name="strategy_k_min_probability" value={Number(config.strategy_k_min_probability ?? .65)} step={.01} onChange={onConfig} />
      <NumberField label="延遲假設" name="strategy_k_latency_seconds" value={Number(config.strategy_k_latency_seconds ?? 2)} step={.5} suffix="秒" onChange={onConfig} />
      <NumberField label="最低淨優勢" name="strategy_k_min_net_edge" value={Number(config.strategy_k_min_net_edge ?? .05)} step={.01} onChange={onConfig} />
      <NumberField label="最大買賣價差" name="strategy_k_max_spread" value={Number(config.strategy_k_max_spread ?? .02)} onChange={onConfig} />
      <NumberField label="價差優勢倍數" name="strategy_k_spread_edge_multiplier" value={Number(config.strategy_k_spread_edge_multiplier ?? 3)} step={.5} suffix="倍" onChange={onConfig} />
      <NumberField label="預期滑價" name="strategy_k_slippage_bps" value={Number(config.strategy_k_slippage_bps ?? 50)} step={1} suffix="bps" onChange={onConfig} />
      <NumberField label="訂單簿最大時差" name="strategy_k_max_book_skew_ms" value={Number(config.strategy_k_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
      <NumberField label="訂單簿最大年齡" name="strategy_k_max_book_age_ms" value={Number(config.strategy_k_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
      <NumberField label="每筆本金" name="strategy_k_stake" value={Number(config.strategy_k_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
    </>;
    logic = "基差校正終局勝率：交易方向與模型勝率只由開盤基差校正後的現貨距離、剩餘時間及實現波動決定。UP／DOWN 訂單價格不參與方向或勝率判斷，只用於費用、滑價、價差、淨優勢與實際可成交性檢查；屬於獨立實驗策略。";
  } else if (id === "L") {
    const windowSeconds = Number(config.strategy_l_window_seconds ?? 60);
    const maxEntry = Number(config.strategy_l_max_entry ?? .5);
    const target = Number(config.strategy_l_target ?? .7);
    const totalStake = Number(config.strategy_l_total_stake ?? 10);
    fields = <>
      <NumberField label="雙腿進場窗口" name="strategy_l_window_seconds" value={windowSeconds} step={1} suffix="秒" onChange={onConfig} />
      <NumberField label="每腿最高買價" name="strategy_l_max_entry" value={maxEntry} onChange={onConfig} />
      <NumberField label="每腿出場目標" name="strategy_l_target" value={target} onChange={onConfig} />
      <NumberField label="雙腿總本金" name="strategy_l_total_stake" value={totalStake} step={1} suffix="USDT" onChange={onConfig} />
      <NumberField label="訂單簿最大時差" name="strategy_l_max_book_skew_ms" value={Number(config.strategy_l_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
      <NumberField label="訂單簿最大年齡" name="strategy_l_max_book_age_ms" value={Number(config.strategy_l_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
    </>;
    logic = `每輪開始後前 ${windowSeconds} 秒內，UP 與 DOWN 各自等待賣價不高於 ${maxEntry.toFixed(2)}；兩腿互相獨立，可在不同時間成交，每腿最多使用總本金的一半（目前各 ${(totalStake / 2).toFixed(2)} USDT）。進場會承接新鮮訂單簿最佳賣價上的可見數量，有多少成交多少，因此實際本金可能低於半額上限；兩側投入相同 USDT 也不代表取得相同 shares。系統每秒觀測，任一腿的最佳買價達到 ${target.toFixed(2)} 且同側第一檔買量足以完整退出時，才以目標價 ${target.toFixed(2)} 模擬賣出並扣除進場與出場費用；未達目標或深度不足的腿持有到正式結算。摘要的交易數以每一腿分別計算；雙邊都買不是無風險或保證套利。若要從完整一輪開始量測，請在兩輪之間按歸零，避免同輪兩腿跨越摘要起點。`;
  } else if (id === "M") {
    const entryWindow = Number(config.strategy_m_entry_window_seconds ?? 10);
    const stake = Number(config.strategy_m_stake ?? 10);
    fields = mFields("m");
    logic = `每輪開盤後前 ${entryWindow} 秒內，WS 事件驅動引擎收到第一個有效且非零的 Binance BTCUSDT spot－正式 Prediction startPrice 偏離就鎖定方向：正數買 UP、負數買 DOWN、相等則等待下一筆 Spot trade 事件。這是跨來源即時符號判斷，固定基差也可能讓方向長期偏向一側；本機以毫秒 timestamp 記錄事件，不代表 Prediction 上游每 1 ms 更新。Prediction 報價不參與方向或勝率判斷，也沒有買價上限；選定側 ask 即使接近 1 仍可能買入，因此不是勝率模型。鎖定後只以新鮮最佳賣價與可見深度模擬最多 ${stake.toFixed(2)} USDT，有多少成交多少；買入後持有到正式結算。舊版每秒策略交易由後端 strategy_version 分開保留。`;
  } else {
    const key = id.toLowerCase();
    const entryWindow = Number(config[`strategy_${key}_entry_window_seconds`] ?? 10);
    const stake = Number(config[`strategy_${key}_stake`] ?? 10);
    const commonExecution = `前 ${entryWindow} 秒內評估，Prediction 價格不參與方向判斷，只以選定側當下 ask 與可見深度模擬最多 ${stake.toFixed(2)} USDT 的部分成交；買後持有到正式結算。策略引擎按收到的快照運作，不代表上游提供毫秒行情。`;
    if (id === "M0") {
      const seed = Number(config.strategy_m0_seed ?? 20260717);
      fields = mFields("m0", <NumberField label="固定隨機種子" name="strategy_m0_seed" value={seed} step={1} onChange={onConfig} />);
      logic = `隨機方向基準：每個市場以固定種子 ${seed.toFixed(0)} 產生一次 UP／DOWN，方向不看現貨、永續或 Prediction 價格；固定種子讓相同市場可重現，這是對照組而非訊號。${commonExecution}`;
    } else if (id === "M01") {
      const seed = Number(config.strategy_m0_seed ?? 20260717);
      const maxEntry = Number(config.strategy_m01_max_entry ?? .30);
      fields = mFields("m01", <NumberField label="選定側最高買價" name="strategy_m01_max_entry" value={maxEntry} step={.01} onChange={onConfig} />);
      logic = `M0 延後進場版：每輪先用與 M0 完全相同的固定種子 ${seed.toFixed(0)} 和 market ID 鎖定 UP／DOWN，但不立即開單。整個設定窗口內只觀察選定側，等新鮮 Prediction 訂單簿的 ask 降到 ${maxEntry.toFixed(2)} 或以下，才依第一檔可見深度模擬最多 ${stake.toFixed(2)} USDT；未到價就整輪不交易，成交後持有到正式結算。方向不會因等待期間的價格變化而改選。`;
    } else if (id === "M01T180") {
      const seed = Number(config.strategy_m0_seed ?? 20260717);
      const minSecondsLeft = Number(config.strategy_m01t180_min_seconds_left ?? 180);
      const maxEntry = Number(config.strategy_m01t180_max_entry ?? .30);
      fields = <>
        <NumberField label="禁止進場起點（含）" name="strategy_m01t180_min_seconds_left" value={minSecondsLeft} step={1} suffix="剩餘秒" onChange={onConfig} />
        <NumberField label="單筆本金上限" name="strategy_m01t180_stake" value={Number(config.strategy_m01t180_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
        <NumberField label="訂單簿最大時差" name="strategy_m01t180_max_book_skew_ms" value={Number(config.strategy_m01t180_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
        <NumberField label="訂單簿最大年齡" name="strategy_m01t180_max_book_age_ms" value={Number(config.strategy_m01t180_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
        <NumberField label="選定側最高買價" name="strategy_m01t180_max_entry" value={maxEntry} step={.01} onChange={onConfig} />
      </>;
      logic = `M01 的時間門檻對照組：使用相同固定種子 ${seed.toFixed(0)}、market ID、方向、選定側 ask 與可見深度規則，但只在剩餘時間嚴格大於 ${minSecondsLeft.toFixed(0)} 秒且 ask 不高於 ${maxEntry.toFixed(2)} 時進場；剩餘時間剛好等於或低於門檻一律禁止。它使用獨立模擬帳本、不改動 M01，也可在正式實單規則中單獨選用；啟用模擬本身不會自動開啟實單。`;
    } else if (id === "M01T180D" || id === "M01TASYM") {
      const maxEntry = Number(config.strategy_m01t180_max_entry ?? .30);
      fields = <>
        <NumberField label="共用單筆本金上限" name="strategy_m01t180_stake" value={Number(config.strategy_m01t180_stake ?? 10)} step={1} suffix="USDT" onChange={onConfig} />
        <NumberField label="共用訂單簿最大時差" name="strategy_m01t180_max_book_skew_ms" value={Number(config.strategy_m01t180_max_book_skew_ms ?? 500)} step={50} suffix="ms" onChange={onConfig} />
        <NumberField label="共用訂單簿最大年齡" name="strategy_m01t180_max_book_age_ms" value={Number(config.strategy_m01t180_max_book_age_ms ?? 2000)} step={100} suffix="ms" onChange={onConfig} />
        <NumberField label="共用選定側最高買價" name="strategy_m01t180_max_entry" value={maxEntry} step={.01} onChange={onConfig} />
      </>;
      logic = id === "M01T180D"
        ? `探索性紙上組：沿用 M01T180 的固定種子方向、Ask ≤ ${maxEntry.toFixed(2)}、可見深度與資料品質規則，但只在方向為 DOWN 且剩餘時間嚴格大於 180 秒時進場。使用獨立帳本，不送入正式實單。`
        : `探索性紙上組：沿用 M01T180 的固定種子方向、Ask ≤ ${maxEntry.toFixed(2)}、可見深度與資料品質規則；UP 只在剩餘時間嚴格大於 210 秒時進場，DOWN 則要求嚴格大於 180 秒。使用獨立帳本，不送入正式實單。`;
    } else if (id === "M01O" || id === "M01O_F1" || id === "M01O_LIVE") {
      const seed = Number(config.strategy_m0_seed ?? 20260717);
      const maxEntry = Number(config.strategy_m01o_max_entry ?? .30);
      const minSamples = Number(config.strategy_m01o_min_observer_samples ?? 6);
      const profile = id === "M01O_F1" ? "F1" : id === "M01O_LIVE" ? "LIVE" : "F2";
      fields = mFields("m01o", <>
        <NumberField label="選定側最高買價" name="strategy_m01o_max_entry" value={maxEntry} step={.01} onChange={onConfig} />
        <NumberField label="最少已結算樣本" name="strategy_m01o_min_observer_samples" value={minSamples} step={1} suffix="輪" onChange={onConfig} />
      </>);
      const stagedScore = "開盤 0～60 秒以雙邊觸及 +2、有效穿越 ≥2 次 +1、30s ER ≤0.35 +1；滿 60 秒後改用 Median 60s ER。TREND override 需 Median ER ≥0.55、穿越 ≤1，並有至少兩筆跨時 60s ER 觀測。";
      const profileRule = profile === "F2"
        ? "歷史必須為 RANGE、當輪震盪分至少 2，且不得觸發 TREND override。"
        : profile === "F1"
          ? "歷史可為 RANGE 或 UNCERTAIN、當輪震盪分至少 1，且不得觸發 TREND override。"
          : "歷史樣本至少 6、狀態不可為 TREND；當輪 UP／DOWN Ask 都曾 ≤0.30 即放行，不等待 ER 或穿越。";
      logic = `${profile} 紙上對照：方向仍以固定種子 ${seed.toFixed(0)} 和同一 market ID 與 M0／M01 完全同步，三組共用 ask、可見深度、最多 ${Number(config.strategy_m01o_stake ?? 10).toFixed(2)} USDT 的模擬成交規則及 ${maxEntry.toFixed(2)} 進場上限。${profileRule}${profile === "LIVE" ? "" : stagedScore} 指標尚未成熟記為 NOT_READY，行情缺失或延遲才記為 MISSING_OR_STALE。這是獨立紙上帳本，絕不送進正式實單。`;
    } else if (id === "M01F") {
      const seed = Number(config.strategy_m0_seed ?? 20260717);
      const minEntry = Number(config.strategy_m01f_min_entry ?? .20);
      const maxEntry = Number(config.strategy_m01f_max_entry ?? .30);
      fields = mFields("m01f", <>
        <NumberField label="選定側最低買價" name="strategy_m01f_min_entry" value={minEntry} step={.01} onChange={onConfig} />
        <NumberField label="選定側最高買價" name="strategy_m01f_max_entry" value={maxEntry} step={.01} onChange={onConfig} />
      </>);
      logic = `M01 價格下限對照組：每輪使用與 M0／M01 完全相同的固定種子 ${seed.toFixed(0)} 和 market ID 鎖定方向，但只在選定側的新鮮 Prediction ask 落在 ${minEntry.toFixed(2)}～${maxEntry.toFixed(2)}（含邊界）時模擬最多 ${stake.toFixed(2)} USDT。高於上限或低於下限都不成交，仍會在整個設定窗口內等待價格重新進入區間；成交後持有到正式結算。它有獨立模擬帳本、摘要與歸零起點，不改動 M01，也不會送進實單執行器。`;
    } else if (id === "M01R") {
      const seed = Number(config.strategy_m0_seed ?? 20260717);
      const maxAnchor = Number(config.strategy_m01r_max_anchor ?? .30);
      const rebound = Number(config.strategy_m01r_rebound ?? .10);
      fields = mFields("m01r", <>
        <NumberField label="啟動追蹤最高價" name="strategy_m01r_max_anchor" value={maxAnchor} step={.01} onChange={onConfig} />
        <NumberField label="低點反彈幅度" name="strategy_m01r_rebound" value={rebound} step={.01} onChange={onConfig} />
      </>);
      logic = `M0 方向低價反彈觸發：每輪使用與 M0 完全相同的固定種子 ${seed.toFixed(0)} 和 market ID 鎖定方向。選定側新鮮 ask 首次到達 ${maxAnchor.toFixed(2)} 或以下後開始記錄最低價；若之後再創新低，基準與觸發價會同步下移。第一個 ask 達到「目前最低價 + ${rebound.toFixed(2)}」時，按該筆 ask 與可見深度模擬最多 ${stake.toFixed(2)} USDT，例如 0.10→0.20 或 0.30→0.40；成交後持有到正式結算。它有獨立紙上帳本、摘要及歸零起點，不會送進實單執行器。`;
    } else if (id === "M0W") {
      const seed = Number(config.strategy_m0_seed ?? 20260717);
      fields = mFields("m0w");
      logic = `M0 命中續買版：只有上一盤 M0 已經結算且命中，本盤才用相同固定種子 ${seed.toFixed(0)} 與本盤 market ID 產生 M0 方向並進場；上一盤 M0 失敗、未結算、沒有交易或尚無前一盤資料時，本盤完全不買。條件看的是上一盤 M0，不是 M0W 自己的輸贏。${commonExecution}`;
    } else if (id === "M01W") {
      const seed = Number(config.strategy_m0_seed ?? 20260717);
      const maxEntry = Number(config.strategy_m01w_max_entry ?? .30);
      fields = mFields("m01w", <NumberField label="選定側最高買價" name="strategy_m01w_max_entry" value={maxEntry} step={.01} onChange={onConfig} />);
      logic = `M0 命中低價等待版：只有上一盤 M0 已正式結算且命中，才以固定種子 ${seed.toFixed(0)} 鎖定本盤 M0 方向，之後等待該側新鮮 Prediction ask 降到 ${maxEntry.toFixed(2)} 或以下才依可見深度買入最多 ${stake.toFixed(2)} USDT。上一盤 M0 失敗、未知或沒有交易就不監掛、不買；不再依賴上一盤 M01 是否成交，到價前不改方向，成交後持有至結算。`;
    } else if (id === "M1") {
      fields = mFields("m1");
      logic = `永遠 UP 基準：每個市場固定選 UP，用來量測方向偏誤與資料樣本的 UP 基準結果，不是看漲判斷或勝率模型。${commonExecution}`;
    } else if (id === "M2") {
      fields = mFields("m2");
      logic = `原 M 的獨立複本：即時實驗引擎收到第一個有效且非零的 Binance BTCUSDT spot－正式 Prediction startPrice 偏離；正數選 UP、負數選 DOWN、相等繼續等待。M2 與原 M 使用獨立交易及摘要 cohort，訊號不是「上游開盤毫秒瞬間」。${commonExecution}`;
    } else if (id === "M3") {
      const threshold = Number(config.strategy_m3_min_abs_delta_bps ?? 1);
      fields = mFields("m3", <NumberField label="最小絕對偏離" name="strategy_m3_min_abs_delta_bps" value={threshold} step={.1} suffix="bps" onChange={onConfig} />);
      logic = `門檻偏離版：等待 Binance BTCUSDT spot 相對正式 Prediction startPrice 的絕對偏離至少 ${threshold.toFixed(1)} bps，再依正負選 UP／DOWN；門檻只過濾太小的符號變化，不代表已校準勝率。${commonExecution}`;
    } else if (id === "M4") {
      const confirmations = Number(config.strategy_m4_required_observations ?? 2);
      fields = mFields("m4", <NumberField label="同方向連續觀測" name="strategy_m4_required_observations" value={confirmations} step={1} suffix="筆" onChange={onConfig} />);
      logic = `連續確認版：要求 ${confirmations.toFixed(0)} 個不同的 Binance Spot trade 事件，其 spot－startPrice 偏離方向連續一致後才鎖定 UP／DOWN；不會把同一筆 trade 的重複評估當成多次確認，也不宣稱上游每 1 ms 更新。${commonExecution}`;
    } else if (id === "M5") {
      fields = mFields("m5");
      logic = `永續方向版：用 Binance USD-M BTCUSDT perpetual aggTrade／last traded price 與正式 Prediction startPrice 的偏離正負決定 UP／DOWN，不使用較慢的 markPrice；這是跨來源比較，固定基差可能造成方向偏移。perpetual timestamp／age 僅在 API 提供時顯示，不能推論上游毫秒精度。${commonExecution}`;
    } else if (id === "M6") {
      const minSamples = Number(config.strategy_m6_min_basis_samples ?? 20);
      fields = mFields("m6", <NumberField label="最少基差樣本" name="strategy_m6_min_basis_samples" value={minSamples} step={1} suffix="市場" onChange={onConfig} />);
      logic = `基差扣除版：基差樣本由與策略開關（toggle）無關的 canonical WS 資料持續收集，定義為每輪開盤 Spot－startPrice 基差；只使用已完成的前序市場，累積至少 ${minSamples.toFixed(0)} 個市場後凍結跨輪 running mean。當輪扣除該平均基差再依殘差正負選方向；當輪資料不回寫到自己的基準，也不讀取未來資料。${commonExecution}`;
    } else {
      const delay = Number(id.split("_")[1]);
      const sharedStake = Number(config.strategy_m7_stake ?? 10);
      const grace = Number(config.strategy_m7_execution_grace_seconds ?? 2);
      fields = mFields("m7", <NumberField label="M7 共用執行寬限" name="strategy_m7_execution_grace_seconds" value={grace} step={.1} suffix="秒" onChange={onConfig} />, true);
      logic = `M7 開盤 +${delay} 秒子組：deadline 固定為市場開盤後絕對 +${delay} 秒，不是首次 M2 或 Spot 訊號後再延遲。到達 deadline 時，以 deadline 前最後一筆且在當下年齡不超過 1 秒的 Binance Spot trade 相對正式 startPrice 決定 UP／DOWN；四個子組各自取樣，所以方向可以不同。選定後等待第一筆 deadline 後的有效 Prediction 訂單簿事件，僅在共用 ${grace.toFixed(1)} 秒執行寬限內以該簿模擬最多 ${sharedStake.toFixed(2)} USDT；沒有合格 Spot 或簿事件就不模擬成交。diagnostics 的 actual delay 以市場開盤為基準，不是首次訊號後延遲，也不能代表上游具備毫秒行情。買後持有到正式結算。`;
    }
  }

  const hMode = strategyH?.mode?.toUpperCase() ?? "DETECTING";
  const hModeLabel = hMode === "ARMED" ? "已武裝" : hMode === "PENDING_OFFICIAL" ? "待正式結算" : "偵測中";
  const requiredReversals = Number(config.strategy_h_required_reversals ?? 2);
  const maxLosses = Number(config.strategy_h_max_consecutive_losses ?? 2);
  const resetAt = summary.resetAt ?? summary.reset_at;
  const isM01OProfile = id === "M01O" || id === "M01O_F1" || id === "M01O_LIVE";
  const m01oProfile: "F2" | "F1" | "LIVE" = id === "M01O_F1" ? "F1" : id === "M01O_LIVE" ? "LIVE" : "F2";
  const m01oGate = isM01OProfile
    ? marketObserver?.m01oGates?.[m01oProfile] ?? (m01oProfile === "F2" ? marketObserver?.m01oGate : undefined)
    : undefined;
  const m01oAllowed = m01oGate?.allowed === true;

  return <section className={`strategy-card ${accent}`}>
    <div className="strategy-head">
      <div><div className="strategy-labels"><span className="eyebrow">策略 {id} · {kicker}</span>{experimental && <span className="experimental-pill">實驗</span>}</div><h2>{title}</h2></div>
      <button type="button" className={`toggle ${enabled ? "on" : ""}`} onClick={() => onConfig(`${prefix}_enabled`, !enabled)} aria-label={`${title}${enabled ? "停用" : "啟用"}`}><i /></button>
    </div>
    <div className="mini-stats">
      <div><span>已實現</span><strong className={summary.realized_pnl >= 0 ? "positive" : "negative"}>{money(summary.realized_pnl)}</strong></div>
      <div><span>交易</span><strong>{summary.trades}</strong></div>
      <div><span>勝 / 負</span><strong>{summary.wins} / {summary.losses}</strong></div>
    </div>
    {id === "M0" && <div className="strategy-state m0-streak-state" aria-label="策略 M0 連續命中與失敗統計">
      <span>目前連續命中 <b>{summary.currentWinStreak ?? 0} 次</b></span>
      <span>平均連續命中 <b>{decimal(summary.averageWinStreak, 2)} 次</b></span>
      <span>目前連續失敗 <b>{summary.currentLossStreak ?? 0} 次</b></span>
      <span>平均連續失敗 <b>{decimal(summary.averageLossStreak, 2)} 次</b></span>
      <span title="把 M0 結果套用『上一盤 M0 勝才觀察下一盤』規則後，所得連敗段的平均長度">一勝一敗平均連續 <b>{decimal(summary.averageWinLossCycleStreak, 2)} 組</b></span>
    </div>}
    {isM01OProfile && <div className={`strategy-state ${m01oAllowed ? "armed" : ""}`} aria-label={`策略 ${id} 市場觀測門檻狀態`}>
      <span className="state-badge">{m01oAllowed ? "目前可放行" : "目前阻擋"}</span>
      <span>對照組 <b>{m01oProfile}</b></span>
      <span>歷史狀態 <b>{m01oGate?.historicalState ?? "等待資料"}{m01oGate?.historicalProvisional ? "（暫定）" : ""}</b></span>
      <span>歷史樣本 <b>{m01oGate?.historicalSampleCount ?? 0} / {m01oGate?.minSettledSamples ?? Number(config.strategy_m01o_min_observer_samples ?? 6)}</b></span>
      <span>當輪條件 <b>{m01oProfile === "LIVE" ? (m01oGate?.currentBothSidesTouched ? "雙邊已觸及" : "等待雙邊觸及") : `${m01oGate?.currentRangeScore ?? 0} / ${m01oGate?.minCurrentRangeScore ?? (m01oProfile === "F2" ? 2 : 1)} 分`}</b></span>
      <span>資料狀態 <b>{m01oGate?.dataQualityStatus ?? "NOT_READY"}</b></span>
      <span title={m01oGate?.reason ?? "觀測器資料累積中"}>原因 <b>{m01oGate?.reason ?? "觀測器資料累積中"}</b></span>
    </div>}
    <div className="summary-reset">
      <div className="measurement-origin"><span>測量起點</span><strong>{fmtDateTime(resetAt)}</strong><small>只重設摘要基準，不刪除歷史交易{summary.cutoffTradeId != null ? ` · Trade ID > ${summary.cutoffTradeId}` : ""}</small></div>
      <button type="button" onClick={() => onReset(id)} disabled={resetState?.status === "loading"} aria-label={`策略 ${id} 歸零已實現收益，從現在重新計算`}>
        {resetState?.status === "loading" ? "處理中…" : "歸零已實現收益"}<span>{resetState?.status === "loading" ? "請稍候" : "從現在重新計算"}</span>
      </button>
      {(summary.carriedOpen ?? 0) > 0 && <p className="carried-open" role="status"><strong>承接未平倉 {summary.carriedOpen} 筆</strong>（不納入本輪績效）<span>目前全部未平倉 {summary.totalOpen ?? summary.carriedOpen} 筆</span></p>}
      {resetState && <p className={`reset-notice ${resetState.status}`} role={resetState.status === "error" ? "alert" : "status"}>{resetState.message}</p>}
    </div>
    {id === "H" && <div className={`strategy-state ${hMode === "ARMED" ? "armed" : hMode === "PENDING_OFFICIAL" ? "pending" : ""}`} aria-label="策略 H 偵測狀態">
      <span className="state-badge">{hModeLabel}</span>
      <span>連續尾盤逆轉 <b>{strategyH?.reversalStreak ?? 0} / {requiredReversals} 局</b></span>
      <span>H 連敗 <b>{strategyH?.lossStreak ?? 0} / {maxLosses} 筆</b></span>
      <span>待正式結算 <b>{strategyH?.pendingOfficialSettlements ?? 0} 局</b></span>
    </div>}
    <div className="fields">{fields}</div>
    <p className="logic">{logic}</p>
  </section>;
}

export default function Home() {
  const [state, setState] = useState<State>(initial);
  const [draft, setDraft] = useState<NumericConfig>({});
  const [saveState, setSaveState] = useState("參數已同步");
  const [apiDown, setApiDown] = useState(false);
  const [statisticsDown, setStatisticsDown] = useState(false);
  const [resetStates, setResetStates] = useState<Partial<Record<StrategyId, ResetState>>>({});
  const [strategyView, setStrategyView] = useState<StrategyView>("m-series");
  const [observerTradePageNumber, setObserverTradePageNumber] = useState(1);
  const [observerTradePage, setObserverTradePage] = useState<TradePage>(EMPTY_OBSERVER_TRADE_PAGE);
  const [observerTradePageLoading, setObserverTradePageLoading] = useState(false);
  const [liveControlState, setLiveControlState] = useState("控制狀態已同步");
  const [liveRulesSaveState, setLiveRulesSaveState] = useState("等待實單規則載入…");
  const [liveRulesDraft, setLiveRulesDraft] = useState<LiveRules>(DEFAULT_LIVE_RULES);
  const [liveRulesDirty, setLiveRulesDirty] = useState(false);
  const [liveRulesDraftHydrated, setLiveRulesDraftHydrated] = useState(false);
  const [manualSellState, setManualSellState] = useState<{
    orderId: number | null; status: "idle" | "loading" | "success" | "error"; message: string;
  }>({ orderId: null, status: "idle", message: "" });
  const strategyViewRef = useRef<StrategyView>(strategyView);
  const observerTradePageNumberRef = useRef(observerTradePageNumber);
  const dashboardSessionReady = useRef(false);
  const memoryRecycleInProgress = useRef(false);

  useEffect(() => {
    const restored = parseDashboardSession(window.sessionStorage.getItem(DASHBOARD_SESSION_STORAGE_KEY));
    if (restored) {
      setStrategyView(restored.strategyView);
      setObserverTradePageNumber(restored.observerTradePageNumber);
      strategyViewRef.current = restored.strategyView;
      observerTradePageNumberRef.current = restored.observerTradePageNumber;
      window.requestAnimationFrame(() => window.scrollTo({ top: restored.scrollY }));
    }
    dashboardSessionReady.current = true;

    const recycleMemory = () => {
      const snapshot: DashboardSessionState = {
        strategyView: strategyViewRef.current,
        observerTradePageNumber: observerTradePageNumberRef.current,
        scrollY: window.scrollY,
      };
      window.sessionStorage.setItem(DASHBOARD_SESSION_STORAGE_KEY, JSON.stringify(snapshot));
      memoryRecycleInProgress.current = true;
      window.location.reload();
    };
    const timer = window.setTimeout(recycleMemory, MEMORY_RECYCLE_MS);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    strategyViewRef.current = strategyView;
    observerTradePageNumberRef.current = observerTradePageNumber;
    if (!dashboardSessionReady.current) return;
    const current = parseDashboardSession(window.sessionStorage.getItem(DASHBOARD_SESSION_STORAGE_KEY));
    const snapshot: DashboardSessionState = {
      strategyView,
      observerTradePageNumber,
      scrollY: current?.scrollY ?? window.scrollY,
    };
    window.sessionStorage.setItem(DASHBOARD_SESSION_STORAGE_KEY, JSON.stringify(snapshot));
  }, [strategyView, observerTradePageNumber]);

  useEffect(() => {
    const stored = parseLiveRulesDraft(window.localStorage.getItem(LIVE_RULE_DRAFT_STORAGE_KEY));
    if (stored) {
      setLiveRulesDraft(stored);
      setLiveRulesDirty(true);
      setLiveRulesSaveState("已恢復尚未套用的本機草稿");
    }
    setLiveRulesDraftHydrated(true);
  }, []);

  useEffect(() => {
    if (!liveRulesDraftHydrated || liveRulesDirty || !state.liveM0W?.rules) return;
    setLiveRulesDraft({ ...DEFAULT_LIVE_RULES, ...state.liveM0W.rules });
    setLiveRulesSaveState("規則已同步");
  }, [liveRulesDraftHydrated, liveRulesDirty, state.liveM0W?.rules?.strategy, state.liveM0W?.rules?.strategies, state.liveM0W?.rules?.maxStakeUsdt, state.liveM0W?.rules?.strategyStakesUsdt, state.liveM0W?.rules?.minHourlyWinRatePct, state.liveM0W?.rules?.maxHourlyWinThenLossRatePct, state.liveM0W?.rules?.futuresLeadObserverEnabled, state.liveM0W?.rules?.futuresLeadObserverVersion, state.liveM0W?.rules?.strategyObserverEnabled, state.liveM0W?.rules?.strategyObserverVersions, state.liveM0W?.rules?.strategyDrawdownControlEnabled, state.liveM0W?.rules?.strategyLossCooldownEnabled]);

  useEffect(() => {
    if (!liveRulesDraftHydrated) return;
    if (liveRulesDirty) window.localStorage.setItem(LIVE_RULE_DRAFT_STORAGE_KEY, JSON.stringify(liveRulesDraft));
    else window.localStorage.removeItem(LIVE_RULE_DRAFT_STORAGE_KEY);
  }, [liveRulesDraftHydrated, liveRulesDirty, liveRulesDraft]);

  useEffect(() => {
    if (!liveRulesDirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      if (memoryRecycleInProgress.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [liveRulesDirty]);

  useEffect(() => {
    let active = true;
    let realtimeLoading = false;
    let statisticsLoading = false;
    let liveRulesLoading = false;
    const pending = new Set<AbortController>();
    const pageVisible = () => document.visibilityState === "visible";
    const loadRealtime = async () => {
      if (realtimeLoading || !pageVisible()) return;
      realtimeLoading = true;
      try {
        const next = await fetchDashboardJson<RealtimeState>("/api/realtime", pending);
        if (!active) return;
        setState(current => ({
          ...current,
          connection: next.connection,
          latest: next.latest,
          history: mergeObservationHistory(current.history, next.latest),
          microstructure: next.microstructure,
          mRealtime: next.mRealtime,
          marketObserver: next.marketObserver,
        }));
        setApiDown(false);
      } catch { if (active && pageVisible()) setApiDown(true); }
      finally { realtimeLoading = false; }
    };
    const loadStatistics = async () => {
      if (statisticsLoading || !pageVisible()) return;
      statisticsLoading = true;
      try {
        const next = await fetchDashboardJson<State>("/api/state", pending);
        if (!active) return;
        setState(current => ({
          ...current,
          config: next.config,
          history: mergeObservationHistory(next.history, current.latest ?? next.latest),
          trades: next.trades,
          summaries: next.summaries,
          researchForward: next.researchForward,
          strategyH: next.strategyH,
          mExitExperiment: next.mExitExperiment ?? current.mExitExperiment,
          m0ExitExperiment: next.m0ExitExperiment ?? current.m0ExitExperiment,
          pairArbExperiment: next.pairArbExperiment ?? current.pairArbExperiment,
          m0HourlyPerformance: next.m0HourlyPerformance,
          m01oFilterExperiment: next.m01oFilterExperiment,
          liveM0W: next.liveM0W
            ? {
                ...current.liveM0W,
                ...next.liveM0W,
                // /api/state intentionally omits the heavy redeem ledger.
                // Preserve those rows until /api/live-details supplies a new
                // complete snapshot instead of flashing the table empty.
                autoRedeem: {
                  ...(current.liveM0W?.autoRedeem ?? {}),
                  ...(next.liveM0W.autoRedeem ?? {}),
                },
              }
            : current.liveM0W,
          connection: current.latest ? current.connection : next.connection,
          latest: current.latest ?? next.latest,
          microstructure: current.microstructure ?? next.microstructure,
          mRealtime: current.mRealtime ?? next.mRealtime,
          marketObserver: current.marketObserver ?? next.marketObserver,
        }));
        setDraft(current => Object.keys(current).length ? current : next.config);
        setStatisticsDown(false);
      } catch { if (active && pageVisible()) setStatisticsDown(true); }
      finally { statisticsLoading = false; }
    };
    const loadLiveRules = async () => {
      if (liveRulesLoading || !pageVisible()) return;
      liveRulesLoading = true;
      try {
        const next = await fetchDashboardJson<LiveM0WState | null>("/api/live-rules", pending);
        if (!active || !next) return;
        setState(current => ({
          ...current,
          liveM0W: {
            ...current.liveM0W,
            ...next,
            // /api/live-rules is a lightweight snapshot and intentionally
            // omits the redeem summary and rows. Keep the complete ledger from
            // /api/live-details instead of clearing it every polling cycle.
            autoRedeem: {
              ...(current.liveM0W?.autoRedeem ?? {}),
              ...(next.autoRedeem ?? {}),
            },
          },
        }));
      } catch { if (active && pageVisible()) setStatisticsDown(true); }
      finally { liveRulesLoading = false; }
    };
    const handleVisibilityChange = () => {
      if (!pageVisible()) {
        abortPendingRequests(pending);
        return;
      }
      void loadRealtime();
      void loadStatistics();
      void loadLiveRules();
    };
    loadRealtime();
    loadStatistics();
    loadLiveRules();
    const realtimeTimer = window.setInterval(loadRealtime, REALTIME_REFRESH_MS);
    const statisticsTimer = window.setInterval(loadStatistics, STATISTICS_REFRESH_MS);
    const liveRulesTimer = window.setInterval(loadLiveRules, STATISTICS_REFRESH_MS);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      active = false;
      abortPendingRequests(pending);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.clearInterval(realtimeTimer);
      window.clearInterval(statisticsTimer);
      window.clearInterval(liveRulesTimer);
    };
  }, []);

  useEffect(() => {
    const targets = strategyView === "live-m0w" || strategyView === "reliability-shadow"
      ? [{ path: "/api/live-details", key: "liveM0W" as const }]
      : strategyView === "paused"
        ? [
            { path: "/api/experiment/m-exit", key: "mExitExperiment" as const },
            { path: "/api/experiment/m0-exit", key: "m0ExitExperiment" as const },
          ]
        : strategyView === "pair-arb"
          ? [{ path: "/api/experiment/pair-arb", key: "pairArbExperiment" as const }]
          : [];
    if (!targets.length) return;
    let active = true;
    let loading = false;
    const pending = new Set<AbortController>();
    const pageVisible = () => document.visibilityState === "visible";
    const loadExperiment = async () => {
      if (loading || !pageVisible()) return;
      loading = true;
      try {
        const payloads = await Promise.all(targets.map(async target => {
          const data = await fetchDashboardJson<State[typeof target.key]>(target.path, pending);
          return { key: target.key, data };
        }));
        if (active) {
          setState(current => payloads.reduce<State>(
            (next, payload) => ({ ...next, [payload.key]: payload.data }),
            current,
          ));
        }
      } catch {
        if (active && pageVisible()) setStatisticsDown(true);
      } finally {
        loading = false;
      }
    };
    const handleVisibilityChange = () => {
      if (!pageVisible()) abortPendingRequests(pending);
      else void loadExperiment();
    };
    loadExperiment();
    const timer = window.setInterval(loadExperiment, STATISTICS_REFRESH_MS);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      active = false;
      abortPendingRequests(pending);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.clearInterval(timer);
    };
  }, [strategyView]);

  useEffect(() => {
    if (strategyView !== "lead-observer") return;
    let active = true;
    let loading = false;
    const pending = new Set<AbortController>();
    const pageVisible = () => document.visibilityState === "visible";
    const loadObserverTrades = async () => {
      if (loading || !pageVisible()) return;
      loading = true;
      if (active) setObserverTradePageLoading(true);
      try {
        const next = await fetchDashboardJson<TradePage>(
          `/api/trades/futures-lead-observer?page=${observerTradePageNumber}`,
          pending,
        );
        if (!active) return;
        setObserverTradePage(next);
        if (next.page !== observerTradePageNumber) {
          setObserverTradePageNumber(next.page);
        }
      } catch {
        if (active && pageVisible()) setStatisticsDown(true);
      } finally {
        loading = false;
        if (active) setObserverTradePageLoading(false);
      }
    };
    const handleVisibilityChange = () => {
      if (!pageVisible()) abortPendingRequests(pending);
      else void loadObserverTrades();
    };
    loadObserverTrades();
    const timer = window.setInterval(loadObserverTrades, STATISTICS_REFRESH_MS);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      active = false;
      abortPendingRequests(pending);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.clearInterval(timer);
    };
  }, [strategyView, observerTradePageNumber]);

  const marketHistory = useMemo(() => state.latest ? state.history.filter(x => x.market_id === state.latest?.market_id) : [], [state.history, state.latest]);
  const update = (key: string, value: number | boolean) => { setDraft(prev => ({ ...prev, [key]: value })); setSaveState("有未儲存變更"); };
  const save = async (event: FormEvent) => {
    event.preventDefault(); setSaveState("儲存中…");
    try {
      const res = await fetch(apiUrl("/api/config"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(draft) });
      if (!res.ok) throw new Error(); setSaveState("參數已同步");
    } catch { setSaveState("儲存失敗"); }
  };
  const resetStrategy = async (strategy: StrategyId) => {
    const confirmed = window.confirm(`確定要將策略 ${strategy} 的已實現收益歸零，並從現在重新計算嗎？\n\n這不會刪除任何歷史交易。`);
    if (!confirmed) return;
    setResetStates(current => ({ ...current, [strategy]: { status: "loading", message: "正在設定新的測量起點…" } }));
    try {
      const res = await fetch(apiUrl("/api/strategy-reset"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy }),
      });
      if (!res.ok) {
        let detail = "伺服器拒絕重設";
        try {
          const payload = await res.json() as { error?: string; message?: string };
          detail = payload.error ?? payload.message ?? detail;
        } catch { /* keep the safe fallback */ }
        throw new Error(detail);
      }
      setResetStates(current => ({ ...current, [strategy]: { status: "success", message: "已設定新的測量起點，正在重新載入…" } }));
      window.setTimeout(() => window.location.reload(), 350);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "未知錯誤";
      setResetStates(current => ({ ...current, [strategy]: { status: "error", message: `重設失敗：${detail}` } }));
    }
  };
  const controlLive = async (action: "pause" | "resume") => {
    const rules = state.liveM0W?.rules ?? DEFAULT_LIVE_RULES;
    const strategyCaps = rules.strategies.map((strategy, index) => `${strategy} ${rules.strategyStakesUsdt[index]} USDT`).join("、");
    if (action === "resume" && !window.confirm(`這會恢復正式實單：${strategyCaps}。符合條件時將使用真實資金，確定繼續嗎？`)) return;
    setLiveControlState("送出中");
    try {
      const res = await fetch(apiUrl("/api/live-control"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const payload = await res.json() as { liveM0W?: LiveM0WState; error?: string };
      if (!res.ok) throw new Error(payload.error ?? "實單控制失敗");
      setState(current => ({ ...current, liveM0W: payload.liveM0W ?? current.liveM0W }));
      setLiveControlState(action === "pause" ? "實單已暫停" : "實單已恢復並重新預檢");
    } catch (error) {
      setLiveControlState(error instanceof Error ? error.message : "實單控制失敗");
    }
  };
  const manualSell = async (position: LiveActivePosition, orderType: "LIMIT" | "MARKET") => {
    const shares = finite(position.filled_share_qty) ?? 0;
    const bid = position.side === "UP" ? finite(state.latest?.up_bid) : finite(state.latest?.down_bid);
    const detail = orderType === "LIMIT"
      ? `以目前最佳買價 ${price(bid)} 送出 LIMIT GTC；未立即成交的部分會留在掛單中。`
      : "以 MARKET FOK 送出，必須立即全部成交，實際價格可能低於畫面價格；滑點容許為 1%。";
    if (!window.confirm(`確定手動賣出 ${position.strategy} 的 ${decimal(shares, 8)} ${position.side} shares？\n\n${detail}\n\n這會送出真實資金賣單，送出後不能由本頁復原。`)) return;
    setManualSellState({ orderId: position.order_local_id, status: "loading", message: "正在重新核對 Binance 持倉與報價…" });
    try {
      const response = await fetch(apiUrl("/api/live-sell"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-BTC-Lab-Manual-Exit": "confirmed",
        },
        body: JSON.stringify({ orderLocalId: position.order_local_id, orderType }),
      });
      const payload = await response.json() as { liveM0W?: LiveM0WState; error?: string };
      if (!response.ok) throw new Error(payload.error ?? "手動賣出遭後端拒絕");
      setState(current => ({ ...current, liveM0W: payload.liveM0W ?? current.liveM0W }));
      setManualSellState({
        orderId: position.order_local_id,
        status: "success",
        message: `${orderType} 賣單已送出；正在等待 Binance 回報成交狀態。`,
      });
    } catch (error) {
      setManualSellState({
        orderId: position.order_local_id,
        status: "error",
        message: error instanceof Error ? error.message : "手動賣出失敗",
      });
    }
  };
  const updateLiveRulesDraft = <K extends keyof LiveRules>(key: K, value: LiveRules[K]) => {
    setLiveRulesDraft(current => ({ ...current, [key]: value } as LiveRules));
    setLiveRulesDirty(true);
    setLiveRulesSaveState("尚未套用；草稿已保留在這個瀏覽器");
  };
  const resetLiveRulesDraft = () => {
    setLiveRulesDraft({ ...DEFAULT_LIVE_RULES, ...(state.liveM0W?.rules ?? {}) });
    setLiveRulesDirty(false);
    setLiveRulesSaveState("已放棄草稿，顯示後端現行規則");
  };
  const saveLiveRules = async (rules: LiveRules) => {
    setLiveRulesSaveState("儲存中…");
    try {
      const res = await fetch(apiUrl("/api/live-rules"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(rules),
      });
      const payload = await res.json() as { liveM0W?: LiveM0WState; error?: string };
      if (!res.ok) throw new Error(payload.error ?? "實單規則儲存失敗");
      setState(current => ({ ...current, liveM0W: payload.liveM0W ?? current.liveM0W }));
      setLiveRulesDraft(payload.liveM0W?.rules ?? rules);
      setLiveRulesDirty(false);
      setLiveRulesSaveState("規則已儲存並重新預檢");
      return true;
    } catch (error) {
      setLiveRulesSaveState(error instanceof Error ? error.message : "實單規則儲存失敗");
      return false;
    }
  };
  const latest = state.latest;
  const status = apiDown ? "OFFLINE" : state.connection.status;
  const isLiveView = strategyView === "live-m0w";
  const isReliabilityView = strategyView === "reliability-shadow";
  const isPairView = strategyView === "pair-arb";
  const isPausedView = strategyView === "paused";
  const isNonConfigView = isLiveView || isReliabilityView;
  const activeLivePositions = isLiveView && latest
    ? (state.liveM0W?.activePositions ?? []).filter(position => (
        position.market_id === latest.market_id
      ))
    : [];
  const stoppedStrategySet = new Set<StrategyId>(TEMPORARILY_STOPPED_STRATEGIES);
  const visibleTrades = strategyView === "lead-observer" ? observerTradePage.trades : state.trades.filter(trade => {
    if (isPausedView) return stoppedStrategySet.has(trade.strategy);
    if (strategyView === "research") return trade.strategy.startsWith("R_") && !trade.strategy.includes("_OBSERVER_");
    if (strategyView === "legacy") return !trade.strategy.startsWith("M") && !trade.strategy.startsWith("R_") && !stoppedStrategySet.has(trade.strategy);
    return trade.strategy.startsWith("M") && !stoppedStrategySet.has(trade.strategy);
  });
  const observerFirstTrade = observerTradePage.total === 0
    ? 0
    : (observerTradePage.page - 1) * observerTradePage.pageSize + 1;
  const observerLastTrade = Math.min(
    observerTradePage.page * observerTradePage.pageSize,
    observerTradePage.total,
  );

  return <main>
    <header className="topbar">
      <div className="brand"><span className="brand-mark">5</span><div><strong>BTC 5M LAB</strong><small>Binance Prediction · Paper + Configurable Live</small></div></div>
      <div className="top-actions"><span className={`status-dot ${status === "LIVE" ? "live" : ""}`} /> <span className="status-copy">{status}</span><span className="divider" /><span className="last-sync">行情每 1 秒 · 統計每 15 秒 · 行情更新 {fmtTime(state.connection.updatedAt)}</span></div>
    </header>

    <div className="shell">
      <section className="market-panel">
        <div className="market-title"><div><span className="eyebrow">{isLiveView ? "正式市場 · 實單持倉監控" : "正式市場 · 模擬研究"}</span><h1>{latest?.title ?? "等待 Binance 資料"}</h1><p>Market #{latest?.market_id ?? "—"} · Chainlink BTCUSDT</p></div><div className="countdown"><span>距離結束</span><strong>{clock(latest?.seconds_left)}</strong></div></div>
        {isLiveView && (activeLivePositions.length
          ? activeLivePositions.map(position => <ActiveLivePosition key={position.order_local_id} position={position} latest={latest} sellState={manualSellState} onSell={manualSell} />)
          : <ActiveLivePosition latest={latest} sellState={manualSellState} onSell={manualSell} />)}
        <div className="ticker-grid">
          <div className="ticker neutral"><span>起始價</span><strong>{money(latest?.start_price)}</strong><small>Price to beat</small></div>
          <div className="ticker neutral"><span>即時價</span><strong>{money(latest?.spot_price)}</strong><small className={(latest?.spot_price ?? 0) >= (latest?.start_price ?? 0) ? "positive" : "negative"}>{latest ? `${latest.spot_price >= latest.start_price ? "+" : ""}${(latest.spot_price - latest.start_price).toFixed(2)}` : "—"}</small></div>
          <div className="ticker up"><span>UP 最佳賣價</span><strong>{price(latest?.up_ask)}</strong><small>買價 {price(latest?.up_bid)}</small></div>
          <div className="ticker down"><span>DOWN 最佳賣價</span><strong>{price(latest?.down_ask)}</strong><small>買價 {price(latest?.down_bid)}</small></div>
        </div>
        <div className="chart-wrap"><div className="chart-head"><span>市場價格軌跡</span><div><i className="legend-up" /> UP <i className="legend-down" /> DOWN</div></div><MarketChart data={marketHistory} /></div>
      </section>

      <OptionalPanel title="毫秒與延遲觀測器" detail="需要檢查 timestamp、簿年齡或跨來源延遲時再開啟。">
        <MarketTiming latest={latest} />
      </OptionalPanel>

      <OptionalPanel title="市場微結構觀測器" detail="Spot／永續／Prediction 訂單流與流動性移除資料，預設不展開。">
        <MicrostructureMonitor data={state.microstructure} />
      </OptionalPanel>

      <form onSubmit={save}>
        <div className="section-heading strategy-console-heading"><div><span className="eyebrow">{strategyView === "live-m0w" ? `REAL MONEY · ${state.liveM0W?.strategy ?? "M0W"}` : strategyView === "reliability-shadow" ? "MODEL RELIABILITY · SHADOW TAGS" : strategyView === "lead-observer" ? "OBSERVER · EIGHT SHADOWS" : strategyView === "research" ? "FIVE PRIMARY + TWELVE SHADOWS · PAPER" : strategyView === "m-series" ? "M SERIES · PRIMARY" : strategyView === "pair-arb" ? "COMPLEMENTARY PAIR · NEW" : strategyView === "paused" ? "TEMPORARILY STOPPED" : "LEGACY A–L"}</span><h2>{strategyView === "live-m0w" ? "正式實單監視與規則" : strategyView === "reliability-shadow" ? "模型可靠／失準研究標籤" : strategyView === "lead-observer" ? "Observer 版本與策略組合觀測" : strategyView === "research" ? "五組主策略＋十二組 Shadow" : strategyView === "m-series" ? "M 系列策略控制台" : strategyView === "pair-arb" ? "UP＋DOWN 互補測試" : strategyView === "paused" ? "暫時停止觀測" : "舊策略控制台"}</h2></div>{!isNonConfigView && <div className="save-box"><span>{saveState}</span><button type="submit">儲存參數</button></div>}</div>
        <div className="strategy-tabs" role="tablist" aria-label="策略系列">
          <button type="button" role="tab" id="live-m0w-tab" aria-controls="live-m0w-panel" aria-selected={strategyView === "live-m0w"} className={strategyView === "live-m0w" ? "active live" : "live"} onClick={() => setStrategyView("live-m0w")}><strong>{state.liveM0W?.strategy ?? "M0W"} 正式實單</strong><span>{liveRulesDirty ? "有尚未套用的實單規則草稿" : "策略、金額與時段門檻可調整"}</span></button>
          <button type="button" role="tab" id="research-tab" aria-controls="research-panel" aria-selected={strategyView === "research"} className={strategyView === "research" ? "active" : ""} onClick={() => setStrategyView("research")}><strong>5 主策略＋12 Shadow</strong><span>新增兩組持續校準 V2 · 全部 paper only</span></button>
          <button type="button" role="tab" id="reliability-shadow-tab" aria-controls="reliability-shadow-panel" aria-selected={strategyView === "reliability-shadow"} className={strategyView === "reliability-shadow" ? "active shadow-tag" : "shadow-tag"} onClick={() => setStrategyView("reliability-shadow")}><strong>可靠／失準標籤</strong><span>實單成交鏡像 · 原單與反事實對比</span></button>
          <button type="button" role="tab" id="lead-observer-tab" aria-controls="lead-observer-panel" aria-selected={strategyView === "lead-observer"} className={strategyView === "lead-observer" ? "active" : ""} onClick={() => setStrategyView("lead-observer")}><strong>Observer 組合</strong><span>Lead 5 版＋其他策略 6 組</span></button>
          <button type="button" role="tab" id="m-series-tab" aria-controls="m-series-panel" aria-selected={strategyView === "m-series"} className={strategyView === "m-series" ? "active" : ""} onClick={() => setStrategyView("m-series")}><strong>M 系列主實驗</strong><span>M01 時間／市況過濾、Floor／Rebound、M1／M3／M7</span></button>
          <button type="button" role="tab" id="pair-arb-tab" aria-controls="pair-arb-panel" aria-selected={strategyView === "pair-arb"} className={strategyView === "pair-arb" ? "active exit" : ""} onClick={() => setStrategyView("pair-arb")}><strong>互補測試</strong><span>UP＋DOWN · 0.010 / 0.020 / 有限風險</span></button>
          <button type="button" role="tab" id="legacy-tab" aria-controls="legacy-panel" aria-selected={strategyView === "legacy"} className={strategyView === "legacy" ? "active" : ""} onClick={() => setStrategyView("legacy")}><strong>舊策略 A–L</strong><span>持續觀測 9 組策略與獨立統計</span></button>
          <button type="button" role="tab" id="paused-tab" aria-controls="paused-panel" aria-selected={strategyView === "paused"} className={strategyView === "paused" ? "active paused" : "paused"} onClick={() => setStrategyView("paused")}><strong>暫時停止觀測</strong><span>{TEMPORARILY_STOPPED_STRATEGIES.length + ALL_MX_SUFFIXES.length * 2} 組 · 含 M／M0 出場分支</span></button>
        </div>

        {strategyView === "live-m0w" ? <LiveM0WPanel data={state.liveM0W} controlState={liveControlState} rulesSaveState={liveRulesSaveState} rulesDraft={liveRulesDraft} rulesDirty={liveRulesDirty} onControl={controlLive} onRulesUpdate={updateLiveRulesDraft} onRulesReset={resetLiveRulesDraft} onRulesSave={saveLiveRules} /> : strategyView === "reliability-shadow" ? <ReliabilityShadowPanel data={state.liveM0W} /> : strategyView === "lead-observer" ? <FuturesLeadObserverPanel data={state.researchForward} summaries={state.summaries} config={draft} observer={state.marketObserver} onConfig={update} onReset={resetStrategy} resetStates={resetStates} /> : strategyView === "research" ? <ResearchForwardPanel data={state.researchForward} summaries={state.summaries} config={draft} live={state.liveM0W} onConfig={update} onReset={resetStrategy} resetStates={resetStates} /> : strategyView === "m-series" ? <div role="tabpanel" id="m-series-panel" aria-labelledby="m-series-tab">
          <div className="strategy-family-intro">
            <div><span className="eyebrow">18 ACTIVE OBSERVATION IDS</span><h3>持續觀測的開盤方向與延遲實驗</h3></div>
            <p>已跌到 -500 USDT 以下的策略移至「暫時停止觀測」。其餘每個 ID 仍有自己的交易摘要與歸零起點；Prediction 訂單簿只負責模擬執行。</p>
          </div>
          <OptionalPanel title="M01 Observer 與 WS 引擎診斷" detail="F1 閘門仍在後端持續執行；此開關只控制畫面顯示。">
            <div className="optional-panel-stack">
              <MRealtimePanel data={state.mRealtime} />
              <MarketObserverPanel data={state.marketObserver} />
              <M01OFilterExperimentPanel data={state.m01oFilterExperiment} observer={state.marketObserver} />
            </div>
          </OptionalPanel>
          <M0HourlyPanel data={state.m0HourlyPerformance} />
          <div className="strategy-grid">
            <StrategyCard id="M0" title="固定種子隨機基準" kicker="每市場隨機 UP／DOWN" accent="silver" summary={state.summaries.M0 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M0} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01" title="M0 低價等待進場" kicker="隨機方向 · ASK ≤ 0.30" accent="teal" summary={state.summaries.M01 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M01} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01T180" title="M01-T180 時間門檻對照" kicker="可選實單 · 剩餘 >180 秒" accent="teal" summary={state.summaries.M01T180 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M01T180} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01T180D" title="M01-T180D DOWN 對照" kicker="紙上測試 · DOWN 且 >180 秒" accent="teal" summary={state.summaries.M01T180D ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M01T180D} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01TASYM" title="M01-TASYM 非對稱門檻" kicker="紙上測試 · UP>210／DOWN>180" accent="cyan" summary={state.summaries.M01TASYM ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M01TASYM} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01O" title="F2 嚴格過濾" kicker="歷史 RANGE · 當輪 ≥2 分" accent="green" summary={state.summaries.M01O ?? EMPTY_SUMMARY} config={draft} marketObserver={state.marketObserver} resetState={resetStates.M01O} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01O_F1" title="F1 寬鬆過濾" kicker="歷史非 TREND · 當輪 ≥1 分" accent="cyan" summary={state.summaries.M01O_F1 ?? EMPTY_SUMMARY} config={draft} marketObserver={state.marketObserver} resetState={resetStates.M01O_F1} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01O_LIVE" title="LIVE 當輪優先" kicker="歷史非 TREND · 雙邊觸及" accent="amber" summary={state.summaries.M01O_LIVE ?? EMPTY_SUMMARY} config={draft} marketObserver={state.marketObserver} resetState={resetStates.M01O_LIVE} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01F" title="M01-Floor 價格下限對照" kicker="隨機方向 · ASK 0.20–0.30" accent="teal" summary={state.summaries.M01F ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M01F} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01R" title="M01-Rebound 低價反彈" kicker="M0 方向 · 低點 +0.10 觸發" accent="cyan" summary={state.summaries.M01R ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M01R} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M0W" title="M0 命中才跟單" kicker="上一盤 M0 勝才買" accent="green" summary={state.summaries.M0W ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M0W} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M01W" title="M0 命中低價等待" kicker="上一盤 M0 勝 · ASK ≤ 0.30" accent="teal" summary={state.summaries.M01W ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M01W} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M1" title="永遠 UP 基準" kicker="固定方向對照組" accent="blue" summary={state.summaries.M1 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M1} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M3" title="門檻偏離" kicker="預設至少 1 bps" accent="amber" summary={state.summaries.M3 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M3} onConfig={update} onReset={resetStrategy} />
          </div>
          <section className="m7-family" aria-labelledby="m7-title">
            <div className="strategy-family-intro compact"><div><span className="eyebrow">M7 ABSOLUTE DEADLINES</span><h3 id="m7-title">四個開盤絕對 deadline</h3></div><p>四組分別在市場開盤後絕對 +1／+2／+3／+5 秒，以各自 deadline 前最後一筆且年齡不超過 1 秒的 Spot trade 決定方向；再等待各自 deadline 後第一筆有效 Prediction 簿事件於執行寬限內成交。四組方向可以不同，並各自啟用、統計及歸零。</p></div>
            <div className="strategy-grid m7-grid">
              <StrategyCard id="M7_1" title="開盤 +1 秒" kicker="M7 絕對 deadline · 1 s" accent="orange" summary={state.summaries.M7_1 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M7_1} onConfig={update} onReset={resetStrategy} />
              <StrategyCard id="M7_2" title="開盤 +2 秒" kicker="M7 絕對 deadline · 2 s" accent="coral" summary={state.summaries.M7_2 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M7_2} onConfig={update} onReset={resetStrategy} />
              <StrategyCard id="M7_3" title="開盤 +3 秒" kicker="M7 絕對 deadline · 3 s" accent="rose" summary={state.summaries.M7_3 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M7_3} onConfig={update} onReset={resetStrategy} />
              <StrategyCard id="M7_5" title="開盤 +5 秒" kicker="M7 絕對 deadline · 5 s" accent="magenta" summary={state.summaries.M7_5 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M7_5} onConfig={update} onReset={resetStrategy} />
            </div>
          </section>
        </div> : strategyView === "pair-arb" ? <div role="tabpanel" id="pair-arb-panel" aria-labelledby="pair-arb-tab">
          <PairArbPanel data={state.pairArbExperiment} config={draft} onConfig={update} />
        </div> : strategyView === "paused" ? <div role="tabpanel" id="paused-panel" aria-labelledby="paused-tab">
          <div className="strategy-family-intro paused-observation-intro"><div><span className="eyebrow">LOSS CUTOFF · ≤ {TEMPORARILY_STOPPED_THRESHOLD_USDT} USDT</span><h3>暫時停止觀測</h3></div><p>依目前帳本的已實現收益，把虧損至少 500 USDT 的策略集中在此頁並關閉。停止後不再建立新模擬交易；既有未平倉仍會正常結算，歷史資料也不會刪除。若要重新測試，可在此重新開啟並儲存參數。</p></div>
          <div className="strategy-grid">
            <StrategyCard id="M" title="原 M 開盤方向盲單" kicker="WS 首筆非零偏離" accent="silver" summary={state.summaries.M ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M2" title="首次非零偏離" kicker="原 M 的獨立複本" accent="mint" summary={state.summaries.M2 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M2} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M4" title="連續方向確認" kicker="預設連續 2 筆" accent="purple" summary={state.summaries.M4 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M4} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M5" title="永續成交方向" kicker="USD-M aggTrade 對照" accent="cyan" summary={state.summaries.M5 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M5} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="M6" title="歷史基差校正" kicker="toggle-independent canonical WS 樣本" accent="lime" summary={state.summaries.M6 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.M6} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="A" title="盤中反彈差價" kicker="前 120 秒" accent="mint" summary={state.summaries.A ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.A} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="C" title="低價翻倍出場" kicker="前 120 秒" accent="blue" summary={state.summaries.C ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.C} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="D" title="時間套利配對" kicker="分時買兩側" accent="purple" summary={state.summaries.D ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.D} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="J" title="尾盤賭狗反彈價差" kicker="最後 120～3 秒" accent="magenta" summary={state.summaries.J ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.J} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="L" title="雙向前段反彈" kicker="前 60 秒各半建倉" accent="green" summary={state.summaries.L ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.L} onConfig={update} onReset={resetStrategy} />
          </div>
          <MExitExperimentPanel data={state.mExitExperiment} signalFamily="M" variantSuffixes={ALL_MX_SUFFIXES} pausedView />
          <MExitExperimentPanel data={state.m0ExitExperiment} signalFamily="M0" variantSuffixes={ALL_MX_SUFFIXES} pausedView />
        </div> : <div role="tabpanel" id="legacy-panel" aria-labelledby="legacy-tab">
          <div className="strategy-family-intro legacy"><div><span className="eyebrow">ARCHIVE · 9 ACTIVE</span><h3>持續觀測的 A–L 舊策略</h3></div><p>已跌到 -500 USDT 以下的五個舊策略移至「暫時停止觀測」。此頁其餘策略仍使用原本設定、交易摘要與歸零 cohort。</p></div>
          <div className="strategy-grid">
            <StrategyCard id="B" title="尾盤高機率" kicker="最後 20 秒" accent="amber" summary={state.summaries.B ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.B} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="B2" title="尾盤動態加碼止損" kicker="時間曲線加碼" accent="orange" summary={state.summaries.B2 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.B2} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="E" title="現貨＋市場確認動能" kicker="剩餘 90–30 秒" accent="cyan" summary={state.summaries.E ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.E} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="F" title="嚴格時間套利" kicker="收緊配對成本" accent="rose" summary={state.summaries.F ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.F} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="E2" title="波動標準化動能" kicker="E 的獨立收窄版" accent="lime" summary={state.summaries.E2 ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.E2} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="G" title="保守機率淨優勢" kicker="機率減完整成本" accent="indigo" summary={state.summaries.G ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.G} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="H" title="尾盤逆轉狙擊偵測" kicker="兩次逆轉後武裝" accent="coral" summary={state.summaries.H ?? EMPTY_SUMMARY} config={draft} strategyH={state.strategyH} resetState={resetStates.H} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="I" title="賭狗策略" kicker="任一側 0.01 即買" accent="yellow" summary={state.summaries.I ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.I} onConfig={update} onReset={resetStrategy} />
            <StrategyCard id="K" title="基差校正終局勝率" kicker="現貨決定方向與勝率" accent="teal" summary={state.summaries.K ?? EMPTY_SUMMARY} config={draft} resetState={resetStates.K} onConfig={update} onReset={resetStrategy} />
          </div>
        </div>}
      </form>

      {!isNonConfigView && !isPairView && <section className="ledger">
        <div className="section-heading"><div><span className="eyebrow">SQLite 本地帳本 · {isPausedView ? "TEMPORARILY STOPPED" : strategyView === "lead-observer" ? "OBSERVER STRATEGIES" : strategyView === "research" ? "RESEARCH STRATEGIES" : strategyView === "m-series" ? "M SERIES" : "LEGACY A–L"}</span><h2>{isPausedView ? "停止觀測策略的最近模擬交易" : strategyView === "lead-observer" ? "九組 Observer 最近模擬交易" : strategyView === "research" ? "研究策略最近模擬交易" : strategyView === "m-series" ? "M 系列最近模擬交易" : "舊策略最近模擬交易"}</h2></div><span className="record-count">{strategyView === "lead-observer" ? `SQLite 完整歷史共 ${observerTradePage.total} 筆` : `${visibleTrades.length} 筆紀錄`}</span></div>
        <div className="table-scroll"><table><thead><tr><th>時間</th><th>策略</th><th>第一腿</th><th>進場</th><th>目標 / 第二腿 / 出場</th><th>本金</th><th>狀態</th><th className="right">損益</th></tr></thead><tbody>
          {visibleTrades.length === 0 ? <tr><td colSpan={8} className="empty">此分頁尚無策略觸發。這裡只記錄模擬成交，不會送出真實訂單。</td></tr> : visibleTrades.map(trade => <tr key={trade.id}><td><span className="trade-opened-at">{fmtTime(trade.opened_at)}</span><TradeTiming trade={trade} /></td><td><span className={`strategy-pill s${trade.strategy}`}>策略 {trade.strategy}</span></td><td className={trade.side === "UP" ? "positive" : "amber-text"}>{trade.side}</td><td>{price(trade.entry_price)}</td><td>{price(trade.exit_price ?? trade.target_price)}</td><td>{money(trade.stake)}</td><td><span className={`trade-status ${trade.status.toLowerCase()}`}>{trade.status.replaceAll("_", " ")}</span></td><td className={`right ${trade.pnl == null ? "" : trade.pnl >= 0 ? "positive" : "negative"}`}>{money(trade.pnl)}</td></tr>)}
        </tbody></table></div>
        {strategyView === "lead-observer" && <nav className="live-table-pager" aria-label="九組 Observer 模擬交易分頁">
          <button type="button" disabled={observerTradePageLoading || observerTradePage.page <= 1} onClick={() => setObserverTradePageNumber(observerTradePage.page - 1)}>← 上一頁</button>
          <span><strong>第 {observerTradePage.page} / {observerTradePage.totalPages} 頁</strong><small>顯示 {observerFirstTrade}–{observerLastTrade} / {observerTradePage.total} 筆 · 每頁固定 10 筆</small></span>
          <button type="button" disabled={observerTradePageLoading || observerTradePage.page >= observerTradePage.totalPages} onClick={() => setObserverTradePageNumber(observerTradePage.page + 1)}>下一頁 →</button>
        </nav>}
      </section>}

      {(apiDown || statisticsDown || state.connection.error) && <aside className="error-bar"><strong>資料連線需要處理</strong><span>{apiDown ? "本地模擬 API 尚未啟動。" : statisticsDown ? "歷史統計暫時延遲；行情仍會繼續每秒更新。" : state.connection.error}</span></aside>}
    </div>
    <footer className={isLiveView ? "live-footer" : ""}><span>{isLiveView ? "REAL MONEY · M0W LIVE" : "LOCAL RESEARCH · PAPER TRADING"}</span><span>{isLiveView ? "本頁資料來自正式 Prediction 訂單與獨立實單帳本；每市場投入硬上限 1 USDT。" : "此頁成交與損益為實驗模擬，不代表實際可成交結果。"} · 頁面每 30 分鐘自動回收記憶體，並保留目前分頁與未套用草稿。</span></footer>
  </main>;
}
