from __future__ import annotations

import sqlite3
import threading

import pytest

from predict_bot import predict_wallet_shadow_observer_v4_21 as v4_21
from predict_bot import target_taker_multi_entry_paper_v1 as multi


class _BaseHarness:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db_lock = threading.RLock()
        self.retention_ms = 7 * 24 * 60 * 60 * 1000
        self.pending_settlement_ids: set[int] = set()
        self.market_id: int | None = None
        self.latest_public_signal_snapshot = None
        self.public_side_model = object()

    def _reset_market(self, market_id: int, bucket: int | None, title: str | None) -> None:
        self.market_id = int(market_id)

    def _advance_shadow(self, book, core) -> None:
        return None

    def _seed_pending_settlements(self) -> None:
        return None

    def _store_market_result(self, market_id: int, market: dict, winner: str) -> None:
        return None

    def _cleanup_retention(self, *, force: bool = False) -> None:
        return None

    def snapshot(self) -> dict:
        return {"targetTakerPublicSideV1Lab": {"abTest": {}}}

    def health_snapshot(self) -> dict:
        return {}


class _Harness(multi.MultiEntryPaperMixin, _BaseHarness):
    pass


def _trade(side: str, ask: float) -> dict:
    return {
        "decision": "TRADE",
        "reason": "PUBLIC_SIDE_EBM_MATCH",
        "side": side,
        "ask": ask,
        "secondsLeft": 120.0,
        "signal": {
            "score": 0.7 if side == "UP" else -0.7,
            "probabilityUp": 0.85 if side == "UP" else 0.15,
            "selectedProbability": 0.85,
        },
    }


def test_first_market_after_deploy_is_excluded_then_next_is_active() -> None:
    observer = _Harness()
    observer._reset_market(100, None, "first")
    assert observer.target_taker_multi_entry_state["active"] is False
    assert observer.target_taker_multi_entry_state["excludedMarketId"] == 100

    observer._reset_market(101, None, "second")
    assert observer.target_taker_multi_entry_state["active"] is True
    row = observer.db.execute(
        "SELECT market_id FROM wallet_target_taker_multi_entry_v1_markets WHERE market_id=101"
    ).fetchone()
    assert row is not None


def test_same_market_accepts_multiple_distinct_trade_snapshots_and_dedupes_same_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = _Harness()
    observer._reset_market(100, None, "excluded")
    observer._reset_market(101, None, "active")

    decisions = iter([_trade("UP", 0.40), _trade("UP", 0.42)])
    monkeypatch.setattr(multi.public_side, "decide_side", lambda *_a, **_k: next(decisions))

    observer._advance_target_taker_multi_entry({}, snapshot_ns=1_000, now_ms=10_000)
    observer._advance_target_taker_multi_entry({}, snapshot_ns=2_000, now_ms=11_000)
    observer._advance_target_taker_multi_entry({}, snapshot_ns=2_000, now_ms=11_001)

    rows = observer.db.execute(
        "SELECT snapshot_timestamp_ns,side,observed_ask FROM wallet_target_taker_multi_entry_v1_events "
        "WHERE market_id=101 ORDER BY snapshot_timestamp_ns"
    ).fetchall()
    assert len(rows) == 2
    assert [int(row["snapshot_timestamp_ns"]) for row in rows] == [1_000, 2_000]
    assert [float(row["observed_ask"]) for row in rows] == pytest.approx([0.40, 0.42])
    assert observer.target_taker_multi_entry_state["entryCount"] == 2


def test_multi_entry_settlement_aggregates_every_entry_even_if_side_flips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = _Harness()
    observer._reset_market(100, None, "excluded")
    observer._reset_market(101, None, "active")

    decisions = iter([_trade("UP", 0.40), _trade("DOWN", 0.30), _trade("UP", 0.50)])
    monkeypatch.setattr(multi.public_side, "decide_side", lambda *_a, **_k: next(decisions))
    for index in range(3):
        observer._advance_target_taker_multi_entry(
            {}, snapshot_ns=1_000 + index, now_ms=10_000 + index
        )

    observer._store_market_result(101, {"title": "settled"}, "UP")
    result = observer.db.execute(
        "SELECT * FROM wallet_target_taker_multi_entry_v1_results WHERE market_id=101"
    ).fetchone()
    assert result is not None
    assert int(result["entry_count"]) == 3
    assert int(result["winning_entries"]) == 2
    assert int(result["losing_entries"]) == 1
    assert int(result["up_entries"]) == 2
    assert int(result["down_entries"]) == 1
    assert float(result["stake_usdt"]) == pytest.approx(3.0)

    # Every entry is settled independently using the same official market winner.
    up1 = multi.public_side.execution(_trade("UP", 0.40))["shares"]
    up2 = multi.public_side.execution(_trade("UP", 0.50))["shares"]
    expected_payout = up1 + up2
    assert float(result["payout_usdt"]) == pytest.approx(expected_payout)
    assert float(result["net_pnl_usdt"]) == pytest.approx(expected_payout - 3.0)

    perf = observer._target_taker_multi_entry_performance()
    assert perf["entries"] == 3
    assert perf["winningEntries"] == 2
    assert perf["entryWinRate"] == pytest.approx(2 / 3)
    assert perf["averageEntriesPerTradedMarket"] == pytest.approx(3.0)


def test_snapshot_exposes_explicit_every_signal_policy() -> None:
    observer = _Harness()
    observer._reset_market(100, None, "excluded")
    observer._reset_market(101, None, "active")
    snapshot = observer.snapshot()["targetTakerPublicSideV1Lab"]["multiEntryExperiment"]
    assert snapshot["version"] == multi.VERSION
    assert snapshot["policy"]["oneEntryPerMarket"] is False
    assert snapshot["policy"]["cooldownMs"] == 0
    assert snapshot["policy"]["sameSideReentryAllowed"] is True
    assert snapshot["policy"]["liveOrdersAffected"] is False


def test_multi_entry_cohort_cannot_be_selected_for_live() -> None:
    with pytest.raises(ValueError, match="SIDE_ONLY or HAZARD_SIDE"):
        v4_21.WalletShadowObserver._cohort_value(multi.COHORT)
