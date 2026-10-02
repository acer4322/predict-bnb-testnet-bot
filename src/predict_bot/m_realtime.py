from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from .live_trading import (
    LIVE_MAX_PREDICTION_BOOK_AGE_MS,
    LIVE_OBSERVER_STRATEGIES,
    LIVE_RESEARCH_STRATEGIES,
)
from .research_forward import (
    FUTURES_LEAD_LIVE_OBSERVER_STRATEGIES,
    RESEARCH_PARAMETERS,
)

M_REALTIME_QUEUE_MAX = 50_000
M_SCHEDULER_TICK_SECONDS = 0.001
M7_DEADLINES_SECONDS = (1.0, 2.0, 3.0, 5.0)
M7_EVENT_REORDER_GRACE_SECONDS = 0.010
M6_CANONICAL_CAPTURE_SECONDS = 10.0
SPOT_DATA_MAX_AGE_MS = 4_000.0
MAX_DIRECT_REST_PREDICTION_BOOK_SKEW_MS = 500.0
LIVE_FORWARDABLE_OBSERVER_STRATEGIES = {"M01O_F1"}
LIVE_FORWARDABLE_PAPER_STRATEGIES = (
    LIVE_FORWARDABLE_OBSERVER_STRATEGIES | set(LIVE_RESEARCH_STRATEGIES)
)
VERIFIED_PREDICTION_ORIENTATIONS = {
    "DIRECT_UP_VERIFIED",
    "INVERTED_TO_UP_VERIFIED",
}


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _utc_iso_from_ns(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000_000, timezone.utc).isoformat(
        timespec="microseconds"
    )


def _book_levels(book: dict[str, Any], key: str, *, reverse: bool) -> list[list[float]]:
    levels: list[list[float]] = []
    for raw in book.get(key) or []:
        if isinstance(raw, dict):
            price = _finite(raw.get("price"))
            size = _finite(raw.get("size", raw.get("quantity")))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            price = _finite(raw[0])
            size = _finite(raw[1])
        else:
            continue
        if price is None or size is None or not 0 < price < 1 or size <= 0:
            continue
        levels.append([price, size])
    return sorted(levels, key=lambda level: level[0], reverse=reverse)


def direct_rest_prediction_event(
    *,
    market_id: int,
    up_book: dict[str, Any],
    down_book: dict[str, Any],
    received_wall_ns: int,
    received_monotonic_ns: int,
    current_timestamp_ms: int,
) -> dict[str, Any]:
    """Build one current, direction-explicit Prediction event from REST books."""
    up_bids = _book_levels(up_book, "bids", reverse=True)
    up_asks = _book_levels(up_book, "asks", reverse=False)
    down_bids = _book_levels(down_book, "bids", reverse=True)
    down_asks = _book_levels(down_book, "asks", reverse=False)
    try:
        up_version = int(up_book.get("updateTimestampMs", up_book.get("timestamp")))
        down_version = int(
            down_book.get("updateTimestampMs", down_book.get("timestamp"))
        )
        version_ms = min(up_version, down_version)
        book_skew_ms = abs(up_version - down_version)
        up_book_content_age_ms = max(
            0.0, float(current_timestamp_ms - up_version)
        )
        down_book_content_age_ms = max(
            0.0, float(current_timestamp_ms - down_version)
        )
        book_age_ms = max(
            up_book_content_age_ms,
            down_book_content_age_ms,
        )
    except (TypeError, ValueError):
        up_version = down_version = version_ms = None
        book_skew_ms = book_age_ms = None
        up_book_content_age_ms = down_book_content_age_ms = None
    top_levels_valid = bool(up_bids and up_asks and down_bids and down_asks)
    return {
        "source": "prediction",
        "stream": "orderbook",
        "market_id": int(market_id),
        "received_wall_ns": int(received_wall_ns),
        "received_monotonic_ns": int(received_monotonic_ns),
        "session_id": f"dual-rest-market-{int(market_id)}",
        "update_id": version_ms,
        "exchange_event_ms": version_ms,
        "prediction_book_version_ms": version_ms,
        "prediction_book_version_age_ms": book_age_ms,
        "prediction_orientation": "DIRECT_UP_VERIFIED",
        "prediction_data_source": "dual_token_rest",
        "prediction_sampling_mode": "periodic_snapshot",
        "direct_outcome_books": True,
        "market_observer_already_updated": True,
        "feature_eligible": bool(
            top_levels_valid
            and book_age_ms is not None
            and book_age_ms <= LIVE_MAX_PREDICTION_BOOK_AGE_MS
            and book_skew_ms is not None
            and book_skew_ms <= MAX_DIRECT_REST_PREDICTION_BOOK_SKEW_MS
        ),
        "best_bid": up_bids[0][0] if up_bids else None,
        "best_bid_qty": up_bids[0][1] if up_bids else None,
        "best_ask": up_asks[0][0] if up_asks else None,
        "best_ask_qty": up_asks[0][1] if up_asks else None,
        "direct_down_bid": down_bids[0][0] if down_bids else None,
        "direct_down_bid_qty": down_bids[0][1] if down_bids else None,
        "direct_down_ask": down_asks[0][0] if down_asks else None,
        "direct_down_ask_qty": down_asks[0][1] if down_asks else None,
        "bids": up_bids,
        "asks": up_asks,
        "down_bids": down_bids,
        "down_asks": down_asks,
        "up_book_timestamp_ms": up_version,
        "down_book_timestamp_ms": down_version,
        "book_skew_ms": book_skew_ms,
        "book_age_ms": book_age_ms,
        "content_version_age_ms": book_age_ms,
        "up_book_content_age_ms": up_book_content_age_ms,
        "down_book_content_age_ms": down_book_content_age_ms,
        "transport_receipt_age_ms": 0.0,
    }


