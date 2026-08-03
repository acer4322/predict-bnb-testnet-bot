"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const REFRESH_MS = 15_000;
const LOCAL_DRAFT_KEY = "btc5m-live-rules-draft-v1";
const CONFIRMATION_SOURCES = new Set([
  "R_MICROPRICE",
  "R_CALIBRATED_VALUE",
  "R_FUTURES_LEAD",
]);

type ExecutionMode = "FIXED" | "CONFIRMATION_ADD";
type ObserverVersion = "F1" | "V2" | "V3" | "V4" | "V6";

type LiveRules = {
  strategy: string;
  strategies: string[];
  maxStakeUsdt: number;
  strategyStakesUsdt: number[];
  strategyExecutionModes: ExecutionMode[];
  strategyInitialStakesUsdt: number[];
  strategyConfirmationAddStakesUsdt: number[];
  minHourlyWinRatePct: number;
  maxHourlyWinThenLossRatePct: number;
  futuresLeadObserverEnabled: boolean;
  futuresLeadObserverVersion: ObserverVersion;
  strategyObserverEnabled: boolean[];
  strategyObserverVersions: ObserverVersion[];
  strategyDrawdownControlEnabled: boolean[];
  strategyLossCooldownEnabled: boolean[];
  reliabilityGateTags: string[];
};

type LiveState = {
  rules?: LiveRules;
  configurableStakeRangeUsdt?: { min?: number; max?: number };
  policy?: {
    confirmationAdd?: {
      supportedSources?: string[];
      multipliers?: number[];
      minimumSecondsLeftExclusive?: number;
    };
  };
};

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function copyRules(rules: LiveRules): LiveRules {
  return {
    ...rules,
    strategies: [...rules.strategies],
    strategyStakesUsdt: [...rules.strategyStakesUsdt],
    strategyExecutionModes: [...rules.strategyExecutionModes],
    strategyInitialStakesUsdt: [...rules.strategyInitialStakesUsdt],
    strategyConfirmationAddStakesUsdt: [
      ...rules.strategyConfirmationAddStakesUsdt,
    ],
    strategyObserverEnabled: [...rules.strategyObserverEnabled],
    strategyObserverVersions: [...rules.strategyObserverVersions],
    strategyDrawdownControlEnabled: [
      ...rules.strategyDrawdownControlEnabled,
    ],
    strategyLossCooldownEnabled: [...rules.strategyLossCooldownEnabled],
    reliabilityGateTags: [...rules.reliabilityGateTags],
  };
}

function replaceLeadSlot(
  rules: LiveRules,
  index: number,
  mode: ExecutionMode,
  initial: number,
  add: number,
): LiveRules {
  const next = copyRules(rules);
  next.strategyExecutionModes[index] = mode;
  next.strategyInitialStakesUsdt[index] = initial;
  next.strategyConfirmationAddStakesUsdt[index] = add;
  next.strategyStakesUsdt[index] =
    mode === "CONFIRMATION_ADD" ? initial + add * 4 : initial;
  next.strategy = next.strategies[0];
  next.maxStakeUsdt = next.strategyStakesUsdt[0];
  return next;
}

function patchNativeModeOptions() {
  document.querySelectorAll<HTMLSelectElement>(
    'select[aria-label$="資金模式"]',
  ).forEach(modeSelect => {
    const match = (modeSelect.getAttribute("aria-label") ?? "")
      .match(/實單策略\s+(\d+)\s+資金模式/);
    if (!match) return;
    const strategySelect = document.querySelector<HTMLSelectElement>(
      `select[aria-label="實單策略 ${match[1]}"]`,
    );
    const option = modeSelect.querySelector<HTMLOptionElement>(
      'option[value="CONFIRMATION_ADD"]',
    );
    if (!strategySelect || !option) return;

    const strategy = strategySelect.value;
    const isLead = strategy === "R_FUTURES_LEAD";
    const supportedByNativeEditor = CONFIRMATION_SOURCES.has(strategy) && !isLead;
    const desiredDisabled = !supportedByNativeEditor;
    const desiredText = strategy === "M01O_F1"
      ? "順勢確認加碼（F1 已移除）"
      : isLead
        ? "順勢確認加碼（使用下方 Lead 控制）"
        : "順勢確認加碼 Shadow 實單版";

    if (option.disabled !== desiredDisabled) option.disabled = desiredDisabled;
    if (option.textContent !== desiredText) option.textContent = desiredText;
  });
}

