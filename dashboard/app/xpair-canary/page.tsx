"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";

const LIVE_SELECTION = "BTC_DOWN_ETH_UP";

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
};

type LatestQuote = {
  accepted?: boolean;
  variant?: string;
  costPerShare?: number;
  error?: string;
};

type RuntimeState = {
  running: boolean;
  phase: string;
  armed: boolean;
  armedAt?: string | null;
  lastAttemptMarketKey?: string | null;
  latestMarket?: LatestMarket | null;
  latestQuote?: LatestQuote | null;
  quoteAttempts?: number;
  quoteAccepts?: number;
  liveAttempts?: number;
  lastWarning?: string | null;
  lastError?: string | null;
  logs?: RuntimeLog[];
};

type CanaryRun = {
  id: number;
  mode: string;
  variant?: string | null;
  status: string;
  btc_market_id?: number | null;
  eth_market_id?: number | null;
  modeled_cost_per_share?: number | null;
  quoted_cost_per_share?: number | null;
  btc_order_status?: string | null;
  eth_order_status?: string | null;
  updated_at?: string | null;
};

type MonitorDefaults = {
  selection: string;
  pairBudgetUsdt: number;
  balanceBufferUsdt: number;
  maxTotalCost: number;
  maxLegReprice: number;
  slippageBps: number;
  quoteIntervalSeconds?: number;
  maximumPairBudgetUsdt?: number;
};

type SafetyState = {
  locked?: boolean;
  lockKind?: string;
  status?: string;
};

type PolicyState = {
  firstEligibleLiveExecution?: boolean;
  executionStartGuardSeconds?: number;
  executionMinimumSecondsLeft?: number;
  liveAllowedSelections?: string[];
  liveSelectionFixed?: boolean;
  liveFixedSelection?: string;
  paperSimulationTestsBothDirections?: boolean;
};

type CanaryState = {
  defaults: MonitorDefaults;
  runtime: RuntimeState;
  safety?: SafetyState;
  policy?: PolicyState;
  recentRuns: CanaryRun[];
};

type FormState = {
  pairBudgetUsdt: string;
  balanceBufferUsdt: string;
  maxTotalCost: string;
  maxLegReprice: string;
  slippageBps: string;
};

