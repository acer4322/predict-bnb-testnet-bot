"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const LEAD_STRATEGIES = [
  {
    id: "R_POLY_LEAD_ENTRY",
    title: "Poly 先翻 · Binance 未翻先進",
    rule: "Polymarket 先跨過方向死區、Binance Prediction 尚未到同方向時，用 Binance Ask 進場；不提前賣，等官方結算。",
  },
  {
    id: "R_POLY_LEAD_EXIT",
    title: "Poly 先翻 · 持倉提前退場",
    rule: "進場規則與上組相同；持倉後 Polymarket 若先翻到反方向，立刻用 Binance Bid 紙上退場，否則等官方結算。",
  },
  {
    id: "R_POLY_GAP_SCALP",
    title: "Poly / Binance 落差高頻",
    rule: "Poly 選定方向的 mid 比 Binance 可買 Ask 高至少門檻時進場；Poly 下一次翻向就以 Binance Bid 賣出，沒有翻向就持有到官方結算。同一市場可多輪。",
  },
] as const;

const CONFIDENCE_SOURCES = [
  ["R_CALIBRATED_VALUE", "Calibrated Value · Poly 信心退場"],
  ["R_MICROPRICE", "Microprice · Poly 信心退場"],
  ["R_MICROPRICE_CONFIRM", "Microprice Confirm · Poly 信心退場"],
  ["R_FUTURES_LEAD", "Futures Lead · Poly 信心退場"],
  ["R_OFI", "OFI · Poly 信心退場"],
] as const;

type LeadStrategyId = typeof LEAD_STRATEGIES[number]["id"];
type ConfidenceSource = typeof CONFIDENCE_SOURCES[number][0];

type StrategySummary = {
  trades?: number;
  open?: number;
  closed?: number;
  wins?: number;
  losses?: number;
  winRate?: number | null;
  grossPnlUsdt?: number;
};

type StrategyTrade = {
  id?: number;
  strategy?: string;
  binance_market_id?: number;
  poly_market_slug?: string;
  side?: string;
  status?: string;
  entry_price?: number;
  exit_price?: number | null;
  stake_usdt?: number;
  gross_pnl_usdt?: number | null;
  opened_at_ms?: number;
  closed_at_ms?: number | null;
  entry_poly_up_mid?: number | null;
  entry_binance_up_mid?: number | null;
  entry_probability_gap?: number | null;
  entry_reason?: string;
  exit_reason?: string | null;
};

type ConfidenceStats = {
  sourceStrategy?: string;
  shadowStrategy?: string;
  sourceTrades?: number;
  active?: number;
  notEvaluable?: number;
  flipTriggered?: number;
  earlyExited?: number;
  finalized?: number;
  wins?: number;
  losses?: number;
  winRate?: number | null;
  realizedPnl?: number;
  counterfactualNoExitPnl?: number;
  avoidedLossUsdt?: number;
  sacrificedProfitUsdt?: number;
  netProtectionUsdt?: number;
  beneficialExits?: number;
  harmfulExits?: number;
  confidenceState?: string;
  currentSide?: string | null;
  currentMarketId?: number | null;
};

type ConfidenceRow = {
  id?: number;
  sourceTradeId?: number;
  sourceStrategy?: string;
  shadowStrategy?: string;
  marketId?: number;
  polyMarketSlug?: string | null;
  side?: string;
  status?: string;
  openedAt?: string;
  entryPrice?: number;
  entryPolyDirection?: string | null;
  entryPolyUpMid?: number | null;
  triggeredAtMs?: number | null;
  triggerPolyFrom?: string | null;
  triggerPolyTo?: string | null;
  triggerPolyUpMid?: number | null;
  exitPrice?: number | null;
  exitAtMs?: number | null;
  exitPnlUsdt?: number | null;
  effectivePnlUsdt?: number | null;
  noExitPnlUsdt?: number | null;
  avoidedLossUsdt?: number | null;
  sacrificedProfitUsdt?: number | null;
  netProtectionUsdt?: number | null;
  sourceFinalStatus?: string | null;
};

type ConfidenceState = {
  version?: string;
  status?: string;
  error?: string | null;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  forwardOnly?: boolean;
  feesIncluded?: boolean;
  forwardStartedAtMs?: number;
  rules?: Record<string, unknown>;
  strategies?: Partial<Record<ConfidenceSource, ConfidenceStats>> & Record<string, ConfidenceStats>;
  recent?: ConfidenceRow[];
};

