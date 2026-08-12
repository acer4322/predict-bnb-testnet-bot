"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const DEFAULT_TARGET_WALLET = "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03";
const STRATEGY_ID = "R_WALLET_6DA6_SHADOW";
const STRATEGY_VERSION = "wallet_6da6_shadow_v0";
const MAKER_QTY = 18;
const PAIR_TARGET = 0.98;
const POLL_MS = 1_000;
const STORAGE_KEY = "btc5m-wallet-shadow-lab-v0";
const MAX_LOCAL_EVENTS = 5_000;

type Side = "UP" | "DOWN";
type Role = "MAKER" | "TAKER";

type LatestObservation = {
  timestamp?: string;
  market_id?: number;
  title?: string;
  start_price?: number;
  spot_price?: number;
  seconds_left?: number;
  up_ask?: number | null;
  up_bid?: number | null;
  down_ask?: number | null;
  down_bid?: number | null;
};

type DecisionPreview = {
  controller?: string;
  market_id?: number;
  status?: string;
  side?: string | null;
  reason?: string;
  estimated_probability?: number | null;
  model_edge?: number | null;
  agreement_weight?: number | null;
  created_at?: string;
};

type DashboardPayload = {
  latest?: LatestObservation | null;
  researchForward?: {
    decisionStrategyExperiment?: {
      strategies?: {
        R_DECISION_RANK1?: { currentPreview?: DecisionPreview | null };
      };
      recentDecisions?: DecisionPreview[];
    };
  } | null;
};

type TargetEvent = {
  id: string;
  marketId: string | null;
  marketTitle: string | null;
  role: Role;
  side: Side | "UNKNOWN";
  quoteType: "BID" | "ASK" | "UNKNOWN";
  orderHash: string | null;
  transactionHash: string | null;
  settlementId: string | null;
  eventMs: number;
  lastEventMs: number;
  eventAt: string;
  price: number | null;
  shares: number | null;
  costUsdtApprox: number | null;
  fillLegs: number;
};

type WalletApiResponse = {
  ok: boolean;
  status?: string;
  error?: string;
  apiKeyConfigured?: boolean;
  wallet?: string;
  bucketStartSec?: number;
  dashboardMarketId?: string | null;
  nativeMarketId?: string | null;
  mappingMode?: string;
  btcVerified?: boolean;
  executedOnly?: boolean;
  openOrdersAvailable?: boolean;
  events?: TargetEvent[];
  counts?: { total?: number; maker?: number; taker?: number; bid?: number; ask?: number };
  position?: {
    upShares?: number;
    downShares?: number;
    upAveragePrice?: number | null;
    downAveragePrice?: number | null;
    rows?: number;
  } | null;
  positionError?: string | null;
  diagnostics?: {
    fetchedParents?: number;
    requestLimit?: number;
    rttMs?: number;
    fetchedAt?: string;
  };
};

type ShadowEvent = {
  id: string;
  strategyId: string;
  strategyVersion: string;
  marketId: number | null;
  bucketStartSec: number;
  timestampMs: number;
  secondsToExpiry: number | null;
  role: Role;
  eventType: "MAKER_QUOTE" | "MAKER_FILL_PROXY" | "TAKER_INTENT";
  side: Side;
  price: number | null;
  qty: number;
  makerUpShares: number;
  makerDownShares: number;
  takerUpShares: number;
  takerDownShares: number;
  makerDelta: number;
  takerDelta: number;
  coreSide: Side | null;
  alphaBps: number | null;
  confidence: number | null;
  thresholdBps: number | null;
  triggerReason: string;
  inference: "KNOWN_PATTERN" | "INFERRED_FILL" | "INFERRED_TAKER_TRIGGER";
};

type BookMemory = {
  bucketStartSec: number;
  upBid: number | null;
  upAsk: number | null;
  downBid: number | null;
  downAsk: number | null;
  upQuote: number | null;
  downQuote: number | null;
  observedMs: number;
};

type Similarity = {
  maker18Rate: number | null;
  makerQuotePrice1Tick: number | null;
  makerFillTiming3s: number | null;
  takerSideMatch: number | null;
  takerTiming3s: number | null;
  matchedPrice1Tick: number | null;
  overall: number | null;
  matchedTargetEvents: number;
  targetEvents: number;
};

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function normalizedSide(value: unknown): Side | null {
  if (typeof value !== "string") return null;
  const text = value.trim().toUpperCase();
  if (text === "UP") return "UP";
  if (text === "DOWN") return "DOWN";
  return null;
}

