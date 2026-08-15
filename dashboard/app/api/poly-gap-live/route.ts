import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = process.env.PREDICT_POLY_GAP_LIVE_BASE_URL ?? "http://127.0.0.1:8769";

async function forward(path: string, init?: RequestInit) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2_500);
  try {
    const response = await fetch(`${BASE}${path}`, {
      ...init,
      cache: "no-store",
      signal: controller.signal,
    });
    const body = await response.json();
    return NextResponse.json(body, {
      status: response.status,
      headers: { "Cache-Control": "no-store, no-cache, must-revalidate" },
    });
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        error: `dedicated Poly GAP live executor unavailable: ${error instanceof Error ? error.message : String(error)}`,
      },
      { status: 503 },
    );
  } finally {
    clearTimeout(timeout);
  }
}

export async function GET() {
  return forward("/state");
}

export async function POST(request: Request) {
  const body = await request.json();
  return forward("/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
