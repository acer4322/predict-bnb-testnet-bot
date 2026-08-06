"use client";

type DecisionStrategyId = "R_DECISION_RANK1" | "R_DECISION_RANK2";

type DecisionPreview = {
  id?: number;
  controller?: DecisionStrategyId;
  market_id?: number;
  trigger_source_strategy?: string;
  status?: string;
  reason?: string;
  side?: string | null;
  selected_family?: string | null;
  selected_source_trade_id?: number | null;
  raw_top_ask?: number | null;
  entry_price?: number | null;
  estimated_probability?: number | null;
  effective_cost?: number | null;
  model_edge?: number | null;
  agreement_weight?: number | null;
  trend_status?: string | null;
  paper_trade_id?: number | null;
  created_at?: string;
};

type DecisionStrategyStats = {
  strategy?: DecisionStrategyId;
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
  resetAt?: string | null;
  statusCounts?: Record<string, number>;
  currentPreview?: DecisionPreview | null;
};

type DecisionExperiment = {
  version?: string;
  status?: string;
  error?: string | null;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  forwardOnly?: boolean;
  nativeEventDriven?: boolean;
  includedFamilies?: string[];
  familySources?: Record<string, string>;
  excludedFamilies?: string[];
  contextSamples?: Record<string, number>;
  rank2Warmup?: string;
  rules?: {
    entryMode?: string;
    stakeUsdt?: number;
    slippageBps?: number;
    maximumBookAgeMs?: number;
    maximumBookSkewMs?: number;
    maximumSpread?: number;
    oneTradePerMarket?: boolean;
    rank1?: Record<string, unknown>;
    rank2?: {
      minimumSameContextHistory?: number;
      historyScope?: string;
    };
    trendGate?: {
      minimumElapsedSeconds?: number;
      minimumMoveBps?: number;
      minimumPathEr?: number;
      minimumSamples?: number;
      maximumSpotAgeMs?: number;
      missingDataPolicy?: string;
    };
  };
  strategies?: Partial<Record<DecisionStrategyId, DecisionStrategyStats>>;
  recentDecisions?: DecisionPreview[];
};

const CONTROLLERS: Array<{
  id: DecisionStrategyId;
  title: string;
  subtitle: string;
  tone: "cyan" | "purple";
}> = [
  {
    id: "R_DECISION_RANK1",
    title: "Rank 1 · 效用加權多數決",
    subtitle: "最近 60 筆 · 半衰期 20 · 67% 共識",
    tone: "cyan",
  },
  {
    id: "R_DECISION_RANK2",
    title: "Rank 2 · 近期情境冠軍",
    subtitle: "同情境最近 30 筆 · 50% 家族上限",
    tone: "purple",
  },
];

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function number(value: unknown, digits = 2) {
  return finite(value) ? value.toFixed(digits) : "—";
}

function percent(value: unknown, digits = 1) {
  return finite(value) ? `${(value * 100).toFixed(digits)}%` : "—";
}

function money(value: unknown) {
  if (!finite(value)) return "—";
  return `${value >= 0 ? "+" : "-"}$${Math.abs(value).toFixed(2)}`;
}

