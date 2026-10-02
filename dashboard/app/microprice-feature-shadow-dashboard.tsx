"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const API_REFRESH_MS = 15_000;

const STRATEGIES = [
  "R_MICROPRICE_NO_020_025",
  "R_MICROPRICE_UP_ONLY",
  "R_MICROPRICE_DOWN_ONLY",
  "R_MICROPRICE_LOW_010_020_ASK_LE_60",
  "R_MICROPRICE_LOW_010_020_ASK_GT_60",
] as const;

type StrategyId = (typeof STRATEGIES)[number];

type StrategyStats = {
  trades?: number;
  open?: number;
  settled?: number;
  wins?: number;
  losses?: number;
  winRate?: number | null;
  realizedPnl?: number;
  averageEntryPrice?: number | null;
  family?: string;
  cohort?: string;
  title?: string;
  rule?: string;
  parameters?: Record<string, unknown>;
};

type FeatureExperiment = {
  version?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  forwardOnly?: boolean;
  sourceStrategy?: string;
  familyCount?: number;
  cohortCount?: number;
  strategies?: Partial<Record<StrategyId, StrategyStats>>;
};

type Summary = {
  trades?: number;
  open?: number;
  wins?: number;
  losses?: number;
  realized_pnl?: number;
};

type ResearchStrategyState = {
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
  summaries?: Partial<Record<StrategyId, Summary>>;
  researchForward?: {
    strategies?: Partial<Record<StrategyId, ResearchStrategyState>>;
    micropriceFeatureShadows?: FeatureExperiment;
  } | null;
};

type CardDefinition = {
  id: StrategyId;
  title: string;
  familyLabel: string;
  mode: string;
  rule: string;
  tone: "cyan" | "coral";
};

const CARDS: CardDefinition[] = [
  {
    id: "R_MICROPRICE_NO_020_025",
    title: "Microprice 排除 0.20–0.25 死區",
    familyLabel: "FAMILY 1 · DEADZONE EXCLUSION",
    mode: "SOURCE MIRROR · OUTSIDE [0.20, 0.25)",
    rule: "只鏡像原 R_MICROPRICE 的可成交 paper 單，但排除歷史上最穩定虧損的 0.20 ≤ entry < 0.25。其餘價格、方向、stake、fee 與結算完全沿用來源單。",
    tone: "cyan",
  },
  {
    id: "R_MICROPRICE_UP_ONLY",
    title: "Microprice 方向拆帳 · UP",
    familyLabel: "FAMILY 2 · DIRECTION SPLIT",
    mode: "SOURCE MIRROR · UP ONLY",
    rule: "只鏡像 R_MICROPRICE 的 UP 單，用來驗證先前 UP 顯著落後是否會在新的 forward 樣本延續。",
    tone: "coral",
  },
  {
    id: "R_MICROPRICE_DOWN_ONLY",
    title: "Microprice 方向拆帳 · DOWN",
    familyLabel: "FAMILY 2 · DIRECTION SPLIT",
    mode: "SOURCE MIRROR · DOWN ONLY",
    rule: "只鏡像 R_MICROPRICE 的 DOWN 單，與 UP cohort 完全拆帳；不把舊資料中的正 PnL 直接當成永久規律。",
    tone: "cyan",
  },
  {
    id: "R_MICROPRICE_LOW_010_020_ASK_LE_60",
    title: "Microprice 低價肥尾 · Ask ≤60",
    familyLabel: "FAMILY 3 · LOW-PRICE TAIL DEPTH",
    mode: "0.10 ≤ ENTRY < 0.20 · AVAILABLE ASK ≤60",
    rule: "低價肥尾候選組：只鏡像 0.10 ≤ entry < 0.20，且來源單在預留後的可用選定側 Ask 深度 ≤60 shares。",
    tone: "coral",
  },
  {
    id: "R_MICROPRICE_LOW_010_020_ASK_GT_60",
    title: "Microprice 低價肥尾 · Ask >60",
    familyLabel: "FAMILY 3 · LOW-PRICE TAIL DEPTH",
    mode: "0.10 ≤ ENTRY < 0.20 · AVAILABLE ASK >60",
    rule: "低價肥尾控制組：使用相同低價區間，但 Ask 深度 >60；保留它才能確認薄 Ask 的效果不是舊樣本挑選偏誤。",
    tone: "cyan",
  },
];

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function money(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : `$${value.toFixed(digits)}`;
}

