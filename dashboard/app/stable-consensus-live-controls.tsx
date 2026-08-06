"use client";

import { useEffect } from "react";

const STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_GUARD";
const STRATEGY_LABEL = "Microprice Confirm · 穩定共識 0.60–0.90";
const OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_OBSERVER";
const OBSERVER_LABEL = "穩定共識 · F1 + Ask 0.60–0.90";
const MAX_OBSERVER_SLOT = 2;
const RULE_REFRESH_MS = 5_000;
const CONTROL_SYNC_MS = 1_000;
const REQUEST_TIMEOUT_MS = 4_000;
const LIVE_RULE_SAVE_TIMEOUT_MS = 12_000;

let savedStrategies: string[] = [];
let savedObserverVersions: string[] = [];

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function setReactSelectValue(select: HTMLSelectElement, value: string) {
  if (select.value === value) return false;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLSelectElement.prototype,
    "value",
  )?.set;
  if (setter) setter.call(select, value);
  else select.value = value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

function ensureOption(
  select: HTMLSelectElement,
  value: string,
  label: string,
) {
  let changed = false;
  let option = Array.from(select.options).find(item => item.value === value);
  if (!option) {
    option = document.createElement("option");
    option.value = value;
    select.append(option);
    changed = true;
  }
  if (option.textContent !== label) {
    option.textContent = label;
    changed = true;
  }
  return changed;
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
      strategy.value !== STRATEGY &&
      document.activeElement !== strategy
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

    if (enabled.disabled) enabled.disabled = false;
    if (version.disabled) version.disabled = false;
    ensureOption(version, OBSERVER_VERSION, OBSERVER_LABEL);

    if (
      savedObserverVersions[slot] === OBSERVER_VERSION &&
      version.value !== OBSERVER_VERSION &&
      document.activeElement !== version
    ) {
      setReactSelectValue(version, OBSERVER_VERSION);
    }

    const help = version.closest("label")?.querySelector<HTMLElement>("small");
    const stableHelp =
      "F1 歷史過渡防護 + 訊號側 Ask 0.60–0.90；缺資料 fail closed";
    if (
      help &&
      version.value === OBSERVER_VERSION &&
      help.textContent !== stableHelp
    ) {
      help.textContent = stableHelp;
    }
  }
}

function installLiveRulesSaveTimeout() {
  const originalFetch = window.fetch.bind(window);
  const patchedFetch: typeof window.fetch = (input, init) => {
    const url = typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
    const method = String(init?.method ?? "GET").toUpperCase();
    const isLiveRulesSave = method === "POST" && url.includes("/api/live-rules");
    if (!isLiveRulesSave || init?.signal) {
      return originalFetch(input, init);
    }

    const controller = new AbortController();
    const timeout = window.setTimeout(
      () => controller.abort(
        new DOMException("實單規則儲存逾時", "TimeoutError"),
      ),
      LIVE_RULE_SAVE_TIMEOUT_MS,
    );
    return originalFetch(input, { ...init, signal: controller.signal })
      .finally(() => window.clearTimeout(timeout));
  };

  window.fetch = patchedFetch;
  return () => {
    if (window.fetch === patchedFetch) window.fetch = originalFetch;
  };
}

async function refreshSavedRules() {
  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    REQUEST_TIMEOUT_MS,
  );
  try {
    const response = await fetch(apiUrl("/api/live-rules"), {
      cache: "no-store",
      signal: controller.signal,
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
    // Main dashboard owns connection and save errors.
  } finally {
    window.clearTimeout(timeout);
  }
}

export default function StableConsensusLiveControls() {
  useEffect(() => {
    let scheduled = false;
    const restoreFetch = installLiveRulesSaveTimeout();
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
    const syncTimer = window.setInterval(schedule, CONTROL_SYNC_MS);
    const ruleTimer = window.setInterval(
      () => void refreshSavedRules(),
      RULE_REFRESH_MS,
    );
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["disabled"],
    });
    document.addEventListener("change", schedule, true);
    document.addEventListener("focusin", schedule, true);

    return () => {
      restoreFetch();
      window.clearInterval(syncTimer);
      window.clearInterval(ruleTimer);
      observer.disconnect();
      document.removeEventListener("change", schedule, true);
      document.removeEventListener("focusin", schedule, true);
    };
  }, []);

  return null;
}
