"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "../page.module.css";

const CLEAR_VALUE = "I_RECONCILED_XPAIR_INCIDENT";

type SafetyState = {
  locked?: boolean;
  lockKind?: string;
  status?: string;
  reason?: string | null;
  marketKey?: string | null;
  runId?: number | null;
  btcOrderId?: string | null;
  ethOrderId?: string | null;
  btcStatus?: string | null;
  ethStatus?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  resolvedAt?: string | null;
};

type ApiState = {
  runtime?: { running?: boolean; phase?: string; armed?: boolean };
  safety?: SafetyState;
  error?: string;
};

function apiUrl(path: string) {
  const hostname = window.location.hostname || "127.0.0.1";
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8767${path}`;
}

function timeLabel(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-TW", { hour12: false });
}

export default function XPairSafetyPage() {
  const [state, setState] = useState<ApiState | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("等待安全狀態…");

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/xpair-canary"), { cache: "no-store" });
      const payload = await response.json() as ApiState;
      if (!response.ok) throw new Error(payload.error ?? "安全狀態讀取失敗");
      setState(payload);
      setMessage(payload.safety?.locked ? "安全鎖生效中" : "目前沒有安全鎖");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Canary API 無法連線");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const clearIncident = async () => {
    if (confirmation !== CLEAR_VALUE) {
      setMessage("解除確認字串不完整");
      return;
    }
    const safety = state?.safety;
    const accepted = window.confirm(
      `即將解除 XPAIR 持久化事故鎖。\n\n` +
      `狀態：${safety?.status ?? "UNKNOWN"}\n` +
      `BTC：${safety?.btcStatus ?? "UNKNOWN"}\n` +
      `ETH：${safety?.ethStatus ?? "UNKNOWN"}\n\n` +
      "只有在你已於 Binance 人工核對訂單、持倉與曝險後才能繼續。確定解除嗎？",
    );
    if (!accepted) return;
    setMessage("解除安全鎖中…");
    try {
      const response = await fetch(apiUrl("/api/xpair-canary/incident/clear"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-BTC-Lab-XPair-Live": "confirmed",
        },
        body: JSON.stringify({ confirmation, note }),
      });
      const payload = await response.json() as ApiState;
      if (!response.ok) throw new Error(payload.error ?? "安全鎖解除失敗");
      setState(payload);
      setConfirmation("");
      setNote("");
      setMessage("事故鎖已由操作者解除；背景 Quote 仍持續運行");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "安全鎖解除失敗");
    }
  };

  const safety = state?.safety;
  const tracking = safety?.lockKind === "TRACKING";
  const incident = safety?.lockKind === "INCIDENT";

  return <main className={styles.page}>
    <header className={styles.hero}>
      <div>
        <span className={styles.eyebrow}>BTC 5M LAB · DURABLE SAFETY LOCK</span>
        <h1>XPAIR 實單事故安全台</h1>
        <p>檢查兩腿訂單同步、單腿成交與持久化事故鎖。重新啟動服務不會自動解除事故。</p>
      </div>
      <div className={styles.heroActions}>
        <a href="/xpair-canary">返回 XPAIR</a>
        <span className={`${styles.statusPill} ${incident ? styles.bad : tracking ? styles.warn : styles.good}`}>
          {incident ? "INCIDENT LOCKED" : tracking ? "ORDER TRACKING" : "CLEAR"}
        </span>
      </div>
    </header>

    <section className={styles.warningBox}>
      <strong>{safety?.status ?? "等待狀態"}</strong>
      <p>{safety?.reason ?? message}</p>
      <p>TRACKING 期間不能手動解除；只有升級為 INCIDENT 且你已人工核對 Binance 訂單與持倉後，才能輸入確認字串解除。</p>
    </section>

    <section className={styles.summaryGrid}>
      <article><span>安全鎖</span><strong>{safety?.locked ? "LOCKED" : "CLEAR"}</strong><small>{safety?.lockKind ?? "—"}</small></article>
      <article><span>BTC 訂單</span><strong>{safety?.btcStatus ?? "—"}</strong><small>{safety?.btcOrderId ?? "沒有 order ID"}</small></article>
      <article><span>ETH 訂單</span><strong>{safety?.ethStatus ?? "—"}</strong><small>{safety?.ethOrderId ?? "沒有 order ID"}</small></article>
      <article><span>市場／Run</span><strong>{safety?.marketKey ?? "—"}</strong><small>Run #{safety?.runId ?? "—"}</small></article>
    </section>

    <section className={styles.console}>
      <div className={styles.sectionHead}>
        <div><span className={styles.eyebrow}>MANUAL RECONCILIATION ONLY</span><h2>事故解除</h2></div>
        <span className={styles.muted}>{message}</span>
      </div>
      <div className={styles.formGrid}>
        <label>解除確認字串
          <input
            value={confirmation}
            onChange={event => setConfirmation(event.target.value)}
            placeholder={CLEAR_VALUE}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <label>人工核對備註
          <input
            value={note}
            onChange={event => setNote(event.target.value)}
            placeholder="例如：兩筆訂單皆取消、無持倉"
          />
        </label>
        <label>建立時間
          <input value={timeLabel(safety?.createdAt)} readOnly />
        </label>
        <label>最後更新
          <input value={timeLabel(safety?.updatedAt)} readOnly />
        </label>
      </div>
      <div className={styles.actionGrid}>
        <button className={styles.secondaryButton} onClick={() => void refresh()}>
          重新讀取安全狀態
          <small>不改動任何訂單或鎖</small>
        </button>
        <button
          className={styles.liveButton}
          disabled={!incident || confirmation !== CLEAR_VALUE}
          onClick={() => void clearIncident()}
        >
          解除持久化事故鎖
          <small>只解鎖，不取消訂單、不平倉</small>
        </button>
      </div>
    </section>
  </main>;
}
