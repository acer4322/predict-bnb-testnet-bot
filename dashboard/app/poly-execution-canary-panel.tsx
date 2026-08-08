"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

const STRATEGIES = [
  "R_POLY_LEAD_ENTRY",
  "R_POLY_LEAD_EXIT",
  "R_POLY_GAP_SCALP",
] as const;

type StrategyId = typeof STRATEGIES[number];

type Summary = {
  attempts?: number;
  completed?: number;
  wouldSubmit?: number;
  wouldSubmitRate?: number | null;
  quoteRejected?: number;
  priceMoved?: number;
  signalGone?: number;
  capacityFailed?: number;
  expiryFailed?: number;
  avgQuoteRttMs?: number | null;
  maxQuoteRttMs?: number | null;
  avgSignalToQuoteMs?: number | null;
  maxSignalToQuoteMs?: number | null;
  avgAdversePriceMove?: number | null;
  exitSimulations?: number;
  exitFinalized?: number;
  exitBestEvaluable?: number;
  avgBestVisibleFinalPnlUsdt?: number | null;
  avgNoSellFinalPnlUsdt?: number | null;
  legacySellQuoteRejectedExcluded?: number;
};

type Attempt = {
  id?: number;
  trade_id?: number;
  strategy?: string;
  phase?: string;
  binance_market_id?: number;
  side?: string;
  signal_at_ms?: number;
  paper_price?: number;
  paper_stake_usdt?: number;
  simulated_entry_stake_usdt?: number | null;
  quote_average_price?: number | null;
  price_limit?: number | null;
  quote_rtt_ms?: number | null;
  signal_to_quote_start_ms?: number | null;
  signal_to_quote_response_ms?: number | null;
  quote_coverage_ratio?: number | null;
  quote_expiry_headroom_ms?: number | null;
  adverse_price_move?: number | null;
  executable_edge_after_quote?: number | null;
  would_submit?: number;
  status?: string;
  reason?: string | null;

  execution_mode?: string | null;
  sim_exit_observed_at_ms?: number | null;
  sim_exit_observation_lag_ms?: number | null;
  sim_entry_cost_usdt?: number | null;
  sim_entry_shares?: number | null;
  sim_exit_bid?: number | null;
  sim_exit_bid_size?: number | null;
  sim_best_case_evaluable?: number | null;
  sim_best_fill_shares?: number | null;
  sim_best_fill_ratio?: number | null;
  sim_best_unfilled_shares?: number | null;
  sim_best_exit_net_usdt?: number | null;
  sim_official_winner?: string | null;
  sim_best_final_pnl_usdt?: number | null;
  sim_no_sell_final_pnl_usdt?: number | null;
  sim_settlement_status?: string | null;
};

type Canary = {
  version?: string;
  status?: string;
  error?: string | null;
  signedQuoteScope?: string;
  sellSignedQuoteRequested?: boolean;
  exitExecutionMode?: string;
  entryQualityPrimary?: boolean;
  placeOrderCalled?: boolean;
  strategies?: Partial<Record<StrategyId, Summary>>;
  recent?: Attempt[];
  recentExitSimulations?: Attempt[];
  sizing?: {
    configuredEntryStakesUsdt?: Partial<Record<StrategyId, number>> & Record<string, number>;
    liveRulesAgeMs?: number | null;
    liveRulesError?: string | null;
  };
};

type Payload = { ok?: boolean; state?: { quoteCanary?: Canary }; error?: string };

