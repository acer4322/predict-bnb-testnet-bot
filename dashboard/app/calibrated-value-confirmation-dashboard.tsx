"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

const API_REFRESH_MS = 15_000;
const TAB_ID = "calibrated-value-confirmation-tab";
const PANEL_ID = "calibrated-value-confirmation-panel";

const STRATEGIES = [
  "R_CALIBRATED_VALUE_IMMEDIATE_CONTROL",
  "R_CALIBRATED_VALUE_CONFIRM_V2",
  "R_CALIBRATED_VALUE_CONFIRM_V2_REVERSE",
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
  mode?: string;
};

type RuntimeDecision = {
  marketId?: number | null;
  status?: string;
  reason?: string;
  sourceSide?: string | null;
  initialEdge?: number | null;
  finalEdge?: number | null;
  retainedEdgeRatio?: number | null;
  confirmationCount?: number | null;
  confirmationDurationMs?: number | null;
  chosenMidpointDelta?: number | null;
};

type Experiment = {
  version?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  forwardOnly?: boolean;
  sourceStrategy?: string;
  immediateMarkets?: number;
  pairedMarkets?: number;
  completeCohorts?: number;
  strategies?: Partial<Record<StrategyId, StrategyStats>>;
  recentCohorts?: Array<{
    marketId?: number;
    immediate?: Record<string, unknown>;
    confirm?: Record<string, unknown>;
    reverse?: Record<string, unknown>;
  }>;
  runtime?: {
    status?: string;
    currentMarketId?: number | null;
    currentInitialSide?: string | null;
    currentInitialEdge?: number | null;
    currentInitialMidpoint?: number | null;
    currentConfirmations?: number;
    lastDecision?: RuntimeDecision | null;
    rejections?: Record<string, number>;
    rules?: Record<string, unknown>;
  };
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
    calibratedValueConfirmationExperiment?: Experiment;
  } | null;
};

type CardDefinition = {
  id: StrategyId;
  title: string;
  kicker: string;
  rule: string;
  note: string;
  tone: "cyan" | "coral" | "purple";
};

