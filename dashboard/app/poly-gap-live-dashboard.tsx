"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

type State = {
  version?: string;
  strategy?: string;
  realMoney?: boolean;
  masterEnabled?: boolean;
  status?: string;
  settings?: {
    runtimeEnabled?: boolean;
    stakeUsdt?: number;
    maximumLossEnabled?: boolean;
    maximumLossUsdt?: number;
    lossTripped?: boolean;
    minimumEdge?: number;
  };
  lossGuard?: {
    netPnlUsdt?: number;
    currentLossUsdt?: number;
    maximumLossUsdt?: number;
    remainingBeforePauseUsdt?: number;
    settledRounds?: number;
    enabled?: boolean;
    tripped?: boolean;
  };
  rules?: {
    sameMarketMultipleRounds?: boolean;
    oneActiveRoundAtATime?: boolean;
    rearmRequiresFlatPosition?: boolean;
    entryExecution?: string;
    exitExecution?: string;
    polyPollMs?: number;
    binanceDirectBookMinIntervalMs?: number;
  };
  market?: { market_id?: number; end_ms?: number };
  poly?: { direction?: string | null; selectedMid?: number | null; ageMs?: number | null } | null;
  binance?: { side?: string; ask?: number | null; askSize?: number | null; bookRttMs?: number | null } | null;
  activeRound?: {
    id?: number;
    market_id?: number;
    round_no?: number;
    side?: string;
    state?: string;
    stake_usdt?: number;
    entry_edge?: number;
    entry_quote_average?: number | null;
    shares?: number | null;
  } | null;
  lastEntryLatency?: {
    signalToQuoteStartMs?: number;
    quoteRttMs?: number;
    signalToQuoteResponseMs?: number;
    edgeAfterQuote?: number | null;
  } | null;
  lastExitLatency?: {
    signalToQuoteStartMs?: number;
    quoteRttMs?: number;
    signalToQuoteResponseMs?: number;
  } | null;
  haltedMarketId?: number | null;
  haltedReason?: string | null;
  lastError?: string | null;
  summary?: {
    rounds?: number;
    closedRounds?: number;
    wins?: number;
    losses?: number;
    winRate?: number | null;
    pnlUsdt?: number;
  };
};

type Payload = { ok?: boolean; state?: State; error?: string };

function money(value: unknown, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : "−"}$${Math.abs(n).toFixed(digits)}`;
}

function ms(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(1)} ms` : "—";
}

function pct(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
}

