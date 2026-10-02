"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type Episode = {
  id?: number;
  marketId?: number;
  side?: string;
  status?: string;
  startedAt?: string | null;
  endReason?: string | null;
  startScore?: number | null;
  peakAbsScore?: number | null;
  endScore?: number | null;
  signalDurationMs?: number | null;
  pendingDurationMs?: number | null;
  positionDurationMs?: number | null;
  entryPrice?: number | null;
  exitPrice?: number | null;
  pnl?: number | null;
  exitRequestedReason?: string | null;
};

type Experiment = {
  status?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  episodes?: number;
  activeEpisodes?: number;
  confirmedEpisodes?: number;
  filledEpisodes?: number;
  cancelledEpisodes?: number;
  exitedEpisodes?: number;
  settlementPendingEpisodes?: number;
  settledWins?: number;
  settledLosses?: number;
  realizedPnl?: number;
  duration?: {
    averageMs?: number | null;
    medianMs?: number | null;
    p90Ms?: number | null;
    maximumMs?: number | null;
  };
  reasons?: Record<string, { count?: number }>;
  recentEpisodes?: Episode[];
  runtime?: {
    active?: {
      market_id?: number;
      side?: string;
      status?: string;
      last_score?: number | null;
      peak_abs_score?: number | null;
      confirmations?: number;
      signalAgeMs?: number | null;
      pendingAgeMs?: number | null;
      positionAgeMs?: number | null;
      order_limit_price?: number | null;
      entry_price?: number | null;
      exit_requested_reason?: string | null;
    } | null;
    lastDecision?: {
      status?: string;
      reason?: string;
      detail?: string;
    } | null;
    lastError?: string | null;
    rules?: Record<string, unknown>;
  };
  error?: string | null;
};

type ApiPayload = {
  experiment?: Experiment | null;
  error?: string | null;
  fetchedAt?: string;
};

const labels: Record<string, string> = {
  EDGE_LOST: "優勢消失",
  SIGNAL_REVERSED: "訊號逆轉",
  ORDER_NOT_FILLED: "限價未成交",
  DATA_INVALIDATED: "盤口資料失效",
  MARKET_ROLLOVER_CANCELLED: "換盤撤單",
  MARKET_ROLLOVER_HELD: "持有至結算",
};

