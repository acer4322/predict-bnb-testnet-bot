from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_maker_heavy_survival_v3 as mod
import backfill_target_maker_survival_settlements_v3 as backfill


def test_heavy_probability_orients_generic_up_probability():
    assert mod._heavy_probability(0.73, "UP") == 0.73
    assert abs(mod._heavy_probability(0.73, "DOWN") - 0.27) < 1e-12


def test_fixed_probability_buckets_are_stable():
    assert mod._prob_bucket(0.19) == "LT_020"
    assert mod._prob_bucket(0.20) == "020_040"
    assert mod._prob_bucket(0.39) == "020_040"
    assert mod._prob_bucket(0.40) == "040_060"
    assert mod._prob_bucket(0.60) == "060_080"
    assert mod._prob_bucket(0.80) == "GE_080"


def test_brier_and_logloss_reward_correct_confidence():
    y = [1, 0]
    good = [0.9, 0.1]
    bad = [0.1, 0.9]
    assert mod._brier(y, good) < mod._brier(y, bad)
    assert mod._logloss(y, good) < mod._logloss(y, bad)


def test_market_blocked_repair_rate_weights_markets_equally():
    rows = [
        {"market_id": 1, "repair_taker_5s": 1},
        {"market_id": 1, "repair_taker_5s": 1},
        {"market_id": 1, "repair_taker_5s": 1},
        {"market_id": 2, "repair_taker_5s": 0},
    ]
    # Market 1 rate=1, market 2 rate=0 => blocked mean=0.5,
    # rather than the raw row-weighted 0.75.
    assert abs(mod._market_blocked_repair_rate(rows) - 0.5) < 1e-12


def test_survival_feature_sets_do_not_include_target_inventory_or_future_action_fields():
    forbidden = {
        "maker_delta",
        "maker_abs_delta",
        "taker_delta",
        "repair_taker_5s",
        "repair_shares_5s",
        "heavy_side_won",
    }
    for features in mod.FEATURE_SETS.values():
        assert not (set(features) & forbidden)


def test_public_market_id_loader_dedupes(tmp_path: Path):
    path = tmp_path / "public.csv"
    path.write_text(
        "market_id,sampled_at_ms\n101,1\n101,2\n202,3\n",
        encoding="utf-8",
    )
    assert backfill._market_ids_from_public_dataset(path) == [101, 202]
