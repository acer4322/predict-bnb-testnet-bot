"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";

const LIVE_CONFIRM_VALUE = "I_ACCEPT_NON_ATOMIC_TWO_LEG_RISK";

type RunMode = "dry-run" | "quote-only" | "live";
type Selection = "BTC_DOWN_ETH_UP" | "BTC_UP_ETH_DOWN" | "CHEAPEST_ELIGIBLE";

type RuntimeLog = {
  timestamp: string;
  level: string;
  message: string;
};

type RuntimeState = {
  running: boolean;
  phase: string;
  runId?: number | null;
  startedAt?: string | null;
  completedAt?: string | null;
  lastError?: string | null;
  logs?: RuntimeLog[];
};

type CanaryRun = {
  id: number;
  mode: RunMode;
  selection: Selection;
  variant?: string | null;
  status: string;
  pair_budget_usdt: number;
  required_balance_usdt: number;
  btc_market_id?: number | null;
  eth_market_id?: number | null;
  target_shares?: number | null;
  modeled_cost_per_share?: number | null;
  quoted_cost_per_share?: number | null;
  btc_order_id?: string | null;
  eth_order_id?: string | null;
  btc_order_status?: string | null;
  eth_order_status?: string | null;
  message?: string | null;
  created_at: string;
  updated_at: string;
};

type CanaryState = {
  strategy: string;
  nonAtomic: boolean;
  defaults: {
    pairBudgetUsdt: number;
    balanceBufferUsdt: number;
    requiredBalanceUsdt: number;
    recommendedAvailableBalanceUsdt: string;
    maxTotalCost: number;
    maxLegReprice: number;
    entrySecondsLeft: number;
    entryWindowSeconds: number;
    slippageBps: number;
    accountType: "CeDeFi" | "SPOT" | "FUNDING";
  };
  policy: {
    oneActiveRunPerProcess: boolean;
    oneLiveAttemptPerAlignedMarket: boolean;
    retryPlacement: boolean;
    automaticCancel: boolean;
    automaticUnwind: boolean;
    liveConfirmationPhrase: string;
    otherLiveExecutorMustBeStopped: boolean;
  };
  runtime: RuntimeState;
  recentRuns: CanaryRun[];
  updatedAt: string;
};

type FormState = {
  selection: Selection;
  pairBudgetUsdt: string;
  balanceBufferUsdt: string;
  maxTotalCost: string;
  maxLegReprice: string;
  entrySecondsLeft: string;
  entryWindowSeconds: string;
  slippageBps: string;
  accountType: "CeDeFi" | "SPOT" | "FUNDING";
};

