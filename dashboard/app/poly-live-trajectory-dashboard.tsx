"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

type Point = {
  marketKey: string;
  atMs: number;
  binanceUp: number | null;
  binanceDown: number | null;
  polyUp: number | null;
  polyDown: number | null;
};

type CrossState = {
  polymarket?: {
    status?: string;
    ageMs?: number | null;
    receivedTimestampMs?: number | null;
    error?: string | null;
    marketDiscovery?: { status?: string; slug?: string; detail?: string; method?: string } | null;
    market?: { slug?: string | null } | null;
    continuity?: {
      healthy?: boolean;
      gapActive?: boolean;
      targetMarketSlug?: string | null;
      marketSlug?: string | null;
      gapReason?: string | null;
    } | null;
    up?: { bestAsk?: number | null };
    down?: { bestAsk?: number | null };
  };
};

type CrossPayload = { ok?: boolean; state?: CrossState; error?: string };
type BinancePayload = {
  latest?: {
    market_id?: number | null;
    up_ask?: number | null;
    down_ask?: number | null;
  } | null;
};

type PolyGapLiveState = {
  version?: string;
  status?: string;
  settings?: { entryDelaySeconds?: number | null };
  entryDelay?: {
    enabled?: boolean;
    configuredSeconds?: number | null;
    minimumAllowedSeconds?: number | null;
    maximumAllowedSeconds?: number | null;
    elapsedMs?: number | null;
    remainingMs?: number | null;
    ready?: boolean;
  };
};

type PolyGapLivePayload = {
  ok?: boolean;
  state?: PolyGapLiveState;
  error?: string;
};

const LIMIT = 90;
const SAMPLE_MS = 1000;
const MAX_RENDERABLE_POLY_AGE_MS = 2000;
const STORAGE_PREFIX = "btc5m-live-poly-trajectory:";
const ENTRY_DELAY_MIN_SECONDS = 0;
const ENTRY_DELAY_MAX_SECONDS = 240;

function finite(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function binanceRealtimeUrl() {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766/api/realtime`;
}

function loadStored(key: string): Point[] {
  try {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${key}`);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(row => row?.marketKey === key).slice(-LIMIT);
  } catch {
    return [];
  }
}

function store(key: string, points: Point[]) {
  try {
    window.localStorage.setItem(`${STORAGE_PREFIX}${key}`, JSON.stringify(points.slice(-LIMIT)));
  } catch {
    // Display cache only. Collector DB remains authoritative.
  }
}

function TrajectoryCanvas({ points }: { points: Point[] }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;

    const render = () => {
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);

      const width = rect.width;
      const height = rect.height;
      const padX = 20;
      const padY = 16;
      ctx.lineWidth = 1;
      ctx.strokeStyle = "rgba(255,255,255,.08)";
      ctx.setLineDash([]);
      for (let i = 0; i <= 4; i++) {
        const y = padY + (i / 4) * (height - padY * 2);
        ctx.beginPath();
        ctx.moveTo(padX, y);
        ctx.lineTo(width - padX, y);
        ctx.stroke();
      }

      const draw = (values: (number | null)[], color: string, dashed = false) => {
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.2;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.setLineDash(dashed ? [7, 5] : []);
        let started = false;
        values.forEach((value, index) => {
          if (value == null || value < 0 || value > 1) {
            started = false;
            return;
          }
          const x = padX + (index / Math.max(1, values.length - 1)) * (width - padX * 2);
          const y = height - padY - value * (height - padY * 2);
          if (!started) {
            ctx.moveTo(x, y);
            started = true;
          } else {
            ctx.lineTo(x, y);
          }
        });
        if (started) ctx.stroke();
        ctx.setLineDash([]);
      };

      const visible = points.slice(-LIMIT);
      draw(visible.map(point => point.binanceUp), "#8df4c0");
      draw(visible.map(point => point.binanceDown), "#ffb45c");
      draw(visible.map(point => point.polyUp), "#55d8ff", true);
      draw(visible.map(point => point.polyDown), "#c58cff", true);
    };

    render();
    const observer = new ResizeObserver(render);
    observer.observe(canvas);
    window.addEventListener("resize", render);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", render);
    };
  }, [points]);

  return <canvas ref={ref} aria-label="實單頁面 Binance 與 Polymarket UP DOWN 市場軌跡" style={{ width: "100%", height: 230, display: "block" }} />;
}