function ratio(value: number | null | undefined) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(1)}%`;
}

function decimal(value: number | null | undefined, digits = 3) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : value.toFixed(digits);
}

function compactParameters(parameters: Record<string, unknown>) {
  return Object.entries(parameters)
    .filter(([, value]) => value != null)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" · ");
}

function updateShadowCountLabels() {
  const replacements = new Map<string, string>([
    [
      "FORWARD PAPER · FIVE PRIMARY + SIXTEEN SHADOWS",
      "FORWARD PAPER · FIVE PRIMARY + TWENTY-ONE SHADOWS",
    ],
    [
      "FIVE PRIMARY + SIXTEEN SHADOWS · PAPER",
      "FIVE PRIMARY + TWENTY-ONE SHADOWS · PAPER",
    ],
    ["五組主策略＋十六組獨立 Shadow", "五組主策略＋二十一組獨立 Shadow"],
    ["五組主策略＋十六組 Shadow", "五組主策略＋二十一組 Shadow"],
    ["5 主策略＋16 Shadow", "5 主策略＋21 Shadow"],
  ]);

  document.querySelectorAll<HTMLElement>("span, h2, h3, p, strong").forEach(node => {
    if (node.childElementCount > 0) return;
    const current = node.textContent?.trim() ?? "";
    const replacement = replacements.get(current);
    if (replacement) node.textContent = replacement;
  });
}

function FeatureShadowCard({
  definition,
  payload,
}: {
  definition: CardDefinition;
  payload: DashboardPayload | null;
}) {
  const experiment = payload?.researchForward?.micropriceFeatureShadows;
  const stats = experiment?.strategies?.[definition.id];
  const summary = payload?.summaries?.[definition.id];
  const research = payload?.researchForward?.strategies?.[definition.id];
  const wins = summary?.wins ?? stats?.wins ?? 0;
  const losses = summary?.losses ?? stats?.losses ?? 0;
  const settled = wins + losses;
  const trades = summary?.trades ?? stats?.trades ?? 0;
  const open = summary?.open ?? stats?.open ?? 0;
  const pnl = summary?.realized_pnl ?? stats?.realizedPnl ?? 0;
  const validation = research?.chronologicalValidation;
  const parameters = research?.selectedBacktestParameters
    ?? stats?.parameters
    ?? {};

  return <article
    className={`m-exit-card ${definition.tone}`}
    data-microprice-feature-shadow={definition.id}
  >
    <div className="m-exit-card-head">
      <div>
        <span className="eyebrow">{definition.id} · {definition.familyLabel}</span>
        <h3>{definition.title}</h3>
      </div>
      <div className="m-exit-card-actions">
        <span className="m-exit-id">PAPER ONLY</span>
      </div>
    </div>

    <div className="m-exit-primary-stats">
      <div>
        <span>已實現收益</span>
        <strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong>
      </div>
      <div><span>勝率</span><strong>{settled ? ratio(wins / settled) : "—"}</strong></div>
      <div><span>交易／未結算</span><strong>{trades} / {open}</strong></div>
    </div>

    <div className={`continuous-calibration-state ${settled >= 30 ? "ready" : "warmup"}`}>
      <span>{definition.mode}</span>
      <strong>已結算 {settled} / 30 · 勝 {wins} · 敗 {losses}</strong>
      <small>
        平均進場價 {decimal(stats?.averageEntryPrice)}
        {stats?.cohort ? ` · cohort ${stats.cohort}` : ""}
      </small>
    </div>

    <p>{definition.rule}</p>
    <small>{compactParameters(parameters)}</small>
    <small>
      Forward-only：只在來源 R_MICROPRICE 真正開出 paper 單之後建立鏡像；不回填舊樣本、不共享主策略曝險、不轉送實單。
    </small>
    <small>
      狀態 {validation?.status ?? "COLLECTING"} · 樣本 {validation?.samples ?? trades} · 版本 {experiment?.version ?? "等待後端"}
    </small>
  </article>;
}

export default function MicropriceFeatureShadowDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [payload, setPayload] = useState<DashboardPayload | null>(null);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;

    const locate = () => {
      const next = document.querySelector<HTMLElement>(
        ".research-forward-panel .research-strategy-grid",
      );
      setTarget(current => current === next ? current : next);
      updateShadowCountLabels();
    };

    const observer = new MutationObserver(locate);
    observer.observe(document.body, { childList: true, subtree: true });
    locate();

    const load = async () => {
      if (document.visibilityState !== "visible") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(apiUrl("/api/state"), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`dashboard request failed: ${response.status}`);
        }
        const next = await response.json() as DashboardPayload;
        if (active) setPayload(next);
      } catch (error) {
        if (
          active
          && !(error instanceof DOMException && error.name === "AbortError")
        ) {
          setPayload(current => current);
        }
      }
    };

    void load();
    const timer = window.setInterval(load, API_REFRESH_MS);
    const visibility = () => {
      locate();
      if (document.visibilityState === "visible") void load();
      else controller?.abort();
    };
    document.addEventListener("visibilitychange", visibility);

    return () => {
      active = false;
      controller?.abort();
      observer.disconnect();
      document.removeEventListener("visibilitychange", visibility);
      window.clearInterval(timer);
    };
  }, []);

  const cards = useMemo(
    () => CARDS.map(definition => (
      <FeatureShadowCard
        key={definition.id}
        definition={definition}
        payload={payload}
      />
    )),
    [payload],
  );

  return target ? createPortal(cards, target) : null;
}
