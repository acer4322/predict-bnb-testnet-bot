"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import styles from "./page.module.css";

type PaperSummary = {
  captured?: number;
  pending?: number;
  settled?: number;
  oneWin?: number;
  twoWins?: number;
  doubleLosses?: number;
  oneWinRate?: number | null;
  twoWinRate?: number | null;
  doubleLossRate?: number | null;
  totalCostUsdt?: number;
  pnlUsdt?: number;
  roi?: number | null;
};

type PaperComparison = {
  uniqueMarkets?: number;
  bothVariantsCapturedMarkets?: number;
  bothVariantsSettledMarkets?: number;
  downMinusUpRoi?: number | null;
  downMinusUpPnlUsdt?: number | null;
  downMinusUpDoubleLossRate?: number | null;
  downMinusUpAverageCostPerTrade?: number | null;
};

type PaperTrade = {
  id: number;
  variant?: string | null;
  btc_market_id?: number | null;
  eth_market_id?: number | null;
  signal_at?: string | null;
  seconds_left?: number | null;
  filled_shares?: number | null;
  cost_per_share?: number | null;
  total_cost?: number | null;
  btc_winner?: string | null;
  eth_winner?: string | null;
  winning_legs?: number | null;
  payout?: number | null;
  pnl?: number | null;
  settlement_status?: string | null;
  settled_at?: string | null;
};

type CaptureItem = {
  variant?: string;
  secondsLeft?: number;
  costPerShare?: number;
  totalCostUsdt?: number;
};

type PaperSimulation = {
  running?: boolean;
  status?: string;
  lastError?: string | null;
  lastCapture?: {
    marketKey?: string;
    variant?: string;
    variants?: CaptureItem[];
    capturedAt?: string;
  } | null;
  updatedAt?: string | null;
  entryRule?: string;
  minimumSecondsAfterStart?: number;
  minimumSecondsLeft?: number;
  summary?: PaperSummary;
  variants?: Record<string, PaperSummary>;
  comparison?: PaperComparison;
  recent?: PaperTrade[];
};

type ApiState = {
  paperSimulation?: PaperSimulation;
  error?: string;
};

const DOWN_VARIANT = "BTC_DOWN_ETH_UP";
const UP_VARIANT = "BTC_UP_ETH_DOWN";

function apiUrl(path: string) {
  const hostname = window.location.hostname || "127.0.0.1";
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8767${path}`;
}

function fixed(value?: number | null, digits = 4) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function percent(value?: number | null) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(2)}%`;
}

function signed(value?: number | null, digits = 4) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function signedPercent(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  const points = value * 100;
  return `${points >= 0 ? "+" : ""}${points.toFixed(2)} 個百分點`;
}

function timeLabel(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-TW", { hour12: false });
}

function outcomeLabel(trade: PaperTrade) {
  if (trade.settlement_status !== "SETTLED") return "待結算";
  if (trade.winning_legs === 2) return "雙勝";
  if (trade.winning_legs === 1) return "單勝";
  if (trade.winning_legs === 0) return "雙敗";
  return "未知";
}

function outcomeTone(trade: PaperTrade) {
  if (trade.settlement_status !== "SETTLED") return styles.warn;
  if (trade.winning_legs === 2 || (trade.pnl ?? 0) > 0) return styles.good;
  if (trade.winning_legs === 0 || (trade.pnl ?? 0) < 0) return styles.bad;
  return styles.neutral;
}

