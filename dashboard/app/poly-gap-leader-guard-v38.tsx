"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

const OFF = "OFF";
const ENTRY_ONLY = "POLY_ONLY_ENTRY";
const ENTRY_AND_KILL = "POLY_ONLY_ENTRY_AND_KILL";

type Leader = {
  regime?: string | null;
  receivedAtMs?: number | null;
  ageMs?: number | null;
  fresh?: boolean;
  error?: string | null;
  sampleRows?: number | null;
  sampledMarketsTotal?: number | null;
};

type LeaderGuard = {
  mode?: string;
  enabled?: boolean;
  entryRequiresPolyLeading?: boolean;
  exitAndLockOnBinanceOrMixed?: boolean;
  leaderSource?: string;
  leaderSourceUsesCompletedMarkets?: boolean;
  currentMarketExcludedFromLeaderWindow?: boolean;
  maxLocalAgeMs?: number;
  leader?: Leader;
  currentMarketLock?: {
    market_id?: number;
    regime?: string;
    reason?: string;
    locked_at_ms?: number;
  } | null;
  entryBlocks?: number;
  marketLocks?: number;
  forcedExitAttempts?: number;
  restingShotgunGtcLeftUntouched?: boolean;
};

type LiveState = {
  version?: string;
  settings?: { leaderGuardMode?: string };
  leaderGuardV38?: LeaderGuard;
};

type Payload = { ok?: boolean; state?: LiveState; error?: string };

function leaderLabel(regime: string | null | undefined) {
  if (regime === "POLY_LEADING") return "Poly 領先";
  if (regime === "BINANCE_LEADING_RISK") return "Binance 領先";
  if (regime === "MIXED") return "混合／近同時";
  if (regime === "INSUFFICIENT_DATA") return "資料不足";
  return "尚未取得";
}

function modeLabel(mode: string | undefined) {
  if (mode === ENTRY_ONLY) return "A · 只允許 Poly 領先進場";
  if (mode === ENTRY_AND_KILL) return "B · 非 Poly 出場 + 鎖本局";
  return "OFF · 不使用 Leader Guard";
}

