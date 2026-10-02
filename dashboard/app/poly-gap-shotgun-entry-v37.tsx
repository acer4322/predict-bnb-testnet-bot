"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

type ShotgunOrder = {
  round_id?: number;
  market_id?: number;
  side?: string;
  level_price?: number;
  amount_usdt?: number;
  state?: string;
  quote_rtt_ms?: number | null;
  place_rtt_ms?: number | null;
  order_id?: string | null;
  error_kind?: string | null;
};

type LiveState = {
  version?: string;
  settings?: {
    maxEntryPrice?: number;
    shotgunEnabled?: boolean;
    shotgunMinPrice?: number;
    shotgunMaxPrice?: number;
    shotgunLevels?: number[];
    shotgunOrderUsdt?: number;
    shotgunMaximumExposureUsdt?: number;
    shotgunMaximumLevels?: number;
    shotgunMinimumOrderUsdt?: number;
  };
  shotgunEntryV37?: {
    enabled?: boolean;
    executionMode?: string;
    minimumOrderUsdt?: number;
    maximumLevels?: number;
    maximumExposureUsdt?: number;
    generationsArmed?: number;
    levelsSubmitted?: number;
    levelsRejected?: number;
    levelsAmbiguous?: number;
    lastGeneration?: {
      marketId?: number;
      roundId?: number;
      side?: string;
      triggerAsk?: number;
      submittedLevels?: number[];
      rejectedLevels?: number[];
      submittedExposureUsdt?: number;
    };
    recentOrders?: ShotgunOrder[];
  };
};

type Payload = { ok?: boolean; state?: LiveState; error?: string };

function num(value: unknown, digits = 3) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : "—";
}

