"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const API_REFRESH_MS = 15_000;

const STRATEGIES = [
  "R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD",
  "R_MICROPRICE_CONFIRM_EXIT_098",
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
  averageExitPrice?: number | null;
  targetFilled?: number;
  title?: string;
  mode?: string;
  rule?: string;
  parameters?: Record<string, unknown>;
};

type Experiment = {
  version?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  forwardOnly?: boolean;
  sourceStrategy?: string;
  sourceMarkets?: number;
  priceSideGuardBlocked?: number;
  priceSideGuardBlockReasons?: Record<string, number>;
  exit098TargetEnabled?: number;
  strategies?: Partial<Record<StrategyId, StrategyStats>>;
  runtime?: {
    mirroredSourceMarkets?: number;
    priceSideGuardOpened?: number;
    priceSideGuardBlocked?: number;
    exit098Opened?: number;
    targetFills?: number;
    targetBlockedDepth?: number;
    targetBlockedBook?: number;
    lastDecision?: Record<string, unknown> | null;
    rules?: Partial<Record<StrategyId, Record<string, unknown>>>;
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
  summaries?: Partial<Record<StrategyId, Summary>>;
  researchForward?: {
    strategies?: Partial<Record<StrategyId, ResearchStrategy>>;
    micropriceConfirmOptimizationShadows?: Experiment;
  } | null;
};

type CardDefinition = {
  id: StrategyId;
  title: string;
  kicker: string;
  rule: string;
  note: string;
  tone: "cyan" | "coral";
  badge: string;
};

const CARDS: CardDefinition[] = [
  {
    id: "R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD",
    title: "Microprice Confirm V2 · 方向價格防護",
    kicker: "V2 CONFIRM + PRICE/SIDE GUARD",
    rule: "完整沿用 R_MICROPRICE_CONFIRM V2 的雙事件確認，只額外阻擋 UP entry <0.40，以及 DOWN 0.50 ≤ entry <0.60；其餘訊號、stake、fee 與官方結算完全鏡像。",
    note: "用來 forward 驗證先前 18 筆敗單分析中的方向 × 價格死區，不回填舊交易。",
    tone: "cyan",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_CONFIRM_EXIT_098",
    title: "Microprice Confirm V2 · 0.98 提前退出",
    kicker: "V2 CONFIRM + SELL TARGET 0.98",
    rule: "進場完全沿用原 V2；當選定側實際 Bid ≥0.98 且可見 Bid 深度足以賣完整持倉時，以 0.98 限價平倉。未成交就繼續持有到官方結算。",
    note: "紙上帳本使用直接雙 token REST book；實單只有在策略選單明確選取時才送單，0.98 退出使用獨立 SELL LIMIT 與完整持倉檢查。",
    tone: "coral",
    badge: "PAPER + LIVE SELECTABLE",
  },
];

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : `$${value.toFixed(2)}`;
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

function compact(parameters: Record<string, unknown>) {
  return Object.entries(parameters)
    .filter(([, value]) => value != null)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" · ");
}

function decisionText(value: Record<string, unknown> | null | undefined) {
  if (!value) return "等待下一個 R_MICROPRICE_CONFIRM 訊號";
  const status = value.status == null ? "WAITING" : String(value.status);
  const reason = value.reason == null ? "" : ` · ${String(value.reason)}`;
  return `${status}${reason}`;
}

