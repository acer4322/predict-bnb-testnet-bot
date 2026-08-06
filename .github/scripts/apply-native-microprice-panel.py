from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:160]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


PANEL = r'''"use client";

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
  parameters?: Record<string, unknown>;
  state?: {
    mode?: string;
    consecutiveLosses?: number;
    probationRemaining?: number;
    shadowSampleCount?: number;
    latestShadowPnlSum?: number | null;
  };
};

type Experiment = {
  version?: string;
  pairedMarkets?: number;
  sourceMarkets?: number;
  blocked?: number;
  blockReasons?: Record<string, number>;
  strategies?: Record<string, StrategyStats>;
  runtime?: {
    opened?: number;
    halfStakeOpened?: number;
    shadowBlocked?: number;
    recoveries?: number;
    targetFills?: number;
    lastDecision?: Record<string, unknown> | null;
    rules?: Record<string, unknown> | Record<string, Record<string, unknown>>;
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
    minimum?: number;
  } | null;
};

type Payload = {
  summaries?: Record<string, Summary>;
  researchForward?: {
    strategies?: Record<string, ResearchStrategy>;
    micropricePairedExperiment?: Experiment;
    micropriceFeatureShadows?: Experiment;
    micropriceConfirmOptimizationShadows?: Experiment & { priceSideGuardBlocked?: number };
    micropriceConfirmStaleExhaustedGuard?: Experiment;
    micropriceConfirmLossStreakGuard?: Experiment;
  } | null;
};

type Family = "paired" | "feature" | "optimization" | "stale" | "loss";
type Tone = "cyan" | "coral" | "mint" | "amber";

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

export const MICROPRICE_STRATEGY_IDS = [
  "R_MICROPRICE_CONFIRM",
  "R_MICROPRICE_REVERSION",
  "R_MICROPRICE_NO_020_025",
  "R_MICROPRICE_UP_ONLY",
  "R_MICROPRICE_DOWN_ONLY",
  "R_MICROPRICE_LOW_010_020_ASK_LE_60",
  "R_MICROPRICE_LOW_010_020_ASK_GT_60",
  "R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD",
  "R_MICROPRICE_CONFIRM_EXIT_098",
  "R_MICROPRICE_CONFIRM_STALE_EXHAUSTED_GUARD",
  "R_MICROPRICE_CONFIRM_LOSS_STREAK_GUARD",
] as const;

const CARDS: CardDefinition[] = [
  {
    id: "R_MICROPRICE_CONFIRM",
    family: "paired",
    title: "Microprice 雙事件確認順勢 V2",
    kicker: "PAIRED SHADOW V2",
    mode: "FOLLOW CONFIRMED IMBALANCE",
    rule: "只接受獨立 UP／DOWN REST book；同方向至少兩個不同事件、持續至少 150ms，並要求簿齡、skew、midpoint 變化與訊號保留率合格後沿原方向進場。",
    note: "固定 paired cohort；只有順勢版在正式實單設定明確選取時才可能轉送，研究帳本本身保持獨立。",
    tone: "cyan",
    badge: "PAPER SHADOW · LIVE 可選",
  },
  {
    id: "R_MICROPRICE_REVERSION",
    family: "paired",
    title: "Microprice 雙事件確認反向 V2",
    kicker: "PAIRED SHADOW V2",
    mode: "REVERSE CONFIRMED IMBALANCE",
    rule: "與順勢版使用相同市場、相同確認事件及相同資料品質門檻，但買入相反方向。",
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
    rule: "鏡像原 R_MICROPRICE 的可成交 paper 單，但排除 0.20 ≤ entry < 0.25。",
    note: "其餘價格、方向、stake、fee 與結算沿用來源單；Forward-only。",
    tone: "cyan",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_UP_ONLY",
    family: "feature",
    title: "Microprice 方向拆帳 · UP",
    kicker: "DIRECTION SPLIT",
    mode: "SOURCE MIRROR · UP ONLY",
    rule: "只鏡像 R_MICROPRICE 的 UP 單，獨立驗證 UP 表現。",
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
    rule: "只鏡像 R_MICROPRICE 的 DOWN 單，獨立驗證 DOWN 表現。",
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
    rule: "只鏡像 0.10 ≤ entry < 0.20，且來源單在預留後的可用選定側 Ask 深度不超過 60 shares。",
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
    rule: "相同低價區間但 Ask 深度大於 60，保留作為薄 Ask 效果的控制組。",
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
    note: "Forward 驗證方向 × 價格死區。",
    tone: "cyan",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_CONFIRM_EXIT_098",
    family: "optimization",
    title: "Microprice Confirm V2 · 0.98 提前退出",
    kicker: "SELL TARGET 0.98",
    mode: "V2 CONFIRM + SELL TARGET 0.98",
    rule: "進場沿用原 V2；實際 Bid ≥0.98 且可見深度足以賣完整持倉時，以 0.98 平倉，否則持有到官方結算。",
    note: "Paper target 與正式選單狀態分開紀錄。",
    tone: "coral",
    badge: "PAPER + LIVE SELECTABLE",
  },
  {
    id: "R_MICROPRICE_CONFIRM_STALE_EXHAUSTED_GUARD",
    family: "stale",
    title: "Microprice Confirm V2 · 舊簿／衰退防護",
    kicker: "STALE/DECAY GUARD",
    mode: "V2 CONFIRM + STALE/DECAY GUARD",
    rule: "完整沿用 R_MICROPRICE_CONFIRM V2；在低價來源單檢查有效資料年齡、訊號衰減與訊號到開單延遲。",
    note: "低價單缺少必要診斷時採 fail-closed；只建立獨立 Paper 帳本。",
    tone: "amber",
    badge: "PAPER ONLY",
  },
  {
    id: "R_MICROPRICE_CONFIRM_LOSS_STREAK_GUARD",
    family: "loss",
    title: "Microprice Confirm · 連敗停機／恢復測試",
    kicker: "FORWARD-ONLY STATE MACHINE",
    mode: "LOSS STREAK SHADOW / PROBATION",
    rule: "正常狀態鏡像 R_MICROPRICE_CONFIRM；兩連敗後下一筆降額，第三敗後只觀察來源訊號，不建立測試單。",
    note: "最近三筆 Shadow PnL 合計轉正後進入兩筆降額觀察，兩筆都勝才回 NORMAL。",
    tone: "mint",
    badge: "PAPER ONLY",
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

function compact(parameters: Record<string, unknown> | undefined) {
  return Object.entries(parameters ?? {})
    .filter(([, value]) => value != null)
    .slice(0, 12)
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join("–") : String(value)}`)
    .join(" · ");
}

function experimentFor(payload: Payload | null | undefined, family: Family): Experiment | undefined {
  const research = payload?.researchForward;
  if (family === "paired") return research?.micropricePairedExperiment;
  if (family === "feature") return research?.micropriceFeatureShadows;
  if (family === "optimization") return research?.micropriceConfirmOptimizationShadows;
  if (family === "stale") return research?.micropriceConfirmStaleExhaustedGuard;
  return research?.micropriceConfirmLossStreakGuard;
}

function parametersFor(payload: Payload | null | undefined, definition: CardDefinition, stats: StrategyStats | undefined) {
  const research = payload?.researchForward;
  const selected = research?.strategies?.[definition.id]?.selectedBacktestParameters;
  if (selected) return selected;
  if (stats?.parameters) return stats.parameters;
  const rules = experimentFor(payload, definition.family)?.runtime?.rules;
  if (definition.family === "optimization" && rules && definition.id in rules) {
    return (rules as Record<string, Record<string, unknown>>)[definition.id];
  }
  return rules as Record<string, unknown> | undefined;
}

function stateModeText(mode: string | undefined) {
  if (mode === "SHADOW") return "SHADOW 暫停建立測試單";
  if (mode === "PROBATION") return "PROBATION 降額觀察";
  return "NORMAL 正常執行";
}

function MicropriceCard({ definition, payload }: { definition: CardDefinition; payload?: Payload | null }) {
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
  const parameters = parametersFor(payload, definition, stats);
  const validation = research?.chronologicalValidation;
  const optimization = payload?.researchForward?.micropriceConfirmOptimizationShadows;
  const lossState = stats?.state;
  const lastDecision = experiment?.runtime?.lastDecision;

  return <article className={`m-exit-card ${definition.tone}`} data-microprice-strategy={definition.id}>
    <div className="m-exit-card-head">
      <div>
        <span className="eyebrow">{definition.id} · {definition.kicker}</span>
        <h3>{definition.title}</h3>
      </div>
      <div className="m-exit-card-actions"><span className="m-exit-id">{definition.badge}</span></div>
    </div>
    <div className="m-exit-primary-stats">
      <div><span>已實現收益</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong></div>
      <div><span>勝率</span><strong>{settled ? ratio(wins / settled) : "—"}</strong></div>
      <div><span>交易／未結算</span><strong>{trades} / {open}</strong></div>
    </div>
    <div className={`continuous-calibration-state ${settled >= 30 || lossState?.mode === "NORMAL" ? "ready" : "warmup"}`}>
      <span>{definition.family === "loss" ? stateModeText(lossState?.mode) : stats?.mode ?? definition.mode}</span>
      <strong>
        {definition.family === "paired" ? `配對市場 ${experiment?.pairedMarkets ?? 0} · ` : ""}
        已結束 {settled} / 30 · 勝 {wins} · 敗 {losses}
      </strong>
      <small>
        平均進場價 {decimal(stats?.averageEntryPrice)}
        {definition.id === "R_MICROPRICE_CONFIRM_EXIT_098" ? ` · 0.98 提前成交 ${stats?.targetFilled ?? experiment?.runtime?.targetFills ?? 0}` : ""}
        {stats?.averageExitPrice != null ? ` · 平均退出 ${decimal(stats.averageExitPrice)}` : ""}
      </small>
    </div>
    <p>{definition.rule}</p>
    <small>{definition.note}</small>
    {definition.family === "stale" && <small>已評估來源市場 {experiment?.sourceMarkets ?? 0} · 已阻擋 {experiment?.blocked ?? 0} · {Object.entries(experiment?.blockReasons ?? {}).map(([reason, count]) => `${reason} ${count}`).join(" · ") || "尚未阻擋來源市場"}</small>}
    {definition.family === "loss" && <small>來源市場 {experiment?.sourceMarkets ?? 0} · 建立 {experiment?.runtime?.opened ?? 0} · 降額 {experiment?.runtime?.halfStakeOpened ?? 0} · Shadow 阻擋 {experiment?.runtime?.shadowBlocked ?? 0} · 恢復 {experiment?.runtime?.recoveries ?? 0} · 連敗 {lossState?.consecutiveLosses ?? 0} · Shadow 樣本 {lossState?.shadowSampleCount ?? 0} · 觀察剩餘 {lossState?.probationRemaining ?? 0}</small>}
    {definition.id === "R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD" && <small>已阻擋 {optimization?.priceSideGuardBlocked ?? 0} 個來源市場。</small>}
    {lastDecision && <small>最近決策：{String(lastDecision.status ?? "WAITING")} · {String(lastDecision.reason ?? "")}</small>}
    <small>{compact(parameters)}</small>
    <small>Forward-only · 狀態 {validation?.status ?? "COLLECTING"} · 樣本 {validation?.samples ?? trades}。</small>
  </article>;
}

export default function MicropriceStrategyPanel({ payload }: { payload?: Payload | null }) {
  const research = payload?.researchForward;
  return <div role="tabpanel" id="microprice-strategies-panel" aria-labelledby="microprice-strategies-tab" className="m-exit-experiment microprice-strategy-panel">
    <style>{`
      .microprice-strategy-panel .microprice-overview{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:14px 0 18px}
      .microprice-strategy-panel .microprice-overview article{padding:12px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
      .microprice-strategy-panel .microprice-overview span,.microprice-strategy-panel .microprice-overview small{display:block;color:#91a0bb}
      .microprice-strategy-panel .microprice-overview strong{display:block;margin:4px 0}
    `}</style>
    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">MICROPRICE · ELEVEN NATIVE FORWARD STRATEGIES</span><h3>Microprice 策略區 · 11 組前向與對照</h3></div>
      <p>九組 Microprice 擴充、舊簿／衰退防護與連敗狀態機集中在唯一原生頁籤。卡片直接由 React render tree 管理，不再透過全域 DOM selector、Portal 或定時定位插入其他頁面。</p>
    </section>
    <div className="microprice-overview">
      <article><span>策略卡</span><strong>{MICROPRICE_STRATEGY_IDS.length}</strong><small>單一原生容器</small></article>
      <article><span>Paired 市場</span><strong>{research?.micropricePairedExperiment?.pairedMarkets ?? 0}</strong><small>順勢／反向同 cohort</small></article>
      <article><span>舊簿防護</span><strong>{research?.micropriceConfirmStaleExhaustedGuard?.blocked ?? 0}</strong><small>已阻擋來源市場</small></article>
      <article><span>連敗狀態機</span><strong>{research?.micropriceConfirmLossStreakGuard?.runtime?.shadowBlocked ?? 0}</strong><small>Shadow 阻擋</small></article>
    </div>
    <div className="m-exit-summary-grid microprice-strategy-grid">
      {CARDS.map(definition => <MicropriceCard key={definition.id} definition={definition} payload={payload} />)}
    </div>
  </div>;
}
'''