function floorCent(value: unknown) {
  if (!finite(value)) return null;
  const clamped = Math.max(0.01, Math.min(0.99, value));
  return Math.floor((clamped + 1e-9) * 100) / 100;
}

function prettyNumber(value: unknown, digits = 2) {
  return finite(value) ? value.toFixed(digits) : "—";
}

function prettyPrice(value: unknown) {
  return finite(value) ? value.toFixed(3) : "—";
}

function prettyPercent(value: unknown, digits = 1) {
  return finite(value) ? `${(value * 100).toFixed(digits)}%` : "—";
}

function shortAddress(value: string) {
  return value.length > 14 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function clock(value: number | string | null | undefined) {
  if (value == null) return "—";
  const date = typeof value === "number" ? new Date(value) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString("zh-TW", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  });
}

function currentBucket(latest: LatestObservation | null | undefined) {
  const observed = latest?.timestamp ? Date.parse(latest.timestamp) : Date.now();
  const timeMs = Number.isFinite(observed) ? observed : Date.now();
  return Math.floor(timeMs / 300_000) * 300;
}

function currentDecision(payload: DashboardPayload | null): DecisionPreview | null {
  const latest = payload?.latest;
  const experiment = payload?.researchForward?.decisionStrategyExperiment;
  const direct = experiment?.strategies?.R_DECISION_RANK1?.currentPreview ?? null;
  if (direct && (latest?.market_id == null || direct.market_id === latest.market_id)) return direct;
  const matching = (experiment?.recentDecisions ?? []).find((row) => (
    row.controller === "R_DECISION_RANK1" &&
    (latest?.market_id == null || row.market_id === latest.market_id)
  ));
  return matching ?? null;
}

function inventory(events: ShadowEvent[], bucketStartSec: number) {
  let makerUp = 0;
  let makerDown = 0;
  let takerUp = 0;
  let takerDown = 0;
  for (const event of events) {
    if (event.bucketStartSec !== bucketStartSec) continue;
    if (event.eventType === "MAKER_FILL_PROXY") {
      if (event.side === "UP") makerUp += event.qty;
      else makerDown += event.qty;
    }
    if (event.eventType === "TAKER_INTENT") {
      if (event.side === "UP") takerUp += event.qty;
      else takerDown += event.qty;
    }
  }
  return {
    makerUp,
    makerDown,
    takerUp,
    takerDown,
    makerDelta: makerUp - makerDown,
    takerDelta: takerUp - takerDown,
  };
}

function nearestShadow(
  target: TargetEvent,
  shadow: ShadowEvent[],
  eventType: ShadowEvent["eventType"],
  maximumMs: number,
) {
  let best: ShadowEvent | null = null;
  let bestDelta = Number.POSITIVE_INFINITY;
  for (const event of shadow) {
    if (event.eventType !== eventType) continue;
    if (target.side !== "UNKNOWN" && event.side !== target.side) continue;
    const delta = Math.abs(event.timestampMs - target.eventMs);
    if (delta <= maximumMs && delta < bestDelta) {
      best = event;
      bestDelta = delta;
    }
  }
  return best ? { event: best, deltaMs: bestDelta } : null;
}

function activeQuoteAt(target: TargetEvent, shadow: ShadowEvent[]) {
  if (target.side === "UNKNOWN") return null;
  let result: ShadowEvent | null = null;
  for (const event of shadow) {
    if (event.eventType !== "MAKER_QUOTE" || event.side !== target.side) continue;
    if (event.timestampMs > target.eventMs) continue;
    if (!result || event.timestampMs > result.timestampMs) result = event;
  }
  return result;
}

function ratio(matches: number, total: number) {
  return total > 0 ? matches / total : null;
}