const DEFAULT_FORM: FormState = {
  pairBudgetUsdt: "3.00",
  balanceBufferUsdt: "0.10",
  maxTotalCost: "0.98",
  maxLegReprice: "0.01",
  slippageBps: "100",
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

function fixed(value?: number | null, digits = 4) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function timeLabel(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-TW", { hour12: false });
}

function statusTone(status: string) {
  if (/AUTO_QUOTE_READY|FILLED_BOTH|HOLD_PROFITABLE|MONITORING/.test(status)) return styles.good;
  if (/ERROR|INCOMPLETE|ONE_SIDED|REJECTED|INCIDENT|UNWIND/.test(status)) return styles.bad;
  if (/ARMED|PLACE|SUBMITTED|WAITING|TRACKING|QUOTE/.test(status)) return styles.warn;
  return styles.neutral;
}

export default function XPairCanaryPage() {
  const [state, setState] = useState<CanaryState | null>(null);
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [initialized, setInitialized] = useState(false);
  const [message, setMessage] = useState("等待 XPAIR API");
  const [apiError, setApiError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/xpair-canary"), { cache: "no-store" });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "XPAIR API 回應失敗");
      setState(payload);
      setApiError(null);
      setMessage(
        payload.safety?.locked
          ? `安全鎖：${payload.safety.status ?? payload.safety.lockKind ?? "LOCKED"}`
          : payload.runtime.armed
            ? "已武裝：BTC_DOWN_ETH_UP 首次符合後立即 Quote／送單"
            : payload.runtime.running
              ? `持續監控：${payload.runtime.phase}`
              : `監控尚未啟動：${payload.runtime.phase}`,
      );
      if (!initialized) {
        setForm({
          pairBudgetUsdt: payload.defaults.pairBudgetUsdt.toFixed(2),
          balanceBufferUsdt: payload.defaults.balanceBufferUsdt.toFixed(2),
          maxTotalCost: String(payload.defaults.maxTotalCost),
          maxLegReprice: String(payload.defaults.maxLegReprice),
          slippageBps: String(payload.defaults.slippageBps),
        });
        setInitialized(true);
      }
    } catch (caught) {
      const text = caught instanceof Error ? caught.message : "XPAIR API 無法連線";
      setApiError(text);
      setMessage(text);
    }
  }, [initialized]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const maxPairBudget = state?.defaults.maximumPairBudgetUsdt ?? 10;
  const pairBudget = Number(form.pairBudgetUsdt);
  const budgetError = !Number.isFinite(pairBudget)
    ? "請輸入有效的兩腿總預算"
    : pairBudget < 2
      ? "兩腿總預算不得低於 2.00 USDT"
      : pairBudget > maxPairBudget
        ? `目前安全上限為 ${maxPairBudget.toFixed(2)} USDT`
        : null;

  const requestPayload = useCallback(() => ({
    selection: LIVE_SELECTION,
    pairBudgetUsdt: numberOr(form.pairBudgetUsdt, 3),
    balanceBufferUsdt: numberOr(form.balanceBufferUsdt, 0.1),
    maxTotalCost: numberOr(form.maxTotalCost, 0.98),
    maxLegReprice: numberOr(form.maxLegReprice, 0.01),
    slippageBps: Math.round(numberOr(form.slippageBps, 100)),
    accountType: "CeDeFi",
  }), [form]);

  const saveConfig = async () => {
    if (budgetError) {
      setActionError(budgetError);
      return;
    }
    setActionError(null);
    try {
      const response = await fetch(apiUrl("/api/xpair-canary/config"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestPayload()),
      });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "監控條件儲存失敗");
      setState(payload);
      setMessage("條件已套用；實單固定 BTC_DOWN_ETH_UP，雙方向紙單繼續運行");
    } catch (caught) {
      const text = caught instanceof Error ? caught.message : "監控條件儲存失敗";
      setActionError(text);
      setMessage(text);
    }
  };

  const arm = async () => {
    if (budgetError) {
      setActionError(budgetError);
      return;
    }
    const startGuard = state?.policy?.executionStartGuardSeconds ?? 5;
    const minimumLeft = state?.policy?.executionMinimumSecondsLeft ?? 20;
    const accepted = window.confirm(
      `武裝後不會等待 180 秒。\n\n` +
      `實單固定為 BTC DOWN＋ETH UP。市場開始 ${startGuard.toFixed(0)} 秒後至剩餘 ${minimumLeft.toFixed(0)} 秒以前，只要此方向首次符合，程式就立即申請 signed Quote；Quote 通過便自動送出一次。\n\n` +
      `總預算上限 ${pairBudget.toFixed(2)} USDT。兩腿非原子，可能只成交一腿。確定武裝嗎？`,
    );
    if (!accepted) return;
    setActionError(null);
    try {
      const response = await fetch(apiUrl("/api/xpair-canary/arm"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-BTC-Lab-XPair-Live": "confirmed",
        },
        body: JSON.stringify(requestPayload()),
      });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "武裝失敗");
      setState(payload);
      setMessage("已武裝：BTC_DOWN_ETH_UP 下一個合格 tick 立即 Quote／嘗試送單");
    } catch (caught) {
      const text = caught instanceof Error ? caught.message : "武裝失敗";
      setActionError(text);
      setMessage(text);
    }
  };

  const disarm = async () => {
    setActionError(null);
    try {
      const response = await fetch(apiUrl("/api/xpair-canary/disarm"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "operator" }),
      });
      const payload = await response.json() as CanaryState & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "取消武裝失敗");
      setState(payload);
      setMessage("已取消正式單武裝；簿面與雙方向紙單仍持續運行");
    } catch (caught) {
      const text = caught instanceof Error ? caught.message : "取消武裝失敗";
      setActionError(text);
      setMessage(text);
    }
  };

  const runtime = state?.runtime;
  const market = runtime?.latestMarket;
  const quote = runtime?.latestQuote;
  const logs = runtime?.logs ?? [];
  const recent = state?.recentRuns ?? [];
  const safetyLocked = Boolean(state?.safety?.locked);
  const requiredBalance = useMemo(
    () => numberOr(form.pairBudgetUsdt, 3) + numberOr(form.balanceBufferUsdt, 0.1),
    [form.balanceBufferUsdt, form.pairBudgetUsdt],
  );
  const startGuard = state?.policy?.executionStartGuardSeconds ?? 5;
  const minimumLeft = state?.policy?.executionMinimumSecondsLeft ?? 20;

  return <main className={styles.page}>
    <header className={styles.hero}>
      <div>
        <span className={styles.eyebrow}>BTC 5M LAB · FIRST ELIGIBLE LIVE EXECUTION</span>
        <h1>BTC／ETH 常駐判價＋一次性實單武裝</h1>
        <p>實單固定 BTC DOWN＋ETH UP；模擬帳本才同時測試兩個相反方向。符合普通簿與 signed Quote 條件後，已武裝的實單會立即嘗試送出。</p>
      </div>
      <div className={styles.heroActions}>
        <a href="/">返回主監控</a>
        <span className={`${styles.statusPill} ${safetyLocked ? styles.bad : runtime?.armed ? styles.warn : apiError ? styles.bad : runtime?.running ? styles.good : styles.neutral}`}>
          {apiError ? "API OFFLINE" : safetyLocked ? "SAFETY LOCKED" : runtime?.armed ? "LIVE ARMED" : runtime?.running ? "FIRST ELIGIBLE" : runtime?.phase ?? "STARTING"}
        </span>
      </div>
    </header>

    <section className={styles.warningBox}>
      <strong>實單與雙方向模擬已分離</strong>
      <p>實單只使用 BTC_DOWN_ETH_UP；BTC_UP_ETH_DOWN 只存在於雙方向紙上比較，不會再進入武裝 payload。</p>
      <p>市場開始 {startGuard.toFixed(0)} 秒後至剩餘 {minimumLeft.toFixed(0)} 秒以前，每秒持續判定；實單方向符合就立即申請 signed Quote。兩腿成功取得 order ID 後，該市場停止後續 Quote。</p>
      {actionError ? <p className={styles.errorText}><strong>操作被拒絕：</strong> {actionError}</p> : null}
    </section>

    <section className={styles.summaryGrid}>
      <article><span>背景監控</span><strong>{runtime?.running ? "持續運行" : "未運行"}</strong><small>合格時約每 {state?.defaults.quoteIntervalSeconds ?? 1} 秒驗證 Quote</small></article>
      <article><span>武裝狀態</span><strong>{runtime?.armed ? "等待首個合格 tick" : "未武裝"}</strong><small>{runtime?.armedAt ? `武裝於 ${timeLabel(runtime.armedAt)}` : "紙單不受武裝影響"}</small></article>
      <article><span>最新 signed Quote</span><strong>{quote?.accepted ? fixed(quote.costPerShare, 6) : "尚無合格 Quote"}</strong><small>{quote?.variant ?? quote?.error ?? "等待符合"}</small></article>
      <article><span>目前市場</span><strong>{market?.secondsLeft == null ? "—" : `${fixed(market.secondsLeft, 1)} 秒`}</strong><small>BTC #{market?.btcMarketId ?? "—"} · ETH #{market?.ethMarketId ?? "—"}</small></article>
    </section>

    <section className={styles.console}>
      <div className={styles.sectionHead}>
        <div><span className={styles.eyebrow}>LIVE CONFIGURATION</span><h2>監控與實單條件</h2></div>
        <span className={styles.muted}>固定執行區：開始後 {startGuard.toFixed(0)} 秒～剩餘 {minimumLeft.toFixed(0)} 秒</span>
      </div>
      <div className={styles.formGrid}>
        <label>實單配對方向
          <input value="BTC DOWN + ETH UP" readOnly />
          <small className={styles.muted}>雙方向比較只在模擬帳本運作</small>
        </label>
        <label>兩腿總預算（USDT）
          <input type="number" min="2" max={maxPairBudget} step="0.01" value={form.pairBudgetUsdt} onChange={event => setForm(current => ({ ...current, pairBudgetUsdt: event.target.value }))} />
          <small className={budgetError ? styles.errorText : styles.muted}>{budgetError ?? `可設定 2.00～${maxPairBudget.toFixed(2)} USDT`}</small>
        </label>
        <label>餘額緩衝（USDT）
          <input type="number" min="0" max="5" step="0.01" value={form.balanceBufferUsdt} onChange={event => setForm(current => ({ ...current, balanceBufferUsdt: event.target.value }))} />
        </label>
        <label>最高總成本／share
          <input type="number" min="0.01" max="1.99" step="0.001" value={form.maxTotalCost} onChange={event => setForm(current => ({ ...current, maxTotalCost: event.target.value }))} />
        </label>
        <label>單腿最大重定價
          <input type="number" min="0" max="0.05" step="0.001" value={form.maxLegReprice} onChange={event => setForm(current => ({ ...current, maxLegReprice: event.target.value }))} />
        </label>
        <label>Slippage（bps）
          <input type="number" min="0" max="500" step="10" value={form.slippageBps} onChange={event => setForm(current => ({ ...current, slippageBps: event.target.value }))} />
        </label>
        <label>執行模式
          <input value="FIRST_ELIGIBLE_CONTINUOUS" readOnly />
        </label>
        <label>最低可用餘額
          <input value={`${requiredBalance.toFixed(2)} USDT`} readOnly />
        </label>
      </div>
      <div className={styles.actionGrid}>
        <button className={styles.secondaryButton} disabled={runtime?.armed || Boolean(budgetError)} onClick={() => void saveConfig()}>
          儲存監控條件
          <small>下一個 tick 立即套用</small>
        </button>
        <button className={styles.quoteButton} disabled={!runtime?.armed} onClick={() => void disarm()}>
          取消正式單武裝
          <small>簿面與雙方向紙單繼續運行</small>
        </button>
        <div className={styles.liveAction}>
          <button className={styles.liveButton} disabled={runtime?.armed || !runtime?.running || safetyLocked || Boolean(budgetError)} onClick={() => void arm()}>
            武裝首個合格機會
            <small>{safetyLocked ? "事故安全鎖生效中" : budgetError ?? "BTC DOWN＋ETH UP 符合即 Quote／送單"}</small>
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
          <div><dt>畫面狀態</dt><dd>{message}</dd></div>
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
