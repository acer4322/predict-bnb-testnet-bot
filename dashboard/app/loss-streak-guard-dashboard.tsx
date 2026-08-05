"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSharedDashboardState } from "./shared-dashboard-state";

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

const TEST_STRATEGY = "R_MICROPRICE_CONFIRM_LOSS_STREAK_GUARD";
const RULE_FIELD = "strategyLossStreakGuardEnabled";
const RESEARCH_TARGET = ".research-forward-panel .research-strategy-grid";
const LIVE_RULES_TARGET = ".live-rules-editor";
const EMPTY_STRATEGIES: string[] = [];
const EMPTY_FLAGS: boolean[] = [];

type LossProtectionMode = "OFF" | "COOLDOWN" | "SHADOW";

type GuardState = {
  enabled?: boolean;
  mode?: string;
  consecutiveLosses?: number;
  probationRemaining?: number;
  shadowSampleCount?: number;
  latestShadowPnlSum?: number | null;
  lastError?: string | null;
};

type CooldownState = {
  enabled?: boolean;
  consecutiveLosses?: number;
  cooldownPending?: boolean;
  lastResult?: "WIN" | "LOSS" | null;
  lastSettlementMarketId?: number | null;
  lastSkippedMarketId?: number | null;
};

type LiveRulesPayload = {
  rules?: {
    strategies?: string[];
    strategyLossCooldownEnabled?: boolean[];
    strategyLossStreakGuardEnabled?: boolean[];
  };
  strategyLossCooldownStates?: CooldownState[];
  strategyLossStreakGuardStates?: GuardState[];
};

type TestSummary = {
  trades?: number;
  open?: number;
  wins?: number;
  losses?: number;
  realized_pnl?: number;
};

type TestExperiment = {
  version?: string;
  sourceMarkets?: number;
  strategies?: Record<string, {
    trades?: number;
    open?: number;
    settled?: number;
    wins?: number;
    losses?: number;
    winRate?: number | null;
    realizedPnl?: number;
    averageEntryPrice?: number | null;
    mode?: string;
    state?: {
      mode?: string;
      consecutiveLosses?: number;
      probationRemaining?: number;
      shadowSampleCount?: number;
      latestShadowPnlSum?: number | null;
    };
  }>;
  runtime?: {
    opened?: number;
    halfStakeOpened?: number;
    shadowBlocked?: number;
    recoveries?: number;
  };
};

type DashboardPayload = {
  summaries?: Record<string, TestSummary>;
  researchForward?: {
    micropriceConfirmLossStreakGuard?: TestExperiment;
  } | null;
};

type RuleMount = {
  index: number;
  element: HTMLLabelElement;
};

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `$${value.toFixed(2)}`;
}

function pct(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}

function modeText(mode: string | undefined) {
  if (mode === "SHADOW") return "SHADOW 暫停實單";
  if (mode === "PROBATION") return "PROBATION 降額觀察";
  return "NORMAL 正常執行";
}

function stateDetail(state: GuardState | undefined) {
  if (!state) return "等待後端狀態";
  if (state.mode === "SHADOW") {
    return `Paper ${state.shadowSampleCount ?? 0}/3 · 最近三筆 PnL ${money(state.latestShadowPnlSum)}`;
  }
  if (state.mode === "PROBATION") {
    return `剩餘 ${state.probationRemaining ?? 0} 筆降額勝利驗證`;
  }
  return `目前連敗 ${state.consecutiveLosses ?? 0}/3${state.consecutiveLosses === 2 ? " · 下一筆降額" : ""}`;
}

function protectionMode(cooldown: boolean, shadow: boolean): LossProtectionMode {
  if (shadow) return "SHADOW";
  if (cooldown) return "COOLDOWN";
  return "OFF";
}

function setNativeSelectValue(select: HTMLSelectElement | undefined, value: string) {
  if (!select || select.value === value) return;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype,
    "value",
  )?.set;
  if (setter) setter.call(select, value);
  else select.value = value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