function timestamp(value: unknown) {
  if (typeof value !== "string" || !value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("zh-TW", { hour12: false });
}

function statusTone(value: unknown) {
  const status = String(value ?? "").toUpperCase();
  if (
    status.includes("OPEN") ||
    status.includes("PASS") ||
    status.includes("COLLECT")
  ) {
    return "positive";
  }
  if (
    status.includes("BLOCK") ||
    status.includes("ERROR") ||
    status.includes("UNAVAILABLE")
  ) {
    return "negative";
  }
  return "";
}

function list(values: string[] | undefined, fallback: string) {
  return values?.length ? values.join(" · ") : fallback;
}

export default function DecisionStrategyPanel({
  experiment,
}: {
  experiment?: DecisionExperiment | null;
}) {
  const rules = experiment?.rules;
  const recent = experiment?.recentDecisions ?? [];
  const contextSamples = experiment?.contextSamples ?? {};

  return (
    <div
      role="tabpanel"
      id="decision-strategy-panel"
      aria-labelledby="decision-strategy-tab"
      className="m-exit-experiment research-forward-panel decision-strategy-panel"
    >
      <style>{`
        .decision-strategy-panel .decision-common-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin:14px 0}
        .decision-strategy-panel .decision-common-grid article{padding:12px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
        .decision-strategy-panel .decision-common-grid span,.decision-strategy-panel .decision-common-grid small{display:block;color:#91a0bb}
        .decision-strategy-panel .decision-common-grid strong{display:block;margin:4px 0;overflow-wrap:anywhere}
        .decision-strategy-panel .decision-preview{margin-top:12px;padding:12px;border-radius:12px;background:rgba(126,145,178,.08)}
        .decision-strategy-panel .decision-preview p{margin:6px 0;color:#aebbd1}
        .decision-strategy-panel .decision-preview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(125px,1fr));gap:8px;margin-top:10px}
        .decision-strategy-panel .decision-preview-grid div{padding:8px;border-radius:9px;background:rgba(7,12,21,.65)}
        .decision-strategy-panel .decision-status-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:7px;margin-top:10px}
        .decision-strategy-panel .decision-status-list div{display:flex;justify-content:space-between;gap:8px;padding:8px 10px;border-radius:10px;background:rgba(7,12,21,.65)}
        .decision-strategy-panel .decision-error{margin:10px 0;padding:10px 12px;border:1px solid rgba(255,99,99,.35);border-radius:10px;color:#ffb2b2;background:rgba(116,24,24,.18)}
      `}</style>

      <section className="strategy-family-intro m-exit-intro">
        <div>
          <span className="eyebrow">DECISION CONTROLLERS · NATIVE FORWARD PAPER</span>
          <h3>決策策略測試 · Rank 1／Rank 2</h3>
        </div>
        <p>
          只由 Futures Lead、Calibrated Value、Consensus 的新來源 Paper
          事件觸發；M01、原始 Microprice、OFI 不進歷史、投票或選擇。
        </p>
      </section>

      {experiment?.error ? (
        <div className="decision-error">
          新決策摘要暫時不可用；既有 Dashboard 與歷史統計不受影響。{` ${experiment.error}`}
        </div>
      ) : null}

      <section className="m-exit-rules" aria-label="決策策略共同規則">
        <div className="m-exit-rules-head">
          <div>
            <span className="eyebrow">ISOLATED LEDGER · EXISTING SETTLEMENT PATH</span>
            <h3>共同資料、安全與暖機狀態</h3>
          </div>
          <span className={`m-exit-api-state ${experiment?.status === "COLLECTING" ? "live" : ""}`}>
            {experiment?.status ?? "WAITING API"}
          </span>
        </div>
        <div className="decision-common-grid">
          <article>
            <span>納入家族</span>
            <strong>{list(experiment?.includedFamilies, "FUTURES_LEAD · CALIBRATED_VALUE · CONSENSUS")}</strong>
            <small>每個來源家族最多一票</small>
          </article>
          <article>
            <span>正式排除</span>
            <strong>{list(experiment?.excludedFamilies, "M01 · MICROPRICE · OFI")}</strong>
            <small>不進歷史、權重或 Champion</small>
          </article>
          <article>
            <span>原生進場</span>
            <strong>{rules?.entryMode ?? "NATIVE_EVENT_DRIVEN"}</strong>
            <small>每市場最多一筆：{rules?.oneTradePerMarket === false ? "否" : "是"}</small>
          </article>
          <article>
            <span>成交品質</span>
            <strong>
              age ≤ {number(rules?.maximumBookAgeMs, 0)}ms · spread ≤ {number(rules?.maximumSpread, 2)}
            </strong>
            <small>
              skew ≤ {number(rules?.maximumBookSkewMs, 0)}ms · slippage {number(rules?.slippageBps, 0)}bps
            </small>
          </article>
          <article>
            <span>逆強趨勢</span>
            <strong>
              {number(rules?.trendGate?.minimumMoveBps, 1)} bps · ER {number(rules?.trendGate?.minimumPathEr, 2)}
            </strong>
            <small>
              elapsed ≥ {number(rules?.trendGate?.minimumElapsedSeconds, 0)}s · 缺資料 {rules?.trendGate?.missingDataPolicy ?? "BLOCK"}
            </small>
          </article>
          <article>
            <span>Rank 2 情境樣本</span>
            <strong>
              FL {contextSamples.R_FUTURES_LEAD ?? 0} · CV {contextSamples.R_CALIBRATED_VALUE ?? 0} · C {contextSamples.R_CONSENSUS ?? 0}
            </strong>
            <small>
              同情境至少 {rules?.rank2?.minimumSameContextHistory ?? 8} 筆；僅部署後前向累積
            </small>
          </article>
        </div>
        <small>{experiment?.rank2Warmup ?? "等待來源策略產生新的前向情境樣本。"}</small>
      </section>

      <div className="m-exit-summary-grid research-strategy-grid">
        {CONTROLLERS.map(({ id, title, subtitle, tone }) => {
          const stats = experiment?.strategies?.[id] ?? {};
          const preview = stats.currentPreview;
          const pnl = stats.realizedPnl ?? 0;
          const statuses = Object.entries(stats.statusCounts ?? {})
            .sort((left, right) => right[1] - left[1])
            .slice(0, 8);
          return (
            <article className={`m-exit-card ${tone}`} key={id} data-decision-strategy={id}>
              <div className="m-exit-card-head">
                <div>
                  <span className="eyebrow">{id} · {subtitle}</span>
                  <h3>{title}</h3>
                </div>
                <span className="m-exit-id">PAPER + LIVE SELECTABLE</span>
              </div>
              <div className="m-exit-primary-stats">
                <div>
                  <span>已實現收益</span>
                  <strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong>
                </div>
                <div>
                  <span>勝率</span>
                  <strong>{percent(stats.winRate)}</strong>
                </div>
                <div>
                  <span>交易／未結算</span>
                  <strong>{stats.trades ?? 0} / {stats.open ?? 0}</strong>
                </div>
              </div>
              <div className={`continuous-calibration-state ${(stats.settled ?? 0) >= 30 ? "ready" : "warmup"}`}>
                <span>{stats.mode ?? subtitle}</span>
                <strong>
                  已結算 {stats.settled ?? 0} · 勝 {stats.wins ?? 0} · 敗 {stats.losses ?? 0}
                </strong>
                <small>
                  平均進場 {number(stats.averageEntryPrice, 3)} · 最大回撤 {money(stats.maxDrawdown)}
                </small>
              </div>

              <div className="decision-preview">
                <strong className={statusTone(preview?.status)}>
                  當輪：{preview?.status ?? "等待第一個來源事件"}
                </strong>
                <p>{preview?.reason ?? "尚未產生前向決策。"}</p>
                <div className="decision-preview-grid">
                  <div>市場 <strong>#{preview?.market_id ?? "—"}</strong></div>
                  <div>方向 <strong>{preview?.side ?? "—"}</strong></div>
                  <div>選中家族 <strong>{preview?.selected_family ?? "—"}</strong></div>
                  <div>共識權重 <strong>{percent(preview?.agreement_weight)}</strong></div>
                  <div>後驗機率 <strong>{percent(preview?.estimated_probability)}</strong></div>
                  <div>有效成本 <strong>{number(preview?.effective_cost, 4)}</strong></div>
                  <div>淨 Edge <strong>{percent(preview?.model_edge, 2)}</strong></div>
                  <div>趨勢閘門 <strong>{preview?.trend_status ?? "—"}</strong></div>
                </div>
              </div>

              <div className="decision-status-list">
                {statuses.length ? (
                  statuses.map(([status, count]) => (
                    <div key={status}>
                      <span className={statusTone(status)}>{status}</span>
                      <strong>{count}</strong>
                    </div>
                  ))
                ) : (
                  <small>尚無阻擋／放行分布。</small>
                )}
              </div>
            </article>
          );
        })}
      </div>

      <section className="shadow-tag-live-orders">
        <div>
          <span className="eyebrow">RECENT DURABLE DECISIONS</span>
          <h3>最近 {recent.length} 筆 Rank 1／Rank 2 決策</h3>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>時間／市場</th>
                <th>控制器／狀態</th>
                <th>方向／家族</th>
                <th>機率／成本</th>
                <th>Edge／趨勢</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 ? (
                <tr>
                  <td colSpan={6} className="empty">
                    等待三個來源家族產生新的前向 Paper 事件。
                  </td>
                </tr>
              ) : (
                recent.map((row, index) => (
                  <tr key={`${row.controller}-${row.id ?? index}`}>
                    <td>
                      {timestamp(row.created_at)}
                      <small>#{row.market_id ?? "—"}</small>
                    </td>
                    <td>
                      {row.controller ?? "—"}
                      <small className={statusTone(row.status)}>{row.status ?? "—"}</small>
                    </td>
                    <td>
                      {row.side ?? "—"}
                      <small>{row.selected_family ?? "—"}</small>
                    </td>
                    <td>
                      {percent(row.estimated_probability)}
                      <small>成本 {number(row.effective_cost, 4)}</small>
                    </td>
                    <td>
                      {percent(row.model_edge, 2)}
                      <small>{row.trend_status ?? "—"}</small>
                    </td>
                    <td>{row.reason ?? "—"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
