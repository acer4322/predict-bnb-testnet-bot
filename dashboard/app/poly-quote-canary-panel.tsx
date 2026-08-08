"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

const STRATEGIES = [
  "R_POLY_LEAD_ENTRY",
  "R_POLY_LEAD_EXIT",
  "R_POLY_GAP_SCALP",
] as const;

type StrategyId = typeof STRATEGIES[number];

type QuoteSummary = {
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
};

type QuoteAttempt = {
  id?: number;
  trade_id?: number;
  strategy?: string;
  phase?: string;
  binance_market_id?: number;
  side?: string;
  signal_at_ms?: number;
  paper_price?: number;
  quote_average_price?: number | null;
  price_limit?: number | null;
  token_cache_hit?: number;
  token_lookup_ms?: number | null;
  quote_rtt_ms?: number | null;
  signal_to_quote_start_ms?: number | null;
  signal_to_quote_response_ms?: number | null;
  quote_coverage_ratio?: number | null;
  quote_expiry_headroom_ms?: number | null;
  adverse_price_move?: number | null;
  executable_edge_after_quote?: number | null;
  signal_still_valid?: number | null;
  would_submit?: number;
  status?: string;
  reason?: string | null;
  quote_id_present?: number;
};

type QuoteCanaryState = {
  version?: string;
  status?: string;
  error?: string | null;
  enabled?: boolean;
  paperOnly?: boolean;
  signedQuoteRequested?: boolean;
  placeOrderCalled?: boolean;
  liveOrdersAffected?: boolean;
  credentialSource?: string;
  parameters?: {
    repriceGap?: number;
    minQuoteCoverage?: number;
    minExpiryHeadroomMs?: number;
    slippageBps?: number;
    scalpMinExecutableEdge?: number;
  };
  marketCache?: {
    marketId?: number | null;
    cachedAtMs?: number | null;
    lookupMs?: number | null;
    lastPrimeAtMs?: number | null;
  };
  lastQuoteAtMs?: number | null;
  queueDepth?: number;
  droppedEvents?: number;
  strategies?: Partial<Record<StrategyId, QuoteSummary>>;
  recent?: QuoteAttempt[];
};

type ApiPayload = {
  ok?: boolean;
  state?: { quoteCanary?: QuoteCanaryState };
  error?: string;
};

