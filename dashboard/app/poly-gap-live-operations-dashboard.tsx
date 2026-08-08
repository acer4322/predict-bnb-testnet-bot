"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

type RoundRow = {
  id?: number;
  market_id?: number;
  round_no?: number;
  side?: string;
  state?: string;
  stake_usdt?: number;
  entry_signal_at_ms?: number | null;
  entry_quote_average?: number | null;
  entry_order_id?: string | null;
  exit_quote_average?: number | null;
  exit_order_id?: string | null;
  pnl_usdt?: number | null;
  close_reason?: string | null;
  error_kind?: string | null;
  error_message?: string | null;
};

type EventRow = {
  id?: number;
  at_ms?: number;
  level?: string;
  event_type?: string;
  market_id?: number | null;
  round_id?: number | null;
  message?: string;
};

type ExecutionMetric = {
  attempts?: number;
  submitted?: number;
  confirmed?: number;
  successRate?: number | null;
  definition?: string;
};

type State = {
  version?: string;
  status?: string;
  masterEnabled?: boolean;
  settings?: { runtimeEnabled?: boolean };
  currentStatus?: {
    status?: string;
    runtimeEnabled?: boolean;
    masterEnabled?: boolean;
    marketId?: number | null;
    activeRoundId?: number | null;
    activeRoundNo?: number | null;
    activeRoundState?: string | null;
    generatedAtMs?: number | null;
  };
  lastErrorDetail?: {
    atMs?: number | null;
    source?: string;
    kind?: string;
    message?: string;
    marketId?: number | null;
    roundId?: number | null;
    roundNo?: number | null;
    state?: string;
  } | null;
  lastError?: string | null;
  entryExecution?: ExecutionMetric & {
    quoteRejected?: number;
    edgeGoneAfterQuote?: number;
    placeRejected?: number;
    ambiguous?: number;
  };
  exitExecution?: ExecutionMetric & {
    quoteRejectedEvents?: number;
    invalidQuoteEvents?: number;
    placeRejectedEvents?: number;
    notFlatRetryEvents?: number;
    ambiguousCurrent?: number;
  };
  executionTuning?: {
    entrySlippageBps?: number;
    exitSlippageBps?: number;
    entryPositionSyncTimeoutMs?: number;
    exitPositionSyncTimeoutMs?: number;
    entryEdgeRuleChanged?: boolean;
    minimumExecutableEntryEdge?: number;
  };
  scalpCycle?: {
    repeatSameMarketRounds?: boolean;
    definiteEntryFailureRetries?: boolean;
    entryRetryCooldownMs?: number;
    placeRejectRetryCooldownMs?: number;
    noDepthRetryCooldownMs?: number;
    rateLimitRetryCooldownMs?: number;
    balanceRetryCooldownMs?: number;
    retryRemainingMs?: number;
    lastRearmAtMs?: number | null;
    lastRearmReason?: string | null;
    rearmImmediatelyAfterConfirmedFlat?: boolean;
    ambiguousPlacementStillHaltsMarket?: boolean;
  };
  recentRounds?: RoundRow[];
  recentEvents?: EventRow[];
};

type Payload = { ok?: boolean; state?: State; error?: string };

function timeText(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "—";
  const date = new Date(n);
  return `${date.toLocaleTimeString("zh-TW", { hour12: false })}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function rate(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
}

function money(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : "−"}$${Math.abs(n).toFixed(4)}`;
}

function shortId(value: unknown) {
  const text = String(value ?? "");
  if (!text) return "—";
  return text.length > 18 ? `${text.slice(0, 8)}…${text.slice(-6)}` : text;
}

