"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_GUARD";
const REFRESH_MS = 2_000;
const REQUEST_TIMEOUT_MS = 4_000;

type Performance = {
  trades?: number;
  open?: number;
  settled?: number;
  wins?: number;
  losses?: number;
  winRatePct?: number | null;
  realizedPnl?: number;
};

type Decision = {
  marketId?: number;
  openedAt?: string | null;
  side?: string | null;
  entryPrice?: number | null;
  status?: string | null;
  pnl?: number | null;
  decision?: {
    status?: string;
    reason?: string;
    rawTopAsk?: number | null;
    historicalState?: string;
    historicalRangeScore?: number;
    historicalTrendScore?: number;
  };
};

type StableConsensusState = {
  version?: string;
  strategy?: string;
  observerVersion?: string;
  sourceStrategy?: string;
  evaluations?: number;
  allowedEvaluations?: number;
  blockedEvaluations?: number;
  transitionBlockedEvaluations?: number;
  priceBandBlockedEvaluations?: number;
  unavailableEvaluations?: number;
  shadowPerformance?: Performance;
  blockedSourceCounterfactual?: Performance;
  sourcePerformance?: Performance;
  recentDecisions?: Decision[];
};

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function money(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  return `${number >= 0 ? "+" : ""}$${number.toFixed(2)}`;
}

function percentage(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `${Number(value).toFixed(1)}%`;
}

function price(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return Number(value).toFixed(4);
}

function time(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("zh-TW", { hour12: false });
}

function performanceText(performance?: Performance) {
  return `${performance?.wins ?? 0} 勝 / ${performance?.losses ?? 0} 敗`;
}