export default function PolyGapLiveDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [state, setState] = useState<State | null>(null);
  const [stake, setStake] = useState("1.00");
  const [maxLoss, setMaxLoss] = useState("10.00");
  const [maxLossEnabled, setMaxLossEnabled] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const locate = () => {
      const node = document.querySelector<HTMLElement>(".live-rules-editor");
      setTarget(current => current === node ? current : node);
    };
    locate();
    const timer = window.setInterval(locate, 750);
    return () => window.clearInterval(timer);
  }, []);

  const load = useCallback(async (preserve = true) => {
    try {
      const response = await fetch("/api/poly-gap-live", { cache: "no-store" });
      const body = await response.json() as Payload;
      if (!response.ok || !body.ok || !body.state) throw new Error(body.error ?? `HTTP ${response.status}`);
      setState(body.state);
      if (!preserve || !dirty) {
        setStake(Number(body.state.settings?.stakeUsdt ?? 1).toFixed(2));
        setMaxLoss(Number(body.state.settings?.maximumLossUsdt ?? 10).toFixed(2));
        setMaxLossEnabled(Boolean(body.state.settings?.maximumLossEnabled));
      }
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [dirty]);

  useEffect(() => {
    void load(false);
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [load]);

  const post = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/poly-gap-live", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json() as Payload;
      if (!response.ok || !payload.ok || !payload.state) throw new Error(payload.error ?? `HTTP ${response.status}`);
      setState(payload.state);
      setStake(Number(payload.state.settings?.stakeUsdt ?? 1).toFixed(2));
      setMaxLoss(Number(payload.state.settings?.maximumLossUsdt ?? 10).toFixed(2));
      setMaxLossEnabled(Boolean(payload.state.settings?.maximumLossEnabled));
      setDirty(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const stakeUsdt = Number(stake);
    const maximumLossUsdt = Number(maxLoss);
    if (!Number.isFinite(stakeUsdt) || stakeUsdt < 0.01 || stakeUsdt > 100) {
      setError("每單金額必須介於 0.01–100 USDT");
      return;
    }
    if (!Number.isFinite(maximumLossUsdt) || maximumLossUsdt < 0.01 || maximumLossUsdt > 1_000_000) {
      setError("最大虧損必須介於 0.01–1,000,000 USDT");
      return;
    }
    await post({ stakeUsdt, maximumLossEnabled: maxLossEnabled, maximumLossUsdt });
  };

  const progress = useMemo(() => {
    const loss = Number(state?.lossGuard?.currentLossUsdt ?? 0);
    const limit = Number(state?.lossGuard?.maximumLossUsdt ?? 0);
    return limit > 0 ? Math.min(100, Math.max(0, loss / limit * 100)) : 0;
  }, [state]);

  if (!target) return null;

  const active = state?.activeRound;
  const runtime = Boolean(state?.settings?.runtimeEnabled);
  const tripped = Boolean(state?.lossGuard?.tripped);
  const master = Boolean(state?.masterEnabled);

  return createPortal(
    <section className="poly-gap-live-control" aria-label="R_POLY_GAP_SCALP 專用實單">
      <style>{`
        .poly-gap-live-control{grid-column:1/-1;margin-top:14px;padding:16px;border:1px solid rgba(94,213,231,.34);border-radius:16px;background:linear-gradient(180deg,rgba(10,25,35,.76),rgba(7,13,22,.74));display:grid;gap:14px}
        .poly-gap-live-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}
        .poly-gap-live-head h3{margin:4px 0 0}.poly-gap-live-badge{padding:6px 9px;border:1px solid rgba(94,213,231,.28);border-radius:999px;font:700 10px var(--font-mono)}
        .poly-gap-live-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:9px}.poly-gap-live-grid>div{padding:10px;border-radius:10px;background:rgba(126,145,178,.07)}
        .poly-gap-live-grid span,.poly-gap-live-grid small{display:block;color:#91a0bb}.poly-gap-live-grid strong{display:block;margin-top:4px}
        .poly-gap-live-controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;align-items:end}
        .poly-gap-live-controls label{display:grid;gap:6px}.poly-gap-live-actions{display:flex;gap:8px;flex-wrap:wrap}
        .poly-gap-live-risk{height:8px;border-radius:999px;background:rgba(126,145,178,.18);overflow:hidden}.poly-gap-live-risk>div{height:100%;background:${tripped ? "#ff7777" : "#d3a85b"}}
        .poly-gap-live-warning{padding:10px;border-radius:10px;background:rgba(255,161,90,.08);border:1px solid rgba(255,161,90,.2)}
      `}</style>

      <div className="poly-gap-live-head">
        <div>
          <span className="eyebrow">DEDICATED LOW-LATENCY REAL MONEY · PORT 8769</span>
          <h3>R_POLY_GAP_SCALP 專用實單</h3>
          <small>同一 5 分鐘市場可多輪；上一輪必須確認 FLAT 才 re-arm。Poly 使用現有 WS collector 的最新狀態，Binance Prediction book／quote／place 走專用持久 client。</small>
        </div>
        <span className="poly-gap-live-badge">{state?.status ?? "OFFLINE"}</span>
      </div>

      {!master && <div className="poly-gap-live-warning">
        <strong>Master 未開啟</strong>
        <small>先將使用者環境變數 PREDICT_POLY_GAP_LIVE_ENABLED=1 後完整重啟。這是刻意的真錢總開關；Dashboard 不能繞過它。</small>
      </div>}

      <div className="poly-gap-live-grid">
        <div><span>每單金額</span><strong>{Number(state?.settings?.stakeUsdt ?? 0).toFixed(2)} USDT</strong><small>每一 round 獨立</small></div>
        <div><span>目前淨損益</span><strong>{money(state?.lossGuard?.netPnlUsdt)}</strong><small>歸零後已完成 {state?.lossGuard?.settledRounds ?? 0} 輪</small></div>
        <div><span>最大虧損</span><strong>{state?.lossGuard?.enabled ? `${Number(state?.lossGuard?.maximumLossUsdt ?? 0).toFixed(2)} USDT` : "關閉"}</strong><small>剩餘 {money(state?.lossGuard?.remainingBeforePauseUsdt)}</small></div>
        <div><span>目前市場／輪次</span><strong>#{active?.market_id ?? state?.market?.market_id ?? "—"} · R{active?.round_no ?? "—"}</strong><small>{active ? `${active.side ?? "—"} · ${active.state ?? "—"}` : "FLAT / 等待 gap"}</small></div>
        <div><span>Poly / Binance</span><strong>{state?.poly?.direction ?? "NEUTRAL"} · {Number(state?.poly?.selectedMid ?? 0).toFixed(3)}</strong><small>Ask {state?.binance?.ask == null ? "—" : Number(state.binance.ask).toFixed(3)} · book RTT {ms(state?.binance?.bookRttMs)}</small></div>
        <div><span>累積實單</span><strong>{state?.summary?.rounds ?? 0} 輪</strong><small>勝率 {pct(state?.summary?.winRate)} · PnL {money(state?.summary?.pnlUsdt)}</small></div>
      </div>

      <div>
        <div className="poly-gap-live-risk"><div style={{ width: `${progress}%` }} /></div>
        <small>目前虧損 {money(state?.lossGuard?.currentLossUsdt)} / 上限 {money(state?.lossGuard?.maximumLossUsdt)}。達上限後停止新 round；已有持倉仍保留自動 EXIT 管理。</small>
      </div>

      <div className="poly-gap-live-grid">
        <div><span>最近 ENTRY</span><strong>{ms(state?.lastEntryLatency?.signalToQuoteResponseMs)}</strong><small>quote RTT {ms(state?.lastEntryLatency?.quoteRttMs)} · quote 後 edge {state?.lastEntryLatency?.edgeAfterQuote == null ? "—" : Number(state.lastEntryLatency.edgeAfterQuote).toFixed(4)}</small></div>
        <div><span>最近 EXIT</span><strong>{ms(state?.lastExitLatency?.signalToQuoteResponseMs)}</strong><small>quote RTT {ms(state?.lastExitLatency?.quoteRttMs)}</small></div>
        <div><span>輪詢</span><strong>Poly {state?.rules?.polyPollMs ?? "—"} ms</strong><small>Binance direct book ≥ {state?.rules?.binanceDirectBookMinIntervalMs ?? "—"} ms</small></div>
      </div>

      <div className="poly-gap-live-controls">
        <label>
          <span>每單金額（USDT）</span>
          <input type="number" min="0.01" max="100" step="0.01" value={stake} disabled={busy} onChange={event => { setStake(event.target.value); setDirty(true); }} />
        </label>
        <label>
          <span>最大虧損保護</span>
          <select value={maxLossEnabled ? "ON" : "OFF"} disabled={busy} onChange={event => { setMaxLossEnabled(event.target.value === "ON"); setDirty(true); }}>
            <option value="ON">啟用</option>
            <option value="OFF">關閉</option>
          </select>
        </label>
        <label>
          <span>最大虧損（USDT）</span>
          <input type="number" min="0.01" max="1000000" step="0.01" value={maxLoss} disabled={busy || !maxLossEnabled} onChange={event => { setMaxLoss(event.target.value); setDirty(true); }} />
        </label>
      </div>

      <div className="poly-gap-live-actions">
        <button type="button" disabled={!dirty || busy} onClick={() => void save()}>{busy ? "處理中…" : "套用專用實單設定"}</button>
        <button type="button" className="secondary" disabled={busy || !master || tripped} onClick={() => void post({ runtimeEnabled: !runtime })}>{runtime ? "暫停新 round" : "恢復專用實單"}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => {
          if (window.confirm("確定將 R_POLY_GAP_SCALP 專用實單的最大虧損統計歸零？")) void post({ resetLoss: true });
        }}>虧損統計歸零</button>
      </div>

      {state?.haltedReason && <small style={{ color: "#ff9f9f" }}>本市場 HALT：{state.haltedReason}</small>}
      {state?.lastError && <small style={{ color: "#ffbd87" }}>最近錯誤：{state.lastError}</small>}
      {error && <small style={{ color: "#ff8f8f" }}>Dashboard：{error}</small>}
      <small>安全規則：任何 place-order transport ambiguity 都不盲目 retry，而是 HALT 當前 market；同市場多輪只取消「永久一次」限制，不取消「一次只能有一個 active round」與「確認 FLAT 才 re-arm」。</small>
    </section>,
    target,
  );
}
