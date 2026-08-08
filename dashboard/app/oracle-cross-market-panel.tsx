"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

type QuoteSide = {
  tokenId?: string | null;
  bestBid?: number | null;
  bestAsk?: number | null;
  lastTrade?: number | null;
};

type CrossOracleState = {
  status?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  generatedAt?: string;
  generatedAtMs?: number | null;
  chainlink?: {
    status?: string;
    symbol?: string;
    price?: number | null;
    sourceTimestampMs?: number | null;
    receivedTimestampMs?: number | null;
    ageMs?: number | null;
    error?: string | null;
  };
  polymarket?: {
    status?: string;
    sourceTimestampMs?: number | null;
    receivedTimestampMs?: number | null;
    ageMs?: number | null;
    error?: string | null;
    startPrice?: number | null;
    startPriceTimestampMs?: number | null;
    startPriceOffsetMs?: number | null;
    secondsLeft?: number | null;
    up?: QuoteSide;
    down?: QuoteSide;
    market?: {
      id?: string | null;
      slug?: string | null;
      question?: string | null;
      conditionId?: string | null;
      windowStartMs?: number | null;
      windowEndMs?: number | null;
      secondsLeft?: number | null;
      upTokenId?: string | null;
      downTokenId?: string | null;
    } | null;
  };
  storage?: {
    schemaVersion?: string;
    dbPath?: string;
    dbBytes?: number | null;
    chainlinkRows?: number;
    polymarketRows?: number;
    rawPayloadsStored?: boolean;
    sourceAndReceiptTimestampsStored?: boolean;
  };
};

type ApiPayload = {
  ok?: boolean;
  state?: CrossOracleState;
  error?: string;
};

type PolyTrajectoryPoint = {
  slug: string;
  atMs: number;
  secondsLeft: number;
  upAsk: number | null;
  downAsk: number | null;
};

const POLY_UP_COLOR = "#55d8ff";
const POLY_DOWN_COLOR = "#c58cff";
const TRAJECTORY_STORAGE_PREFIX = "btc5m-poly-trajectory:";
const MARKET_DURATION_SECONDS = 300;

