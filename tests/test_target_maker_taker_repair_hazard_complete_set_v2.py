from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_maker_taker_repair_hazard_complete_set_v2 as mod


def _action(at: int, side: str, shares: float = 4.0, price: float = 0.6) -> dict:
    return {
        "first_event_ms": at,
        "parent_id": f"p-{at}",
        "side": side,
        "quote_type": "BID",
        "shares": shares,
        "notional_usdt": shares * price,
    }


def test_probability_buckets_are_fixed_before_analysis():
    assert mod._prob_bucket(0.19) == "LT_020"
    assert mod._prob_bucket(0.20) == "020_040"
    assert mod._prob_bucket(0.40) == "040_060"
    assert mod._prob_bucket(0.60) == "060_080"
    assert mod._prob_bucket(0.80) == "GE_080"


def test_future_repair_label_uses_snapshot_maker_heavy_side():
    # Positive Maker delta means UP-heavy. A future DOWN BID is repair against that state.
    actions = [_action(2500, "DOWN", 3.0), _action(7000, "UP", 2.0)]
    times = [2500, 7000]
    labels = mod._future_labels(1000, 10.0, actions, times)
    assert labels["any_taker_1s"] == 0
    assert labels["repair_taker_2s"] == 1
    assert labels["repair_shares_2s"] == 3.0
    assert labels["repair_taker_5s"] == 1
    assert labels["first_repair_delay_ms_5s"] == 1500


def test_same_side_future_taker_is_not_called_repair():
    actions = [_action(1500, "UP", 3.0)]
    labels = mod._future_labels(1000, 10.0, actions, [1500])
    assert labels["any_taker_1s"] == 1
    assert labels["repair_taker_1s"] == 0


def test_complete_set_pair_cost_and_fee_adjusted_edge():
    lots = deque([
        {"side": "UP", "price": 0.30, "event_ms": 1000, "remaining": 5.0}
    ])
    taker_event = {
        "market_id": 1,
        "side": "DOWN",
        "quote_type": "BID",
        "shares": 4.0,
        "price": 0.60,
        "event_ms": 2000,
    }
    action = {
        "parent_id": "t1",
        "first_event_ms": 2000,
        "is_first_taker": 0,
        "maker_delta_before": 5.0,
        "side": "DOWN",
        "quote_type": "BID",
        "shares": 4.0,
    }
    rows = mod._match_taker_leg(
        lots,
        taker_event,
        action,
        fee_bps=200,
        regime="ORDINARY_PRE_SPECIAL",
        signal={"predict_up_mid": 0.3, "predict_down_mid": 0.7, "seconds_left": 50.0},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["paired_shares"] == 4.0
    assert abs(row["pair_cost"] - 0.90) < 1e-12
    assert abs(row["raw_edge_per_share"] - 0.10) < 1e-12
    assert row["pair_class_raw"] == "LOCKED_POSITIVE"
    assert row["repair_against_maker_heavy"] == 1
    assert row["phase"] == "TAIL"
    assert row["heavy_probability_bucket"] == "020_040"
    assert row["fee_adjusted_edge_per_share"] < row["raw_edge_per_share"]
    assert abs(lots[0]["remaining"] - 1.0) < 1e-12


def test_ask_consumption_prevents_reusing_sold_maker_origin_lots():
    lots = deque([
        {"side": "UP", "price": 0.35, "event_ms": 1000, "remaining": 5.0}
    ])
    consumed = mod._consume_lots(lots, 3.0)
    assert consumed == 3.0
    assert len(lots) == 1
    assert lots[0]["remaining"] == 2.0


def test_pair_class_distinguishes_locked_edge_near_par_and_paid_insurance():
    assert mod._pair_class(0.02) == "LOCKED_POSITIVE"
    assert mod._pair_class(0.005) == "NEAR_PAR"
    assert mod._pair_class(-0.02) == "INSURANCE_COST"


def test_primary_low_high_comparison_is_market_blocked():
    rows = []
    # Market 1: low-prob snapshots repair every time.
    for i in range(4):
        rows.append({
            "market_id": 1,
            "lifecycle_state": "POST_FIRST_TAKER",
            "phase": "MID",
            "heavy_win_probability": 0.30,
            "repair_taker_5s": 1,
        })
    # Market 2: high-prob snapshots never repair.
    for i in range(4):
        rows.append({
            "market_id": 2,
            "lifecycle_state": "POST_FIRST_TAKER",
            "phase": "TAIL",
            "heavy_win_probability": 0.70,
            "repair_taker_5s": 0,
        })
    payload = mod._market_blocked_low_high(rows)
    assert payload["LOW"]["markets"] == 1
    assert payload["HIGH"]["markets"] == 1
    assert payload["marketBlockedDifferenceLowMinusHigh"] == 1.0
