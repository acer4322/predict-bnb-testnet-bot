"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const LIVE_RULES_TARGET = ".live-rules-editor";
const POLL_MS = 2_000;

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

type MaximumNetLossGuardState = {
  version?: string;
  enabled?: boolean;
  status?: string;
  maximumLossUsdt?: number;
  netPnlUsdt?: number;
  currentLossUsdt?: number;
  remainingBeforePauseUsdt?: number;
  thresholdReached?: boolean;
  settledTrades?: number;
  resetAt?: string | null;
  tripped?: boolean;
  trippedAt?: string | null;
  trippedLossUsdt?: number | null;
  acknowledgedLossUsdt?: number;
  acknowledgedAt?: string | null;
  updatedAt?: string | null;
  manualResumeRequired?: boolean;
  reductionEnabled?: boolean;
  reductionThresholdUsdt?: number;
  reductionMultiplierPct?: number;
  reductionTripped?: boolean;
  reductionTrippedAt?: string | null;
  reductionTrippedLossUsdt?: number | null;
  remainingBeforeReductionUsdt?: number;
  reductionThresholdReached?: boolean;
  effectiveStakeMultiplier?: number;
  phase?: "NORMAL" | "REDUCED" | "STOPPED" | string;
  sharedLossCounter?: boolean;
  reductionLatchUntilReset?: boolean;
  reductionAppliesToNewOrdersOnly?: boolean;
};

type LivePayload = {
  runtimeEnabled?: boolean;
  armed?: boolean;
  status?: string;
  maximumNetLossGuard?: MaximumNetLossGuardState;
};

