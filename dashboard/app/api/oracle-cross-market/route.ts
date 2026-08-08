import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const CROSS_ORACLE_BASE = process.env.PREDICT_CROSS_ORACLE_BASE_URL ?? "http://127.0.0.1:8767";

export async function GET() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2_000);
  try {
    const response = await fetch(`${CROSS_ORACLE_BASE}/state`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      return NextResponse.json(
        {
          ok: false,
          status: "OFFLINE",
          error: `cross-oracle collector returned HTTP ${response.status}`,
          fetchedAt: new Date().toISOString(),
        },
        { status: 503 },
      );
    }
    const state = await response.json();
    return NextResponse.json(
      { ok: true, state, fetchedAt: new Date().toISOString() },
      { headers: { "Cache-Control": "no-store, no-cache, must-revalidate" } },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      {
        ok: false,
        status: "OFFLINE",
        error: `cross-oracle collector unavailable: ${message}`,
        fetchedAt: new Date().toISOString(),
      },
      { status: 503 },
    );
  } finally {
    clearTimeout(timeout);
  }
}
