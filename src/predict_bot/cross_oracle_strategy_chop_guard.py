from __future__ import annotations

import os
import time
from typing import Any

from . import cross_oracle_strategy_stable_exit as stable


CHOP_GUARD_REVERSALS_PER_MARKET = max(
    2,
    int(os.environ.get("PREDICT_POLY_CHOP_GUARD_REVERSALS_PER_MARKET", "2")),
)
CHOP_GUARD_TRIGGER_WINDOW_MARKETS = max(
    2,
    int(os.environ.get("PREDICT_POLY_CHOP_GUARD_TRIGGER_WINDOW_MARKETS", "3")),
)
CHOP_GUARD_TRIGGER_CHOPPY_MARKETS = max(
    1,
    min(
        CHOP_GUARD_TRIGGER_WINDOW_MARKETS,
        int(os.environ.get("PREDICT_POLY_CHOP_GUARD_TRIGGER_CHOPPY_MARKETS", "2")),
    ),
)
CHOP_GUARD_RESUME_CALM_MARKETS = max(
    2,
    int(os.environ.get("PREDICT_POLY_CHOP_GUARD_RESUME_CALM_MARKETS", "3")),
)
CHOP_GUARD_MIN_DISTINCT_RECEIPTS = max(
    stable.STABLE_EXIT_CONFIRM_SAMPLES,
    int(os.environ.get("PREDICT_POLY_CHOP_GUARD_MIN_DISTINCT_RECEIPTS", "3")),
)


