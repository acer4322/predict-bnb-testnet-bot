"use client";

const STRATEGIES = [
  ["R_CALIBRATED_VALUE_IMMEDIATE_CONTROL", "Calibrated Value 即時進場對照", "INITIAL EVENT CONTROL", "第一個直接雙 token REST 事件若淨 edge ≥0.015，就使用當時直接 Ask 開立 5 USDT paper 單，不等待價格確認。", "purple"],
  ["R_CALIBRATED_VALUE_CONFIRM_V2", "Calibrated Value 多事件確認順勢 V2", "FOLLOW CONFIRMED REPRICING", "至少第二個不同事件、持續 ≥150ms；方向不變、midpoint 同向、最終 edge 合格才沿原方向進場。", "cyan"],
  ["R_CALIBRATED_VALUE_CONFIRM_V2_REVERSE", "Calibrated Value 多事件確認反向 V2", "REVERSE CONFIRMED REPRICING", "與順勢 V2 使用相同市場與確認事件，但買入相反方向。", "coral"],
  ["R_CALIBRATED_VALUE_CONFIRM_RANGE12", "Calibrated Value 確認 · Range 1–2", "CONFIRM V2 + RANGE SCORE 1–2", "沿用 Confirm V2，另外要求當輪 Range score 為 1 或 2，且有效穿越不超過 2 次。", "green"],
  ["R_CALIBRATED_VALUE_LOWTAIL_CONFIRM", "Calibrated Value 低價肥尾確認", "STRICT LOW-PRICE TAIL", "確認後 entry 介於 0.10～0.221，使用更嚴格事件數、延遲、book freshness 與 edge retention。", "amber"],
] as const;