function finite(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function usd(value: unknown) {
  const number = finite(value);
  return number == null ? "—" : `$${number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function probability(value: unknown) {
  const number = finite(value);
  return number == null ? "—" : number.toFixed(3);
}

function duration(value: unknown) {
  const number = finite(value);
  if (number == null) return "—:—";
  const seconds = Math.max(0, Math.floor(number));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function age(value: unknown) {
  const number = finite(value);
  if (number == null) return "—";
  if (number < 1000) return `${Math.round(number)} ms`;
  return `${(number / 1000).toFixed(1)} s`;
}

function sourceTime(value: unknown) {
  const number = finite(value);
  if (number == null) return "—";
  const date = new Date(number);
  return `${date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function bytes(value: unknown) {
  const number = finite(value);
  if (number == null) return "—";
  if (number < 1024 * 1024) return `${(number / 1024).toFixed(1)} KiB`;
  return `${(number / 1024 / 1024).toFixed(1)} MiB`;
}

function shortId(value: string | null | undefined, keep = 8) {
  if (!value) return "—";
  if (value.length <= keep * 2 + 3) return value;
  return `${value.slice(0, keep)}…${value.slice(-keep)}`;
}

function statusLive(value: string | null | undefined) {
  return String(value ?? "").toUpperCase() === "LIVE";
}

function trajectoryStorageKey(slug: string) {
  return `${TRAJECTORY_STORAGE_PREFIX}${slug}`;
}

function loadTrajectory(slug: string): PolyTrajectoryPoint[] {
  try {
    const raw = window.localStorage.getItem(trajectoryStorageKey(slug));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((row): PolyTrajectoryPoint | null => {
        if (!row || row.slug !== slug) return null;
        const atMs = finite(row.atMs);
        const secondsLeft = finite(row.secondsLeft);
        const upAsk = finite(row.upAsk);
        const downAsk = finite(row.downAsk);
        if (atMs == null || secondsLeft == null) return null;
        return {
          slug,
          atMs,
          secondsLeft: Math.max(0, Math.min(MARKET_DURATION_SECONDS, secondsLeft)),
          upAsk,
          downAsk,
        };
      })
      .filter((row): row is PolyTrajectoryPoint => row != null)
      .slice(-720);
  } catch {
    return [];
  }
}

function saveTrajectory(slug: string, points: PolyTrajectoryPoint[]) {
  try {
    window.localStorage.setItem(trajectoryStorageKey(slug), JSON.stringify(points.slice(-720)));
  } catch {
    // Display cache is best-effort only; raw collector data remains authoritative.
  }
}

function PolyTrajectoryOverlay({
  host,
  points,
  currentSecondsLeft,
}: {
  host: Element;
  points: PolyTrajectoryPoint[];
  currentSecondsLeft: number | null;
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const overlay = ref.current;
    const base = host.querySelector<HTMLCanvasElement>("canvas.market-chart");
    if (!overlay || !base) return;

    const hostElement = host as HTMLElement;
    const previousInlinePosition = hostElement.style.position;
    if (window.getComputedStyle(hostElement).position === "static") {
      hostElement.style.position = "relative";
    }

    const render = () => {
      const hostRect = host.getBoundingClientRect();
      const baseRect = base.getBoundingClientRect();
      if (baseRect.width <= 0 || baseRect.height <= 0) return;

      overlay.style.left = `${baseRect.left - hostRect.left}px`;
      overlay.style.top = `${baseRect.top - hostRect.top}px`;
      overlay.style.width = `${baseRect.width}px`;
      overlay.style.height = `${baseRect.height}px`;

      const dpr = window.devicePixelRatio || 1;
      overlay.width = Math.max(1, Math.round(baseRect.width * dpr));
      overlay.height = Math.max(1, Math.round(baseRect.height * dpr));
      const ctx = overlay.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, baseRect.width, baseRect.height);

      const width = baseRect.width;
      const height = baseRect.height;
      const pad = 18;
      const elapsedNow = Math.max(
        1,
        MARKET_DURATION_SECONDS - Math.max(0, Math.min(MARKET_DURATION_SECONDS, currentSecondsLeft ?? points.at(-1)?.secondsLeft ?? MARKET_DURATION_SECONDS)),
      );

      const draw = (select: (point: PolyTrajectoryPoint) => number | null, color: string) => {
        let started = false;
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.15;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.setLineDash([7, 5]);
        for (const point of points) {
          const value = select(point);
          if (value == null || value < 0 || value > 1) continue;
          const elapsed = MARKET_DURATION_SECONDS - point.secondsLeft;
          if (elapsed < 0 || elapsed > elapsedNow + 2) continue;
          const progress = Math.max(0, Math.min(1, elapsed / elapsedNow));
          const x = pad + progress * (width - pad * 2);
          const y = height - pad - value * (height - pad * 2);
          if (!started) {
            ctx.moveTo(x, y);
            started = true;
          } else {
            ctx.lineTo(x, y);
          }
        }
        if (started) ctx.stroke();
        ctx.setLineDash([]);
      };

      draw(point => point.upAsk, POLY_UP_COLOR);
      draw(point => point.downAsk, POLY_DOWN_COLOR);
    };

    render();
    const resizeObserver = new ResizeObserver(render);
    resizeObserver.observe(base);
    resizeObserver.observe(host);
    window.addEventListener("resize", render);
    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", render);
      hostElement.style.position = previousInlinePosition;
    };
  }, [host, points, currentSecondsLeft]);

  return <canvas
    ref={ref}
    className="poly-market-chart-overlay"
    aria-label="Polymarket UP 與 DOWN 價格疊加軌跡"
    style={{
      position: "absolute",
      zIndex: 3,
      pointerEvents: "none",
    }}
  />;
}

