"use client";

/**
 * Emergency-disabled.
 *
 * The paper statistics remain available in /api/state and continue to accrue
 * in the backend. This sidecar view is temporarily disabled because it created
 * an additional high-frequency /api/state poll beside the dashboard's native
 * polling. It will return as a native Observer-panel child that reuses the
 * already-loaded page state.
 */
export default function StableConsensusObserverSimulation() {
  return null;
}
