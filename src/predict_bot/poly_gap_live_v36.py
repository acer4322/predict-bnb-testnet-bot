from __future__ import annotations

import os
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v7 import ENTRY_RETRY_COOLDOWN_MS
from .poly_gap_live_v35 import DurableExitCommitmentPolyGapLiveEngine


ENTRY_NO_FILL_RETRY_COOLDOWN_MS = max(
    50,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_NO_FILL_RETRY_COOLDOWN_MS", "75")),
)
ENTRY_NO_FILL_MAX_FAST_RETRIES = max(
    0,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_NO_FILL_MAX_FAST_RETRIES", "2")),
)
ENTRY_NO_FILL_COMMITMENT_WINDOW_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_NO_FILL_COMMITMENT_WINDOW_MS", "1200")),
)


def _entry_retry_policy(
    *,
    direction: str,
    intended_side: str,
    elapsed_ms: int,
    retries_used: int,
    max_retries: int,
    window_ms: int,
) -> str:
    """Pure policy for a BUY whose previous MARKET/FOK was definitively NO_FILL."""
    if max(0, int(elapsed_ms)) > max(0, int(window_ms)):
        return "EXPIRED"
    if max(0, int(retries_used)) >= max(0, int(max_retries)):
        return "EXHAUSTED"
    normalized = str(direction or "").upper()
    intended = str(intended_side or "").upper()
    if normalized in {"UP", "DOWN"} and normalized != intended:
        return "CANCEL_DIRECTION_CHANGED"
    if normalized != intended:
        return "WAIT_FRESH_POLY"
    return "RETRY"


