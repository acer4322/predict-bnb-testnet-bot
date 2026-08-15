"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const API_REFRESH_MS = 1_000;

type LifecycleEpisode = {
  id?: number;
  marketId?: number;
  side?: string;
  status?: string;
  startedAt?: string | null;
  confirmedAt?: string | null;
  filledAt?: string | null;
  endedAt?: string | null;
  endReason?: string | null;
  startScore?: number | null;
  peakAbsScore?: number | null;
  endScore?: number | null;
  confirmations?: number;
  eventCount?: number;
  signalDurationMs?: number | null;
  pendingDurationMs?: number | null;
  positionDurationMs?: number | null;
  entryPrice?: number | null;
  exitPrice?: number | null;
  pnl?: number | null;
  tradeStatus?: string | null;
  exitRequestedReason?: string | null;
};

type LifecycleRuntime = {
  active?: {
    id?: number;
    market_id?: number;
    side?: string;
    status?: string;
    last_score?: number | null;
    peak_abs_score?: number | null;
    confirmations?: number;
    order_limit_price?: number | null;
    entry_price?: number | null;
    signalAgeMs?: number | null;
    pendingAgeMs?: number | null;
    positionAgeMs?: number | null;
    exit_requested_reason?: string | null;
  } | null;
  lastDecision?: {
    status?: string;
    reason?: string;
    detail?: string;
    score?: number | null;
    signalDurationMs?: number | null;
    positionDurationMs?: number | null;
  } | null;
  rules?: Record<string, unknown>;
  counters?: Record<string, number>;
};

type LifecycleExperiment = {
  version?: string;
  strategy?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  episodes?: number;
  activeEpisodes?: number;
  confirmedEpisodes?: number;
  filledEpisodes?: number;
  cancelledEpisodes?: number;
  exitedEpisodes?: number;
  heldEpisodes?: number;
  realizedPnl?: number;
  duration?: {
    averageMs?: number | null;
    medianMs?: number | null;
    p90Ms?: number | null;
    minimumMs?: number | null;
    maximumMs?: number | null;
  };
  reasons?: Record<string, {
    count?: number;
    averageDurationMs?: number | null;
    medianDurationMs?: number | null;
  }>;
  recentEpisodes?: LifecycleEpisode[];
  runtime?: LifecycleRuntime;
};