TEST = r'''import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const page = fs.readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const layout = fs.readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
const panel = fs.readFileSync(new URL("../app/microprice-strategy-panel.tsx", import.meta.url), "utf8");
const loss = fs.readFileSync(new URL("../app/loss-streak-guard-dashboard.tsx", import.meta.url), "utf8");
const root = new URL("../app/", import.meta.url);

const ids = [
  "R_MICROPRICE_CONFIRM",
  "R_MICROPRICE_REVERSION",
  "R_MICROPRICE_NO_020_025",
  "R_MICROPRICE_UP_ONLY",
  "R_MICROPRICE_DOWN_ONLY",
  "R_MICROPRICE_LOW_010_020_ASK_LE_60",
  "R_MICROPRICE_LOW_010_020_ASK_GT_60",
  "R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD",
  "R_MICROPRICE_CONFIRM_EXIT_098",
  "R_MICROPRICE_CONFIRM_STALE_EXHAUSTED_GUARD",
  "R_MICROPRICE_CONFIRM_LOSS_STREAK_GUARD",
];

const banned = ["createPortal", "document.querySelector", "document.createElement", "MutationObserver", "setInterval", "/api/state"];

test("Microprice has one native page tab and eleven declared strategy IDs", () => {
  assert.match(page, /type StrategyView = [^;]*"microprice-strategies"/s);
  assert.match(page, /id="microprice-strategies-tab"/);
  assert.match(page, /<MicropriceStrategyPanel payload=\{state\}/);
  assert.match(page, /strategyView === "microprice-strategies"/);
  for (const id of ids) assert.equal(panel.includes(id), true, id);
  assert.equal((panel.match(/^  "R_MICROPRICE/gm) ?? []).length, 11);
});

test("Microprice cards do not use global DOM injection or their own polling", () => {
  for (const token of banned) assert.equal(panel.includes(token), false, token);
  assert.equal(panel.includes("research-strategy-grid"), false);
  assert.match(panel, /microprice-strategy-grid/);
});

test("old global card injectors are unmounted and removed", () => {
  assert.equal(layout.includes("ResearchDashboardEnhancements"), false);
  assert.equal(layout.includes("MicropriceStaleExhaustedGuardDashboard"), false);
  assert.equal(fs.existsSync(new URL("research-dashboard-enhancements.tsx", root)), false);
  assert.equal(fs.existsSync(new URL("microprice-stale-exhausted-guard-dashboard.tsx", root)), false);
});

test("loss streak live controls remain but no longer inject a research card", () => {
  assert.equal(layout.includes("LossStreakGuardDashboard"), true);
  assert.equal(loss.includes("RESEARCH_TARGET"), false);
  assert.equal(loss.includes("researchTarget"), false);
  assert.equal(loss.includes("createPortal(testCard"), false);
});
'''

