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
  strategy?: string | null;
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
  experiment?: string | null;
  ledger_class?: "CURRENT_DUAL_EXPERIMENT" | "LEGACY_ARCHIVE" | string;
};

type CaptureItem = {
  variant?: string;
  secondsLeft?: number;
  costPerShare?: number;
  totalCostUsdt?: number;
};

type PaperStorage = {
  persistent?: boolean;
  autoDelete?: boolean;
  historyTotal?: number;
  recentPreviewLimit?: number;
  historyPageSize?: number;
  importedLegacyRows?: number;
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
  ledgerPath?: string;
  storage?: PaperStorage;
  summary?: PaperSummary;
  variants?: Record<string, PaperSummary>;
  comparison?: PaperComparison;
  recent?: PaperTrade[];
};

type PaperHistory = {
  persistent?: boolean;
  autoDelete?: boolean;
  ledgerPath?: string;
  total?: number;
  limit?: number;
  offset?: number;
  hasMore?: boolean;
  items?: PaperTrade[];
  migration?: {
    importedNow?: number;
    importedTotal?: number;
    checkedSources?: string[];
  };
  error?: string;
};

type ApiState = {
  paperSimulation?: PaperSimulation;
  error?: string;
};

const DOWN_VARIANT = "BTC_DOWN_ETH_UP";
const UP_VARIANT = "BTC_UP_ETH_DOWN";
const HISTORY_PAGE_SIZE = 50;

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

function experimentLabel(trade: PaperTrade) {
  return trade.ledger_class === "CURRENT_DUAL_EXPERIMENT"
    ? "目前雙方向實驗"
    : "舊帳本永久匯入";
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
      <article><span>已結算</span><strong>{summary.settled ?? 0}</strong><small>勝率只使用目前雙方向實驗</small></article>
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
  const [history, setHistory] = useState<PaperHistory | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyPage, setHistoryPage] = useState(0);

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

  const refreshHistory = useCallback(async (page: number) => {
    try {
      const offset = Math.max(0, page) * HISTORY_PAGE_SIZE;
      const response = await fetch(
        apiUrl(`/api/xpair-canary/paper-history?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`),
        { cache: "no-store" },
      );
      const payload = await response.json() as PaperHistory;
      if (!response.ok) throw new Error(payload.error ?? "永久紙單歷史讀取失敗");
      setHistory(payload);
      setHistoryError(null);
    } catch (caught) {
      setHistoryError(caught instanceof Error ? caught.message : "永久紙單歷史 API 無法連線");
    }
  }, []);

  useEffect(() => {
    if (pathname !== "/xpair-canary") return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [pathname, refresh]);

  useEffect(() => {
    if (pathname !== "/xpair-canary") return;
    void refreshHistory(historyPage);
    if (historyPage !== 0) return;
    const timer = window.setInterval(() => void refreshHistory(0), 5000);
    return () => window.clearInterval(timer);
  }, [historyPage, pathname, refreshHistory]);

  if (pathname !== "/xpair-canary") return null;

  const summary = paper?.summary ?? {};
  const variants = paper?.variants ?? {};
  const comparison = paper?.comparison ?? {};
  const down = variants[DOWN_VARIANT] ?? {};
  const up = variants[UP_VARIANT] ?? {};
  const trades = history?.items ?? paper?.recent ?? [];
  const historyTotal = history?.total ?? paper?.storage?.historyTotal ?? trades.length;
  const totalPages = Math.max(1, Math.ceil(historyTotal / HISTORY_PAGE_SIZE));
  const lastCaptures = paper?.lastCapture?.variants ?? [];
  const ledgerPath = history?.ledgerPath ?? paper?.ledgerPath ?? "—";
  const importedLegacy = history?.migration?.importedTotal
    ?? paper?.storage?.importedLegacyRows
    ?? 0;

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
      <strong>永久 SQLite 紙單帳本</strong>
      <p>
        每組對齊的 BTC／ETH 市場最多建立兩筆獨立紙單：`BTC_DOWN_ETH_UP` 與 `BTC_UP_ETH_DOWN`。
        重啟 API、重新整理頁面或更新程式都不會清除資料；表格可分頁翻到最早紀錄。
      </p>
      <p>
        永久檔案：<code>{ledgerPath}</code><br />
        自動刪除：停用 · 永久歷史：{historyTotal} 筆 · 已匯入舊帳本：{importedLegacy} 筆
      </p>
      <p>
        舊帳本會保留在永久歷史，但不混入目前雙方向實驗的 ROI、PnL 與勝率，避免比較失真。
      </p>
      <p>模擬帳本與實單完全獨立，不需要武裝、不申請 signed Quote，也不呼叫任何交易 API。</p>
      {error || paper?.lastError || historyError ? <p className={styles.errorText}>{error ?? paper?.lastError ?? historyError}</p> : null}
    </div>

    <div className={styles.summaryGrid}>
      <article><span>觀察市場數</span><strong>{comparison.uniqueMarkets ?? 0}</strong><small>目前雙方向實驗</small></article>
      <article><span>雙方向都有進場</span><strong>{comparison.bothVariantsCapturedMarkets ?? 0}</strong><small>可直接比較同市場差異</small></article>
      <article><span>雙方向都已結算</span><strong>{comparison.bothVariantsSettledMarkets ?? 0}</strong><small>完整成對樣本</small></article>
      <article><span>目前實驗紙單／待結算</span><strong>{summary.captured ?? 0}／{summary.pending ?? 0}</strong><small>不含舊帳本匯入</small></article>
      <article><span>永久歷史總數</span><strong>{historyTotal}</strong><small>永不因 40 筆上限消失</small></article>
      <article><span>DOWN-UP ROI 差</span><strong>{signedPercent(comparison.downMinusUpRoi)}</strong><small>正值代表 DOWN＋UP 較高</small></article>
      <article><span>DOWN-UP 雙敗率差</span><strong>{signedPercent(comparison.downMinusUpDoubleLossRate)}</strong><small>負值代表 DOWN＋UP 雙敗較少</small></article>
      <article><span>DOWN-UP PnL 差</span><strong>{signed(comparison.downMinusUpPnlUsdt, 4)} USDT</strong><small>目前實驗累計差值</small></article>
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
      <div><span className={styles.eyebrow}>PERMANENT PAGINATED SQLITE LEDGER</span><h2>全部模擬單與最終結果</h2></div>
      <div className={styles.heroActions}>
        <button
          className={styles.secondaryButton}
          disabled={historyPage <= 0}
          onClick={() => setHistoryPage(page => Math.max(0, page - 1))}
        >上一頁</button>
        <span className={styles.muted}>第 {Math.min(historyPage + 1, totalPages)}／{totalPages} 頁 · 共 {historyTotal} 筆</span>
        <button
          className={styles.secondaryButton}
          disabled={!history?.hasMore}
          onClick={() => setHistoryPage(page => page + 1)}
        >下一頁</button>
      </div>
    </div>
    <div className={styles.tableWrap}>
      <table>
        <thead><tr><th>ID</th><th>組合／來源</th><th>市場</th><th>進場</th><th>模擬成本</th><th>最終結果</th><th>PnL</th><th>結算時間</th></tr></thead>
        <tbody>
          {trades.length === 0 ? <tr><td colSpan={8} className={styles.empty}>尚無模擬單永久紀錄</td></tr> : trades.map(trade => <tr key={`${trade.strategy ?? "paper"}-${trade.id}`}>
            <td>#{trade.id}</td>
            <td>{trade.variant ?? "—"}<br /><span className={styles.muted}>{experimentLabel(trade)}</span></td>
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
