"use client";

import { useSyncExternalStore } from "react";

const REFRESH_MS = 15_000;
const INITIAL_DELAY_MS = 900;

type Listener = () => void;

type SharedState = {
  snapshot: unknown | null;
  error: string | null;
  updatedAt: number | null;
};

let state: SharedState = {
  snapshot: null,
  error: null,
  updatedAt: null,
};
let inFlight: Promise<void> | null = null;
let initialTimer: number | null = null;
let refreshTimer: number | null = null;
let visibilityInstalled = false;
const listeners = new Set<Listener>();

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function emit() {
  for (const listener of listeners) listener();
}

async function load() {
  if (typeof window === "undefined" || document.visibilityState !== "visible") return;
  if (inFlight) return inFlight;

  inFlight = (async () => {
    try {
      const response = await fetch(apiUrl("/api/state"), {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`dashboard request failed: ${response.status}`);
      }
      const snapshot = await response.json() as unknown;
      state = {
        snapshot,
        error: null,
        updatedAt: Date.now(),
      };
      emit();
    } catch (error) {
      state = {
        ...state,
        error: error instanceof Error ? error.message : "dashboard request failed",
      };
      emit();
    } finally {
      inFlight = null;
    }
  })();

  return inFlight;
}

function handleVisibility() {
  if (document.visibilityState === "visible") void load();
}

function start() {
  if (typeof window === "undefined") return;
  if (initialTimer == null && state.snapshot == null) {
    // Let the main dashboard's realtime/statistics requests paint first.
    // The add-on cards then share one delayed state request instead of racing
    // four identical full-state requests during first render.
    initialTimer = window.setTimeout(() => {
      initialTimer = null;
      void load();
    }, INITIAL_DELAY_MS);
  }
  if (refreshTimer == null) {
    refreshTimer = window.setInterval(() => void load(), REFRESH_MS);
  }
  if (!visibilityInstalled) {
    document.addEventListener("visibilitychange", handleVisibility);
    visibilityInstalled = true;
  }
}

function stop() {
  if (listeners.size > 0 || typeof window === "undefined") return;
  if (initialTimer != null) {
    window.clearTimeout(initialTimer);
    initialTimer = null;
  }
  if (refreshTimer != null) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (visibilityInstalled) {
    document.removeEventListener("visibilitychange", handleVisibility);
    visibilityInstalled = false;
  }
}

function subscribe(listener: Listener) {
  listeners.add(listener);
  start();
  return () => {
    listeners.delete(listener);
    stop();
  };
}

function getSnapshot() {
  return state;
}

const SERVER_SNAPSHOT: SharedState = {
  snapshot: null,
  error: null,
  updatedAt: null,
};

function getServerSnapshot() {
  return SERVER_SNAPSHOT;
}

export function useSharedDashboardState<T>() {
  const current = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return {
    payload: current.snapshot as T | null,
    error: current.error,
    updatedAt: current.updatedAt,
  };
}
