import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const BACKEND_BASE = process.env.PREDICT_API_BASE_URL ?? "http://127.0.0.1:8766";

export async function GET() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4_000);
  try {
    const response = await fetch(`${BACKEND_BASE}/api/live-details`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      return NextResponse.json(
        {
          ok: false,
          experiment: null,
          error: `BTC 5M API returned HTTP ${response.status}`,
          fetchedAt: new Date().toISOString(),
        },
        { status: 503 },
      );
    }
    const payload = await response.json() as {
      micropriceSignalLifecycle?: unknown;
    };
    const experiment = payload?.micropriceSignalLifecycle ?? null;
    return NextResponse.json(
      {
        ok: experiment != null,
        experiment,
        error: experiment == null
          ? "Lifecycle sidecar is waiting for its first collector snapshot."
          : null,
        fetchedAt: new Date().toISOString(),
      },
      { status: experiment == null ? 503 : 200 },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      {
        ok: false,
        experiment: null,
        error: `Lifecycle sidecar unavailable: ${message}`,
        fetchedAt: new Date().toISOString(),
      },
      { status: 503 },
    );
  } finally {
    clearTimeout(timeout);
  }
}
