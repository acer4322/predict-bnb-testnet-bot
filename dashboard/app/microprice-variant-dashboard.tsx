"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const API_REFRESH_MS = 15_000;
const VARIANT_IDS = [
  "R_MICROPRICE_CONFIRM",
  "R_MICROPRICE_REVERSION",
] as const;

type VariantId = (typeof VARIANT_IDS)[number];

type VariantSummary = {
  trades?: number;
  open?: number;
  wins?: number;
  losses?: number;
  realized_pnl?: number;
};

type VariantResearchState = {
  enabled?: boolean;
  stakeUsdt?: number;
  selectedBacktestParameters?: Record<string, unknown>;
  chronologicalValidation?: {
    status?: string;
    samples?: number;
    settled?: number;
    wins?: number;
    losses?: number;
    realizedPnl?: number;
    minimum?: number;
    pairedMarketOnly?: boolean;
  } | null;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
};

type PairedExperiment = {
  version?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  pairedMarkets?: number;
  pairingRule?: string;
  strategies?: Partial<Record<VariantId, {
    trades?: number;
    open?: number;
    settled?: number;
    wins?: number;
    losses?: number;
    winRate?: number | null;
    realizedPnl?: number;
    averageEntryPrice?: number | null;
  }>>;
  runtime?: {
    confirmedPairs?: number;
    currentMarketId?: number | null;
    currentDirection?: string | null;
    currentConfirmations?: number;
    lastDecision?: {
      status?: string;
      reason?: string;
      score?: number | null;
      sourceSide?: string | null;
      confirmationCount?: number;
      confirmationDurationMs?: number;
    } | null;
    rejections?: Record<string, number>;
    rules?: Record<string, unknown>;
  };
};

type DashboardPayload = {
  summaries?: Partial<Record<VariantId, VariantSummary>>;
  researchForward?: {
    strategies?: Partial<Record<VariantId, VariantResearchState>>;
    micropricePairedExperiment?: PairedExperiment;
  } | null;
};

type CardDefinition = {
  id: VariantId;
  title: string;
  mode: string;
  tone: string;
  rule: string;
};

const CARDS: CardDefinition[] = [
  {
    id: "R_MICROPRICE_CONFIRM",
    title: "Microprice 三次確認順勢",
    mode: "FOLLOW CONFIRMED IMBALANCE",
    tone: "cyan",
    rule: "配對 Shadow：只接受獨立 UP／DOWN REST book；同方向至少 3 個不同事件、持續 ≥300ms、book age ≤500ms、skew ≤150ms，且 midpoint 同向確認後才沿原 Microprice 方向進場。",
  },
  {
    id: "R_MICROPRICE_REVERSION",
    title: "Microprice 三次確認反向",
    mode: "REVERSE CONFIRMED IMBALANCE",
    tone: "coral",
    rule: "配對 Shadow：與確認順勢版使用同一市場、同一確認事件與同一盤口品質條件，但改買相反方向；任一側價差或深度不足時兩組都不開。",
  },
];

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function money(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : `$${value.toFixed(digits)}`;
}

function ratio(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}

function decimal(value: number | null | undefined, digits = 3) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function compactParameters(parameters: Record<string, unknown>) {
  const preferred = [
    "threshold",
    "minimumConfirmations",
    "minimumConfirmationMs",
    "maximumBookAgeMs",
    "maximumBookSkewMs",
    "minimumMidpointMove",
    "minimumRetainedStrength",
    "stakeUsdtPerVariant",
    "slippageBps",
  ];
  return preferred
    .filter(key => parameters[key] != null)
    .map(key => `${key}=${String(parameters[key])}`)
    .join(" · ");
}

function updateResearchLabels() {
  const replacements = new Map<string, string>([
    [
      "FORWARD PAPER · FIVE PRIMARY + TEN SHADOWS",
      "FORWARD PAPER · FIVE PRIMARY + SIXTEEN SHADOWS",
    ],
    [
      "FIVE PRIMARY + TWELVE SHADOWS · PAPER",
      "FIVE PRIMARY + SIXTEEN SHADOWS · PAPER",
    ],
    [
      "五組主策略＋十二組獨立 Shadow",
      "五組主策略＋十六組獨立 Shadow",
    ],
    [
      "五組主策略＋十二組 Shadow",
      "五組主策略＋十六組 Shadow",
    ],
    ["5 主策略＋12 Shadow", "5 主策略＋16 Shadow"],
    [
      "新增兩組持續校準 V2 · 全部 paper only",
      "新增 Microprice 確認／回歸配對 · 全部 paper only",
    ],
    [
      "五組主策略共用 100 USDT 模擬曝險；十二組 Shadow 各自獨立做反事實對照。兩組持續校準 V2 只跟隨同市場已開出的來源 paper 單，且永遠不在 live executor 白名單。",
      "五組主策略共用 100 USDT 模擬曝險；十六組 Shadow 各自獨立做反事實對照。持續校準 V2 與 Microprice 確認／回歸配對都只使用紙上資料，且永遠不在 live executor 白名單。",
    ],
  ]);

  document.querySelectorAll<HTMLElement>("span, h2, h3, p, strong").forEach(node => {
    if (node.childElementCount > 0) return;
    const current = node.textContent?.trim() ?? "";
    const replacement = replacements.get(current);
    if (replacement) node.textContent = replacement;
  });
}