type StrategyState = {
  status?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  feesIncluded?: boolean;
  parameters?: {
    polyUpThreshold?: number;
    polyDownThreshold?: number;
    scalpMinExecutableEdge?: number;
    paperStakeUsdt?: number;
    pollIntervalMs?: number;
    maxAlignmentSkewSeconds?: number;
    maxPolyAgeMs?: number;
    maxBinanceObservationAgeMs?: number;
    maxBinanceBookAgeMs?: number;
    maxBinanceBookSkewMs?: number;
    confidenceAttachMaxLagMs?: number;
  };
  runtime?: {
    status?: string;
    error?: string | null;
    updatedAtMs?: number | null;
    polyUpMid?: number | null;
    binanceUpMid?: number | null;
    probabilityGap?: number | null;
    polyDirection?: string | null;
    binanceDirection?: string | null;
    binanceMarketId?: number | null;
    polyMarketSlug?: string | null;
    secondsLeftSkew?: number | null;
    aligned?: boolean;
  };
  lastFlip?: {
    atMs?: number;
    from?: string;
    to?: string;
    polyUpMid?: number;
    binanceUpMid?: number;
    probabilityGap?: number;
  } | null;
  summaries?: Partial<Record<LeadStrategyId, StrategySummary>>;
  openPositions?: StrategyTrade[];
  recentTrades?: StrategyTrade[];
  confidenceShadows?: ConfidenceState;
};

type ApiPayload = { ok?: boolean; state?: StrategyState; error?: string };

