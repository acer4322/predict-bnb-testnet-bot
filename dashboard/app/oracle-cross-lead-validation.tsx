"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

type ProbabilityInterval = {
  probability?: number | null;
  lower95?: number | null;
  upper95?: number | null;
};

type SecondsStats = {
  count?: number;
  mean?: number | null;
  median?: number | null;
  p90?: number | null;
  min?: number | null;
  max?: number | null;
};

type WindowStats = {
  requestedMarkets?: number;
  sampledMarkets?: number;
  evaluableMarkets?: number;
  insufficientMarkets?: number;
  polyLeadMarkets?: number;
  binanceLeadMarkets?: number;
  tieMixedMarkets?: number;
  polyLeadMarketProbability?: ProbabilityInterval;
  binanceLeadMarketProbability?: ProbabilityInterval;
  tieMixedMarketProbability?: ProbabilityInterval;
  matchedHeadlineEvents?: number;
  polyFirstEvents?: number;
  binanceFirstEvents?: number;
  tieEvents?: number;
  polyOnlyUnmatchedEvents?: number;
  binanceOnlyUnmatchedEvents?: number;
  polyFirstEventProbability?: ProbabilityInterval;
  binanceFirstEventProbability?: ProbabilityInterval;
  polyLeadSeconds?: SecondsStats;
  binanceLeadSeconds?: SecondsStats;
  signedPolyLeadSeconds?: SecondsStats;
  marketIds?: number[];
};

type LeadEvent = {
  marketId?: number;
  side?: string;
  milestone?: number;
  matched?: boolean;
  leader?: string;
  polyAtMs?: number | null;
  binanceAtMs?: number | null;
  signedPolyLeadSeconds?: number | null;
  absoluteLagSeconds?: number | null;
};

type MarketAnalysis = {
  marketId?: number;
  polyMarketSlug?: string;
  samples?: number;
  sampleStartMs?: number | null;
  sampleEndMs?: number | null;
  marketLeader?: string;
  matchedHeadlineEvents?: number;
  polyFirstEvents?: number;
  binanceFirstEvents?: number;
  tieEvents?: number;
  polyOnlyEvents?: number;
  binanceOnlyEvents?: number;
  medianSignedPolyLeadSeconds?: number | null;
  meanSignedPolyLeadSeconds?: number | null;
  strongestMatchedMilestone?: number | null;
  averageBinanceBookAgeMs?: number | null;
  events?: LeadEvent[];
};

type Validation = {
  version?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  forwardOnly?: boolean;
  collectionStartedAtMs?: number | null;
  sampleRows?: number;
  sampledMarketsTotal?: number;
  currentMarketExcludedFromWindows?: boolean;
  currentRegime?: string;
  definition?: string;
  milestones?: number[];
  headlineMinimumMilestone?: number;
  rearmHysteresis?: number;
  maxPairLagMs?: number;
  tieToleranceMs?: number;
  samplingTargetMs?: number;
  probabilityIntervals?: string;
  windows?: Record<string, WindowStats>;
  recentMarkets?: MarketAnalysis[];
};

type Payload = {
  ok?: boolean;
  state?: { polyBinanceLeadValidation?: Validation };
  error?: string;
};

