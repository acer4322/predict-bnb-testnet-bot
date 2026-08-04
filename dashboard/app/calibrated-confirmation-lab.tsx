"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSharedDashboardState } from "./shared-dashboard-state";

const TAB_ID = "calibrated-value-confirmation-tab";
const PANEL_ID = "calibrated-value-confirmation-panel";
const LOCATE_MS = 1_000;

const STRATEGIES = [
  "R_CALIBRATED_VALUE_IMMEDIATE_CONTROL",
  "R_CALIBRATED_VALUE_CONFIRM_V2",
  "R_CALIBRATED_VALUE_CONFIRM_V2_REVERSE",
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

type RuntimeDecision = Record<string, unknown> & {
  marketId?: number | null;
  status?: string;
  reason?: string;
  initialEdge?: number | null;
  finalEdge?: number | null;
  retainedEdgeRatio?: number | null;
  confirmationCount?: number | null;
  confirmationDurationMs?: number | null;
  chosenMidpointDelta?: number | null;
};

type Experiment = {
  version?: string;
  filterExtensionVersion?: string;
  sourceStrategy?: string;
  immediateMarkets?: number;
  pairedMarkets?: number;
  completeCohorts?: number;
  range12Markets?: number;
  lowtailMarkets?: number;
  strategies?: Partial<Record<StrategyId, StrategyStats>>;
  runtime?: {
    status?: string;
    currentMarketId?: number | null;
    currentInitialSide?: string | null;
    currentInitialEdge?: number | null;
    currentInitialMidpoint?: number | null;
    currentConfirmations?: number;
    lastDecision?: RuntimeDecision | null;
    rules?: Record<string, unknown>;
  };
  filterRuntime?: {
    lastDecisions?: Partial<Record<StrategyId, RuntimeDecision | null>>;
    rules?: Partial<Record<StrategyId, Record<string, unknown>>>;
  };
  recentCohorts?: Array<{
    marketId?: number;
    immediate?: Record<string, unknown>;
    confirm?: Record<string, unknown>;
    reverse?: Record<string, unknown>;
    range12?: Record<string, unknown>;
    lowtail?: Record<string, unknown>;
  }>;
};

type DashboardPayload = {
  summaries?: Partial<Record<StrategyId, Summary>>;
  researchForward?: {
    strategies?: Partial<Record<StrategyId, ResearchStrategy>>;
    calibratedValueConfirmationExperiment?: Experiment;
  } | null;
};

type Tone = "purple" | "cyan" | "coral" | "green" | "amber";

type CardDefinition = {
  id: StrategyId;
  title: string;
  kicker: string;
  rule: string;
  note: string;
  tone: Tone;
};

const CARDS: CardDefinition[] = [
  {
    id: "R_CALIBRATED_VALUE_IMMEDIATE_CONTROL",
    title: "Calibrated Value 即時進場對照",
    kicker: "INITIAL EVENT CONTROL",
    rule: "第一個直接雙 token REST 事件若淨 edge ≥0.015，就使用當時直接 Ask 開立 5 USDT paper 單，不等待價格確認。",
    note: "保存等待確認前的基準；正式比較以完整 cohort 為主。",
    tone: "purple",
  },
  {
    id: "R_CALIBRATED_VALUE_CONFIRM_V2",
    title: "Calibrated Value 多事件確認順勢 V2",
    kicker: "FOLLOW CONFIRMED REPRICING",
    rule: "初始淨 edge ≥0.015 後，至少第二個不同事件、持續 ≥150ms；方向不變、選定側 midpoint 同向 ≥0.005，最終淨 edge ≥0.010 且至少保留初始 edge 的 65% 才沿原方向進場。",
    note: "確認時重新計算模型機率、直接 Ask、滑價與 fee。",
    tone: "cyan",
  },
  {
    id: "R_CALIBRATED_VALUE_CONFIRM_V2_REVERSE",
    title: "Calibrated Value 多事件確認反向 V2",
    kicker: "REVERSE CONFIRMED REPRICING",
    rule: "與順勢 V2 使用相同市場、確認事件與資料品質條件，但買入相反方向；任一側深度或 spread 不足時兩組都不開。",
    note: "永久 paper only，用來辨認延續或過度修正。",
    tone: "coral",
  },
  {
    id: "R_CALIBRATED_VALUE_CONFIRM_RANGE12",
    title: "Calibrated Value 確認 · Range 1–2",
    kicker: "CONFIRM V2 + RANGE SCORE 1–2",
    rule: "沿用 Confirm V2，另外要求當輪 Range score 為 1 或 2，且有效穿越不超過 2 次。",
    note: "Observer 欄位缺失或 market ID 不一致時 fail closed。",
    tone: "green",
  },
  {
    id: "R_CALIBRATED_VALUE_LOWTAIL_CONFIRM",
    title: "Calibrated Value 低價肥尾確認",
    kicker: "STRICT LOW-PRICE TAIL",
    rule: "確認後 entry 介於 0.10～0.221；至少 3 個事件、持續 ≥250ms，book age ≤300ms、skew ≤100ms、保留初始 edge ≥75%，且 trend veto=false。",
    note: "目標是保留少數大賺尾端，不是追求高勝率。",
    tone: "amber",
  },
];

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `$${value.toFixed(2)}`;
}

