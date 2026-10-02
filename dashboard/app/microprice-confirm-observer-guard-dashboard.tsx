"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

const SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM";
const OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_OBSERVER_GUARD";
const SYNC_MS = 750;
const POLL_MS = 2_000;
const RULE_REFRESH_MS = 5_000;

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

type Performance = {
  trades?: number;
  open?: number;
  settled?: number;
  wins?: number;
  losses?: number;
  winRatePct?: number | null;
  realizedPnl?: number;
};

type GuardExperiment = {
  version?: string;
  observerVersion?: string;
  sourceStrategy?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  liveSelectableAsObserverVersion?: boolean;
  registeredAsLiveStrategy?: boolean;
  causalHistoricalSettlementsOnly?: boolean;
  currentRoundOutcomeUsed?: boolean;
  evaluations?: number;
  allowedEvaluations?: number;
  blockedEvaluations?: number;
  unavailableEvaluations?: number;
  shadowPerformance?: Performance;
  blockedSourceCounterfactual?: Performance;
  sourcePerformance?: Performance;
};

type DashboardPayload = {
  researchForward?: {
    micropriceConfirmObserverGuard?: GuardExperiment;
  };
};

type LiveRulesPayload = {
  rules?: {
    strategies?: string[];
    strategyObserverVersions?: string[];
  };
  liveM0W?: {
    rules?: {
      strategies?: string[];
      strategyObserverVersions?: string[];
    };
  };
};

const draftVersions = new Map<number, string>();
let savedStrategies: string[] = [];
let savedObserverVersions: string[] = [];

function slotFromLabel(label: string | null, pattern: RegExp) {
  const match = (label ?? "").match(pattern);
  return match ? Number(match[1]) - 1 : null;
}

function strategySlot(select: HTMLSelectElement) {
  return slotFromLabel(select.getAttribute("aria-label"), /^實單策略\s+(\d+)$/);
}

function observerVersionSlot(select: HTMLSelectElement) {
  return slotFromLabel(select.getAttribute("aria-label"), /^策略\s+(\d+)\s+Observer 版本$/);
}

function observerEnabledSelect(slot: number) {
  return document.querySelector<HTMLSelectElement>(
    `select[aria-label="策略 ${slot + 1} 是否使用 Observer"]`,
  );
}

function observerVersionSelect(slot: number) {
  return document.querySelector<HTMLSelectElement>(
    `select[aria-label="策略 ${slot + 1} Observer 版本"]`,
  );
}

function setReactSelectValue(select: HTMLSelectElement, value: string) {
  if (select.value === value) return;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLSelectElement.prototype,
    "value",
  )?.set;
  if (setter) setter.call(select, value);
  else select.value = value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

function ensureGuardOption(select: HTMLSelectElement) {
  let option = Array.from(select.options).find(
    item => item.value === OBSERVER_VERSION,
  );
  if (!option) {
    option = document.createElement("option");
    option.value = OBSERVER_VERSION;
    option.textContent = "Microprice Confirm · 趨勢過渡防護";
    select.append(option);
  } else if (option.textContent !== "Microprice Confirm · 趨勢過渡防護") {
    option.textContent = "Microprice Confirm · 趨勢過渡防護";
  }
}

function removeGuardOption(select: HTMLSelectElement) {
  Array.from(select.options)
    .filter(option => option.value === OBSERVER_VERSION)
    .forEach(option => option.remove());
}

function updateObserverPanelLabels() {
  const panel = document.querySelector<HTMLElement>("#lead-observer-panel");
  if (!panel) return;
  const eyebrow = panel.querySelector<HTMLElement>(".strategy-family-intro .eyebrow");
  if (eyebrow?.textContent?.includes("ELEVEN INDEPENDENT SHADOWS")) {
    eyebrow.textContent = "OBSERVER · TWELVE INDEPENDENT SHADOWS";
  }
  document.querySelectorAll<HTMLButtonElement>("#lead-observer-tab span").forEach(span => {
    if (span.textContent?.includes("Lead 5 版＋其他策略 6 組")) {
      span.textContent = "Lead 5 版＋其他策略 7 組";
    }
  });
}

