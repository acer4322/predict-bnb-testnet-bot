"use client";

import { useEffect } from "react";

type Point = {
  marketId: number;
  timestamp: string;
  upAsk: number | null;
  downAsk: number | null;
};

function finite(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function paint(canvas: HTMLCanvasElement, points: Point[]) {
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;

  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(rect.width * dpr));
  const height = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const w = rect.width;
  const h = rect.height;
  const pad = 18;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(255,255,255,.07)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const y = (h / 4) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  const drawable = points.filter(point => point.upAsk != null || point.downAsk != null);
  if (!drawable.length) {
    ctx.fillStyle = "rgba(220,230,255,.55)";
    ctx.font = "12px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("等待 Prediction UP / DOWN 可執行 Ask", w / 2, h / 2);
    canvas.dataset.realtimeRecovery = "waiting-quotes";
    return;
  }

  const draw = (key: "upAsk" | "downAsk", color: string) => {
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.4;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    let segmentOpen = false;
    let plotted = 0;
    points.forEach((point, index) => {
      const value = point[key];
      if (value == null || value < 0 || value > 1) {
        segmentOpen = false;
        return;
      }
      const x = pad + (index / Math.max(1, points.length - 1)) * (w - pad * 2);
      const y = h - pad - value * (h - pad * 2);
      if (!segmentOpen) {
        ctx.moveTo(x, y);
        segmentOpen = true;
      } else {
        ctx.lineTo(x, y);
      }
      plotted += 1;
    });
    ctx.stroke();

    if (plotted === 1) {
      const pointIndex = points.findIndex(point => point[key] != null);
      const value = points[pointIndex]?.[key];
      if (value != null) {
        const x = pad + (pointIndex / Math.max(1, points.length - 1)) * (w - pad * 2);
        const y = h - pad - value * (h - pad * 2);
        ctx.beginPath();
        ctx.fillStyle = color;
        ctx.arc(x, y, 2.8, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  };

  draw("upAsk", "#8df4c0");
  draw("downAsk", "#ffb45c");
  canvas.dataset.realtimeRecovery = "painted";
}

export default function MarketChartRecovery() {
  useEffect(() => {
    let active = true;
    let marketId: number | null = null;
    let points: Point[] = [];
    let loading = false;
    const controller = new AbortController();

    const refresh = async () => {
      if (!active || loading || document.visibilityState !== "visible") return;
      loading = true;
      try {
        const response = await fetch(apiUrl("/api/realtime"), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) return;
        const payload = await response.json() as {
          latest?: {
            market_id?: number;
            timestamp?: string;
            up_ask?: number | null;
            down_ask?: number | null;
          } | null;
        };
        if (!active || !payload.latest) return;
        const nextMarket = Number(payload.latest.market_id ?? 0);
        if (!Number.isInteger(nextMarket) || nextMarket <= 0) return;
        if (marketId !== nextMarket) {
          marketId = nextMarket;
          points = [];
        }
        const timestamp = String(payload.latest.timestamp ?? "");
        const next: Point = {
          marketId: nextMarket,
          timestamp,
          upAsk: finite(payload.latest.up_ask),
          downAsk: finite(payload.latest.down_ask),
        };
        const previous = points[points.length - 1];
        if (!previous || previous.timestamp !== next.timestamp) {
          points = [...points, next].slice(-360);
        } else {
          points = [...points.slice(0, -1), next];
        }
        const canvas = document.querySelector<HTMLCanvasElement>("canvas.market-chart");
        if (canvas) paint(canvas, points);
      } catch {
        // The main dashboard owns connection-error UI. This helper only keeps
        // the chart from silently disappearing when history/statistics lag.
      } finally {
        loading = false;
      }
    };

    const visibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const resize = () => {
      const canvas = document.querySelector<HTMLCanvasElement>("canvas.market-chart");
      if (canvas) paint(canvas, points);
    };

    const start = window.setTimeout(() => void refresh(), 250);
    const timer = window.setInterval(() => void refresh(), 1000);
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("resize", resize);
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(start);
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return null;
}
