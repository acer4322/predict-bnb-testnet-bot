"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const STRATEGIES = ["R_DECISION_RANK1", "R_DECISION_RANK2"] as const;
type StrategyId = (typeof STRATEGIES)[number];

type RecentTrade = {
  id?: number;
  market_id?: number;
  side?: string;
  status?: string;
  entry_price?: number | null;
  stake?: number | null;
  pnl?: number | null;
  opened_at?: string | null;
  closed_at?: string | null;
};

type RecentDecision = {
  strategy?: string;
  market_id?: number;
  side?: string | null;
  status?: string;
  reason?: string;
  selected_family?: string | null;
  agreement_weight?: number | null;
  estimated_probability?: number | null;
  effective_cost?: number | null;
  model_edge?: number | null;
  trend_status?: string | null;
  evaluations?: number;
  updated_at?: string;
};

type CurrentPreview = {
  strategy?: string;
  marketId?: number;
  status?: string;
  reason?: string;
  side?: string | null;
  selectedFamily?: string | null;
  agreementWeight?: number | null;
  estimatedProbability?: number | null;
  effectiveCost?: number | null;
  modelEdge?: number | null;
  trend?: {
    status?: string;
    moveBps?: number;
    pathEr?: number;
    trendSide?: string | null;
  } | null;
  updatedAt?: string;
};

type StrategyStats = {
  strategy: StrategyId;
  displayName: string;
  mode: string;
  trades: number;
  open: number;
  settled: number;
  wins: number;
  losses: number;
  winRate: number | null;
  realizedPnl: number;
  maxDrawdown: number;
  averageEntryPrice: number | null;
  liveSelectable: boolean;
  paperOnly: boolean;
  forwardOnly: boolean;
  statusCounts: Record<string, number>;
  currentPreview: CurrentPreview | null;
  recentTrades: RecentTrade[];
  recentDecisions: RecentDecision[];
};

type DecisionPayload = {
  version: string;
  paperOnly: boolean;
  forwardOnly: boolean;
  nativeEventDriven: boolean;
  liveSelectable: boolean;
  forwardStartedAt?: string | null;
  includedFamilies: string[];
  excludedFamilies: string[];
  rules: {
    historyLimit?: number;
    historyHalfLife?: number;
    minimumHistory?: number;
    rank1MinimumFamilies?: number;
    rank1AgreementWeight?: number;
    rank1FamilyCap?: number;
    rank2FamilyCap?: number;
    rank2CapWindow?: number;
    minimumNetEdge?: number;
    stakeUsdt?: number;
    slippageBps?: number;
    maximumBookAgeMs?: number;
    maximumBookSkewMs?: number;
    maximumSpread?: number;
    oneTradePerMarket?: boolean;
    entryMode?: string;
    trendGate?: {
      minimumElapsedSeconds?: number;
      minimumMoveBps?: number;
      minimumPathEr?: number;
      minimumSamples?: number;
      maximumSpotAgeMs?: number;
      missingDataPolicy?: string;
    };
  };
  strategies: Record<StrategyId, StrategyStats>;
};

type ApiState = {
  decisionStrategyTest?: DecisionPayload;
};

type ResetState = { status: "idle" | "loading" | "success" | "error"; message: string };

const pageStyle: React.CSSProperties = {
  minHeight: "100vh",
  padding: "92px 24px 80px",
  color: "#edf3ff",
  background: "radial-gradient(circle at 15% 10%, rgba(39, 95, 157, .22), transparent 36%), radial-gradient(circle at 90% 22%, rgba(94, 55, 151, .18), transparent 34%), #070a10",
};

