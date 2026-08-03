"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";

const LIVE_CONFIRM_VALUE = "I_ACCEPT_NON_ATOMIC_TWO_LEG_RISK";

type Selection = "BTC_DOWN_ETH_UP" | "BTC_UP_ETH_DOWN" | "CHEAPEST_ELIGIBLE";

type RuntimeLog = {
  timestamp: string;
  level: string;
  message: string;
};

type LatestMarket = {
  marketKey?: string;
  btcMarketId?: number;
  ethMarketId?: number;
  secondsLeft?: number;
  startMs?: number;
  endMs?: number;
};

type LatestQuote = {
  accepted?: boolean;
  marketKey?: string;
  variant?: string;
  costPerShare?: number;
  targetShares?: number;
  updatedAt?: string;
  error?: string;
  armed?: boolean;
};

type RuntimeState = {
  running: boolean;
  phase: string;
  startedAt?: string | null;
  updatedAt?: string | null;
  lastError?: string | null;
  lastWarning?: string | null;
  armed: boolean;
  armedAt?: string | null;
  armedGeneration?: number;
  lastAttemptMarketKey?: string | null;
  latestMarket?: LatestMarket | null;
  latestPlan?: Record<string, unknown> | null;
  latestQuote?: LatestQuote | null;
  quoteAttempts?: number;
  quoteAccepts?: number;
  liveAttempts?: number;
  logs?: RuntimeLog[];
};

type CanaryRun = {
  id: number;
  mode: string;
  variant?: string | null;
  status: string;
  pair_budget_usdt: number;
  btc_market_id?: number | null;
  eth_market_id?: number | null;
  modeled_cost_per_share?: number | null;
  quoted_cost_per_share?: number | null;
  btc_order_status?: string | null;
  eth_order_status?: string | null;
  message?: string | null;
  updated_at?: string | null;
};

type MonitorDefaults = {
  selection: Selection;
  pairBudgetUsdt: number;
  balanceBufferUsdt: number;
  requiredBalanceUsdt: number;
  maxTotalCost: number;
  maxLegReprice: number;
  entrySecondsLeft: number;
  entryWindowSeconds: number;
  slippageBps: number;
  accountType: "CeDeFi";
  quoteIntervalSeconds?: number;
};

type CanaryState = {
  strategy: string;
  nonAtomic: boolean;
  defaults: MonitorDefaults;
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
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-TW", { hour12: false });
}

function statusTone(status: string) {
  if (/AUTO_QUOTE_READY|FILLED_BOTH|MONITORING/.test(status)) return styles.good;
  if (/ERROR|INCOMPLETE|ONE_SIDED|REJECTED/.test(status)) return styles.bad;
  if (/ARMED|PLACE|SUBMITTED|WAITING_SAFE/.test(status)) return styles.warn;
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
};