panel_path = ROOT / "dashboard/app/microprice-strategy-panel.tsx"
panel_path.write_text(PANEL, encoding="utf-8")
(ROOT / "dashboard/tests/native-microprice-strategy-panel.test.mjs").write_text(TEST, encoding="utf-8")

layout = ROOT / "dashboard/app/layout.tsx"
text = layout.read_text(encoding="utf-8")
text = text.replace('import MicropriceStaleExhaustedGuardDashboard from "./microprice-stale-exhausted-guard-dashboard";\n', "")
text = text.replace('import ResearchDashboardEnhancements from "./research-dashboard-enhancements";\n', "")
text = text.replace('      <ResearchDashboardEnhancements />\n', "")
text = text.replace('      <MicropriceStaleExhaustedGuardDashboard />\n', "")
layout.write_text(text, encoding="utf-8")

page = ROOT / "dashboard/app/page.tsx"
replace_once(
    page,
    'import StrongTrendGuardPanel from "./strong-trend-guard-panel";\n',
    'import StrongTrendGuardPanel from "./strong-trend-guard-panel";\nimport MicropriceStrategyPanel, { MICROPRICE_STRATEGY_IDS } from "./microprice-strategy-panel";\n',
)
replace_once(
    page,
    'type StrategyView = "live-m0w" | "research" | "calibrated-confirmation" | "strong-trend-guard" | "reliability-shadow" | "lead-observer" | "m-series" | "pair-arb" | "legacy" | "paused";',
    'type StrategyView = "live-m0w" | "research" | "microprice-strategies" | "calibrated-confirmation" | "strong-trend-guard" | "reliability-shadow" | "lead-observer" | "m-series" | "pair-arb" | "legacy" | "paused";',
)
replace_once(
    page,
    '  const isNonConfigView = isLiveView || isReliabilityView || strategyView === "calibrated-confirmation" || strategyView === "strong-trend-guard";\n',
    '  const isNonConfigView = isLiveView || isReliabilityView || strategyView === "microprice-strategies" || strategyView === "calibrated-confirmation" || strategyView === "strong-trend-guard";\n',
)
replace_once(
    page,
    '  const visibleTrades = strategyView === "lead-observer" ? observerTradePage.trades : state.trades.filter(trade => {\n    if (strategyView === "strong-trend-guard") return trade.strategy.startsWith("R_STRONG_TREND_GUARD_");\n',
    '  const visibleTrades = strategyView === "lead-observer" ? observerTradePage.trades : state.trades.filter(trade => {\n    if (strategyView === "microprice-strategies") return MICROPRICE_STRATEGY_IDS.includes(String(trade.strategy) as (typeof MICROPRICE_STRATEGY_IDS)[number]);\n    if (strategyView === "strong-trend-guard") return trade.strategy.startsWith("R_STRONG_TREND_GUARD_");\n',
)
replace_once(
    page,
    ': strategyView === "calibrated-confirmation" ? "CALIBRATED VALUE · NATIVE CONFIRMATION"',
    ': strategyView === "microprice-strategies" ? "MICROPRICE · ELEVEN NATIVE STRATEGIES" : strategyView === "calibrated-confirmation" ? "CALIBRATED VALUE · NATIVE CONFIRMATION"',
)
replace_once(
    page,
    ': strategyView === "calibrated-confirmation" ? "Calibrated Value 五組前向確認測試"',
    ': strategyView === "microprice-strategies" ? "Microprice 策略區" : strategyView === "calibrated-confirmation" ? "Calibrated Value 五組前向確認測試"',
)
replace_once(
    page,
    '          <button type="button" role="tab" id="calibrated-value-confirmation-tab"',
    '          <button type="button" role="tab" id="microprice-strategies-tab" aria-controls="microprice-strategies-panel" aria-selected={strategyView === "microprice-strategies"} className={strategyView === "microprice-strategies" ? "active" : ""} onClick={() => setStrategyView("microprice-strategies")}><strong>Microprice 策略區</strong><span>11 組前向、對照與防護 · 原生頁籤</span></button>\n          <button type="button" role="tab" id="calibrated-value-confirmation-tab"',
)
replace_once(
    page,
    'onRulesSave={saveLiveRules} /> : strategyView === "calibrated-confirmation" ? <CalibratedConfirmationLab payload={state} />',
    'onRulesSave={saveLiveRules} /> : strategyView === "microprice-strategies" ? <MicropriceStrategyPanel payload={state} /> : strategyView === "calibrated-confirmation" ? <CalibratedConfirmationLab payload={state} />',
)

