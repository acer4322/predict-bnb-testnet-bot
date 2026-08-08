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

function ScenarioRow({ label, stats, tone }: { label: string; stats: ScenarioStats; tone: "best" | "worst" }) {
  const pnl = finite(stats.grossPnlUsdt) ?? 0;
  return <div className={`poly-scenario-row ${tone}`}>
    <div className="poly-scenario-label">
      <span>{label}</span>
      <small>{tone === "best" ? "可見最佳退出／樂觀上限" : "退出完全賣不掉，全持有到結算"}</small>
    </div>
    <div><span>交易／持倉</span><strong>{stats.trades ?? 0} / {stats.open ?? 0}</strong></div>
    <div><span>勝率</span><strong>{pct(stats.winRate)}</strong></div>
    <div><span>Gross PnL</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(stats.grossPnlUsdt)}</strong></div>
  </div>;
}

export default function PolyStrategyScenarioComparison() {
  const [hosts, setHosts] = useState<Element[]>([]);
  const [summaries, setSummaries] = useState<Record<string, ScenarioPortfolio>>({});
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    const locate = () => {
      const section = document.querySelector(
        "#poly-cross-market-panel .poly-section[aria-label='Polymarket lead Paper strategies']",
      );
      const cards = section
        ? Array.from(section.querySelectorAll(".m-exit-summary-grid .m-exit-card")).slice(0, 3)
        : [];
      setHosts(cards);
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
        setVersion(payload.state.quoteCanary.version ?? null);
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
    {hosts.map((host, index) => {
      const strategy = STRATEGIES[index];
      if (!strategy) return null;
      const summary = summaries[strategy];
      if (!summary) return null;
      const eligible = summary.eligibleEntries ?? 0;
      const attempted = summary.attemptedEntries ?? 0;
      const excluded = summary.excludedEntryFailures ?? 0;
      const envelope = summary.exitEnvelope ?? {};
      return createPortal(
        <div className="poly-scenario-comparison" data-poly-scenario-strategy={strategy}>
          <style>{`
            .poly-scenario-comparison{margin-top:13px;padding-top:12px;border-top:1px solid rgba(126,145,178,.20)}
            .poly-scenario-comparison .poly-scenario-title{display:flex;justify-content:space-between;gap:10px;align-items:flex-end;margin-bottom:8px;flex-wrap:wrap}
            .poly-scenario-comparison .poly-scenario-title>span{font:700 10px/1.2 var(--font-mono);letter-spacing:.08em;color:#91a0bb}
            .poly-scenario-comparison .poly-scenario-title>small{color:#91a0bb}
            .poly-scenario-row{display:grid;grid-template-columns:minmax(150px,1.2fr) repeat(3,minmax(90px,1fr));gap:7px;margin-top:7px}
            .poly-scenario-row>div{padding:8px 9px;border-radius:9px;background:rgba(126,145,178,.07);min-width:0}
            .poly-scenario-row.best>div{border:1px solid rgba(141,244,192,.12)}
            .poly-scenario-row.worst>div{border:1px solid rgba(255,180,92,.12)}
            .poly-scenario-row span,.poly-scenario-row small{display:block;color:#91a0bb;font-size:10px}
            .poly-scenario-row strong{display:block;margin-top:3px;font-size:14px}
            .poly-scenario-row .poly-scenario-label>span{font-weight:800;color:#dce6ff;font-size:11px}
            @media(max-width:720px){.poly-scenario-row{grid-template-columns:1fr 1fr}.poly-scenario-row .poly-scenario-label{grid-column:1/-1}}
          `}</style>
          <div className="poly-scenario-title">
            <span>EXECUTION SCENARIO COMPARISON · GROSS</span>
            <small>可實作進場 {eligible}/{attempted} · 排除 {excluded} · {version ?? "V5"}</small>
          </div>
          <ScenarioRow label="最佳狀況" stats={summary.best ?? {}} tone="best" />
          <ScenarioRow label="最糟狀況" stats={summary.worst ?? {}} tone="worst" />
          <small style={{ display: "block", marginTop: 7, color: "#91a0bb" }}>
            退出資料：第一檔深度 {envelope.firstLevelDepth ?? 0} · 無深度樂觀上限 {envelope.upperBoundNoDepth ?? 0} · 無可靠 Bid {envelope.noReliableBid ?? 0}。Gross PnL 不扣 fee，與上方原 Paper 結果同口徑。
          </small>
        </div>,
        host,
        strategy,
      );
    })}
  </>;
}
