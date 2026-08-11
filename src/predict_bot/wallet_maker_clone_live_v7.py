from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_live_v6 as base


def paired_generation_ready(
    up: dict[str, Any] | None,
    down: dict[str, Any] | None,
    *,
    now_ms: int,
    delay_seconds: float = base.REPLENISH_DELAY_SECONDS,
    max_generations: int = base.MAX_GENERATIONS_PER_SIDE,
) -> tuple[bool, str]:
    """Return whether the next UP+DOWN generation may be created.

    V7 deliberately refuses unilateral replenishment.  Both latest parents must
    belong to the same generation, both must be venue-confirmed FILLED, and the
    delay is measured from the later terminal timestamp.  This bounds the
    adverse-selection failure observed in the first V6 Echtgeld round where one
    outcome repeatedly refilled while its complement never caught up.
    """

    if not isinstance(up, dict) or not isinstance(down, dict):
        return False, "WAITING_BOTH_SIDES"

    up_gen = int(up.get("generation") or 1)
    down_gen = int(down.get("generation") or 1)
    if up_gen != down_gen:
        return False, "GENERATION_MISMATCH"
    if up_gen >= int(max_generations):
        return False, "GENERATION_CAP_REACHED"

    up_state = str(up.get("state") or "")
    down_state = str(down.get("state") or "")
    if up_state != "FILLED" or down_state != "FILLED":
        if up_state == "FILLED" or down_state == "FILLED":
            return False, "WAITING_COMPLEMENT_FILL"
        return False, "WAITING_PAIR_FILL"

    up_terminal = int(up.get("terminal_at_ms") or 0)
    down_terminal = int(down.get("terminal_at_ms") or 0)
    if up_terminal <= 0 or down_terminal <= 0:
        return False, "WAITING_TERMINAL_TIMESTAMPS"

    ready_at = max(up_terminal, down_terminal) + int(float(delay_seconds) * 1000.0)
    if int(now_ms) < ready_at:
        return False, "PAIR_REPLENISH_COOLDOWN"
    return True, "READY"