function finite(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function ms(value: unknown) {
  const number = finite(value);
  return number == null ? "—" : `${number.toFixed(number < 100 ? 1 : 0)} ms`;
}

function prob(value: unknown) {
  const number = finite(value);
  return number == null ? "—" : number.toFixed(3);
}

function pct(value: unknown) {
  const number = finite(value);
  return number == null ? "—" : `${(number * 100).toFixed(1)}%`;
}

function signed(value: unknown, digits = 4) {
  const number = finite(value);
  return number == null ? "—" : `${number >= 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function clock(value: unknown) {
  const number = finite(value);
  if (number == null) return "—";
  const date = new Date(number);
  return `${date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function tone(status: string | null | undefined) {
  const value = String(status ?? "");
  if (/^PASS_/.test(value)) return "positive";
  if (/FAIL_|REJECTED|ERROR/.test(value)) return "negative";
  return "";
}

export default function PolyQuoteCanaryPanel() {
  const [host, setHost] = useState<Element | null>(null);
  const [canary, setCanary] = useState<QuoteCanaryState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const locate = () => {
      const target = document.querySelector(
        "#poly-cross-market-panel .poly-section[aria-label='Polymarket lead Paper strategies']",
      );
      setHost(target);
    };
    locate();
    const observer = new MutationObserver(locate);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
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
        const payload = await response.json() as ApiPayload;
        if (!response.ok || !payload.ok || !payload.state) {
          throw new Error(payload.error ?? `HTTP ${response.status}`);
        }
        if (!active) return;
        setCanary(payload.state.quoteCanary ?? null);
        setError(null);
      } catch (caught) {
        if (!active || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
    const timer = window.setInterval(load, 1000);
    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, []);

  if (!host) return null;

  const recent = canary?.recent ?? [];
  return createPortal(
    <section className="poly-quote-canary" aria-label="Polymarket signed quote execution canary" style={{ marginTop: 20 }}>
      <style>{`
        .poly-quote-canary{padding-top:18px;border-top:1px solid rgba(126,145,178,.22)}
        .poly-quote-canary .quote-head{display:flex;gap:16px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap}
        .poly-quote-canary .quote-head p{max-width:850px;color:#91a0bb;margin:6px 0 0}
        .poly-quote-canary .quote-status{padding:7px 10px;border-radius:999px;border:1px solid rgba(126,145,178,.28);font:700 11px/1 var(--font-mono)}
        .poly-quote-canary .quote-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px;margin:14px 0}
        .poly-quote-canary .quote-card{padding:13px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
        .poly-quote-canary .quote-card h4{margin:4px 0 10px;font-size:14px}
        .poly-quote-canary .quote-card .primary{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
        .poly-quote-canary .quote-card .primary div{padding:8px;border-radius:9px;background:rgba(126,145,178,.08)}
        .poly-quote-canary .quote-card span,.poly-quote-canary .quote-card small{display:block;color:#91a0bb}
        .poly-quote-canary .quote-card strong{display:block;margin-top:3px}
        .poly-quote-canary .quote-fails{margin-top:9px;font-size:11px;color:#91a0bb}
        .poly-quote-canary table small{display:block;color:#91a0bb;margin-top:2px}
        @media(max-width:720px){.poly-quote-canary .quote-card .primary{grid-template-columns:1fr}}
      `}</style>
      <div className="quote-head">
        <div>
          <span className="eyebrow">SIGNED GET-QUOTE CANARY · NO PLACE ORDER</span>
          <h3>實際報價／模擬送單可執行性</h3>
          <p>每當三組 Poly Paper 策略真的產生進場，立即呼叫正式實單相同的 Binance Prediction signed get-quote；Lead Exit / Gap Scalp 紙上退場也測 SELL quote。只取得 quoteId、價格、容量、到期時間與 RTT，絕不呼叫 place-order-bundle。</p>
        </div>
        <span className={`quote-status ${canary?.status === "READY" ? "positive" : canary?.status?.includes("BLOCK") ? "negative" : ""}`}>
          {canary?.status ?? "WAITING"}
        </span>
      </div>

      <div className="poly-runtime">
        <article><span>Quote 模式</span><strong>{canary?.signedQuoteRequested ? "SIGNED GET-QUOTE" : "—"}</strong><small>place-order {canary?.placeOrderCalled ? "CALLED" : "NEVER CALLED"}</small></article>
        <article><span>Token cache</span><strong>#{canary?.marketCache?.marketId ?? "—"}</strong><small>prime {ms(canary?.marketCache?.lookupMs)} · queue {canary?.queueDepth ?? 0}</small></article>
        <article><span>最後 Quote</span><strong>{clock(canary?.lastQuoteAtMs)}</strong><small>credentials {canary?.credentialSource ?? "—"}</small></article>
        <article><span>執行門檻</span><strong>coverage ≥ {pct(canary?.parameters?.minQuoteCoverage)}</strong><small>expiry ≥ {canary?.parameters?.minExpiryHeadroomMs ?? "—"}ms · reprice +{prob(canary?.parameters?.repriceGap)}</small></article>
      </div>

      <div className="quote-grid">
        {STRATEGIES.map(strategy => {
          const summary = canary?.strategies?.[strategy] ?? {};
          return <article className="quote-card" key={strategy} data-poly-quote-strategy={strategy}>
            <span className="eyebrow">{strategy}</span>
            <h4>Signed quote 可執行率</h4>
            <div className="primary">
              <div><span>可模擬送單</span><strong className={(summary.wouldSubmitRate ?? 0) >= .8 ? "positive" : ""}>{summary.wouldSubmit ?? 0}/{summary.completed ?? 0}<small>{pct(summary.wouldSubmitRate)}</small></strong></div>
              <div><span>Quote RTT</span><strong>{ms(summary.avgQuoteRttMs)}<small>max {ms(summary.maxQuoteRttMs)}</small></strong></div>
              <div><span>訊號→Quote</span><strong>{ms(summary.avgSignalToQuoteMs)}<small>max {ms(summary.maxSignalToQuoteMs)}</small></strong></div>
            </div>
            <div className="quote-fails">
              價格追掉 {summary.priceMoved ?? 0} · 訊號已消失 {summary.signalGone ?? 0} · 容量不足 {summary.capacityFailed ?? 0} · quote 到期太近 {summary.expiryFailed ?? 0} · API 拒絕 {summary.quoteRejected ?? 0}
            </div>
            <small>平均不利價格變化 {signed(summary.avgAdversePriceMove)}；attempts {summary.attempts ?? 0}</small>
          </article>;
        })}
      </div>

      <div className="table-scroll" style={{ marginTop: 14 }}>
        <table>
          <thead><tr><th>時間</th><th>策略／階段</th><th>市場</th><th>Paper → Signed quote</th><th>Quote RTT</th><th>訊號→回應</th><th>容量／Expiry</th><th>模擬送單結果</th></tr></thead>
          <tbody>
            {recent.length === 0
              ? <tr><td colSpan={8} className="empty">等待三組 Poly 策略下一筆真實 Paper 訊號後進行 signed quote canary。</td></tr>
              : recent.slice(0, 30).map(row => <tr key={row.id}>
                  <td>{clock(row.signal_at_ms)}<small>trade #{row.trade_id ?? "—"}</small></td>
                  <td>{row.strategy ?? "—"}<small>{row.phase ?? "—"} · {row.side ?? "—"}</small></td>
                  <td>#{row.binance_market_id ?? "—"}<small>{row.token_cache_hit ? "token cache hit" : `lookup ${ms(row.token_lookup_ms)}`}</small></td>
                  <td>{prob(row.paper_price)} → {prob(row.quote_average_price)}<small>limit {prob(row.price_limit)} · adverse {signed(row.adverse_price_move)}</small></td>
                  <td>{ms(row.quote_rtt_ms)}</td>
                  <td>{ms(row.signal_to_quote_response_ms)}<small>start lag {ms(row.signal_to_quote_start_ms)}</small></td>
                  <td>{pct(row.quote_coverage_ratio)}<small>expiry {ms(row.quote_expiry_headroom_ms)}</small></td>
                  <td className={tone(row.status)}>{row.status ?? "—"}<small>{row.reason ?? "—"}{row.executable_edge_after_quote != null ? ` · edge ${signed(row.executable_edge_after_quote)}` : ""}</small></td>
                </tr>)}
          </tbody>
        </table>
      </div>

      {(error || canary?.error) && <p style={{ marginTop: 10, color: "#ffb45c", fontSize: 12 }}>{error ?? canary?.error}</p>}
      <p style={{ marginTop: 9, fontSize: 11, color: "rgba(215,225,245,.52)" }}>
        PASS_SIMULATED_PLACE 只表示「若此刻立刻使用回傳 quoteId 送出 LIMIT，價格／容量／有效期與策略訊號仍通過 canary」。它不假裝 place-order 已成功，也不假裝一定成交；真正實單仍會經過原 live executor 的 book freshness、depth、quote 與 placement 安全檢查。
      </p>
    </section>,
    host,
  );
}