const CARDS: CardDefinition[] = [
  {
    id: "R_CALIBRATED_VALUE_IMMEDIATE_CONTROL",
    title: "Calibrated Value 即時進場對照",
    kicker: "INITIAL EVENT CONTROL",
    rule: "第一個直接雙 token REST 事件若淨 edge ≥0.015，就使用當時直接 Ask 開立 5 USDT paper 單，不等待價格確認。",
    note: "這組保留等待成本的基準。比較確認版時，應優先查看三組都存在的 complete cohort，而不是拿全部即時單硬比確認成功樣本。",
    tone: "purple",
  },
  {
    id: "R_CALIBRATED_VALUE_CONFIRM_V2",
    title: "Calibrated Value 多事件確認順勢 V2",
    kicker: "FOLLOW CONFIRMED REPRICING",
    rule: "初始淨 edge ≥0.015 後，至少第二個不同事件、持續 ≥150ms；方向不變、選定側 midpoint 同向 ≥0.005，最終淨 edge ≥0.010 且至少保留初始 edge 的 65% 才沿原方向進場。",
    note: "確認時重新計算模型機率、直接 Ask、滑價與 fee；不是只看價格往同方向移動。",
    tone: "cyan",
  },
  {
    id: "R_CALIBRATED_VALUE_CONFIRM_V2_REVERSE",
    title: "Calibrated Value 多事件確認反向 V2",
    kicker: "REVERSE CONFIRMED REPRICING",
    rule: "與順勢 V2 使用完全相同的市場、確認事件與資料品質條件，但買入相反方向；任一側深度或 spread 不足時順勢與反向都不開。",
    note: "永久 paper only，用來辨認確認事件究竟代表價格延續，還是代表短期過度修正。",
    tone: "coral",
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
  const priority = [
    "minimumInitialNetEdge",
    "minimumFinalNetEdge",
    "minimumConfirmations",
    "minimumConfirmationMs",
    "maximumConfirmationMs",
    "maximumBookAgeMs",
    "maximumBookSkewMs",
    "minimumChosenMidpointMove",
    "minimumRetainedEdgeRatio",
    "stakeUsdtPerVariant",
    "slippageBps",
  ];
  return priority
    .filter(key => parameters[key] != null)
    .map(key => `${key}=${String(parameters[key])}`)
    .join(" · ");
}

function rowText(row: Record<string, unknown> | undefined, key: string) {
  const value = row?.[key];
  return value == null ? "—" : String(value);
}

function StrategyCard({
  definition,
  payload,
}: {
  definition: CardDefinition;
  payload: DashboardPayload | null;
}) {
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
  const validation = research?.chronologicalValidation;

  return <article className={`m-exit-card ${definition.tone}`}>
    <div className="m-exit-card-head">
      <div>
        <span className="eyebrow">{definition.id} · {definition.kicker}</span>
        <h3>{definition.title}</h3>
      </div>
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
    <small>
      Forward-only · 固定 market cohort · 來源 {experiment?.sourceStrategy ?? "R_CALIBRATED_VALUE"} · 版本 {experiment?.version ?? "等待後端"}
    </small>
    <small>
      狀態 {validation?.status ?? "COLLECTING"} · 樣本 {validation?.samples ?? trades} · 不回填舊交易、不轉送實單。
    </small>
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
      .calibrated-value-confirmation-lab .cv-confirm-runtime {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 10px;
        margin: 14px 0;
      }
      .calibrated-value-confirmation-lab .cv-confirm-runtime article {
        padding: 12px;
        border: 1px solid rgba(126,145,178,.24);
        border-radius: 14px;
        background: rgba(13,18,29,.72);
      }
      .calibrated-value-confirmation-lab .cv-confirm-runtime span,
      .calibrated-value-confirmation-lab .cv-confirm-runtime small {
        display: block;
        color: #91a0bb;
      }
      .calibrated-value-confirmation-lab .cv-confirm-runtime strong {
        display: block;
        margin: 4px 0;
      }
      .calibrated-value-confirmation-lab .cv-confirm-reason {
        margin: 12px 0 18px;
        padding: 12px 14px;
        border-left: 3px solid #7ee3f5;
        background: rgba(74,196,219,.08);
      }
      .calibrated-value-confirmation-lab .cv-confirm-table small {
        display: block;
        color: #91a0bb;
      }
    `}</style>

    <section className="strategy-family-intro m-exit-intro">
      <div>
        <span className="eyebrow">CALIBRATED VALUE CONFIRMATION LAB · FORWARD PAPER</span>
        <h3>Calibrated Value 即時／順勢確認／反向確認</h3>
      </div>
      <p>獨立的新測試區，不混入五組主策略＋二十一組 Shadow。即時控制組保存第一次合格 edge；確認後的順勢與反向兩組必須在同一個後續事件成對開倉。</p>
    </section>

    <section className="m-exit-rules" aria-label="Calibrated Value 確認規則">
      <div className="m-exit-rules-head">
        <div><span className="eyebrow">DIRECT DUAL-TOKEN REST · FIXED COHORT</span><h3>確認引擎狀態</h3></div>
        <span className={`m-exit-api-state ${runtimeStatus.includes("OPENED") ? "live" : ""}`}>{runtimeStatus}</span>
      </div>
      <div className="cv-confirm-runtime">
        <article><span>目前市場</span><strong>#{runtime?.currentMarketId ?? "—"}</strong><small>來源 {experiment?.sourceStrategy ?? "R_CALIBRATED_VALUE"}</small></article>
        <article><span>初始方向／edge</span><strong>{runtime?.currentInitialSide ?? "—"} · {decimal(runtime?.currentInitialEdge, 4)}</strong><small>初始 midpoint {decimal(runtime?.currentInitialMidpoint, 4)}</small></article>
        <article><span>確認事件</span><strong>{runtime?.currentConfirmations ?? 0} / {String(rules.minimumConfirmations ?? 2)}</strong><small>至少 {String(rules.minimumConfirmationMs ?? 150)}ms</small></article>
        <article><span>即時／確認市場</span><strong>{experiment?.immediateMarkets ?? 0} / {experiment?.pairedMarkets ?? 0}</strong><small>完整三組 cohort {experiment?.completeCohorts ?? 0}</small></article>
      </div>
      <p className="cv-confirm-reason">
        <strong>{decision?.reason ?? "等待剩餘 55～61 秒內的直接 REST book 事件"}</strong><br />
        市場 #{decision?.marketId ?? runtime?.currentMarketId ?? "—"} · 初始 edge {decimal(decision?.initialEdge, 4)} · 最終 edge {decimal(decision?.finalEdge, 4)} · 保留 {ratio(decision?.retainedEdgeRatio)} · midpoint Δ {decimal(decision?.chosenMidpointDelta, 4)}
      </p>
      <small>{compactParameters(rules)}</small>
    </section>

    <div className="m-exit-summary-grid research-strategy-grid">
      {CARDS.map(definition => <StrategyCard key={definition.id} definition={definition} payload={payload} />)}
    </div>

    <section className="shadow-tag-live-orders cv-confirm-table" aria-label="最近完整確認 cohort">
      <div><span className="eyebrow">MATCHED COHORT AUDIT</span><h3>最近三組完整配對市場</h3></div>
      <div className="table-scroll"><table><thead><tr><th>市場</th><th>即時控制</th><th>確認順勢</th><th>確認反向</th></tr></thead><tbody>
        {recent.length === 0
          ? <tr><td colSpan={4} className="empty">等待部署後的新事件；不回填缺少連續 REST book 事件的舊交易。</td></tr>
          : recent.map(item => <tr key={item.marketId}>
            <td>#{item.marketId ?? "—"}</td>
            <td>{rowText(item.immediate, "side")} · {rowText(item.immediate, "status")}<small>{rowText(item.immediate, "entry_price")}</small></td>
            <td>{rowText(item.confirm, "side")} · {rowText(item.confirm, "status")}<small>{rowText(item.confirm, "entry_price")}</small></td>
            <td>{rowText(item.reverse, "side")} · {rowText(item.reverse, "status")}<small>{rowText(item.reverse, "entry_price")}</small></td>
          </tr>)}
      </tbody></table></div>
      <small>順勢與反向一定使用同一個 confirmation event；任一側直接 Ask、spread 或深度不合格時兩組都不建立。即時控制可能多於完整 cohort，正式比較時以 complete cohort 為主。</small>
    </section>
  </div>;
}

function restoreHiddenPanels(form: HTMLElement | null) {
  form?.querySelectorAll<HTMLElement>("[data-cv-confirm-hidden='true']").forEach(panel => {
    panel.style.removeProperty("display");
    delete panel.dataset.cvConfirmHidden;
  });
  form?.querySelectorAll<HTMLElement>("[data-cv-confirm-save-hidden='true']").forEach(node => {
    node.style.removeProperty("display");
    delete node.dataset.cvConfirmSaveHidden;
  });
}

export default function CalibratedValueConfirmationDashboard() {
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [panelTarget, setPanelTarget] = useState<HTMLElement | null>(null);
  const [active, setActive] = useState(false);
  const activeRef = useRef(false);
  activeRef.current = active;

  useEffect(() => {
    let mounted = true;
    let controller: AbortController | null = null;
    let tabHost: HTMLButtonElement | null = null;
    let panelHost: HTMLDivElement | null = null;
    let tabList: HTMLElement | null = null;
    let form: HTMLElement | null = null;
    let savedEyebrow = "";
    let savedHeading = "";

    const applyVisibility = () => {
      const isActive = activeRef.current;
      if (tabHost) {
        tabHost.classList.toggle("active", isActive);
        tabHost.setAttribute("aria-selected", String(isActive));
      }
      if (panelHost) {
        panelHost.hidden = !isActive;
        panelHost.style.display = isActive ? "block" : "none";
      }
      if (!form) return;

      if (isActive) {
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
          if (eyebrow.textContent !== "CALIBRATED VALUE · CONFIRMATION LAB") {
            eyebrow.textContent = "CALIBRATED VALUE · CONFIRMATION LAB";
          }
        }
        if (heading) {
          if (!savedHeading) savedHeading = heading.textContent ?? "";
          if (heading.textContent !== "Calibrated Value 多事件確認測試") {
            heading.textContent = "Calibrated Value 多事件確認測試";
          }
        }
      } else {
        restoreHiddenPanels(form);
        const eyebrow = form.querySelector<HTMLElement>(".strategy-console-heading .eyebrow");
        const heading = form.querySelector<HTMLElement>(".strategy-console-heading h2");
        if (eyebrow && savedEyebrow && eyebrow.textContent === "CALIBRATED VALUE · CONFIRMATION LAB") {
          eyebrow.textContent = savedEyebrow;
        }
        if (heading && savedHeading && heading.textContent === "Calibrated Value 多事件確認測試") {
          heading.textContent = savedHeading;
        }
      }
    };

    const activate = (event: Event) => {
      event.preventDefault();
      activeRef.current = true;
      setActive(true);
      applyVisibility();
    };

    const handleTabClick = (event: Event) => {
      const target = event.target as HTMLElement | null;
      const button = target?.closest<HTMLButtonElement>("button[role='tab']");
      if (!button || button.id === TAB_ID) return;
      activeRef.current = false;
      setActive(false);
      restoreHiddenPanels(form);
    };

    const locate = () => {
      const nextTabList = document.querySelector<HTMLElement>(".strategy-tabs");
      const reliabilityTab = document.getElementById("reliability-shadow-tab");
      if (!nextTabList || !reliabilityTab) return;
      tabList = nextTabList;
      form = nextTabList.closest("form");

      let nextTabHost = document.getElementById(TAB_ID) as HTMLButtonElement | null;
      if (!nextTabHost) {
        nextTabHost = document.createElement("button");
        nextTabHost.type = "button";
        nextTabHost.id = TAB_ID;
        nextTabHost.setAttribute("role", "tab");
        nextTabHost.setAttribute("aria-controls", PANEL_ID);
        nextTabHost.setAttribute("aria-selected", "false");
        nextTabHost.className = "shadow-tag";
        nextTabHost.innerHTML = "<strong>Calibrated 確認</strong><span>即時控制＋順勢 V2＋反向 V2</span>";
        reliabilityTab.parentElement?.insertBefore(nextTabHost, reliabilityTab);
      }
      if (tabHost !== nextTabHost) {
        tabHost?.removeEventListener("click", activate);
        tabHost = nextTabHost;
        tabHost.addEventListener("click", activate);
      }

      let nextPanelHost = document.getElementById(PANEL_ID) as HTMLDivElement | null;
      if (!nextPanelHost) {
        nextPanelHost = document.createElement("div");
        nextPanelHost.id = PANEL_ID;
        nextPanelHost.setAttribute("role", "tabpanel");
        nextPanelHost.setAttribute("aria-labelledby", TAB_ID);
        nextPanelHost.hidden = true;
        nextPanelHost.style.display = "none";
        nextTabList.insertAdjacentElement("afterend", nextPanelHost);
      }
      if (panelHost !== nextPanelHost) {
        panelHost = nextPanelHost;
        setPanelTarget(nextPanelHost);
      }

      nextTabList.removeEventListener("click", handleTabClick);
      nextTabList.addEventListener("click", handleTabClick);
      applyVisibility();
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
        if (!response.ok) throw new Error(`dashboard request failed: ${response.status}`);
        const next = await response.json() as DashboardPayload;
        if (mounted) setPayload(next);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
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
      mounted = false;
      controller?.abort();
      observer.disconnect();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
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
