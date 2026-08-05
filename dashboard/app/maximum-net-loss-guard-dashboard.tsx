"use client";

import { useCallback, useEffect, useState } from "react";
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
  status?: "DISABLED" | "MONITORING" | "TRIPPED" | string;
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
  const [enabledDraft, setEnabledDraft] = useState(false);
  const [limitDraft, setLimitDraft] = useState("10.00");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState("");

  const loadState = useCallback(async (preserveDraft = true) => {
    try {
      const response = await fetch(apiUrl("/api/live-rules"), { cache: "no-store" });
      const body = await response.json() as LivePayload & { error?: string };
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      setLive(body);
      const guard = body.maximumNetLossGuard;
      if (!preserveDraft || !dirty) {
        setEnabledDraft(Boolean(guard?.enabled));
        if (Number.isFinite(guard?.maximumLossUsdt)) {
          setLimitDraft(Number(guard?.maximumLossUsdt).toFixed(2));
        }
      }
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "無法讀取最大虧損狀態");
    }
  }, [dirty]);

  useEffect(() => {
    const locate = () => {
      const next = document.querySelector<HTMLElement>(LIVE_RULES_TARGET);
      setTarget(current => current === next ? current : next);
    };
    locate();
    const locateTimer = window.setInterval(locate, 750);
    return () => window.clearInterval(locateTimer);
  }, []);

  useEffect(() => {
    void loadState(false);
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadState(true);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [loadState]);

  const save = async () => {
    const maximumLossUsdt = Number(limitDraft);
    if (!Number.isFinite(maximumLossUsdt) || maximumLossUsdt < 0.01 || maximumLossUsdt > 1_000_000) {
      setError("最大虧損必須介於 0.01–1,000,000 USDT");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const response = await fetch(apiUrl("/api/live-rules"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          maximumNetLossGuardEnabled: enabledDraft,
          maximumNetLossUsdt: maximumLossUsdt,
        }),
      });
      const body = await response.json() as {
        liveM0W?: LivePayload;
        error?: string;
      } & LivePayload;
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      const next = body.liveM0W ?? body;
      setLive(next);
      setEnabledDraft(Boolean(next.maximumNetLossGuard?.enabled));
      setLimitDraft(Number(next.maximumNetLossGuard?.maximumLossUsdt ?? maximumLossUsdt).toFixed(2));
      setDirty(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "儲存失敗");
    } finally {
      setSaving(false);
    }
  };

  const resetCounter = async () => {
    if (!window.confirm("確定要把最大虧損統計歸零嗎？這不會自動恢復已暫停的實單。")) return;
    setResetting(true);
    setError("");
    try {
      const response = await fetch(apiUrl("/api/live-rules"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resetMaximumNetLoss: true }),
      });
      const body = await response.json() as {
        liveM0W?: LivePayload;
        error?: string;
      } & LivePayload;
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      const next = body.liveM0W ?? body;
      setLive(next);
      setEnabledDraft(Boolean(next.maximumNetLossGuard?.enabled));
      setLimitDraft(Number(next.maximumNetLossGuard?.maximumLossUsdt ?? 10).toFixed(2));
      setDirty(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "歸零失敗");
    } finally {
      setResetting(false);
    }
  };

  if (!target) return null;

  const guard = live?.maximumNetLossGuard;
  const currentLoss = Number(guard?.currentLossUsdt ?? 0);
  const maximumLoss = Number(guard?.maximumLossUsdt ?? 0);
  const progress = maximumLoss > 0
    ? Math.min(100, Math.max(0, currentLoss / maximumLoss * 100))
    : 0;
  const tripped = guard?.tripped === true;
  const monitoring = guard?.enabled === true && !tripped;
  const statusText = tripped
    ? "已觸發 · 實單已暫停"
    : monitoring
      ? "監控中"
      : "未啟用";
  const statusColor = tripped ? "#ff8f8f" : monitoring ? "#8ce6ad" : "#aeb9cc";

  return createPortal(
    <section
      aria-label="最大淨虧損暫停保護"
      style={{
        gridColumn: "1 / -1",
        marginTop: 12,
        padding: 16,
        border: `1px solid ${tripped ? "rgba(255, 104, 104, .62)" : "rgba(126, 145, 178, .32)"}`,
        borderRadius: 14,
        background: tripped ? "rgba(97, 27, 34, .23)" : "rgba(10, 16, 27, .58)",
        display: "grid",
        gap: 14,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <div>
          <span className="eyebrow">GLOBAL LIVE CIRCUIT BREAKER</span>
          <h3 style={{ margin: "4px 0 0" }}>最大淨虧損暫停</h3>
        </div>
        <strong style={{ color: statusColor }}>{statusText}</strong>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
        <div><small>目前淨損益</small><strong style={{ display: "block", marginTop: 4, color: Number(guard?.netPnlUsdt ?? 0) >= 0 ? "#8ce6ad" : "#ff9f9f" }}>{money(guard?.netPnlUsdt, true)}</strong></div>
        <div><small>目前虧損</small><strong style={{ display: "block", marginTop: 4 }}>{money(guard?.currentLossUsdt)}</strong></div>
        <div><small>暫停門檻</small><strong style={{ display: "block", marginTop: 4 }}>{money(guard?.maximumLossUsdt)}</strong></div>
        <div><small>歸零後已結算</small><strong style={{ display: "block", marginTop: 4 }}>{guard?.settledTrades ?? 0} 筆</strong></div>
      </div>

      <div>
        <div style={{ height: 8, borderRadius: 999, background: "rgba(126, 145, 178, .2)", overflow: "hidden" }}>
          <div style={{ width: `${progress}%`, height: "100%", background: tripped ? "#ff7777" : "#d3a85b", transition: "width .25s ease" }} />
        </div>
        <small style={{ display: "block", marginTop: 6 }}>
          距離暫停還有 {money(guard?.remainingBeforePauseUsdt)} · 上次歸零 {timeText(guard?.resetAt)}
        </small>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, alignItems: "end" }}>
        <label style={{ display: "grid", gap: 6 }}>
          <span>啟用最大虧損保護</span>
          <select
            value={enabledDraft ? "ON" : "OFF"}
            disabled={saving || resetting}
            onChange={event => {
              setEnabledDraft(event.target.value === "ON");
              setDirty(true);
            }}
          >
            <option value="OFF">關閉</option>
            <option value="ON">啟用</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: 6 }}>
          <span>最大淨虧損（USDT）</span>
          <input
            type="number"
            min="0.01"
            max="1000000"
            step="0.01"
            value={limitDraft}
            disabled={saving || resetting}
            onChange={event => {
              setLimitDraft(event.target.value);
              setDirty(true);
            }}
          />
        </label>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button type="button" disabled={!dirty || saving || resetting} onClick={() => void save()}>
          {saving ? "儲存中…" : "套用最大虧損設定"}
        </button>
        <button type="button" className="secondary" disabled={saving || resetting} onClick={() => void resetCounter()}>
          {resetting ? "歸零中…" : "將目前損益歸零"}
        </button>
      </div>

      <small>
        計算方式：上次手動歸零後所有正式已結算訂單的 PnL 相加。勝單收益會抵銷虧損；只有淨值為負時才顯示為「目前虧損」。未結算持倉不會提前計入。
      </small>
      <small>
        達到門檻會直接觸發現有實單暫停。歸零不會自動恢復交易；請確認狀況後，再使用原本的「恢復實單」按鈕手動繼續。
      </small>
      {tripped && <small style={{ color: "#ff9f9f" }}>
        觸發時間 {timeText(guard?.trippedAt)} · 觸發虧損 {money(guard?.trippedLossUsdt)}。手動恢復後，若虧損繼續擴大會再次暫停；若先回到門檻內，保護會重新完整上鎖。
      </small>}
      {error && <small style={{ color: "#ffbd87" }}>錯誤：{error}</small>}
    </section>,
    target,
  );
}