export default function XPairCanaryPage() {
  const [state, setState] = useState<CanaryState | null>(null);
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [formInitialized, setFormInitialized] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [requestState, setRequestState] = useState("等待常駐監控 API");
  const [apiError, setApiError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/xpair-canary"), { cache: "no-store" });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "Canary API 回應失敗");
      setState(payload);
      setApiError(null);
      setRequestState(
        payload.runtime.armed
          ? `已武裝：等待下一個符合條件的 quote`
          : payload.runtime.running
            ? `自動 Quote 監控中：${payload.runtime.phase}`
            : `監控尚未啟動：${payload.runtime.phase}`,
      );
      if (!formInitialized && payload.defaults) {
        setForm({
          selection: payload.defaults.selection,
          pairBudgetUsdt: payload.defaults.pairBudgetUsdt.toFixed(2),
          balanceBufferUsdt: payload.defaults.balanceBufferUsdt.toFixed(2),
          maxTotalCost: String(payload.defaults.maxTotalCost),
          maxLegReprice: String(payload.defaults.maxLegReprice),
          entrySecondsLeft: String(payload.defaults.entrySecondsLeft),
          entryWindowSeconds: String(payload.defaults.entryWindowSeconds),
          slippageBps: String(payload.defaults.slippageBps),
        });
        setFormInitialized(true);
      }
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Canary API 無法連線");
      setRequestState("常駐監控 API 離線");
    }
  }, [formInitialized]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const requestPayload = useCallback(() => ({
    selection: form.selection,
    pairBudgetUsdt: numberOr(form.pairBudgetUsdt, 2),
    balanceBufferUsdt: numberOr(form.balanceBufferUsdt, 0.1),
    maxTotalCost: numberOr(form.maxTotalCost, 0.98),
    maxLegReprice: numberOr(form.maxLegReprice, 0.01),
    entrySecondsLeft: numberOr(form.entrySecondsLeft, 180),
    entryWindowSeconds: numberOr(form.entryWindowSeconds, 10),
    slippageBps: Math.round(numberOr(form.slippageBps, 100)),
    accountType: "CeDeFi",
  }), [form]);

  const saveConfig = async () => {
    setRequestState("儲存監控條件中…");
    try {
      const response = await fetch(apiUrl("/api/xpair-canary/config"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestPayload()),
      });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "監控條件儲存失敗");
      setState(payload);
      setRequestState("條件已套用；背景會自動取得 quote");
    } catch (error) {
      setRequestState(error instanceof Error ? error.message : "監控條件儲存失敗");
    }
  };

  const arm = async () => {
    if (confirmation !== LIVE_CONFIRM_VALUE) {
      setRequestState("正式送單確認字串不完整");
      return;
    }
    const accepted = window.confirm(
      `這不會立刻送單，而是武裝下一個符合條件的 BTC／ETH 兩腿 quote。\n\n` +
      `總預算上限 ${form.pairBudgetUsdt} USDT；一旦兩腿報價通過，程式會自動嘗試送出一次，無法再等你確認。\n\n` +
      `兩腿非原子，可能只成交一腿；另一個正式實單程序必須先停止。\n\n確定武裝嗎？`,
    );
    if (!accepted) return;
    setRequestState("正在武裝下一次正式單…");
    try {
      const response = await fetch(apiUrl("/api/xpair-canary/arm"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-BTC-Lab-XPair-Live": "confirmed",
        },
        body: JSON.stringify({
          ...requestPayload(),
          confirmation,
        }),
      });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "武裝失敗");
      setState(payload);
      setConfirmation("");
      setRequestState("已武裝；下一個符合條件的 quote 會自動嘗試一次正式送單");
    } catch (error) {
      setRequestState(error instanceof Error ? error.message : "武裝失敗");
    }
  };

  const disarm = async () => {
    setRequestState("取消武裝中…");
    try {
      const response = await fetch(apiUrl("/api/xpair-canary/disarm"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "operator" }),
      });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "取消武裝失敗");
      setState(payload);
      setRequestState("已取消武裝；背景 quote 監控仍會繼續");
    } catch (error) {
      setRequestState(error instanceof Error ? error.message : "取消武裝失敗");
    }
  };

  const runtime = state?.runtime;
  const latestMarket = runtime?.latestMarket;
  const latestQuote = runtime?.latestQuote;
  const logs = runtime?.logs ?? [];
  const recent = state?.recentRuns ?? [];
  const requiredBalance = useMemo(() => (
    numberOr(form.pairBudgetUsdt, 2) + numberOr(form.balanceBufferUsdt, 0.1)
  ), [form.balanceBufferUsdt, form.pairBudgetUsdt]);
  const liveUnlocked = confirmation === LIVE_CONFIRM_VALUE;

  return <main className={styles.page}>
    <header className={styles.hero}>
      <div>
        <span className={styles.eyebrow}>BTC 5M LAB · ALWAYS-ON QUOTE CANARY</span>
        <h1>BTC／ETH 自動報價＋一次性實單武裝</h1>
        <p>背景會持續讀取價格並在進場窗內自動取得 signed quote；只有武裝後，下一個合格 quote 才會嘗試一次正式送單。</p>
      </div>
      <div className={styles.heroActions}>
        <a href="/">返回主監控</a>
        <span className={`${styles.statusPill} ${runtime?.armed ? styles.warn : apiError ? styles.bad : runtime?.running ? styles.good : styles.neutral}`}>
          {apiError ? "API OFFLINE" : runtime?.armed ? "LIVE ARMED" : runtime?.running ? "QUOTE MONITORING" : runtime?.phase ?? "STARTING"}
        </span>
      </div>
    </header>

    <section className={styles.warningBox}>
      <strong>武裝不是立即下單</strong>
      <p>按下武裝後，程式會等待下一個符合價格、深度、時間窗與 signed quote 成本限制的機會，再自動送出一次。報價不合格時會繼續等，不會放寬條件。</p>
      <p>真正送單前仍會重新檢查活動訂單與持倉。若另一個實單策略仍在使用同一錢包，武裝會保留但不會送單，直到錢包安全或你取消武裝。</p>
    </section>

    <section className={styles.summaryGrid}>
      <article><span>背景監控</span><strong>{runtime?.running ? "持續運行" : "未運行"}</strong><small>進場窗內約每 {state?.defaults.quoteIntervalSeconds ?? 1} 秒取得新 quote</small></article>
      <article><span>武裝狀態</span><strong>{runtime?.armed ? "等待自動送單" : "未武裝"}</strong><small>{runtime?.armedAt ? `武裝於 ${timeLabel(runtime.armedAt)}` : "平常只取得 quote，不送單"}</small></article>
      <article><span>最新 signed quote</span><strong>{latestQuote?.accepted ? fixed(latestQuote.costPerShare, 6) : "尚無合格 quote"}</strong><small>{latestQuote?.variant ?? latestQuote?.error ?? "等待進場窗"}</small></article>
      <article><span>目前市場</span><strong>{latestMarket?.secondsLeft == null ? "—" : `${fixed(latestMarket.secondsLeft, 1)} 秒`}</strong><small>BTC #{latestMarket?.btcMarketId ?? "—"} · ETH #{latestMarket?.ethMarketId ?? "—"}</small></article>
    </section>

    <section className={styles.console}>
      <div className={styles.sectionHead}>
        <div><span className={styles.eyebrow}>AUTO QUOTE · ONE-SHOT ARM</span><h2>監控條件</h2></div>
        <span className={styles.muted}>CeDeFi / Prediction Wallet · API 127.0.0.1:8767</span>
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
        <label>最低可用餘額
          <input value={`${requiredBalance.toFixed(2)} USDT`} readOnly />
        </label>
      </div>

      <div className={styles.actionGrid}>
        <button className={styles.secondaryButton} disabled={runtime?.armed} onClick={() => void saveConfig()}>
          儲存監控條件
          <small>背景 quote 不會停止；下一輪立即套用</small>
        </button>
        <button className={styles.quoteButton} disabled={!runtime?.armed} onClick={() => void disarm()}>
          取消正式單武裝
          <small>只取消送單；自動 quote 繼續運行</small>
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
          <button className={styles.liveButton} disabled={runtime?.armed || !liveUnlocked || !runtime?.running} onClick={() => void arm()}>
            武裝下一次符合條件正式單
            <small>不立即送單 · 合格 quote 出現時自動嘗試一次</small>
          </button>
        </div>
      </div>
    </section>

    <section className={styles.runtimeGrid}>
      <article className={styles.runtimeCard}>
        <div className={styles.sectionHead}><div><span className={styles.eyebrow}>AUTOPILOT STATE</span><h2>即時狀態</h2></div><span className={`${styles.statusPill} ${statusTone(runtime?.phase ?? "STARTING")}`}>{runtime?.phase ?? "STARTING"}</span></div>
        <dl>
          <div><dt>Quote 嘗試／通過</dt><dd>{runtime?.quoteAttempts ?? 0}／{runtime?.quoteAccepts ?? 0}</dd></div>
          <div><dt>正式送單嘗試</dt><dd>{runtime?.liveAttempts ?? 0}</dd></div>
          <div><dt>上次嘗試市場</dt><dd>{runtime?.lastAttemptMarketKey ?? "—"}</dd></div>
          <div><dt>最新警告</dt><dd>{runtime?.lastWarning ?? "—"}</dd></div>
          <div><dt>最新錯誤</dt><dd className={styles.errorText}>{runtime?.lastError ?? "—"}</dd></div>
          <div><dt>畫面狀態</dt><dd>{requestState}</dd></div>
        </dl>
      </article>
      <article className={styles.logCard}>
        <div className={styles.sectionHead}><div><span className={styles.eyebrow}>LIVE LOG</span><h2>監控與送單日誌</h2></div><span className={styles.muted}>{logs.length} 筆</span></div>
        <div className={styles.logList}>
          {logs.length === 0 ? <p className={styles.empty}>尚無執行記錄</p> : logs.map((item, index) => <div key={`${item.timestamp}-${index}`} className={item.level === "ERROR" ? styles.logError : item.level === "WARN" ? styles.logWarn : ""}>
            <time>{timeLabel(item.timestamp)}</time><b>{item.level}</b><span>{item.message}</span>
          </div>)}
        </div>
      </article>
    </section>

    <section className={styles.historySection}>
      <div className={styles.sectionHead}><div><span className={styles.eyebrow}>LOCAL SQLITE LEDGER</span><h2>最近報價／實單嘗試</h2></div><span className={styles.muted}>{recent.length} 筆</span></div>
      <div className={styles.tableWrap}>
        <table>
          <thead><tr><th>ID</th><th>模式</th><th>狀態</th><th>組合</th><th>市場</th><th>成本/share</th><th>訂單狀態</th><th>時間</th></tr></thead>
          <tbody>
            {recent.length === 0 ? <tr><td colSpan={8} className={styles.empty}>尚無紀錄</td></tr> : recent.map(run => <tr key={run.id}>
              <td>#{run.id}</td>
              <td>{run.mode}</td>
              <td><span className={`${styles.statusPill} ${statusTone(run.status)}`}>{run.status}</span></td>
              <td>{run.variant ?? "—"}</td>
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