class MSeriesRealtimeEngine:
    """Feed M-family paper strategies from WebSocket events, not REST polling.

    The socket callback updates the lightweight, lock-bounded Market Observer
    and copies the event into this engine's bounded queue.  A dedicated worker
    serializes signal state, checks delayed M7 entries on a high-resolution
    monotonic clock, and writes simulated orders through Store.  No Binance
    trading endpoint is called by this class.
    """

    def __init__(
        self,
        *,
        store: Any,
        current_market: Callable[[], dict[str, Any] | None],
        live_signal_sink: Callable[[dict[str, Any]], None] | None = None,
        market_observer: Any = None,
        queue_max: int = M_REALTIME_QUEUE_MAX,
        scheduler_tick_seconds: float = M_SCHEDULER_TICK_SECONDS,
    ) -> None:
        self.store = store
        self.current_market = current_market
        # This sink only accepts a non-blocking queue copy.  Network requests
        # remain isolated in the live executor and never stall market events.
        self.live_signal_sink = live_signal_sink
        self.market_observer = market_observer
        self.events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_max)
        self.scheduler_tick_seconds = max(0.001, float(scheduler_tick_seconds))
        # Socket callbacks timestamp before enqueue.  Wait a tiny bounded
        # interval before freezing each as-of sample so a Spot event received
        # just before the deadline cannot lose to the scheduler merely because
        # another callback enqueued first.
        self.m7_event_reorder_grace_seconds = M7_EVENT_REORDER_GRACE_SECONDS
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.status = "STANDBY"
        self.error: str | None = None
        self.market_id: int | None = None
        self.market_start_monotonic_ns: int | None = None
        self.market_end_monotonic_ns: int | None = None
        self.market_clock_mapped_wall_ns: int | None = None
        self.market_clock_mapped_monotonic_ns: int | None = None
        self.market_clock_offset_ms: float | None = None
        self.spot_event: dict[str, Any] | None = None
        self.spot_book_event: dict[str, Any] | None = None
        self.spot_trade_ingress_received_ns = 0
        self.spot_trade_processed_received_ns = 0
        self.futures_event: dict[str, Any] | None = None
        self.prediction_event: dict[str, Any] | None = None
        # Telemetry only: retain the newest matching Prediction snapshot even
        # when the strategy freshness gate rejects it.  It must never replace
        # prediction_event, which remains the last strategy-eligible book.
        self.latest_rest_prediction_observation: dict[str, Any] | None = None
        self.accepted_prediction_events = 0
        self.rejected_unverified_prediction_events = 0
        self.last_accepted_prediction_at: str | None = None
        self.spot_history: deque[dict[str, Any]] = deque(maxlen=20_000)
        self.prediction_history: deque[dict[str, Any]] = deque(maxlen=10_000)
        self.m7_emitted_deadlines: set[float] = set()
        self.last_trade_id: dict[str, int | None] = {"spot": None, "futures": None}
        self.rejected_trade_replays = 0
        self.processed_events = 0
        self.dropped_events = 0
        # A queue overflow makes event-order strategies unverifiable for the
        # rest of that market.  Keep collecting diagnostics, but do not create
        # paper trades from a sequence with a known hole.
        self.integrity_epoch = 0
        self.market_integrity_epoch = 0
        self.integrity_skipped_evaluations = 0
        self.evaluations = 0
        self.last_event_at: str | None = None
        self.last_decision_at: str | None = None
        self.last_queue_delay_ms: float | None = None
        self.last_decision_duration_ms: float | None = None
        self.last_mx_duration_ms: float | None = None
        self.last_observer_gate_duration_ms: float | None = None
        self.last_store_duration_ms: float | None = None
        self.event_times: deque[int] = deque(maxlen=20_000)
        self.evaluation_horizon_seconds = 300.0
        self.direction_evaluation_horizon_seconds = M6_CANONICAL_CAPTURE_SECONDS
        self.horizon_refresh_after_ns = 0
        self.m01o_gate_enabled = False
        self.m01o_f2_enabled = False
        self.m01o_f1_enabled = False
        self.m01o_live_enabled = False
        self.m01o_min_observer_samples = 6
        self.m01o_min_current_range_score = 2
        self.out_of_window_skips = 0
        self.direction_out_of_window_skips = 0

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.status = "STARTING"
        self.thread = threading.Thread(
            target=self._run, name="m-series-realtime", daemon=True
        )
        self.thread.start()

    def submit(self, event: dict[str, Any]) -> None:
        """Non-blocking sink used directly by MicrostructureObserver."""
        source = str(event.get("source") or "")
        stream = str(event.get("stream") or "")
        if not (
            (source == "spot" and stream == "trade")
            or (source == "spot" and stream == "bookTicker")
            or (source == "futures" and stream == "aggTrade")
            or (source == "prediction" and stream == "orderbook")
        ):
            return
        if source == "spot" and stream == "trade":
            received_ns = int(event.get("received_monotonic_ns") or 0)
            if received_ns > 0:
                # Capture ingress before the bounded strategy queue.  This is
                # intentionally independent of processed-event age.
                self.spot_trade_ingress_received_ns = received_ns
        if event.get("market_observer_already_updated") is not True:
            self._update_market_observer(event)
        try:
            self.events.put_nowait(dict(event))
        except queue.Full:
            with self.lock:
                self.dropped_events += 1
                self.integrity_epoch += 1
                self.status = "DEGRADED"

    def _update_market_observer(self, event: dict[str, Any]) -> None:
        """Keep F1 market state current without waiting behind paper DB work."""
        if self.market_observer is None:
            return
        source = str(event.get("source") or "")
        stream = str(event.get("stream") or "")
        if not (
            (source == "spot" and stream == "trade")
            or (source == "spot" and stream == "bookTicker")
            or (source == "prediction" and stream == "orderbook")
        ):
            return
        try:
            market = self.current_market()
            if not market:
                return
            market_id = int(market["market_id"])
            if source == "prediction":
                if (
                    int(event.get("market_id") or -1) != market_id
                    or event.get("feature_eligible") is not True
                ):
                    return
            received_wall_ns = int(
                event.get("received_wall_ns") or time.time_ns()
            )
            server_offset_ms = _finite(
                market.get("server_clock_offset_ms")
            ) or 0.0
            event_server_ms = received_wall_ns / 1_000_000 + server_offset_ms
            if not (
                float(market["start_ms"])
                <= event_server_ms
                <= float(market["end_ms"])
            ):
                return
            self.market_observer.reset_market(
                market_id,
                _finite(market.get("start_price")),
                market_start_ts=float(market["start_ms"]) / 1000.0,
            )
            self.market_observer.update_tick(
                now_ts=received_wall_ns / 1_000_000_000,
                spot_price=(
                    _finite(event.get("price")) if source == "spot" else None
                ),
                # The verified Prediction socket is the UP token.  Refresh
                # only its direct Ask here; DOWN remains sourced from its own
                # REST outcome book and is never complement-inferred.
                up_ask=(
                    _finite(event.get("best_ask"))
                    if source == "prediction"
                    else None
                ),
                market_id=market_id,
                book_age_seconds=(
                    float(event["prediction_book_version_age_ms"]) / 1000.0
                    if source == "prediction"
                    and _finite(event.get("prediction_book_version_age_ms"))
                    is not None
                    else None
                ),
                book_skew_ms=(
                    _finite(event.get("book_skew_ms")) or 0.0
                    if source == "prediction"
                    else None
                ),
                require_verified_book_freshness=(source == "prediction"),
            )
        except Exception:
            # Observer telemetry must never take a market-data socket down.
            return

    def _reset_market(
        self, market: dict[str, Any], anchor_event: dict[str, Any]
    ) -> None:
        market_id = int(market["market_id"])
        self.market_id = market_id
        received_wall_ns = int(anchor_event.get("received_wall_ns") or time.time_ns())
        received_monotonic_ns = int(
            anchor_event.get("received_monotonic_ns") or time.monotonic_ns()
        )
        server_offset_ms = _finite(market.get("server_clock_offset_ms")) or 0.0
        start_ms = float(market["start_ms"])
        end_ms = float(market["end_ms"])
        server_now_ms = received_wall_ns / 1_000_000 + server_offset_ms
        self.market_start_monotonic_ns = int(
            received_monotonic_ns - (server_now_ms - start_ms) * 1_000_000
        )
        self.market_end_monotonic_ns = int(
            self.market_start_monotonic_ns + (end_ms - start_ms) * 1_000_000
        )
        self.market_clock_mapped_wall_ns = received_wall_ns
        self.market_clock_mapped_monotonic_ns = received_monotonic_ns
        self.market_clock_offset_ms = server_offset_ms
        self.market_integrity_epoch = self.integrity_epoch
        # A pre-open Spot/Perpetual tick is not a valid opening signal for the
        # new five-minute round.  Prediction is also invalid until its dynamic
        # subscription has produced a verified snapshot for this market.
        self.spot_event = None
        self.spot_book_event = None
        self.futures_event = None
        self.prediction_event = None
        self.latest_rest_prediction_observation = None
        self.prediction_history.clear()
        # Spot is a continuous public stream and remains timestamp-valid across
        # Prediction market rollovers.  Retain its bounded history so a market
        # record discovered shortly after the boundary can still evaluate each
        # M7 deadline with the last event received *before* that deadline.  The
        # candidate filter below excludes every event before the new start.
        self.m7_emitted_deadlines.clear()
        self._refresh_evaluation_horizon(force=True)
        if self.market_observer:
            self.market_observer.reset_market(
                market_id,
                _finite(market.get("start_price")),
                market_start_ts=float(market["start_ms"]) / 1000.0,
            )

    def _market_integrity_ok(self) -> bool:
        return self.integrity_epoch == self.market_integrity_epoch

    def _refresh_evaluation_horizon(self, *, force: bool = False) -> None:
        """Cache how long this market can still produce an M-family action.

        Events remain fully timestamped and counted after the horizon, but we
        avoid taking Store's SQLite lock hundreds of times per second once all
        opening cohorts are impossible.  The one-second refresh means a live
        config increase still takes effect during the same market.
        """
        now_ns = time.monotonic_ns()
        if not force and now_ns < self.horizon_refresh_after_ns:
            return
        self.horizon_refresh_after_ns = now_ns + 1_000_000_000
        try:
            cfg = self.store.config()
            horizons = [M6_CANONICAL_CAPTURE_SECONDS]
            direction_horizons = [M6_CANONICAL_CAPTURE_SECONDS]
            if bool(cfg.get("strategy_m_enabled")):
                window = float(cfg["strategy_m_entry_window_seconds"])
                horizons.append(window)
                direction_horizons.append(window)
            for suffix in range(7):
                prefix = f"m{suffix}"
                if bool(cfg.get(f"strategy_{prefix}_enabled")):
                    window = float(
                        cfg[f"strategy_{prefix}_entry_window_seconds"]
                    )
                    horizons.append(window)
                    direction_horizons.append(window)
            if bool(cfg.get("strategy_m01_enabled")):
                horizons.append(
                    float(cfg["strategy_m01_entry_window_seconds"])
                )
            self.m01o_f2_enabled = bool(cfg.get("strategy_m01o_enabled"))
            self.m01o_f1_enabled = bool(
                cfg.get("strategy_m01o_f1_enabled")
            )
            self.m01o_live_enabled = bool(
                cfg.get("strategy_m01o_live_enabled")
            )
            self.m01o_gate_enabled = bool(
                self.m01o_f2_enabled
                or self.m01o_f1_enabled
                or self.m01o_live_enabled
            )
            self.m01o_min_observer_samples = int(
                cfg.get("strategy_m01o_min_observer_samples", 6)
            )
            self.m01o_min_current_range_score = int(
                cfg.get("strategy_m01o_min_current_range_score", 2)
            )
            if self.m01o_gate_enabled:
                horizons.append(
                    float(cfg["strategy_m01o_entry_window_seconds"])
                )
            if bool(cfg.get("strategy_m01f_enabled")):
                horizons.append(
                    float(cfg["strategy_m01f_entry_window_seconds"])
                )
            if bool(cfg.get("strategy_m01r_enabled")):
                horizons.append(
                    float(cfg["strategy_m01r_entry_window_seconds"])
                )
            if bool(cfg.get("strategy_m0w_enabled")):
                window = float(cfg["strategy_m0w_entry_window_seconds"])
                horizons.append(window)
                direction_horizons.append(window)
            if bool(cfg.get("strategy_m01w_enabled")):
                horizons.append(
                    float(cfg["strategy_m01w_entry_window_seconds"])
                )
            m7_entry_window = float(cfg["strategy_m7_entry_window_seconds"])
            m7_grace = float(cfg["strategy_m7_execution_grace_seconds"])
            for delay in M7_DEADLINES_SECONDS:
                strategy_key = f"strategy_m7_{int(delay)}_enabled"
                if bool(cfg.get(strategy_key)) and delay <= m7_entry_window:
                    window = delay + m7_grace
                    horizons.append(window)
                    direction_horizons.append(window)

            # Research strategies have their own horizons and must keep the
            # realtime engine evaluating Spot/Futures/Prediction events for
            # the full configured research window.
            for strategy, parameters in RESEARCH_PARAMETERS.items():
                enabled_key = f"strategy_{strategy.lower()}_enabled"

                if not bool(cfg.get(enabled_key)):
                    continue

                try:
                    research_horizon = float(parameters.get("horizon", 0.0))
                except (TypeError, ValueError):
                    continue

                if research_horizon <= 0:
                    continue

                horizons.append(research_horizon)

                # Research strategies such as R_FUTURES_LEAD need Spot/Futures
                # direction events throughout the complete horizon, not only
                # subsequent Prediction-book execution events.
                direction_horizons.append(research_horizon)
            self.evaluation_horizon_seconds = max(
                0.0, min(300.0, max(horizons))
            )
            self.direction_evaluation_horizon_seconds = max(
                0.0, min(300.0, max(direction_horizons))
            )
        except Exception:
            # Fail open on configuration lookup: correctness is more important
            # than this optimization.
            self.evaluation_horizon_seconds = 300.0
            self.direction_evaluation_horizon_seconds = 300.0

    @staticmethod
    def _event_sequence(event: dict[str, Any]) -> str:
        source = str(event.get("source") or "unknown")
        stream = str(event.get("stream") or "unknown")
        trade_id = event.get("trade_id")
        if trade_id is not None:
            return f"{source}:{stream}:trade:{trade_id}"
        update_id = event.get("update_id")
        if update_id is not None:
            market = event.get("market_id")
            return f"{source}:{stream}:market:{market}:update:{update_id}"
        return (
            f"{source}:{stream}:session:{event.get('session_id') or 'unknown'}:"
            f"recv:{event.get('received_monotonic_ns') or 0}"
        )

    @staticmethod
    def _event_price(event: dict[str, Any] | None) -> float | None:
        if not event:
            return None
        value = _finite(event.get("price"))
        if value is None and str(event.get("stream") or "") == "bookTicker":
            bid = _finite(event.get("best_bid"))
            ask = _finite(event.get("best_ask"))
            if bid is not None and ask is not None and 0 < bid <= ask:
                value = (bid + ask) / 2.0
        return value if value is not None and value > 0 else None

    @classmethod
    def _event_age_ms(cls, asof_ns: int, event: dict[str, Any] | None) -> float | None:
        received_ns = int((event or {}).get("received_monotonic_ns") or 0)
        if received_ns <= 0 or received_ns > asof_ns:
            return None
        return max(0.0, (asof_ns - received_ns) / 1_000_000)

    def _spot_signal_event(self, asof_ns: int) -> tuple[dict[str, Any] | None, str | None]:
        """Return trade first, then an explicit fresh bookTicker midpoint.

        No futures aggTrade or opaque last-value cache is consulted here.  A
        later fallback can be evaluated only after queue blockage is proven;
        this path remains limited to the independent Spot book socket.
        """
        trade = self.spot_event
        if self._event_price(trade) is not None:
            age = self._event_age_ms(asof_ns, trade)
            if age is not None and age <= SPOT_DATA_MAX_AGE_MS:
                return trade, "trade"
        book = self.spot_book_event
        if self._event_price(book) is not None:
            age = self._event_age_ms(asof_ns, book)
            if age is not None and age <= SPOT_DATA_MAX_AGE_MS:
                return book, "bookTicker_midpoint"
        return None, None

    def _prediction_values(
        self,
        event: dict[str, Any] | None,
        now_mono_ns: int,
        *,
        require_feature_eligible: bool = True,
    ) -> dict[str, float] | None:
        if not event or (
            require_feature_eligible
            and event.get("feature_eligible") is not True
        ):
            return None
        up_bid = _finite(event.get("best_bid"))
        up_ask = _finite(event.get("best_ask"))
        up_bid_size = _finite(event.get("best_bid_qty"))
        up_ask_size = _finite(event.get("best_ask_qty"))
        received_mono_ns = int(event.get("received_monotonic_ns") or 0)
        if (
            None in (up_bid, up_ask, up_bid_size, up_ask_size)
            or received_mono_ns <= 0
        ):
            return None
        assert up_bid is not None and up_ask is not None
        assert up_bid_size is not None and up_ask_size is not None
        if not (
            0 <= up_bid <= up_ask <= 1
            and up_bid_size > 0
            and up_ask_size > 0
        ):
            return None
        local_receipt_age_ms = max(
            0.0, (now_mono_ns - received_mono_ns) / 1_000_000
        )
        source_age_ms = _finite(
            event.get(
                "book_age_ms",
                event.get("prediction_book_version_age_ms"),
            )
        )
        up_source_content_age_ms = _finite(
            event.get("up_book_content_age_ms")
        )
        down_source_content_age_ms = _finite(
            event.get("down_book_content_age_ms")
        )
        if up_source_content_age_ms is None:
            up_source_content_age_ms = source_age_ms
        if down_source_content_age_ms is None:
            down_source_content_age_ms = source_age_ms
        up_content_age_ms = (
            up_source_content_age_ms + local_receipt_age_ms
            if up_source_content_age_ms is not None
            else None
        )
        down_content_age_ms = (
            down_source_content_age_ms + local_receipt_age_ms
            if down_source_content_age_ms is not None
            else None
        )
        effective_content_ages = [
            age
            for age in (up_content_age_ms, down_content_age_ms)
            if age is not None
        ]
        book_age_ms = (
            max(local_receipt_age_ms, *effective_content_ages)
            if effective_content_ages
            else local_receipt_age_ms
        )
        if event.get("direct_outcome_books") is True:
            down_bid = _finite(event.get("direct_down_bid"))
            down_ask = _finite(event.get("direct_down_ask"))
            down_bid_size = _finite(event.get("direct_down_bid_qty"))
            down_ask_size = _finite(event.get("direct_down_ask_qty"))
            if (
                None in (down_bid, down_ask, down_bid_size, down_ask_size)
                or not 0 <= down_bid <= down_ask <= 1
                or down_bid_size <= 0
                or down_ask_size <= 0
            ):
                return None
            assert down_bid is not None and down_ask is not None
            assert down_bid_size is not None and down_ask_size is not None
            book_skew_ms = _finite(event.get("book_skew_ms"))
            book_timestamp_ms = _finite(event.get("prediction_book_version_ms"))
        else:
            # Legacy WSS books contain one verified UP book.  Derive the
            # opposite token only for diagnostics; stale WSS frames are now
            # rejected before reaching this engine.
            down_bid = 1.0 - up_ask
            down_ask = 1.0 - up_bid
            down_bid_size = up_ask_size
            down_ask_size = up_bid_size
            book_skew_ms = 0.0
            book_timestamp_ms = _finite(event.get("prediction_book_version_ms"))
        return {
            "up_bid": up_bid,
            "up_ask": up_ask,
            "up_bid_size": up_bid_size,
            "up_ask_size": up_ask_size,
            "down_bid": down_bid,
            "down_ask": down_ask,
            "down_bid_size": down_bid_size,
            "down_ask_size": down_ask_size,
            "book_age_ms": book_age_ms,
            "effective_book_age_ms": book_age_ms,
            "content_version_age_ms": book_age_ms,
            "transport_receipt_age_ms": local_receipt_age_ms,
            "up_book_content_age_ms": up_content_age_ms,
            "down_book_content_age_ms": down_content_age_ms,
            "book_skew_ms": book_skew_ms,
            "book_timestamp_ms": book_timestamp_ms,
        }

    def _snapshot(
        self,
        *,
        market: dict[str, Any],
        trigger_event: dict[str, Any] | None,
        now_mono_ns: int,
        now_wall_ns: int,
        spot_event_override: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        trigger = trigger_event or {}
        # A replay caused by cross-socket ordering must use that immutable
        # Prediction frame, not whichever newer book most recently replaced
        # self.prediction_event.  Otherwise M7 silently fills on the last book
        # seen during the reorder grace instead of the first post-deadline one.
        prediction_event = (
            trigger
            if str(trigger.get("source") or "") == "prediction"
            and str(trigger.get("stream") or "") == "orderbook"
            else (self.prediction_event or {})
        )
        prediction = self._prediction_values(prediction_event, now_mono_ns) or {}
        start_price = _finite(market.get("start_price"))
        server_offset_ms = _finite(market.get("server_clock_offset_ms")) or 0.0
        if (
            start_price is None
            or start_price <= 0
            or self.market_start_monotonic_ns is None
            or self.market_end_monotonic_ns is None
        ):
            return None
        trigger_received_mono_ns = int(trigger.get("received_monotonic_ns") or now_mono_ns)
        trigger_received_wall_ns = int(trigger.get("received_wall_ns") or now_wall_ns)
        signal_sequence = self._event_sequence(trigger)
        signal_spot_event, spot_price_source = (
            (spot_event_override, "trade")
            if spot_event_override is not None
            else self._spot_signal_event(trigger_received_mono_ns)
        )
        spot_price = self._event_price(signal_spot_event)
        spot_received_mono_ns = int(
            (signal_spot_event or {}).get("received_monotonic_ns") or 0
        )
        futures_price = self._event_price(self.futures_event)
        futures_received_mono_ns = int(
            (self.futures_event or {}).get("received_monotonic_ns") or 0
        )
        snapshot: dict[str, Any] = {
            "timestamp": _utc_iso_from_ns(trigger_received_wall_ns),
            "timestamp_ns": trigger_received_wall_ns,
            "topic_id": int(market["topic_id"]),
            "market_id": int(market["market_id"]),
            "title": str(market.get("title") or "BTC Up or Down 5m"),
            "market_start_ms": int(market["start_ms"]),
            "market_end_ms": int(market["end_ms"]),
            "start_price": start_price,
            "spot_price": spot_price,
            "spot_age_ms": (
                max(
                    0.0,
                    (trigger_received_mono_ns - spot_received_mono_ns) / 1_000_000,
                )
                if spot_received_mono_ns
                else None
            ),
            "spot_price_source": spot_price_source,
            "spot_trade_ingress_age_ms": self._event_age_ms(
                trigger_received_mono_ns,
                {"received_monotonic_ns": self.spot_trade_ingress_received_ns},
            ),
            "spot_trade_processed_age_ms": self._event_age_ms(
                trigger_received_mono_ns,
                {"received_monotonic_ns": self.spot_trade_processed_received_ns},
            ),
            "futures_price": futures_price,
            "futures_timestamp_ms": (self.futures_event or {}).get(
                "exchange_trade_ms"
            ),
            "futures_age_ms": (
                max(
                    0.0,
                    (trigger_received_mono_ns - futures_received_mono_ns)
                    / 1_000_000,
                )
                if futures_received_mono_ns
                else None
            ),
            "futures_agg_trade_id": (self.futures_event or {}).get("trade_id"),
            "seconds_left": max(
                0.0,
                (self.market_end_monotonic_ns - trigger_received_mono_ns)
                / 1_000_000_000,
            ),
            "market_elapsed_seconds": max(
                0.0,
                (trigger_received_mono_ns - self.market_start_monotonic_ns)
                / 1_000_000_000,
            ),
            "up_ask": prediction.get("up_ask"),
            "up_bid": prediction.get("up_bid"),
            "down_ask": prediction.get("down_ask"),
            "down_bid": prediction.get("down_bid"),
            "up_ask_size": prediction.get("up_ask_size"),
            "up_bid_size": prediction.get("up_bid_size"),
            "down_ask_size": prediction.get("down_ask_size"),
            "down_bid_size": prediction.get("down_bid_size"),
            "book_skew_ms": prediction.get("book_skew_ms"),
            "up_book_timestamp_ms": prediction.get("book_timestamp_ms"),
            "down_book_timestamp_ms": prediction.get("book_timestamp_ms"),
            "book_age_ms": prediction.get("book_age_ms"),
            "signal_event_source": str(trigger.get("source") or "scheduler"),
            "signal_event_stream": str(trigger.get("stream") or "timer"),
            "signal_event_sequence": signal_sequence,
            "signal_exchange_event_ms": trigger.get("exchange_event_ms"),
            "signal_prediction_book_version_ms": trigger.get(
                "prediction_book_version_ms"
            ),
            "signal_exchange_trade_ms": trigger.get("exchange_trade_ms"),
            "signal_received_wall_ns": trigger_received_wall_ns,
            "signal_received_monotonic_ns": trigger_received_mono_ns,
            "received_wall_ns": trigger_received_wall_ns,
            "received_monotonic_ns": trigger_received_mono_ns,
            "spot_event_sequence": self._event_sequence(signal_spot_event)
            if signal_spot_event
            else None,
            "futures_event_sequence": self._event_sequence(self.futures_event)
            if self.futures_event
            else None,
        }
        context = {
            "mode": "websocket_event_driven_paper",
            "paper_only": True,
            "trigger_source": snapshot["signal_event_source"],
            "trigger_stream": snapshot["signal_event_stream"],
            "signal_event_type": snapshot["signal_event_source"],
            # Direction events only freeze/update a signal.  A simulated fill
            # is eligible on the first subsequent verified Prediction book,
            # preventing use of liquidity that existed before the signal.
            "execution_eligible": bool(
                snapshot["signal_event_source"] == "prediction"
                and prediction
                and int(prediction_event.get("received_monotonic_ns") or -1)
                == trigger_received_mono_ns
            ),
            "signal_event_sequence": signal_sequence,
            "signal_exchange_event_ms": snapshot["signal_exchange_event_ms"],
            "signal_exchange_trade_ms": snapshot["signal_exchange_trade_ms"],
            "signal_received_wall_ns": trigger_received_wall_ns,
            "signal_received_monotonic_ns": trigger_received_mono_ns,
            "decision_started_wall_ns": now_wall_ns,
            "decision_started_monotonic_ns": now_mono_ns,
            "queue_delay_ms": max(
                0.0, (now_mono_ns - trigger_received_mono_ns) / 1_000_000
            ),
            "prediction_received_wall_ns": prediction_event.get("received_wall_ns"),
            "prediction_received_monotonic_ns": prediction_event.get(
                "received_monotonic_ns"
            ),
            "prediction_book_received_wall_ns": prediction_event.get(
                "received_wall_ns"
            ),
            "prediction_book_received_monotonic_ns": prediction_event.get(
                "received_monotonic_ns"
            ),
            "prediction_exchange_event_ms": prediction_event.get("exchange_event_ms"),
            "prediction_book_version_ms": prediction_event.get(
                "prediction_book_version_ms"
            ),
            "prediction_book_age_ms": prediction.get("book_age_ms"),
            "spot_age_ms": snapshot["spot_age_ms"],
            "spot_price_source": snapshot["spot_price_source"],
            "spot_trade_ingress_age_ms": snapshot["spot_trade_ingress_age_ms"],
            "spot_trade_processed_age_ms": snapshot["spot_trade_processed_age_ms"],
            "futures_age_ms": snapshot["futures_age_ms"],
            "prediction_book_orientation": prediction_event.get(
                "prediction_orientation"
            ),
            "prediction_data_source": (
                prediction_event.get("prediction_data_source") or "websocket"
            ),
            "prediction_sampling_mode": (
                prediction_event.get("prediction_sampling_mode") or "event_stream"
            ),
            "clock_resolution": "nanosecond_capture_millisecond_metrics",
            "server_clock_offset_ms": server_offset_ms,
            "market_start_monotonic_ns": self.market_start_monotonic_ns,
            "market_end_monotonic_ns": self.market_end_monotonic_ns,
            "market_elapsed_seconds": snapshot["market_elapsed_seconds"],
            "integrity_epoch": self.integrity_epoch,
            "market_data_integrity_ok": self._market_integrity_ok(),
        }
        for key in (
            "m7_deadline_seconds",
            "m7_deadline_monotonic_ns",
            "m7_scheduler_lateness_ms",
            "m7_asof_spot_received_monotonic_ns",
            "m7_asof_spot_event_sequence",
            "m7_asof_spot_age_ms",
        ):
            if trigger.get(key) is not None:
                context[key] = trigger[key]
        return snapshot, context

    def _evaluate(
        self,
        market: dict[str, Any],
        trigger_event: dict[str, Any] | None,
        spot_event_override: dict[str, Any] | None = None,
    ) -> None:
        if not self._market_integrity_ok():
            with self.lock:
                self.integrity_skipped_evaluations += 1
            return
        started_mono_ns = time.monotonic_ns()
        started_wall_ns = time.time_ns()
        built = self._snapshot(
            market=market,
            trigger_event=trigger_event,
            now_mono_ns=started_mono_ns,
            now_wall_ns=started_wall_ns,
            spot_event_override=spot_event_override,
        )
        if built is None:
            return
        snapshot, context = built
        # Store owns the configurable per-strategy windows (up to the full
        # five-minute market); this transport layer must not silently impose a
        # shorter fixed cap.
        elapsed = 300.0 - float(snapshot["seconds_left"])
        if elapsed < 0 or elapsed > 300:
            return
        # MX exit branches share M's causal Spot signal but must keep observing
        # Spot and Prediction events for the entire market.  Run their compact
        # state machine before the original M opening-horizon optimization.
        mx_processor = getattr(self.store, "process_mx_event", None)
        mx_started_ns = time.monotonic_ns()
        if callable(mx_processor):
            mx_processor(
                snapshot,
                int(market.get("fee_bps") or 0),
                realtime_context=context,
            )
        mx_finished_ns = time.monotonic_ns()
        if elapsed > self.evaluation_horizon_seconds:
            self._refresh_evaluation_horizon()
        if elapsed > self.evaluation_horizon_seconds:
            finished_mono_ns = time.monotonic_ns()
            with self.lock:
                self.evaluations += 1
                self.out_of_window_skips += 1
                self.last_decision_at = _utc_iso_from_ns(time.time_ns())
                self.last_queue_delay_ms = float(context["queue_delay_ms"])
                self.last_decision_duration_ms = max(
                    0.0, (finished_mono_ns - started_mono_ns) / 1_000_000
                )
            return
        # Long-window M01-family entries are executable only on Prediction
        # books.  Once every Spot/Futures/M7 opening window has elapsed, these
        # high-rate direction events still feed MX above and update the local
        # caches, but must not wait on Store merely to take the same immediate
        # no-op branch hundreds of times per second.
        if (
            context.get("trigger_source") != "prediction"
            and elapsed > self.direction_evaluation_horizon_seconds
        ):
            finished_mono_ns = time.monotonic_ns()
            with self.lock:
                self.evaluations += 1
                self.direction_out_of_window_skips += 1
                self.last_decision_at = _utc_iso_from_ns(time.time_ns())
                self.last_queue_delay_ms = float(context["queue_delay_ms"])
                self.last_decision_duration_ms = max(
                    0.0, (finished_mono_ns - started_mono_ns) / 1_000_000
                )
                self.last_mx_duration_ms = max(
                    0.0, (mx_finished_ns - mx_started_ns) / 1_000_000
                )
                self.last_observer_gate_duration_ms = 0.0
                self.last_store_duration_ms = 0.0
            return
        gate_started_ns = time.monotonic_ns()
        if context.get("trigger_source") == "prediction":
            unavailable_gates = {
                profile: {
                    "allowed": False,
                    "status": "BLOCK",
                    "profile": profile,
                    "blockCategory": "MISSING_OR_STALE",
                    "reason": "市場狀況觀測器未啟動",
                    "paperOnly": True,
                    "liveOrdersAffected": False,
                }
                for profile in ("F2", "F1", "LIVE")
            }
            if self.market_observer is None:
                context["m01o_observer_gates"] = unavailable_gates
            else:
                try:
                    gate_builder = getattr(
                        self.market_observer, "m01o_entry_gates", None
                    )
                    if callable(gate_builder):
                        gates = gate_builder(
                            min_settled_samples=self.m01o_min_observer_samples,
                            min_current_range_score=(
                                self.m01o_min_current_range_score
                            ),
                        )
                    else:
                        strict_gate = self.market_observer.m01o_entry_gate(
                            min_settled_samples=self.m01o_min_observer_samples,
                            min_current_range_score=(
                                self.m01o_min_current_range_score
                            ),
                        )
                        gates = {**unavailable_gates, "F2": strict_gate}
                    context["m01o_observer_gates"] = gates
                except Exception as exc:
                    context["m01o_observer_gates"] = {
                        profile: {
                            **gate,
                            "reason": f"市場狀況觀測器門檻錯誤：{exc}",
                        }
                        for profile, gate in unavailable_gates.items()
                    }
            context["m01o_observer_gate"] = context[
                "m01o_observer_gates"
            ]["F2"]
        gate_finished_ns = time.monotonic_ns()
        store_started_ns = time.monotonic_ns()
        opened = self.store.maybe_enter_m_series(
            snapshot,
            int(market.get("fee_bps") or 0),
            realtime_context=context,
        )
        store_finished_ns = time.monotonic_ns()
        if (
            self.live_signal_sink is not None
            and context.get("execution_eligible") is True
        ):
            for candidate in opened or []:
                candidate_created_ns = time.monotonic_ns()
                candidate_side = str(candidate.get("side") or "").upper()
                signal_ask_key = (
                    "up_ask" if candidate_side == "UP" else "down_ask"
                )
                signal_ask_size_key = (
                    "up_ask_size" if candidate_side == "UP" else "down_ask_size"
                )
                signal_bid_key = (
                    "up_bid" if candidate_side == "UP" else "down_bid"
                )
                candidate = {
                    **candidate,
                    # Preserve every causal boundary through the live queue.
                    # These monotonic values are process-local telemetry only;
                    # they must never be replaced by exchange wall clocks.
                    "market_event_received_monotonic_ns": context.get(
                        "signal_received_monotonic_ns"
                    ),
                    "strategy_decision_started_monotonic_ns": context.get(
                        "decision_started_monotonic_ns"
                    ),
                    "strategy_store_started_monotonic_ns": store_started_ns,
                    "strategy_store_finished_monotonic_ns": store_finished_ns,
                    "live_candidate_created_monotonic_ns": candidate_created_ns,
                    # Freeze the exact Prediction book which produced this
                    # decision.  The live executor may compare it with a newer
                    # verified book, but must never mutate this signal copy.
                    "signal_prediction_book_age_ms": snapshot.get("book_age_ms"),
                    "signal_prediction_received_monotonic_ns": context.get(
                        "prediction_received_monotonic_ns"
                    ),
                    "signal_prediction_ask": snapshot.get(signal_ask_key),
                    "signal_prediction_ask_size": snapshot.get(
                        signal_ask_size_key
                    ),
                    "signal_prediction_bid": snapshot.get(signal_bid_key),
                    "signal_spot_age_ms": snapshot.get("spot_age_ms"),
                    "signal_spot_price_source": snapshot.get("spot_price_source"),
                    "signal_spot_trade_ingress_age_ms": context.get(
                        "spot_trade_ingress_age_ms"
                    ),
                    "signal_spot_trade_processed_age_ms": context.get(
                        "spot_trade_processed_age_ms"
                    ),
                    "signal_prediction_orientation": context.get(
                        "prediction_book_orientation"
                    ),
                    "signal_market_data_integrity_ok": context.get(
                        "market_data_integrity_ok"
                    ),
                    "signal_event_sequence": context.get("signal_event_sequence"),
                    # Freeze the causal BTC snapshot used by the drawdown
                    # controller.  Prediction entry_price is a token price and
                    # must never be substituted for Spot here.
                    "drawdown_control_start_price": snapshot.get("start_price"),
                    "drawdown_control_spot_price": snapshot.get("spot_price"),
                    "drawdown_control_spot_age_ms": snapshot.get("spot_age_ms"),
                    "drawdown_control_spot_source": snapshot.get(
                        "spot_price_source"
                    ),
                    "drawdown_signal_spot_price": snapshot.get("spot_price"),
                    "drawdown_signal_spot_age_ms": snapshot.get("spot_age_ms"),
                    "drawdown_signal_spot_source": (
                        "SPOT_TRADE"
                        if snapshot.get("spot_price_source") == "trade"
                        else "SPOT_BOOK_MIDPOINT"
                        if snapshot.get("spot_price_source")
                        == "bookTicker_midpoint"
                        else snapshot.get("spot_price_source")
                    ),
                }
                if candidate.get("paper_only") is True:
                    strategy = str(candidate.get("strategy"))
                    if strategy not in LIVE_FORWARDABLE_PAPER_STRATEGIES:
                        continue
                    candidate = {
                        **candidate,
                        "paper_only": False,
                        "live_orders_affected": True,
                        "live_forwarded_from_paper": True,
                    }
                    if strategy in LIVE_FORWARDABLE_OBSERVER_STRATEGIES:
                        # The live executor independently revalidates the
                        # complete F1 gate before obtaining a quote.
                        observer_gate = candidate.get("market_observer_gate")
                        candidate = {
                            **candidate,
                            "observer_gate_required": True,
                            "market_observer_gate": (
                                {
                                    **observer_gate,
                                    "paperOnly": False,
                                    "liveOrdersAffected": True,
                                }
                                if isinstance(observer_gate, dict)
                                else observer_gate
                            ),
                        }
                    if strategy in LIVE_OBSERVER_STRATEGIES:
                        observer_gates = context.get("m01o_observer_gates")
                        observer_gate = (
                            observer_gates.get("F1")
                            if isinstance(observer_gates, dict)
                            else None
                        )
                        candidate = {
                            **candidate,
                            "strategy_observer_gate_required": True,
                            "strategy_observer_gate": (
                                {
                                    **observer_gate,
                                    "paperOnly": False,
                                    "liveOrdersAffected": True,
                                }
                                if isinstance(observer_gate, dict)
                                else observer_gate
                            ),
                        }
                    if strategy in FUTURES_LEAD_LIVE_OBSERVER_STRATEGIES:
                        # Keep the old envelope until all callers have moved to
                        # the per-strategy Observer fields above.
                        observer_gate = candidate.get("strategy_observer_gate")
                        candidate = {
                            **candidate,
                            "futures_lead_observer_gate_required": True,
                            "futures_lead_observer_gate": observer_gate,
                        }
                # The live executor owns the persisted strategy selection and
                # performs the final filter again when dequeuing.  Forwarding
                # every opened M-family candidate lets that selection change
                # without restarting the realtime market-data engine.
                self.live_signal_sink(candidate)
        finished_mono_ns = time.monotonic_ns()
        with self.lock:
            self.evaluations += 1
            self.last_decision_at = _utc_iso_from_ns(time.time_ns())
            self.last_queue_delay_ms = float(context["queue_delay_ms"])
            self.last_decision_duration_ms = max(
                0.0, (finished_mono_ns - started_mono_ns) / 1_000_000
            )
            self.last_mx_duration_ms = max(
                0.0, (mx_finished_ns - mx_started_ns) / 1_000_000
            )
            self.last_observer_gate_duration_ms = max(
                0.0, (gate_finished_ns - gate_started_ns) / 1_000_000
            )
            self.last_store_duration_ms = max(
                0.0, (store_finished_ns - store_started_ns) / 1_000_000
            )

    def _prediction_book_copy(
        self,
        event: dict[str, Any],
        market_id: int | None,
        now_ns: int,
        *,
        require_feature_eligible: bool = True,
    ) -> dict[str, Any] | None:
        if not event:
            return None
        received_ns = int(event.get("received_monotonic_ns") or 0)
        orientation = str(event.get("prediction_orientation") or "UNVERIFIED")
        values = self._prediction_values(
            event,
            now_ns,
            require_feature_eligible=require_feature_eligible,
        )
        if values is None or received_ns <= 0:
            return None

        def levels(key: str) -> list[list[float]]:
            result: list[list[float]] = []
            for raw in event.get(key) or []:
                if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                    continue
                price = _finite(raw[0])
                size = _finite(raw[1])
                if price is None or size is None or size <= 0:
                    continue
                result.append([price, size])
            return result

        up_bids = levels("bids")
        up_asks = levels("asks")
        down_asks = (
            levels("down_asks")
            if event.get("direct_outcome_books") is True
            else sorted(
                [[1.0 - price, size] for price, size in up_bids],
                key=lambda level: level[0],
            )
        )
        return {
            "market_id": int(event.get("market_id") or market_id or 0),
            "orientation": orientation,
            "received_monotonic_ns": received_ns,
            "book_age_ms": values["book_age_ms"],
            "effective_book_age_ms": values["effective_book_age_ms"],
            "content_version_age_ms": values["content_version_age_ms"],
            "transport_receipt_age_ms": values["transport_receipt_age_ms"],
            "up_book_content_age_ms": values["up_book_content_age_ms"],
            "down_book_content_age_ms": values["down_book_content_age_ms"],
            "book_skew_ms": values["book_skew_ms"],
            "up_bid": values["up_bid"],
            "up_ask": values["up_ask"],
            "up_bid_size": values["up_bid_size"],
            "up_ask_size": values["up_ask_size"],
            "down_bid": values["down_bid"],
            "down_ask": values["down_ask"],
            "down_bid_size": values["down_bid_size"],
            "down_ask_size": values["down_ask_size"],
            "up_asks": up_asks,
            "down_asks": down_asks,
            "event_sequence": self._event_sequence(event),
            "data_source": event.get("prediction_data_source") or "websocket",
            "verified": orientation in VERIFIED_PREDICTION_ORIENTATIONS,
        }

    def current_verified_prediction_book(self) -> dict[str, Any] | None:
        """Return a fast immutable copy of the latest strategy-eligible book."""
        now_ns = time.monotonic_ns()
        with self.lock:
            event = dict(self.prediction_event or {})
            market_id = self.market_id
        return self._prediction_book_copy(event, market_id, now_ns)

    def current_spot_reference(self) -> dict[str, Any]:
        """Return independent current Spot trade and book reference inputs.

        This method deliberately does not choose a live-trading fallback or
        apply freshness thresholds.  The live executor owns those safety
        rules; this callback only exposes immutable, process-local values and
        monotonic receipt ages from the two independent Spot sockets.
        """
        now_ns = time.monotonic_ns()
        with self.lock:
            trade = dict(self.spot_event or {})
            book = dict(self.spot_book_event or {})

        trade_price = self._event_price(trade)
        trade_age_ms = self._event_age_ms(now_ns, trade)
        book_age_ms = self._event_age_ms(now_ns, book)
        book_bid = _finite(book.get("best_bid"))
        book_ask = _finite(book.get("best_ask"))
        book_bid_size = _finite(book.get("best_bid_qty"))
        book_ask_size = _finite(book.get("best_ask_qty"))
        book_midpoint: float | None = None
        book_microprice: float | None = None
        if (
            book_bid is not None
            and book_ask is not None
            and 0 < book_bid <= book_ask
        ):
            book_midpoint = (book_bid + book_ask) / 2.0
            if (
                book_bid_size is not None
                and book_ask_size is not None
                and book_bid_size > 0
                and book_ask_size > 0
            ):
                total_size = book_bid_size + book_ask_size
                book_microprice = (
                    book_ask * book_bid_size
                    + book_bid * book_ask_size
                ) / total_size
        return {
            "captured_monotonic_ns": now_ns,
            "trade_price": trade_price,
            "trade_age_ms": trade_age_ms,
            "book_bid": book_bid,
            "book_ask": book_ask,
            "book_bid_size": book_bid_size,
            "book_ask_size": book_ask_size,
            "book_midpoint": book_midpoint,
            "book_microprice": book_microprice,
            "book_age_ms": book_age_ms,
        }

    def current_direct_rest_prediction_book(self) -> dict[str, Any] | None:
        """Return the latest independently fetched UP/DOWN REST books.

        Unlike ``current_verified_prediction_book``, this view is not pinned to
        the last strategy-eligible exchange content version.  Pair execution
        uses its local REST receipt age plus its own content-age/skew limits.
        """
        now_ns = time.monotonic_ns()
        with self.lock:
            event = dict(self.latest_rest_prediction_observation or {})
            market_id = self.market_id
        if (
            event.get("prediction_data_source") != "dual_token_rest"
            or event.get("direct_outcome_books") is not True
        ):
            return None
        return self._prediction_book_copy(
            event,
            market_id,
            now_ns,
            require_feature_eligible=False,
        )

    def _emit_due_m7_deadlines(self, now_monotonic_ns: int) -> None:
        if (
            self.market_id is None
            or self.market_start_monotonic_ns is None
            or self.market_clock_mapped_wall_ns is None
            or self.market_clock_mapped_monotonic_ns is None
        ):
            return
        market = self.current_market()
        if not market or int(market["market_id"]) != self.market_id:
            return
        for delay in M7_DEADLINES_SECONDS:
            if delay in self.m7_emitted_deadlines:
                continue
            deadline_ns = self.market_start_monotonic_ns + int(
                delay * 1_000_000_000
            )
            freeze_after_ns = deadline_ns + int(
                self.m7_event_reorder_grace_seconds * 1_000_000_000
            )
            if now_monotonic_ns < freeze_after_ns:
                continue
            # Each absolute deadline is emitted once, even when no as-of Spot
            # event exists.  That variant then honestly records no trade.
            self.m7_emitted_deadlines.add(delay)
            candidates = [
                item
                for item in self.spot_history
                if self.market_start_monotonic_ns
                <= int(item.get("received_monotonic_ns") or 0)
                <= deadline_ns
            ]
            if not candidates:
                continue
            asof_spot = candidates[-1]
            asof_ns = int(asof_spot["received_monotonic_ns"])
            asof_age_ms = max(0.0, (deadline_ns - asof_ns) / 1_000_000)
            # A seconds-old cached trade is not a credible deadline price.
            if asof_age_ms > 1_000.0:
                continue
            deadline_wall_ns = int(
                self.market_clock_mapped_wall_ns
                + (deadline_ns - self.market_clock_mapped_monotonic_ns)
            )
            synthetic = {
                "source": "scheduler",
                "stream": "m7_deadline",
                "received_wall_ns": deadline_wall_ns,
                "received_monotonic_ns": deadline_ns,
                "session_id": f"m7-market-{self.market_id}",
                "update_id": int(delay * 1_000),
                "m7_deadline_seconds": delay,
                "m7_deadline_monotonic_ns": deadline_ns,
                "m7_scheduler_lateness_ms": max(
                    0.0, (now_monotonic_ns - deadline_ns) / 1_000_000
                ),
                "m7_event_reorder_grace_ms": (
                    self.m7_event_reorder_grace_seconds * 1_000
                ),
                "m7_asof_spot_received_monotonic_ns": asof_ns,
                "m7_asof_spot_event_sequence": self._event_sequence(asof_spot),
                "m7_asof_spot_age_ms": asof_age_ms,
            }
            self._evaluate(market, synthetic, spot_event_override=asof_spot)
            # In a cross-socket scheduling race the first post-deadline book
            # may already have been processed.  Replaying that immutable
            # cached event is causal because Store requires its receive time
            # to be strictly later than this frozen deadline.
            cached_prediction = next(
                (
                    item
                    for item in self.prediction_history
                    if int(item.get("received_monotonic_ns") or 0) > deadline_ns
                ),
                None,
            )
            if cached_prediction is not None:
                self._evaluate(market, cached_prediction)

    def _accept_spot_trade(
        self, event: dict[str, Any], *, set_current: bool
    ) -> bool:
        """Deduplicate and retain one continuous Spot trade stream.

        The Prediction market reference can briefly be unavailable at a
        five-minute boundary.  Spot timestamps remain valid during that gap,
        so retain them for the new market's absolute M7 deadlines without
        treating them as the current signal before that market is known.
        """
        trade_id = event.get("trade_id")
        if trade_id is not None:
            trade_id = int(trade_id)
            prior_trade_id = self.last_trade_id["spot"]
            if prior_trade_id is not None and trade_id <= prior_trade_id:
                self.rejected_trade_replays += 1
                return False
            self.last_trade_id["spot"] = trade_id
        if set_current:
            self.spot_event = event
        received_ns = int(event.get("received_monotonic_ns") or 0)
        if received_ns > 0:
            self.spot_trade_processed_received_ns = received_ns
        self.spot_history.append(event)
        return True

    def _handle(self, event: dict[str, Any]) -> None:
        source = str(event.get("source") or "")
        stream = str(event.get("stream") or "")
        event_monotonic_ns = int(event.get("received_monotonic_ns") or 0)
        market = self.current_market()
        if market is None:
            self.status = "WAITING_MARKET"
            if (
                source == "spot"
                and stream == "trade"
                and event_monotonic_ns > 0
            ):
                self._accept_spot_trade(event, set_current=False)
            return
        market_id = int(market["market_id"])
        if self.market_id != market_id:
            self._reset_market(market, event)
        if (
            not event_monotonic_ns
            or self.market_start_monotonic_ns is None
            or event_monotonic_ns < self.market_start_monotonic_ns
        ):
            return
        if source == "prediction" and stream == "orderbook":
            if (
                int(event.get("market_id") or -1) == market_id
                and event.get("prediction_data_source") == "dual_token_rest"
            ):
                self.latest_rest_prediction_observation = event
            if (
                int(event.get("market_id") or -1) == market_id
                and event.get("feature_eligible") is True
            ):
                self.prediction_event = event
                self.prediction_history.append(event)
                self.accepted_prediction_events += 1
                self.last_accepted_prediction_at = _utc_iso_from_ns(
                    int(event.get("received_wall_ns") or time.time_ns())
                )
            else:
                if event.get("feature_eligible") is not True:
                    self.rejected_unverified_prediction_events += 1
                return
        elif source == "spot" and stream == "trade":
            if not self._accept_spot_trade(event, set_current=True):
                return
        elif source == "spot" and stream == "bookTicker":
            if self._event_price(event) is None:
                return
            self.spot_book_event = event
        elif source == "futures" and stream == "aggTrade":
            trade_id = event.get("trade_id")
            if trade_id is not None:
                trade_id = int(trade_id)
                prior_trade_id = self.last_trade_id["futures"]
                if prior_trade_id is not None and trade_id <= prior_trade_id:
                    self.rejected_trade_replays += 1
                    return
                self.last_trade_id["futures"] = trade_id
            self.futures_event = event
        else:
            return
        self._evaluate(market, event)
        # Socket callbacks timestamp before enqueueing.  It is therefore
        # possible for a newer Prediction event to reach this worker before an
        # older Spot/Futures event from another socket.  Re-evaluate that
        # cached book after freezing the external-price signal so the first
        # causally later book is not lost merely due to enqueue order.
        if source in {"spot", "futures"} and self.prediction_event is not None:
            cached_prediction = next(
                (
                    item
                    for item in self.prediction_history
                    if int(item.get("received_monotonic_ns") or 0)
                    > event_monotonic_ns
                ),
                None,
            )
            if cached_prediction is not None:
                self._evaluate(market, cached_prediction)

    def _run(self) -> None:
        self.status = "LIVE"
        while not self.stop_event.is_set():
            event: dict[str, Any] | None
            try:
                event = self.events.get(timeout=self.scheduler_tick_seconds)
            except queue.Empty:
                event = None
            try:
                if event is not None:
                    received_ns = int(event.get("received_monotonic_ns") or 0)
                    with self.lock:
                        self.processed_events += 1
                        self.event_times.append(time.monotonic_ns())
                        self.last_event_at = _utc_iso_from_ns(
                            int(event.get("received_wall_ns") or time.time_ns())
                        )
                        if received_ns:
                            self.last_queue_delay_ms = max(
                                0.0,
                                (time.monotonic_ns() - received_ns) / 1_000_000,
                            )
                    # Emit any exact deadline decisions before processing a
                    # later event, so that event may become the first eligible
                    # post-deadline Prediction fill.
                    self._emit_due_m7_deadlines(received_ns or time.monotonic_ns())
                    self._handle(event)
                    self._emit_due_m7_deadlines(received_ns or time.monotonic_ns())
                else:
                    self._emit_due_m7_deadlines(time.monotonic_ns())
                if event is not None:
                    self.status = (
                        "LIVE" if self._market_integrity_ok() else "DEGRADED"
                    )
                    self.error = None
            except Exception as exc:
                self.status = "ERROR"
                self.error = str(exc)[:240]
                self.stop_event.wait(0.05)
        self.status = "STOPPED"

    def state(self) -> dict[str, Any]:
        now_ns = time.monotonic_ns()
        with self.lock:
            recent = [value for value in self.event_times if value >= now_ns - 5_000_000_000]
            prediction_received = int(
                (self.prediction_event or {}).get("received_monotonic_ns") or 0
            )
            latest_prediction_received = int(
                (self.latest_rest_prediction_observation or {}).get(
                    "received_monotonic_ns"
                )
                or 0
            )
            strategy_book_age_ms = (
                (self._prediction_values(self.prediction_event, now_ns) or {}).get(
                    "book_age_ms"
                )
                if prediction_received
                else None
            )
            latest_prediction_receipt_age_ms = (
                max(0.0, (now_ns - latest_prediction_received) / 1_000_000)
                if latest_prediction_received
                else None
            )
            latest_prediction_source_age_ms = _finite(
                (self.latest_rest_prediction_observation or {}).get(
                    "book_age_ms",
                    (self.latest_rest_prediction_observation or {}).get(
                        "prediction_book_version_age_ms"
                    ),
                )
            )
            latest_prediction_content_age_ms = (
                latest_prediction_source_age_ms + latest_prediction_receipt_age_ms
                if latest_prediction_source_age_ms is not None
                and latest_prediction_receipt_age_ms is not None
                else None
            )
            spot_signal_event, spot_price_source = self._spot_signal_event(now_ns)
            spot_signal_age_ms = self._event_age_ms(now_ns, spot_signal_event)
            spot_trade_ingress_age_ms = self._event_age_ms(
                now_ns,
                {"received_monotonic_ns": self.spot_trade_ingress_received_ns},
            )
            spot_trade_processed_age_ms = self._event_age_ms(
                now_ns,
                {"received_monotonic_ns": self.spot_trade_processed_received_ns},
            )
            spot_trade_age_ms = self._event_age_ms(now_ns, self.spot_event)
            spot_book_age_ms = self._event_age_ms(now_ns, self.spot_book_event)
            futures_received = int(
                (self.futures_event or {}).get("received_monotonic_ns") or 0
            )
            return {
                "status": self.status,
                "mode": "HYBRID_WS_WITH_DIRECT_REST_PREDICTION",
                "paperOnly": True,
                "marketId": self.market_id,
                "eventRate": len(recent) / 5.0,
                "queueDepth": self.events.qsize(),
                "processedEvents": self.processed_events,
                "droppedEvents": self.dropped_events,
                "acceptedPredictionEvents": self.accepted_prediction_events,
                "rejectedUnverifiedPredictionEvents": (
                    self.rejected_unverified_prediction_events
                ),
                "lastAcceptedPredictionAt": self.last_accepted_prediction_at,
                "marketDataIntegrityOk": self._market_integrity_ok(),
                "integrityEpoch": self.integrity_epoch,
                "integritySkippedEvaluations": self.integrity_skipped_evaluations,
                "evaluations": self.evaluations,
                "lastEventAt": self.last_event_at,
                "lastDecisionAt": self.last_decision_at,
                "lastQueueDelayMs": self.last_queue_delay_ms,
                "lastDecisionDurationMs": self.last_decision_duration_ms,
                "lastMxDurationMs": self.last_mx_duration_ms,
                "lastObserverGateDurationMs": (
                    self.last_observer_gate_duration_ms
                ),
                "lastStoreDurationMs": self.last_store_duration_ms,
                "spotAgeMs": (
                    spot_signal_age_ms
                ),
                "spotPriceSource": spot_price_source,
                "spotTradeAgeMs": spot_trade_age_ms,
                "spotBookAgeMs": spot_book_age_ms,
                "spotTradeIngressAgeMs": spot_trade_ingress_age_ms,
                "spotTradeProcessedAgeMs": spot_trade_processed_age_ms,
                "futuresAgeMs": (
                    max(0.0, (now_ns - futures_received) / 1_000_000)
                    if futures_received
                    else None
                ),
                # Keep predictionBookAgeMs as a compatibility alias for health
                # checks and older dashboard clients.
                "predictionBookAgeMs": strategy_book_age_ms,
                "predictionStrategyBookAgeMs": strategy_book_age_ms,
                "predictionLocalReceiptAgeMs": (
                    max(0.0, (now_ns - prediction_received) / 1_000_000)
                    if prediction_received
                    else None
                ),
                "predictionRestReceiptAgeMs": (
                    latest_prediction_receipt_age_ms
                    if (self.latest_rest_prediction_observation or {}).get(
                        "prediction_data_source"
                    )
                    == "dual_token_rest"
                    else None
                ),
                "predictionExchangeContentAgeMs": (
                    latest_prediction_content_age_ms
                    if (self.latest_rest_prediction_observation or {}).get(
                        "prediction_data_source"
                    )
                    == "dual_token_rest"
                    else None
                ),
                "predictionLatestRestEligible": (
                    (self.latest_rest_prediction_observation or {}).get(
                        "feature_eligible"
                    )
                    if (self.latest_rest_prediction_observation or {}).get(
                        "prediction_data_source"
                    )
                    == "dual_token_rest"
                    else None
                ),
                "predictionDataSource": (
                    (self.prediction_event or {}).get("prediction_data_source")
                    or "websocket"
                ),
                "predictionOrientation": (
                    (self.prediction_event or {}).get("prediction_orientation")
                ),
                "schedulerTickMs": self.scheduler_tick_seconds * 1_000,
                "m7EventReorderGraceMs": self.m7_event_reorder_grace_seconds * 1_000,
                "evaluationHorizonSeconds": self.evaluation_horizon_seconds,
                "directionEvaluationHorizonSeconds": (
                    self.direction_evaluation_horizon_seconds
                ),
                "m01oGateEnabled": self.m01o_gate_enabled,
                "m01oF2Enabled": self.m01o_f2_enabled,
                "m01oF1Enabled": self.m01o_f1_enabled,
                "m01oLiveEnabled": self.m01o_live_enabled,
                "m01oMinObserverSamples": self.m01o_min_observer_samples,
                "m01oMinCurrentRangeScore": self.m01o_min_current_range_score,
                "outOfWindowSkips": self.out_of_window_skips,
                "directionOutOfWindowSkips": (
                    self.direction_out_of_window_skips
                ),
                "marketStartMonotonicNs": self.market_start_monotonic_ns,
                "marketEndMonotonicNs": self.market_end_monotonic_ns,
                "marketClockOffsetMs": self.market_clock_offset_ms,
                "rejectedTradeReplays": self.rejected_trade_replays,
                "error": self.error,
            }

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