function VariantStats({ variant, summary }: { variant: string; summary: PaperSummary }) {
  const pnl = summary.pnlUsdt ?? 0;
  const roi = summary.roi ?? null;
  const label = variant === DOWN_VARIANT
    ? "BTC DOWN＋ETH UP"
    : "BTC UP＋ETH DOWN";
  return <section className={styles.console}>
    <div className={styles.sectionHead}>
      <div><span className={styles.eyebrow}>{variant}</span><h2>{label}</h2></div>
      <span className={`${styles.statusPill} ${(roi ?? 0) < 0 ? styles.bad : styles.good}`}>
        ROI {percent(roi)}
      </span>
    </div>
    <div className={styles.summaryGrid}>
      <article><span>紙單／待結算</span><strong>{summary.captured ?? 0}／{summary.pending ?? 0}</strong><small>每個對齊市場最多一筆</small></article>
      <article><span>已結算</span><strong>{summary.settled ?? 0}</strong><small>勝率只使用已結算樣本</small></article>
      <article><span>單勝</span><strong>{summary.oneWin ?? 0}</strong><small>{percent(summary.oneWinRate)}</small></article>
      <article><span>雙勝</span><strong>{summary.twoWins ?? 0}</strong><small>{percent(summary.twoWinRate)}</small></article>
      <article><span>雙敗</span><strong>{summary.doubleLosses ?? 0}</strong><small>{percent(summary.doubleLossRate)}</small></article>
      <article><span>累計 PnL</span><strong className={pnl < 0 ? styles.errorText : ""}>{signed(pnl, 4)} USDT</strong><small>成本 {fixed(summary.totalCostUsdt, 4)} USDT</small></article>
      <article><span>ROI</span><strong className={(roi ?? 0) < 0 ? styles.errorText : ""}>{percent(roi)}</strong><small>PnL ÷ 已結算總成本</small></article>
    </div>
  </section>;
}

