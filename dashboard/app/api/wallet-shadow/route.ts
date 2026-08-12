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
const POSITION_CACHE_MS = 5_000;

type JsonRecord = Record<string, unknown>;

type WalletParentEvent = {
  id: string;
  marketId: string | null;
  marketTitle: string | null;
  role: "MAKER" | "TAKER";
  side: "UP" | "DOWN" | "UNKNOWN";
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

type PositionCacheEntry = {
  expiresAt: number;
  value: PositionSummary | null;
  error: string | null;
};

type PositionSummary = {
  upShares: number;
  downShares: number;
  upAveragePrice: number | null;
  downAveragePrice: number | null;
  rows: number;
};

const positionCache = new Map<string, PositionCacheEntry>();

function asRecord(value: unknown): JsonRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
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

function priceValue(value: unknown) {
  const parsed = numeric(value);
  if (parsed == null || parsed < 0) return null;
  if (parsed <= 1.5) return parsed;
  if (parsed > 1e12) {
    const scaled = parsed / 1e18;
    return scaled >= 0 && scaled <= 1.5 ? scaled : null;
  }
  if (parsed <= 100 && Number.isInteger(parsed)) return parsed / 100;
  return null;
}

function shareValue(value: unknown) {
  const parsed = numeric(value);
  if (parsed == null || parsed < 0) return null;
  if (parsed > 1e12) return parsed / 1e18;
  return parsed;
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

function nested(record: JsonRecord, key: string) {
  return asRecord(record[key]);
}

function addressOf(record: JsonRecord | null) {
  if (!record) return null;
  const user = nested(record, "user");
  const wallet = nested(record, "wallet");
  return stringValue(
    record.signerAddress,
    record.signer,
    record.walletAddress,
    record.address,
    record.makerAddress,
    record.takerAddress,
    user?.address,
    user?.walletAddress,
    wallet?.address,
  )?.toLowerCase() ?? null;
}

function normalizeOutcome(...values: unknown[]): "UP" | "DOWN" | "UNKNOWN" {
  for (const value of values) {
    let text: string | null = null;
    if (typeof value === "string") text = value;
    else if (asRecord(value)) {
      const row = asRecord(value)!;
      text = stringValue(row.name, row.label, row.outcome, row.title);
    }
    if (!text) continue;
    const normalized = text.trim().toUpperCase();
    if (normalized === "UP" || normalized.includes(" UP")) return "UP";
    if (normalized === "DOWN" || normalized.includes(" DOWN")) return "DOWN";
  }
  return "UNKNOWN";
}

function normalizeQuoteType(...values: unknown[]): "BID" | "ASK" | "UNKNOWN" {
  for (const value of values) {
    if (typeof value !== "string") continue;
    const normalized = value.trim().toUpperCase();
    if (normalized === "BID" || normalized === "BUY") return "BID";
    if (normalized === "ASK" || normalized === "SELL") return "ASK";
  }
  return "UNKNOWN";
}

function extractRows(payload: unknown): unknown[] {
  if (Array.isArray(payload)) return payload;
  const root = asRecord(payload);
  if (!root) return [];
  const data = asRecord(root.data);
  const candidates = [
    data?.items,
    data?.matches,
    data?.results,
    root.matches,
    root.items,
    root.results,
    root.data,
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) return candidate;
  }
  return [];
}

function marketIdOf(row: JsonRecord) {
  const market = nested(row, "market");
  return stringValue(row.marketId, row.market_id, market?.id, market?.marketId);
}

function marketTitleOf(row: JsonRecord) {
  const market = nested(row, "market");
  return stringValue(
    row.marketTitle,
    row.market_title,
    row.question,
    market?.title,
    market?.question,
    market?.name,
  );
}

function eventTimeOf(row: JsonRecord, participant: JsonRecord | null) {
  return timestampMs(
    participant?.executedAt ??
    participant?.executed_at ??
    participant?.timestamp ??
    row.executedAt ??
    row.executed_at ??
    row.timestamp ??
    row.createdAt ??
    row.created_at ??
    row.blockTimestamp,
  );
}

function quantityOf(record: JsonRecord | null, fallback: JsonRecord) {
  return shareValue(
    record?.filledAmount ??
    record?.filled_amount ??
    record?.shares ??
    record?.quantity ??
    record?.size ??
    record?.amount ??
    fallback.filledAmount ??
    fallback.filled_amount ??
    fallback.shares ??
    fallback.quantity ??
    fallback.size ??
    fallback.amount,
  );
}

function priceOf(record: JsonRecord | null, fallback: JsonRecord) {
  return priceValue(
    record?.averagePrice ??
    record?.average_price ??
    record?.price ??
    record?.fillPrice ??
    record?.fill_price ??
    fallback.averagePrice ??
    fallback.average_price ??
    fallback.price ??
    fallback.fillPrice ??
    fallback.fill_price,
  );
}

function eventFromParticipant(
  row: JsonRecord,
  participant: JsonRecord | null,
  role: "MAKER" | "TAKER",
  target: string,
  index: number,
): WalletParentEvent | null {
  const eventMs = eventTimeOf(row, participant);
  if (eventMs == null) return null;
  const market = nested(row, "market");
  const token = nested(row, "token");
  const side = normalizeOutcome(
    participant?.outcome,
    participant?.marketOutcome,
    participant?.tokenOutcome,
    row.outcome,
    row.marketOutcome,
    token?.outcome,
    token?.name,
    market?.outcome,
  );
  const quoteType = normalizeQuoteType(
    participant?.quoteType,
    participant?.orderSide,
    participant?.side,
    row.quoteType,
    row.orderSide,
    row.side,
  );
  const order = participant ? nested(participant, "order") : null;
  const orderHash = stringValue(
    participant?.orderHash,
    participant?.order_hash,
    order?.hash,
    row.orderHash,
    row.order_hash,
  );
  const transactionHash = stringValue(
    participant?.transactionHash,
    participant?.txHash,
    row.transactionHash,
    row.txHash,
    row.transaction_hash,
  );
  const settlementId = stringValue(
    participant?.settlementId,
    row.settlementId,
    row.settlement_id,
    row.id,
  );
  const shares = quantityOf(participant, row);
  const price = priceOf(participant, row);
  return {
    id: `${role}:${orderHash ?? settlementId ?? transactionHash ?? `${eventMs}:${index}`}:${side}`,
    marketId: marketIdOf(row),
    marketTitle: marketTitleOf(row),
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

function normalizeMatchRows(payload: unknown, wallet: string) {
  const target = wallet.toLowerCase();
  const legs: WalletParentEvent[] = [];
  for (const [index, item] of extractRows(payload).entries()) {
    const row = asRecord(item);
    if (!row) continue;
    const taker = nested(row, "taker");
    const makers = asArray(row.makers).map(asRecord).filter((value): value is JsonRecord => Boolean(value));
    let matchedNestedRole = false;

    if (addressOf(taker) === target) {
      const event = eventFromParticipant(row, taker, "TAKER", target, index);
      if (event) legs.push(event);
      matchedNestedRole = true;
    }
    for (const maker of makers) {
      if (addressOf(maker) !== target) continue;
      const event = eventFromParticipant(row, maker, "MAKER", target, index);
      if (event) legs.push(event);
      matchedNestedRole = true;
    }
    if (matchedNestedRole) continue;

    const signer = addressOf(row);
    if (signer !== target) continue;
    const isMaker = row.isSignerMaker === true || String(row.role ?? "").toUpperCase() === "MAKER";
    const isTaker = row.isSignerMaker === false || String(row.role ?? "").toUpperCase() === "TAKER";
    const event = eventFromParticipant(row, row, isMaker && !isTaker ? "MAKER" : "TAKER", target, index);
    if (event) legs.push(event);
  }

  const parents = new Map<string, WalletParentEvent>();
  for (const leg of legs) {
    const key = `${leg.role}:${leg.orderHash ?? leg.id}:${leg.marketId ?? "?"}:${leg.side}`;
    const current = parents.get(key);
    if (!current) {
      parents.set(key, { ...leg });
      continue;
    }
    const previousShares = current.shares ?? 0;
    const nextShares = leg.shares ?? 0;
    const totalShares = previousShares + nextShares;
    if (totalShares > 0 && current.price != null && leg.price != null) {
      current.price = (current.price * previousShares + leg.price * nextShares) / totalShares;
    } else if (current.price == null && leg.price != null) {
      current.price = leg.price;
    }
    current.shares = totalShares || current.shares || leg.shares;
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
  }
  return Array.from(parents.values()).sort((a, b) => b.eventMs - a.eventMs);
}

function isBitcoinTitle(title: string | null) {
  if (!title) return false;
  const normalized = title.toLowerCase();
  return normalized.includes("bitcoin") || /(^|\W)btc(\W|$)/.test(normalized);
}

function chooseCurrentMarket(
  events: WalletParentEvent[],
  bucketStartSec: number,
  dashboardMarketId: string | null,
) {
  const startMs = bucketStartSec * 1000 - 5_000;
  const endMs = (bucketStartSec + 300) * 1000 + 5_000;
  const inBucket = events.filter((event) => event.eventMs >= startMs && event.eventMs <= endMs);
  const btcRows = inBucket.filter((event) => isBitcoinTitle(event.marketTitle));
  const candidates = btcRows.length ? btcRows : inBucket;
  const groups = new Map<string, WalletParentEvent[]>();
  for (const event of candidates) {
    const key = event.marketId ?? "UNKNOWN";
    const group = groups.get(key) ?? [];
    group.push(event);
    groups.set(key, group);
  }
  if (!groups.size) {
    return {
      events: [] as WalletParentEvent[],
      nativeMarketId: null as string | null,
      mappingMode: "WAITING_TARGET_ACTIVITY",
      btcVerified: false,
    };
  }

  let selectedKey: string | null = null;
  if (dashboardMarketId && groups.has(dashboardMarketId)) selectedKey = dashboardMarketId;
  if (!selectedKey) {
    selectedKey = Array.from(groups.entries())
      .sort((left, right) => {
        const leftScore = left[1].reduce((sum, row) => sum + (row.shares ?? 1), 0);
        const rightScore = right[1].reduce((sum, row) => sum + (row.shares ?? 1), 0);
        return rightScore - leftScore;
      })[0]?.[0] ?? null;
  }
  const selected = selectedKey ? groups.get(selectedKey) ?? [] : [];
  const btcVerified = selected.some((event) => isBitcoinTitle(event.marketTitle));
  return {
    events: selected.sort((a, b) => b.eventMs - a.eventMs),
    nativeMarketId: selectedKey === "UNKNOWN" ? null : selectedKey,
    mappingMode:
      dashboardMarketId && selectedKey === dashboardMarketId
        ? "DIRECT_MARKET_ID"
        : btcVerified
          ? "BTC_TITLE_PLUS_5M_BUCKET"
          : "INFERRED_BY_5M_BUCKET_ACTIVITY",
    btcVerified,
  };
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
      "User-Agent": "BTC-5M-Wallet-Shadow-Lab/0.1",
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
    if (!response.ok) {
      throw new Error(`Predict HTTP ${response.status}: ${text.slice(0, 240)}`);
    }
    return payload;
  } finally {
    clearTimeout(timeout);
  }
}

function normalizePositions(payload: unknown): PositionSummary | null {
  const rows = extractRows(payload);
  if (!rows.length) return null;
  let upShares = 0;
  let downShares = 0;
  let upCost = 0;
  let downCost = 0;
  for (const item of rows) {
    const row = asRecord(item);
    if (!row) continue;
    const side = normalizeOutcome(row.outcome, row.side, row.marketOutcome, nested(row, "token")?.outcome);
    const shares = shareValue(row.amount ?? row.shares ?? row.quantity ?? row.size) ?? 0;
    const average = priceValue(row.averageBuyPrice ?? row.averagePrice ?? row.average_price);
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
    const payload = await predictGet(`${POSITIONS_PATH}/${wallet}`, { marketId });
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
  const limit = Math.max(20, Math.min(250, Math.floor(Number(url.searchParams.get("limit")) || 100)));

  if (!/^0x[a-f0-9]{40}$/.test(wallet)) {
    return NextResponse.json({ ok: false, status: "INVALID_WALLET", error: "wallet must be a 0x-prefixed 20-byte address" }, { status: 400 });
  }
  if (!Number.isFinite(bucketStartSec) || bucketStartSec <= 0) {
    return NextResponse.json({ ok: false, status: "INVALID_BUCKET", error: "bucketStartSec is required" }, { status: 400 });
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
    const payload = await predictGet(MATCHES_PATH, { signerAddress: wallet, limit });
    const normalized = normalizeMatchRows(payload, wallet);
    const selected = chooseCurrentMarket(normalized, bucketStartSec, dashboardMarketId);
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
          fetchedParents: normalized.length,
          requestLimit: limit,
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
