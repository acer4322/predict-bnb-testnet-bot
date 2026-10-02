from __future__ import annotations

import os
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v39 import EntryFreshnessAndEdgeGuardPolyGapLiveEngine

REVERSAL_REENTRY_CONFIRM_MS = max(250, int(os.environ.get("PREDICT_POLY_GAP_LIVE_REVERSAL_REENTRY_CONFIRM_MS", "2000")))
REVERSAL_REENTRY_MIN_EDGE = max(0.0, float(os.environ.get("PREDICT_POLY_GAP_LIVE_REVERSAL_REENTRY_MIN_EDGE", "0.075")))

class _ReversalReentryEdgeBlocked(RuntimeError):
    pass

def reversal_reentry_confirmation_policy(*, target_side: str, direction: str | None, fresh: bool, elapsed_ms: int | float | None, confirm_ms: int = REVERSAL_REENTRY_CONFIRM_MS) -> str:
    target = str(target_side or "").upper()
    observed = str(direction or "").upper()
    if not fresh:
        return "RESET_STALE"
    if observed not in {"UP", "DOWN"}:
        return "RESET_NEUTRAL"
    if observed != target:
        return "RESET_DIRECTION_CHANGED"
    try:
        elapsed = max(0.0, float(elapsed_ms or 0))
    except (TypeError, ValueError):
        elapsed = 0.0
    return "ELIGIBLE" if elapsed >= max(0, int(confirm_ms)) else "WAIT_CONFIRMATION"

def reversal_reentry_edge_policy(edge: int | float | None) -> str:
    value = base._finite(edge)
    if value is None:
        return "BLOCK_UNAVAILABLE"
    return "ALLOW" if value + 1e-12 >= REVERSAL_REENTRY_MIN_EDGE else "BLOCK_EDGE"

