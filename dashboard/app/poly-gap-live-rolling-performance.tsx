"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

type RollingPerformance = {
  windowMarkets?: number;
  markets?: number;
  wins?: number;
  losses?: number;
  neutral?: number;
  winRate?: number | null;
  totalPnlUsdt?: number | null;
  averagePnlUsdt?: number | null;
  reversalLossMarkets?: number;
  marketIds?: number[];
  reversalLossDefinition?: string;
};

type Payload = {
  ok?: boolean;
  state?: {
    version?: string;
    rolling10Performance?: RollingPerformance;
  };
};

function money(value: number | null | undefined, digits = 4) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : "−"}$${Math.abs(value).toFixed(digits)}`;
}

function pct(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export default function PolyGapLiveRollingPerformance() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [stats, setStats] = useState<RollingPerformance | null>(null);

  useEffect(() => {
    const locate = () => {
      const node = document.querySelector<HTMLElement>(".poly-gap-live-control");
      setTarget(current => current === node ? current : node);
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
        const response = await fetch("/api/poly-gap-live", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await response.json() as Payload;
        if (!alive || !response.ok || !payload.ok || !payload.state) return;
        setStats(payload.state.rolling10Performance ?? null);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
      }
    };
    void load();
    const timer = window.setInterval(load, 1500);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, []);

  if (!target) return null;

  const sample = stats?.markets ?? 0;
  const wins = stats?.wins ?? 0;
  const losses = stats?.losses ?? 0;
  const neutral = stats?.neutral ?? 0;
  const avgPnl = stats?.averagePnlUsdt;
  const totalPnl = stats?.totalPnlUsdt;
  const reversalLosses = stats?.reversalLossMarkets ?? 0;

  return createPortal(
    <section className="poly-gap-live-rolling" aria-label="R_POLY_GAP_SCALP 近10局實單統計">
      <style>{`
        .poly-gap-live-rolling{padding:12px;border-radius:12px;background:rgba(94,213,231,.055);border:1px solid rgba(94,213,231,.18)}
        .poly-gap-live-rolling-head{display:flex;justify-content:space-between;gap:10px;align-items:end;flex-wrap:wrap;margin-bottom:9px}
        .poly-gap-live-rolling-head h4{margin:2px 0 0}.poly-gap-live-rolling-head small{color:#91a0bb}
        .poly-gap-live-rolling-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
        .poly-gap-live-rolling-grid>div{padding:10px;border-radius:10px;background:rgba(126,145,178,.07)}
        .poly-gap-live-rolling-grid span,.poly-gap-live-rolling-grid small{display:block;color:#91a0bb}
        .poly-gap-live-rolling-grid strong{display:block;margin:4px 0 2px;font-size:1.12rem}
        .poly-gap-live-rolling .positive{color:#7ee3a1}.poly-gap-live-rolling .negative{color:#ff8b8b}.poly-gap-live-rolling .warning{color:#ffbd87}
        @media(max-width:720px){.poly-gap-live-rolling-grid{grid-template-columns:1fr}}
      `}</style>
      <div className="poly-gap-live-rolling-head">
        <div><span className="eyebrow">ROLLING LIVE PERFORMANCE</span><h4>近 10 局實單表現</h4></div>
        <small>以 5 分鐘市場為一局 · 同市場多 round 先合併</small>
      </div>
      <div className="poly-gap-live-rolling-grid">
        <div>
          <span>近 10 局勝率</span>
          <strong>{pct(stats?.winRate)}</strong>
          <small>勝 {wins} · 敗 {losses} · 平 {neutral} · 樣本 {sample}/10</small>
        </div>
        <div>
          <span>近 10 局平均損益</span>
          <strong className={(avgPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(avgPnl)}</strong>
          <small>同批市場合計 {money(totalPnl)}</small>
        </div>
        <div>
          <span>市場翻轉致虧局數</span>
          <strong className={reversalLosses > 0 ? "warning" : "positive"}>{reversalLosses} / {sample || 10}</strong>
          <small>實際翻轉 SELL 成交後為負 PnL；同市場只算 1 局</small>
        </div>
      </div>
    </section>,
    target,
  );
}
