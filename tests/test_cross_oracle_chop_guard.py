from __future__ import annotations

from pathlib import Path

from predict_bot.cross_oracle_strategy_chop_guard import ChopGuardPaperEngine


def engine(tmp_path: Path, name: str = "guard.db") -> ChopGuardPaperEngine:
    return ChopGuardPaperEngine(tmp_path / name, lambda: {})


def finalize_market(
    guard: ChopGuardPaperEngine,
    market_id: int,
    *,
    reversals: int,
    evaluable: bool = True,
) -> None:
    current = {
        "marketId": market_id,
        "polySlug": f"btc-updown-5m-{market_id}",
        "baselineDirection": "UP",
        "reversals": reversals,
        "distinctReceipts": 10,
        "lastReceiptMs": market_id * 1000 + 900,
        "firstSeenAtMs": market_id * 1000,
        "lastSeenAtMs": market_id * 1000 + 900,
        "evaluable": evaluable,
        "reason": None if evaluable else "TEST_GAP",
    }
    guard._chop_current = current
    guard._finalize_chop_market(current)


def test_two_of_three_choppy_markets_pause_then_three_calm_resume(tmp_path: Path) -> None:
    guard = engine(tmp_path)
    try:
        finalize_market(guard, 101, reversals=2)
        finalize_market(guard, 102, reversals=0)
        finalize_market(guard, 103, reversals=2)
        state = guard.snapshot()["chopGuard"]
        assert state["paused"] is True
        assert [row["status"] for row in state["recentEvaluableMarkets"][:3]] == [
            "CHOPPY",
            "CALM",
            "CHOPPY",
        ]

        finalize_market(guard, 104, reversals=0)
        finalize_market(guard, 105, reversals=1)
        # Only two consecutive calm markets so far; hysteresis must keep the pause.
        assert guard.snapshot()["chopGuard"]["paused"] is True

        finalize_market(guard, 106, reversals=0)
        state = guard.snapshot()["chopGuard"]
        assert state["paused"] is False
        assert [row["status"] for row in state["recentEvaluableMarkets"][:3]] == [
            "CALM",
            "CALM",
            "CALM",
        ]
    finally:
        guard.stop()


def test_non_evaluable_market_cannot_clear_pause(tmp_path: Path) -> None:
    guard = engine(tmp_path)
    try:
        finalize_market(guard, 201, reversals=2)
        finalize_market(guard, 202, reversals=0)
        finalize_market(guard, 203, reversals=2)
        assert guard.snapshot()["chopGuard"]["paused"] is True

        finalize_market(guard, 204, reversals=0)
        finalize_market(guard, 205, reversals=0, evaluable=False)
        finalize_market(guard, 206, reversals=0)
        # 205 is excluded; only two evaluable calm markets follow the pause.
        assert guard.snapshot()["chopGuard"]["paused"] is True
    finally:
        guard.stop()


def test_pause_state_persists_across_engine_restart(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    first = ChopGuardPaperEngine(path, lambda: {})
    try:
        finalize_market(first, 301, reversals=2)
        finalize_market(first, 302, reversals=0)
        finalize_market(first, 303, reversals=2)
        assert first.snapshot()["chopGuard"]["paused"] is True
    finally:
        first.stop()

    second = ChopGuardPaperEngine(path, lambda: {})
    try:
        assert second.snapshot()["chopGuard"]["paused"] is True
        assert second.snapshot()["chopGuard"]["persistsAcrossApiRestart"] is True
    finally:
        second.stop()


def test_reversal_counter_requires_stable_distinct_receipts(tmp_path: Path) -> None:
    guard = engine(tmp_path, "receipts.db")
    try:
        guard._start_chop_market(401, "btc-updown-5m-401", "UP", 1_000, 1_000)
        # Same receipt repeated cannot advance the regime reversal.
        guard._observe_chop_sample(
            market_id=401,
            poly_slug="btc-updown-5m-401",
            direction="DOWN",
            poly_up_mid=0.40,
            receipt_ms=1_200,
            now_ms=1_200,
        )
        guard._observe_chop_sample(
            market_id=401,
            poly_slug="btc-updown-5m-401",
            direction="DOWN",
            poly_up_mid=0.40,
            receipt_ms=1_200,
            now_ms=1_450,
        )
        assert int(guard._chop_current["reversals"]) == 0

        guard._observe_chop_sample(
            market_id=401,
            poly_slug="btc-updown-5m-401",
            direction="DOWN",
            poly_up_mid=0.40,
            receipt_ms=1_450,
            now_ms=1_450,
        )
        guard._observe_chop_sample(
            market_id=401,
            poly_slug="btc-updown-5m-401",
            direction="DOWN",
            poly_up_mid=0.40,
            receipt_ms=1_800,
            now_ms=1_800,
        )
        assert int(guard._chop_current["reversals"]) == 1
    finally:
        guard.stop()