class ImmediateExitCautiousReentryPolyGapLiveEngine(EntryFreshnessAndEdgeGuardPolyGapLiveEngine):
    """V40: immediate reversal SELL; move caution to a confirmed reversal BUY.

    The first fresh/confident opposite Poly direction bypasses V12's 500ms / 3
    receipt exit debounce and uses the inherited signed SELL/FOK path. Once the
    SELL is submitted, only the opposite side may re-enter the same market. It
    must remain continuously fresh/confident for 2s. Neutral, stale, or a return
    to the exited side resets the full timer. After 2s, V40 re-reads the normal
    Binance FOK book/depth path, re-reads Poly after that HTTP request, requires
    fresh-book edge >= 0.075, and requires signed-quote edge >= 0.075 immediately
    before placement. All inherited max-entry, depth, quote, leader and loss
    guards remain active. Re-entry forces normal MARKET/FOK even when Shotgun is
    globally enabled; saved Shotgun settings and resting GTC orders are untouched.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v40_reentry_guard: dict[str, Any] | None = None
        self._v40_force_market_reentry = False
        self._v40_immediate_reversal_exits = 0
        self._v40_reentry_resets = 0
        self._v40_reentry_eligible = 0
        self._v40_reentry_book_edge_blocks = 0
        self._v40_reentry_quote_edge_blocks = 0
        self._v40_reentry_fills = 0
        self._v40_last_reentry_check: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        if getattr(self, "_v40_force_market_reentry", False):
            settings["shotgunEnabled"] = False
        return settings

    @staticmethod
    def _opposite(side: str) -> str | None:
        side = str(side or "").upper()
        return "DOWN" if side == "UP" else "UP" if side == "DOWN" else None

    def _observe_exit_flip(self, active: dict[str, Any], *, direction: str, up_mid: float | None, receipt_ms: int | None, now_ms: int) -> bool:
        held = str(active.get("side") or "").upper()
        observed = str(direction or "").upper()
        if observed not in {"UP", "DOWN"} or observed == held:
            return False
        round_id = int(active["id"])
        self.exit_flip_candidate = None
        self._set_exit_intent(round_id, "POLY_DIRECTION_FLIP")
        self._v40_immediate_reversal_exits += 1
        self._event("WARN", "REVERSAL_EXIT_IMMEDIATE_V40", int(active["market_id"]), round_id,
                    f"first fresh confident opposite Poly direction; held={held}; to={observed}; upMid={up_mid}; receiptMs={receipt_ms}; bypassing V12 500ms/3-receipt wait and starting signed SELL/FOK")
        return True

    def _arm_reentry_guard(self, row: dict[str, Any], submitted_at_ms: int) -> None:
        target = self._opposite(str(row.get("side") or ""))
        if target is None:
            return
        now_ms = max(base._now_ms(), int(submitted_at_ms or 0))
        self._v40_reentry_guard = {
            "sourceRoundId": int(row["id"]), "marketId": int(row["market_id"]),
            "exitedSide": str(row.get("side") or "").upper(), "targetSide": target,
            "sellSubmittedAtMs": now_ms, "confirmationStartedAtMs": now_ms,
            "eligibleSinceMs": None, "eligibleAnnounced": False, "entryRoundId": None,
            "lastResetReason": None, "lastDirection": target, "lastPolyReceiptMs": None,
        }
        self._event("INFO", "REVERSAL_REENTRY_LOCK_ARMED_V40", int(row["market_id"]), int(row["id"]),
                    f"SELL submitted; target={target}; require continuous fresh same-direction {REVERSAL_REENTRY_CONFIRM_MS}ms; no BUY pre-submitted")

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        round_id = int(row["id"])
        had_order = bool(str(row.get("exit_order_id") or "").strip())
        try:
            intent = str(self._exit_intent(round_id) or "")
        except Exception:
            intent = str(row.get("exit_intent") or "")
        super()._exit_round(row, signal_ms)
        if had_order or intent != "POLY_DIRECTION_FLIP":
            return
        refreshed = self._round_state(round_id)
        if not isinstance(refreshed, dict):
            return
        if str(refreshed.get("exit_order_id") or "").strip() and int(refreshed.get("exit_placed_at_ms") or 0) > 0 and str(refreshed.get("state") or "") in {"EXIT_SYNC", "CLOSED"}:
            self._arm_reentry_guard(refreshed, int(refreshed["exit_placed_at_ms"]))

    def _reset_reentry_confirmation(self, reason: str, direction: str | None) -> None:
        guard = self._v40_reentry_guard
        if not isinstance(guard, dict):
            return
        if guard.get("confirmationStartedAtMs") is None and guard.get("lastResetReason") == reason:
            return
        guard.update(confirmationStartedAtMs=None, eligibleSinceMs=None, eligibleAnnounced=False,
                     lastResetReason=str(reason), lastDirection=direction)
        self._v40_reentry_resets += 1
        self._event("INFO", "REVERSAL_REENTRY_CONFIRM_RESET_V40", int(guard["marketId"]), int(guard["sourceRoundId"]),
                    f"reason={reason}; direction={direction or 'UNAVAILABLE/NEUTRAL'}; full {REVERSAL_REENTRY_CONFIRM_MS}ms confirmation required again")

    def _refresh_reentry_confirmation(self) -> str:
        guard = self._v40_reentry_guard
        if not isinstance(guard, dict):
            return "INACTIVE"
        market = self._prime_market()
        now_ms = base._now_ms()
        if not isinstance(market, dict) or int(market.get("market_id") or 0) != int(guard["marketId"]) or now_ms >= int(market.get("end_ms") or 0):
            self._v40_reentry_guard = None
            return "CLEARED_MARKET"
        poly = self._poly_state()
        direction = str((poly or {}).get("direction") or "").upper()
        target = str(guard.get("targetSide") or "").upper()
        receipt = (poly or {}).get("receivedTimestampMs")
        try:
            guard["lastPolyReceiptMs"] = int(receipt) if receipt is not None else None
        except (TypeError, ValueError):
            guard["lastPolyReceiptMs"] = None
        guard["lastDirection"] = direction or None
        started = guard.get("confirmationStartedAtMs")
        elapsed = max(0, now_ms - int(started)) if started is not None else 0
        policy = reversal_reentry_confirmation_policy(target_side=target, direction=direction, fresh=isinstance(poly, dict), elapsed_ms=elapsed)
        if policy.startswith("RESET_"):
            self._reset_reentry_confirmation(policy, direction or None)
            self.status = "REVERSAL_REENTRY_WAITING_FRESH_CONFIRM_V40"
            return policy
        if started is None:
            guard.update(confirmationStartedAtMs=now_ms, lastResetReason=None, eligibleSinceMs=None, eligibleAnnounced=False)
            self.status = "REVERSAL_REENTRY_CONFIRMING_V40"
            return "WAIT_CONFIRMATION"
        elapsed = max(0, now_ms - int(guard["confirmationStartedAtMs"]))
        if elapsed < REVERSAL_REENTRY_CONFIRM_MS:
            self.status = "REVERSAL_REENTRY_CONFIRMING_V40"
            self.last_error = f"reversal re-entry locked {REVERSAL_REENTRY_CONFIRM_MS - elapsed}ms; target={target}"
            return "WAIT_CONFIRMATION"
        guard["eligibleSinceMs"] = int(guard.get("eligibleSinceMs") or now_ms)
        if not guard.get("eligibleAnnounced"):
            guard["eligibleAnnounced"] = True
            self._v40_reentry_eligible += 1
            self._event("INFO", "REVERSAL_REENTRY_CONFIRM_ELIGIBLE_V40", int(guard["marketId"]), int(guard["sourceRoundId"]),
                        f"target={target} remained fresh/confident {elapsed}ms; next BUY must retain executable edge >= {REVERSAL_REENTRY_MIN_EDGE:.6f}")
        return "ELIGIBLE"

    def _direct_book(self, market: dict[str, Any], side: str) -> tuple[float | None, float | None, float]:
        guard = self._v40_reentry_guard
        if not isinstance(guard, dict):
            return super()._direct_book(market, side)
        if int(market.get("market_id") or 0) != int(guard["marketId"]):
            self._v40_reentry_guard = None
            return super()._direct_book(market, side)
        target = str(guard.get("targetSide") or "").upper()
        if self._refresh_reentry_confirmation() != "ELIGIBLE" or str(side or "").upper() != target:
            self._entry_price_blocked_this_tick = False
            self._entry_depth_blocked_this_tick = False
            self.status = "REVERSAL_REENTRY_BLOCKED_V40"
            return None, None, 0.0
        self._v40_force_market_reentry = True
        try:
            ask, ask_size, rtt_ms = super()._direct_book(market, target)
        finally:
            self._v40_force_market_reentry = False
        if ask is None:
            return ask, ask_size, rtt_ms
        poly = self._poly_state()
        direction = str((poly or {}).get("direction") or "").upper()
        selected = base._finite((poly or {}).get("selectedMid"))
        if not isinstance(poly, dict) or direction != target or selected is None:
            self._reset_reentry_confirmation("RESET_STALE_OR_DIRECTION_CHANGED_AFTER_BOOK", direction or None)
            return None, ask_size, rtt_ms
        edge = selected - float(ask)
        policy = reversal_reentry_edge_policy(edge)
        self._v40_last_reentry_check = {"marketId": int(market["market_id"]), "targetSide": target, "polySelectedMid": selected,
                                            "freshBinanceAsk": float(ask), "bookRttMs": rtt_ms, "bookEdge": edge,
                                            "minimumEdge": REVERSAL_REENTRY_MIN_EDGE, "policy": policy, "checkedAtMs": base._now_ms()}
        if policy != "ALLOW":
            self._v40_reentry_book_edge_blocks += 1
            self.status = "REVERSAL_REENTRY_BOOK_EDGE_BLOCKED_V40"
            self.last_error = f"fresh book edge {edge:+.6f} < {REVERSAL_REENTRY_MIN_EDGE:.6f}"
            return None, ask_size, rtt_ms
        return ask, ask_size, rtt_ms

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        guard = self._v40_reentry_guard
        if isinstance(guard, dict) and int(row.get("market_id") or 0) == int(guard.get("marketId") or -1):
            target = str(guard.get("targetSide") or "").upper()
            side = str(row.get("side") or "").upper()
            confirmation = self._refresh_reentry_confirmation()
            if side != target or confirmation != "ELIGIBLE":
                message = f"reversal re-entry BUY blocked; side={side}; target={target}; confirmation={confirmation}"
                self._update_round(int(row["id"]), state="REJECTED", close_reason="REVERSAL_REENTRY_LOCKED_V40",
                                   error_kind="REVERSAL_REENTRY_LOCKED_V40", error_message=message)
                self.status = "REVERSAL_REENTRY_BLOCKED_V40"
                self.last_error = message
                self._event("INFO", "REVERSAL_REENTRY_BUY_BLOCKED_V40", int(row["market_id"]), int(row["id"]), message)
                return
            target_reentry = True
        else:
            target_reentry = False
        if not target_reentry:
            return super()._open_round(row, poly)
        assert isinstance(guard, dict)
        guard["entryRoundId"] = int(row["id"])
        with self.lock:
            client = self.client
        if client is None:
            return super()._open_round(row, poly)
        original_place = client.place_market_order
        blocked: dict[str, Any] = {}
        def guarded_place(*args: Any, **kwargs: Any) -> dict[str, Any]:
            edge_after = base._finite((self.last_entry_latency or {}).get("edgeAfterQuote"))
            policy = reversal_reentry_edge_policy(edge_after)
            if policy != "ALLOW":
                blocked.update(policy=policy, edgeAfterQuote=edge_after)
                text = "unavailable" if edge_after is None else f"{edge_after:+.6f}"
                raise _ReversalReentryEdgeBlocked(f"reversal re-entry signed edge {text} must be >= {REVERSAL_REENTRY_MIN_EDGE:.6f}")
            return original_place(*args, **kwargs)
        client.place_market_order = guarded_place  # type: ignore[method-assign]
        self._v40_force_market_reentry = True
        try:
            super()._open_round(row, poly)
        finally:
            self._v40_force_market_reentry = False
            client.place_market_order = original_place  # type: ignore[method-assign]
        if blocked:
            edge_after = base._finite(blocked.get("edgeAfterQuote"))
            text = "unavailable" if edge_after is None else f"{edge_after:+.6f}"
            message = f"reversal re-entry BUY not submitted: signed executable edge {text} must be >= {REVERSAL_REENTRY_MIN_EDGE:.6f}"
            self._update_round(int(row["id"]), state="REJECTED", close_reason="REVERSAL_REENTRY_SIGNED_EDGE_BELOW_MIN_V40",
                               error_kind="REVERSAL_REENTRY_SIGNED_EDGE_BELOW_MIN_V40", error_message=message)
            self._v40_reentry_quote_edge_blocks += 1
            self.status = "REVERSAL_REENTRY_SIGNED_EDGE_BLOCKED_V40"
            self.last_error = message
            self._event("INFO", "REVERSAL_REENTRY_SIGNED_EDGE_BLOCKED_V40", int(row["market_id"]), int(row["id"]), message)

    def _sync_entry(self, row: dict[str, Any]) -> None:
        round_id = int(row["id"])
        super()._sync_entry(row)
        guard = self._v40_reentry_guard
        if not isinstance(guard, dict) or int(guard.get("entryRoundId") or -1) != round_id:
            return
        refreshed = self._round_state(round_id)
        if isinstance(refreshed, dict) and str(refreshed.get("state") or "") == "OPEN":
            self._v40_reentry_fills += 1
            self._event("INFO", "REVERSAL_REENTRY_CONFIRMED_V40", int(refreshed["market_id"]), round_id,
                        f"{refreshed.get('side')} re-entry confirmed after {REVERSAL_REENTRY_CONFIRM_MS}ms + >= {REVERSAL_REENTRY_MIN_EDGE:.6f} edge gates")
            self._v40_reentry_guard = None

    def _tick(self) -> None:
        if isinstance(self._v40_reentry_guard, dict):
            self._refresh_reentry_confirmation()
        return super()._tick()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V40"
        payload["reversalReentryV40"] = {
            "enabled": True, "firstFreshOppositeDirectionExitsImmediately": True,
            "oldExitDebounce500ms3ReceiptsApplied": False, "reentryConfirmMs": REVERSAL_REENTRY_CONFIRM_MS,
            "reentryMinimumExecutableEdge": REVERSAL_REENTRY_MIN_EDGE, "neutralOriginalOrStaleResetsFullConfirmation": True,
            "preSubmitOppositeBuyDuringCooldown": False, "freshPolyRecheckedAfterFreshBinanceBook": True,
            "signedQuoteEdgeMustAlsoMeetMinimum": True, "forceNormalMarketFokForReentry": True,
            "shotgunSavedSettingChanged": False, "shotgunRestingOrdersChanged": False,
            "immediateReversalExits": self._v40_immediate_reversal_exits, "confirmationResets": self._v40_reentry_resets,
            "eligibleConfirmations": self._v40_reentry_eligible, "freshBookEdgeBlocks": self._v40_reentry_book_edge_blocks,
            "signedQuoteEdgeBlocks": self._v40_reentry_quote_edge_blocks, "confirmedReentryFills": self._v40_reentry_fills,
            "active": dict(self._v40_reentry_guard or {}), "lastExecutableCheck": dict(self._v40_last_reentry_check or {}),
        }
        payload.setdefault("rules", {}).update(reversalExitImmediateV40=True, reversalExitStableDebounceDisabledV40=True,
            reversalReentryConfirmMs=REVERSAL_REENTRY_CONFIRM_MS, reversalReentryMinimumExecutableEdge=REVERSAL_REENTRY_MIN_EDGE,
            reversalReentrySignedQuoteMinimumEdge=REVERSAL_REENTRY_MIN_EDGE, reversalReentryForceNormalMarketFok=True,
            reversalReentryShotgunCancellationChanged=False)
        return payload

base.PolyGapLiveEngine = ImmediateExitCautiousReentryPolyGapLiveEngine

def main() -> int:
    return base.main()

if __name__ == "__main__":
    raise SystemExit(main())