function bps(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n} bps (${(n / 100).toFixed(2)}%)` : "—";
}

export default function PolyGapLiveOperationsDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [state, setState] = useState<State | null>(null);
  const [fetchError, setFetchError] = useState("");

  useEffect(() => {
    const locate = () => {
      const node = document.querySelector<HTMLElement>(".live-rules-editor");
      setTarget(current => current === node ? current : node);
    };
    locate();
    const timer = window.setInterval(locate, 750);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    const load = async () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch("/api/poly-gap-live", { cache: "no-store", signal: controller.signal });
        const body = await response.json() as Payload;
        if (!response.ok || !body.ok || !body.state) throw new Error(body.error ?? `HTTP ${response.status}`);
        if (!alive) return;
        setState(body.state);
        setFetchError("");
      } catch (caught) {
        if (!alive || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setFetchError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
    const timer = window.setInterval(load, 1000);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, []);

  if (!target) return null;

  const current = state?.currentStatus;
  const lastError = state?.lastErrorDetail;
  const entry = state?.entryExecution;
  const exit = state?.exitExecution;
  const tuning = state?.executionTuning;
  const cycle = state?.scalpCycle;
  const recentRounds = (state?.recentRounds ?? []).slice(0, 12);
  const recentEvents = (state?.recentEvents ?? []).slice(0, 12);
  const currentStatus = current?.status ?? state?.status ?? "OFFLINE";
  const statusHealthy = [
    "ARMED_WAITING_GAP",
    "MANAGING_POSITION",
    "ENTRY_SYNC",
    "EXIT_SYNC",
    "ENTRY_RETRY_COOLDOWN",
    "GAP_RETRY_COOLDOWN",
    "GAP_WAITING_DEPTH_RETRY",
    "PAUSED",
    "MASTER_DISABLED",
  ].includes(currentStatus);

  return createPortal(
    <section aria-label="R_POLY_GAP_SCALP 專用實單執行紀錄" style={{
      gridColumn: "1 / -1",
      marginTop: 14,
      padding: 16,
      border: "1px solid rgba(126,145,178,.28)",
      borderRadius: 16,
      background: "rgba(7,13,22,.76)",
      display: "grid",
      gap: 14,
    }}>
      <style>{`
        .poly-gap-ops-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
        .poly-gap-ops-card{padding:12px;border-radius:11px;background:rgba(126,145,178,.07);border:1px solid rgba(126,145,178,.12)}
        .poly-gap-ops-card span,.poly-gap-ops-card small{display:block;color:#91a0bb}.poly-gap-ops-card strong{display:block;margin-top:5px}
        .poly-gap-ops-table{width:100%;border-collapse:collapse;font-size:11px}.poly-gap-ops-table th,.poly-gap-ops-table td{padding:7px 8px;border-bottom:1px solid rgba(126,145,178,.12);text-align:left;vertical-align:top}.poly-gap-ops-table th{color:#91a0bb;font-weight:600}.poly-gap-ops-scroll{overflow:auto;max-width:100%}
        .poly-gap-ops-message{max-width:520px;white-space:normal;word-break:break-word}
      `}</style>

      <div>
        <span className="eyebrow">DEDICATED LIVE EXECUTION · OPERATIONS</span>
        <h3 style={{ margin: "4px 0 0" }}>R_POLY_GAP_SCALP 專用實單狀態與紀錄</h3>
        <small>目前狀況與歷史錯誤分開顯示；V7 允許同市場反覆 scalp，確定失敗只短暫 cooldown，確認 FLAT 後立即 re-arm。</small>
      </div>

      <div className="poly-gap-ops-grid">
        <div className="poly-gap-ops-card">
          <span>當前狀況</span>
          <strong style={{ color: statusHealthy ? "#8ce6ad" : "#ffbd87" }}>{currentStatus}</strong>
          <small>Market #{current?.marketId ?? "—"} · Round {current?.activeRoundNo ?? "—"} · {current?.activeRoundState ?? "FLAT"}</small>
          <small>Master {current?.masterEnabled ?? state?.masterEnabled ? "ON" : "OFF"} · Runtime {current?.runtimeEnabled ?? state?.settings?.runtimeEnabled ? "ON" : "OFF"}</small>
        </div>

        <div className="poly-gap-ops-card">
          <span>高頻循環 / Re-arm</span>
          <strong>{cycle?.repeatSameMarketRounds ? "同市場多輪已啟用" : "等待 V7"}</strong>
          <small>Retry 倒數 {Number(cycle?.retryRemainingMs ?? 0).toFixed(0)} ms · 一般失敗 {cycle?.entryRetryCooldownMs ?? "—"} ms · Place 拒絕 {cycle?.placeRejectRetryCooldownMs ?? "—"} ms</small>
          <small>最後 re-arm：{cycle?.lastRearmReason ?? "—"}{cycle?.lastRearmAtMs ? ` · ${timeText(cycle.lastRearmAtMs)}` : ""}</small>
        </div>

        <div className="poly-gap-ops-card">
          <span>上次錯誤訊息</span>
          <strong style={{ color: lastError ? "#ff9f9f" : "#8ce6ad" }}>{lastError?.kind ?? "沒有已記錄錯誤"}</strong>
          <small>{lastError?.message ?? state?.lastError ?? "—"}</small>
          {lastError && <small>{timeText(lastError.atMs)} · Market #{lastError.marketId ?? "—"} · Round {lastError.roundNo ?? lastError.roundId ?? "—"}</small>}
        </div>

        <div className="poly-gap-ops-card">
          <span>成功開單率</span>
          <strong>{rate(entry?.successRate)}</strong>
          <small>確認開倉 {entry?.confirmed ?? 0} / 嘗試 {entry?.attempts ?? 0} · 已送 BUY {entry?.submitted ?? 0}</small>
          <small>Quote 拒絕 {entry?.quoteRejected ?? 0} · Quote 後 edge 消失 {entry?.edgeGoneAfterQuote ?? 0} · Place 拒絕 {entry?.placeRejected ?? 0} · Ambiguous {entry?.ambiguous ?? 0}</small>
        </div>

        <div className="poly-gap-ops-card">
          <span>成功出場率</span>
          <strong>{rate(exit?.successRate)}</strong>
          <small>確認 FLAT {exit?.confirmed ?? 0} / 有退出訊號 {exit?.attempts ?? 0} · 已送 SELL {exit?.submitted ?? 0}</small>
          <small>Quote 拒絕 {exit?.quoteRejectedEvents ?? 0} · 無效 Quote {exit?.invalidQuoteEvents ?? 0} · Place 拒絕 {exit?.placeRejectedEvents ?? 0} · 未平倉重試 {exit?.notFlatRetryEvents ?? 0}</small>
        </div>

        <div className="poly-gap-ops-card">
          <span>執行容忍度</span>
          <strong>ENTRY {bps(tuning?.entrySlippageBps)} · EXIT {bps(tuning?.exitSlippageBps)}</strong>
          <small>ENTRY executable edge ≥ {rate(tuning?.minimumExecutableEntryEdge)}，規則未放寬。</small>
          <small>Position sync：ENTRY {tuning?.entryPositionSyncTimeoutMs ?? "—"} ms · EXIT {tuning?.exitPositionSyncTimeoutMs ?? "—"} ms</small>
        </div>
      </div>

      <div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", marginBottom: 7 }}>
          <strong>開單／出場紀錄</strong>
          <small>ENTRY：ENTRY_CONFIRMED / attempts；EXIT：確認 FLAT / 有退出訊號的 rounds</small>
        </div>
        <div className="poly-gap-ops-scroll">
          <table className="poly-gap-ops-table">
            <thead><tr><th>時間</th><th>Market / Round</th><th>方向</th><th>狀態</th><th>金額</th><th>Entry / Exit quote</th><th>BUY / SELL Order</th><th>PnL / 原因</th></tr></thead>
            <tbody>
              {recentRounds.length === 0 && <tr><td colSpan={8}>尚無專用實單 round</td></tr>}
              {recentRounds.map(row => <tr key={row.id ?? `${row.market_id}:${row.round_no}`}>
                <td>{timeText(row.entry_signal_at_ms)}</td>
                <td>#{row.market_id ?? "—"} / R{row.round_no ?? "—"}</td>
                <td>{row.side ?? "—"}</td>
                <td>{row.state ?? "—"}</td>
                <td>{Number(row.stake_usdt ?? 0).toFixed(2)} USDT</td>
                <td>{row.entry_quote_average == null ? "—" : Number(row.entry_quote_average).toFixed(4)} / {row.exit_quote_average == null ? "—" : Number(row.exit_quote_average).toFixed(4)}</td>
                <td>{shortId(row.entry_order_id)} / {shortId(row.exit_order_id)}</td>
                <td className="poly-gap-ops-message">{row.pnl_usdt == null ? (row.error_kind ?? row.close_reason ?? "—") : money(row.pnl_usdt)}{row.error_message ? ` · ${row.error_message}` : ""}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <strong>最近訊息</strong>
        <div className="poly-gap-ops-scroll" style={{ marginTop: 7 }}>
          <table className="poly-gap-ops-table">
            <thead><tr><th>時間</th><th>Level</th><th>事件</th><th>Market</th><th>Round</th><th>訊息</th></tr></thead>
            <tbody>
              {recentEvents.length === 0 && <tr><td colSpan={6}>尚無執行訊息</td></tr>}
              {recentEvents.map(row => <tr key={row.id ?? `${row.at_ms}:${row.event_type}`}>
                <td>{timeText(row.at_ms)}</td>
                <td style={{ color: String(row.level).toUpperCase() === "ERROR" ? "#ff9f9f" : undefined }}>{row.level ?? "—"}</td>
                <td>{row.event_type ?? "—"}</td>
                <td>#{row.market_id ?? "—"}</td>
                <td>{row.round_id ?? "—"}</td>
                <td className="poly-gap-ops-message">{row.message ?? "—"}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
      </div>

      {fetchError && <small style={{ color: "#ff9f9f" }}>8769 狀態讀取錯誤：{fetchError}</small>}
      <small style={{ color: "#91a0bb" }}>版本 {state?.version ?? "—"}。`ENTRY_REARM_SCHEDULED` 代表確定失敗後等待下一次嘗試；`SCALP_REARMED_FLAT` 代表賣出確認 FLAT 後已重新武裝。</small>
    </section>,
    target,
  );
}