function similarity(target: TargetEvent[], shadow: ShadowEvent[]): Similarity {
  const maker = target.filter((row) => row.role === "MAKER");
  const taker = target.filter((row) => row.role === "TAKER");
  let maker18 = 0;
  let makerQuotePrice = 0;
  let makerQuoteComparable = 0;
  let makerTiming = 0;
  let takerSide = 0;
  let takerComparable = 0;
  let takerTiming = 0;
  let matchedPrice = 0;
  let matchedPriceComparable = 0;
  let matchedTargetEvents = 0;

  for (const row of maker) {
    if (finite(row.shares) && Math.abs(row.shares - MAKER_QTY) <= 0.05) maker18 += 1;
    const quote = activeQuoteAt(row, shadow);
    if (quote && finite(quote.price) && finite(row.price)) {
      makerQuoteComparable += 1;
      if (Math.abs(quote.price - row.price) <= 0.011) makerQuotePrice += 1;
    }
    const fill = nearestShadow(row, shadow, "MAKER_FILL_PROXY", 3_000);
    if (fill) {
      makerTiming += 1;
      matchedTargetEvents += 1;
      if (finite(fill.event.price) && finite(row.price)) {
        matchedPriceComparable += 1;
        if (Math.abs(fill.event.price - row.price) <= 0.011) matchedPrice += 1;
      }
    }
  }

  for (const row of taker) {
    const nearestAnySide = shadow
      .filter((event) => event.eventType === "TAKER_INTENT")
      .map((event) => ({ event, deltaMs: Math.abs(event.timestampMs - row.eventMs) }))
      .filter((item) => item.deltaMs <= 5_000)
      .sort((left, right) => left.deltaMs - right.deltaMs)[0];
    if (nearestAnySide) {
      takerComparable += 1;
      if (row.side !== "UNKNOWN" && nearestAnySide.event.side === row.side) takerSide += 1;
    }
    const sameSide = nearestShadow(row, shadow, "TAKER_INTENT", 3_000);
    if (sameSide) {
      takerTiming += 1;
      matchedTargetEvents += 1;
      if (finite(sameSide.event.price) && finite(row.price)) {
        matchedPriceComparable += 1;
        if (Math.abs(sameSide.event.price - row.price) <= 0.011) matchedPrice += 1;
      }
    }
  }

  const metrics = [
    ratio(maker18, maker.length),
    ratio(makerQuotePrice, makerQuoteComparable),
    ratio(makerTiming, maker.length),
    ratio(takerSide, takerComparable),
    ratio(takerTiming, taker.length),
    ratio(matchedPrice, matchedPriceComparable),
  ].filter((value): value is number => value != null);

  return {
    maker18Rate: ratio(maker18, maker.length),
    makerQuotePrice1Tick: ratio(makerQuotePrice, makerQuoteComparable),
    makerFillTiming3s: ratio(makerTiming, maker.length),
    takerSideMatch: ratio(takerSide, takerComparable),
    takerTiming3s: ratio(takerTiming, taker.length),
    matchedPrice1Tick: ratio(matchedPrice, matchedPriceComparable),
    overall: metrics.length ? metrics.reduce((sum, value) => sum + value, 0) / metrics.length : null,
    matchedTargetEvents,
    targetEvents: target.length,
  };
}

function residualSide(delta: number): Side | null {
  if (delta > 0) return "UP";
  if (delta < 0) return "DOWN";
  return null;
}

