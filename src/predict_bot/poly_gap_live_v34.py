from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .core import BinancePredictionTradingClient
from .poly_gap_live_v32 import (
    EXIT_DEPTH_TOLERANCE_BPS,
    EXIT_NO_FILL_RETRY_COOLDOWN_MS,
    _book_levels,
    _sell_depth,
)
from .poly_gap_live_v33 import PersistedPaperPauseFallbackPolyGapLiveEngine
from .poly_quote_canary import _credential_pair


EXIT_COMMITMENT_WINDOW_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_EXIT_COMMITMENT_WINDOW_MS", "1000")),
)
SELL_TELEMETRY_MAX_AGE_MS = max(
    0,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_SELL_TELEMETRY_MAX_AGE_MS", "100")),
)


class TimedPredictionTradingClient(BinancePredictionTradingClient):
    """Trading client that measures the write request itself, not surrounding logic."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.place_sequence = 0
        self.last_place_timing: dict[str, Any] | None = None

    def place_market_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.place_sequence += 1
        sequence = int(self.place_sequence)
        started_at_ms = base._now_ms()
        started = time.monotonic()
        outcome = "RETURNED"
        error_type: str | None = None
        try:
            return super().place_market_order(*args, **kwargs)
        except Exception as exc:
            outcome = "ERROR"
            error_type = type(exc).__name__
            raise
        finally:
            response_at_ms = base._now_ms()
            self.last_place_timing = {
                "sequence": sequence,
                "quoteId": str(kwargs.get("quote_id") or "") or None,
                "requestStartedAtMs": started_at_ms,
                "responseAtMs": response_at_ms,
                "rttMs": max(0.0, (time.monotonic() - started) * 1000.0),
                "outcome": outcome,
                "errorType": error_type,
            }


def _commitment_policy(
    *,
    direction: str,
    held_side: str,
    elapsed_ms: int,
    window_ms: int,
) -> str:
    """Pure decision helper for a previously confirmed reversal exit."""
    normalized_direction = str(direction or "").upper()
    normalized_held = str(held_side or "").upper()
    if normalized_direction == normalized_held and normalized_held in {"UP", "DOWN"}:
        return "CANCEL_HELD_SIDE"
    if normalized_direction in {"UP", "DOWN"}:
        return "RETRY_OPPOSITE"
    if max(0, int(elapsed_ms)) <= max(0, int(window_ms)):
        return "RETRY_NEUTRAL_COMMITTED"
    return "WAIT_NEUTRAL_EXPIRED"


class ExitCommitmentTelemetryPolyGapLiveEngine(
    PersistedPaperPauseFallbackPolyGapLiveEngine
):
    """V34: committed reversal retries, fresh SELL telemetry and exact place RTT.

    A reversal still needs V12's original 500ms / three distinct receipt signal
    confirmation before the first SELL. After that decision has been made, a
    definite FOK NO_FILL arms a short EXIT commitment window. During that window
    a neutral Poly state does not force another signal debounce; only an explicit
    return to the held direction cancels the committed retry. A still-opposite
    direction may retry even after the neutral commitment window expires.

    Every fresh SELL attempt is paired with a held-side Bid ladder no older than
    SELL_TELEMETRY_MAX_AGE_MS. Refreshing happens before quote acquisition, never
    between signed quote response and place-order. The execution client records
    the exact place request start/response timestamps and monotonic RTT so the
    previous combined quote->order metric can be split into local gap vs Binance
    placement RTT.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._exit_commitments: dict[int, dict[str, Any]] = {}
        self._last_place_timing_v34: dict[str, Any] | None = None
        self._sell_telemetry_refreshes = 0
        self._sell_telemetry_reuses = 0
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        columns = {
            "place_request_start_at_ms": "INTEGER",
            "place_response_at_ms": "INTEGER",
            "quote_response_to_place_start_ms": "INTEGER",
            "place_rtt_ms": "REAL",
            "place_sequence": "INTEGER",
            "book_to_quote_start_ms": "INTEGER",
        }
        with self.db_lock:
            existing = {
                str(row["name"])
                for row in self.db.execute(
                    "PRAGMA table_info(poly_gap_live_execution_attempts)"
                ).fetchall()
            }
            for name, sql_type in columns.items():
                if name not in existing:
                    self.db.execute(
                        f"ALTER TABLE poly_gap_live_execution_attempts ADD COLUMN {name} {sql_type}"
                    )
            self.db.commit()

    def _ensure_clients(self) -> bool:
        with self.lock:
            if (
                isinstance(self.client, TimedPredictionTradingClient)
                and self.metadata_client is not None
                and self.wallet_address
                and self.wallet_id
            ):
                return True

        api_key, api_secret, source = _credential_pair()
        self.credential_source = source
        if not api_key or not api_secret:
            self.last_error = "Binance live credentials unavailable"
            self.status = "CONFIG_REQUIRED"
            return False

        execution: TimedPredictionTradingClient | None = None
        metadata: BinancePredictionTradingClient | None = None
        try:
            execution = TimedPredictionTradingClient(api_key, api_secret)
            metadata = BinancePredictionTradingClient(api_key, api_secret)
            wallets = execution.wallets().get("wallets") or []
            if len(wallets) != 1:
                raise RuntimeError(f"expected one Prediction wallet, got {len(wallets)}")
            wallet_address = str(wallets[0].get("walletAddress") or "")
            wallet_id = str(wallets[0].get("walletId") or "")
            if not wallet_address or not wallet_id:
                raise RuntimeError("Prediction wallet metadata incomplete")
            execution.server_timestamp_ms()
            metadata.server_timestamp_ms()
            with self.lock:
                old_execution = self.client
                old_metadata = self.metadata_client
                self.client = execution
                self.metadata_client = metadata
                self.wallet_address = wallet_address
                self.wallet_id = wallet_id
            for old in (old_execution, old_metadata):
                try:
                    if old is not None and old not in {execution, metadata}:
                        old.close()
                except Exception:
                    pass
            self.last_error = None
            return True
        except Exception as exc:
            for created in (execution, metadata):
                try:
                    if created is not None:
                        created.close()
                except Exception:
                    pass
            self.last_error = str(exc)[:500]
            self.status = "BLOCKED_PREFLIGHT"
            return False

    def _place_sequence(self) -> int:
        with self.lock:
            client = self.client
        if isinstance(client, TimedPredictionTradingClient):
            return int(client.place_sequence)
        return 0

    def _persist_place_timing(
        self,
        *,
        round_id: int,
        action: str,
        before_sequence: int,
    ) -> None:
        with self.lock:
            client = self.client
        if not isinstance(client, TimedPredictionTradingClient):
            return
        timing = dict(client.last_place_timing or {})
        sequence = int(timing.get("sequence") or 0)
        if sequence <= int(before_sequence):
            return

        attempt_id = self._latest_attempt_id(int(round_id), str(action))
        if attempt_id is None:
            return
        with self.db_lock:
            attempt = self.db.execute(
                "SELECT * FROM poly_gap_live_execution_attempts WHERE id=?",
                (int(attempt_id),),
            ).fetchone()
            round_row = self.db.execute(
                "SELECT * FROM poly_gap_live_rounds WHERE id=?",
                (int(round_id),),
            ).fetchone()
        if attempt is None or round_row is None:
            return

        prefix = "entry" if action == "BUY" else "exit"
        quote_completed = round_row[f"{prefix}_quote_completed_at_ms"]
        quote_started = round_row[f"{prefix}_quote_started_at_ms"]
        place_started = int(timing.get("requestStartedAtMs") or 0) or None
        place_response = int(timing.get("responseAtMs") or 0) or None
        if place_started is None:
            return
        # A stale timing record from another placement must never be attached.
        if quote_completed is not None and place_started + 5 < int(quote_completed):
            return

        quote_to_place_start = None
        if quote_completed is not None:
            quote_to_place_start = max(0, place_started - int(quote_completed))
        book_to_quote_start = None
        observed = attempt["book_observed_at_ms"]
        if observed is not None and quote_started is not None:
            book_to_quote_start = max(0, int(quote_started) - int(observed))
        quote_to_place_response = None
        if quote_completed is not None and place_response is not None:
            quote_to_place_response = max(0, place_response - int(quote_completed))

        with self.db_lock:
            self.db.execute(
                """UPDATE poly_gap_live_execution_attempts SET
                       place_request_start_at_ms=?,
                       place_response_at_ms=?,
                       quote_response_to_place_start_ms=?,
                       place_rtt_ms=?,
                       place_sequence=?,
                       book_to_quote_start_ms=?,
                       order_response_at_ms=COALESCE(?,order_response_at_ms),
                       quote_response_to_order_response_ms=COALESCE(?,quote_response_to_order_response_ms)
                     WHERE id=?""",
                (
                    place_started,
                    place_response,
                    quote_to_place_start,
                    base._finite(timing.get("rttMs")),
                    sequence,
                    book_to_quote_start,
                    place_response,
                    quote_to_place_response,
                    int(attempt_id),
                ),
            )
            self.db.commit()

        self._last_place_timing_v34 = {
            "roundId": int(round_id),
            "action": str(action),
            "attemptId": int(attempt_id),
            "placeSequence": sequence,
            "bookToQuoteStartMs": book_to_quote_start,
            "quoteResponseToPlaceStartMs": quote_to_place_start,
            "placeRttMs": base._finite(timing.get("rttMs")),
            "quoteResponseToPlaceResponseMs": quote_to_place_response,
            "placeRequestStartedAtMs": place_started,
            "placeResponseAtMs": place_response,
            "placeOutcome": timing.get("outcome"),
            "placeErrorType": timing.get("errorType"),
        }

    def _refresh_sell_depth_for_attempt(self, row: dict[str, Any]) -> dict[str, Any]:
        now_ms = base._now_ms()
        cached = self._last_exit_depth_snapshot or {}
        cached_age = now_ms - int(cached.get("observedAtMs") or 0)
        if (
            int(cached.get("roundId") or -1) == int(row["id"])
            and cached_age >= 0
            and cached_age <= SELL_TELEMETRY_MAX_AGE_MS
        ):
            reused = dict(cached)
            reused["telemetryAgeAtAttemptMs"] = cached_age
            reused["freshForExecutionAttempt"] = True
            reused["freshnessSource"] = "RECENT_HELD_SIDE_BOOK_V34"
            self._last_exit_depth_snapshot = reused
            self._sell_telemetry_reuses += 1
            return reused

        with self.lock:
            client = self.client
        if client is None:
            snapshot = {
                "marketId": int(row["market_id"]),
                "roundId": int(row["id"]),
                "side": str(row["side"]),
                "observedAtMs": now_ms,
                "available": False,
                "freshForExecutionAttempt": False,
                "freshnessSource": "CLIENT_UNAVAILABLE_V34",
            }
            self._last_exit_depth_snapshot = snapshot
            return snapshot

        started = time.monotonic()
        try:
            book = client.orderbook(int(row["market_id"]), str(row["token_id"]))
        except Exception as exc:
            snapshot = {
                "marketId": int(row["market_id"]),
                "roundId": int(row["id"]),
                "side": str(row["side"]),
                "observedAtMs": base._now_ms(),
                "available": False,
                "error": str(exc)[:300],
                "freshForExecutionAttempt": False,
                "freshnessSource": "DIRECT_BOOK_REFRESH_FAILED_V34",
            }
            self._last_exit_depth_snapshot = snapshot
            return snapshot

        rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        observed_ms = base._now_ms()
        self.last_binance_book_at = time.monotonic()
        levels = _book_levels(book, "bids")
        bid = levels[0][0] if levels else None
        bid_size = levels[0][1] if levels else None
        required_shares = base._finite(row.get("shares")) or 0.0
        min_price = (
            max(0.0, bid * (1.0 - EXIT_DEPTH_TOLERANCE_BPS / 10_000.0))
            if bid is not None
            else 0.0
        )
        depth = _sell_depth(
            levels,
            required_shares=required_shares,
            min_price=min_price,
        )
        snapshot = {
            "marketId": int(row["market_id"]),
            "roundId": int(row["id"]),
            "side": str(row["side"]),
            "bid": bid,
            "bidSize": bid_size,
            "bookRttMs": rtt_ms,
            "observedAtMs": observed_ms,
            "minPriceWithinExitTolerance": min_price,
            "exitDepthToleranceBps": EXIT_DEPTH_TOLERANCE_BPS,
            "available": bool(levels),
            "freshForExecutionAttempt": True,
            "freshnessSource": "DIRECT_BOOK_REFRESH_V34",
            "telemetryAgeAtAttemptMs": 0,
            **depth,
        }
        self._last_exit_depth_snapshot = snapshot
        self._last_take_profit_book = dict(snapshot)
        self._last_take_profit_error = None
        self._sell_telemetry_refreshes += 1
        with self.lock:
            existing = dict(self.last_binance or {})
            if (
                int(existing.get("marketId") or 0) != int(row["market_id"])
                or str(existing.get("side") or "") != str(row["side"])
            ):
                existing = {
                    "marketId": int(row["market_id"]),
                    "side": str(row["side"]),
                }
            existing.update(snapshot)
            self.last_binance = existing
        return snapshot

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        before_sequence = self._place_sequence()
        super()._open_round(row, poly)
        self._persist_place_timing(
            round_id=int(row["id"]),
            action="BUY",
            before_sequence=before_sequence,
        )

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        existing_order_id = str(row.get("exit_order_id") or "").strip()
        if not existing_order_id:
            self._refresh_sell_depth_for_attempt(row)
        before_sequence = self._place_sequence()
        super()._exit_round(row, signal_ms)
        self._persist_place_timing(
            round_id=int(row["id"]),
            action="SELL",
            before_sequence=before_sequence,
        )

    def _sync_exit(self, row: dict[str, Any]) -> None:
        before_order_id = str(row.get("exit_order_id") or "").strip()
        super()._sync_exit(row)
        refreshed = self._round_state(int(row["id"])) or row
        attempt_id = self._latest_attempt_id(int(row["id"]), "SELL")
        if attempt_id is not None and before_order_id:
            with self.db_lock:
                self.db.execute(
                    """UPDATE poly_gap_live_execution_attempts
                          SET order_id=CASE
                              WHEN order_id IS NULL OR TRIM(order_id)='' THEN ?
                              ELSE order_id END
                        WHERE id=?""",
                    (before_order_id, int(attempt_id)),
                )
                self.db.commit()
        if str(refreshed.get("state") or "") == "CLOSED":
            self._exit_commitments.pop(int(row["id"]), None)

    def _commitment_for(self, active: dict[str, Any], now_ms: int) -> dict[str, Any]:
        round_id = int(active["id"])
        current = self._exit_commitments.get(round_id)
        if current is not None:
            return current
        started_at = int(active.get("updated_at_ms") or now_ms)
        commitment = {
            "roundId": round_id,
            "marketId": int(active["market_id"]),
            "heldSide": str(active["side"]),
            "startedAtMs": started_at,
            "expiresAtMs": started_at + EXIT_COMMITMENT_WINDOW_MS,
            "windowMs": EXIT_COMMITMENT_WINDOW_MS,
            "reason": "CONFIRMED_REVERSAL_SELL_FOK_NO_FILL",
        }
        self._exit_commitments[round_id] = commitment
        self._event(
            "WARN",
            "EXIT_COMMITMENT_ARMED",
            int(active["market_id"]),
            round_id,
            (
                f"confirmed reversal SELL had no fill; EXIT committed for "
                f"{EXIT_COMMITMENT_WINDOW_MS}ms so neutral Poly does not restart signal debounce"
            ),
        )
        return commitment

    def _fast_exit_retry_if_needed(self) -> bool:
        active = self._current_active_round()
        if not isinstance(active, dict) or str(active.get("state") or "") != "OPEN":
            return False
        if str(active.get("error_kind") or "") != "EXIT_ORDER_NOT_FILLED":
            return False
        if self._exit_intent(int(active["id"])) != "POLY_DIRECTION_FLIP":
            return False

        now_ms = base._now_ms()
        updated_ms = int(active.get("updated_at_ms") or 0)
        remaining = EXIT_NO_FILL_RETRY_COOLDOWN_MS - max(0, now_ms - updated_ms)
        if remaining > 0:
            self.status = "EXIT_NO_FILL_RETRY_COOLDOWN"
            self.last_error = (
                f"confirmed reversal SELL no-fill; execution retry in {remaining}ms"
            )
            return True

        market = self._prime_market()
        if (
            not isinstance(market, dict)
            or int(market.get("market_id") or 0) != int(active["market_id"])
            or now_ms >= int(market.get("end_ms") or 0)
        ):
            return False

        poly = self._poly_state()
        if not isinstance(poly, dict):
            self.status = "EXIT_COMMITMENT_WAITING_FRESH_POLY"
            self.last_error = (
                "confirmed reversal SELL no-fill; waiting for a fresh Poly state before retry"
            )
            return True

        commitment = self._commitment_for(active, now_ms)
        direction = str(poly.get("direction") or "")
        held_side = str(active.get("side") or "")
        elapsed_ms = max(0, now_ms - int(commitment["startedAtMs"]))
        policy = _commitment_policy(
            direction=direction,
            held_side=held_side,
            elapsed_ms=elapsed_ms,
            window_ms=EXIT_COMMITMENT_WINDOW_MS,
        )

        if policy == "CANCEL_HELD_SIDE":
            self._exit_commitments.pop(int(active["id"]), None)
            self._set_exit_intent(int(active["id"]), "")
            self._update_round(int(active["id"]), error_kind=None, error_message=None)
            self._event(
                "INFO",
                "EXIT_COMMITMENT_CANCELLED_HELD_SIDE",
                int(active["market_id"]),
                int(active["id"]),
                "Poly explicitly returned to the held direction after SELL no-fill; committed retry cancelled",
            )
            return False

        if policy == "WAIT_NEUTRAL_EXPIRED":
            self.status = "EXIT_COMMITMENT_EXPIRED_WAITING_OPPOSITE_POLY"
            self.last_error = (
                f"EXIT commitment neutral window expired after {EXIT_COMMITMENT_WINDOW_MS}ms; "
                "waiting for explicit opposite Poly before another SELL"
            )
            return True

        retry_key = (int(active["id"]), updated_ms)
        if self._last_fast_retry_key != retry_key:
            self._last_fast_retry_key = retry_key
            self._fast_exit_retries += 1
            self._event(
                "WARN",
                "EXIT_COMMITMENT_RETRY",
                int(active["market_id"]),
                int(active["id"]),
                (
                    f"previous reversal SELL FOK had no fill; policy={policy}; direction={direction or 'NEUTRAL'}; "
                    f"retrying after {EXIT_NO_FILL_RETRY_COOLDOWN_MS}ms without repeating V12 debounce"
                ),
            )
        signal_ms = int(active.get("exit_signal_at_ms") or now_ms)
        self._exit_round(active, signal_ms)
        return True

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V34"
        active = self._current_active_round()
        active_commitment = None
        if isinstance(active, dict):
            active_commitment = self._exit_commitments.get(int(active["id"]))
        payload["exitCommitmentV34"] = {
            "enabled": True,
            "neutralRetryWindowMs": EXIT_COMMITMENT_WINDOW_MS,
            "noFillRetryCooldownMs": EXIT_NO_FILL_RETRY_COOLDOWN_MS,
            "neutralInsideWindowRetries": True,
            "explicitHeldSideCancels": True,
            "oppositeDirectionRetriesAfterWindow": True,
            "missingFreshPolyStillWaits": True,
            "active": dict(active_commitment or {}),
        }
        payload["executionTimingV34"] = {
            "sellTelemetryMaxAgeMs": SELL_TELEMETRY_MAX_AGE_MS,
            "freshSellTelemetryBeforeNewAttempt": True,
            "extraBookRequestBetweenQuoteAndPlace": False,
            "sellTelemetryRefreshes": int(self._sell_telemetry_refreshes),
            "sellTelemetryRecentReuses": int(self._sell_telemetry_reuses),
            "lastPlaceTiming": dict(self._last_place_timing_v34 or {}),
            "attemptColumns": [
                "book_to_quote_start_ms",
                "quote_response_to_place_start_ms",
                "place_rtt_ms",
                "place_request_start_at_ms",
                "place_response_at_ms",
            ],
        }
        attempts = payload.get("executionDepthV32")
        if isinstance(attempts, dict):
            attempts["sellDepthSnapshotFreshnessMaxMs"] = SELL_TELEMETRY_MAX_AGE_MS
            attempts["precisePlaceRttV34"] = True
            attempts["neutralPolyCanRetryInsideExitCommitment"] = True
        rules = payload.setdefault("rules", {})
        rules.update(
            {
                "exitCommitmentNeutralWindowMs": EXIT_COMMITMENT_WINDOW_MS,
                "exitCommitmentHeldSideCancels": True,
                "freshSellTelemetryBeforeExecutionAttempt": True,
                "exactPlaceRequestRttRecorded": True,
            }
        )
        return payload


base.PolyGapLiveEngine = ExitCommitmentTelemetryPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