function VariantCard({
  definition,
  payload,
}: {
  definition: CardDefinition;
  payload: DashboardPayload | null;
}) {
  const experiment = payload?.researchForward?.micropricePairedExperiment;
  const apiState = payload?.researchForward?.strategies?.[definition.id];
  const experimentStats = experiment?.strategies?.[definition.id];
  const summary = payload?.summaries?.[definition.id];
  const wins = summary?.wins ?? experimentStats?.wins ?? 0;
  const losses = summary?.losses ?? experimentStats?.losses ?? 0;
  const settled = wins + losses;
  const trades = summary?.trades ?? experimentStats?.trades ?? 0;
  const open = summary?.open ?? experimentStats?.open ?? 0;
  const pnl = summary?.realized_pnl ?? experimentStats?.realizedPnl ?? 0;
  const parameters = apiState?.selectedBacktestParameters
    ?? experiment?.runtime?.rules
    ?? {};
  const validation = apiState?.chronologicalValidation;
  const last = experiment?.runtime?.lastDecision;
  const isConfirm = definition.id === "R_MICROPRICE_CONFIRM";

  return <article className={`m-exit-card ${definition.tone}`} data-microprice-variant={definition.id}>
    <div className="m-exit-card-head">
      <div>
        <span className="eyebrow">{definition.id} · PAIRED ISOLATED SHADOW</span>
        <h3>{definition.title}</h3>
      </div>
      <div className="m-exit-card-actions">
        <span className="m-exit-id">PAPER ONLY</span>
      </div>
    </div>

    <div className="m-exit-primary-stats">
      <div>
        <span>已實現收益</span>
        <strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong>
      </div>
      <div><span>勝率</span><strong>{settled ? ratio(wins / settled) : "—"}</strong></div>
      <div><span>交易／未結算</span><strong>{trades} / {open}</strong></div>
    </div>

    <div className={`continuous-calibration-state ${settled >= 30 ? "ready" : "warmup"}`}>
      <span>{definition.mode}</span>
      <strong>配對市場 {experiment?.pairedMarkets ?? 0} · 已結算 {settled} / 30</strong>
      <small>
        {isConfirm ? "沿確認後原方向" : "同事件反向對照"}
        {experimentStats?.averageEntryPrice == null
          ? " · 平均進場價 —"
          : ` · 平均進場價 ${decimal(experimentStats.averageEntryPrice)}`}
      </small>
    </div>

    <p>{definition.rule}</p>
    <small>{compactParameters(parameters)}</small>
    <small>
      固定 paired cohort：{validation?.samples ?? trades} 筆；狀態 {validation?.status ?? "COLLECTING"}。
      兩組同事件成對開倉，不占五組主策略共享曝險，也不會轉送實單。
    </small>
    <small>
      Runtime：{last?.status ?? "WAITING"}
      {last?.reason ? ` · ${last.reason}` : ""}
      {last?.sourceSide ? ` · ${last.sourceSide}` : ""}
      {last?.score == null ? "" : ` · score ${decimal(last.score, 4)}`}
      {last?.confirmationCount == null ? "" : ` · confirmations ${last.confirmationCount}`}
    </small>
    <small>目前跟隨 R_MICROPRICE 主開關；無獨立實單或資金控制。</small>
  </article>;
}

export default function MicropriceVariantDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [payload, setPayload] = useState<DashboardPayload | null>(null);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;

    const locate = () => {
      const next = document.querySelector<HTMLElement>(
        ".research-forward-panel .research-strategy-grid",
      );
      setTarget(current => current === next ? current : next);
      updateResearchLabels();
    };

    const observer = new MutationObserver(locate);
    observer.observe(document.body, { childList: true, subtree: true });
    locate();

    const load = async () => {
      if (document.visibilityState !== "visible") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(apiUrl("/api/state"), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`dashboard request failed: ${response.status}`);
        const next = await response.json() as DashboardPayload;
        if (active) setPayload(next);
      } catch (error) {
        if (active && !(error instanceof DOMException && error.name === "AbortError")) {
          setPayload(current => current);
        }
      }
    };

    void load();
    const timer = window.setInterval(load, API_REFRESH_MS);
    const visibility = () => {
      locate();
      if (document.visibilityState === "visible") void load();
      else controller?.abort();
    };
    document.addEventListener("visibilitychange", visibility);

    return () => {
      active = false;
      controller?.abort();
      observer.disconnect();
      document.removeEventListener("visibilitychange", visibility);
      window.clearInterval(timer);
    };
  }, []);

  const cards = useMemo(
    () => CARDS.map(definition => (
      <VariantCard key={definition.id} definition={definition} payload={payload} />
    )),
    [payload],
  );

  return target ? createPortal(cards, target) : null;
}
