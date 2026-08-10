"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

const STRATEGIES = [
  "R_POLY_LEAD_ENTRY",
  "R_POLY_LEAD_EXIT",
  "R_POLY_GAP_SCALP",
] as const;

type StrategyId = typeof STRATEGIES[number];

type RollingSummary = {
  rolling10Markets?: number;
  rolling10AveragePnlUsdt?: number | null;
  rolling10AverageReversals?: number | null;
  rolling10Window?: number;
};

type StatePayload = {
  ok?: boolean;
  state?: {
    summaries?: Partial<Record<StrategyId, RollingSummary>>;
    rolling10MarketStats?: {
      flipDefinition?: string;
    };
  };
};

function money(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : "−"}$${Math.abs(value).toFixed(4)}`;
}

function average(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(2);
}

function sameTargets(
  left: Partial<Record<StrategyId, Element>>,
  right: Partial<Record<StrategyId, Element>>,
) {
  return STRATEGIES.every(id => left[id] === right[id]);
}

export default function OracleCrossRollingStats() {
  const [summaries, setSummaries] = useState<Partial<Record<StrategyId, RollingSummary>>>({});
  const [targets, setTargets] = useState<Partial<Record<StrategyId, Element>>>({});

  useEffect(() => {
    const locate = () => {
      const cards = Array.from(
        document.querySelectorAll("#poly-cross-market-panel article.m-exit-card.cyan"),
      );
      const next: Partial<Record<StrategyId, Element>> = {};
      for (const id of STRATEGIES) {
        const card = cards.find(candidate => {
          const label = candidate.querySelector(".eyebrow")?.textContent?.trim();
          return label === id;
        });
        if (card) next[id] = card;
      }
      setTargets(current => sameTargets(current, next) ? current : next);
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
        const payload = await response.json() as StatePayload;
        if (!alive || !response.ok || !payload.ok || !payload.state) return;
        setSummaries(payload.state.summaries ?? {});
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
      }
    };

    void load();
    const timer = window.setInterval(load, 2500);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, []);

  return <>
    <style>{`
      .poly-rolling-10-stats{
        display:grid;
        grid-template-columns:repeat(2,minmax(0,1fr));
        gap:8px;
        margin-top:10px;
      }
      .poly-rolling-10-stats>div{
        padding:9px 10px;
        border-radius:10px;
        background:rgba(126,145,178,.08);
        border:1px solid rgba(126,145,178,.16);
      }
      .poly-rolling-10-stats span,.poly-rolling-10-stats small{
        display:block;
        color:#91a0bb;
      }
      .poly-rolling-10-stats strong{
        display:block;
        margin:3px 0 2px;
        font-size:1.06rem;
      }
      .poly-rolling-10-stats strong.positive{color:#7ee3a1}
      .poly-rolling-10-stats strong.negative{color:#ff8b8b}
    `}</style>
    {STRATEGIES.map(id => {
      const target = targets[id];
      if (!target) return null;
      const summary = summaries[id] ?? {};
      const sample = summary.rolling10Markets ?? 0;
      const pnl = summary.rolling10AveragePnlUsdt;
      const reversals = summary.rolling10AverageReversals;
      return createPortal(
        <div className="poly-rolling-10-stats" data-poly-rolling-stats={id} key={id}>
          <div>
            <span>近 10 局平均收益</span>
            <strong className={(pnl ?? 0) >= 0 ? "positive" : "negative"}>{money(pnl)}</strong>
            <small>完成且可評估市場 {sample}/10</small>
          </div>
          <div>
            <span>平均每場翻轉次數</span>
            <strong>{average(reversals)}</strong>
            <small>CHOP confirmed reversal · 同批市場</small>
          </div>
        </div>,
        target,
      );
    })}
  </>;
}
