"use client";

import { useEffect, useMemo, useState } from "react";
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

type GuardState = {
  enabled?: boolean;
  mode?: string;
  consecutiveLosses?: number;
  probationRemaining?: number;
  shadowSampleCount?: number;
  latestShadowPnlSum?: number | null;
  lastError?: string | null;
};

type LiveRulesPayload = {
  rules?: {
    strategies?: string[];
    strategyLossStreakGuardEnabled?: boolean[];
  };
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
  if (mode === "PROBATION") return "PROBATION 半倉觀察";
  return "NORMAL 正常執行";
}

function stateDetail(state: GuardState | undefined) {
  if (!state) return "等待後端狀態";
  if (state.mode === "SHADOW") {
    return `Paper ${state.shadowSampleCount ?? 0}/3 · 最近三筆 PnL ${money(state.latestShadowPnlSum)}`;
  }
  if (state.mode === "PROBATION") {
    return `剩餘 ${state.probationRemaining ?? 0} 筆半倉勝利驗證`;
  }
  return `目前連敗 ${state.consecutiveLosses ?? 0}/3${state.consecutiveLosses === 2 ? " · 下一筆半倉" : ""}`;
}

function InlineLossStreakControl({
  strategy,
  index,
  enabled,
  state,
  saving,
  error,
  onChange,
}: {
  strategy: string;
  index: number;
  enabled: boolean;
  state?: GuardState;
  saving: boolean;
  error?: string;
  onChange: (index: number, enabled: boolean) => void;
}) {
  return <>
    <span>策略 {index + 1} 連敗過濾</span>
    <select
      aria-label={`策略 ${index + 1} ${strategy} 是否使用連敗過濾`}
      value={enabled ? "enabled" : "disabled"}
      disabled={saving}
      onChange={event => onChange(index, event.target.value === "enabled")}
    >
      <option value="disabled">不使用連敗過濾</option>
      <option value="enabled">啟用三連敗 Shadow 過濾</option>
    </select>
    <small>
      {saving
        ? "正在儲存…"
        : error
          ? `儲存失敗：${error}`
          : enabled
            ? `${modeText(state?.mode)} · ${stateDetail(state)}`
            : "關閉；不影響這個策略的實單。"}
    </small>
    {enabled && <small>兩連敗後下一筆半倉；三連敗暫停。最近三筆 Paper PnL 合計轉正後，以兩筆半倉重新驗證。</small>}
    {state?.lastError && <small style={{ color: "#ffbd87" }}>{state.lastError}</small>}
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
    <p>完整鏡像 R_MICROPRICE_CONFIRM。正常狀態按原本金建立獨立 Paper 單；兩連敗後下一筆改為半倉，第三敗後只觀察來源訊號、不建立測試單。</p>
    <small>Shadow 至少累積三筆已結算來源單，且最近三筆 PnL 合計大於 0 才恢復；恢復後兩筆均為半倉，兩筆都勝才回 NORMAL，任一敗立即開啟新 Shadow cycle。</small>
    <small>已觀察來源市場 {experiment?.sourceMarkets ?? 0} · 建立 {experiment?.runtime?.opened ?? 0} · 半倉 {experiment?.runtime?.halfStakeOpened ?? 0} · Shadow 阻擋 {experiment?.runtime?.shadowBlocked ?? 0} · 恢復 {experiment?.runtime?.recoveries ?? 0}</small>
    <small>平均進場價 {stats?.averageEntryPrice == null ? "—" : stats.averageEntryPrice.toFixed(3)} · 版本 {experiment?.version ?? "V1"}</small>
  </article>;
}

export default function LossStreakGuardDashboard() {
  const [ruleMounts, setRuleMounts] = useState<RuleMount[]>([]);
  const [researchTarget, setResearchTarget] = useState<HTMLElement | null>(null);
  const [livePayload, setLivePayload] = useState<LiveRulesPayload | null>(null);
  const [savingIndex, setSavingIndex] = useState<number | null>(null);
  const [saveErrors, setSaveErrors] = useState<Record<number, string>>({});
  const { payload } = useSharedDashboardState<DashboardPayload>();

  useEffect(() => {
    let active = true;
    let loading = false;

    const locate = () => {
      const editor = document.querySelector<HTMLElement>(LIVE_RULES_TARGET);
      if (!editor) {
        setRuleMounts([]);
      } else {
        const baseLabels = Array.from(editor.querySelectorAll<HTMLLabelElement>("label:not([data-loss-streak-inline-mount])"));
        const drawdownLabels = baseLabels.filter(label => /回撤控制器/.test(label.textContent ?? ""));
        const cooldownLabels = baseLabels.filter(label => /兩連敗冷卻/.test(label.textContent ?? ""));
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
        if (active && savingIndex == null) setLivePayload(body);
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
    };
  }, [savingIndex]);

  const changeRule = async (index: number, enabled: boolean) => {
    const strategies = livePayload?.rules?.strategies ?? EMPTY_STRATEGIES;
    const currentFlags = livePayload?.rules?.strategyLossStreakGuardEnabled ?? EMPTY_FLAGS;
    if (!strategies[index] || savingIndex != null) return;
    const nextFlags = strategies.map((_, slot) => slot === index ? enabled : Boolean(currentFlags[slot]));
    const previous = livePayload;
    setLivePayload(current => current ? {
      ...current,
      rules: {
        ...current.rules,
        strategies,
        strategyLossStreakGuardEnabled: nextFlags,
      },
    } : current);
    setSavingIndex(index);
    setSaveErrors(current => {
      const next = { ...current };
      delete next[index];
      return next;
    });

    try {
      const response = await fetch(apiUrl("/api/live-rules"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [RULE_FIELD]: nextFlags }),
      });
      const body = await response.json() as {
        liveM0W?: LiveRulesPayload;
        error?: string;
      } & LiveRulesPayload;
      if (!response.ok) throw new Error(body.error ?? `後端拒絕連敗過濾設定（HTTP ${response.status}）`);
      setLivePayload(body.liveM0W ?? body);
    } catch (error) {
      setLivePayload(previous);
      setSaveErrors(current => ({
        ...current,
        [index]: error instanceof Error ? error.message : "未知錯誤",
      }));
    } finally {
      setSavingIndex(null);
    }
  };

  const strategies = livePayload?.rules?.strategies ?? EMPTY_STRATEGIES;
  const flags = livePayload?.rules?.strategyLossStreakGuardEnabled ?? EMPTY_FLAGS;
  const states = livePayload?.strategyLossStreakGuardStates ?? [];
  const testCard = useMemo(() => <TestStrategyCard payload={payload} />, [payload]);

  return <>
    {ruleMounts.map(({ index, element }) => createPortal(
      <InlineLossStreakControl
        strategy={strategies[index] ?? `策略 ${index + 1}`}
        index={index}
        enabled={Boolean(flags[index])}
        state={states[index]}
        saving={savingIndex === index}
        error={saveErrors[index]}
        onChange={changeRule}
      />,
      element,
      `loss-streak-rule-${index}`,
    ))}
    {researchTarget ? createPortal(testCard, researchTarget) : null}
  </>;
}