export default function PolyLiveTrajectoryDashboard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [marketKey, setMarketKey] = useState<string | null>(null);
  const [poly, setPoly] = useState<CrossState["polymarket"] | null>(null);
  const [binanceMarketId, setBinanceMarketId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [polyGapLive, setPolyGapLive] = useState<PolyGapLiveState | null>(null);
  const [entryDelayInput, setEntryDelayInput] = useState("10");
  const [entryDelayDirty, setEntryDelayDirty] = useState(false);
  const [entryDelaySaveState, setEntryDelaySaveState] = useState("讀取中…");
  const [entryDelayError, setEntryDelayError] = useState("");
  const keyRef = useRef<string | null>(null);

  useEffect(() => {
    const locate = () => {
      const node = document.querySelector<HTMLElement>(".live-rules-editor");
      setTarget(current => current === node ? current : node);
    };
    locate();
    const timer = window.setInterval(locate, 750);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    const load = async () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const [crossResponse, binanceResponse] = await Promise.all([
          fetch("/api/oracle-cross-market", { cache: "no-store", signal: controller.signal }),
          fetch(binanceRealtimeUrl(), { cache: "no-store", signal: controller.signal }),
        ]);
        const cross = await crossResponse.json() as CrossPayload;
        const binance = await binanceResponse.json() as BinancePayload;
        if (!crossResponse.ok || !cross.ok || !cross.state) throw new Error(cross.error ?? `cross-oracle HTTP ${crossResponse.status}`);
        if (!binanceResponse.ok || !binance.latest) throw new Error(`Binance realtime HTTP ${binanceResponse.status}`);
        if (!alive) return;

        const nextPoly = cross.state.polymarket;
        const slug = String(nextPoly?.market?.slug ?? "");
        const marketId = finite(binance.latest.market_id);
        const ageMs = finite(nextPoly?.ageMs);
        const receivedMs = finite(nextPoly?.receivedTimestampMs);
        const continuity = nextPoly?.continuity;
        const identityAligned = !continuity?.targetMarketSlug || !continuity?.marketSlug
          ? true
          : continuity.targetMarketSlug === continuity.marketSlug;
        const polyFresh = String(nextPoly?.status ?? "").toUpperCase() === "LIVE"
          && receivedMs != null
          && ageMs != null
          && ageMs <= MAX_RENDERABLE_POLY_AGE_MS
          && continuity?.healthy !== false
          && continuity?.gapActive !== true
          && identityAligned;

        setPoly(nextPoly ?? null);
        setBinanceMarketId(marketId == null ? null : Math.trunc(marketId));
        setError("");

        if (!slug || marketId == null) {
          keyRef.current = null;
          setMarketKey(null);
          setPoints([]);
          return;
        }

        const nextKey = `${Math.trunc(marketId)}:${slug}`;
        const point: Point = {
          marketKey: nextKey,
          atMs: Math.floor(Date.now() / SAMPLE_MS) * SAMPLE_MS,
          binanceUp: finite(binance.latest.up_ask),
          binanceDown: finite(binance.latest.down_ask),
          polyUp: polyFresh ? finite(nextPoly?.up?.bestAsk) : null,
          polyDown: polyFresh ? finite(nextPoly?.down?.bestAsk) : null,
        };
        setMarketKey(nextKey);
        setPoints(current => {
          let same = current;
          if (keyRef.current !== nextKey) {
            keyRef.current = nextKey;
            same = loadStored(nextKey);
          } else {
            same = current.filter(row => row.marketKey === nextKey);
          }
          const last = same.at(-1);
          const next = last?.atMs === point.atMs
            ? [...same.slice(0, -1), point]
            : [...same, point].slice(-LIMIT);
          store(nextKey, next);
          return next;
        });
      } catch (caught) {
        if (!alive || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    };

    void load();
    const timer = window.setInterval(load, SAMPLE_MS);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    const loadLiveSettings = async () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch("/api/poly-gap-live", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await response.json() as PolyGapLivePayload;
        if (!response.ok || !payload.ok || !payload.state) {
          throw new Error(payload.error ?? `Poly GAP Live HTTP ${response.status}`);
        }
        if (!alive) return;
        setPolyGapLive(payload.state);
        const applied = finite(payload.state.settings?.entryDelaySeconds);
        if (!entryDelayDirty && applied != null) {
          setEntryDelayInput(String(applied));
        }
        setEntryDelaySaveState("已同步");
        setEntryDelayError("");
      } catch (caught) {
        if (!alive || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setEntryDelaySaveState("讀取失敗");
        setEntryDelayError(caught instanceof Error ? caught.message : String(caught));
      }
    };

    void loadLiveSettings();
    const timer = window.setInterval(loadLiveSettings, 2000);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [entryDelayDirty]);

  const saveEntryDelay = async () => {
    const delay = Number(entryDelayInput);
    if (!Number.isFinite(delay) || delay < ENTRY_DELAY_MIN_SECONDS || delay > ENTRY_DELAY_MAX_SECONDS) {
      setEntryDelaySaveState("輸入無效");
      setEntryDelayError(`延遲秒數必須介於 ${ENTRY_DELAY_MIN_SECONDS}～${ENTRY_DELAY_MAX_SECONDS} 秒`);
      return;
    }
    setEntryDelaySaveState("套用中…");
    setEntryDelayError("");
    try {
      const response = await fetch("/api/poly-gap-live", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entryDelaySeconds: delay }),
      });
      const payload = await response.json() as PolyGapLivePayload;
      if (!response.ok || !payload.ok || !payload.state) {
        throw new Error(payload.error ?? `Poly GAP Live settings HTTP ${response.status}`);
      }
      setPolyGapLive(payload.state);
      const applied = finite(payload.state.settings?.entryDelaySeconds) ?? delay;
      setEntryDelayInput(String(applied));
      setEntryDelayDirty(false);
      setEntryDelaySaveState(`已套用 ${applied} 秒`);
    } catch (caught) {
      setEntryDelaySaveState("套用失敗");
      setEntryDelayError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  if (!target) return null;
  const discovery = poly?.marketDiscovery;
  const polyStatus = discovery?.status === "WAITING_GAMMA"
    ? "WAITING_GAMMA"
    : poly?.status ?? "—";
  const continuity = poly?.continuity;
  const polyFresh = poly?.receivedTimestampMs != null
    && poly?.ageMs != null
    && Number(poly.ageMs) <= MAX_RENDERABLE_POLY_AGE_MS
    && String(poly?.status ?? "").toUpperCase() === "LIVE"
    && continuity?.healthy !== false
    && continuity?.gapActive !== true;
  const appliedDelay = finite(polyGapLive?.settings?.entryDelaySeconds);
  const delayRemainingMs = finite(polyGapLive?.entryDelay?.remainingMs);
  const delayReady = polyGapLive?.entryDelay?.ready === true;

  return createPortal(
    <section aria-label="R_POLY_GAP_SCALP 專用實單控制與 Polymarket 市場軌跡" style={{
      gridColumn: "1 / -1",
      marginTop: 14,
      padding: 16,
      border: "1px solid rgba(85,216,255,.26)",
      borderRadius: 16,
      background: "rgba(7,13,22,.72)",
    }}>
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        gap: 16,
        alignItems: "center",
        flexWrap: "wrap",
        padding: 12,
        marginBottom: 14,
        border: "1px solid rgba(141,244,192,.2)",
        borderRadius: 12,
        background: "rgba(141,244,192,.045)",
      }}>
        <div style={{ minWidth: 240, flex: "1 1 320px" }}>
          <span className="eyebrow">R_POLY_GAP_SCALP · 專用實單</span>
          <h3 style={{ margin: "4px 0 3px" }}>開局延遲入場</h3>
          <small>
            每個 5 分鐘市場開盤後延遲指定秒數才允許 NEW BUY。0 秒代表關閉延遲；既有部位的 SELL / 同步 / 結算不受影響。
          </small>
        </div>
        <div style={{ display: "flex", alignItems: "end", gap: 8, flexWrap: "wrap" }}>
          <label style={{ display: "grid", gap: 5, fontSize: 11 }}>
            <span>延遲秒數（0–240）</span>
            <input
              aria-label="R_POLY_GAP_SCALP 開局延遲入場秒數"
              type="number"
              min={ENTRY_DELAY_MIN_SECONDS}
              max={ENTRY_DELAY_MAX_SECONDS}
              step="1"
              value={entryDelayInput}
              onChange={event => {
                setEntryDelayInput(event.target.value);
                setEntryDelayDirty(true);
                setEntryDelaySaveState("尚未套用");
                setEntryDelayError("");
              }}
              onKeyDown={event => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void saveEntryDelay();
                }
              }}
              style={{ width: 110 }}
            />
          </label>
          <button
            type="button"
            onClick={() => void saveEntryDelay()}
            disabled={entryDelaySaveState === "套用中…" || !entryDelayDirty}
          >
            {entryDelaySaveState === "套用中…" ? "套用中…" : "套用"}
          </button>
          <div style={{ minWidth: 160, textAlign: "right" }}>
            <strong>{appliedDelay == null ? "—" : `${appliedDelay} 秒`}</strong>
            <small style={{ display: "block", marginTop: 4 }}>
              {polyGapLive?.status ?? "LIVE 狀態讀取中"} · {entryDelaySaveState}
            </small>
            {delayRemainingMs != null && !delayReady && <small style={{ display: "block", marginTop: 2, color: "#ffcf8f" }}>
              本局尚需等待 {(delayRemainingMs / 1000).toFixed(1)} 秒
            </small>}
          </div>
        </div>
        {entryDelayError && <small style={{ flexBasis: "100%", color: "#ff9f9f" }}>設定錯誤：{entryDelayError}</small>}
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div>
          <span className="eyebrow">LIVE VIEW · POLYMARKET / BINANCE</span>
          <h3 style={{ margin: "4px 0 0" }}>實單市場軌跡</h3>
          <small>最近 90 秒共同 1 秒取樣。Binance 為實線，Polymarket 為虛線；Poly freshness 無效時會斷線，不延用舊值。</small>
        </div>
        <div style={{ textAlign: "right" }}>
          <strong>{polyStatus}</strong>
          <small style={{ display: "block", marginTop: 4 }}>Binance #{binanceMarketId ?? "—"} · Poly age {poly?.ageMs == null ? "—" : `${Math.round(Number(poly.ageMs))} ms`}</small>
          <small style={{ display: "block", marginTop: 2 }}>Poly receipt {poly?.receivedTimestampMs == null ? "—" : new Date(Number(poly.receivedTimestampMs)).toLocaleTimeString("zh-TW", { hour12: false })} · {polyFresh ? "FRESH" : "NOT FRESH"}</small>
        </div>
      </div>

      <div style={{ marginTop: 12, borderRadius: 12, overflow: "hidden", background: "rgba(0,0,0,.16)" }}>
        <TrajectoryCanvas points={points} />
      </div>

      <div style={{ display: "flex", gap: "8px 16px", flexWrap: "wrap", marginTop: 10, fontSize: 11 }}>
        <span><i style={{ display: "inline-block", width: 10, height: 3, background: "#8df4c0", marginRight: 5 }} />Binance UP</span>
        <span><i style={{ display: "inline-block", width: 10, height: 3, background: "#ffb45c", marginRight: 5 }} />Binance DOWN</span>
        <span><i style={{ display: "inline-block", width: 10, height: 3, background: "#55d8ff", marginRight: 5 }} />Poly UP</span>
        <span><i style={{ display: "inline-block", width: 10, height: 3, background: "#c58cff", marginRight: 5 }} />Poly DOWN</span>
        <span style={{ opacity: .7 }}>{marketKey ?? discovery?.slug ?? "等待市場對齊"}</span>
      </div>

      {discovery?.status === "WAITING_GAMMA" && <small style={{ display: "block", marginTop: 9, color: "#ffcf8f" }}>
        Gamma 尚未發布目前 5 分鐘 slug；collector 會持續重試，並優先使用預抓的下一市場 metadata。這是市場 discovery 等待，不是 TLS 錯誤。
      </small>}
      {!polyFresh && polyStatus !== "WAITING_GAMMA" && <small style={{ display: "block", marginTop: 9, color: "#ffcf8f" }}>
        Poly freshness 尚未確認；虛線暫停，避免把 cache 或上一市場價格當作即時價。
      </small>}
      {(error || poly?.error) && <small style={{ display: "block", marginTop: 9, color: "#ff9f9f" }}>資料錯誤：{error || poly?.error}</small>}
    </section>,
    target,
  );
}
