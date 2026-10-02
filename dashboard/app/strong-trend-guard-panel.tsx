"use client";

type StrategyStats = {
  sourceStrategy?: string;
  shadowStrategy?: string;
  sourceTrades?: number;
  allowed?: number;
  blocked?: number;
  notEvaluable?: number;
  shadowOpened?: number;
  trades?: number;
  open?: number;
  settled?: number;
  wins?: number;
  losses?: number;
  winRate?: number | null;
  realizedPnl?: number;
  roi?: number | null;
  blockedSettled?: number;
  blockedPending?: number;
  blockedFlat?: number;
  blockedWins?: number;
  blockedLosses?: number;
  blockedWinRate?: number | null;
  avoidedLossUsdt?: number;
  sacrificedProfitUsdt?: number;
  netProtectionUsdt?: number;
};

type Decision = {
  id?: number;
  openedAt?: string;
  marketId?: number;
  sourceStrategy?: string;
  shadowStrategy?: string;
  side?: string;
  startMoveBps?: number | null;
  pathEfficiencyRatio?: number | null;
  elapsedSeconds?: number | null;
  decision?: string;
  reason?: string;
  sourceStatus?: string | null;
  shadowStatus?: string | null;
};

type Experiment = {
  version?: string;
  status?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  forwardOnly?: boolean;
  normalizedStakeUsdt?: number;
  rules?: Record<string, unknown>;
  decisions?: number;
  strategies?: Record<string, StrategyStats>;
  recentDecisions?: Decision[];
  runtime?: Decision | null;
};

const CARDS = [
  ["R_STRONG_TREND_GUARD_M01", "M01 逆強趨勢阻擋", "M01"],
  ["R_STRONG_TREND_GUARD_M01F", "M01F 逆強趨勢阻擋", "M01F"],
  ["R_STRONG_TREND_GUARD_M01T180", "M01T180 逆強趨勢阻擋", "M01T180"],
  ["R_STRONG_TREND_GUARD_M01O_F1", "M01O F1 逆強趨勢阻擋", "M01O_F1"],
  ["R_STRONG_TREND_GUARD_M01R", "M01R 逆強趨勢阻擋", "M01R"],
  ["R_STRONG_TREND_GUARD_MICROPRICE", "Microprice 逆強趨勢阻擋", "R_MICROPRICE"],
  ["R_STRONG_TREND_GUARD_FUTURES_LEAD", "Futures Lead 逆強趨勢阻擋", "R_FUTURES_LEAD"],
  ["R_STRONG_TREND_GUARD_CONSENSUS", "Consensus 逆強趨勢阻擋", "R_CONSENSUS"],
] as const;

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `$${value.toFixed(2)}`;
}
function pct(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}
function decimal(value: number | null | undefined, digits = 3) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}
function compact(rules: Record<string, unknown> | undefined) {
  return Object.entries(rules ?? {}).map(([key, value]) => `${key}=${String(value)}`).join(" · ");
}

