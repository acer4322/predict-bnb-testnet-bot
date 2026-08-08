"use client";

import { useEffect, useMemo, useState } from "react";
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

function finite(value: unknown): number | null {
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

export default function OracleCrossMarketPanel() {
  const [host, setHost] = useState<Element | null>(null);
  const [researchView, setResearchView] = useState(false);
  const [state, setState] = useState<CrossOracleState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const locate = () => {
      const next = document.querySelector(".market-panel");
      setHost(next);
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

  return createPortal(
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
        只供研究與後續 cross-oracle 回放；不參與策略判斷、不送單。起始價為本機 Chainlink BTC/USD 在該 5 分鐘邊界最近的已保存 tick，offset 會明示，避免把重建值誤當 Polymarket 官方顯示值。
      </p>
    </div>,
    host,
  );
}