function apiUrl(path: string) {
  const hostname = window.location.hostname || "127.0.0.1";
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8767${path}`;
}

function numberOr(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function fixed(value: number | null | undefined, digits = 4) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function timeLabel(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-TW", { hour12: false });
}

function statusTone(status: string) {
  if (/FILLED_BOTH|QUOTE_ONLY_READY|DRY_RUN_READY/.test(status)) return styles.good;
  if (/ERROR|INCOMPLETE|ONE_SIDED|REJECTED|TIMEOUT/.test(status)) return styles.bad;
  if (/SUBMITTED|PLACE|LIVE/.test(status)) return styles.warn;
  return styles.neutral;
}

const DEFAULT_FORM: FormState = {
  selection: "BTC_DOWN_ETH_UP",
  pairBudgetUsdt: "2.00",
  balanceBufferUsdt: "0.10",
  maxTotalCost: "0.98",
  maxLegReprice: "0.01",
  entrySecondsLeft: "180",
  entryWindowSeconds: "10",
  slippageBps: "100",
  accountType: "CeDeFi",
};

export default function XPairCanaryPage() {
  const [state, setState] = useState<CanaryState | null>(null);
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [confirmation, setConfirmation] = useState("");
  const [requestState, setRequestState] = useState("等待 Canary API");
  const [apiError, setApiError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/xpair-canary"), { cache: "no-store" });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "Canary API 回應失敗");
      setState(payload);
      setApiError(null);
      setRequestState(payload.runtime.running ? `執行中：${payload.runtime.phase}` : "狀態已同步");
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Canary API 無法連線");
      setRequestState("Canary API 離線");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!state?.defaults) return;
    setForm(current => current === DEFAULT_FORM ? {
      selection: "BTC_DOWN_ETH_UP",
      pairBudgetUsdt: String(state.defaults.pairBudgetUsdt.toFixed(2)),
      balanceBufferUsdt: String(state.defaults.balanceBufferUsdt.toFixed(2)),
      maxTotalCost: String(state.defaults.maxTotalCost),
      maxLegReprice: String(state.defaults.maxLegReprice),
      entrySecondsLeft: String(state.defaults.entrySecondsLeft),
      entryWindowSeconds: String(state.defaults.entryWindowSeconds),
      slippageBps: String(state.defaults.slippageBps),
      accountType: state.defaults.accountType,
    } : current);
  }, [state?.defaults]);

  const requiredBalance = useMemo(() => (
    numberOr(form.pairBudgetUsdt, 2) + numberOr(form.balanceBufferUsdt, 0.1)
  ), [form.balanceBufferUsdt, form.pairBudgetUsdt]);

  const submit = async (mode: RunMode) => {
    if (state?.runtime.running) return;
    if (mode === "live") {
      if (confirmation !== LIVE_CONFIRM_VALUE) {
        setRequestState("正式送單確認字串不完整");
        return;
      }
      const accepted = window.confirm(
        `即將送出非原子 BTC／ETH 兩腿實單，總預算上限 ${form.pairBudgetUsdt} USDT。\n\n` +
        "任何一腿可能單獨成交；系統不會自動取消、追單或平倉。另一個實單程序必須先停止。\n\n確定只執行這一次 Canary 嗎？",
      );
      if (!accepted) return;
    }
    setRequestState(`${mode} 請求送出中…`);
    try {
      const response = await fetch(apiUrl("/api/xpair-canary/run"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(mode === "live" ? { "X-BTC-Lab-XPair-Live": "confirmed" } : {}),
        },
        body: JSON.stringify({
          mode,
          selection: form.selection,
          pairBudgetUsdt: numberOr(form.pairBudgetUsdt, 2),
          balanceBufferUsdt: numberOr(form.balanceBufferUsdt, 0.1),
          maxTotalCost: numberOr(form.maxTotalCost, 0.98),
          maxLegReprice: numberOr(form.maxLegReprice, 0.01),
          entrySecondsLeft: numberOr(form.entrySecondsLeft, 180),
          entryWindowSeconds: numberOr(form.entryWindowSeconds, 10),
          slippageBps: Math.round(numberOr(form.slippageBps, 100)),
          accountType: form.accountType,
          confirmation: mode === "live" ? confirmation : undefined,
        }),
      });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "Canary 請求被拒絕");
      setState(payload);
      setRequestState(`${mode} 已接受，等待進場窗`);
      if (mode === "live") setConfirmation("");
    } catch (error) {
      setRequestState(error instanceof Error ? error.message : "Canary 請求失敗");
    }
  };

  const runtime = state?.runtime;
  const logs = runtime?.logs ?? [];
  const recent = state?.recentRuns ?? [];
  const liveUnlocked = confirmation === LIVE_CONFIRM_VALUE;

  return <main className={styles.page}>
    <header className={styles.hero}>
      <div>
        <span className={styles.eyebrow}>BTC 5M LAB · EXECUTION CANARY</span>
        <h1>BTC／ETH 跨市場實單可行性測試</h1>
        <p>只驗證兩腿報價、送單與成交可行性。它不是鎖利套利，也不是可長跑的正式策略。</p>
      </div>
      <div className={styles.heroActions}>
        <a href="/">返回主監控</a>
        <span className={`${styles.statusPill} ${runtime?.running ? styles.warn : apiError ? styles.bad : styles.good}`}>
          {apiError ? "API OFFLINE" : runtime?.running ? runtime.phase : runtime?.phase ?? "IDLE"}
        </span>
      </div>
    </header>

    <section className={styles.warningBox}>
      <strong>非原子兩腿風險</strong>
      <p>BTC 與 ETH 訂單會並行送出，但交易所沒有 atomic multi-leg。可能只成交一腿；本工具不會自動重送、取消或平倉。</p>
      <p>執行 quote-only 或 live 前，必須停止原本的正式實單程序，並確認錢包沒有活動訂單或未結持倉。</p>
    </section>

    <section className={styles.summaryGrid}>
      <article><span>預設總預算</span><strong>{form.pairBudgetUsdt} USDT</strong><small>兩腿依價格拆分，不是各固定 1 USDT</small></article>
      <article><span>最低可用餘額</span><strong>{requiredBalance.toFixed(2)} USDT</strong><small>建議錢包實際留 3–5 USDT</small></article>
      <article><span>預設方向</span><strong>{form.selection.replaceAll("_", " ")}</strong><small>第一次維持 BTC DOWN／ETH UP</small></article>
      <article><span>目前狀態</span><strong>{requestState}</strong><small>本頁每秒更新一次</small></article>
    </section>

    <section className={styles.console}>
      <div className={styles.sectionHead}>
        <div><span className={styles.eyebrow}>ONE SHOT · MAX 3 USDT</span><h2>Canary 設定</h2></div>
        <span className={styles.muted}>API：127.0.0.1:8767</span>
      </div>

      <div className={styles.formGrid}>
        <label>配對方向
          <select value={form.selection} onChange={event => setForm(current => ({ ...current, selection: event.target.value as Selection }))}>
            <option value="BTC_DOWN_ETH_UP">BTC DOWN + ETH UP</option>
            <option value="BTC_UP_ETH_DOWN">BTC UP + ETH DOWN</option>
            <option value="CHEAPEST_ELIGIBLE">當輪最低成本組合</option>
          </select>
        </label>
        <label>兩腿總預算（USDT）
          <input type="number" min="0.02" max="3" step="0.01" value={form.pairBudgetUsdt} onChange={event => setForm(current => ({ ...current, pairBudgetUsdt: event.target.value }))} />
        </label>
        <label>餘額緩衝（USDT）
          <input type="number" min="0" max="1" step="0.01" value={form.balanceBufferUsdt} onChange={event => setForm(current => ({ ...current, balanceBufferUsdt: event.target.value }))} />
        </label>
        <label>最高總成本／share
          <input type="number" min="0.01" max="1.99" step="0.001" value={form.maxTotalCost} onChange={event => setForm(current => ({ ...current, maxTotalCost: event.target.value }))} />
        </label>
        <label>單腿最大重定價
          <input type="number" min="0" max="0.05" step="0.001" value={form.maxLegReprice} onChange={event => setForm(current => ({ ...current, maxLegReprice: event.target.value }))} />
        </label>
        <label>目標剩餘秒數
          <input type="number" min="30" max="290" step="1" value={form.entrySecondsLeft} onChange={event => setForm(current => ({ ...current, entrySecondsLeft: event.target.value }))} />
        </label>
        <label>進場窗寬度（秒）
          <input type="number" min="1" max="60" step="1" value={form.entryWindowSeconds} onChange={event => setForm(current => ({ ...current, entryWindowSeconds: event.target.value }))} />
        </label>
        <label>Slippage（bps）
          <input type="number" min="0" max="500" step="10" value={form.slippageBps} onChange={event => setForm(current => ({ ...current, slippageBps: event.target.value }))} />
        </label>
        <label>付款帳戶
          <select value={form.accountType} onChange={event => setForm(current => ({ ...current, accountType: event.target.value as "CeDeFi" | "SPOT" | "FUNDING" }))}>
            <option value="CeDeFi">CeDeFi / Prediction Wallet</option>
            <option value="SPOT">SPOT</option>
            <option value="FUNDING">FUNDING</option>
          </select>
        </label>
      </div>

      <div className={styles.actionGrid}>
        <button className={styles.secondaryButton} disabled={runtime?.running} onClick={() => void submit("dry-run")}>
          Dry-run
          <small>只讀訂單簿，不取 signed quote</small>
        </button>
        <button className={styles.quoteButton} disabled={runtime?.running} onClick={() => void submit("quote-only")}>
          Quote-only
          <small>驗證最低金額與雙腿等 shares，不送單</small>
        </button>
        <div className={styles.liveAction}>
          <input
            aria-label="正式送單確認字串"
            placeholder={LIVE_CONFIRM_VALUE}
            value={confirmation}
            onChange={event => setConfirmation(event.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <button className={styles.liveButton} disabled={runtime?.running || !liveUnlocked} onClick={() => void submit("live")}>
            送出一次正式 Canary
            <small>真實資金 · 不可自動復原</small>
          </button>
        </div>
      </div>
    </section>

    <section className={styles.runtimeGrid}>
      <article className={styles.runtimeCard}>
        <div className={styles.sectionHead}><div><span className={styles.eyebrow}>RUNTIME</span><h2>目前執行</h2></div><span className={`${styles.statusPill} ${statusTone(runtime?.phase ?? "IDLE")}`}>{runtime?.phase ?? "IDLE"}</span></div>
        <dl>
          <div><dt>Run ID</dt><dd>{runtime?.runId ?? "—"}</dd></div>
          <div><dt>開始</dt><dd>{timeLabel(runtime?.startedAt)}</dd></div>
          <div><dt>完成</dt><dd>{timeLabel(runtime?.completedAt)}</dd></div>
          <div><dt>錯誤</dt><dd className={styles.errorText}>{runtime?.lastError ?? "—"}</dd></div>
        </dl>
      </article>
      <article className={styles.logCard}>
        <div className={styles.sectionHead}><div><span className={styles.eyebrow}>LIVE LOG</span><h2>Canary 日誌</h2></div><span className={styles.muted}>{logs.length} 筆</span></div>
        <div className={styles.logList}>
          {logs.length === 0 ? <p className={styles.empty}>尚無執行記錄</p> : logs.map((item, index) => <div key={`${item.timestamp}-${index}`} className={item.level === "ERROR" ? styles.logError : item.level === "WARN" ? styles.logWarn : ""}>
            <time>{timeLabel(item.timestamp)}</time><b>{item.level}</b><span>{item.message}</span>
          </div>)}
        </div>
      </article>
    </section>

    <section className={styles.historySection}>
      <div className={styles.sectionHead}><div><span className={styles.eyebrow}>LOCAL SQLITE LEDGER</span><h2>最近 Canary 執行</h2></div><span className={styles.muted}>{recent.length} 筆</span></div>
      <div className={styles.tableWrap}>
        <table>
          <thead><tr><th>ID</th><th>模式</th><th>狀態</th><th>組合</th><th>市場</th><th>成本/share</th><th>訂單狀態</th><th>時間</th></tr></thead>
          <tbody>
            {recent.length === 0 ? <tr><td colSpan={8} className={styles.empty}>尚無紀錄</td></tr> : recent.map(run => <tr key={run.id}>
              <td>#{run.id}</td>
              <td>{run.mode}</td>
              <td><span className={`${styles.statusPill} ${statusTone(run.status)}`}>{run.status}</span></td>
              <td>{run.variant ?? run.selection}</td>
              <td>BTC {run.btc_market_id ?? "—"}<br />ETH {run.eth_market_id ?? "—"}</td>
              <td>model {fixed(run.modeled_cost_per_share, 6)}<br />quote {fixed(run.quoted_cost_per_share, 6)}</td>
              <td>BTC {run.btc_order_status ?? "—"}<br />ETH {run.eth_order_status ?? "—"}</td>
              <td>{timeLabel(run.updated_at)}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </section>
  </main>;
}
