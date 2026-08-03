"use client";

import { useEffect } from "react";

const RULES: Record<string, { title: string; rule: string }> = {
  R_MICROPRICE_CONFIRM: {
    title: "Microprice 雙事件確認順勢",
    rule: "配對 Shadow V2：只接受獨立 UP／DOWN REST book；同方向至少 2 個不同事件、持續 ≥150ms、book age ≤500ms、skew ≤150ms，且 midpoint 同向變化 ≥0.0005 後才沿原 Microprice 方向進場。有效窗口為剩餘 170～181 秒。",
  },
  R_MICROPRICE_REVERSION: {
    title: "Microprice 雙事件確認反向",
    rule: "配對 Shadow V2：與順勢版使用同一市場、同一確認事件與相同資料品質條件，但改買相反方向；任一側價差或深度不足時兩組都不開。",
  },
};

function updateLabels() {
  for (const [strategy, copy] of Object.entries(RULES)) {
    const card = document.querySelector<HTMLElement>(
      `[data-microprice-variant="${strategy}"]`,
    );
    if (!card) continue;
    const title = card.querySelector<HTMLElement>("h3");
    if (title && title.textContent !== copy.title) title.textContent = copy.title;
    const paragraphs = card.querySelectorAll<HTMLElement>("p");
    if (paragraphs[0] && paragraphs[0].textContent !== copy.rule) {
      paragraphs[0].textContent = copy.rule;
    }
  }
}

export default function MicropriceVariantV2Label() {
  useEffect(() => {
    const observer = new MutationObserver(updateLabels);
    observer.observe(document.body, { childList: true, subtree: true });
    updateLabels();
    return () => observer.disconnect();
  }, []);
  return null;
}