function synchronizeObserverControls() {
  document.querySelectorAll<HTMLSelectElement>('select[aria-label^="實單策略 "]').forEach(
    strategySelect => {
      const slot = strategySlot(strategySelect);
      if (slot == null || slot > 2) return;
      const enabledSelect = observerEnabledSelect(slot);
      const versionSelect = observerVersionSelect(slot);
      if (!enabledSelect || !versionSelect) return;

      const selectedStrategy = strategySelect.value || savedStrategies[slot] || "";
      const isConfirm = selectedStrategy === SOURCE_STRATEGY;
      if (isConfirm) {
        enabledSelect.disabled = false;
        versionSelect.disabled = false;
        ensureGuardOption(versionSelect);
        const label = versionSelect.closest("label");
        const help = label?.querySelector<HTMLElement>("small");
        if (help) {
          help.textContent = (
            "可選趨勢過渡防護；只讀進場前已結算 Observer 歷史，資料缺失時 fail closed"
          );
        }
        const desired = draftVersions.get(slot) ?? savedObserverVersions[slot];
        if (desired === OBSERVER_VERSION && versionSelect.value !== OBSERVER_VERSION) {
          setReactSelectValue(versionSelect, OBSERVER_VERSION);
        }
      } else {
        if (versionSelect.value === OBSERVER_VERSION) {
          draftVersions.set(slot, "F1");
          setReactSelectValue(versionSelect, "F1");
        }
        removeGuardOption(versionSelect);
      }
    },
  );
  updateObserverPanelLabels();
}

function money(value: number | null | undefined, signed = false) {
  if (value == null || !Number.isFinite(value)) return "—";
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(2)} USDT`;
}

function pct(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}%`;
}

export default function MicropriceConfirmObserverGuardDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [experiment, setExperiment] = useState<GuardExperiment | null>(null);
  const [error, setError] = useState("");

  const refreshRules = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/live-rules"), { cache: "no-store" });
      if (!response.ok) return;
      const body = await response.json() as LiveRulesPayload;
      const rules = body.rules ?? body.liveM0W?.rules;
      if (Array.isArray(rules?.strategies)) {
        savedStrategies = rules.strategies.map(String);
      }
      if (Array.isArray(rules?.strategyObserverVersions)) {
        savedObserverVersions = rules.strategyObserverVersions.map(String);
      }
      synchronizeObserverControls();
    } catch {
      // The main dashboard owns live-rule connection errors.
    }
  }, []);

  const refreshExperiment = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/state"), { cache: "no-store" });
      const body = await response.json() as DashboardPayload & { error?: string };
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      setExperiment(body.researchForward?.micropriceConfirmObserverGuard ?? null);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "無法讀取 Observer Guard 狀態");
    }
  }, []);

  useEffect(() => {
    const locate = () => {
      const panel = document.querySelector<HTMLElement>("#lead-observer-panel");
      setTarget(current => current === panel ? current : panel);
      synchronizeObserverControls();
    };
    locate();
    const locateTimer = window.setInterval(locate, SYNC_MS);

    const handleChange = (event: Event) => {
      const element = event.target;
      if (!(element instanceof HTMLSelectElement)) return;
      const versionSlot = observerVersionSlot(element);
      if (versionSlot != null) draftVersions.set(versionSlot, element.value);
      const selectedSlot = strategySlot(element);
      if (selectedSlot != null && element.value !== SOURCE_STRATEGY) {
        const versionSelect = observerVersionSelect(selectedSlot);
        if (versionSelect?.value === OBSERVER_VERSION) {
          draftVersions.set(selectedSlot, "F1");
        }
      }
      window.requestAnimationFrame(synchronizeObserverControls);
    };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        void refreshRules();
        void refreshExperiment();
        window.requestAnimationFrame(synchronizeObserverControls);
      }
    };
    document.addEventListener("change", handleChange, true);
    document.addEventListener("focusin", synchronizeObserverControls, true);
    document.addEventListener("visibilitychange", handleVisibility);

    void refreshRules();
    void refreshExperiment();
    const ruleTimer = window.setInterval(() => void refreshRules(), RULE_REFRESH_MS);
    const stateTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refreshExperiment();
    }, POLL_MS);

    return () => {
      window.clearInterval(locateTimer);
      window.clearInterval(ruleTimer);
      window.clearInterval(stateTimer);
      document.removeEventListener("change", handleChange, true);
      document.removeEventListener("focusin", synchronizeObserverControls, true);
      document.removeEventListener("visibilitychange", handleVisibility);
      draftVersions.clear();
    };
  }, [refreshExperiment, refreshRules]);

  if (!target) return null;

  const shadow = experiment?.shadowPerformance;
  const blocked = experiment?.blockedSourceCounterfactual;
  return createPortal(
    <section
      data-observer-combination={OBSERVER_VERSION}
      style={{
        marginTop: 14,
        padding: 18,
        border: "1px solid rgba(255, 189, 107, .42)",
        borderRadius: 14,
        background: "linear-gradient(145deg, rgba(44, 31, 12, .72), rgba(8, 13, 16, .94))",
        display: "grid",
        gap: 14,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div>
          <span className="eyebrow">OBSERVER COMBINATION · PAPER SHADOW + LIVE VERSION</span>
          <h3 style={{ margin: "5px 0 0" }}>R_MICROPRICE_CONFIRM · 趨勢過渡防護</h3>
        </div>
        <strong style={{ color: "#ffbd87" }}>
          {experiment ? "觀測中" : "等待後端載入"}
        </strong>
      </div>

      <p style={{ margin: 0, color: "#aeb9cc", lineHeight: 1.7 }}>
        來源策略仍是 <code>{SOURCE_STRATEGY}</code>。當進場前的歷史 Observer 為
        <strong> UNCERTAIN</strong>、RANGE 分數 ≤ 2、TREND 分數 ≥ 3 時阻擋；其餘狀態通過。
        只讀已結算歷史輪次，不使用當輪最終結果。
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 10 }}>
        <article><small>已評估來源單</small><strong style={{ display: "block", marginTop: 5 }}>{experiment?.evaluations ?? 0}</strong></article>
        <article><small>通過／建立 Shadow</small><strong style={{ display: "block", marginTop: 5 }}>{experiment?.allowedEvaluations ?? 0}</strong></article>
        <article><small>過渡區阻擋</small><strong style={{ display: "block", marginTop: 5 }}>{experiment?.blockedEvaluations ?? 0}</strong></article>
        <article><small>資料不足／不可用</small><strong style={{ display: "block", marginTop: 5 }}>{experiment?.unavailableEvaluations ?? 0}</strong></article>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
        <article style={{ padding: 12, border: "1px solid rgba(126,145,178,.25)", borderRadius: 10 }}>
          <small>Guard 通過組</small>
          <strong style={{ display: "block", marginTop: 6 }}>{shadow?.settled ?? 0} 筆 · 勝率 {pct(shadow?.winRatePct)}</strong>
          <span style={{ display: "block", marginTop: 5 }}>已實現 {money(shadow?.realizedPnl, true)}</span>
        </article>
        <article style={{ padding: 12, border: "1px solid rgba(255,107,120,.25)", borderRadius: 10 }}>
          <small>被阻擋來源單反事實</small>
          <strong style={{ display: "block", marginTop: 6 }}>{blocked?.settled ?? 0} 筆 · 勝率 {pct(blocked?.winRatePct)}</strong>
          <span style={{ display: "block", marginTop: 5 }}>原策略損益 {money(blocked?.realizedPnl, true)}</span>
        </article>
      </div>

      <small style={{ color: "#8f9bab", lineHeight: 1.65 }}>
        實單使用方式：在原本的策略槽選 <code>{SOURCE_STRATEGY}</code>，開啟該槽 Observer，
        再從「Observer 版本」選擇此 Guard。它不是新的實單策略，也不會出現在實單策略名單。
      </small>
      {error && <small style={{ color: "#ffbd87" }}>狀態讀取錯誤：{error}</small>}
    </section>,
    target,
  );
}