type DashboardPayload = {
  researchForward?: {
    micropriceSignalLifecycle?: LifecycleExperiment;
  } | null;
};

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function number(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function ms(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value >= 60_000) return `${(value / 60_000).toFixed(2)} min`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)} s`;
  return `${value.toFixed(0)} ms`;
}

function price(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(3);
}

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `$${value.toFixed(3)}`;
}

function fmtTime(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleTimeString("zh-TW", { hour12: false });
}

function reasonLabel(reason: string | null | undefined) {
  const labels: Record<string, string> = {
    EDGE_LOST: "優勢消失",
    SIGNAL_REVERSED: "訊號逆轉",
    ORDER_NOT_FILLED: "限價未成交",
    DATA_INVALIDATED: "盤口資料失效",
    MARKET_ROLLOVER_CANCELLED: "換盤撤單",
    MARKET_ROLLOVER_HELD: "持有至結算",
  };
  return labels[String(reason ?? "")] ?? reason ?? "—";
}

function updateResearchLabels() {
  const replacements = new Map<string, string>([
    ["FORWARD PAPER · FIVE PRIMARY + SIXTEEN SHADOWS", "FORWARD PAPER · FIVE PRIMARY + SEVENTEEN SHADOWS"],
    ["FIVE PRIMARY + SIXTEEN SHADOWS · PAPER", "FIVE PRIMARY + SEVENTEEN SHADOWS · PAPER"],
    ["五組主策略＋十六組獨立 Shadow", "五組主策略＋十七組獨立 Shadow"],
    ["五組主策略＋十六組 Shadow", "五組主策略＋十七組 Shadow"],
    ["5 主策略＋16 Shadow", "5 主策略＋17 Shadow"],
  ]);
  document.querySelectorAll<HTMLElement>("span, h2, h3, p, strong").forEach(node => {
    if (node.childElementCount > 0) return;
    const current = node.textContent?.trim() ?? "";
    const replacement = replacements.get(current);
    if (replacement) node.textContent = replacement;
  });
}

function LifecyclePanel({ experiment }: { experiment?: LifecycleExperiment }) {
  const active = experiment?.runtime?.active;
  const last = experiment?.runtime?.lastDecision;
  const recent = experiment?.recentEpisodes?.slice(0, 8) ?? [];
  const reasons = experiment?.reasons ?? {};
  const edgeLost = reasons.EDGE_LOST;
  const reversed = reasons.SIGNAL_REVERSED;
  const status = active?.status ?? last?.status ?? "WAITING";
  const statusClass = /EXIT|FILLED|OPEN/.test(status)
    ? "positive"
    : /CANCEL|REVERSE|BLOCK|INVALID/.test(status)
      ? "negative"
      : "";

  return <article
    className="m-exit-card cyan"
    data-microprice-lifecycle="R_MICROPRICE_LIFECYCLE"
    style={{ gridColumn: "1 / -1" }}
  >
    <div className="m-exit-card-head">
      <div>
        <span className="eyebrow">R_MICROPRICE_LIFECYCLE · SIGNAL STATE MACHINE</span>
        <h3>Microprice 優勢消失／逆轉撤單測試</h3>
      </div>
      <div className="m-exit-card-actions">
        <span className="m-exit-id">PAPER ONLY</span>
      </div>
    </div>

    <p>
      180 秒附近出現強訊號後先記錄生命週期，連續 3 次且至少 300 ms 才建立延遲限價意圖；
      未成交前優勢消失或反向就撤單，成交後則以可見買一全數模擬退出。實單完全不受影響。
    </p>

    <div className="m-exit-primary-stats">
      <div><span>目前狀態</span><strong className={statusClass}>{status}</strong></div>
      <div><span>方向／分數</span><strong>{active?.side ?? "—"} · {number(active?.last_score, 4)}</strong></div>
      <div><span>訊號已持續</span><strong>{ms(active?.signalAgeMs)}</strong></div>
    </div>

    <div className="m-exit-fill-grid">
      <div><span>生命週期樣本</span><strong>{experiment?.episodes ?? 0}</strong><small>確認 {experiment?.confirmedEpisodes ?? 0} · 成交 {experiment?.filledEpisodes ?? 0}</small></div>
      <div><span>未成交撤單</span><strong>{experiment?.cancelledEpisodes ?? 0}</strong><small>消失 {edgeLost?.count ?? 0} · 逆轉 {reversed?.count ?? 0}</small></div>
      <div><span>成交後退出</span><strong>{experiment?.exitedEpisodes ?? 0}</strong><small>持有至結算 {experiment?.heldEpisodes ?? 0}</small></div>
      <div><span>平均／中位壽命</span><strong>{ms(experiment?.duration?.averageMs)}</strong><small>median {ms(experiment?.duration?.medianMs)}</small></div>
      <div><span>P90／最長壽命</span><strong>{ms(experiment?.duration?.p90Ms)}</strong><small>max {ms(experiment?.duration?.maximumMs)}</small></div>
      <div><span>已實現 PnL</span><strong className={(experiment?.realizedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(experiment?.realizedPnl)}</strong><small>只計退出或官方結算後資料</small></div>
    </div>

    <div className={`continuous-calibration-state ${active ? "ready" : "warmup"}`}>
      <span>{active ? "ACTIVE SIGNAL" : "WAITING FOR NEXT 180S WINDOW"}</span>
      <strong>
        {active
          ? `#${active.market_id ?? "—"} · ${active.side ?? "—"} · ${active.confirmations ?? 0} confirmations`
          : `${last?.status ?? "WAITING"}${last?.reason ? ` · ${reasonLabel(last.reason)}` : ""}`}
      </strong>
      <small>
        {active?.status === "PENDING" ? `限價 ${price(active.order_limit_price)} · pending ${ms(active.pendingAgeMs)}` : ""}
        {active?.status === "OPEN" ? `成交 ${price(active.entry_price)} · position ${ms(active.positionAgeMs)}` : ""}
        {active?.exit_requested_reason ? ` · 退出等待 ${reasonLabel(active.exit_requested_reason)}` : ""}
        {!active && last?.detail ? ` · ${last.detail}` : ""}
      </small>
    </div>

    <div className="table-scroll" style={{ marginTop: 14 }}>
      <table>
        <thead><tr><th>開始</th><th>市場</th><th>方向</th><th>結果</th><th>訊號壽命</th><th>Pending</th><th>持倉</th><th>分數</th><th>進／出</th><th>PnL</th></tr></thead>
        <tbody>
          {recent.length === 0
            ? <tr><td colSpan={10} className="empty">等待新的 Microprice 優勢訊號。</td></tr>
            : recent.map(row => <tr key={row.id}>
              <td>{fmtTime(row.startedAt)}</td>
              <td>#{row.marketId ?? "—"}</td>
              <td>{row.side ?? "—"}</td>
              <td><strong>{row.status ?? "—"}</strong><small>{reasonLabel(row.endReason ?? row.exitRequestedReason)}</small></td>
              <td>{ms(row.signalDurationMs)}</td>
              <td>{ms(row.pendingDurationMs)}</td>
              <td>{ms(row.positionDurationMs)}</td>
              <td>{number(row.startScore, 3)} → {number(row.endScore, 3)}<small>peak {number(row.peakAbsScore, 3)}</small></td>
              <td>{price(row.entryPrice)} / {price(row.exitPrice)}</td>
              <td className={(row.pnl ?? 0) >= 0 ? "positive" : "negative"}>{money(row.pnl)}</td>
            </tr>)}
        </tbody>
      </table>
    </div>

    <small>
      進場門檻 0.20、優勢消失門檻 0.10、反向門檻 0.20；限價意圖至少等待 250 ms，
      1.5 秒仍無法成交就撤銷。退出必須有足額可見 bid，深度不足時保持 EXIT_PENDING 並在下一筆新鮮盤口重試。
    </small>
  </article>;
}

export default function MicropriceSignalLifecycleDashboard() {
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

  const panel = useMemo(
    () => <LifecyclePanel experiment={payload?.researchForward?.micropriceSignalLifecycle} />,
    [payload],
  );

  return target ? createPortal(panel, target) : null;
}