function InlineLossProtectionControl({
  strategy,
  index,
  mode,
  guardState,
  cooldownState,
  dirty,
  saving,
  error,
  onChange,
}: {
  strategy: string;
  index: number;
  mode: LossProtectionMode;
  guardState?: GuardState;
  cooldownState?: CooldownState;
  dirty: boolean;
  saving: boolean;
  error?: string;
  onChange: (index: number, mode: LossProtectionMode) => void;
}) {
  const cooldownDetail = cooldownState?.cooldownPending
    ? `已達兩連敗，等待跳過下一市場；上一結算市場 #${cooldownState.lastSettlementMarketId ?? "—"}`
    : `目前連敗 ${cooldownState?.consecutiveLosses ?? 0} · 上一結果 ${cooldownState?.lastResult ?? "—"}`;

  return <>
    <span>策略 {index + 1} 連敗保護模式</span>
    <select
      aria-label={`策略 ${index + 1} ${strategy} 連敗保護模式`}
      value={mode}
      disabled={saving}
      onChange={event => onChange(index, event.target.value as LossProtectionMode)}
    >
      <option value="OFF">不使用連敗保護</option>
      <option value="COOLDOWN">兩連敗後跳過下一市場</option>
      <option value="SHADOW">三連敗 Shadow／降額恢復</option>
    </select>
    <small>
      {saving
        ? "正在套用連敗保護設定…"
        : error
          ? `儲存失敗：${error}`
          : dirty
            ? "尚未套用；請按下方「確認並套用新規則」"
            : mode === "COOLDOWN"
              ? cooldownDetail
              : mode === "SHADOW"
                ? `${modeText(guardState?.mode)} · ${stateDetail(guardState)}`
                : "關閉；不會因連敗額外阻擋這個策略。"}
    </small>
    {mode === "COOLDOWN" && <small>只計此策略官方結算；連續兩敗後跳過一個市場，再將冷卻狀態歸零。勝利會立即把連敗歸零。</small>}
    {mode === "SHADOW" && <small>兩連敗後下一筆使用 50% 本金，但最低維持 1 USDT；三連敗暫停。最近三筆 Paper PnL 合計轉正後，以兩筆降額單重新驗證。</small>}
    {guardState?.lastError && mode === "SHADOW" && <small style={{ color: "#ffbd87" }}>{guardState.lastError}</small>}
  </>;
}

function TestStrategyCard({ payload }: { payload: DashboardPayload | null }) {
  const experiment = payload?.researchForward?.micropriceConfirmLossStreakGuard;
  const stats = experiment?.strategies?.[TEST_STRATEGY];
  const summary = payload?.summaries?.[TEST_STRATEGY];
  const wins = summary?.wins ?? stats?.wins ?? 0;
  const losses = summary?.losses ?? stats?.losses ?? 0;
  const settled = wins + losses;
  const trades = summary?.trades ?? stats?.trades ?? 0;
  const open = summary?.open ?? stats?.open ?? 0;
  const pnl = summary?.realized_pnl ?? stats?.realizedPnl ?? 0;
  const state = stats?.state;

  return <article className="m-exit-card mint" data-research-enhancement={TEST_STRATEGY}>
    <div className="m-exit-card-head">
      <div><span className="eyebrow">{TEST_STRATEGY} · FORWARD-ONLY STATE MACHINE</span><h3>Microprice Confirm · 連敗停機／恢復測試</h3></div>
      <div className="m-exit-card-actions"><span className="m-exit-id">PAPER ONLY</span></div>
    </div>
    <div className="m-exit-primary-stats">
      <div><span>已實現收益</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong></div>
      <div><span>勝率</span><strong>{settled ? pct(wins / settled) : "—"}</strong></div>
      <div><span>交易／未結算</span><strong>{trades} / {open}</strong></div>
    </div>
    <div className={`continuous-calibration-state ${state?.mode === "NORMAL" ? "ready" : "warmup"}`}>
      <span>{modeText(state?.mode ?? stats?.mode)}</span>
      <strong>連敗 {state?.consecutiveLosses ?? 0} · Shadow {state?.shadowSampleCount ?? 0} · 觀察剩餘 {state?.probationRemaining ?? 0}</strong>
      <small>最近三筆 Shadow PnL {money(state?.latestShadowPnlSum)}</small>
    </div>
    <p>完整鏡像 R_MICROPRICE_CONFIRM。正常狀態按原本金建立獨立 Paper 單；兩連敗後下一筆降額，第三敗後只觀察來源訊號、不建立測試單。</p>
    <small>Shadow 至少累積三筆已結算來源單，且最近三筆 PnL 合計大於 0 才恢復；恢復後兩筆均為降額單，兩筆都勝才回 NORMAL，任一敗立即開啟新 Shadow cycle。</small>
    <small>已觀察來源市場 {experiment?.sourceMarkets ?? 0} · 建立 {experiment?.runtime?.opened ?? 0} · 降額 {experiment?.runtime?.halfStakeOpened ?? 0} · Shadow 阻擋 {experiment?.runtime?.shadowBlocked ?? 0} · 恢復 {experiment?.runtime?.recoveries ?? 0}</small>
    <small>平均進場價 {stats?.averageEntryPrice == null ? "—" : stats.averageEntryPrice.toFixed(3)} · 版本 {experiment?.version ?? "V1"}</small>
  </article>;
}

