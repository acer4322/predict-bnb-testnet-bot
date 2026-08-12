import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const runtime = "nodejs";

const PREDICT_API_BASE = (
  process.env.PREDICT_API_BASE_URL ??
  process.env.PREDICT_FUN_API_BASE_URL ??
  "https://api.predict.fun"
).replace(/\/+$/, "");
const PREDICT_API_KEY =
  process.env.PREDICT_API_KEY ??
  process.env.PREDICT_FUN_API_KEY ??
  process.env.PREDICTFUN_API_KEY ??
  "";
const PREDICT_API_KEY_HEADER = process.env.PREDICT_API_KEY_HEADER?.trim() || "x-api-key";
const MATCHES_PATH = process.env.PREDICT_MATCHES_PATH?.trim() || "/v1/orders/matches";
const POSITIONS_PATH = process.env.PREDICT_POSITIONS_PATH?.trim() || "/v1/positions";
const REQUEST_TIMEOUT_MS = 3_500;
const MATCH_PAGE_SIZE = 100;
const INITIAL_BACKFILL_PAGES_PER_ROLE = 4;
const POSITION_CACHE_MS = 5_000;
const BUCKET_CACHE_TTL_MS = 20 * 60_000;

type JsonRecord = Record<string, unknown>;
type Role = "MAKER" | "TAKER";
type Side = "UP" | "DOWN" | "UNKNOWN";
type QuoteType = "BID" | "ASK" | "UNKNOWN";

