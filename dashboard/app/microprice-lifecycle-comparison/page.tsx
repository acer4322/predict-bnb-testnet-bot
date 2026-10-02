"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type Pair = {
  pairId?: number;
  marketId?: number;
  side?: string;
  entryPrice?: number | null;
  exitArmStatus?: string;
  exitReason?: string | null;
  exitPrice?: number | null;
  exitPnl?: number | null;
  holdArmStatus?: string;
  officialWinner?: string | null;
  holdPnl?: number | null;
  advantage?: number | null;
  winner?: string | null;
};

type Comparison = {
  status?: string;
  totalPairs?: number;
  completedPairs?: number;
  pendingPairs?: number;
  earlyExitPairs?: number;
  exitOnSignal?: { totalPnl?: number | null; averagePnl?: number | null };
  holdToEnd?: { totalPnl?: number | null; averagePnl?: number | null };
  comparison?: {
    netAdvantage?: number | null;
    averageAdvantage?: number | null;
    medianAdvantage?: number | null;
    exitBetter?: number;
    holdBetter?: number;
    ties?: number;
    p90AbsoluteDifference?: number | null;
  };
  byExitReason?: Record<string, {
    count?: number;
    exitOnSignalPnl?: number;
    holdToEndPnl?: number;
    netAdvantage?: number;
  }>;
  recentPairs?: Pair[];
  runtime?: { lastSyncAt?: string | null; lastError?: string | null };
  error?: string | null;
};

type Payload = {
  experiment?: { comparison?: Comparison | null } | null;
  error?: string | null;
};

