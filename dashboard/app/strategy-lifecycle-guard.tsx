"use client";

export type StrategyLifecycleWindow = {
  window?: number;
  samples?: number;
  wins?: number;
  losses?: number;
  flat?: number;
  winRatePct?: number | null;
  pnlUsdt?: number;
  costUsdt?: number;
  roiPct?: number | null;
  expectancyUsdt?: number | null;
};

export type StrategyLifecycleDrawdown = {
  lifetimePnlUsdt?: number;
  peakPnlUsdt?: number;
  currentDrawdownUsdt?: number;
  maxDrawdownUsdt?: number;
  maxDrawdownAt?: string | null;
  episodeDepthUsdt?: number;
  reboundFromTroughUsdt?: number;
  recoveryPct?: number | null;
};

export type StrategyLifecycleRecovery = {
  last10Positive?: boolean;
  last20Positive?: boolean;
  expectancyImproving?: boolean;
  drawdownRecovering?: boolean;
  last10PnlUsdt?: number;
  expectancyDelta20Vs50Usdt?: number | null;
  recoveryPct?: number | null;
  reboundFromTroughUsdt?: number;
  positiveSignals?: number;
};

export type StrategyLifecycleRow = {
  strategy?: string;
  active?: boolean;
  status?: "ACTIVE" | "WATCH" | "DEGRADED" | "RECOVERY" | "BUILDING" | "NO_DATA" | string;
  settledMarkets?: number;
  lastSettledAt?: string | null;
  last20?: StrategyLifecycleWindow;
  last50?: StrategyLifecycleWindow;
  drawdown?: StrategyLifecycleDrawdown;
  recoveryEvidence?: StrategyLifecycleRecovery;
  dataSource?: "LIVE" | "PAPER_FALLBACK" | "NO_DATA" | string;
  paperFallbackActive?: boolean;
  paperCollectorEnabled?: boolean | null;
  live?: StrategyLifecycleRow;
  paper?: StrategyLifecycleRow;
};

export type StrategyLifecycleState = {
  version?: string;
  status?: string;
  advisoryOnly?: boolean;
  automaticBlocking?: boolean;
  automaticStakeChanges?: boolean;
  paperFallbackEnabled?: boolean;
  paperStatus?: string;
  paperError?: string | null;
  sampleBasis?: string;
  supportedStrategies?: number;
  activeStrategies?: string[];
  paperFallbackStrategies?: string[];
  paperFallbackCount?: number;
  liveStrategiesWithSamples?: number;
  paperStrategiesWithSamples?: number;
  counts?: Record<string, number>;
  strategies?: StrategyLifecycleRow[];
  updatedAt?: string | null;
};

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "ACTIVE",
  WATCH: "WATCH",
  DEGRADED: "DEGRADED",
  RECOVERY: "RECOVERY",
  BUILDING: "BUILDING",
  NO_DATA: "NO DATA",
};

const STATUS_ORDER: Record<string, number> = {
  DEGRADED: 0,
  WATCH: 1,
  RECOVERY: 2,
  ACTIVE: 3,
  BUILDING: 4,
  NO_DATA: 5,
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
  const evidence = row.recoveryEvidence;
  if (!row.settledMarkets) return "等待 Live / Paper 樣本";
  const parts = [
    `L10 ${money(evidence?.last10PnlUsdt)}`,
    `回復 ${pct(evidence?.recoveryPct)}`,
    `證據 ${evidence?.positiveSignals ?? 0}/4`,
  ];
  if (evidence?.expectancyImproving) parts.push("20 vs 50 ↑");
  return parts.join(" · ");
}

function sourceLabel(row: StrategyLifecycleRow) {
  if (row.dataSource === "LIVE") return "LIVE";
  if (row.dataSource === "PAPER_FALLBACK") return "PAPER FALLBACK";
  return "NO DATA";
}

function sourceDetail(row: StrategyLifecycleRow) {
  const liveSamples = row.live?.settledMarkets ?? 0;
  const paperSamples = row.paper?.settledMarkets ?? 0;
  const collector = row.paperCollectorEnabled === true
    ? "Paper 收集中"
    : row.paperCollectorEnabled === false
      ? "Paper 已停用"
      : "Paper 狀態未知";
  return `Live n=${liveSamples} · Paper n=${paperSamples} · ${collector}`;
}