loss = ROOT / "dashboard/app/loss-streak-guard-dashboard.tsx"
text = loss.read_text(encoding="utf-8")
text = text.replace('import { useEffect, useMemo, useRef, useState } from "react";', 'import { useEffect, useRef, useState } from "react";')
text = text.replace('import { useSharedDashboardState } from "./shared-dashboard-state";\n', '')
text = text.replace('const RESEARCH_TARGET = ".research-forward-panel .research-strategy-grid";\n', '')
text = text.replace('  const [researchTarget, setResearchTarget] = useState<HTMLElement | null>(null);\n', '')
text = text.replace('  const { payload } = useSharedDashboardState<DashboardPayload>();\n', '')
text = text.replace('\n      const research = document.querySelector<HTMLElement>(RESEARCH_TARGET);\n      setResearchTarget(current => current === research ? current : research);', '')
text = text.replace('  const testCard = useMemo(() => <TestStrategyCard payload={payload} />, [payload]);\n', '')
text = text.replace('    {researchTarget ? createPortal(testCard, researchTarget) : null}\n', '')
loss.write_text(text, encoding="utf-8")

for old in (
    ROOT / "dashboard/app/research-dashboard-enhancements.tsx",
    ROOT / "dashboard/app/microprice-stale-exhausted-guard-dashboard.tsx",
):
    old.unlink(missing_ok=True)

print("native Microprice strategy panel patch applied")