function updateShadowCountLabels() {
  const replacements = new Map<string, string>([
    [
      "FORWARD PAPER · FIVE PRIMARY + TWENTY-ONE SHADOWS",
      "FORWARD PAPER · FIVE PRIMARY + TWENTY-THREE SHADOWS",
    ],
    [
      "FIVE PRIMARY + TWENTY-ONE SHADOWS · PAPER",
      "FIVE PRIMARY + TWENTY-THREE SHADOWS · PAPER",
    ],
    ["五組主策略＋二十一組獨立 Shadow", "五組主策略＋二十三組獨立 Shadow"],
    ["五組主策略＋二十一組 Shadow", "五組主策略＋二十三組 Shadow"],
    ["5 主策略＋21 Shadow", "5 主策略＋23 Shadow"],
  ]);

  document.querySelectorAll<HTMLElement>("span, h2, h3, p, strong").forEach(node => {
    if (node.childElementCount > 0) return;
    const current = node.textContent?.trim() ?? "";
    const replacement = replacements.get(current);
    if (replacement) node.textContent = replacement;
  });
}

function OptimizationCard({
  definition,
  payload,
}: {
  definition: CardDefinition;
  payload: DashboardPayload | null;
}) {
  const experiment = payload?.researchForward?.micropriceConfirmOptimizationShadows;
  const stats = experiment?.strategies?.[definition.id];
  const summary = payload?.summaries?.[definition.id];
  const research = payload?.researchForward?.strategies?.[definition.id];
  const wins = summary?.wins ?? stats?.wins ?? 0;
  const losses = summary?.losses ?? stats?.losses ?? 0;
  const settled = wins + losses;
  const trades = summary?.trades ?? stats?.trades ?? 0;
  const open = summary?.open ?? stats?.open ?? 0;
  const pnl = summary?.realized_pnl ?? stats?.realizedPnl ?? 0;
  const parameters = research?.selectedBacktestParameters
    ?? stats?.parameters
    ?? experiment?.runtime?.rules?.[definition.id]
    ?? {};
  const validation = research?.chronologicalValidation;
  const isExit = definition.id === "R_MICROPRICE_CONFIRM_EXIT_098";

  return <article
    className={`m-exit-card ${definition.tone}`}
    data-microprice-confirm-optimization={definition.id}
  >
    <div className="m-exit-card-head">
      <div>
        <span className="eyebrow">{definition.id} · {definition.kicker}</span>
        <h3>{definition.title}</h3>
      </div>
      <div className="m-exit-card-actions">
        <span className="m-exit-id">{definition.badge}</span>
      </div>
    </div>

    <div className="m-exit-primary-stats">
      <div>
        <span>已實現收益</span>
        <strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong>
      </div>
      <div><span>正收益率</span><strong>{settled ? ratio(wins / settled) : "—"}</strong></div>
      <div><span>交易／未結算</span><strong>{trades} / {open}</strong></div>
    </div>

    <div className={`continuous-calibration-state ${settled >= 30 ? "ready" : "warmup"}`}>
      <span>{definition.kicker}</span>
      <strong>已結束 {settled} / 30 · 正收益 {wins} · 非正收益 {losses}</strong>
      <small>
        平均進場價 {decimal(stats?.averageEntryPrice)}
        {isExit ? ` · 0.98 提前成交 ${stats?.targetFilled ?? 0}` : ""}
        {isExit && stats?.averageExitPrice != null
          ? ` · 平均退出 ${decimal(stats.averageExitPrice)}`
          : ""}
      </small>
    </div>

    <p>{definition.rule}</p>
    <small>{definition.note}</small>
    <small>{compact(parameters)}</small>
    <small>
      Forward-only · 每筆沿用來源 5 USDT paper stake · 不回填舊樣本 · 狀態 {validation?.status ?? "COLLECTING"}。
    </small>
    <small>
      Runtime：{decisionText(experiment?.runtime?.lastDecision)}
      {isExit ? ` · paper target fills ${experiment?.runtime?.targetFills ?? 0}` : ` · blocked ${experiment?.priceSideGuardBlocked ?? 0}`}
    </small>
  </article>;
}

export default function MicropriceConfirmOptimizationDashboard() {
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
      <OptimizationCard
        key={definition.id}
        definition={definition}
        payload={payload}
      />
    )),
    [payload],
  );

  return target ? createPortal(cards, target) : null;
}
