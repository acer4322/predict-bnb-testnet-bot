"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

const OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_OBSERVER_GUARD";
const POLL_MS = 2_000;

type Performance = {
  trades?: number;
  open?: number;
  settled?: number;
  wins?: number;
  losses?: number;
  realizedPnl?: number;
};

type Payload = {
  researchForward?: {
    micropriceConfirmObserverGuard?: {
      shadowPerformance?: Performance;
      blockedSourceCounterfactual?: Performance;
    };
  };
};

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function count(value: number | undefined) {
  return Number.isFinite(value) ? Number(value) : 0;
}

export default function ObserverPaperAccountingStatus() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [pass, setPass] = useState<Performance>({});
  const [blocked, setBlocked] = useState<Performance>({});

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/state"), { cache: "no-store" });
      if (!response.ok) return;
      const body = (await response.json()) as Payload;
      const experiment = body.researchForward?.micropriceConfirmObserverGuard;
      setPass(experiment?.shadowPerformance ?? {});
      setBlocked(experiment?.blockedSourceCounterfactual ?? {});
    } catch {
      // The parent Observer card already owns connection error display.
    }
  }, []);

  useEffect(() => {
    const locate = () => {
      const section = document.querySelector<HTMLElement>(
        `section[data-observer-version="${OBSERVER_VERSION}"]`,
      );
      setTarget(current => (current === section ? current : section));
    };
    locate();
    const locateTimer = window.setInterval(locate, 750);
    void refresh();
    const pollTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => {
      window.clearInterval(locateTimer);
      window.clearInterval(pollTimer);
    };
  }, [refresh]);

  if (!target) return null;

  return createPortal(
    <div
      data-observer-paper-accounting="immediate"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
        gap: 10,
        paddingTop: 2,
      }}
    >
      <article style={{ padding: 12, border: "1px solid rgba(126,227,245,.28)", borderRadius: 10 }}>
        <small>Paper 通過組 · 即時計數</small>
        <strong style={{ display: "block", marginTop: 6 }}>
          {count(pass.trades)} 筆模擬單
        </strong>
        <span style={{ display: "block", marginTop: 4 }}>
          未結算 {count(pass.open)} · 已結算 {count(pass.settled)}
        </span>
      </article>
      <article style={{ padding: 12, border: "1px solid rgba(255,189,107,.32)", borderRadius: 10 }}>
        <small>Paper 被阻擋反事實 · 即時計數</small>
        <strong style={{ display: "block", marginTop: 6 }}>
          {count(blocked.trades)} 筆來源模擬單
        </strong>
        <span style={{ display: "block", marginTop: 4 }}>
          未結算 {count(blocked.open)} · 已結算 {count(blocked.settled)}
        </span>
      </article>
      <small style={{ gridColumn: "1 / -1", color: "#8f9bab" }}>
        這裡在 Paper 模擬單建立時立即增加，不需要送出實單，也不需要等待市場結算。
      </small>
    </div>,
    target,
  );
}
