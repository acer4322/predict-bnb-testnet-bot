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

type StatHosts = {
  strategy: StrategyId;
  trades: Element;
  winRate: Element;
  pnl: Element;
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

function trades(stats: ScenarioStats | undefined) {
  return `${stats?.trades ?? 0} / ${stats?.open ?? 0}`;
}

function ComparisonLines({ best, worst, kind }: {
  best: ScenarioStats | undefined;
  worst: ScenarioStats | undefined;
  kind: "trades" | "winRate" | "pnl";
}) {
  const bestValue = kind === "trades"
    ? trades(best)
    : kind === "winRate"
      ? pct(best?.winRate)
      : money(best?.grossPnlUsdt);
  const worstValue = kind === "trades"
    ? trades(worst)
    : kind === "winRate"
      ? pct(worst?.winRate)
      : money(worst?.grossPnlUsdt);
  const bestPnl = finite(best?.grossPnlUsdt) ?? 0;
  const worstPnl = finite(worst?.grossPnlUsdt) ?? 0;

  return <div className="poly-native-scenario-lines" data-scenario-kind={kind}>
    <div className="best">
      <span>最佳</span>
      <strong className={kind === "pnl" ? (bestPnl >= 0 ? "positive" : "negative") : ""}>{bestValue}</strong>
    </div>
    <div className="worst">
      <span>最糟</span>
      <strong className={kind === "pnl" ? (worstPnl >= 0 ? "positive" : "negative") : ""}>{worstValue}</strong>
    </div>
  </div>;
}

export default function PolyStrategyScenarioComparison() {
  const [hosts, setHosts] = useState<StatHosts[]>([]);
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
      const next: StatHosts[] = [];
      cards.forEach((card, index) => {
        const strategy = STRATEGIES[index];
        const cells = Array.from(card.querySelectorAll(":scope > .m-exit-primary-stats > div"));
        if (!strategy || cells.length < 3) return;
        next.push({
          strategy,
          trades: cells[0],
          winRate: cells[1],
          pnl: cells[2],
        });
      });
      setHosts(previous => {
        if (
          previous.length === next.length
          && previous.every((item, index) => (
            item.strategy === next[index]?.strategy
            && item.trades === next[index]?.trades
            && item.winRate === next[index]?.winRate
            && item.pnl === next[index]?.pnl
          ))
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
      #poly-cross-market-panel .poly-native-scenario-lines{margin-top:8px;padding-top:7px;border-top:1px solid rgba(126,145,178,.18);display:grid;gap:5px}
      #poly-cross-market-panel .poly-native-scenario-lines>div{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:4px 6px;border-radius:7px}
      #poly-cross-market-panel .poly-native-scenario-lines .best{background:rgba(141,244,192,.065);border:1px solid rgba(141,244,192,.11)}
      #poly-cross-market-panel .poly-native-scenario-lines .worst{background:rgba(255,180,92,.055);border:1px solid rgba(255,180,92,.11)}
      #poly-cross-market-panel .poly-native-scenario-lines span{font:700 9px/1 var(--font-mono);letter-spacing:.06em;color:#91a0bb}
      #poly-cross-market-panel .poly-native-scenario-lines strong{margin:0;font-size:12px;white-space:nowrap}
    `}</style>
    {hosts.flatMap(host => {
      const summary = summaries[host.strategy];
      if (!summary) return [];
      return [
        createPortal(
          <ComparisonLines best={summary.best} worst={summary.worst} kind="trades" />,
          host.trades,
          `${host.strategy}-trades`,
        ),
        createPortal(
          <ComparisonLines best={summary.best} worst={summary.worst} kind="winRate" />,
          host.winRate,
          `${host.strategy}-winrate`,
        ),
        createPortal(
          <ComparisonLines best={summary.best} worst={summary.worst} kind="pnl" />,
          host.pnl,
          `${host.strategy}-pnl`,
        ),
      ];
    })}
  </>;
}