export default function LossStreakGuardDashboard() {
  const [ruleMounts, setRuleMounts] = useState<RuleMount[]>([]);
  const [researchTarget, setResearchTarget] = useState<HTMLElement | null>(null);
  const [mainApplyButton, setMainApplyButton] = useState<HTMLButtonElement | null>(null);
  const [livePayload, setLivePayload] = useState<LiveRulesPayload | null>(null);
  const [draftGuardFlags, setDraftGuardFlags] = useState<boolean[]>(EMPTY_FLAGS);
  const [draftCooldownFlags, setDraftCooldownFlags] = useState<boolean[]>(EMPTY_FLAGS);
  const [customDirty, setCustomDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const guardFlagsRef = useRef<boolean[]>(EMPTY_FLAGS);
  const cooldownFlagsRef = useRef<boolean[]>(EMPTY_FLAGS);
  const cooldownSelectsRef = useRef<Array<HTMLSelectElement | undefined>>([]);
  const customDirtyRef = useRef(false);
  const savingRef = useRef(false);
  const livePayloadRef = useRef<LiveRulesPayload | null>(null);
  const mainSaveWatchRef = useRef<number | null>(null);
  const { payload } = useSharedDashboardState<DashboardPayload>();

  useEffect(() => { guardFlagsRef.current = draftGuardFlags; }, [draftGuardFlags]);
  useEffect(() => { cooldownFlagsRef.current = draftCooldownFlags; }, [draftCooldownFlags]);
  useEffect(() => { customDirtyRef.current = customDirty; }, [customDirty]);
  useEffect(() => { savingRef.current = saving; }, [saving]);
  useEffect(() => { livePayloadRef.current = livePayload; }, [livePayload]);

  const persistLossProtectionDraft = async () => {
    if (savingRef.current || !customDirtyRef.current) return true;
    savingRef.current = true;
    setSaving(true);
    setSaveError("");
    try {
      const latestResponse = await fetch(apiUrl("/api/live-rules"), { cache: "no-store" });
      if (!latestResponse.ok) throw new Error(`無法重新讀取實單規則（HTTP ${latestResponse.status}）`);
      const latest = await latestResponse.json() as LiveRulesPayload;
      const strategies = latest.rules?.strategies ?? livePayloadRef.current?.rules?.strategies ?? EMPTY_STRATEGIES;
      if (!strategies.length) throw new Error("尚未讀到實單策略列表");
      const guardFlags = strategies.map((_, index) => Boolean(guardFlagsRef.current[index]));

      const response = await fetch(apiUrl("/api/live-rules"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [RULE_FIELD]: guardFlags }),
      });
      const body = await response.json() as {
        liveM0W?: LiveRulesPayload;
        error?: string;
      } & LiveRulesPayload;
      if (!response.ok) throw new Error(body.error ?? `後端拒絕連敗保護設定（HTTP ${response.status}）`);
      const next = body.liveM0W ?? body;
      const savedGuard = next.rules?.strategyLossStreakGuardEnabled ?? guardFlags;
      const savedCooldown = next.rules?.strategyLossCooldownEnabled
        ?? latest.rules?.strategyLossCooldownEnabled
        ?? strategies.map(() => false);
      setLivePayload(next);
      livePayloadRef.current = next;
      setDraftGuardFlags(savedGuard);
      setDraftCooldownFlags(savedCooldown);
      guardFlagsRef.current = savedGuard;
      cooldownFlagsRef.current = savedCooldown;
      setCustomDirty(false);
      customDirtyRef.current = false;
      const editor = document.querySelector<HTMLElement>(LIVE_RULES_TARGET);
      const apply = editor?.querySelector<HTMLButtonElement>(".live-rules-actions button:not(.secondary)");
      if (apply && !editor?.querySelector(".live-rules-head .dirty")) apply.disabled = true;
      return true;
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "未知錯誤");
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  useEffect(() => {
    let active = true;
    let loading = false;

    const locate = () => {
      const editor = document.querySelector<HTMLElement>(LIVE_RULES_TARGET);
      if (!editor) {
        setRuleMounts([]);
        setMainApplyButton(null);
        cooldownSelectsRef.current = [];
      } else {
        const baseLabels = Array.from(editor.querySelectorAll<HTMLLabelElement>("label:not([data-loss-streak-inline-mount])"));
        const drawdownLabels = baseLabels.filter(label => /回撤控制器/.test(label.textContent ?? ""));
        const cooldownLabels = baseLabels.filter(label => /兩連敗冷卻/.test(label.textContent ?? ""));
        cooldownSelectsRef.current = cooldownLabels.slice(0, 4).map(label => label.querySelector<HTMLSelectElement>("select") ?? undefined);
        cooldownLabels.forEach(label => {
          if (!label.hasAttribute("data-loss-protection-hidden-cooldown")) {
            label.dataset.lossProtectionPreviousDisplay = label.style.display;
            label.dataset.lossProtectionHiddenCooldown = "true";
          }
          label.style.display = "none";
        });
        const anchors = drawdownLabels.length ? drawdownLabels : cooldownLabels;
        const nextMounts = anchors.slice(0, 4).map((anchor, index) => {
          let mount = editor.querySelector<HTMLLabelElement>(`label[data-loss-streak-inline-mount="${index}"]`);
          if (!mount) {
            mount = document.createElement("label");
            mount.dataset.lossStreakInlineMount = String(index);
            anchor.insertAdjacentElement("afterend", mount);
          } else if (mount.previousElementSibling !== anchor) {
            anchor.insertAdjacentElement("afterend", mount);
          }
          return { index, element: mount };
        });
        editor.querySelectorAll<HTMLLabelElement>("label[data-loss-streak-inline-mount]").forEach(mount => {
          const index = Number(mount.dataset.lossStreakInlineMount);
          if (!nextMounts.some(item => item.index === index)) mount.remove();
        });
        setRuleMounts(current => {
          const unchanged = current.length === nextMounts.length
            && current.every((item, index) => item.index === nextMounts[index].index && item.element === nextMounts[index].element);
          return unchanged ? current : nextMounts;
        });
        const apply = editor.querySelector<HTMLButtonElement>(".live-rules-actions button:not(.secondary)");
        setMainApplyButton(current => current === apply ? current : apply);
      }

      const research = document.querySelector<HTMLElement>(RESEARCH_TARGET);
      setResearchTarget(current => current === research ? current : research);
    };

    const load = async () => {
      if (loading || document.visibilityState !== "visible") return;
      loading = true;
      try {
        const response = await fetch(apiUrl("/api/live-rules"), { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body = await response.json() as LiveRulesPayload;
        if (active && !customDirtyRef.current && !savingRef.current) {
          const strategies = body.rules?.strategies ?? EMPTY_STRATEGIES;
          const guard = body.rules?.strategyLossStreakGuardEnabled ?? strategies.map(() => false);
          const cooldown = body.rules?.strategyLossCooldownEnabled ?? strategies.map(() => false);
          setLivePayload(body);
          livePayloadRef.current = body;
          setDraftGuardFlags(guard);
          setDraftCooldownFlags(cooldown);
          guardFlagsRef.current = guard;
          cooldownFlagsRef.current = cooldown;
        }
      } catch {
        // Main dashboard owns the connection error display. Keep the last snapshot.
      } finally {
        loading = false;
      }
    };

    const tick = () => { locate(); void load(); };
    tick();
    const timer = window.setInterval(tick, 2_000);
    document.addEventListener("click", locate, true);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("click", locate, true);
      document.querySelectorAll("label[data-loss-streak-inline-mount]").forEach(mount => mount.remove());
      document.querySelectorAll<HTMLLabelElement>("label[data-loss-protection-hidden-cooldown]").forEach(label => {
        label.style.display = label.dataset.lossProtectionPreviousDisplay ?? "";
        delete label.dataset.lossProtectionPreviousDisplay;
        delete label.dataset.lossProtectionHiddenCooldown;
      });
    };
  }, []);

  useEffect(() => {
    if (!mainApplyButton) return;
    const editor = mainApplyButton.closest<HTMLElement>(LIVE_RULES_TARGET);

    const keepEnabledForCustomDraft = () => {
      if (customDirtyRef.current && mainApplyButton.disabled) mainApplyButton.disabled = false;
    };

    const waitForMainSave = () => {
      if (mainSaveWatchRef.current != null) window.clearInterval(mainSaveWatchRef.current);
      let sawSaving = false;
      let ticks = 0;
      mainSaveWatchRef.current = window.setInterval(async () => {
        ticks += 1;
        const buttonText = mainApplyButton.textContent ?? "";
        const headerText = editor?.querySelector(".live-rules-head > span")?.textContent ?? "";
        if (buttonText.includes("儲存中")) sawSaving = true;
        if (sawSaving && headerText.includes("規則已儲存")) {
          if (mainSaveWatchRef.current != null) window.clearInterval(mainSaveWatchRef.current);
          mainSaveWatchRef.current = null;
          await persistLossProtectionDraft();
          return;
        }
        if ((sawSaving && !buttonText.includes("儲存中") && editor?.querySelector(".live-rules-head .dirty")) || ticks >= 120) {
          if (mainSaveWatchRef.current != null) window.clearInterval(mainSaveWatchRef.current);
          mainSaveWatchRef.current = null;
        }
      }, 100);
    };

    const handleApply = async (event: MouseEvent) => {
      if (!customDirtyRef.current || savingRef.current) return;
      const mainRulesDirty = Boolean(editor?.querySelector(".live-rules-head .dirty"));
      if (mainRulesDirty) {
        waitForMainSave();
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      const strategies = livePayloadRef.current?.rules?.strategies ?? EMPTY_STRATEGIES;
      const summary = strategies.map((strategy, index) => {
        const mode = protectionMode(
          Boolean(cooldownFlagsRef.current[index]),
          Boolean(guardFlagsRef.current[index]),
        );
        const label = mode === "COOLDOWN"
          ? "兩連敗冷卻"
          : mode === "SHADOW"
            ? "三連敗 Shadow"
            : "關閉";
        return `${strategy}：${label}`;
      }).join("、");
      if (!window.confirm(`確定更新正式實單連敗保護？\n\n${summary}\n\n每個策略只能選擇一種模式；新規則只影響之後的新訊號。`)) return;
      await persistLossProtectionDraft();
    };

    keepEnabledForCustomDraft();
    const observer = new MutationObserver(keepEnabledForCustomDraft);
    observer.observe(mainApplyButton, { attributes: true, attributeFilter: ["disabled"] });
    mainApplyButton.addEventListener("click", handleApply, true);
    return () => {
      observer.disconnect();
      mainApplyButton.removeEventListener("click", handleApply, true);
      if (mainSaveWatchRef.current != null) {
        window.clearInterval(mainSaveWatchRef.current);
        mainSaveWatchRef.current = null;
      }
    };
  }, [mainApplyButton, customDirty]);

  const changeMode = (index: number, mode: LossProtectionMode) => {
    const strategies = livePayload?.rules?.strategies ?? EMPTY_STRATEGIES;
    if (!strategies[index] || savingRef.current) return;
    const nextGuard = strategies.map((_, slot) => (
      slot === index ? mode === "SHADOW" : Boolean(guardFlagsRef.current[slot])
    ));
    const nextCooldown = strategies.map((_, slot) => (
      slot === index ? mode === "COOLDOWN" : Boolean(cooldownFlagsRef.current[slot])
    ));
    setDraftGuardFlags(nextGuard);
    setDraftCooldownFlags(nextCooldown);
    guardFlagsRef.current = nextGuard;
    cooldownFlagsRef.current = nextCooldown;
    setNativeSelectValue(
      cooldownSelectsRef.current[index],
      mode === "COOLDOWN" ? "enabled" : "disabled",
    );
    setCustomDirty(true);
    customDirtyRef.current = true;
    setSaveError("");
    if (mainApplyButton) mainApplyButton.disabled = false;
  };

  const strategies = livePayload?.rules?.strategies ?? EMPTY_STRATEGIES;
  const guardStates = livePayload?.strategyLossStreakGuardStates ?? [];
  const cooldownStates = livePayload?.strategyLossCooldownStates ?? [];
  const testCard = useMemo(() => <TestStrategyCard payload={payload} />, [payload]);

  return <>
    {ruleMounts.map(({ index, element }) => createPortal(
      <InlineLossProtectionControl
        strategy={strategies[index] ?? `策略 ${index + 1}`}
        index={index}
        mode={protectionMode(
          Boolean(draftCooldownFlags[index]),
          Boolean(draftGuardFlags[index]),
        )}
        guardState={guardStates[index]}
        cooldownState={cooldownStates[index]}
        dirty={customDirty}
        saving={saving}
        error={saveError}
        onChange={changeMode}
      />,
      element,
      `loss-protection-rule-${index}`,
    ))}
    {researchTarget ? createPortal(testCard, researchTarget) : null}
  </>;
}
