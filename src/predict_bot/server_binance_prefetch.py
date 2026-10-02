from __future__ import annotations

import time
from typing import Any

from . import server as base
from .binance_exact_market import DEFAULT_WINDOW_MS, bucket_start_ms, find_exact_market_summary


SERVER_BINANCE_PREFETCH_LEAD_MS = 120_000
SERVER_BINANCE_PREFETCH_RETRY_MS = 5_000
SERVER_BINANCE_CURRENT_RETRY_MS = 500
SERVER_BINANCE_EXACT_MAX_PAGES = 1


def _ensure_prefetch_state(self: Any) -> None:
    if not hasattr(self, "binance_prefetched_market"):
        self.binance_prefetched_market = None
    if not hasattr(self, "binance_prefetch_target_start_ms"):
        self.binance_prefetch_target_start_ms = None
    if not hasattr(self, "binance_prefetch_attempts"):
        self.binance_prefetch_attempts = 0
    if not hasattr(self, "binance_prefetch_hits"):
        self.binance_prefetch_hits = 0
    if not hasattr(self, "binance_prefetch_promotions"):
        self.binance_prefetch_promotions = 0
    if not hasattr(self, "binance_exact_current_hits"):
        self.binance_exact_current_hits = 0
    if not hasattr(self, "binance_exact_current_misses"):
        self.binance_exact_current_misses = 0
    if not hasattr(self, "binance_last_prefetch_error"):
        self.binance_last_prefetch_error = None
    if not hasattr(self, "binance_last_current_error"):
        self.binance_last_current_error = None
    if not hasattr(self, "_next_binance_prefetch_attempt_mono"):
        self._next_binance_prefetch_attempt_mono = 0.0
    if not hasattr(self, "_next_binance_current_attempt_mono"):
        self._next_binance_current_attempt_mono = 0.0


def _summary_matches_start(summary: dict[str, Any] | None, target_start_ms: int) -> bool:
    if not summary:
        return False
    try:
        start_ms = int(summary.get("startDate") or 0)
        end_ms = int(summary.get("endDate") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        abs(start_ms - int(target_start_ms)) <= 1_500
        and abs(end_ms - (int(target_start_ms) + DEFAULT_WINDOW_MS)) <= 1_500
    )


def _queue_expired_for_settlement(self: Any, market: dict[str, Any]) -> None:
    if self.last_spot is None:
        return
    try:
        topic_id = int(market["marketTopicId"])
    except (KeyError, TypeError, ValueError):
        return
    with self.pending_settlement_lock:
        if not any(
            int(item[0]["marketTopicId"]) == topic_id
            for item in self.pending_rollover_settlements
        ):
            self.pending_rollover_settlements.append((market, float(self.last_spot)))


def _find_exact(self: Any, target_start_ms: int) -> dict[str, Any] | None:
    assert self.prediction is not None
    return find_exact_market_summary(
        self.prediction,
        symbol="BTCUSDT",
        target_start_ms=int(target_start_ms),
        max_pages=SERVER_BINANCE_EXACT_MAX_PAGES,
    )


