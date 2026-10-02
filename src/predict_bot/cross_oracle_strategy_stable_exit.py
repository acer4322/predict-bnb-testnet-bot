from __future__ import annotations

import json
import os
import time
from typing import Any

from . import cross_oracle_strategy_entry_quote_exit_sim as launch
from .cross_oracle_strategies import SCALP_MIN_EDGE, selected_probability, selected_quote


STRATEGY_POLY_GAP_SCALP_STABLE = "R_POLY_GAP_SCALP_STABLE"
STABLE_EXIT_CONFIRM_MS = max(
    100,
    int(os.environ.get("PREDICT_POLY_GAP_STABLE_EXIT_CONFIRM_MS", "500")),
)
STABLE_EXIT_CONFIRM_SAMPLES = max(
    2,
    int(os.environ.get("PREDICT_POLY_GAP_STABLE_EXIT_CONFIRM_SAMPLES", "3")),
)
STABLE_EXIT_MAX_SAMPLE_GAP_MS = max(
    STABLE_EXIT_CONFIRM_MS,
    int(os.environ.get("PREDICT_POLY_GAP_STABLE_EXIT_MAX_SAMPLE_GAP_MS", "1250")),
)
STABLE_EXIT_EMERGENCY_UP_MID = min(
    0.95,
    max(0.55, float(os.environ.get("PREDICT_POLY_GAP_STABLE_EXIT_EMERGENCY_UP_MID", "0.70"))),
)
STABLE_EXIT_EMERGENCY_DOWN_MID = max(
    0.05,
    min(0.45, float(os.environ.get("PREDICT_POLY_GAP_STABLE_EXIT_EMERGENCY_DOWN_MID", "0.30"))),
)


ParentPaperEngine = launch.strategy_module.GapAwareCrossOraclePaperEngine


