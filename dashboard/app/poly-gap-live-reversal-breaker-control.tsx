"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

type LiveState = {
  version?: string;
  settings?: {
    sameMarketReversalExitThreshold?: number;
  };
  sameMarketLiveExitBreaker?: {
    marketId?: number | null;
    completedReversalExits?: number;
    threshold?: number;
    remainingBeforeBlock?: number;
    blocked?: boolean;
  };
};

type Payload = { ok?: boolean; state?: LiveState; error?: string };

export default function PolyGapLiveReversalBreakerControl() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [state, setState] = useState<LiveState | null>(null);
  const [threshold, setThreshold] = useState("2");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const locate = () => {
      const node = document.querySelector<HTMLElement>(".poly-gap-live-control");
      setTarget(current => current === node ? current : node);
    };
    locate();
    const timer = window.setInterval(locate, 500);
    return () => window.clearInterval(timer);
  }, []);

  const applyState = useCallback((next: LiveState, preserveDirty: boolean) => {
    setState(next);
    if (!preserveDirty || !dirty) {
      const value = Number(
        next.settings?.sameMarketReversalExitThreshold
        ?? next.sameMarketLiveExitBreaker?.threshold
        ?? 2,
      );
      setThreshold(Number.isFinite(value) ? String(Math.round(value)) : "2");
    }
  }, [dirty]);

  const load = useCallback(async (preserveDirty = true) => {
    try {
      const response = await fetch("/api/poly-gap-live", { cache: "no-store" });
      const payload = await response.json() as Payload;
      if (!response.ok || !payload.ok || !payload.state) {
        throw new Error(payload.error ?? `HTTP ${response.status}`);
      }
      applyState(payload.state, preserveDirty);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [applyState]);

  useEffect(() => {
    void load(false);
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [load]);

  const save = async () => {
    const value = Number(threshold);
    if (!Number.isInteger(value) || value < 1 || value > 20) {
      setError("翻轉阻擋門檻必須是 1–20 的整數");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/poly-gap-live", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sameMarketReversalExitThreshold: value }),
      });
      const payload = await response.json() as Payload;
      if (!response.ok || !payload.ok || !payload.state) {
        throw new Error(payload.error ?? `HTTP ${response.status}`);
      }
      setDirty(false);
      applyState(payload.state, false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  if (!target) return null;

  const breaker = state?.sameMarketLiveExitBreaker;
  const completed = Number(breaker?.completedReversalExits ?? 0);
  const activeThreshold = Number(
    breaker?.threshold ?? state?.settings?.sameMarketReversalExitThreshold ?? 2,
  );
  const blocked = Boolean(breaker?.blocked);

  return createPortal(
    <div className="poly-gap-reversal-breaker-control">
      <style>{`
        .poly-gap-reversal-breaker-control{padding:12px;border-radius:12px;border:1px solid rgba(255,180,92,.26);background:rgba(255,161,90,.06);display:grid;gap:10px}
        .poly-gap-reversal-breaker-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}
        .poly-gap-reversal-breaker-head h4{margin:0}
        .poly-gap-reversal-breaker-status{font:800 10px var(--font-mono);padding:5px 8px;border-radius:999px;border:1px solid rgba(255,180,92,.28)}
        .poly-gap-reversal-breaker-grid{display:grid;grid-template-columns:minmax(190px,1fr) minmax(180px,1fr);gap:10px;align-items:end}
        .poly-gap-reversal-breaker-grid label{display:grid;gap:6px}
        .poly-gap-reversal-breaker-meta{padding:10px;border-radius:10px;background:rgba(126,145,178,.07)}
        .poly-gap-reversal-breaker-meta span,.poly-gap-reversal-breaker-meta small{display:block;color:#91a0bb}.poly-gap-reversal-breaker-meta strong{display:block;margin:4px 0}
        @media(max-width:720px){.poly-gap-reversal-breaker-grid{grid-template-columns:1fr}}
      `}</style>
      <div className="poly-gap-reversal-breaker-head">
        <div>
          <span className="eyebrow">REVERSAL EXIT BREAKER · LIVE EDITABLE</span>
          <h4>同市場翻轉阻擋</h4>
        </div>
        <span className="poly-gap-reversal-breaker-status">{blocked ? "BLOCKED" : "ARMED"}</span>
      </div>
      <div className="poly-gap-reversal-breaker-grid">
        <label>
          <span>完成幾次翻轉出場後禁止新 BUY</span>
          <input
            type="number"
            min="1"
            max="20"
            step="1"
            value={threshold}
            disabled={busy}
            onChange={event => { setThreshold(event.target.value); setDirty(true); }}
          />
          <small>預設 2；只計算實單 POLY_DIRECTION_FLIP 完成出場，不把止盈算進來。</small>
        </label>
        <div className="poly-gap-reversal-breaker-meta">
          <span>目前市場進度</span>
          <strong>{completed} / {activeThreshold} 次</strong>
          <small>{blocked ? "已禁止此市場後續新 BUY；既有持倉 EXIT 繼續管理。" : `還可完成 ${Math.max(0, activeThreshold - completed)} 次翻轉出場後才阻擋。`}</small>
        </div>
      </div>
      <div className="poly-gap-live-actions">
        <button type="button" disabled={!dirty || busy} onClick={() => void save()}>
          {busy ? "套用中…" : "套用翻轉阻擋門檻"}
        </button>
      </div>
      <small>V31 同一門檻同時套用一般 re-entry 與快速 reversal handoff 的 projected breaker；調低到目前已達門檻時會立即阻擋下一筆新 BUY，不影響 SELL／reconciliation／settlement。</small>
      {error && <small style={{ color: "#ff8f8f" }}>翻轉阻擋設定：{error}</small>}
    </div>,
    target,
  );
}
