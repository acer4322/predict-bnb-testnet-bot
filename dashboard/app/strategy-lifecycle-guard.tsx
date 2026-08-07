"use client";

export type StrategyLifecycleWindow = {
  samples?: number; wins?: number; losses?: number; flat?: number;
  winRatePct?: number | null; pnlUsdt?: number; costUsdt?: number;
  roiPct?: number | null; expectancyUsdt?: number | null;
};
export type StrategyLifecycleDrawdown = {
  lifetimePnlUsdt?: number; peakPnlUsdt?: number;
  currentDrawdownUsdt?: number; maxDrawdownUsdt?: number;
  maxDrawdownAt?: string | null; episodeDepthUsdt?: number;
  reboundFromTroughUsdt?: number; recoveryPct?: number | null;
};
export type StrategyLifecycleRecovery = {
  last10Positive?: boolean; last20Positive?: boolean;
  expectancyImproving?: boolean; drawdownRecovering?: boolean;
  last10PnlUsdt?: number; expectancyDelta20Vs50Usdt?: number | null;
  recoveryPct?: number | null; reboundFromTroughUsdt?: number;
  positiveSignals?: number;
};
export type StrategyLifecycleRow = {
  strategy?: string; active?: boolean; status?: string;
  settledMarkets?: number; lastSettledAt?: string | null;
  last20?: StrategyLifecycleWindow; last50?: StrategyLifecycleWindow;
  drawdown?: StrategyLifecycleDrawdown;
  recoveryEvidence?: StrategyLifecycleRecovery;
};
export type StrategyLifecycleState = {
  version?: string; status?: string; advisoryOnly?: boolean;
  automaticBlocking?: boolean; automaticStakeChanges?: boolean;
  sampleBasis?: string; supportedStrategies?: number;
  activeStrategies?: string[]; counts?: Record<string, number>;
  strategies?: StrategyLifecycleRow[]; updatedAt?: string | null;
};

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "ACTIVE", WATCH: "WATCH", DEGRADED: "DEGRADED",
  RECOVERY: "RECOVERY", BUILDING: "BUILDING", NO_DATA: "NO DATA",
};
const STATUS_ORDER: Record<string, number> = {
  DEGRADED: 0, WATCH: 1, RECOVERY: 2, ACTIVE: 3, BUILDING: 4, NO_DATA: 5,
};

