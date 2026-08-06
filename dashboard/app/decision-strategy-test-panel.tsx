"use client";

const STRATEGIES = [
  ["R_DECISION_RANK1", "Rank 1 · Utility Weighted Majority", "效用加權共識", "cyan"],
  ["R_DECISION_RANK2", "Rank 2 · Recent Context Champion", "同情境冠軍＋50% 家族上限", "purple"],
] as const;

type StrategyId = (typeof STRATEGIES)[number][0];
type ResetState = { status: "loading" | "success" | "error"; message: string };

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
  updated_at?: string;
};

type CurrentPreview = {
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
  strategy?: StrategyId;
  displayName?: string;
  mode?: string;
  trades?: number;
  open?: number;
  settled?: number;
  wins?: number;
  losses?: number;
  winRate?: number | null;
  realizedPnl?: number;
  maxDrawdown?: number;
  averageEntryPrice?: number | null;
  liveSelectable?: boolean;
  statusCounts?: Record<string, number>;
  currentPreview?: CurrentPreview | null;
  recentDecisions?: RecentDecision[];
};

type DecisionExperiment = {
  version?: string;
  paperOnly?: boolean;
  forwardOnly?: boolean;
  nativeEventDriven?: boolean;
  liveSelectable?: boolean;
  forwardStartedAt?: string | null;
  includedFamilies?: string[];
  excludedFamilies?: string[];
  rules?: {
    rank1MinimumFamilies?: number;
    rank1AgreementWeight?: number;
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
  strategies?: Partial<Record<StrategyId, StrategyStats>>;
};

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
  if (key.includes("OPEN") || key.includes("PASS") || key.includes("ALLOW")) return "positive";
  if (key.includes("BLOCK") || key.includes("LOSS") || key.includes("ERROR")) return "negative";
  return "";
}

function compact(values: string[] | undefined, fallback: string) {
  return values?.length ? values.join(" · ") : fallback;
}