export default function StrategyLifecycleGuardPanel({ data }: { data?: StrategyLifecycleState | null }) {
  const rows = [...(data?.strategies ?? [])].sort((a, b) => {
    if (Boolean(a.active) !== Boolean(b.active)) return a.active ? -1 : 1;
    const rank = (STATUS_ORDER[String(a.status)] ?? 99) - (STATUS_ORDER[String(b.status)] ?? 99);
    if (rank !== 0) return rank;
    return String(a.strategy ?? "").localeCompare(String(b.strategy ?? ""));
  });
  const counts = data?.counts ?? {};
  const activeCount = rows.filter(row => row.active).length;

  return <section className="live-hourly-guard allow" aria-label="Strategy Lifecycle Guard">
    <div className="live-hourly-guard-head">
      <div>
        <span className="eyebrow">STRATEGY LIFECYCLE GUARD · LIVE + PAPER FALLBACK</span>
        <h3>Strategy Lifecycle Guard</h3>
      </div>
      <strong>{data?.status === "READY" ? "監測中 · 不自動擋單" : "等待統計"}</strong>
    </div>

    <div className="live-hourly-guard-metrics">
      <div><span>實單白名單</span><strong>{data?.supportedStrategies ?? rows.length}</strong><small>全部可選實單策略，各自獨立計算</small></div>
      <div><span>目前生效</span><strong>{activeCount}</strong><small>{(data?.activeStrategies ?? []).join(" · ") || "目前沒有啟用策略"}</small></div>
      <div><span>Paper fallback</span><strong>{data?.paperFallbackCount ?? 0}</strong><small>有 Paper 樣本、但尚無任何已結算 Live 的策略</small></div>
      <div><span>需要注意</span><strong>{(counts.DEGRADED ?? 0) + (counts.WATCH ?? 0)}</strong><small>DEGRADED {counts.DEGRADED ?? 0} · WATCH {counts.WATCH ?? 0} · RECOVERY {counts.RECOVERY ?? 0}</small></div>
    </div>

    <p>
      每個策略優先使用自己的真實已結算 Live 樣本；只有 Live n=0 時才以同策略 Paper 模擬結果作為 Lifecycle 判級。
      Live 與 Paper 的 PnL、勝率、DD 永遠不相加。一般方向策略只讀官方已結算 Paper；PAIR 使用自己的模擬雙腿鎖定 PnL ledger。
    </p>

    {data?.paperStatus === "UNAVAILABLE" ? <p className="negative">Paper fallback 暫時不可用：{data.paperError ?? "simulation.db 尚未就緒"}</p> : null}

    <div className="table-scroll">
      <table>
        <thead><tr><th>策略</th><th>判級來源</th><th>Lifecycle</th><th>Live / Paper</th><th>Last20</th><th>Last50</th><th>DD</th><th>Recovery evidence</th></tr></thead>
        <tbody>
          {rows.length === 0
            ? <tr><td colSpan={8} className="empty">等待 Lifecycle payload。</td></tr>
            : rows.map(row => {
                const status = String(row.status ?? "NO_DATA");
                const dd = row.drawdown;
                const source = sourceLabel(row);
                return <tr key={row.strategy ?? status}>
                  <td><strong>{row.strategy ?? "—"}</strong><small>{row.active ? "目前實單已啟用" : "白名單候選"}</small></td>
                  <td><span className={`trade-status ${source === "LIVE" ? "positive" : ""}`}>{source}</span><small>{row.paperFallbackActive ? "尚無 Live 結算，暫以 Paper 判級" : source === "LIVE" ? "已有 Live 結算，主判級只看 Live" : "尚無可用結果"}</small></td>
                  <td><span className={`trade-status ${statusTone(status)}`}>{STATUS_LABEL[status] ?? status}</span></td>
                  <td>{sourceDetail(row)}<small>{row.lastSettledAt ? `判級樣本最後 ${new Date(row.lastSettledAt).toLocaleString("zh-TW", { hour12: false })}` : "尚無判級樣本"}</small></td>
                  <td className={(row.last20?.pnlUsdt ?? 0) >= 0 ? "positive" : "negative"}>{windowSummary(row.last20)}<small>EV {money(row.last20?.expectancyUsdt)}</small></td>
                  <td className={(row.last50?.pnlUsdt ?? 0) >= 0 ? "positive" : "negative"}>{windowSummary(row.last50)}<small>EV {money(row.last50?.expectancyUsdt)}</small></td>
                  <td className={(dd?.currentDrawdownUsdt ?? 0) < 0 ? "negative" : "positive"}>現在 {money(dd?.currentDrawdownUsdt)}<small>歷史最大 {money(dd?.maxDrawdownUsdt)} · lifetime {money(dd?.lifetimePnlUsdt)}</small></td>
                  <td>{recoveryCopy(row)}<small>rebound {money(row.recoveryEvidence?.reboundFromTroughUsdt)}</small></td>
                </tr>;
              })}
        </tbody>
      </table>
    </div>

    <p>
      判級門檻不變：樣本 &lt;20 為 BUILDING；20–49 筆只用 Last20 判斷 WATCH；50 筆以上若 Last20、Last50 同負為 DEGRADED，
      Last20 轉正但 Last50 仍負為 RECOVERY，Last20 負而 Last50 正為 WATCH，兩窗皆非負才為 ACTIVE。Paper fallback 仍只提供決策證據，完全不進入實單下單 hot path。
    </p>
  </section>;
}