function finite(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function money(value: number | null | undefined) {
  const safe = finite(value);
  if (safe == null) return "—";
  return `${safe >= 0 ? "+" : "−"}$${Math.abs(safe).toFixed(2)}`;
}
function pct(value: number | null | undefined, digits = 1) {
  const safe = finite(value);
  return safe == null ? "—" : `${safe.toFixed(digits)}%`;
}
function windowSummary(value?: StrategyLifecycleWindow) {
  if (!value?.samples) return "n=0 · —";
  return `${money(value.pnlUsdt)} · WR ${pct(value.winRatePct)} · ROI ${pct(value.roiPct)}`;
}
function statusTone(status?: string) {
  if (status === "ACTIVE" || status === "RECOVERY") return "positive";
  if (status === "DEGRADED") return "negative";
  return "";
}
function recoveryCopy(row: StrategyLifecycleRow) {
  if (!row.settledMarkets) return "等待真實已結算樣本";
  const evidence = row.recoveryEvidence;
  const parts = [
    `L10 ${money(evidence?.last10PnlUsdt)}`,
    `回復 ${pct(evidence?.recoveryPct)}`,
    `證據 ${evidence?.positiveSignals ?? 0}/4`,
  ];
  if (evidence?.expectancyImproving) parts.push("20 vs 50 ↑");
  return parts.join(" · ");
}

export default function StrategyLifecycleGuardPanel({ data }: { data?: StrategyLifecycleState | null }) {
  const rows = [...(data?.strategies ?? [])].sort((a, b) => {
    if (Boolean(a.active) !== Boolean(b.active)) return a.active ? -1 : 1;
    const rank = (STATUS_ORDER[String(a.status)] ?? 99) - (STATUS_ORDER[String(b.status)] ?? 99);
    return rank !== 0 ? rank : String(a.strategy ?? "").localeCompare(String(b.strategy ?? ""));
  });
  const counts = data?.counts ?? {};
  const activeCount = rows.filter(row => row.active).length;

  return <section className="live-hourly-guard allow" aria-label="Strategy Lifecycle Guard">
    <div className="live-hourly-guard-head">
      <div><span className="eyebrow">STRATEGY LIFECYCLE GUARD · REAL LIVE SETTLEMENTS</span><h3>Strategy Lifecycle Guard</h3></div>
      <strong>{data?.status === "READY" ? "監測中 · 不自動擋單" : "等待實單統計"}</strong>
    </div>
    <div className="live-hourly-guard-metrics">
      <div><span>實單白名單</span><strong>{data?.supportedStrategies ?? rows.length}</strong><small>全部可選實單策略，各自獨立計算</small></div>
      <div><span>目前生效</span><strong>{activeCount}</strong><small>{(data?.activeStrategies ?? []).join(" · ") || "目前沒有啟用策略"}</small></div>
      <div><span>需要注意</span><strong>{(counts.DEGRADED ?? 0) + (counts.WATCH ?? 0)}</strong><small>DEGRADED {counts.DEGRADED ?? 0} · WATCH {counts.WATCH ?? 0} · RECOVERY {counts.RECOVERY ?? 0}</small></div>
    </div>
    <p>V1 只提供決策證據，不自動暫停、不自動換策略、也不修改 stake。Last20／Last50 只使用該策略自己的真實已結算實單；CONFIRM_ADD 子單與 PAIR 雙腿先合併成同一策略、同一市場結果，避免多腿執行把樣本數灌大。</p>
    <div className="table-scroll">
      <table>
        <thead><tr><th>策略</th><th>Lifecycle</th><th>樣本</th><th>Last20</th><th>Last50</th><th>DD</th><th>Recovery evidence</th></tr></thead>
        <tbody>
          {rows.length === 0 ? <tr><td colSpan={7} className="empty">等待實單 Lifecycle payload。</td></tr> : rows.map(row => {
            const status = String(row.status ?? "NO_DATA");
            const dd = row.drawdown;
            return <tr key={row.strategy ?? status}>
              <td><strong>{row.strategy ?? "—"}</strong><small>{row.active ? "目前實單已啟用" : "白名單候選"}</small></td>
              <td><span className={`trade-status ${statusTone(status)}`}>{STATUS_LABEL[status] ?? status}</span></td>
              <td>{row.settledMarkets ?? 0}<small>{row.lastSettledAt ? `最後 ${new Date(row.lastSettledAt).toLocaleString("zh-TW", { hour12: false })}` : "尚無結算"}</small></td>
              <td className={(row.last20?.pnlUsdt ?? 0) >= 0 ? "positive" : "negative"}>{windowSummary(row.last20)}<small>EV {money(row.last20?.expectancyUsdt)}</small></td>
              <td className={(row.last50?.pnlUsdt ?? 0) >= 0 ? "positive" : "negative"}>{windowSummary(row.last50)}<small>EV {money(row.last50?.expectancyUsdt)}</small></td>
              <td className={(dd?.currentDrawdownUsdt ?? 0) < 0 ? "negative" : "positive"}>現在 {money(dd?.currentDrawdownUsdt)}<small>歷史最大 {money(dd?.maxDrawdownUsdt)} · lifetime {money(dd?.lifetimePnlUsdt)}</small></td>
              <td>{recoveryCopy(row)}<small>rebound {money(row.recoveryEvidence?.reboundFromTroughUsdt)}</small></td>
            </tr>;
          })}
        </tbody>
      </table>
    </div>
    <p>判級：樣本 &lt;20 為 BUILDING；20–49 筆只用 Last20 判斷 WATCH；50 筆以上若 Last20、Last50 同負為 DEGRADED，Last20 轉正但 Last50 仍負為 RECOVERY，Last20 負而 Last50 正為 WATCH，兩窗皆非負才為 ACTIVE。這些狀態目前完全不進入下單 hot path。</p>
  </section>;
}