export default function PolyGapLeaderGuardV38() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [state, setState] = useState<LiveState | null>(null);
  const [mode, setMode] = useState(OFF);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const locate = () => setTarget(document.querySelector<HTMLElement>(".live-rules-editor"));
    locate();
    const timer = window.setInterval(locate, 1200);
    return () => window.clearInterval(timer);
  }, []);

  const sync = useCallback((next: LiveState) => {
    const nextMode = String(next.settings?.leaderGuardMode ?? next.leaderGuardV38?.mode ?? OFF);
    setMode([OFF, ENTRY_ONLY, ENTRY_AND_KILL].includes(nextMode) ? nextMode : OFF);
  }, []);

  const load = useCallback(async (preserve = true) => {
    try {
      const response = await fetch("/api/poly-gap-live", { cache: "no-store" });
      const body = await response.json() as Payload;
      if (!response.ok || !body.ok || !body.state) throw new Error(body.error ?? `HTTP ${response.status}`);
      setState(body.state);
      if (!preserve || !dirty) sync(body.state);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [dirty, sync]);

  useEffect(() => {
    void load(false);
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [load]);

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/poly-gap-live", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ leaderGuardMode: mode }),
      });
      const body = await response.json() as Payload;
      if (!response.ok || !body.ok || !body.state) throw new Error(body.error ?? `HTTP ${response.status}`);
      setState(body.state);
      sync(body.state);
      setDirty(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  if (!target) return null;

  const guard = state?.leaderGuardV38;
  const leader = guard?.leader;
  const leaderFresh = leader?.fresh === true;
  const lock = guard?.currentMarketLock;

  return createPortal(
    <section className="poly-gap-leader-guard-v38" aria-label="Poly leader guard V38">
      <style>{`
        .poly-gap-leader-guard-v38{grid-column:1/-1;margin-top:12px;padding:14px;border:1px solid rgba(140,230,173,.25);border-radius:14px;background:rgba(15,42,28,.18);display:grid;gap:12px}
        .poly-gap-leader-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}.poly-gap-leader-head h4{margin:3px 0 0}.poly-gap-leader-badge{font:800 10px var(--font-mono);padding:5px 8px;border-radius:999px;border:1px solid rgba(140,230,173,.32)}
        .poly-gap-leader-controls{display:grid;grid-template-columns:minmax(290px,1fr) auto;gap:10px;align-items:end}.poly-gap-leader-controls label{display:grid;gap:5px}.poly-gap-leader-controls small,.poly-gap-leader-note{color:#9da9bc}
        .poly-gap-leader-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:8px}.poly-gap-leader-summary>div{padding:9px;border-radius:9px;background:rgba(126,145,178,.07)}.poly-gap-leader-summary span,.poly-gap-leader-summary small{display:block;color:#91a0bb}.poly-gap-leader-summary strong{display:block;margin-top:3px}
        @media(max-width:720px){.poly-gap-leader-controls{grid-template-columns:1fr}}
      `}</style>

      <div className="poly-gap-leader-head">
        <div>
          <span className="eyebrow">V38 · POLY/BINANCE LEADER GUARD</span>
          <h4>領先狀態實單過濾</h4>
          <small>直接沿用 8768 現有 Poly ↔ Binance Lead Validation 的 currentRegime；不是另做一套領先判定。</small>
        </div>
        <span className="poly-gap-leader-badge">{modeLabel(mode)}</span>
      </div>

      <div className="poly-gap-leader-controls">
        <label>
          <span>模式</span>
          <select value={mode} disabled={busy} onChange={event => { setMode(event.target.value); setDirty(true); }}>
            <option value={OFF}>OFF · 原本策略</option>
            <option value={ENTRY_ONLY}>版本 A · 只有 Poly 領先才允許新進場</option>
            <option value={ENTRY_AND_KILL}>版本 B · 只有 Poly 領先才進場；Binance／混合則出場並鎖本局</option>
          </select>
        </label>
        <button type="button" disabled={!dirty || busy} onClick={() => void save()}>{busy ? "處理中…" : "套用 Leader Guard"}</button>
      </div>

      <div className="poly-gap-leader-summary">
        <div><span>目前 Lead Regime</span><strong>{leaderLabel(leader?.regime)}</strong><small>{leaderFresh ? `fresh · ${Math.round(Number(leader?.ageMs ?? 0))}ms` : `STALE / unavailable · ${leader?.ageMs ?? "—"}ms`}</small></div>
        <div><span>新進場</span><strong>{mode === OFF ? "原本規則" : leaderFresh && leader?.regime === "POLY_LEADING" && !lock ? "允許" : "阻擋"}</strong><small>{mode === OFF ? "Leader 不參與實單" : "必須 fresh POLY_LEADING"}</small></div>
        <div><span>本局鎖定</span><strong>{lock ? `#${lock.market_id ?? "—"}` : "未鎖"}</strong><small>{lock ? leaderLabel(lock.regime) : mode === ENTRY_AND_KILL ? "Binance/Mixed 會鎖到市場結束" : "版本 A 不鎖市場"}</small></div>
        <div><span>統計</span><strong>{guard?.entryBlocks ?? 0} blocks · {guard?.marketLocks ?? 0} locks</strong><small>forced SELL attempts {guard?.forcedExitAttempts ?? 0}</small></div>
      </div>

      <div className="poly-gap-leader-note">
        <strong>A：</strong>只有 fresh `POLY_LEADING` 才建立新的 Live BUY；Binance 領先、混合／近同時、資料不足或 Leader feed stale 都不進場，但既有持倉照原規則管理。<br />
        <strong>B：</strong>同樣只有 Poly 領先才進；一旦明確變成 Binance 領先或 MIXED，若已有持倉就沿用原本 signed SELL/FOK 出場，並把該 market 鎖住，不再建立新的策略 BUY。<br />
        <strong>Shotgun：</strong>依目前要求，本版不新增 GTC 撤單；已經存在的 resting Shotgun orders 保持原行為。
      </div>

      <small>來源樣本：{leader?.sampleRows ?? "—"} rows · {leader?.sampledMarketsTotal ?? "—"} markets · Engine {state?.version ?? "—"}</small>
      {leader?.error && <small style={{ color: "#ffbd87" }}>Leader source：{leader.error}</small>}
      {error && <small style={{ color: "#ff8f8f" }}>Leader Guard：{error}</small>}
    </section>,
    target,
  );
}