type WalletParentEvent = {
  id: string;
  sourceLegId: string;
  marketId: string | null;
  marketTitle: string | null;
  marketVariantType: string | null;
  priceFeedSymbol: string | null;
  role: Role;
  side: Side;
  quoteType: QuoteType;
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

type RoleCache = {
  events: Map<string, WalletParentEvent>;
  seenLegs: Set<string>;
  initialized: boolean;
  lastPollAtMs: number;
};

type BucketCache = {
  maker: RoleCache;
  taker: RoleCache;
  lastAccessAtMs: number;
};

type PositionSummary = {
  upShares: number;
  downShares: number;
  upAveragePrice: number | null;
  downAveragePrice: number | null;
  rows: number;
};

type PositionCacheEntry = {
  expiresAt: number;
  value: PositionSummary | null;
  error: string | null;
};

const bucketCaches = new Map<string, BucketCache>();
const positionCache = new Map<string, PositionCacheEntry>();

function blankRoleCache(): RoleCache {
  return { events: new Map(), seenLegs: new Set(), initialized: false, lastPollAtMs: 0 };
}

function asRecord(value: unknown): JsonRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function stringValue(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return null;
}

function numeric(value: unknown) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function decimalValue(value: unknown) {
  const parsed = numeric(value);
  if (parsed == null || parsed < 0) return null;
  if (parsed > 1e12) return parsed / 1e18;
  return parsed;
}

function priceValue(value: unknown) {
  const parsed = numeric(value);
  if (parsed == null || parsed < 0) return null;
  if (parsed <= 1.5) return parsed;
  if (parsed > 1e12) {
    const scaled = parsed / 1e18;
    return scaled >= 0 && scaled <= 1.5 ? scaled : null;
  }
  return null;
}

function timestampMs(value: unknown) {
  const number = numeric(value);
  if (number != null && number > 0) {
    if (number < 10_000_000_000) return Math.round(number * 1000);
    if (number >= 10_000_000_000_000_000) return Math.round(number / 1_000_000);
    if (number >= 10_000_000_000_000) return Math.round(number / 1000);
    return Math.round(number);
  }
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function normalizeOutcome(value: unknown): Side {
  if (typeof value === "string") {
    const normalized = value.trim().toUpperCase();
    if (normalized === "UP" || normalized.includes("UP")) return "UP";
    if (normalized === "DOWN" || normalized.includes("DOWN")) return "DOWN";
    return "UNKNOWN";
  }
  const record = asRecord(value);
  return record ? normalizeOutcome(record.name ?? record.label ?? record.outcome) : "UNKNOWN";
}

function normalizeQuoteType(value: unknown): QuoteType {
  if (typeof value !== "string") return "UNKNOWN";
  const normalized = value.trim().toUpperCase();
  if (normalized === "BID" || normalized === "BUY") return "BID";
  if (normalized === "ASK" || normalized === "SELL") return "ASK";
  return "UNKNOWN";
}

function responseRows(payload: unknown) {
  const root = asRecord(payload);
  if (!root || !Array.isArray(root.data)) return [] as JsonRecord[];
  return root.data.map(asRecord).filter((row): row is JsonRecord => Boolean(row));
}

function responseCursor(payload: unknown) {
  const root = asRecord(payload);
  return root ? stringValue(root.cursor) : null;
}

function marketMetadata(row: JsonRecord) {
  const market = asRecord(row.market);
  const variant = market ? asRecord(market.variantData) : null;
  return {
    marketId: market ? stringValue(market.id) : null,
    marketTitle: market ? stringValue(market.title, market.question) : null,
    marketVariantType: variant ? stringValue(variant.type) : null,
    priceFeedSymbol: variant ? stringValue(variant.priceFeedSymbol) : null,
  };
}

function participantAddress(participant: JsonRecord | null) {
  return participant ? stringValue(participant.signer)?.toLowerCase() ?? null : null;
}

function buildLeg(
  row: JsonRecord,
  participant: JsonRecord,
  role: Role,
  targetWallet: string,
  makerIndex: number | null,
): WalletParentEvent | null {
  if (participantAddress(participant) !== targetWallet) return null;
  const eventMs = timestampMs(row.executedAt);
  if (eventMs == null) return null;
  const market = marketMetadata(row);
  const side = normalizeOutcome(participant.outcome);
  const quoteType = normalizeQuoteType(participant.quoteType);
  const orderHash = stringValue(participant.hash);
  const transactionHash = stringValue(row.transactionHash);
  const settlementId = stringValue(row.settlementId);
  const shares = decimalValue(participant.amount ?? row.amountFilled);
  const price = priceValue(participant.price ?? row.priceExecuted);
  const sourceLegId = [
    transactionHash ?? "no-tx",
    settlementId ?? "no-settlement",
    role,
    orderHash ?? "no-order",
    makerIndex ?? "taker",
    side,
    quoteType,
    String(participant.amount ?? row.amountFilled ?? ""),
    String(participant.price ?? row.priceExecuted ?? ""),
    String(row.executedAt ?? ""),
  ].join(":");
  const parentFallback = [
    transactionHash ?? "no-tx",
    settlementId ?? "no-settlement",
    role,
    makerIndex ?? "taker",
    side,
    quoteType,
  ].join(":");
  return {
    id: `${role}:${orderHash ?? parentFallback}`,
    sourceLegId,
    ...market,
    role,
    side,
    quoteType,
    orderHash,
    transactionHash,
    settlementId,
    eventMs,
    lastEventMs: eventMs,
    eventAt: new Date(eventMs).toISOString(),
    price,
    shares,
    costUsdtApprox: price != null && shares != null ? price * shares : null,
    fillLegs: 1,
  };
}

function roleLegs(payload: unknown, wallet: string, role: Role) {
  const target = wallet.toLowerCase();
  const legs: WalletParentEvent[] = [];
  for (const row of responseRows(payload)) {
    if (role === "TAKER") {
      const taker = asRecord(row.taker);
      if (!taker) continue;
      const event = buildLeg(row, taker, "TAKER", target, null);
      if (event) legs.push(event);
      continue;
    }
    const makers = Array.isArray(row.makers) ? row.makers : [];
    makers.forEach((value, makerIndex) => {
      const maker = asRecord(value);
      if (!maker) return;
      const event = buildLeg(row, maker, "MAKER", target, makerIndex);
      if (event) legs.push(event);
    });
  }
  return legs;
}

function mergeParent(target: Map<string, WalletParentEvent>, leg: WalletParentEvent) {
  const current = target.get(leg.id);
  if (!current) {
    target.set(leg.id, { ...leg });
    return;
  }
  const previousShares = current.shares ?? 0;
  const incomingShares = leg.shares ?? 0;
  const totalShares = previousShares + incomingShares;
  if (totalShares > 0 && current.price != null && leg.price != null) {
    current.price = ((current.price * previousShares) + (leg.price * incomingShares)) / totalShares;
  } else if (current.price == null && leg.price != null) {
    current.price = leg.price;
  }
  current.shares = totalShares > 0 ? totalShares : current.shares ?? leg.shares;
  current.costUsdtApprox = current.price != null && current.shares != null
    ? current.price * current.shares
    : null;
  current.eventMs = Math.min(current.eventMs, leg.eventMs);
  current.lastEventMs = Math.max(current.lastEventMs, leg.lastEventMs);
  current.eventAt = new Date(current.eventMs).toISOString();
  current.fillLegs += 1;
  current.transactionHash ||= leg.transactionHash;
  current.settlementId ||= leg.settlementId;
  current.marketTitle ||= leg.marketTitle;
  current.marketVariantType ||= leg.marketVariantType;
  current.priceFeedSymbol ||= leg.priceFeedSymbol;
}

function isBtcCryptoMarket(event: WalletParentEvent) {
  const typeOk = event.marketVariantType?.toUpperCase() === "CRYPTO_UP_DOWN";
  const symbol = event.priceFeedSymbol?.toUpperCase() ?? "";
  const title = event.marketTitle?.toUpperCase() ?? "";
  const btcOk = symbol.includes("BTC") || title.includes("BITCOIN") || /(^|\W)BTC(\W|$)/.test(title);
  return Boolean(typeOk && btcOk);
}

async function predictGet(path: string, query: Record<string, string | number | boolean | null | undefined>) {
  const url = new URL(`${PREDICT_API_BASE}${path}`);
  for (const [key, value] of Object.entries(query)) {
    if (value == null || value === "") continue;
    url.searchParams.set(key, String(value));
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "User-Agent": "BTC-5M-Wallet-Shadow-Lab/0.2",
    };
    if (PREDICT_API_KEY) headers[PREDICT_API_KEY_HEADER] = PREDICT_API_KEY;
    const response = await fetch(url, {
      cache: "no-store",
      headers,
      signal: controller.signal,
    });
    const text = await response.text();
    let payload: unknown = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      payload = { raw: text.slice(0, 500) };
    }
    if (!response.ok) throw new Error(`Predict HTTP ${response.status}: ${text.slice(0, 240)}`);
    const root = asRecord(payload);
    if (root?.success === false) throw new Error(`Predict API rejected ${path}`);
    return payload;
  } finally {
    clearTimeout(timeout);
  }
}