class StableExitComparisonPaperEngine(ParentPaperEngine):
    """Add an isolated stable-exit GAP Paper ledger beside the original strategy.

    R_POLY_GAP_SCALP is intentionally untouched.  The stable variant copies its
    entry rule and Paper ask execution, then changes only the exit trigger:
    three distinct fresh Polymarket receipts must remain confidently opposite
    for at least 500 ms, unless the opposite probability reaches the emergency
    threshold.  This makes the later A/B attributable to exit sensitivity.

    The stable variant stays Paper-only.  It is intentionally not added to the
    signed quote canary strategy list in this first stability pass, avoiding a
    second stream of signed quote traffic while the live executor is being
    stabilized.
    """

    def __init__(self, db_path: Any, provider: Any) -> None:
        self._stable_last_poly_receipt_ms: int | None = None
        self.stable_exit_candidate: dict[str, Any] | None = None
        self.stable_exit_confirmed_count = 0
        self.stable_exit_emergency_count = 0
        self.stable_exit_reset_count = 0

        def capturing_provider() -> dict[str, Any]:
            snapshot = provider()
            raw = snapshot.get("receivedTimestampMs") if isinstance(snapshot, dict) else None
            try:
                self._stable_last_poly_receipt_ms = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                self._stable_last_poly_receipt_ms = None
            return snapshot

        super().__init__(db_path, capturing_provider)

    def _invalidate_simple_exit_positions_on_process_start(self) -> None:
        super()._invalidate_simple_exit_positions_on_process_start()
        now_ms = int(time.time() * 1000)
        with self.db_lock:
            cursor = self.db.execute(
                """UPDATE cross_oracle_strategy_trades
                      SET status='NOT_EVALUABLE_PROCESS_RESTART', closed_at_ms=?,
                          exit_reason='PROCESS_RESTART_DATA_GAP'
                    WHERE status='OPEN' AND strategy=?""",
                (now_ms, STRATEGY_POLY_GAP_SCALP_STABLE),
            )
            self.db.commit()
        self.feed_gap_excluded_trades += max(0, int(cursor.rowcount or 0))

    def _invalidate_gap_sensitive_positions(self, continuity: dict[str, Any]) -> None:
        super()._invalidate_gap_sensitive_positions(continuity)
        affected_slug = self._gap_affects_current_slug(continuity)
        if not affected_slug:
            return
        now_ms = int(time.time() * 1000)
        with self.db_lock:
            cursor = self.db.execute(
                """UPDATE cross_oracle_strategy_trades
                      SET status='NOT_EVALUABLE_FEED_GAP', closed_at_ms=?,
                          exit_reason='POLY_FEED_GAP'
                    WHERE status='OPEN' AND poly_market_slug=? AND strategy=?""",
                (now_ms, affected_slug, STRATEGY_POLY_GAP_SCALP_STABLE),
            )
            self.db.commit()
        self.feed_gap_excluded_trades += max(0, int(cursor.rowcount or 0))
        self.stable_exit_candidate = None

    def _reset_poly_flip_baseline(self) -> None:
        super()._reset_poly_flip_baseline()
        self.stable_exit_candidate = None

    @staticmethod
    def _stable_emergency(active_side: str, poly_up_mid: float) -> bool:
        if active_side == "UP":
            return poly_up_mid <= STABLE_EXIT_EMERGENCY_DOWN_MID
        if active_side == "DOWN":
            return poly_up_mid >= STABLE_EXIT_EMERGENCY_UP_MID
        return False

    def _clear_stable_candidate(self) -> None:
        if self.stable_exit_candidate is not None:
            self.stable_exit_reset_count += 1
        self.stable_exit_candidate = None

    def _stable_exit_ready(
        self,
        *,
        trade: Any,
        poly_direction: str,
        poly_up_mid: float,
        now_ms: int,
    ) -> tuple[bool, str | None]:
        active_side = str(trade["side"])
        if poly_direction == active_side:
            self._clear_stable_candidate()
            return False, None

        if self._stable_emergency(active_side, poly_up_mid):
            self.stable_exit_candidate = None
            self.stable_exit_emergency_count += 1
            return True, "POLY_DIRECTION_FLIP_STABLE_EMERGENCY"

        receipt_ms = self._stable_last_poly_receipt_ms
        if receipt_ms is None:
            self._clear_stable_candidate()
            return False, None

        candidate = self.stable_exit_candidate
        same = bool(
            candidate
            and int(candidate.get("tradeId") or -1) == int(trade["id"])
            and str(candidate.get("direction") or "") == poly_direction
        )
        if not same:
            self.stable_exit_candidate = {
                "tradeId": int(trade["id"]),
                "marketId": int(trade["binance_market_id"]),
                "heldSide": active_side,
                "direction": poly_direction,
                "firstSeenAtMs": now_ms,
                "lastReceiptMs": receipt_ms,
                "samples": 1,
                "polyUpMid": poly_up_mid,
            }
            return False, None

        assert candidate is not None
        last_receipt = int(candidate.get("lastReceiptMs") or 0)
        if receipt_ms <= last_receipt:
            candidate["polyUpMid"] = poly_up_mid
            return False, None

        if receipt_ms - last_receipt > STABLE_EXIT_MAX_SAMPLE_GAP_MS:
            self.stable_exit_candidate = {
                "tradeId": int(trade["id"]),
                "marketId": int(trade["binance_market_id"]),
                "heldSide": active_side,
                "direction": poly_direction,
                "firstSeenAtMs": now_ms,
                "lastReceiptMs": receipt_ms,
                "samples": 1,
                "polyUpMid": poly_up_mid,
            }
            self.stable_exit_reset_count += 1
            return False, None

        candidate["samples"] = int(candidate.get("samples") or 1) + 1
        candidate["lastReceiptMs"] = receipt_ms
        candidate["polyUpMid"] = poly_up_mid
        elapsed_ms = now_ms - int(candidate["firstSeenAtMs"])
        if (
            int(candidate["samples"]) >= STABLE_EXIT_CONFIRM_SAMPLES
            and elapsed_ms >= STABLE_EXIT_CONFIRM_MS
        ):
            self.stable_exit_candidate = None
            self.stable_exit_confirmed_count += 1
            return True, "POLY_DIRECTION_FLIP_STABLE_CONFIRMED"
        return False, None

    def _maybe_open_gap_scalp(self, **kwargs: Any) -> None:
        # Keep the original R_POLY_GAP_SCALP behavior byte-for-byte through the
        # parent call; the new ledger is evaluated independently afterward.
        super()._maybe_open_gap_scalp(**kwargs)

        latest = kwargs["latest"]
        market_id = int(kwargs["binance_market_id"])
        poly_slug = str(kwargs["poly_slug"])
        poly_direction = str(kwargs["poly_direction"])
        poly_up_mid = float(kwargs["poly_up_mid"])
        binance_up_mid = float(kwargs["binance_up_mid"])
        now_ms = int(kwargs["now_ms"])

        stable_trade = self._open_trade_for_market(
            STRATEGY_POLY_GAP_SCALP_STABLE,
            market_id,
        )
        if stable_trade is not None:
            ready, reason = self._stable_exit_ready(
                trade=stable_trade,
                poly_direction=poly_direction,
                poly_up_mid=poly_up_mid,
                now_ms=now_ms,
            )
            if ready and reason:
                closed = self._exit_trade_at_bid(stable_trade, latest, now_ms, reason)
                if not closed:
                    return
                stable_trade = None
            else:
                return

        ask = selected_quote(latest, poly_direction, "ask")
        if ask is None:
            return
        poly_selected = selected_probability(poly_up_mid, poly_direction)
        executable_edge = poly_selected - ask
        if executable_edge < SCALP_MIN_EDGE:
            return
        self._open_trade(
            strategy=STRATEGY_POLY_GAP_SCALP_STABLE,
            latest=latest,
            binance_market_id=market_id,
            poly_slug=poly_slug,
            side=poly_direction,
            poly_up_mid=poly_up_mid,
            binance_up_mid=binance_up_mid,
            now_ms=now_ms,
            reason="POLY_BINANCE_EXECUTABLE_GAP_STABLE_EXIT",
            metadata={
                "polySelectedMid": poly_selected,
                "executableEdge": executable_edge,
                "exitVariant": "stable_confirmed_flip",
                "exitConfirmMs": STABLE_EXIT_CONFIRM_MS,
                "exitConfirmSamples": STABLE_EXIT_CONFIRM_SAMPLES,
                "emergencyUpMid": STABLE_EXIT_EMERGENCY_UP_MID,
                "emergencyDownMid": STABLE_EXIT_EMERGENCY_DOWN_MID,
            },
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        summaries = payload.setdefault("summaries", {})
        summaries[STRATEGY_POLY_GAP_SCALP_STABLE] = self._summary(
            STRATEGY_POLY_GAP_SCALP_STABLE
        )
        rules = payload.setdefault("strategyRules", {})
        rules[STRATEGY_POLY_GAP_SCALP_STABLE] = (
            "Same entry as R_POLY_GAP_SCALP. Exit only after the opposite confident "
            f"Polymarket direction persists >= {STABLE_EXIT_CONFIRM_MS}ms across >= "
            f"{STABLE_EXIT_CONFIRM_SAMPLES} distinct fresh receipt timestamps; "
            "strong opposite probability uses an emergency immediate exit."
        )
        payload["stableExitComparison"] = {
            "strategy": STRATEGY_POLY_GAP_SCALP_STABLE,
            "paperOnly": True,
            "originalStrategyUnchanged": True,
            "entryRuleMatchesOriginalGapScalp": True,
            "normalConfirmMs": STABLE_EXIT_CONFIRM_MS,
            "normalConfirmDistinctSamples": STABLE_EXIT_CONFIRM_SAMPLES,
            "maxDistinctSampleGapMs": STABLE_EXIT_MAX_SAMPLE_GAP_MS,
            "emergencyUpMidAgainstDown": STABLE_EXIT_EMERGENCY_UP_MID,
            "emergencyUpMidAgainstUp": STABLE_EXIT_EMERGENCY_DOWN_MID,
            "requiresDistinctPolyReceiptTimestamps": True,
            "signedQuoteCanaryEnabledForVariant": False,
            "candidate": dict(self.stable_exit_candidate) if self.stable_exit_candidate else None,
            "confirmedNormalExits": int(self.stable_exit_confirmed_count),
            "emergencyExits": int(self.stable_exit_emergency_count),
            "candidateResets": int(self.stable_exit_reset_count),
        }
        return payload


# The entry_quote_exit_sim launcher already installs the latest signed quote
# canary. Replace only the engine class it constructs; the HTTP API and all
# existing strategy behavior remain on the same sidecar/DB.
launch.strategy_module.GapAwareCrossOraclePaperEngine = StableExitComparisonPaperEngine


def main() -> int:
    return launch.main()


if __name__ == "__main__":
    raise SystemExit(main())