export default function StableConsensusObserverSimulation() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [data, setData] = useState<StableConsensusState | null>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    const locate = () => {
      const next = document.querySelector<HTMLElement>("#lead-observer-panel");
      setTarget(current => current === next ? current : next);
    };
    locate();
    const observer = new MutationObserver(locate);
    observer.observe(document.body, { subtree: true, childList: true });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    let loading = false;

    const load = async () => {
      if (loading || document.visibilityState === "hidden") return;
      loading = true;
      const controller = new AbortController();
      const timeout = window.setTimeout(
        () => controller.abort(),
        REQUEST_TIMEOUT_MS,
      );
      try {
        const response = await fetch(apiUrl("/api/state"), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`API ${response.status}`);
        const body = await response.json();
        const next = body?.researchForward
          ?.micropriceConfirmStableConsensusGuard as StableConsensusState | undefined;
        if (!active) return;
        setData(next ?? null);
        setError(next ? "" : "後端尚未載入 Stable Consensus 模擬統計");
      } catch (caught) {
        if (!active) return;
        setError(caught instanceof Error ? caught.message : "讀取失敗");
      } finally {
        window.clearTimeout(timeout);
        loading = false;
      }
    };

    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const recent = useMemo(
    () => Array.isArray(data?.recentDecisions)
      ? data.recentDecisions.slice(0, 8)
      : [],
    [data],
  );

  if (!target) return null;

  const shadow = data?.shadowPerformance;
  const blocked = data?.blockedSourceCounterfactual;
  const source = data?.sourcePerformance;

  return createPortal(
    <section
      className="m-exit-rules"
      aria-label="Stable Consensus 模擬觀測"
      data-stable-consensus-simulation="true"
    >
      <div className="m-exit-rules-head">
        <div>
          <span className="eyebrow">STABLE CONSENSUS · PAPER FORWARD</span>
          <h3>Microprice Confirm · 穩定共識模擬</h3>
        </div>
        <span className={`m-exit-api-state ${data ? "live" : ""}`}>
          {data ? "PAPER 觀測中" : "等待後端"}
        </span>
      </div>

      <p>
        {STRATEGY}：先通過歷史 F1 過渡防護，再要求訊號側原始 Ask 位於
        0.60–0.90。這張卡只統計模擬帳本；獨立實單策略必須在正式實單頁明確選用。
      </p>

      <div className="m-exit-rule-grid">
        <article>
          <span>來源評估</span>
          <strong>{data?.evaluations ?? 0}</strong>
          <small>來源 {source?.trades ?? 0} 筆 · 未結算 {source?.open ?? 0}</small>
        </article>
        <article>
          <span>模擬放行</span>
          <strong>{data?.allowedEvaluations ?? 0}</strong>
          <small>已建立 Stable Consensus Paper 單</small>
        </article>
        <article>
          <span>模擬阻擋</span>
          <strong>{data?.blockedEvaluations ?? 0}</strong>
          <small>
            價格帶 {data?.priceBandBlockedEvaluations ?? 0} · 過渡區 {data?.transitionBlockedEvaluations ?? 0}
          </small>
        </article>
        <article>
          <span>資料不可用</span>
          <strong>{data?.unavailableEvaluations ?? 0}</strong>
          <small>缺 gate、錯市場或欄位不足時 fail closed</small>
        </article>
      </div>

      <div className="m-exit-summary-grid">
        <article className="m-exit-card green">
          <div className="m-exit-card-head">
            <div>
              <span className="eyebrow">ALLOWED PAPER COHORT</span>
              <h3>通過組</h3>
            </div>
            <span className="m-exit-id">PAPER</span>
          </div>
          <div className="m-exit-primary-stats">
            <div><span>已實現收益</span><strong className={(shadow?.realizedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(shadow?.realizedPnl)}</strong></div>
            <div><span>勝率</span><strong>{percentage(shadow?.winRatePct)}</strong></div>
            <div><span>交易／未結算</span><strong>{shadow?.trades ?? 0} / {shadow?.open ?? 0}</strong></div>
          </div>
          <p>{performanceText(shadow)} · 已結算 {shadow?.settled ?? 0}</p>
        </article>

        <article className="m-exit-card amber">
          <div className="m-exit-card-head">
            <div>
              <span className="eyebrow">BLOCKED COUNTERFACTUAL</span>
              <h3>被阻擋來源組</h3>
            </div>
            <span className="m-exit-id">NO ORDER</span>
          </div>
          <div className="m-exit-primary-stats">
            <div><span>來源原本 PnL</span><strong className={(blocked?.realizedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(blocked?.realizedPnl)}</strong></div>
            <div><span>來源勝率</span><strong>{percentage(blocked?.winRatePct)}</strong></div>
            <div><span>阻擋／未結算</span><strong>{blocked?.trades ?? 0} / {blocked?.open ?? 0}</strong></div>
          </div>
          <p>阻擋組不建立第二張 Paper 單；此處顯示來源策略事後結果。</p>
        </article>
      </div>

      <div className="table-scroll">
        <table className="m-exit-table">
          <thead>
            <tr>
              <th>時間</th>
              <th>市場</th>
              <th>方向／進場</th>
              <th>判定</th>
              <th>Ask</th>
              <th>歷史狀態</th>
              <th>來源結果</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {recent.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty">
                  {error || "等待新的 R_MICROPRICE_CONFIRM Paper 訊號。"}
                </td>
              </tr>
            ) : recent.map((item, index) => {
              const decision = item.decision ?? {};
              const allowed = decision.status === "ALLOW";
              return (
                <tr key={`${item.marketId ?? "market"}-${index}`}>
                  <td>{time(item.openedAt)}</td>
                  <td>#{item.marketId ?? "—"}</td>
                  <td>{item.side ?? "—"} · {price(item.entryPrice)}</td>
                  <td className={allowed ? "positive" : "negative"}>{decision.status ?? "UNAVAILABLE"}</td>
                  <td>{price(decision.rawTopAsk)}</td>
                  <td>{decision.historicalState ?? "—"}<small>R {decision.historicalRangeScore ?? "—"} / T {decision.historicalTrendScore ?? "—"}</small></td>
                  <td className={(item.pnl ?? 0) >= 0 ? "positive" : "negative"}>{item.status ?? "—"} · {item.pnl == null ? "—" : money(item.pnl)}</td>
                  <td>{decision.reason ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {error && data && <p className="stream-error">最近更新：{error}</p>}
    </section>,
    target,
  );
}
