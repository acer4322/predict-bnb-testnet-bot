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
    reduceLossEnabled?: boolean;
    reduceLossUsdt?: number;
    reducedStakeUsdt?: number;
    lossReduced?: boolean;
    minimumEdge?: number;
    takeProfitPrice?: number;
    maxEntryPrice?: number;
  };
  lossGuard?: {
    netPnlUsdt?: number;
    currentLossUsdt?: number;
    maximumLossUsdt?: number;
    remainingBeforePauseUsdt?: number;
    settledRounds?: number;
    enabled?: boolean;
    tripped?: boolean;
    reductionEnabled?: boolean;
    reductionThresholdUsdt?: number;
    reducedStakeUsdt?: number;
    reductionTripped?: boolean;
    remainingBeforeReductionUsdt?: number;
    normalStakeUsdt?: number;
    effectiveStakeUsdt?: number;
    phase?: "NORMAL" | "REDUCED" | "STOPPED" | string;
    reductionLatchUntilReset?: boolean;
    sharedLossCounter?: boolean;
  };
  rules?: {
    sameMarketMultipleRounds?: boolean;
    oneActiveRoundAtATime?: boolean;
    rearmRequiresFlatPosition?: boolean;
    entryExecution?: string;
    exitExecution?: string;
    polyPollMs?: number;
    binanceDirectBookMinIntervalMs?: number;
    takeProfitBidPollMinIntervalMs?: number;
  };
  priceRiskControls?: {
    takeProfitPrice?: number;
    maxEntryPrice?: number;
    currentEntryGuard?: {
      ask?: number | null;
      blocked?: boolean;
    };
    currentTakeProfitBook?: {
      bid?: number | null;
      bookRttMs?: number | null;
    };
  };
  market?: { market_id?: number; end_ms?: number };
  poly?: { direction?: string | null; selectedMid?: number | null; ageMs?: number | null } | null;
  binance?: { side?: string; ask?: number | null; askSize?: number | null; bid?: number | null; bidSize?: number | null; bookRttMs?: number | null } | null;
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