export default function FuturesLeadConfirmationAddControlV2() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [state, setState] = useState<LiveState | null>(null);
  const [draft, setDraft] = useState<LiveRules | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("等待實單規則載入…");

  useEffect(() => {
    const locate = () => {
      const editor = document.querySelector<HTMLElement>(".live-rules-editor");
      setTarget(current => current === editor ? current : editor);
      patchNativeModeOptions();
    };
    const observer = new MutationObserver(locate);
    observer.observe(document.body, { childList: true, subtree: true });
    locate();
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;

    const load = async () => {
      if (document.visibilityState !== "visible" || dirty) return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(apiUrl("/api/live-rules"), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const next = await response.json() as LiveState;
        if (!active) return;
        setState(next);
        setDraft(next.rules ? copyRules(next.rules) : null);
        setStatus(next.rules ? "規則已同步" : "後端尚未提供實單規則");
      } catch (error) {
        if (
          active
          && !(error instanceof DOMException && error.name === "AbortError")
        ) {
          setStatus(error instanceof Error ? error.message : "載入失敗");
        }
      }
    };

    void load();
    const timer = window.setInterval(load, REFRESH_MS);
    const visibility = () => {
      if (document.visibilityState === "visible") void load();
      else controller?.abort();
    };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [dirty]);

  const leadSlots = useMemo(() => (
    draft?.strategies
      .map((strategy, index) => ({ strategy, index }))
      .filter(item => item.strategy === "R_FUTURES_LEAD") ?? []
  ), [draft]);

  const change = (
    index: number,
    mode: ExecutionMode,
    initial: number,
    add: number,
  ) => {
    if (!draft) return;
    setDraft(replaceLeadSlot(draft, index, mode, initial, add));
    setDirty(true);
    setStatus("R_FUTURES_LEAD 資金模式尚未套用");
  };

  const save = async () => {
    if (!draft || !dirty || saving || leadSlots.length === 0) return;
    const summary = leadSlots.map(({ index }) => {
      const mode = draft.strategyExecutionModes[index] ?? "FIXED";
      const initial = draft.strategyInitialStakesUsdt[index] ?? 1;
      const add = draft.strategyConfirmationAddStakesUsdt[index] ?? 1;
      return mode === "CONFIRMATION_ADD"
        ? `槽位 ${index + 1}：初始 ${initial} USDT；每階 ${add} USDT；總上限 ${initial + add * 4} USDT`
        : `槽位 ${index + 1}：固定一次 ${initial} USDT`;
    }).join("\n");
    const hasMainDraft = Boolean(window.localStorage.getItem(LOCAL_DRAFT_KEY));
    const confirmed = window.confirm(
      `確定套用 R_FUTURES_LEAD 的實單資金模式？\n\n${summary}`
      + (hasMainDraft
        ? "\n\n目前瀏覽器另有尚未套用的實單規則草稿；成功後會清除該舊草稿並重新載入，避免它覆蓋這次設定。"
        : "")
      + "\n\n新規則只影響之後的新訊號，不修改既有訂單。",
    );
    if (!confirmed) return;

    setSaving(true);
    setStatus("儲存中…");
    try {
      const response = await fetch(apiUrl("/api/live-rules"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      const payload = await response.json() as {
        liveM0W?: LiveState;
        error?: string;
      };
      if (!response.ok) throw new Error(payload.error ?? "實單規則儲存失敗");
      if (hasMainDraft) window.localStorage.removeItem(LOCAL_DRAFT_KEY);
      if (payload.liveM0W) setState(payload.liveM0W);
      if (payload.liveM0W?.rules) {
        setDraft(copyRules(payload.liveM0W.rules));
      }
      setDirty(false);
      setStatus("已套用，正在重新載入 Dashboard…");
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "實單規則儲存失敗");
    } finally {
      setSaving(false);
    }
  };

  if (!target) return null;

  const minimumStake = state?.configurableStakeRangeUsdt?.min ?? 0.01;
  const maximumStake = state?.configurableStakeRangeUsdt?.max ?? 100;
  const supportedSources = state?.policy?.confirmationAdd?.supportedSources ?? [];
  const backendReady = supportedSources.includes("R_FUTURES_LEAD")
    && !supportedSources.includes("M01O_F1");

  return createPortal(
    <section
      aria-label="R_FUTURES_LEAD 順勢確認加碼實單控制"
      style={{
        margin: "18px 0 0",
        padding: 18,
        border: "1px solid rgba(80, 210, 255, .28)",
        borderRadius: 16,
        background: "rgba(13, 26, 38, .72)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div>
          <span className="eyebrow">FUTURES LEAD · LIVE CONFIRMATION ADD</span>
          <h3 style={{ margin: "6px 0" }}>R_FUTURES_LEAD 順勢確認加碼</h3>
          <p style={{ margin: 0, maxWidth: 900 }}>
            F1 已從順勢確認加碼來源移除，改由 R_FUTURES_LEAD 接替。初始單成交後，只有同方向 Ask 到達原成交價的 1.1×、1.2×、1.3×、1.4× 才各加一次；剩餘 ≤30 秒停止。
          </p>
        </div>
        <span className={backendReady ? "m-exit-api-state live" : "m-exit-api-state"}>
          {backendReady ? "後端已切換" : "等待重啟載入新版後端"}
        </span>
      </div>

      {leadSlots.length === 0 ? <p style={{ margin: "16px 0 0" }}>
        目前已套用的實單槽位沒有 R_FUTURES_LEAD。先在上方選定並套用該策略，重新載入後便會在這裡出現資金模式。
      </p> : leadSlots.map(({ index }) => {
        const mode = draft?.strategyExecutionModes[index] ?? "FIXED";
        const initial = draft?.strategyInitialStakesUsdt[index]
          ?? draft?.strategyStakesUsdt[index]
          ?? 1;
        const add = draft?.strategyConfirmationAddStakesUsdt[index] ?? 1;
        const total = mode === "CONFIRMATION_ADD" ? initial + add * 4 : initial;
        return <div key={index} style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 12,
          marginTop: 16,
          alignItems: "end",
        }}>
          <label>
            <span>實單槽位 {index + 1} 資金模式</span>
            <select value={mode} onChange={event => change(index, event.target.value as ExecutionMode, initial, add)}>
              <option value="FIXED">固定一次下單</option>
              <option value="CONFIRMATION_ADD">順勢確認加碼</option>
            </select>
          </label>
          <label>
            <span>初始 USDT</span>
            <input type="number" min={minimumStake} max={maximumStake} step="0.01" value={initial} onChange={event => change(index, mode, Number(event.target.value), add)} />
          </label>
          <label>
            <span>每階 USDT</span>
            <input type="number" min={minimumStake} max={maximumStake} step="0.01" disabled={mode !== "CONFIRMATION_ADD"} value={add} onChange={event => change(index, mode, initial, Number(event.target.value))} />
          </label>
          <div>
            <span>單市場總上限</span>
            <strong style={{ display: "block", marginTop: 8 }}>{total.toFixed(2)} USDT</strong>
            <small>{mode === "CONFIRMATION_ADD" ? "初始＋四次加碼" : "單次固定下單"}</small>
          </div>
        </div>;
      })}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginTop: 16, flexWrap: "wrap" }}>
        <small>{status}</small>
        <button type="button" disabled={!dirty || saving || leadSlots.length === 0 || !backendReady} onClick={save}>
          {saving ? "套用中…" : "確認並套用 Lead 資金模式"}
        </button>
      </div>
    </section>,
    target,
  );
}