export default function WalletShadowLab() {
  const [walletDraft, setWalletDraft] = useState(DEFAULT_TARGET_WALLET);
  const [wallet, setWallet] = useState(DEFAULT_TARGET_WALLET);
  const [running, setRunning] = useState(true);
  const [dashboard, setDashboard] = useState<DashboardPayload | null>(null);
  const [walletData, setWalletData] = useState<WalletApiResponse | null>(null);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const [walletError, setWalletError] = useState<string | null>(null);
  const [shadowEvents, setShadowEvents] = useState<ShadowEvent[]>([]);
  const shadowRef = useRef<ShadowEvent[]>([]);
  const bookRef = useRef<BookMemory | null>(null);
  const lastFillRef = useRef<Record<Side, string>>({ UP: "", DOWN: "" });
  const lastTakerRef = useRef<{ signature: string; timestampMs: number }>({ signature: "", timestampMs: 0 });

  useEffect(() => {
    try {
      const savedWallet = window.localStorage.getItem(`${STORAGE_KEY}:wallet`);
      if (savedWallet && /^0x[a-fA-F0-9]{40}$/.test(savedWallet)) {
        setWallet(savedWallet.toLowerCase());
        setWalletDraft(savedWallet.toLowerCase());
      }
      const raw = window.localStorage.getItem(`${STORAGE_KEY}:events`);
      if (raw) {
        const parsed = JSON.parse(raw) as ShadowEvent[];
        if (Array.isArray(parsed)) setShadowEvents(parsed.slice(-MAX_LOCAL_EVENTS));
      }
    } catch {
      // Local persistence is optional. Live observation keeps running.
    }
  }, []);

  useEffect(() => {
    shadowRef.current = shadowEvents;
    try {
      window.localStorage.setItem(`${STORAGE_KEY}:events`, JSON.stringify(shadowEvents.slice(-MAX_LOCAL_EVENTS)));
    } catch {
      // Ignore browser quota/private-mode failures.
    }
  }, [shadowEvents]);

  useEffect(() => {
    if (!running) return;
    let stopped = false;
    let controller: AbortController | null = null;

    const tick = async () => {
      if (stopped) return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(apiUrl("/api/state"), { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(`dashboard API HTTP ${response.status}`);
        const payload = await response.json() as DashboardPayload;
        if (stopped) return;
        setDashboard(payload);
        setDashboardError(null);

        const latest = payload.latest;
        const bucketStartSec = currentBucket(latest);
        const query = new URLSearchParams({
          wallet,
          bucketStartSec: String(bucketStartSec),
          limit: "150",
        });
        if (latest?.market_id != null) query.set("dashboardMarketId", String(latest.market_id));
        const walletResponse = await fetch(`/api/wallet-shadow?${query.toString()}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const walletPayload = await walletResponse.json() as WalletApiResponse;
        if (stopped) return;
        setWalletData(walletPayload);
        setWalletError(walletPayload.ok ? null : walletPayload.error ?? `wallet API HTTP ${walletResponse.status}`);
      } catch (error) {
        if (stopped || (error instanceof DOMException && error.name === "AbortError")) return;
        const message = error instanceof Error ? error.message : String(error);
        if (message.includes("wallet") || message.includes("Predict")) setWalletError(message);
        else setDashboardError(message);
      }
    };

    void tick();
    const timer = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      stopped = true;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [running, wallet]);

  useEffect(() => {
    const latest = dashboard?.latest;
    if (!latest) return;
    const timestamp = latest.timestamp ? Date.parse(latest.timestamp) : Date.now();
    const observedMs = Number.isFinite(timestamp) ? timestamp : Date.now();
    const bucketStartSec = currentBucket(latest);
    const core = currentDecision(dashboard);
    const coreSide = normalizedSide(core?.side);
    const alphaBps = finite(core?.model_edge) ? core.model_edge * 10_000 : null;
    const confidence = finite(core?.estimated_probability) ? core.estimated_probability : null;
    const upQuote = floorCent(latest.up_bid);
    const downQuote = floorCent(latest.down_bid);
    const previous = bookRef.current;
    let working = shadowRef.current.slice();

    if (!previous || previous.bucketStartSec !== bucketStartSec) {
      bookRef.current = null;
      lastFillRef.current = { UP: "", DOWN: "" };
      lastTakerRef.current = { signature: "", timestampMs: 0 };
    }

    const append = (
      eventType: ShadowEvent["eventType"],
      role: Role,
      side: Side,
      price: number | null,
      qty: number,
      triggerReason: string,
      inference: ShadowEvent["inference"],
    ) => {
      const current = inventory(working, bucketStartSec);
      const makerUp = current.makerUp + (eventType === "MAKER_FILL_PROXY" && side === "UP" ? qty : 0);
      const makerDown = current.makerDown + (eventType === "MAKER_FILL_PROXY" && side === "DOWN" ? qty : 0);
      const takerUp = current.takerUp + (eventType === "TAKER_INTENT" && side === "UP" ? qty : 0);
      const takerDown = current.takerDown + (eventType === "TAKER_INTENT" && side === "DOWN" ? qty : 0);
      const event: ShadowEvent = {
        id: `${bucketStartSec}:${observedMs}:${eventType}:${side}:${working.length}`,
        strategyId: STRATEGY_ID,
        strategyVersion: STRATEGY_VERSION,
        marketId: latest.market_id ?? null,
        bucketStartSec,
        timestampMs: observedMs,
        secondsToExpiry: finite(latest.seconds_left) ? latest.seconds_left : null,
        role,
        eventType,
        side,
        price,
        qty,
        makerUpShares: makerUp,
        makerDownShares: makerDown,
        takerUpShares: takerUp,
        takerDownShares: takerDown,
        makerDelta: makerUp - makerDown,
        takerDelta: takerUp - takerDown,
        coreSide,
        alphaBps,
        confidence,
        thresholdBps: null,
        triggerReason,
        inference,
      };
      working = [...working, event].slice(-MAX_LOCAL_EVENTS);
    };

    const sameBucketPrevious = previous?.bucketStartSec === bucketStartSec ? previous : null;
    if (upQuote != null && (!sameBucketPrevious || upQuote !== sameBucketPrevious.upQuote)) {
      append("MAKER_QUOTE", "MAKER", "UP", upQuote, MAKER_QTY, "18-share passive cent-grid bid quote", "KNOWN_PATTERN");
    }
    if (downQuote != null && (!sameBucketPrevious || downQuote !== sameBucketPrevious.downQuote)) {
      append("MAKER_QUOTE", "MAKER", "DOWN", downQuote, MAKER_QTY, "18-share passive cent-grid bid quote", "KNOWN_PATTERN");
    }

    if (sameBucketPrevious) {
      const fillProxy = (side: Side, priorQuote: number | null, currentBid: number | null | undefined, currentAsk: number | null | undefined) => {
        if (priorQuote == null) return;
        const bidFellThrough = finite(currentBid) && currentBid < priorQuote - 0.005;
        const askCrossed = finite(currentAsk) && currentAsk <= priorQuote + 0.0001;
        if (!bidFellThrough && !askCrossed) return;
        const signature = `${bucketStartSec}:${side}:${priorQuote.toFixed(2)}:${Math.floor(observedMs / 1000)}`;
        if (lastFillRef.current[side] === signature) return;
        lastFillRef.current[side] = signature;
        append(
          "MAKER_FILL_PROXY",
          "MAKER",
          side,
          priorQuote,
          MAKER_QTY,
          bidFellThrough ? "best bid moved through inferred passive quote" : "best ask touched inferred passive quote",
          "INFERRED_FILL",
        );
      };
      fillProxy("UP", sameBucketPrevious.upQuote, latest.up_bid, latest.up_ask);
      fillProxy("DOWN", sameBucketPrevious.downQuote, latest.down_bid, latest.down_ask);
    }

    const postMaker = inventory(working, bucketStartSec);
    const makerResidual = residualSide(postMaker.makerDelta);
    if (coreSide && makerResidual && coreSide !== makerResidual) {
      const magnitudeBand = Math.max(1, Math.ceil(Math.abs(postMaker.makerDelta) / MAKER_QTY));
      const signature = `${bucketStartSec}:${coreSide}:${makerResidual}:${magnitudeBand}`;
      const elapsed = observedMs - lastTakerRef.current.timestampMs;
      if (signature !== lastTakerRef.current.signature && elapsed >= 900) {
        const takerQty = Math.min(180, (magnitudeBand + 1) * MAKER_QTY);
        const takerPrice = coreSide === "UP"
          ? (finite(latest.up_ask) ? latest.up_ask : null)
          : (finite(latest.down_ask) ? latest.down_ask : null);
        append(
          "TAKER_INTENT",
          "TAKER",
          coreSide,
          takerPrice,
          takerQty,
          "WALLET_MAKER_TAKER_DIVERGENCE: core direction opposes inferred passive residual",
          "INFERRED_TAKER_TRIGGER",
        );
        lastTakerRef.current = { signature, timestampMs: observedMs };
      }
    }

    if (working.length !== shadowRef.current.length || working.at(-1)?.id !== shadowRef.current.at(-1)?.id) {
      shadowRef.current = working;
      setShadowEvents(working);
    }
    bookRef.current = {
      bucketStartSec,
      upBid: finite(latest.up_bid) ? latest.up_bid : null,
      upAsk: finite(latest.up_ask) ? latest.up_ask : null,
      downBid: finite(latest.down_bid) ? latest.down_bid : null,
      downAsk: finite(latest.down_ask) ? latest.down_ask : null,
      upQuote,
      downQuote,
      observedMs,
    };
  }, [dashboard]);

  const latest = dashboard?.latest ?? null;
  const bucketStartSec = currentBucket(latest);
  const core = currentDecision(dashboard);
  const coreSide = normalizedSide(core?.side);
  const currentShadow = useMemo(
    () => shadowEvents.filter((event) => event.bucketStartSec === bucketStartSec),
    [shadowEvents, bucketStartSec],
  );
  const targetEvents = useMemo(
    () => (walletData?.events ?? []).filter((event) => event.side === "UP" || event.side === "DOWN"),
    [walletData],
  );
  const shadowInventory = useMemo(() => inventory(currentShadow, bucketStartSec), [currentShadow, bucketStartSec]);
  const score = useMemo(() => similarity(targetEvents, currentShadow), [targetEvents, currentShadow]);
  const upQuote = floorCent(latest?.up_bid);
  const downQuote = floorCent(latest?.down_bid);
  const pairSum = upQuote != null && downQuote != null ? upQuote + downQuote : null;
  const makerResidual = residualSide(shadowInventory.makerDelta);
  const takerResidual = residualSide(shadowInventory.takerDelta);

  const applyWallet = () => {
    const normalized = walletDraft.trim().toLowerCase();
    if (!/^0x[a-f0-9]{40}$/.test(normalized)) {
      setWalletError("錢包地址格式錯誤：需要 0x + 40 個十六進位字元");
      return;
    }
    setWallet(normalized);
    setWalletError(null);
    try {
      window.localStorage.setItem(`${STORAGE_KEY}:wallet`, normalized);
    } catch {
      // Optional persistence only.
    }
  };

  const exportCurrent = () => {
    const payload = {
      exportedAt: new Date().toISOString(),
      strategyId: STRATEGY_ID,
      strategyVersion: STRATEGY_VERSION,
      targetWallet: wallet,
      dashboardMarketId: latest?.market_id ?? null,
      nativeTargetMarketId: walletData?.nativeMarketId ?? null,
      bucketStartSec,
      bucketStartAt: new Date(bucketStartSec * 1000).toISOString(),
      coreDecision: core,
      latestObservation: latest,
      target: walletData,
      shadowEvents: currentShadow,
      similarity: score,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `wallet-shadow-${bucketStartSec}-${wallet.slice(2, 8)}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(href);
  };

  const clearShadow = () => {
    const retained = shadowEvents.filter((event) => event.bucketStartSec !== bucketStartSec);
    shadowRef.current = retained;
    setShadowEvents(retained);
    bookRef.current = null;
    lastFillRef.current = { UP: "", DOWN: "" };
    lastTakerRef.current = { signature: "", timestampMs: 0 };
  };

  const recentTarget = targetEvents.slice(0, 18);
  const recentShadow = currentShadow.slice(-18).reverse();

  return (
    <section className="wallet-shadow-lab" aria-label="BTC 5M Wallet Shadow Lab">
      <style>{`
        .wallet-shadow-lab{margin:18px 0;padding:16px;border:1px solid rgba(92,217,255,.24);border-radius:18px;background:linear-gradient(145deg,rgba(7,17,28,.96),rgba(12,19,31,.92))}
        .wallet-shadow-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}
        .wallet-shadow-head h3{margin:4px 0 7px}.wallet-shadow-head p{max-width:850px;margin:0;color:#9dacbf;line-height:1.55}
        .wallet-shadow-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.wallet-shadow-controls input{width:min(420px,70vw);padding:9px 11px;border-radius:10px;border:1px solid rgba(132,159,190,.35);background:#09101a;color:#e8f0fa;font-family:monospace}.wallet-shadow-controls button{padding:9px 12px;border-radius:10px;border:1px solid rgba(116,202,235,.34);background:rgba(24,67,86,.36);color:#d8f4ff;cursor:pointer}.wallet-shadow-controls button:hover{background:rgba(33,87,108,.46)}
        .wallet-shadow-status{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.wallet-shadow-pill{padding:5px 9px;border-radius:999px;background:rgba(110,130,160,.14);border:1px solid rgba(120,145,176,.22);font-size:12px}.wallet-shadow-pill.live{border-color:rgba(74,219,159,.4);color:#95f0c4}.wallet-shadow-pill.warn{border-color:rgba(255,187,76,.4);color:#ffd28c}.wallet-shadow-pill.bad{border-color:rgba(255,100,100,.4);color:#ffabab}
        .wallet-shadow-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin:12px 0}.wallet-shadow-card{padding:12px;border-radius:14px;background:rgba(6,11,19,.68);border:1px solid rgba(110,140,170,.18)}.wallet-shadow-card span,.wallet-shadow-card small{color:#8ea0b8}.wallet-shadow-card strong{display:block;margin:4px 0;font-size:17px}.wallet-shadow-card code{font-size:12px;color:#acd8e8;overflow-wrap:anywhere}
        .wallet-shadow-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:8px;margin:12px 0}.wallet-shadow-metric{padding:10px;border-radius:12px;background:rgba(19,35,50,.6)}.wallet-shadow-metric span{display:block;font-size:12px;color:#91a3ba}.wallet-shadow-metric strong{display:block;margin-top:3px;font-size:18px}
        .wallet-shadow-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px;margin-top:14px}.wallet-shadow-table{overflow:auto;max-height:460px;border:1px solid rgba(110,140,170,.18);border-radius:12px}.wallet-shadow-table table{width:100%;border-collapse:collapse;font-size:12px}.wallet-shadow-table th,.wallet-shadow-table td{padding:8px 9px;text-align:left;border-bottom:1px solid rgba(110,140,170,.12);white-space:nowrap}.wallet-shadow-table th{position:sticky;top:0;background:#0d1723;z-index:1;color:#9eb3ca}.wallet-shadow-table small{display:block;color:#778aa2}.wallet-shadow-table .maker{color:#90d7ff}.wallet-shadow-table .taker{color:#e6b2ff}.wallet-shadow-table .up{color:#8de4bc}.wallet-shadow-table .down{color:#ffadad}
        .wallet-shadow-note{margin:12px 0 0;padding:10px 12px;border-radius:10px;background:rgba(255,186,72,.08);border:1px solid rgba(255,186,72,.18);color:#d9c49c;line-height:1.5}.wallet-shadow-error{margin-top:10px;color:#ffaaaa;overflow-wrap:anywhere}
        @media(max-width:720px){.wallet-shadow-two{grid-template-columns:1fr}.wallet-shadow-controls input{width:100%}}
      `}</style>

      <div className="wallet-shadow-head">
        <div>
          <span className="eyebrow">BTC 5M WALLET SHADOW LAB · PAPER ONLY</span>
          <h3>{STRATEGY_ID} · 目標錢包成交 vs 我們的推定策略</h3>
          <p>
            Maker 層只採用目前已觀察到的固定 18 shares、cent-grid、雙邊被動 BID 特徵；
            Maker fill 是行情穿越報價後的 proxy，不宣稱是真成交。Taker 層則用 Rank 1 V2 核心方向與
            Maker residual 的反向 divergence 觸發，明確標記為 INFERRED。
          </p>
        </div>
        <div className="wallet-shadow-controls">
          <input
            aria-label="模仿對象錢包"
            value={walletDraft}
            onChange={(event) => setWalletDraft(event.target.value)}
            spellCheck={false}
          />
          <button type="button" onClick={applyWallet}>套用錢包</button>
          <button type="button" onClick={() => setRunning((value) => !value)}>{running ? "暫停監看" : "開始監看"}</button>
          <button type="button" onClick={exportCurrent}>匯出本輪 JSON</button>
          <button type="button" onClick={clearShadow}>清除本輪 Shadow</button>
        </div>
      </div>

      <div className="wallet-shadow-status">
        <span className={`wallet-shadow-pill ${running ? "live" : "warn"}`}>{running ? "1s POLLING" : "PAUSED"}</span>
        <span className={`wallet-shadow-pill ${walletData?.ok ? "live" : "bad"}`}>Predict {walletData?.status ?? "WAITING"}</span>
        <span className="wallet-shadow-pill">TARGET {shortAddress(wallet)}</span>
        <span className="wallet-shadow-pill">BTC 5M BUCKET {clock(bucketStartSec * 1000)}</span>
        <span className={`wallet-shadow-pill ${walletData?.btcVerified ? "live" : "warn"}`}>{walletData?.mappingMode ?? "WAITING MARKET MAP"}</span>
        <span className="wallet-shadow-pill warn">EXECUTED ONLY · OPEN ORDERS UNAVAILABLE</span>
      </div>

      {(dashboardError || walletError) ? (
        <div className="wallet-shadow-error">{dashboardError ?? walletError}</div>
      ) : null}

      <div className="wallet-shadow-grid">
        <article className="wallet-shadow-card">
          <span>市場映射</span>
          <strong>Dashboard #{latest?.market_id ?? "—"} → Predict #{walletData?.nativeMarketId ?? "等待首筆成交"}</strong>
          <small>{latest?.title ?? "BTC 5M current market"}</small>
        </article>
        <article className="wallet-shadow-card">
          <span>核心方向 · Rank 1 V2</span>
          <strong>{coreSide ?? "WAITING"} · P {prettyPercent(core?.estimated_probability)}</strong>
          <small>edge {prettyPercent(core?.model_edge, 2)} · agreement {prettyPercent(core?.agreement_weight)}</small>
        </article>
        <article className="wallet-shadow-card">
          <span>推定 Maker quotes</span>
          <strong>UP {prettyPrice(upQuote)} · DOWN {prettyPrice(downQuote)}</strong>
          <small>18 shares each · pair sum {prettyPrice(pairSum)} · historical center ≈ {PAIR_TARGET.toFixed(2)}</small>
        </article>
        <article className="wallet-shadow-card">
          <span>Shadow residual</span>
          <strong>Maker {makerResidual ?? "FLAT"} {Math.abs(shadowInventory.makerDelta).toFixed(0)} · Taker {takerResidual ?? "FLAT"} {Math.abs(shadowInventory.takerDelta).toFixed(0)}</strong>
          <small>Maker UP/DOWN {shadowInventory.makerUp.toFixed(0)}/{shadowInventory.makerDown.toFixed(0)} · Taker {shadowInventory.takerUp.toFixed(0)}/{shadowInventory.takerDown.toFixed(0)}</small>
        </article>
        <article className="wallet-shadow-card">
          <span>Target events</span>
          <strong>{walletData?.counts?.total ?? 0} parents · M {walletData?.counts?.maker ?? 0} / T {walletData?.counts?.taker ?? 0}</strong>
          <small>BID {walletData?.counts?.bid ?? 0} · ASK {walletData?.counts?.ask ?? 0} · fetched {walletData?.diagnostics?.fetchedParents ?? 0}</small>
        </article>
        <article className="wallet-shadow-card">
          <span>Target position API</span>
          <strong>UP {prettyNumber(walletData?.position?.upShares, 1)} · DOWN {prettyNumber(walletData?.position?.downShares, 1)}</strong>
          <small>avg UP {prettyPrice(walletData?.position?.upAveragePrice)} · DOWN {prettyPrice(walletData?.position?.downAveragePrice)}</small>
        </article>
      </div>

      <div className="wallet-shadow-metrics">
        <div className="wallet-shadow-metric"><span>Overall similarity</span><strong>{prettyPercent(score.overall)}</strong></div>
        <div className="wallet-shadow-metric"><span>Target Maker = 18</span><strong>{prettyPercent(score.maker18Rate)}</strong></div>
        <div className="wallet-shadow-metric"><span>Maker quote ±1 tick</span><strong>{prettyPercent(score.makerQuotePrice1Tick)}</strong></div>
        <div className="wallet-shadow-metric"><span>Maker proxy ±3s</span><strong>{prettyPercent(score.makerFillTiming3s)}</strong></div>
        <div className="wallet-shadow-metric"><span>Taker side match</span><strong>{prettyPercent(score.takerSideMatch)}</strong></div>
        <div className="wallet-shadow-metric"><span>Taker timing ±3s</span><strong>{prettyPercent(score.takerTiming3s)}</strong></div>
        <div className="wallet-shadow-metric"><span>Matched price ±1 tick</span><strong>{prettyPercent(score.matchedPrice1Tick)}</strong></div>
      </div>

      <div className="wallet-shadow-two">
        <div>
          <span className="eyebrow">TARGET WALLET · REAL EXECUTED PARENTS</span>
          <div className="wallet-shadow-table">
            <table>
              <thead><tr><th>時間</th><th>Role</th><th>方向</th><th>價格</th><th>Shares</th><th>型態</th></tr></thead>
              <tbody>
                {recentTarget.length ? recentTarget.map((row) => (
                  <tr key={row.id}>
                    <td>{clock(row.eventMs)}<small>{row.fillLegs} legs</small></td>
                    <td className={row.role === "MAKER" ? "maker" : "taker"}>{row.role}</td>
                    <td className={row.side === "UP" ? "up" : "down"}>{row.side}</td>
                    <td>{prettyPrice(row.price)}</td>
                    <td>{prettyNumber(row.shares, 2)}</td>
                    <td>{row.quoteType}<small>{row.orderHash ? `${row.orderHash.slice(0, 8)}…` : "no hash"}</small></td>
                  </tr>
                )) : <tr><td colSpan={6}>本輪尚未抓到目標錢包 BTC 5M 成交。</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          <span className="eyebrow">SHADOW · CAUSAL INFERRED EVENTS</span>
          <div className="wallet-shadow-table">
            <table>
              <thead><tr><th>時間</th><th>事件</th><th>方向</th><th>價格</th><th>Qty</th><th>原因</th></tr></thead>
              <tbody>
                {recentShadow.length ? recentShadow.map((row) => (
                  <tr key={row.id}>
                    <td>{clock(row.timestampMs)}<small>T-{prettyNumber(row.secondsToExpiry, 1)}</small></td>
                    <td className={row.role === "MAKER" ? "maker" : "taker"}>{row.eventType}</td>
                    <td className={row.side === "UP" ? "up" : "down"}>{row.side}</td>
                    <td>{prettyPrice(row.price)}</td>
                    <td>{row.qty.toFixed(0)}</td>
                    <td>{row.triggerReason}<small>{row.inference}</small></td>
                  </tr>
                )) : <tr><td colSpan={6}>等待第一張有效 BTC 5M book snapshot。</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <p className="wallet-shadow-note">
        v0 的比較故意不使用目標錢包成交來驅動 Shadow 決策，避免「看答案後模仿」。Target Maker 成交只拿來驗證當時我們是否已推定同側／同價的 18-share quote；
        Target Taker 則拿來驗證 divergence trigger 的方向與時間。若兩者長時間高重疊，才代表我們對看不到的掛單意圖有可檢驗的解釋力。
      </p>
    </section>
  );
}
