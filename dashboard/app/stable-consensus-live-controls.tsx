"use client";

import { useEffect } from "react";

const STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_GUARD";
const STRATEGY_LABEL = "Microprice Confirm · 穩定共識 0.60–0.90";
const OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_OBSERVER";
const OBSERVER_LABEL = "穩定共識 · F1 + Ask 0.60–0.90";
const MAX_OBSERVER_SLOT = 2;

let savedStrategies: string[] = [];
let savedObserverVersions: string[] = [];

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
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

function ensureOption(
  select: HTMLSelectElement,
  value: string,
  label: string,
) {
  let option = Array.from(select.options).find(item => item.value === value);
  if (!option) {
    option = document.createElement("option");
    option.value = value;
    select.append(option);
  }
  option.textContent = label;
}

function strategySelect(slot: number) {
  return document.querySelector<HTMLSelectElement>(
    `select[aria-label="實單策略 ${slot + 1}"]`,
  );
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

function isObserverCompatible(strategy: string) {
  const normalized = strategy.trim().toUpperCase();
  return Boolean(normalized) && !normalized.startsWith("PAIR_ARB_");
}

function synchronizeControls() {
  for (let slot = 0; slot < 4; slot += 1) {
    const strategy = strategySelect(slot);
    if (!strategy) continue;
    ensureOption(strategy, STRATEGY, STRATEGY_LABEL);
    if (
      savedStrategies[slot] === STRATEGY &&
      strategy.value !== STRATEGY
    ) {
      setReactSelectValue(strategy, STRATEGY);
    }
  }

  for (let slot = 0; slot <= MAX_OBSERVER_SLOT; slot += 1) {
    const strategy = strategySelect(slot);
    const enabled = observerEnabledSelect(slot);
    const version = observerVersionSelect(slot);
    if (!strategy || !enabled || !version) continue;
    if (!isObserverCompatible(strategy.value || savedStrategies[slot] || "")) {
      continue;
    }

    enabled.disabled = false;
    version.disabled = false;
    ensureOption(version, OBSERVER_VERSION, OBSERVER_LABEL);

    if (
      savedObserverVersions[slot] === OBSERVER_VERSION &&
      version.value !== OBSERVER_VERSION
    ) {
      setReactSelectValue(version, OBSERVER_VERSION);
    }

    const help = version.closest("label")?.querySelector<HTMLElement>("small");
    if (help && version.value === OBSERVER_VERSION) {
      help.textContent =
        "F1 歷史過渡防護 + 訊號側 Ask 0.60–0.90；缺資料 fail closed";
    }
  }
}

async function refreshSavedRules() {
  try {
    const response = await fetch(apiUrl("/api/live-rules"), {
      cache: "no-store",
    });
    if (!response.ok) return;
    const body = await response.json();
    const rules = body?.rules ?? body?.liveM0W?.rules;
    if (Array.isArray(rules?.strategies)) {
      savedStrategies = rules.strategies.map(String);
    }
    if (Array.isArray(rules?.strategyObserverVersions)) {
      savedObserverVersions = rules.strategyObserverVersions.map(String);
    }
    synchronizeControls();
  } catch {
    // Main dashboard owns live connection errors.
  }
}

export default function StableConsensusLiveControls() {
  useEffect(() => {
    let scheduled = false;
    const schedule = () => {
      if (scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(() => {
        scheduled = false;
        synchronizeControls();
      });
    };

    schedule();
    void refreshSavedRules();
    const syncTimer = window.setInterval(schedule, 500);
    const ruleTimer = window.setInterval(
      () => void refreshSavedRules(),
      5_000,
    );
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["disabled", "value"],
    });
    document.addEventListener("change", schedule, true);
    document.addEventListener("focusin", schedule, true);

    return () => {
      window.clearInterval(syncTimer);
      window.clearInterval(ruleTimer);
      observer.disconnect();
      document.removeEventListener("change", schedule, true);
      document.removeEventListener("focusin", schedule, true);
    };
  }, []);

  return null;
}