function number(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function pct(value: unknown) {
  const parsed = number(value);
  return parsed == null ? "—" : `${(parsed * 100).toFixed(1)}%`;
}

function probability(value: ProbabilityInterval | undefined) {
  if (!value || number(value.probability) == null) return "—";
  const main = pct(value.probability);
  const low = number(value.lower95);
  const high = number(value.upper95);
  return low == null || high == null
    ? main
    : `${main} · 95% ${pct(low)}–${pct(high)}`;
}

function seconds(value: unknown, signed = false) {
  const parsed = number(value);
  if (parsed == null) return "—";
  const prefix = signed && parsed > 0 ? "+" : "";
  return `${prefix}${parsed.toFixed(2)}s`;
}

function clock(value: unknown) {
  const parsed = number(value);
  return parsed == null
    ? "—"
    : new Date(parsed).toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}

function leaderLabel(value: string | undefined) {
  if (value === "POLY") return "Poly 領先";
  if (value === "BINANCE") return "Binance 領先";
  if (value === "TIE_MIXED") return "混合／近同時";
  if (value === "TIE") return "近同時";
  if (value === "POLY_ONLY") return "僅 Poly 到達";
  if (value === "BINANCE_ONLY") return "僅 Binance 到達";
  return "資料不足";
}

function regimeLabel(value: string | undefined) {
  if (value === "POLY_LEADING") return "POLY LEADING · 假設成立";
  if (value === "BINANCE_LEADING_RISK") return "BINANCE LEADING · 策略風險";
  if (value === "MIXED") return "MIXED · 無穩定領先";
  return "COLLECTING · 樣本不足";
}

export default function OracleCrossLeadValidation() {
  const [target, setTarget] = useState<Element | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const locate = () => {
      const section = document.querySelector(
        '#poly-cross-market-panel section[aria-label="Polymarket lead Paper strategies"]',
      );
      setTarget(current => current === section ? current : section);
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
        setValidation(payload.state.polyBinanceLeadValidation ?? null);
        setError(null);
      } catch (caught) {
        if (!alive || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
    const timer = window.setInterval(load, 2000);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, []);

  const recentEvents = useMemo(() => {
    const rows: Array<LeadEvent & { marketId: number }> = [];
    for (const market of validation?.recentMarkets ?? []) {
      for (const event of market.events ?? []) {
        rows.push({ ...event, marketId: Number(market.marketId ?? event.marketId ?? 0) });
      }
    }
    return rows
      .sort((a, b) => Math.max(Number(b.polyAtMs ?? 0), Number(b.binanceAtMs ?? 0)) - Math.max(Number(a.polyAtMs ?? 0), Number(a.binanceAtMs ?? 0)))
      .slice(0, 30);
  }, [validation]);

  if (!target) return null;

  const windows = validation?.windows ?? {};
  const milestones = validation?.milestones ?? [];
  const started = validation?.collectionStartedAtMs;

  return createPortal(
    <section className="poly-lead-validation" aria-label="Poly Binance lead validation research">
      <style>{`
        .poly-lead-validation{grid-column:1/-1;margin-top:16px;padding:15px;border:1px solid rgba(126,227,245,.28);border-radius:16px;background:rgba(8,18,28,.68);display:grid;gap:12px}
        .poly-lead-validation-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.poly-lead-validation-head h3{margin:4px 0}.poly-lead-validation-badge{padding:6px 9px;border-radius:999px;border:1px solid rgba(126,227,245,.32);font:700 10px var(--font-mono)}
        .poly-lead-window-grid{display:grid;grid-template-columns:repeat(3,minmax(220px,1fr));gap:10px}.poly-lead-window{padding:12px;border:1px solid rgba(126,145,178,.22);border-radius:13px;background:rgba(126,145,178,.06)}.poly-lead-window h4{margin:0 0 8px}.poly-lead-kv{display:grid;grid-template-columns:1fr auto;gap:5px 10px;font-size:12px}.poly-lead-kv span{color:#91a0bb}.poly-lead-kv strong{text-align:right}
        .poly-lead-table-shell{overflow:auto;max-height:430px;border:1px solid rgba(126,145,178,.18);border-radius:12px}.poly-lead-table{width:100%;border-collapse:collapse;font-size:11px;min-width:920px}.poly-lead-table th,.poly-lead-table td{padding:7px 8px;border-bottom:1px solid rgba(126,145,178,.12);text-align:left;white-space:nowrap}.poly-lead-table th{position:sticky;top:0;background:#111824;z-index:1;color:#9fadc7}.poly-lead-poly{color:#8ce6ad}.poly-lead-binance{color:#ffbd87}.poly-lead-mixed{color:#d5c08b}.poly-lead-note{padding:9px 10px;border-radius:10px;background:rgba(126,227,245,.06);color:#aab8cf}.poly-lead-event-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}.poly-lead-event-grid>div{padding:9px;border-radius:10px;background:rgba(126,145,178,.06)}.poly-lead-event-grid span,.poly-lead-event-grid small{display:block;color:#91a0bb}.poly-lead-event-grid strong{display:block;margin-top:3px}
        @media(max-width:900px){.poly-lead-window-grid{grid-template-columns:1fr}}
      `}</style>

      <div className="poly-lead-validation-head">
        <div>
          <span className="eyebrow">POLY LEAD HYPOTHESIS VALIDATION · PAPER RESEARCH</span>
          <h3>Poly ↔ Binance 同價位到達先後驗證</h3>
          <small>
            不用相關係數猜領先：同一次方向推進中，比較哪一邊先到達完全相同的機率價位。
            目前 headline 只用 ≥ {number(validation?.headlineMinimumMilestone)?.toFixed(2) ?? "0.70"} 的大幅方向事件。
          </small>
        </div>
        <span className="poly-lead-validation-badge">{regimeLabel(validation?.currentRegime)}</span>
      </div>

      <div className="poly-lead-note">
        <strong>定義：</strong> 追蹤 {milestones.map(value => value.toFixed(2)).join(" / ") || "0.60 / 0.70 / 0.80 / 0.90"}。
        例如 Poly UP 先到 0.80、Binance UP 1.35 秒後才到 0.80，記為 Poly 領先 +1.35s；反之為 Binance 領先。
        另一邊 {number(validation?.maxPairLagMs) == null ? "15" : (Number(validation?.maxPairLagMs) / 1000).toFixed(0)} 秒內沒到則記「單邊到達」，不算成功領先。
        價格回落至少 {number(validation?.rearmHysteresis)?.toFixed(2) ?? "0.03"} 後重新武裝，因此反轉後再次上衝會成為新事件。
      </div>

      <div className="poly-lead-event-grid">
        <div><span>資料收集</span><strong>{validation?.sampleRows ?? 0} samples</strong><small>{validation?.sampledMarketsTotal ?? 0} 個市場 · 約 {validation?.samplingTargetMs ?? 250}ms</small></div>
        <div><span>開始時間</span><strong>{clock(started)}</strong><small>Forward-only；不偽造舊資料</small></div>
        <div><span>同時到達容忍</span><strong>±{validation?.tieToleranceMs ?? 300}ms</strong><small>低於取樣解析度不硬判先後</small></div>
        <div><span>統計信賴區間</span><strong>{validation?.probabilityIntervals ?? "Wilson 95%"}</strong><small>目前市場不納入 10/30/50</small></div>
      </div>

      <div className="poly-lead-window-grid">
        {[10, 30, 50].map(windowSize => {
          const stats = windows[String(windowSize)] ?? {};
          return <article className="poly-lead-window" key={windowSize}>
            <h4>最近 {windowSize} 場</h4>
            <div className="poly-lead-kv">
              <span>已收集／可判定</span><strong>{stats.sampledMarkets ?? 0} / {stats.evaluableMarkets ?? 0}</strong>
              <span>Poly 領先場次</span><strong className="poly-lead-poly">{stats.polyLeadMarkets ?? 0} · {probability(stats.polyLeadMarketProbability)}</strong>
              <span>Binance 領先場次</span><strong className="poly-lead-binance">{stats.binanceLeadMarkets ?? 0} · {probability(stats.binanceLeadMarketProbability)}</strong>
              <span>混合／近同時</span><strong className="poly-lead-mixed">{stats.tieMixedMarkets ?? 0} · {probability(stats.tieMixedMarketProbability)}</strong>
              <span>同價位配對事件</span><strong>{stats.matchedHeadlineEvents ?? 0}</strong>
              <span>事件：Poly 先到</span><strong className="poly-lead-poly">{stats.polyFirstEvents ?? 0} · {probability(stats.polyFirstEventProbability)}</strong>
              <span>事件：Binance 先到</span><strong className="poly-lead-binance">{stats.binanceFirstEvents ?? 0} · {probability(stats.binanceFirstEventProbability)}</strong>
              <span>Poly 領先秒數</span><strong>{seconds(stats.polyLeadSeconds?.median)} median · P90 {seconds(stats.polyLeadSeconds?.p90)}</strong>
              <span>Binance 領先秒數</span><strong>{seconds(stats.binanceLeadSeconds?.median)} median · P90 {seconds(stats.binanceLeadSeconds?.p90)}</strong>
              <span>未跟隨事件</span><strong>Poly-only {stats.polyOnlyUnmatchedEvents ?? 0} · Binance-only {stats.binanceOnlyUnmatchedEvents ?? 0}</strong>
            </div>
          </article>;
        })}
      </div>

      <div>
        <span className="eyebrow">MARKET-BY-MARKET · 最多最近 50 場</span>
        <div className="poly-lead-table-shell" style={{ marginTop: 7 }}>
          <table className="poly-lead-table">
            <thead><tr><th>市場</th><th>判定</th><th>配對事件</th><th>Poly先</th><th>Binance先</th><th>近同時</th><th>中位 Poly lead</th><th>最深價位</th><th>samples</th><th>Binance book age</th></tr></thead>
            <tbody>
              {(validation?.recentMarkets ?? []).map(market => {
                const tone = market.marketLeader === "POLY" ? "poly-lead-poly" : market.marketLeader === "BINANCE" ? "poly-lead-binance" : "poly-lead-mixed";
                return <tr key={market.marketId}>
                  <td>#{market.marketId ?? "—"}</td>
                  <td className={tone}><strong>{leaderLabel(market.marketLeader)}</strong></td>
                  <td>{market.matchedHeadlineEvents ?? 0}</td>
                  <td>{market.polyFirstEvents ?? 0}</td>
                  <td>{market.binanceFirstEvents ?? 0}</td>
                  <td>{market.tieEvents ?? 0}</td>
                  <td>{seconds(market.medianSignedPolyLeadSeconds, true)}</td>
                  <td>{number(market.strongestMatchedMilestone)?.toFixed(2) ?? "—"}</td>
                  <td>{market.samples ?? 0}</td>
                  <td>{number(market.averageBinanceBookAgeMs) == null ? "—" : `${Number(market.averageBinanceBookAgeMs).toFixed(0)}ms`}</td>
                </tr>;
              })}
              {!validation?.recentMarkets?.length && <tr><td colSpan={10}>尚未有完成市場；重啟後會從新資料開始累積。</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <span className="eyebrow">RECENT MILESTONE EVENTS · 最近 30 個</span>
        <div className="poly-lead-table-shell" style={{ marginTop: 7, maxHeight: 330 }}>
          <table className="poly-lead-table" style={{ minWidth: 760 }}>
            <thead><tr><th>市場</th><th>方向</th><th>同價位</th><th>先到者</th><th>先後差</th><th>Poly 時間</th><th>Binance 時間</th></tr></thead>
            <tbody>
              {recentEvents.map((event, index) => <tr key={`${event.marketId}-${event.side}-${event.milestone}-${index}`}>
                <td>#{event.marketId}</td>
                <td>{event.side ?? "—"}</td>
                <td>{number(event.milestone)?.toFixed(2) ?? "—"}</td>
                <td className={event.leader === "POLY" ? "poly-lead-poly" : event.leader === "BINANCE" ? "poly-lead-binance" : "poly-lead-mixed"}>{leaderLabel(event.leader)}</td>
                <td>{event.matched ? seconds(event.signedPolyLeadSeconds, true) : "未在窗口內跟隨"}</td>
                <td>{clock(event.polyAtMs)}</td>
                <td>{clock(event.binanceAtMs)}</td>
              </tr>)}
              {!recentEvents.length && <tr><td colSpan={7}>等待同價位跨越事件。</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {error && <small style={{ color: "#ff9f9f" }}>Lead validation：{error}</small>}
      <small>
        研究用途：這個面板只驗證「Poly 是否仍領先 Binance」的市場結構假設，不會直接暫停、恢復或修改 8769 Echtgeld。
        若之後數據證明 Binance-leading regime 明顯增加，再把它升級成實單進場 gate 會比較安全。
      </small>
    </section>,
    target,
  );
}
