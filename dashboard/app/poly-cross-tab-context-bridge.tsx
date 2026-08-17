"use client";

import { useEffect } from "react";

/**
 * OracleCrossStrategyPanel is mounted from the root layout so it can inject a
 * tab without coupling the cross-oracle sidecar to the large native page.
 * Before that injected tab activates, move the native page to its research
 * context so the top market card remains "正式市場 · 模擬研究" rather than a
 * stale real-money view.
 */
export default function PolyCrossTabContextBridge() {
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const target = event.target instanceof Element
        ? event.target.closest("button.poly-cross-tab")
        : null;
      if (!target) return;
      const research = document.getElementById("research-tab");
      if (!(research instanceof HTMLButtonElement)) return;
      if (research.getAttribute("aria-selected") === "true") return;
      research.click();
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, []);
  return null;
}
