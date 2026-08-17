from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_maker_heavy_survival_v3_3 as mod


def test_profit_class_uses_combined_pnl_sign():
    assert mod._profit_class(1.0) == "WIN"
    assert mod._profit_class(-0.01) == "LOSS"
    assert mod._profit_class(0.0) == "FLAT"


def test_canonical_market_outcome_replaces_legacy_heavy_side_label_but_pnl_defines_target_win():
    risk_rows = [
        {
            "market_id": 101,
            "maker_heavy_side": "UP",
            "heavy_side_won": 0,
            "regime": "ORDINARY_PRE_SPECIAL",
            "lifecycle_state": "POST_FIRST_TAKER",
            "repair_taker_5s": 1,
            "repair_shares_5s": 5.0,
        }
    ]
    settlements = {101: "UP"}
    pnl = {
        101: {
            "net_pnl_usdt": 3.0,
            "maker_net_pnl_usdt": 7.0,
            "taker_net_pnl_usdt": -4.0,
        }
    }
    rows, audit = mod._canonicalize_risk_outcomes(risk_rows, settlements, pnl)
    assert rows[0]["heavy_side_won"] == 1
    assert rows[0]["heavy_side_outcome_won"] == 1
    assert rows[0]["market_outcome"] == "UP"
    assert rows[0]["target_win"] == 1
    assert rows[0]["target_profit_class"] == "WIN"
    assert rows[0]["target_maker_net_pnl_usdt"] == 7.0
    assert rows[0]["target_taker_net_pnl_usdt"] == -4.0
    assert audit["rowsChangedToCanonicalOutcome"] == 1


def test_portfolio_repair_audit_counts_maker_win_taker_loss_combined_win():
    rows = [
        {
            "market_id": 1,
            "regime": "ORDINARY_PRE_SPECIAL",
            "lifecycle_state": "POST_FIRST_TAKER",
            "repair_taker_5s": 1,
            "repair_shares_5s": 2.0,
        },
        {
            "market_id": 1,
            "regime": "ORDINARY_PRE_SPECIAL",
            "lifecycle_state": "POST_FIRST_TAKER",
            "repair_taker_5s": 0,
            "repair_shares_5s": 0.0,
        },
        {
            "market_id": 2,
            "regime": "ORDINARY_PRE_SPECIAL",
            "lifecycle_state": "POST_FIRST_TAKER",
            "repair_taker_5s": 1,
            "repair_shares_5s": 4.0,
        },
    ]
    pnl = {
        1: {"net_pnl_usdt": 3.0, "maker_net_pnl_usdt": 7.0, "taker_net_pnl_usdt": -4.0},
        2: {"net_pnl_usdt": -2.0, "maker_net_pnl_usdt": -5.0, "taker_net_pnl_usdt": 3.0},
    }
    audit = mod._portfolio_repair_audit(rows, pnl)["ORDINARY_PRE_SPECIAL"]
    assert audit["byTargetProfitClass"]["WIN"]["markets"] == 1
    assert audit["byTargetProfitClass"]["LOSS"]["markets"] == 1
    assert audit["makerTakerCombinedCompositionMarketCounts"]["MAKER_WIN_TAKER_LOSS_COMBINED_WIN"] == 1
    assert audit["makerTakerCombinedCompositionMarketCounts"]["MAKER_LOSS_TAKER_WIN_COMBINED_LOSS"] == 1


def test_target_pnl_coverage_is_independent_of_market_outcome_coverage():
    rows = [
        {"market_id": 1, "regime": "ORDINARY_PRE_SPECIAL"},
        {"market_id": 2, "regime": "ORDINARY_PRE_SPECIAL"},
        {"market_id": 3, "regime": "SPECIAL"},
    ]
    pnl = {
        1: {"net_pnl_usdt": 1.0, "maker_net_pnl_usdt": 1.0, "taker_net_pnl_usdt": 0.0},
        3: {"net_pnl_usdt": -1.0, "maker_net_pnl_usdt": -1.0, "taker_net_pnl_usdt": 0.0},
    }
    coverage = mod._target_pnl_coverage(rows, pnl, 0.75)
    assert coverage["ORDINARY_PRE_SPECIAL"]["coverage"] == 0.5
    assert coverage["ORDINARY_PRE_SPECIAL"]["passed"] is False
    assert coverage["SPECIAL"]["coverage"] == 1.0
    assert coverage["SPECIAL"]["passed"] is True
    assert coverage["passed"] is False
