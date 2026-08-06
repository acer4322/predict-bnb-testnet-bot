from __future__ import annotations

from types import SimpleNamespace

from predict_bot import decision_strategy_shadows as decision
from predict_bot import decision_strategy_safe_integration_patch as safe


def test_decision_installer_does_not_wrap_legacy_dashboard() -> None:
    class Store:
        def dashboard(self) -> dict[str, bool]:
            return {"legacy": True}

    original = Store.dashboard
    decision._wrap_dashboard(Store)
    assert Store.dashboard is original
    assert Store().dashboard() == {"legacy": True}


def test_no_source_trade_skips_decision_evaluation(monkeypatch) -> None:
    calls = {"process": 0}

    class Tracker:
        def __init__(self, engine, store) -> None:
            self.engine = engine
            self.store = store

        def process(self, snapshot, fee_bps, context):
            calls["process"] += 1
            return [{"strategy": "R_DECISION_RANK1"}]

    class Store:
        def maybe_enter_m_series(self, snapshot, fee_bps, *, realtime_context=None):
            return [{"strategy": "M01", "market_id": 1}]

    monkeypatch.setattr(safe._decision, "DecisionStrategyTracker", Tracker)
    store = Store()
    engine = SimpleNamespace()
    safe._safe_wrap_store(engine, store)

    opened = store.maybe_enter_m_series({}, 200, realtime_context={})
    assert opened == [{"strategy": "M01", "market_id": 1}]
    assert calls["process"] == 0


def test_decision_failure_preserves_original_source_candidates(monkeypatch) -> None:
    class Tracker:
        def __init__(self, engine, store) -> None:
            self.engine = engine
            self.store = store

        def process(self, snapshot, fee_bps, context):
            raise RuntimeError("decision experiment failure")

    source = {"strategy": "R_FUTURES_LEAD", "market_id": 7, "side": "UP"}

    class Store:
        def maybe_enter_m_series(self, snapshot, fee_bps, *, realtime_context=None):
            return [dict(source)]

    monkeypatch.setattr(safe._decision, "DecisionStrategyTracker", Tracker)
    store = Store()
    engine = SimpleNamespace()
    safe._safe_wrap_store(engine, store)

    opened = store.maybe_enter_m_series({}, 200, realtime_context={})
    assert opened == [source]
