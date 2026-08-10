from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v26 import LIVE_SAME_MARKET_BREAKER_EXIT_COUNT
from .poly_gap_live_v28 import PriceRiskControlsPolyGapLiveEngine


_ACTIVE_STATES = {"ENTRY_QUOTE", "ENTRY_SYNC", "OPEN", "EXIT_QUOTE", "EXIT_SYNC"}
_TERMINAL_STATES = {"CLOSED", "SETTLED", "REJECTED", "FAILED", "AMBIGUOUS", "HALTED"}


class ReversalHandoffPolyGapLiveEngine(PriceRiskControlsPolyGapLiveEngine):
    """V29: submit the new-direction BUY after SELL submission, before SELL confirmation.

    V28 and earlier versions require the old round to become fully terminal/flat
    before the normal same-market re-entry path can create another round. That is
    deliberately safe, but a fast Polymarket reversal can move the Binance
    Prediction quote materially while EXIT_SYNC is still reconciling.

    V29 adds a bounded reversal handoff:

    - only a confirmed POLY_DIRECTION_FLIP is eligible; TAKE_PROFIT never handoffs;
    - the old SELL is submitted through the unchanged V28/V11 reconciliation path;
    - only after SELL placement returned successfully and the parent row is
      EXIT_SYNC with an order id may an opposite BUY be submitted immediately;
    - the BUY does not wait for the SELL to be reported FILLED or for the old
      wallet position to be reported flat;
    - both rows keep independent order ids and reconciliation state;
    - while the old parent remains active, it stays the primary managed round;
      the handoff BUY is still reconciled in the background;
    - if this reversal would make the projected completed-reversal count reach
      the V26 same-market breaker threshold, the SELL still proceeds but the
      handoff BUY is forbidden;
    - all existing new-entry gates still apply: master/runtime, loss guards,
      persistent Paper guard, current exact market, entry delay, fresh Poly
      direction, minimum gap, max-entry Ask and signed-average price ceiling.

    The sequencing is intentionally SELL-submitted -> BUY-submitted rather than
    two blind concurrent place calls. This removes the expensive EXIT fill/
    position-confirmation wait while still refusing to add opposite exposure when
    the SELL placement itself is transport-ambiguous or rejected.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._last_reversal_handoff: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            columns = {
                str(row["name"])
                for row in self.db.execute("PRAGMA table_info(poly_gap_live_rounds)").fetchall()
            }
            if "handoff_parent_round_id" not in columns:
                self.db.execute(
                    "ALTER TABLE poly_gap_live_rounds ADD COLUMN handoff_parent_round_id INTEGER"
                )
                self.db.commit()

    def _active_rows(self) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT * FROM poly_gap_live_rounds
                    WHERE state IN ('ENTRY_QUOTE','ENTRY_SYNC','OPEN','EXIT_QUOTE','EXIT_SYNC')
                    ORDER BY id ASC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def _current_active_round(self) -> dict[str, Any] | None:
        """Keep an unresolved handoff parent authoritative until it is terminal.

        A handoff child may already be ENTRY_SYNC or OPEN while the previous side
        is still EXIT_SYNC/OPEN. Returning the parent here preserves the existing
        SELL retry/reconciliation lifecycle instead of letting the newer row hide
        money that is still at risk on the old side.
        """
        rows = self._active_rows()
        if not rows:
            return None
        active_ids = {int(row["id"]) for row in rows}
        parent_ids = {
            int(row["handoff_parent_round_id"])
            for row in rows
            if row.get("handoff_parent_round_id") is not None
            and int(row.get("handoff_parent_round_id") or 0) in active_ids
        }
        for row in rows:
            if int(row["id"]) in parent_ids:
                return row
        # Preserve the pre-V29 newest-active behavior when there is no live
        # parent/child overlap.
        return rows[-1]

    def _handoff_children(self, parent_round_id: int) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT * FROM poly_gap_live_rounds
                    WHERE handoff_parent_round_id=?
                    ORDER BY id DESC""",
                (int(parent_round_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _active_handoff_child(self, parent_round_id: int) -> dict[str, Any] | None:
        for row in self._handoff_children(parent_round_id):
            if str(row.get("state") or "") in _ACTIVE_STATES:
                return row
        return None

    def _set_handoff_parent(self, round_id: int, parent_round_id: int) -> dict[str, Any] | None:
        with self.db_lock:
            self.db.execute(
                "UPDATE poly_gap_live_rounds SET handoff_parent_round_id=?, updated_at_ms=? WHERE id=?",
                (int(parent_round_id), base._now_ms(), int(round_id)),
            )
            self.db.commit()
            row = self.db.execute(
                "SELECT * FROM poly_gap_live_rounds WHERE id=?", (int(round_id),)
            ).fetchone()
        return dict(row) if row else None

    def _projected_reversal_breaker(self, market_id: int) -> dict[str, Any]:
        completed = self._completed_reversal_exits_for_market(int(market_id))
        projected = completed + 1
        return {
            "marketId": int(market_id),
            "completedBeforeCurrentExit": completed,
            "projectedIfCurrentExitCompletes": projected,
            "threshold": LIVE_SAME_MARKET_BREAKER_EXIT_COUNT,
            "handoffBlocked": projected >= LIVE_SAME_MARKET_BREAKER_EXIT_COUNT,
        }

    def _entry_delay_ready_for_handoff(self) -> bool:
        method = getattr(self, "_entry_delay_state", None)
        if not callable(method):
            return True
        try:
            state = method()
        except Exception:
            return False
        return bool(state.get("ready")) if state.get("enabled") else bool(state.get("identityCurrent", True))

    def _reconcile_handoff_children_background(self) -> None:
        """Reconcile submitted BUYs even while their old-side parent is active."""
        with self.db_lock:
            rows = self.db.execute(
                """SELECT child.*
                     FROM poly_gap_live_rounds child
                     JOIN poly_gap_live_rounds parent
                       ON parent.id=child.handoff_parent_round_id
                    WHERE child.state='ENTRY_SYNC'
                      AND parent.state IN ('ENTRY_QUOTE','ENTRY_SYNC','OPEN','EXIT_QUOTE','EXIT_SYNC')
                    ORDER BY child.id ASC"""
            ).fetchall()
        for raw in rows:
            child = dict(raw)
            # The inherited V9+ entry reconciliation is order-id/position based
            # and does not require the row to be the sole active round.
            self._sync_entry(child)

    def _attempt_handoff_entry(
        self,
        *,
        parent: dict[str, Any],
        market: dict[str, Any],
        expected_direction: str,
    ) -> None:
        parent_id = int(parent["id"])
        market_id = int(parent["market_id"])
        existing = self._active_handoff_child(parent_id)
        if existing is not None:
            self._last_reversal_handoff = {
                "marketId": market_id,
                "parentRoundId": parent_id,
                "childRoundId": int(existing["id"]),
                "status": "EXISTING_HANDOFF_ACTIVE",
                "blocked": False,
                "checkedAtMs": base._now_ms(),
            }
            return

        breaker = self._projected_reversal_breaker(market_id)
        if breaker["handoffBlocked"]:
            self._last_reversal_handoff = {
                **breaker,
                "parentRoundId": parent_id,
                "status": "BLOCKED_PROJECTED_REVERSAL_BREAKER",
                "blocked": True,
                "checkedAtMs": base._now_ms(),
            }
            self._event(
                "WARN",
                "REVERSAL_HANDOFF_BLOCKED_BREAKER",
                market_id,
                parent_id,
                (
                    f"current reversal would project completed reversal exits to "
                    f"{breaker['projectedIfCurrentExitCompletes']}/{breaker['threshold']}; "
                    "SELL continues but opposite BUY is forbidden"
                ),
            )
            return

        if not self._entry_allowed():
            self._last_reversal_handoff = {
                **breaker,
                "parentRoundId": parent_id,
                "status": "BLOCKED_EXISTING_ENTRY_GUARD",
                "blocked": True,
                "checkedAtMs": base._now_ms(),
            }
            return
        if not self._entry_delay_ready_for_handoff():
            self._last_reversal_handoff = {
                **breaker,
                "parentRoundId": parent_id,
                "status": "BLOCKED_ENTRY_DELAY",
                "blocked": True,
                "checkedAtMs": base._now_ms(),
            }
            return

        poly = self._poly_state()
        if poly is None or str(poly.get("direction") or "") != expected_direction:
            self._last_reversal_handoff = {
                **breaker,
                "parentRoundId": parent_id,
                "status": "BLOCKED_POLY_DIRECTION_CHANGED",
                "blocked": True,
                "checkedAtMs": base._now_ms(),
            }
            return
        selected_mid = base._finite(poly.get("selectedMid"))
        if selected_mid is None:
            return

        ask, ask_size, _book_rtt = self._direct_book(market, expected_direction)
        if ask is None:
            self._last_reversal_handoff = {
                **breaker,
                "parentRoundId": parent_id,
                "status": "BLOCKED_BINANCE_BOOK_OR_MAX_ENTRY",
                "blocked": True,
                "checkedAtMs": base._now_ms(),
            }
            return
        edge = selected_mid - ask
        if edge + 1e-12 < base.SCALP_MIN_EDGE:
            self._last_reversal_handoff = {
                **breaker,
                "parentRoundId": parent_id,
                "status": "BLOCKED_MIN_EDGE",
                "blocked": True,
                "selectedMid": selected_mid,
                "ask": ask,
                "edge": edge,
                "minimumEdge": base.SCALP_MIN_EDGE,
                "checkedAtMs": base._now_ms(),
            }
            return

        risk = self._loss_state()
        stake = float(risk.get("effectiveStakeUsdt") or self._settings()["stakeUsdt"])
        if ask_size is not None and ask_size > 0 and ask_size * ask < min(stake, 0.01):
            self._last_reversal_handoff = {
                **breaker,
                "parentRoundId": parent_id,
                "status": "BLOCKED_VISIBLE_DEPTH",
                "blocked": True,
                "checkedAtMs": base._now_ms(),
            }
            return

        token_id = str(market[f"{expected_direction.lower()}_token_id"])
        child = self._insert_round(
            market=market,
            side=expected_direction,
            token_id=token_id,
            stake=stake,
            poly_selected=selected_mid,
            ask=ask,
            edge=edge,
        )
        child = self._set_handoff_parent(int(child["id"]), parent_id) or child
        self._event(
            "INFO",
            "REVERSAL_HANDOFF_ENTRY_START",
            market_id,
            int(child["id"]),
            (
                f"parent round {parent_id} SELL already submitted; starting opposite "
                f"{expected_direction} BUY without waiting for EXIT fill confirmation"
            ),
        )
        self._open_round(child, poly)
        with self.db_lock:
            refreshed_row = self.db.execute(
                "SELECT * FROM poly_gap_live_rounds WHERE id=?", (int(child["id"]),)
            ).fetchone()
        refreshed = dict(refreshed_row) if refreshed_row else child
        child_state = str(refreshed.get("state") or "")
        self._last_reversal_handoff = {
            **breaker,
            "parentRoundId": parent_id,
            "childRoundId": int(child["id"]),
            "direction": expected_direction,
            "selectedMid": selected_mid,
            "ask": ask,
            "edge": edge,
            "status": "ENTRY_SUBMITTED" if child_state == "ENTRY_SYNC" else child_state,
            "blocked": False if child_state == "ENTRY_SYNC" else True,
            "checkedAtMs": base._now_ms(),
        }
        if child_state == "ENTRY_SYNC":
            self._event(
                "WARN",
                "REVERSAL_HANDOFF_ENTRY_SUBMITTED",
                market_id,
                int(child["id"]),
                (
                    f"opposite {expected_direction} BUY submitted while parent round {parent_id} "
                    "remains in exit reconciliation"
                ),
            )

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        """Submit reversal SELL, then immediately hand off if projected breaker permits."""
        market_id = int(row["market_id"])
        parent_id = int(row["id"])
        expected_direction = "DOWN" if str(row.get("side") or "") == "UP" else "UP"
        breaker = self._projected_reversal_breaker(market_id)

        # V28 persists POLY_DIRECTION_FLIP and uses the full existing SELL safety
        # chain. Do not place any new BUY until this call has deterministically
        # returned an EXIT_SYNC row with an order id.
        super()._exit_round(row, signal_ms)
        with self.db_lock:
            refreshed_row = self.db.execute(
                "SELECT * FROM poly_gap_live_rounds WHERE id=?", (parent_id,)
            ).fetchone()
        refreshed = dict(refreshed_row) if refreshed_row else dict(row)
        if (
            str(refreshed.get("state") or "") != "EXIT_SYNC"
            or not str(refreshed.get("exit_order_id") or "").strip()
        ):
            self._last_reversal_handoff = {
                **breaker,
                "parentRoundId": parent_id,
                "status": "NO_HANDOFF_EXIT_NOT_SUBMITTED",
                "blocked": True,
                "parentState": refreshed.get("state"),
                "checkedAtMs": base._now_ms(),
            }
            return

        market = self._prime_market()
        if (
            not isinstance(market, dict)
            or int(market.get("market_id") or 0) != market_id
            or base._now_ms() >= int(market.get("end_ms") or 0)
        ):
            self._last_reversal_handoff = {
                **breaker,
                "parentRoundId": parent_id,
                "status": "BLOCKED_MARKET_ROLLOVER",
                "blocked": True,
                "checkedAtMs": base._now_ms(),
            }
            return

        self._attempt_handoff_entry(
            parent=refreshed,
            market=market,
            expected_direction=expected_direction,
        )

    def _tick(self) -> None:
        # Keep handoff BUY reconciliation moving while the old-side parent still
        # owns normal active-round management.
        self._reconcile_handoff_children_background()
        return super()._tick()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        active_rows = self._active_rows()
        overlaps = []
        for row in active_rows:
            parent_id = row.get("handoff_parent_round_id")
            if parent_id is not None:
                overlaps.append(
                    {
                        "childRoundId": int(row["id"]),
                        "parentRoundId": int(parent_id),
                        "childState": row.get("state"),
                        "childSide": row.get("side"),
                    }
                )
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V29"
        payload["reversalHandoff"] = {
            "enabled": True,
            "sequence": "SELL_SUBMITTED_THEN_OPPOSITE_BUY_WITHOUT_EXIT_FILL_CONFIRMATION",
            "requiresExitPlacementAcknowledged": True,
            "waitsForExitFillBeforeBuy": False,
            "independentEntryAndExitReconciliation": True,
            "projectedBreakerCheck": True,
            "breakerThreshold": LIVE_SAME_MARKET_BREAKER_EXIT_COUNT,
            "handoffForbiddenWhenProjectedReversalCountReachesThreshold": True,
            "takeProfitCreatesHandoff": False,
            "existingEntryGuardsStillApply": True,
            "activeOverlaps": overlaps,
            "last": dict(self._last_reversal_handoff or {}),
        }
        rules = payload.setdefault("rules", {})
        rules.update(
            {
                "reversalReentryWaitsForFlat": False,
                "reversalReentryRequiresSellSubmitted": True,
                "reversalReentryProjectedBreakerAware": True,
                "takeProfitReentryBehaviorChanged": False,
            }
        )
        return payload


base.PolyGapLiveEngine = ReversalHandoffPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
