"use client";

import { useEffect, useState } from "react";
import styles from "./book-monitor.module.css";

type Trial = {
  variant?: string;
  entryStatus?: string;
  eligible?: boolean;
  rejectionReason?: string | null;
  costPerShare?: number | null;
  filledShares?: number | null;
  btcVwap?: number | null;
  ethVwap?: number | null;
  btcBookAgeMs?: number | null;
  ethBookAgeMs?: number | null;
  crossBookSkewMs?: number | null;
};

type BookAnalysis = {
  marketKey?: string;
  updatedAt?: string;
  secondsLeft?: number;
  insideEntryWindow?: boolean;
  selection?: string;
  selectedVariant?: string | null;
  selectedCostPerShare?: number | null;
  selectedFilledShares?: number | null;
  trials?: Trial[];
};

type ApiState = {
  defaults?: {
    entrySecondsLeft?: number;
    entryWindowSeconds?: number;
    bookMonitorIntervalSeconds?: number;
  };
  runtime?: {
    running?: boolean;
    bookAnalysis?: BookAnalysis | null;
  };
  policy?: {
    liveAllowedSelections?: string[];
    firstEligibleLiveExecution?: boolean;
    executionStartGuardSeconds?: number;
    executionMinimumSecondsLeft?: number;
  };
};

function apiUrl() {
  const hostname = window.location.hostname || "127.0.0.1";
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8767/api/xpair-canary`;
}

function numberLabel(value: number | null | undefined, digits = 4) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function timeLabel(value: string | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("zh-TW", { hour12: false });
}

export default function BookMonitorPanel() {
  const [state, setState] = useState<ApiState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetch(apiUrl(), { cache: "no-store" });
        const payload = await response.json() as ApiState & { error?: string };
        if (!response.ok) throw new Error(payload.error ?? "XPAIR API 回應失敗");
        if (active) {
          setState(payload);
          setError(null);
        }
      } catch (caught) {
        if (active) {
          setError(caught instanceof Error ? caught.message : "XPAIR API 無法連線");
        }
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const analysis = state?.runtime?.bookAnalysis;
  const trials = analysis?.trials ?? [];
  const interval = state?.defaults?.bookMonitorIntervalSeconds ?? 1;
  const liveDirections = state?.policy?.liveAllowedSelections?.join(", ")
    ?? "BTC_DOWN_ETH_UP";
  const startGuard = state?.policy?.executionStartGuardSeconds ?? 5;
  const minimumLeft = state?.policy?.executionMinimumSecondsLeft ?? 20;
  const firstEligible = state?.policy?.firstEligibleLiveExecution === true;

  return <aside className={styles.panel}>
    <div className={styles.header}>
      <div>
        <span className={styles.eyebrow}>PAIR_ARB-STYLE CONTINUOUS BOOK MONITOR</span>
        <h2>即時簿面判定</h2>
      </div>
      <span className={`${styles.badge} ${analysis?.insideEntryWindow ? styles.window : styles.monitor}`}>
        {error
          ? "API OFFLINE"
          : analysis?.insideEntryWindow
            ? "LIVE ELIGIBLE ZONE"
            : "BOOK MONITORING"}
      </span>
    </div>

    {error ? <p className={styles.error}>{error}</p> : null}

    <div className={styles.summary}>
      <div><span>普通簿刷新</span><strong>每 {interval.toFixed(1)} 秒</strong></div>
      <div><span>剩餘時間</span><strong>{numberLabel(analysis?.secondsLeft, 1)} 秒</strong></div>
      <div><span>目前選定方向</span><strong>{analysis?.selection ?? "—"}</strong></div>
      <div><span>簿面合格方向</span><strong>{analysis?.selectedVariant ?? "目前沒有"}</strong></div>
      <div><span>預估成本/share</span><strong>{numberLabel(analysis?.selectedCostPerShare, 6)}</strong></div>
      <div><span>最新更新</span><strong>{timeLabel(analysis?.updatedAt)}</strong></div>
    </div>

    <div className={styles.tableWrap}>
      <table>
        <thead>
          <tr>
            <th>組合</th>
            <th>判定</th>
            <th>成本/share</th>
            <th>BTC／ETH VWAP</th>
            <th>簿齡／跨簿偏差</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          {trials.length === 0
            ? <tr><td colSpan={6} className={styles.empty}>等待對齊的 BTC／ETH 市場與四本 outcome book</td></tr>
            : trials.map(trial => <tr key={trial.variant ?? "unknown"}>
                <td>{trial.variant ?? "—"}</td>
                <td><span className={`${styles.status} ${trial.eligible ? styles.good : styles.blocked}`}>
                  {trial.entryStatus ?? "UNKNOWN"}
                </span></td>
                <td>{numberLabel(trial.costPerShare, 6)}</td>
                <td>{numberLabel(trial.btcVwap, 4)}／{numberLabel(trial.ethVwap, 4)}</td>
                <td>
                  BTC {numberLabel(trial.btcBookAgeMs, 0)}ms · ETH {numberLabel(trial.ethBookAgeMs, 0)}ms<br />
                  skew {numberLabel(trial.crossBookSkewMs, 0)}ms
                </td>
                <td>{trial.rejectionReason ?? (trial.eligible ? "通過普通簿面 gate" : "—")}</td>
              </tr>)}
        </tbody>
      </table>
    </div>

    <p className={styles.note}>
      {firstEligible
        ? `實單已改為 FIRST_ELIGIBLE：市場開始 ${startGuard.toFixed(0)} 秒後至剩餘 ${minimumLeft.toFixed(0)} 秒以前，只要目前實單方向符合就立即申請 signed Quote；已武裝且 Quote 通過便立即送單，不再等待目標剩餘秒數。`
        : "普通訂單簿會整場常駐判定。"}
      實單方向 gate 目前只允許 {liveDirections}，不會因另一方向當輪較便宜而自動切換。
    </p>
  </aside>;
}