function duration(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value >= 60_000) return `${(value / 60_000).toFixed(2)} min`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)} s`;
  return `${value.toFixed(0)} ms`;
}

function decimal(value: number | null | undefined, digits = 3) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `$${value.toFixed(3)}`;
}

function timeLabel(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("zh-TW", { hour12: false });
}

function reason(value: string | null | undefined) {
  const normalized = String(value ?? "");
  return labels[normalized] ?? (normalized || "—");
}

export default function MicropriceLifecyclePage() {
  const [payload, setPayload] = useState<ApiPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    let controller: AbortController | null = null;
    const load = async () => {
      if (document.visibilityState !== "visible") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch("/api/microprice-lifecycle", {
          cache: "no-store",
          signal: controller.signal,
        });
        const next = await response.json() as ApiPayload;
        if (mounted) setPayload(next);
      } catch (error) {
        if (mounted && !(error instanceof DOMException && error.name === "AbortError")) {
          setPayload({
            experiment: null,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      } finally {
        if (mounted) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(load, 1_000);
    const visibility = () => {
      if (document.visibilityState === "visible") void load();
      else controller?.abort();
    };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      mounted = false;
      controller?.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);

  const experiment = payload?.experiment ?? null;
  const active = experiment?.runtime?.active;
  const last = experiment?.runtime?.lastDecision;
  const recent = experiment?.recentEpisodes?.slice(0, 12) ?? [];
  const error = payload?.error ?? experiment?.runtime?.lastError ?? experiment?.error;
  const status = active?.status ?? last?.status ?? experiment?.status ?? "WAITING";
  const statusTone = /OPEN|FILLED|EXITED|SETTLED_WIN|LIVE/.test(status)
    ? "good"
    : /ERROR|DEGRADED|BLOCK|CANCEL|LOSS|REVERSE/.test(status)
      ? "bad"
      : "neutral";
  const rules = Object.entries(experiment?.runtime?.rules ?? {});

  return <main className="life-page">
    <header>
      <div>
        <span className="eyebrow">ISOLATED PAPER SIDECAR · FAIL OPEN</span>
        <h1>Microprice 訊號生命週期</h1>
        <p>記錄優勢出現、確認、模擬掛單、成交、消失與逆轉。此頁或旁路故障時，不會影響主 Dashboard、實單或主狀態 API。</p>
      </div>
      <div className="actions">
        <span className={`status ${statusTone}`}>{loading ? "LOADING" : status}</span>
        <Link href="/">返回主監控</Link>
      </div>
    </header>

    {error && <section className="error"><strong>隔離模組目前不可用</strong><span>{error}</span><small>主 Dashboard 不受影響；此頁每秒自行重試。</small></section>}

    <section className="hero grid4">
      <article><span>目前方向／分數</span><strong>{active?.side ?? "—"} · {decimal(active?.last_score, 4)}</strong><small>peak {decimal(active?.peak_abs_score, 4)}</small></article>
      <article><span>訊號持續時間</span><strong>{duration(active?.signalAgeMs)}</strong><small>確認 {active?.confirmations ?? 0} 次</small></article>
      <article><span>掛單／持倉時間</span><strong>{active?.status === "PENDING" ? duration(active.pendingAgeMs) : duration(active?.positionAgeMs)}</strong><small>{active?.status === "PENDING" ? `limit ${decimal(active.order_limit_price)}` : `entry ${decimal(active?.entry_price)}`}</small></article>
      <article><span>已實現 PnL</span><strong className={(experiment?.realizedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(experiment?.realizedPnl)}</strong><small>只計紙上退出／官方結算</small></article>
    </section>

    <section className="summary grid6">
      <article><span>生命週期</span><strong>{experiment?.episodes ?? 0}</strong><small>active {experiment?.activeEpisodes ?? 0}</small></article>
      <article><span>確認／成交</span><strong>{experiment?.confirmedEpisodes ?? 0} / {experiment?.filledEpisodes ?? 0}</strong><small>3 次＋300 ms</small></article>
      <article><span>撤單／退出</span><strong>{experiment?.cancelledEpisodes ?? 0} / {experiment?.exitedEpisodes ?? 0}</strong><small>消失或逆轉</small></article>
      <article><span>官方結算</span><strong>{experiment?.settledWins ?? 0} W / {experiment?.settledLosses ?? 0} L</strong><small>pending {experiment?.settlementPendingEpisodes ?? 0}</small></article>
      <article><span>平均／中位壽命</span><strong>{duration(experiment?.duration?.averageMs)}</strong><small>median {duration(experiment?.duration?.medianMs)}</small></article>
      <article><span>P90／最長壽命</span><strong>{duration(experiment?.duration?.p90Ms)}</strong><small>max {duration(experiment?.duration?.maximumMs)}</small></article>
    </section>

    <section className="panel">
      <div className="panel-title"><div><span className="eyebrow">RECENT EPISODES</span><h2>最近訊號紀錄</h2></div><small>API {timeLabel(payload?.fetchedAt)}</small></div>
      <div className="table-wrap"><table>
        <thead><tr><th>開始</th><th>市場</th><th>方向</th><th>結果</th><th>訊號壽命</th><th>Pending</th><th>持倉</th><th>分數</th><th>進／出</th><th>PnL</th></tr></thead>
        <tbody>{recent.length === 0
          ? <tr><td colSpan={10} className="empty">等待 176–181 秒區間的新 Microprice 優勢訊號。</td></tr>
          : recent.map(row => <tr key={row.id}>
            <td>{timeLabel(row.startedAt)}</td>
            <td>#{row.marketId ?? "—"}</td>
            <td className={row.side === "UP" ? "positive" : "negative"}>{row.side ?? "—"}</td>
            <td><strong>{row.status ?? "—"}</strong><small>{reason(row.endReason ?? row.exitRequestedReason)}</small></td>
            <td>{duration(row.signalDurationMs)}</td>
            <td>{duration(row.pendingDurationMs)}</td>
            <td>{duration(row.positionDurationMs)}</td>
            <td>{decimal(row.startScore)} → {decimal(row.endScore)}<small>peak {decimal(row.peakAbsScore)}</small></td>
            <td>{decimal(row.entryPrice)} / {decimal(row.exitPrice)}</td>
            <td className={(row.pnl ?? 0) >= 0 ? "positive" : "negative"}>{money(row.pnl)}</td>
          </tr>)}</tbody>
      </table></div>
    </section>

    <section className="panel split">
      <div><span className="eyebrow">FROZEN RULES</span><h2>測試條件</h2><div className="rules">{rules.map(([key, value]) => <div key={key}><span>{key}</span><strong>{Array.isArray(value) ? value.join("–") : String(value)}</strong></div>)}</div></div>
      <div><span className="eyebrow">SAFETY BOUNDARY</span><h2>隔離邊界</h2><ul><li>只讀 Collector 的直接 UP／DOWN REST 雙簿快照。</li><li>使用獨立資料表，不建立正式或一般研究交易。</li><li>任何例外只記錄在此頁，外層呼叫不拋錯。</li><li>主狀態使用 include_ledger=false，完全略過本模組。</li><li>獨立 Next API 代理失敗不會拖垮主頁。</li></ul></div>
    </section>

    <style jsx>{`
      .life-page{min-height:100vh;padding:42px 28px 70px;color:#e8eefc;background:radial-gradient(circle at 15% 0%,rgba(40,174,210,.14),transparent 30%),#080b12;font-family:var(--font-geist),sans-serif}header,.hero,.summary,.panel,.error{max-width:1500px;margin-left:auto;margin-right:auto}header{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:24px}.eyebrow{font-size:11px;letter-spacing:.14em;color:#78d6ea}h1{font-size:38px;margin:7px 0}h2{margin:7px 0}header p{max-width:850px;color:#98a8c5;line-height:1.7}.actions{display:flex;align-items:center;gap:10px}.actions a{color:#dce6ff;text-decoration:none;border:1px solid #33415d;border-radius:999px;padding:9px 14px}.status{border-radius:999px;padding:9px 13px;font-size:12px;font-weight:800}.good{background:#153f35;color:#8df4c0}.bad{background:#4a2026;color:#ff9ba7}.neutral{background:#252e40;color:#b9c8e5}.error{border:1px solid #733843;background:#2a151b;border-radius:16px;padding:16px;margin-bottom:18px;display:grid;gap:5px;color:#ffb0ba}.error small{color:#d68d98}.grid4,.grid6{display:grid;gap:12px}.grid4{grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:12px}.grid6{grid-template-columns:repeat(6,minmax(0,1fr));margin-bottom:18px}.hero article,.summary article,.panel{border:1px solid rgba(126,145,178,.22);background:rgba(16,21,32,.92);border-radius:17px}.hero article,.summary article{padding:17px;display:grid;gap:7px}.hero span,.summary span,.rules span{font-size:12px;color:#8999b6}.hero strong{font-size:23px}.summary strong{font-size:20px}.hero small,.summary small,td small{display:block;color:#687a9a}.panel{padding:20px;margin-bottom:18px}.panel-title{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:14px}.panel-title small{color:#7d8dab}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1050px}th,td{text-align:left;padding:12px;border-bottom:1px solid rgba(126,145,178,.14);font-size:13px}th{color:#7f91b0;font-size:11px;letter-spacing:.08em}.empty{text-align:center;color:#7585a2;padding:32px}.positive{color:#8df4c0}.negative{color:#ff9ba7}.split{display:grid;grid-template-columns:1.4fr 1fr;gap:28px}.rules{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.rules div{display:flex;justify-content:space-between;gap:15px;border:1px solid rgba(126,145,178,.16);border-radius:10px;padding:10px}.split ul{color:#99aac7;line-height:1.8;padding-left:20px}@media(max-width:1000px){header{display:grid}.grid4{grid-template-columns:repeat(2,1fr)}.grid6{grid-template-columns:repeat(3,1fr)}.split{grid-template-columns:1fr}}@media(max-width:620px){.life-page{padding:24px 14px 60px}.grid4,.grid6{grid-template-columns:1fr}.rules{grid-template-columns:1fr}.actions{flex-wrap:wrap}}
    `}</style>
  </main>;
}