class ChopGuardPaperEngine(stable.StableExitComparisonPaperEngine):
    """Paper-owned market-regime guard for repeated confirmed retracements.

    The guard is deliberately independent of live fills/PnL.  It observes the
    same fresh Polymarket receipt stream used by the stable-exit Paper variant
    and counts only reversals that satisfy the same stable confirmation semantics
    (500 ms + three distinct receipt timestamps by default, with the same strong
    emergency threshold).

    A completed/evaluable five-minute market is CHOPPY after >=2 confirmed
    reversals (UP->DOWN->UP, or the mirror image).  Live should pause new entries
    when 2 of the last 3 evaluable markets are CHOPPY.  Once paused, hysteresis is
    intentionally conservative: three consecutive CALM evaluable markets are
    required before the guard resumes.  Non-evaluable/feed-gap markets neither
    trigger nor clear the pause.

    State and completed market classifications are persisted in cross_oracle.db,
    so restarting the API cannot silently clear a risk pause.
    """

    def __init__(self, db_path: Any, provider: Any) -> None:
        self._chop_current: dict[str, Any] | None = None
        self._chop_candidate: dict[str, Any] | None = None
        super().__init__(db_path, provider)

    def _create_schema(self) -> None:
        super()._create_schema()
        now_ms = int(time.time() * 1000)
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS poly_chop_guard_markets (
                    market_id INTEGER PRIMARY KEY,
                    poly_market_slug TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confirmed_reversals INTEGER NOT NULL DEFAULT 0,
                    distinct_receipts INTEGER NOT NULL DEFAULT 0,
                    evaluable INTEGER NOT NULL DEFAULT 1,
                    first_seen_at_ms INTEGER NOT NULL,
                    last_seen_at_ms INTEGER NOT NULL,
                    finalized_at_ms INTEGER,
                    reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_poly_chop_guard_finalized
                    ON poly_chop_guard_markets(status, market_id DESC);
                CREATE TABLE IF NOT EXISTS poly_chop_guard_state (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    paused INTEGER NOT NULL DEFAULT 0,
                    changed_at_ms INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT 'INITIAL'
                );
                """
            )
            self.db.execute(
                """INSERT OR IGNORE INTO poly_chop_guard_state(id,paused,changed_at_ms,reason)
                   VALUES(1,0,?,'INITIAL')""",
                (now_ms,),
            )
            # A partially observed market cannot be reconstructed exactly after a
            # process restart.  Preserve it as non-evaluable rather than letting a
            # restart manufacture a calm market and clear a live pause.
            self.db.execute(
                """UPDATE poly_chop_guard_markets
                      SET status='NOT_EVALUABLE', evaluable=0, finalized_at_ms=?,
                          reason='PROCESS_RESTART_DATA_GAP'
                    WHERE status='ACTIVE'""",
                (now_ms,),
            )
            self.db.commit()

    def _persist_current(self) -> None:
        current = self._chop_current
        if current is None:
            return
        with self.db_lock:
            self.db.execute(
                """INSERT INTO poly_chop_guard_markets(
                       market_id,poly_market_slug,status,confirmed_reversals,
                       distinct_receipts,evaluable,first_seen_at_ms,last_seen_at_ms,
                       finalized_at_ms,reason
                   ) VALUES(?,?,'ACTIVE',?,?,?,?,?,NULL,?)
                   ON CONFLICT(market_id) DO UPDATE SET
                       poly_market_slug=excluded.poly_market_slug,
                       status='ACTIVE',
                       confirmed_reversals=excluded.confirmed_reversals,
                       distinct_receipts=excluded.distinct_receipts,
                       evaluable=excluded.evaluable,
                       first_seen_at_ms=MIN(poly_chop_guard_markets.first_seen_at_ms, excluded.first_seen_at_ms),
                       last_seen_at_ms=excluded.last_seen_at_ms,
                       finalized_at_ms=NULL,
                       reason=excluded.reason""",
                (
                    int(current["marketId"]),
                    str(current["polySlug"]),
                    int(current.get("reversals") or 0),
                    int(current.get("distinctReceipts") or 0),
                    1 if current.get("evaluable", True) else 0,
                    int(current["firstSeenAtMs"]),
                    int(current["lastSeenAtMs"]),
                    current.get("reason"),
                ),
            )
            self.db.commit()

    def _start_chop_market(
        self,
        market_id: int,
        poly_slug: str,
        direction: str,
        receipt_ms: int,
        now_ms: int,
    ) -> None:
        restart_gap = False
        with self.db_lock:
            prior = self.db.execute(
                "SELECT status,reason FROM poly_chop_guard_markets WHERE market_id=?",
                (int(market_id),),
            ).fetchone()
        if prior is not None and str(prior["reason"] or "") == "PROCESS_RESTART_DATA_GAP":
            restart_gap = True
        self._chop_candidate = None
        self._chop_current = {
            "marketId": int(market_id),
            "polySlug": str(poly_slug),
            "baselineDirection": str(direction),
            "reversals": 0,
            "distinctReceipts": 1,
            "lastReceiptMs": int(receipt_ms),
            "firstSeenAtMs": int(now_ms),
            "lastSeenAtMs": int(now_ms),
            "evaluable": not restart_gap,
            "reason": "PROCESS_RESTART_DATA_GAP" if restart_gap else None,
        }
        self._persist_current()

    def _mark_current_not_evaluable(self, reason: str) -> None:
        if self._chop_current is None:
            return
        self._chop_current["evaluable"] = False
        self._chop_current["reason"] = str(reason)[:240]
        self._chop_candidate = None
        self._persist_current()

    @staticmethod
    def _emergency_reversal(baseline: str, poly_up_mid: float) -> bool:
        if baseline == "UP":
            return poly_up_mid <= stable.STABLE_EXIT_EMERGENCY_DOWN_MID
        if baseline == "DOWN":
            return poly_up_mid >= stable.STABLE_EXIT_EMERGENCY_UP_MID
        return False

    def _confirm_chop_reversal(self, direction: str, now_ms: int) -> None:
        current = self._chop_current
        if current is None:
            return
        current["baselineDirection"] = str(direction)
        current["reversals"] = int(current.get("reversals") or 0) + 1
        current["lastSeenAtMs"] = int(now_ms)
        self._chop_candidate = None
        self._persist_current()

    def _observe_chop_sample(
        self,
        *,
        market_id: int,
        poly_slug: str,
        direction: str,
        poly_up_mid: float,
        receipt_ms: int,
        now_ms: int,
    ) -> None:
        current = self._chop_current
        if current is None or int(current["marketId"]) != int(market_id):
            if current is not None:
                self._finalize_chop_market(current)
            self._start_chop_market(market_id, poly_slug, direction, receipt_ms, now_ms)
            return

        last_receipt = int(current.get("lastReceiptMs") or 0)
        if receipt_ms <= last_receipt:
            return
        current["lastReceiptMs"] = int(receipt_ms)
        current["distinctReceipts"] = int(current.get("distinctReceipts") or 0) + 1
        current["lastSeenAtMs"] = int(now_ms)

        baseline = str(current.get("baselineDirection") or "")
        if direction == baseline:
            self._chop_candidate = None
            self._persist_current()
            return

        if self._emergency_reversal(baseline, poly_up_mid):
            self._confirm_chop_reversal(direction, now_ms)
            return

        candidate = self._chop_candidate
        same = bool(candidate and str(candidate.get("direction") or "") == direction)
        if not same:
            self._chop_candidate = {
                "direction": str(direction),
                "firstSeenAtMs": int(now_ms),
                "lastReceiptMs": int(receipt_ms),
                "samples": 1,
            }
            self._persist_current()
            return

        assert candidate is not None
        candidate_last_receipt = int(candidate.get("lastReceiptMs") or 0)
        if receipt_ms - candidate_last_receipt > stable.STABLE_EXIT_MAX_SAMPLE_GAP_MS:
            self._chop_candidate = {
                "direction": str(direction),
                "firstSeenAtMs": int(now_ms),
                "lastReceiptMs": int(receipt_ms),
                "samples": 1,
            }
            self._persist_current()
            return

        candidate["lastReceiptMs"] = int(receipt_ms)
        candidate["samples"] = int(candidate.get("samples") or 1) + 1
        elapsed_ms = int(now_ms) - int(candidate["firstSeenAtMs"])
        if (
            int(candidate["samples"]) >= stable.STABLE_EXIT_CONFIRM_SAMPLES
            and elapsed_ms >= stable.STABLE_EXIT_CONFIRM_MS
        ):
            self._confirm_chop_reversal(direction, now_ms)
        else:
            self._persist_current()

    def _guard_state_row(self) -> dict[str, Any]:
        with self.db_lock:
            row = self.db.execute(
                "SELECT paused,changed_at_ms,reason FROM poly_chop_guard_state WHERE id=1"
            ).fetchone()
        return dict(row) if row is not None else {
            "paused": 0,
            "changed_at_ms": int(time.time() * 1000),
            "reason": "INITIAL",
        }

    def _recent_evaluable(self, limit: int) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT * FROM poly_chop_guard_markets
                    WHERE finalized_at_ms IS NOT NULL AND evaluable=1
                      AND status IN ('CALM','CHOPPY')
                    ORDER BY market_id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _set_guard_paused(self, paused: bool, reason: str) -> None:
        now_ms = int(time.time() * 1000)
        current = self._guard_state_row()
        if bool(current.get("paused")) == bool(paused):
            return
        with self.db_lock:
            self.db.execute(
                """UPDATE poly_chop_guard_state
                      SET paused=?, changed_at_ms=?, reason=? WHERE id=1""",
                (1 if paused else 0, now_ms, str(reason)[:500]),
            )
            self.db.commit()

    def _recompute_guard_pause(self) -> None:
        state = self._guard_state_row()
        paused = bool(state.get("paused"))
        needed = max(CHOP_GUARD_TRIGGER_WINDOW_MARKETS, CHOP_GUARD_RESUME_CALM_MARKETS)
        recent = self._recent_evaluable(needed)

        if not paused:
            trigger = recent[:CHOP_GUARD_TRIGGER_WINDOW_MARKETS]
            if len(trigger) < CHOP_GUARD_TRIGGER_WINDOW_MARKETS:
                return
            choppy = sum(str(row.get("status")) == "CHOPPY" for row in trigger)
            if choppy >= CHOP_GUARD_TRIGGER_CHOPPY_MARKETS:
                ids = [int(row["market_id"]) for row in trigger]
                self._set_guard_paused(
                    True,
                    f"{choppy}/{CHOP_GUARD_TRIGGER_WINDOW_MARKETS} recent evaluable markets CHOPPY; markets={ids}",
                )
            return

        recovery = recent[:CHOP_GUARD_RESUME_CALM_MARKETS]
        if (
            len(recovery) >= CHOP_GUARD_RESUME_CALM_MARKETS
            and all(str(row.get("status")) == "CALM" for row in recovery)
        ):
            ids = [int(row["market_id"]) for row in recovery]
            self._set_guard_paused(
                False,
                f"{CHOP_GUARD_RESUME_CALM_MARKETS} consecutive evaluable CALM markets confirmed; markets={ids}",
            )

    def _finalize_chop_market(self, current: dict[str, Any]) -> None:
        evaluable = bool(current.get("evaluable", True)) and int(
            current.get("distinctReceipts") or 0
        ) >= CHOP_GUARD_MIN_DISTINCT_RECEIPTS
        reversals = int(current.get("reversals") or 0)
        if not evaluable:
            status = "NOT_EVALUABLE"
            reason = current.get("reason") or "INSUFFICIENT_FRESH_RECEIPTS"
        elif reversals >= CHOP_GUARD_REVERSALS_PER_MARKET:
            status = "CHOPPY"
            reason = f"confirmedReversals={reversals} >= {CHOP_GUARD_REVERSALS_PER_MARKET}"
        else:
            status = "CALM"
            reason = f"confirmedReversals={reversals} < {CHOP_GUARD_REVERSALS_PER_MARKET}"
        finalized_ms = int(time.time() * 1000)
        with self.db_lock:
            self.db.execute(
                """INSERT INTO poly_chop_guard_markets(
                       market_id,poly_market_slug,status,confirmed_reversals,
                       distinct_receipts,evaluable,first_seen_at_ms,last_seen_at_ms,
                       finalized_at_ms,reason
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market_id) DO UPDATE SET
                       poly_market_slug=excluded.poly_market_slug,
                       status=excluded.status,
                       confirmed_reversals=excluded.confirmed_reversals,
                       distinct_receipts=excluded.distinct_receipts,
                       evaluable=excluded.evaluable,
                       first_seen_at_ms=MIN(poly_chop_guard_markets.first_seen_at_ms, excluded.first_seen_at_ms),
                       last_seen_at_ms=excluded.last_seen_at_ms,
                       finalized_at_ms=excluded.finalized_at_ms,
                       reason=excluded.reason""",
                (
                    int(current["marketId"]),
                    str(current["polySlug"]),
                    status,
                    reversals,
                    int(current.get("distinctReceipts") or 0),
                    1 if evaluable else 0,
                    int(current["firstSeenAtMs"]),
                    int(current["lastSeenAtMs"]),
                    finalized_ms,
                    str(reason)[:500],
                ),
            )
            self.db.commit()
        self._chop_candidate = None
        self._chop_current = None
        self._recompute_guard_pause()

    def _invalidate_gap_sensitive_positions(self, continuity: dict[str, Any]) -> None:
        super()._invalidate_gap_sensitive_positions(continuity)
        current = self._chop_current
        if current is None:
            return
        affected = self._gap_affects_current_slug(continuity)
        if affected and str(current.get("polySlug")) == str(affected):
            self._mark_current_not_evaluable("POLY_FEED_GAP")

    def _evaluate_once(self) -> None:
        super()._evaluate_once()
        with self.lock:
            runtime = dict(self.runtime)
        if runtime.get("aligned") is not True:
            return
        direction = runtime.get("polyDirection")
        if direction not in {"UP", "DOWN"}:
            return
        market_id_raw = runtime.get("binanceMarketId")
        poly_slug = str(runtime.get("polyMarketSlug") or "")
        poly_up_mid_raw = runtime.get("polyUpMid")
        receipt_ms = self._stable_last_poly_receipt_ms
        try:
            market_id = int(market_id_raw)
            poly_up_mid = float(poly_up_mid_raw)
            receipt = int(receipt_ms) if receipt_ms is not None else None
        except (TypeError, ValueError):
            return
        if market_id <= 0 or not poly_slug or receipt is None:
            return
        self._observe_chop_sample(
            market_id=market_id,
            poly_slug=poly_slug,
            direction=str(direction),
            poly_up_mid=poly_up_mid,
            receipt_ms=receipt,
            now_ms=int(runtime.get("updatedAtMs") or time.time() * 1000),
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        state = self._guard_state_row()
        recent = self._recent_evaluable(max(6, CHOP_GUARD_RESUME_CALM_MARKETS))
        current = dict(self._chop_current) if self._chop_current else None
        payload["chopGuard"] = {
            "enabled": True,
            "paperOwned": True,
            "paused": bool(state.get("paused")),
            "reason": state.get("reason"),
            "stateChangedAtMs": int(state.get("changed_at_ms") or 0),
            "evaluatedAtMs": int(time.time() * 1000),
            "marketClassification": {
                "choppyAtConfirmedReversals": CHOP_GUARD_REVERSALS_PER_MARKET,
                "confirmationMs": stable.STABLE_EXIT_CONFIRM_MS,
                "confirmationDistinctReceipts": stable.STABLE_EXIT_CONFIRM_SAMPLES,
                "minimumDistinctReceiptsForEvaluableMarket": CHOP_GUARD_MIN_DISTINCT_RECEIPTS,
            },
            "pauseRule": {
                "windowMarkets": CHOP_GUARD_TRIGGER_WINDOW_MARKETS,
                "choppyMarketsRequired": CHOP_GUARD_TRIGGER_CHOPPY_MARKETS,
            },
            "resumeRule": {
                "consecutiveCalmMarketsRequired": CHOP_GUARD_RESUME_CALM_MARKETS,
            },
            "currentMarket": current,
            "currentCandidate": dict(self._chop_candidate) if self._chop_candidate else None,
            "recentEvaluableMarkets": recent,
            "persistsAcrossApiRestart": True,
            "nonEvaluableMarketsDoNotClearPause": True,
            "livePolicy": "pause new entries only; never interfere with managing an existing position",
        }
        return payload


# Build on the stable-exit Paper wrapper and replace only the engine class that
# the existing sidecar launcher constructs.  Original R_POLY_GAP_SCALP and the
# R_POLY_GAP_SCALP_STABLE A/B ledger remain unchanged.
stable.launch.strategy_module.GapAwareCrossOraclePaperEngine = ChopGuardPaperEngine


def main() -> int:
    return stable.main()


if __name__ == "__main__":
    raise SystemExit(main())
