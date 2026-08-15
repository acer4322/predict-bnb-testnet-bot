"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

const STRATEGY = "R_POLY_INVERTED_PRICE";

type Summary = {
  trades?: number;
  open?: number;
  closed?: number;
  wins?: number;
  losses?: number;
  winRate?: number | null;
  grossPnlUsdt?: number | null;
  rolling10Markets?: number;
  rolling10AveragePnlUsdt?: number | null;
  rolling10AverageReversals?: number | null;
};

type State = {
  summaries?: Record<string, Summary>;
  parameters?: {
    invertedMinPolySelectedMid?: number;
    invertedMaxBinanceSelectedMid?: number;
    invertedMirrorTolerance?: number;
    invertedMaxEntryAsk?: number;
  };
  invertedPriceStrategy?: {
    paperOnly?: boolean;
    exit?: string;
    oneTradePerMarket?: boolean;
  };
};

type Payload = { ok?: boolean; state?: State };

function pct(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}

function prob(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(2);
}

function money(value: number | null | undefined, digits = 4) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : "−"}$${Math.abs(value).toFixed(digits)}`;
}

export default function OracleCrossInvertedPriceCard() {
  const [target, setTarget] = useState<Element | null>(null);
  const [state, setState] = useState<State | null>(null);

  useEffect(() => {
    const locate = () => {
      const section = document.querySelector(
        '#poly-cross-market-panel section[aria-label="Polymarket lead Paper strategies"]',
      );
      const grid = section?.querySelector(".research-strategy-grid") ?? null;
      setTarget(current => current === grid ? current : grid);
    };
    locate();
    const observer = new MutationObserver(locate);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    const load = async () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch("/api/oracle-cross-strategies", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await response.json() as Payload;
        if (!alive || !response.ok || !payload.ok || !payload.state) return;
        setState(payload.state);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
      }
    };
    void load();
    const timer = window.setInterval(load, 2000);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, []);

  if (!target) return null;

  const summary = state?.summaries?.[STRATEGY] ?? {};
  const params = state?.parameters ?? {};
  const pnl = summary.grossPnlUsdt ?? 0;

  return createPortal(
    <article className="m-exit-card cyan" data-poly-inverted-price-strategy={STRATEGY}>
      <div className="m-exit-card-head">
        <div>
          <span className="eyebrow">{STRATEGY}</span>
          <h3>Poly / Binance 鏡像顛倒</h3>
        </div>
        <span className="m-exit-id">PAPER ONLY</span>
      </div>
      <p>
        Poly 明確看向某側、Binance 同側價格卻幾乎呈鏡像反向時，信任 Poly 方向買入 Binance。
        第一版每個 5 分鐘市場最多一筆，持有到官方結算，不混入翻轉提前退出。
      </p>
      <div className="m-exit-primary-stats">
        <div><span>交易／持倉</span><strong>{summary.trades ?? 0} / {summary.open ?? 0}</strong></div>
        <div><span>勝率</span><strong>{pct(summary.winRate)}</strong></div>
        <div><span>Gross PnL</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong></div>
      </div>
      <div className="poly-protection" style={{ marginTop: 10 }}>
        <div>
          <span>Poly 選定側</span>
          <strong>≥ {prob(params.invertedMinPolySelectedMid)}</strong>
        </div>
        <div>
          <span>Binance 同側 mid</span>
          <strong>≤ {prob(params.invertedMaxBinanceSelectedMid)}</strong>
        </div>
        <div>
          <span>鏡像誤差</span>
          <strong>≤ {prob(params.invertedMirrorTolerance)}</strong>
        </div>
      </div>
      <small>
        訊號用 mid：|Poly選定側 + Binance同側 − 1| ≤ {prob(params.invertedMirrorTolerance)}；
        Paper 實際成交仍用 Binance Ask，且 Ask ≤ {prob(params.invertedMaxEntryAsk)} 才進場。
      </small>
    </article>,
    target,
  );
}