function plainMoney(value: unknown, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(digits)} USDT` : "—";
}

function price(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(3) : "—";
}

export default function PolyGapLiveDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [state, setState] = useState<State | null>(null);
  const [stake, setStake] = useState("1.00");
  const [reduceLoss, setReduceLoss] = useState("5.00");
  const [reducedStake, setReducedStake] = useState("0.50");
  const [reduceLossEnabled, setReduceLossEnabled] = useState(false);
  const [maxLoss, setMaxLoss] = useState("10.00");
  const [maxLossEnabled, setMaxLossEnabled] = useState(true);
  const [takeProfitPrice, setTakeProfitPrice] = useState("0.95");
  const [maxEntryPrice, setMaxEntryPrice] = useState("0.90");
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

  const syncSettings = useCallback((next: State) => {
    setStake(Number(next.settings?.stakeUsdt ?? 1).toFixed(2));
    setReduceLoss(Number(next.settings?.reduceLossUsdt ?? 5).toFixed(2));
    setReducedStake(Number(next.settings?.reducedStakeUsdt ?? 0.5).toFixed(2));
    setReduceLossEnabled(Boolean(next.settings?.reduceLossEnabled));
    setMaxLoss(Number(next.settings?.maximumLossUsdt ?? 10).toFixed(2));
    setMaxLossEnabled(Boolean(next.settings?.maximumLossEnabled));
    setTakeProfitPrice(Number(next.settings?.takeProfitPrice ?? 0.95).toFixed(3));
    setMaxEntryPrice(Number(next.settings?.maxEntryPrice ?? 0.90).toFixed(3));
  }, []);

  const load = useCallback(async (preserve = true) => {
    try {
      const response = await fetch("/api/poly-gap-live", { cache: "no-store" });
      const body = await response.json() as Payload;
      if (!response.ok || !body.ok || !body.state) throw new Error(body.error ?? `HTTP ${response.status}`);
      setState(body.state);
      if (!preserve || !dirty) syncSettings(body.state);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [dirty, syncSettings]);

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
      syncSettings(payload.state);
      setDirty(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const stakeUsdt = Number(stake);
    const reduceLossUsdt = Number(reduceLoss);
    const reducedStakeUsdt = Number(reducedStake);
    const maximumLossUsdt = Number(maxLoss);
    const takeProfit = Number(takeProfitPrice);
    const maxEntry = Number(maxEntryPrice);
    if (!Number.isFinite(stakeUsdt) || stakeUsdt < 0.01 || stakeUsdt > 100) {
      setError("正常每單金額必須介於 0.01–100 USDT");
      return;
    }
    if (!Number.isFinite(reducedStakeUsdt) || reducedStakeUsdt < 0.01 || reducedStakeUsdt > 100) {
      setError("減額後每單金額必須介於 0.01–100 USDT");
      return;
    }
    if (reduceLossEnabled && reducedStakeUsdt > stakeUsdt) {
      setError("減額後每單金額不能高於正常每單金額");
      return;
    }
    if (!Number.isFinite(reduceLossUsdt) || reduceLossUsdt < 0.01 || reduceLossUsdt > 1_000_000) {
      setError("減額門檻必須介於 0.01–1,000,000 USDT");
      return;
    }
    if (!Number.isFinite(maximumLossUsdt) || maximumLossUsdt < 0.01 || maximumLossUsdt > 1_000_000) {
      setError("停止門檻必須介於 0.01–1,000,000 USDT");
      return;
    }
    if (reduceLossEnabled && maxLossEnabled && reduceLossUsdt >= maximumLossUsdt) {
      setError("同時啟用時，減額門檻必須低於停止門檻");
      return;
    }
    if (!Number.isFinite(takeProfit) || takeProfit < 0.01 || takeProfit > 0.99) {
      setError("止盈價格必須介於 0.01–0.99");
      return;
    }
    if (!Number.isFinite(maxEntry) || maxEntry < 0.01 || maxEntry > 0.99) {
      setError("禁止入場價格必須介於 0.01–0.99");
      return;
    }
    if (maxEntry >= takeProfit) {
      setError("禁止入場價格必須低於止盈價格");
      return;
    }
    await post({
      stakeUsdt,
      reduceLossEnabled,
      reduceLossUsdt,
      reducedStakeUsdt,
      maximumLossEnabled: maxLossEnabled,
      maximumLossUsdt,
      takeProfitPrice: takeProfit,
      maxEntryPrice: maxEntry,
    });
  };

  const riskBar = useMemo(() => {
    const loss = Math.max(0, Number(state?.lossGuard?.currentLossUsdt ?? 0));
    const reductionEnabled = Boolean(state?.lossGuard?.reductionEnabled);
    const stopEnabled = Boolean(state?.lossGuard?.enabled);
    const reductionThreshold = Number(state?.lossGuard?.reductionThresholdUsdt ?? 0);
    const stopThreshold = Number(state?.lossGuard?.maximumLossUsdt ?? 0);
    const limit = stopEnabled && stopThreshold > 0
      ? stopThreshold
      : reductionEnabled && reductionThreshold > 0
        ? reductionThreshold
        : 0;
    const progress = limit > 0 ? Math.min(100, Math.max(0, loss / limit * 100)) : 0;
    const reductionMarker = reductionEnabled && stopEnabled && reductionThreshold > 0 && stopThreshold > 0
      ? Math.min(100, Math.max(0, reductionThreshold / stopThreshold * 100))
      : null;
    return { progress, reductionMarker, limit };
  }, [state]);

  if (!target) return null;

  const active = state?.activeRound;
  const runtime = Boolean(state?.settings?.runtimeEnabled);
  const tripped = Boolean(state?.lossGuard?.tripped);
  const reduced = Boolean(state?.lossGuard?.reductionTripped);
  const phase = state?.lossGuard?.phase ?? (tripped ? "STOPPED" : reduced ? "REDUCED" : "NORMAL");
  const master = Boolean(state?.masterEnabled);
  const effectiveStake = Number(state?.lossGuard?.effectiveStakeUsdt ?? state?.settings?.stakeUsdt ?? 0);

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
        .poly-gap-live-risk-shell{position:relative;padding-top:20px}.poly-gap-live-risk{position:relative;height:8px;border-radius:999px;background:rgba(126,145,178,.18);overflow:visible}.poly-gap-live-risk-fill{height:100%;border-radius:999px;background:${tripped ? "#ff7777" : reduced ? "#ffb45c" : "#d3a85b"}}
        .poly-gap-live-risk-marker{position:absolute;top:-5px;bottom:-5px;width:2px;background:#7ee3f5;box-shadow:0 0 8px rgba(126,227,245,.55)}.poly-gap-live-risk-marker span{position:absolute;left:50%;bottom:12px;transform:translateX(-50%);white-space:nowrap;padding:2px 5px;border-radius:5px;background:rgba(9,18,28,.94);color:#7ee3f5;font:700 9px var(--font-mono)}
        .poly-gap-live-risk-labels{display:flex;justify-content:space-between;gap:12px;margin-top:7px;color:#91a0bb;font-size:11px;flex-wrap:wrap}.poly-gap-live-phase{font-weight:800}.poly-gap-live-phase.reduced{color:#ffb45c}.poly-gap-live-phase.stopped{color:#ff7777}.poly-gap-live-phase.normal{color:#8ce6ad}
        .poly-gap-live-warning{padding:10px;border-radius:10px;background:rgba(255,161,90,.08);border:1px solid rgba(255,161,90,.2)}
        .poly-gap-price-controls{padding:12px;border-radius:12px;border:1px solid rgba(126,227,245,.2);background:rgba(58,151,177,.06);display:grid;gap:10px}.poly-gap-price-controls h4{margin:0}.poly-gap-price-controls>small{color:#91a0bb}
      `}</style>

      <div className="poly-gap-live-head">
        <div>
          <span className="eyebrow">DEDICATED LOW-LATENCY REAL MONEY · PORT 8769</span>
          <h3>R_POLY_GAP_SCALP 專用實單</h3>
          <small>同一 5 分鐘市場可多輪；上一輪必須確認 FLAT 才 re-arm。兩階段虧損風控共用同一個已實現淨損益計數：先減額、後停止；已有持倉的 EXIT 不受影響。</small>
        </div>
        <span className="poly-gap-live-badge">{state?.status ?? "OFFLINE"}</span>
      </div>

      {!master && <div className="poly-gap-live-warning">
        <strong>Master 未開啟</strong>
        <small>先將使用者環境變數 PREDICT_POLY_GAP_LIVE_ENABLED=1 後完整重啟。這是刻意的真錢總開關；Dashboard 不能繞過它。</small>
      </div>}

      <div className="poly-gap-live-grid">
        <div><span>目前實際每單</span><strong>{effectiveStake.toFixed(2)} USDT</strong><small>正常 {Number(state?.lossGuard?.normalStakeUsdt ?? state?.settings?.stakeUsdt ?? 0).toFixed(2)} · 減額 {Number(state?.lossGuard?.reducedStakeUsdt ?? state?.settings?.reducedStakeUsdt ?? 0).toFixed(2)}</small></div>
        <div><span>風控階段</span><strong className={`poly-gap-live-phase ${phase.toLowerCase()}`}>{phase}</strong><small>{phase === "REDUCED" ? "新 round 使用減額金額，直到虧損統計歸零" : phase === "STOPPED" ? "停止新 round；EXIT 繼續管理" : "使用正常每單金額"}</small></div>
        <div><span>止盈價格</span><strong>{price(state?.settings?.takeProfitPrice ?? 0.95)}</strong><small>持倉同側 Binance Bid ≥ 此值直接 SELL</small></div>
        <div><span>禁止入場價格</span><strong>{price(state?.settings?.maxEntryPrice ?? 0.90)}</strong><small>Binance Ask／signed average ≥ 此值不 BUY</small></div>
        <div><span>目前淨損益</span><strong>{money(state?.lossGuard?.netPnlUsdt)}</strong><small>歸零後已完成 {state?.lossGuard?.settledRounds ?? 0} 輪</small></div>
        <div><span>減額門檻</span><strong>{state?.lossGuard?.reductionEnabled ? plainMoney(state?.lossGuard?.reductionThresholdUsdt) : "關閉"}</strong><small>{state?.lossGuard?.reductionTripped ? "已觸發並鎖定" : `剩餘 ${money(state?.lossGuard?.remainingBeforeReductionUsdt)}`}</small></div>
        <div><span>停止門檻</span><strong>{state?.lossGuard?.enabled ? plainMoney(state?.lossGuard?.maximumLossUsdt) : "關閉"}</strong><small>{state?.lossGuard?.tripped ? "已停止新 round" : `剩餘 ${money(state?.lossGuard?.remainingBeforePauseUsdt)}`}</small></div>
        <div><span>目前市場／輪次</span><strong>#{active?.market_id ?? state?.market?.market_id ?? "—"} · R{active?.round_no ?? "—"}</strong><small>{active ? `${active.side ?? "—"} · ${active.state ?? "—"}` : "FLAT / 等待 gap"}</small></div>
        <div><span>Poly / Binance</span><strong>{state?.poly?.direction ?? "NEUTRAL"} · {Number(state?.poly?.selectedMid ?? 0).toFixed(3)}</strong><small>Bid {price(state?.binance?.bid)} · Ask {price(state?.binance?.ask)} · book RTT {ms(state?.binance?.bookRttMs)}</small></div>
        <div><span>累積實單</span><strong>{state?.summary?.rounds ?? 0} 輪</strong><small>勝率 {pct(state?.summary?.winRate)} · PnL {money(state?.summary?.pnlUsdt)}</small></div>
      </div>

      <div className="poly-gap-live-risk-shell">
        <div className="poly-gap-live-risk" aria-label="共用虧損風控進度">
          <div className="poly-gap-live-risk-fill" style={{ width: `${riskBar.progress}%` }} />
          {riskBar.reductionMarker != null && <div className="poly-gap-live-risk-marker" style={{ left: `${riskBar.reductionMarker}%` }}><span>減額線</span></div>}
        </div>
        <div className="poly-gap-live-risk-labels">
          <span>目前虧損 {money(state?.lossGuard?.currentLossUsdt)}</span>
          <span>減額門檻 {state?.lossGuard?.reductionEnabled ? money(state?.lossGuard?.reductionThresholdUsdt) : "關閉"}</span>
          <span>停止門檻 {state?.lossGuard?.enabled ? money(state?.lossGuard?.maximumLossUsdt) : "關閉"}</span>
        </div>
        <small>同一條虧損計數先判斷減額門檻，再判斷停止門檻。減額觸發後維持小額直到按「虧損統計歸零」；停止只禁止新 round，已有持倉仍保留自動 EXIT 管理。</small>
      </div>

      <div className="poly-gap-live-grid">
        <div><span>最近 ENTRY</span><strong>{ms(state?.lastEntryLatency?.signalToQuoteResponseMs)}</strong><small>quote RTT {ms(state?.lastEntryLatency?.quoteRttMs)} · quote 後 edge {state?.lastEntryLatency?.edgeAfterQuote == null ? "—" : Number(state.lastEntryLatency.edgeAfterQuote).toFixed(4)}</small></div>
        <div><span>最近 EXIT</span><strong>{ms(state?.lastExitLatency?.signalToQuoteResponseMs)}</strong><small>quote RTT {ms(state?.lastExitLatency?.quoteRttMs)}</small></div>
        <div><span>輪詢</span><strong>Poly {state?.rules?.polyPollMs ?? "—"} ms</strong><small>Binance direct book ≥ {state?.rules?.binanceDirectBookMinIntervalMs ?? "—"} ms · 止盈 Bid ≥ {state?.rules?.takeProfitBidPollMinIntervalMs ?? state?.rules?.binanceDirectBookMinIntervalMs ?? "—"} ms</small></div>
      </div>

      <div className="poly-gap-price-controls">
        <div>
          <span className="eyebrow">PRICE RISK · LIVE EDITABLE</span>
          <h4>價格風控</h4>
        </div>
        <div className="poly-gap-live-controls">
          <label>
            <span>止盈價格（held-side Bid）</span>
            <input type="number" min="0.01" max="0.99" step="0.001" value={takeProfitPrice} disabled={busy} onChange={event => { setTakeProfitPrice(event.target.value); setDirty(true); }} />
            <small>預設 0.950；Bid ≥ 此值立即啟動 SELL/FOK。</small>
          </label>
          <label>
            <span>禁止入場價格（Ask / signed average）</span>
            <input type="number" min="0.01" max="0.99" step="0.001" value={maxEntryPrice} disabled={busy} onChange={event => { setMaxEntryPrice(event.target.value); setDirty(true); }} />
            <small>預設 0.900；價格 ≥ 此值不建立新 BUY。</small>
          </label>
        </div>
        <small>止盈只處理目前持倉，不會被算成 Poly reversal exit；禁止入場同時檢查直接 Order Book Ask 與真正送單前 signed quote average。</small>
      </div>

      <div className="poly-gap-live-controls">
        <label>
          <span>正常每單金額（USDT）</span>
          <input type="number" min="0.01" max="100" step="0.01" value={stake} disabled={busy} onChange={event => { setStake(event.target.value); setDirty(true); }} />
        </label>
        <label>
          <span>減額風控</span>
          <select value={reduceLossEnabled ? "ON" : "OFF"} disabled={busy} onChange={event => { setReduceLossEnabled(event.target.value === "ON"); setDirty(true); }}>
            <option value="ON">啟用</option>
            <option value="OFF">關閉</option>
          </select>
        </label>
        <label>
          <span>減額門檻（USDT）</span>
          <input type="number" min="0.01" max="1000000" step="0.01" value={reduceLoss} disabled={busy || !reduceLossEnabled} onChange={event => { setReduceLoss(event.target.value); setDirty(true); }} />
        </label>
        <label>
          <span>減額後每單（USDT）</span>
          <input type="number" min="0.01" max="100" step="0.01" value={reducedStake} disabled={busy || !reduceLossEnabled} onChange={event => { setReducedStake(event.target.value); setDirty(true); }} />
        </label>
        <label>
          <span>停止新單風控</span>
          <select value={maxLossEnabled ? "ON" : "OFF"} disabled={busy} onChange={event => { setMaxLossEnabled(event.target.value === "ON"); setDirty(true); }}>
            <option value="ON">啟用</option>
            <option value="OFF">關閉</option>
          </select>
        </label>
        <label>
          <span>停止門檻（USDT）</span>
          <input type="number" min="0.01" max="1000000" step="0.01" value={maxLoss} disabled={busy || !maxLossEnabled} onChange={event => { setMaxLoss(event.target.value); setDirty(true); }} />
        </label>
      </div>

      <div className="poly-gap-live-actions">
        <button type="button" disabled={!dirty || busy} onClick={() => void save()}>{busy ? "處理中…" : "套用實單風控設定"}</button>
        <button type="button" className="secondary" disabled={busy || !master || tripped} onClick={() => void post({ runtimeEnabled: !runtime })}>{runtime ? "暫停新 round" : "恢復專用實單"}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => {
          if (window.confirm("確定將 R_POLY_GAP_SCALP 專用實單的共用虧損統計歸零？\n\n這會同時解除『減額』與『停止』兩個已觸發階段；歷史交易不會刪除。")) void post({ resetLoss: true });
        }}>虧損統計歸零</button>
      </div>

      {state?.haltedReason && <small style={{ color: "#ff9f9f" }}>本市場 HALT：{state.haltedReason}</small>}
      {state?.lastError && <small style={{ color: "#ffbd87" }}>最近錯誤：{state.lastError}</small>}
      {error && <small style={{ color: "#ff8f8f" }}>Dashboard：{error}</small>}
      <small>安全規則：價格上限與兩階段虧損風控只限制 NEW BUY；止盈與 Poly reversal 都使用既有 SELL／reconciliation 路徑。任何 place-order transport ambiguity 仍不盲目 retry，而是 HALT 當前 market。</small>
    </section>,
    target,
  );
}
