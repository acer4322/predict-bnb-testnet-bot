"use client";

import { useEffect } from "react";

const SYNC_MS = 1_500;
const LEGACY_NATIVE_SOURCE_SET = [
  "R_MICROPRICE",
  "R_CALIBRATED_VALUE",
  "M01O_F1",
] as const;

const LIVE_STRATEGY_LABEL_OVERRIDES: Record<string, string> = {
  R_MICROPRICE_CONFIRM: "研究實單 · Microprice 雙事件確認順勢",
  R_MICROPRICE_CONFIRM_EXIT_098: "研究實單 · Microprice Confirm V2 · 0.98 提前退出",
};

function isLegacyConfirmationSourceSet(
  target: Set<unknown>,
  originalHas: (this: Set<unknown>, value: unknown) => boolean,
) {
  return target.size === LEGACY_NATIVE_SOURCE_SET.length
    && LEGACY_NATIVE_SOURCE_SET.every(value => originalHas.call(target, value));
}

function synchronizeOptions() {
  if (document.visibilityState !== "visible") return;

  document.querySelectorAll<HTMLSelectElement>(
    'select[aria-label^="實單策略 "]:not([aria-label$="資金模式"])',
  ).forEach(strategySelect => {
    Object.entries(LIVE_STRATEGY_LABEL_OVERRIDES).forEach(([strategy, label]) => {
      const option = Array.from(strategySelect.options).find(item => item.value === strategy);
      if (option && option.textContent !== label) option.textContent = label;
    });
  });

  document.querySelectorAll<HTMLSelectElement>(
    'select[aria-label$="資金模式"]',
  ).forEach(modeSelect => {
    const match = (modeSelect.getAttribute("aria-label") ?? "")
      .match(/實單策略\s+(\d+)\s+資金模式/);
    if (!match) return;
    const strategySelect = document.querySelector<HTMLSelectElement>(
      `select[aria-label="實單策略 ${match[1]}"]`,
    );
    const option = modeSelect.querySelector<HTMLOptionElement>(
      'option[value="CONFIRMATION_ADD"]',
    );
    if (!strategySelect || !option) return;

    const desiredDisabled = !strategySelect.value;
    const desiredText = "順勢確認加碼 Shadow 實單版";
    if (option.disabled !== desiredDisabled) option.disabled = desiredDisabled;
    if (option.textContent !== desiredText) option.textContent = desiredText;
  });
}

export default function ConfirmationAddModeOptionGuardFast() {
  useEffect(() => {
    const originalHas = Set.prototype.has as (
      this: Set<unknown>,
      value: unknown,
    ) => boolean;
    const universalHas = function(this: Set<unknown>, value: unknown) {
      if (
        typeof value === "string"
        && value.length > 0
        && isLegacyConfirmationSourceSet(this, originalHas)
      ) {
        return true;
      }
      return originalHas.call(this, value);
    };

    Object.defineProperty(Set.prototype, "has", {
      configurable: true,
      writable: true,
      value: universalHas,
    });

    const scheduleSync = () => window.requestAnimationFrame(synchronizeOptions);
    const handleVisibility = () => {
      if (document.visibilityState === "visible") scheduleSync();
    };
    document.addEventListener("change", scheduleSync, true);
    document.addEventListener("focusin", scheduleSync, true);
    document.addEventListener("visibilitychange", handleVisibility);
    synchronizeOptions();
    const timer = window.setInterval(synchronizeOptions, SYNC_MS);

    return () => {
      window.clearInterval(timer);
      document.removeEventListener("change", scheduleSync, true);
      document.removeEventListener("focusin", scheduleSync, true);
      document.removeEventListener("visibilitychange", handleVisibility);
      if (Set.prototype.has === universalHas) {
        Object.defineProperty(Set.prototype, "has", {
          configurable: true,
          writable: true,
          value: originalHas,
        });
      }
    };
  }, []);

  return null;
}
