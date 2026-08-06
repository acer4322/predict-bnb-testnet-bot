"use client";

import { useEffect } from "react";

const OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_OBSERVER_GUARD";
const OBSERVER_LABEL = "趨勢過渡防護 · Generic";
const MAX_OBSERVER_SLOT = 2;

function strategySelect(slot: number) {
  return document.querySelector<HTMLSelectElement>(
    `select[aria-label="實單策略 ${slot + 1}"]`,
  );
}

function enabledSelect(slot: number) {
  return document.querySelector<HTMLSelectElement>(
    `select[aria-label="策略 ${slot + 1} 是否使用 Observer"]`,
  );
}

function versionSelect(slot: number) {
  return document.querySelector<HTMLSelectElement>(
    `select[aria-label="策略 ${slot + 1} Observer 版本"]`,
  );
}

function isObserverCompatible(strategy: string) {
  const normalized = strategy.trim().toUpperCase();
  return Boolean(normalized) && !normalized.startsWith("PAIR_ARB_");
}

function ensureGenericOption(select: HTMLSelectElement) {
  let option = Array.from(select.options).find(
    item => item.value === OBSERVER_VERSION,
  );
  if (!option) {
    option = document.createElement("option");
    option.value = OBSERVER_VERSION;
    select.append(option);
  }
  option.textContent = OBSERVER_LABEL;
}

function synchronizeSlots() {
  for (let slot = 0; slot <= MAX_OBSERVER_SLOT; slot += 1) {
    const strategy = strategySelect(slot);
    const enabled = enabledSelect(slot);
    const version = versionSelect(slot);
    if (!strategy || !enabled || !version) continue;

    const compatible = isObserverCompatible(strategy.value);
    if (!compatible) continue;

    if (enabled.disabled) enabled.disabled = false;
    if (version.disabled) version.disabled = false;
    ensureGenericOption(version);

    const enabledHelp = enabled.closest("label")?.querySelector("small");
    if (enabledHelp) {
      enabledHelp.textContent =
        "各策略槽位獨立；Generic Observer 可套用至任何非配對型策略，資料缺失時 fail closed";
    }
    const versionHelp = version.closest("label")?.querySelector("small");
    if (versionHelp) {
      versionHelp.textContent =
        "可選趨勢過渡防護；第 1、2、3 策略槽皆可獨立啟用";
    }
  }
}

export default function GenericObserverSlotUnlock() {
  useEffect(() => {
    let scheduled = false;
    const scheduleSync = () => {
      if (scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(() => {
        scheduled = false;
        synchronizeSlots();
      });
    };

    scheduleSync();
    const interval = window.setInterval(scheduleSync, 500);
    const observer = new MutationObserver(scheduleSync);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["disabled", "value"],
    });
    document.addEventListener("change", scheduleSync, true);
    document.addEventListener("focusin", scheduleSync, true);

    return () => {
      window.clearInterval(interval);
      observer.disconnect();
      document.removeEventListener("change", scheduleSync, true);
      document.removeEventListener("focusin", scheduleSync, true);
    };
  }, []);

  return null;
}
