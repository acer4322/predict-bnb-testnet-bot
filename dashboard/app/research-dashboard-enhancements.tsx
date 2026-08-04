"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useSharedDashboardState } from "./shared-dashboard-state";

const TARGET_SELECTOR = ".research-forward-panel .research-strategy-grid";
const LOCATE_MS = 1_000;

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
  mode?: string;
  cohort?: string;
  parameters?: Record<string, unknown>;
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

type DashboardPayload = {
  summaries?: Record<string, Summary>;
  researchForward?: {
    strategies?: Record<string, ResearchStrategy>;
    micropricePairedExperiment?: {
      version?: string;
      pairedMarkets?: number;
      strategies?: Record<string, StrategyStats>;
      runtime?: {
        lastDecision?: Record<string, unknown> | null;
        rules?: Record<string, unknown>;
      };
    };
    micropriceFeatureShadows?: {
      version?: string;
      strategies?: Record<string, StrategyStats>;
    };
    micropriceConfirmOptimizationShadows?: {
      version?: string;
      priceSideGuardBlocked?: number;
      strategies?: Record<string, StrategyStats>;
      runtime?: {
        targetFills?: number;
        lastDecision?: Record<string, unknown> | null;
        rules?: Record<string, Record<string, unknown>>;
      };
    };
  } | null;
};

type Family = "paired" | "feature" | "optimization";
type Tone = "cyan" | "coral";

type CardDefinition = {
  id: string;
  family: Family;
  title: string;
  kicker: string;
  mode: string;
  rule: string;
  note: string;
  tone: Tone;
  badge: string;
};

