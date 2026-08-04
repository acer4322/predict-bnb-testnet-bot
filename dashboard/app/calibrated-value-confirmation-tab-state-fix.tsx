"use client";

import { useEffect } from "react";

const TAB_ID = "calibrated-value-confirmation-tab";

export default function CalibratedValueConfirmationTabStateFix() {
  useEffect(() => {
    const sync = () => {
      const tab = document.getElementById(TAB_ID) as HTMLButtonElement | null;
      const tabList = tab?.closest<HTMLElement>(".strategy-tabs");
      if (!tab || !tabList) return;

      const isActive = tab.getAttribute("aria-selected") === "true";
      if (!isActive) return;

      tabList.querySelectorAll<HTMLButtonElement>("button[role='tab']").forEach(button => {
        if (button === tab) return;
        button.classList.remove("active");
        button.setAttribute("aria-selected", "false");
      });
    };

    const observer = new MutationObserver(sync);
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["aria-selected", "class"],
      childList: true,
      subtree: true,
    });
    sync();
    return () => observer.disconnect();
  }, []);

  return null;
}