export default function DecisionStrategyTestPanel({
  payload,
  onReset,
  resetStates,
}: {
  payload: any;
  onReset: (strategy: StrategyId) => void;
  resetStates: Partial<Record<StrategyId, ResetState>>;
}) {
  const experiment = payload?.decisionStrategyTest as DecisionExperiment | undefined;
  const rules = experiment?.rules;
  const allRecent = STRATEGIES.flatMap(([strategy]) =>
    (experiment?.strategies?.[strategy]?.recentDecisions ?? []).map(item => ({ ...item, strategy }))
  ).sort((left, right) => String(right.updated_at ?? "").localeCompare(String(left.updated_at ?? ""))).slice(0, 30);

  return <div role="tabpanel" id="decision-strategy-panel" aria-labelledby="decision-strategy-tab" className="m-exit-experiment research-forward-panel decision-strategy-test-panel">
    <style>{`
      .decision-strategy-test-panel .decision-runtime{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:10px;margin:14px 0}
      .decision-strategy-test-panel .decision-runtime article{padding:12px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
      .decision-strategy-test-panel .decision-runtime span,.decision-strategy-test-panel .decision-runtime small{display:block;color:#91a0bb}
      .decision-strategy-test-panel .decision-runtime strong{display:block;margin:4px 0}
      .decision-strategy-test-panel .decision-preview{margin:12px 0;padding:12px;border-radius:12px;background:rgba(126,145,178,.08)}
      .decision-strategy-test-panel .decision-preview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(125px,1fr));gap:8px;margin-top:10px}
      .decision-strategy-test-panel .decision-preview-grid div{padding:8px;border-radius:9px;background:rgba(7,12,21,.65)}
      .decision-strategy-test-panel .decision-status-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:7px;margin-top:10px}
      .decision-strategy-test-panel .decision-status-list div{display:flex;justify-content:space-between;gap:8px;padding:8px 10px;border-radius:10px;background:rgba(7,12,21,.65)}
      .decision-strategy-test-panel .decision-reset{border:1px solid rgba(126,145,178,.3);border-radius:10px;background:rgba(22,31,48,.9);color:#dbe7ff;padding:7px 10px;cursor:pointer}
      .decision-strategy-test-panel .decision-reset:disabled{cursor:wait;opacity:.65}
    `}</style>

    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">DECISION CONTROLLERS · NATIVE FORWARD PAPER</span><h3>決策策略測試 · Rank 1／Rank 2</h3></div>
      <p>與「逆強趨勢阻擋」相同，這是主頁原生 React 頁籤；直接共用主監控的 API state，不建立獨立頁面、不使用 DOM 注入，也不額外輪詢。</p>
    </section>

    <section className="m-exit-rules" aria-label="決策策略共同規則">
      <div className="m-exit-rules-head">
        <div><span className="eyebrow">M01 / RAW MICROPRICE / OFI EXCLUDED</span><h3>共同資料與安全閘門</h3></div>
        <span className="m-exit-api-state live">{experiment ? "FORWARD ACTIVE" : "WAITING API"}</span>
      </div>
      <div className="decision-runtime">
        <article><span>納入家族</span><strong>{compact(experiment?.includedFamilies, "FUTURES_LEAD · CALIBRATED_VALUE · CONSENSUS")}</strong><small>每家族只算一票</small></article>
        <article><span>正式排除</span><strong>{compact(experiment?.excludedFamilies, "M01 · MICROPRICE · OFI")}</strong><small>不進歷史、權重或冠軍計算</small></article>
        <article><span>成交品質</span><strong>簿齡 ≤ {decimal(rules?.maximumBookAgeMs, 0)}ms · spread ≤ {decimal(rules?.maximumSpread, 2)}</strong><small>skew ≤ {decimal(rules?.maximumBookSkewMs, 0)}ms · 滑點 {decimal(rules?.slippageBps, 0)}bps</small></article>
        <article><span>逆強趨勢</span><strong>{decimal(rules?.trendGate?.minimumMoveBps, 1)} bps · ER {decimal(rules?.trendGate?.minimumPathEr, 2)}</strong><small>elapsed ≥ {decimal(rules?.trendGate?.minimumElapsedSeconds, 0)}s · 缺資料 {rules?.trendGate?.missingDataPolicy ?? "BLOCK"}</small></article>
        <article><span>進場模式</span><strong>{rules?.entryMode ?? "NATIVE_EVENT_DRIVEN"}</strong><small>每市場最多一筆：{rules?.oneTradePerMarket === false ? "否" : "是"}</small></article>
        <article><span>版本／起點</span><strong>{experiment?.version ?? "等待後端"}</strong><small>{time(experiment?.forwardStartedAt)}</small></article>
      </div>
    </section>

    <div className="m-exit-summary-grid research-strategy-grid">
      {STRATEGIES.map(([strategy, title, subtitle, cardTone]) => {
        const stats = experiment?.strategies?.[strategy] ?? {};
        const preview = stats.currentPreview;
        const reset = resetStates[strategy];
        const pnl = stats.realizedPnl ?? 0;
        const statuses = Object.entries(stats.statusCounts ?? {}).sort((left, right) => right[1] - left[1]).slice(0, 8);
        return <article className={`m-exit-card ${cardTone}`} key={strategy} data-decision-strategy={strategy}>
          <div className="m-exit-card-head">
            <div><span className="eyebrow">{strategy} · {subtitle}</span><h3>{stats.displayName ?? title}</h3></div>
            <div className="m-exit-card-actions">
              <span className="m-exit-id">PAPER FORWARD</span>
              <button type="button" className="decision-reset" disabled={reset?.status === "loading"} onClick={() => onReset(strategy)}>{reset?.status === "loading" ? "重設中…" : "重設起點"}</button>
            </div>
          </div>
          <div className="m-exit-primary-stats">
            <div><span>已實現收益</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong></div>
            <div><span>勝率</span><strong>{percent(stats.winRate)}</strong></div>
            <div><span>交易／未結算</span><strong>{stats.trades ?? 0} / {stats.open ?? 0}</strong></div>
          </div>
          <div className={`continuous-calibration-state ${(stats.settled ?? 0) >= 30 ? "ready" : "warmup"}`}>
            <span>{stats.mode ?? subtitle}</span>
            <strong>已結算 {stats.settled ?? 0} · 勝 {stats.wins ?? 0} · 敗 {stats.losses ?? 0}</strong>
            <small>平均進場價 {decimal(stats.averageEntryPrice, 3)} · 最大回撤 {money(stats.maxDrawdown)} · 實單白名單 {stats.liveSelectable === false ? "未開啟" : "可選"}</small>
          </div>

          <div className="decision-preview">
            <strong className={tone(preview?.status)}>當輪：{preview?.status ?? "等待第一個決策事件"}</strong>
            <p>{preview?.reason ?? "尚未產生前向決策。"}</p>
            <div className="decision-preview-grid">
              <div>市場 <strong>#{preview?.marketId ?? "—"}</strong></div>
              <div>方向 <strong>{preview?.side ?? "—"}</strong></div>
              <div>選中家族 <strong>{preview?.selectedFamily ?? "—"}</strong></div>
              <div>共識權重 <strong>{percent(preview?.agreementWeight)}</strong></div>
              <div>後驗機率 <strong>{percent(preview?.estimatedProbability)}</strong></div>
              <div>有效成本 <strong>{decimal(preview?.effectiveCost, 4)}</strong></div>
              <div>淨 Edge <strong>{percent(preview?.modelEdge, 2)}</strong></div>
              <div>趨勢閘門 <strong>{preview?.trend?.status ?? "—"}</strong></div>
            </div>
          </div>

          <div className="decision-status-list">
            {statuses.length ? statuses.map(([status, count]) => <div key={status}><span className={tone(status)}>{status}</span><strong>{count}</strong></div>) : <small>尚無阻擋／放行分布。</small>}
          </div>
          {reset?.message ? <small className={reset.status === "error" ? "negative" : ""}>{reset.message}</small> : null}
        </article>;
      })}
    </div>

    <section className="shadow-tag-live-orders">
      <div><span className="eyebrow">RECENT NATIVE DECISIONS</span><h3>最近 30 筆 Rank 1／Rank 2 決策</h3></div>
      <div className="table-scroll"><table><thead><tr><th>時間／市場</th><th>策略／狀態</th><th>方向／家族</th><th>機率／成本</th><th>Edge／趨勢</th><th>原因</th></tr></thead><tbody>
        {allRecent.length === 0 ? <tr><td colSpan={6} className="empty">等待來源家族形成足夠歷史並產生新的前向決策。</td></tr> : allRecent.map((row, index) => <tr key={`${row.strategy}-${row.market_id}-${index}`}>
          <td>{time(row.updated_at)}<small>#{row.market_id ?? "—"}</small></td>
          <td>{row.strategy ?? "—"}<small className={tone(row.status)}>{row.status ?? "—"}</small></td>
          <td>{row.side ?? "—"}<small>{row.selected_family ?? "—"}</small></td>
          <td>{percent(row.estimated_probability)}<small>成本 {decimal(row.effective_cost, 4)}</small></td>
          <td>{percent(row.model_edge, 2)}<small>{row.trend_status ?? "—"}</small></td>
          <td>{row.reason ?? "—"}</td>
        </tr>)}
      </tbody></table></div>
    </section>
  </div>;
}
