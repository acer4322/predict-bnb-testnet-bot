"use client";

import { useEffect } from "react";

const TAB_ID = "calibrated-value-confirmation-tab";

export default function CalibratedValueConfirmationTabStateFix() {
  useEffect(() => {
    let syncing = false;

    const sync = () => {
      if (syncing) return;

      const tab = document.getElementById(TAB_ID) as HTMLButtonElement | null;
      const tabList = tab?.closest<HTMLElement>(".strategy-tabs");
      if (!tab || !tabList) return;

      const isActive = tab.getAttribute("aria-selected") === "true";
      if (!isActive) return;

      syncing = true;
      try {
        tabList.querySelectorAll<HTMLButtonElement>("button[role='tab']").forEach(button => {
          if (button === tab) return;

          if (button.classList.contains("active")) {
            button.classList.remove("active");
          }
          if (button.getAttribute("aria-selected") !== "false") {
            button.setAttribute("aria-selected", "false");
          }
        });
      } finally {
        syncing = false;
      }
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
