"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const API_REFRESH_MS = 15_000;
const LOCATE_MS = 1_000;

const STRATEGIES = [
  "R_CALIBRATED_VALUE_CONFIRM_RANGE12",
  "R_CALIBRATED_VALUE_LOWTAIL_CONFIRM",
] as const;

type StrategyId = (typeof STRATEGIES)[number];

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
};

type ResearchStrategy = {
  selectedBacktestParameters?: Record<string, unknown>;
  chronologicalValidation?: {
    status?: string;
    samples?: number;
    settled?: number;
    wins?: number;
    losses?: number;
    minimum?: number;
  } | null;
};

type FilterRuntime = {
  version?: string;
  range12Opened?: number;
  lowtailOpened?: number;
  lastDecisions?: Partial<Record<StrategyId, Record<string, unknown> | null>>;
  rules?: Partial<Record<StrategyId, Record<string, unknown>>>;
};

type Experiment = {
  filterExtensionVersion?: string;
  range12Markets?: number;
  lowtailMarkets?: number;
  strategies?: Partial<Record<StrategyId, StrategyStats>>;
  filterRuntime?: FilterRuntime;
};

type DashboardPayload = {
  summaries?: Partial<Record<StrategyId, Summary>>;
  researchForward?: {
    calibratedValueConfirmationExperiment?: Experiment;
    strategies?: Partial<Record<StrategyId, ResearchStrategy>>;
  } | null;
};

type CardDefinition = {
  id: StrategyId;
  title: string;
  kicker: string;
  rule: string;
  note: string;
  tone: "green" | "amber";
};

