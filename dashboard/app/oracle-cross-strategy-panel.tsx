"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

const STRATEGIES = [
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

type StrategyId = typeof STRATEGIES[number]["id"];

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
  summaries?: Partial<Record<StrategyId, StrategySummary>>;
  openPositions?: StrategyTrade[];
  recentTrades?: StrategyTrade[];
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

function signed(value: unknown) {
  const result = num(value);
  return result == null ? "—" : `${result >= 0 ? "+" : ""}${result.toFixed(3)}`;
}

function money(value: unknown) {
  const result = num(value);
  return result == null ? "—" : `${result >= 0 ? "+" : "−"}$${Math.abs(result).toFixed(4)}`;
}

function pct(value: unknown) {
  const result = num(value);
  return result == null ? "—" : `${(result * 100).toFixed(1)}%`;
}

function clock(value: unknown) {
  const result = num(value);
  return result == null ? "—" : new Date(result).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function OracleCrossStrategyPanel() {
  const [host, setHost] = useState<Element | null>(null);
  const [researchView, setResearchView] = useState(false);
  const [state, setState] = useState<StrategyState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const locate = () => {
      const next = document.querySelector(".market-panel");
      setHost(next);
      const eyebrow = next?.querySelector(".market-title .eyebrow");
      setResearchView(Boolean(eyebrow?.textContent?.includes("模擬研究")));
    };
    locate();
    const observer = new MutationObserver(locate);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
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
        const response = await fetch("/api/oracle-cross-strategies", { cache: "no-store", signal: controller.signal });
        const payload = await response.json() as ApiPayload;
        if (!response.ok || !payload.ok || !payload.state) throw new Error(payload.error ?? `HTTP ${response.status}`);
        if (!active) return;
        setState(payload.state);
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

  if (!host || !researchView) return null;

  const runtime = state?.runtime;
  const params = state?.parameters;
  const recent = state?.recentTrades ?? [];

  return createPortal(
    <section style={{ marginTop: 22, paddingTop: 22, borderTop: "1px solid rgba(126,145,178,.24)" }} aria-label="Polymarket lead Paper strategies">
      <div className="market-title">
        <div>
          <span className="eyebrow">POLYMARKET LEAD · THREE FORWARD PAPER STRATEGIES</span>
          <h2 style={{ margin: "4px 0 0" }}>跨市場領先／落差策略</h2>
          <p>只使用 forward 收到的 Polymarket + Binance Prediction；Ask 進、Bid 退，不影響正式實單。</p>
        </div>
        <div className="countdown">
          <span>策略引擎</span>
          <strong style={{ fontSize: 18 }}>{state?.status ?? "OFFLINE"}</strong>
        </div>
      </div>

      <div className="ticker-grid" style={{ marginTop: 14 }}>
        <div className="ticker neutral"><span>Polymarket UP mid</span><strong>{prob(runtime?.polyUpMid)}</strong><small>{runtime?.polyDirection ?? "NEUTRAL"}</small></div>
        <div className="ticker neutral"><span>Binance UP mid</span><strong>{prob(runtime?.binanceUpMid)}</strong><small>{runtime?.binanceDirection ?? "NEUTRAL"}</small></div>
        <div className="ticker neutral"><span>Poly − Binance</span><strong>{signed(runtime?.probabilityGap)}</strong><small>5m 對齊差 {num(runtime?.secondsLeftSkew)?.toFixed(2) ?? "—"} s</small></div>
        <div className="ticker neutral"><span>最近 Poly 翻轉</span><strong>{state?.lastFlip ? `${state.lastFlip.from} → ${state.lastFlip.to}` : "—"}</strong><small>{state?.lastFlip?.atMs ? clock(state.lastFlip.atMs) : "等待 confident flip"}</small></div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 12, marginTop: 14 }}>
        {STRATEGIES.map(strategy => {
          const summary = state?.summaries?.[strategy.id] ?? {};
          const pnl = num(summary.grossPnlUsdt) ?? 0;
          return <article key={strategy.id} style={{ border: "1px solid rgba(126,145,178,.22)", borderRadius: 14, padding: 14, background: "rgba(10,15,25,.55)" }}>
            <span className="eyebrow">{strategy.id}</span>
            <h3 style={{ margin: "5px 0 8px" }}>{strategy.title}</h3>
            <p style={{ minHeight: 54, margin: 0, fontSize: 12, color: "rgba(215,225,245,.68)" }}>{strategy.rule}</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8, marginTop: 12 }}>
              <div><small>交易／持倉</small><strong style={{ display: "block" }}>{summary.trades ?? 0} / {summary.open ?? 0}</strong></div>
              <div><small>勝率</small><strong style={{ display: "block" }}>{pct(summary.winRate)}</strong></div>
              <div><small>Gross PnL</small><strong className={pnl >= 0 ? "positive" : "negative"} style={{ display: "block" }}>{money(pnl)}</strong></div>
            </div>
          </article>;
        })}
      </div>

      <div style={{ marginTop: 12, fontSize: 12, color: "rgba(215,225,245,.66)" }}>
        Flip deadband：UP ≥ {prob(params?.polyUpThreshold)}、DOWN ≤ {prob(params?.polyDownThreshold)}；高頻可執行落差門檻 {prob(params?.scalpMinExecutableEdge)}；每筆 Paper {num(params?.paperStakeUsdt)?.toFixed(2) ?? "—"} USDT；輪詢 {params?.pollIntervalMs ?? "—"} ms。{state?.feesIncluded === false ? "目前顯示 gross PnL，尚未扣 Prediction fee。" : ""}
      </div>

      <div className="table-scroll" style={{ marginTop: 14 }}>
        <table>
          <thead><tr><th>時間</th><th>策略</th><th>市場</th><th>方向</th><th>進場</th><th>退出</th><th>Poly / Binance UP mid</th><th>狀態／PnL</th></tr></thead>
          <tbody>
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
                  <td className={(num(trade.gross_pnl_usdt) ?? 0) >= 0 ? "positive" : "negative"}>{trade.status ?? "—"} · {money(trade.gross_pnl_usdt)}</td>
                </tr>)}
          </tbody>
        </table>
      </div>

      {(error || runtime?.error) && <p style={{ marginTop: 10, color: "#ffb45c", fontSize: 12 }}>{error ?? runtime?.error}</p>}
      <p style={{ marginTop: 8, fontSize: 11, color: "rgba(215,225,245,.52)" }}>
        高頻組可以在同一市場多輪；Lead Entry / Lead Exit 各市場最多一筆，便於直接比較提前退場是否改善結果。沒有 Polymarket 翻轉時不做盤中賣出，最後只用 Binance 官方結算結果收尾。
      </p>
    </section>,
    host,
  );
}
