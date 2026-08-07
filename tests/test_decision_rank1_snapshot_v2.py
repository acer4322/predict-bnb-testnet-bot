from __future__ import annotations

from predict_bot import live_trading, m_realtime, research_forward, server
from predict_bot.decision_rank1_snapshot_v2 import (
    RANK1_LOGIC_VERSION,
    RANK1_V1_BASELINE_STRATEGY,
    install_rank1_snapshot_v2,
    latest_signal_snapshot,
    rank1_snapshot_decision,
)
from predict_bot.decision_strategy_rules import (
    RANK1_MIN_HISTORY,
    RANK1_STRATEGY,
    RANK2_STRATEGY,
)


def _stats(utility: float, probability: float) -> dict[str, float | int]:
    return {
        "n": RANK1_MIN_HISTORY,
        "utility": utility,
        "p": probability,
        "weightSum": float(RANK1_MIN_HISTORY),
    }


def _vote(signal_id: int, side: str) -> dict[str, object]:
    return {
        "id": signal_id,
        "side": side,
        "signal_timestamp_ns": 1_000_000_000 + signal_id,
    }


def _family(
    signal_id: int | None,
    side: str,
    *,
    utility: float,
    probability: float,
) -> dict[str, object]:
    return {
        "source": None,
        "vote": _vote(signal_id, side) if signal_id is not None else None,
        "rank1": _stats(utility, probability),
        "rank2": {},
    }


def _valid_rank1_live_signal() -> dict[str, object]:
    return {
        "strategy": RANK1_STRATEGY,
        "entry_price": 0.402,
        "raw_top_ask": 0.40,
        "estimated_probability": 0.70,
        "agreement_weight": 0.80,
        "model_edge": 0.10,
        "decision_strategy_controller": True,
        "decision_strategy_version": "DECISION_STRATEGY_NATIVE_V2",
        "decision_evaluation_id": 12,
        "paper_trade_id": 34,
        "selected_family": "CONSENSUS",
        "selected_source_trade_id": None,
        "selected_source_signal_id": 56,
        "trigger_source_trade_id": 78,
        "rank1_logic_version": RANK1_LOGIC_VERSION,
        "market_data_integrity_ok": True,
        "trend_status": "PASS",
    }


def test_rank1_v2_waits_for_two_same_market_signal_snapshots() -> None:
    decision = rank1_snapshot_decision(
        {
            "FUTURES_LEAD": _family(
                None, "UP", utility=1.0, probability=0.70
            ),
            "CALIBRATED_VALUE": _family(
                None, "UP", utility=-2.0, probability=0.32
            ),
            "CONSENSUS": _family(
                3, "UP", utility=0.8, probability=0.66
            ),
        }
    )
    assert decision["status"] == "WAITING_SIGNAL_OVERLAP"
    assert decision["voterCount"] == 1


def test_rank1_v2_allows_zero_weight_same_side_confirmation() -> None:
    decision = rank1_snapshot_decision(
        {
            "FUTURES_LEAD": _family(
                None, "UP", utility=0.4, probability=0.44
            ),
            "CALIBRATED_VALUE": _family(
                2, "DOWN", utility=-2.0, probability=0.32
            ),
            "CONSENSUS": _family(
                3, "DOWN", utility=0.8, probability=0.66
            ),
        }
    )
    assert decision["status"] == "CANDIDATE"
    assert decision["side"] == "DOWN"
    assert set(decision["supporters"]) == {"CALIBRATED_VALUE", "CONSENSUS"}
    assert decision["positiveWeightSupporters"] == ["CONSENSUS"]
    assert decision["zeroWeightConfirmers"] == ["CALIBRATED_VALUE"]
    assert decision["selectedSourceSignalId"] == 3
    assert decision["selectedSourceTradeId"] is None


def test_rank1_v2_does_not_count_opposite_zero_weight_signal_as_confirmation() -> None:
    decision = rank1_snapshot_decision(
        {
            "FUTURES_LEAD": _family(
                None, "UP", utility=0.4, probability=0.44
            ),
            "CALIBRATED_VALUE": _family(
                2, "DOWN", utility=-2.0, probability=0.32
            ),
            "CONSENSUS": _family(
                3, "UP", utility=0.8, probability=0.66
            ),
        }
    )
    assert decision["status"] == "NO_CONSENSUS"


def test_v1_baseline_is_paper_only_and_never_added_to_live_sets(tmp_path) -> None:
    store = server.Store(tmp_path / "simulation.db")
    install_rank1_snapshot_v2(store)
    assert RANK1_V1_BASELINE_STRATEGY in research_forward.RESEARCH_STRATEGIES
    assert (
        RANK1_V1_BASELINE_STRATEGY
        not in research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES
    )
    assert RANK1_V1_BASELINE_STRATEGY not in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert RANK1_V1_BASELINE_STRATEGY not in live_trading.LIVE_RESEARCH_STRATEGIES
    assert (
        RANK1_V1_BASELINE_STRATEGY
        not in m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    )
    assert RANK1_STRATEGY in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert RANK2_STRATEGY in live_trading.LIVE_SUPPORTED_STRATEGIES


def test_rank1_live_provenance_uses_trigger_trade_and_signal_snapshot() -> None:
    signal = _valid_rank1_live_signal()
    allowed, reason = live_trading.LiveM0WEngine._research_signal_price_is_allowed(
        signal, RANK1_STRATEGY
    )
    assert (allowed, reason) == (True, "")

    missing_signal = dict(signal)
    missing_signal.pop("selected_source_signal_id")
    allowed, reason = live_trading.LiveM0WEngine._research_signal_price_is_allowed(
        missing_signal, RANK1_STRATEGY
    )
    assert allowed is False
    assert "source signal snapshot id" in reason

    missing_trigger = dict(signal)
    missing_trigger.pop("trigger_source_trade_id")
    allowed, reason = live_trading.LiveM0WEngine._research_signal_price_is_allowed(
        missing_trigger, RANK1_STRATEGY
    )
    assert allowed is False
    assert "trigger source trade id" in reason


def test_rank1_snapshot_table_is_empty_before_forward_signals(tmp_path) -> None:
    store = server.Store(tmp_path / "simulation.db")
    install_rank1_snapshot_v2(store)
    assert latest_signal_snapshot(store, "R_CONSENSUS", 12345) is None
