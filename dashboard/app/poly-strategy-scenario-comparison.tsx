"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

const STRATEGIES = [
  "R_POLY_LEAD_ENTRY",
  "R_POLY_LEAD_EXIT",
  "R_POLY_GAP_SCALP",
] as const;

type StrategyId = typeof STRATEGIES[number];

type ScenarioStats = {
  trades?: number;
  open?: number;
  closed?: number;
  wins?: number;
  losses?: number;
  winRate?: number | null;
  grossPnlUsdt?: number | null;
};

type ScenarioPortfolio = {
  basis?: string;
  grossPnl?: boolean;
  attemptedEntries?: number;
  eligibleEntries?: number;
  excludedEntryFailures?: number;
  best?: ScenarioStats;
  worst?: ScenarioStats;
  exitEnvelope?: {
    firstLevelDepth?: number;
    upperBoundNoDepth?: number;
    noReliableBid?: number;
  };
};

type ApiPayload = {
  ok?: boolean;
  state?: {
    quoteCanary?: {
      version?: string;
      scenarioPortfolioSummaries?: Partial<Record<StrategyId, ScenarioPortfolio>> & Record<string, ScenarioPortfolio>;
    };
  };
};

type CardHost = {
  strategy: StrategyId;
  card: Element;
};

function finite(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function pct(value: unknown) {
  const number = finite(value);
  return number == null ? "—" : `${(number * 100).toFixed(1)}%`;
}

function money(value: unknown) {
  const number = finite(value);
  if (number == null) return "—";
  return `${number >= 0 ? "+" : "−"}$${Math.abs(number).toFixed(4)}`;
}

function ScenarioRow({ label, stats, kind }: {
  label: string;
  stats: ScenarioStats | undefined;
  kind: "best" | "worst";
}) {
  const pnl = finite(stats?.grossPnlUsdt) ?? 0;
  return <div className={`poly-card-scenario-row ${kind}`} data-poly-scenario={kind}>
    <div className="poly-card-scenario-name">
      <strong>{label}</strong>
      <small>{kind === "best" ? "最佳可見退出／無深度時樂觀上限" : "退出完全賣不掉，全部持有到結算"}</small>
    </div>
    <div>
      <span>交易／持倉</span>
      <strong>{stats?.trades ?? 0} / {stats?.open ?? 0}</strong>
    </div>
    <div>
      <span>勝率</span>
      <strong>{pct(stats?.winRate)}</strong>
    </div>
    <div>
      <span>Gross PnL</span>
      <strong className={pnl >= 0 ? "positive" : "negative"}>{money(stats?.grossPnlUsdt)}</strong>
    </div>
  </div>;
}

export default function PolyStrategyScenarioComparison() {
  const [hosts, setHosts] = useState<CardHost[]>([]);
  const [summaries, setSummaries] = useState<Record<string, ScenarioPortfolio>>({});

  useEffect(() => {
    const locate = () => {
      const section = document.querySelector(
        "#poly-cross-market-panel .poly-section[aria-label='Polymarket lead Paper strategies']",
      );
      if (!section) {
        setHosts([]);
        return;
      }
      const cards = Array.from(
        section.querySelectorAll<HTMLElement>(".m-exit-summary-grid.research-strategy-grid > .m-exit-card"),
      ).slice(0, 3);
      const next = cards.flatMap((card, index) => {
        const strategy = STRATEGIES[index];
        return strategy ? [{ strategy, card }] : [];
      });
      setHosts(previous => {
        if (
          previous.length === next.length
          && previous.every((item, index) => item.strategy === next[index]?.strategy && item.card === next[index]?.card)
        ) return previous;
        return next;
      });
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
        if (!response.ok) return;
        const payload = await response.json() as ApiPayload;
        if (!alive || !payload.ok || !payload.state?.quoteCanary) return;
        setSummaries(payload.state.quoteCanary.scenarioPortfolioSummaries ?? {});
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
      }
    };
    void load();
    const timer = window.setInterval(load, 1000);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, []);

  return <>
    <style>{`
      #poly-cross-market-panel .poly-card-scenario-comparison{margin-top:12px;padding-top:11px;border-top:1px solid rgba(126,145,178,.22)}
      #poly-cross-market-panel .poly-card-scenario-meta{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:7px;flex-wrap:wrap}
      #poly-cross-market-panel .poly-card-scenario-meta span{font:700 9px/1.2 var(--font-mono);letter-spacing:.07em;color:#91a0bb}
      #poly-cross-market-panel .poly-card-scenario-meta small{color:#91a0bb}
      #poly-cross-market-panel .poly-card-scenario-row{display:grid;grid-template-columns:minmax(150px,1.2fr) repeat(3,minmax(90px,1fr));gap:7px;margin-top:7px}
      #poly-cross-market-panel .poly-card-scenario-row>div{padding:8px 9px;border-radius:9px;background:rgba(126,145,178,.07);min-width:0}
      #poly-cross-market-panel .poly-card-scenario-row.best>div{border:1px solid rgba(141,244,192,.12)}
      #poly-cross-market-panel .poly-card-scenario-row.worst>div{border:1px solid rgba(255,180,92,.12)}
      #poly-cross-market-panel .poly-card-scenario-row span,#poly-cross-market-panel .poly-card-scenario-row small{display:block;color:#91a0bb;font-size:10px}
      #poly-cross-market-panel .poly-card-scenario-row>div>strong{display:block;margin-top:3px;font-size:14px}
      #poly-cross-market-panel .poly-card-scenario-name>strong{margin:0!important;font-size:12px!important;color:#dce6ff}
      #poly-cross-market-panel .poly-card-scenario-name small{margin-top:4px}
      @media(max-width:720px){#poly-cross-market-panel .poly-card-scenario-row{grid-template-columns:1fr 1fr}#poly-cross-market-panel .poly-card-scenario-name{grid-column:1/-1}}
    `}</style>
    {hosts.map(host => {
      const summary = summaries[host.strategy];
      if (!summary) return null;
      return createPortal(
        <div className="poly-card-scenario-comparison" data-poly-scenario-strategy={host.strategy}>
          <div className="poly-card-scenario-meta">
            <span>實際進場後的執行情境對比</span>
            <small>Signed BUY 可實作 {summary.eligibleEntries ?? 0}/{summary.attemptedEntries ?? 0} · 排除 {summary.excludedEntryFailures ?? 0}</small>
          </div>
          <ScenarioRow label="最佳狀況" stats={summary.best} kind="best" />
          <ScenarioRow label="最糟狀況" stats={summary.worst} kind="worst" />
        </div>,
        host.card,
        host.strategy,
      );
    })}
  </>;
}