function getBucketCache(wallet: string, bucketStartSec: number) {
  const now = Date.now();
  for (const [key, value] of bucketCaches.entries()) {
    if (now - value.lastAccessAtMs > BUCKET_CACHE_TTL_MS) bucketCaches.delete(key);
  }
  const key = `${wallet}:${bucketStartSec}`;
  let cache = bucketCaches.get(key);
  if (!cache) {
    cache = {
      maker: blankRoleCache(),
      taker: blankRoleCache(),
      lastAccessAtMs: now,
    };
    bucketCaches.set(key, cache);
  }
  cache.lastAccessAtMs = now;
  return cache;
}

async function pollRole(
  cache: RoleCache,
  wallet: string,
  role: Role,
  bucketStartSec: number,
) {
  const isMaker = role === "MAKER";
  const bucketFloorMs = bucketStartSec * 1000 - 5_000;
  const pages = cache.initialized ? 1 : INITIAL_BACKFILL_PAGES_PER_ROLE;
  let after: string | null = null;
  let fetchedRows = 0;
  let fetchedPages = 0;
  let newLegs = 0;

  for (let page = 0; page < pages; page += 1) {
    const payload = await predictGet(MATCHES_PATH, {
      first: MATCH_PAGE_SIZE,
      after,
      signerAddress: wallet,
      isSignerMaker: isMaker,
    });
    fetchedPages += 1;
    const rows = responseRows(payload);
    fetchedRows += rows.length;
    const legs = roleLegs(payload, wallet, role);
    for (const leg of legs) {
      if (cache.seenLegs.has(leg.sourceLegId)) continue;
      cache.seenLegs.add(leg.sourceLegId);
      mergeParent(cache.events, leg);
      newLegs += 1;
    }

    const oldest = legs.reduce(
      (value, item) => Math.min(value, item.eventMs),
      Number.POSITIVE_INFINITY,
    );
    const cursor = responseCursor(payload);
    if (!cursor || rows.length === 0 || oldest <= bucketFloorMs) break;
    after = cursor;
  }
  cache.initialized = true;
  cache.lastPollAtMs = Date.now();
  return { fetchedRows, fetchedPages, newLegs };
}