function n(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function ms(value: unknown) {
  const valueN = n(value);
  return valueN == null ? "—" : `${valueN.toFixed(valueN < 100 ? 1 : 0)} ms`;
}

function pct(value: unknown) {
  const valueN = n(value);
  return valueN == null ? "—" : `${(valueN * 100).toFixed(1)}%`;
}

function price(value: unknown) {
  const valueN = n(value);
  return valueN == null ? "—" : valueN.toFixed(3);
}

function money(value: unknown, digits = 4) {
  const valueN = n(value);
  if (valueN == null) return "—";
  return `${valueN >= 0 ? "+" : "−"}$${Math.abs(valueN).toFixed(digits)}`;
}

function signed(value: unknown, digits = 4) {
  const valueN = n(value);
  return valueN == null ? "—" : `${valueN >= 0 ? "+" : ""}${valueN.toFixed(digits)}`;
}

function clock(value: unknown) {
  const valueN = n(value);
  if (valueN == null) return "—";
  const date = new Date(valueN);
  return `${date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function tone(status: string | null | undefined) {
  const text = String(status ?? "");
  if (/^PASS_/.test(text) || /FINALIZED/.test(text)) return "positive";
  if (/FAIL_|REJECTED|ERROR/.test(text)) return "negative";
  return "";
}

export default function PolyExecutionCanaryPanel() {
  const [host, setHost] = useState<Element | null>(null);
  const [canary, setCanary] = useState<Canary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const locate = () => {
      setHost(document.querySelector(
        "#poly-cross-market-panel .poly-section[aria-label='Polymarket lead Paper strategies']",
      ));
    };
    locate();
    const observer = new MutationObserver(locate);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    const load = async () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch("/api/oracle-cross-strategies", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await response.json() as Payload;
        if (!response.ok || !payload.ok || !payload.state) {
          throw new Error(payload.error ?? `HTTP ${response.status}`);
        }
        if (!alive) return;
        setCanary(payload.state.quoteCanary ?? null);
        setError(null);
      } catch (caught) {
        if (!alive || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
    const timer = window.setInterval(load, 1000);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, []);

  if (!host) return null;

  const entries = canary?.recent ?? [];
  const exits = canary?.recentExitSimulations ?? [];
  const configured = canary?.sizing?.configuredEntryStakesUsdt ?? {};

  return createPortal(
    <section className="poly-execution-canary" style={{ marginTop: 20, paddingTop: 18, borderTop: "1px solid rgba(126,145,178,.22)" }}>
      <style>{`
        .poly-execution-canary .pec-head{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;align-items:flex-start}
        .poly-execution-canary .pec-head p{max-width:900px;color:#91a0bb;margin:6px 0 0}
        .poly-execution-canary .pec-badge{padding:7px 10px;border:1px solid rgba(126,145,178,.28);border-radius:999px;font:700 11px/1 var(--font-mono)}
        .poly-execution-canary .pec-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin:14px 0}
        .poly-execution-canary .pec-card{padding:13px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
        .poly-execution-canary .pec-card span,.poly-execution-canary .pec-card small{display:block;color:#91a0bb}
        .poly-execution-canary .pec-card strong{display:block;margin:4px 0}
        .poly-execution-canary .pec-row{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:8px}
        .poly-execution-canary .pec-row>div{padding:8px;border-radius:9px;background:rgba(126,145,178,.08)}
        .poly-execution-canary .scenario-note{margin:10px 0;color:#91a0bb;font-size:12px}
        .poly-execution-canary table small{display:block;color:#91a0bb;margin-top:2px}
        @media(max-width:720px){.poly-execution-canary .pec-row{grid-template-columns:1fr}}
      `}</style>

      <div className="pec-head">
        <div>
          <span className="eyebrow">ENTRY REAL QUOTE · EXIT COUNTERFACTUAL SCENARIOS</span>
          <h3>Poly 實際進場品質／退出壓力模擬</h3>
          <p>進場仍真的呼叫 Binance Prediction signed BUY get-quote，量測價格、容量、RTT、expiry 與訊號是否已消失；退出不再呼叫 SELL quote，因 Paper BUY 沒有建立真實 shares。退出改用「最佳可見第一檔」與「完全賣不掉持有到官方結算」兩個情境。</p>
        </div>
        <span className={`pec-badge ${canary?.status === "READY" ? "positive" : ""}`}>{canary?.status ?? "WAITING"}</span>
      </div>

      <div className="pec-grid">
        {STRATEGIES.map(strategy => {
          const summary = canary?.strategies?.[strategy] ?? {};
          return <article className="pec-card" key={strategy}>
            <span className="eyebrow">{strategy}</span>
            <strong>進場 Signed Quote 可執行率</strong>
            <div className="pec-row">
              <div><span>可送單</span><strong className={(summary.wouldSubmitRate ?? 0) >= .8 ? "positive" : ""}>{summary.wouldSubmit ?? 0}/{summary.completed ?? 0}</strong><small>{pct(summary.wouldSubmitRate)}</small></div>
              <div><span>Quote RTT</span><strong>{ms(summary.avgQuoteRttMs)}</strong><small>max {ms(summary.maxQuoteRttMs)}</small></div>
              <div><span>訊號→Quote</span><strong>{ms(summary.avgSignalToQuoteMs)}</strong><small>max {ms(summary.maxSignalToQuoteMs)}</small></div>
            </div>
            <small>實測本金 {configured[strategy] == null ? "Paper fallback" : `${configured[strategy].toFixed(2)} USDT`} · 價格追掉 {summary.priceMoved ?? 0} · 訊號消失 {summary.signalGone ?? 0} · 容量不足 {summary.capacityFailed ?? 0} · API 拒絕 {summary.quoteRejected ?? 0}</small>
            <small>退出模擬 {summary.exitSimulations ?? 0} · 已等到官方結算 {summary.exitFinalized ?? 0} · 第一檔可評估 {summary.exitBestEvaluable ?? 0} · 舊 SELL reject 排除 {summary.legacySellQuoteRejectedExcluded ?? 0}</small>
          </article>;
        })}
      </div>

      <div className="table-scroll">
        <table>
          <thead><tr><th>進場時間</th><th>策略／方向</th><th>市場</th><th>Paper → Signed BUY</th><th>RTT</th><th>訊號→回應</th><th>容量／Expiry</th><th>進場判定</th></tr></thead>
          <tbody>
            {entries.length === 0
              ? <tr><td colSpan={8} className="empty">等待新的 Poly 進場訊號。</td></tr>
              : entries.slice(0, 30).map(row => <tr key={row.id}>
                  <td>{clock(row.signal_at_ms)}<small>trade #{row.trade_id ?? "—"}</small></td>
                  <td>{row.strategy ?? "—"}<small>{row.side ?? "—"}</small></td>
                  <td>#{row.binance_market_id ?? "—"}</td>
                  <td>{price(row.paper_price)} → {price(row.quote_average_price)}<small>limit {price(row.price_limit)} · adverse {signed(row.adverse_price_move)}</small></td>
                  <td>{ms(row.quote_rtt_ms)}</td>
                  <td>{ms(row.signal_to_quote_response_ms)}<small>start {ms(row.signal_to_quote_start_ms)}</small></td>
                  <td>{pct(row.quote_coverage_ratio)}<small>expiry {ms(row.quote_expiry_headroom_ms)}</small></td>
                  <td className={tone(row.status)}>{row.status ?? "—"}<small>{row.reason ?? "—"}{row.executable_edge_after_quote != null ? ` · edge ${signed(row.executable_edge_after_quote)}` : ""}</small></td>
                </tr>)}
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 20 }}>
        <span className="eyebrow">EXIT EXECUTION STRESS · NO SIGNED SELL QUOTE</span>
        <h3>退出：最佳可見 vs 完全賣不掉</h3>
        <p className="scenario-note">「最佳可見」也不是完美成交：只允許吃當下 held-side 最佳 Bid，而且成交 shares 不得超過可見第一檔 Bid size；剩下的部位持有到官方結算。「完全賣不掉」則 0 shares 成交，整筆持有到官方結算。官方結果出來後才回填兩個最終 PnL。</p>
        <div className="table-scroll">
          <table>
            <thead><tr><th>退出訊號</th><th>策略／市場</th><th>進場實測</th><th>退出 Bid／深度</th><th>最佳可見成交</th><th>最佳可見最終</th><th>完全賣不掉最終</th><th>官方結果</th></tr></thead>
            <tbody>
              {exits.length === 0
                ? <tr><td colSpan={8} className="empty">等待 Lead Exit / Gap Scalp 的下一個退出訊號。</td></tr>
                : exits.slice(0, 30).map(row => <tr key={row.id}>
                    <td>{clock(row.signal_at_ms)}<small>觀測 lag {ms(row.sim_exit_observation_lag_ms)}</small></td>
                    <td>{row.strategy ?? "—"}<small>#{row.binance_market_id ?? "—"} · {row.side ?? "—"}</small></td>
                    <td>{money(row.sim_entry_cost_usdt)}<small>{n(row.sim_entry_shares)?.toFixed(6) ?? "—"} shares</small></td>
                    <td>{price(row.sim_exit_bid)}<small>visible {n(row.sim_exit_bid_size)?.toFixed(6) ?? "—"} shares</small></td>
                    <td>{row.sim_best_case_evaluable ? pct(row.sim_best_fill_ratio) : "不可評估"}<small>{row.sim_best_fill_shares == null ? "無可靠第一檔深度" : `${n(row.sim_best_fill_shares)?.toFixed(6)} sold · ${n(row.sim_best_unfilled_shares)?.toFixed(6)} hold`}</small></td>
                    <td className={(n(row.sim_best_final_pnl_usdt) ?? 0) >= 0 ? "positive" : "negative"}>{row.sim_best_final_pnl_usdt == null ? "等待結算" : money(row.sim_best_final_pnl_usdt)}<small>第一檔成交部分含估算 exit fee</small></td>
                    <td className={(n(row.sim_no_sell_final_pnl_usdt) ?? 0) >= 0 ? "positive" : "negative"}>{row.sim_no_sell_final_pnl_usdt == null ? "等待結算" : money(row.sim_no_sell_final_pnl_usdt)}<small>0 shares 賣出，全部 hold</small></td>
                    <td className={tone(row.status)}>{row.sim_official_winner ?? row.sim_settlement_status ?? "—"}<small>{row.status ?? "—"}</small></td>
                  </tr>)}
            </tbody>
          </table>
        </div>
      </div>

      {(error || canary?.error || canary?.sizing?.liveRulesError) && <p style={{ marginTop: 10, color: "#ffb45c", fontSize: 12 }}>{error ?? canary?.error ?? canary?.sizing?.liveRulesError}</p>}
      <p style={{ marginTop: 9, fontSize: 11, color: "rgba(215,225,245,.52)" }}>
        目前策略是否值得進入實單，優先看 BUY signed quote 的 PASS_SIMULATED_PLACE 比率、signal→quote 延遲、價格追掉與訊號消失率。退出情境只是壓力測試，不宣稱等同真實 SELL quote 或實際成交。
      </p>
    </section>,
    host,
  );
}
