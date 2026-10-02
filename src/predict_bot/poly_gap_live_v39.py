from __future__ import annotations

import os
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v38 import PolyLeaderGuardLiveEngine


FAST_RETRY_MAX_ORIGINAL_SIGNAL_AGE_MS = max(
    100,
    int(
        os.environ.get(
            "PREDICT_POLY_GAP_LIVE_FAST_RETRY_MAX_SIGNAL_AGE_MS",
            "1200",
        )
    ),
)
POST_QUOTE_MIN_EDGE = max(
    0.0,
    float(os.environ.get("PREDICT_POLY_GAP_LIVE_POST_QUOTE_MIN_EDGE", "0.0")),
)
POST_QUOTE_BLOCK_MARKER = "V39_POST_QUOTE_EDGE_BLOCKED"


class _PostQuoteEdgeBlocked(RuntimeError):
    pass


def fast_retry_signal_age_policy(signal_age_ms: int | float | None) -> str:
    if signal_age_ms is None:
        return "EXPIRED_UNAVAILABLE"
    try:
        age = max(0.0, float(signal_age_ms))
    except (TypeError, ValueError):
        return "EXPIRED_UNAVAILABLE"
    if age > float(FAST_RETRY_MAX_ORIGINAL_SIGNAL_AGE_MS):
        return "EXPIRED"
    return "ALLOW"


def post_quote_edge_policy(edge: int | float | None) -> str:
    value = base._finite(edge)
    if value is None:
        return "BLOCK_UNAVAILABLE"
    if value <= POST_QUOTE_MIN_EDGE + 1e-12:
        return "BLOCK_NON_POSITIVE"
    return "ALLOW"


