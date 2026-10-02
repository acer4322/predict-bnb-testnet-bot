from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v13 import PaperChopGuardedPolyGapLiveEngine


ENTRY_POLY_WARMUP_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_POLY_WARMUP_MS", "750")),
)
ENTRY_POLY_WARMUP_SAMPLES = max(
    2,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_POLY_WARMUP_SAMPLES", "3")),
)
ENTRY_POLY_WARMUP_MAX_SAMPLE_GAP_MS = max(
    ENTRY_POLY_WARMUP_MS,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_POLY_WARMUP_MAX_GAP_MS", "1500")),
)
ENTRY_MARKET_END_MAX_SKEW_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_MARKET_END_MAX_SKEW_MS", "1500")),
)


class MarketBoundPolyGapLiveEngine(PaperChopGuardedPolyGapLiveEngine):
    """V14: a BUY cannot outlive or outrun its exact Polymarket market identity.

    This is an independent last line of defense above the collector:
    - wall-clock five-minute bucket, Binance Prediction end time and Poly event
      slug/window end must all identify the same market;
    - the Poly receipt must have been received after this market window started;
    - continuity gap generation is part of the entry identity, so any feed break
      forces a fresh warm-up before another BUY;
    - a newly ready market needs three distinct fresh receipts spanning at least
      750 ms before the first entry is allowed;
    - after the signed BUY quote returns, V8's existing signal re-check calls this
      same method while the round is ENTRY_QUOTE.  The previously confirmed market
      key must still match, so rollover or feed recovery during quote flight rejects
      the order before placement.

    Existing OPEN/EXIT position management remains V13/V12/V11 behavior.  This
    gate is deliberately stricter only for creating new real-money exposure.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entry_market_warmup: dict[str, Any] | None = None
        self.entry_market_ready_key: tuple[int, str, int] | None = None
        self.entry_market_ready_at_ms: int | None = None
        self.entry_market_ready_receipt_ms: int | None = None
        self.entry_market_gate_resets = 0
        self.entry_market_gate_rejects = 0
        self.last_entry_market_gate_reason: str | None = "waiting for current-market Poly readiness"

    @staticmethod
    def _wall_bucket(now_ms: int) -> int:
        return int((now_ms // 1000) // 300) * 300

    @staticmethod
    def _binance_expected_bucket(end_ms: int) -> int | None:
        if end_ms <= 0:
            return None
        end_sec = int(round(end_ms / 1000.0))
        bucket = end_sec - 300
        if bucket <= 0:
            return None
        # Binance five-minute Prediction markets are expected on exact boundaries.
        return int(round(bucket / 300.0)) * 300

    def _reset_entry_market_gate(self, reason: str) -> None:
        if self.entry_market_warmup is not None or self.entry_market_ready_key is not None:
            self.entry_market_gate_resets += 1
        self.entry_market_warmup = None
        self.entry_market_ready_key = None
        self.entry_market_ready_at_ms = None
        self.entry_market_ready_receipt_ms = None
        self.last_entry_market_gate_reason = reason

    def _strict_entry_binding(
        self,
        poly: dict[str, Any],
        *,
        now_ms: int,
    ) -> tuple[tuple[int, str, int], int] | None:
        with self.lock:
            market = dict(self.market_cache or {})
        market_id = int(market.get("market_id") or 0)
        binance_end_ms = int(market.get("end_ms") or 0)
        if market_id <= 0 or binance_end_ms <= 0:
            self.last_entry_market_gate_reason = "Binance current market identity unavailable"
            return None

        expected_bucket = self._binance_expected_bucket(binance_end_ms)
        wall_bucket = self._wall_bucket(now_ms)
        if expected_bucket is None or expected_bucket != wall_bucket:
            self.last_entry_market_gate_reason = (
                f"Binance market is not the current wall-clock 5m bucket: "
                f"expectedBucket={expected_bucket}; wallBucket={wall_bucket}"
            )
            return None

        expected_slug = f"btc-updown-5m-{expected_bucket}"
        poly_slug = str(poly.get("slug") or "")
        if poly_slug != expected_slug:
            self.last_entry_market_gate_reason = (
                f"Poly event slug mismatch: expected={expected_slug}; got={poly_slug or 'none'}"
            )
            return None

        poly_end_ms = int(poly.get("windowEndMs") or 0)
        if (
            poly_end_ms <= 0
            or abs(poly_end_ms - binance_end_ms) > ENTRY_MARKET_END_MAX_SKEW_MS
        ):
            self.last_entry_market_gate_reason = (
                f"Poly/Binance window mismatch: polyEnd={poly_end_ms}; "
                f"binanceEnd={binance_end_ms}; maxSkew={ENTRY_MARKET_END_MAX_SKEW_MS}ms"
            )
            return None

        receipt_raw = poly.get("receivedTimestampMs")
        try:
            receipt_ms = int(receipt_raw)
        except (TypeError, ValueError):
            self.last_entry_market_gate_reason = "Poly receipt timestamp unavailable"
            return None
        window_start_ms = expected_bucket * 1000
        if receipt_ms < window_start_ms:
            self.last_entry_market_gate_reason = (
                f"Poly receipt belongs before current market window: "
                f"receipt={receipt_ms}; windowStart={window_start_ms}"
            )
            return None
        if receipt_ms > now_ms + 2_000:
            self.last_entry_market_gate_reason = (
                f"Poly receipt timestamp is unexpectedly in the future: {receipt_ms}"
            )
            return None

        generation_raw = poly.get("gapGeneration")
        try:
            gap_generation = int(generation_raw)
        except (TypeError, ValueError):
            self.last_entry_market_gate_reason = "Poly continuity generation unavailable"
            return None
        if gap_generation < 0:
            self.last_entry_market_gate_reason = "Poly continuity generation invalid"
            return None

        return (market_id, expected_slug, gap_generation), receipt_ms

    def _warm_entry_market(
        self,
        key: tuple[int, str, int],
        receipt_ms: int,
        now_ms: int,
    ) -> bool:
        if self.entry_market_ready_key == key:
            # Collector freshness still gates every read through base _poly_state.
            self.entry_market_ready_receipt_ms = max(
                int(self.entry_market_ready_receipt_ms or 0), receipt_ms
            )
            self.last_entry_market_gate_reason = None
            return True

        candidate = self.entry_market_warmup
        same = bool(candidate and tuple(candidate.get("key") or ()) == key)
        if not same:
            self.entry_market_warmup = {
                "key": list(key),
                "firstSeenAtMs": now_ms,
                "lastSeenAtMs": now_ms,
                "lastReceiptMs": receipt_ms,
                "samples": 1,
            }
            self.last_entry_market_gate_reason = (
                f"warming current Poly market: 1/{ENTRY_POLY_WARMUP_SAMPLES} distinct receipts"
            )
            return False

        assert candidate is not None
        last_receipt = int(candidate.get("lastReceiptMs") or 0)
        if receipt_ms <= last_receipt:
            candidate["lastSeenAtMs"] = now_ms
            self.last_entry_market_gate_reason = (
                f"warming current Poly market: {candidate.get('samples', 1)}/"
                f"{ENTRY_POLY_WARMUP_SAMPLES} distinct receipts"
            )
            return False

        if receipt_ms - last_receipt > ENTRY_POLY_WARMUP_MAX_SAMPLE_GAP_MS:
            self.entry_market_gate_resets += 1
            self.entry_market_warmup = {
                "key": list(key),
                "firstSeenAtMs": now_ms,
                "lastSeenAtMs": now_ms,
                "lastReceiptMs": receipt_ms,
                "samples": 1,
            }
            self.last_entry_market_gate_reason = "Poly warm-up continuity gap; restarted"
            return False

        candidate["samples"] = int(candidate.get("samples") or 1) + 1
        candidate["lastSeenAtMs"] = now_ms
        candidate["lastReceiptMs"] = receipt_ms
        elapsed_ms = now_ms - int(candidate.get("firstSeenAtMs") or now_ms)
        if (
            int(candidate["samples"]) >= ENTRY_POLY_WARMUP_SAMPLES
            and elapsed_ms >= ENTRY_POLY_WARMUP_MS
        ):
            self.entry_market_ready_key = key
            self.entry_market_ready_at_ms = now_ms
            self.entry_market_ready_receipt_ms = receipt_ms
            self.entry_market_warmup = None
            self.last_entry_market_gate_reason = None
            self._event(
                "INFO",
                "ENTRY_POLY_MARKET_READY",
                int(key[0]),
                None,
                (
                    f"Poly {key[1]} generation {key[2]} warmed for {elapsed_ms}ms "
                    f"across {ENTRY_POLY_WARMUP_SAMPLES}+ distinct receipts"
                ),
            )
            return True

        self.last_entry_market_gate_reason = (
            f"warming current Poly market: {candidate['samples']}/{ENTRY_POLY_WARMUP_SAMPLES} "
            f"receipts, {elapsed_ms}/{ENTRY_POLY_WARMUP_MS}ms"
        )
        return False

    def _poly_state(self) -> dict[str, Any] | None:
        poly = super()._poly_state()
        if poly is None:
            # Base state already fail-closes stale, gap-active and non-LIVE feeds.
            return None

        active = self._current_active_round()
        active_state = str((active or {}).get("state") or "")

        # Once a real position exists, keep V12/V11 exit behavior intact.  V2's
        # rollover protection still prevents a new market feed from exiting an old
        # held round.  The strict market gate below is for creating/placing BUYs.
        if active is not None and active_state in {"OPEN", "EXIT_QUOTE", "EXIT_SYNC", "ENTRY_SYNC"}:
            return poly

        now_ms = base._now_ms()
        binding = self._strict_entry_binding(poly, now_ms=now_ms)
        if binding is None:
            self.entry_market_gate_rejects += 1
            if active is None:
                # A mismatch or feed-generation change must require a brand-new warm-up.
                self._reset_entry_market_gate(
                    self.last_entry_market_gate_reason or "entry market binding failed"
                )
            return None
        key, receipt_ms = binding

        if active is not None and active_state == "ENTRY_QUOTE":
            # This is V8's post-signed-quote re-check.  Never allow a quote that
            # started under one market/feed generation to be placed under another.
            if self.entry_market_ready_key != key:
                self.entry_market_gate_rejects += 1
                self.last_entry_market_gate_reason = (
                    "signed BUY quote returned after Poly market/feed generation changed"
                )
                return None
            if receipt_ms < int(self.entry_market_ready_receipt_ms or 0):
                self.entry_market_gate_rejects += 1
                self.last_entry_market_gate_reason = "Poly receipt regressed during BUY quote"
                return None
            return poly

        if active is None and not self._warm_entry_market(key, receipt_ms, now_ms):
            return None
        return poly

    def _tick(self) -> None:
        had_active = self._current_active_round() is not None
        super()._tick()
        if had_active or self._current_active_round() is not None:
            return
        if self.status == "WAITING_DATA" and self.last_entry_market_gate_reason:
            self.status = "BLOCKED_POLY_MARKET_NOT_READY"
            self.last_error = self.last_entry_market_gate_reason

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V14"
        warmup = dict(self.entry_market_warmup) if self.entry_market_warmup else None
        payload["entryMarketBinding"] = {
            "ready": self.entry_market_ready_key is not None,
            "readyKey": list(self.entry_market_ready_key) if self.entry_market_ready_key else None,
            "readyAtMs": self.entry_market_ready_at_ms,
            "readyReceiptMs": self.entry_market_ready_receipt_ms,
            "warmup": warmup,
            "warmupMs": ENTRY_POLY_WARMUP_MS,
            "warmupDistinctReceipts": ENTRY_POLY_WARMUP_SAMPLES,
            "warmupMaxSampleGapMs": ENTRY_POLY_WARMUP_MAX_SAMPLE_GAP_MS,
            "maxPolyBinanceEndSkewMs": ENTRY_MARKET_END_MAX_SKEW_MS,
            "gateResets": int(self.entry_market_gate_resets),
            "gateRejects": int(self.entry_market_gate_rejects),
            "lastReason": self.last_entry_market_gate_reason,
            "requiresWallClockBucketMatch": True,
            "requiresBinanceEndMatch": True,
            "requiresExactPolyEventSlug": True,
            "requiresCurrentWindowReceipt": True,
            "gapGenerationIsPartOfIdentity": True,
            "signedQuoteRevalidatesSameIdentityBeforePlacement": True,
            "collectorNotReadyNeverFallsBackToOldPoly": True,
        }
        return payload


base.PolyGapLiveEngine = MarketBoundPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