const CARDS: CardDefinition[] = [
  {
    id: "R_CALIBRATED_VALUE_CONFIRM_RANGE12",
    title: "Calibrated Value 確認 · Range 1–2",
    kicker: "CONFIRM V2 + RANGE SCORE 1–2",
    rule: "完整沿用 Confirm V2：初始 edge ≥0.015、至少 2 個事件與 150ms、midpoint 同向 ≥0.005、最終 edge ≥0.010、保留 ≥65%；另外要求當輪 Range score 為 1 或 2，且有效穿越不超過 2 次。",
    note: "Observer 欄位缺失或 market ID 不一致時 fail closed。這組用來驗證一般獲利樣本中最穩定的盤況組合。",
    tone: "green",
  },
  {
    id: "R_CALIBRATED_VALUE_LOWTAIL_CONFIRM",
    title: "Calibrated Value 低價肥尾確認",
    kicker: "STRICT LOW-PRICE TAIL",
    rule: "確認後實際 entry 必須介於 0.10～0.221；至少 3 個不同事件、持續 ≥250ms，book age ≤300ms、skew ≤100ms、最終 edge ≥0.010、保留初始 edge ≥75%，且 trend veto 必須為 false。",
    note: "這組不是追求高勝率，而是嘗試保留少數大賺尾端，同時剔除品質較差的低價訊號。標準 Confirm V2 可在第 2 個事件開倉，本組仍會獨立等待第 3 個事件。",
    tone: "amber",
  },
];

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

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
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join("–") : String(value)}`)
    .join(" · ");
}

function decisionText(value: Record<string, unknown> | null | undefined) {
  if (!value) return "等待符合條件的新事件";
  const status = value.status == null ? "WAITING" : String(value.status);
  const reason = value.reason == null ? "" : ` · ${String(value.reason)}`;
  return `${status}${reason}`;
}

function FilterCard({ definition, payload }: { definition: CardDefinition; payload: DashboardPayload | null }) {
  const experiment = payload?.researchForward?.calibratedValueConfirmationExperiment;
  const stats = experiment?.strategies?.[definition.id];
  const summary = payload?.summaries?.[definition.id];
  const research = payload?.researchForward?.strategies?.[definition.id];
  const wins = summary?.wins ?? stats?.wins ?? 0;
  const losses = summary?.losses ?? stats?.losses ?? 0;
  const settled = wins + losses;
  const trades = summary?.trades ?? stats?.trades ?? 0;
  const open = summary?.open ?? stats?.open ?? 0;
  const pnl = summary?.realized_pnl ?? stats?.realizedPnl ?? 0;
  const rules = research?.selectedBacktestParameters
    ?? experiment?.filterRuntime?.rules?.[definition.id]
    ?? {};
  const decision = experiment?.filterRuntime?.lastDecisions?.[definition.id];
  const validation = research?.chronologicalValidation;

  return <article className={`m-exit-card ${definition.tone}`} data-calibrated-filter={definition.id}>
    <div className="m-exit-card-head">
      <div><span className="eyebrow">{definition.id} · {definition.kicker}</span><h3>{definition.title}</h3></div>
      <div className="m-exit-card-actions"><span className="m-exit-id">PAPER ONLY</span></div>
    </div>
    <div className="m-exit-primary-stats">
      <div><span>已實現收益</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong></div>
      <div><span>勝率</span><strong>{settled ? ratio(wins / settled) : "—"}</strong></div>
      <div><span>交易／未結算</span><strong>{trades} / {open}</strong></div>
    </div>
    <div className={`continuous-calibration-state ${settled >= 30 ? "ready" : "warmup"}`}>
      <span>{stats?.mode ?? definition.kicker}</span>
      <strong>已結算 {settled} / 30 · 勝 {wins} · 敗 {losses}</strong>
      <small>平均進場價 {decimal(stats?.averageEntryPrice)}</small>
    </div>
    <p>{definition.rule}</p>
    <small>{definition.note}</small>
    <small>{compact(rules)}</small>
    <small>Runtime：{decisionText(decision)}</small>
    <small>Forward-only · 每筆 5 USDT · 不回填舊交易 · 不轉送實單 · {validation?.status ?? "COLLECTING"}。</small>
  </article>;
}

function updateLabels() {
  const tab = document.getElementById("calibrated-value-confirmation-tab");
  const tabCopy = tab?.querySelector<HTMLElement>("span");
  const expectedTab = "即時控制＋順勢／反向 V2＋Range12＋Lowtail";
  if (tabCopy && tabCopy.textContent !== expectedTab) tabCopy.textContent = expectedTab;

  const lab = document.querySelector<HTMLElement>(".calibrated-value-confirmation-lab");
  const heading = lab?.querySelector<HTMLElement>(".strategy-family-intro h3");
  const expectedHeading = "Calibrated Value 五組前向確認測試";
  if (heading && heading.textContent !== expectedHeading) heading.textContent = expectedHeading;
}

export default function CalibratedValueConfirmationFilterDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [payload, setPayload] = useState<DashboardPayload | null>(null);

  useEffect(() => {
    let mounted = true;
    let controller: AbortController | null = null;

    const locate = () => {
      const next = document.querySelector<HTMLElement>(
        ".calibrated-value-confirmation-lab .research-strategy-grid",
      );
      setTarget(current => current === next ? current : next);
      updateLabels();
    };

    const load = async () => {
      if (document.visibilityState !== "visible") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(apiUrl("/api/state"), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`dashboard request failed: ${response.status}`);
        const next = await response.json() as DashboardPayload;
        if (mounted) setPayload(next);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setPayload(current => current);
        }
      }
    };

    locate();
    void load();
    const locateTimer = window.setInterval(locate, LOCATE_MS);
    const loadTimer = window.setInterval(load, API_REFRESH_MS);
    const visibility = () => {
      locate();
      if (document.visibilityState === "visible") void load();
      else controller?.abort();
    };
    document.addEventListener("visibilitychange", visibility);

    return () => {
      mounted = false;
      controller?.abort();
      window.clearInterval(locateTimer);
      window.clearInterval(loadTimer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);

  const cards = useMemo(
    () => CARDS.map(definition => <FilterCard key={definition.id} definition={definition} payload={payload} />),
    [payload],
  );
  return target ? createPortal(cards, target) : null;
}