function selectCurrentBtcMarket(
  events: WalletParentEvent[],
  bucketStartSec: number,
  dashboardMarketId: string | null,
) {
  const startMs = bucketStartSec * 1000 - 5_000;
  const endMs = (bucketStartSec + 300) * 1000 + 5_000;
  const current = events.filter((event) => (
    event.eventMs >= startMs &&
    event.eventMs <= endMs &&
    isBtcCryptoMarket(event)
  ));
  const groups = new Map<string, WalletParentEvent[]>();
  for (const event of current) {
    const key = event.marketId ?? "UNKNOWN";
    const group = groups.get(key) ?? [];
    group.push(event);
    groups.set(key, group);
  }
  if (!groups.size) {
    return {
      events: [] as WalletParentEvent[],
      nativeMarketId: null as string | null,
      mappingMode: "WAITING_TARGET_BTC5M_ACTIVITY",
      btcVerified: false,
      cryptoUpDownVerified: false,
    };
  }

  let selectedKey: string | null = null;
  if (dashboardMarketId && groups.has(dashboardMarketId)) selectedKey = dashboardMarketId;
  if (!selectedKey) {
    selectedKey = Array.from(groups.entries())
      .sort((left, right) => {
        const leftLatest = Math.max(...left[1].map((row) => row.eventMs));
        const rightLatest = Math.max(...right[1].map((row) => row.eventMs));
        if (leftLatest !== rightLatest) return rightLatest - leftLatest;
        const leftSize = left[1].reduce((sum, row) => sum + (row.shares ?? 0), 0);
        const rightSize = right[1].reduce((sum, row) => sum + (row.shares ?? 0), 0);
        return rightSize - leftSize;
      })[0]?.[0] ?? null;
  }
  const selected = selectedKey ? groups.get(selectedKey) ?? [] : [];
  return {
    events: selected.sort((a, b) => b.eventMs - a.eventMs),
    nativeMarketId: selectedKey === "UNKNOWN" ? null : selectedKey,
    mappingMode: dashboardMarketId && selectedKey === dashboardMarketId
      ? "DIRECT_MARKET_ID"
      : "BTC_CRYPTO_UP_DOWN_PLUS_5M_BUCKET",
    btcVerified: selected.length > 0,
    cryptoUpDownVerified: selected.every((event) => event.marketVariantType === "CRYPTO_UP_DOWN"),
  };
}

function normalizePositions(payload: unknown): PositionSummary | null {
  const rows = responseRows(payload);
  if (!rows.length) return null;
  let upShares = 0;
  let downShares = 0;
  let upCost = 0;
  let downCost = 0;
  for (const row of rows) {
    const side = normalizeOutcome(row.outcome);
    const shares = decimalValue(row.amount) ?? 0;
    const average = priceValue(row.averageBuyPriceUsd);
    if (side === "UP") {
      upShares += shares;
      if (average != null) upCost += shares * average;
    } else if (side === "DOWN") {
      downShares += shares;
      if (average != null) downCost += shares * average;
    }
  }
  return {
    upShares,
    downShares,
    upAveragePrice: upShares > 0 && upCost > 0 ? upCost / upShares : null,
    downAveragePrice: downShares > 0 && downCost > 0 ? downCost / downShares : null,
    rows: rows.length,
  };
}

async function getPositionSummary(wallet: string, marketId: string | null) {
  if (!marketId) return { value: null as PositionSummary | null, error: null as string | null };
  const cacheKey = `${wallet}:${marketId}`;
  const cached = positionCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) return { value: cached.value, error: cached.error };
  try {
    const payload = await predictGet(`${POSITIONS_PATH}/${wallet}`, {
      first: 100,
      marketId,
    });
    const value = normalizePositions(payload);
    positionCache.set(cacheKey, { value, error: null, expiresAt: Date.now() + POSITION_CACHE_MS });
    return { value, error: null };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    positionCache.set(cacheKey, { value: null, error: message, expiresAt: Date.now() + POSITION_CACHE_MS });
    return { value: null, error: message };
  }
}