function num(value: unknown): number | null {
  if (value == null || value === "") return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function prob(value: unknown) {
  const result = num(value);
  return result == null ? "—" : result.toFixed(3);
}

function signed(value: unknown, digits = 3) {
  const result = num(value);
  return result == null ? "—" : `${result >= 0 ? "+" : ""}${result.toFixed(digits)}`;
}

function money(value: unknown, digits = 2) {
  const result = num(value);
  return result == null ? "—" : `${result >= 0 ? "+" : "−"}$${Math.abs(result).toFixed(digits)}`;
}

function pct(value: unknown) {
  const result = num(value);
  return result == null ? "—" : `${(result * 100).toFixed(1)}%`;
}

function clock(value: unknown) {
  const result = num(value);
  return result == null ? "—" : new Date(result).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function sourceClock(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : value;
}

function confidenceLabel(value: string | null | undefined) {
  const state = String(value ?? "WAITING_SOURCE_ENTRY");
  if (state === "SUPPORTED") return "Poly 同方向支持";
  if (state === "POLY_NEUTRAL") return "Poly 中性";
  if (state === "OPPOSED_NO_NEW_FLIP") return "Poly 目前反向 · 尚非新翻轉";
  if (state === "EXIT_PENDING") return "Poly 已翻轉 · 等待可成交 Bid";
  if (state === "EXITED_ON_POLY_FLIP") return "已依 Poly 翻轉退出";
  if (state === "ATTACHING") return "正在綁定來源單";
  if (state === "WAITING_SOURCE_ENTRY") return "等待來源策略進場";
  if (state.startsWith("NOT_EVALUABLE")) return `無法評估 · ${state.replace("NOT_EVALUABLE_", "")}`;
  if (state === "FINALIZED_EXIT") return "已完成退出反事實";
  if (state === "FINALIZED_NO_EXIT") return "未翻轉 · 原策略收尾";
  if (state === "FINALIZED_TRIGGER_MISSED") return "曾觸發但無可成交 Bid";
  return state;
}

function confidenceTone(value: string | null | undefined) {
  const state = String(value ?? "");
  if (/SUPPORTED|FINALIZED_NO_EXIT/.test(state)) return "positive";
  if (/EXIT_PENDING|OPPOSED|MISSED/.test(state)) return "negative";
  return "";
}

export default function OracleCrossStrategyPanel() {
  const [tabHost, setTabHost] = useState<Element | null>(null);
  const [panelHost, setPanelHost] = useState<Element | null>(null);
  const [active, setActive] = useState(false);
  const [state, setState] = useState<StrategyState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const locate = () => {
      const tabs = document.querySelector(".strategy-tabs");
      setTabHost(tabs);
      setPanelHost(tabs?.closest("form") ?? null);
    };
    locate();
    const observer = new MutationObserver(locate);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!tabHost) return;
    const onNativeTab = (event: Event) => {
      const target = event.target instanceof Element ? event.target.closest("button[role='tab']") : null;
      if (target && !target.classList.contains("poly-cross-tab")) setActive(false);
    };
    tabHost.addEventListener("click", onNativeTab, true);
    return () => tabHost.removeEventListener("click", onNativeTab, true);
  }, [tabHost]);

  useEffect(() => {
    document.body.classList.toggle("poly-cross-market-active", active);
    if (tabHost) {
      tabHost.querySelectorAll("button[role='tab']:not(.poly-cross-tab)").forEach(button => {
        button.setAttribute("aria-selected", active ? "false" : button.classList.contains("active") ? "true" : "false");
      });
    }
    return () => document.body.classList.remove("poly-cross-market-active");
  }, [active, tabHost]);

  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    const load = async () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch("/api/oracle-cross-strategies", { cache: "no-store", signal: controller.signal });
        const payload = await response.json() as ApiPayload;
        if (!response.ok || !payload.ok || !payload.state) throw new Error(payload.error ?? `HTTP ${response.status}`);
        if (!alive) return;
        setState(payload.state);
        setError(null);
      } catch (caught) {
        if (!alive || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
    const timer = window.setInterval(load, active ? 1000 : 5000);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [active]);

  const confidence = state?.confidenceShadows;
  const confidenceStrategies = confidence?.strategies ?? {};
  const confidenceRecent = confidence?.recent ?? [];
  const overallProtection = useMemo(() => {
    return CONFIDENCE_SOURCES.reduce((total, [source]) => {
      const stats = confidenceStrategies[source] ?? {};
      total.avoided += num(stats.avoidedLossUsdt) ?? 0;
      total.sacrificed += num(stats.sacrificedProfitUsdt) ?? 0;
      total.net += num(stats.netProtectionUsdt) ?? 0;
      total.exits += stats.earlyExited ?? 0;
      return total;
    }, { avoided: 0, sacrificed: 0, net: 0, exits: 0 });
  }, [confidenceStrategies]);

  const runtime = state?.runtime;
  const params = state?.parameters;
  const recent = state?.recentTrades ?? [];

  const tabPortal = tabHost ? createPortal(
    <button
      type="button"
      role="tab"
      id="poly-cross-market-tab"
      aria-controls="poly-cross-market-panel"
      aria-selected={active}
      className={active ? "active shadow-tag poly-cross-tab" : "shadow-tag poly-cross-tab"}
      onClick={() => setActive(true)}
    >
      <strong>Poly 跨市場</strong>
      <span>Lead／Gap＋5 組 Poly 信心退場 Shadow</span>
    </button>,
    tabHost,
  ) : null;

  const panelPortal = panelHost ? createPortal(
    <div
      role="tabpanel"
      id="poly-cross-market-panel"
      aria-labelledby="poly-cross-market-tab"
      className="m-exit-experiment research-forward-panel oracle-cross-independent-panel"
      hidden={!active}
    >
      <style>{`
        body.poly-cross-market-active .strategy-console-heading{display:none!important}
        body.poly-cross-market-active form>[role="tabpanel"]:not(.oracle-cross-independent-panel){display:none!important}
        body.poly-cross-market-active section.ledger{display:none!important}
        body.poly-cross-market-active .strategy-tabs>button:not(.poly-cross-tab).active{filter:saturate(.35);box-shadow:none!important;opacity:.72}
        .oracle-cross-independent-panel{margin-top:4px}
        .oracle-cross-independent-panel .poly-runtime{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
        .oracle-cross-independent-panel .poly-runtime article{padding:12px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
        .oracle-cross-independent-panel .poly-runtime span,.oracle-cross-independent-panel .poly-runtime small{display:block;color:#91a0bb}
        .oracle-cross-independent-panel .poly-runtime strong{display:block;margin:4px 0}
        .oracle-cross-independent-panel .poly-protection{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}
        .oracle-cross-independent-panel .poly-protection div{padding:9px;border-radius:10px;background:rgba(126,145,178,.08)}
        .oracle-cross-independent-panel .poly-protection span,.oracle-cross-independent-panel .poly-protection strong{display:block}
        .oracle-cross-independent-panel .poly-section{margin-top:22px}
        @media(max-width:720px){.oracle-cross-independent-panel .poly-protection{grid-template-columns:1fr}}
      `}</style>

      <section className="strategy-family-intro m-exit-intro">
        <div><span className="eyebrow">POLYMARKET CROSS-MARKET · FORWARD PAPER</span><h3>跨市場領先／落差＋Polymarket 信心退場</h3></div>
        <p>全部與正式實單隔離。Lead／Gap 研究 Poly 是否先於 Binance Prediction 定價；五組信心 Shadow 則只鏡像來源策略真正開出的 Paper 單，來源單進場後若出現新的 Poly confident flip 才提前按 Binance Bid 退出。</p>
      </section>

      <section className="m-exit-rules" aria-label="Polymarket 跨市場即時狀態">
        <div className="m-exit-rules-head"><div><span className="eyebrow">POLY SIGNAL · BINANCE EXECUTION</span><h3>即時信號與資料品質</h3></div><span className="m-exit-api-state live">{state?.status ?? "OFFLINE"}</span></div>
        <div className="poly-runtime">
          <article><span>Polymarket UP mid</span><strong>{prob(runtime?.polyUpMid)}</strong><small>{runtime?.polyDirection ?? "NEUTRAL"}</small></article>
          <article><span>Binance UP mid</span><strong>{prob(runtime?.binanceUpMid)}</strong><small>{runtime?.binanceDirection ?? "NEUTRAL"}</small></article>
          <article><span>Poly − Binance</span><strong>{signed(runtime?.probabilityGap)}</strong><small>5m 對齊差 {num(runtime?.secondsLeftSkew)?.toFixed(2) ?? "—"} s</small></article>
          <article><span>最近 Poly 翻轉</span><strong>{state?.lastFlip ? `${state.lastFlip.from} → ${state.lastFlip.to}` : "—"}</strong><small>{state?.lastFlip?.atMs ? clock(state.lastFlip.atMs) : "等待 confident flip"}</small></article>
        </div>
        <small>Flip deadband：UP ≥ {prob(params?.polyUpThreshold)}、DOWN ≤ {prob(params?.polyDownThreshold)}；Poly age ≤ {params?.maxPolyAgeMs ?? "—"}ms · Binance observation ≤ {params?.maxBinanceObservationAgeMs ?? "—"}ms · book ≤ {params?.maxBinanceBookAgeMs ?? "—"}ms · skew ≤ {params?.maxBinanceBookSkewMs ?? "—"}ms。</small>
      </section>

      <section className="poly-section" aria-label="Polymarket 信心退場 Shadow">
        <div className="strategy-family-intro compact">
          <div><span className="eyebrow">SOURCE-MIRROR CONFIDENCE EXIT · FIVE SHADOWS</span><h3>主要策略 × Polymarket 信心退場</h3></div>
          <p>不改原策略進場。只有來源策略已經開單後，Polymarket 才從一個 confident 方向真正翻到相反 confident 方向，而且新方向反對持倉時，Shadow 才以當下 Binance held-side Bid 退出；沒有新翻轉就完全照來源策略結果收尾。</p>
        </div>

        <div className="poly-runtime">
          <article><span>總避免虧損</span><strong className="positive">{money(overallProtection.avoided)}</strong><small>只計已完成反事實的來源敗單</small></article>
          <article><span>總犧牲獲利</span><strong className="negative">{money(overallProtection.sacrificed)}</strong><small>只計已完成反事實的來源勝單</small></article>
          <article><span>總淨保護</span><strong className={overallProtection.net >= 0 ? "positive" : "negative"}>{money(overallProtection.net)}</strong><small>Shadow PnL − 不退場 PnL</small></article>
          <article><span>Poly 提前退出</span><strong>{overallProtection.exits}</strong><small>{confidence?.version ?? "等待後端"} · fees {confidence?.feesIncluded ? "included" : "unknown"}</small></article>
        </div>

        <div className="m-exit-summary-grid research-strategy-grid">
          {CONFIDENCE_SOURCES.map(([source, title]) => {
            const stats = confidenceStrategies[source] ?? {};
            const net = num(stats.netProtectionUsdt) ?? 0;
            return <article className="m-exit-card cyan" key={source} data-poly-confidence-source={source}>
              <div className="m-exit-card-head"><div><span className="eyebrow">{source} · POLY EXIT SHADOW</span><h3>{title}</h3></div><span className="m-exit-id">PAPER ONLY</span></div>
              <div className="m-exit-primary-stats">
                <div><span>Poly 信心後收益</span><strong className={(stats.realizedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(stats.realizedPnl)}</strong></div>
                <div><span>若不退場 PnL</span><strong className={(stats.counterfactualNoExitPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(stats.counterfactualNoExitPnl)}</strong></div>
                <div><span>Shadow 勝率</span><strong>{pct(stats.winRate)}</strong></div>
              </div>
              <div className={`continuous-calibration-state ${confidenceTone(stats.confidenceState)}`}>
                <span>目前 Poly 信心</span>
                <strong className={confidenceTone(stats.confidenceState)}>{confidenceLabel(stats.confidenceState)}</strong>
                <small>{stats.currentMarketId ? `Market #${stats.currentMarketId} · ${stats.currentSide ?? "—"}` : "目前沒有來源持倉"}</small>
              </div>
              <div className="poly-protection">
                <div><span>避免虧損</span><strong className="positive">{money(stats.avoidedLossUsdt)}</strong></div>
                <div><span>犧牲獲利</span><strong className="negative">{money(stats.sacrificedProfitUsdt)}</strong></div>
                <div><span>淨保護</span><strong className={net >= 0 ? "positive" : "negative"}>{money(net)}</strong></div>
              </div>
              <p>來源 {stats.sourceTrades ?? 0} · 活躍 {stats.active ?? 0} · Poly 翻轉 {stats.flipTriggered ?? 0} · 已退出 {stats.earlyExited ?? 0} · 已完成比較 {stats.finalized ?? 0}</p>
              <small>有效退出：改善 {stats.beneficialExits ?? 0}／傷害 {stats.harmfulExits ?? 0} · 無法評估 {stats.notEvaluable ?? 0}</small>
              <small>避免虧損／犧牲獲利使用來源策略最後實際 PnL 作「不退場」反事實；淨保護則直接計算提前退出結果減去原來源結果。</small>
            </article>;
          })}
        </div>

        <section className="shadow-tag-live-orders" style={{ marginTop: 16 }}>
          <div><span className="eyebrow">RECENT SOURCE-MIRROR DECISIONS</span><h3>最近 60 筆 Poly 信心 Shadow</h3></div>
          <div className="table-scroll"><table><thead><tr><th>時間／市場</th><th>來源／方向</th><th>進場／Poly</th><th>翻轉／退出</th><th>提前退出 PnL</th><th>若不退場</th><th>淨保護</th></tr></thead><tbody>
            {confidenceRecent.length === 0
              ? <tr><td colSpan={7} className="empty">等待五個來源策略建立新的 forward Paper 單。</td></tr>
              : confidenceRecent.map(row => <tr key={row.id}>
                  <td>{sourceClock(row.openedAt)}<small>#{row.marketId ?? "—"} · source #{row.sourceTradeId ?? "—"}</small></td>
                  <td>{row.sourceStrategy ?? "—"}<small className={row.side === "UP" ? "positive" : "negative"}>{row.side ?? "—"} · {row.status ?? "—"}</small></td>
                  <td>{prob(row.entryPrice)}<small>Poly {row.entryPolyDirection ?? "NEUTRAL"} · UP {prob(row.entryPolyUpMid)}</small></td>
                  <td>{row.triggeredAtMs ? `${row.triggerPolyFrom ?? "—"} → ${row.triggerPolyTo ?? "—"}` : "無翻轉"}<small>{row.exitAtMs ? `${clock(row.exitAtMs)} · Bid ${prob(row.exitPrice)}` : row.triggeredAtMs ? "等待／未取得 Bid" : "持續鏡像"}</small></td>
                  <td className={(num(row.exitPnlUsdt) ?? num(row.effectivePnlUsdt) ?? 0) >= 0 ? "positive" : "negative"}>{row.exitAtMs ? money(row.exitPnlUsdt, 4) : "—"}<small>{row.exitAtMs ? "實際 Shadow 提前退出" : "未提前退出"}</small></td>
                  <td className={(num(row.noExitPnlUsdt) ?? 0) >= 0 ? "positive" : "negative"}>{money(row.noExitPnlUsdt, 4)}<small>{row.sourceFinalStatus ?? "等待來源結算"}</small></td>
                  <td className={(num(row.netProtectionUsdt) ?? 0) >= 0 ? "positive" : "negative"}>{row.noExitPnlUsdt == null ? "—" : money(row.netProtectionUsdt, 4)}<small>避損 {money(row.avoidedLossUsdt, 4)} · 犧牲 {money(row.sacrificedProfitUsdt, 4)}</small></td>
                </tr>)}
          </tbody></table></div>
        </section>
      </section>

      <section className="poly-section" aria-label="Polymarket lead Paper strategies">
        <div className="strategy-family-intro compact">
          <div><span className="eyebrow">POLYMARKET LEAD · THREE FORWARD PAPER STRATEGIES</span><h3>原三組跨市場領先／落差策略</h3></div>
          <p>這三組是獨立新進場實驗，不是上方來源鏡像 Shadow：Ask 進、Bid 退；Lead Entry / Exit 每市場最多一筆，高頻 Gap 同市場可多輪。</p>
        </div>
        <div className="m-exit-summary-grid research-strategy-grid">
          {LEAD_STRATEGIES.map(strategy => {
            const summary = state?.summaries?.[strategy.id] ?? {};
            const pnl = num(summary.grossPnlUsdt) ?? 0;
            return <article className="m-exit-card cyan" key={strategy.id}>
              <div className="m-exit-card-head"><div><span className="eyebrow">{strategy.id}</span><h3>{strategy.title}</h3></div><span className="m-exit-id">PAPER ONLY</span></div>
              <p>{strategy.rule}</p>
              <div className="m-exit-primary-stats">
                <div><span>交易／持倉</span><strong>{summary.trades ?? 0} / {summary.open ?? 0}</strong></div>
                <div><span>勝率</span><strong>{pct(summary.winRate)}</strong></div>
                <div><span>Gross PnL</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl, 4)}</strong></div>
              </div>
            </article>;
          })}
        </div>
        <small>高頻可執行落差門檻 {prob(params?.scalpMinExecutableEdge)}；每筆 Paper {num(params?.paperStakeUsdt)?.toFixed(2) ?? "—"} USDT；輪詢 {params?.pollIntervalMs ?? "—"} ms。{state?.feesIncluded === false ? "原三組目前仍顯示 gross PnL，尚未扣 Prediction fee。" : ""}</small>

        <div className="table-scroll" style={{ marginTop: 14 }}>
          <table><thead><tr><th>時間</th><th>策略</th><th>市場</th><th>方向</th><th>進場</th><th>退出</th><th>Poly / Binance UP mid</th><th>狀態／PnL</th></tr></thead><tbody>
            {recent.length === 0
              ? <tr><td colSpan={8} className="empty">等待新的 Polymarket lead / gap 訊號。</td></tr>
              : recent.slice(0, 20).map(trade => <tr key={trade.id}>
                  <td>{clock(trade.opened_at_ms)}</td>
                  <td>{trade.strategy}</td>
                  <td>#{trade.binance_market_id ?? "—"}</td>
                  <td className={trade.side === "UP" ? "positive" : "negative"}>{trade.side ?? "—"}</td>
                  <td>{prob(trade.entry_price)}</td>
                  <td>{prob(trade.exit_price)}</td>
                  <td>{prob(trade.entry_poly_up_mid)} / {prob(trade.entry_binance_up_mid)}<small style={{ display: "block" }}>gap {signed(trade.entry_probability_gap)}</small></td>
                  <td className={(num(trade.gross_pnl_usdt) ?? 0) >= 0 ? "positive" : "negative"}>{trade.status ?? "—"} · {money(trade.gross_pnl_usdt, 4)}</td>
                </tr>)}
          </tbody></table>
        </div>
      </section>

      {(error || runtime?.error || confidence?.error) && <p style={{ marginTop: 12, color: "#ffb45c", fontSize: 12 }}>{error ?? runtime?.error ?? confidence?.error}</p>}
      <p style={{ marginTop: 10, fontSize: 11, color: "rgba(215,225,245,.52)" }}>
        信心 Shadow 為 forward-only：啟用前的舊來源交易不回填；若 sidecar 太晚才綁定來源單或資料缺失，標成 NOT_EVALUABLE，不把缺資料誤算成 Poly 保護效果。所有 Poly 退出只改獨立 cross_oracle.db Shadow 帳本。
      </p>
    </div>,
    panelHost,
  ) : null;

  return <>{tabPortal}{panelPortal}</>;
}
