from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "profile_predict_wallet_regime_day.py"
SPEC = importlib.util.spec_from_file_location("regime_day", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def test_winner_from_market_prefers_explicit_outcome_status() -> None:
    winner, source = m.winner_from_market({
        "outcomes": [
            {"name": "Up", "status": "WON"},
            {"name": "Down", "status": "LOST"},
        ],
        "variantData": {"startPrice": 100.0, "endPrice": 99.0},
    })
    assert winner == "UP"
    assert source == "outcomes.status"


def test_winner_from_market_can_fall_back_to_start_end_price() -> None:
    winner, source = m.winner_from_market({
        "variantData": {"startPrice": 100.0, "endPrice": 101.0},
    })
    assert winner == "UP"
    assert source == "variantData.startPrice/endPrice"


def test_market_price_meta_reports_final_distance_bps() -> None:
    result = m.market_price_meta({"variantData": {"startPrice": 100.0, "endPrice": 100.01}})
    assert abs(result["finalMoveBps"] - 1.0) < 1e-9
    assert abs(result["absFinalMoveBps"] - 1.0) < 1e-9
