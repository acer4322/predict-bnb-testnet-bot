import Script from "next/script";

const BOOTSTRAP = String.raw`(() => {
  if (window.__BTC5M_DASHBOARD_PERF_BOOTSTRAPPED__) return;
  window.__BTC5M_DASHBOARD_PERF_BOOTSTRAPPED__ = true;

  const nativeFetch = window.fetch.bind(window);
  const nativeSetInterval = window.setInterval.bind(window);
  const nativeClearInterval = window.clearInterval.bind(window);
  const nativeSetTimeout = window.setTimeout.bind(window);
  const nativeClearTimeout = window.clearTimeout.bind(window);
  const nativeStorageSetItem = Storage.prototype.setItem;

  const responseCache = new Map();
  const trajectoryWriteAt = new Map();
  const CACHE_TTL_MS = 850;
  const TRAJECTORY_STORAGE_WRITE_MS = 5000;
  const metrics = {
    cacheHits: 0,
    networkFetches: 0,
    sharedFetches: 0,
    staggeredIntervals: 0,
    slowedIntervals: 0,
    skippedTrajectoryWrites: 0,
    startedAtMs: Date.now(),
  };

  function requestInfo(input, init) {
    let url;
    try {
      const raw = typeof input === "string" || input instanceof URL
        ? String(input)
        : input && typeof input.url === "string"
          ? input.url
          : "";
      url = new URL(raw, window.location.href);
    } catch {
      return null;
    }
    const requestMethod = typeof Request !== "undefined" && input instanceof Request ? input.method : "GET";
    const method = String((init && init.method) || requestMethod || "GET").toUpperCase();
    if (method !== "GET") return null;

    const isPolyGap = url.origin === window.location.origin && url.pathname === "/api/poly-gap-live";
    const isOracle = url.origin === window.location.origin && url.pathname === "/api/oracle-cross-market";
    const isBinanceRealtime = url.pathname === "/api/realtime"
      && (url.port === "8766" || url.origin === window.location.origin);
    if (!isPolyGap && !isOracle && !isBinanceRealtime) return null;

    return { key: method + " " + url.href, url: url.href };
  }

  function cloneSnapshot(snapshot) {
    return new Response(snapshot.body, {
      status: snapshot.status,
      statusText: snapshot.statusText,
      headers: snapshot.headers,
    });
  }

  window.fetch = async function dashboardSharedFetch(input, init) {
    const info = requestInfo(input, init);
    if (!info) return nativeFetch(input, init);

    const now = Date.now();
    const cached = responseCache.get(info.key);
    if (cached) {
      if (cached.snapshot && now - cached.completedAtMs <= CACHE_TTL_MS) {
        metrics.cacheHits += 1;
        return cloneSnapshot(cached.snapshot);
      }
      if (cached.promise) {
        metrics.sharedFetches += 1;
        const snapshot = await cached.promise;
        return cloneSnapshot(snapshot);
      }
    }

    const fetchInit = init ? { ...init } : {};
    // These are display-only shared GETs. One card must not abort a request
    // that other dashboard cards are sharing. Trading processes are separate.
    if ("signal" in fetchInit) delete fetchInit.signal;

    const promise = (async () => {
      metrics.networkFetches += 1;
      const response = await nativeFetch(input, fetchInit);
      const body = await response.text();
      const snapshot = {
        body,
        status: response.status,
        statusText: response.statusText,
        headers: Array.from(response.headers.entries()),
      };
      responseCache.set(info.key, {
        snapshot,
        completedAtMs: Date.now(),
        promise: null,
      });
      return snapshot;
    })();

    responseCache.set(info.key, {
      snapshot: null,
      completedAtMs: 0,
      promise,
    });

    try {
      const snapshot = await promise;
      return cloneSnapshot(snapshot);
    } catch (error) {
      const current = responseCache.get(info.key);
      if (current && current.promise === promise) responseCache.delete(info.key);
      throw error;
    }
  };

  Storage.prototype.setItem = function dashboardStorageSetItem(key, value) {
    const textKey = String(key || "");
    const trajectoryKey = textKey.startsWith("btc5m-live-poly-trajectory:")
      || textKey.startsWith("btc5m-sync-trajectory:");
    if (!trajectoryKey) return nativeStorageSetItem.call(this, key, value);

    const now = Date.now();
    const previous = trajectoryWriteAt.get(textKey) || 0;
    if (now - previous < TRAJECTORY_STORAGE_WRITE_MS) {
      metrics.skippedTrajectoryWrites += 1;
      return;
    }
    trajectoryWriteAt.set(textKey, now);
    return nativeStorageSetItem.call(this, key, value);
  };

  let intervalSequence = 0;
  let virtualIntervalId = 1500000000;
  const staggered = new Map();

  window.setInterval = function dashboardStaggeredInterval(handler, timeout, ...args) {
    const requestedMs = Number(timeout) || 0;
    if (typeof handler !== "function" || requestedMs < 450 || requestedMs > 2500) {
      return nativeSetInterval(handler, timeout, ...args);
    }

    // The sub-second timers found in the dashboard are DOM host locators, not
    // trading clocks. Slow them down so they do not continuously scan the DOM.
    const ms = requestedMs < 900 ? 1500 : requestedMs;
    if (ms !== requestedMs) metrics.slowedIntervals += 1;

    const slots = 6;
    const step = Math.max(40, Math.min(120, Math.floor(ms / slots)));
    const offset = (intervalSequence++ % slots) * step;
    if (offset === 0) return nativeSetInterval(handler, ms, ...args);

    metrics.staggeredIntervals += 1;
    const id = virtualIntervalId++;
    const record = { timeoutId: null, intervalId: null };
    staggered.set(id, record);
    record.timeoutId = nativeSetTimeout(() => {
      if (!staggered.has(id)) return;
      handler(...args);
      record.intervalId = nativeSetInterval(handler, ms, ...args);
    }, offset);
    return id;
  };

  window.clearInterval = function dashboardClearInterval(id) {
    const record = staggered.get(id);
    if (record) {
      if (record.timeoutId != null) nativeClearTimeout(record.timeoutId);
      if (record.intervalId != null) nativeClearInterval(record.intervalId);
      staggered.delete(id);
      return;
    }
    nativeClearInterval(id);
  };

  window.__BTC5M_DASHBOARD_PERF__ = {
    metrics,
    cacheTtlMs: CACHE_TTL_MS,
    trajectoryStorageWriteMs: TRAJECTORY_STORAGE_WRITE_MS,
    snapshot() {
      return {
        ...metrics,
        ageMs: Date.now() - metrics.startedAtMs,
        cachedEndpoints: responseCache.size,
        staggeredActive: staggered.size,
        trajectoryKeys: trajectoryWriteAt.size,
      };
    },
  };
})();`;

export default function DashboardPerformanceBootstrap() {
  return (
    <Script id="btc5m-dashboard-performance-bootstrap" strategy="beforeInteractive">
      {BOOTSTRAP}
    </Script>
  );
}