function ms(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${parsed.toFixed(1)} ms` : "—";
}

export default function PolyGapShotgunEntryV37() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [state, setState] = useState<LiveState | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [minPrice, setMinPrice] = useState("0.05");
  const [maxPrice, setMaxPrice] = useState("0.40");
  const [levels, setLevels] = useState("0.05,0.10,0.20,0.30,0.40");
  const [perLevel, setPerLevel] = useState("1.00");
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
    const settings = next.settings ?? {};
    setEnabled(Boolean(settings.shotgunEnabled));
    setMinPrice(Number(settings.shotgunMinPrice ?? 0.05).toFixed(2));
    setMaxPrice(Number(settings.shotgunMaxPrice ?? 0.40).toFixed(2));
    const nextLevels = Array.isArray(settings.shotgunLevels) && settings.shotgunLevels.length
      ? settings.shotgunLevels
      : [0.05, 0.10, 0.20, 0.30, 0.40];
    setLevels([...nextLevels].sort((a, b) => a - b).map(value => Number(value).toFixed(2)).join(","));
    setPerLevel(Number(settings.shotgunOrderUsdt ?? 1).toFixed(2));
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

  const parsedLevels = useMemo(() => {
    const parsed = levels.split(",")
      .map(value => Number(value.trim()))
      .filter(value => Number.isFinite(value));
    return [...new Set(parsed.map(value => Number(value.toFixed(6))))].sort((a, b) => a - b);
  }, [levels]);
  const exposure = Number(perLevel) * parsedLevels.length;

  const save = async () => {
    const minimum = Number(minPrice);
    const maximum = Number(maxPrice);
    const amount = Number(perLevel);
    const liveMaxEntry = Number(state?.settings?.maxEntryPrice ?? 0.99);
    if (!Number.isFinite(minimum) || minimum < 0.01 || minimum > 0.99) {
      setError("Shotgun 最低價格必須介於 0.01–0.99"); return;
    }
    if (!Number.isFinite(maximum) || maximum < minimum || maximum > 0.99) {
      setError("Shotgun 最高價格必須大於等於最低價格且不高於 0.99"); return;
    }
    if (maximum > liveMaxEntry + 1e-12) {
      setError(`Shotgun 最高價格不能高於目前禁止入場價格 ${liveMaxEntry.toFixed(3)}`); return;
    }
    if (parsedLevels.length < 1 || parsedLevels.length > 5) {
      setError("Shotgun 必須設定 1–5 個不重複價格層"); return;
    }
    if (parsedLevels.some(value => value < minimum - 1e-12 || value > maximum + 1e-12)) {
      setError("每個價格層都必須落在 Shotgun 最低／最高價格內"); return;
    }
    if (!Number.isFinite(amount) || amount < 1 || amount > 100) {
      setError("Shotgun 單層金額必須介於 1.00–100 USDT"); return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/poly-gap-live", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          shotgunEnabled: enabled,
          shotgunMinPrice: minimum,
          shotgunMaxPrice: maximum,
          shotgunLevels: parsedLevels,
          shotgunOrderUsdt: amount,
        }),
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
  const shotgun = state?.shotgunEntryV37;
  const recent = shotgun?.recentOrders ?? [];

  return createPortal(
    <section className="poly-gap-shotgun-v37" aria-label="Shotgun Entry V37">
      <style>{`
        .poly-gap-shotgun-v37{grid-column:1/-1;margin-top:12px;padding:14px;border:1px solid rgba(255,189,87,.28);border-radius:14px;background:rgba(44,31,12,.18);display:grid;gap:12px}
        .poly-gap-shotgun-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}.poly-gap-shotgun-head h4{margin:3px 0 0}.poly-gap-shotgun-badge{font:800 10px var(--font-mono);padding:5px 8px;border-radius:999px;border:1px solid rgba(255,189,87,.3)}
        .poly-gap-shotgun-controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:9px;align-items:end}.poly-gap-shotgun-controls label{display:grid;gap:5px}.poly-gap-shotgun-controls small,.poly-gap-shotgun-note{color:#9da9bc}
        .poly-gap-shotgun-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px}.poly-gap-shotgun-summary>div{padding:9px;border-radius:9px;background:rgba(126,145,178,.07)}.poly-gap-shotgun-summary span,.poly-gap-shotgun-summary small{display:block;color:#91a0bb}.poly-gap-shotgun-summary strong{display:block;margin-top:3px}
        .poly-gap-shotgun-orders{display:grid;gap:5px}.poly-gap-shotgun-order{display:grid;grid-template-columns:70px 70px 90px 1fr;gap:8px;padding:6px 8px;border-radius:8px;background:rgba(126,145,178,.06);font:11px var(--font-mono)}
        @media(max-width:720px){.poly-gap-shotgun-order{grid-template-columns:60px 60px 1fr}.poly-gap-shotgun-order span:last-child{grid-column:1/-1}}
      `}</style>
      <div className="poly-gap-shotgun-head">
        <div><span className="eyebrow">V37 · EXPERIMENTAL ENTRY EXECUTION</span><h4>散彈式進場 Shotgun Entry</h4><small>啟用後取代正常 BUY/FOK；同一 market + direction 只建立一組向下 LIMIT/GTC ladder。</small></div>
        <span className="poly-gap-shotgun-badge">{enabled ? "ON" : "OFF"}</span>
      </div>

      <div className="poly-gap-shotgun-controls">
        <label><span>功能</span><select value={enabled ? "ON" : "OFF"} disabled={busy} onChange={event => { setEnabled(event.target.value === "ON"); setDirty(true); }}><option value="OFF">關閉</option><option value="ON">啟用</option></select></label>
        <label><span>最低掛單價</span><input type="number" min="0.01" max="0.99" step="0.01" value={minPrice} disabled={busy} onChange={event => { setMinPrice(event.target.value); setDirty(true); }} /></label>
        <label><span>觸發／最高掛單價</span><input type="number" min="0.01" max="0.99" step="0.01" value={maxPrice} disabled={busy} onChange={event => { setMaxPrice(event.target.value); setDirty(true); }} /><small>選定側 Ask ≤ 此值才建立 ladder。</small></label>
        <label><span>價格層（最多 5 層）</span><input value={levels} disabled={busy} onChange={event => { setLevels(event.target.value); setDirty(true); }} /><small>例：0.05,0.10,0.20,0.30,0.40</small></label>
        <label><span>單層金額（USDT）</span><input type="number" min="1" max="100" step="0.01" value={perLevel} disabled={busy} onChange={event => { setPerLevel(event.target.value); setDirty(true); }} /><small>依實際市場限制硬性最低 1.00 USDT。</small></label>
        <div><button type="button" disabled={!dirty || busy} onClick={() => void save()}>{busy ? "處理中…" : "套用 Shotgun 設定"}</button></div>
      </div>

      <div className="poly-gap-shotgun-summary">
        <div><span>最大曝險</span><strong>{Number.isFinite(exposure) ? `${exposure.toFixed(2)} USDT` : "—"}</strong><small>{parsedLevels.length} 層 × {num(perLevel, 2)}</small></div>
        <div><span>建立組數</span><strong>{shotgun?.generationsArmed ?? 0}</strong><small>submitted levels {shotgun?.levelsSubmitted ?? 0}</small></div>
        <div><span>最近 Shotgun</span><strong>#{shotgun?.lastGeneration?.marketId ?? "—"} · {shotgun?.lastGeneration?.side ?? "—"}</strong><small>trigger {num(shotgun?.lastGeneration?.triggerAsk)} · exposure {num(shotgun?.lastGeneration?.submittedExposureUsdt, 2)}</small></div>
        <div><span>拒絕／不確定層</span><strong>{shotgun?.levelsRejected ?? 0} / {shotgun?.levelsAmbiguous ?? 0}</strong><small>任何 placement ambiguity 仍 HALT market。</small></div>
      </div>

      {recent.length > 0 && <div className="poly-gap-shotgun-orders">
        <strong>最近價格層</strong>
        {recent.slice(0, 10).map((order, index) => <div className="poly-gap-shotgun-order" key={`${order.round_id}-${order.level_price}-${index}`}>
          <span>{order.side ?? "—"} {num(order.level_price)}</span><span>${num(order.amount_usdt, 2)}</span><span>{order.state ?? "—"}</span><span>Q {ms(order.quote_rtt_ms)} · P {ms(order.place_rtt_ms)}{order.error_kind ? ` · ${order.error_kind}` : ""}</span>
        </div>)}
      </div>}

      <div className="poly-gap-shotgun-note">目前版本不自動 batch-cancel 尚未成交的低價 GTC。反轉／止盈仍沿用原本 SELL 路徑，會以當下實際 wallet shares 出場；若反轉時尚無已成交 shares，round 不會被誤判為已平倉。</div>
      {state?.version && <small>Engine：{state.version}</small>}
      {error && <small style={{ color: "#ff8f8f" }}>Shotgun：{error}</small>}
    </section>,
    target,
  );
}
