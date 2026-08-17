"use client";

import { useEffect } from "react";

type CrossOracleHealth = {
  ok?: boolean;
  state?: {
    polymarket?: {
      status?: string | null;
      ageMs?: number | null;
      error?: string | null;
      market?: { slug?: string | null } | null;
    } | null;
  } | null;
};

type BinanceRealtimeHealth = {
  latest?: {
    market_id?: number | null;
    up_ask?: number | null;
    down_ask?: number | null;
  } | null;
};

const POLY_MAX_DISPLAY_AGE_MS = 2500;

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function finite(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function synchronizedSourcesHealthy(
  cross: CrossOracleHealth,
  binance: BinanceRealtimeHealth,
) {
  const poly = cross.state?.polymarket;
  const ageMs = finite(poly?.ageMs);
  const marketId = finite(binance.latest?.market_id);
  const hasBinanceQuote = (
    finite(binance.latest?.up_ask) != null
    || finite(binance.latest?.down_ask) != null
  );
  return Boolean(
    cross.ok
    && String(poly?.status ?? "").toUpperCase() === "LIVE"
    && !poly?.error
    && poly?.market?.slug
    && ageMs != null
    && ageMs <= POLY_MAX_DISPLAY_AGE_MS
    && marketId != null
    && marketId > 0
    && hasBinanceQuote
  );
}

/**
 * Visibility coordinator only.
 *
 * The synchronized Poly overlay is the sole painter while both feeds are
 * healthy. The native MarketChart is the sole painter while the Poly overlay
 * is unavailable/stale. This component must never draw into either canvas;
 * doing so creates competing animation loops and makes old trajectory segments
 * appear/disappear between frames.
 */
export default function MarketChartRecovery() {
  useEffect(() => {
    let active = true;
    let healthy = false;
    let loading = false;
    let applyingVisibility = false;
    let controller: AbortController | null = null;

    const applyVisibility = () => {
      if (applyingVisibility) return;
      const base = document.querySelector<HTMLCanvasElement>("canvas.market-chart");
      const overlay = document.querySelector<HTMLCanvasElement>(
        "canvas.synchronized-market-chart-overlay",
      );
      if (!base) return;

      const baseVisibility = overlay && healthy ? "hidden" : "visible";
      const overlayVisibility = healthy ? "visible" : "hidden";
      applyingVisibility = true;
      try {
        if (base.style.visibility !== baseVisibility) {
          base.style.visibility = baseVisibility;
        }
        if (overlay && overlay.style.visibility !== overlayVisibility) {
          overlay.style.visibility = overlayVisibility;
        }
        base.dataset.chartPainter = overlay && healthy
          ? "synchronized-overlay"
          : "native-market-chart";
        if (overlay) {
          overlay.dataset.chartPainter = healthy
            ? "synchronized-overlay"
            : "disabled-fallback";
        }
      } finally {
        applyingVisibility = false;
      }
    };

    const refreshHealth = async () => {
      if (!active || loading || document.visibilityState === "hidden") return;
      loading = true;
      controller?.abort();
      controller = new AbortController();
      try {
        const [crossResponse, binanceResponse] = await Promise.all([
          fetch("/api/oracle-cross-market", {
            cache: "no-store",
            signal: controller.signal,
          }),
          fetch(apiUrl("/api/realtime"), {
            cache: "no-store",
            signal: controller.signal,
          }),
        ]);
        const cross = await crossResponse.json() as CrossOracleHealth;
        const binance = await binanceResponse.json() as BinanceRealtimeHealth;
        healthy = Boolean(
          crossResponse.ok
          && binanceResponse.ok
          && synchronizedSourcesHealthy(cross, binance)
        );
      } catch (caught) {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        healthy = false;
      } finally {
        loading = false;
        if (active) applyVisibility();
      }
    };

    const observer = new MutationObserver(applyVisibility);
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["style"],
    });

    const visibility = () => {
      if (document.visibilityState === "visible") void refreshHealth();
    };

    applyVisibility();
    void refreshHealth();
    const timer = window.setInterval(() => void refreshHealth(), 1000);
    document.addEventListener("visibilitychange", visibility);

    return () => {
      active = false;
      controller?.abort();
      observer.disconnect();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
      const base = document.querySelector<HTMLCanvasElement>("canvas.market-chart");
      const overlay = document.querySelector<HTMLCanvasElement>(
        "canvas.synchronized-market-chart-overlay",
      );
      if (base) {
        base.style.visibility = "visible";
        delete base.dataset.chartPainter;
      }
      if (overlay) {
        overlay.style.visibility = "visible";
        delete overlay.dataset.chartPainter;
      }
    };
  }, []);

  return null;
}
