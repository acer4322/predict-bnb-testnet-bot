"use client";

import { useEffect } from "react";

type RuleCopy = {
  title: string;
  rule: string;
  badge: string;
  cohort: string;
  control: string;
};

const RULES: Record<string, RuleCopy> = {
  R_MICROPRICE_CONFIRM: {
    title: "Microprice 雙事件確認順勢 V2",
    rule: "配對 Shadow V2：只接受獨立 UP／DOWN REST book；|Microprice| ≥0.2，同方向至少 2 個不同事件、持續 ≥150ms、book age ≤500ms、skew ≤150ms，midpoint 同向變化 ≥0.0005，且保留訊號強度 ≥65% 後才沿原方向進場。有效窗口為剩餘 170～181 秒；每組 5 USDT，滑價 50bps。",
    badge: "PAPER SHADOW · LIVE 可選",
    cohort: "兩組 paper 帳本仍使用固定 paired cohort 同事件成對開倉；只有順勢版在實單設定明確選取時才轉送實單，反向版維持 paper only。",
    control: "紙上訊號仍跟隨 R_MICROPRICE 主開關；實單需另外選取 R_MICROPRICE_CONFIRM，並受獨立金額、順勢確認加碼與所有實單風控限制。",
  },
  R_MICROPRICE_REVERSION: {
    title: "Microprice 雙事件確認反向 V2",
    rule: "配對 Shadow V2：與順勢版使用同一市場、同一確認事件及相同 V2 資料品質門檻，但改買相反方向；任一側價差或深度不足時兩組都不開。每組 5 USDT，滑價 50bps。",
    badge: "PAPER ONLY",
    cohort: "兩組 paper 帳本使用固定 paired cohort 同事件成對開倉；反向版只作反事實對照，不加入 live executor 白名單。",
    control: "目前跟隨 R_MICROPRICE 主開關；R_MICROPRICE_REVERSION 無獨立實單或資金控制，永不轉送實單。",
  },
};

const GLOBAL_REPLACEMENTS = new Map<string, string>([
  [
    "新增 Microprice 確認／回歸配對 · 全部 paper only",
    "新增 Microprice 配對 Shadow V2 · 順勢版可選實單",
  ],
  [
    "五組主策略共用 100 USDT 模擬曝險；十六組 Shadow 各自獨立做反事實對照。持續校準 V2 與 Microprice 確認／回歸配對都只使用紙上資料，且永遠不在 live executor 白名單。",
    "五組主策略共用 100 USDT 模擬曝險；十六組 Shadow 各自獨立做反事實對照。Microprice 配對帳本仍為 paper；只有 R_MICROPRICE_CONFIRM 在實單設定明確選取時可轉送，R_MICROPRICE_REVERSION 維持 paper only。",
  ],
]);

function updateLabels() {
  for (const [strategy, copy] of Object.entries(RULES)) {
    const card = document.querySelector<HTMLElement>(
      `[data-microprice-variant="${strategy}"]`,
    );
    if (!card) continue;

    const title = card.querySelector<HTMLElement>("h3");
    if (title && title.textContent !== copy.title) title.textContent = copy.title;

    const badge = card.querySelector<HTMLElement>(".m-exit-id");
    if (badge && badge.textContent !== copy.badge) badge.textContent = copy.badge;

    const paragraphs = card.querySelectorAll<HTMLElement>("p");
    if (paragraphs[0] && paragraphs[0].textContent !== copy.rule) {
      paragraphs[0].textContent = copy.rule;
    }

    const smalls = Array.from(card.querySelectorAll<HTMLElement>("small"));
    const cohort = smalls.find(node =>
      node.textContent?.includes("兩組同事件成對開倉")
      || node.textContent?.includes("兩組 paper 帳本")
    );
    if (cohort && cohort.textContent !== copy.cohort) cohort.textContent = copy.cohort;

    const control = smalls.find(node =>
      node.textContent?.includes("目前跟隨 R_MICROPRICE 主開關")
      || node.textContent?.includes("紙上訊號仍跟隨 R_MICROPRICE 主開關")
    );
    if (control && control.textContent !== copy.control) control.textContent = copy.control;
  }

  document.querySelectorAll<HTMLElement>("span, h2, h3, p, strong").forEach(node => {
    if (node.childElementCount > 0) return;
    const current = node.textContent?.trim() ?? "";
    const replacement = GLOBAL_REPLACEMENTS.get(current);
    if (replacement) node.textContent = replacement;
  });
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