const shellStyle: React.CSSProperties = { maxWidth: 1480, margin: "0 auto", display: "grid", gap: 18 };
const panelStyle: React.CSSProperties = { border: "1px solid rgba(132, 157, 198, .24)", borderRadius: 22, background: "rgba(10, 15, 25, .88)", boxShadow: "0 22px 70px rgba(0, 0, 0, .32)" };
const cardStyle: React.CSSProperties = { ...panelStyle, padding: 20, display: "grid", gap: 16, minWidth: 0 };
const badgeStyle: React.CSSProperties = { display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 9px", borderRadius: 999, border: "1px solid rgba(129, 160, 209, .28)", background: "rgba(20, 30, 48, .8)", fontSize: 12, fontWeight: 700 };

function apiUrl(path: string) {
  if (typeof window === "undefined") return `http://127.0.0.1:8766${path}`;
  return `http://${window.location.hostname}:8766${path}`;
}

function decimal(value: number | null | undefined, digits = 2) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function percent(value: number | null | undefined, digits = 1) {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : "—";
}

function money(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : "-"}$${Math.abs(value).toFixed(2)}`;
}

function time(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-TW", { hour12: false });
}

function tone(status: string | null | undefined) {
  const key = String(status ?? "").toUpperCase();
  if (key.includes("OPEN") || key.includes("PASS")) return "#7ef0b8";
  if (key.includes("BLOCK") || key.includes("LOSS") || key.includes("ERROR")) return "#ff8f9e";
  if (key.includes("WAIT") || key.includes("EDGE") || key.includes("CAP")) return "#ffd48a";
  return "#a9c7ff";
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div style={{ padding: 13, borderRadius: 15, border: "1px solid rgba(126, 151, 190, .18)", background: "rgba(8, 13, 22, .72)" }}>
    <div style={{ color: "#8fa4c5", fontSize: 12 }}>{label}</div>
    <div style={{ marginTop: 5, fontSize: 23, fontWeight: 800, letterSpacing: "-.02em" }}>{value}</div>
    {detail ? <div style={{ marginTop: 4, color: "#7487a7", fontSize: 11 }}>{detail}</div> : null}
  </div>;
}

function DecisionCard({ stats, resetting, onReset }: { stats: StrategyStats; resetting: ResetState; onReset: (strategy: StrategyId) => void }) {
  const preview = stats.currentPreview;
  const pnlColor = stats.realizedPnl >= 0 ? "#7ef0b8" : "#ff8f9e";
  return <article style={cardStyle}>
    <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 14, flexWrap: "wrap" }}>
      <div>
        <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginBottom: 9 }}>
          <span style={{ ...badgeStyle, color: "#7ee3f5" }}>PAPER FORWARD</span>
          <span style={{ ...badgeStyle, color: "#a5f2ba" }}>實單白名單可選</span>
          <span style={{ ...badgeStyle, color: "#ffd48a" }}>原生事件驅動</span>
        </div>
        <h2 style={{ margin: 0, fontSize: 26 }}>{stats.displayName}</h2>
        <div style={{ marginTop: 6, color: "#8fa4c5", fontFamily: "var(--font-mono)", fontSize: 12 }}>{stats.strategy} · {stats.mode}</div>
      </div>
      <button
        type="button"
        onClick={() => onReset(stats.strategy)}
        disabled={resetting.status === "loading"}
        style={{ border: "1px solid rgba(126, 151, 190, .3)", borderRadius: 12, background: "rgba(22, 31, 48, .9)", color: "#dbe7ff", padding: "9px 12px", cursor: resetting.status === "loading" ? "wait" : "pointer" }}
      >
        {resetting.status === "loading" ? "重設中…" : "重設測量起點"}
      </button>
    </header>

    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(132px, 1fr))", gap: 10 }}>
      <Metric label="已結算" value={`${stats.settled}`} detail={`總交易 ${stats.trades} · 未結算 ${stats.open}`} />
      <Metric label="勝率" value={percent(stats.winRate)} detail={`${stats.wins} 勝 / ${stats.losses} 敗`} />
      <div style={{ color: pnlColor }}><Metric label="已實現收益" value={money(stats.realizedPnl)} detail={`最大回撤 ${money(stats.maxDrawdown)}`} /></div>
      <Metric label="平均進場價" value={decimal(stats.averageEntryPrice, 3)} detail="含策略當下執行滑點" />
    </div>

    <section style={{ padding: 15, borderRadius: 16, border: `1px solid ${tone(preview?.status)}44`, background: "rgba(7, 12, 21, .78)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <strong style={{ color: tone(preview?.status) }}>當輪：{preview?.status ?? "等待第一個評估事件"}</strong>
        <span style={{ color: "#7588a8", fontSize: 12 }}>{time(preview?.updatedAt)}</span>
      </div>
      <div style={{ marginTop: 7, color: "#b6c5dd", lineHeight: 1.55 }}>{preview?.reason ?? "尚未產生前向決策。"}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 8, marginTop: 12, fontSize: 12 }}>
        <div>市場 <b>#{preview?.marketId ?? "—"}</b></div>
        <div>方向 <b>{preview?.side ?? "—"}</b></div>
        <div>選中家族 <b>{preview?.selectedFamily ?? "—"}</b></div>
        <div>共識權重 <b>{percent(preview?.agreementWeight)}</b></div>
        <div>後驗機率 <b>{percent(preview?.estimatedProbability)}</b></div>
        <div>有效成本 <b>{decimal(preview?.effectiveCost, 4)}</b></div>
        <div>淨 Edge <b>{percent(preview?.modelEdge, 2)}</b></div>
        <div>趨勢閘門 <b>{preview?.trend?.status ?? "—"}</b></div>
      </div>
    </section>

    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(0, .8fr)", gap: 12 }}>
      <section style={{ minWidth: 0 }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>最近決策</h3>
        <div style={{ overflowX: "auto", border: "1px solid rgba(126, 151, 190, .16)", borderRadius: 13 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 660, fontSize: 12 }}>
            <thead><tr style={{ color: "#8297b9", textAlign: "left" }}><th style={{ padding: 9 }}>市場</th><th>狀態</th><th>方向</th><th>家族</th><th>機率 / 成本</th><th>Edge</th><th>原因</th></tr></thead>
            <tbody>{stats.recentDecisions.slice(0, 8).map((row, index) => <tr key={`${row.market_id}-${index}`} style={{ borderTop: "1px solid rgba(126, 151, 190, .12)" }}>
              <td style={{ padding: 9 }}>#{row.market_id ?? "—"}</td>
              <td style={{ color: tone(row.status) }}>{row.status ?? "—"}</td>
              <td>{row.side ?? "—"}</td>
              <td>{row.selected_family ?? "—"}</td>
              <td>{percent(row.estimated_probability)} / {decimal(row.effective_cost, 3)}</td>
              <td>{percent(row.model_edge, 2)}</td>
              <td style={{ color: "#9fb0ca", maxWidth: 260 }}>{row.reason ?? "—"}</td>
            </tr>)}</tbody>
          </table>
          {stats.recentDecisions.length === 0 ? <div style={{ padding: 18, color: "#7385a4" }}>尚無決策紀錄。</div> : null}
        </div>
      </section>
      <section>
        <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>阻擋／放行分布</h3>
        <div style={{ display: "grid", gap: 7 }}>
          {Object.entries(stats.statusCounts).sort((a, b) => b[1] - a[1]).map(([status, count]) => <div key={status} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "8px 10px", borderRadius: 11, background: "rgba(7, 12, 21, .65)" }}>
            <span style={{ color: tone(status) }}>{status}</span><b>{count}</b>
          </div>)}
          {Object.keys(stats.statusCounts).length === 0 ? <div style={{ color: "#7385a4" }}>尚無前向樣本。</div> : null}
        </div>
      </section>
    </div>

    {resetting.message ? <div style={{ color: resetting.status === "error" ? "#ff8f9e" : "#9fb6d8", fontSize: 12 }}>{resetting.message}</div> : null}
  </article>;
}

export default function DecisionStrategyTestPanel() {
  const [payload, setPayload] = useState<DecisionPayload | null>(null);
  const [status, setStatus] = useState("連線中…");
  const [resets, setResets] = useState<Record<StrategyId, ResetState>>({
    R_DECISION_RANK1: { status: "idle", message: "" },
    R_DECISION_RANK2: { status: "idle", message: "" },
  });

  const load = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/state"), { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json() as ApiState;
      setPayload(data.decisionStrategyTest ?? null);
      setStatus(data.decisionStrategyTest ? `已同步 · ${new Date().toLocaleTimeString("zh-TW", { hour12: false })}` : "後端尚未載入決策策略 patch");
    } catch (error) {
      setStatus(error instanceof Error ? `讀取失敗：${error.message}` : "讀取失敗");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 2_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const resetStrategy = useCallback(async (strategy: StrategyId) => {
    if (!window.confirm(`確定重設 ${strategy} 的測量起點？歷史交易不會被刪除。`)) return;
    setResets(current => ({ ...current, [strategy]: { status: "loading", message: "正在設定新的測量起點…" } }));
    try {
      const response = await fetch(apiUrl("/api/strategy-reset"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy }),
      });
      const result = await response.json().catch(() => ({})) as { error?: string; message?: string };
      if (!response.ok) throw new Error(result.error ?? result.message ?? "伺服器拒絕重設");
      setResets(current => ({ ...current, [strategy]: { status: "success", message: "已設定新起點。" } }));
      await load();
    } catch (error) {
      setResets(current => ({ ...current, [strategy]: { status: "error", message: error instanceof Error ? error.message : "重設失敗" } }));
    }
  }, [load]);

  const cards = useMemo(() => payload ? STRATEGIES.map(strategy => payload.strategies?.[strategy]).filter((item): item is StrategyStats => Boolean(item)) : [], [payload]);

  return <main style={pageStyle}>
    <div style={shellStyle}>
      <header style={{ ...panelStyle, padding: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginBottom: 10 }}>
              <span style={{ ...badgeStyle, color: "#7ee3f5" }}>兩組獨立 PAPER</span>
              <span style={{ ...badgeStyle, color: "#ffbe8c" }}>逆強趨勢阻擋</span>
              <span style={{ ...badgeStyle, color: "#a5f2ba" }}>LIVE WHITELIST</span>
            </div>
            <h1 style={{ margin: 0, fontSize: "clamp(30px, 4vw, 48px)", letterSpacing: "-.04em" }}>決策策略測試</h1>
            <p style={{ margin: "10px 0 0", color: "#9fb0ca", lineHeight: 1.65, maxWidth: 900 }}>
              Rank 1 與 Rank 2 使用專案原生 M-series 事件流、SQLite 前向帳本與真實當下雙 token 報價。Paper 永遠獨立記帳；只有在實單控制列明確選中策略且通過既有預檢時，候選訊號才可能進入實單引擎。
            </p>
          </div>
          <div style={{ textAlign: "right", color: "#8498b8", fontSize: 12 }}>
            <div>{status}</div>
            <div style={{ marginTop: 4 }}>Forward 起點：{time(payload?.forwardStartedAt)}</div>
          </div>
        </div>
      </header>

      <section style={{ ...panelStyle, padding: 18, display: "grid", gap: 12 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          <div style={{ padding: 14, borderRadius: 15, background: "rgba(8, 13, 22, .7)" }}><b>納入家族</b><div style={{ marginTop: 7, color: "#a5f2ba" }}>{payload?.includedFamilies?.join(" · ") ?? "FUTURES_LEAD · CALIBRATED_VALUE · CONSENSUS"}</div></div>
          <div style={{ padding: 14, borderRadius: 15, background: "rgba(8, 13, 22, .7)" }}><b>正式排除</b><div style={{ marginTop: 7, color: "#ff9cac" }}>{payload?.excludedFamilies?.join(" · ") ?? "M01 · MICROPRICE · OFI"}</div></div>
          <div style={{ padding: 14, borderRadius: 15, background: "rgba(8, 13, 22, .7)" }}><b>共同安全閘門</b><div style={{ marginTop: 7, color: "#ffd48a" }}>淨 Edge ≥ {percent(payload?.rules.minimumNetEdge)} · 簿齡 ≤ {decimal(payload?.rules.maximumBookAgeMs, 0)}ms · 缺趨勢資料直接阻擋</div></div>
        </div>
        <div style={{ color: "#7f93b3", fontSize: 12, lineHeight: 1.6 }}>
          逆強趨勢：市場經過至少 {decimal(payload?.rules.trendGate?.minimumElapsedSeconds, 0)} 秒、起始移動 ≥ {decimal(payload?.rules.trendGate?.minimumMoveBps, 1)} bps、Path ER ≥ {decimal(payload?.rules.trendGate?.minimumPathEr, 2)} 時，反向訊號阻擋。每市場最多一筆；不使用固定 60／45／30 秒延後進場。
        </div>
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 570px), 1fr))", gap: 16 }}>
        {cards.map(stats => <DecisionCard key={stats.strategy} stats={stats} resetting={resets[stats.strategy]} onReset={resetStrategy} />)}
      </section>

      {!payload ? <section style={{ ...panelStyle, padding: 24, color: "#98aac7" }}>等待後端 `decisionStrategyTest` 狀態。若服務剛更新，請確認 API 已重新啟動。</section> : null}
    </div>
  </main>;
}