def _market_rollover_loop_prefetched(self: Any) -> None:
    """Keep 8766 on the exact Binance 5m window and prefetch the next one.

    The base collector only starts discovery after ``self.market`` expires.  If
    Binance's current-market index lags the boundary, the UI and all paper/live
    consumers of 8766 sit in WAITING_FOR_MARKET even when the future topic was
    already visible before the rollover.  This replacement uses that future
    metadata early, but never substitutes a nearest wrong window.
    """

    assert self.prediction is not None
    _ensure_prefetch_state(self)

    while not self.stop_event.is_set():
        wait_seconds = 0.05
        try:
            now_ms = int(self.prediction.server_timestamp_ms())
            current_start_ms = bucket_start_ms(now_ms)
            next_start_ms = current_start_ms + DEFAULT_WINDOW_MS

            with self.market_lock:
                market = self.market
                if market is not None and int(market.get("endDate") or 0) <= now_ms:
                    _queue_expired_for_settlement(self, market)
                    self.market = None
                    market = None

            # If the market that was prefetched during the previous round is now
            # the exact current window, publish it before making any rollover REST
            # request.  The normal collector loop still validates/detail-fetches it
            # before publishing books or signals.
            if market is None:
                with self.market_lock:
                    prefetched = dict(self.binance_prefetched_market or {})
                if _summary_matches_start(prefetched, current_start_ms):
                    checked_ms = int(self.prediction.server_timestamp_ms())
                    if int(prefetched.get("startDate") or 0) <= checked_ms < int(
                        prefetched.get("endDate") or 0
                    ):
                        with self.market_lock:
                            if self.market is None:
                                self.market = prefetched
                                self.binance_prefetched_market = None
                                self.binance_prefetch_target_start_ms = None
                                market = self.market
                        self.binance_prefetch_promotions += 1
                        self.binance_last_current_error = None

            # If prefetch was unavailable, look up only the exact current window.
            # Do not use find_market_summary's "nearest candidate" fallback here.
            if market is None:
                mono = time.monotonic()
                if mono >= self._next_binance_current_attempt_mono:
                    self._next_binance_current_attempt_mono = (
                        mono + SERVER_BINANCE_CURRENT_RETRY_MS / 1000.0
                    )
                    discovered = _find_exact(self, current_start_ms)
                    checked_ms = int(self.prediction.server_timestamp_ms())
                    if (
                        discovered is not None
                        and int(discovered.get("startDate") or 0) <= checked_ms
                        < int(discovered.get("endDate") or 0)
                    ):
                        with self.market_lock:
                            if self.market is None:
                                self.market = discovered
                                market = self.market
                        self.binance_exact_current_hits += 1
                        self.binance_last_current_error = None
                    else:
                        self.binance_exact_current_misses += 1
                        self.binance_last_current_error = (
                            f"exact Binance BTC 5m topic not published for start={current_start_ms}"
                        )
                if market is None:
                    self.status = "WAITING_FOR_MARKET"
                    wait_seconds = SERVER_BINANCE_CURRENT_RETRY_MS / 1000.0

            # While the current topic is healthy, prefetch only the exact next
            # boundary.  Once found, stop polling until that boundary arrives.
            if market is not None:
                remaining_ms = int(market.get("endDate") or 0) - int(
                    self.prediction.server_timestamp_ms()
                )
                with self.market_lock:
                    prefetched = dict(self.binance_prefetched_market or {})
                if (
                    0 < remaining_ms <= SERVER_BINANCE_PREFETCH_LEAD_MS
                    and not _summary_matches_start(prefetched, next_start_ms)
                ):
                    mono = time.monotonic()
                    if mono >= self._next_binance_prefetch_attempt_mono:
                        self._next_binance_prefetch_attempt_mono = (
                            mono + SERVER_BINANCE_PREFETCH_RETRY_MS / 1000.0
                        )
                        self.binance_prefetch_attempts += 1
                        candidate = _find_exact(self, next_start_ms)
                        if candidate is not None:
                            with self.market_lock:
                                self.binance_prefetched_market = candidate
                                self.binance_prefetch_target_start_ms = next_start_ms
                            self.binance_prefetch_hits += 1
                            self.binance_last_prefetch_error = None
                        else:
                            self.binance_last_prefetch_error = (
                                f"next exact Binance BTC 5m topic not published yet: "
                                f"start={next_start_ms}"
                            )
                if remaining_ms > 0:
                    wait_seconds = min(0.05, max(0.005, remaining_ms / 1000.0))

        except Exception as exc:
            self.status = "RATE_LIMITED" if "HTTP 429" in str(exc) else "ERROR"
            self.error = str(exc)[:300]
            if self.status == "RATE_LIMITED":
                wait_seconds = max(10.0, self.prediction.retry_after_seconds or 0.0)
            else:
                wait_seconds = 0.5

        self.stop_event.wait(wait_seconds)


# server.py creates its global COLLECTOR during module import.  Replacing the
# class method, rather than replacing the object, preserves all existing Store,
# observer and API references while changing the rollover behavior used when
# Collector.run() starts its market-watch thread.
base.Collector._market_rollover_loop = _market_rollover_loop_prefetched


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
