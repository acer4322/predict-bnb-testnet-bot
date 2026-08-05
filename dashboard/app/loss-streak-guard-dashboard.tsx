"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useSharedDashboardState } from "./shared-dashboard-state";

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

function LossStreakRulesEditor({
  payload,
  onRefresh,
}: {
  payload: LiveRulesPayload | null;
  onRefresh: (value: LiveRulesPayload) => void;
}) {
  const strategies = payload?.rules?.strategies ?? EMPTY_STRATEGIES;
  const saved = payload?.rules?.strategyLossStreakGuardEnabled ?? EMPTY_FLAGS;
  const states = payload?.strategyLossStreakGuardStates ?? [];
  const [draft, setDraft] = useState<boolean[]>(saved);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState("規則已同步");

  useEffect(() => {
    if (dirty) return;
    setDraft(strategies.map((_, index) => Boolean(saved[index])));
  }, [dirty, strategies, saved]);

  const save = async () => {
    setStatus("儲存中…");
    try {
      const response = await fetch("/api/live-rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [RULE_FIELD]: draft }),
      });
      const body = await response.json() as {
        liveM0W?: LiveRulesPayload;
        error?: string;
      } & LiveRulesPayload;
      if (!response.ok) throw new Error(body.error ?? "後端拒絕連敗過濾設定");
      const next = body.liveM0W ?? body;
      onRefresh(next);
      setDirty(false);
      setStatus("連敗過濾已持久化");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "儲存失敗");
    }
  };

  return <div data-loss-streak-rules style={{ marginTop: 16, padding: 16, border: "1px solid rgba(126, 145, 178, .28)", borderRadius: 16, background: "rgba(7, 12, 22, .54)" }}>
    <div style={{ display: "flex", gap: 12, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
      <div>
        <span className="eyebrow">PERSISTENT · LOSS STREAK GUARD V1</span>
        <h4 style={{ margin: "4px 0" }}>逐策略連敗過濾器</h4>
        <small>與回撤控制器相同，按實單策略槽位獨立啟用並寫入 live_settings。</small>
      </div>
      <span className={dirty ? "dirty" : "synced"}>{dirty ? "有未儲存變更" : status}</span>
    </div>

    <div className="live-rules-grid" style={{ marginTop: 14 }}>
      {strategies.map((strategy, index) => {
        const state = states[index];
        return <label key={`${strategy}-${index}`}>
          <span>策略 {index + 1} 連敗過濾器</span>
          <select
            aria-label={`策略 ${index + 1} 是否使用連敗過濾器`}
            value={draft[index] ? "enabled" : "disabled"}
            onChange={event => {
              const enabled = event.target.value === "enabled";
              setDraft(current => strategies.map((_, slot) => slot === index ? enabled : Boolean(current[slot])));
              setDirty(true);
              setStatus("有未儲存變更");
            }}
          >
            <option value="disabled">不使用連敗過濾器</option>
            <option value="enabled">三連敗 Shadow／正 PnL 恢復</option>
          </select>
          <small>{draft[index] ? `${modeText(state?.mode)} · ${stateDetail(state)}` : "停用時不阻擋；後端仍保留官方結算狀態供日後啟用。"}</small>
          {state?.lastError && <small style={{ color: "#ffbd87" }}>{state.lastError}</small>}
        </label>;
      })}
    </div>

    <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 14, flexWrap: "wrap" }}>
      <button type="button" disabled={!dirty || status === "儲存中…"} onClick={save}>
        {status === "儲存中…" ? "儲存中…" : "確認並持久化連敗過濾"}
      </button>
      <small>兩連敗後下一筆初始單 50%；三連敗後不送單。Shadow 至少三筆且最近三筆 Paper PnL 合計 &gt; 0 才進入兩筆半倉 PROBATION；任何一敗重新進 Shadow。</small>
    </div>
  </div>;
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
  const [rulesTarget, setRulesTarget] = useState<HTMLElement | null>(null);
  const [researchTarget, setResearchTarget] = useState<HTMLElement | null>(null);
  const [livePayload, setLivePayload] = useState<LiveRulesPayload | null>(null);
  const { payload } = useSharedDashboardState<DashboardPayload>();

  useEffect(() => {
    let active = true;
    let loading = false;
    const locate = () => {
      const editor = document.querySelector<HTMLElement>(LIVE_RULES_TARGET);
      if (editor) {
        let mount = editor.querySelector<HTMLElement>("[data-loss-streak-rules-mount]");
        if (!mount) {
          mount = document.createElement("div");
          mount.dataset.lossStreakRulesMount = "true";
          editor.appendChild(mount);
        }
        setRulesTarget(current => current === mount ? current : mount);
      } else {
        setRulesTarget(null);
      }
      const research = document.querySelector<HTMLElement>(RESEARCH_TARGET);
      setResearchTarget(current => current === research ? current : research);
    };
    const load = async () => {
      if (loading || document.visibilityState !== "visible") return;
      loading = true;
      try {
        const response = await fetch("/api/live-rules", { cache: "no-store" });
        if (!response.ok) throw new Error();
        const body = await response.json() as LiveRulesPayload;
        if (active) setLivePayload(body);
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
    };
  }, []);

  const rulesEditor = useMemo(() => <LossStreakRulesEditor payload={livePayload} onRefresh={setLivePayload} />, [livePayload]);
  const testCard = useMemo(() => <TestStrategyCard payload={payload} />, [payload]);

  return <>
    {rulesTarget ? createPortal(rulesEditor, rulesTarget) : null}
    {researchTarget ? createPortal(testCard, researchTarget) : null}
  </>;
}