export default function PaperSimulationPanel() {
  const pathname = usePathname();
  const [paper, setPaper] = useState<PaperSimulation | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/xpair-canary"), { cache: "no-store" });
      const payload = await response.json() as ApiState;
      if (!response.ok) throw new Error(payload.error ?? "紙上帳本讀取失敗");
      setPaper(payload.paperSimulation ?? null);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "紙上帳本 API 無法連線");
    }
  }, []);

  useEffect(() => {
    if (pathname !== "/xpair-canary") return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [pathname, refresh]);

  if (pathname !== "/xpair-canary") return null;

  const summary = paper?.summary ?? {};
  const variants = paper?.variants ?? {};
  const comparison = paper?.comparison ?? {};
  const down = variants[DOWN_VARIANT] ?? {};
  const up = variants[UP_VARIANT] ?? {};
  const recent = paper?.recent ?? [];
  const lastCaptures = paper?.lastCapture?.variants ?? [];

  return <section className={styles.historySection}>
    <div className={styles.sectionHead}>
      <div>
        <span className={styles.eyebrow}>PAPER · DUAL VARIANT · FIRST ELIGIBLE</span>
        <h2>XPAIR 雙方向常駐模擬</h2>
      </div>
      <span className={`${styles.statusPill} ${error ? styles.bad : paper?.running ? styles.good : styles.warn}`}>
        {error ? "PAPER OFFLINE" : paper?.running ? paper.status ?? "RUNNING" : paper?.status ?? "STARTING"}
      </span>
    </div>

    <div className={styles.warningBox}>
      <strong>同一市場同時測試兩個相反組合</strong>
      <p>
        每組對齊的 BTC／ETH 市場最多建立兩筆獨立紙單：`BTC_DOWN_ETH_UP` 與 `BTC_UP_ETH_DOWN`。
        市場開始 {paper?.minimumSecondsAfterStart ?? 5} 秒後、剩餘至少 {paper?.minimumSecondsLeft ?? 20} 秒，
        每個方向在自己第一次符合價格與深度條件時分別進場，因此兩邊可能有不同的進場秒數與成本。
      </p>
      <p>模擬帳本與實單完全獨立，不需要武裝、不申請 signed Quote，也不呼叫任何交易 API。</p>
      {error || paper?.lastError ? <p className={styles.errorText}>{error ?? paper?.lastError}</p> : null}
    </div>

    <div className={styles.summaryGrid}>
      <article><span>觀察市場數</span><strong>{comparison.uniqueMarkets ?? 0}</strong><small>至少一個方向曾符合</small></article>
      <article><span>雙方向都有進場</span><strong>{comparison.bothVariantsCapturedMarkets ?? 0}</strong><small>可直接比較同市場差異</small></article>
      <article><span>雙方向都已結算</span><strong>{comparison.bothVariantsSettledMarkets ?? 0}</strong><small>完整成對樣本</small></article>
      <article><span>總紙單／待結算</span><strong>{summary.captured ?? 0}／{summary.pending ?? 0}</strong><small>每市場最多兩筆</small></article>
      <article><span>DOWN-UP ROI 差</span><strong>{signedPercent(comparison.downMinusUpRoi)}</strong><small>正值代表 DOWN＋UP 較高</small></article>
      <article><span>DOWN-UP 雙敗率差</span><strong>{signedPercent(comparison.downMinusUpDoubleLossRate)}</strong><small>負值代表 DOWN＋UP 雙敗較少</small></article>
      <article><span>DOWN-UP PnL 差</span><strong>{signed(comparison.downMinusUpPnlUsdt, 4)} USDT</strong><small>累計差值</small></article>
      <article><span>平均成本差</span><strong>{signed(comparison.downMinusUpAverageCostPerTrade, 4)} USDT</strong><small>每筆已結算平均成本差</small></article>
    </div>

    {lastCaptures.length > 0 ? <div className={styles.warningBox}>
      <strong>最近模擬進場：{paper?.lastCapture?.marketKey ?? "—"}</strong>
      {lastCaptures.map(item => <p key={item.variant ?? "unknown"}>
        {item.variant ?? "—"} · 剩 {fixed(item.secondsLeft, 1)} 秒 · 成本 {fixed(item.costPerShare, 6)}／share · {fixed(item.totalCostUsdt, 4)} USDT
      </p>)}
      <p>{timeLabel(paper?.lastCapture?.capturedAt)}</p>
    </div> : null}

    <VariantStats variant={DOWN_VARIANT} summary={down} />
    <VariantStats variant={UP_VARIANT} summary={up} />

    <div className={styles.sectionHead}>
      <div><span className={styles.eyebrow}>DUAL-VARIANT SQLITE PAPER LEDGER</span><h2>最近模擬單與最終結果</h2></div>
      <span className={styles.muted}>{recent.length} 筆</span>
    </div>
    <div className={styles.tableWrap}>
      <table>
        <thead><tr><th>ID</th><th>組合</th><th>市場</th><th>進場</th><th>模擬成本</th><th>最終結果</th><th>PnL</th><th>結算時間</th></tr></thead>
        <tbody>
          {recent.length === 0 ? <tr><td colSpan={8} className={styles.empty}>尚無符合條件的雙方向模擬單</td></tr> : recent.map(trade => <tr key={trade.id}>
            <td>#{trade.id}</td>
            <td>{trade.variant ?? "—"}</td>
            <td>BTC {trade.btc_market_id ?? "—"}<br />ETH {trade.eth_market_id ?? "—"}</td>
            <td>{timeLabel(trade.signal_at)}<br /><span className={styles.muted}>剩 {fixed(trade.seconds_left, 1)} 秒</span></td>
            <td>{fixed(trade.cost_per_share, 6)}／share<br /><span className={styles.muted}>{fixed(trade.filled_shares, 4)} shares · {fixed(trade.total_cost, 4)} USDT</span></td>
            <td><span className={`${styles.statusPill} ${outcomeTone(trade)}`}>{outcomeLabel(trade)}</span><br /><span className={styles.muted}>BTC {trade.btc_winner ?? "—"} · ETH {trade.eth_winner ?? "—"}</span></td>
            <td className={(trade.pnl ?? 0) < 0 ? styles.errorText : ""}>{trade.settlement_status === "SETTLED" ? `${signed(trade.pnl, 4)} USDT` : "—"}<br /><span className={styles.muted}>payout {fixed(trade.payout, 4)}</span></td>
            <td>{timeLabel(trade.settled_at)}</td>
          </tr>)}
        </tbody>
      </table>
    </div>
  </section>;
}
