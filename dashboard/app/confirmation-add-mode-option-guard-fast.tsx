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
  R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD: "研究實單 · Microprice Confirm V2 · 方向價格防護",
  R_MICROPRICE_CONFIRM_EXIT_098: "研究實單 · Microprice Confirm V2 · 0.98 提前退出",
  R_CALIBRATED_VALUE_CONFIRM_V2: "研究實單 · Calibrated Value 多事件確認順勢 V2",
};

function isLegacyConfirmationSourceSet(
  target: Set<unknown>,
  originalHas: (this: Set<unknown>, value: unknown) => boolean,
) {
  return target.size === LEGACY_NATIVE_SOURCE_SET.length
    && LEGACY_NATIVE_SOURCE_SET.every(value => originalHas.call(target, value));
}

function synchronizeResearchCards() {
  const micropriceCard = document.querySelector<HTMLElement>(
    '[data-research-enhancement="R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD"]',
  );
  if (micropriceCard) {
    const badge = micropriceCard.querySelector<HTMLElement>(".m-exit-id");
    if (badge && badge.textContent !== "PAPER + LIVE SELECTABLE") {
      badge.textContent = "PAPER + LIVE SELECTABLE";
    }
    Array.from(micropriceCard.querySelectorAll<HTMLElement>("small")).forEach(item => {
      if (item.textContent === "用來 forward 驗證方向 × 價格死區。") {
        item.textContent = "保留獨立 paper 帳本；只有在實單設定明確選取時才轉送，並再次檢查實際簽名報價不得進入方向價格死區。";
      }
    });
  }

  const calibratedCard = document.querySelector<HTMLElement>(
    '[data-calibrated-strategy="R_CALIBRATED_VALUE_CONFIRM_V2"]',
  );
  if (!calibratedCard) return;
  const badge = calibratedCard.querySelector<HTMLElement>(".m-exit-id");
  if (badge && badge.textContent !== "PAPER + LIVE SELECTABLE") {
    badge.textContent = "PAPER + LIVE SELECTABLE";
  }
  Array.from(calibratedCard.querySelectorAll<HTMLElement>("small")).forEach(item => {
    if (item.textContent?.includes("不回填、不轉送實單")) {
      item.textContent = item.textContent.replace(
        "不回填、不轉送實單",
        "不回填；只有實單設定明確選取時才轉送正向 Confirm V2",
      );
    }
  });
}

function synchronizeOptions() {
  if (document.visibilityState !== "visible") return;

  document.querySelectorAll<HTMLSelectElement>(
    'select[aria-label^="實單策略 "]:not([aria-label$="資金模式"])',
  ).forEach(strategySelect => {
    Object.entries(LIVE_STRATEGY_LABEL_OVERRIDES).forEach(([strategy, label]) => {
      let option = Array.from(strategySelect.options).find(item => item.value === strategy);
      if (!option) {
        option = document.createElement("option");
        option.value = strategy;
        strategySelect.appendChild(option);
      }
      if (option.textContent !== label) option.textContent = label;
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

  synchronizeResearchCards();
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
