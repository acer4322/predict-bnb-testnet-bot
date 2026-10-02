import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const CROSS_ORACLE_BASE = process.env.PREDICT_CROSS_ORACLE_BASE_URL ?? "http://127.0.0.1:8767";
const COLLECTOR_TIMEOUT_MS = 3_000;
const STALE_FALLBACK_MAX_MS = 5_000;

type CachedState = {
  state: Record<string, unknown>;
  fetchedAtMs: number;
};

let lastGood: CachedState | null = null;

function staleState(state: Record<string, unknown>, staleForMs: number) {
  const clone = JSON.parse(JSON.stringify(state)) as Record<string, any>;
  for (const key of ["chainlink", "polymarket"]) {
    const feed = clone[key];
    if (feed && typeof feed === "object") {
      const age = Number(feed.ageMs);
      feed.ageMs = Number.isFinite(age) ? Math.max(0, age) + staleForMs : staleForMs;
    }
  }
  if (clone.continuity && typeof clone.continuity === "object") {
    clone.continuity = {
      ...clone.continuity,
      healthy: false,
      proxyStale: true,
      proxyStaleForMs: staleForMs,
    };
  }
  clone.dashboardProxy = {
    stale: true,
    staleForMs,
    source: "LAST_GOOD_CROSS_ORACLE_STATE",
  };
  return clone;
}

export async function GET() {
  const controller = new AbortController();
  const startedAt = performance.now();
  const timeout = setTimeout(() => controller.abort(), COLLECTOR_TIMEOUT_MS);
  try {
    const response = await fetch(`${CROSS_ORACLE_BASE}/state`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`collector returned HTTP ${response.status}`);
    }
    const state = (await response.json()) as Record<string, unknown>;
    const fetchedAtMs = Date.now();
    lastGood = { state, fetchedAtMs };
    return NextResponse.json(
      {
        ok: true,
        state,
        stale: false,
        collectorRttMs: Math.max(0, performance.now() - startedAt),
        fetchedAt: new Date(fetchedAtMs).toISOString(),
      },
      { headers: { "Cache-Control": "no-store, no-cache, must-revalidate" } },
    );
  } catch (error) {
    const nowMs = Date.now();
    const message = error instanceof Error ? error.message : String(error);
    const staleForMs = lastGood ? Math.max(0, nowMs - lastGood.fetchedAtMs) : null;
    if (lastGood && staleForMs !== null && staleForMs <= STALE_FALLBACK_MAX_MS) {
      return NextResponse.json(
        {
          ok: true,
          state: staleState(lastGood.state, staleForMs),
          stale: true,
          warning: `cross-oracle live read failed; showing last good state: ${message}`,
          staleForMs,
          collectorTimeoutMs: COLLECTOR_TIMEOUT_MS,
          fetchedAt: new Date(nowMs).toISOString(),
        },
        { headers: { "Cache-Control": "no-store, no-cache, must-revalidate" } },
      );
    }
    return NextResponse.json(
      {
        ok: false,
        status: "OFFLINE",
        error: `cross-oracle collector unavailable: ${message}`,
        collectorTimeoutMs: COLLECTOR_TIMEOUT_MS,
        fetchedAt: new Date(nowMs).toISOString(),
      },
      { status: 503 },
    );
  } finally {
    clearTimeout(timeout);
  }
}