function money(value: unknown) { const n = Number(value); return Number.isFinite(n) ? `$${n.toFixed(2)}` : "—"; }
function ratio(value: unknown) { const n = Number(value); return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—"; }
function decimal(value: unknown, digits = 3) { const n = Number(value); return Number.isFinite(n) ? n.toFixed(digits) : "—"; }
function compact(value: unknown) { return value && typeof value === "object" ? Object.entries(value as Record<string, unknown>).slice(0, 14).map(([key, item]) => `${key}=${String(item)}`).join(" · ") : ""; }
function rowText(row: Record<string, unknown> | undefined, key: string) { return row?.[key] == null ? "—" : String(row[key]); }

export default function CalibratedConfirmationLab({ payload }: { payload: any }) {
  const experiment = payload?.researchForward?.calibratedValueConfirmationExperiment;
  const runtime = experiment?.runtime;
  const decision = runtime?.lastDecision;
  const recent = experiment?.recentCohorts ?? [];
  return <div role="tabpanel" id="calibrated-value-confirmation-panel" aria-labelledby="calibrated-value-confirmation-tab" className="m-exit-experiment research-forward-panel calibrated-value-confirmation-lab">
    <style>{`
      .calibrated-value-confirmation-lab .cv-confirm-runtime{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
      .calibrated-value-confirmation-lab .cv-confirm-runtime article{padding:12px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
      .calibrated-value-confirmation-lab .cv-confirm-runtime span,.calibrated-value-confirmation-lab .cv-confirm-runtime small{display:block;color:#91a0bb}
      .calibrated-value-confirmation-lab .cv-confirm-runtime strong{display:block;margin:4px 0}
      .calibrated-value-confirmation-lab .cv-confirm-reason{margin:12px 0 18px;padding:12px 14px;border-left:3px solid #7ee3f5;background:rgba(74,196,219,.08)}
    `}</style>
    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">CALIBRATED VALUE CONFIRMATION LAB · NATIVE FORWARD PAPER</span><h3>Calibrated Value 五組前向確認測試</h3></div>
      <p>已改為主頁原生 React 頁籤：不再使用額外 DOM 注入或額外輪詢。五組前向帳本與原有後端規則保持不變。</p>
    </section>
    <section className="m-exit-rules">
      <div className="m-exit-rules-head"><div><span className="eyebrow">DIRECT DUAL-TOKEN REST · FIXED COHORT</span><h3>確認引擎狀態</h3></div><span className="m-exit-api-state live">{decision?.status ?? runtime?.status ?? "WAITING"}</span></div>
      <div className="cv-confirm-runtime">
        <article><span>目前市場</span><strong>#{runtime?.currentMarketId ?? "—"}</strong><small>來源 {experiment?.sourceStrategy ?? "R_CALIBRATED_VALUE"}</small></article>
        <article><span>初始方向／edge</span><strong>{runtime?.currentInitialSide ?? "—"} · {decimal(runtime?.currentInitialEdge, 4)}</strong><small>midpoint {decimal(runtime?.currentInitialMidpoint, 4)}</small></article>
        <article><span>確認事件</span><strong>{runtime?.currentConfirmations ?? 0} / {String(runtime?.rules?.minimumConfirmations ?? 2)}</strong><small>至少 {String(runtime?.rules?.minimumConfirmationMs ?? 150)}ms</small></article>
        <article><span>即時／確認市場</span><strong>{experiment?.immediateMarkets ?? 0} / {experiment?.pairedMarkets ?? 0}</strong><small>完整三組 {experiment?.completeCohorts ?? 0} · Range12 {experiment?.range12Markets ?? 0} · Lowtail {experiment?.lowtailMarkets ?? 0}</small></article>
      </div>
      <p className="cv-confirm-reason"><strong>{decision?.reason ?? "等待合格的直接 REST book 事件"}</strong><br />市場 #{decision?.marketId ?? runtime?.currentMarketId ?? "—"} · 初始 edge {decimal(decision?.initialEdge, 4)} · 最終 edge {decimal(decision?.finalEdge, 4)} · 保留 {ratio(decision?.retainedEdgeRatio)} · midpoint Δ {decimal(decision?.chosenMidpointDelta, 4)}</p>
      <small>{compact(runtime?.rules)}</small>
    </section>
    <div className="m-exit-summary-grid research-strategy-grid">
      {STRATEGIES.map(([id, title, kicker, rule, tone]) => {
        const summary = payload?.summaries?.[id] ?? {};
        const stats = experiment?.strategies?.[id] ?? {};
        const wins = summary.wins ?? stats.wins ?? 0;
        const losses = summary.losses ?? stats.losses ?? 0;
        const settled = wins + losses;
        const pnl = summary.realized_pnl ?? stats.realizedPnl ?? 0;
        const validation = payload?.researchForward?.strategies?.[id]?.chronologicalValidation;
        const runtimeDecision = experiment?.filterRuntime?.lastDecisions?.[id];
        return <article className={`m-exit-card ${tone}`} key={id} data-calibrated-strategy={id}>
          <div className="m-exit-card-head"><div><span className="eyebrow">{id} · {kicker}</span><h3>{title}</h3></div><div className="m-exit-card-actions"><span className="m-exit-id">PAPER ONLY</span></div></div>
          <div className="m-exit-primary-stats"><div><span>已實現收益</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong></div><div><span>勝率</span><strong>{settled ? ratio(wins / settled) : "—"}</strong></div><div><span>交易／未結算</span><strong>{summary.trades ?? stats.trades ?? 0} / {summary.open ?? stats.open ?? 0}</strong></div></div>
          <div className={`continuous-calibration-state ${settled >= 30 ? "ready" : "warmup"}`}><span>{stats.mode ?? kicker}</span><strong>已結算 {settled} / 30 · 勝 {wins} · 敗 {losses}</strong><small>平均進場價 {decimal(stats.averageEntryPrice)}</small></div>
          <p>{rule}</p><small>狀態 {validation?.status ?? "COLLECTING"} · Forward-only · 每筆 5 USDT · 不回填、不轉送實單。</small>
          {runtimeDecision && <small>Runtime：{String(runtimeDecision.status ?? "WAITING")} · {String(runtimeDecision.reason ?? "")}</small>}
        </article>;
      })}
    </div>
    <section className="shadow-tag-live-orders"><div><span className="eyebrow">MATCHED COHORT AUDIT</span><h3>最近完整確認 cohort</h3></div><div className="table-scroll"><table><thead><tr><th>市場</th><th>即時</th><th>順勢</th><th>反向</th><th>Range／Lowtail</th></tr></thead><tbody>
      {recent.length === 0 ? <tr><td colSpan={5} className="empty">等待新的前向確認事件。</td></tr> : recent.map((item: any) => <tr key={item.marketId}><td>#{item.marketId ?? "—"}</td><td>{rowText(item.immediate, "side")} · {rowText(item.immediate, "status")}</td><td>{rowText(item.confirm, "side")} · {rowText(item.confirm, "status")}</td><td>{rowText(item.reverse, "side")} · {rowText(item.reverse, "status")}</td><td>{rowText(item.range12, "status")} / {rowText(item.lowtail, "status")}</td></tr>)}
    </tbody></table></div></section>
  </div>;
}
