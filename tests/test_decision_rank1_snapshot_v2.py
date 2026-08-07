from __future__ import annotations

from types import SimpleNamespace

from predict_bot import live_trading, m_realtime, research_forward, server
from predict_bot import decision_rank1_p50_80_shadow as p50
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


class _P50Store:
    def __init__(self) -> None:
        self.opened: list[dict[str, object]] = []

    def config(self) -> dict[str, object]:
        return {f"strategy_{p50.RANK1_P50_80_STRATEGY.lower()}_enabled": True}

    def has_trade(self, strategy: str, market_id: int) -> bool:
        return False

    def open_trade(self, **kwargs: object) -> None:
        self.opened.append(dict(kwargs))


class _P50DecisionStore:
    def __init__(self, entry: float) -> None:
        self.entry = entry
        self.evaluations: list[dict[str, object]] = []
        self._paper_id = 100

    def latest_trade(
        self, store: _P50Store, strategy: str, market_id: int
    ) -> dict[str, object] | None:
        if strategy == p50.RANK1_P50_80_STRATEGY:
            return {"id": self._paper_id, "market_id": market_id}
        return {
            "id": 7,
            "strategy": strategy,
            "market_id": market_id,
            "topic_id": 9,
            "side": "UP",
            "opened_at": "2026-08-08T00:00:00+00:00",
        }

    def _evaluation_exists(self, store: _P50Store, controller: str, trigger: int) -> bool:
        return False

    def family_state(self, store: _P50Store, market_id: int, as_of: str) -> dict[str, object]:
        return {}

    def trend_gate(self, store: _P50Store, snapshot: dict[str, object], side: str) -> dict[str, object]:
        return {"passed": True, "status": "PASS"}

    def execution_candidate(
        self,
        snapshot: dict[str, object],
        context: dict[str, object],
        side: str,
    ) -> tuple[dict[str, float], str]:
        return (
            {
                "entryPrice": self.entry,
                "rawTopAsk": self.entry / 1.005,
                "bookAgeMs": 100.0,
            },
            "",
        )

    def record_evaluation(self, store: _P50Store, **kwargs: object) -> int:
        self.evaluations.append(dict(kwargs))
        return len(self.evaluations)


def _run_p50_boundary(monkeypatch, entry: float) -> tuple[_P50Store, _P50DecisionStore]:
    store = _P50Store()
    decision_store = _P50DecisionStore(entry)
    tracker = SimpleNamespace(store=store)
    monkeypatch.setattr(
        p50,
        "rank1_snapshot_decision",
        lambda families: {
            "status": "CANDIDATE",
            "reason": "test candidate",
            "side": "UP",
            "selectedFamily": "CONSENSUS",
            "selectedSourceTradeId": None,
            "selectedSourceSignalId": 10,
            "estimatedProbability": 0.75,
            "agreementWeight": 0.80,
        },
    )
    p50._evaluate(
        decision_store,
        tracker,
        [{"strategy": "R_CONSENSUS"}],
        {
            "market_id": 123,
            "topic_id": 9,
            "timestamp": "2026-08-08T00:00:00+00:00",
            "seconds_left": 120.0,
        },
        200,
        {},
    )
    return store, decision_store


def test_p50_80_entry_boundaries(monkeypatch) -> None:
    below, below_ds = _run_p50_boundary(monkeypatch, 0.499999)
    assert below.opened == []
    assert below_ds.evaluations[-1]["status"] == "BLOCK_ENTRY_RANGE"

    minimum, minimum_ds = _run_p50_boundary(monkeypatch, 0.50)
    assert len(minimum.opened) == 1
    assert minimum_ds.evaluations[-1]["status"] == "OPENED"

    inside, inside_ds = _run_p50_boundary(monkeypatch, 0.799999)
    assert len(inside.opened) == 1
    assert inside_ds.evaluations[-1]["status"] == "OPENED"

    maximum, maximum_ds = _run_p50_boundary(monkeypatch, 0.80)
    assert maximum.opened == []
    assert maximum_ds.evaluations[-1]["status"] == "BLOCK_ENTRY_RANGE"


def test_p50_80_shadow_is_paper_only_and_not_live_forwardable(tmp_path) -> None:
    store = server.Store(tmp_path / "simulation.db")
    p50.install_rank1_p50_80_shadow(store)
    assert p50.RANK1_P50_80_STRATEGY in research_forward.RESEARCH_STRATEGIES
    assert (
        p50.RANK1_P50_80_STRATEGY
        not in research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES
    )
    assert p50.RANK1_P50_80_STRATEGY not in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert p50.RANK1_P50_80_STRATEGY not in live_trading.LIVE_RESEARCH_STRATEGIES
    assert (
        p50.RANK1_P50_80_STRATEGY
        not in m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    )

    experiment = server.decision_strategy_store.experiment_state(store) if hasattr(server, "decision_strategy_store") else None
    if experiment is not None:
        assert experiment["rank1V2"]["p50_80Strategy"] == p50.RANK1_P50_80_STRATEGY