function ratio(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}

function decimal(value: unknown, digits = 3) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "—";
}

function compact(parameters: Record<string, unknown>) {
  return Object.entries(parameters)
    .filter(([, value]) => value != null)
    .slice(0, 14)
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join("–") : String(value)}`)
    .join(" · ");
}

function rowText(row: Record<string, unknown> | undefined, key: string) {
  const value = row?.[key];
  return value == null ? "—" : String(value);
}

function restoreHiddenPanels(form: HTMLElement | null) {
  form?.querySelectorAll<HTMLElement>("[data-cv-confirm-hidden='true']").forEach(panel => {
    panel.style.removeProperty("display");
    panel.hidden = false;
    delete panel.dataset.cvConfirmHidden;
  });
  form?.querySelectorAll<HTMLElement>("[data-cv-confirm-save-hidden='true']").forEach(node => {
    node.style.removeProperty("display");
    delete node.dataset.cvConfirmSaveHidden;
  });
}

function StrategyCard({ definition, payload }: { definition: CardDefinition; payload: DashboardPayload | null }) {
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
    ?? experiment?.runtime?.rules
    ?? {};
  const decision = experiment?.filterRuntime?.lastDecisions?.[definition.id];
  const validation = research?.chronologicalValidation;

  return <article className={`m-exit-card ${definition.tone}`} data-calibrated-strategy={definition.id}>
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
    {decision && <small>Runtime：{String(decision.status ?? "WAITING")} · {String(decision.reason ?? "")}</small>}
    <small>Forward-only · 每筆 5 USDT · 狀態 {validation?.status ?? "COLLECTING"} · 不回填、不轉送實單。</small>
  </article>;
}

function ExperimentPanel({ payload }: { payload: DashboardPayload | null }) {
  const experiment = payload?.researchForward?.calibratedValueConfirmationExperiment;
  const runtime = experiment?.runtime;
  const decision = runtime?.lastDecision;
  const rules = runtime?.rules ?? {};
  const recent = experiment?.recentCohorts ?? [];
  const runtimeStatus = decision?.status ?? runtime?.status ?? "WAITING";

  return <div className="m-exit-experiment research-forward-panel calibrated-value-confirmation-lab">
    <style>{`
      .calibrated-value-confirmation-lab .cv-confirm-runtime { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; margin:14px 0; }
      .calibrated-value-confirmation-lab .cv-confirm-runtime article { padding:12px; border:1px solid rgba(126,145,178,.24); border-radius:14px; background:rgba(13,18,29,.72); }
      .calibrated-value-confirmation-lab .cv-confirm-runtime span,.calibrated-value-confirmation-lab .cv-confirm-runtime small { display:block; color:#91a0bb; }
      .calibrated-value-confirmation-lab .cv-confirm-runtime strong { display:block; margin:4px 0; }
      .calibrated-value-confirmation-lab .cv-confirm-reason { margin:12px 0 18px; padding:12px 14px; border-left:3px solid #7ee3f5; background:rgba(74,196,219,.08); }
      .calibrated-value-confirmation-lab .cv-confirm-table small { display:block; color:#91a0bb; }
    `}</style>

    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">CALIBRATED VALUE CONFIRMATION LAB · FORWARD PAPER</span><h3>Calibrated Value 五組前向確認測試</h3></div>
      <p>獨立測試區，不混入五組主策略＋二十三組 Shadow。即時控制保存第一次合格 edge；其餘四組各自按確認與篩選規則建立前向帳本。</p>
    </section>

    <section className="m-exit-rules" aria-label="Calibrated Value 確認規則">
      <div className="m-exit-rules-head">
        <div><span className="eyebrow">DIRECT DUAL-TOKEN REST · FIXED COHORT</span><h3>確認引擎狀態</h3></div>
        <span className={`m-exit-api-state ${String(runtimeStatus).includes("OPENED") ? "live" : ""}`}>{runtimeStatus}</span>
      </div>
      <div className="cv-confirm-runtime">
        <article><span>目前市場</span><strong>#{runtime?.currentMarketId ?? "—"}</strong><small>來源 {experiment?.sourceStrategy ?? "R_CALIBRATED_VALUE"}</small></article>
        <article><span>初始方向／edge</span><strong>{runtime?.currentInitialSide ?? "—"} · {decimal(runtime?.currentInitialEdge, 4)}</strong><small>初始 midpoint {decimal(runtime?.currentInitialMidpoint, 4)}</small></article>
        <article><span>確認事件</span><strong>{runtime?.currentConfirmations ?? 0} / {String(rules.minimumConfirmations ?? 2)}</strong><small>至少 {String(rules.minimumConfirmationMs ?? 150)}ms</small></article>
        <article><span>即時／確認市場</span><strong>{experiment?.immediateMarkets ?? 0} / {experiment?.pairedMarkets ?? 0}</strong><small>完整三組 {experiment?.completeCohorts ?? 0} · Range12 {experiment?.range12Markets ?? 0} · Lowtail {experiment?.lowtailMarkets ?? 0}</small></article>
      </div>
      <p className="cv-confirm-reason">
        <strong>{decision?.reason ?? "等待合格的直接 REST book 事件"}</strong><br />
        市場 #{decision?.marketId ?? runtime?.currentMarketId ?? "—"} · 初始 edge {decimal(decision?.initialEdge, 4)} · 最終 edge {decimal(decision?.finalEdge, 4)} · 保留 {ratio(typeof decision?.retainedEdgeRatio === "number" ? decision.retainedEdgeRatio : null)} · midpoint Δ {decimal(decision?.chosenMidpointDelta, 4)}
      </p>
      <small>{compact(rules)}</small>
    </section>

    <div className="m-exit-summary-grid research-strategy-grid">
      {CARDS.map(definition => <StrategyCard key={definition.id} definition={definition} payload={payload} />)}
    </div>

    <section className="shadow-tag-live-orders cv-confirm-table" aria-label="最近完整確認 cohort">
      <div><span className="eyebrow">MATCHED COHORT AUDIT</span><h3>最近確認市場</h3></div>
      <div className="table-scroll"><table><thead><tr><th>市場</th><th>即時</th><th>順勢</th><th>反向</th><th>Range12</th><th>Lowtail</th></tr></thead><tbody>
        {recent.length === 0
          ? <tr><td colSpan={6} className="empty">等待部署後的新事件；不回填缺少連續 REST book 事件的舊交易。</td></tr>
          : recent.map(item => <tr key={item.marketId}>
            <td>#{item.marketId ?? "—"}</td>
            <td>{rowText(item.immediate, "side")} · {rowText(item.immediate, "status")}</td>
            <td>{rowText(item.confirm, "side")} · {rowText(item.confirm, "status")}</td>
            <td>{rowText(item.reverse, "side")} · {rowText(item.reverse, "status")}</td>
            <td>{rowText(item.range12, "side")} · {rowText(item.range12, "status")}</td>
            <td>{rowText(item.lowtail, "side")} · {rowText(item.lowtail, "status")}</td>
          </tr>)}
      </tbody></table></div>
    </section>
  </div>;
}

export default function CalibratedConfirmationLab() {
  const { payload } = useSharedDashboardState<DashboardPayload>();
  const [panelTarget, setPanelTarget] = useState<HTMLElement | null>(null);
  const [active, setActive] = useState(false);
  const activeRef = useRef(false);
  activeRef.current = active;

  useEffect(() => {
    let tabHost: HTMLButtonElement | null = null;
    let panelHost: HTMLDivElement | null = null;
    let tabList: HTMLElement | null = null;
    let form: HTMLElement | null = null;
    let savedEyebrow = "";
    let savedHeading = "";

    const applyVisibility = () => {
      const isActive = activeRef.current;
      if (tabHost) {
        if (tabHost.classList.contains("active") !== isActive) tabHost.classList.toggle("active", isActive);
        if (tabHost.getAttribute("aria-selected") !== String(isActive)) tabHost.setAttribute("aria-selected", String(isActive));
      }
      if (panelHost) {
        panelHost.hidden = !isActive;
        const expected = isActive ? "block" : "none";
        if (panelHost.style.display !== expected) panelHost.style.display = expected;
      }
      if (!form) return;

      if (isActive) {
        tabList?.querySelectorAll<HTMLButtonElement>("button[role='tab']").forEach(button => {
          if (button === tabHost) return;
          if (button.classList.contains("active")) button.classList.remove("active");
          if (button.getAttribute("aria-selected") !== "false") button.setAttribute("aria-selected", "false");
        });
        form.querySelectorAll<HTMLElement>("[role='tabpanel']").forEach(panel => {
          if (panel.id === PANEL_ID) return;
          panel.dataset.cvConfirmHidden = "true";
          panel.style.display = "none";
        });
        const saveBox = form.querySelector<HTMLElement>(".strategy-console-heading .save-box");
        if (saveBox) {
          saveBox.dataset.cvConfirmSaveHidden = "true";
          saveBox.style.display = "none";
        }
        const eyebrow = form.querySelector<HTMLElement>(".strategy-console-heading .eyebrow");
        const heading = form.querySelector<HTMLElement>(".strategy-console-heading h2");
        if (eyebrow) {
          if (!savedEyebrow) savedEyebrow = eyebrow.textContent ?? "";
          if (eyebrow.textContent !== "CALIBRATED VALUE · CONFIRMATION LAB") eyebrow.textContent = "CALIBRATED VALUE · CONFIRMATION LAB";
        }
        if (heading) {
          if (!savedHeading) savedHeading = heading.textContent ?? "";
          if (heading.textContent !== "Calibrated Value 多事件確認測試") heading.textContent = "Calibrated Value 多事件確認測試";
        }
      } else {
        restoreHiddenPanels(form);
        const eyebrow = form.querySelector<HTMLElement>(".strategy-console-heading .eyebrow");
        const heading = form.querySelector<HTMLElement>(".strategy-console-heading h2");
        if (eyebrow && savedEyebrow && eyebrow.textContent === "CALIBRATED VALUE · CONFIRMATION LAB") eyebrow.textContent = savedEyebrow;
        if (heading && savedHeading && heading.textContent === "Calibrated Value 多事件確認測試") heading.textContent = savedHeading;
      }
    };

    const activate = (event: Event) => {
      event.preventDefault();
      activeRef.current = true;
      setActive(true);
      applyVisibility();
    };

    const handleTabClick = (event: Event) => {
      const button = (event.target as HTMLElement | null)?.closest<HTMLButtonElement>("button[role='tab']");
      if (!button || button.id === TAB_ID) return;
      activeRef.current = false;
      setActive(false);
      applyVisibility();
    };

    const locate = () => {
      const nextTabList = document.querySelector<HTMLElement>(".strategy-tabs");
      const reliabilityTab = document.getElementById("reliability-shadow-tab");
      if (!nextTabList || !reliabilityTab) return;
      if (tabList !== nextTabList) {
        tabList?.removeEventListener("click", handleTabClick);
        tabList = nextTabList;
        tabList.addEventListener("click", handleTabClick);
      }
      form = nextTabList.closest("form");

      let nextTab = document.getElementById(TAB_ID) as HTMLButtonElement | null;
      if (!nextTab) {
        nextTab = document.createElement("button");
        nextTab.type = "button";
        nextTab.id = TAB_ID;
        nextTab.setAttribute("role", "tab");
        nextTab.setAttribute("aria-controls", PANEL_ID);
        nextTab.setAttribute("aria-selected", "false");
        nextTab.className = "shadow-tag";
        nextTab.innerHTML = "<strong>Calibrated 確認</strong><span>即時＋順反 V2＋Range12＋Lowtail</span>";
        reliabilityTab.parentElement?.insertBefore(nextTab, reliabilityTab);
      }
      if (tabHost !== nextTab) {
        tabHost?.removeEventListener("click", activate);
        tabHost = nextTab;
        tabHost.addEventListener("click", activate);
      }

      let nextPanel = document.getElementById(PANEL_ID) as HTMLDivElement | null;
      if (!nextPanel) {
        nextPanel = document.createElement("div");
        nextPanel.id = PANEL_ID;
        nextPanel.setAttribute("role", "tabpanel");
        nextPanel.setAttribute("aria-labelledby", TAB_ID);
        nextPanel.hidden = true;
        nextPanel.style.display = "none";
        nextTabList.insertAdjacentElement("afterend", nextPanel);
      }
      if (panelHost !== nextPanel) {
        panelHost = nextPanel;
        setPanelTarget(nextPanel);
      }
      applyVisibility();
    };

    locate();
    const timer = window.setInterval(locate, LOCATE_MS);
    return () => {
      window.clearInterval(timer);
      tabList?.removeEventListener("click", handleTabClick);
      tabHost?.removeEventListener("click", activate);
      restoreHiddenPanels(form);
      tabHost?.remove();
      panelHost?.remove();
    };
  }, []);

  const panel = useMemo(() => <ExperimentPanel payload={payload} />, [payload]);
  return panelTarget ? createPortal(panel, panelTarget) : null;
}
