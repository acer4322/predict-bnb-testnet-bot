import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const STRATEGY_BASE = process.env.PREDICT_CROSS_ORACLE_STRATEGY_BASE_URL ?? "http://127.0.0.1:8768";
const REQUEST_TIMEOUT_MS = 8_000;
const STATE_CACHE_MS = 10_000;

type CachedState = {
  state: unknown;
  fetchedAtMs: number;
};

let cached: CachedState | null = null;
let inFlight: Promise<CachedState> | null = null;

async function loadStrategyState(): Promise<CachedState> {
  const now = Date.now();
  if (cached && now - cached.fetchedAtMs <= STATE_CACHE_MS) return cached;
  if (inFlight) return inFlight;

  inFlight = (async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(`${STRATEGY_BASE}/state`, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`cross-oracle strategy sidecar returned HTTP ${response.status}`);
      }
      const state = await response.json();
      const next = { state, fetchedAtMs: Date.now() };
      cached = next;
      return next;
    } finally {
      clearTimeout(timeout);
    }
  })();

  try {
    return await inFlight;
  } finally {
    inFlight = null;
  }
}

export async function GET() {
  try {
    const result = await loadStrategyState();
    return NextResponse.json(
      {
        ok: true,
        state: result.state,
        fetchedAt: new Date(result.fetchedAtMs).toISOString(),
        stateAgeMs: Math.max(0, Date.now() - result.fetchedAtMs),
        cached: Date.now() - result.fetchedAtMs > 250,
      },
      { headers: { "Cache-Control": "no-store, no-cache, must-revalidate" } },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);

    // A previously successful Paper snapshot is much safer for a research
    // dashboard than silently replacing every historical counter with zero.
    // It is display-only; trading processes never consume this Next route.
    if (cached) {
      return NextResponse.json(
        {
          ok: true,
          state: cached.state,
          fetchedAt: new Date(cached.fetchedAtMs).toISOString(),
          stateAgeMs: Math.max(0, Date.now() - cached.fetchedAtMs),
          cached: true,
          stale: true,
          warning: `cross-oracle strategy refresh failed; showing last good snapshot: ${message}`,
        },
        { headers: { "Cache-Control": "no-store, no-cache, must-revalidate" } },
      );
    }

    return NextResponse.json(
      {
        ok: false,
        status: "OFFLINE",
        error: `cross-oracle strategy sidecar unavailable: ${message}`,
        fetchedAt: new Date().toISOString(),
      },
      { status: 503 },
    );
  }
}