function money(value: number | null | undefined, signed = false) {
  if (value == null || !Number.isFinite(value)) return "—";
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(2)} USDT`;
}

function timeText(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-TW", { hour12: false });
}

export default function MaximumNetLossGuardDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [live, setLive] = useState<LivePayload | null>(null);
  const [reduceEnabledDraft, setReduceEnabledDraft] = useState(false);
  const [reduceThresholdDraft, setReduceThresholdDraft] = useState("5.00");
  const [reduceMultiplierDraft, setReduceMultiplierDraft] = useState("50.00");
  const [stopEnabledDraft, setStopEnabledDraft] = useState(false);
  const [stopThresholdDraft, setStopThresholdDraft] = useState("10.00");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState("");

  const syncDraft = useCallback((guard?: MaximumNetLossGuardState) => {
    setReduceEnabledDraft(Boolean(guard?.reductionEnabled));
    setReduceThresholdDraft(Number(guard?.reductionThresholdUsdt ?? 5).toFixed(2));
    setReduceMultiplierDraft(Number(guard?.reductionMultiplierPct ?? 50).toFixed(2));
    setStopEnabledDraft(Boolean(guard?.enabled));
    setStopThresholdDraft(Number(guard?.maximumLossUsdt ?? 10).toFixed(2));
  }, []);

  const loadState = useCallback(async (preserveDraft = true) => {
    try {
      const response = await fetch(apiUrl("/api/live-rules"), { cache: "no-store" });
      const body = await response.json() as LivePayload & { error?: string };
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      setLive(body);
      if (!preserveDraft || !dirty) syncDraft(body.maximumNetLossGuard);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "無法讀取兩階段虧損風控狀態");
    }
  }, [dirty, syncDraft]);

  useEffect(() => {
    const locate = () => {
      const next = document.querySelector<HTMLElement>(LIVE_RULES_TARGET);
      setTarget(current => current === next ? current : next);
    };
    locate();
    const timer = window.setInterval(locate, 750);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    void loadState(false);
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadState(true);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [loadState]);

  const save = async () => {
    const reduceThreshold = Number(reduceThresholdDraft);
    const reduceMultiplier = Number(reduceMultiplierDraft);
    const stopThreshold = Number(stopThresholdDraft);

    if (!Number.isFinite(reduceThreshold) || reduceThreshold < 0.01 || reduceThreshold > 1_000_000) {
      setError("減額門檻必須介於 0.01–1,000,000 USDT");
      return;
    }
    if (!Number.isFinite(reduceMultiplier) || reduceMultiplier < 1 || reduceMultiplier > 100) {
      setError("減額後比例必須介於 1–100%");
      return;
    }
    if (!Number.isFinite(stopThreshold) || stopThreshold < 0.01 || stopThreshold > 1_000_000) {
      setError("停止門檻必須介於 0.01–1,000,000 USDT");
      return;
    }
    if (reduceEnabledDraft && stopEnabledDraft && reduceThreshold >= stopThreshold) {
      setError("同時啟用時，減額門檻必須小於停止門檻");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const response = await fetch(apiUrl("/api/live-rules"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          maximumNetLossReduceEnabled: reduceEnabledDraft,
          maximumNetLossReduceUsdt: reduceThreshold,
          maximumNetLossReduceMultiplierPct: reduceMultiplier,
          maximumNetLossGuardEnabled: stopEnabledDraft,
          maximumNetLossUsdt: stopThreshold,
        }),
      });
      const body = await response.json() as { liveM0W?: LivePayload; error?: string } & LivePayload;
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      const next = body.liveM0W ?? body;
      setLive(next);
      syncDraft(next.maximumNetLossGuard);
      setDirty(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "儲存失敗");
    } finally {
      setSaving(false);
    }
  };

  const resetCounter = async () => {
    if (!window.confirm("確定將共用虧損統計歸零？這會同時解除『減額』與『停止』兩階段的已觸發狀態；歷史交易不會刪除。")) return;
    setResetting(true);
    setError("");
    try {
      const response = await fetch(apiUrl("/api/live-rules"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resetMaximumNetLoss: true }),
      });
      const body = await response.json() as { liveM0W?: LivePayload; error?: string } & LivePayload;
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      const next = body.liveM0W ?? body;
      setLive(next);
      syncDraft(next.maximumNetLossGuard);
      setDirty(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "歸零失敗");
    } finally {
      setResetting(false);
    }
  };

  const guard = live?.maximumNetLossGuard;
  const currentLoss = Math.max(0, Number(guard?.currentLossUsdt ?? 0));
  const phase = guard?.phase ?? (guard?.tripped ? "STOPPED" : guard?.reductionTripped ? "REDUCED" : "NORMAL");
  const reduced = phase === "REDUCED";
  const stopped = phase === "STOPPED";

  const riskBar = useMemo(() => {
    const reduceThreshold = Number(guard?.reductionThresholdUsdt ?? 0);
    const stopThreshold = Number(guard?.maximumLossUsdt ?? 0);
    const reduceEnabled = Boolean(guard?.reductionEnabled);
    const stopEnabled = Boolean(guard?.enabled);
    const limit = stopEnabled && stopThreshold > 0
      ? stopThreshold
      : reduceEnabled && reduceThreshold > 0
        ? reduceThreshold
        : 0;
    const progress = limit > 0 ? Math.min(100, currentLoss / limit * 100) : 0;
    const reduceMarker = reduceEnabled && stopEnabled && reduceThreshold > 0 && stopThreshold > 0
      ? Math.min(100, Math.max(0, reduceThreshold / stopThreshold * 100))
      : null;
    return { progress, reduceMarker };
  }, [currentLoss, guard?.enabled, guard?.maximumLossUsdt, guard?.reductionEnabled, guard?.reductionThresholdUsdt]);

  if (!target) return null;

  const statusText = stopped
    ? "STOPPED · 已停止新單"
    : reduced
      ? `REDUCED · 新單 ${Number(guard?.reductionMultiplierPct ?? 100).toFixed(0)}%`
      : guard?.enabled || guard?.reductionEnabled
        ? "NORMAL · 監控中"
        : "未啟用";
  const statusColor = stopped ? "#ff8f8f" : reduced ? "#ffb45c" : guard?.enabled || guard?.reductionEnabled ? "#8ce6ad" : "#aeb9cc";

  return createPortal(
    <section
      aria-label="兩階段淨虧損風控"
      style={{
        gridColumn: "1 / -1",
        marginTop: 12,
        padding: 16,
        border: `1px solid ${stopped ? "rgba(255,104,104,.62)" : reduced ? "rgba(255,180,92,.55)" : "rgba(126,145,178,.32)"}`,
        borderRadius: 14,
        background: stopped ? "rgba(97,27,34,.23)" : reduced ? "rgba(89,58,18,.18)" : "rgba(10,16,27,.58)",
        display: "grid",
        gap: 14,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <div>
          <span className="eyebrow">GLOBAL LIVE TIERED LOSS GUARD</span>
          <h3 style={{ margin: "4px 0 0" }}>淨虧損減額／停止保護</h3>
          <small>同一個已實現淨損益計數：先達減額門檻就縮小新單，之後達停止門檻才停止新單。</small>
        </div>
        <strong style={{ color: statusColor }}>{statusText}</strong>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 10 }}>
        <div><small>目前淨損益</small><strong style={{ display: "block", marginTop: 4, color: Number(guard?.netPnlUsdt ?? 0) >= 0 ? "#8ce6ad" : "#ff9f9f" }}>{money(guard?.netPnlUsdt, true)}</strong></div>
        <div><small>目前虧損</small><strong style={{ display: "block", marginTop: 4 }}>{money(guard?.currentLossUsdt)}</strong></div>
        <div><small>減額門檻</small><strong style={{ display: "block", marginTop: 4 }}>{guard?.reductionEnabled ? money(guard?.reductionThresholdUsdt) : "關閉"}</strong></div>
        <div><small>減額後新單</small><strong style={{ display: "block", marginTop: 4 }}>{guard?.reductionEnabled ? `${Number(guard?.reductionMultiplierPct ?? 100).toFixed(0)}%` : "100%"}</strong></div>
        <div><small>停止門檻</small><strong style={{ display: "block", marginTop: 4 }}>{guard?.enabled ? money(guard?.maximumLossUsdt) : "關閉"}</strong></div>
        <div><small>歸零後已結算</small><strong style={{ display: "block", marginTop: 4 }}>{guard?.settledTrades ?? 0} 筆</strong></div>
      </div>

      <div style={{ paddingTop: 18 }}>
        <div style={{ position: "relative", height: 8, borderRadius: 999, background: "rgba(126,145,178,.2)", overflow: "visible" }} aria-label="共用虧損風控進度">
          <div style={{ width: `${riskBar.progress}%`, height: "100%", borderRadius: 999, background: stopped ? "#ff7777" : reduced ? "#ffb45c" : "#d3a85b", transition: "width .25s ease" }} />
          {riskBar.reduceMarker != null && <div style={{ position: "absolute", left: `${riskBar.reduceMarker}%`, top: -5, bottom: -5, width: 2, background: "#7ee3f5" }}>
            <span style={{ position: "absolute", left: "50%", bottom: 12, transform: "translateX(-50%)", whiteSpace: "nowrap", fontSize: 10, color: "#7ee3f5" }}>減額線</span>
          </div>}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
          <small>目前虧損 {money(guard?.currentLossUsdt)}</small>
          <small>減額 {guard?.reductionEnabled ? money(guard?.reductionThresholdUsdt) : "關閉"}</small>
          <small>停止 {guard?.enabled ? money(guard?.maximumLossUsdt) : "關閉"}</small>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 12, alignItems: "end" }}>
        <label style={{ display: "grid", gap: 6 }}>
          <span>減額風控</span>
          <select value={reduceEnabledDraft ? "ON" : "OFF"} disabled={saving || resetting} onChange={event => { setReduceEnabledDraft(event.target.value === "ON"); setDirty(true); }}>
            <option value="OFF">關閉</option>
            <option value="ON">啟用</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: 6 }}>
          <span>減額門檻（USDT）</span>
          <input type="number" min="0.01" max="1000000" step="0.01" value={reduceThresholdDraft} disabled={saving || resetting} onChange={event => { setReduceThresholdDraft(event.target.value); setDirty(true); }} />
        </label>
        <label style={{ display: "grid", gap: 6 }}>
          <span>減額後比例（%）</span>
          <input type="number" min="1" max="100" step="1" value={reduceMultiplierDraft} disabled={saving || resetting} onChange={event => { setReduceMultiplierDraft(event.target.value); setDirty(true); }} />
        </label>
        <label style={{ display: "grid", gap: 6 }}>
          <span>停止新單風控</span>
          <select value={stopEnabledDraft ? "ON" : "OFF"} disabled={saving || resetting} onChange={event => { setStopEnabledDraft(event.target.value === "ON"); setDirty(true); }}>
            <option value="OFF">關閉</option>
            <option value="ON">啟用</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: 6 }}>
          <span>停止門檻（USDT）</span>
          <input type="number" min="0.01" max="1000000" step="0.01" value={stopThresholdDraft} disabled={saving || resetting} onChange={event => { setStopThresholdDraft(event.target.value); setDirty(true); }} />
        </label>
      </div>

      <small style={{ color: "#9eabc2" }}>
        三個數字欄位即使對應開關目前是關閉也可以先修改。減額比例套用到各策略原本的下注額，例如 50%：2.00→1.00、0.50→0.25；最低仍受正式實單最小 stake 0.01 USDT 保護。
      </small>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button type="button" disabled={!dirty || saving || resetting} onClick={() => void save()}>
          {saving ? "儲存中…" : "套用兩階段風控設定"}
        </button>
        <button type="button" className="secondary" disabled={saving || resetting} onClick={() => void resetCounter()}>
          {resetting ? "歸零中…" : "將共用損益歸零"}
        </button>
      </div>

      <small>
        計算方式：上次手動歸零後所有正式已結算訂單的 PnL 相加；勝單會抵銷虧損。減額一旦觸發會鎖定到下次手動歸零，避免在門檻附近反覆切換大小單。
      </small>
      <small>
        減額只影響新的 BUY／新 confirmation-add tranche；已有持倉的 SELL、手動出場、正式結算與 reconciliation 不會被縮小或阻擋。停止門檻則沿用既有 fail-closed 暫停新單機制。
      </small>
      {guard?.reductionTripped && !guard?.tripped && <small style={{ color: "#ffbd87" }}>
        減額已觸發：{timeText(guard?.reductionTrippedAt)} · 觸發時虧損 {money(guard?.reductionTrippedLossUsdt)} · 目前新單倍率 {Number(guard?.effectiveStakeMultiplier ?? 1) * 100}%
      </small>}
      {guard?.tripped && <small style={{ color: "#ff9f9f" }}>
        停止已觸發：{timeText(guard?.trippedAt)} · 觸發虧損 {money(guard?.trippedLossUsdt)}。要恢復新單仍需使用既有手動恢復流程。
      </small>}
      {error && <small style={{ color: "#ffbd87" }}>錯誤：{error}</small>}
    </section>,
    target,
  );
}
