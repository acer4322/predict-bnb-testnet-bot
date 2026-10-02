"use client";

/**
 * Emergency-disabled.
 *
 * Live strategy and Observer options must be owned by the main React state.
 * This component previously injected options, polled live rules separately,
 * dispatched synthetic change events, and replaced window.fetch globally.
 * Those side effects made live-rule persistence and dashboard health unsafe.
 */
export default function StableConsensusLiveControls() {
  return null;
}