class EntryFreshnessAndEdgeGuardPolyGapLiveEngine(PolyLeaderGuardLiveEngine):
    """V39: reject stale V36 retries and BUY quotes whose live edge is gone.

    This layer intentionally changes only two entry-safety decisions:

    1. A V36 definitive-NO_FILL fast retry may reuse the original round only
       while the *original* entry signal is at most 1200 ms old by default.
       The old V36 commitment window is still retained, but it can no longer
       start several seconds after the informational signal and thereby revive
       a 5-12 second old opportunity.

    2. Normal MARKET/FOK BUY placement is blocked after the signed quote returns
       unless current Poly selected probability - signed BUY average remains
       strictly positive. The original >= SCALP_MIN_EDGE signal is still required
       before quoting, but V39 does not require the full original edge to remain.
       It only forbids crossing into zero/negative live edge.

    Shotgun LIMIT/GTC entry is deliberately unchanged in this patch. V38 leader
    guard behavior, V37 Shotgun behavior, SELL logic, and Shotgun cancellation
    behavior remain unchanged.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v39_stale_fast_retry_blocks = 0
        self._v39_post_quote_edge_blocks = 0
        self._v39_last_fast_retry_signal_age_ms: int | None = None
        self._v39_last_post_quote_guard: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    @staticmethod
    def _signal_age_ms(row: dict[str, Any], commitment: dict[str, Any] | None) -> int | None:
        signal_raw = row.get("entry_signal_at_ms")
        if signal_raw is None and isinstance(commitment, dict):
            signal_raw = commitment.get("signalAtMs")
        try:
            signal_ms = int(signal_raw)
        except (TypeError, ValueError):
            return None
        if signal_ms <= 0:
            return None
        return max(0, base._now_ms() - signal_ms)

    def _expire_stale_fast_retry(
        self,
        row: dict[str, Any],
        commitment: dict[str, Any],
        *,
        stage: str,
    ) -> bool:
        age_ms = self._signal_age_ms(row, commitment)
        self._v39_last_fast_retry_signal_age_ms = age_ms
        if fast_retry_signal_age_policy(age_ms) == "ALLOW":
            return False

        round_id = int(row["id"])
        market_id = int(row["market_id"])
        age_text = "unavailable" if age_ms is None else f"{age_ms}ms"
        reason = "ABSOLUTE_SIGNAL_AGE_EXPIRED_V39"
        self._v39_stale_fast_retry_blocks += 1
        self._cancel_entry_commitment(reason, event=False)
        self._schedule_normal_rearm(row, reason)
        self.status = "ENTRY_FAST_RETRY_SIGNAL_EXPIRED_V39"
        self.last_error = (
            f"fast retry blocked: original signal age {age_text} exceeds "
            f"{FAST_RETRY_MAX_ORIGINAL_SIGNAL_AGE_MS}ms absolute limit; waiting for a new signal"
        )
        self._event(
            "INFO",
            "ENTRY_FAST_RETRY_SIGNAL_EXPIRED_V39",
            market_id,
            round_id,
            (
                f"stage={stage}; originalSignalAge={age_text}; "
                f"maxAge={FAST_RETRY_MAX_ORIGINAL_SIGNAL_AGE_MS}ms; "
                "old round will not be retried; normal rearm requires a new entry signal"
            ),
        )
        return True

    def _sync_entry(self, row: dict[str, Any]) -> None:
        super()._sync_entry(row)
        commitment = self._v36_entry_commitment
        if not isinstance(commitment, dict):
            return
        round_id = int(row["id"])
        if int(commitment.get("roundId") or -1) != round_id:
            return
        refreshed = self._round_state(round_id) or row
        self._expire_stale_fast_retry(
            refreshed,
            commitment,
            stage="NO_FILL_RECONCILED",
        )

    def _fast_entry_retry_if_needed(self) -> bool:
        commitment = self._v36_entry_commitment
        if isinstance(commitment, dict):
            round_id = int(commitment.get("roundId") or 0)
            row = self._round_state(round_id) if round_id > 0 else None
            if isinstance(row, dict) and self._expire_stale_fast_retry(
                row,
                commitment,
                stage="BEFORE_FAST_RETRY",
            ):
                return True
        return super()._fast_entry_retry_if_needed()

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        settings = self._settings()
        if bool(settings.get("shotgunEnabled")):
            return super()._open_round(row, poly)

        with self.lock:
            client = self.client
        if client is None:
            return super()._open_round(row, poly)

        original_place_market_order = client.place_market_order
        blocked: dict[str, Any] = {}

        def guarded_place_market_order(*args: Any, **kwargs: Any) -> dict[str, Any]:
            latency = dict(self.last_entry_latency or {})
            edge_after = base._finite(latency.get("edgeAfterQuote"))
            policy = post_quote_edge_policy(edge_after)
            if policy != "ALLOW":
                blocked.update(
                    {
                        "policy": policy,
                        "edgeAfterQuote": edge_after,
                        "signedQuoteAverage": base._finite(latency.get("signedQuoteAverage")),
                        "triggerAsk": base._finite(latency.get("triggerAsk")),
                        "signalToQuoteResponseMs": latency.get("signalToQuoteResponseMs"),
                    }
                )
                edge_text = "unavailable" if edge_after is None else f"{edge_after:+.6f}"
                raise _PostQuoteEdgeBlocked(
                    f"{POST_QUOTE_BLOCK_MARKER}: post-quote edge {edge_text} must be > "
                    f"{POST_QUOTE_MIN_EDGE:.6f}"
                )
            return original_place_market_order(*args, **kwargs)

        client.place_market_order = guarded_place_market_order  # type: ignore[method-assign]
        try:
            super()._open_round(row, poly)
        finally:
            client.place_market_order = original_place_market_order  # type: ignore[method-assign]

        if not blocked:
            return

        round_id = int(row["id"])
        market_id = int(row["market_id"])
        edge_after = base._finite(blocked.get("edgeAfterQuote"))
        quote_average = base._finite(blocked.get("signedQuoteAverage"))
        signal_age_ms = blocked.get("signalToQuoteResponseMs")
        edge_text = "unavailable" if edge_after is None else f"{edge_after:+.6f}"
        quote_text = "unavailable" if quote_average is None else f"{quote_average:.6f}"
        message = (
            f"signed BUY not submitted: post-quote edge {edge_text} must remain > "
            f"{POST_QUOTE_MIN_EDGE:.6f}; signedAverage={quote_text}; "
            f"signalToQuoteResponseMs={signal_age_ms}"
        )
        refreshed = self._update_round(
            round_id,
            state="REJECTED",
            close_reason="ENTRY_POST_QUOTE_EDGE_NON_POSITIVE",
            error_kind="ENTRY_POST_QUOTE_EDGE_NON_POSITIVE",
            error_message=message,
        ) or self._round_state(round_id) or row

        self._v39_post_quote_edge_blocks += 1
        self._v39_last_post_quote_guard = {
            "marketId": market_id,
            "roundId": round_id,
            "side": str(row.get("side") or ""),
            "policy": blocked.get("policy"),
            "edgeAfterQuote": edge_after,
            "minimumRequiredEdge": POST_QUOTE_MIN_EDGE,
            "signedQuoteAverage": quote_average,
            "triggerAsk": base._finite(blocked.get("triggerAsk")),
            "signalToQuoteResponseMs": signal_age_ms,
            "blockedAtMs": base._now_ms(),
        }
        self.status = "ENTRY_POST_QUOTE_EDGE_BLOCKED_V39"
        self.last_error = message
        self._event(
            "INFO",
            "ENTRY_POST_QUOTE_EDGE_BLOCKED_V39",
            market_id,
            round_id,
            message,
        )

        latest_attempt_id = getattr(self, "_latest_attempt_id", None)
        finish_attempt = getattr(self, "_finish_attempt", None)
        if callable(latest_attempt_id) and callable(finish_attempt):
            try:
                attempt_id = latest_attempt_id(round_id, "BUY")
                if attempt_id is not None:
                    finish_attempt(
                        int(attempt_id),
                        refreshed,
                        outcome="ENTRY_POST_QUOTE_EDGE_NON_POSITIVE",
                        message=message,
                    )
            except Exception:
                # The round-level safety decision is authoritative. Telemetry
                # repair is best-effort and must never turn a blocked BUY into a
                # placement attempt.
                pass

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V39"
        payload["entrySafetyV39"] = {
            "enabled": True,
            "fastRetryAbsoluteSignalAgeGuard": True,
            "fastRetryMaxOriginalSignalAgeMs": FAST_RETRY_MAX_ORIGINAL_SIGNAL_AGE_MS,
            "lastFastRetryOriginalSignalAgeMs": self._v39_last_fast_retry_signal_age_ms,
            "staleFastRetryBlocks": int(self._v39_stale_fast_retry_blocks),
            "postQuoteEdgeGuard": True,
            "postQuoteMinimumEdgeExclusive": POST_QUOTE_MIN_EDGE,
            "postQuoteEdgeMustBeStrictlyPositive": POST_QUOTE_MIN_EDGE <= 1e-12,
            "postQuoteEdgeBlocks": int(self._v39_post_quote_edge_blocks),
            "lastPostQuoteGuard": dict(self._v39_last_post_quote_guard or {}),
            "normalMarketFokOnly": True,
            "shotgunLimitEntryChanged": False,
            "leaderGuardChanged": False,
            "exitLogicChanged": False,
        }
        rules = payload.setdefault("rules", {})
        rules.update(
            entryFastRetryAbsoluteSignalAgeV39=True,
            entryFastRetryMaxOriginalSignalAgeMs=FAST_RETRY_MAX_ORIGINAL_SIGNAL_AGE_MS,
            entryPostQuoteEdgeGuardV39=True,
            entryPostQuoteMinimumEdgeExclusive=POST_QUOTE_MIN_EDGE,
            entryPostQuoteNegativeOrZeroEdgeBlocked=True,
            entryShotgunPostQuoteGuardApplied=False,
        )
        return payload


base.PolyGapLiveEngine = EntryFreshnessAndEdgeGuardPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