const CARDS: CardDefinition[] = [
  {
    id: "R_MICROPRICE_CONFIRM",
    family: "paired",
    title: "Microprice 雙事件確認順勢 V2",
    kicker: "PAIRED SHADOW V2",
    mode: "FOLLOW CONFIRMED IMBALANCE",
    rule: "只接受獨立 UP／DOWN REST book；|Microprice| ≥0.2，同方向至少 2 個不同事件、持續 ≥150ms、book age ≤500ms、skew ≤150ms，midpoint 同向變化 ≥0.0005，且保留訊號強度 ≥65% 後沿原方向進場。有效窗口為剩餘 170～181 秒。",
    note: "兩組 paper 帳本使用固定 paired cohort；只有順勢版在實單設定明確選取時才轉送實單，反向版維持 paper only。",
    tone: "cyan",
    badge: "PAPER SHADOW · LIVE 可選",
  },
  {
    id: "R_MICROPRICE_REVERSION",
    family: "paired",
    title: "Microprice 雙事件確認反向 V2",
    kicker: "PAIRED SHADOW V2",
    mode: "REVERSE CONFIRMED IMBALANCE",
    rule: "與順勢版使用同一市場、同一確認事件及相同 V2 資料品質門檻，但買入相反方向；任一側價差或深度不足時兩組都不開。",
    note: "固定 paired cohort 的反事實對照，不加入 live executor 白名單。",
    tone: "coral",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_NO_020_025",
    family: "feature",
    title: "Microprice 排除 0.20–0.25 死區",
    kicker: "DEADZONE EXCLUSION",
    mode: "SOURCE MIRROR · OUTSIDE [0.20, 0.25)",
    rule: "鏡像原 R_MICROPRICE 的可成交 paper 單，但排除 0.20 ≤ entry <0.25；其餘價格、方向、stake、fee 與結算沿用來源單。",
    note: "Forward-only，不回填舊樣本。",
    tone: "cyan",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_UP_ONLY",
    family: "feature",
    title: "Microprice 方向拆帳 · UP",
    kicker: "DIRECTION SPLIT",
    mode: "SOURCE MIRROR · UP ONLY",
    rule: "只鏡像 R_MICROPRICE 的 UP 單，驗證 UP 的歷史落後是否在新樣本延續。",
    note: "與 DOWN cohort 完全拆帳。",
    tone: "coral",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_DOWN_ONLY",
    family: "feature",
    title: "Microprice 方向拆帳 · DOWN",
    kicker: "DIRECTION SPLIT",
    mode: "SOURCE MIRROR · DOWN ONLY",
    rule: "只鏡像 R_MICROPRICE 的 DOWN 單，不把舊資料中的正 PnL 直接當成永久規律。",
    note: "與 UP cohort 完全拆帳。",
    tone: "cyan",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_LOW_010_020_ASK_LE_60",
    family: "feature",
    title: "Microprice 低價肥尾 · Ask ≤60",
    kicker: "LOW-PRICE TAIL DEPTH",
    mode: "0.10 ≤ ENTRY <0.20 · AVAILABLE ASK ≤60",
    rule: "只鏡像 0.10 ≤ entry <0.20，且來源單在預留後的可用選定側 Ask 深度 ≤60 shares。",
    note: "低價肥尾候選組。",
    tone: "coral",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_LOW_010_020_ASK_GT_60",
    family: "feature",
    title: "Microprice 低價肥尾 · Ask >60",
    kicker: "LOW-PRICE TAIL DEPTH",
    mode: "0.10 ≤ ENTRY <0.20 · AVAILABLE ASK >60",
    rule: "相同低價區間但 Ask 深度 >60，保留作薄 Ask 效果的控制組。",
    note: "低價肥尾控制組。",
    tone: "cyan",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD",
    family: "optimization",
    title: "Microprice Confirm V2 · 方向價格防護",
    kicker: "PRICE/SIDE GUARD",
    mode: "V2 CONFIRM + PRICE/SIDE GUARD",
    rule: "完整沿用 R_MICROPRICE_CONFIRM V2，只額外阻擋 UP entry <0.40，以及 DOWN 0.50 ≤ entry <0.60。",
    note: "用來 forward 驗證方向 × 價格死區。",
    tone: "cyan",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_CONFIRM_EXIT_098",
    family: "optimization",
    title: "Microprice Confirm V2 · 0.98 提前退出",
    kicker: "SELL TARGET 0.98",
    mode: "V2 CONFIRM + SELL TARGET 0.98",
    rule: "進場沿用原 V2；實際 Bid ≥0.98 且可見 Bid 深度足以賣完整持倉時，以 0.98 限價平倉，否則持有到官方結算。",
    note: "紙上帳本使用直接雙 token REST；實單只在策略選單明確選取時送單。",
    tone: "coral",
    badge: "PAPER + LIVE SELECTABLE",
  },
];

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
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join("–") : String(value)}`)
    .join(" · ");
}

function experimentFor(payload: DashboardPayload | null, family: Family) {
  const research = payload?.researchForward;
  if (family === "paired") return research?.micropricePairedExperiment;
  if (family === "feature") return research?.micropriceFeatureShadows;
  return research?.micropriceConfirmOptimizationShadows;
}

function syncResearchLabels(target: HTMLElement | null) {
  if (!target) return;
  const form = target.closest("form");
  const eyebrow = form?.querySelector<HTMLElement>(".strategy-console-heading .eyebrow");
  const heading = form?.querySelector<HTMLElement>(".strategy-console-heading h2");
  if (eyebrow && eyebrow.textContent?.includes("FIVE PRIMARY")) {
    const expected = "FIVE PRIMARY + TWENTY-THREE SHADOWS · PAPER";
    if (eyebrow.textContent !== expected) eyebrow.textContent = expected;
  }
  if (heading && heading.textContent?.includes("五組主策略")) {
    const expected = "五組主策略＋二十三組 Shadow";
    if (heading.textContent !== expected) heading.textContent = expected;
  }
}

function ResearchCard({ definition, payload }: { definition: CardDefinition; payload: DashboardPayload | null }) {
  const experiment = experimentFor(payload, definition.family);
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
    ?? (definition.family === "paired" ? payload?.researchForward?.micropricePairedExperiment?.runtime?.rules : undefined)
    ?? (definition.family === "optimization" ? payload?.researchForward?.micropriceConfirmOptimizationShadows?.runtime?.rules?.[definition.id] : undefined)
    ?? {};
  const validation = research?.chronologicalValidation;
  const pairedMarkets = payload?.researchForward?.micropricePairedExperiment?.pairedMarkets ?? 0;
  const optimization = payload?.researchForward?.micropriceConfirmOptimizationShadows;
  const isExit = definition.id === "R_MICROPRICE_CONFIRM_EXIT_098";

  return <article className={`m-exit-card ${definition.tone}`} data-research-enhancement={definition.id}>
    <div className="m-exit-card-head">
      <div>
        <span className="eyebrow">{definition.id} · {definition.kicker}</span>
        <h3>{definition.title}</h3>
      </div>
      <div className="m-exit-card-actions"><span className="m-exit-id">{definition.badge}</span></div>
    </div>
    <div className="m-exit-primary-stats">
      <div><span>已實現收益</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong></div>
      <div><span>{isExit ? "正收益率" : "勝率"}</span><strong>{settled ? ratio(wins / settled) : "—"}</strong></div>
      <div><span>交易／未結算</span><strong>{trades} / {open}</strong></div>
    </div>
    <div className={`continuous-calibration-state ${settled >= 30 ? "ready" : "warmup"}`}>
      <span>{stats?.mode ?? definition.mode}</span>
      <strong>{definition.family === "paired" ? `配對市場 ${pairedMarkets} · ` : ""}已結束 {settled} / 30 · 勝 {wins} · 敗 {losses}</strong>
      <small>
        平均進場價 {decimal(stats?.averageEntryPrice)}
        {isExit ? ` · 0.98 提前成交 ${stats?.targetFilled ?? 0}` : ""}
        {isExit && stats?.averageExitPrice != null ? ` · 平均退出 ${decimal(stats.averageExitPrice)}` : ""}
      </small>
    </div>
    <p>{definition.rule}</p>
    <small>{definition.note}</small>
    <small>{compact(parameters)}</small>
    <small>Forward-only · 狀態 {validation?.status ?? "COLLECTING"} · 樣本 {validation?.samples ?? trades}。</small>
    {definition.id === "R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD" && <small>已阻擋 {optimization?.priceSideGuardBlocked ?? 0} 個來源市場。</small>}
    {isExit && <small>Paper target fills {optimization?.runtime?.targetFills ?? 0}。</small>}
  </article>;
}

export default function ResearchDashboardEnhancements() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const { payload } = useSharedDashboardState<DashboardPayload>();

  useEffect(() => {
    const locate = () => {
      const next = document.querySelector<HTMLElement>(TARGET_SELECTOR);
      setTarget(current => current === next ? current : next);
      syncResearchLabels(next);
    };
    const onInteraction = () => window.requestAnimationFrame(locate);
    locate();
    const timer = window.setInterval(locate, LOCATE_MS);
    document.addEventListener("click", onInteraction, true);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("click", onInteraction, true);
    };
  }, []);

  const cards = useMemo(
    () => CARDS.map(definition => <ResearchCard key={definition.id} definition={definition} payload={payload} />),
    [payload],
  );

  return target ? createPortal(cards, target) : null;
}