export default function OracleCrossMarketPanel() {
  const [host, setHost] = useState<Element | null>(null);
  const [chartHost, setChartHost] = useState<Element | null>(null);
  const [researchView, setResearchView] = useState(false);
  const [state, setState] = useState<CrossOracleState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [trajectory, setTrajectory] = useState<PolyTrajectoryPoint[]>([]);

  useEffect(() => {
    const locate = () => {
      const next = document.querySelector(".market-panel");
      setHost(next);
      setChartHost(next?.querySelector(".chart-wrap") ?? null);
      const eyebrow = next?.querySelector(".market-title .eyebrow");
      setResearchView(Boolean(eyebrow?.textContent?.includes("模擬研究")));
    };
    locate();
    const observer = new MutationObserver(locate);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;
    const load = async () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch("/api/oracle-cross-market", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await response.json() as ApiPayload;
        if (!response.ok || !payload.ok || !payload.state) {
          throw new Error(payload.error ?? `HTTP ${response.status}`);
        }
        if (!active) return;
        setState(payload.state);
        setError(null);
      } catch (caught) {
        if (!active || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
    const timer = window.setInterval(load, 1000);
    const visibility = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);

  const chainlink = state?.chainlink;
  const poly = state?.polymarket;
  const market = poly?.market;
  const activeSlug = market?.slug ?? null;

  useEffect(() => {
    if (!activeSlug) {
      setTrajectory([]);
      return;
    }
    setTrajectory(loadTrajectory(activeSlug));
  }, [activeSlug]);

  useEffect(() => {
    if (!activeSlug) return;
    const secondsLeft = finite(poly?.secondsLeft ?? market?.secondsLeft);
    const upAsk = finite(poly?.up?.bestAsk);
    const downAsk = finite(poly?.down?.bestAsk);
    if (secondsLeft == null || (upAsk == null && downAsk == null)) return;
    const normalizedSecondsLeft = Math.max(0, Math.min(MARKET_DURATION_SECONDS, secondsLeft));
    const windowEndMs = finite(market?.windowEndMs);
    const atMs = windowEndMs == null
      ? finite(state?.generatedAtMs) ?? Date.now()
      : windowEndMs - normalizedSecondsLeft * 1000;

    setTrajectory(current => {
      const sameMarket = current.filter(point => point.slug === activeSlug);
      const nextPoint: PolyTrajectoryPoint = {
        slug: activeSlug,
        atMs,
        secondsLeft: normalizedSecondsLeft,
        upAsk,
        downAsk,
      };
      const last = sameMarket.at(-1);
      let next: PolyTrajectoryPoint[];
      if (last && Math.abs(last.atMs - atMs) < 650) {
        next = [...sameMarket.slice(0, -1), nextPoint];
      } else {
        next = [...sameMarket, nextPoint].slice(-720);
      }
      saveTrajectory(activeSlug, next);
      return next;
    });
  }, [activeSlug, market?.secondsLeft, market?.windowEndMs, poly?.secondsLeft, poly?.up?.bestAsk, poly?.down?.bestAsk, state?.generatedAtMs]);

  const current = finite(chainlink?.price);
  const start = finite(poly?.startPrice);
  const delta = current != null && start != null ? current - start : null;
  const deltaBps = current != null && start != null && start > 0 ? (current - start) / start * 10_000 : null;
  const marketLabel = market?.id ? `Market ${market.id}` : market?.slug ?? "等待 Polymarket 市場";
  const collectorLive = statusLive(chainlink?.status) && statusLive(poly?.status);
  const boundaryQuality = finite(poly?.startPriceOffsetMs);
  const startDetail = start == null
    ? "等待下一個完整 5m 開盤基準"
    : `Chainlink window-open · offset ${boundaryQuality == null ? "—" : `${boundaryQuality >= 0 ? "+" : ""}${Math.round(boundaryQuality)} ms`}`;
  const streamDetail = useMemo(() => {
    const pieces = [
      `Chainlink ${chainlink?.status ?? "—"} · age ${age(chainlink?.ageMs)} · source ${sourceTime(chainlink?.sourceTimestampMs)}`,
      `Polymarket ${poly?.status ?? "—"} · age ${age(poly?.ageMs)} · source ${sourceTime(poly?.sourceTimestampMs)}`,
    ];
    return pieces.join(" ｜ ");
  }, [chainlink?.status, chainlink?.ageMs, chainlink?.sourceTimestampMs, poly?.status, poly?.ageMs, poly?.sourceTimestampMs]);

  if (!host || !researchView) return null;

  const legendHost = chartHost?.querySelector(".chart-head > div") ?? null;
  const currentSecondsLeft = finite(poly?.secondsLeft ?? market?.secondsLeft);

  return <>
    {createPortal(
      <div
        className="cross-oracle-market"
        aria-label="Polymarket BTC Up or Down 5m 與 Chainlink BTCUSD 即時研究資料"
        style={{
          marginTop: 22,
          paddingTop: 22,
          borderTop: "1px solid rgba(126,145,178,.24)",
        }}
      >
        <div className="market-title">
          <div>
            <span className="eyebrow">POLYMARKET · CROSS-ORACLE RESEARCH · READ ONLY</span>
            <h1>BTC Up or Down 5m</h1>
            <p>{marketLabel} · Chainlink BTC/USD · {market?.slug ?? "正在尋找當前 5m 市場"}</p>
          </div>
          <div className="countdown">
            <span>距離結束</span>
            <strong>{duration(poly?.secondsLeft ?? market?.secondsLeft)}</strong>
          </div>
        </div>

        <div className="ticker-grid">
          <div className="ticker neutral">
            <span>起始價</span>
            <strong>{usd(start)}</strong>
            <small>{startDetail}</small>
          </div>
          <div className="ticker neutral">
            <span>即時價</span>
            <strong>{usd(current)}</strong>
            <small className={(delta ?? 0) >= 0 ? "positive" : "negative"}>
              {delta == null ? "—" : `${delta >= 0 ? "+" : ""}${delta.toFixed(2)} · ${deltaBps == null ? "—" : `${deltaBps >= 0 ? "+" : ""}${deltaBps.toFixed(3)} bps`}`}
            </small>
          </div>
          <div className="ticker up">
            <span>UP 最佳賣價</span>
            <strong>{probability(poly?.up?.bestAsk)}</strong>
            <small>買價 {probability(poly?.up?.bestBid)} · last {probability(poly?.up?.lastTrade)}</small>
          </div>
          <div className="ticker down">
            <span>DOWN 最佳賣價</span>
            <strong>{probability(poly?.down?.bestAsk)}</strong>
            <small>買價 {probability(poly?.down?.bestBid)} · last {probability(poly?.down?.lastTrade)}</small>
          </div>
        </div>

        <div style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "8px 18px",
          marginTop: 12,
          fontSize: 12,
          color: "rgba(215,225,245,.72)",
        }}>
          <span style={{ color: collectorLive ? "#8df4c0" : "#ffb45c" }}>
            ● {collectorLive ? "兩條即時來源正常" : "來源尚未全部 LIVE"}
          </span>
          <span>{streamDetail}</span>
          <span>Condition {shortId(market?.conditionId)}</span>
          <span>UP token {shortId(poly?.up?.tokenId, 6)} · DOWN token {shortId(poly?.down?.tokenId, 6)}</span>
          <span>研究 DB：{state?.storage?.chainlinkRows ?? 0} Chainlink ticks · {state?.storage?.polymarketRows ?? 0} Polymarket events · {bytes(state?.storage?.dbBytes)}</span>
          <span>原始 payload + source/receipt timestamps 已保存</span>
        </div>

        {(error || chainlink?.error || poly?.error) && <p style={{ marginTop: 10, color: "#ffb45c", fontSize: 12 }}>
          {error ?? chainlink?.error ?? poly?.error}
        </p>}
        <p style={{ marginTop: 8, fontSize: 11, color: "rgba(215,225,245,.52)" }}>
          只供研究與後續 cross-oracle 回放；不參與策略判斷、不送單。起始價為本機 Chainlink BTC/USD 在該 5 分鐘邊界最近的已保存 tick，offset 會明示，避免把重建值誤當 Polymarket 官方顯示值。上方 Binance 市場價格軌跡會同步疊加 Polymarket Ask：青色虛線為 Poly UP、紫色虛線為 Poly DOWN。
        </p>
      </div>,
      host,
    )}

    {chartHost && createPortal(
      <PolyTrajectoryOverlay host={chartHost} points={trajectory} currentSecondsLeft={currentSecondsLeft} />,
      chartHost,
    )}

    {legendHost && createPortal(
      <span style={{ marginLeft: 12, whiteSpace: "nowrap" }} data-poly-overlay-legend>
        <i style={{ display: "inline-block", width: 9, height: 3, borderRadius: 3, background: POLY_UP_COLOR, margin: "0 4px 2px 0" }} /> Poly UP
        <i style={{ display: "inline-block", width: 9, height: 3, borderRadius: 3, background: POLY_DOWN_COLOR, margin: "0 4px 2px 10px" }} /> Poly DOWN
      </span>,
      legendHost,
    )}
  </>;
}