const reasonLabels: Record<string, string> = {
  EDGE_LOST: "優勢消失",
  SIGNAL_REVERSED: "訊號逆轉",
  MARKET_ROLLOVER_HELD: "未觸發退出",
  HELD_TO_SETTLEMENT: "持有至結算",
};

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${value >= 0 ? "+" : ""}$${value.toFixed(3)}`;
}

function price(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(3);
}

function label(value: string | null | undefined) {
  const text = String(value ?? "");
  return reasonLabels[text] ?? (text || "—");
}

export default function MicropriceLifecycleComparisonPage() {
  const [payload, setPayload] = useState<Payload | null>(null);

  useEffect(() => {
    let active = true;
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
        const next = await response.json() as Payload;
        if (active) setPayload(next);
      } catch (error) {
        if (active && !(error instanceof DOMException && error.name === "AbortError")) {
          setPayload({ error: error instanceof Error ? error.message : String(error) });
        }
      }
    };
    void load();
    const timer = window.setInterval(load, 1_000);
    const visibility = () => document.visibilityState === "visible" ? void load() : controller?.abort();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);

  const comparison = payload?.experiment?.comparison ?? null;
  const result = comparison?.comparison;
  const rows = comparison?.recentPairs?.slice(0, 15) ?? [];
  const error = payload?.error ?? comparison?.runtime?.lastError ?? comparison?.error;
  const net = result?.netAdvantage ?? 0;
  const leader = net > 0 ? "訊號退出組領先" : net < 0 ? "持有到結束組領先" : "目前平手";

  return <main className="page">
    <header>
      <div>
        <span className="eyebrow">PAIRED PAPER A/B · IDENTICAL ENTRY</span>
        <h1>Microprice 退出 vs 持有到結束</h1>
        <p>每次模擬成交建立兩個共用相同進場價、份額與費用的配對組。A 組在訊號消失或逆轉時退出；B 組忽略後續訊號並持有到官方結算。</p>
      </div>
      <div className="links"><Link href="/microprice-lifecycle">生命週期</Link><Link href="/">主監控</Link></div>
    </header>

    {error && <section className="error"><strong>對比旁路目前不可用</strong><span>{error}</span><small>主策略與主 Dashboard 不受影響。</small></section>}

    <section className="leader">
      <span>目前結果</span>
      <strong className={net >= 0 ? "positive" : "negative"}>{leader}</strong>
      <b>{money(net)}</b>
      <small>正值代表「訊號退出」比「持有到結束」多賺／少賠</small>
    </section>

    <section className="cards">
      <article><span>完整配對</span><strong>{comparison?.completedPairs ?? 0}</strong><small>總計 {comparison?.totalPairs ?? 0} · 待結算 {comparison?.pendingPairs ?? 0}</small></article>
      <article><span>訊號退出組 PnL</span><strong className={(comparison?.exitOnSignal?.totalPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(comparison?.exitOnSignal?.totalPnl)}</strong><small>平均 {money(comparison?.exitOnSignal?.averagePnl)}</small></article>
      <article><span>持有到結束組 PnL</span><strong className={(comparison?.holdToEnd?.totalPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(comparison?.holdToEnd?.totalPnl)}</strong><small>平均 {money(comparison?.holdToEnd?.averagePnl)}</small></article>
      <article><span>勝出次數</span><strong>{result?.exitBetter ?? 0} : {result?.holdBetter ?? 0}</strong><small>退出組 : 持有組 · 平手 {result?.ties ?? 0}</small></article>
      <article><span>平均優勢</span><strong>{money(result?.averageAdvantage)}</strong><small>中位 {money(result?.medianAdvantage)}</small></article>
      <article><span>早退樣本</span><strong>{comparison?.earlyExitPairs ?? 0}</strong><small>P90 差異 {money(result?.p90AbsoluteDifference)}</small></article>
    </section>

    <section className="panel">
      <div className="title"><div><span className="eyebrow">PAIRED RESULTS</span><h2>最近配對結果</h2></div><small>{comparison?.status ?? "WAITING"}</small></div>
      <div className="table-wrap"><table>
        <thead><tr><th>市場</th><th>方向</th><th>進場</th><th>A 組退出原因</th><th>A 組退出／PnL</th><th>B 組結算／PnL</th><th>較佳組</th><th>A-B 差異</th></tr></thead>
        <tbody>{rows.length === 0
          ? <tr><td colSpan={8} className="empty">等待第一筆成功成交與官方結算。</td></tr>
          : rows.map(row => <tr key={row.pairId}>
            <td>#{row.marketId ?? "—"}</td>
            <td className={row.side === "UP" ? "positive" : "negative"}>{row.side ?? "—"}</td>
            <td>{price(row.entryPrice)}</td>
            <td>{label(row.exitReason)}<small>{row.exitArmStatus ?? "—"}</small></td>
            <td>{price(row.exitPrice)}<small className={(row.exitPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(row.exitPnl)}</small></td>
            <td>{row.officialWinner ?? "—"}<small className={(row.holdPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(row.holdPnl)} · {row.holdArmStatus ?? "—"}</small></td>
            <td>{row.winner === "EXIT_ON_SIGNAL" ? "訊號退出" : row.winner === "HOLD_TO_END" ? "持有到底" : row.winner === "TIE" ? "平手" : "待結算"}</td>
            <td className={(row.advantage ?? 0) >= 0 ? "positive" : "negative"}>{money(row.advantage)}</td>
          </tr>)}</tbody>
      </table></div>
    </section>

    <section className="panel reasons">
      <span className="eyebrow">BY EXIT REASON</span><h2>依退出原因拆分</h2>
      <div className="reason-grid">{Object.entries(comparison?.byExitReason ?? {}).map(([key, value]) => <article key={key}>
        <strong>{label(key)}</strong><span>{value.count ?? 0} 筆</span><small>A {money(value.exitOnSignalPnl)} · B {money(value.holdToEndPnl)}</small><b className={(value.netAdvantage ?? 0) >= 0 ? "positive" : "negative"}>差異 {money(value.netAdvantage)}</b>
      </article>)}</div>
    </section>

    <style jsx>{`
      .page{min-height:100vh;padding:42px 28px 70px;color:#eaf0ff;background:radial-gradient(circle at 20% 0%,rgba(63,177,211,.15),transparent 31%),#080b12;font-family:var(--font-geist),sans-serif}header,.leader,.cards,.panel,.error{max-width:1500px;margin-left:auto;margin-right:auto}header{display:flex;justify-content:space-between;gap:24px;margin-bottom:22px}.eyebrow{font-size:11px;letter-spacing:.14em;color:#7edfed}h1{font-size:38px;margin:7px 0}h2{margin:7px 0}header p{max-width:900px;color:#99aac7;line-height:1.7}.links{display:flex;gap:9px;align-items:flex-start}.links a{color:#dce6ff;text-decoration:none;border:1px solid #34415c;border-radius:999px;padding:9px 13px}.error{display:grid;gap:5px;padding:16px;margin-bottom:16px;border:1px solid #733843;border-radius:16px;background:#2a151b;color:#ffb0ba}.leader{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:15px;padding:20px;margin-bottom:12px;border:1px solid #2f6070;border-radius:18px;background:rgba(19,37,45,.88)}.leader span,.cards span,.reason-grid span{font-size:12px;color:#91a3c1}.leader strong{font-size:26px}.leader b{font-size:28px}.leader small{grid-column:2/4;color:#72839e}.cards{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:18px}.cards article,.panel{border:1px solid rgba(126,145,178,.22);background:rgba(16,21,32,.93);border-radius:17px}.cards article{padding:17px;display:grid;gap:7px}.cards strong{font-size:21px}.cards small,td small{display:block;color:#7384a3}.panel{padding:20px;margin-bottom:18px}.title{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:13px}.title small{color:#7e90af}.table-wrap{overflow:auto}table{width:100%;min-width:1120px;border-collapse:collapse}th,td{text-align:left;padding:12px;border-bottom:1px solid rgba(126,145,178,.14);font-size:13px}th{font-size:11px;letter-spacing:.07em;color:#8192b0}.empty{text-align:center;color:#7485a4;padding:32px}.positive{color:#8df4c0}.negative{color:#ff9ba7}.reason-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.reason-grid article{display:grid;gap:6px;padding:14px;border:1px solid rgba(126,145,178,.17);border-radius:12px}.reason-grid b{font-size:13px}@media(max-width:1100px){.cards{grid-template-columns:repeat(3,1fr)}.reason-grid{grid-template-columns:1fr 1fr}}@media(max-width:650px){.page{padding:24px 14px 60px}header{display:grid}.cards{grid-template-columns:1fr}.leader{grid-template-columns:1fr}.leader small{grid-column:auto}.reason-grid{grid-template-columns:1fr}}
    `}</style>
  </main>;
}