export async function GET(request: Request) {
  const now = Date.now();
  const url = new URL(request.url);
  const wallet = (url.searchParams.get("wallet") ?? "").trim().toLowerCase();
  const bucketStartSec = Math.floor(Number(url.searchParams.get("bucketStartSec")));
  const dashboardMarketId = url.searchParams.get("dashboardMarketId")?.trim() || null;

  if (!/^0x[a-f0-9]{40}$/.test(wallet)) {
    return NextResponse.json(
      { ok: false, status: "INVALID_WALLET", error: "wallet must be a 0x-prefixed 20-byte address" },
      { status: 400 },
    );
  }
  if (!Number.isFinite(bucketStartSec) || bucketStartSec <= 0) {
    return NextResponse.json(
      { ok: false, status: "INVALID_BUCKET", error: "bucketStartSec is required" },
      { status: 400 },
    );
  }
  if (!PREDICT_API_KEY) {
    return NextResponse.json(
      {
        ok: false,
        status: "API_KEY_MISSING",
        error: "Set PREDICT_API_KEY (or PREDICT_FUN_API_KEY) in the Dashboard server environment.",
        apiBase: PREDICT_API_BASE,
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  try {
    const startedAt = performance.now();
    const cache = getBucketCache(wallet, bucketStartSec);
    const [makerFetch, takerFetch] = await Promise.all([
      pollRole(cache.maker, wallet, "MAKER", bucketStartSec),
      pollRole(cache.taker, wallet, "TAKER", bucketStartSec),
    ]);
    const allCached = [
      ...cache.maker.events.values(),
      ...cache.taker.events.values(),
    ];
    const selected = selectCurrentBtcMarket(allCached, bucketStartSec, dashboardMarketId);
    const positions = await getPositionSummary(wallet, selected.nativeMarketId);
    const makerEvents = selected.events.filter((event) => event.role === "MAKER");
    const takerEvents = selected.events.filter((event) => event.role === "TAKER");

    return NextResponse.json(
      {
        ok: true,
        status: "LIVE",
        apiKeyConfigured: true,
        wallet,
        bucketStartSec,
        bucketStartAt: new Date(bucketStartSec * 1000).toISOString(),
        bucketEndAt: new Date((bucketStartSec + 300) * 1000).toISOString(),
        dashboardMarketId,
        nativeMarketId: selected.nativeMarketId,
        mappingMode: selected.mappingMode,
        btcVerified: selected.btcVerified,
        cryptoUpDownVerified: selected.cryptoUpDownVerified,
        executedOnly: true,
        openOrdersAvailable: false,
        events: selected.events,
        counts: {
          total: selected.events.length,
          maker: makerEvents.length,
          taker: takerEvents.length,
          bid: selected.events.filter((event) => event.quoteType === "BID").length,
          ask: selected.events.filter((event) => event.quoteType === "ASK").length,
        },
        position: positions.value,
        positionError: positions.error,
        diagnostics: {
          cachedMakerOrders: cache.maker.events.size,
          cachedTakerOrders: cache.taker.events.size,
          cachedMakerLegs: cache.maker.seenLegs.size,
          cachedTakerLegs: cache.taker.seenLegs.size,
          makerFetchedRows: makerFetch.fetchedRows,
          takerFetchedRows: takerFetch.fetchedRows,
          makerFetchedPages: makerFetch.fetchedPages,
          takerFetchedPages: takerFetch.fetchedPages,
          makerNewLegs: makerFetch.newLegs,
          takerNewLegs: takerFetch.newLegs,
          pageSize: MATCH_PAGE_SIZE,
          initialBackfillPagesPerRole: INITIAL_BACKFILL_PAGES_PER_ROLE,
          apiBase: PREDICT_API_BASE,
          matchesPath: MATCHES_PATH,
          rttMs: Math.max(0, performance.now() - startedAt),
          fetchedAt: new Date(now).toISOString(),
        },
      },
      { headers: { "Cache-Control": "no-store, no-cache, must-revalidate" } },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      {
        ok: false,
        status: "OFFLINE",
        error: message,
        apiKeyConfigured: Boolean(PREDICT_API_KEY),
        wallet,
        bucketStartSec,
        dashboardMarketId,
        fetchedAt: new Date(now).toISOString(),
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
