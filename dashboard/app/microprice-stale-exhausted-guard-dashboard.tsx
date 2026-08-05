"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useSharedDashboardState } from "./shared-dashboard-state";

const STRATEGY = "R_MICROPRICE_CONFIRM_STALE_EXHAUSTED_GUARD";
const TARGET_SELECTOR = ".research-forward-panel .research-strategy-grid";

type Summary = {
  trades?: number;
  open?: number;
  wins?: number;
  losses?: number;
  realized_pnl?: number;
};

type StrategyStats = {
  trades?: number;
  open?: number;
  settled?: number;
  wins?: number;
  losses?: number;
  winRate?: number | null;
  realizedPnl?: number;
  averageEntryPrice?: number | null;
  mode?: string;
  parameters?: Record<string, unknown>;
};

type Experiment = {
  version?: string;
  sourceMarkets?: number;
  blocked?: number;
  blockReasons?: Record<string, number>;
  strategies?: Record<string, StrategyStats>;
  runtime?: {
    opened?: number;
    blocked?: number;
    missingDiagnostics?: number;
    lastDecision?: Record<string, unknown> | null;
    rules?: Record<string, Record<string, unknown>>;
  };
};

type ResearchStrategy = {
  selectedBacktestParameters?: Record<string, unknown>;
  chronologicalValidation?: {
    status?: string;
    samples?: number;
    settled?: number;
    wins?: number;
    losses?: number;
    realizedPnl?: number;
    minimum?: number;
  } | null;
};

type DashboardPayload = {
  summaries?: Record<string, Summary>;
  researchForward?: {
    strategies?: Record<string, ResearchStrategy>;
    micropriceConfirmStaleExhaustedGuard?: Experiment;
  } | null;
};

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `$${value.toFixed(2)}`;
}

function ratio(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}

function decimal(value: number | null | undefined, digits = 3) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function compact(parameters: Record<string, unknown>) {
  return Object.entries(parameters)
    .filter(([, value]) => value != null)
    .slice(0, 12)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" · ");
}

function blockReasonText(reasons: Record<string, number> | undefined) {
  if (!reasons || Object.keys(reasons).length === 0) return "尚未阻擋來源市場";
  return Object.entries(reasons)
    .map(([reason, count]) => `${reason} ${count}`)
    .join(" · ");
}

function decisionText(value: Record<string, unknown> | null | undefined) {
  if (!value) return "等待下一個 R_MICROPRICE_CONFIRM 訊號";
  const status = value.status == null ? "WAITING" : String(value.status);
  const reason = value.reason == null ? "" : ` · ${String(value.reason)}`;
  const market = value.marketId == null ? "" : ` · #${String(value.marketId)}`;
  return `${status}${reason}${market}`;
}

function syncShadowCountLabels() {
  const replacements = new Map<string, string>([
    [
      "FORWARD PAPER · FIVE PRIMARY + TWENTY-THREE SHADOWS",
      "FORWARD PAPER · FIVE PRIMARY + TWENTY-FOUR SHADOWS",
    ],
    [
      "FIVE PRIMARY + TWENTY-THREE SHADOWS · PAPER",
      "FIVE PRIMARY + TWENTY-FOUR SHADOWS · PAPER",
    ],
    ["五組主策略＋二十三組獨立 Shadow", "五組主策略＋二十四組獨立 Shadow"],
    ["五組主策略＋二十三組 Shadow", "五組主策略＋二十四組 Shadow"],
    ["5 主策略＋23 Shadow", "5 主策略＋24 Shadow"],
  ]);
  document.querySelectorAll<HTMLElement>("span, h2, h3, p, strong").forEach(node => {
    if (node.childElementCount > 0) return;
    const current = node.textContent?.trim() ?? "";
    const replacement = replacements.get(current);
    if (replacement) node.textContent = replacement;
  });
}

function GuardCard({ payload }: { payload: DashboardPayload | null }) {
  const experiment = payload?.researchForward?.micropriceConfirmStaleExhaustedGuard;
  const stats = experiment?.strategies?.[STRATEGY];
  const summary = payload?.summaries?.[STRATEGY];
  const research = payload?.researchForward?.strategies?.[STRATEGY];
  const wins = summary?.wins ?? stats?.wins ?? 0;
  const losses = summary?.losses ?? stats?.losses ?? 0;
  const settled = wins + losses;
  const trades = summary?.trades ?? stats?.trades ?? 0;
  const open = summary?.open ?? stats?.open ?? 0;
  const pnl = summary?.realized_pnl ?? stats?.realizedPnl ?? 0;
  const parameters = research?.selectedBacktestParameters
    ?? stats?.parameters
    ?? experiment?.runtime?.rules?.[STRATEGY]
    ?? {};
  const validation = research?.chronologicalValidation;

  return <article className="m-exit-card cyan" data-research-enhancement={STRATEGY}>
    <div className="m-exit-card-head">
      <div>
        <span className="eyebrow">{STRATEGY} · STALE/DECAY GUARD</span>
        <h3>Microprice Confirm V2 · 舊簿／衰退防護</h3>
      </div>
      <div className="m-exit-card-actions"><span className="m-exit-id">PAPER ONLY</span></div>
    </div>

    <div className="m-exit-primary-stats">
      <div><span>已實現收益</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong></div>
      <div><span>勝率</span><strong>{settled ? ratio(wins / settled) : "—"}</strong></div>
      <div><span>交易／未結算</span><strong>{trades} / {open}</strong></div>
    </div>

    <div className={`continuous-calibration-state ${settled >= 30 ? "ready" : "warmup"}`}>
      <span>{stats?.mode ?? "V2 CONFIRM + STALE/DECAY GUARD"}</span>
      <strong>已結束 {settled} / 30 · 勝 {wins} · 敗 {losses}</strong>
      <small>平均進場價 {decimal(stats?.averageEntryPrice)}</small>
    </div>

    <p>
      完整沿用 R_MICROPRICE_CONFIRM V2；只在 entry ≤0.55 時檢查有效資料年齡。
      當 book_age_ms＋signal_to_open_ms ≥450ms，且訊號相較第一個確認事件衰減，
      或訊號到開單延遲 ≥60ms 時阻擋。
    </p>
    <small>
      這是動態資料品質防護，不等同方向價格死區；高於 0.55 的來源單不受此條件影響。
      低價單缺少必要診斷時採 fail-closed。只建立獨立 paper 帳本，不加入正式實單可選名單。
    </small>
    <small>{compact(parameters)}</small>
    <small>
      Forward-only · 狀態 {validation?.status ?? "COLLECTING"} · 樣本 {validation?.samples ?? trades}。
      已評估來源市場 {experiment?.sourceMarkets ?? 0} · 已阻擋 {experiment?.blocked ?? 0}。
    </small>
    <small>{blockReasonText(experiment?.blockReasons)}</small>
    <small>Runtime：{decisionText(experiment?.runtime?.lastDecision)}</small>
  </article>;
}

export default function MicropriceStaleExhaustedGuardDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const { payload } = useSharedDashboardState<DashboardPayload>();

  useEffect(() => {
    const locate = () => {
      const next = document.querySelector<HTMLElement>(TARGET_SELECTOR);
      setTarget(current => current === next ? current : next);
      syncShadowCountLabels();
    };
    const onInteraction = () => window.requestAnimationFrame(locate);
    locate();
    const timer = window.setInterval(locate, 500);
    document.addEventListener("click", onInteraction, true);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("click", onInteraction, true);
    };
  }, []);

  const card = useMemo(() => <GuardCard payload={payload} />, [payload]);
  return target ? createPortal(card, target) : null;
}
