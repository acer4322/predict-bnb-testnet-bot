"use client";

import { useEffect } from "react";

const NATIVE_CONFIRMATION_SOURCES = new Set([
  "R_MICROPRICE",
  "R_CALIBRATED_VALUE",
]);

function synchronizeOptions() {
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

    const strategy = strategySelect.value;
    const desiredDisabled = !NATIVE_CONFIRMATION_SOURCES.has(strategy);
    const desiredText = strategy === "M01O_F1"
      ? "順勢確認加碼（F1 已移除）"
      : strategy === "R_FUTURES_LEAD"
        ? "順勢確認加碼（使用下方 Lead 控制）"
        : "順勢確認加碼 Shadow 實單版";

    if (option.disabled !== desiredDisabled) option.disabled = desiredDisabled;
    if (option.textContent !== desiredText) option.textContent = desiredText;
  });
}

export default function ConfirmationAddModeOptionGuard() {
  useEffect(() => {
    const handleChange = () => window.requestAnimationFrame(synchronizeOptions);
    const observer = new MutationObserver(synchronizeOptions);
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["disabled"],
    });
    document.addEventListener("change", handleChange, true);
    synchronizeOptions();
    return () => {
      observer.disconnect();
      document.removeEventListener("change", handleChange, true);
    };
  }, []);
  return null;
}
