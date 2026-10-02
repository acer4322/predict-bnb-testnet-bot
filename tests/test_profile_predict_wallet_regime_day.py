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


def test_resolve_local_window_supports_narrow_taipei_hours() -> None:
    local_start, local_end, start_utc, end_utc = m.resolve_local_window(
        "2026-08-08", "Asia/Taipei", "06:30", "09:00"
    )
    assert local_start.hour == 6 and local_start.minute == 30
    assert local_end.hour == 9 and local_end.minute == 0
    assert start_utc.isoformat().startswith("2026-08-07T22:30:00")
    assert end_utc.isoformat().startswith("2026-08-08T01:00:00")


def test_resolve_local_window_allows_24_00() -> None:
    local_start, local_end, _, _ = m.resolve_local_window(
        "2026-08-08", "Asia/Taipei", "00:00", "24:00"
    )
    assert (local_end - local_start).total_seconds() == 86400


def test_cutoff_snapshot_separates_maker_taker_and_ignores_late_parent() -> None:
    market_end_ms = 300_000
    parents = [
        {
            "role": "MAKER",
            "quoteType": "BID",
            "side": "UP",
            "shares": 10.0,
            "costUsdtApprox": 4.0,
            "eventMs": 200_000,
        },
        {
            "role": "TAKER",
            "quoteType": "BID",
            "side": "DOWN",
            "shares": 6.0,
            "costUsdtApprox": 3.0,
            "eventMs": 230_000,
        },
        {
            "role": "TAKER",
            "quoteType": "BID",
            "side": "DOWN",
            "shares": 20.0,
            "costUsdtApprox": 12.0,
            "eventMs": 295_000,
        },
    ]
    snap = m.cutoff_snapshot(parents, "UP", market_end_ms, 20.0)
    assert snap["maker"]["parents"] == 1
    assert snap["taker"]["parents"] == 1
    assert snap["combined"]["parents"] == 2
    assert snap["combined"]["buyCostTiltSide"] == "UP"
    assert snap["combined"]["buyCostTiltHit"] is True


def test_threshold_slice_name_is_stable() -> None:
    assert m.threshold_slice_name(0.02) == "finalAbsMoveLe0p02bps"
    assert m.threshold_slice_name(1.0) == "finalAbsMoveLe1bps"
