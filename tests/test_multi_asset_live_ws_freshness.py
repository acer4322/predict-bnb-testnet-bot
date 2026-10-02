from __future__ import annotations

import importlib


def _module(monkeypatch):
    monkeypatch.setenv("PREDICT_POLY_GAP_LIVE_ASSET", "ETH")
    monkeypatch.setenv("PREDICT_POLY_GAP_LIVE_SYMBOL", "ETHUSDT")
    return importlib.import_module("predict_bot.poly_gap_multi_asset_live_v2")


def _engine(module, *, current_session: int, quote_session: int):
    engine = object.__new__(module.LiveGradeMultiAssetPolyGapLiveEngine)
    engine.asset = "ETH"
    engine._asset_observer_version = module.REQUIRED_OBSERVER_VERSION
    engine._last_asset_observer_state = {
        "poly": {
            "up": {
                "wsSession": current_session,
                "quoteWsSession": quote_session,
            }
        }
    }
    engine._ws_generation_blocks = 0
    engine.last_poly = {"old": True}
    engine.last_error = None
    return engine


def test_old_ws_generation_fails_closed(monkeypatch) -> None:
    module = _module(monkeypatch)
    monkeypatch.setattr(
        module.MultiAssetPolyGapLiveEngine,
        "_poly_state",
        lambda self: {"upMid": 0.8, "direction": "UP"},
    )
    engine = _engine(module, current_session=12, quote_session=11)

    assert engine._poly_state() is None
    assert engine.last_poly is None
    assert engine._ws_generation_blocks == 1
    assert "waiting for a fresh book/price_change" in str(engine.last_error)


def test_current_ws_generation_is_allowed(monkeypatch) -> None:
    module = _module(monkeypatch)
    monkeypatch.setattr(
        module.MultiAssetPolyGapLiveEngine,
        "_poly_state",
        lambda self: {"upMid": 0.8, "direction": "UP"},
    )
    engine = _engine(module, current_session=12, quote_session=12)

    result = engine._poly_state()

    assert result is not None
    assert result["collectorCurrentWsSession"] == 12
    assert result["collectorQuoteWsSession"] == 12
    assert result["signalFreshnessTimestampSource"] == "MULTI_OBSERVER_V2_CURRENT_WS_QUOTE"
    assert engine._ws_generation_blocks == 0