class FastEntryRetryPolyGapLiveEngine(DurableExitCommitmentPolyGapLiveEngine):
    """V36: preserve an informational lead across a definitive BUY FOK no-fill.

    V35 fixed the SELL side. Echtgeld telemetry then showed the BUY side still
    losing otherwise-valid Poly leads because Binance returned definitive FOK
    NO_FILL after a fresh book and signed quote. In those samples local latency
    was already tiny (book->quote ~2ms and quote-response->place ~3-20ms), while
    placement/matching consumed roughly 235-330ms.

    V36 treats *only* a definitive ENTRY_ORDER_NOT_FILLED as an execution
    failure. It keeps the same round/signal alive for a short bounded commitment
    and may obtain a fresh book + fresh signed quote and retry the FOK without
    requiring a new strategy signal/debounce. Safety remains fail-closed:
      - transport/placement ambiguity is never retried;
      - fresh Poly must still explicitly point to the intended side;
      - the inherited entry price/depth guards run again on every retry;
      - the inherited signed-quote checks run again on every retry;
      - retries and commitment lifetime are hard bounded;
      - when the fast-retry budget ends, V7's slower normal rearm cooldown is
        scheduled so a new round cannot bypass the V36 cap in the same tick.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v36_entry_commitment: dict[str, Any] | None = None
        self._v36_retrying_round_id: int | None = None
        self._v36_retry_ordinal = 0
        self._v36_entry_commitments_armed = 0
        self._v36_fast_retry_attempts = 0
        self._v36_fast_retry_submissions = 0
        self._v36_fast_retry_fills = 0
        self._v36_retry_waits_for_book = 0
        self._v36_retry_cancels: dict[str, int] = {}
        self._v36_normal_rearms_scheduled = 0
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        columns = {
            "fast_retry": "INTEGER DEFAULT 0",
            "retry_ordinal": "INTEGER",
            "entry_commitment_started_at_ms": "INTEGER",
            "signal_to_place_start_ms": "INTEGER",
            "signal_to_place_response_ms": "INTEGER",
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

    def _schedule_normal_rearm(self, row: dict[str, Any], reason: str) -> None:
        market_id = int(row.get("market_id") or 0)
        side = str(row.get("side") or "").upper()
        if market_id <= 0 or side not in {"UP", "DOWN"}:
            return
        try:
            cooldown_ms = max(
                ENTRY_RETRY_COOLDOWN_MS,
                int(self._failure_cooldown_ms(row)),
            )
        except Exception:
            cooldown_ms = ENTRY_RETRY_COOLDOWN_MS
        self._schedule_retry(
            (market_id, side),
            cooldown_ms,
            f"V36_{str(reason or 'FAST_RETRY_ENDED')}",
        )
        self._v36_normal_rearms_scheduled += 1

    def _cancel_entry_commitment(self, reason: str, *, event: bool = True) -> None:
        commitment = self._v36_entry_commitment
        if not isinstance(commitment, dict):
            return
        normalized = str(reason or "UNKNOWN")
        self._v36_retry_cancels[normalized] = int(
            self._v36_retry_cancels.get(normalized, 0)
        ) + 1
        if event:
            self._event(
                "INFO",
                "ENTRY_NO_FILL_COMMITMENT_CANCELLED_V36",
                int(commitment.get("marketId") or 0) or None,
                int(commitment.get("roundId") or 0) or None,
                (
                    f"BUY no-fill commitment cancelled; reason={normalized}; "
                    f"retriesUsed={int(commitment.get('retriesUsed') or 0)}"
                ),
            )
        self._v36_entry_commitment = None

    def _arm_or_refresh_entry_commitment(self, row: dict[str, Any]) -> None:
        round_id = int(row["id"])
        now_ms = base._now_ms()
        existing = self._v36_entry_commitment
        if isinstance(existing, dict) and int(existing.get("roundId") or -1) == round_id:
            existing["nextRetryAtMs"] = now_ms + ENTRY_NO_FILL_RETRY_COOLDOWN_MS
            existing["lastNoFillAtMs"] = now_ms
            existing["lastUpdatedAtMs"] = now_ms
            return

        self._v36_entry_commitment = {
            "roundId": round_id,
            "marketId": int(row["market_id"]),
            "side": str(row["side"]),
            "tokenId": str(row.get("token_id") or ""),
            "signalAtMs": int(row.get("entry_signal_at_ms") or now_ms),
            "startedAtMs": now_ms,
            "lastNoFillAtMs": now_ms,
            "lastUpdatedAtMs": now_ms,
            "nextRetryAtMs": now_ms + ENTRY_NO_FILL_RETRY_COOLDOWN_MS,
            "retriesUsed": 0,
        }
        self._v36_entry_commitments_armed += 1
        self._event(
            "WARN",
            "ENTRY_NO_FILL_COMMITMENT_ARMED_V36",
            int(row["market_id"]),
            round_id,
            (
                "BUY MARKET/FOK definitively returned NO_FILL; preserving the same signal "
                f"for up to {ENTRY_NO_FILL_COMMITMENT_WINDOW_MS}ms with at most "
                f"{ENTRY_NO_FILL_MAX_FAST_RETRIES} fast retries"
            ),
        )

    def _persist_place_timing(
        self,
        *,
        round_id: int,
        action: str,
        before_sequence: int,
    ) -> None:
        super()._persist_place_timing(
            round_id=round_id,
            action=action,
            before_sequence=before_sequence,
        )
        if str(action) != "BUY":
            return
        attempt_id = self._latest_attempt_id(int(round_id), "BUY")
        if attempt_id is None:
            return
        with self.db_lock:
            attempt = self.db.execute(
                "SELECT place_request_start_at_ms,place_response_at_ms FROM poly_gap_live_execution_attempts WHERE id=?",
                (int(attempt_id),),
            ).fetchone()
            round_row = self.db.execute(
                "SELECT entry_signal_at_ms FROM poly_gap_live_rounds WHERE id=?",
                (int(round_id),),
            ).fetchone()
        if attempt is None or round_row is None:
            return
        signal_ms = int(round_row["entry_signal_at_ms"] or 0)
        place_start = int(attempt["place_request_start_at_ms"] or 0)
        place_response = int(attempt["place_response_at_ms"] or 0)
        signal_to_place_start = (
            max(0, place_start - signal_ms) if signal_ms > 0 and place_start > 0 else None
        )
        signal_to_place_response = (
            max(0, place_response - signal_ms)
            if signal_ms > 0 and place_response > 0
            else None
        )
        is_retry = int(self._v36_retrying_round_id == int(round_id))
        commitment = self._v36_entry_commitment or {}
        commitment_started = (
            (int(commitment.get("startedAtMs") or 0) or None) if is_retry else None
        )
        retry_ordinal = int(self._v36_retry_ordinal) if is_retry else 0
        with self.db_lock:
            self.db.execute(
                """UPDATE poly_gap_live_execution_attempts SET
                       fast_retry=?,retry_ordinal=?,entry_commitment_started_at_ms=?,
                       signal_to_place_start_ms=?,signal_to_place_response_ms=?
                     WHERE id=?""",
                (
                    is_retry,
                    retry_ordinal,
                    commitment_started,
                    signal_to_place_start,
                    signal_to_place_response,
                    int(attempt_id),
                ),
            )
            self.db.commit()

    def _sync_entry(self, row: dict[str, Any]) -> None:
        round_id = int(row["id"])
        before_commitment = self._v36_entry_commitment
        super()._sync_entry(row)
        refreshed = self._round_state(round_id) or row
        state = str(refreshed.get("state") or "")
        error_kind = str(refreshed.get("error_kind") or "")

        if state == "OPEN":
            if (
                isinstance(before_commitment, dict)
                and int(before_commitment.get("roundId") or -1) == round_id
                and int(before_commitment.get("retriesUsed") or 0) > 0
            ):
                self._v36_fast_retry_fills += 1
                self._event(
                    "INFO",
                    "ENTRY_FAST_RETRY_FILLED_V36",
                    int(refreshed["market_id"]),
                    round_id,
                    (
                        "BUY position confirmed after fast retry; "
                        f"retryOrdinal={int(before_commitment.get('retriesUsed') or 0)}"
                    ),
                )
            if (
                isinstance(self._v36_entry_commitment, dict)
                and int(self._v36_entry_commitment.get("roundId") or -1) == round_id
            ):
                self._v36_entry_commitment = None
            return

        if error_kind == "ENTRY_ORDER_NOT_FILLED":
            self._arm_or_refresh_entry_commitment(refreshed)
            return

        if state == "AMBIGUOUS" or error_kind in {
            "ENTRY_PLACE_AMBIGUOUS",
            "ENTRY_POSITION_UNCONFIRMED",
        }:
            if (
                isinstance(self._v36_entry_commitment, dict)
                and int(self._v36_entry_commitment.get("roundId") or -1) == round_id
            ):
                self._cancel_entry_commitment("AMBIGUOUS_OR_UNCONFIRMED")

    def _fast_entry_retry_if_needed(self) -> bool:
        commitment = self._v36_entry_commitment
        if not isinstance(commitment, dict):
            return False

        round_id = int(commitment["roundId"])
        row = self._round_state(round_id)
        if not isinstance(row, dict):
            self._cancel_entry_commitment("ROUND_MISSING")
            return False

        state = str(row.get("state") or "")
        if state == "ENTRY_SYNC":
            # A retry was submitted; let the normal reconciliation path run.
            return False
        if state == "OPEN":
            self._v36_entry_commitment = None
            return False
        if state == "AMBIGUOUS":
            self._cancel_entry_commitment("AMBIGUOUS")
            return False
        if str(row.get("error_kind") or "") != "ENTRY_ORDER_NOT_FILLED":
            self._cancel_entry_commitment("ROUND_NO_LONGER_NO_FILL")
            return False

        now_ms = base._now_ms()
        elapsed_ms = max(0, now_ms - int(commitment.get("startedAtMs") or now_ms))
        retries_used = int(commitment.get("retriesUsed") or 0)
        intended_side = str(commitment.get("side") or row.get("side") or "").upper()

        if now_ms < int(commitment.get("nextRetryAtMs") or 0):
            remaining = int(commitment["nextRetryAtMs"]) - now_ms
            self.status = "ENTRY_NO_FILL_RETRY_COOLDOWN_V36"
            self.last_error = f"BUY no-fill fast retry in {remaining}ms"
            return True

        market = self._prime_market()
        if (
            not isinstance(market, dict)
            or int(market.get("market_id") or 0) != int(commitment["marketId"])
            or now_ms >= int(market.get("end_ms") or 0)
        ):
            self._cancel_entry_commitment("MARKET_CHANGED_OR_ENDED")
            return False

        poly = self._poly_state()
        direction = str((poly or {}).get("direction") or "").upper()
        policy = _entry_retry_policy(
            direction=direction,
            intended_side=intended_side,
            elapsed_ms=elapsed_ms,
            retries_used=retries_used,
            max_retries=ENTRY_NO_FILL_MAX_FAST_RETRIES,
            window_ms=ENTRY_NO_FILL_COMMITMENT_WINDOW_MS,
        )
        if policy in {"EXPIRED", "EXHAUSTED"}:
            self._cancel_entry_commitment(policy)
            self._schedule_normal_rearm(row, policy)
            self.status = "ENTRY_FAST_RETRY_ENDED_NORMAL_REARM_V36"
            return True
        if policy == "CANCEL_DIRECTION_CHANGED":
            self._cancel_entry_commitment(policy)
            return False
        if policy == "WAIT_FRESH_POLY":
            commitment["nextRetryAtMs"] = now_ms + ENTRY_NO_FILL_RETRY_COOLDOWN_MS
            commitment["lastUpdatedAtMs"] = now_ms
            self.status = "ENTRY_NO_FILL_WAITING_FRESH_POLY_V36"
            self.last_error = "BUY no-fill commitment waiting for fresh same-side Poly"
            return True

        # Fresh book/depth must pass again. This is intentionally before the fresh
        # signed quote; the inherited V32 direct-book path re-applies max-entry and
        # 115% FOK depth guards without adding a second book request.
        self._entry_depth_blocked_this_tick = False
        self._entry_price_blocked_this_tick = False
        try:
            ask, _ask_size, _book_rtt = self._direct_book(market, intended_side)
        except Exception as exc:
            commitment["nextRetryAtMs"] = base._now_ms() + ENTRY_NO_FILL_RETRY_COOLDOWN_MS
            commitment["lastUpdatedAtMs"] = base._now_ms()
            self._v36_retry_waits_for_book += 1
            self.status = "ENTRY_NO_FILL_RETRY_BOOK_ERROR_V36"
            self.last_error = f"fast retry fresh book failed: {str(exc)[:200]}"
            return True

        if ask is None:
            if bool(getattr(self, "_entry_price_blocked_this_tick", False)):
                self._cancel_entry_commitment("FRESH_BOOK_PRICE_BLOCKED")
                self._schedule_normal_rearm(row, "FRESH_BOOK_PRICE_BLOCKED")
                self.status = "ENTRY_FAST_RETRY_PRICE_BLOCKED_NORMAL_REARM_V36"
                return True
            commitment["nextRetryAtMs"] = base._now_ms() + ENTRY_NO_FILL_RETRY_COOLDOWN_MS
            commitment["lastUpdatedAtMs"] = base._now_ms()
            self._v36_retry_waits_for_book += 1
            self.status = "ENTRY_NO_FILL_WAITING_FRESH_BOOK_V36"
            self.last_error = "BUY no-fill retry waiting for fresh executable FOK depth"
            return True

        # Keep the original signal timestamp for executable-lead measurement, but
        # use the fresh trigger Ask for the inherited signed-quote deterioration cap.
        retry_row = dict(row)
        retry_row["entry_binance_ask"] = float(ask)
        retries_used += 1
        commitment["retriesUsed"] = retries_used
        commitment["lastRetryStartedAtMs"] = base._now_ms()
        commitment["lastUpdatedAtMs"] = base._now_ms()
        commitment["nextRetryAtMs"] = base._now_ms() + ENTRY_NO_FILL_RETRY_COOLDOWN_MS
        self._v36_fast_retry_attempts += 1

        self._update_round(
            round_id,
            state="ENTRY_QUOTE",
            entry_order_id=None,
            entry_placed_at_ms=None,
            entry_sync_started_at_ms=None,
            entry_quote_started_at_ms=None,
            entry_quote_completed_at_ms=None,
            entry_quote_rtt_ms=None,
            entry_quote_average=None,
            entry_quote_amount_in_wei=None,
            entry_quote_amount_out_wei=None,
            entry_quote_expire_at_ms=None,
            entry_cost_usdt=None,
            shares=None,
            close_reason=None,
            error_kind=None,
            error_message=None,
        )
        retry_row["state"] = "ENTRY_QUOTE"
        retry_row["entry_order_id"] = None
        retry_row["error_kind"] = None
        retry_row["error_message"] = None
        retry_row["close_reason"] = None

        self._event(
            "WARN",
            "ENTRY_NO_FILL_FAST_RETRY_V36",
            int(commitment["marketId"]),
            round_id,
            (
                f"retry {retries_used}/{ENTRY_NO_FILL_MAX_FAST_RETRIES} on the same {intended_side} signal; "
                f"freshAsk={float(ask):.6f}; commitmentAge={elapsed_ms}ms; "
                "fresh Poly and inherited price/depth guards passed"
            ),
        )

        self._v36_retrying_round_id = round_id
        self._v36_retry_ordinal = retries_used
        try:
            self._open_round(retry_row, poly or {})
        finally:
            self._v36_retrying_round_id = None
            self._v36_retry_ordinal = 0

        refreshed = self._round_state(round_id) or retry_row
        retry_state = str(refreshed.get("state") or "")
        if retry_state == "ENTRY_SYNC":
            self._v36_fast_retry_submissions += 1
            self.status = "ENTRY_FAST_RETRY_SUBMITTED_V36"
            self.last_error = None
            return True

        # Any quote/signal/price rejection during a retry is a fresh reason not to
        # chase the market. Only a later definitive FOK NO_FILL can preserve the
        # commitment, and that arrives through _sync_entry after ENTRY_SYNC.
        reason = str(
            refreshed.get("error_kind")
            or refreshed.get("close_reason")
            or retry_state
            or "RETRY_NOT_SUBMITTED"
        )
        self._cancel_entry_commitment(reason)
        self._schedule_normal_rearm(refreshed, reason)
        self.status = "ENTRY_FAST_RETRY_REJECTED_NORMAL_REARM_V36"
        return True

    def _tick(self) -> None:
        if self._fast_entry_retry_if_needed():
            return
        return super()._tick()

    def _latest_buy_execution_v36(self) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                """SELECT id,round_id,market_id,side,attempt_no,outcome,order_status,
                          fast_retry,retry_ordinal,entry_commitment_started_at_ms,
                          signal_to_place_start_ms,signal_to_place_response_ms,
                          book_to_quote_start_ms,quote_rtt_ms,
                          quote_response_to_place_start_ms,place_rtt_ms,
                          coverage_ratio,quote_average
                     FROM poly_gap_live_execution_attempts
                    WHERE action='BUY' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        return dict(row) if row else None

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V36"
        payload["entryExecutionV36"] = {
            "enabled": True,
            "definitiveNoFillOnly": True,
            "ambiguousPlacementRetried": False,
            "sameRoundRetry": True,
            "sameSideFreshPolyRequired": True,
            "freshBookAndDepthRequired": True,
            "retryCooldownMs": ENTRY_NO_FILL_RETRY_COOLDOWN_MS,
            "maxFastRetries": ENTRY_NO_FILL_MAX_FAST_RETRIES,
            "commitmentWindowMs": ENTRY_NO_FILL_COMMITMENT_WINDOW_MS,
            "commitmentsArmed": int(self._v36_entry_commitments_armed),
            "fastRetryAttempts": int(self._v36_fast_retry_attempts),
            "fastRetrySubmissions": int(self._v36_fast_retry_submissions),
            "fastRetryFills": int(self._v36_fast_retry_fills),
            "retryBookWaits": int(self._v36_retry_waits_for_book),
            "normalRearmsScheduled": int(self._v36_normal_rearms_scheduled),
            "cancellations": dict(self._v36_retry_cancels),
            "activeCommitment": dict(self._v36_entry_commitment or {}),
            "lastBuyExecution": self._latest_buy_execution_v36(),
        }
        rules = payload.setdefault("rules", {})
        rules.update(
            {
                "entryNoFillFastRetryV36": True,
                "entryNoFillFastRetrySameRound": True,
                "entryNoFillRetryCooldownMs": ENTRY_NO_FILL_RETRY_COOLDOWN_MS,
                "entryNoFillMaxFastRetries": ENTRY_NO_FILL_MAX_FAST_RETRIES,
                "entryNoFillCommitmentWindowMs": ENTRY_NO_FILL_COMMITMENT_WINDOW_MS,
                "entryNoFillRetryRequiresFreshSameSidePoly": True,
                "entryNoFillRetryRechecksFreshBookDepth": True,
                "entryAmbiguousPlacementNeverRetried": True,
                "entryFastRetryCapCannotBypassNormalRearmCooldown": True,
            }
        )
        return payload


base.PolyGapLiveEngine = FastEntryRetryPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