class PairedCycleWalletMakerCloneEngine(base.SequentialReplenishmentWalletMakerCloneEngine):
    """V7 Echtgeld experiment: replenish only after the pair is complete.

    V6 independently replenished an outcome as soon as that side filled.  In a
    live market the side being adversely selected can therefore fill repeatedly
    while the complementary resting order remains untouched, causing inventory
    imbalance to compound.  V7 changes only this trigger:

    * generation 1 remains V5's neutral 0.50/0.50 concurrent pair;
    * later generations still quote each outcome at its then-current passive bid;
    * no outcome can replenish independently;
    * generation N+1 is submitted concurrently only after BOTH generation N
      parents are confirmed FILLED;
    * the 3 second delay starts from the later of the two fill timestamps;
    * any incomplete/ambiguous two-leg replenishment is cancelled and runtime is
      auto-paused instead of allowing a one-sided next generation.

    This is intentionally a paired-cycle experiment, not a claim that the target
    wallet uses this exact rule.  It is designed to test whether the target's
    many makerHash parents can be reproduced without letting one-sided fills run
    away during adverse selection.
    """

    VERSION = "WALLET_MAKER_CLONE_LIVE_V7_PAIRED_CYCLE"

    def _place_replenishment_pair(
        self,
        pair: dict[str, Any],
        market: dict[str, Any],
    ) -> bool:
        plans = {side: self._plan_replenishment(side, market) for side in ("UP", "DOWN")}
        if any(plan is None for plan in plans.values()):
            self.status = "WAITING_BOTH_REPLENISH_BOOKS"
            return False
        blocked = [plan for plan in plans.values() if isinstance(plan, dict) and plan.get("blocked")]
        if blocked:
            self.status = "PAIR_REPLENISH_POST_ONLY_BLOCKED"
            for plan in blocked:
                self._event(
                    "INFO",
                    "PAIR_REPLENISH_POST_ONLY_BLOCK_V7",
                    int(market["market_id"]),
                    int(pair["id"]),
                    str(plan.get("reason")),
                )
            return False

        row_ids: dict[str, int] = {}
        try:
            for side in ("UP", "DOWN"):
                row_ids[side] = self._insert_order_plan(
                    int(pair["id"]),
                    int(market["market_id"]),
                    plans[side] or {},
                )
        except Exception as exc:
            self._set_setting("runtime_enabled", "0")
            self.status = "PAIR_REPLENISH_PLAN_FAILED"
            self._event(
                "ERROR",
                "PAIR_REPLENISH_PLAN_FAILED_V7",
                int(market["market_id"]),
                int(pair["id"]),
                str(exc)[:500],
            )
            return False

        generations: dict[str, int] = {}
        with self.db_lock:
            for side, row_id in row_ids.items():
                row = self.db.execute(
                    "SELECT generation FROM wallet_maker_clone_orders WHERE id=?",
                    (int(row_id),),
                ).fetchone()
                generations[side] = int(row["generation"] if row else 0)

        if generations.get("UP") != generations.get("DOWN"):
            self._set_setting("runtime_enabled", "0")
            self.status = "GENERATION_MISMATCH_MANUAL_RECONCILIATION"
            self._event(
                "ERROR",
                "PAIR_REPLENISH_GENERATION_MISMATCH_V7",
                int(market["market_id"]),
                int(pair["id"]),
                f"generations={generations}; no orders submitted",
            )
            return False

        generation = int(generations.get("UP") or 0)
        self._event(
            "WARN",
            "PAIR_REPLENISH_PLACE_STARTED_V7",
            int(market["market_id"]),
            int(pair["id"]),
            (
                f"generation={generation}; "
                f"UP@{float((plans['UP'] or {})['price']):.4f} cost={float((plans['UP'] or {})['plannedCost']):.4f}; "
                f"DOWN@{float((plans['DOWN'] or {})['price']):.4f} cost={float((plans['DOWN'] or {})['plannedCost']):.4f}"
            ),
        )

        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="clone-paired-replenish") as pool:
            futures = {
                pool.submit(self._place_one, row_ids[side], plans[side] or {}, market): side
                for side in ("UP", "DOWN")
            }
            for future in as_completed(futures):
                side = futures[future]
                try:
                    results[side] = future.result()
                except Exception as exc:
                    results[side] = {
                        "ok": False,
                        "completedAtMs": core._now_ms(),
                        "error": str(exc)[:500],
                    }

        all_ok = all(bool(results.get(side, {}).get("ok")) for side in ("UP", "DOWN"))
        if all_ok:
            self._event(
                "INFO",
                "PAIR_REPLENISH_PLACE_COMPLETED_V7",
                int(market["market_id"]),
                int(pair["id"]),
                f"generation={generation}; UP={results.get('UP')}; DOWN={results.get('DOWN')}",
            )
            self.status = "PAIRED_REPLENISH_ACTIVE"
            return True

        # A new paired cycle must never degrade into a unilateral cycle.  Cancel
        # whichever leg was successfully recorded and require operator review.
        self._set_setting("runtime_enabled", "0")
        self._event(
            "ERROR",
            "PAIR_REPLENISH_INCOMPLETE_AUTO_PAUSE_V7",
            int(market["market_id"]),
            int(pair["id"]),
            f"generation={generation}; UP={results.get('UP')}; DOWN={results.get('DOWN')}",
        )
        self._cancel_pair(int(pair["id"]), "PAIR_REPLENISH_INCOMPLETE_V7")
        self.status = "PAIR_REPLENISH_INCOMPLETE_MANUAL_RECONCILIATION"
        return False

    def _tick(self) -> None:
        settings = self._settings()
        if not core.MASTER_ENABLED:
            self.status = "MASTER_DISABLED"
            return
        if not self._ensure_client():
            return

        interlock = self._normal_live_conflict()
        if interlock.get("blocked") is True:
            if settings["runtimeEnabled"]:
                self._set_setting("runtime_enabled", "0")
                self._event(
                    "ERROR",
                    "NORMAL_LIVE_INTERLOCK_TRIPPED",
                    None,
                    None,
                    str(interlock.get("reason")),
                )
                self._cancel_recorded_active("NORMAL_LIVE_INTERLOCK")
            self.status = "BLOCKED_NORMAL_LIVE"
            return

        market = self._prime_market()
        if market is None:
            self.status = "WAITING_MARKET"
            return
        self._poll_books(market)

        pair = self._current_pair()
        if pair is not None and int(pair["market_id"]) != int(market["market_id"]):
            if str(pair.get("state") or "") != "CANCEL_PENDING":
                self._cancel_pair(int(pair["id"]), "MARKET_ROLLOVER")
            if not self._final_reconcile_pair(pair):
                self.status = "ROLLOVER_RECONCILING_FINAL_FILLS"
                return
            self._set_pair(int(pair["id"]), state="ROLLED", close_reason="MARKET_ROLLOVER")
            pair = None

        if pair is None:
            pair = self._new_pair(market)

        if str(pair.get("state") or "") == "CANCEL_PENDING":
            if not self._final_reconcile_pair(pair):
                self.status = "CANCEL_PENDING_RECONCILIATION"
                return
            pair = self._current_pair() or pair

        self._reconcile_pair(pair, market)
        pair = self._current_pair() or pair

        now_ms = core._now_ms()
        seconds_left = base.base.base.market_seconds_left(market, now_ms)
        orders = self._pair_orders(int(pair["id"]))
        live_orders = [
            row
            for row in orders
            if row.get("order_id")
            and str(row.get("state") or "") not in base.base.base.TERMINAL_ORDER_STATES
        ]
        if seconds_left <= base.base.base.RESTING_CANCEL_SECONDS:
            if live_orders:
                self._cancel_pair(int(pair["id"]), "PRE_SETTLEMENT_CUTOFF_V7")
                self._final_reconcile_pair(pair)
                self.status = "PRE_SETTLEMENT_CANCEL_PENDING"
            else:
                self.status = "PRE_SETTLEMENT_CUTOFF"
            return

        if not settings["runtimeEnabled"]:
            self.status = "PAUSED"
            return
        if seconds_left < float(settings["minimumRemainingSeconds"]):
            self.status = "WAITING_NEXT_MARKET"
            return

        if not orders:
            if self._complementary_anchor() is None:
                self.status = "WAITING_BOTH_BOOKS"
                return
            # Generation 1 is still placed concurrently from the neutral anchor.
            super(base.SequentialReplenishmentWalletMakerCloneEngine, self)._place_pair(pair, market)
            self.status = "PAIR_ACTIVE"
            return

        latest_up = self._latest_side_order(int(pair["id"]), "UP")
        latest_down = self._latest_side_order(int(pair["id"]), "DOWN")
        ready, reason = paired_generation_ready(
            latest_up,
            latest_down,
            now_ms=now_ms,
        )
        if ready:
            self._place_replenishment_pair(pair, market)
            return

        self.status = reason

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        pair = self._current_pair()
        if pair is not None:
            up = self._latest_side_order(int(pair["id"]), "UP")
            down = self._latest_side_order(int(pair["id"]), "DOWN")
            ready, reason = paired_generation_ready(up, down, now_ms=core._now_ms())
        else:
            ready, reason = False, "NO_CURRENT_PAIR"
        payload["pairedCycle"] = {
            "nextGenerationReady": ready,
            "gateReason": reason,
            "requiresBothLatestParentsFilled": True,
            "unilateralReplenishmentAllowed": False,
        }
        payload.setdefault("rules", {}).update(
            experimentV7="PAIRED_CYCLE_REPLENISHMENT",
            replenishTrigger="BOTH_LATEST_GENERATION_PARENTS_FILLED",
            replenishDelayFrom="LATER_OF_UP_DOWN_TERMINAL_TIMESTAMPS",
            unilateralReplenishmentAllowed=False,
            incompleteReplenishmentPairAutoPause=True,
            maximumParentGenerationsPerMarketOutcome=base.MAX_GENERATIONS_PER_SIDE,
        )
        return payload


# Patch V6 -> V5 -> V4 -> V3 -> V2 -> V1 entrypoint chain.
base.WalletMakerCloneEngine = PairedCycleWalletMakerCloneEngine
base.base.WalletMakerCloneEngine = PairedCycleWalletMakerCloneEngine
base.base.base.WalletMakerCloneEngine = PairedCycleWalletMakerCloneEngine
base.base.base.base.WalletMakerCloneEngine = PairedCycleWalletMakerCloneEngine
base.base.base.base.base.WalletMakerCloneEngine = PairedCycleWalletMakerCloneEngine
base.base.base.base.base.base.WalletMakerCloneEngine = PairedCycleWalletMakerCloneEngine


def main() -> int:
    return base.base.base.base.base.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