export default function StrongTrendGuardPanel({ experiment }: { experiment?: Experiment | null }) {
  const runtime = experiment?.runtime;
  const strategies = experiment?.strategies ?? {};
  const recent = experiment?.recentDecisions ?? [];
  return <div role="tabpanel" id="strong-trend-guard-panel" aria-labelledby="strong-trend-guard-tab" className="m-exit-experiment research-forward-panel strong-trend-guard-panel">
    <style>{`
      .strong-trend-guard-panel .stg-runtime{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
      .strong-trend-guard-panel .stg-runtime article{padding:12px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
      .strong-trend-guard-panel .stg-runtime span,.strong-trend-guard-panel .stg-runtime small{display:block;color:#91a0bb}
      .strong-trend-guard-panel .stg-runtime strong{display:block;margin:4px 0}
      .strong-trend-guard-panel .stg-protection{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}
      .strong-trend-guard-panel .stg-protection div{padding:9px;border-radius:10px;background:rgba(126,145,178,.08)}
      .strong-trend-guard-panel .stg-protection span,.strong-trend-guard-panel .stg-protection strong{display:block}
      @media(max-width:720px){.strong-trend-guard-panel .stg-protection{grid-template-columns:1fr}}
    `}</style>
    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">STRONG OPPOSING TREND GUARD · NATIVE FORWARD PAPER</span><h3>逆強趨勢阻擋 · 八組獨立 Paper Shadow</h3></div>
      <p>每個來源策略真正開立 paper 單後才評估；只使用該筆進場時間以前的 Spot observations。原策略完全不變，八組 Shadow 固定 5 USDT、永久不轉送實單。</p>
    </section>
    <section className="m-exit-rules" aria-label="逆強趨勢阻擋規則">
      <div className="m-exit-rules-head"><div><span className="eyebrow">CAUSAL OBSERVATIONS · FAIL OPEN FOR MISSING DATA</span><h3>Guard 即時狀態</h3></div><span className="m-exit-api-state live">{experiment?.status ?? "WAITING"}</span></div>
      <div className="stg-runtime">
        <article><span>最近市場／來源</span><strong>#{runtime?.marketId ?? "—"} · {runtime?.sourceStrategy ?? "—"}</strong><small>{runtime?.side ?? "—"} · {runtime?.decision ?? "等待來源交易"}</small></article>
        <article><span>Start move</span><strong>{decimal(runtime?.startMoveBps, 3)} bps</strong><small>門檻 |move| ≥ {String(experiment?.rules?.minimumAbsoluteStartMoveBpsInclusive ?? 2.5)} bps</small></article>
        <article><span>路徑 ER／已開盤</span><strong>{decimal(runtime?.pathEfficiencyRatio, 3)} · {decimal(runtime?.elapsedSeconds, 1)}s</strong><small>ER ≥ {String(experiment?.rules?.minimumPathEfficiencyRatioInclusive ?? 0.4)} · elapsed ≥ {String(experiment?.rules?.minimumElapsedSecondsInclusive ?? 30)}s</small></article>
        <article><span>決策總數／版本</span><strong>{experiment?.decisions ?? 0}</strong><small>{experiment?.version ?? "等待後端"}</small></article>
      </div>
      <small>{compact(experiment?.rules)}</small>
    </section>
    <div className="m-exit-summary-grid research-strategy-grid">
      {CARDS.map(([id, title, source]) => {
        const stats = strategies[id] ?? {};
        return <article className="m-exit-card cyan" key={id} data-strong-trend-strategy={id}>
          <div className="m-exit-card-head"><div><span className="eyebrow">{id}</span><h3>{title}</h3></div><div className="m-exit-card-actions"><span className="m-exit-id">PAPER ONLY</span></div></div>
          <div className="m-exit-primary-stats">
            <div><span>Guard 後收益</span><strong className={(stats.realizedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(stats.realizedPnl)}</strong></div>
            <div><span>Guard 後勝率</span><strong>{pct(stats.winRate)}</strong></div>
            <div><span>ROI</span><strong>{pct(stats.roi)}</strong></div>
          </div>
          <div className={`continuous-calibration-state ${(stats.settled ?? 0) >= 30 ? "ready" : "warmup"}`}>
            <span>來源 {source} · 固定 5 USDT</span>
            <strong>來源 {stats.sourceTrades ?? 0} · 放行 {stats.allowed ?? 0} · 阻擋 {stats.blocked ?? 0}</strong>
            <small>無法評估 {stats.notEvaluable ?? 0} · Shadow {stats.shadowOpened ?? 0} · 未結算 {stats.open ?? 0}</small>
          </div>
          <div className="stg-protection">
            <div><span>避免虧損</span><strong className="positive">{money(stats.avoidedLossUsdt)}</strong></div>
            <div><span>犧牲獲利</span><strong className="negative">{money(stats.sacrificedProfitUsdt)}</strong></div>
            <div><span>淨保護</span><strong className={(stats.netProtectionUsdt ?? 0) >= 0 ? "positive" : "negative"}>{money(stats.netProtectionUsdt)}</strong></div>
          </div>
          <p>阻擋後已實現 {stats.blockedSettled ?? 0} · 等待結果 {stats.blockedPending ?? 0} · 原本勝率 {pct(stats.blockedWinRate)} · 勝 {stats.blockedWins ?? 0}／敗 {stats.blockedLosses ?? 0}／平 {stats.blockedFlat ?? 0}</p>
          <small>避免虧損、犧牲獲利與淨保護只計已被 Guard 阻擋且來源交易已有實現 PnL 的反事實結果；允許交易只影響上方 Guard 後收益。</small>
          <small>Forward-only；資料缺失採 ALLOW_NOT_EVALUABLE，不把缺資料誤算成危險趨勢。</small>
        </article>;
      })}
    </div>
    <section className="shadow-tag-live-orders">
      <div><span className="eyebrow">RECENT CAUSAL DECISIONS</span><h3>最近 40 筆 Guard 決策</h3></div>
      <div className="table-scroll"><table><thead><tr><th>時間／市場</th><th>來源／方向</th><th>Move／ER</th><th>決策</th><th>來源／Shadow 結果</th></tr></thead><tbody>
        {recent.length === 0 ? <tr><td colSpan={5} className="empty">等待八個來源策略建立新的 forward paper 交易。</td></tr> : recent.map(row => <tr key={row.id}>
          <td>{row.openedAt ?? "—"}<small>#{row.marketId ?? "—"}</small></td>
          <td>{row.sourceStrategy ?? "—"} · {row.side ?? "—"}<small>{row.shadowStrategy ?? "—"}</small></td>
          <td>{decimal(row.startMoveBps, 3)} bps<small>ER {decimal(row.pathEfficiencyRatio, 3)} · {decimal(row.elapsedSeconds, 1)}s</small></td>
          <td>{row.decision ?? "—"}<small>{row.reason ?? ""}</small></td>
          <td>{row.sourceStatus ?? "—"}<small>Shadow {row.shadowStatus ?? "—"}</small></td>
        </tr>)}
      </tbody></table></div>
    </section>
  </div>;
}
